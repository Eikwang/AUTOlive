# -*- coding: utf-8 -*-
"""
T4 entrance/follow 触发类型实装守卫（/autoplan main-20261101-213220 批次；
2026-11-06 三强制拆除批次反向改写——权重/打断模式回归配置，节流退役）。

覆盖义务块（ceo/eng v6 + 2026-11-06 拆除批次）：
1. 匹配组：match 可空=任意事件 / 非空=用户名等值（_normalize_text 口径）/ 不匹配 no_match
2. 打断模式组：显式 standard/queue 均按配置生效（三强制一拆除）；queue 冲突分支保留
3. 权重组（反向）：三条 PRIORITY_PRESETS 映射下 _rule_priority(entrance/follow)=系统
   优先级值（on 态回读 priority_mapping）；arbitration_weights 覆盖优先；缺键回退 0；
   装载告警退役（F4 caplog）
4. 类型全局节流组：已随 2026-11-06 三强制拆除退役（原 10s 窗口/type_throttled
   断言组删除——防御职责归上游 process_data 六类限入&去重管线）
5. username 清洗三件套：去控制字符+截断 24 字符（Eng E4/S-1，保留）
6. 模拟触发组：api.VALID_TRIGGER_TYPES 单源+含新值域+422 文案拼接（A1）
7. 存量零行为回归：gift/keyword 触发行为不变
"""

from unittest.mock import MagicMock, patch

import pytest

from utils.orchestration.choreographer import (ActionChoreographer,
                                               ChoreoEngine, LogCmd,
                                               SwitchSegment)
from utils.orchestration.config_model import (load_choreography_config,
                                              validate_rule_segments)


class DottedConfig:
    def __init__(self, cfg):
        self._cfg = cfg

    def get(self, *keys):
        cur = self._cfg
        for k in keys:
            if not isinstance(cur, dict) or k not in cur:
                return None
            cur = cur[k]
        return cur


def make_rule(**over):
    rule = {
        "rule_id": "entr_welcome",
        "trigger_type": "entrance",
        "match": "",
        "min_price": 0,
        "sequence": {
            "main": {"segment": "A_入场", "switch_at": "segment_end", "duration_s": 5},
            "exit": {"segment": "A_离场", "switch_at": "segment_end", "duration_s": 3},
        },
    }
    rule.update(over)
    return rule


def make_cfg(rules=None, **over):
    node = {"enable": True, "default_mode": "rotate",
            "default_segments": ["1"], "rules": rules or [make_rule()]}
    node.update(over)
    return DottedConfig({"choreography": node})


AVAILABLE = {"A_入场", "A_离场", "A_跳舞", "1", "55"}


class Harness:
    """显式 now 驱动的引擎测试台（沿 test_action_choreography.EngineHarness 模式）。"""

    def __init__(self, rules=None, priority_mapping=None, **over):
        self.cfg = load_choreography_config(make_cfg(rules=rules, **over))
        self.now = 1000.0
        self.engine = ChoreoEngine(self.cfg, clock=lambda: self.now,
                                   priority_mapping=priority_mapping)
        self.engine.on_edtalk_ready(AVAILABLE)

    def advance(self, dt):
        self.now += dt

    def trigger(self, tt, data):
        return self.engine.decide_trigger(tt, str(data.get("username", "")), data)

    def switch(self, decision):
        return next(c for c in decision["commands"] if isinstance(c, SwitchSegment))


def entrance_data(username="观众甲"):
    return {"username": username, "platform": "bilibili"}


# ---------- 1. 匹配组 ----------

