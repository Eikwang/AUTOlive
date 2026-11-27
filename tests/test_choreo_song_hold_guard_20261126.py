"""歌曲画面 hold 收尾防线（A）+ 保险丝预武装（B）守卫
（2026-11-26 画面来回跳根修，log1.txt 实测回归）。

根因回归背景：on_song_started（唯一 hold 延长权威信号）只在第一个 song
切片 push_full 受理时发出——被 LLM 回答组消费积压（~58s）+前奏静音（31s）
推迟到点歌接管后 91s；而接管序列 main hold 只有 step_timeout_s=60s 保险丝，
序列 66s 走完 → 条件1 只检查挂起旧文案 deadline（也过点）→ 回默认（歌未
起播）→ 25s 后通知到达 RC2 直切回唱=画面来回跳。

覆盖：
- B1 接管带 song_total_s → main hold 受理 deadline=max(保险丝, 接管锚+总时长)
- B2 未带 song_total_s → 保险丝原语义（60s）
- B3 default 态 song 触发 → _start_sequence 同样预武装
- A1 序列播完时音频侧歌曲活跃 → 直切 song hold（不回默认、不恢复挂起，
  挂起锚保留供歌毕 d2/d13 恢复）
- A2 歌毕（provider 返回 None）→ 既有收尾语义回默认
- A3 provider 未注册/规则未命中 → 既有收尾语义回默认（零变化）
"""
from utils.orchestration.choreographer import (
    ChoreoEngine, SwitchSegment, RestoreDefault)
from utils.orchestration.config_model import load_choreography_config

NOW = [1000.0]

COPY = "今天天气真好"


def _dt_rule(rid="dt", main_dur=10, **over):
    r = {"rule_id": rid, "trigger_type": "dynamic_text", "match": "",
         "interrupt": "standard", "pred_version": "new",
         "sequence": {
             "main": {"segment": "M_" + rid, "switch_at": "immediate",
                      "duration_s": main_dur},
             "exit": {"segment": "X_" + rid, "switch_at": "immediate",
                      "duration_s": 2},
         }}
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


def _start_resident_then_song(eng, song_data):
    """前置流：文案 resident 在播 → 点歌让位挂起 → 旧 exit 插播 → song main
    hold 受理（保险丝 armed）。返回受理时刻。"""
    d = eng.decide_trigger("dynamic_text", COPY, {})
    assert d["accepted"] is True
    _accept_current(eng)                        # 文案 main（duration 10）
    NOW[0] = 1005.0
    r = eng.decide_trigger("song", "晴天", song_data)
    assert r["reason"] == "interrupted"
    assert eng.song_suspended is not None
    assert eng.song_suspended["kind"] == "resident"
    assert eng.seq["steps"][eng.seq["idx"]]["segment"] == "X_dt"
    _accept_current(eng)                        # 旧 exit 插播（duration 2）
    NOW[0] = 1008.01
    eng.on_tick(NOW[0])                         # exit 播完 → song main hold
    assert eng.seq["steps"][eng.seq["idx"]]["name"] == "main"
    _accept_current(eng)                        # hold 受理
    return NOW[0]


# ------------------------------------------------- B 预武装

def test_b_floor_arms_takeover_deadline():
    """B1：接管携带 song_total_s → hold 受理 deadline=接管锚+总时长（>保险丝）。"""
    NOW[0] = 1000.0
    eng = _engine([_dt_rule(), _song_rule()])
    _start_resident_then_song(eng, {"song_total_s": 210.0})
    # 受理时刻 1008.01；保险丝=1068.01；下限=接管锚 1005+210=1215
    assert eng.seq["current_deadline"] == 1005.0 + 210.0
    assert eng.seq["song_deadline_floor"] == 1005.0 + 210.0
    # 旧保险丝时点不推进（歌曲未起播、切片积压中）
    NOW[0] = 1068.02
    eng.on_tick(NOW[0])
    assert eng.seq["main_done"] is False
    assert eng.seq["steps"][eng.seq["idx"]]["name"] == "main"


def test_b_no_total_keeps_fuse():
    """B2：未携带 song_total_s（时长不可测）→ 保险丝原语义。"""
    NOW[0] = 1000.0
    eng = _engine([_dt_rule(), _song_rule()])
    _start_resident_then_song(eng, {})
    assert eng.seq["song_deadline_floor"] is None
    assert eng.seq["current_deadline"] == 1008.01 + 60.0


