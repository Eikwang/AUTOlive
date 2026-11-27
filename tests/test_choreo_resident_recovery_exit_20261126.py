"""resident 恢复序列离场步补齐守卫（方案一，2026-11-26 log3.txt 实测回归）。

根因回归背景：_recover_song_hold(kind=resident) 此前只重建 enter+hold、
无 exit 步——文案消费完（copy_done 精确终结/loop_deadline 保险丝到期）越过
步尾直回默认，离场永久丢失（log3.txt 17:45:28.076 与 17:51:08.349 两轮实测：
「主线循环终结」与「序列播完回默认」同毫秒，exit 段 39 素材 1.96s 从未出现）。

覆盖：
- t1 resident 恢复序列=[enter 重播, main hold, exit 补齐]
- t2 copy_done 精确终结 → 推进到 exit 切段 → exit 播完 → 回默认
- t3 保险丝到期终结 → 同样经 exit 回默认
- t4 song 恢复序列保持无 exit（歌队列连续性既有语义不动）
"""
from utils.orchestration.choreographer import (
    ChoreoEngine, SwitchSegment, RestoreDefault)
from utils.orchestration.config_model import load_choreography_config

NOW = [1000.0]

COPY = "今天天气真好"


def _dt_rule(rid="dt", **over):
    r = {"rule_id": rid, "trigger_type": "dynamic_text", "match": "",
         "interrupt": "standard", "pred_version": "new",
         "sequence": {
             "enter": {"segment": "E_" + rid, "switch_at": "immediate",
                       "duration_s": 2},
             "main": {"segment": "M_" + rid, "switch_at": "immediate",
                      "duration_s": 10},
             "exit": {"segment": "X_" + rid, "switch_at": "segment_end",
                      "duration_s": 2},
         }}
    r.update(over)
    return r


def _gift_rule(rid="g1", main_dur=5, **over):
    r = {"rule_id": rid, "trigger_type": "gift", "match": "小心心",
         "interrupt": "standard", "pred_version": "new",
         "sequence": {"main": {"segment": "G_" + rid, "switch_at": "immediate",
                               "duration_s": main_dur}}}
    r.update(over)
    return r


def _song_rule(rid="song_a", match="晴天", seg="S_主", **over):
    r = {"rule_id": rid, "trigger_type": "song", "match": match,
         "interrupt": "standard", "pred_version": "new",
         "sequence": {"main": {"segment": seg, "switch_at": "immediate",
                               "duration_s": 30}}}
    r.update(over)
    return r


def _engine(rules, **over):
    node = {"enable": True, "rules": list(rules), "step_timeout_s": 60,
            "default_segments": ["D_1"]}
    node.update(over)
    cfg = load_choreography_config({"choreography": node})
    eng = ChoreoEngine(cfg, clock=lambda: NOW[0], priority_mapping={})
    for r in eng.cfg.rules:
        if r.trigger_type == "song" and r.main is not None:
            r.main.hold_for_song = True
    return eng


def _accept_current(eng):
    step = eng.seq["steps"][eng.seq["idx"]]
    return eng.on_switch_result(step["step_id"], True, NOW[0])


def _resume_resident(eng):
    """前置流：文案 resident 在播 → 礼物让位挂起 → 礼物序列播完 → 恢复播报
    画面。返回恢复序列 commands。"""
    d = eng.decide_trigger("dynamic_text", COPY, {})
    assert d["accepted"] is True
    _accept_current(eng)                          # 文案 enter（duration 2）
    NOW[0] += 2.01
    eng.on_tick(NOW[0])                           # enter 播完 → main
    _accept_current(eng)                          # 文案 main（duration 10）
    NOW[0] = 1020.0
    r = eng.decide_trigger("gift", "小心心", {"gift_name": "小心心"})
    assert r["reason"] == "interrupted"
    assert eng.song_suspended is not None
    assert eng.song_suspended["kind"] == "resident"
    assert eng.seq["steps"][eng.seq["idx"]]["segment"] == "X_dt"  # 旧 exit 插播
    _accept_current(eng)                          # 旧 exit（duration 2）
    NOW[0] = 1022.01
    eng.on_tick(NOW[0])                           # 旧 exit 播完 → 礼物 main
    _accept_current(eng)                          # 礼物 main（duration 5）
    NOW[0] = 1027.02
    return eng.on_tick(NOW[0])                    # 礼物播完 → 条件1 恢复


# ------------------------------------------------- 离场步补齐

