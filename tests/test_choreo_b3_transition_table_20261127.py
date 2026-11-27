"""阶段二 B3（2026-11-27）：显式状态转移表（P0-1 根治）。

背景（docs/动作编排功能报告总结.md）：顶层 2 状态是假的，真实行为由
seq × song_suspended × resident × copy_done × preempt_request × ... 笛卡尔积
决定——隐式组合状态空间不可穷尽，历轮「拆东墙补西墙」的机制性原因。

B3 把触发判定域压平为有序守卫转移表（具名状态 × 守卫 → 动作）：
- 装载期穷尽检查（每具名状态必达 always 兜底行，半张表=import 即失败）
- 具名状态分类器（DEFAULT/SEQ/RESIDENT/SONG_HOLD）+ 步相位观测透出
- decide_trigger 嵌套 if-else 逐分支迁移为表行（行为零变化）

覆盖：
- t1 装载穷尽检查通过 + 篡改表必须失败（静态保证本身被测试锁死）
- t2 具名状态分类器四态可达 + get_state 透出
- t3~t10 表行行为锁：同键忽略/重排、song hold 承接与放弃、queue 排队、
  resident 让位与挂起更新、权重门、Gap A 无缝门、DEFAULT 启动
- t11 d22 回退语义：仲裁 off 时 song hold 走兜底打断行（表兜底行可定位）
"""
import pytest

from utils.orchestration.choreographer import ChoreoEngine, TransRow
from utils.orchestration.config_model import load_choreography_config

NOW = [1000.0]


def _rule(rid, ttype, match, seg=None, main=None, **over):
    seg = seg or (ttype[0].upper() + "_" + rid)
    r = {"rule_id": rid, "trigger_type": ttype, "match": match,
         "interrupt": "standard", "pred_version": "new",
         "sequence": {"main": {"segment": seg, "switch_at": "immediate",
                               "duration_s": 30}}}
    if main:
        r["sequence"]["main"] = dict(main)
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


# ------------------------------------------------- 装载期穷尽检查

def test_table_exhaustive_and_registry():
    """t1a：装载检查通过（import 已隐式验证）——显式再调一次锁死。"""
    ChoreoEngine._verify_transition_table()


def test_table_exhaustive_fails_on_gap(monkeypatch):
    """t1b：穷尽检查本身被测试锁死——删掉通配兜底行后各状态必须报缺陷。"""
    patched = tuple(r for r in ChoreoEngine._TRIGGER_TABLE
                    if not (r.guard == "always" and r.state == "*"))
    monkeypatch.setattr(ChoreoEngine, "_TRIGGER_TABLE", patched)
    # RuntimeError（2026-11-27 审查修复 #6）：装载检查 assert 改显式 raise——
    # python -O 下 assert 被剥除会让穷尽性静态保证整体失效
    with pytest.raises(RuntimeError, match="穷尽性缺陷"):
        ChoreoEngine._verify_transition_table()


def test_table_rows_have_batch_notes():
    """t1c：每行必须携带批次锚点 note（历轮 bug 场景在表中可定位）。"""
    for row in ChoreoEngine._TRIGGER_TABLE:
        assert row.note, f"行缺 note：{row.state}/{row.guard}/{row.action}"


# ------------------------------------------------- 具名状态分类器

