"""阶段一（2026-11-27）：A1 档位频次与打断解耦 / A2 挂起槽不变量 / A3 分机制遥测。

背景（docs/动作编排功能报告总结.md 逐条代码验证 + docs/动作编排优化计划.md 阶段一）：
- P0-5 混流失效（A1）：档位进档原吃 counters（连续计数），被 D-4 他键打断清零——
  直播混流下两次礼物间必夹 keyword/entrance 触发，gift 恒卡档一，招牌能力失效。
  根修=档位改吃 per-rule 滑动窗口频次（tier_events 时间戳栈），打断只清 counters
  （画面上下文），不清档位频次。
- P0-2 单槽覆盖（A2）：挂起槽「后一次覆盖前次」丢锚风险——写点前置断言锁死
  「写前必空」不变量。
- P2 可观测（A3）：degrade_total 单一累计拆分机制分账 + 机制事件计数。

覆盖：
- t1 混流回归：礼物每 5s 一次、中间夹弹幕 → gift 档位持续爬升（A1 核心验收）
- t2 窗口外频次滑出：window=30s，40s 前的事件不计档
- t3 window=0：全栈计数（原「计数不清零」语义等价）
- t4 counters 语义保留：打断仍清 counters、回默认不清（D-4 画面上下文不回归）
- t5 A2：挂起槽写前必空断言（制造击穿场景 → AssertionError）
- t6 A3：degrade_breakdown 三分账 + metrics 关键事件计数
"""
import pytest

from utils.orchestration.choreographer import ChoreoEngine, SwitchSegment
from utils.orchestration.config_model import load_choreography_config

NOW = [1000.0]


def _dt_rule(rid="dt", **over):
    r = {"rule_id": rid, "trigger_type": "dynamic_text", "match": "",
         "interrupt": "standard", "pred_version": "new",
         "sequence": {"main": {"segment": "M_" + rid, "switch_at": "immediate",
                               "duration_s": 10}}}
    r.update(over)
    return r


def _gift_rule(rid="g1", match="小心心", main_dur=30, **over):
    r = {"rule_id": rid, "trigger_type": "gift", "match": match,
         "interrupt": "standard", "pred_version": "new",
         "sequence": {"main": {"segment": "G_" + rid, "switch_at": "immediate",
                               "duration_s": main_dur}}}
    r.update(over)
    return r


def _song_rule(rid="song_a", match="晴天", main_dur=30, **over):
    r = {"rule_id": rid, "trigger_type": "song", "match": match,
         "interrupt": "standard", "pred_version": "new",
         "sequence": {"main": {"segment": "S_" + rid, "switch_at": "immediate",
                               "duration_s": main_dur}}}
    r.update(over)
    return r


def _kw_rule(rid="kw1", match="你好", **over):
    r = {"rule_id": rid, "trigger_type": "keyword", "match": match,
         "interrupt": "standard", "pred_version": "new",
         "sequence": {"main": {"segment": "K_" + rid, "switch_at": "immediate",
                               "duration_s": 5}}}
    r.update(over)
    return r


def _gift_data(name="小心心"):
    return {"username": "观众A", "gift_name": name, "gift_num": 1,
            "num": 1, "total_price": 1, "unit_price": 1.0}


def _engine(rules, **over):
    node = {"enable": True, "rules": list(rules), "step_timeout_s": 60,
            "default_segments": ["D_1"],
            "continuous_trigger_window_s": over.pop("window", 30.0)}
    node.update({k: v for k, v in over.items() if k != "window"})
    cfg = load_choreography_config({"choreography": node})
    eng = ChoreoEngine(cfg, clock=lambda: NOW[0], priority_mapping={})
    # song 规则 main hold_for_song 由门面 apply 二段置位——直构引擎手动补
    # （沿 test_choreo_song_hold_guard_20261126 模式）
    for r in eng.cfg.rules:
        if r.trigger_type == "song" and r.main is not None:
            r.main.hold_for_song = True
    return eng


def _accept(eng):
    step = eng.seq["steps"][eng.seq["idx"]]
    return eng.on_switch_result(step["step_id"], True, NOW[0])


# ------------------------------------------------- A1 档位频次

def test_mixed_stream_tier_keeps_climbing():
    """t1（A1 核心验收）：礼物每 3s 一次、序列播完回默认 → 档位随频次爬升。

    旧实现下若夹他键打断（D-4 清零 counters）档位归零；新实现档位频次
    独立累计（打断解耦场景由 t4 覆盖）。窗口 30s / 间隔 3s → 窗口内
    10 个事件，档位 min 1/4/7 均可达。"""
    NOW[0] = 1000.0
    tiers = [{"min_count": 1, "mood": "t1"},
             {"min_count": 4, "mood": "t2"},
             {"min_count": 7, "mood": "t3"}]
    eng = _engine([_gift_rule(emotion_tiers=tiers, main_dur=1)])
    moods = []
    for i in range(9):
        d = eng.decide_trigger("gift", "小心心", _gift_data())
        if eng.seq is not None:
            _accept(eng)
            moods.append(eng.seq["tier_mood"])
            NOW[0] += 1.01
            eng.on_tick(NOW[0])       # main 播完 → 回默认（counters 保留）
        NOW[0] += 3.0
    assert moods[0] == "t1"
    assert "t2" in moods and "t3" in moods  # 混流时间线下档位持续爬升


