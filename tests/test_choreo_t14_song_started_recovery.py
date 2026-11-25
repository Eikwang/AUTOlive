"""T14 歌曲起播画面恢复 + song hold deadline 并行延长守卫
（2026-11-24 修复批次，用户实测两问题根修）。

根因回归背景：
- RC1：resident 让位点歌场景 song_suspended 在整个 song hold 期间恒非 None
  （仅序列收尾清空）——on_song_started 挂起分支此前只刷新挂起 deadline 并
  return，song hold 主线 deadline 被饿死在 step_timeout 保险丝：歌曲时长>
  保险丝必然 mid-song 提前走完序列（唱歌期间画面漂移/歌曲起播前走完）。
- RC2：挂起清空后歌曲才起播（文案播完回默认/恢复文案 hold 在播）——此前
  直接 return 无任何画面动作，唱歌在错误画面开始。

覆盖：
- t1 挂起期切片起播：挂起 deadline 刷新 + song hold 主线 deadline 并行延长
  （旧保险丝时点 on_tick 不推进 main）
- t2 song hold 走完恢复文案 hold 后歌曲起播 → 重建 resident 挂起 + song hold
  直切（歌毕按挂起恢复切回文案画面）
- t3 default 态歌曲起播命中 song 规则 → 新建 song hold 序列（state=sequence）
- t4 幂等边界：无命中/remaining≤0/None/总开关 off 零动作
- t5 susp kind=song 防御分支仅刷新挂起 deadline（无 seq 动作）
"""
from utils.orchestration.choreographer import ChoreoEngine, SwitchSegment
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


def _gift_rule(rid="g1", main_dur=5, **over):
    r = {"rule_id": rid, "trigger_type": "gift", "match": "小心心",
         "interrupt": "standard", "pred_version": "new",
         "sequence": {"main": {"segment": "G_" + rid, "switch_at": "immediate",
                               "duration_s": main_dur}}}
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


def _start_resident_then_song(eng):
    """前置流：文案 resident 在播 → 点歌让位挂起 → song hold 受理（保险丝 armed）。

    返回 song hold 主线的保险丝到期时刻。"""
    d = eng.decide_trigger("dynamic_text", COPY, {})
    assert d["accepted"] is True
    _accept_current(eng)                        # 文案 main（duration 10）
    NOW[0] = 1005.0
    r = eng.decide_trigger("song", "晴天", {})
    assert r["reason"] == "interrupted"
    assert eng.song_suspended is not None
    assert eng.song_suspended["kind"] == "resident"
    # song 序列首步=旧 exit 插播（resident 让位插播离场段）
    assert eng.seq["steps"][eng.seq["idx"]]["segment"] == "X_dt"
    _accept_current(eng)                        # 旧 exit（duration 2）
    NOW[0] = 1008.0
    eng.on_tick(NOW[0])                         # exit 播完 → song main hold
    assert eng.seq["steps"][eng.seq["idx"]]["name"] == "main"
    _accept_current(eng)                        # hold 受理 → seq.hold_for_song 回填
    assert eng.seq.get("hold_for_song") is True
    return eng.seq["current_deadline"]          # 保险丝=受理时刻+60s


# ------------------------------------------------- RC1 并行延长

def test_suspended_song_start_extends_hold_deadline_parallel():
    """挂起期歌曲切片起播：挂起 deadline 刷新 + song hold deadline 并行延长。"""
    NOW[0] = 1000.0
    eng = _engine([_dt_rule(), _song_rule()])
    fuse = _start_resident_then_song(eng)       # 保险丝≈1008+60=1068
    NOW[0] = 1050.0                             # 歌曲在保险丝到期前起播
    cmds = eng.on_song_started("晴天", 240.0, NOW[0])
    # 2026-11-24 歌毕闪帧根修：挂起窗口=歌曲结束+歌后备窗（step_timeout_s=60）
    assert eng.song_suspended["deadline"] == 1050.0 + 240.0 + 60.0
    assert eng.seq["current_deadline"] == 1050.0 + 240.0   # RC1 并行延长
    assert eng.seq["current_deadline"] > fuse
    NOW[0] = fuse + 0.01                        # 旧保险丝时点：不得推进
    eng.on_tick(NOW[0])
    assert eng.seq["steps"][eng.seq["idx"]]["name"] == "main"
    assert eng.seq["main_done"] is False
    assert eng.seq["idx"] == eng.seq["steps"].index(
        eng.seq["steps"][eng.seq["idx"]])


