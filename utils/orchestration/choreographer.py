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


def _load_priority_mapping(config) -> dict:
    """读取 filter.priority_mapping（O1/O3，2026-10-28 消费批次）。

    兼容形态：Config 实例（get 两参）/ dict（点式取 filter.priority_mapping）。
    异常或非 dict 一律返回空表——引擎侧权重全 0 档退化（排队 FIFO、standard
    打断不受限），行为与改造前一致，安全。
    """
    try:
        node = None
        get = getattr(config, "get", None)
        if get is not None:
            node = get("filter", "priority_mapping")
        elif isinstance(config, dict):
            node = _DottedNode(config).get("filter", "priority_mapping")
        return dict(node) if isinstance(node, dict) else {}
    except Exception:
        return {}

# 事件队列轮询间隔上限（秒）——无近期 deadline 时的最大等待粒度
TICK_CAP_S = 1.0
# 恢复默认轮播重试参数（义务：恢复是回默认唯一路径，失败重试）
RESTORE_RETRY_MAX = 3
RESTORE_RETRY_INTERVAL_S = 5.0
# EDTalk 就绪探测间隔（秒）
READY_PROBE_INTERVAL_S = 5.0

# O7（2026-10-28 消费批次）：编排音轨插队权重（data_json.priority_override）——
# 高于常规语音权重域（config priority_mapping 实测 10~30），低于 999 复读专线；
# 固定值不占配置面。编排音轨与画面同程（先切段后入队），积压时插队是
# 画面-音频对齐的核心手段，故不开放配置避免用户误设低于常规语音
CHOREO_AUDIO_PRIORITY = 500


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


@dataclass
class CancelAudio:
    """O8（2026-10-28 消费批次）：打断撤旧音频——撤销旧序列未消费的 inflight
    音轨（message_queue/voice_tmp_path_queue 中匹配 choreo_step_id 的条目）。
    已在播/已消费的无法撤销：其完成回调因 inflight 已重置被静默忽略。"""
    step_id: str


def _normalize_text(text: str) -> str:
    """S-2 规范化思路的独立实现：NFKC + 去空白 + casefold（义务 CL-3）。"""
    import unicodedata
    return "".join(unicodedata.normalize("NFKC", str(text or "")).split()).casefold()


