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
    """回默认轮播（条件1 exit 完成；rotate 原序 / random 每次打乱）。"""
    sequence: list


@dataclass
class LogCmd:
    level: str
    msg: str


def _normalize_text(text: str) -> str:
    """S-2 规范化思路的独立实现：NFKC + 去空白 + casefold（义务 CL-3）。"""
    import unicodedata
    return "".join(unicodedata.normalize("NFKC", str(text or "")).split()).casefold()


def select_tier(rule: RuleCfg, count: int) -> Optional[object]:
    """情绪档选择：最后一个 max_count >= count 的档；超档用最高档（CP-4）。"""
    selected = None
    for tier in rule.tiers:
        if count <= tier.max_count:
            selected = tier
            break
        selected = tier  # count 超过当前档上界 → 继续用更高档（末档兜底）
    return selected


def build_steps(rule: RuleCfg, count: int, seq_counter: int) -> List[dict]:
    """按当前计数档位物化三步（segment_overrides 可覆盖任一步，CL-4/WP-10）。"""
    tier = select_tier(rule, count)
    overrides = tier.segment_overrides if tier else {}
    steps = []
    for name in ("enter", "main", "exit"):
        base: StepCfg = rule.name_step(name)
        steps.append({
            "step_id": f"{rule.rule_id}:{name}#{seq_counter}",
            "name": name,
            "segment": overrides.get(name, base.segment),
            "switch_at": base.switch_at,
            "duration_s": base.duration_s,
            "audio_mode": base.audio_mode,
            "audio_path": base.audio_path,
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
        self.degrade_total = 0
        self._seq_counter = 0
        self._restore_attempts = 0
        # 活动序列状态（state == "sequence" 时非 None）
        self.seq: Optional[dict] = None

    # ---------- 触发判定（同步纯内存，on_gift/on_keyword/on_song 路径） ----------

    def decide_trigger(self, trigger_type: str, match_value: str, data: dict) -> dict:
        """触发判定。Returns: {accepted, takeover, rule, commands}"""
        now = self.clock()
        commands: List = []
        if not self.cfg.enable or not self.enabled_rules:
            return {"accepted": False, "takeover": False, "rule": None, "commands": commands}
        rule = self._match_rule(trigger_type, match_value, data)
        if rule is None:
            return {"accepted": False, "takeover": False, "rule": None, "commands": commands}

        # 节流窗口（每规则 min_retrigger_s，防连点风暴）：忽略不计数（WP-6 同语义）
        last = self.key_last.get(rule.rule_id)
        if last is not None and (now - last) < self.cfg.min_retrigger_s:
            self.key_last[rule.rule_id] = now
            return {"accepted": False, "takeover": False, "rule": rule, "commands": commands}
        self.key_last[rule.rule_id] = now

        if self.state == "sequence" and self.seq is not None:
            seq = self.seq
            # 全局序列最小展示时间（F5）：hold 内他键触发忽略（不终止不计数）
            if rule.rule_id != seq["rule_id"] and now < seq["hold_until"]:
                return {"accepted": False, "takeover": False, "rule": rule, "commands": commands}
            if rule.rule_id == seq["rule_id"]:
                # 条件2（同键再触发）
                if not seq["main_done"]:
                    # main 未完成：忽略本次触发且不计数（WP-6）
                    return {"accepted": False, "takeover": False, "rule": rule, "commands": commands}
                # main 已完成（处 exit 步）：当前 exit 播完后重排完整三步（WP-9）
                self.counters[rule.rule_id] = self.counters.get(rule.rule_id, 0) + 1
                self._seq_counter += 1
                seq["pending_requeue"] = build_steps(rule, self.counters[rule.rule_id], self._seq_counter)
                commands.append(LogCmd("info", f"[编排] 规则 {rule.rule_id} 同键重入（第 {self.counters[rule.rule_id]} 次），当前 exit 播完后重排"))
                return {"accepted": True, "takeover": rule.has_tts_audio(), "rule": rule, "commands": commands}
            # 条件3（他键触发，beyond hold）
            return self._interrupt_sequence(rule, data, now, commands)

        # DEFAULT 态：启动新序列（条件1 的正常入口）
        return self._start_sequence(rule, data, now, commands)

    def _match_rule(self, trigger_type: str, match_value: str, data: dict) -> Optional[RuleCfg]:
        """规则匹配（义务 C3-3/WP-10：gift 全等+min_price / keyword 独立词表子串 / song 可空=任意）。"""
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
                        return rule
            elif trigger_type == "keyword":
                if _normalize_text(match_value).find(_normalize_text(rule.match)) >= 0:
                    return rule
            elif trigger_type == "song":
                if not rule.match or _normalize_text(match_value).find(_normalize_text(rule.match)) >= 0:
                    return rule
        return None

    def _start_sequence(self, rule: RuleCfg, data: dict, now: float, commands: List) -> dict:
        """DEFAULT→SEQUENCE：计数+1、按档位物化三步、发出 enter 切换。"""
        self.counters[rule.rule_id] = self.counters.get(rule.rule_id, 0) + 1
        self._seq_counter += 1
        steps = build_steps(rule, self.counters[rule.rule_id], self._seq_counter)
        self.seq = {
            "rule_id": rule.rule_id,
            "steps": steps,
            "idx": 0,
            "hold_until": now + self.cfg.min_sequence_hold_s,
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
            "hold_until": now + self.cfg.min_sequence_hold_s,
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
            return self._complete_step(now, timeout_degrade=False)
        self.seq["step_accepted"] = True
        self.seq["accepted_at"] = now
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
        if timeout_degrade:
            self.degrade_total += 1
        step = seq["steps"][seq["idx"]]
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
        # 条件1：exit 完成 → 回默认轮播（rotate 原序 / random 每次打乱 / single 池首）
        self.state = "default"
        self._restore_attempts = 0
        pool = self._default_pool()
        mood = seq.get("tier_mood") or "base"
        commands.append(LogCmd("info",
                               f"[编排] {seq['rule_id']} 序列播完（mood={mood}），回默认轮播"
                               f"（计数保留 {self.counters.get(seq['rule_id'], 0)}）"))
        if pool:
            commands.append(RestoreDefault(pool))
        else:
            commands.append(LogCmd("warning",
                                   "[编排] 默认段池为空，无法恢复轮播；problem=default_segments 校验后为空；"
                                   "cause=段名不在 EDTalk available_segments；fix=修正 default_segments"))
        self.seq = None
        return commands

    def on_restore_result(self, ok: bool, now: float) -> List:
        """恢复默认结果回填：失败重试（恢复是回默认唯一路径，义务 S1-1）。"""
        if ok:
            self._restore_attempts = 0
            return []
        self._restore_attempts += 1
        if self._restore_attempts <= RESTORE_RETRY_MAX:
            pool = self._default_pool()
            if pool:
                logger.warning(f"[编排] 恢复默认失败，第 {self._restore_attempts}/{RESTORE_RETRY_MAX} 次重试")
                return [RestoreDefault(pool)]
        logger.error(
            "[编排] 恢复默认轮播重试耗尽：problem=画面停留在序列末段；"
            "cause=EDTalk /segment/schedule 连续失败；fix=检查 EDTalk 服务后手动切回或重启编排"
        )
        self._restore_attempts = 0
        return []

    def _default_pool(self) -> list:
        """按 default_mode 物化恢复序列（C2-3：random=每次打乱后下发 /segment/schedule）。"""
        pool = list(self.cfg.default_segments)
        if not pool:
            return []
        if self.cfg.default_mode == "single":
            return [pool[0]]
        if self.cfg.default_mode == "random":
            random.shuffle(pool)
        return pool  # rotate：原序（上游 SegmentScheduler 顺序循环承载）

    def next_deadline(self, now: float) -> Optional[float]:
        """worker 等待队列的超时基准（None=无近期 deadline）。"""
        if self.seq is not None and self.seq.get("current_deadline") is not None:
            return self.seq["current_deadline"]
        return None

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
        return [LogCmd("info", f"[编排] DEFAULT 初始化：mode={self.cfg.default_mode}，池={self.cfg.default_segments}"),
                RestoreDefault(self._default_pool())]

    def get_state(self) -> dict:
        """状态快照（P2 状态条/测试断言用：当前模式/活动序列/各键计数/降级计数）。"""
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
            "degrade_total": self.degrade_total,
            "enabled_rules": [r.rule_id for r in self.enabled_rules],
        }


class ActionChoreographer:
    """编排器门面：同步判定 API（handler 线程调用）+ 单工作线程执行。"""

    def __init__(self, config, edtalk_client, audio_enqueue: Callable,
                 thanks_generate: Callable, clock: Callable[[], float] = time.monotonic):
        self.cfg: ChoreographyConfig = load_choreography_config(config)
        self.edtalk_client = edtalk_client
        self._audio_enqueue = audio_enqueue      # my_handle.audio_synthesis_handle
        self._thanks_generate = thanks_generate  # event_handler._thanks_generate
        self._clock = clock
        self.engine = ChoreoEngine(self.cfg, clock=clock)
        self._lock = threading.RLock()
        self._events: "queue.Queue" = queue.Queue()
        self._worker: Optional[threading.Thread] = None
        self._stop = threading.Event()
        # enable=false 或 EDTalk 客户端缺失：不启动 worker（订阅点短路直通，
        # 关闭=全链零行为变化——义务"总开关"）
        if self.cfg.enable and self.edtalk_client is not None:
            self._worker = threading.Thread(target=self._worker_main, daemon=True,
                                            name="action-choreographer")
            self._worker.start()

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

    def get_state(self) -> dict:
        with self._lock:
            return self.engine.get_state()

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
            else:
                commands = []
        self._execute(commands)

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
                    result = self.edtalk_client.schedule(cmd.sequence, align=True)
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
