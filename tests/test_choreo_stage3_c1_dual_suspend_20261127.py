"""阶段三 C1（2026-11-27）：挂起锚双栈——resident 锚与 song 锚分离（P0-2 根治）。

背景（docs/动作编排功能报告总结.md P0-2 + 优化计划 C1）：旧单槽
song_suspended「后一次覆盖前次」丢锚风险全靠流程拓扑隐式保证；A2 已上
写前必空断言（击穿即暴露），C1 进一步把锚按恢复域分立双槽：
- resident 锚：文案让位（_suspend_hold_for kind=resident）/ RC2（歌起播挂起
  播报序列）登记，恢复=切回文案画面；
- song 锚：song 让位挂起（d9/e4 防御域）登记，恢复=切回点歌画面；
双锚互不覆写；嵌套挂起语义显式声明（LIFO 依恢复序消费）。

覆盖：
- t1 双锚共存不覆写（P0-2 根治验收）
- t2 setter 按 kind 定向入槽 / None 全清 / property resident 优先读取
- t3 消费点定向清槽：收尾只清本锚，另一锚保留并在下个序列收尾消费（嵌套
  LIFO 恢复链全流程）
- t4 A2 不变量随双栈分立：同槽覆写仍 AssertionError
- t5 get_state 双槽透出
"""
import pytest

NOW = [1000.0]

from utils.orchestration.choreographer import ChoreoEngine
from utils.orchestration.config_model import load_choreography_config


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
    step = eng.seq["steps"][eng.seq["idx"]]
    return eng.on_switch_result(step["step_id"], True, NOW[0])


def _resident_anchor(rid="dt", deadline=1060.0):
    return {"rule_id": rid, "deadline": deadline, "entered_at": NOW[0],
            "kind": "resident", "copy": "x", "copy_done": False}


def test_dual_slots_no_overwrite():
    """t1（P0-2 根治验收）：resident 锚占用时 song 挂起入 song 槽——不覆写
    不丢锚（旧单槽此处断言爆炸或静默覆盖）。"""
    NOW[0] = 1000.0
    eng = _engine([_full_rule("dt", "dynamic_text", ""),
                   _rule("g1", "gift", "小心心"),
                   _rule("song_a", "song", "晴天")])
    # 真实流程：dt resident → gift 让位 → resident 锚登记
    eng.decide_trigger("dynamic_text", "今天天气真好", {},
                       completion_text="今天天气真好")
    eng.decide_trigger("gift", "小心心", _gift_data())
    assert eng._suspend_resident is not None
    assert eng._suspend_resident["rule_id"] == "dt"
    assert eng._suspend_song is None
    # song 挂起防御域直接登记（同槽拓扑保证，per-slot 断言不阻碍）
    song_rule = eng.cfg.rule_by_id("song_a")
    cmds = []
    eng._suspend_hold_for(song_rule, {"song_total_s": 300}, NOW[0], cmds, "song",
                          match_value="晴天")
    # resident 锚原样保留（P0-2 核心：旧单槽会被覆写丢失）；song 锚记录的是
    # 被打断方（g1）归属——恢复语义=切回 g1 画面
    assert eng._suspend_resident is not None
    assert eng._suspend_resident["rule_id"] == "dt"
    assert eng._suspend_song is not None
    assert eng._suspend_song["kind"] == "song"
    assert eng._suspend_song["rule_id"] == "g1"
    assert eng.seq["rule_id"] == "song_a"   # 新序列=song 让位接管


def test_setter_kind_routing_and_clear():
    """t2：setter 按 kind 定向入槽；None=双槽全清；property resident 优先。"""
    NOW[0] = 1000.0
    eng = _engine([_rule("g1", "gift", "小心心")])
    eng.song_suspended = _resident_anchor("dt")
    assert eng._suspend_resident is not None and eng._suspend_song is None
    assert eng.song_suspended["kind"] == "resident"   # resident 优先读取
    eng.song_suspended = {"rule_id": "song_a", "deadline": NOW[0] + 300,
                          "entered_at": NOW[0], "kind": "song",
                          "copy": "", "copy_done": False}
    assert eng._suspend_resident is not None          # 双锚共存
    assert eng._suspend_song is not None
    assert eng.song_suspended["kind"] == "resident"   # 读取仍 resident 优先
    eng.song_suspended = None
    assert eng._suspend_resident is None and eng._suspend_song is None


