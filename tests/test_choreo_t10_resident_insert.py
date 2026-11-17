"""T10 动态文案插播&切回批次守卫（2026-11-16 批次）。

覆盖裁决条款（用户确认的三项设计+状态机安全性）：
- 设计1（resident 判定放宽）：dynamic_text 类型序列触发即注册 resident
  （不再要求规则 priority==999；999⊆dynamic_text 照常 resident）——
  基础标志断言见 test_choreo_t8_closure.test_resident_flag_on_dynamic_text_any_weight
- 设计2（插播播旧离场）：resident 在播期间更高/同权重 standard 触发 →
  先插入并播放 resident 的 exit 步骤（参照 _interrupt_sequence CP-3 做法）
  再切新规则 enter/main/exit；999 特权 resident 保持任意 standard 让位
- 设计3（切回重播进场）：插播规则完成后切回 resident 时重播 enter 步骤
  再进 main/hold 主画面（不直接回默认轮播；恢复后保险丝续挂起锚）
- 设计4（song hold 拒绝）：见 test_choreo_t4_song_suspend（rejected 语义）
- 安全性：resident 挂起期间新一轮 dynamic_text=更新挂起指针（不排队过期
  文案、不重复播 enter 死循环）；恢复路径异常（resident 序列已失效）=
  回退默认轮播不悬挂；低权重触发不插播（排队，不打断文案画面）
- F-B/d22 迁移守卫：挂起期 on_song_started 刷新挂起 deadline（挂起机制
  现仅服务 resident）；挂起超硬上限=强制回默认+告警
"""
from unittest.mock import patch

from utils.orchestration.choreographer import ChoreoEngine
from utils.orchestration.config_model import load_choreography_config

NOW = [1000.0]


def _dt_rule(rid="dt", with_exit=True, with_enter=True, match="", **over):
    """dynamic_text 规则（main 必配；enter/exit 可选——验证插播/重播需要）。"""
    seq = {}
    if with_enter:
        seq["enter"] = {"segment": "E_" + rid, "switch_at": "immediate",
                        "duration_s": 3}
    seq["main"] = {"segment": "M_" + rid, "switch_at": "immediate",
                   "duration_s": 10}
    if with_exit:
        seq["exit"] = {"segment": "X_" + rid, "switch_at": "immediate",
                       "duration_s": 2}
    r = {"rule_id": rid, "trigger_type": "dynamic_text", "match": match,
         "interrupt": "standard", "sequence": seq}
    r.update(over)
    return r


def _gift_rule(rid="g", **over):
    r = {"rule_id": rid, "trigger_type": "gift", "match": "小心心",
         "interrupt": "standard",
         "sequence": {"main": {"segment": "G_" + rid, "switch_at": "immediate",
                               "duration_s": 5}}}
    r.update(over)
    return r


def _song_rule(rid="song_a", **over):
    r = {"rule_id": rid, "trigger_type": "song", "match": "",
         "interrupt": "standard", "pred_version": "new",
         "sequence": {"main": {"segment": "S_" + rid, "switch_at": "immediate",
                               "duration_s": 30}}}
    r.update(over)
    return r


def _engine(rules, **over):
    node = {"enable": True, "rules": list(rules), "step_timeout_s": 60,
            "default_segments": ["D_1"]}
    node.update(over)
    cfg = load_choreography_config({"choreography": node})
    eng = ChoreoEngine(cfg, clock=lambda: NOW[0], priority_mapping={})
    # apply 二段对 song 规则主线自动置位 hold_for_song（同 T4 直置）
    for r in eng.cfg.rules:
        if r.trigger_type == "song" and r.main is not None:
            r.main.hold_for_song = True
    return eng


def _hit(eng, t, match=""):
    data = {"gift_name": match, "total_price": 1} if t == "gift" else {}
    return eng.decide_trigger(t, match, data)


def _accept_current(eng):
    """接受当前步切换（生产=EDTalk 回填；deadline 在接受点建立）。"""
    step = eng.seq["steps"][eng.seq["idx"]]
    return eng.on_switch_result(step["step_id"], True, NOW[0])


def _finish_current(eng):
    """快进越过当前步 deadline 并由 F1 看门狗完成推进。"""
    NOW[0] = eng.seq["current_deadline"] + 0.01
    return eng.on_tick(NOW[0])