def test_named_state_classifier():
    """t2：四具名状态可达 + get_state 透出（隐式组合状态显式化验收）。"""
    NOW[0] = 1000.0
    eng = _engine([_rule("g1", "gift", "小心心"),
                   _full_rule("dt", "dynamic_text", ""),
                   _rule("song_a", "song", "晴天")])
    assert eng._named_state() == "DEFAULT"
    assert eng.get_state()["named_state"] == "DEFAULT"
    # gift 序列 → SEQ
    eng.decide_trigger("gift", "小心心", _gift_data())
    assert eng._named_state() == "SEQ"
    assert eng.get_state()["seq_phase"].startswith("main:")  # 切换未受理
    _accept(eng)
    assert eng.get_state()["seq_phase"] == "main"
    eng.on_tick(NOW[0] + 30.5)      # main 播完（单 main 步序列收尾回默认）
    assert eng._named_state() == "DEFAULT"
    # dynamic_text 序列 → RESIDENT（dt 触发须在 DEFAULT 起——gift(30) 挡 dt(15)）
    eng.decide_trigger("dynamic_text", "今天天气真好", {},
                       completion_text="今天天气真好")
    assert eng._named_state() == "RESIDENT"
    # song(20)≥dt(15) 让位插播（先插旧规则离场过渡步）→ 离场受理播完进 song
    # main 受理后 SONG_HOLD
    eng.decide_trigger("song", "晴天", {})
    _accept(eng)            # 旧离场过渡步受理
    NOW[0] += 2.5
    eng.on_tick(NOW[0])     # 离场播完 → song main
    _accept(eng)            # song main 受理 → SONG_HOLD
    assert eng._named_state() == "SONG_HOLD"
    assert eng.get_state()["seq_phase"] == "song_hold"


# ------------------------------------------------- 表行行为锁

def test_same_rule_in_progress():
    """t3：同键 main 未完成 → in_progress（忽略不计数，WP-6）。"""
    NOW[0] = 1000.0
    eng = _engine([_rule("g1", "gift", "小心心")])
    eng.decide_trigger("gift", "小心心", _gift_data())
    _accept(eng)
    d = eng.decide_trigger("gift", "小心心", _gift_data())
    assert d["reason"] == "in_progress"
    assert d["accepted"] is False
    assert eng.counters["g1"] == 1  # 忽略不计数


def test_same_rule_requeue():
    """t4：同键 main 已完成（exit 中）→ requeued（WP-9 重排）。"""
    NOW[0] = 1000.0
    eng = _engine([_full_rule("g1", "gift", "小心心")])
    eng.decide_trigger("gift", "小心心", _gift_data())
    _accept(eng)                    # enter
    NOW[0] += 2.5
    eng.on_tick(NOW[0])             # enter 播完 → main
    _accept(eng)
    NOW[0] += 30.5
    eng.on_tick(NOW[0])             # main 播完 → exit
    assert eng.seq["steps"][eng.seq["idx"]]["name"] == "exit"
    d = eng.decide_trigger("gift", "小心心", _gift_data())
    assert d["reason"] == "requeued"
    assert d["accepted"] is True
    assert eng.seq["pending_requeue"] is not None


def test_song_hold_song_queued_and_reject():
    """t5：song hold 中 song 重触发 → song_queued（d11）；非 song → rejected。"""
    NOW[0] = 1000.0
    eng = _engine([_rule("song_a", "song", "晴天"),
                   _rule("song_b", "song", "雨天"),
                   _rule("kw", "keyword", "你好")])
    eng.decide_trigger("song", "晴天", {})
    _accept(eng)
    assert eng._named_state() == "SONG_HOLD"
    d = eng.decide_trigger("song", "雨天", {})
    assert d["reason"] == "song_queued"
    assert d["accepted"] is True
    assert eng._named_state() == "SONG_HOLD"  # 画面保持
    d = eng.decide_trigger("keyword", "你好呀", {})
    assert d["reason"] == "rejected"
    assert d["accepted"] is False


def test_queue_mode_enqueue():
    """t6：queue 模式触发 → 排队（O1/O2）。"""
    NOW[0] = 1000.0
    eng = _engine([_rule("g1", "gift", "小心心"),
                   _rule("g2", "gift", "玫瑰", interrupt="queue")])
    eng.decide_trigger("gift", "小心心", _gift_data())
    d = eng.decide_trigger("gift", "玫瑰", _gift_data("玫瑰"))
    assert d["reason"] == "queued"
    assert d["accepted"] is False
    assert len(eng.pending_queue) == 1


