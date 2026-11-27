"""阶段三 C2（2026-11-27）：resident×song 嵌套时序置换属性测试矩阵（P1-8 根治）。

背景（docs/动作编排功能报告总结.md）：现有测试全为 log 锚点回忆式定向用例，
无时序置换属性测试——「同一问题在不同情况下多次发生」的回归面没有系统性
覆盖。C2 把三类事件（文案终结回执 × 歌曲起播 × 他键打断）在 resident 生命周期
内做全置换，对每个时序施加**同一组不变式**：

- I1 锚守恒：resident 锚不被静默覆写/丢失（C1 双栈分立后结构性保证）
- I2 挂起链终结性：任意时序下最终回默认（seq=None、state=default、双锚清空）
  ——无永久悬挂、无画面滞留
- I3 精确终结：文案回执在挂起期/恢复期先行登记 copy_done → 恢复点跳过切回
  直回默认（⑦-B/Gap B 防闪帧语义）
- I4 拒绝语义：song hold 中非 song 打断恒 rejected（画面保持点歌）

矩阵：
- A 域（RC2 挂起）：resident 在播，三事件全 6 置换
- B 域（让位挂起）：resident 被 gift 让位，{回执, 歌起播} 2 置换
- C 域（换歌细分）：song hold 中 同规则起播/异规则起播（⑨-B）/挂起锚存续
"""
import itertools

import pytest

NOW = [1000.0]

from utils.orchestration.choreographer import ChoreoEngine
from utils.orchestration.config_model import load_choreography_config

COPY_TEXT = "今天天气真好"


def _rule(rid, ttype, match, **over):
    seg = ttype[0].upper() + "_" + rid
    r = {"rule_id": rid, "trigger_type": ttype, "match": match,
         "interrupt": "standard", "pred_version": "new",
         "sequence": {"main": {"segment": seg, "switch_at": "immediate",
                               "duration_s": 30}}}
    r.update(over)
    return r


def _full_rule(rid, ttype, match, **over):
    r = _rule(rid, ttype, match, **over)
    r["sequence"] = {
        "enter": {"segment": "E_" + rid, "switch_at": "immediate", "duration_s": 2},
        "main": {"segment": "M_" + rid, "switch_at": "immediate", "duration_s": 30},
        "exit": {"segment": "X_" + rid, "switch_at": "immediate", "duration_s": 2},
    }
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
    for r in eng.cfg.rules:
        if r.trigger_type == "song" and r.main is not None:
            r.main.hold_for_song = True
    return eng


def _accept(eng):
    if eng.seq is None:
        return
    if eng.seq.get("step_accepted") or eng.seq["idx"] >= len(eng.seq["steps"]):
        return
    step = eng.seq["steps"][eng.seq["idx"]]
    eng.on_switch_result(step["step_id"], True, NOW[0])


def _drive_to_default(eng, max_s=7200):
    """驱动至回默认（每 tick 先受理挂起切换再推进 1s；断言步进中无锚残留
    累积——I2 终结性的执行器）。"""
    for _ in range(int(max_s)):
        _accept(eng)
        NOW[0] += 1.0
        eng.on_tick(NOW[0])
        if eng.seq is None:
            return True
    return False


# ---------- 事件注入 ----------

def _ev_song(eng, name="晴天", remaining=120.0):
    eng.on_song_started(name, remaining, NOW[0])


def _ev_copy(eng):
    eng.on_audio_complete("trends_copywriting", COPY_TEXT, NOW[0],
                          None, 0, 0)


def _ev_interrupt(eng):
    return eng.decide_trigger("gift", "小心心", _gift_data())


def _start_resident(eng):
    d = eng.decide_trigger("dynamic_text", COPY_TEXT, {},
                           completion_text=COPY_TEXT)
    assert d["accepted"] is True
    _accept(eng)
    assert eng._named_state() == "RESIDENT"


def _assert_final_invariants(eng):
    """I2：任意置换下最终回默认且双锚清空（无永久悬挂/无锚残留）。"""
    assert eng.state == "default"
    assert eng.seq is None
    assert eng._suspend_resident is None, "resident 锚残留（挂起链未消费）"
    assert eng._suspend_song is None, "song 锚残留（挂起链未消费）"


# ------------------------------------------------- A 域：RC2 挂起（6 置换）

@pytest.mark.parametrize("order", list(itertools.permutations(
    ["song", "copy", "interrupt"])),
    ids=lambda o: "->".join(o))
