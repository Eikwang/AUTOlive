"""T13 resident 主线循环（Gap B，2026-11-24 动作编排完善批次）守卫。

用户设计语义：dynamic_text 规则主线在文案任务生命周期内循环播放，直到
文案音频（trends_copywriting 完成回执）消费完才离场；挂起恢复后继续循环
至消费完成/保险丝到期。

覆盖：
- fresh resident 序列主线素材播完 → 重播主线（loop_i、step_id 换代、不置 main_done）
- 回执命中（归一化双向包含）→ copy_done → 推进 exit → 回默认
- 非匹配回执不终结循环
- loop_deadline 保险丝：到期后主线播完正常 exit（防回执丢失永循环）
- enter 期回执到达只置位不跳过 enter
- 恢复序列（挂起切回）hold 继续循环至回执终结 → 跳过再恢复直回默认
- 非 none（tts）主线不循环（防每轮重复入队音频）
"""
from utils.orchestration.choreographer import ChoreoEngine, SwitchSegment
from utils.orchestration.config_model import load_choreography_config

NOW = [1000.0]

COPY = "今天天气真好"


def _dt_rule(rid="dt", with_enter=False, main_dur=10, main_audio=None, **over):
    seq = {}
    if with_enter:
        seq["enter"] = {"segment": "E_" + rid, "duration_s": 3}
    main = {"segment": "M_" + rid, "switch_at": "segment_end", "duration_s": main_dur}
    if main_audio:
        main["audio"] = {"mode": main_audio}
    seq["main"] = main
    seq["exit"] = {"segment": "X_" + rid, "duration_s": 2}
    r = {"rule_id": rid, "trigger_type": "dynamic_text", "match": "",
         "interrupt": "standard", "sequence": seq}
    r.update(over)
    return r


def _gift_rule(rid="ga", main_dur=5, **over):
    r = {"rule_id": rid, "trigger_type": "gift", "match": "小心心",
         "interrupt": "standard",
         "sequence": {"main": {"segment": "G_" + rid, "duration_s": main_dur}}}
    r.update(over)
    return r


def _engine(rules, **over):
    node = {"enable": True, "rules": list(rules), "step_timeout_s": 60,
            "default_segments": ["D_1"]}
    node.update(over)
    cfg = load_choreography_config({"choreography": node})
    return ChoreoEngine(cfg, clock=lambda: NOW[0], priority_mapping={})


def _hit(eng, t, match, data=None):
    return eng.decide_trigger(t, match, data or {})


def _accept_current(eng):
    step = eng.seq["steps"][eng.seq["idx"]]
    return eng.on_switch_result(step["step_id"], True, NOW[0])


def _finish_current(eng):
    NOW[0] = eng.seq["current_deadline"] + 0.01
    return eng.on_tick(NOW[0])


def _receipt(eng, content):
    return eng.on_audio_complete("trends_copywriting", content, NOW[0], None)


# ------------------------------------------------- fresh 序列循环

def test_resident_main_loops_until_copy_done():
    """主线素材播完 → 循环重播（step_id 换代、不置 main_done）。"""
    NOW[0] = 1000.0
    eng = _engine([_dt_rule()])
    d = _hit(eng, "dynamic_text", COPY)
    assert d["accepted"] is True
    assert eng.seq["resident"] is True
    assert eng.seq["resident_copy"] == COPY
    assert eng.seq["loop_deadline"] == 1000.0 + 60
    _accept_current(eng)
    NOW[0] = 1010.01
    cmds = eng.on_tick(NOW[0])              # 素材播完 → 循环分支
    step = eng.seq["steps"][eng.seq["idx"]]
    assert step["step_id"].endswith("#loop1")
    assert step["segment"] == "M_dt"
    assert eng.seq["main_done"] is False    # 不置 main_done（循环中同键忽略语义）
    assert eng.seq["idx"] == 0
    assert any(isinstance(c, SwitchSegment) and c.segment == "M_dt" for c in cmds)
    _accept_current(eng)
    # 回执命中（改写形态容差）→ copy_done → 推进 exit
    cmds = _receipt(eng, "今天天气真好，谢谢支持")
    assert eng.seq["copy_done"] is True
    assert eng.seq["steps"][eng.seq["idx"]]["name"] == "exit"
    _accept_current(eng)
    NOW[0] = eng.seq["current_deadline"] + 0.01
    eng.on_tick(NOW[0])                     # exit 播完 → 回默认
    assert eng.state == "default"


def test_non_matching_receipt_keeps_loop():
    """非匹配回执不终结循环（归一化双向包含不命中）。"""
    NOW[0] = 1000.0
    eng = _engine([_dt_rule()])
    _hit(eng, "dynamic_text", COPY)
    _accept_current(eng)
    cmds = _receipt(eng, "完全无关的内容")
    assert eng.seq.get("copy_done") is False
    assert eng.seq["steps"][eng.seq["idx"]]["name"] == "main"
    _finish_current(eng)                    # 主线播完 → loop1
    assert eng.seq["steps"][eng.seq["idx"]]["step_id"].endswith("#loop1")
    _accept_current(eng)
    _finish_current(eng)                    # loop1 播完 → loop2
    assert eng.seq["steps"][eng.seq["idx"]]["step_id"].endswith("#loop2")


