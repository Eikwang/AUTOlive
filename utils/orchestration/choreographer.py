# -*- coding: utf-8 -*-
"""
动作编排器（/autoplan 2026-10-21 批次 T1/T5 核心）

事件驱动的画面编排：打赏/关键词/点歌触发 片段序列（enter→main→exit），
按连续触发次数切换情绪分级素材（flowchartTD.md Compile〔规划〕节点填实）。

架构（义务 D-1/CP-3/WP-6/WP-9/F1/F5/S1-1）：
- ChoreoEngine：纯状态机（无 IO 无线程），所有变更由调用方持锁串行化
- ActionChoreographer：单工作线程 + 线程安全事件队列；on_* 同步纯内存判定
  返回接管与否，切段/入队动作由工作线程串行执行
- 完成判定谓词（C1-1/F1）：audio 存在=完成回调匹配，duration_s 上限兜底；
  无 audio=duration_s 计时（起点=切换被 EDTalk 接受）
- 条件1/2/3 + 全局 hold（F5 防乒乓）+ 计数器（D-4：他键清零/回默认不清零/
  忽略不计数/超档兜底）

失败降级（义务 C5-1/F1/S1-1）：EDTalk 不可用期间切段失败跳过并计数，
恢复后状态机续走；worker 循环体整体 try/except 防线程死亡=永久静默。
"""
import queue
import random
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, List, Optional

from utils.my_log import logger
from utils import alarm_notifier
from utils.orchestration.config_model import (ChoreographyConfig, RuleCfg, StepCfg,
                                              load_choreography_config,
                                              validate_rule_segments)


class _DottedNode:
    """raw dict 的点式 get 包装（apply_config 直收 choreography 节用）。"""

    def __init__(self, node: dict):
        self._node = node if isinstance(node, dict) else {}

    def get(self, *keys):
        cur = self._node
        for k in keys:
            if not isinstance(cur, dict) or k not in cur:
                return None
            cur = cur[k]
        return cur

# 事件队列轮询间隔上限（秒）——无近期 deadline 时的最大等待粒度
TICK_CAP_S = 1.0
# 恢复默认轮播重试参数（义务：恢复是回默认唯一路径，失败重试）
RESTORE_RETRY_MAX = 3
RESTORE_RETRY_INTERVAL_S = 5.0
# EDTalk 就绪探测间隔（秒）
READY_PROBE_INTERVAL_S = 5.0


# ---------- 命令（引擎产出，执行器消费） ----------

@dataclass
class SwitchSegment:
    step_id: str
    segment: str
    at: str  # immediate | segment_end


@dataclass
class TtsThanks:
    """tts 音轨：经注入引用复用原生答谢链（E-2 限流共享，义务 WP-3/WP-4）。"""
    step_id: str
    data: dict
    variables: dict


@dataclass
class EnqueueFile:
    """file 音轨：复用 type=song 直通通道（同 SongMode 匹配成功路径构造）。"""
    step_id: str
    audio_path: str


@dataclass
class RestoreDefault:
    """回默认轮播（条件1 exit 完成；rotate 原序 / random 每次打乱）。

    loop（2026-10-27 动作编排完善批次）：默认画面一律「立刻切换」（align=True
    由执行点保证）；循环方式 forward=轮播/随机顺序循环、pingpong=单段首尾循环
    （用户裁决：默认画面无需设切换方式，减少系统逻辑复杂性）。
    """
    sequence: list
    loop: str = "forward"


@dataclass
class LogCmd:
    level: str
    msg: str


def _normalize_text(text: str) -> str:
    """S-2 规范化思路的独立实现：NFKC + 去空白 + casefold（义务 CL-3）。"""
    import unicodedata
    return "".join(unicodedata.normalize("NFKC", str(text or "")).split()).casefold()


def select_tier(rule: RuleCfg, count: int) -> Optional[object]:
    """触发档选择（2026-10-27 用户裁决：计次进档「从第 N 次起」下界语义）。

    升序遍历 min_count，取 count >= min_count 的最高档；未达任何档 → None
    （基础三步）。示例：档位一 1次/档位二 4次/档位三 7次 → 第1~3次档一、
    第4~6次档二、第7次起档三。
    """
    selected = None
    for tier in rule.tiers:  # min_count 升序（load 校验保证）
        if count >= tier.min_count:
            selected = tier
        else:
            break
    return selected


def build_steps(rule: RuleCfg, count: int, seq_counter: int) -> List[dict]:
    """按当前计数档位物化三步（segment_overrides 可覆盖任一步，CL-4/WP-10）。

    2026-10-27 动作编排完善批次：enter/exit 为可选过渡片段——解析为 None 的步
    不物化（main→exit 直连或 enter→main 直连）；主线必选恒在。
    """
    tier = select_tier(rule, count)
    overrides = tier.segment_overrides if tier else {}
    steps = []
    for name in ("enter", "main", "exit"):
        base: StepCfg = rule.name_step(name)
        if base is None:
            continue  # 可选过渡片段缺省=跳过该步
        steps.append({
            "step_id": f"{rule.rule_id}:{name}#{seq_counter}",
            "name": name,
            "segment": overrides.get(name, base.segment),
            "switch_at": base.switch_at,
            "duration_s": base.duration_s,
            "audio_mode": base.audio_mode,
            "audio_path": base.audio_path,
            "pred_version": rule.pred_version,   # R-eng3：mute 谓词按步分叉
            "lip_flag": base.lip_flag,           # new 谓词：素材口型标志（apply 二段解析）
            "hold_for_song": (base.hold_for_song and name == "main"
                              and rule.trigger_type == "song"),
        })
    return steps