def test_resident_yield_and_susp():
    """t7：resident 让位（高权重 gift 打断文案）→ interrupted + 挂起登记。"""
    NOW[0] = 1000.0
    eng = _engine([_rule("dt", "dynamic_text", ""),
                   _rule("g1", "gift", "小心心")])
    eng.decide_trigger("dynamic_text", "今天天气真好", {},
                       completion_text="今天天气真好")
    _accept(eng)
    assert eng._named_state() == "RESIDENT"
    d = eng.decide_trigger("gift", "小心心", _gift_data())
    assert d["reason"] == "interrupted"
    assert d["accepted"] is True
    assert eng.song_suspended is not None
    assert eng.song_suspended["kind"] == "resident"
    assert eng.song_suspended["rule_id"] == "dt"


def test_resident_updated_on_new_copy():
    """t7b：resident 挂起占用中新一轮文案 → resident_updated（挂起指针更新）。"""
    NOW[0] = 1000.0
    eng = _engine([_rule("dt", "dynamic_text", ""),
                   _rule("g1", "gift", "小心心")])
    eng.decide_trigger("dynamic_text", "今天天气真好", {},
                       completion_text="今天天气真好")
    _accept(eng)
    eng.decide_trigger("gift", "小心心", _gift_data())   # 让位 → 挂起登记
    assert eng.song_suspended is not None
    d = eng.decide_trigger("dynamic_text", "第二篇文章内容", {},
                           completion_text="第二篇文章内容")
    assert d["reason"] == "resident_updated"
    assert d["accepted"] is True
    assert eng.song_suspended["rule_id"] == "dt"
    assert eng.song_suspended["copy"] == "第二篇文章内容"


def test_weight_lower_queue():
    """t8：低权重触发不得打断高权重序列（O3 权重门）→ queued。"""
    NOW[0] = 1000.0
    eng = _engine([_rule("g1", "gift", "小心心"),
                   _rule("kw", "keyword", "你好")])
    eng.decide_trigger("gift", "小心心", _gift_data())
    _accept(eng)
    d = eng.decide_trigger("keyword", "你好呀", {})   # keyword=10 < gift=30
    assert d["reason"] == "queued"
    assert eng._named_state() == "SEQ"               # 画面不打断


def test_preempt_gate_segment_end_main():
    """t9：Gap A 无缝门——segment_end 主线未播完 → preempt_pending 延迟抢占。"""
    NOW[0] = 1000.0
    eng = _engine([_rule("g1", "gift", "小心心",
                         main={"segment": "M1", "switch_at": "segment_end",
                               "duration_s": 30}),
                   _rule("g2", "gift", "玫瑰")])
    eng.decide_trigger("gift", "小心心", _gift_data())
    _accept(eng)
    d = eng.decide_trigger("gift", "玫瑰", _gift_data("玫瑰"))
    assert d["reason"] == "preempt_pending"
    assert d["accepted"] is False
    assert eng.seq["preempt_request"]["rule_id"] == "g2"


def test_default_start():
    """t10：DEFAULT 态命中 → accepted 启动新序列。"""
    NOW[0] = 1000.0
    eng = _engine([_rule("g1", "gift", "小心心")])
    d = eng.decide_trigger("gift", "小心心", _gift_data())
    assert d["reason"] == "accepted"
    assert d["accepted"] is True
    assert eng._named_state() == "SEQ"


def test_arb_off_song_hold_falls_to_interrupt_row():
    """t11：d22 仲裁 off 整体回退——song hold 中 standard 触发走兜底打断行
    （表兜底行可定位：d22 off=回退 2026-11-16 前语义）。"""
    NOW[0] = 1000.0
    eng = _engine([_rule("song_a", "song", "晴天"),
                   _rule("g1", "gift", "小心心")],
                  arbitration_enable=False)
    eng.decide_trigger("song", "晴天", {})
    _accept(eng)
    assert eng._named_state() == "SONG_HOLD"
    d = eng.decide_trigger("gift", "小心心", _gift_data())
    assert d["reason"] == "interrupted"
    assert d["accepted"] is True
