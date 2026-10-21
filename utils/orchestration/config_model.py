# -*- coding: utf-8 -*-
"""
动作编排配置模型（/autoplan 2026-10-21 批次 T4）

choreography 节的解析 + 结构校验（装载期 fail-fast，逐规则拒绝）+ 第二段
段名存在性校验（EDTalk 就绪后，失败规则 disabled + 资产清单缺口报告）。

权威结构（CEO 审查 R2 C2-1 修正后 = 扁平）见 config.example.json choreography 节。
校验规则表（义务 CL-5/WP-8）：必填字段 / trigger_type 值域 / switch_at 值域 /
audio.mode 值域 / duration_s（无 audio 步骤必须 >0）/ interrupt 值域 /
emotion_tiers 递增 / min_retrigger_s / wait_previous 告警（none 步）/ wait_previous 首步置位。
"""
from dataclasses import dataclass, field
from typing import Optional

from utils.my_log import logger

VALID_TRIGGER_TYPES = ("gift", "keyword", "song")  # entrance/follow 预留，P1 不订阅
VALID_SWITCH_AT = ("immediate", "segment_end")
VALID_AUDIO_MODES = ("none", "tts", "file")
VALID_INTERRUPT = ("standard",)


@dataclass
class StepCfg:
    """序列步骤：一段画面 + 音轨声明。"""
    name: str                      # enter | main | exit
    segment: str
    switch_at: str = "segment_end"
    duration_s: Optional[float] = None
    audio_mode: str = "none"
    audio_path: str = ""


@dataclass
class TierCfg:
    """情绪分级档：连续触发次数上限 → 素材组覆盖。"""
    max_count: int
    mood: str = ""
    segment_overrides: dict = field(default_factory=dict)  # {enter|main|exit: segment}


@dataclass
class RuleCfg:
    """触发规则：触发源 + 三步序列 + 情绪分级。"""
    rule_id: str
    trigger_type: str
    match: str
    min_price: float = 0.0
    enter: StepCfg = None
    main: StepCfg = None
    exit: StepCfg = None
    tiers: list = field(default_factory=list)
    interrupt: str = "standard"

    def has_tts_audio(self) -> bool:
        """序列是否含 tts 音轨（接管判定：抑制原生答谢的依据）。"""
        return any(self.name_step(n).audio_mode == "tts" for n in ("enter", "main", "exit"))

    def name_step(self, name: str) -> StepCfg:
        return {"enter": self.enter, "main": self.main, "exit": self.exit}[name]


@dataclass
class ChoreographyConfig:
    """choreography 节全量配置（已过结构校验）。"""
    enable: bool = False
    default_mode: str = "rotate"            # single | rotate | random
    default_segments: list = field(default_factory=list)
    min_retrigger_s: float = 2.0
    min_sequence_hold_s: float = 5.0
    step_timeout_s: float = 60.0
    rules: list = field(default_factory=list)  # list[RuleCfg]，已剔除被拒规则

    def rule_by_id(self, rule_id: str) -> Optional[RuleCfg]:
        for r in self.rules:
            if r.rule_id == rule_id:
                return r
        return None


def _reject(rule_id: str, reason: str, rejections: list) -> None:
    """三段式逐规则拒绝日志（仓库既定口径 problem=/cause=/fix=）。"""
    logger.error(
        f"编排规则 [{rule_id}] 被拒绝：problem=配置未通过结构校验；cause={reason}；"
        f"fix=修正 config.json choreography.rules 中该规则后重启"
    )
    rejections.append({"rule_id": rule_id, "reason": reason})