def _suspend_resident(eng, dt_rid="dt"):
    """构造 resident 在播并完成一次 gift 插播让位（挂起建立+插播 exit 接受）。"""
    _hit(eng, "dynamic_text", "文案一")
    _accept_current(eng)                       # resident enter 接受
    NOW[0] = 1002.0
    d = _hit(eng, "gift", "小心心")
    assert d["reason"] == "interrupted"
    assert eng.song_suspended["kind"] == "resident"
    assert eng.song_suspended["rule_id"] == dt_rid
    _accept_current(eng)                       # 插播 exit 接受（deadline=1004）
    return d


def _finish_interruptor(eng):
    """快进插播 exit + 打断者 main 完成（恢复判定触发点）。"""
    NOW[0] = 1004.01
    eng.on_tick(NOW[0])                        # 插播 exit 完成 → 打断者 main 切换
    _accept_current(eng)                       # 打断者 main 接受（deadline=1009.01）
    NOW[0] = 1009.02
    return eng.on_tick(NOW[0])                 # main 完成 → 恢复判定


# ------------------------------------------------- 设计2：插播先播旧 exit

def test_plug_inserts_old_exit_before_new_sequence():
    """resident 在播 + 更高权重（gift 30 > dt 15）触发：旧 exit 被插入播放，
    再切新规则序列（原 d9 硬切不播 exit 行为改写）。"""
    NOW[0] = 1000.0
    eng = _engine([_dt_rule(), _gift_rule()])
    d = _suspend_resident(eng, "dt")
    assert eng.seq["rule_id"] == "g"
    steps = eng.seq["steps"]
    assert steps[0]["step_id"].startswith("dt:exit#interrupted")
    assert steps[0]["segment"] == "X_dt"       # 旧规则离场段
    assert [s["name"] for s in steps[1:]] == ["main"]  # gift 规则（单 main）
    assert eng.counters["dt"] == 0             # D-4 清零（e9 恢复=基础档前提）
    # ⑦-B（2026-11-17 动作编排审查批次）：挂起登记文案对齐锚点（copy=触发
    # match_value，copy_done 挂起期回执先行登记）
    assert eng.song_suspended == {"rule_id": "dt", "deadline": 1062.0,
                                  "entered_at": 1002.0, "kind": "resident",
                                  "copy": "文案一", "copy_done": False}
    # 不撤销旧序列音轨（挂起恢复语义——resident 步骤纯画面为主，残留音频
    # 完成回调因 inflight 重置被静默忽略）
    assert [c for c in d["commands"] if type(c).__name__ == "CancelAudio"] == []
    # 插播 exit 播完 → 打断者 main
    NOW[0] = 1004.01
    eng.on_tick(NOW[0])
    assert eng.seq["steps"][eng.seq["idx"]]["name"] == "main"


def test_equal_weight_dynamic_text_yields_with_plug():
    """同权重（dt 15 × dt 15）dynamic_text 触发 → 让位插播（new_p >= cur_p）。"""
    NOW[0] = 1000.0
    eng = _engine([_dt_rule("dt1", match="一"), _dt_rule("dt2", match="二")])
    _hit(eng, "dynamic_text", "文案一")
    _accept_current(eng)
    NOW[0] = 1002.0
    r = _hit(eng, "dynamic_text", "文案二")
    assert r["reason"] == "interrupted"
    assert eng.seq["rule_id"] == "dt2"
    assert eng.seq["steps"][0]["step_id"].startswith("dt1:exit#interrupted")
    assert eng.song_suspended["rule_id"] == "dt1"


def test_999_resident_keeps_yield_privilege():
    """999 特权 resident 保持任意 standard 让位（权重比较失效，T8 d13 语义）。"""
    NOW[0] = 1000.0
    eng = _engine([_dt_rule(), _gift_rule()],
                  over={"arbitration_weights": {"dynamic_text": 999,
                                                "gift": 30, "song": 20,
                                                "keyword": 10}})
    _hit(eng, "dynamic_text", "文案一")
    _accept_current(eng)
    NOW[0] = 1002.0
    r = _hit(eng, "gift", "小心心")
    assert r["reason"] == "interrupted"        # gift 30 < 999 仍让位（特权保持）
    assert eng.seq["rule_id"] == "g"
    assert eng.seq["steps"][0]["step_id"].startswith("dt:exit#interrupted")


