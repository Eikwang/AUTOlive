"""T3 仲裁收敛守卫（2026-11-05 编排优化批次）。

覆盖裁决条款：
- d10：编排面独立仲裁权重表（gift=30>song=20>dynamic_text=15>keyword=10；
  entrance/follow 恒 0；表外回退 0 保守；arbitration_weights 类型键级覆盖；
  dynamic_text 编排权重 ≠ trends_copywriting 生产实值——C1 防污染守卫）
- d7：连续触发窗口消费（窗口内累计/窗外清零回落基础档/滑动锚点 29s 永久连续
  ——d24a 行为边界/0=禁用/总开关 off 禁用）
- e6：三路径锚点刷新（in_progress/queued/interrupted 均刷新 key_last）
- d22：总开关 off=整体回退（权重源回落 legacy _TRIGGER_PRIORITY_KEY 映射）
- d22/uc5b：builtin 999 挂起看门狗（超时告警一次/挂起期，不强制回落）
"""
from unittest.mock import patch

import pytest

from utils.orchestration.choreographer import ChoreoEngine
from utils.orchestration.config_model import RuleCfg, load_choreography_config

NOW = [1000.0]
PM = {"comment": 20, "gift": 20, "song": 10, "trends_copywriting": 999,
      "entrance": 20, "follow": 20}  # entrance/follow 键=2026-11-06 拆除批次（验收载体防空心化）


def _rule(rid, t, match="x", interrupt="standard"):
    return {"rule_id": rid, "trigger_type": t, "match": match, "interrupt": interrupt,
            "sequence": {"main": {"segment": "44", "switch_at": "immediate",
                                  "duration_s": 5}}}


def _engine(rules, pm=None, over=None):
    node = {"enable": True, "rules": list(rules), "step_timeout_s": 60}
    if over:
        node.update(over)
    cfg = load_choreography_config({"choreography": node})
    return ChoreoEngine(cfg, clock=lambda: NOW[0], priority_mapping=pm or dict(PM))


def _hit(eng, t, match="x"):
    # gift 匹配走 data["gift_name"] 全等+min_price 门槛；其余类型走 match_value/username
    data = {"gift_name": match, "total_price": 1} if t == "gift" else {"username": match}
    return eng.decide_trigger(t, match, data)


# ------------------------------------------------- d10 编排仲裁表

def test_d10_branch_ownership():
    """d10 守卫：gift > song > keyword 三分支各一断言（优先级序=方案 §2/§3）。"""
    eng = _engine([_rule("g", "gift"), _rule("s", "song", match=""),
                   _rule("k", "keyword")])
    p_gift = eng._rule_priority(eng.cfg.rule_by_id("g"))
    p_song = eng._rule_priority(eng.cfg.rule_by_id("s"))
    p_kw = eng._rule_priority(eng.cfg.rule_by_id("k"))
    assert (p_gift, p_song, p_kw) == (30, 20, 10)
    assert p_gift > p_song > p_kw


def test_d10_dynamic_text_not_polluted_by_trends_copywriting():
    """C1 守卫：dynamic_text 编排权重=15，绝不取 trends_copywriting 生产实值
    999（污染后果：礼物 30 仍 > 999 为假——礼物应打断动态文案却被挡排队）。"""
    eng = _engine([_rule("dt", "dynamic_text", match="")], pm=dict(PM))
    dt = eng.cfg.rule_by_id("dt")
    assert eng._rule_priority(dt) == 15
    assert eng._rule_priority(dt) != eng.priority_mapping.get("trends_copywriting")


def test_d10_arbitration_weights_override_single_key():
    eng = _engine([_rule("g", "gift"), _rule("s", "song", match="")],
                  over={"arbitration_weights": {"gift": 50}})
    assert eng._rule_priority(eng.cfg.rule_by_id("g")) == 50
    assert eng._rule_priority(eng.cfg.rule_by_id("s")) == 20  # 未覆盖键用内置表


def test_d10_unknown_type_falls_back_zero():
    eng = _engine([_rule("g", "gift")])
    assert eng._rule_priority(RuleCfg(rule_id="x", trigger_type="future_type", match="")) == 0