def _parse_step(rule_id: str, name: str, raw, rejections: list) -> Optional[StepCfg]:
    """解析单步并套用校验表（switch_at 值域 / audio.mode 值域 / duration_s）。"""
    if not isinstance(raw, dict) or not str(raw.get("segment", "")).strip():
        _reject(rule_id, f"sequence.{name} 缺失或 segment 为空", rejections)
        return None
    switch_at = raw.get("switch_at", "segment_end")
    if switch_at not in VALID_SWITCH_AT:
        _reject(rule_id, f"sequence.{name}.switch_at 值域外（{switch_at!r}，仅 immediate|segment_end）", rejections)
        return None
    audio = raw.get("audio") or {}
    audio_mode = (audio.get("mode") or "none") if isinstance(audio, dict) else "none"
    if audio_mode not in VALID_AUDIO_MODES:
        _reject(rule_id, f"sequence.{name}.audio.mode 值域外（{audio_mode!r}，仅 none|tts|file）", rejections)
        return None
    audio_path = str(audio.get("path", "") or "") if isinstance(audio, dict) else ""
    if audio_mode == "file" and not audio_path.strip():
        _reject(rule_id, f"sequence.{name}.audio.path 缺失（file 模式必填）", rejections)
        return None
    duration_s = raw.get("duration_s")
    if duration_s is not None:
        try:
            duration_s = float(duration_s)
        except (TypeError, ValueError):
            _reject(rule_id, f"sequence.{name}.duration_s 非数值（{duration_s!r}）", rejections)
            return None
    if audio_mode == "none" and (duration_s is None or duration_s <= 0):
        # FB-2：无 audio 步骤必须 duration_s>0（拦截单槽 pending 静默跳段）
        _reject(rule_id, f"sequence.{name} 无音轨但 duration_s 缺失或 ≤0（静默跳段防护）", rejections)
        return None
    return StepCfg(name=name, segment=str(raw["segment"]).strip(), switch_at=switch_at,
                   duration_s=duration_s, audio_mode=audio_mode, audio_path=audio_path)


def load_choreography_config(config) -> ChoreographyConfig:
    """从 config 包装器读取 choreography 节，结构校验后返回配置。

    逐规则 fail-fast：被拒规则不进 rules（其余规则照常装载）。
    全节缺失/非 dict → 返回 enable=False 的空配置（关闭=全链零行为变化）。
    """
    node = config.get("choreography")
    if not isinstance(node, dict):
        cfg = ChoreographyConfig(enable=False)
        cfg._rejections = []
        return cfg

    rejections: list = []
    enable = bool(node.get("enable", False))
    default_mode = node.get("default_mode", "rotate")
    if default_mode not in ("single", "rotate", "random"):
        logger.error(
            "choreography.default_mode 值域外；problem=配置未通过结构校验；"
            f"cause={default_mode!r}；fix=改为 single|rotate|random（已回退 rotate 并告警）"
        )
        default_mode = "rotate"
    default_segments = [str(s).strip() for s in (node.get("default_segments") or []) if str(s).strip()]

    try:
        min_retrigger_s = float(node.get("min_retrigger_s", 2) or 2)
        if min_retrigger_s <= 0:
            logger.warning("choreography.min_retrigger_s ≤0 无效，problem=配置告警；cause=须>0；fix=已回退默认 2s")
            min_retrigger_s = 2.0
    except (TypeError, ValueError):
        logger.warning("choreography.min_retrigger_s 非数值，problem=配置告警；cause=类型错误；fix=已回退默认 2s")
        min_retrigger_s = 2.0
    try:
        min_sequence_hold_s = float(node.get("min_sequence_hold_s", 5) or 5)
        if min_sequence_hold_s < 0:
            min_sequence_hold_s = 5.0
    except (TypeError, ValueError):
        min_sequence_hold_s = 5.0
    try:
        step_timeout_s = float(node.get("step_timeout_s", 60) or 60)
        if step_timeout_s <= 0:
            step_timeout_s = 60.0
    except (TypeError, ValueError):
        step_timeout_s = 60.0

    rules: list = []
    for raw in node.get("rules") or []:
        if not isinstance(raw, dict):
            _reject("?", "规则项非对象", rejections)
            continue
        rule_id = str(raw.get("rule_id", "")).strip()
        if not rule_id:
            _reject("?", "rule_id 缺失", rejections)
            continue
        trigger_type = raw.get("trigger_type")
        if trigger_type not in VALID_TRIGGER_TYPES:
            hint = "（entrance/follow 为预留枚举位，P1 不订阅）" if trigger_type in ("entrance", "follow") else ""
            _reject(rule_id, f"trigger_type 值域外：{trigger_type!r}{hint}", rejections)
            continue
        match = str(raw.get("match", "") or "")
        if trigger_type != "song" and not match:
            _reject(rule_id, "match 缺失（gift/keyword 必填；song 可空=任意点歌）", rejections)
            continue
        try:
            min_price = float(raw.get("min_price", 0) or 0)
        except (TypeError, ValueError):
            min_price = 0.0
        seq = raw.get("sequence") or {}
        enter = _parse_step(rule_id, "enter", seq.get("enter"), rejections)
        main = _parse_step(rule_id, "main", seq.get("main"), rejections)
        exit_step = _parse_step(rule_id, "exit", seq.get("exit"), rejections)
        if enter is None or main is None or exit_step is None:
            continue  # 步骤级拒绝已记录
        interrupt = raw.get("interrupt", "standard")
        if interrupt not in VALID_INTERRUPT:
            _reject(rule_id, f"interrupt 值域外：{interrupt!r}（仅 standard）", rejections)
            continue
        tiers: list = []
        tier_error = None
        prev_max = 0
        for t in raw.get("emotion_tiers") or []:
            if not isinstance(t, dict):
                tier_error = "emotion_tiers 项非对象"
                break
            try:
                max_count = int(t.get("max_count", 0))
            except (TypeError, ValueError):
                tier_error = f"emotion_tiers.max_count 非整数：{t.get('max_count')!r}"
                break
            if max_count <= prev_max:
                tier_error = f"emotion_tiers.max_count 非严格递增（{max_count} ≤ {prev_max}）"
                break
            prev_max = max_count
            overrides = t.get("segment_overrides") or {}
            tiers.append(TierCfg(max_count=max_count, mood=str(t.get("mood", "") or ""),
                                 segment_overrides={str(k): str(v).strip()
                                                    for k, v in overrides.items()
                                                    if k in ("enter", "main", "exit") and str(v).strip()}))
        if tier_error:
            _reject(rule_id, tier_error, rejections)
            continue

        # wait_previous 置位/告警义务（WP-8）：当前结构中 wait_previous 由步骤
        # 间时序隐式表达（无音轨步骤按 duration 推进，音轨步骤等完成回调），
        # 独立 wait_previous 键被解析并按矩阵处置：none 步告警、首步置位忽略。
        for name in ("enter", "main", "exit"):
            raw_step = (seq.get(name) or {})
            if isinstance(raw_step, dict) and raw_step.get("wait_previous"):
                if (seq.get(name) or {}).get("audio") in (None, {"mode": "none"}):
                    logger.warning(
                        f"编排规则 [{rule_id}] {name}.wait_previous=true 无效果："
                        f"problem=矩阵退化；cause=none 音轨无音频可等；fix=删除该键或改音轨模式（不拦截）"
                    )

        rules.append(RuleCfg(rule_id=rule_id, trigger_type=trigger_type, match=match,
                             min_price=min_price, enter=enter, main=main, exit=exit_step,
                             tiers=tiers, interrupt=interrupt))

    if rejections:
        logger.warning(
            f"choreography 装载完成：{len(rules)} 条规则生效，{len(rejections)} 条被拒"
            f"（problem=部分规则未启用；cause=结构校验未过；fix=按上方三段式日志逐条修正）"
        )
    cfg = ChoreographyConfig(enable=enable, default_mode=default_mode,
                             default_segments=default_segments,
                             min_retrigger_s=min_retrigger_s,
                             min_sequence_hold_s=min_sequence_hold_s,
                             step_timeout_s=step_timeout_s, rules=rules)
    cfg._rejections = rejections
    return cfg