def test_song_end_after_rc1_extension_restores_resident():
    """歌毕（deadline 到期）→ song hold 走完 → 恢复挂起文案 hold
    （2026-11-24 歌毕闪帧根修：挂起窗口=歌曲结束+歌后备窗，歌毕 exit 播完时
    窗口仍有余量 → 按 d13 语义恢复文案画面；旧口径窗口恰被歌曲耗尽，恢复
    被 now<deadline 拒走默认=歌毕直接回默认）。恢复后兜底窗到期 → 回默认。"""
    NOW[0] = 1000.0
    eng = _engine([_dt_rule(), _song_rule()])
    _start_resident_then_song(eng)
    NOW[0] = 1050.0
    eng.on_song_started("晴天", 240.0, NOW[0])   # seq deadline=1290/susp=1350
    NOW[0] = 1290.01
    eng.on_tick(NOW[0])                         # 歌毕 → main_done → 收尾
    # 挂起恢复：文案 hold 重建（画面回文案规则，非默认）
    assert eng.state == "sequence"
    assert eng.seq["rule_id"] == "dt"
    assert eng.seq.get("resident") is True
    assert eng.song_suspended is None           # 挂起已消费（转为恢复 hold）
    _accept_current(eng)
    NOW[0] = 1290.01 + 10.01
    eng.on_tick(NOW[0])                         # 恢复 hold 素材播完 → Gap B 循环
    assert eng.seq["steps"][eng.seq["idx"]]["name"] == "main"
    NOW[0] = 1350.01                            # 兜底窗（歌后备）到期 → 默认
    eng.on_tick(NOW[0])
    assert eng.state == "default"
    assert eng.seq is None


# ------------------------------------------------- RC2 起播恢复

def test_song_started_recovers_resident_hold_to_song_hold():
    """恢复文案 hold 在播时歌曲起播 → 重建 resident 挂起 + song hold 直切。

    现实路径：文案在播→礼物让位挂起→礼物序列播完→恢复文案 hold（susp 已
    消费）→此前期点歌的歌曲切片此刻才起播→RC2 挂起文案切 song hold。"""
    NOW[0] = 1000.0
    eng = _engine([_dt_rule(), _gift_rule(), _song_rule()])
    d = eng.decide_trigger("dynamic_text", COPY, {})
    assert d["accepted"] is True
    _accept_current(eng)                        # 文案 main（duration 10）
    NOW[0] = 1002.0
    r = eng.decide_trigger("gift", "小心心", {"gift_name": "小心心"})
    assert r["reason"] == "interrupted"
    assert eng.song_suspended["kind"] == "resident"
    _accept_current(eng)                        # 旧 exit 插播（duration 2）
    NOW[0] = 1004.01
    eng.on_tick(NOW[0])                         # exit 播完 → 礼物 main
    _accept_current(eng)                        # 礼物 main（duration 5）
    NOW[0] = 1009.02
    eng.on_tick(NOW[0])                         # 礼物播完 → 挂起恢复文案 hold
    assert eng.seq["rule_id"] == "dt"
    assert eng.seq.get("resident") is True
    assert eng.song_suspended is None           # 挂起已消费（打断者=礼物）
    _accept_current(eng)                        # 恢复 hold 受理
    NOW[0] = 1020.0
    cmds = eng.on_song_started("晴天", 220.0, NOW[0])   # 歌曲此刻才起播
    # RC2：重建 resident 挂起（文案上下文保留）+ song hold 直切
    assert eng.song_suspended is not None
    assert eng.song_suspended["kind"] == "resident"
    assert eng.song_suspended["rule_id"] == "dt"
    assert eng.song_suspended["copy"] == COPY
    assert eng.song_suspended["copy_done"] is False
    assert eng.seq["rule_id"] == "song_a"
    assert eng.seq.get("hold_for_song") is True
    assert eng.seq["current_deadline"] == 1020.0 + 220.0
    # 歌毕闪帧根修：RC2 初始挂起窗口=歌曲结束+歌后备窗（60s）
    assert eng.song_suspended["deadline"] == 1020.0 + 220.0 + 60.0
    assert eng.counters.get("song_a") == 1
    sw = [c for c in cmds if isinstance(c, SwitchSegment)]
    assert len(sw) == 1 and sw[0].segment == "S_主" and sw[0].at == "immediate"
    # 歌毕 → 恢复挂起文案 hold（窗口有余量）→ 兜底窗到期 → 回默认
    _accept_current(eng)
    NOW[0] = 1020.0 + 220.0 + 0.01
    eng.on_tick(NOW[0])
    assert eng.state == "sequence"
    assert eng.seq["rule_id"] == "dt"
    assert eng.seq.get("resident") is True
    assert eng.song_suspended is None
    _accept_current(eng)
    NOW[0] = 1020.0 + 220.0 + 60.0 + 0.01       # 歌后备窗到期
    eng.on_tick(NOW[0])
    assert eng.state == "default"