@pytest.mark.parametrize("t", ["entrance", "follow"])
def test_entrance_follow_two_state_priority(t):
    """entrance/follow 两态取值路径（2026-11-06 三强制拆除——用户配置生效的验收
    载体；三断言=spec r2 四-1 钉死）：①on 态无覆盖→priority_mapping 值生效
    ②on 态 arbitration_weights 覆盖→覆盖胜（覆盖顺序 C8）③off 态→mapping 值
    且 override 不注入（F-E 口径）。原「两态恒 0」断言随钳制拆除反向改写。"""
    on = _engine([_rule("e", t, match="")])
    assert on._rule_priority(on.cfg.rule_by_id("e")) == 20  # ① mapping 值生效
    on_covered = _engine([_rule("e", t, match="")], over={"arbitration_weights": {t: 8}})
    assert on_covered._rule_priority(on_covered.cfg.rule_by_id("e")) == 8  # ② 覆盖胜
    off = _engine([_rule("e", t, match="")], over={"arbitration_enable": False})
    assert off._rule_priority(off.cfg.rule_by_id("e")) == 20  # ③ off 直取 mapping
    assert off.cfg.arbitration_enable is False  # override 不注入（F-E：off 态通道关闭）


def test_off_state_entrance_standard_interrupts_lower_priority():
    """F9 行为守卫（2026-11-06 拆除批次）：off 态入场 standard 规则以 mapping
    权重打断低权在播序列——「用户配置实际生效」钉在行为层而非仅 _rule_priority
    值层（song=10 < entrance=20 → 抢占）。"""
    eng = _engine([_rule("s", "song", match=""), _rule("e", "entrance", match="")],
                  over={"arbitration_enable": False})
    d1 = _hit(eng, "song", "歌名")
    assert d1["accepted"] is True and eng.state == "sequence"
    d2 = _hit(eng, "entrance", "观众甲")
    assert d2["reason"] == "interrupted"  # 20 >= 10 走抢占（O3 门仅低权才排队）
    assert eng.seq["rule_id"] == "e"


def test_d22_off_falls_back_to_legacy_mapping_source():
    """总开关 off：权重源回落 legacy _TRIGGER_PRIORITY_KEY→filter.priority_mapping
    （keyword→comment=20；gift 直取映射值 99）。"""
    eng = _engine([_rule("g", "gift"), _rule("k", "keyword"),
                   _rule("dt", "dynamic_text", match="")],
                  pm={"comment": 20, "gift": 99, "song": 10},
                  over={"arbitration_enable": False})
    assert eng._rule_priority(eng.cfg.rule_by_id("g")) == 99
    assert eng._rule_priority(eng.cfg.rule_by_id("k")) == 20
    assert eng._rule_priority(eng.cfg.rule_by_id("dt")) == 0  # legacy 表无此键


# ------------------------------------------------- d7 连续触发窗口

def _accept_first(eng, t="gift", match="x"):
    r = _hit(eng, t, match)
    assert r["reason"] == "accepted"
    eng.seq["main_done"] = True  # 直入 exit 步（后续触发走条件2 重排计数路径）
    return r


def test_window_within_accumulates():
    NOW[0] = 1000.0
    eng = _engine([_rule("g", "gift")])  # 窗口缺省 30
    _accept_first(eng)                    # counters=1
    NOW[0] = 1010.0                       # 10s 后（窗口内）
    r = _hit(eng, "gift")
    assert r["reason"] == "requeued"
    assert eng.counters["g"] == 2


def test_window_beyond_resets_to_base_tier():
    NOW[0] = 1000.0
    eng = _engine([_rule("g", "gift")])
    _accept_first(eng)                    # counters=1
    NOW[0] = 1035.0                       # 35s > 30s 窗口
    r = _hit(eng, "gift")
    assert r["reason"] == "requeued"
    assert eng.counters["g"] == 1  # 清零后自增=回落基础档（d7）


def test_sliding_anchor_permanent_continuity():
    """d24a 行为边界（M3 实播验收项）：每 29s 触发一次在滑动窗口下永久连续。"""
    NOW[0] = 1000.0
    eng = _engine([_rule("g", "gift")])
    _accept_first(eng)
    for i, t in enumerate((1029.0, 1058.0, 1087.0), start=2):
        NOW[0] = t
        eng.seq["main_done"] = True
        _hit(eng, "gift")
        assert eng.counters["g"] == i  # 29s 间隔不跨窗（<30s）→ 持续累计