def test_low_weight_trigger_queues_without_plug():
    """低权重触发（keyword 10 < dt 15）不插播不打断文案画面 → 权重门排队。"""
    NOW[0] = 1000.0
    kw = {"rule_id": "k", "trigger_type": "keyword", "match": "任意",
          "interrupt": "standard",
          "sequence": {"main": {"segment": "K_词", "switch_at": "immediate",
                                "duration_s": 5}}}
    eng = _engine([_dt_rule(), kw])
    _hit(eng, "dynamic_text", "文案一")
    _accept_current(eng)
    NOW[0] = 1002.0
    r = _hit(eng, "keyword", "任意词")
    assert r["reason"] == "queued"
    assert eng.seq["rule_id"] == "dt"
    assert eng.seq["steps"][0]["name"] == "enter"  # 序列零扰动（无插播 exit）
    assert eng.song_suspended is None


# ------------------------------------------------- 设计3：切回重播 enter

def test_recovery_replays_enter_then_hold_not_default():
    """插播规则完成 → 重播 resident enter 再进 main/hold 主画面（不直接回
    默认轮播）；恢复序列 deadline 续挂起锚；保险丝走完回默认。"""
    NOW[0] = 1000.0
    eng = _engine([_dt_rule(), _gift_rule()])
    _suspend_resident(eng, "dt")
    cmds = _finish_interruptor(eng)
    assert eng.state == "sequence"
    assert eng.seq["rule_id"] == "dt"
    assert eng.song_suspended is None
    assert not [c for c in cmds if type(c).__name__ == "RestoreDefault"]
    steps = eng.seq["steps"]
    assert [s["name"] for s in steps] == ["enter", "main"]      # 重播 enter + hold
    assert steps[0]["step_id"].startswith("dt:enter#resume")
    assert steps[0]["segment"] == "E_dt"                        # 基础档 enter
    assert steps[1]["step_id"].startswith("dt:main#resume")
    assert steps[1]["hold_for_song"] is False
    assert eng.seq.get("resident") is True
    assert eng.counters["dt"] == 0                              # e9 基础档
    assert eng.seq["current_deadline"] == 1062.0                # deadline 续挂起锚
    # enter 重播完成 → 进 hold 主画面（重播仅恢复点单次）
    _accept_current(eng)
    NOW[0] = 1012.03
    eng.on_tick(NOW[0])
    assert eng.seq["steps"][eng.seq["idx"]]["step_id"].startswith("dt:main#resume")
    # hold 播完（时长到点）→ 既有条件1 回默认轮播（文案画面自然结束）
    _accept_current(eng)
    NOW[0] = 1100.0
    cmds = eng.on_tick(NOW[0])
    assert eng.state == "default"
    assert [c for c in cmds if type(c).__name__ == "RestoreDefault"]


def test_recovery_without_enter_step_goes_straight_to_hold():
    """规则无 enter 步（可选过渡缺省）→ 恢复直接回 hold 主画面（不悬挂）。"""
    NOW[0] = 1000.0
    eng = _engine([_dt_rule("dt", with_exit=False, with_enter=False),
                   _gift_rule()])
    _hit(eng, "dynamic_text", "文案一")
    _accept_current(eng)                       # dt main 接受
    NOW[0] = 1002.0
    _hit(eng, "gift", "小心心")                # 插播 steps[-1]=main 段（CP-3 同构）
    _accept_current(eng)                       # 插播段接受（deadline=1002+10）
    NOW[0] = 1012.01
    eng.on_tick(NOW[0])                        # 插播段完成 → g main 切换
    _accept_current(eng)                       # g main 接受（deadline=1017.01）
    NOW[0] = 1017.02
    cmds = eng.on_tick(NOW[0])                 # g main 完成 → 恢复判定
    assert eng.state == "sequence"
    assert eng.seq["rule_id"] == "dt"
    assert [s["name"] for s in eng.seq["steps"]] == ["main"]    # 无 enter 可重播
    assert eng.seq["steps"][0]["step_id"].startswith("dt:main#resume")
    assert not [c for c in cmds if type(c).__name__ == "RestoreDefault"]


# ------------------------------------------------- 安全性：失效回退/挂起更新

def test_recovery_falls_back_to_default_when_rule_gone():
    """恢复路径异常（resident 序列已失效=规则被删/换装）→ 回退默认轮播，
    不悬挂。"""
    NOW[0] = 1000.0
    eng = _engine([_dt_rule(), _gift_rule()])
    _suspend_resident(eng, "dt")
    eng.cfg.rules = [r for r in eng.cfg.rules if r.trigger_type != "dynamic_text"]
    cmds = _finish_interruptor(eng)
    assert eng.state == "default"              # 回退默认轮播，不悬挂
    assert eng.song_suspended is None
    assert [c for c in cmds if type(c).__name__ == "RestoreDefault"]


