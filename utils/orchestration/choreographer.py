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

# O7（2026-10-28 消费批次；B2 因果对齐批次 2026-11-27 迁值）：编排音轨插队
# 权重（data_json.priority_override）——高于常规语音权重域（config
# priority_mapping 实测 10~30），低于复读专线（reread_top_priority=500）。
# B2：500→480 迁出复读共域——旧值与复读专线同权重时退化插入序 FIFO（两条
# 语义不同的流谁先入队谁先响，画面可能配错声音）；480 单列后复读（用户主动
# 重听，最高意图）恒先于编排音轨，编排音轨恒高于常规语音。固定值不占配置面。
# 编排音轨与画面同程（先切段后入队），积压时插队是画面-音频对齐的核心手段，
# 故不开放配置避免用户误设低于常规语音
CHOREO_AUDIO_PRIORITY = 480


# ---------- 命令（引擎产出，执行器消费） ----------

@dataclass
class SwitchSegment:
    step_id: str
    segment: str
    at: str  # immediate | segment_end
    # 步名（2026-11-27 审查修复 #5）：边界执行轮询的起线程门——仅 exit 步需要
    # （消费面唯一=on_switch_executed 离场计时锚定）；enter/main 起线程纯浪费。
    step_name: str = ""
    # 段循环模式（2026-11-24 pingpong 批次）：'loop'=锯齿折返（默认）；
    # 'pingpong'=三角波正放↔倒放无缝往返（服务端帧级映射，口型音频驱动
    # 不受影响）——主线 hold 场景（唱歌/动态文案循环期）画面零跳变。
    loop_mode: str = "loop"


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


@dataclass(frozen=True)
class TransRow:
    """B3 转移表行（2026-11-27 显式状态转移批次，P0-1 根治）：具名状态 × 守卫 →
    动作的声明式转移。行序即求值序（首条 state+guard 命中行生效）；每具名状态
    必达 guard="always" 兜底行（装载期穷尽检查保证）。

    state：具名状态（ENGINE_STATES 之一或 "*" 任意）；guard/action：具名守卫/
    动作（注册表字符串键——行为本体在引擎方法，表只声明「谁在什么状态下被谁
    处理」，历轮 bug 场景在表中可定位可枚举）。note=行为出处批次锚点。
    """
    state: str
    guard: str
    action: str
    event: str = "trigger"
    note: str = ""


def _normalize_text(text: str) -> str:
    """S-2 规范化思路的独立实现：NFKC + 去空白 + casefold（义务 CL-3）。"""
    import unicodedata
    return "".join(unicodedata.normalize("NFKC", str(text or "")).split()).casefold()


def _completion_key(text: str) -> str:
    """完成匹配回退路径的归一化键（O9，2026-10-28 消费批次）：S-2 去空白后再
    剥标点（isalnum 过滤）——下游改写形态（换行→句号、remove_extra_words
    点缀词裁剪、首尾空白）容差匹配，改写不再造成回退路径误失配。"""
    return "".join(ch for ch in _normalize_text(text) if ch.isalnum())


def _copy_content_matched(rc_key: str, cc_key: str) -> bool:
    """文案完成回执一致性校验（B1 结构化终结批次，2026-11-27）。

    B1 主判定=结构化组事件（本组 group_pending<=0 + 其它文案组
    copy_groups_pending<=0——播放侧按两队列实算传入），文本比对降级为
    一致性防线：双向包含（rc∈cc or cc∈rc）。

    历史注记：双向包含曾是唯一判定（旧语义）——「match_value=全文时任意
    早段回执都是全文子串 → 第一段消费完即误判完成」的缺陷在当时成立，根因
    是缺组条件；2026-11-25 引入 group_pending 双条件后改尾窗启发式
    （min(8) 自适应）兜早段。B1 起组事件双条件完备（早段=本组未空；异组
    残留=其它组非空），双向包含的误判面被结构条件闭合，尾窗启发式退役
    （重复尾句/末段越界/归一化长度<8 三类边角随之消除）。"""
    if not rc_key or not cc_key:
        return False
    return rc_key in cc_key or cc_key in rc_key


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


def _step_material_props(base: StepCfg, tier, name: str):
    """步素材属性回填（覆盖段缺口修复 2026-11-25）：情绪档覆盖段取其自身
    sidecar 属性（apply 二段解析到 tier.override_props）；无覆盖/未解析=
    基础段属性。覆盖段 sidecar 缺有效时长时回退基础段显式时长（有界推进
    保底，R-eng6②）；lip_flag 恒有值（sidecar 缺省 on）。"""
    if tier is not None and name in (tier.segment_overrides or {}):
        props = tier.override_props.get(name) or {}
        return (props.get("lip_flag", base.lip_flag),
                props.get("duration_s") or base.duration_s)
    return base.lip_flag, base.duration_s