def test_window_zero_disables():
    NOW[0] = 1000.0
    eng = _engine([_rule("g", "gift")], over={"continuous_trigger_window_s": 0})
    _accept_first(eng)
    NOW[0] = 1100.0
    _hit(eng, "gift")
    assert eng.counters["g"] == 2  # 0=禁用=永续累计（现行为）


def test_switch_off_disables_window():
    NOW[0] = 1000.0
    eng = _engine([_rule("g", "gift")], over={"arbitration_enable": False})
    _accept_first(eng)
    NOW[0] = 1100.0
    _hit(eng, "gift")
    assert eng.counters["g"] == 2


# ------------------------------------------------- e6 三路径锚点刷新

def test_e6_in_progress_refreshes_anchor():
    NOW[0] = 1000.0
    eng = _engine([_rule("g", "gift")])
    _hit(eng, "gift")
    NOW[0] = 1005.0
    r = _hit(eng, "gift")  # main 未完成 → in_progress
    assert r["reason"] == "in_progress"
    assert eng.key_last["g"] == 1005.0


def test_e6_queued_refreshes_anchor():
    NOW[0] = 1000.0
    eng = _engine([_rule("a", "gift"), _rule("b", "keyword")])
    _hit(eng, "gift")  # A 序列（p=30）
    NOW[0] = 1010.0
    r = _hit(eng, "keyword")  # B（p=10）standard 权重门 → 排队
    assert r["reason"] == "queued"
    assert eng.key_last["b"] == 1010.0


def test_e6_interrupted_refreshes_anchor():
    NOW[0] = 1000.0
    eng = _engine([_rule("k", "keyword"), _rule("g", "gift")])
    _hit(eng, "keyword")  # K 序列（p=10）
    NOW[0] = 1010.0
    r = _hit(eng, "gift")  # G（p=30）> 10 → 打断
    assert r["reason"] == "interrupted"
    assert eng.key_last["g"] == 1010.0


# ------------------------------------------------- d22/uc5b builtin 999 挂起看门狗

def _suspended_player(monkeypatch=None):
    import os
    import tempfile
    import threading
    import time

    from utils.audio.builtin_play_center import AUDIO_PLAY_CENTER
    p = AUDIO_PLAY_CENTER.__new__(AUDIO_PLAY_CENTER)
    p.config_data = {"priority_mapping": {"comment": 20}}
    p.config_path = os.path.join(tempfile.gettempdir(), "__no_such_t3__.json")
    p.stream = None
    p.pause_event = threading.Event()
    p.audio_data_event = threading.Event()
    p._done_event = threading.Event()
    p._skip_event = threading.Event()
    p._stop_flag = threading.Event()
    p.audio_json_list = []
    p.list_lock = threading.Lock()
    p._priority_provider = None
    p._release_999_hook = None
    p._playing_is_999 = False
    p._suspended_play = None
    p._interruptor_pending = False
    p._suspended_999 = None
    p._suspended_999_at = None
    p._suspended_999_alarm_at = None
    return p


def test_watchdog_alarms_once_after_threshold():
    import time

    p = _suspended_player()
    p._suspended_999 = {"type": "reread_top_priority", "voice_path": "x.mp3"}
    p._suspended_999_at = time.monotonic() - 400.0  # 超 300s 阈值
    with patch("utils.alarm_notifier.alarm_error") as alarm:
        p._watchdog_999_suspension()
        assert alarm.called
        p._watchdog_999_suspension()  # 同挂起期去重
        assert alarm.call_count == 1


def test_watchdog_silent_when_fresh_or_cleared():
    import time

    p = _suspended_player()
    p._suspended_999 = {"type": "reread_top_priority", "voice_path": "x.mp3"}
    p._suspended_999_at = time.monotonic() - 10.0  # 未超阈值
    with patch("utils.alarm_notifier.alarm_error") as alarm:
        p._watchdog_999_suspension()
        assert not alarm.called
    # 挂起清理后（discard/resume 路径）看门狗归零
    p._suspended_999_at = time.monotonic() - 400.0
    p._discard_suspended("测试")
    with patch("utils.alarm_notifier.alarm_error") as alarm:
        p._watchdog_999_suspension()
        assert not alarm.called
        assert p._suspended_999_at is None