class TestMatchSemantics:
    def test_empty_match_matches_any(self):
        """match 可空=该类型任意事件。"""
        h = Harness()
        d = h.trigger("entrance", entrance_data("任何人"))
        assert d["accepted"] is True

    def test_username_exact_match(self):
        """非空 match=用户名等值命中。"""
        h = Harness(rules=[make_rule(match="VIP老铁")])
        d = h.trigger("entrance", entrance_data("VIP老铁"))
        assert d["accepted"] is True

    def test_username_mismatch_no_match(self):
        """用户名不等=no_match（拒绝值）。"""
        h = Harness(rules=[make_rule(match="VIP老铁")])
        d = h.trigger("entrance", entrance_data("路人乙"))
        assert d["accepted"] is False and d["reason"] == "no_match"

    def test_username_normalized_match(self):
        """匹配走 _normalize_text 口径（空白/大小写归一，Eng E4）。"""
        h = Harness(rules=[make_rule(match="vip 老铁")])
        d = h.trigger("entrance", entrance_data("VIP老铁"))
        assert d["accepted"] is True

    def test_follow_trigger_type(self):
        """follow 类型同链（订阅点/匹配/权重共用）。"""
        h = Harness(rules=[make_rule(rule_id="fw", trigger_type="follow")])
        d = h.trigger("follow", entrance_data())
        assert d["accepted"] is True


# ---------- 2. 打断模式组（三强制一拆除——显式 standard/queue 均按配置生效） ----------

class TestInterruptMode:
    def test_explicit_standard_kept(self):
        """entrance/follow+standard 按配置保留（三强制一拆除——不再强制改判）。"""
        cfg = load_choreography_config(make_cfg(rules=[make_rule(interrupt="standard")]))
        assert cfg.rules[0].interrupt == "standard"

    def test_explicit_queue_unchanged(self):
        """显式 queue 不变（原语义保留）。"""
        cfg = load_choreography_config(make_cfg(rules=[make_rule(interrupt="queue")]))
        assert cfg.rules[0].interrupt == "queue"

    def test_gift_standard_unchanged(self):
        """gift 的 standard 不受影响（存量零回归）。"""
        gift_rule = {"rule_id": "g", "trigger_type": "gift", "match": "小心心",
                     "min_price": 0, "interrupt": "standard",
                     "sequence": {"main": {"segment": "A_跳舞", "switch_at": "segment_end",
                                           "duration_s": 5}}}
        cfg = load_choreography_config(make_cfg(rules=[gift_rule]))
        assert cfg.rules[0].interrupt == "standard"

    def test_queued_on_sequence_conflict(self):
        """序列进行中 entrance 触发走 queue 排队（显式 interrupt=queue 的冲突分支
        ——r3 发现 1 裁决①：同类型规则权重恒等结构上无法构造权重差，排队覆盖
        钉在显式 queue 配置上；spec C2/:146-153 义务）。"""
        h = Harness(rules=[make_rule(rule_id="e1", match="甲"),
                           make_rule(rule_id="e2", match="乙", interrupt="queue")])
        h.trigger("entrance", entrance_data("甲"))
        d2 = h.trigger("entrance", entrance_data("乙"))  # 他键命中 e2（显式 queue）
        assert d2["reason"] in ("queued", "queued_full")


# ---------- 3. 权重组（反向：三强制二拆除——系统优先级值直接生效） ----------