def _completion_key(text: str) -> str:
    """完成匹配回退路径的归一化键（O9，2026-10-28 消费批次）：S-2 去空白后再
    剥标点（isalnum 过滤）——下游改写形态（换行→句号、remove_extra_words
    点缀词裁剪、首尾空白）容差匹配，改写不再造成回退路径误失配。"""
    return "".join(ch for ch in _normalize_text(text) if ch.isalnum())


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

    PENDING_QUEUE_MAX = 5  # queue 模式多槽上限（2026-10-28 G3；O2：满额挤最低优）

    # 触发类型 → filter.priority_mapping 权重键映射（O1/O3，2026-10-28 消费批次）：
    # keyword 触发源=弹幕（语义同 comment 权重）；gift/song 与映射表同名直取
    # （d22 off=整体回退现行为时仍走本表——d10 on 态改读编排仲裁表）。
    _TRIGGER_PRIORITY_KEY = {"gift": "gift", "song": "song", "keyword": "comment"}

    # d10（2026-11-05 编排优化批次；S-1 修订取代 d5）：编排面独立仲裁权重表——
    # 与 filter.priority_mapping 完全解耦（生产 trends_copywriting=999 会污染
    # 编排面：礼物应打断动态文案却被挡排队，违背方案 §2/§3 优先级语义）。
    # 覆盖粒度=类型键级（choreography.arbitration_weights 可覆盖单键，T1 装载
    # 校验已限值域）；表外类型回退 0 保守（最低档=排队退化既有 FIFO 安全）。
    # entrance/follow 恒 0 钳制不变（三强制二，优先级高于本表与开关）。
    _ARBITRATION_WEIGHTS_DEFAULT = {"gift": 30, "song": 20, "dynamic_text": 15, "keyword": 10}

    # T4 三强制二（Eng A2）：entrance/follow 恒强制最低权重档——priority_mapping
    # 现成 entrance=55/follow=65（PRIORITY_PRESETS），不钳制则入场/关注天然获得
    # 仅次于 comment(80)/gift(70) 的高权重，风暴防护失效。配置值仅 load 告警。
    _CLAMPED_PRIORITY_TYPES = ("entrance", "follow")

    # T4 三强制三（R-ceo5 弹幕源定向例外，Eng F1）：entrance/follow 引擎内类型
    # 专属最小触发间隔（按 trigger_type 全局计时——同类跨规则共享一个计时器，
    # 间隔内同类全部触发静默忽略；DEFAULT 态与序列态统一生效）。事件流虽经
    # process_data 六类限入&去重管线（2026-11-04 事实修正：原"无上游去重"表述
    # 有误——danmaku_consumer 六类均走 process_data），但用户名键去重拦不住
    # 不同用户集中进场、限入窗口参数可调宽——本节流为不依赖上游配置的防御
    # 纵深；常量起步不暴露配置键，实播反馈后评估可配置化。
    ENTRANCE_FOLLOW_MIN_INTERVAL_S = 10.0

    # d22 看门狗（T4）：song 伴唱挂起硬上限——deadline（歌曲结束）为正常退出
    # 锚；本上限兜底「挂起锚永不刷新」的异常态（歌曲链路死亡且无新切片起播）。
    # 值域须 ≥ song 最长链路（切片×队列总时长可较长，1h 起步保守）。
    SONG_SUSPEND_MAX_S = 3600.0

    def __init__(self, cfg: ChoreographyConfig, clock: Callable[[], float] = time.monotonic,
                 priority_mapping: Optional[dict] = None):
        self.cfg = cfg
        self.clock = clock
        self.priority_mapping: dict = dict(priority_mapping or {})  # O1/O3：全局权重表快照
        self.state = "default"
        self.ready = False
        self.enabled_rules: List[RuleCfg] = list(cfg.rules)
        self.counters: dict = {}          # rule_id -> consecutive_count
        self.key_last: dict = {}          # rule_id -> 上次触发尝试时刻（节流/衰减均已退役——保留兼容锚点，引擎零消费）
        self.pending_queue: list = []     # queue 模式待播多槽（O1：权重降序插入）
        self.degrade_total = 0
        self._seq_counter = 0
        self._restore_attempts = 0
        self._type_last: dict = {}        # T4：trigger_type -> 上次放行时刻（类型专属节流）
        # 活动序列状态（state == "sequence" 时非 None）
        self.seq: Optional[dict] = None
        # song 伴唱挂起态（T4/d9/e4，2026-11-05 批次）：seq 之外的旁路跟踪结构
        # ——seq 被 _interrupt_sequence 整体替换无法承载挂起。字段=e4 契约：
        # rule_id（恢复画面归属）/deadline（歌曲结束锚点，on_song_started 挂起
        # 分派刷新——F-B）/entered_at（d22 看门狗硬上限锚）。切片游标/伴奏状态
        # 单点持有者=音频侧（F-B：编排侧只持 deadline 锚，经既有 on_song_started
        # 事件通道回写，防两份状态漂移）。单槽：后一次挂起覆盖前次（最新歌曲
        # 请求的 deadline 为活跃锚；前序歌曲经 d11 入歌曲队列由音频侧排序）。
        self.song_suspended: Optional[dict] = None

        # T4/A2 装载告警（一次）：priority_mapping 显式配置 entrance/follow 时提示钳制
        for _t in self._CLAMPED_PRIORITY_TYPES:
            if _t in self.priority_mapping:
                logger.warning(
                    f"[编排] filter.priority_mapping 含 {_t}={self.priority_mapping[_t]}，"
                    "该值对编排不生效（entrance/follow 权重恒强制最低档 0——风暴防护"
                    "三强制，2026-11-01 批次）；如需调整请修改编排规则触发类型")

    def _arbitration_active(self) -> bool:
        """仲裁总开关（d22）：off=整体回退现行为（直播中出问题一键回退无需重启）。

        off 域：d7 窗口清零、d10 编排表（回落 legacy _TRIGGER_PRIORITY_KEY→
        filter.priority_mapping 读取）、T4 song 序列特判、d18 规则级 999 注入
        （F-E：off 态 override 通道不注入——999 判定收敛回既有 type 映射单源）。"""
        return bool(getattr(self.cfg, "arbitration_enable", True))

    def _rule_priority(self, rule: Optional[RuleCfg]) -> int:
        """规则权重（O1/O3 → d10 改读编排表，2026-11-05 批次）。

        开关 on（d10）：编排仲裁表——内置默认（gift=30 > song=20 >
        dynamic_text=15 > keyword=10）+ choreography.arbitration_weights 类型
        键级覆盖（T1 装载已校验值域）；表外类型回退 0 保守（最低档：排队退化
        既有 FIFO、打断不受限，行为安全）。
        开关 off（d22）：整体回退现行为——legacy _TRIGGER_PRIORITY_KEY 映射
        filter.priority_mapping 读取（keyword→comment 等，2026-10-28 语义）。
        两态下 entrance/follow 恒返回 0（T4 三强制二——风暴防护优先于开关与
        任何配置，装载期已告警）。"""
        if rule is None:
            return 0
        _tt = getattr(rule, "trigger_type", "")
        if _tt in self._CLAMPED_PRIORITY_TYPES:
            return 0
        if not self._arbitration_active():
            key = self._TRIGGER_PRIORITY_KEY.get(_tt)
            p = self.priority_mapping.get(key) if key else None
            try:
                return int(p) if p is not None else 0
            except (TypeError, ValueError):
                return 0
        table = dict(self._ARBITRATION_WEIGHTS_DEFAULT)
        table.update(getattr(self.cfg, "arbitration_weights", None) or {})
        p = table.get(_tt)
        try:
            return int(p) if p is not None else 0
        except (TypeError, ValueError):
            return 0

    def _expire_counter_window(self, rule_id: str, now: float) -> None:
        """d7 连续触发窗口（滑动锚点，d24a）：窗外触发=该规则计数清零回落基础档。

        窗口=choreography.continuous_trigger_window_s（T1 装载，默认 30；
        0=禁用=永续累计——2026-10-31 退役 reset_after_s 后的现行为）；总开关
        off 同样禁用（d22 整体回退）。清零是继 D-4 他键打断、配置重存换装之后
        第三种计数清零源。调用点=decide_trigger 命中规则后、key_last 锚点刷新
        前（此刻 key_last 仍持上一触发时刻；type_throttled 早退与未匹配不经过
        此处=不刷新不判定——d7 终门标注语义）。"""
        window = float(getattr(self.cfg, "continuous_trigger_window_s", 0.0) or 0.0)
        if not (self._arbitration_active() and window > 0):
            return
        anchor = self.key_last.get(rule_id)
        if anchor is None or (now - anchor) <= window:
            return
        if self.counters.get(rule_id):
            logger.info(
                f"[编排] 规则 {rule_id} 超出连续触发窗口（间隔 {now - anchor:.1f}s > "
                f"{window:g}s）：计数 {self.counters[rule_id]}→0 回落基础档（d7 滑动窗口）")
        self.counters[rule_id] = 0

    # ---------- 触发判定（同步纯内存，on_gift/on_keyword/on_song 路径） ----------

    def decide_trigger(self, trigger_type: str, match_value: str, data: dict) -> dict:
        """触发判定。Returns: {accepted, takeover, rule, commands, reason, retry_after_s}

        reason 枚举（GAP-3/F2 义务；T4 扩展+退役值见下）：拒绝值 =
        no_match / below_min_price / type_throttled（T4：entrance/follow 类型
        专属节流，retry_after_s=距放行秒数）/ disabled；
        接受值 = accepted（DEFAULT 启动）/ interrupted（条件3 打断）/
        requeued（条件2 重排，accepted=true 说明值）+ queued/queued_full（排队，
        accepted=false）。历史值 throttled/hold 已随 R-ceo5 退役（Eng H3）。
        模拟触发与观测消费。
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

        # 节流窗口（R-ceo5，2026-10-27 批次）与计数时间衰减（reset_after_s，
        # 2026-10-31 动作编排审查批次任务书"计数窗口弃用直接移除"）均已退役：
        # 弹幕层限时去重在上游拦截重复；触发计次不再随时间清零（清零仅剩
        # 他键打断 D-4 与配置重存换装）；同键重入走条件2 计数+重排。
        # key_last 保留作兼容锚点（测试旁路句柄；引擎内零消费）。
        # T4 三强制三（Eng F1）：entrance/follow 类型专属节流——按 trigger_type
        # 全局计时（同类跨规则共享窗口），间隔内同类全部触发静默忽略（不刷新
        # 窗口：固定间隔，风暴持续也不会永久阻塞）；DEFAULT 态与序列态统一生效
        # （风暴在 DEFAULT 态同样直通切换，队列防护不覆盖该面）。窗口外放行并
        # 记时刻。计次不受节流影响（被节流触发不进 decide 后续=不计数）。
        if trigger_type in ("entrance", "follow"):
            last = self._type_last.get(trigger_type)
            if last is not None and (now - last) < self.ENTRANCE_FOLLOW_MIN_INTERVAL_S:
                retry = round(self.ENTRANCE_FOLLOW_MIN_INTERVAL_S - (now - last), 1)
                commands.append(LogCmd(
                    "debug", f"[编排] {trigger_type} 类型节流：窗口内静默忽略"
                             f"（{rule.rule_id}，{retry}s 后放行同类下一个）"))
                self._type_last[trigger_type] = last  # 固定窗口，不刷新
                return {"accepted": False, "takeover": False, "rule": rule,
                        "commands": commands, "reason": "type_throttled",
                        "retry_after_s": retry}
            self._type_last[trigger_type] = now

        # d7 连续触发窗口（2026-11-05 批次）：窗口判定在锚点刷新前——此刻
        # key_last 仍持上一触发时刻，窗外=计数清零（本次触发随后从基础档重新
        # 计数）。滑动锚点（d24a）：下一行的写入即锚点刷新，命中路径
        # （in_progress/requeued/queued/interrupted/DEFAULT 启动）统一刷新——
        # type_throttled 早退与未匹配不经过此处=不刷新（e6 断言锚定）。
        self._expire_counter_window(rule.rule_id, now)
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
            # queue 模式（2026-10-27 批次；2026-10-28 O1/O2：权重降序排队+满额挤低优）：
            # 低权重触发不打断当前序列——入待播队列按 priority_mapping 权重降序
            # 插入（同权重保持 FIFO），当前序列播完（exit 完成）后自队首（最高
            # 权重）出队重放。消息/音频侧照常入链（Sort 排序消费），本排队仅作用
            # 于画面序列。
            # d11（song×song，2026-11-05 批次）：歌曲播放期间又有点歌=点歌队列
            # 追加承接（choose_song 既有歌曲排队——音频侧职责），编排 song 序列
            # 保持 hold 不重排不重入（deadline 由切片自校正覆盖队列总时长）。
            # 先于 queue 门：song 触发的「排队」归属歌曲队列而非编排画面队列。
            # 非 song 场景同权重保持现行抢占语义不变（仅 song 特判）。
            if (self._arbitration_active() and seq.get("hold_for_song")
                    and trigger_type == "song"):
                commands.append(LogCmd(
                    "info", f"[编排] song hold 中 song 重触发：点歌队列追加承接"
                            f"（d11，序列保持 hold，{rule.rule_id} 入歌曲队列）"))
                return {"accepted": True, "takeover": False, "rule": rule,
                        "commands": commands, "reason": "song_queued",
                        "retry_after_s": None}
            if rule.interrupt == "queue":
                reason = self._enqueue_pending(rule, trigger_type, match_value, data, commands)
                return {"accepted": False, "takeover": False, "rule": rule,
                        "commands": commands, "reason": reason, "retry_after_s": None}
            # T8/d13（resident 让位，镜像音频面 uc5b 字面语义）：999 resident
            # 序列让位任意 standard 触发——权重门不适用（999 音频面被任何消息
            # 暂停=画面同拍让位，打断者完成后切回播报画面）。§3 Case B：打赏
            # 答谢(30) 须打断 999 文案(999) 视觉——权重比较在此失效。
            if self._arbitration_active() and seq.get("resident"):
                result = self._suspend_hold_for(rule, data, now, commands, "resident")
                result["reason"] = "interrupted"
                result["retry_after_s"] = None
                return result
            # O3（2026-10-28 消费批次）：standard 打断权重门——低权重触发不得打断
            # 高权重序列（改走排队），同权重保持既有抢占语义（含打断清零 D-4）。
            cur_p = self._rule_priority(self.cfg.rule_by_id(seq["rule_id"]))
            new_p = self._rule_priority(rule)
            if new_p < cur_p:
                self._enqueue_pending(rule, trigger_type, match_value, data, commands)
                commands.append(LogCmd(
                    "info", f"[编排] 规则 {rule.rule_id}（p={new_p}）权重低于当前序列 "
                            f"{seq['rule_id']}（p={cur_p}），standard 打断让位改排队"))
                return {"accepted": False, "takeover": False, "rule": rule,
                        "commands": commands, "reason": "queued", "retry_after_s": None}
            # d9/e4（song 伴唱挂起，2026-11-05 批次）：打断对象为 song hold 序列
            # （歌曲链路活跃）→ 不走 _interrupt_sequence 终止（歌曲伴奏/人声链路
            # 归音频侧不撤销；编排 inflight 仅答谢音轨——hold 步应为 None），转入
            # seq 旁路挂起态；打断者序列照常启动，播完后按 d2 画面归属恢复。
            # T8/d13/N2/C3（resident 切回）：dynamic_text 999 序列同机制——打断者
            # 完成后切回动态文案画面（编排队列切回=③机制；deadline=step_timeout
            # 保险丝——播报时长不可知，fuse 到期回默认=播报已结束的诚实降级）。
            # 总开关 off=回退现行 standard 抢占终止语义（d22）。
            if self._arbitration_active() and (seq.get("hold_for_song")
                                               or seq.get("resident")):
                _kind = "song" if seq.get("hold_for_song") else "resident"
                result = self._suspend_hold_for(rule, data, now, commands, _kind)
                result["reason"] = "interrupted"
                result["retry_after_s"] = None
                return result
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
            elif trigger_type == "dynamic_text":
                # d4（2026-11-05 编排优化批次）：动态文案源直连——keyword_hub
                # 动态文案源直接触发（不走关键词匹配链）；match 可空=任意源
                # 事件命中，非空=match_value 子串过滤（多规则场景预留）。
                if not rule.match or _normalize_text(match_value).find(_normalize_text(rule.match)) >= 0:
                    return rule, None
            elif trigger_type in ("entrance", "follow"):
                # T4（2026-11-01 批次）：match 可空=该类型任意事件；非空=用户名
                # 等值匹配（复用 _normalize_text 口径，Eng E4）。触发载荷携带
                # username 供文案变量替换（CEO F5 探针确认字段必有）。
                if not rule.match:
                    return rule, None
                username = str(data.get("username", "") or "")
                if username and _normalize_text(username) == _normalize_text(rule.match):
                    return rule, None
        return None, ("below_min_price" if price_gate_hit else "no_match")

    def _enqueue_pending(self, rule: RuleCfg, trigger_type: str, match_value: str,
                         data: dict, commands: List) -> str:
        """queue 模式入队（O1 权重降序 + O2 满额挤低优，2026-10-28 消费批次）。

        排序语义对齐音频队列（queue_manager 规则1/2/4）：按 priority_mapping
        权重降序找位插入（同权重保持 FIFO 即插到同权重组尾）；满额时挤掉最低
        优组中最旧条目（对齐音频队列"取代同权重组首"让位语义），新触发权重不
        高于最低优时直接舍弃（对齐规则4 低优丢弃）。

        Returns:
            "queued"（已入队）| "queued_full"（满额且权重不足被舍弃）
        """
        entry = {"trigger_type": trigger_type, "match_value": match_value,
                 "data": dict(data or {}), "rule_id": rule.rule_id,
                 "priority": self._rule_priority(rule)}
        if len(self.pending_queue) >= self.PENDING_QUEUE_MAX:
            min_p = min(e.get("priority", 0) for e in self.pending_queue)
            if entry["priority"] <= min_p:
                commands.append(LogCmd(
                    "warning", f"[编排] 排队已满（{self.PENDING_QUEUE_MAX}）且新触发权重 "
                               f"{entry['priority']} 不高于最低优 {min_p}，舍弃 {rule.rule_id}"))
                return "queued_full"
            evict_idx = next(i for i, e in enumerate(self.pending_queue)
                             if e.get("priority", 0) == min_p)
            dropped = self.pending_queue.pop(evict_idx)
            commands.append(LogCmd(
                "warning", f"[编排] 排队已满（{self.PENDING_QUEUE_MAX}），挤掉最低优触发 "
                           f"{dropped['rule_id']}（p={min_p}），让位 {rule.rule_id}（p={entry['priority']}）"))
        pos = len(self.pending_queue)
        for i, e in enumerate(self.pending_queue):
            if e.get("priority", 0) < entry["priority"]:
                pos = i
                break
        self.pending_queue.insert(pos, entry)
        commands.append(LogCmd(
            "info", f"[编排] 规则 {rule.rule_id} 排队等待（第 {pos + 1}/{len(self.pending_queue)} 位，"
                    f"p={entry['priority']}，interrupt=queue，当前序列 {self.seq['rule_id']} 播完后按权重序出队）"))
        return "queued"

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
            # T8/d13/N2（resident 切回）：dynamic_text 999 序列=resident——
            # 他键打断转挂起、打断者完成切回（③编排队列切回机制）
            "resident": (rule.trigger_type == "dynamic_text"
                         and self._rule_priority(rule) == 999),
        }
        self.state = "sequence"
        commands.append(LogCmd("info",
                               f"[编排] 触发 {rule.rule_id}（第 {self.counters[rule.rule_id]} 次，"
                               f"mood={self.seq['tier_mood'] or 'base'}），进入序列"))
        commands.extend(self._start_current_step(now, force_immediate=True))
        return {"accepted": True, "takeover": rule.has_tts_audio(), "rule": rule, "commands": commands}

    def _interrupt_sequence(self, rule: RuleCfg, data: dict, now: float, commands: List) -> dict:
        """条件3：旧键序列立即终止，队列替换 [旧 exit?, 新 enter, 新 main, 新 exit]（CP-3）。"""
        seq = self.seq
        old_rule_id = seq["rule_id"]
        # O8（2026-10-28 消费批次）：旧序列未消费的 inflight 音轨随打断撤销——
        # 否则新序列画面期间播出旧规则答谢（画面/音频错配）。已在播/已消费的
        # 无法撤销，其完成回调因 inflight 重置被静默忽略（无副作用）。
        old_inflight = seq.get("inflight")
        if old_inflight and old_inflight.get("step_id"):
            commands.append(CancelAudio(old_inflight["step_id"]))
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
        commands.extend(self._start_current_step(now, force_immediate=True))
        return {"accepted": True, "takeover": rule.has_tts_audio(), "rule": rule, "commands": commands}

    def _suspend_hold_for(self, rule: RuleCfg, data: dict, now: float,
                          commands: List, kind: str = "song") -> dict:
        """d9/e4（song）+T8/d13/N2/C3（resident 切回）——hold 序列让位挂起
        （seq 旁路结构），打断者序列照常启动。

        kind=song：deadline=歌曲锚（on_song_started 挂起分派刷新——F-B）；
        kind=resident：deadline=step_timeout 保险丝（播报时长不可知的诚实
        上限；到期回默认=播报已结束）。

        与 _interrupt_sequence 的差异：不撤销歌曲链路（伴奏/人声归音频侧；编排
        inflight 在 hold 步应为 None，残留答谢由既有完成回调静默忽略）；不重插
        旧 exit（主视觉→打断者 enter 直切，旧序列离场段不重放）；旧序列转入
        song_suspended 挂起态（打断者完成后按 d2 恢复）。D-4 计数清零照常
        （e9：恢复重入=基础档画面）。总开关 off 时本方法不被调用（decide_trigger
        门控）。"""
        seq = self.seq
        old_rule_id = seq["rule_id"]
        self.counters[old_rule_id] = 0  # 他键触发清零（D-4；e9 恢复=基础档）
        if kind == "song":
            _deadline = seq.get("current_deadline")
        else:
            _deadline = now + float(getattr(self.cfg, "step_timeout_s", 60.0) or 60.0)
        self.song_suspended = {
            "rule_id": old_rule_id,
            "deadline": _deadline,
            "entered_at": now,
            "kind": kind,
        }
        self._seq_counter += 1
        self.counters[rule.rule_id] = self.counters.get(rule.rule_id, 0) + 1
        new_steps = build_steps(rule, self.counters[rule.rule_id], self._seq_counter)
        self.seq = {
            "rule_id": rule.rule_id,
            "steps": new_steps,
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
        commands.append(LogCmd(
            "info", f"[编排] {'song 伴唱' if kind == 'song' else 'dynamic_text resident'}"
                    f"挂起（{old_rule_id} 保留，deadline 锚移交挂起态），"
                    f"切换 {rule.rule_id}；打断者完成后切回（d2/d13）"))
        commands.extend(self._start_current_step(now, force_immediate=True))
        return {"accepted": True, "takeover": rule.has_tts_audio(), "rule": rule, "commands": commands}

    def _recover_song_hold(self, rule: RuleCfg, deadline: float, now: float,
                           kind: str = "song") -> List:
        """d2/e9 恢复（T8 泛化 resident 同构）：回主画面（基础档 hold，deadline
        续挂起锚）。

        恢复序列仅主线 hold 步（不重放入场/离场——主视觉连续；入场已播过，
        离场留给自然结束后的默认轮播）；deadline 到点由既有 on_tick 超时机制
        走条件1 回默认池。e9：打断时 D-4 已清零，恢复=基础档画面
        （select_tier(rule, 1) 与首次触发同档）。kind=resident 时 deadline=
        step_timeout 保险丝（播报时长不可知的诚实上限——到期即播报已结束）。"""
        self._seq_counter += 1
        tier = select_tier(rule, 1)
        overrides = tier.segment_overrides if tier else {}
        base = rule.name_step("main")
        is_song = (kind == "song")
        hold_step = {
            "step_id": f"{rule.rule_id}:main#resume{self._seq_counter}",
            "name": "main",
            "segment": overrides.get("main", base.segment if base else ""),
            "switch_at": "immediate",
            "duration_s": base.duration_s if base else None,
            "audio_mode": base.audio_mode if base else "none",
            "audio_path": base.audio_path if base else "",
            "pred_version": rule.pred_version,
            "lip_flag": base.lip_flag if base else True,
            "hold_for_song": is_song,
        }
        self.seq = {
            "rule_id": rule.rule_id,
            "steps": [hold_step],
            "idx": 0,
            "hold_until": now,
            "main_done": False,
            "exit_started": False,
            "pending_requeue": None,
            "inflight": None,
            "current_deadline": deadline,
            "step_accepted": False,
            "trigger_data": {},
            "tier_mood": "base",
            "hold_for_song": is_song,
            "resident": (not is_song),
        }
        self.state = "sequence"
        commands: List = [LogCmd(
            "info", f"[编排] 打断者播完，恢复{'唱歌' if is_song else '播报'}画面"
                    f"（{rule.rule_id} 基础档，剩余 {deadline - now:.1f}s——"
                    f"{'d2/e9' if is_song else 'd13/N2 resident'}）")]
        commands.extend(self._start_current_step(now, force_immediate=True))
        return commands

    def _start_current_step(self, now: float, force_immediate: bool = False) -> List:
        """发出当前步切换指令（执行器收到后调 EDTalk，结果回填 on_switch_result）。

        force_immediate（2026-11-05 用户实测「触发后画面不切换」根修）：
        序列启动的第一步（DEFAULT/打断→enter）强制 at='immediate'——
        segment_end 是「当前段自然播完的边界衔接」，而触发时刻当前段是
        默认池长素材（实测 22 段=15007 帧=600s），挂起切换要等 5-10 分钟
        才到边界=画面永不切换（引擎按 sidecar 时长正常推进，双时钟漂移
        静默）。编排接管=打断默认轮播，第一步必须立即硬切；序列内后续
        步保持规则 switch_at（短段自然边界=引擎时长边界，无缝衔接）。
        immediate 同时清服务端遗留的挂起切换（render_loop 语义）。
        """
        commands: List = []
        if self.seq is None or self.state != "sequence":
            return commands
        step = self.seq["steps"][self.seq["idx"]]
        if step["name"] == "exit":
            self.seq["exit_started"] = True
        at = "immediate" if force_immediate else step["switch_at"]
        commands.append(SwitchSegment(step["step_id"], step["segment"], at))
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

    def is_active_step(self, step_id: str) -> bool:
        """step_id 是否属于当前活动序列（O8 收口判定，2026-10-28 P3 审查批次）。

        打断/同键重排/回默认后，worker 队列中滞留的旧步骤 IO 命令仍会执行完
        并入队音频（_execute 锁外无原子性——打断无法中止正在执行的 TtsThanks）；
        其 audio_enqueued 回执到达时以此为门：非当前序列步骤=孤儿音轨，门面层
        撤销队列条目（封死「打断后旧答谢以 500 插队权重播出」的错配窗口）。
        """
        if self.state != "sequence" or self.seq is None or not step_id:
            return False
        return any(s["step_id"] == step_id for s in self.seq["steps"])

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
        T4/F-B（2026-11-05 批次）：挂起期自校正仍推进——命中 song_suspended 挂起
        态时刷新挂起 deadline（分支先于 state/seq 门：打断者序列运行中
        state=sequence 但 seq 非 song hold）。"""
        commands: List = []
        if remaining_s is None:
            return commands
        try:
            remaining = float(remaining_s)
        except (TypeError, ValueError):
            return commands
        if remaining <= 0:
            return commands
        susp = self.song_suspended
        if susp is not None:
            susp["deadline"] = now + remaining
            commands.append(LogCmd(
                "info", f"[编排] 挂起歌曲切片起播（{song_name}）：挂起 deadline 刷新"
                        f"（+{remaining:.1f}s，F-B 挂起期自校正）"))
            return commands
        if self.state != "sequence" or self.seq is None:
            return commands
        if not self.seq.get("hold_for_song"):
            return commands
        steps = self.seq["steps"]
        idx = self.seq["idx"]
        if idx >= len(steps) or not steps[idx].get("hold_for_song"):
            return commands
        anchor = now
        self.seq["current_deadline"] = anchor + remaining
        commands.append(LogCmd(
            "info",
            f"[编排] 歌曲起播（{song_name}）：主线段保持至歌曲结束"
            f"（锚点+{remaining:.1f}s，R-eng1 预排程权威信号）"))
        return commands

    def on_audio_complete(self, qtype: str, content: str, now: float,
                          step_id: Optional[str] = None) -> List:
        """播放完成回调关联（CL-2 + O6 实例序号，2026-10-28 消费批次）。

        匹配规则：回调携带 step_id（choreo_step_id 全链透传，非空）时按实例
        序号精确匹配——同内容重复入队（同关键词重复触发/同模板答谢/同曲重点）
        不再误配（频繁触发错配根修）；未携带（旧链路/未透传路径）退回
        (type,content)+晚于入队时间 匹配（既有 CL-2 语义）。换序列清空 inflight
        后，旧音频的完成回调两种匹配路径均不推进新序列——F1 竞态根修保持。
        """
        if self.seq is None or self.state != "sequence":
            return []
        inflight = self.seq.get("inflight")
        if not inflight:
            return []
        if step_id:
            if inflight["step_id"] != step_id:
                return []  # 他步/他序列/旧实例音频（O6 精确匹配）
        else:
            # O9（2026-10-28 消费批次）：回退路径 content 归一化（去空白+剥标点）
            # +双向包含比较——下游 remove_extra_words 点缀词裁剪（改写后更短）、
            # 换行替换为句号等改写不再误失配。回退路径本有 now>=enqueued_at+
            # inflight 单槽约束，包含匹配的误配面可控（仅未透传 step_id 的旧路径）。
            inflight_norm = _completion_key(inflight["content"])
            content_norm = _completion_key(content)
            # O9 空键守卫（P3 审查批次）：任一归一化键为空（纯标点/空白内容）不
            # 匹配——空串是任意字符串的子串，放行=任意空内容回调无条件误配，
            # 宁可 F1 超时兜底也不误推进
            if inflight["qtype"] != qtype or not inflight_norm or not content_norm:
                return []  # 他链路音频，忽略（RUNBOOK 局限声明）
            if not (inflight_norm in content_norm or content_norm in inflight_norm):
                return []
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
        # T4/d2（2026-11-05 批次）：song 伴唱挂起恢复优先——歌曲未结束=回点歌
        # 主线画面（恢复序列=基础档 hold，deadline 续挂起锚）；歌曲已结束
        # （deadline 过点/锚缺失/规则已删/硬上限超时）=清挂起走正常默认轮播
        # （e4：挂起期默认池不接管——恢复在此同步发生，不经 RestoreDefault）。
        # T8：resident（dynamic_text 999 序列）同构恢复——切回播报画面，保险丝
        # 到期回默认（d13/N2/C3）。
        susp = self.song_suspended
        if susp is not None and self._arbitration_active():
            self.song_suspended = None
            susp_rule = self.cfg.rule_by_id(susp.get("rule_id"))
            susp_deadline = susp.get("deadline")
            if (susp_rule is not None and susp_deadline is not None
                    and now < susp_deadline
                    and now - susp["entered_at"] <= self.SONG_SUSPEND_MAX_S):
                commands.extend(self._recover_song_hold(
                    susp_rule, susp_deadline, now,
                    kind=susp.get("kind") or "song"))
                return commands
            if susp_rule is not None and susp_deadline is not None and now >= susp_deadline:
                commands.append(LogCmd(
                    "info", f"[编排] 挂起{'歌曲' if susp.get('kind') == 'song' else '播报'}"
                            f"已结束，恢复正常默认轮播（d2/d13，{susp['rule_id']}）"))
            elif now - susp["entered_at"] > self.SONG_SUSPEND_MAX_S:
                logger.error(
                    f"[编排] 挂起超硬上限（{self.SONG_SUSPEND_MAX_S:g}s），"
                    "强制回默认轮播（d22 看门狗）：problem=挂起锚异常；"
                    "cause=锚链路死亡且无刷新（deadline 永不到期）；"
                    "fix=检查歌曲链路（切片自校正/点歌队列）")
                try:
                    alarm_notifier.alarm_error(
                        alarm_notifier.MODULE_ORCHESTRATION,
                        "编排挂起超硬上限，强制回默认轮播",
                        fix_hint="检查歌曲链路（切片自校正/点歌队列）是否异常")
                except Exception:
                    pass
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
        # queue 模式出队（2026-10-27 批次；2026-10-28 O1 权重序化）：当前序列播完
        # → 取队首（最高权重，O1 降序插入保证）待播触发重放（余下保留，下一
        # 序列播完继续出队）。state 已回 default，走正常 _start_sequence；出队
        # 失败不回滚——日志留痕
        pending = self.pending_queue.pop(0) if self.pending_queue else None
        if pending:
            remaining = len(self.pending_queue)
            commands.append(LogCmd("info",
                                   f"[编排] 出队执行排队的规则 {pending['rule_id']}"
                                   + (f"（余 {remaining} 条排队）" if remaining else "")))
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
            "song_suspended": (dict(self.song_suspended) if self.song_suspended else None),
            "degrade_total": self.degrade_total,
            "enabled_rules": [r.rule_id for r in self.enabled_rules],
            "pending_requeue": bool(seq.get("pending_requeue")) if seq else False,
            "step_remaining_s": (round(seq["current_deadline"] - self.clock(), 1)
                                 if seq and seq.get("current_deadline") is not None else None),
            "mute_window": self.mute_window(),
            "pending_depth": len(self.pending_queue),  # G3：queue 多槽排队深度观测
        }