class ChoreoEngine:
    """纯状态机（无 IO）。所有方法由 ActionChoreographer 持锁调用。

    状态：default（默认轮播，承载=上游 SegmentScheduler）| sequence（片段序列执行中）。
    """

    def __init__(self, cfg: ChoreographyConfig, clock: Callable[[], float] = time.monotonic):
        self.cfg = cfg
        self.clock = clock
        self.state = "default"
        self.ready = False
        self.enabled_rules: List[RuleCfg] = list(cfg.rules)
        self.counters: dict = {}          # rule_id -> consecutive_count
        self.key_last: dict = {}          # rule_id -> 上次触发尝试时刻（节流窗口）
        self.pending_queue = None         # queue 模式待播单槽（2026-10-27 批次：排序联动）
        self.degrade_total = 0
        self._seq_counter = 0
        self._restore_attempts = 0
        # 活动序列状态（state == "sequence" 时非 None）
        self.seq: Optional[dict] = None

    # ---------- 触发判定（同步纯内存，on_gift/on_keyword/on_song 路径） ----------

    def decide_trigger(self, trigger_type: str, match_value: str, data: dict) -> dict:
        """触发判定。Returns: {accepted, takeover, rule, commands, reason, retry_after_s}

        reason 枚举（GAP-3/F2 义务）：拒绝值 = no_match / below_min_price /
        throttled{retry_after_s} / hold{retry_after_s} / in_progress / disabled；
        接受值 = accepted（DEFAULT 启动）/ interrupted（条件3 打断）/
        requeued（条件2 重排，accepted=true 说明值）。模拟触发与观测消费。
        """
        now = self.clock()
        commands: List = []
        if not self.cfg.enable or not self.enabled_rules:
            return {"accepted": False, "takeover": False, "rule": None, "commands": commands,
                    "reason": "disabled", "retry_after_s": None}
        rule, match_reason = self._match_rule(trigger_type, match_value, data)
        if rule is None:
            return {"accepted": False, "takeover": False, "rule": None, "commands": commands,
                    "reason": match_reason or "no_match", "retry_after_s": None}

        # 节流窗口已退役（R-ceo5/终门 P6 用户方向，2026-10-27 批次）：弹幕层限时
        # 去重（ac90fe60）已在上游拦截重复，引擎级节流不再生效；同键重入走
        # 条件2 计数+重排（触发次数轮换语义）。key_last 保留（reset_after_s 衰减
        # 窗口锚点仍需最后一次尝试时刻）。
        last = self.key_last.get(rule.rule_id)
        if False and last is not None and (now - last) < self.cfg.min_retrigger_s:
            self.key_last[rule.rule_id] = now
            return {"accepted": False, "takeover": False, "rule": rule, "commands": commands,
                    "reason": "throttled",
                    "retry_after_s": round(self.cfg.min_retrigger_s - (now - last), 2)}
        # 计数时间衰减（GAP-2，设计文档 reset_after 语义）：窗口锚点=最后一次尝试
        # （含被节流拒绝的，与 key_last 同源——Eng F5）；位置=节流判定之后、计数+1 之前，
        # 超窗清零后本轮从 1 重新累计；reset_after_s=0 禁用衰减。
        if self.cfg.reset_after_s > 0 and last is not None and (now - last) > self.cfg.reset_after_s:
            self.counters[rule.rule_id] = 0
        self.key_last[rule.rule_id] = now

        if self.state == "sequence" and self.seq is not None:
            seq = self.seq
            # 全局序列最小展示保护已退役（R-ceo5/终门 uc2a 用户裁决：触发命中
            # 立即切换，无最短保持保护——R-dsg7 文案+模拟连打验收对冲）。hold_until
            # 字段保留但恒为过去时刻，本判定永假（下一打断检查即时生效）。
            if rule.rule_id == seq["rule_id"]:
                # 条件2（同键再触发）
                if not seq["main_done"]:
                    # main 未完成：忽略本次触发且不计数（WP-6）
                    return {"accepted": False, "takeover": False, "rule": rule, "commands": commands,
                            "reason": "in_progress", "retry_after_s": None}
                # main 已完成（处 exit 步）：当前 exit 播完后重排完整三步（WP-9）
                self.counters[rule.rule_id] = self.counters.get(rule.rule_id, 0) + 1
                self._seq_counter += 1
                seq["pending_requeue"] = build_steps(rule, self.counters[rule.rule_id], self._seq_counter)
                commands.append(LogCmd("info", f"[编排] 规则 {rule.rule_id} 同键重入（第 {self.counters[rule.rule_id]} 次），当前 exit 播完后重排"))
                return {"accepted": True, "takeover": rule.has_tts_audio(), "rule": rule, "commands": commands,
                        "reason": "requeued", "retry_after_s": None}
            # 条件3（他键触发，beyond hold）
            # queue 模式（2026-10-27 批次：优先级排序联动）：低权重触发不打断
            # 当前序列——入单槽待播队列（后到覆盖先到+日志），当前序列播完
            # （exit 完成）后自动出队重放。消息/音频侧照常入链（Sort 排序消费），
            # 本排队仅作用于画面序列。
            if rule.interrupt == "queue":
                self.pending_queue = {
                    "trigger_type": trigger_type, "match_value": match_value,
                    "data": dict(data or {}), "rule_id": rule.rule_id}
                commands.append(LogCmd(
                    "info", f"[编排] 规则 {rule.rule_id} 排队等待（interrupt=queue，"
                    f"当前序列 {seq['rule_id']} 播完后执行）"))
                return {"accepted": False, "takeover": False, "rule": rule,
                        "commands": commands, "reason": "queued", "retry_after_s": None}
            result = self._interrupt_sequence(rule, data, now, commands)
            result["reason"] = "interrupted"
            result["retry_after_s"] = None
            return result

        # DEFAULT 态：启动新序列（条件1 的正常入口）
        result = self._start_sequence(rule, data, now, commands)
        result["reason"] = "accepted"
        result["retry_after_s"] = None
        return result

    def _match_rule(self, trigger_type: str, match_value: str, data: dict):
        """规则匹配（义务 C3-3/WP-10：gift 全等+min_price / keyword 独立词表子串 / song 可空=任意）。

        Returns: (rule, reason)——reason 仅在 rule=None 时非 None：
        no_match（无命中）/ below_min_price（礼物名命中但价值未达门槛，GAP-3 reason 枚举）。
        """
        price_gate_hit = False
        for rule in self.enabled_rules:
            if rule.trigger_type != trigger_type:
                continue
            if trigger_type == "gift":
                if str(data.get("gift_name", "")) == rule.match:
                    try:
                        total_price = float(data.get("total_price", 0) or 0)
                    except (TypeError, ValueError):
                        total_price = 0.0
                    if total_price >= rule.min_price:
                        return rule, None
                    price_gate_hit = True
            elif trigger_type == "keyword":
                if _normalize_text(match_value).find(_normalize_text(rule.match)) >= 0:
                    return rule, None
            elif trigger_type == "song":
                if not rule.match or _normalize_text(match_value).find(_normalize_text(rule.match)) >= 0:
                    return rule, None
        return None, ("below_min_price" if price_gate_hit else "no_match")

    def _start_sequence(self, rule: RuleCfg, data: dict, now: float, commands: List) -> dict:
        """DEFAULT→SEQUENCE：计数+1、按档位物化三步、发出 enter 切换。"""
        self.counters[rule.rule_id] = self.counters.get(rule.rule_id, 0) + 1
        self._seq_counter += 1
        steps = build_steps(rule, self.counters[rule.rule_id], self._seq_counter)
        self.seq = {
            "rule_id": rule.rule_id,
            "steps": steps,
            "idx": 0,
            "hold_until": now,  # 保护窗已退役（R-ceo5/uc2a）：恒可打断
            "main_done": False,
            "exit_started": False,
            "pending_requeue": None,
            "inflight": None,          # {"step_id","qtype","content","enqueued_at"}
            "current_deadline": None,  # 当前步完成上限（monotonic）
            "step_accepted": False,
            "trigger_data": dict(data or {}),
            "tier_mood": (select_tier(rule, self.counters[rule.rule_id]).mood
                          if select_tier(rule, self.counters[rule.rule_id]) else ""),
        }
        self.state = "sequence"
        commands.append(LogCmd("info",
                               f"[编排] 触发 {rule.rule_id}（第 {self.counters[rule.rule_id]} 次，"
                               f"mood={self.seq['tier_mood'] or 'base'}），进入序列"))
        commands.extend(self._start_current_step(now))
        return {"accepted": True, "takeover": rule.has_tts_audio(), "rule": rule, "commands": commands}

    def _interrupt_sequence(self, rule: RuleCfg, data: dict, now: float, commands: List) -> dict:
        """条件3：旧键序列立即终止，队列替换 [旧 exit?, 新 enter, 新 main, 新 exit]（CP-3）。"""
        seq = self.seq
        old_rule_id = seq["rule_id"]
        self.counters[old_rule_id] = 0  # 他键触发清零（D-4）
        self.counters[rule.rule_id] = self.counters.get(rule.rule_id, 0) + 1
        self._seq_counter += 1
        new_steps = build_steps(rule, self.counters[rule.rule_id], self._seq_counter)
        # 旧 exit 切换请求已发出则不重插 exit，直接排新 enter（CP-3）
        queue = list(new_steps)
        if not seq["exit_started"]:
            old_exit = dict(seq["steps"][-1])
            old_exit["step_id"] = f"{old_rule_id}:exit#interrupted{self._seq_counter}"
            queue.insert(0, old_exit)
        self.seq = {
            "rule_id": rule.rule_id,
            "steps": queue,
            "idx": 0,
            "hold_until": now,  # 保护窗已退役（R-ceo5/uc2a）：恒可打断
            "main_done": False,
            "exit_started": False,
            "pending_requeue": None,
            "inflight": None,
            "current_deadline": None,
            "step_accepted": False,
            "trigger_data": dict(data or {}),
            "tier_mood": (select_tier(rule, self.counters[rule.rule_id]).mood
                          if select_tier(rule, self.counters[rule.rule_id]) else ""),
        }
        commands.append(LogCmd("info",
                               f"[编排] 他键触发：{old_rule_id} 计数清零，切换至 {rule.rule_id}"
                               f"（{'含旧 exit 衔接' if not seq['exit_started'] else '旧 exit 已显示，直接入场'}）"))
        commands.extend(self._start_current_step(now))
        return {"accepted": True, "takeover": rule.has_tts_audio(), "rule": rule, "commands": commands}

    # ---------- 步骤执行推进 ----------

    def _start_current_step(self, now: float) -> List:
        """发出当前步切换指令（执行器收到后调 EDTalk，结果回填 on_switch_result）。"""
        commands: List = []
        if self.seq is None or self.state != "sequence":
            return commands
        step = self.seq["steps"][self.seq["idx"]]
        if step["name"] == "exit":
            self.seq["exit_started"] = True
        commands.append(SwitchSegment(step["step_id"], step["segment"], step["switch_at"]))
        return commands

    def on_switch_result(self, step_id: str, ok: bool, now: float) -> List:
        """切换请求结果回填。计时起点 = 被接受时刻（CL-1）；失败=降级跳步。"""
        if self.seq is None or self.state != "sequence":
            return []
        step = self.seq["steps"][self.seq["idx"]]
        if step["step_id"] != step_id:
            return []  # 过期步结果（被打断替换的旧步），忽略
        if not ok:
            self.degrade_total += 1
            logger.error(
                f"[编排] 步骤 {step_id} 切段失败已跳过：problem=EDTalk 切段未接受；"
                f"cause=服务不可达或段名无效；fix=检查 EDTalk 服务状态（累计降级 {self.degrade_total}）"
            )
            # 报警重构（2026-10-26）：切段失败→动作编排报警（webhook，60s 冷却合并同类降级）
            alarm_notifier.alarm_error(
                alarm_notifier.MODULE_ORCHESTRATION,
                f"编排步骤 {step_id} 切段失败已跳过（累计降级 {self.degrade_total}）",
                fix_hint="检查 EDTalk 服务状态（切段未接受：服务不可达或段名无效）")
            return self._complete_step(now, timeout_degrade=False)
        self.seq["step_accepted"] = True
        self.seq["accepted_at"] = now
        if step.get("pred_version") == "new":
            # 新语义（R-ceo4）：时长=素材时长（apply 二段物化）。hold_for_song=
            # song 规则主线段：deadline=step_timeout_s 保险丝（R-eng1 歌曲未起播
            # 的诚实降级），on_song_started 到达时以「锚点+剩余时长」覆盖延长。
            if step.get("hold_for_song"):
                self.seq["hold_for_song"] = True
                self.seq["current_deadline"] = now + float(self.cfg.step_timeout_s)
                return []
            dur = step.get("duration_s")
            if dur is None or float(dur) <= 0:
                # R-eng6② 引擎防御：时长未知→step_timeout_s 兜底+timeout_degrade
                # 报警形态（不静默跳段；UI 侧「生效待就绪」标记为事前面）
                logger.warning(
                    f"[编排] 步骤 {step_id} 素材时长未知（sidecar 缺 duration_s），"
                    f"以 step_timeout_s={self.cfg.step_timeout_s}s 兜底推进；"
                    "problem=素材时长未知；cause=sidecar 无该段时长；"
                    "fix=重新添加素材或重处理以补齐时长")
                self.degrade_total += 1
                alarm_notifier.alarm_error(
                    alarm_notifier.MODULE_ORCHESTRATION,
                    f"编排步骤 {step_id} 素材时长未知，{self.cfg.step_timeout_s}s 兜底推进",
                    fix_hint="重新添加素材或按口型推理重处理以补齐时长")
                self.seq["current_deadline"] = now + float(self.cfg.step_timeout_s)
                return []
            self.seq["current_deadline"] = now + float(dur)
            return []
        if step["audio_mode"] == "none":
            self.seq["current_deadline"] = now + float(step["duration_s"] or 0)
            return []
        # audio 步：duration_s 语义=上限兜底（F1），缺省全局 step_timeout_s
        upper = step["duration_s"] if step["duration_s"] else self.cfg.step_timeout_s
        self.seq["current_deadline"] = now + float(upper)
        # 音频在切换被接受后入队/生成（义务：计时与入队同源）
        if step["audio_mode"] == "tts":
            data = self.seq.get("trigger_data") or {}
            gift_num = data.get("gift_num", data.get("num", 1))
            variables = {
                "gift_name": data.get("gift_name", ""),
                "gift_num": gift_num,
                "unit_price": data.get("unit_price", 0),
                "total_price": data.get("total_price", 0),
            }
            return [TtsThanks(step["step_id"], data, variables)]
        return [EnqueueFile(step["step_id"], step["audio_path"])]

    def on_audio_enqueued(self, step_id: str, qtype: str, content: str, ok: bool, now: float) -> List:
        """执行器完成音频入队/生成后回填；ok=False（生成失败/未入队）→ 立即完成该步。"""
        if self.seq is None or self.state != "sequence":
            return []
        step = self.seq["steps"][self.seq["idx"]]
        if step["step_id"] != step_id:
            return []
        if not ok:
            return self._complete_step(now, timeout_degrade=True)
        self.seq["inflight"] = {"step_id": step_id, "qtype": qtype,
                                "content": content, "enqueued_at": now}
        return []

    def current_step_lip_need(self) -> Optional[bool]:
        """当前步素材口型标志（T6/R-eng5：None=无需联动——legacy 步不动 lip_mode）。

        仅 new 谓词步返回 lip_flag；song hold 主线段按素材标志照常（歌曲音频
        驱动口型与 lip 门控正交——off 素材+画外音音频合法组合，R-ceo4 F5）。
        """
        if self.state != "sequence" or self.seq is None:
            return None
        steps = self.seq["steps"]
        idx = self.seq["idx"]
        if idx >= len(steps):
            return None
        step = steps[idx]
        if step.get("pred_version") != "new":
            return None
        return bool(step.get("lip_flag", True))

    def on_song_started(self, song_name: str, remaining_s: float,
                        now: float) -> List:
        """歌曲起播通知（R-eng1/F-M4 权威信号）：hold_for_song 主线段 deadline
        以「锚点+剩余时长」覆盖 step_timeout_s 保险丝——杜绝「歌没播完序列走完」。

        仅 song 规则主线段生效；remaining_s≤0 或无 hold 段=忽略（幂等安全）。
        """
        commands: List = []
        if self.state != "sequence" or self.seq is None or remaining_s is None:
            return commands
        if not self.seq.get("hold_for_song"):
            return commands
        steps = self.seq["steps"]
        idx = self.seq["idx"]
        if idx >= len(steps) or not steps[idx].get("hold_for_song"):
            return commands
        try:
            remaining = float(remaining_s)
        except (TypeError, ValueError):
            return commands
        if remaining <= 0:
            return commands
        anchor = now
        self.seq["current_deadline"] = anchor + remaining
        commands.append(LogCmd(
            "info",
            f"[编排] 歌曲起播（{song_name}）：主线段保持至歌曲结束"
            f"（锚点+{remaining:.1f}s，R-eng1 预排程权威信号）"))
        return commands

    def on_audio_complete(self, qtype: str, content: str, now: float) -> List:
        """播放完成回调关联（CL-2：(type,content)+晚于入队时间；误匹配忽略）。"""
        if self.seq is None or self.state != "sequence":
            return []
        inflight = self.seq.get("inflight")
        if not inflight:
            return []
        if inflight["qtype"] != qtype or inflight["content"] != content:
            return []  # 他链路音频，忽略（RUNBOOK 局限声明）
        if now < inflight["enqueued_at"]:
            return []
        self.seq["inflight"] = None
        return self._complete_step(now, timeout_degrade=False)

    def on_tick(self, now: float) -> List:
        """deadline 检查（F1 看门狗：回调丢失超时强制推进 + 降级计数）。"""
        if self.seq is None or self.state != "sequence":
            return []
        deadline = self.seq.get("current_deadline")
        if deadline is None or now < deadline:
            return []
        step = self.seq["steps"][self.seq["idx"]]
        timeout_degrade = step["audio_mode"] != "none"  # audio 步超时=降级事件（F1）
        if timeout_degrade:
            logger.warning(
                f"[编排] 步骤 {step['step_id']} 音频完成回调超时，强制推进："
                f"problem=完成判定超时；cause=回调丢失（file 门拦截/启发式失准）；"
                f"fix=超时兜底推进并计降级（F1）"
            )
        return self._complete_step(now, timeout_degrade=timeout_degrade)

    def _complete_step(self, now: float, timeout_degrade: bool) -> List:
        """步完成推进：下一步 / 重排 / 回默认（条件1 + WP-9 重排 + D-4）。"""
        commands: List = []
        seq = self.seq
        step = seq["steps"][seq["idx"]]
        if timeout_degrade:
            self.degrade_total += 1
            # 报警重构（2026-10-26）：音频步降级（回调超时兜底/生成失败，on_tick 与
            # on_audio_enqueued 双路径汇入口）→动作编排报警（60s 冷却合并）
            alarm_notifier.alarm_error(
                alarm_notifier.MODULE_ORCHESTRATION,
                f"编排步骤 {step['step_id']} 音频环节降级推进（累计降级 {self.degrade_total}）",
                fix_hint="音频完成回调超时或生成失败；频繁出现请检查 TTS 与 EDTalk 服务")
        if step["name"] == "main":
            seq["main_done"] = True
        seq["idx"] += 1
        seq["inflight"] = None
        seq["current_deadline"] = None
        seq["step_accepted"] = False
        if seq["idx"] < len(seq["steps"]):
            commands.extend(self._start_current_step(now))
            return commands
        if seq.get("pending_requeue"):
            # 条件2：当前 exit 播完后重排完整三步（WP-9）
            seq["steps"] = seq["pending_requeue"]
            seq["idx"] = 0
            seq["pending_requeue"] = None
            seq["exit_started"] = False
            seq["main_done"] = False
            commands.append(LogCmd("info", f"[编排] {seq['rule_id']} 重排执行（enter→main→exit）"))
            commands.extend(self._start_current_step(now))
            return commands
        # 条件1：exit 完成 → 回默认轮播（rotate 原序 / random 每次打乱 / single 单段 pingpong）
        self.state = "default"
        self._restore_attempts = 0
        pool, loop = self._default_pool()
        mood = seq.get("tier_mood") or "base"
        commands.append(LogCmd("info",
                               f"[编排] {seq['rule_id']} 序列播完（mood={mood}），回默认轮播"
                               f"（计数保留 {self.counters.get(seq['rule_id'], 0)}）"))
        if pool:
            commands.append(RestoreDefault(pool, loop=loop))
        else:
            commands.append(LogCmd("warning",
                                   "[编排] 默认段池为空，无法恢复轮播；problem=default_segments 校验后为空；"
                                   "cause=段名不在 EDTalk available_segments；fix=修正 default_segments"))
        self.seq = None
        # queue 模式出队（2026-10-27 批次）：当前序列播完 → 待播触发自动重放
        # （state 已回 default，走正常 _start_sequence；出队失败不回滚——日志留痕）
        pending = self.pending_queue
        self.pending_queue = None
        if pending:
            commands.append(LogCmd("info",
                                   f"[编排] 出队执行排队的规则 {pending['rule_id']}"))
            try:
                replay = self.decide_trigger(pending["trigger_type"],
                                             pending["match_value"], pending["data"])
                commands.extend(replay.get("commands") or [])
            except Exception as e:
                logger.error(f"[编排] 出队重放异常（丢弃）：{type(e).__name__}: {e}")
        return commands

    def on_restore_result(self, ok: bool, now: float) -> List:
        """恢复默认结果回填：失败重试（恢复是回默认唯一路径，义务 S1-1）。

        F1 竞态守卫：仅 DEFAULT 态（无活动序列）才重试——序列已启动则放弃本次
        重试（计数归零，新序列 exit 完成后 _complete_step 自然再走恢复），
        消除 EDTalk 抖动期"重试 schedule 覆盖已接受 enter 段"的画面打架。
        """
        if ok:
            self._restore_attempts = 0
            return []
        self._restore_attempts += 1
        if self.state != "default" or self.seq is not None:
            logger.info("[编排] 恢复重试让位于新序列（F1 守卫，放弃本次重试）")
            self._restore_attempts = 0
            return []
        if self._restore_attempts <= RESTORE_RETRY_MAX:
            pool, loop = self._default_pool()
            if pool:
                logger.warning(f"[编排] 恢复默认失败，第 {self._restore_attempts}/{RESTORE_RETRY_MAX} 次重试")
                return [RestoreDefault(pool, loop=loop)]
        logger.error(
            "[编排] 恢复默认轮播重试耗尽：problem=画面停留在序列末段；"
            "cause=EDTalk /segment/schedule 连续失败；fix=检查 EDTalk 服务后手动切回或重启编排"
        )
        # 报警重构（2026-10-26）：重试耗尽（一次性事件）→动作编排报警
        alarm_notifier.alarm_error(
            alarm_notifier.MODULE_ORCHESTRATION,
            "编排恢复默认轮播重试耗尽，画面停留在序列末段",
            fix_hint="检查 EDTalk 服务后手动切回或重启编排")
        self._restore_attempts = 0
        return []

    def _default_pool(self) -> list:
        """按 default_mode 物化恢复序列（C2-3：random=每次打乱后下发 /segment/schedule）。

        Returns:
            (pool, loop)：rotate/random→forward 顺序循环；single→[循环段] +
            pingpong 首尾循环（用户裁决 2026-10-27：单段=默认循环列表第一个
            片段，可选指定循环段；一律立刻切换）。
        """
        pool = list(self.cfg.default_segments)
        if not pool:
            return [], "forward"
        if self.cfg.default_mode == "single":
            loop_seg = self.cfg.default_single_segment or pool[0]
            if loop_seg not in pool:
                loop_seg = pool[0]
            return [loop_seg], "pingpong"
        if self.cfg.default_mode == "random":
            random.shuffle(pool)
        return pool, "forward"  # rotate：原序（上游 SegmentScheduler 顺序循环承载）

    def next_deadline(self, now: float) -> Optional[float]:
        """worker 等待队列的超时基准（None=无近期 deadline）。"""
        if self.seq is not None and self.seq.get("current_deadline") is not None:
            return self.seq["current_deadline"]
        return None

    def mute_window(self) -> bool:
        """纯画面窗口（2026-10-25 口型推理联动）：序列执行中且当前步无音频
        （audio_mode=none，如跳舞/转场片段）——此窗口内数字人只播画面不出声
        不做口型，音频链推送应延迟（playback_manager 守卫消费本判定）。

        含步间隙延续：当前步 none 播完、下一步（同为 none）切换命令已发出
        尚未被 EDTalk 接受的瞬间窗口不关闭——否则音频会在间隙插入，待跳舞
        段切过来时口型叠加在画面上（步进推进同步进行，间隙通常毫秒级）。

        False 情形：默认轮播（口型正常）/ 当前步带语音（tts/file——该音频
        本身就是口型驱动源，切换接受后由编排入队）。
        """
        if self.state != "sequence" or self.seq is None:
            return False
        steps = self.seq["steps"]
        idx = self.seq["idx"]
        if idx >= len(steps):
            return False
        step = steps[idx]
        if step.get("pred_version") == "new":
            # 新语义（R-ceo4/R-eng3）：素材口型标志 off=纯画面窗口；窗口长度=
            # 素材时长（duration_s 已由 apply 二段从 sidecar 物化）。on 素材=
            # 音频驱动口型照常。F-M2/R-eng5：off 桩素材在渲染层恒直通，本窗口
            # 的增量=音频延迟推送时序（GPU 经济由 lip 联动承担，非正确性依赖）。
            return step.get("lip_flag") is False
        # 存量规则旧谓词（R-ceo4 零静默行为变化：维持 audio_mode==none 直至重存）
        return step["audio_mode"] == "none"

    def mute_window_remaining_s(self) -> Optional[float]:
        """纯画面窗口剩余秒数（None=不在窗口）；看门狗 F1 保证窗口必然结束。"""
        if not self.mute_window():
            return None
        deadline = self.seq.get("current_deadline")
        if deadline is None:
            return None
        return max(0.0, round(deadline - self.clock(), 1))

    def on_edtalk_ready(self, available_segments: set) -> dict:
        """EDTalk 就绪：第二段校验（段名存在性+资产缺口报告 F2）+ DEFAULT 初始化（CP-5）。"""
        self.ready = True
        result = validate_rule_segments(self.enabled_rules, available_segments)
        enabled_ids = set(result["enabled_rules"])
        self.enabled_rules = [r for r in self.enabled_rules if r.rule_id in enabled_ids]
        pool = [s for s in self.cfg.default_segments if s in available_segments]
        dropped = [s for s in self.cfg.default_segments if s not in available_segments]
        if dropped:
            logger.warning(
                f"[编排] default_segments 含不可用段已剔除：{dropped}；"
                f"problem=默认段池裁剪；cause=段名不在 available_segments；fix=制作素材或修正配置"
            )
        self.cfg.default_segments = pool
        return result

    def default_init_commands(self) -> List:
        """DEFAULT 初始化指令（就绪后调用；enable=false 或池空=零动作，权属线 CP-5）。"""
        if not self.cfg.enable or not self.cfg.default_segments:
            return []
        pool, loop = self._default_pool()
        return [LogCmd("info", f"[编排] DEFAULT 初始化：mode={self.cfg.default_mode}，池={pool}，loop={loop}"),
                RestoreDefault(pool, loop=loop)]

    def get_state(self) -> dict:
        """状态快照（P2 状态条/测试断言用：当前模式/活动序列/各键计数/降级计数）。

        GAP-4/F4 扩展：pending_requeue（重排待执行标志）+ step_remaining_s（当前步
        剩余秒数，None=无计时中步骤）；worker_alive/queue_depth 由门面 get_state 补。
        """
        seq = self.seq
        return {
            "ready": self.ready,
            "enable": self.cfg.enable,
            "state": self.state,
            "active_rule": seq["rule_id"] if seq else None,
            "step": (seq["steps"][seq["idx"]]["name"] if seq else None),
            "mood": (seq.get("tier_mood") or "") if seq else "",
            "counters": dict(self.counters),
            "hold_remaining": max(0.0, (seq["hold_until"] - self.clock())) if seq else 0.0,
            "hold_for_song": bool(seq.get("hold_for_song")) if seq else False,
            "degrade_total": self.degrade_total,
            "enabled_rules": [r.rule_id for r in self.enabled_rules],
            "pending_requeue": bool(seq.get("pending_requeue")) if seq else False,
            "step_remaining_s": (round(seq["current_deadline"] - self.clock(), 1)
                                 if seq and seq.get("current_deadline") is not None else None),
            "mute_window": self.mute_window(),
        }