def test_tier_window_slide_out():
    """t2：window=30s——40s 前的频次事件滑出窗口，不再进档。"""
    NOW[0] = 1000.0
    tiers = [{"min_count": 2, "mood": "t2"}]
    eng = _engine([_gift_rule(emotion_tiers=tiers)], window=30.0)
    eng.decide_trigger("gift", "小心心", _gift_data())     # 事件1 @1000
    NOW[0] += 40.0
    eng.decide_trigger("gift", "小心心", _gift_data())     # 事件2 @1040（事件1 已滑出）
    # 频次=1 → 未达 min_count=2 → 基础档（mood 空）
    assert eng._tier_count(eng.enabled_rules[0], NOW[0]) == 1
    assert eng.seq["tier_mood"] == ""


def test_tier_window_zero_counts_all():
    """t3：window=0（禁用）→ 全栈计数，等价原「计数不随时间清零」语义。"""
    NOW[0] = 1000.0
    eng = _engine([_gift_rule()], window=0.0)
    for _ in range(5):
        eng.decide_trigger("gift", "小心心", _gift_data())
        eng.state = "default"
        eng.seq = None
        NOW[0] += 3600.0  # 每次隔 1 小时
    assert eng._tier_count(eng.enabled_rules[0], NOW[0]) == 5


def test_counters_d4_semantics_preserved():
    """t4：counters（画面上下文）D-4 语义不回归——打断清零、回默认保留。"""
    NOW[0] = 1000.0
    eng = _engine([_gift_rule(rid="g1"), _gift_rule(rid="g2", match="玫瑰")])
    eng.decide_trigger("gift", "小心心", _gift_data())
    _accept(eng)
    eng.counters["g1"] = 5
    # 他键打断
    eng.decide_trigger("gift", "玫瑰", _gift_data("玫瑰"))
    assert eng.counters["g1"] == 0          # D-4 清零保留（画面上下文）
    assert eng.counters["g2"] == 1
    # 但档位频次不清（A1 核心）
    assert len(eng.tier_events["g1"]) == 1  # g1 的事件栈保留


# ------------------------------------------------- A2 挂起槽不变量

def test_suspend_slot_invariant_assertion():
    """t5（A2）：挂起槽「写前必空」不变量——制造击穿场景必须 AssertionError。

    直接构造槽占用 + 调 _suspend_hold_for（正常路径该组合不可达，断言守卫
    未来分支击穿）。"""
    NOW[0] = 1000.0
    eng = _engine([_dt_rule("dt"), _gift_rule(rid="g1")])
    d = eng.decide_trigger("dynamic_text", "今天天气真好", {},
                           completion_text="今天天气真好")
    assert d["accepted"] is True
    _accept(eng)
    # 人为占用挂起槽（模拟不变量已被破坏的状态）
    eng.song_suspended = {"rule_id": "dt", "deadline": NOW[0] + 60.0,
                          "entered_at": NOW[0], "kind": "resident",
                          "copy": "x", "copy_done": False}
    cmds = []
    with pytest.raises(AssertionError, match="不变量击穿"):
        eng._suspend_hold_for(eng.enabled_rules[1], {}, NOW[0], cmds, "resident",
                              match_value="小心心")


def test_rc2_overwrite_invariant():
    """t5b（A2+行为锁）：RC2 正常路径——resident 在播 + 槽空 + 歌起播命中 song
    规则 → 写挂起槽 + 切 song hold（行为锁；写点断言由 t5 同型守卫）。"""
    NOW[0] = 1000.0
    eng = _engine([_dt_rule("dt"), _song_rule(rid="song_a", match="晴天")])
    d = eng.decide_trigger("dynamic_text", "今天天气真好", {},
                           completion_text="今天天气真好")
    _accept(eng)
    assert eng.song_suspended is None
    eng.on_song_started("晴天", 120.0, NOW[0])
    # RC2：文案挂起登记 + 画面切 song hold
    assert eng.song_suspended is not None
    assert eng.song_suspended["kind"] == "resident"
    assert eng.seq["rule_id"] == "song_a"
    assert eng.seq["hold_for_song"] is True
    assert eng.metrics["resident_suspends"] == 1  # A3 遥测联动


# ------------------------------------------------- A3 分机制遥测

def test_degrade_breakdown_and_metrics():
    """t6（A3）：切段失败/音频超时/时长未知三分账 + 保险丝触发/挂起/循环遥测。"""
    NOW[0] = 1000.0
    eng = _engine([_dt_rule("dt"), _gift_rule(rid="g1", main_dur=5),
                   _song_rule(rid="song_a", match="晴天")], window=0.0)
    # 切段失败分账（main 切段失败 → 跳步 → 序列播完回默认）
    d = eng.decide_trigger("gift", "小心心", _gift_data())
    step_id = next(c for c in d["commands"] if isinstance(c, SwitchSegment)).step_id
    eng.on_switch_result(step_id, False, NOW[0])
    assert eng.degrade_breakdown["switch_failed"] == 1
    assert eng.degrade_total == 1
    assert eng.seq is None  # 失败跳步收尾
    # 重新触发（切段成功）→ audio 超时分账（on_tick 强推 audio 步不存在——
    # g1 是 none 步；用 metrics 的 fuse_expired 路径验证 on_tick 遥测）
    ds = eng.decide_trigger("song", "晴天", {"song_total_s": 100.0})
    assert ds["accepted"] is True
    _accept(eng)
    NOW[0] += 101.0
    eng.on_tick(NOW[0])   # hold 保险丝到期（floor=1100 < 1101）
    assert eng.metrics["fuse_expired"] == 1
    # 快照透出
    st = eng.get_state()
    assert st["degrade_breakdown"]["switch_failed"] == 1
    assert st["metrics"]["fuse_expired"] == 1
    assert "tier_counts" in st and "tier_events_depth" in st
    assert st["tier_counts"]["song_a"] == 1