def build_steps(rule: RuleCfg, count: int, seq_counter: int) -> List[dict]:
    """按当前计数档位物化三步（segment_overrides 可覆盖任一步，CL-4/WP-10）。

    2026-10-27 动作编排完善批次：enter/exit 为可选过渡片段——解析为 None 的步
    不物化（main→exit 直连或 enter→main 直连）；主线必选恒在。
    覆盖段换段时 lip_flag/duration_s 随覆盖段自身 sidecar（_step_material_props）。
    """
    tier = select_tier(rule, count)
    overrides = tier.segment_overrides if tier else {}
    steps = []
    for name in ("enter", "main", "exit"):
        base: StepCfg = rule.name_step(name)
        if base is None:
            continue  # 可选过渡片段缺省=跳过该步
        lip_flag, duration_s = _step_material_props(base, tier, name)
        steps.append({
            "step_id": f"{rule.rule_id}:{name}#{seq_counter}",
            "name": name,
            "segment": overrides.get(name, base.segment),
            "switch_at": base.switch_at,
            "duration_s": duration_s,
            "audio_mode": base.audio_mode,
            "audio_path": base.audio_path,
            "pred_version": rule.pred_version,   # R-eng3：mute 谓词按步分叉
            "lip_flag": lip_flag,                # new 谓词：素材口型标志（含覆盖段自身 sidecar）
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
    # gift/song/entrance/follow 与映射表同名直取（entrance/follow 直取=2026-11-06
    # 三强制拆除批次——off 态回归系统优先级权重）；keyword 触发源=弹幕（语义同
    # comment 权重）。（d22 off=整体回退现行为时仍走本表——d10 on 态改读编排仲裁表，
    # entrance/follow 例外回读本表。）
    _TRIGGER_PRIORITY_KEY = {"gift": "gift", "song": "song", "keyword": "comment",
                             "entrance": "entrance", "follow": "follow"}

    # d10（2026-11-05 编排优化批次；S-1 修订取代 d5）：编排面独立仲裁权重表——
    # 对 gift/song/dynamic_text/keyword 与 filter.priority_mapping 解耦（生产
    # trends_copywriting=999 会污染编排面：礼物应打断动态文案却被挡排队，违背
    # 方案 §2/§3 优先级语义）。覆盖粒度=类型键级（choreography.arbitration_weights
    # 可覆盖单键，T1 装载校验已限值域）；表外类型回退 0 保守（最低档=排队退化
    # 既有 FIFO 安全）。entrance/follow 例外：d10 表外时回读 priority_mapping
    # （2026-11-06 三强制拆除批次——系统优先级权重直接生效，choreography 面
    # 不设独立档，避免与用户 filter 配置静默分叉）。
    _ARBITRATION_WEIGHTS_DEFAULT = {"gift": 30, "song": 20, "dynamic_text": 15, "keyword": 10}

    # d22 看门狗（T4）：song 伴唱挂起硬上限——deadline（歌曲结束）为正常退出
    # 锚；本上限兜底「挂起锚永不刷新」的异常态（歌曲链路死亡且无新切片起播）。
    # 值域须 ≥ song 最长链路（切片×队列总时长可较长，1h 起步保守）。
    SONG_SUSPEND_MAX_S = 3600.0

    # A1 档位频次栈上限（2026-11-27 档位与打断解耦批次）：per-rule 事件时间戳
    # 环形栈容量——档位判定只看窗口内频次，时间戳个数有限截断（超界丢最旧）。
    TIER_EVENT_STACK_MAX = 256

    # ---------- B3 显式状态转移表（2026-11-27 基础架构批次，P0-1 根治） ----------
    #
    # 病根（docs/动作编排功能报告总结.md）：顶层 2 状态是假的，真实行为由
    # seq × song_suspended × resident × copy_done × preempt_request × ...
    # 笛卡尔积决定——隐式组合状态空间不可穷尽，每加一个机制回归面乘法增长，
    # 这是历轮「拆东墙补西墙」的机制性原因。
    #
    # B3 把**触发判定域**（bug 密度最高的决策树）压平为有序守卫转移表：
    # 具名状态 × 守卫 → 动作，行序=旧嵌套 if-else 求值序（行为零变化），
    # 装载期穷尽检查保证每具名状态必达兜底行（半张表=启动即失败不静默）。
    #
    # 具名状态（触发判定上下文；真实空间=具名状态 × 步相位 × 挂起覆盖层）：
    # - DEFAULT    默认轮播（承载=上游 SegmentScheduler）
    # - SEQ        普通片段序列（gift/keyword/entrance/follow 等）
    # - RESIDENT   dynamic_text resident 序列（让位/挂起更新/主线循环语义域）
    # - SONG_HOLD  song 主线 hold 序列（点歌画面保持域）
    ENGINE_STATES = ("DEFAULT", "SEQ", "RESIDENT", "SONG_HOLD")

    # 触发决策表（行序=2026-11-27 前嵌套 if-else 求值序，行为零变化）。
    # 每行=一次可枚举的行为转移：历轮 bug 场景在此可定位（note=出处批次）。
    _TRIGGER_TABLE = (
        TransRow("*", "same_rule_main_running", "in_progress",
                 note="条件2-WP6：同键 main 未完成，忽略不计数"),
        TransRow("*", "same_rule_exit_done", "requeue",
                 note="条件2-WP9：同键 main 已完成（exit 中），exit 播完重排三步"),
        TransRow("*", "song_hold_song_trigger", "song_queued",
                 note="d11：song hold 中 song 重触发 → 点歌队列承接，画面保持"),
        TransRow("*", "song_hold_arb_on", "reject_hold",
                 note="2026-11-16：song hold 中非 song 触发放弃（不排队不挂起）"),
        TransRow("*", "queue_mode", "queue_mode",
                 note="O1/O2：queue 模式权重降序多槽排队"),
        TransRow("*", "susp_resident_dynamic_text", "resident_updated",
                 note="2026-11-16：resident 挂起期新文案 → 挂起指针更新+保险丝刷新"),
        TransRow("RESIDENT", "resident_yield", "resident_yield",
                 note="T8/d13：resident 让位插播（更高/同权重或 999 特权）"),
        TransRow("*", "weight_lower", "weight_lower",
                 note="O3：standard 打断权重门——低优让位改排队"),
        TransRow("SEQ", "preempt_gate", "preempt_gate",
                 note="Gap A 2026-11-24：无缝主线保护——登记延迟抢占"),
        TransRow("DEFAULT", "always", "start_sequence",
                 note="条件1：DEFAULT 启动新序列"),
        TransRow("*", "always", "interrupt_sequence",
                 note="条件3：standard 抢占终止当前序列（含 d22 off 态 song hold 回退）"),
    )

    # 守卫注册表（guard 名 → 引擎方法名；签名统一 (rule, trigger_type) -> bool）
    _TRIGGER_GUARDS = {
        "same_rule_main_running": "_tg_same_rule_main_running",
        "same_rule_exit_done": "_tg_same_rule_exit_done",
        "song_hold_song_trigger": "_tg_song_hold_song_trigger",
        "song_hold_arb_on": "_tg_song_hold_arb_on",
        "queue_mode": "_tg_queue_mode",
        "susp_resident_dynamic_text": "_tg_susp_resident_dynamic_text",
        "resident_yield": "_tg_resident_yield",
        "weight_lower": "_tg_weight_lower",
        "preempt_gate": "_tg_preempt_gate",
        "always": "_tg_always",
    }

    # 动作注册表（action 名 → 引擎方法名；签名统一
    # (rule, trigger_type, match_value, data, now, commands, completion_text) -> dict）
    _TRIGGER_ACTIONS = {
        "in_progress": "_ta_in_progress",
        "requeue": "_ta_requeue",
        "song_queued": "_ta_song_queued",
        "reject_hold": "_ta_reject_hold",
        "queue_mode": "_ta_queue_mode",
        "resident_updated": "_ta_resident_updated",
        "resident_yield": "_ta_resident_yield",
        "weight_lower": "_ta_weight_lower",
        "preempt_gate": "_ta_preempt_gate",
        "start_sequence": "_ta_start_sequence",
        "interrupt_sequence": "_ta_interrupt_sequence",
    }

    def __init__(self, cfg: ChoreographyConfig, clock: Callable[[], float] = time.monotonic,
                 priority_mapping: Optional[dict] = None):
        self.cfg = cfg
        self.clock = clock
        self.priority_mapping: dict = dict(priority_mapping or {})  # O1/O3：全局权重表快照
        self.state = "default"
        self.ready = False
        self.enabled_rules: List[RuleCfg] = list(cfg.rules)
        self.counters: dict = {}          # rule_id -> 画面上下文计数（D-4 清零语义保留）
        # A1（2026-11-27 档位与打断解耦批次）：rule_id -> 该规则触发事件时间戳栈
        # （list，最旧在前）。档位进档改为「滑动窗口频次 count(该规则事件 ∈
        # window_s)」——与画面打断/挂起/清零完全解耦：打断只决定「此刻显示谁的
        # 画面」，不再清档位频次（P0-5 混流失效根修：两次礼物间夹弹幕不再把
        # gift 档位打回档一）。key_last/d7 窗口语义不变。
        self.tier_events: dict = {}
        self.key_last: dict = {}          # rule_id -> 上次触发尝试时刻（节流/衰减均已退役——保留兼容锚点，引擎零消费）
        self.pending_queue: list = []     # queue 模式待播多槽（O1：权重降序插入）
        self.degrade_total = 0
        # A3 分机制遥测（2026-11-27）：degrade_total 单一累计数看不出「哪个机制
        # 在坏」——按机制分账，get_state 快照透出供回归遥测/告警消费。
        self.degrade_breakdown: dict = {
            "switch_failed": 0,     # EDTalk 切段失败跳步
            "audio_timeout": 0,     # audio 步完成回调超时/生成失败（F1 降级推进）
            "duration_unknown": 0,  # sidecar 时长缺失兜底推进
        }
        # A3 机制事件观测（计数器口径；终结方式分布是 P1-6 结构化终结的回归遥测）
        self.metrics: dict = {
            "fuse_expired": 0,          # song hold 保险丝触发次数（锚自校正失效信号）
            "copy_done_structural": 0,  # 文案终结：尾窗命中方式（P1-6 替换后应恒 0）
            "copy_done_fallback": 0,    # 文案终结：保险丝兜底方式
            "loop_rounds": 0,           # Gap B 主线循环轮数累计
            "resident_suspends": 0,     # resident 让位挂起次数
            "song_suspends": 0,         # song 挂起次数
            "suspend_expired_default": 0,  # 挂起 deadline 过点回默认次数
            "preempt_overwritten": 0,   # Gap A preempt 单槽被高优覆盖次数（P1-7 观测）
            "boundary_wait_expired": 0,  # segment_end 边界等待超时降级推进次数
        }
        self._seq_counter = 0
        self._restore_attempts = 0
        self._preempt_token_seq = 0  # C3：preempt 令牌单调计数（唯一性）
        # 活动序列状态（state == "sequence" 时非 None）
        self.seq: Optional[dict] = None
        # A 收尾防线数据源（2026-11-26 画面来回跳根修）：音频侧歌曲活跃查询
        # provider（PlaybackMixin.song_hold_alive 经 facade set_song_alive_provider
        # 注入）。None=未注册（测试/降级）→ 收尾防线静默跳过，既有语义零变化。
        # 引擎保持纯状态机（无 IO）——查询本体在音频侧（F-B 单点持有者同源）。
        self.song_alive_provider: Optional[Callable[[], Optional[dict]]] = None
        # C1（2026-11-27 挂起锚双栈批次，P0-2 根治）：挂起锚按 kind 分离双槽
        # ——resident 锚（文案挂起：让位/RC2 登记，恢复=切回文案画面）与
        # song 锚（点歌挂起：d9/e4 防御域）独立占用、互不覆写。旧单槽的
        # 「后一次覆盖前次丢锚」风险由结构消除；嵌套挂起语义显式声明（见
        # song_suspended property docstring）。song_suspended property 提供
        # 单槽时代兼容读写面（既有读点零改动）。
        self._suspend_resident: Optional[dict] = None
        self._suspend_song: Optional[dict] = None

    @property
    def song_suspended(self) -> Optional[dict]:
        """C1 兼容读取面（单槽时代读点零改动）：resident 锚优先，song 锚次之。

        嵌套挂起语义（显式声明，C1）：双锚可共存——resident 挂起（文案让位）
        与 song 挂起（点歌让位）分属两个恢复域，互不覆写；恢复顺序=序列收尾
        消费点先取 resident 锚切回文案，文案恢复序列再收尾时消费 song 锚
        （LIFO 依恢复序消费）。单槽时代的「写 None=全清」由 setter 保持。

        song 锚（_suspend_song）当前为预留基建（2026-11-27 审查确认）：无
        写入方（d9/e4 防御域尚未接线），property 读取恒先命中 resident 锚。
        """
        return (self._suspend_resident if self._suspend_resident is not None
                else self._suspend_song)

    @song_suspended.setter
    def song_suspended(self, value: Optional[dict]) -> None:
        """C1 兼容写入面：按 kind 定向入槽（resident→resident 锚，其余→song
        锚）；写 None=双槽全清（单槽时代消费点语义保持）。"""
        if value is None:
            self._suspend_resident = None
            self._suspend_song = None
            return
        if value.get("kind") == "resident":
            self._suspend_resident = value
        else:
            self._suspend_song = value

    def _clear_suspend_kind(self, kind: str) -> None:
        """C1 定向清槽：按 kind 清对应锚（消费点不再误清另一锚）。"""
        if kind == "resident":
            self._suspend_resident = None
        else:
            self._suspend_song = None

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
        既有 FIFO、打断不受限，行为安全）。entrance/follow 例外（2026-11-06
        三强制拆除批次）：d10 表（含覆盖表）无键时回读 filter.priority_mapping
        ——系统优先级权重直接生效，覆盖顺序=arbitration_weights > priority_mapping，
        两处均无键回退 0。
        开关 off（d22）：整体回退现行为——legacy _TRIGGER_PRIORITY_KEY 映射
        filter.priority_mapping 读取（gift/song/entrance/follow 同名直取、
        keyword→comment，2026-10-28 语义+2026-11-06 直取扩展）。"""
        if rule is None:
            return 0
        _tt = getattr(rule, "trigger_type", "")
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
        if p is None and _tt in ("entrance", "follow"):
            # 2026-11-06 三强制拆除：d10 表外类型回读系统优先级权重（覆盖顺序
            # =arbitration_weights > priority_mapping；两处均无键回退 0）。
            p = self.priority_mapping.get(_tt)
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
        前（此刻 key_last 仍持上一触发时刻；未匹配不经过此处=不刷新不判定——
        d7 终门标注语义）。"""
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

    # ---------- A1 档位频次（2026-11-27 档位与打断解耦批次） ----------

    def _record_tier_event(self, rule: RuleCfg, now: float) -> None:
        """记录一次规则触发事件（档位频次栈追加；与画面 D-4 清零完全解耦）。

        栈容量 TIER_EVENT_STACK_MAX 截断（超界丢最旧）——窗口判定只需窗口内
        时间戳，个数上限=min(末档 min_count)+1 量级，256 覆盖任意档位配置。
        """
        stack = self.tier_events.setdefault(rule.rule_id, [])
        stack.append(now)
        if len(stack) > self.TIER_EVENT_STACK_MAX:
            del stack[:len(stack) - self.TIER_EVENT_STACK_MAX]

    def _tier_count(self, rule: RuleCfg, now: float) -> int:
        """档位进档计数（A1）：滑动窗口频次 = count(该规则事件 ∈ window_s)。

        window=continuous_trigger_window_s（与 d7 同源同值；0=禁用=全栈计数
        ——长直播累计不衰减，等价原「计数不清零」语义）。window>0 时窗口外
        旧事件从栈头滑出（惰性清理）。
        """
        stack = self.tier_events.get(rule.rule_id)
        if not stack:
            return 0
        window = float(getattr(self.cfg, "continuous_trigger_window_s", 0.0) or 0.0)
        if window <= 0:
            return len(stack)
        cut = now - window
        i = 0
        n = len(stack)
        while i < n and stack[i] <= cut:
            i += 1
        if i:
            del stack[:i]  # 惰性清理窗口外旧时间戳
        return n - i

    # ---------- B3 显式状态转移表：分类器/守卫/动作/分派（2026-11-27 批次） ----------

    def _named_state(self) -> str:
        """B3 具名状态分类器（P0-1 显式化）：把隐式组合状态折叠为触发判定域的
        有限具名状态。真实空间=具名状态 × 步相位（_seq_phase）× 挂起覆盖层
        （song_suspended.kind）——触发判定只消费具名状态，步相位细节（main_done/
        exit_started/preempt 门）由具名守卫消费。

        分类优先序=hold_for_song 先于 resident（song hold 序列恒归 SONG_HOLD；
        两标志按构建点互斥——resident=dynamic_text 序列、hold=song 规则主线）。
        """
        if self.state != "sequence" or self.seq is None:
            return "DEFAULT"
        if self.seq.get("hold_for_song"):
            return "SONG_HOLD"
        if self.seq.get("resident"):
            return "RESIDENT"
        return "SEQ"

    def _seq_phase(self) -> str:
        """B3 步相位观测（get_state 透出；非触发判定域）：当前步级相位——
        await_switch（切换已发出未受理）/ song_hold / res_hold_loop /
        enter|main|exit|finished。"""
        if self.state != "sequence" or self.seq is None:
            return ""
        seq = self.seq
        steps = seq.get("steps") or []
        idx = int(seq.get("idx") or 0)
        if idx >= len(steps):
            return "finished"
        step = steps[idx]
        name = str(step.get("name") or "")
        if not seq.get("step_accepted"):
            return f"{name}:await_switch"
        if step.get("hold_for_song"):
            return "song_hold"
        if (seq.get("resident") and name == "main"
                and step.get("audio_mode") == "none"):
            return "res_hold_loop"
        return name

    @classmethod
    def _verify_transition_table(cls) -> None:
        """B3 装载期穷尽检查（模块导入时执行一次）：触发决策表必须覆盖
        「具名状态 × trigger」全域——每个具名状态按行序模拟必达 always 兜底行；
        守卫/动作注册齐全；state 取值合法。半张表=import 即失败（宁可拒绝启动
        不静默跑残表——P0-1 根治的静态保证）。"""
        # 显式 raise（2026-11-27 审查修复 #6）：assert 在 python -O 下被剥除，
        # 装载期穷尽检查（P0-1 静态保证）会整体失效——改为 RuntimeError。
        for row in cls._TRIGGER_TABLE:
            if row.guard not in cls._TRIGGER_GUARDS:
                raise RuntimeError(f"[编排] 触发决策表守卫未注册：{row.guard}")
            if row.action not in cls._TRIGGER_ACTIONS:
                raise RuntimeError(f"[编排] 触发决策表动作未注册：{row.action}")
            if row.state != "*" and row.state not in cls.ENGINE_STATES:
                raise RuntimeError(f"[编排] 触发决策表具名状态非法：{row.state}")
        for state in cls.ENGINE_STATES:
            bounded = False
            for row in cls._TRIGGER_TABLE:
                if row.event != "trigger" or row.state not in ("*", state):
                    continue
                if row.guard == "always":
                    bounded = True
                    break
            if not bounded:
                raise RuntimeError(
                    f"[编排] 触发决策表穷尽性缺陷：具名状态 {state} 无 always 兜底行；"
                    "fix=为该状态补兜底行（B3 表驱动架构装载期检查）")

    # ----- 具名守卫（签名统一 (rule, trigger_type) -> bool；谓词与旧嵌套
    # if-else 逐条对应，行为零变化） -----

    def _tg_same_rule_main_running(self, rule, trigger_type) -> bool:
        """同键再触发且 main 未完成（条件2 忽略分支）。"""
        seq = self.seq
        return (seq is not None and rule.rule_id == seq["rule_id"]
                and not seq["main_done"])

    def _tg_same_rule_exit_done(self, rule, trigger_type) -> bool:
        """同键再触发且 main 已完成（exit 步中，WP-9 重排分支）。"""
        seq = self.seq
        return (seq is not None and rule.rule_id == seq["rule_id"]
                and bool(seq["main_done"]))

    def _tg_song_hold_song_trigger(self, rule, trigger_type) -> bool:
        """song hold 序列中 song 类型触发（d11 点歌队列承接分支）。"""
        return (self._arbitration_active() and self.seq is not None
                and bool(self.seq.get("hold_for_song"))
                and trigger_type == "song")

    def _tg_song_hold_arb_on(self, rule, trigger_type) -> bool:
        """song hold 序列中非 song 触发且仲裁开（放弃分支；off 态走兜底打断
        =d22 整体回退语义）。"""
        return (self._arbitration_active() and self.seq is not None
                and bool(self.seq.get("hold_for_song")))

    def _tg_queue_mode(self, rule, trigger_type) -> bool:
        """queue 模式规则且序列执行中（O1/O2 排队分支）——DEFAULT 态 queue 规则
        直接启动（旧代码 queue 门位于序列分支内部，default_state_starts_directly
        行为锁；无 seq 时 _enqueue_pending 日志引用 seq 会崩）。"""
        return (self.seq is not None and rule.interrupt == "queue")

    def _tg_susp_resident_dynamic_text(self, rule, trigger_type) -> bool:
        """resident 挂起占用中且本轮触发为新 dynamic_text（挂起指针更新分支）。"""
        susp = self.song_suspended
        return (self._arbitration_active() and susp is not None
                and susp.get("kind") == "resident"
                and trigger_type == "dynamic_text")

    def _tg_resident_yield(self, rule, trigger_type) -> bool:
        """resident 序列让位（T8/d13：更高/同权重 standard 或 999 特权 resident
        被任意 standard 让位）。"""
        seq = self.seq
        if not (self._arbitration_active() and seq is not None
                and seq.get("resident")):
            return False
        cur_p = self._rule_priority(self.cfg.rule_by_id(seq["rule_id"]))
        new_p = self._rule_priority(rule)
        return new_p >= cur_p or cur_p >= 999

    def _tg_weight_lower(self, rule, trigger_type) -> bool:
        """standard 打断权重门（O3：低优触发不得打断高优序列，改走排队）。"""
        seq = self.seq
        if seq is None:
            return False
        cur_p = self._rule_priority(self.cfg.rule_by_id(seq["rule_id"]))
        return self._rule_priority(rule) < cur_p

    def _tg_preempt_gate(self, rule, trigger_type) -> bool:
        """Gap A 无缝主线门（2026-11-24：segment_end 主线未播完 → 延迟抢占登记）。"""
        seq = self.seq
        if seq is None:
            return False
        if not (self._arbitration_active() and not seq.get("resident")
                and not seq.get("hold_for_song") and not seq["main_done"]):
            return False
        return self._seq_main_switch_at(seq) == "segment_end"

    def _tg_always(self, rule, trigger_type) -> bool:
        """兜底守卫（穷尽性终点）。"""
        return True

    # ----- 具名动作（签名统一，返回 decide_trigger 决策 dict；与旧分支逐条
    # 对应，行为零变化） -----

    def _ta_in_progress(self, rule, trigger_type, match_value, data, now,
                        commands, completion_text) -> dict:
        return {"accepted": False, "takeover": False, "rule": rule,
                "commands": commands, "reason": "in_progress",
                "retry_after_s": None}

    def _ta_requeue(self, rule, trigger_type, match_value, data, now,
                    commands, completion_text) -> dict:
        seq = self.seq
        self.counters[rule.rule_id] = self.counters.get(rule.rule_id, 0) + 1
        _tc = self._tier_count(rule, now)
        self._seq_counter += 1
        seq["pending_requeue"] = build_steps(rule, _tc, self._seq_counter)
        self._patch_resident_copy(completion_text)
        commands.append(LogCmd(
            "info", f"[编排] 规则 {rule.rule_id} 同键重入（第 {self.counters[rule.rule_id]} 次，"
                    f"档位频次 {_tc}），当前 exit 播完后重排"))
        return {"accepted": True, "takeover": rule.has_tts_audio(), "rule": rule,
                "commands": commands, "reason": "requeued", "retry_after_s": None}

    def _ta_song_queued(self, rule, trigger_type, match_value, data, now,
                        commands, completion_text) -> dict:
        commands.append(LogCmd(
            "info", f"[编排] song hold 中 song 重触发：点歌队列追加承接"
                    f"（d11，序列保持 hold，{rule.rule_id} 入歌曲队列）"))
        return {"accepted": True, "takeover": False, "rule": rule,
                "commands": commands, "reason": "song_queued", "retry_after_s": None}

    def _ta_reject_hold(self, rule, trigger_type, match_value, data, now,
                        commands, completion_text) -> dict:
        commands.append(LogCmd(
            "info", f"[编排] song 在播（{self.seq['rule_id']}），触发 {rule.rule_id} "
                    f"放弃（不排队不挂起，保持点歌画面）"))
        return {"accepted": False, "takeover": False, "rule": rule,
                "commands": commands, "reason": "rejected", "retry_after_s": None}

    def _ta_queue_mode(self, rule, trigger_type, match_value, data, now,
                       commands, completion_text) -> dict:
        reason = self._enqueue_pending(rule, trigger_type, match_value, data, commands)
        return {"accepted": False, "takeover": False, "rule": rule,
                "commands": commands, "reason": reason, "retry_after_s": None}

    def _ta_resident_updated(self, rule, trigger_type, match_value, data, now,
                             commands, completion_text) -> dict:
        susp = self.song_suspended
        susp["rule_id"] = rule.rule_id
        susp["deadline"] = now + float(getattr(self.cfg, "step_timeout_s", 60.0) or 60.0)
        self.counters[rule.rule_id] = 0  # 新文案=基础档（画面上下文；A1 起不清档位频次）
        commands.append(LogCmd(
            "info", f"[编排] resident 挂起期间新一轮文案：挂起序列更新为 "
                    f"{rule.rule_id}（deadline 保险丝刷新），打断者完成后切回新文案画面"))
        return {"accepted": True, "takeover": False, "rule": rule,
                "commands": commands, "reason": "resident_updated",
                "retry_after_s": None}

    def _ta_resident_yield(self, rule, trigger_type, match_value, data, now,
                           commands, completion_text) -> dict:
        result = self._suspend_hold_for(rule, data, now, commands, "resident",
                                        match_value=match_value)
        result["reason"] = "interrupted"
        result["retry_after_s"] = None
        return result

    def _ta_weight_lower(self, rule, trigger_type, match_value, data, now,
                         commands, completion_text) -> dict:
        seq = self.seq
        self._enqueue_pending(rule, trigger_type, match_value, data, commands)
        cur_p = self._rule_priority(self.cfg.rule_by_id(seq["rule_id"]))
        new_p = self._rule_priority(rule)
        commands.append(LogCmd(
            "info", f"[编排] 规则 {rule.rule_id}（p={new_p}）权重低于当前序列 "
                    f"{seq['rule_id']}（p={cur_p}），standard 打断让位改排队"))
        return {"accepted": False, "takeover": False, "rule": rule,
                "commands": commands, "reason": "queued", "retry_after_s": None}

    def _ta_preempt_gate(self, rule, trigger_type, match_value, data, now,
                         commands, completion_text) -> dict:
        gate_reason = self._register_preempt_request(
            rule, trigger_type, match_value, data, commands)
        # C3（2026-11-27 用户裁决「音频随画面取消」）：takeover 保持 False——
        # new 语义礼物答谢只走原生链（has_tts_audio 对 new 规则恒 song，编排
        # 无法代生音轨；takeover=True 会连正常执行的答谢一起吞掉）。取消语义
        # 由**令牌机制**实现：登记成功时 _register_preempt_request 向触发载荷
        # 盖章 choreo_preempt_token——原生答谢携带令牌入队，被覆盖/执行/失效
        # 时令牌死亡，播放侧出队门丢弃死令牌条目（音频随画面取消；P1-7
        # 「有声音无画面」错配消除）。
        return {"accepted": False, "takeover": False, "rule": rule,
                "commands": commands, "reason": gate_reason, "retry_after_s": None}

    def _ta_start_sequence(self, rule, trigger_type, match_value, data, now,
                           commands, completion_text) -> dict:
        result = self._start_sequence(rule, data, now, commands,
                                      match_value=match_value)
        self._patch_resident_copy(completion_text)
        result["reason"] = "accepted"
        result["retry_after_s"] = None
        return result

    def _ta_interrupt_sequence(self, rule, trigger_type, match_value, data, now,
                               commands, completion_text) -> dict:
        result = self._interrupt_sequence(rule, data, now, commands,
                                          match_value=match_value)
        self._patch_resident_copy(completion_text)
        result["reason"] = "interrupted"
        result["retry_after_s"] = None
        return result

    def _dispatch_trigger(self, rule, trigger_type, match_value, data, now,
                          commands: List, completion_text: str) -> dict:
        """B3 触发决策表分派（2026-11-27 显式状态转移批次）：按具名状态取
        _TRIGGER_TABLE 行序匹配——首条「state 命中 + 守卫为真」行生效，其动作
        产出决策 dict。穷尽性由 _verify_transition_table 装载期保证（每具名
        状态必达 always 兜底行），运行至此仍未命中=表配置缺陷，立即失败不
        静默。"""
        state = self._named_state()
        for row in self._TRIGGER_TABLE:
            if row.event != "trigger" or row.state not in ("*", state):
                continue
            guard = getattr(self, self._TRIGGER_GUARDS[row.guard])
            if not guard(rule, trigger_type):
                continue
            action = getattr(self, self._TRIGGER_ACTIONS[row.action])
            return action(rule, trigger_type, match_value, data, now,
                          commands, completion_text)
        raise AssertionError(
            f"[编排] 触发决策表分派落空：problem=转移表穷尽性缺陷；"
            f"cause=具名状态 {state} 无匹配行；"
            "fix=检查 _TRIGGER_TABLE 与 _verify_transition_table")

    # ---------- 触发判定（同步纯内存，on_gift/on_keyword/on_song 路径） ----------

    def decide_trigger(self, trigger_type: str, match_value: str, data: dict,
                       completion_text: str = "") -> dict:
        """触发判定。Returns: {accepted, takeover, rule, commands, reason, retry_after_s}

        reason 枚举（GAP-3/F2 义务；退役值见下）：拒绝值 =
        no_match / below_min_price / disabled / rejected（2026-11-16 批次：song
        hold 中 standard 触发放弃）/ preempt_pending（2026-11-24 批次：无缝主线
        保护中，延迟抢占已登记或低优触发排队）；接受值 = accepted（DEFAULT 启动）/
        interrupted（条件3 打断）/ requeued（条件2 重排，accepted=true 说明值）+
        queued/queued_full（排队，accepted=false）/ song_queued（d11 点歌队列
        承接）/ resident_updated（2026-11-16 批次：挂起 resident 指针更新）。
        历史值 throttled/hold 已随 R-ceo5 退役（Eng H3）；type_throttled 已随
        2026-11-06 三强制拆除批次退役（entrance/follow 类型节流移除，防御职责
        归上游 process_data 限入&去重管线）。模拟触发与观测消费。
        """
        now = self.clock()
        commands: List = []
        # 完成锚刷新（2026-11-25 问题③根修，先于一切接受/拒绝分支）：文案
        # 音频照常入播放链（no_match/被拒只影响画面）——凡携带 completion_text
        # 的 dynamic_text 触发一律刷新完成对齐锚至最新文章，防尾段回执与
        # 旧锚失配（详见 _patch_completion_anchor docstring）
        if trigger_type == "dynamic_text":
            self._patch_completion_anchor(completion_text)
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
        # T4 三强制三类型专属节流已随 2026-11-06 三强制拆除批次退役——防御职责
        # 归上游 process_data 六类限入&去重管线（2026-11-04 事实修正自证）。

        # d7 连续触发窗口（2026-11-05 批次）：窗口判定在锚点刷新前——此刻
        # key_last 仍持上一触发时刻，窗外=计数清零（本次触发随后从基础档重新
        # 计数）。滑动锚点（d24a）：下一行的写入即锚点刷新，命中路径
        # （in_progress/requeued/queued/interrupted/DEFAULT 启动）统一刷新——
        # 未匹配不经过此处=不刷新（e6 断言锚定）。
        self._expire_counter_window(rule.rule_id, now)
        self.key_last[rule.rule_id] = now
        # A1（2026-11-27）：命中规则的每次触发都记档位频次事件——包括本次
        # 触发被忽略/排队/挂起更新的情形（频次语义=观众刷屏强度，与画面
        # 是否受理正交；与 D-4 打断清零解耦的核心）。
        self._record_tier_event(rule, now)

        # B3（2026-11-27 显式状态转移批次）：触发判定域压平为有序守卫转移表
        # （具名状态 × 守卫 → 动作，_TRIGGER_TABLE），行序=旧嵌套 if-else 求值序，
        # 行为零变化；穷尽性由 _verify_transition_table 装载期检查保证。
        return self._dispatch_trigger(rule, trigger_type, match_value, data,
                                      now, commands, completion_text)

    def _patch_resident_copy(self, completion_text: str):
        """completion_text 注入（2026-11-25 画面同步修复）：T3「关键词|文案」
        格式触发时 resident_copy 必须是文案全文而非关键词——文案完成回执
        （分段文本）按归一化结尾锚定对齐 resident_copy，关键词永远无法命中
        （实测表现：文案消费完画面滞留至 loop_deadline 保险丝/下次触发）。
        仅 resident（dynamic_text）序列生效；queued 画面队列稍后经
        _start_sequence 重放时无 completion_text（回退 match_value+保险丝）。"""
        if (completion_text and self.seq is not None
                and self.seq.get("resident")):
            self.seq["resident_copy"] = str(completion_text)

    def _patch_completion_anchor(self, completion_text: str):
        """完成锚刷新（2026-11-25 问题③根修）：动态文案任务多文章连发时，
        后续文章的编排触发可被拒/忽略（song hold 拒绝、条件2 in_progress、
        resident_updated 等），但**文案音频照常入播放链**（正交声明）——
        尾段回执（最后一篇文章末段）与旧锚（首篇文章）永不命中，copy_done
        只能靠 loop_deadline 保险丝（实测歌毕文案播完后画面滞留 60s+ 不回
        默认）。凡携带 completion_text 的 dynamic_text 触发一律把锚刷新到
        最新文章（活动 resident 序列与挂起态两处），保证尾段回执可命中。"""
        if not completion_text:
            return
        susp = self.song_suspended
        if susp is not None and susp.get("kind") == "resident":
            susp["copy"] = str(completion_text)
        if self.seq is not None and self.seq.get("resident"):
            self.seq["resident_copy"] = str(completion_text)

    def _match_rule(self, trigger_type: str, match_value: str, data: dict):
        """规则匹配（义务 C3-3/WP-10：gift 全等+min_price / keyword 独立词表子串 / song 可空=任意）。

        Returns: (rule, reason)——reason 仅在 rule=None 时非 None：
        no_match（无命中）/ below_min_price（礼物名命中但价值未达门槛，GAP-3 reason 枚举）。
        """
        price_gate_hit = False
        for rule in self.enabled_rules:
            # 契约修复（2026-11-23）：dynamic_text 事件除命中 dynamic_text 规则外，
            # 还可命中 keyword 类型规则——恢复 UI「触发类型」tooltip 承诺口径
            # （keyword=弹幕或动态文案关键词子串，choreography.py 触发类型下拉）。
            # keyword 规则要求 match 非空（防空 match 吞掉任意文案），子串口径
            # 与 keyword 分支一致；命中顺序仍为 enabled_rules 首个命中。
            if trigger_type == "dynamic_text" and rule.trigger_type == "keyword":
                if rule.match and _normalize_text(match_value).find(
                        _normalize_text(rule.match)) >= 0:
                    return rule, None
                continue
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

    @staticmethod
    def _seq_main_switch_at(seq: dict) -> str:
        """当前序列主线 switch_at（2026-11-24 Gap A）。

        扫 steps 取 name=="main" 步的 switch_at——不用快照字段（pending_requeue
        整体替换 steps L1145 会使快照过期）。找不到 main 步（防御）=immediate
        （不保护）。
        """
        for s in seq.get("steps") or []:
            if s.get("name") == "main":
                return str(s.get("switch_at") or "segment_end")
        return "immediate"

    def _register_preempt_request(self, rule: RuleCfg, trigger_type: str,
                                  match_value: str, data: dict,
                                  commands: List) -> str:
        """Gap A 无缝门登记（2026-11-24 批次）：preempt_request 单槽延迟抢占。

        单槽覆盖语义对齐 O3「同权重保持抢占」：无已登记请求或新触发优先级 >=
        已登记（后者赢）→ 覆盖登记；优先级更低 → _enqueue_pending 排队（排序
        与消息/音频队列权重一致——抢占执行后排队者在新序列播完依次出队）。
        主线播完前触发者音频照常原生链消费（编排只延迟画面切换）。

        C3 令牌盖章（2026-11-27「音频随画面取消」裁决）：登记成功的触发向
        data 载荷盖章 choreo_preempt_token——gift_handle 原生答谢携带令牌入队，
        本请求被覆盖/执行/序列更替时令牌死亡（preempt_token_alive），播放侧
        出队门丢弃死令牌条目（P1-7 错配消除）。排队回退路径不盖章（画面稍后
        会来，原生答谢=既有 queue 模式语义）。

        Returns:
            "preempt_pending"（已登记）| "queued"（低优入队）| "queued_full"（满额舍弃）
        """
        self._preempt_token_seq += 1
        _token = f"preempt#{self._preempt_token_seq}"
        entry = {"trigger_type": trigger_type, "match_value": match_value,
                 "data": dict(data or {}), "rule_id": rule.rule_id,
                 "priority": self._rule_priority(rule), "token": _token}
        cur = self.seq.get("preempt_request")
        if cur is None or entry["priority"] >= cur.get("priority", 0):
            if cur is not None:
                self.metrics["preempt_overwritten"] += 1  # A3 遥测（P1-7 覆盖丢弃观测）
                # C3：旧令牌随覆盖死亡（旧请求原生答谢由出队门丢弃）
            self.seq["preempt_request"] = entry
            if isinstance(data, dict):
                data["choreo_preempt_token"] = _token  # 盖章：原生答谢携带令牌
            commands.append(LogCmd(
                "info", f"[编排] 规则 {rule.rule_id} 触发登记延迟抢占"
                        f"（当前主线无缝切换保护中，主线播完后切离场转 {rule.rule_id} 入场）"))
            return "preempt_pending"
        return self._enqueue_pending(rule, trigger_type, match_value, data, commands)

    def preempt_token_alive(self, token: str) -> bool:
        """C3 令牌活性查询（2026-11-27，播放侧出队门数据源）：令牌对应的延迟
        抢占请求是否仍是当前序列的活跃登记。覆盖（单槽换人）/执行（主线播完
        转入抢占序列）/序列更替（打断/收尾）均=死亡——死令牌的原生答谢条目
        出队时丢弃（音频随画面取消）。"""
        if not token or self.seq is None:
            return False
        cur = self.seq.get("preempt_request") or {}
        return cur.get("token") == token

    def _execute_preempt_request(self, rule: RuleCfg, req: dict, now: float,
                                 commands: List) -> List:
        """Gap A 延迟抢占执行点（2026-11-24 批次）：主线播完时刻的打断。

        与 _interrupt_sequence 同构（旧规则计数清零 D-4、[旧 exit + 新 steps]
        队列、新规则计数+1），差异=执行锚点为主线完成而非触发时刻——触发者
        音频已在主线播放期间经原生链消费。旧 exit 未开始（main 完成点恒成立）
        插队衔接（与 _interrupt_sequence 一致：exit 未配置时回退重放末段作过渡）。"""
        seq = self.seq
        old_rule_id = seq["rule_id"]
        old_inflight = seq.get("inflight")
        if old_inflight and old_inflight.get("step_id"):
            commands.append(CancelAudio(old_inflight["step_id"]))
        self.counters[old_rule_id] = 0  # 他键触发清零（D-4：画面上下文；A1 起不清档位频次）
        self.counters[rule.rule_id] = self.counters.get(rule.rule_id, 0) + 1
        self._seq_counter += 1
        _tc = self._tier_count(rule, now)
        new_steps = build_steps(rule, _tc, self._seq_counter)
        old_exit = dict(seq["steps"][-1])
        old_exit["step_id"] = f"{old_rule_id}:exit#preempt{self._seq_counter}"
        queue = [old_exit] + list(new_steps)
        self.seq = {
            "rule_id": rule.rule_id,
            "steps": queue,
            "idx": 0,
            "hold_until": now,
            "main_done": False,
            "exit_started": False,
            "pending_requeue": None,
            "inflight": None,
            "current_deadline": None,
            "step_accepted": False,
            "trigger_data": dict(req.get("data") or {}),
            "match_value": str(req.get("match_value") or ""),
            "tier_mood": (select_tier(rule, _tc).mood
                          if select_tier(rule, _tc) else ""),
            "preempt_request": None,
        }
        commands.append(LogCmd(
            "info", f"[编排] 主线播完执行延迟抢占：{old_rule_id} 计数清零，"
                    f"切离场转 {rule.rule_id} 入场（Gap A，2026-11-24 批次）"))
        commands.extend(self._start_current_step(now, force_immediate=True))
        return commands

    @staticmethod
    def _song_floor_deadline(data: dict, now: float) -> Optional[float]:
        """B 预武装下限（2026-11-26 画面来回跳根修）：song 触发携带
        song_total_s（翻唱成品探测时长）→ hold deadline 下限=接管锚+歌曲总
        时长。旧口径 hold 保险丝（step_timeout_s）从受理起算，首个切片 push
        受理（on_song_started 唯一权威延长信号）被消费积压/前奏推迟超过保险丝
        时序列提前走完（log1.txt 16:55:01 接管→16:56:07 回默认→16:56:32 切回
        实测）。非 song 触发/时长无效/None=下限缺席（保险丝原语义）。"""
        _t = (data or {}).get("song_total_s")
        try:
            _t = float(_t) if _t is not None else 0.0
        except (TypeError, ValueError):
            return None
        return (now + _t) if _t > 0 else None

    def _start_sequence(self, rule: RuleCfg, data: dict, now: float, commands: List,
                        match_value: str = "") -> dict:
        """DEFAULT→SEQUENCE：计数+1、按档位物化三步、发出 enter 切换。

        match_value（2026-11-17 动作编排审查批次 ⑦-B）：触发匹配值随 seq 留痕，
        resident 挂起时作为文案完成对齐锚点（trends_copywriting 完成回执按
        归一化包含匹配）。"""
        self.counters[rule.rule_id] = self.counters.get(rule.rule_id, 0) + 1
        self._seq_counter += 1
        # A1（2026-11-27）：档位物化改吃滑动窗口频次（_tier_count）——打断
        # 清零只影响 counters（画面上下文），不清档位频次。
        _tc = self._tier_count(rule, now)
        steps = build_steps(rule, _tc, self._seq_counter)
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
            # ⑦-B（2026-11-17 动作编排审查批次）：匹配值随 seq 留痕——resident
            # 挂起/恢复时作为文案完成对齐锚点（trends_copywriting 回执包含匹配）
            "match_value": str(match_value or ""),
            "tier_mood": (select_tier(rule, _tc).mood
                          if select_tier(rule, _tc) else ""),
            # T8/d13/N2 + 2026-11-16 插播&切回批次（resident 判定放宽）：
            # dynamic_text 类型序列触发即注册 resident——他键打断转挂起、
            # 打断者完成切回（③编排队列切回机制）；999 规则⊆dynamic_text
            # 照常成为 resident（让位特权见 decide_trigger resident 分支）
            "resident": (rule.trigger_type == "dynamic_text"),
            # Gap B（2026-11-24 动作编排完善批次）：resident 主线循环锚点——
            # resident_copy=文案匹配值（trends_copywriting 完成回执双向包含
            # 精确终结循环）；loop_deadline=step_timeout 保险丝（回执丢失防
            # 永循环）；copy_done/loop_i 循环状态。
            "resident_copy": (str(match_value or "")
                              if rule.trigger_type == "dynamic_text" else ""),
            "copy_done": False,
            "loop_deadline": (now + float(getattr(self.cfg, "step_timeout_s", 60.0) or 60.0)
                              if rule.trigger_type == "dynamic_text" else 0.0),
            "loop_i": 0,
            # Gap A（2026-11-24 批次）：无缝门延迟抢占单槽（None=无请求）
            "preempt_request": None,
            # B 预武装（2026-11-26）：song 触发携带的歌曲总时长下限（None=缺席）
            "song_deadline_floor": self._song_floor_deadline(data, now),
        }
        self.state = "sequence"
        commands.append(LogCmd("info",
                               f"[编排] 触发 {rule.rule_id}（第 {self.counters[rule.rule_id]} 次，"
                               f"mood={self.seq['tier_mood'] or 'base'}），进入序列"))
        commands.extend(self._start_current_step(now, force_immediate=True))
        return {"accepted": True, "takeover": rule.has_tts_audio(), "rule": rule, "commands": commands}

    def _interrupt_sequence(self, rule: RuleCfg, data: dict, now: float, commands: List,
                            match_value: str = "") -> dict:
        """条件3：旧键序列立即终止，队列替换 [旧 exit?, 新 enter, 新 main, 新 exit]（CP-3）。"""
        seq = self.seq
        old_rule_id = seq["rule_id"]
        # O8（2026-10-28 消费批次）：旧序列未消费的 inflight 音轨随打断撤销——
        # 否则新序列画面期间播出旧规则答谢（画面/音频错配）。已在播/已消费的
        # 无法撤销，其完成回调因 inflight 重置被静默忽略（无副作用）。
        old_inflight = seq.get("inflight")
        if old_inflight and old_inflight.get("step_id"):
            commands.append(CancelAudio(old_inflight["step_id"]))
        self.counters[old_rule_id] = 0  # 他键触发清零（D-4：画面上下文；A1 起不清档位频次）
        self.counters[rule.rule_id] = self.counters.get(rule.rule_id, 0) + 1
        self._seq_counter += 1
        _tc = self._tier_count(rule, now)
        new_steps = build_steps(rule, _tc, self._seq_counter)
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
            # ⑦-B（2026-11-17 动作编排审查批次）：匹配值随 seq 留痕——resident
            # 挂起/恢复时作为文案完成对齐锚点（trends_copywriting 回执包含匹配）
            "match_value": str(match_value or ""),
            "tier_mood": (select_tier(rule, _tc).mood
                          if select_tier(rule, _tc) else ""),
            # B 预武装（2026-11-26）：song 触发携带的歌曲总时长下限（None=缺席）
            "song_deadline_floor": self._song_floor_deadline(data, now),
        }
        commands.append(LogCmd("info",
                               f"[编排] 他键触发：{old_rule_id} 计数清零，切换至 {rule.rule_id}"
                               f"（{'含旧 exit 衔接' if not seq['exit_started'] else '旧 exit 已显示，直接入场'}）"))
        commands.extend(self._start_current_step(now, force_immediate=True))
        return {"accepted": True, "takeover": rule.has_tts_audio(), "rule": rule, "commands": commands}

    def _suspend_hold_for(self, rule: RuleCfg, data: dict, now: float,
                          commands: List, kind: str = "song",
                          match_value: str = "") -> dict:
        """d9/e4（song）+T8/d13/N2/C3（resident 切回）——hold 序列让位挂起
        （seq 旁路结构），打断者序列照常启动。

        kind=song：deadline=歌曲锚（on_song_started 挂起分派刷新——F-B）；
        kind=resident：deadline=step_timeout 保险丝（播报时长不可知的诚实
        上限；到期回默认=播报已结束）。
        A3 遥测（2026-11-27）：挂起计数按 kind 分账。

        与 _interrupt_sequence 的差异：不撤销歌曲链路（伴奏/人声归音频侧；编排
        inflight 在 hold 步应为 None，残留答谢由既有完成回调静默忽略）；旧序列
        转入 song_suspended 挂起态（打断者完成后按 d2 恢复）。D-4 计数清零照常
        （e9：恢复重入=基础档画面）。总开关 off 时本方法不被调用（decide_trigger
        门控）。

        2026-11-16 动态文案插播&切回批次：kind=resident 时**先插播旧规则 exit
        离场段**再排打断者 enter/main/exit（参照 _interrupt_sequence CP-3 的
        exit 插入做法——旧 exit 切换请求已发出则不重插）；song hold 场景保持
        主视觉→打断者直切不重插（该路径现仅存于防御分支）。"""
        seq = self.seq
        old_rule_id = seq["rule_id"]
        self.counters[old_rule_id] = 0  # 他键触发清零（D-4 画面上下文；A1 起不清档位频次）
        # A3 遥测（2026-11-27）：挂起按 kind 分账（resident 让位率/song 挂起率）
        self.metrics["resident_suspends" if kind == "resident" else "song_suspends"] += 1
        # A2/C1 不变量（2026-11-27 双栈化修订）：对应 kind 锚槽写入前必须为空
        # ——双栈后断言按槽分立（resident 锚被占时不阻碍 song 挂起入 song 槽，
        # 反之亦然）；同槽覆写=不变量击穿，立刻暴露不静默（P0-2）。
        _slot = self._suspend_resident if kind == "resident" else self._suspend_song
        assert _slot is None, (
            f"[编排] 不变量击穿：{kind} 挂起锚槽写入前非空（{_slot}）；"
            "problem=挂起锚将被静默覆盖丢锚（P0-2）；cause=新分支在挂起占用期写入同槽；"
            "fix=检查新分支是否应先消费/清空对应锚（d2/d13/⑦-B 语义）")
        if kind == "song":
            _deadline = seq.get("current_deadline")
        else:
            _deadline = now + float(getattr(self.cfg, "step_timeout_s", 60.0) or 60.0)
        # setter 按 kind 定向入槽（song→song 锚 / resident→resident 锚）
        self.song_suspended = {
            "rule_id": old_rule_id,
            "deadline": _deadline,
            "entered_at": now,
            "kind": kind,
            # ⑦-B（2026-11-17 动作编排审查批次）：resident 挂起时登记其文案
            # 匹配值——trends_copywriting 完成回执挂起期间先行登记 copy_done
            # （恢复时文案已消费完则跳过 hold 直回默认轮播）。
            # 2026-11-26 回归修复：读 resident_copy（完成对齐锚=文案全文，
            # 2026-11-25 画面同步修复引入）优先、match_value（T3 关键词）
            # 回退——与 RC2 挂起站点（on_song_started L1215）口径一致。原
            # 实现恒读 match_value：T3「关键词|文案」路径 match_value=关键词，
            # 挂起锚退化为关键词，恢复后分段回执结尾锚定永不命中 → copy_done
            # 不落位 → resident 主线空转滞留（log7 实测：点歌打断后文案消费
            # 完画面不回默认，直至下次触发刷锚）。旧验证被挂起期触发刷锚
            # 兜底路径掩盖（消费门拦截触发后兜底失效，缺陷裸露）。
            "copy": (str(seq.get("resident_copy")
                         or seq.get("match_value") or "")
                     if kind == "resident" else ""),
            "copy_done": False,
        }
        self._seq_counter += 1
        self.counters[rule.rule_id] = self.counters.get(rule.rule_id, 0) + 1
        new_steps = build_steps(rule, self.counters[rule.rule_id], self._seq_counter)
        # 2026-11-16 动态文案插播&切回批次：resident 插播先播旧规则离场段
        # （画面离场过渡；resident 步骤纯画面为主，不撤销 inflight——挂起恢复
        # 后剩余画面段照常推进，残留音频完成回调因 inflight 重置被静默忽略）
        if kind == "resident" and not seq["exit_started"]:
            old_exit = dict(seq["steps"][-1])
            old_exit["step_id"] = f"{old_rule_id}:exit#interrupted{self._seq_counter}"
            new_steps.insert(0, old_exit)
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
            # ⑦-B（2026-11-17 动作编排审查批次）：匹配值随 seq 留痕——resident
            # 挂起/恢复时作为文案完成对齐锚点（trends_copywriting 回执包含匹配）
            "match_value": str(match_value or ""),
            # B 预武装（2026-11-26）：song 触发让位接管时携带的歌曲总时长下限
            "song_deadline_floor": self._song_floor_deadline(data, now),
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
                           kind: str = "song", copy_text: str = "") -> List:
        """d2/e9 恢复（T8 泛化 resident 同构）：回主画面（基础档 hold，deadline
        续挂起锚）。

        kind=song：恢复序列仅主线 hold 步（不重放入场/离场——主视觉连续；入场
        已播过，离场留给自然结束后的默认轮播）；deadline 到点由既有 on_tick
        超时机制走条件1 回默认池。e9：打断时 D-4 已清零，恢复=基础档画面
        （select_tier(rule, 1) 与首次触发同档）。
        kind=resident：2026-11-16 动态文案插播&切回批次——**重播 enter 步骤**
        （画面重新进场过渡，规则无 enter 步则跳过直接回主画面）再进 main/hold
        主画面，不直接回默认轮播；deadline=step_timeout 保险丝（播报时长不可知
        的诚实上限——到期即播报已结束，hold 播完走条件1 回默认）。
        copy_text（⑦-B，2026-11-17 动作编排审查批次）：resident 恢复序列登记
        其文案匹配值——on_audio_complete 收到 trends_copywriting 完成回执且
        归一化包含匹配时，hold 精确提前结束回默认（不再只依赖保险丝）。"""
        self._seq_counter += 1
        tier = select_tier(rule, 1)
        overrides = tier.segment_overrides if tier else {}
        base = rule.name_step("main")
        is_song = (kind == "song")
        # 覆盖段属性随覆盖段自身 sidecar（缺口修复；base 缺省=保守 on/None）
        _m_lip, _m_dur = (_step_material_props(base, tier, "main")
                          if base else (True, None))
        hold_step = {
            "step_id": f"{rule.rule_id}:main#resume{self._seq_counter}",
            "name": "main",
            "segment": overrides.get("main", base.segment if base else ""),
            # 主线切换方式随规则（2026-11-27 离场跳过 bug 修复）：恢复序列
            # exit 步的 at 判定（_seq_main_switch_at）扫描 main 步——硬编码
            # immediate 会让恢复离场恒走立刻模式。入口切换仍由
            # _start_current_step(force_immediate=True) 强制 immediate（恢复
            # 显示必须立即），本字段只承载模式语义。
            "switch_at": (base.switch_at if base else "immediate"),
            "duration_s": _m_dur,
            "audio_mode": base.audio_mode if base else "none",
            "audio_path": base.audio_path if base else "",
            "pred_version": rule.pred_version,
            "lip_flag": _m_lip,
            "hold_for_song": is_song,
        }
        # 2026-11-16 动态文案插播&切回批次：resident 恢复重播 enter（基础档；
        # 重播仅恢复点单次——此后 resident 在播，再触发走条件2/让位常规路径，
        # 不重复播 enter 死循环）。enter 为可选过渡步，缺省=直接回主画面。
        steps: List = []
        if not is_song:
            enter_base = rule.name_step("enter")
            if enter_base is not None:
                _e_lip, _e_dur = _step_material_props(enter_base, tier, "enter")
                steps.append({
                    "step_id": f"{rule.rule_id}:enter#resume{self._seq_counter}",
                    "name": "enter",
                    "segment": overrides.get("enter", enter_base.segment),
                    "switch_at": "immediate",
                    "duration_s": _e_dur,
                    "audio_mode": enter_base.audio_mode,
                    "audio_path": enter_base.audio_path,
                    "pred_version": rule.pred_version,
                    "lip_flag": _e_lip,
                    "hold_for_song": False,
                })
        steps.append(hold_step)
        # 方案一（2026-11-26 离场步补齐，log3.txt 实测回归）：resident 恢复序列
        # 此前只重建 enter+hold、无 exit 步——文案消费完（copy_done 精确终结/
        # 保险丝到期）越过步尾直回默认，离场永久丢失（log3.txt 17:45:28.076/
        # 17:51:08.349 终结与回默认同毫秒、exit 段 39 素材 1.96s 从未出现实测）。
        # 补基础档 exit 步：终结时先播离场过渡再回默认，与正常首播序列
        # （enter→main→exit）行为对齐。song 恢复保持无 exit（离场留给自然结束
        # 后的默认轮播——歌队列连续性既有语义不动）。
        if not is_song:
            exit_base = rule.name_step("exit")
            if exit_base is not None:
                _x_lip, _x_dur = _step_material_props(exit_base, tier, "exit")
                steps.append({
                    "step_id": f"{rule.rule_id}:exit#resume{self._seq_counter}",
                    "name": "exit",
                    "segment": overrides.get("exit", exit_base.segment),
                    "switch_at": exit_base.switch_at,
                    "duration_s": _x_dur,
                    "audio_mode": exit_base.audio_mode,
                    "audio_path": exit_base.audio_path,
                    "pred_version": rule.pred_version,
                    "lip_flag": _x_lip,
                    "hold_for_song": False,
                })
        self.seq = {
            "rule_id": rule.rule_id,
            "steps": steps,
            "idx": 0,
            "hold_until": now,
            "main_done": False,
            "exit_started": False,
            "pending_requeue": None,
            "inflight": None,
            "current_deadline": deadline,
            "step_accepted": False,
            "trigger_data": {},
            # ⑦-B：resident 恢复 hold 的文案完成对齐锚点（song 恢复恒空）
            "match_value": ("" if is_song else str(copy_text or "")),
            "resident_copy": ("" if is_song else str(copy_text or "")),
            "tier_mood": "base",
            "hold_for_song": is_song,
            "resident": (not is_song),
            # Gap B（2026-11-24 批次）：恢复序列主线/hold 继续循环至文案消费完
            # （回执终结）或挂起 deadline 保险丝——current_deadline 会被
            # on_switch_result 覆盖为素材时长，独立 loop_deadline 兜底。
            "loop_deadline": deadline,
            "copy_done": False,
            "loop_i": 0,
        }
        self.state = "sequence"
        commands: List = [LogCmd(
            "info", f"[编排] 打断者播完，恢复{'唱歌' if is_song else '播报'}画面"
                    f"（{rule.rule_id} 基础档"
                    f"{'' if is_song else '，重播 enter，终结后补播 exit 离场'}，"
                    f"剩余 {deadline - now:.1f}s——"
                    f"{'d2/e9' if is_song else 'd13/N2 resident+2026-11-16 切回批次'}）")]
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
        # 立刻模式离场硬切（2026-11-27 离场跳过 bug 修复）：主线 switch_at=
        # immediate（用户「立刻切换」语义）时离场改 immediate——「音频消费
        # 完成后立刻切离场」；受理=执行，计时起点无脱节。无缝模式（主线
        # segment_end）离场保持边界续接（主线至少完整播放一次后自然衔接；
        # 计时锚定见 on_switch_result 边界等待分支——旧实现离场 deadline 从
        # 受理起算，早于边界到期即收尾回默认，RestoreDefault 清掉排队离场
        # （render_loop M3/L3 last-write-wins）=离场从未显示）。
        if (step["name"] == "exit" and at != "immediate"
                and self._seq_main_switch_at(self.seq) == "immediate"):
            at = "immediate"
            commands.append(LogCmd(
                "info", f"[编排] 立刻模式离场硬切（{step['step_id']}）：主线切换"
                        "方式=立刻切换，音频消费完成后立即离场（2026-11-27 修复）"))
        # 受理时实际 at 留痕（on_switch_result 边界等待判定依据——exit 步
        # switch_at 可能被上行改判/force_immediate 覆盖，不能回读 step 配置）
        self.seq["last_switch_at"] = at
        # 段循环模式（2026-11-24 pingpong 批次）：主线 hold 场景（song hold
        # 主线/动态文案 resident 主线）切 pingpong——素材播完后服务端正放↔
        # 倒放无缝往返（口型音频驱动不受影响）；enter/exit 与普通主线保持
        # 默认锯齿循环（短段单次播放，模式无感）。
        _pingpong = (step["name"] == "main"
                     and bool(step.get("hold_for_song") or self.seq.get("resident")))
        commands.append(SwitchSegment(step["step_id"], step["segment"], at,
                                      step_name=step["name"],
                                      loop_mode="pingpong" if _pingpong else "loop"))
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
            self.degrade_breakdown["switch_failed"] += 1  # A3 分机制遥测
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
        # 边界续接等待（2026-11-27 离场跳过 bug 修复）：segment_end 切换只是
        # 服务端排队（render_loop M16 pending_switch），受理≠执行——实际显示
        # 要等当前素材自然播完的边界（长主线=最多一整轮后）。旧实现从受理
        # 时刻起算本步时长 → deadline 远早于边界到期 → 序列收尾
        # RestoreDefault(align=True) 清掉排队离场（M3/L3 last-write-wins）→
        # 离场从未显示（短音频动态文案实测：主线被砍、离场跳过、直回默认）。
        # 修复：离场步计时锚定边界实际执行（facade 轮询 current_segment 达成
        # 回填 on_switch_executed）；临时上限=前一步时长+本步时长兜底（F1
        # 同构双路径：边界正常=提前精确替换；异常=看门狗降级推进不悬挂）。
        # 音频离场步（tts/file）不走边界等待分支（2026-11-27 审查修复 #2）：
        # 本分支提前 return 会跳过下方 legacy 音频入队——离场告别语/音轨丢失。
        # 音频离场步保持 legacy 计时（deadline=受理+上限，完成=音频回执）。
        if (step["name"] == "exit"
                and step["audio_mode"] == "none"
                and str(self.seq.get("last_switch_at") or "") == "segment_end"):
            _prev_dur = 0.0
            if self.seq["idx"] > 0:
                _prev_dur = float(self.seq["steps"][self.seq["idx"] - 1]
                                  .get("duration_s") or 0.0)
            _fallback = float(getattr(self.cfg, "step_timeout_s", 30.0) or 30.0)
            _step_dur = float(step.get("duration_s") or 0.0) or _fallback
            _wait = (_prev_dur if _prev_dur > 0 else _fallback) + _step_dur
            self.seq["current_deadline"] = now + _wait
            self.seq["boundary_wait"] = True
            logger.info(
                f"[编排] 离场边界续接已排队（{step['step_id']}）：计时锚定边界"
                f"实际执行，临时上限 {_wait:.1f}s"
                f"（前一步 {_prev_dur:.1f}s + 离场 {_step_dur:.1f}s 兜底）")
            return []
        if step.get("pred_version") == "new":
            # 新语义（R-ceo4）：时长=素材时长（apply 二段物化）。hold_for_song=
            # song 规则主线段：deadline=step_timeout_s 保险丝（R-eng1 歌曲未起播
            # 的诚实降级），on_song_started 到达时以「锚点+剩余时长」覆盖延长。
            if step.get("hold_for_song"):
                self.seq["hold_for_song"] = True
                # ⑨-B（2026-11-17 动作编排审查批次）：歌队列逐歌切歌时受理前
                # 已按「锚点+歌曲剩余时长」预置 deadline——保险丝只作下限保底
                # （max），不得收缩真实歌曲剩余时长（否则长片期间提前回默认）
                # B 预武装（2026-11-26）：接管时已知的歌曲总时长下限一并保底
                # （首个切片 push 受理被积压/前奏推迟超保险丝时不提前走完）
                _cands = [now + float(self.cfg.step_timeout_s)]
                _prev = self.seq.get("current_deadline")
                if _prev is not None:
                    _cands.append(float(_prev))
                _floor = self.seq.get("song_deadline_floor")
                if _floor is not None:
                    _cands.append(float(_floor))
                self.seq["current_deadline"] = max(_cands)
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
                self.degrade_breakdown["duration_unknown"] += 1  # A3 分机制遥测
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

    def on_switch_executed(self, step_id: str, now: float) -> List:
        """边界执行回填（2026-11-27 离场跳过 bug 修复）：segment_end 排队切换
        实际显示（EDTalk current_segment 达成）时刻回填——离场步计时从真实
        显示起点起算（边界等待分支的精确路径，替换临时上限）。

        步不匹配（序列已推进/被打断/已收尾）=过期事件忽略；boundary_wait
        未置位=非边界等待受理（immediate 直切，防御），不覆盖既有计时。"""
        if self.seq is None or self.state != "sequence":
            return []
        step = self.seq["steps"][self.seq["idx"]]
        if step["step_id"] != step_id or not self.seq.get("boundary_wait"):
            return []
        _dur = float(step.get("duration_s") or 0.0)
        if _dur <= 0:
            _dur = float(getattr(self.cfg, "step_timeout_s", 30.0) or 30.0)
        self.seq["current_deadline"] = now + _dur
        self.seq["boundary_wait"] = False
        logger.info(f"[编排] 离场段已实际显示（{step_id}）：计时起点=边界执行"
                    f"时刻（+{_dur:.1f}s 后回默认）")
        return []

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

    def _audio_song_alive(self) -> Optional[dict]:
        """音频侧歌曲活跃快照规整（A 收尾防线数据源，2026-11-26）。provider
        未注册/查询异常/形态非法/remaining≤0/歌名空 = None（收尾防线静默
        跳过，落回既有收尾语义）。查询本体=PlaybackMixin.song_hold_alive
        （F-B 音频侧单点持有者——引擎纯状态机不直接触碰音频内部状态）。"""
        _p = self.song_alive_provider
        if _p is None:
            return None
        try:
            _snap = _p()
        except Exception as e:
            logger.debug(f"[编排] 音频侧歌曲活跃查询异常（忽略）：{type(e).__name__}: {e}")
            return None
        if not isinstance(_snap, dict):
            return None
        try:
            _rem = float(_snap.get("remaining_s"))
            _name = str(_snap.get("song_name") or "")
        except (TypeError, ValueError):
            return None
        if not _name or _rem <= 0:
            return None
        return {"song_name": _name, "remaining_s": _rem}

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
            # 2026-11-24 歌毕闪帧根修：挂起窗口=歌曲结束估算+歌后备窗
            # （step_timeout_s）——旧口径 deadline=锚点+剩余（+2s 余量），歌曲
            # 恰好消费整个窗口：歌曲 exit 步播完（歌音频结束后 2~10s）时窗口
            # 已耗尽，序列播完恢复挂起要么被 now<deadline 拒走默认，要么恢复
            # 的 hold 保险丝几秒内到期（文案画面一闪回默认）。歌曲消费的是
            # 插播时长，文案有效期应在歌毕重新起算（恢复后由 copy_done 回执
            # 精确终结，本窗口仅兜底）。
            _grace = float(getattr(self.cfg, "step_timeout_s", 60.0) or 60.0)
            susp["deadline"] = now + remaining + _grace
            commands.append(LogCmd(
                "info", f"[编排] 挂起歌曲切片起播（{song_name}）：挂起 deadline 刷新"
                        f"（歌曲结束+{remaining:.1f}s+歌后备{_grace:.0f}s，F-B 挂起期自校正）"))
            # 2026-11-24 修复批次（RC1）：挂起分支此前直接 return——resident 让位
            # 点歌场景 susp 在整个 song hold 期间恒非 None（仅序列收尾清空），
            # song hold 主线 deadline 被饿死在 step_timeout 保险丝：歌曲时长>
            # 保险丝必然 mid-song 提前走完序列（恢复文案画面→唱歌期间画面漂移；
            # 或歌曲起播前走完→唱歌在默认画面开始）。并行延长当前 song hold
            # 序列 deadline（susp kind=song 防御分支无 seq hold，保持仅刷新）。
            if (self.state == "sequence" and self.seq is not None
                    and self.seq.get("hold_for_song")):
                _steps = self.seq["steps"]
                _idx = self.seq["idx"]
                if _idx < len(_steps) and _steps[_idx].get("hold_for_song"):
                    self.seq["current_deadline"] = now + remaining
                    commands.append(LogCmd(
                        "info", f"[编排] song hold 主线 deadline 并行延长至歌曲"
                                f"结束（锚点+{remaining:.1f}s，RC1 修复）"))
            return commands
        # 2026-11-24 修复批次（RC2）：挂起已清空后歌曲才起播（动态文案播完回
        # 默认/恢复文案 hold 在播）——此前直接 return 无任何画面动作，唱歌在
        # 错误画面开始。歌曲起播命中启用中的 song 规则 → 画面恢复/切至 song
        # hold（⑨-B 同源语义「歌起播画面跟随」）：default 态直接新建 hold 序列；
        # resident 恢复 hold 在播 → 挂起文案（复用 d13/⑦-B 挂起恢复链路）后切
        # song hold，歌毕收尾按既有挂起恢复切回。无命中 song 规则/其它序列
        # （gift 等）在播/总开关 off = 保持幂等 return（零变化）。
        if self._arbitration_active():
            try:
                _matched, _mreason = self._match_rule("song", song_name, {})
            except Exception:
                _matched = None
            if _matched is not None:
                if self.state != "sequence" or self.seq is None:
                    commands.extend(self._switch_song_rule(
                        _matched, now, remaining, song_name))
                    return commands
                if (self.seq.get("resident")
                        and not self.seq.get("hold_for_song")):
                    # 2026-11-24 歌毕闪帧根修：初始窗口与 F-B 刷新同口径
                    # （歌曲结束估算+歌后备窗）——RC1 刷新覆盖前不裸用 60s
                    # 保险丝（歌曲时长>保险丝时同样窗口耗尽闪帧）。
                    _grace = float(getattr(self.cfg, "step_timeout_s", 60.0) or 60.0)
                    self.metrics["resident_suspends"] += 1  # A3 遥测
                    # A2 不变量：RC2 分支位于 susp is None 守卫内，写入前槽必空
                    # （断言锁死——未来分支重排破坏该前提时立即暴露）
                    assert self.song_suspended is None, (
                        "[编排] 不变量击穿：RC2 挂起槽写入前非 None（P0-2 覆写丢锚风险）")
                    self.song_suspended = {
                        "rule_id": self.seq["rule_id"],
                        "deadline": now + remaining + _grace,
                        "entered_at": now,
                        "kind": "resident",
                        "copy": str(self.seq.get("resident_copy")
                                    or self.seq.get("match_value") or ""),
                        "copy_done": bool(self.seq.get("copy_done")),
                    }
                    commands.append(LogCmd(
                        "info", f"[编排] 歌曲起播挂起播报序列（{self.seq['rule_id']}，"
                                "RC2 修复——歌毕按挂起恢复切回文案画面）"))
                    commands.extend(self._switch_song_rule(
                        _matched, now, remaining, song_name))
                    return commands
        if self.state != "sequence" or self.seq is None:
            return commands
        if not self.seq.get("hold_for_song"):
            return commands
        steps = self.seq["steps"]
        idx = self.seq["idx"]
        if idx >= len(steps) or not steps[idx].get("hold_for_song"):
            return commands
        # ⑨-B 逐歌对应（2026-11-17 用户裁决）：队列中下一首歌起播且命中**不同**
        # song 规则=画面直切新规则主线 hold（歌队列连续性——不重插旧离场，非
        # 打断语义故旧规则计数不清零、新规则计数+1 按档位升档）；同规则重触发
        # 或无规则命中=保持当前画面仅刷新 deadline。总开关 off=回退既有语义
        # （只刷新 deadline，d22）。
        if self._arbitration_active():
            try:
                matched_rule, _mreason = self._match_rule("song", song_name, {})
            except Exception:
                matched_rule = None
            if matched_rule is not None and matched_rule.rule_id != self.seq["rule_id"]:
                commands.extend(self._switch_song_rule(matched_rule, now, remaining,
                                                       song_name))
                return commands
        anchor = now
        self.seq["current_deadline"] = anchor + remaining
        commands.append(LogCmd(
            "info",
            f"[编排] 歌曲起播（{song_name}）：主线段保持至歌曲结束"
            f"（锚点+{remaining:.1f}s，R-eng1 预排程权威信号）"))
        return commands

    def _switch_song_rule(self, rule: RuleCfg, now: float, remaining: float,
                          song_name: str) -> List:
        """⑨-B 歌队列逐歌对应（2026-11-17 裁决）：歌队列中下一首起播命中不同
        song 规则——替换当前 hold 序列为新规则主线 hold（仅 hold 步，离场留给
        自然结束后的默认轮播，与 _recover_song_hold 同构）。

        计数语义：新规则 +1（按档位升档——逐歌切换视同有效触发）；旧规则计数
        保留不清零（歌队列是连续承接而非打断，D-4 他键打断清零不适用）。
        deadline=锚点+歌曲剩余时长（on_switch_result 受理时 max 保底不收缩，
        下一片起播 on_song_started 自校正）。"""
        self.counters[rule.rule_id] = self.counters.get(rule.rule_id, 0) + 1
        old_rule_id = self.seq["rule_id"] if self.seq else None
        self._seq_counter += 1
        # A1（2026-11-27）：逐歌切换档位改吃滑动窗口频次（起播信号本身不记
        # 事件——频次事件由 decide_trigger 统一记录；此处仅按已有频次取档）。
        _tc = self._tier_count(rule, now)
        tier = select_tier(rule, _tc)
        overrides = tier.segment_overrides if tier else {}
        base = rule.name_step("main")
        # 覆盖段属性随覆盖段自身 sidecar（缺口修复；base 缺省=保守 on/None）
        _m_lip, _m_dur = (_step_material_props(base, tier, "main")
                          if base else (True, None))
        hold_step = {
            "step_id": f"{rule.rule_id}:main#queue{self._seq_counter}",
            "name": "main",
            "segment": overrides.get("main", base.segment if base else ""),
            "switch_at": "immediate",
            "duration_s": _m_dur,
            "audio_mode": base.audio_mode if base else "none",
            "audio_path": base.audio_path if base else "",
            "pred_version": rule.pred_version,
            "lip_flag": _m_lip,
            "hold_for_song": True,
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
            "current_deadline": now + remaining,
            "step_accepted": False,
            "trigger_data": {},
            "match_value": str(song_name or ""),
            "tier_mood": (tier.mood if tier else ""),
            "hold_for_song": True,
            "resident": False,
        }
        # 2026-11-24 修复批次（RC2）：支持 default 态进入（歌曲起播画面恢复）——
        # 原方法仅由 on_song_started 的 sequence 态分支调用，未设 state。
        self.state = "sequence"
        commands: List = [LogCmd(
            "info", f"[编排] 歌曲队列逐歌切换（{old_rule_id}→{rule.rule_id}，"
                    f"《{song_name}》命中规则，count={self.counters[rule.rule_id]}，"
                    f"mood={tier.mood if tier else 'base'}）：画面直切新规则主线 hold，"
                    f"保持至歌曲结束（锚点+{remaining:.1f}s，⑨-B）")]
        commands.append(SwitchSegment(hold_step["step_id"], hold_step["segment"],
                                      "immediate", loop_mode="pingpong"))
        return commands

    def on_audio_complete(self, qtype: str, content: str, now: float,
                          step_id: Optional[str] = None,
                          group_pending: int = 0,
                          copy_groups_pending: int = 0) -> List:
        """播放完成回调关联（CL-2 + O6 实例序号，2026-10-28 消费批次）。

        group_pending（2026-11-25 画面同步修复）：回执条目自身 split_group 组
        在两队列的剩余段数（播放侧计算传入；无组条目=0）——文案完成判定须
        组内全部消费完，早段回执不再误登记 copy_done。

        copy_groups_pending（B1 结构化终结批次，2026-11-27）：**其它**文案组
        在两队列的组数（播放侧按回执组 id 排除自身统计）——=0 表示全局无
        未消费文案音频。B1 主判定=结构化组事件（本组空+全局空），文本比对
        降级为一致性防线（_copy_content_matched 双向包含），尾窗启发式退役。

        匹配规则：回调携带 step_id（choreo_step_id 全链透传，非空）时按实例
        序号精确匹配——同内容重复入队（同关键词重复触发/同模板答谢/同曲重点）
        不再误配（频繁触发错配根修）；未携带（旧链路/未透传路径）退回
        (type,content)+晚于入队时间 匹配（既有 CL-2 语义）。换序列清空 inflight
        后，旧音频的完成回调两种匹配路径均不推进新序列——F1 竞态根修保持。
        """
        if self.seq is None or self.state != "sequence":
            return []
        # ⑦-B（2026-11-17 动作编排审查批次）resident 文案消费精确对齐——两处：
        # ①挂起期间（打断者序列在播）：文案完成回执先行登记 susp["copy_done"]
        # （恢复时跳过 hold 直回默认，防闪帧）；
        # B1（2026-11-27）：完成判定改结构化组事件三条件（本组空+全局空+
        # 一致性校验），不再依赖尾串锚定。
        susp = self.song_suspended
        if (susp is not None and susp.get("kind") == "resident"
                and susp.get("copy") and not susp.get("copy_done")
                and qtype == "trends_copywriting"):
            rc = _completion_key(susp["copy"])
            cc = _completion_key(content)
            if (rc and cc and _copy_content_matched(rc, cc)
                    and int(group_pending or 0) <= 0
                    and int(copy_groups_pending or 0) <= 0):
                susp["copy_done"] = True
                self.metrics["copy_done_structural"] += 1  # A3 遥测：结构化组事件方式
                logger.info("[编排] 挂起播报文案音频消费完成（结构化组事件："
                            "本组空+全局空，插播期间登记，恢复时跳过切回直回默认）")
                return []
        # ②恢复 hold 期间：inflight 必空（hold 步纯画面/无步级音轨）——文案完成
        # 回执「结构化组事件」命中=文案音频真实消费完，hold 精确提前结束回默认
        # （不再只依赖 step_timeout 保险丝；qtype 过滤防他链路音频误配）
        inflight = self.seq.get("inflight")
        if (not inflight and self.seq.get("resident")
                and self.seq.get("resident_copy")
                and qtype == "trends_copywriting"):
            # 保险丝续期（2026-11-25 主线循环早退回归修复）：分段消费回执=
            # 消费链存活证明——每条回执刷新 loop_deadline。原实现 loop_deadline
            # 序列启动时一次性固定（触发+step_timeout_s），循环轮只延长
            # current_deadline——长文案（分段数×串行合成耗时）必超保险丝，
            # 主线在消费中途被杀（实测 60s 保险丝 vs 26 段≈112s 消费，剩
            # 2-3 句时即回默认跳过离场，10/10 复现）。回执持续推进则循环
            # 不灭；回执停止（链路死亡）最后一次回执后 step_timeout 到期
            # 照常走保险丝收尾，防永循环语义保持。
            if not self.seq.get("copy_done"):
                self.seq["loop_deadline"] = now + float(
                    getattr(self.cfg, "step_timeout_s", 60.0) or 60.0)
            rc = _completion_key(self.seq["resident_copy"])
            cc = _completion_key(content)
            if (rc and cc and _copy_content_matched(rc, cc)
                    and int(group_pending or 0) <= 0
                    and int(copy_groups_pending or 0) <= 0):
                # Gap B（2026-11-24 批次）：登记 copy_done 后仅当主线在播才推进
                # （enter 期回执到达只置位——跳过 enter 会丢过渡段；exit 期置位
                # 无副作用）。循环中的主线由此精确终结（不再只依赖保险丝）。
                self.seq["copy_done"] = True
                self.metrics["copy_done_structural"] += 1  # A3 遥测：结构化组事件方式
                logger.info("[编排] 播报文案音频消费完成，主线循环终结"
                            "（B1 结构化组事件：本组空+全局空 + Gap B 2026-11-24 批次）")
                if self.seq["steps"][self.seq["idx"]]["name"] == "main":
                    return self._complete_step(now, timeout_degrade=False)
                return []
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
        if self.seq.get("boundary_wait"):
            # 边界等待超时（2026-11-27 离场跳过 bug 修复的兜底路径）：临时上限
            # 内 segment_end 排队切换未实际执行——EDTalk 异常（失联/素材边界
            # 迟迟不到）。降级收尾推进（离场可能未显示，恢复默认兜底，不悬挂）。
            self.metrics["boundary_wait_expired"] += 1  # A3 遥测
            logger.warning(
                f"[编排] 步骤 {step['step_id']} 边界等待超时，降级推进："
                "problem=segment_end 排队切换未在窗口内实际显示；"
                "cause=EDTalk 服务异常或当前素材边界迟迟未到；"
                "fix=检查 EDTalk 服务状态")
            return self._complete_step(now, timeout_degrade=False)
        # A3 遥测（2026-11-27）：song hold 主线保险丝触发（非歌毕、纯保险丝走完）
        # =「锚自校正没跟上」的直接信号——持续增长说明 on_song_started 通道异常
        if (step.get("hold_for_song") and step["audio_mode"] == "none"):
            self.metrics["fuse_expired"] += 1
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
            self.degrade_breakdown["audio_timeout"] += 1  # A3 分机制遥测
            # 报警重构（2026-10-26）：音频步降级（回调超时兜底/生成失败，on_tick 与
            # on_audio_enqueued 双路径汇入口）→动作编排报警（60s 冷却合并）
            alarm_notifier.alarm_error(
                alarm_notifier.MODULE_ORCHESTRATION,
                f"编排步骤 {step['step_id']} 音频环节降级推进（累计降级 {self.degrade_total}）",
                fix_hint="音频完成回调超时或生成失败；频繁出现请检查 TTS 与 EDTalk 服务")
        if step["name"] == "main":
            # Gap A 抢占执行点（2026-11-24 批次）：主线播完且有延迟抢占请求 →
            # 直接 [旧 exit + 新规则 steps] 替换（用户语义：主线完整播完→切离场→
            # 转新规则入场）；请求规则已失效（禁用/删除）→ 弃请求正常推进。
            if seq.get("preempt_request"):
                req = seq.pop("preempt_request")
                preempt_rule = self.cfg.rule_by_id(req.get("rule_id"))
                if preempt_rule is not None:
                    return self._execute_preempt_request(preempt_rule, req, now, commands)
                commands.append(LogCmd(
                    "info", f"[编排] 延迟抢占请求 {req.get('rule_id')} 已失效"
                            "（规则禁用/删除），弃请求按原序列推进"))
            # Gap B 主线循环（2026-11-24 批次）：resident（dynamic_text）主线
            # 素材播完且文案音频未消费完且未超循环保险丝 → 重播主线不推进
            # （循环至消费完成）。仅 audio_mode=none 主线（纯画面）——tts/file
            # 主线循环会每轮重复入队音频，非 none 正常推进离场。
            # step_accepted 门（2026-11-24 全量回归修复）：切段失败（on_switch_result
            # ok=False 降级跳步）的步骤从未被接受——不得进循环分支（否则
            # 「失败→跳步→循环→再失败」零间隔自旋打爆线程，全量回归实测
            # 308 万轮直至进程崩溃）；只有切换被接受且正常完成的 main 才循环。
            if (seq.get("resident") and not seq.get("copy_done")
                    and seq.get("step_accepted")
                    and now < float(seq.get("loop_deadline") or 0.0)
                    and step.get("audio_mode") == "none"):
                seq["loop_i"] = int(seq.get("loop_i") or 0) + 1
                self.metrics["loop_rounds"] += 1  # A3 遥测
                seq["inflight"] = None
                # 2026-11-24 pingpong 批次：循环轮不再重发切段指令——主线切段
                # 已带 loop_mode=pingpong（服务端帧级正放↔倒放无缝往返），每轮
                # 重切段会重置为正放首帧（硬切回归）。改为仅延长 deadline：
                # 步骤完成谓词=audio_mode none 计时，延 deadline 即续存，服务端
                # 原段无缝往返直至 copy_done/保险丝到期/exit 切段到达。
                # step_accepted 保持 True（无新切段，无回执等待）。
                _loop_dur = float(step.get("duration_s") or 0.0)
                if _loop_dur <= 0:
                    _loop_dur = float(getattr(self.cfg, "step_timeout_s", 30.0))
                seq["current_deadline"] = now + _loop_dur
                commands.append(LogCmd(
                    "info", f"[编排] resident 主线循环第 {seq['loop_i']} 轮"
                            f"（{seq['rule_id']}，文案音频未消费完，deadline 延长"
                            f"+{_loop_dur:.1f}s——服务端 pingpong 无缝往返，"
                            "Gap B 2026-11-24 pingpong 批次）"))
                return commands
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
        # A 收尾防线（2026-11-26 画面来回跳根修）：序列播完时音频侧歌曲仍活跃
        # （切片待播/唱歌时间轴进行中，垃圾时间不算）=保险丝或挂起锚先于歌终
        # 到期——不回默认、不恢复挂起文案，直切歌曲规则主线 hold（⑨-B 同源
        # 语义，remaining=音频侧真实切片口径，后续 on_song_started 逐段自校正）。
        # 挂起锚保留：新 hold 歌毕到期时条件1 再次进入，按既有 d2/d13 语义恢复
        # 文案或回默认。provider 未注册/查询为空/规则未命中=落回下方既有分支。
        if self._arbitration_active():
            _alive = self._audio_song_alive()
            if _alive is not None:
                _arule = None
                try:
                    _arule, _ar = self._match_rule("song", _alive["song_name"], {})
                except Exception:
                    _arule = None
                if _arule is not None:
                    commands.extend(self._switch_song_rule(
                        _arule, now, _alive["remaining_s"], _alive["song_name"]))
                    return commands
        # 条件1：exit 完成 → 回默认轮播（rotate 原序 / random 每次打乱 / single 单段 pingpong）
        # T4/d2（2026-11-05 批次）：song 伴唱挂起恢复优先——歌曲未结束=回点歌
        # 主线画面（恢复序列=基础档 hold，deadline 续挂起锚）；歌曲已结束
        # （deadline 过点/锚缺失/规则已删/硬上限超时）=清挂起走正常默认轮播
        # （e4：挂起期默认池不接管——恢复在此同步发生，不经 RestoreDefault）。
        # T8：resident（dynamic_text 序列，2026-11-16 批次判定放宽）同构恢复——
        # 重播 enter 后切回播报画面，保险丝到期回默认（d13/N2/C3）；恢复路径
        # 异常（挂起规则已失效 rule_by_id=None / deadline 过点 / 硬上限超时）
        # 一律回退默认轮播，不悬挂。
        susp = self.song_suspended
        if susp is not None and self._arbitration_active():
            # C1（2026-11-27 双栈批次）：定向清槽——只清本锚（另一锚保留，嵌套
            # 挂起 LIFO 依恢复序消费；旧单槽「写 None=全清」在双栈下会误删
            # 未消费的另一锚）
            self._clear_suspend_kind(susp.get("kind") or "song")
            susp_rule = self.cfg.rule_by_id(susp.get("rule_id"))
            susp_deadline = susp.get("deadline")
            # ⑦-B（2026-11-17 动作编排审查批次）：resident 文案在挂起期间已消费
            # 完成（trends_copywriting 完成回执登记 copy_done）——切回后无内容
            # 可播，hold 一闪即回=闪帧；直接回默认轮播（跳过恢复 hold）。
            # Gap B（2026-11-24 批次）：恢复序列循环期间回执到达（②路径登记
            # seq.copy_done）同样视为消费完成——跳过再恢复（re-recover 会重置
            # copy_done 导致回执终结失效、空转到保险丝），直接回默认轮播。
            if (susp.get("kind") == "resident"
                    and (susp.get("copy_done") or seq.get("copy_done"))
                    and susp_rule is not None):
                commands.append(LogCmd(
                    "info", f"[编排] 播报文案音频已消费完成"
                            f"（{'插播期间' if susp.get('copy_done') else '恢复循环期间'}登记），"
                            f"跳过切回直回默认轮播（⑦-B 精确对齐 + Gap B，{susp['rule_id']}）"))
                susp_rule = None
            if (susp_rule is not None and susp_deadline is not None
                    and now < susp_deadline
                    and now - susp["entered_at"] <= self.SONG_SUSPEND_MAX_S):
                commands.extend(self._recover_song_hold(
                    susp_rule, susp_deadline, now,
                    kind=susp.get("kind") or "song",
                    copy_text=str(susp.get("copy") or "")))
                return commands
            if susp_rule is not None and susp_deadline is not None and now >= susp_deadline:
                self.metrics["suspend_expired_default"] += 1  # A3 遥测
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
        # A3 遥测（2026-11-27）：resident 文案以保险丝兜底方式终结（尾窗未命中
        # ——P1-6 结构化终结的回归信号）；song 挂起 deadline 过点回默认。
        if seq.get("resident") and not seq.get("copy_done"):
            self.metrics["copy_done_fallback"] += 1
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
        if self.seq.get("boundary_wait"):
            # 边界等待期（2026-11-27 离场跳过 bug 修复伴随修正）：画面仍是
            # 上一步素材（主线在播，离场只是排队）——离场步的纯画面窗口尚未
            # 真实开始，不延迟音频推送（否则长主线剩余整轮的常规语音被静默）
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
            # A1/A3（2026-11-27）：档位频次观测（与 counters 画面上下文分账）
            "tier_counts": {r.rule_id: self._tier_count(r, self.clock())
                            for r in self.enabled_rules},
            "tier_events_depth": {k: len(v) for k, v in self.tier_events.items()},
            "hold_remaining": max(0.0, (seq["hold_until"] - self.clock())) if seq else 0.0,
            "hold_for_song": bool(seq.get("hold_for_song")) if seq else False,
            "song_suspended": (dict(self.song_suspended) if self.song_suspended else None),
            # C1（2026-11-27 双栈批次）：双锚分立观测（resident/song 各自槽位）
            "suspend_resident": (dict(self._suspend_resident)
                                 if self._suspend_resident else None),
            "suspend_song": (dict(self._suspend_song)
                             if self._suspend_song else None),
            "degrade_total": self.degrade_total,
            # A3（2026-11-27）：分机制降级遥测 + 机制事件观测（回归锚点）
            "degrade_breakdown": dict(self.degrade_breakdown),
            "metrics": dict(self.metrics),
            "enabled_rules": [r.rule_id for r in self.enabled_rules],
            "pending_requeue": bool(seq.get("pending_requeue")) if seq else False,
            "step_remaining_s": (round(seq["current_deadline"] - self.clock(), 1)
                                 if seq and seq.get("current_deadline") is not None else None),
            "mute_window": self.mute_window(),
            "pending_depth": len(self.pending_queue),  # G3：queue 多槽排队深度观测
            # B3（2026-11-27 显式状态转移批次）：具名状态/步相位观测——
            # 状态条与回归测试可直接断言（隐式组合状态空间显式化）
            "named_state": self._named_state(),
            "seq_phase": self._seq_phase(),
            # 边界等待观测（2026-11-27 离场跳过 bug 修复）：segment_end 排队
            # 切换等待实际执行中（离场步计时未起算）
            "boundary_wait": bool(seq.get("boundary_wait")) if seq else False,
            # Gap A（2026-11-24 批次）：延迟抢占请求观测（rule_id 或 None）
            "preempt_request": ((seq.get("preempt_request") or {}).get("rule_id")
                                if seq else None),
        }