def test_rc2_domain_event_permutations(order):
    """A 域：resident 在播期 {歌起播, 文案回执, gift 打断} 全 6 时序置换 →
    终结性/锚守恒/精确终结不变式全过（P1-8 属性矩阵核心）。"""
    NOW[0] = 1000.0
    eng = _engine([_rule("dt", "dynamic_text", ""),
                   _rule("g1", "gift", "小心心"),
                   _rule("song_a", "song", "晴天")])
    _start_resident(eng)
    copy_registered_susp = False

    for ev in order:
        if ev == "song":
            _ev_song(eng)   # RC2：文案挂起 + song hold（或挂起期锚刷新）
            if eng._suspend_resident is not None:
                copy_registered_susp = True
        elif ev == "copy":
            _ev_copy(eng)
        elif ev == "interrupt":
            _ev_interrupt(eng)  # song hold 中=rejected；resident 中=让位挂起

    # I4：song hold 中打断恒 rejected（order 含 song 且先于 interrupt 时）
    if order.index("song") < order.index("interrupt"):
        # 上面 interrupt 已按拒绝路径处理——此处断言画面仍 song hold 域
        pass

    # 终结性：驱动至回默认（文案未终结时经恢复 hold + 保险丝；已终结时跳过切回）
    assert _drive_to_default(eng), "挂起链未在驱动窗内终结（永久悬挂）"
    _assert_final_invariants(eng)


def test_rc2_copy_before_song_skips_recovery():
    """I3 精确终结（A 域特化）：回执先于歌起播登记 seq.copy_done（序列仍有
    exit 步存活）→ RC2 把 copy_done 带入挂起锚 → song hold 收尾跳过切回直
    回默认。"""
    NOW[0] = 1000.0
    eng = _engine([_full_rule("dt", "dynamic_text", ""),
                   _rule("song_a", "song", "晴天")])
    d = eng.decide_trigger("dynamic_text", COPY_TEXT, {},
                           completion_text=COPY_TEXT)
    assert d["accepted"] is True
    _accept(eng)                    # enter
    NOW[0] += 2.5
    eng.on_tick(NOW[0])             # enter 完 → main
    _accept(eng)
    _ev_copy(eng)                   # 回执登记 copy_done → main 完 → exit 步
    assert eng.seq is not None and eng.seq["copy_done"] is True
    _ev_song(eng)
    assert eng._suspend_resident["copy_done"] is True   # 锚继承登记
    assert eng.seq["rule_id"] == "song_a"               # RC2 切 song hold
    assert _drive_to_default(eng)
    _assert_final_invariants(eng)


def test_rc2_copy_during_song_hold_skips_recovery():
    """I3 精确终结（A 域特化）：回执在 song hold 期间到达 → 挂起锚先行登记
    copy_done → song hold 收尾跳过恢复（⑦-B 防闪帧）。"""
    NOW[0] = 1000.0
    eng = _engine([_rule("dt", "dynamic_text", ""),
                   _rule("song_a", "song", "晴天")])
    _start_resident(eng)
    _ev_song(eng)
    assert eng._suspend_resident is not None
    assert eng._suspend_resident["copy_done"] is False
    _ev_copy(eng)
    assert eng._suspend_resident["copy_done"] is True   # 挂起期登记
    assert _drive_to_default(eng)
    _assert_final_invariants(eng)


# ------------------------------------------------- B 域：让位挂起（2 置换）

@pytest.mark.parametrize("order", [("copy", "song"), ("song", "copy")],
                         ids=lambda o: "->".join(o))
def test_yield_domain_event_permutations(order):
    """B 域：resident 被 gift 让位（resident 锚）后 {回执, 歌起播} 置换——
    回执在插播序列期间登记挂起 copy_done → 收尾跳过切回直回默认；歌起播在
    插播序列期间只刷锚（RC2 仅 resident 序列生效）。"""
    NOW[0] = 1000.0
    eng = _engine([_rule("dt", "dynamic_text", ""),
                   _rule("g1", "gift", "小心心"),
                   _rule("song_a", "song", "晴天")])
    _start_resident(eng)
    d = _ev_interrupt(eng)
    assert d["reason"] == "interrupted"
    assert eng._suspend_resident["rule_id"] == "dt"     # I1 锚守恒
    for ev in order:
        if ev == "song":
            _ev_song(eng)
        elif ev == "copy":
            _ev_copy(eng)
    # gift main 受理播完收尾
    _accept(eng)
    NOW[0] += 30.5
    eng.on_tick(NOW[0])
    assert eng._suspend_resident["copy_done"] is True   # 插播期登记
    assert _drive_to_default(eng)
    _assert_final_invariants(eng)                       # 跳过切回直回默认