def test_suspended_resident_updated_by_new_copy():
    """resident 挂起期间新一轮 dynamic_text 触发=更新挂起 resident 指针
    （最新文案覆盖旧文案，不排队过期文案）；打断者完成后按更新后的 resident
    恢复（重播 enter 单次，不死循环）。"""
    NOW[0] = 1000.0
    eng = _engine([_dt_rule("dt1", match="一"), _dt_rule("dt2", match="二"),
                   _gift_rule()])
    _hit(eng, "dynamic_text", "文案一")
    _accept_current(eng)
    NOW[0] = 1002.0
    _hit(eng, "gift", "小心心")                # 挂起 dt1（deadline=1062）
    _accept_current(eng)                       # 插播 exit 接受
    NOW[0] = 1005.0
    r = _hit(eng, "dynamic_text", "文案二")
    assert r["reason"] == "resident_updated"
    assert r["accepted"] is True               # 触发被承接（切回延后）
    assert r["takeover"] is False              # 编排只管画面，音频照常
    assert eng.seq["rule_id"] == "g"           # 打断者序列不受扰动
    susp = eng.song_suspended
    assert susp["rule_id"] == "dt2"            # 挂起指针更新为最新文案
    assert susp["deadline"] == 1065.0          # 保险丝刷新（1005+60）
    assert eng.counters.get("dt2", 0) == 0     # 新文案=基础档
    cmds = _finish_interruptor(eng)
    assert eng.state == "sequence"
    assert eng.seq["rule_id"] == "dt2"         # 按更新后的 resident 恢复
    assert eng.seq["steps"][0]["step_id"].startswith("dt2:enter#resume")
    assert eng.seq["current_deadline"] == 1065.0
    assert not [c for c in cmds if type(c).__name__ == "RestoreDefault"]


def test_recovery_fuse_expiry_goes_default():
    """挂起保险丝到期（播报已结束的诚实降级）→ 打断者完成时直接回默认。"""
    NOW[0] = 1000.0
    eng = _engine([_dt_rule(), _gift_rule()])
    _suspend_resident(eng, "dt")               # deadline=1062
    eng.cfg.rules = [r for r in eng.cfg.rules if r.trigger_type != "dynamic_text"]
    NOW[0] = 1100.0                            # 快进至保险丝过点后打断者才完成
    eng.on_tick(NOW[0])                        # 插播 exit 超时完成
    _accept_current(eng)                       # g main 接受
    NOW[0] = 1105.01
    cmds = eng.on_tick(NOW[0])
    assert eng.state == "default"              # 播报已结束 → 直接回默认
    assert eng.song_suspended is None
    assert [c for c in cmds if type(c).__name__ == "RestoreDefault"]


# ------------------------------------------------- F-B/d22 迁移守卫（挂起仅 resident）

def test_fb_song_started_refreshes_suspended_resident_deadline():
    """F-B（迁移）：挂起期 on_song_started 仍刷新挂起 deadline（自校正不断链
    ——挂起机制现仅服务 resident，分派不区分 kind）。"""
    NOW[0] = 1000.0
    eng = _engine([_dt_rule(), _song_rule()])
    _hit(eng, "dynamic_text", "文案一")
    _accept_current(eng)
    NOW[0] = 1002.0
    d = _hit(eng, "song", "歌名")              # song 20 >= dt 15 → 让位插播
    assert d["reason"] == "interrupted"
    assert eng.song_suspended["kind"] == "resident"
    _accept_current(eng)                       # 插播 exit 接受（song main 切换）
    NOW[0] = 1003.0
    cmds = eng.on_song_started("test_song", 180.0, NOW[0])
    assert eng.song_suspended["deadline"] == 1183.0  # now+180（F-B 挂起分派刷新）
    assert eng.seq["rule_id"] == "song_a"      # seq 未被扰动
    assert any("F-B" in c.msg for c in cmds if hasattr(c, "msg"))


def test_d22_watchdog_force_default_on_resident_cap_exceeded():
    """d22 硬上限（迁移）：挂起超 3600s 强制回默认+告警（挂起锚异常兜底）。"""
    NOW[0] = 1000.0
    eng = _engine([_dt_rule(), _gift_rule()])
    _suspend_resident(eng, "dt")
    eng.song_suspended["entered_at"] = NOW[0] - 4000.0  # 超 3600s 硬上限
    with patch("utils.orchestration.choreographer.alarm_notifier") as alarm:
        cmds = _finish_interruptor(eng)
        assert eng.state == "default"          # 强制回默认，不悬挂
        assert eng.song_suspended is None
        assert alarm.alarm_error.called