def test_recovery_sequence_includes_exit_step():
    """t1：resident 恢复序列=[enter 重播, main hold, exit 补齐]。"""
    NOW[0] = 1000.0
    eng = _engine([_dt_rule(), _gift_rule()])
    _resume_resident(eng)
    assert eng.state == "sequence"
    assert eng.seq["rule_id"] == "dt"
    assert [s["name"] for s in eng.seq["steps"]] == ["enter", "main", "exit"]
    assert eng.seq["steps"][2]["segment"] == "X_dt"
    assert eng.seq["steps"][2]["step_id"].startswith("dt:exit#resume")
    assert eng.seq.get("resident") is True


def test_copy_done_terminates_through_exit_to_default():
    """t2：copy_done 精确终结 → 推进 exit 切段（segment_end）→ 播完回默认。"""
    NOW[0] = 1000.0
    eng = _engine([_dt_rule(), _gift_rule()])
    _resume_resident(eng)
    assert eng.seq["steps"][eng.seq["idx"]]["name"] == "enter"
    _accept_current(eng)                          # 恢复 enter（duration 2）
    NOW[0] = 1029.03
    eng.on_tick(NOW[0])                           # enter 播完 → main hold
    assert eng.seq["steps"][eng.seq["idx"]]["name"] == "main"
    _accept_current(eng)
    # 文案尾段回执（结尾锚定+组消费完成）→ 主线循环终结 → 推进 exit
    cmds = eng.on_audio_complete("trends_copywriting", COPY, NOW[0],
                                 group_pending=0)
    assert eng.seq["copy_done"] is True
    sw = [c for c in cmds if isinstance(c, SwitchSegment)]
    # 2026-11-27 立刻模式离场硬切：该规则主线 switch_at=immediate → 恢复
    # 序列 exit 改判 immediate（音频消费后立刻离场，受理=执行无脱节）
    assert len(sw) == 1 and sw[0].segment == "X_dt" and sw[0].at == "immediate"
    assert eng.seq["steps"][eng.seq["idx"]]["name"] == "exit"
    # exit 播完 → 条件1 → 默认轮播
    _accept_current(eng)                          # exit（duration 2）
    NOW[0] = 1029.03 + 2.01
    end_cmds = eng.on_tick(NOW[0])
    assert eng.state == "default"
    assert eng.seq is None
    assert [c for c in end_cmds if isinstance(c, RestoreDefault)]


def test_fuse_expiry_terminates_through_exit_to_default():
    """t3：loop_deadline 保险丝到期 → 主线循环收尾 → 同样经 exit 回默认。"""
    NOW[0] = 1000.0
    eng = _engine([_dt_rule(), _gift_rule()])
    _resume_resident(eng)
    _accept_current(eng)                          # 恢复 enter
    NOW[0] = 1029.03
    eng.on_tick(NOW[0])                           # → main hold
    _accept_current(eng)                          # hold 受理（deadline=素材 10s）
    eng.seq["loop_deadline"] = NOW[0] + 1.0       # 挂起锚到期（保险丝收尾）
    NOW[0] = 1029.03 + 10.01
    cmds = eng.on_tick(NOW[0])                    # main 素材播完→循环门:已过保险丝→推进
    assert eng.seq["steps"][eng.seq["idx"]]["name"] == "exit"
    assert [c for c in cmds if isinstance(c, SwitchSegment)]
    _accept_current(eng)
    NOW[0] = 1029.03 + 10.01 + 2.01
    eng.on_tick(NOW[0])
    assert eng.state == "default"
    assert eng.seq is None


def test_song_recovery_keeps_no_exit():
    """t4：song 恢复序列仍无 exit（离场留给自然结束后的默认轮播）。"""
    NOW[0] = 1000.0
    eng = _engine([_song_rule(), _gift_rule()])
    eng.song_suspended = {"rule_id": "song_a", "deadline": 1200.0,
                          "entered_at": 900.0, "kind": "song",
                          "copy": "", "copy_done": False}
    # 礼物序列在播 → 播完 → 条件1 → song 恢复
    r = eng.decide_trigger("gift", "小心心", {"gift_name": "小心心"})
    assert r["reason"] == "accepted"
    _accept_current(eng)
    NOW[0] = 1005.01
    eng.on_tick(NOW[0])
    assert eng.state == "sequence"
    assert eng.seq["rule_id"] == "song_a"
    assert [s["name"] for s in eng.seq["steps"]] == ["main"]
    assert eng.song_suspended is None