def test_loop_fuse_expires_to_exit():
    """loop_deadline 保险丝：到期后主线播完正常 exit（防回执丢失永循环）。"""
    NOW[0] = 1000.0
    eng = _engine([_dt_rule()])
    _hit(eng, "dynamic_text", COPY)
    _accept_current(eng)
    NOW[0] = 1010.01
    eng.on_tick(NOW[0])                     # loop1（loop_deadline=1060 未到）
    _accept_current(eng)
    NOW[0] = 1061.0                         # 超过保险丝
    eng.on_tick(NOW[0])
    assert eng.seq["main_done"] is True
    assert eng.seq["steps"][eng.seq["idx"]]["name"] == "exit"


def test_receipt_during_enter_marks_done_without_skip():
    """enter 期回执到达只置 copy_done，不跳过 enter（过渡段保完整）。"""
    NOW[0] = 1000.0
    eng = _engine([_dt_rule(with_enter=True)])
    _hit(eng, "dynamic_text", COPY)
    assert eng.seq["steps"][eng.seq["idx"]]["name"] == "enter"
    _accept_current(eng)
    cmds = _receipt(eng, COPY)
    assert cmds == []                       # enter 在播：只置位不推进
    assert eng.seq["copy_done"] is True
    assert eng.seq["steps"][eng.seq["idx"]]["name"] == "enter"
    _finish_current(eng)                    # enter 播完 → main
    assert eng.seq["steps"][eng.seq["idx"]]["name"] == "main"
    _accept_current(eng)
    _finish_current(eng)                    # main 播完 → copy_done → 不循环 → exit
    assert eng.seq["steps"][eng.seq["idx"]]["name"] == "exit"


def test_tts_main_does_not_loop():
    """非 none（tts）主线不循环：防每轮重复入队音频，播完直接 exit。"""
    NOW[0] = 1000.0
    eng = _engine([_dt_rule(main_audio="tts", main_dur=5)])
    d = _hit(eng, "dynamic_text", COPY)
    assert d["accepted"] is True
    _accept_current(eng)                    # main 接受（上限 5s 兜底 deadline）
    NOW[0] = 1005.01
    eng.on_tick(NOW[0])                     # 主线播完 → audio_mode≠none 不循环
    assert eng.seq["main_done"] is True
    assert eng.seq["steps"][eng.seq["idx"]]["name"] == "exit"


def test_switch_failure_does_not_loop():
    """step_accepted 门（2026-11-24 全量回归修复）：切段失败降级跳步不得进
    循环分支——否则「失败→跳步→循环→再失败」零间隔自旋打爆 worker 线程。"""
    NOW[0] = 1000.0
    eng = _engine([_dt_rule()])
    d = _hit(eng, "dynamic_text", COPY)
    assert d["accepted"] is True
    step = eng.seq["steps"][eng.seq["idx"]]
    eng.on_switch_result(step["step_id"], False, NOW[0])   # 切段失败 → 降级跳步
    assert eng.seq["main_done"] is True                    # 不循环：正常推进
    assert eng.seq["steps"][eng.seq["idx"]]["name"] == "exit"
    assert int(eng.seq.get("loop_i") or 0) == 0


# ------------------------------------------------- 恢复序列循环

def test_recovery_hold_loops_until_copy_done():
    """挂起切回后恢复 hold 继续循环至回执终结 → 跳过再恢复直回默认。"""
    NOW[0] = 1000.0
    eng = _engine([_dt_rule(with_enter=True), _gift_rule()])
    _hit(eng, "dynamic_text", COPY)
    _accept_current(eng)                    # enter（deadline=1003）
    NOW[0] = 1003.01
    eng.on_tick(NOW[0])                     # → main
    _accept_current(eng)                    # main（deadline=1013.01）
    # gift 触发 → resident 让位插播（挂起）
    d = _hit(eng, "gift", "小心心", {"gift_name": "小心心", "total_price": 1})
    assert d["accepted"] is True
    susp = eng.song_suspended
    assert susp is not None and susp["kind"] == "resident"
    assert susp["copy"] == COPY
    fuse = susp["deadline"]                 # 挂起时刻 1003.01 + 60 = 1063.01
    # 消费打断者：接受旧 exit → 播完 → 接受主线 → 播完 → 条件1 恢复
    _accept_current(eng)                    # 旧 exit#interrupted（deadline=1005.01）
    NOW[0] = 1005.02
    eng.on_tick(NOW[0])                     # → 打断者主线 G_ga
    assert eng.seq["rule_id"] == "ga"
    _accept_current(eng)                    # G_ga（deadline=1010.02）
    NOW[0] = 1010.03
    eng.on_tick(NOW[0])                     # 打断者序列播完 → 恢复
    assert eng.seq["rule_id"] == "dt"       # 恢复序列
    assert eng.seq["loop_deadline"] == fuse
    names = [s["name"] for s in eng.seq["steps"]]
    assert names == ["enter", "main"]       # 重播 enter + hold
    _accept_current(eng)                    # enter 接受（deadline=1013.03）
    NOW[0] = 1013.04
    eng.on_tick(NOW[0])                     # → hold（main#resume）
    assert "#resume" in eng.seq["steps"][eng.seq["idx"]]["step_id"]
    _accept_current(eng)                    # hold 接受（素材时长 10s 兜底 deadline=1023.04）
    # hold 素材播完（< 保险丝）→ Gap B 循环（不再依赖 re-recover 空转）
    NOW[0] = 1023.05
    eng.on_tick(NOW[0])
    assert eng.seq["steps"][eng.seq["idx"]]["step_id"].endswith("#loop1")
    _accept_current(eng)
    # 回执命中 → copy_done → 推进 → 超出序列 → 回默认
    cmds = _receipt(eng, COPY + "，谢谢")
    assert eng.state == "default"
    from utils.orchestration.choreographer import RestoreDefault
    assert any(isinstance(c, RestoreDefault) for c in cmds)
