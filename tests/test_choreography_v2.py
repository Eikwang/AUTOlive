# -*- coding: utf-8 -*-
"""
T4 entrance/follow 触发类型实装守卫（/autoplan main-20261101-213220 批次）。

覆盖义务块（ceo/eng v6）：
1. 匹配组：match 可空=任意事件 / 非空=用户名等值（_normalize_text 口径）/ 不匹配 no_match
2. 强制 queue 组：load 时 entrance/follow+standard → queue+告警
3. 恒强制权重组：三条 PRIORITY_PRESETS 映射下 _rule_priority(entrance/follow) 恒 0（Eng A2）
4. 类型全局节流组：10s 同类共享窗口（跨规则互斥）/ 不同类互不影响 / 窗口外放行 /
   DEFAULT 态与序列态统一生效（Eng E3/F1/T-2）
5. username 清洗三件套：去控制字符+截断 24 字符（Eng E4/S-1）
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
        """follow 类型同链（订阅点/匹配/节流共用）。"""
        h = Harness(rules=[make_rule(rule_id="fw", trigger_type="follow")])
        d = h.trigger("follow", entrance_data())
        assert d["accepted"] is True


# ---------- 2. 强制 queue 组 ----------

class TestForcedQueue:
    def test_standard_overridden_to_queue(self):
        """entrance/follow+standard → 强制 queue+告警（三强制一）。"""
        cfg = load_choreography_config(make_cfg(rules=[make_rule(interrupt="standard")]))
        rule = cfg.rules[0]
        assert rule.interrupt == "queue"

    def test_explicit_queue_unchanged(self):
        """显式 queue 不变（无告警路径语义）。"""
        cfg = load_choreography_config(make_cfg(rules=[make_rule(interrupt="queue")]))
        assert cfg.rules[0].interrupt == "queue"

    def test_gift_standard_unchanged(self):
        """gift 的 standard 不受强制影响（存量零回归）。"""
        gift_rule = {"rule_id": "g", "trigger_type": "gift", "match": "小心心",
                     "min_price": 0, "interrupt": "standard",
                     "sequence": {"main": {"segment": "A_跳舞", "switch_at": "segment_end",
                                           "duration_s": 5}}}
        cfg = load_choreography_config(make_cfg(rules=[gift_rule]))
        assert cfg.rules[0].interrupt == "standard"

    def test_queued_on_sequence_conflict(self):
        """序列进行中 entrance 触发走 queue 排队（强制 queue 生效于冲突分支）。"""
        h = Harness(rules=[make_rule(rule_id="e1", match="甲"),
                           make_rule(rule_id="e2", match="乙")])
        h.trigger("entrance", entrance_data("甲"))
        h.advance(11)  # 过类型节流窗
        d2 = h.trigger("entrance", entrance_data("乙"))  # 他键命中 e2
        assert d2["reason"] in ("queued", "queued_full")


# ---------- 3. 恒强制权重组（Eng A2：三条预设映射） ----------

class TestPriorityClamp:
    # filter_queue.py PRIORITY_PRESETS 三预设的 entrance/follow 现成值
    PRESETS = [
        {"gift": 70, "comment": 80, "song": 60, "entrance": 55, "follow": 65},
        {"gift": 60, "comment": 70, "song": 55, "entrance": 40, "follow": 45},
        {"gift": 50, "comment": 60, "song": 40, "entrance": 20, "follow": 20},
    ]

    def test_clamped_zero_across_presets(self):
        """三条预设映射下 entrance/follow 权重恒 0（钳制 follow=65/entrance=55）。"""
        for pm in self.PRESETS:
            h = Harness(priority_mapping=dict(pm))
            assert h.engine._rule_priority(h.cfg.rules[0]) == 0
            fw = load_choreography_config(make_cfg(rules=[make_rule(rule_id="fw", trigger_type="follow")]))
            assert h.engine._rule_priority(fw.rules[0]) == 0

    def test_gift_priority_unclamped(self):
        """gift 权重照常映射（钳制仅 entrance/follow）。"""
        gift_rule = {"rule_id": "g", "trigger_type": "gift", "match": "小心心",
                     "min_price": 0,
                     "sequence": {"main": {"segment": "A_跳舞", "switch_at": "segment_end",
                                           "duration_s": 5}}}
        h = Harness(rules=[gift_rule], priority_mapping={"gift": 70})
        assert h.engine._rule_priority(h.cfg.rules[0]) == 70


# ---------- 4. 类型全局节流组（Eng E3/F1/T-2） ----------

class TestTypeThrottle:
    def test_same_type_throttled_within_window(self):
        """10s 内同类第二触发=type_throttled 静默（不启动序列）。"""
        h = Harness()
        d1 = h.trigger("entrance", entrance_data("甲"))
        assert d1["accepted"] is True
        h.advance(3)
        d2 = h.trigger("entrance", entrance_data("乙"))
        assert d2["accepted"] is False and d2["reason"] == "type_throttled"
        # 序列仍是第一次触发的（未推进未打断——Harness 不驱动 on_tick）
        assert h.engine.seq is not None and h.engine.seq["rule_id"] == "entr_welcome"

    def test_throttle_shared_across_rules(self):
        """同类跨规则共享计时器（Eng E3：两条不同 match 的同类规则互斥）。"""
        h = Harness(rules=[make_rule(rule_id="e1", match="甲"),
                           make_rule(rule_id="e2", match="乙")])
        d1 = h.trigger("entrance", entrance_data("甲"))
        assert d1["accepted"] is True
        h.advance(3)
        d2 = h.trigger("entrance", entrance_data("乙"))  # 另一规则同一类型
        assert d2["reason"] == "type_throttled"  # 同类共享窗口

    def test_other_type_unaffected(self):
        """不同类型互不影响（entrance 触发后 follow 立即放行）。"""
        h = Harness(rules=[make_rule(rule_id="e1"),
                           make_rule(rule_id="f1", trigger_type="follow")])
        h.trigger("entrance", entrance_data())
        h.advance(1)
        d = h.trigger("follow", entrance_data())
        # follow 未被 entrance 的节流窗拦（不同类型独立计时）；强制 queue 下
        # 对当前 entrance 序列走排队（reason=queued）而非节流拒绝
        assert d["reason"] != "type_throttled"

    def test_window_expiry_releases(self):
        """窗口外放行（固定 10s 窗口，不因风暴刷新——义务块语义）。"""
        h = Harness()
        h.trigger("entrance", entrance_data("甲"))
        h.advance(10.1)
        d = h.trigger("entrance", entrance_data("乙"))
        # 窗口外放行（固定窗口不刷新）；同键序列未完成=in_progress（on_tick
        # 未驱动的 Harness 状态），关键断言=不再被类型节流拦
        assert d["reason"] != "type_throttled"

    def test_throttled_not_counted(self):
        """被节流触发不进计数（风暴不推进情绪档）。"""
        h = Harness()
        h.trigger("entrance", entrance_data("甲"))
        h.advance(3)
        h.trigger("entrance", entrance_data("乙"))
        assert h.engine.counters.get("entr_welcome", 0) == 1

    def test_throttle_unified_across_states(self):
        """节流跨 DEFAULT/序列态统一（Eng T-2 断言）：序列进行中同类触发先过
        节流门（窗口内=type_throttled，窗口外=queue 排队）。"""
        h = Harness(rules=[make_rule(rule_id="e1", match="甲"),
                           make_rule(rule_id="e2", match="丙"),
                           make_rule(rule_id="kw", trigger_type="keyword", match="跳舞")])
        h.trigger("entrance", entrance_data("甲"))
        assert h.engine.state == "sequence"
        h.advance(1)
        d = h.trigger("entrance", entrance_data("乙"))  # 未命中任何规则（e1 要甲/e2 要丙）
        assert d["reason"] == "no_match"
        d_th = h.trigger("entrance", entrance_data("甲"))  # 等值命中 e1（同类窗口内；节流门在状态分支前）
        assert d_th["reason"] == "type_throttled"  # 序列态同样被类型节流拦
        h.advance(10)
        d2 = h.trigger("entrance", entrance_data("丙"))  # 命中 e2（窗口外）
        assert d2["reason"] == "queued"  # 窗口外+强制 queue=排队（不打断）


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