def test_nested_lifo_recovery_chain():
    """t3（C1 核心）：嵌套挂起 LIFO 恢复链全流程——gift 序列收尾消费 resident
    锚（song 锚保留），恢复文案序列收尾再消费 song 锚（song 恢复序列）。"""
    NOW[0] = 1000.0
    eng = _engine([_full_rule("dt", "dynamic_text", ""),
                   _rule("g1", "gift", "小心心"),
                   _rule("song_a", "song", "晴天")])
    # ① dt resident → gift 让位（resident 锚 @deadline=1060）
    eng.decide_trigger("dynamic_text", "今天天气真好", {},
                       completion_text="今天天气真好")
    eng.decide_trigger("gift", "小心心", _gift_data())
    assert eng._suspend_resident["deadline"] == pytest.approx(1060.0)
    # ② song 锚直接登记（防御域；deadline 远未来=歌曲未结束）
    eng._suspend_song = {"rule_id": "song_a", "deadline": NOW[0] + 3600,
                         "entered_at": NOW[0], "kind": "song",
                         "copy": "", "copy_done": False}
    # ③ gift 序列推进：插播离场步受理播完 → gift main 受理播完收尾 →
    #    消费 resident 锚 → 恢复文案序列；song 锚保留
    _accept(eng)                                       # dt 离场过渡步（插播）
    NOW[0] += 2.5
    eng.on_tick(NOW[0])                                # 离场完 → gift main
    _accept(eng)                                       # gift main 受理
    NOW[0] += 30.5
    eng.on_tick(NOW[0])                                # main 播完收尾 → 消费 resident 锚
    assert eng._suspend_resident is None               # resident 已消费
    assert eng._suspend_song is not None               # song 锚保留（C1 核心）
    assert eng.seq["resident"] is True                # 恢复文案序列（enter/hold/exit）
    # ④ 恢复序列：enter 受理播完 → hold 主线（循环至保险丝）→ exit → 收尾
    _accept(eng)                                       # enter
    NOW[0] += 2.5
    eng.on_tick(NOW[0])                                # enter 完 → hold main
    _accept(eng)
    NOW[0] += 61.0                                     # 越过 loop_deadline(1060)
    eng.on_tick(NOW[0])                                # hold 保险丝终结 → exit
    _accept(eng)
    NOW[0] += 2.5
    eng.on_tick(NOW[0])                                # exit 完收尾 → 消费 song 锚
    assert eng._suspend_song is None                   # song 锚已消费
    assert eng.seq["hold_for_song"] is True            # song 恢复序列（hold）
    assert eng.seq["rule_id"] == "song_a"


def test_per_slot_invariant_assertion():
    """t4（A2 随双栈分立）：同槽覆写仍 AssertionError（resident 锚占用期再写
    resident 槽=不变量击穿）；跨槽（song 槽）写入不触发。"""
    NOW[0] = 1000.0
    eng = _engine([_full_rule("dt", "dynamic_text", ""),
                   _rule("g1", "gift", "小心心")])
    eng.decide_trigger("dynamic_text", "今天天气真好", {},
                       completion_text="今天天气真好")
    eng.decide_trigger("gift", "小心心", _gift_data())
    assert eng._suspend_resident is not None
    with pytest.raises(AssertionError, match="挂起锚槽写入前非空"):
        cmds = []
        eng._suspend_hold_for(eng.cfg.rule_by_id("dt"), {}, NOW[0], cmds,
                              "resident", match_value="x")


def test_get_state_dual_slots():
    """t5：get_state 双槽透出（song_suspended 兼容字段保持）。"""
    NOW[0] = 1000.0
    eng = _engine([_rule("g1", "gift", "小心心")])
    st = eng.get_state()
    assert st["song_suspended"] is None
    assert st["suspend_resident"] is None
    assert st["suspend_song"] is None
    eng.song_suspended = _resident_anchor("dt")
    st = eng.get_state()
    assert st["suspend_resident"]["rule_id"] == "dt"
    assert st["song_suspended"]["rule_id"] == "dt"    # 兼容面一致