class ActionChoreographer:
    """编排器门面：同步判定 API（handler 线程调用）+ 单工作线程执行。"""

    def __init__(self, config, edtalk_client, audio_enqueue: Callable,
                 thanks_generate: Callable, clock: Callable[[], float] = time.monotonic):
        self.cfg: ChoreographyConfig = load_choreography_config(config)
        self._config = config                    # 原始配置引用（avatar 联动读初值用）
        self.edtalk_client = edtalk_client
        self._audio_enqueue = audio_enqueue      # my_handle.audio_synthesis_handle
        self._thanks_generate = thanks_generate  # event_handler._thanks_generate
        self._clock = clock
        self.engine = ChoreoEngine(self.cfg, clock=clock)
        self._lock = threading.RLock()
        self._events: "queue.Queue" = queue.Queue()
        self._worker: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._lip_mode_cache = None  # T6/R-eng5：lip 联动去抖缓存（None=未知不下发判断失效）
        self._repair_mode_cache = None  # 2026-10-27 批次：面部修复/眼神修正联动去抖
        self._gaze_mode_cache = None
        self._muted_audio_pending: list = []  # R-eng10：静默窗口内被延迟推送的音频摘要（观测）
        # enable=false 或 EDTalk 客户端缺失：不启动 worker（订阅点短路直通，
        # 关闭=全链零行为变化——义务"总开关"）。_ensure_worker 幂等（GAP-1：
        # __init__ 与 apply_config 共用单一启动路径，is_alive 判定不二次 start）。
        with self._lock:
            self._ensure_worker()

    def _ensure_worker(self, enable=None) -> bool:
        """确保 worker 线程在运行（GAP-1/Eng 钉子：幂等，持锁调用）。

        enable：显式生效开关（apply_config 换装前按 new_cfg 判定用）；None=self.cfg.enable。

        Returns: True=worker 在运行（含已在跑）；False=无需启动（enable=false
        或客户端缺失）或启动失败（调用方按义务三段式处置）。
        """
        if self._worker is not None and self._worker.is_alive():
            return True
        eff_enable = self.cfg.enable if enable is None else enable
        if not (eff_enable and self.edtalk_client is not None):
            return False
        try:
            self._worker = threading.Thread(target=self._worker_main, daemon=True,
                                            name="action-choreographer")
            self._worker.start()
            logger.info("[编排] worker 线程启动（热启用生效）")
            return True
        except Exception as e:
            # DX CAP6:apply:fix/recovery——启动失败三段式；self._worker 复位，
            # 调用方（apply_config）整体回滚换装（side effects=none 二值）。
            self._worker = None
            logger.error(
                f"[编排] worker 启动失败：problem=编排执行线程未运行；"
                f"cause={type(e).__name__}: {e}；fix=检查配置与 EDTalk 启用状态后重新应用（fix and retry）"
            )
            # 报警重构（2026-10-26）：worker 启动失败→动作编排报警
            alarm_notifier.alarm_error(
                alarm_notifier.MODULE_ORCHESTRATION,
                f"编排 worker 启动失败：{type(e).__name__}: {e}",
                fix_hint="检查配置与 EDTalk 启用状态后重新应用")
            return False

    def worker_alive(self) -> bool:
        """worker 存活观测（GAP-4/F4：三态区分"未启用/已启未就绪/就绪运行"）。"""
        return bool(self._worker is not None and self._worker.is_alive())

    # ---------- 同步判定 API（分类分发层订阅点调用，微秒级返回） ----------

    def on_gift(self, data: dict) -> bool:
        """gift_handle 答谢门槛前调用（event_handler.py:139 前）。

        Returns: True=接管（gift_handle 跳过 _thanks_generate，双通道一并覆盖）。
        """
        try:
            with self._lock:
                decision = self.engine.decide_trigger("gift", str(data.get("gift_name", "")), data)
                self._dispatch(decision["commands"])
            return bool(decision["takeover"])
        except Exception as e:
            logger.error(f"[编排] on_gift 异常（直通处理）：problem=编排判定异常；cause={type(e).__name__}: {e}；fix=已降级直通（原生答谢照常）")
            return False

    def on_keyword(self, text: str) -> bool:
        """comment_handler 格式检查后、按键映射前调用。命中即触发画面序列（无答谢语义）。"""
        try:
            with self._lock:
                decision = self.engine.decide_trigger("keyword", str(text or ""), {})
                self._dispatch(decision["commands"])
            return bool(decision["accepted"])
        except Exception as e:
            logger.error(f"[编排] on_keyword 异常（忽略）：problem=编排判定异常；cause={type(e).__name__}: {e}；fix=已降级忽略")
            return False

    def on_song(self, song_name: str) -> bool:
        """SongMode 命中点调用。True=画面序列接管；点歌音频走 SongMode 原路（WP-10）。"""
        try:
            with self._lock:
                decision = self.engine.decide_trigger("song", str(song_name or ""), {})
                self._dispatch(decision["commands"])
            return bool(decision["accepted"])
        except Exception as e:
            logger.error(f"[编排] on_song 异常（忽略）：problem=编排判定异常；cause={type(e).__name__}: {e}；fix=已降级忽略")
            return False

    def on_audio_complete(self, qtype: str, content: str) -> None:
        """播放完成回调（playback_manager EDTalk push 路径每条目完成处，asyncio 线程）。"""
        try:
            with self._lock:
                commands = self.engine.on_audio_complete(qtype, str(content or ""), self._clock())
            self._dispatch(commands)
        except Exception as e:
            logger.error(f"[编排] 完成回调异常：problem=完成关联异常；cause={type(e).__name__}: {e}；fix=依赖 F1 超时兜底推进")

    def on_song_started(self, song_name: str, remaining_s: float) -> None:
        """歌曲起播通知（R-eng1；点歌播放链 push_full 受理时刻调用——帧锁定
        起播锚点）。入事件队列由工作线程锁内处理（非阻塞）。"""
        if remaining_s is None:
            return
        self._events.put(("song_started", str(song_name or ""), remaining_s))

    def get_state(self) -> dict:
        with self._lock:
            state = self.engine.get_state()
            state["worker_alive"] = self.worker_alive()
            state["queue_depth"] = self._events.qsize()
            state["lip_mode"] = self._lip_mode_cache  # R-eng9：lip_mode 观测透出
            return state

    # ---------- 口型推理联动（2026-10-25 goal：playback_manager 推送守卫消费） ----------

    def in_mute_window(self) -> bool:
        """纯画面窗口判定（线程安全）：audio_mode=none 步骤播放期间 True——
        音频链此刻不应推 /audio/push_full（跳舞/转场片段无需口型推理）。"""
        try:
            with self._lock:
                return self.engine.mute_window()
        except Exception:
            return False  # 判定异常不阻塞音频链（降级为正常推送）

    def mute_window_remaining_s(self) -> Optional[float]:
        """纯画面窗口剩余秒数（None=不在窗口），供日志与等待上限估算。"""
        try:
            with self._lock:
                return self.engine.mute_window_remaining_s()
        except Exception:
            return None

    # ---------- 模拟触发（GAP-3：管理页验证通道，与真实事件同链路） ----------

    _SIM_MESSAGES = {
        "no_match": "没有命中的启用规则",
        "below_min_price": "礼物价值未达规则 min_price 门槛",
        "throttled": "触发过快被节流",
        "hold": "序列最小展示窗口内，他键触发被忽略",
        "in_progress": "同规则序列 main 未完成，忽略不计数",
        "disabled": "编排器未启用（enable=false）或 worker 未运行",
        "requeued": "已排队：当前 exit 播完后重排完整序列",
        "interrupted": "已打断当前序列并启动新序列",
        "accepted": "已启动新序列",
    }
    _SIM_FIXES = {
        "no_match": "检查规则 match 与触发值是否一致（keyword 为 NFKC 子串匹配）",
        "below_min_price": "降低规则 min_price 或使用真实礼物（裸 API 载荷 total_price 缺省 0）",
        "throttled": "等待 retry_after_s 后重试",
        "hold": "等待当前序列展示窗口结束（hold_remaining 秒）",
        "in_progress": "等待 main 步完成（exit 步中再触发会重排）",
        "disabled": "打开 choreography.enable 并应用，或检查 worker 启动日志",
        "requeued": "观察状态条 pending_requeue，exit 完成后自动重排",
        "interrupted": "观察状态条活动序列",
        "accepted": "观察状态条活动序列与画面切换",
    }

    def simulate_trigger(self, payload: dict) -> dict:
        """模拟触发（GAP-3）：与真实事件同链路（decide_trigger+worker 执行）。

        载荷两形态（义务 F3 裁决）：
        - 管理页主形态 {"rule_id": ...}：端点从规则配置合成载荷——gift 规则
          total_price := rule.min_price（恰好满足门槛），下拉任一规则必可触发；
        - 裸 API 形态 {"trigger_type", "match_value", 可选 "gift"}：gift 载荷
          白名单 gift_name/total_price/gift_num/unit_price，total_price 缺省 0
          （min_price>0 规则不命中为诚实门槛语义）。

        Returns: {accepted, reason, rule_id, retry_after_s, code, message, fix}
        reason 枚举见 decide_trigger（六拒绝值+accepted/interrupted/requeued）。
        """
        try:
            rule_id = payload.get("rule_id")
            with self._lock:
                if not self.worker_alive():
                    # 诚实失败（DX channel 裁决）：指令不进队列（无消费者堆积）
                    return self._sim_result({"accepted": False, "reason": "disabled",
                                             "retry_after_s": None}, None)
                if rule_id:
                    rule = self.engine.cfg.rule_by_id(str(rule_id))
                    if rule is None:
                        return self._sim_result({"accepted": False, "reason": "no_match",
                                                 "retry_after_s": None}, None)
                    data = {}
                    if rule.trigger_type == "gift":
                        data = {"gift_name": rule.match,
                                "total_price": float(rule.min_price),
                                "gift_num": 1, "unit_price": 0}
                    decision = self.engine.decide_trigger(rule.trigger_type, rule.match, data)
                else:
                    trigger_type = str(payload.get("trigger_type", "") or "")
                    match_value = str(payload.get("match_value", "") or "")
                    gift = payload.get("gift") if isinstance(payload.get("gift"), dict) else {}
                    data = {k: gift[k] for k in ("gift_name", "total_price", "gift_num", "unit_price")
                            if k in gift}
                    decision = self.engine.decide_trigger(trigger_type, match_value, data)
                self._dispatch(decision["commands"])
            result = self._sim_result(decision, decision.get("rule"))
            logger.info(
                f"[编排][模拟] rule={result.get('rule_id')} trigger={payload.get('trigger_type') or 'rule_id形态'} "
                f"accepted={result['accepted']} reason={result['reason']}"
            )
            return result
        except Exception as e:
            logger.error(f"[编排][模拟] 异常：problem=模拟触发失败；cause={type(e).__name__}: {e}；fix=查看编排日志")
            return {"accepted": False, "reason": "error", "rule_id": None,
                    "retry_after_s": None, "code": "error",
                    "message": f"模拟触发异常：{type(e).__name__}: {e}",
                    "fix": "查看编排日志"}

    def _sim_result(self, decision: dict, rule) -> dict:
        reason = decision.get("reason") or ("accepted" if decision.get("accepted") else "no_match")
        return {"accepted": bool(decision.get("accepted")), "reason": reason,
                "rule_id": (rule.rule_id if rule is not None else None),
                "retry_after_s": decision.get("retry_after_s"),
                "code": str(reason),
                "message": self._SIM_MESSAGES.get(reason, reason),
                "fix": self._SIM_FIXES.get(reason, "查看编排日志")}

    @staticmethod
    def _resolve_new_rule_material_props(cfg) -> None:
        """new 规则素材属性解析（T5/R-ceo4/R-eng2）：时长+口型标志物化。

        sidecar 读取（data/edtalk_materials_meta.json，main 单写者）；missing
        字段留 None（时长）/缺省 on（口型标志）——运行期兜底路径见 on_switch_result。
        hold_for_song：song 规则主线段（R-eng1 保持至歌曲结束）。
        """
        try:
            from utils.edtalk_realtime.materials import get_material_meta
        except Exception as e:
            logger.warning(f"[编排] sidecar 模块不可用（new 规则时长/口型未解析）：{e}")
            return
        for rule in cfg.rules:
            if rule.pred_version != "new":
                continue
            for step in (rule.enter, rule.main, rule.exit):
                if step is None:
                    continue
                try:
                    meta = get_material_meta(step.segment)
                except Exception as e:
                    logger.warning(f"[编排] 素材元数据读取失败 seg={step.segment}: {e}")
                    continue
                step.lip_flag = bool(meta.get("lip_flag", True))
                step.duration_s = meta.get("duration_s")
                if rule.trigger_type == "song" and step.name == "main":
                    step.hold_for_song = True

            def _fmt(step: Optional[StepCfg]) -> str:
                if step is None:
                    return "（未设）"
                return (f"{step.segment}({step.duration_s}s/lip={step.lip_flag}"
                        f"{'/hold_for_song' if step.hold_for_song else ''})")
            logger.info(
                f"[编排] new 规则 {rule.rule_id} 素材属性已解析："
                f"enter={_fmt(rule.enter)} main={_fmt(rule.main)} exit={_fmt(rule.exit)}")

    def apply_config(self, cfg_node: dict) -> dict:
        """运行时热换装（P2 管理页「应用」按钮；写者=orchestration.api 持久化）。

        流程：结构校验（fail-fast 逐规则）→ EDTalk 可达时段名第二段校验 →
        安全换装（序列执行中拒绝 409；DEFAULT 态换 cfg+enabled_rules，计数保留
        同名键、剔除失效键）→ 重新下发 DEFAULT 轮播。

        Returns: {"applied": bool, "status": 200|409|503, "enabled_rules": [...],
                  "rejected": [{rule_id, reason}], "missing_all": [...], "message": str}
        """
        new_cfg = load_choreography_config(_DottedNode({"choreography": cfg_node}))
        rejected = getattr(new_cfg, "_rejections", [])
        with self._lock:
            if self.engine.state == "sequence":
                return {"applied": False, "status": 409, "enabled_rules": [],
                        "rejected": rejected, "missing_all": [],
                        "message": "编排序列执行中，暂不能应用（exit 完成回默认后重试）"}
            # 段名第二段校验（EDTalk 可达时；不可达=保存但不生效，C5-1 语义）
            status = {}
            try:
                status = self.edtalk_client.get_status() or {}
            except Exception:
                status = {}
            segments = set(status.get("available_segments") or [])
            if not segments:
                return {"applied": False, "status": 503, "enabled_rules": [],
                        "rejected": rejected, "missing_all": [],
                        "message": "EDTalk 未连接（/status 无可用段）：规则已校验但未生效，"
                                   "服务就绪后重试应用"}
            result = validate_rule_segments(new_cfg.rules, segments)
            enabled_ids = set(result["enabled_rules"])
            # new 规则素材属性解析（R-ceo7/R-eng2/R-eng6③：时长+口型标志从
            # sidecar 物化进 RuleCfg——运行期零 I/O；FB-2 替代守卫的第二段位置。
            # 时长未知不拒绝（F-M1 两分法：允许保存+「生效待就绪」）——运行期
            # 由 on_switch_result 的 step_timeout_s 兜底+timeout_degrade 报警。
            # EDTalk 不可达时本段已在上方 503 返回（C5-1），此处必有 sidecar 可读。
            self._resolve_new_rule_material_props(new_cfg)
            # 换装：cfg/enabled_rules 替换；计数保留同名键（规则语义可能已变）
            new_engine = ChoreoEngine(new_cfg, clock=self._clock)
            new_engine.ready = self.engine.ready
            new_engine.enabled_rules = [r for r in new_cfg.rules if r.rule_id in enabled_ids]
            new_engine.counters = {k: v for k, v in self.engine.counters.items()
                                   if k in enabled_ids}
            new_engine.key_last = {k: v for k, v in self.engine.key_last.items()
                                   if k in enabled_ids}
            new_engine.degrade_total = self.engine.degrade_total
            old_engine = self.engine
            old_cfg = self.cfg
            # GAP-1：换装前补启动 worker（初始 enable=false → 管理页热启用场景）。
            # 启动失败=整体回滚换装（side effects 仅 complete/none 二值无 partial，
            # DX CAP6:apply:recovery）。
            need_worker = bool(new_cfg.enable and self.edtalk_client is not None)
            if need_worker and not self._ensure_worker(enable=new_cfg.enable):
                logger.error(
                    "[编排] 热启用失败已整体回滚：problem=worker 启动失败配置未生效；"
                    "cause=线程启动异常（见上方三段式日志）；"
                    "fix=检查配置与 EDTalk 启用状态后重新应用（fix and retry）"
                )
                return {"applied": False, "status": 503, "enabled_rules": [],
                        "rejected": rejected, "missing_all": [],
                        "worker_started": False, "ready": self.engine.ready,
                        "message": "worker 启动失败，配置未应用（已回滚；检查 EDTalk 启用状态后重试）"}
            self.engine = new_engine
            self.cfg = new_cfg
            # 重新下发 DEFAULT 轮播（default_segments 可能已变；权属线 CP-5）
            commands = new_engine.default_init_commands()
            self._dispatch(commands)
            logger.info(
                f"[编排] 运行时换装：{len(new_engine.enabled_rules)} 条规则生效，"
                f"{len(rejected)} 条结构拒绝，{len(result['disabled_rules'])} 条段名禁用"
            )
            return {"applied": True, "status": 200,
                    "enabled_rules": [r.rule_id for r in new_engine.enabled_rules],
                    "rejected": rejected,
                    "missing_all": result["missing_all"],
                    "worker_started": True,
                    "ready": new_engine.ready,
                    "message": "已应用并恢复默认轮播"}

    # ---------- 内部 ----------

    def _dispatch(self, commands: List) -> None:
        """命令入队（判定线程生产，工作线程串行消费——动作不占判定路径）。"""
        for cmd in commands:
            self._events.put(cmd)

    def _worker_main(self) -> None:
        """单工作线程：就绪探测 → 事件主循环（循环体整体 try/except，义务 S1-1）。"""
        # 就绪探测：首次 GET /status 成功（available_segments 非空）即就绪
        while not self._stop.is_set():
            try:
                status = self.edtalk_client.get_status() or {}
                segments = set(status.get("available_segments") or [])
                if segments:
                    with self._lock:
                        result = self.engine.on_edtalk_ready(segments)
                        if result["disabled_rules"]:
                            logger.warning(
                                f"[编排] 就绪校验：{len(result['enabled_rules'])} 条规则生效，"
                                f"{len(result['disabled_rules'])} 条禁用（资产缺口 {result['missing_all']}）"
                            )
                        for cmd in self.engine.default_init_commands():
                            self._events.put(cmd)
                    break
            except Exception as e:
                logger.warning(f"[编排] 就绪探测异常（重试）：{type(e).__name__}: {e}")
            self._stop.wait(READY_PROBE_INTERVAL_S)
        logger.info("[编排] EDTalk 就绪，编排器进入主循环")
        # 主循环：队列事件驱动 + deadline 轮询（F1 看门狗无额外线程）。
        # 队列混装两类载荷：判定线程投递的命令对象（SwitchSegment/TtsThanks/
        # EnqueueFile/RestoreDefault/LogCmd——IO 动作在本线程串行执行）与
        # 执行结果回填事件（元组——引擎状态推进）。
        while not self._stop.is_set():
            try:
                with self._lock:
                    deadline = self.engine.next_deadline(self._clock())
                now = self._clock()
                if deadline is None:
                    timeout = TICK_CAP_S
                else:
                    timeout = max(0.0, min(deadline - now, TICK_CAP_S))
                try:
                    item = self._events.get(timeout=timeout)
                except queue.Empty:
                    item = ("tick",)
                if isinstance(item, (SwitchSegment, TtsThanks, EnqueueFile, RestoreDefault, LogCmd)):
                    self._execute([item])
                else:
                    self._handle_event(item)
            except Exception as e:
                # worker 循环体防护（S1-1）：记日志继续，与 F1 超时看门狗双保险
                logger.error(f"[编排] 工作循环异常（已继续）：problem=单事件处理失败；cause={type(e).__name__}: {e}；fix=循环体防护，后续事件照常")

    def _handle_event(self, event) -> None:
        """单事件处理（锁内引擎变更，锁外 IO）。"""
        kind = event[0]
        with self._lock:
            if kind == "tick":
                commands = self.engine.on_tick(self._clock())
            elif kind == "switch_result":
                _, step_id, ok = event
                commands = self.engine.on_switch_result(step_id, ok, self._clock())
            elif kind == "audio_complete":
                _, qtype, content = event
                commands = self.engine.on_audio_complete(qtype, content, self._clock())
            elif kind == "audio_enqueued":
                _, step_id, qtype, content, ok = event
                commands = self.engine.on_audio_enqueued(step_id, qtype, content, ok, self._clock())
            elif kind == "restore_result":
                _, ok = event
                commands = self.engine.on_restore_result(ok, self._clock())
            elif kind == "song_started":
                _, song_name, remaining_s = event
                commands = self.engine.on_song_started(song_name, remaining_s,
                                                       self._clock())
            else:
                commands = []
        self._execute(commands)

    # ---------- lip 联动（T6/R-eng5：性能层；失败=debug 日志，off→on 方向读回重试） ----------

    def _apply_lip_linkage(self, need_on: bool, step_id: str) -> None:
        """下发口型门控（去抖+读回断言+一次重试；线程=worker 或 lip-linkage）。"""
        want = "on" if need_on else "off"
        if getattr(self, "_lip_mode_cache", None) == want:
            return  # R-eng5 MED-4 去抖：值不变不下发
        result = self.edtalk_client.set_lip_params(want)
        if not result.get("ok"):
            self._lip_linkage_fail(want, step_id, result, retried=False)
            return
        self._lip_mode_cache = want
        # F-M5 off→on 读回断言（on 失败=数字人静止不张嘴=可见缺陷）
        if want == "on":
            st = {}
            try:
                st = self.edtalk_client.get_status() or {}
            except Exception:
                pass
            if st.get("lip_mode") != "on":
                retry = self.edtalk_client.set_lip_params("on")
                if retry.get("ok"):
                    self._lip_mode_cache = "on"
                    logger.info(f"[编排] lip 联动读回不符已重试成功（step={step_id}）")
                else:
                    self._lip_linkage_fail("on", step_id, retry, retried=True)
        logger.debug(f"[编排] lip 联动：{want}（step={step_id}）")
        self._apply_avatar_aux_flags(need_on, step_id)

    # ---------- 面部修复/眼神修正常开关联动（2026-10-27 动作编排完善批次） ----------

    def _avatar_config_mode(self, key: str) -> str:
        """webui 数字人配置初值（edtalk_realtime.face_repair/gaze_correction，
        值域 on|adaptive|off；异常降级 off=不主动开启）。"""
        try:
            get = getattr(self._config, "get", None)
            if get is None and isinstance(self._config, dict):
                node = self._config.get("edtalk_realtime") or {}
                return str(node.get(key, "off") or "off")
            v = get("edtalk_realtime", key)
            return str(v or "off")
        except Exception:
            return "off"

    def _apply_avatar_aux_flags(self, need_on: bool, step_id: str) -> None:
        """面部修复/眼神修正热开关（用户裁决 2026-10-27）。

        不需口型的素材（跳舞/画外音直通）→ 口型推理、视线修正、面部修复全关，
        相关音频作画外音由系统播放流程消费；需口型素材（唱歌/带货/主线口型）
        → 恢复 webui 配置初值。409（启动未构造 capable=false）= 降级 debug，
        性能层不阻塞切换、不告警（功能未装配属常态非故障）。
        """
        for kind, client_fn, cache_attr in (
                ("repair", "set_repair_params", "_repair_mode_cache"),
                ("gaze", "set_gaze_params", "_gaze_mode_cache")):
            want = self._avatar_config_mode(
                "face_repair" if kind == "repair" else "gaze_correction") \
                if need_on else "off"
            fn = getattr(self.edtalk_client, client_fn, None)
            if fn is None:
                continue
            if getattr(self, cache_attr, None) == want:
                continue  # 去抖：值不变不下发
            try:
                result = fn(want) or {}
            except Exception as e:
                logger.debug(f"[编排] {kind} 联动异常（step={step_id}）：{e}")
                continue
            if result.get("ok"):
                setattr(self, cache_attr, want)
                logger.debug(f"[编排] {kind} 联动：{want}（step={step_id}）")
            else:
                # 409=启动未构造（常态）；其它失败=debug（性能层，方向 off 无害）
                logger.debug(f"[编排] {kind} 联动失败（step={step_id} want={want}）：{result}")

    def _lip_linkage_fail(self, want: str, step_id: str, result: dict,
                          retried: bool) -> None:
        """联动失败（R-eng5 性能层）：debug 日志；on 方向低频告警（可见缺陷）。"""
        logger.debug(
            f"[编排] lip 联动失败（step={step_id} want={want} retried={retried}）："
            f"{result}")
        if want == "on":
            alarm_notifier.alarm_error(
                alarm_notifier.MODULE_ORCHESTRATION,
                f"编排 lip 联动置 on 失败（step={step_id}）——数字人可能静止不张嘴",
                fix_hint="检查 EDTalk 服务与 /lip/params 端点；切回 on 素材会自动重试")

    def _lip_linkage_at_boundary(self, segment: str, need_on: bool,
                                 step_id: str) -> None:
        """segment_end 切换的边界轮询联动（T0b 降级语义：轮询 current_segment
        达成后前置位；不阻塞 worker；超时 60s 放弃=容忍窗写明于验收清单）。"""
        deadline = time.time() + 60.0
        while time.time() < deadline:
            try:
                st = self.edtalk_client.get_status() or {}
                if st.get("current_segment") == segment:
                    self._apply_lip_linkage(need_on, step_id)
                    return
            except Exception:
                pass
            time.sleep(1.0)
        logger.debug(f"[编排] lip 联动边界轮询超时（step={step_id} seg={segment}）——"
                     "容忍窗内口型可能提前/滞后")

    def _execute(self, commands: List) -> None:
        """命令执行（工作线程；HTTP 调用与音频入队，结果回填事件队列）。"""
        for cmd in commands:
            try:
                if isinstance(cmd, LogCmd):
                    getattr(logger, cmd.level, logger.info)(cmd.msg)
                elif isinstance(cmd, SwitchSegment):
                    result = self.edtalk_client.switch_segment(cmd.segment, at=cmd.at)
                    ok = bool(result.get("ok", True)) if isinstance(result, dict) else False
                    self._events.put(("switch_result", cmd.step_id, ok))
                    # lip 联动（T6/R-ceo2③/R-eng5 性能层）：按当前步素材口型标志
                    # 前置位。immediate=切换受理即联动（容忍窗≤2帧）；segment_end=
                    # 后台轮询边界（不阻塞 worker——单线程，阻塞会拖垮 deadline 轮询）。
                    # 失败=debug 日志（on→off 方向无害）；off→on 失败=可见缺陷→
                    # 读回断言+一次重试+低频告警（F-M5 方向限定）。
                    with self._lock:
                        _lip_need = self.engine.current_step_lip_need()
                    if _lip_need is not None:
                        if cmd.at == "immediate":
                            self._apply_lip_linkage(_lip_need, cmd.step_id)
                        else:
                            threading.Thread(
                                target=self._lip_linkage_at_boundary,
                                args=(cmd.segment, _lip_need, cmd.step_id),
                                daemon=True, name="lip-linkage").start()
                elif isinstance(cmd, TtsThanks):
                    # 经注入引用复用原生答谢链（E-2 限流+周期通道全复用，WP-3）
                    try:
                        message = self._thanks_generate("gift", cmd.data, cmd.variables)
                    except Exception as e:
                        logger.error(f"[编排] tts 步骤答谢生成异常：{type(e).__name__}: {e}")
                        message = None
                    if message and isinstance(message, dict):
                        self._events.put(("audio_enqueued", cmd.step_id, message.get("type", "gift"),
                                          message.get("content", ""), True))
                    else:
                        # 生成失败/开关关闭：无音频会来 → 立即完成该步（F1 语义前置）
                        self._events.put(("audio_enqueued", cmd.step_id, "gift", "", False))
                elif isinstance(cmd, EnqueueFile):
                    try:
                        self._audio_enqueue({"type": "song", "content": cmd.audio_path})
                        ok = True
                    except Exception as e:
                        logger.error(f"[编排] file 音轨入队异常：problem=本地音频未入队；cause={type(e).__name__}: {e}；fix=检查文件路径与 play_audio.enable")
                        ok = False
                    # 关联键=(type,content)：song 直通 data_json 的 content=路径（synthesis_manager :153 同构）
                    self._events.put(("audio_enqueued", cmd.step_id, "song", cmd.audio_path, ok))
                elif isinstance(cmd, RestoreDefault):
                    result = self.edtalk_client.schedule(cmd.sequence, align=True,
                                                         loop=cmd.loop)
                    ok = bool(result.get("ok", True)) if isinstance(result, dict) else False
                    self._events.put(("restore_result", ok))
            except Exception as e:
                logger.error(f"[编排] 命令执行异常（降级继续）：problem={type(cmd).__name__} 执行失败；cause={type(e).__name__}: {e}；fix=依赖超时/重试机制兜底")

    def shutdown(self) -> None:
        self._stop.set()


# 模块级 holder（沿 captions_manager / edtalk_client 注册模式）
_choreographer_holder = {"instance": None}


def register_choreographer(instance: ActionChoreographer) -> None:
    """注册编排器实例（My_handle 装配时调用），供 handler 订阅点读取。"""
    _choreographer_holder["instance"] = instance


def get_choreographer() -> Optional[ActionChoreographer]:
    return _choreographer_holder["instance"]