class ActionChoreographer:
    """编排器门面：同步判定 API（handler 线程调用）+ 单工作线程执行。"""

    def __init__(self, config, edtalk_client, audio_enqueue: Callable,
                 thanks_generate: Callable, clock: Callable[[], float] = time.monotonic,
                 audio_cancel: Optional[Callable] = None):
        self.cfg: ChoreographyConfig = load_choreography_config(config)
        # R-ceo7 时长回填补链（2026-11-05 用户实测「测试二」批次）：管理页创建的
        # new 规则 sequence 无 duration_s（R-ceo7 合法——时长=素材时长），apply
        # 二段（apply_config→_resolve_new_rule_material_props）是唯一回填点——
        # **启动装载路径（本 __init__）从未回填**：重启系统后规则以裸 None 进引
        #擎，触发即 60s 兜底卡序列（sidecar 里时长明明都在——「测试二」实测）。
        # 启动装载与热应用双链同源回填（sidecar 读取不依赖 EDTalk 就绪）。
        self._resolve_new_rule_material_props(self.cfg)
        self._config = config                    # 原始配置引用（avatar 联动读初值用）
        self.edtalk_client = edtalk_client
        self._audio_enqueue = audio_enqueue      # my_handle.audio_synthesis_handle
        self._thanks_generate = thanks_generate  # event_handler._thanks_generate
        # O8：打断撤旧音轨（QueueMixin.cancel_choreo_audio）；缺省 no-op（测试/
        # 装配降级不打断主链）
        self._audio_cancel = audio_cancel or (lambda step_id: None)
        self._clock = clock
        # O1/O3（2026-10-28 消费批次）：全局权重表快照注入引擎——编排排队位次、
        # standard 打断权重门与消息队列/音频队列同源 filter.priority_mapping
        self._priority_mapping = _load_priority_mapping(config)
        self.engine = ChoreoEngine(self.cfg, clock=clock,
                                   priority_mapping=self._priority_mapping)
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

    def on_dynamic_text(self, text: str) -> bool:
        """d4（2026-11-05 编排优化批次）：动态文案源直连——动态文案内容整段作为
        match_value 触发 dynamic_text 规则（不走关键词匹配；match 可空=任意源
        事件命中，非空=content 子串过滤）。task_scheduler 格式化路径与
        keyword_hub copywriting 源共用本入口。"""
        try:
            with self._lock:
                decision = self.engine.decide_trigger(
                    "dynamic_text", str(text or ""), {})
                self._dispatch(decision["commands"])
            return bool(decision["accepted"])
        except Exception as e:
            logger.error(f"[编排] on_dynamic_text 异常（忽略）：problem=编排判定异常；"
                         f"cause={type(e).__name__}: {e}；fix=已降级忽略")
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

    def on_entrance(self, data: dict) -> bool:
        """入场事件订阅点（T4：event_handler.entrance_handle 调用，感谢链同源）。

        True=画面序列启动/排队受理；入场主流程（积分/答谢）不受影响（正交）。
        username 清洗三件套（Eng E4/S-1：去控制字符+截断 24 字符——不可信外部
        输入直通 TTS/字幕变量，清洗在编排侧副本上做，原 data 不动）。
        节流/权重/queue 三强制在引擎与装载侧（Eng A2/E3/E5）。
        """
        return self._on_entrance_follow("entrance", data)

    def on_follow(self, data: dict) -> bool:
        """关注事件订阅点（T4：event_handler.follow_handle 调用，语义同 on_entrance）。"""
        return self._on_entrance_follow("follow", data)

    def _on_entrance_follow(self, trigger_type: str, data: dict) -> bool:
        """entrance/follow 共用订阅入口（T4）。"""
        try:
            raw = dict(data or {})
            username = str(raw.get("username", "") or "")
            cleaned = "".join(ch for ch in username if ch.isprintable())[:24]
            raw["username"] = cleaned
            with self._lock:
                decision = self.engine.decide_trigger(trigger_type, cleaned, raw)
                self._dispatch(decision["commands"])
            return bool(decision["accepted"])
        except Exception as e:
            logger.error(f"[编排] on_{trigger_type} 异常（忽略）：problem=编排判定异常；cause={type(e).__name__}: {e}；fix=已降级忽略")
            return False

    def on_audio_complete(self, qtype: str, content: str,
                          step_id: Optional[str] = None) -> None:
        """播放完成回调（playback_manager EDTalk push 路径每条目完成处，asyncio 线程）。

        O6（2026-10-28 消费批次）：回调携带 choreo_step_id（非空）时按实例序号
        精确匹配，同内容重复入队不再误配。
        """
        try:
            with self._lock:
                commands = self.engine.on_audio_complete(qtype, str(content or ""),
                                                         self._clock(), step_id)
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
        "queued": "已排队：当前序列播完后按权重序出队执行",
        "queued_full": "排队已满且触发权重不高于最低优，已舍弃",
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
        "queued": "观察状态条 pending_depth，序列播完后按权重序自动执行",
        "queued_full": "调整规则 interrupt 或提高对应类型的 priority_mapping 权重",
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
                # R-ceo4 系统接管时长：sidecar 有效值覆盖显式配置；sidecar 缺/
                # 无效（None/0——erro3 批次 T1 实测根因）**保留 load 显式时长**
                # ——曾无条件赋 None 把 load 校验过的时长覆盖掉，60s 兜底把序列
                # 卡死 60s（erro3.log:221/369，期间同键重入全部被忽略=「编排
                # 无法触发」）。load 的「无音轨+缺时长」结构拒绝保证显式时长
                # 缺席时不静默（静默跳段防护）。
                _meta_dur = meta.get("duration_s")
                if _meta_dur is not None and float(_meta_dur) > 0:
                    step.duration_s = float(_meta_dur)
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
                # C5-1 语义（2026-11-04 实测校正）：不可达=保存但不生效——
                # 曾实现为裸 503 applied=False（端点不写盘、前端仍报「已保存」
                # 假成功），配置期改动（默认画面池/单段段）全部静默丢失。
                # reason 标记供 api.py 端点区分「该持久化」与 409/worker 失败
                # 「不该持久化」两种未应用态。
                return {"applied": False, "status": 503, "enabled_rules": [],
                        "rejected": rejected, "missing_all": [],
                        "reason": "edtalk_unreachable",
                        "message": "EDTalk 未连接（/status 无可用段）：已保存、暂未生效，"
                                   "服务就绪后重试应用即校验生效"}
            result = validate_rule_segments(new_cfg.rules, segments)
            enabled_ids = set(result["enabled_rules"])
            # new 规则素材属性解析（R-ceo7/R-eng2/R-eng6③：时长+口型标志从
            # sidecar 物化进 RuleCfg——运行期零 I/O；FB-2 替代守卫的第二段位置。
            # 时长未知不拒绝（F-M1 两分法：允许保存+「生效待就绪」）——运行期
            # 由 on_switch_result 的 step_timeout_s 兜底+timeout_degrade 报警。
            # EDTalk 不可达时本段已在上方 503 返回（C5-1），此处必有 sidecar 可读。
            self._resolve_new_rule_material_props(new_cfg)
            # 换装：cfg/enabled_rules 替换；计数保留同名键（规则语义可能已变）
            new_engine = ChoreoEngine(new_cfg, clock=self._clock,
                                      priority_mapping=self._priority_mapping)
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
        commands: List = []
        stale_orphan = None
        with self._lock:
            if kind == "tick":
                commands = self.engine.on_tick(self._clock())
            elif kind == "switch_result":
                _, step_id, ok = event
                commands = self.engine.on_switch_result(step_id, ok, self._clock())
            elif kind == "audio_complete":
                _, qtype, content, step_id = event
                commands = self.engine.on_audio_complete(qtype, content, self._clock(), step_id)
            elif kind == "audio_enqueued":
                _, step_id, qtype, content, ok = event
                if self.engine.is_active_step(step_id):
                    commands = self.engine.on_audio_enqueued(step_id, qtype, content, ok,
                                                             self._clock())
                elif ok:
                    # O8 收口（P3 审查批次）：打断/序列更替后迟到的旧步骤音频入队
                    # 回执——条目已成孤儿（不注册 inflight、完成回调也匹配不上），
                    # 锁外撤销队列条目（先行 CancelAudio 只覆盖 inflight 已注册
                    # 情形；worker 队列滞留/正在执行的旧 TtsThanks 在此兜住）
                    stale_orphan = step_id
            elif kind == "restore_result":
                _, ok = event
                commands = self.engine.on_restore_result(ok, self._clock())
            elif kind == "song_started":
                _, song_name, remaining_s = event
                commands = self.engine.on_song_started(song_name, remaining_s,
                                                       self._clock())
            else:
                commands = []
        if stale_orphan:
            try:
                self._audio_cancel(stale_orphan)
            except Exception as e:
                logger.debug(f"[编排] 孤儿音轨撤销异常（忽略）：{type(e).__name__}: {e}")
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
        # X4（2026-11-05 批次）：song 序列 lip_mode 切换观测（次数+距上次耗时）
        _prev_ts = getattr(self, "_lip_switch_last_ts", None)
        _now_ts = time.monotonic()
        try:
            from utils.obs_stats import record_lip_switch
            record_lip_switch(want, (_now_ts - _prev_ts) if _prev_ts is not None else 0.0)
        except Exception:
            pass
        self._lip_switch_last_ts = _now_ts
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
                    # 经注入引用复用原生答谢链（E-2 限流+周期通道全复用，WP-3）。
                    # O6/O7：choreo_step_id 实例序号（完成回调精确匹配）+
                    # priority_override 插队权重，经答谢链原路透传到播放条目
                    try:
                        message = self._thanks_generate(
                            "gift", cmd.data, cmd.variables,
                            extra_fields={"choreo_step_id": cmd.step_id,
                                          "priority_override": CHOREO_AUDIO_PRIORITY})
                    except Exception as e:
                        logger.error(f"[编排] tts 步骤答谢生成异常：{type(e).__name__}: {e}")
                        message = None
                    if message and isinstance(message, dict):
                        if not message.get("choreo_step_id"):
                            # O9（2026-10-28 消费批次）：透传链断裂可见化——答谢链
                            # 丢失实例序号时完成匹配退回 content 路径（F1 兜底仍在），
                            # 但须告警暴露断裂点，防静默超时
                            logger.error(
                                "[编排] tts 音轨 choreo_step_id 透传断裂（答谢链丢键）——"
                                "完成匹配退回 content 路径；problem=实例序号未随 message 透传；"
                                "cause=答谢/助播/周期通道重构丢键；fix=检查 _thanks_generate 链路")
                            alarm_notifier.alarm_error(
                                alarm_notifier.MODULE_ORCHESTRATION,
                                "编排 tts 音轨实例序号透传断裂，完成匹配退回 content 匹配",
                                fix_hint="检查 _thanks_generate 至播放条目的 choreo_step_id 透传链")
                        self._events.put(("audio_enqueued", cmd.step_id, message.get("type", "gift"),
                                          message.get("content", ""), True))
                    else:
                        # 生成失败/开关关闭：无音频会来 → 立即完成该步（F1 语义前置）
                        self._events.put(("audio_enqueued", cmd.step_id, "gift", "", False))
                elif isinstance(cmd, EnqueueFile):
                    try:
                        # O6/O7：同 TtsThanks——实例序号+插队权重透传（song 直通道）
                        self._audio_enqueue({"type": "song", "content": cmd.audio_path,
                                             "choreo_step_id": cmd.step_id,
                                             "priority_override": CHOREO_AUDIO_PRIORITY})
                        ok = True
                    except Exception as e:
                        logger.error(f"[编排] file 音轨入队异常：problem=本地音频未入队；cause={type(e).__name__}: {e}；fix=检查文件路径与 play_audio.enable")
                        ok = False
                    # 关联键=(type,content)：song 直通 data_json 的 content=路径（synthesis_manager :153 同构）
                    self._events.put(("audio_enqueued", cmd.step_id, "song", cmd.audio_path, ok))
                elif isinstance(cmd, CancelAudio):
                    # O8：撤销旧序列未消费音轨（队列级移除；注入缺失时静默跳过）
                    try:
                        self._audio_cancel(cmd.step_id)
                    except Exception as e:
                        logger.debug(f"[编排] 撤销旧音轨异常（忽略）：{type(e).__name__}: {e}")
                elif isinstance(cmd, RestoreDefault):
                    result = self.edtalk_client.schedule(cmd.sequence, align=True,
                                                         loop=cmd.loop)
                    ok = bool(result.get("ok", True)) if isinstance(result, dict) else False
                    self._events.put(("restore_result", ok))
                    # 回默认 lip/repair/gaze 联动复位（2026-10-31 审查批次 F1）：
                    # 序列中 lip-off 步把三开关置 off 后，默认画面（口型正常）
                    # 必须恢复——否则泄漏为「默认画面有音频不张嘴」。去抖缓存
                    # 保证仅在离开 off 态时真正下发；服务端默认 lip=on（纯运行
                    # 中开关无 config 初值），repair/gaze 恢复 webui 配置初值。
                    self._apply_lip_linkage(True, "restore_default")
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