def validate_rule_segments(rules: list, available_segments: set) -> dict:
    """第二段校验（EDTalk 就绪后）：段名存在性 + 资产清单缺口报告（义务 F2）。

    Returns:
        {"enabled_rules": [rule_id...], "disabled_rules": [(rule_id, [缺失段名...])...],
         "report_lines": [三段式日志已打出的行], "missing_all": set(全部缺失段名)}
    """
    enabled, disabled, missing_all = [], [], set()
    for rule in rules:
        needed = set()
        for name in ("enter", "main", "exit"):
            step = rule.name_step(name)
            needed.add(step.segment)
        for tier in rule.tiers:
            needed.update(tier.segment_overrides.values())
        missing = sorted(s for s in needed if s not in available_segments)
        if missing:
            disabled.append((rule.rule_id, missing))
            missing_all.update(missing)
            logger.error(
                f"编排规则 [{rule.rule_id}] 段名校验失败已禁用：problem=引用的素材段不存在；"
                f"cause=缺失 {missing}（可用段见 EDTalk /status available_segments）；"
                f"fix=制作对应素材段或修正规则段名（资产清单缺口，义务 F2）"
            )
        else:
            enabled.append(rule.rule_id)
    return {"enabled_rules": enabled, "disabled_rules": disabled,
            "missing_all": sorted(missing_all)}