# B3 装载期穷尽检查（2026-11-27 显式状态转移批次）：模块导入即校验触发决策表
# 覆盖「具名状态 × trigger」全域——半张表拒绝启动（P0-1 静态保证）。
ChoreoEngine._verify_transition_table()


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
        # C5（2026-11-27 暂存换装）：序列执行中收到的换装请求暂存位——
        # 下个回默认点由 _flush_pending_config 自动应用
        self._pending_cfg_node = None
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

    def on_dynamic_text(self, text: str, completion_text: str = "") -> bool:
        """d4（2026-11-05 编排优化批次）：动态文案源直连——动态文案内容整段作为
        match_value 触发 dynamic_text 规则（不走关键词匹配；match 可空=任意源
        事件命中，非空=content 子串过滤）。task_scheduler 格式化路径与
        keyword_hub copywriting 源共用本入口。

        completion_text（2026-11-25 画面同步修复）：T3「关键词|文案」路径传
        文案全文——触发匹配用关键词、完成对齐用全文（resident_copy 注入）。"""
        try:
            with self._lock:
                decision = self.engine.decide_trigger(
                    "dynamic_text", str(text or ""), {},
                    completion_text=str(completion_text or ""))
                self._dispatch(decision["commands"])
            return bool(decision["accepted"])
        except Exception as e:
            logger.error(f"[编排] on_dynamic_text 异常（忽略）：problem=编排判定异常；"
                         f"cause={type(e).__name__}: {e}；fix=已降级忽略")
            return False

    def on_song(self, song_name: str, total_s: Optional[float] = None) -> bool:
        """SongMode 命中点调用。True=画面序列接管；点歌音频走 SongMode 原路（WP-10）。

        total_s（B 预武装，2026-11-26 画面来回跳根修）：翻唱成品探测时长
        （人声轨=歌曲时间轴全长）——接管序列 hold 保险丝下限抬升到
        「接管锚+歌曲总时长」。None=时长不可测，保险丝原语义。"""
        try:
            with self._lock:
                decision = self.engine.decide_trigger(
                    "song", str(song_name or ""), {"song_total_s": total_s})
                self._dispatch(decision["commands"])
            return bool(decision["accepted"])
        except Exception as e:
            logger.error(f"[编排] on_song 异常（忽略）：problem=编排判定异常；cause={type(e).__name__}: {e}；fix=已降级忽略")
            return False

    def set_song_alive_provider(self, provider) -> None:
        """注册音频侧歌曲活跃查询（A 收尾防线数据源，2026-11-26；PlaybackMixin
        song_hold_alive 注入）。幂等覆盖——choreographer 重建后随下一次消费
        自愈；provider 异常由引擎侧规整兜底（静默跳过）。"""
        try:
            self.engine.song_alive_provider = provider
        except Exception:
            pass

    def is_active_step(self, step_id: str) -> bool:
        """B2 第二批出队因果门查询（2026-11-27，playback_manager 出队编排
        条目时调用）：step_id 是否属于当前活动序列。

        fail-open：查询异常=True（音频照播——宁可口型错配不吞音频，与既有
        「人声照唱」降级语义对称）。"""
        try:
            with self._lock:
                return self.engine.is_active_step(step_id)
        except Exception:
            return True

    def choreo_token_alive(self, token: str) -> bool:
        """C3 preempt 令牌活性查询门面（2026-11-27，playback_manager 出队
        死令牌条目丢弃用）。fail-open=True（查询异常=照播）。"""
        try:
            with self._lock:
                return self.engine.preempt_token_alive(token)
        except Exception:
            return True

    def on_entrance(self, data: dict) -> bool:
        """入场事件订阅点（T4：event_handler.entrance_handle 调用，感谢链同源）。

        True=画面序列启动/排队受理；入场主流程（积分/答谢）不受影响（正交）。
        username 清洗三件套（Eng E4/S-1：去控制字符+截断 24 字符——不可信外部
        输入直通 TTS/字幕变量，清洗在编排侧副本上做，原 data 不动）。
        权重=系统优先级（filter.priority_mapping，2026-11-06 三强制拆除批次）；
        打断模式=规则配置（standard/queue 均合法）。
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
                          step_id: Optional[str] = None,
                          group_pending: int = 0,
                          copy_groups_pending: int = 0) -> None:
        """播放完成回调（playback_manager EDTalk push 路径每条目完成处，asyncio 线程）。

        O6（2026-10-28 消费批次）：回调携带 choreo_step_id（非空）时按实例序号
        精确匹配，同内容重复入队不再误配。group_pending（2026-11-25 画面同步
        修复）：回执条目 split_group 组两队列剩余段数（文案完成判定用）。
        copy_groups_pending（B1，2026-11-27）：其它文案组数（结构化终结全局条件）。
        """
        try:
            with self._lock:
                commands = self.engine.on_audio_complete(qtype, str(content or ""),
                                                         self._clock(), step_id,
                                                         group_pending,
                                                         copy_groups_pending)
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
        # 2026-11-16 动态文案插播&切回批次新增枚举
        "rejected": "song 在播：触发已放弃（不排队不挂起，保持点歌画面）",
        "resident_updated": "挂起播报序列已更新为新一轮文案，打断者完成后切回",
        "song_queued": "点歌请求已入歌曲队列承接（画面保持当前序列）",
        "preempt_pending": "已登记延迟抢占：当前主线为无缝切换保护，主线播完后切离场转本规则",
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
        # 2026-11-16 动态文案插播&切回批次新增枚举
        "rejected": "song 播放期间编排画面不接管，无需处理（音频语义照常走原生链）",
        "resident_updated": "观察状态条，打断者播完后自动切回新文案画面",
        "song_queued": "观察歌曲队列，歌曲按序播放",
        "preempt_pending": "观察状态条 preempt_request，主线播完后自动执行抢占（无需处理）",
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
        覆盖段（缺口修复 2026-11-25）：情绪档 segment_overrides 换段后
        lip_flag/duration_s 曾恒取基础段属性——覆盖段开关不同则口型门控按
        错误素材标志下发（反向=可见缺陷不张嘴）。解析写入 tier.override_props，
        物化点经 _step_material_props 消费。
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

            # 覆盖段属性解析（缺口修复）：按覆盖段自身 sidecar 回填
            for tier in rule.tiers:
                for _name, _seg in (tier.segment_overrides or {}).items():
                    if not _seg:
                        continue
                    try:
                        meta = get_material_meta(str(_seg))
                    except Exception as e:
                        logger.warning(f"[编排] 覆盖段元数据读取失败 seg={_seg}: {e}")
                        continue
                    _dur = meta.get("duration_s")
                    tier.override_props[str(_name)] = {
                        "lip_flag": bool(meta.get("lip_flag", True)),
                        "duration_s": (float(_dur)
                                       if _dur is not None and float(_dur) > 0
                                       else None),
                    }

            def _fmt(step: Optional[StepCfg]) -> str:
                if step is None:
                    return "（未设）"
                return (f"{step.segment}({step.duration_s}s/lip={step.lip_flag}"
                        f"{'/hold_for_song' if step.hold_for_song else ''})")
            logger.info(
                f"[编排] new 规则 {rule.rule_id} 素材属性已解析："
                f"enter={_fmt(rule.enter)} main={_fmt(rule.main)} exit={_fmt(rule.exit)}")
            _ov = {t.min_count: sorted(t.override_props)
                   for t in rule.tiers if t.override_props}
            if _ov:
                logger.info(f"[编排] new 规则 {rule.rule_id} 覆盖段属性已解析：{_ov}")

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
                # C5 暂存换装（2026-11-27）：序列执行中不再 409 拒绝——接受
                # 写入暂存，下个回默认点自动应用（用户不再需要「exit 完成后
                # 重试」；api.py 端点对 staged=True 应照常持久化——配置稍后
                # 必然生效）。冲刷点=_worker_main/_handle_event 事件处理后
                # DEFAULT 态检查（不打断挂起恢复/新序列接续）。
                self._pending_cfg_node = cfg_node
                logger.info("[编排] 序列执行中：换装请求已暂存（C5），"
                            "下个回默认点自动应用")
                return {"applied": False, "status": 202, "enabled_rules": [],
                        "rejected": rejected, "missing_all": [],
                        "staged": True,
                        "message": "编排序列执行中：换装已暂存，当前序列结束"
                                   "回默认后自动应用"}
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
            # A1（2026-11-27）：档位频次栈同名保留（档位语义=观众刷屏强度，
            # 换装不重置观众行为历史；失效键剔除）
            new_engine.tier_events = {k: list(v) for k, v in
                                      self.engine.tier_events.items()
                                      if k in enabled_ids}
            new_engine.key_last = {k: v for k, v in self.engine.key_last.items()
                                   if k in enabled_ids}
            new_engine.degrade_total = self.engine.degrade_total
            # A3（2026-11-27）：分机制遥测跨换装延续（累计口径）
            new_engine.degrade_breakdown = dict(self.engine.degrade_breakdown)
            new_engine.metrics = dict(self.engine.metrics)
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
                    self._flush_pending_config()
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
            elif kind == "switch_executed":
                _, step_id = event
                commands = self.engine.on_switch_executed(step_id, self._clock())
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
        self._flush_pending_config()

    def _flush_pending_config(self) -> None:
        """C5 暂存换装冲刷（2026-11-27）：暂存配置在序列收尾回默认后自动应用
        （worker 单线程事件处理后调用；DEFAULT 态才冲刷——挂起恢复/新序列
        接续不打断）。冲刷即消费（清除暂存后重走 apply_config 完整校验链，
        EDTalk 失联走既有 503 保存不生效语义，不回滚暂存）。"""
        # 读-清原子化（2026-11-27 审查修复 #4）：暂存读取与清除必须与写入方
        # （API 线程 apply_config 持锁暂存）同锁——锁外读清存在「旧值覆写新值」
        # 竞态（worker 读到旧暂存后 API 线程写入新配置，worker 清除丢新配置）。
        try:
            with self._lock:
                if self._pending_cfg_node is None:
                    return
                if not (self.engine.state == "default"
                        and self.engine.seq is None):
                    return
                node = self._pending_cfg_node
                self._pending_cfg_node = None
        except Exception:
            return
        logger.info("[编排] 序列已收尾回默认：自动应用暂存换装（C5）")
        try:
            result = self.apply_config(node)
            logger.info(f"[编排] 暂存换装自动应用结果：applied={result.get('applied')} "
                        f"status={result.get('status')} "
                        f"enabled={len(result.get('enabled_rules') or [])}")
        except Exception as e:
            logger.error(f"[编排] 暂存换装自动应用失败（已丢弃暂存，可重试应用）："
                         f"problem={type(e).__name__}: {e}；"
                         "fix=管理页重新点「应用」")

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

    def _wait_boundary_executed(self, step_id: str, segment: str) -> None:
        """segment_end 排队切换的边界执行轮询（2026-11-27 离场跳过 bug 修复）。

        轮询 EDTalk current_segment 达成目标段=切换真实显示，回填
        switch_executed 事件（引擎侧离场步计时从实际显示起点起算）。步失活
        （序列推进/打断/收尾）即退出——不投递过期事件。上限窗口远大于引擎
        侧临时 deadline（超时=引擎看门狗先到，本线程随步失活退出）。
        失联（get_status 异常）重试，引擎临时上限兜底推进。"""
        _deadline = time.time() + 3600.0
        while time.time() < _deadline:
            with self._lock:
                try:
                    active = self.engine.is_active_step(step_id)
                except Exception:
                    active = True  # 查询异常 fail-open（B2 出队门同构）
            if not active:
                return
            try:
                st = self.edtalk_client.get_status() or {}
                # 同素材复用误命中边角（2026-11-27 审查修复 #5 ②声明）：若步失活
                # 后另一序列恰好切到同段（复用素材），轮询在「失活检查先于状态
                # 查询」的窗口内可能误投过期 switch_executed——引擎侧
                # on_switch_executed 按 step_id 精确匹配拒收（L1594），无害。
                if st.get("current_segment") == segment:
                    self._events.put(("switch_executed", step_id))
                    return
            except Exception:
                pass  # 失联重试（引擎临时上限兜底）
            time.sleep(1.0)

    def _execute(self, commands: List) -> None:
        """命令执行（工作线程；HTTP 调用与音频入队，结果回填事件队列）。"""
        for cmd in commands:
            try:
                if isinstance(cmd, LogCmd):
                    getattr(logger, cmd.level, logger.info)(cmd.msg)
                elif isinstance(cmd, SwitchSegment):
                    result = self.edtalk_client.switch_segment(
                        cmd.segment, at=cmd.at, loop_mode=cmd.loop_mode)
                    ok = bool(result.get("ok", True)) if isinstance(result, dict) else False
                    self._events.put(("switch_result", cmd.step_id, ok))
                    # 边界执行轮询（2026-11-27 离场跳过 bug 修复）：segment_end
                    # 受理≠执行——实际显示要等当前素材自然播完的边界。轮询
                    # current_segment 达成后回填 switch_executed（引擎侧离场步
                    # 计时锚定实际显示起点；临时上限兜底=看门狗）。步失活
                    # （序列推进/被打断/收尾）即停止，不投递过期事件。
                    # 仅 exit 步起线程（2026-11-27 审查修复 #5）：轮询消费面
                    # 唯一=离场步计时锚定；enter/main 的 segment_end（自然
                    # 边界衔接）无消费方，起线程纯浪费。
                    if ok and cmd.at == "segment_end" and cmd.step_name == "exit":
                        threading.Thread(
                            target=self._wait_boundary_executed,
                            args=(cmd.step_id, cmd.segment),
                            daemon=True, name="choreo-boundary").start()
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