def test_b_floor_in_default_state_start():
    """B3：default 态 song 触发 → _start_sequence 同样预武装下限。"""
    NOW[0] = 1000.0
    eng = _engine([_song_rule()])
    r = eng.decide_trigger("song", "晴天", {"song_total_s": 210.0})
    assert r["reason"] == "accepted"
    assert eng.seq["song_deadline_floor"] == 1000.0 + 210.0
    _accept_current(eng)
    assert eng.seq["current_deadline"] == 1000.0 + 210.0  # max(保险丝 1060, 下限 1210)


# ------------------------------------------------- A 收尾防线

def test_a_finish_while_song_alive_switches_to_song_hold():
    """A1：保险丝先到期序列播完，音频侧歌曲仍活跃 → 直切 song hold 不回默认。"""
    NOW[0] = 1000.0
    eng = _engine([_dt_rule(), _song_rule()])
    _start_resident_then_song(eng, {})           # 保险丝 1068.01
    eng.song_alive_provider = lambda: {"song_name": "晴天", "remaining_s": 150.0}
    NOW[0] = 1068.02
    cmds = eng.on_tick(NOW[0])                   # main 保险丝到期 → 条件1
    # 直切 song hold（⑨-B 同源语义），不回默认
    assert eng.state == "sequence"
    assert eng.seq["rule_id"] == "song_a"
    assert eng.seq.get("hold_for_song") is True
    assert eng.seq["current_deadline"] == 1068.02 + 150.0
    assert eng.song_suspended is not None        # 挂起锚保留（歌毕 d2/d13 恢复）
    sw = [c for c in cmds if isinstance(c, SwitchSegment)]
    assert len(sw) == 1 and sw[0].segment == "S_主" and sw[0].at == "immediate"
    assert not [c for c in cmds if isinstance(c, RestoreDefault)]
    # 歌毕（provider 返回 None）→ 挂起锚（deadline=1005+60=1065 已过点）
    # →「挂起播报已结束」→ 既有语义回默认
    eng.song_alive_provider = lambda: None
    _accept_current(eng)                         # 新 hold 主线受理
    NOW[0] = 1068.02 + 150.01
    eng.on_tick(NOW[0])
    assert eng.state == "default"
    assert eng.seq is None
    assert eng.song_suspended is None


def test_a_no_provider_falls_back_to_default():
    """A3a：provider 未注册（降级/测试）→ 既有收尾语义回默认（零变化）。"""
    NOW[0] = 1000.0
    eng = _engine([_dt_rule(), _song_rule()])
    _start_resident_then_song(eng, {})
    NOW[0] = 1068.02
    cmds = eng.on_tick(NOW[0])
    assert eng.state == "default"
    assert eng.seq is None
    assert [c for c in cmds if isinstance(c, RestoreDefault)]


def test_a_alive_but_rule_missed_falls_back_to_default():
    """A3b：歌曲活跃但规则未命中 → 落回既有收尾语义回默认。"""
    NOW[0] = 1000.0
    eng = _engine([_dt_rule(), _song_rule()])
    _start_resident_then_song(eng, {})
    eng.song_alive_provider = lambda: {"song_name": "无名歌", "remaining_s": 150.0}
    NOW[0] = 1068.02
    cmds = eng.on_tick(NOW[0])
    assert eng.state == "default"
    assert eng.seq is None
    assert [c for c in cmds if isinstance(c, RestoreDefault)]


def test_a_provider_exception_or_bad_shape_idempotent():
    """A3c：provider 抛异常/remaining≤0 → 规整为 None 走既有语义（回默认）。"""
    NOW[0] = 1000.0
    eng = _engine([_dt_rule(), _song_rule()])
    _start_resident_then_song(eng, {})           # 保险丝 1068.01

    def _boom():
        raise RuntimeError("probe failed")
    eng.song_alive_provider = _boom
    NOW[0] = 1068.02
    eng.on_tick(NOW[0])                          # 条件1：provider 异常→None
    assert eng.state == "default"
    assert eng.seq is None

    eng2 = _engine([_dt_rule(), _song_rule()])
    _start_resident_then_song(eng2, {})
    eng2.song_alive_provider = lambda: {"song_name": "晴天", "remaining_s": -3.0}
    NOW[0] = 1068.02
    eng2.on_tick(NOW[0])                         # remaining≤0→None→既有语义
    assert eng2.state == "default"