def test_song_started_in_default_state_starts_song_hold():
    """default 态歌曲起播命中 song 规则 → 新建 song hold 序列（state=sequence）。"""
    NOW[0] = 1000.0
    eng = _engine([_song_rule()])
    cmds = eng.on_song_started("晴天", 200.0, NOW[0])
    assert eng.state == "sequence"
    assert eng.seq is not None
    assert eng.seq["rule_id"] == "song_a"
    assert eng.seq.get("hold_for_song") is True
    assert eng.seq["current_deadline"] == 1200.0
    assert eng.counters == {"song_a": 1}
    sw = [c for c in cmds if isinstance(c, SwitchSegment)]
    assert len(sw) == 1 and sw[0].segment == "S_主" and sw[0].at == "immediate"


# ------------------------------------------------- 幂等边界

def test_song_started_no_match_and_bad_remaining_idempotent():
    """无命中/remaining≤0/None=default 态零动作。"""
    NOW[0] = 1000.0
    eng = _engine([_song_rule("song_a", "晴天")])
    assert eng.on_song_started("无名歌", 200.0, NOW[0]) == []
    assert eng.on_song_started("晴天", 0, NOW[0]) == []
    assert eng.on_song_started("晴天", -5, NOW[0]) == []
    assert eng.on_song_started("晴天", None, NOW[0]) == []
    assert eng.state == "default"
    assert eng.seq is None


def test_song_started_arbitration_off_no_action_in_default():
    """总开关 off：default 态歌曲起播零动作（回退现行为）。"""
    NOW[0] = 1000.0
    eng = _engine([_song_rule()], arbitration_enable=False)
    cmds = eng.on_song_started("晴天", 200.0, NOW[0])
    assert eng.state == "default"
    assert eng.seq is None
    assert not [c for c in cmds if isinstance(c, SwitchSegment)]


def test_song_started_susp_kind_song_defensive_refresh_only():
    """susp kind=song 防御分支：仅刷新挂起 deadline，无 seq 动作。"""
    NOW[0] = 1000.0
    eng = _engine([_song_rule()])
    eng.song_suspended = {"rule_id": "song_a", "deadline": 1200.0,
                          "entered_at": 900.0, "kind": "song",
                          "copy": "", "copy_done": False}
    cmds = eng.on_song_started("晴天", 180.0, NOW[0])
    # 歌毕闪帧根修：窗口=歌曲结束+歌后备窗（step_timeout_s=60）
    assert eng.song_suspended["deadline"] == 1000.0 + 180.0 + 60.0
    assert eng.state == "default"
    assert eng.seq is None
    assert not [c for c in cmds if isinstance(c, SwitchSegment)]