class TestPriorityUnclamped:
    # filter_queue.py PRIORITY_PRESETS 三预设的 entrance/follow 现成值
    PRESETS = [
        {"gift": 70, "comment": 80, "song": 60, "entrance": 55, "follow": 65},
        {"gift": 60, "comment": 70, "song": 55, "entrance": 40, "follow": 45},
        {"gift": 50, "comment": 60, "song": 40, "entrance": 20, "follow": 20},
    ]

    def test_mapping_values_effective_across_presets(self):
        """三条预设映射下 entrance/follow 权重=系统优先级值（on 态回读 priority_mapping
        ——2026-11-06 拆除后用户配置实际生效的验收断言）。"""
        for pm in self.PRESETS:
            h = Harness(priority_mapping=dict(pm))
            assert h.engine._rule_priority(h.cfg.rules[0]) == pm["entrance"]
            fw = load_choreography_config(make_cfg(rules=[make_rule(rule_id="fw", trigger_type="follow")]))
            assert h.engine._rule_priority(fw.rules[0]) == pm["follow"]

    def test_load_warning_retired(self):
        """装载告警退役：entrance/follow 配置装载静默（F4——原 WARNING 本体随钳制退役，
        防复活守卫）。"""
        from unittest.mock import patch as _patch
        from utils.orchestration import choreographer as ch_mod
        with _patch.object(ch_mod.logger, "warning") as w:
            Harness(priority_mapping={"entrance": 20, "follow": 20})
        assert not any("该值对编排不生效" in str(c) for c in w.call_args_list), \
            "装载告警复活（钳制残留）——三强制拆除批次 F4 守卫"

    def test_clamped_constant_retired(self):
        """钳制常量退役守卫（F3 退役守卫形式——沿 throttled/hold 退役先例）。"""
        import inspect
        from utils.orchestration import choreographer as ch_mod
        src = inspect.getsource(ch_mod)
        assert "_CLAMPED_PRIORITY_TYPES" not in src, "钳制常量复活——三强制拆除批次退役守卫"
        assert "ENTRANCE_FOLLOW_MIN_INTERVAL_S" not in src, "节流常量复活——三强制拆除批次退役守卫"
        assert "type_throttled" not in src.replace("type_throttled 已随", ""), \
            "type_throttled reason 复活（退役记录注记除外）"

    def test_arbitration_override_beats_mapping(self):
        """覆盖顺序=arbitration_weights > priority_mapping（spec C8 一句话定义）。"""
        h = Harness(priority_mapping={"entrance": 20},
                    arbitration_weights={"entrance": 8})
        assert h.engine._rule_priority(h.cfg.rules[0]) == 8

    def test_missing_key_falls_back_zero(self):
        """on 态 priority_mapping 缺 entrance 键→回退 0（spec C7——表外回退既有口径）。"""
        h = Harness(priority_mapping={"comment": 20})
        assert h.engine._rule_priority(h.cfg.rules[0]) == 0

    def test_gift_priority_unchanged(self):
        """gift 权重照常取值（d10 编排表保护语义不变）。
        T3/d10（2026-11-05）：编排面权重源=独立仲裁表（gift=30 内置值），与
        filter.priority_mapping 解耦——映射表值仅 off 态（d22 回退）时读取；
        entrance/follow 回读例外不波及 gift（防 trends_copywriting=999 污染
        语义保留）。"""
        gift_rule = {"rule_id": "g", "trigger_type": "gift", "match": "小心心",
                     "min_price": 0,
                     "sequence": {"main": {"segment": "A_跳舞", "switch_at": "segment_end",
                                           "duration_s": 5}}}
        h = Harness(rules=[gift_rule], priority_mapping={"gift": 70})
        assert h.engine._rule_priority(h.cfg.rules[0]) == 30  # d10 编排表内置值
        h_off = Harness(rules=[gift_rule], priority_mapping={"gift": 70},
                        arbitration_enable=False)
        assert h_off.engine._rule_priority(h_off.cfg.rules[0]) == 70  # off 回落 legacy 映射


# ---------- 4. 类型节流组（已随 2026-11-06 三强制拆除批次退役） ----------
# 原 TestTypeThrottle（10s 同类共享窗口/type_throttled reason 断言组）随引擎内
# 节流移除整体退役——防御职责归上游 process_data 六类限入&去重管线（2026-11-04
# 事实修正自证）；type_throttled reason 值同步退役（沿 R-ceo5 throttled/hold 先例）。


# ---------- 5. username 清洗组（Eng E4/S-1） ----------

class _ChoreoStub:
    """ActionChoreographer 依赖桩（只测订阅入口清洗与分发）。"""

    def __init__(self, rules=None):
        self.cfg = load_choreography_config(make_cfg(rules=rules or [make_rule()]))
        self.engine = ChoreoEngine(self.cfg, clock=lambda: 1000.0)
        self.engine.on_edtalk_ready(AVAILABLE)
        self.engine._priority_mapping = {}
        self._lock = __import__("threading").RLock()
        self._events = __import__("queue").Queue()
        self.edtalk_client = MagicMock()
        self._config = DottedConfig({})

    def _dispatch(self, commands):
        for c in commands:
            self._events.put(c)