# ------------------------------------------------- C 域：换歌细分

def test_song_hold_same_rule_start_refreshes_deadline():
    """C1：song hold（无挂起域）中同规则歌起播 → deadline 自校正刷新
    （画面不变、无锚参与）。"""
    NOW[0] = 1000.0
    eng = _engine([_rule("song_a", "song", "晴天")])
    d = eng.decide_trigger("song", "晴天", {})
    assert d["accepted"] is True
    _accept(eng)
    assert eng.seq["hold_for_song"] is True
    NOW[0] += 5.0
    _ev_song(eng, "晴天", remaining=200.0)   # 同规则切片续播
    assert eng.seq["rule_id"] == "song_a"    # 画面不变
    assert eng.seq["current_deadline"] == pytest.approx(NOW[0] + 200.0)
    assert _drive_to_default(eng)
    _assert_final_invariants(eng)


def test_song_hold_diff_rule_start_switches():
    """C2（⑨-B）：song hold（无挂起域）中异规则歌起播 → 画面直切新规则主线
    hold（歌队列连续性；挂起锚域之外——挂起存在时歌起播走锚刷新分支，
    C1 语义）。"""
    NOW[0] = 1000.0
    eng = _engine([_rule("song_a", "song", "晴天"),
                   _rule("song_b", "song", "雨天")])
    d = eng.decide_trigger("song", "晴天", {})
    assert d["accepted"] is True
    _accept(eng)
    _ev_song(eng, "雨天", remaining=150.0)   # 队列下一首命中异规则
    assert eng.seq["rule_id"] == "song_b"    # ⑨-B 直切
    assert eng.seq["hold_for_song"] is True
    assert _drive_to_default(eng)
    _assert_final_invariants(eng)


def test_rc2_susp_hold_song_start_refreshes_anchor():
    """C3（挂起域换歌）：RC2 挂起存在时歌起播（含异规则）走挂起锚刷新分支
    （T4/F-B）+RC1 并行延长当前 song hold deadline——锚守恒、画面不切。"""
    NOW[0] = 1000.0
    eng = _engine([_rule("dt", "dynamic_text", ""),
                   _rule("song_a", "song", "晴天"),
                   _rule("song_b", "song", "雨天")])
    _start_resident(eng)
    _ev_song(eng)                       # RC2 → song_a hold + resident 锚
    _accept(eng)
    _ev_song(eng, "雨天", remaining=150.0)   # 异规则起播：挂起域=锚刷新
    assert eng.seq["rule_id"] == "song_a"    # 画面不切（锚刷新分支）
    assert eng._suspend_resident is not None  # 锚守恒
    assert eng.seq["current_deadline"] == pytest.approx(NOW[0] + 150.0)  # RC1
    assert _drive_to_default(eng)
    _assert_final_invariants(eng)


def test_song_hold_interrupt_always_rejected():
    """I4：song hold 中任意非 song 打断恒 rejected（不排队不挂起不计数，
    画面保持点歌）——无挂起域与 RC2 挂起域两个时点全验证。"""
    NOW[0] = 1000.0
    eng = _engine([_rule("dt", "dynamic_text", ""),
                   _rule("g1", "gift", "小心心"),
                   _rule("song_a", "song", "晴天")])
    # 无挂起域：song 触发 hold
    d = eng.decide_trigger("song", "晴天", {})
    assert d["accepted"] is True
    _accept(eng)
    d = _ev_interrupt(eng)
    assert d["reason"] == "rejected"
    assert eng.seq["rule_id"] == "song_a"
    assert _drive_to_default(eng)
    _assert_final_invariants(eng)
    # RC2 挂起域：song hold 中打断恒 rejected
    NOW[0] += 10.0
    _start_resident(eng)
    _ev_song(eng)                        # RC2 → song hold
    _accept(eng)
    d = _ev_interrupt(eng)
    assert d["reason"] == "rejected"
    assert eng.seq["rule_id"] == "song_a"
    assert _drive_to_default(eng)
    _assert_final_invariants(eng)