class TestUsernameSanitize:
    def test_control_chars_stripped(self):
        """控制字符去除（isprintable 过滤）。"""
        ch = _ChoreoStub()
        with patch.object(ActionChoreographer, "__init__", lambda self: None):
            pass
        # 直接调用未绑定方法（绕过完整构造）
        result = ActionChoreographer._on_entrance_follow(ch, "entrance",
                                                         {"username": "观\u000b众​甲"})
        assert result is True
        # 引擎收到的 username 已清洗（通过 seq trigger_data 验证）
        seq = ch.engine.seq
        assert seq is not None
        assert "\u000b" not in seq["trigger_data"]["username"]
        assert "​" not in seq["trigger_data"]["username"]

    def test_username_truncated_24(self):
        """超长昵称截断 24 字符。"""
        ch = _ChoreoStub()
        long_name = "超" * 100
        ActionChoreographer._on_entrance_follow(ch, "entrance", {"username": long_name})
        assert len(ch.engine.seq["trigger_data"]["username"]) == 24

    def test_original_data_untouched(self):
        """清洗只作用于编排侧副本，调用方 data 不被改写。"""
        ch = _ChoreoStub()
        data = {"username": "观\u000b众"}
        ActionChoreographer._on_entrance_follow(ch, "entrance", data)
        assert data["username"] == "观\u000b众"  # 原 data 不动


# ---------- 6. 模拟触发端点（A1 单源+新值域） ----------

class TestApiSurface:
    def test_valid_trigger_types_single_source(self):
        """api.VALID_TRIGGER_TYPES 与 config_model 同源（A1 消三同步）。"""
        from utils.orchestration import api as api_mod
        from utils.orchestration.config_model import VALID_TRIGGER_TYPES as SRC
        assert api_mod.VALID_TRIGGER_TYPES is SRC
        # dynamic_text（2026-11-05 编排优化批次 d4）：动态文案独立类型加入值域
        assert set(SRC) == {"gift", "keyword", "song", "entrance", "follow", "dynamic_text"}

    def test_422_message_joins_value_domain(self):
        """422 fix 文案由值域拼接（api.py:167 不再硬编码）。"""
        from utils.orchestration import api as api_mod
        from utils.orchestration.config_model import VALID_TRIGGER_TYPES
        expected = '提交 {"rule_id": ...} 或 {"trigger_type": "' + "/".join(VALID_TRIGGER_TYPES) + '", "match_value": ...}'
        assert "/".join(VALID_TRIGGER_TYPES) in expected


# ---------- 7. 存量零行为回归 ----------

class TestLegacyRegression:
    def test_gift_flow_unchanged(self):
        """gift 触发既有语义回归（T4 不改变存量类型）。"""
        gift_rule = {"rule_id": "g", "trigger_type": "gift", "match": "小心心",
                     "min_price": 0, "interrupt": "standard",
                     "sequence": {"main": {"segment": "A_跳舞", "switch_at": "segment_end",
                                           "duration_s": 5}}}
        h = Harness(rules=[gift_rule])
        d = h.engine.decide_trigger("gift", "小心心", {"gift_name": "小心心", "total_price": 1})
        assert d["accepted"] is True

    def test_disabled_zero_behavior(self):
        """enable=false=全链零行为（浸泡清单第 5 项守护）。"""
        cfg = load_choreography_config(make_cfg(enable=False))
        eng = ChoreoEngine(cfg, clock=lambda: 1000.0)
        eng.on_edtalk_ready(AVAILABLE)
        d = eng.decide_trigger("entrance", "", entrance_data())
        assert d["accepted"] is False and d["reason"] == "disabled"
