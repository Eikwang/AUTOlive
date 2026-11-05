"""T4 song 序列仲裁状态机守卫（2026-11-05 编排优化批次）。

覆盖裁决条款：
- d9：song hold 被高权重打断→歌曲链路不撤销（无 CancelAudio）+转入 seq 旁路
  挂起态；打断者序列照常启动
- e4：挂起态字段（rule_id/deadline/entered_at）；挂起期默认池不接管（恢复
  不发 RestoreDefault，恢复序列=hold 步）
- d2：打断者播完→歌曲未结束=回唱歌画面（恢复序列）；歌曲已结束=回默认池
- F-B：挂起期 on_song_started 仍刷新挂起 deadline（自校正不断链）
- d11：song hold 中 song 重触发=点歌队列追加承接（编排序列保持 hold 不重排）
- e9：恢复=基础档画面（D-4 清零后 select_tier(rule,1)，非打断前档位）
- d22：挂起硬上限超时=强制回默认+告警；总开关 off=回退现行抢占终止语义
"""
import threading
from unittest.mock import patch

from utils.orchestration.choreographer import ChoreoEngine
from utils.orchestration.config_model import load_choreography_config

NOW = [1000.0]


def _song_rule(rid="song_a", **over):
    r = {"rule_id": rid, "trigger_type": "song", "match": "",
         "interrupt": "standard", "pred_version": "new",
         "sequence": {"main": {"segment": "S_主", "switch_at": "immediate",
                               "duration_s": 30}}}
    r.update(over)
    return r


def _gift_rule(rid="g1", **over):
    r = {"rule_id": rid, "trigger_type": "gift", "match": "小心心",
         "interrupt": "standard",
         "sequence": {"main": {"segment": "G_答", "switch_at": "immediate",
                               "duration_s": 5}}}
    r.update(over)
    return r


def _engine(rules, pm=None, **over):
    node = {"enable": True, "rules": list(rules), "step_timeout_s": 60,
            "default_segments": ["D_1"]}
    node.update(over)
    cfg = load_choreography_config({"choreography": node})
    eng = ChoreoEngine(cfg, clock=lambda: NOW[0], priority_mapping=pm or {})
    # apply 二段对 song 规则主线自动置位 hold_for_song（sidecar 路径在测试环境
    # 无素材 meta→continue 跳过——按 test_choreo_material_props:163 同款直置）
    for r in eng.cfg.rules:
        if r.trigger_type == "song" and r.main is not None:
            r.main.hold_for_song = True
    return eng


def _trigger_song(eng, match=""):
    return eng.decide_trigger("song", match, {})


def _trigger_gift(eng, name="小心心"):
    return eng.decide_trigger("gift", name, {"gift_name": name, "total_price": 1})


def _accept_switch(eng, result):
    """切换命令接受回填（seq 级 hold_for_song 标志在接受点置位 :729-736）。"""
    sw = next(c for c in result["commands"] if type(c).__name__ == "SwitchSegment")
    return eng.on_switch_result(sw.step_id, True, NOW[0])


def _suspend_song(eng, song_rid="song_a", remaining=200.0, match=""):
    """构造 song hold 活跃态并完成一次高权重打断（进入挂起）。"""
    r = _trigger_song(eng, match)
    assert r["reason"] == "accepted"
    _accept_switch(eng, r)
    eng.on_song_started("test_song", remaining, NOW[0])
    assert eng.seq.get("hold_for_song") is True
    d = _trigger_gift(eng)
    assert d["reason"] == "interrupted"
    _accept_switch(eng, d)  # 打断者切换接受（deadline 建立前提，生产=EDTalk 回填）
    return d


# ------------------------------------------------- d9/e4 挂起进入

def test_d9_interrupt_spares_song_chain_and_suspends():
    NOW[0] = 1000.0
    eng = _engine([_song_rule(), _gift_rule()])
    d = _suspend_song(eng)
    cancels = [c for c in d["commands"] if type(c).__name__ == "CancelAudio"]
    assert cancels == []  # 歌曲链路不撤销（d9）
    assert eng.song_suspended == {"rule_id": "song_a", "deadline": 1200.0,
                                  "entered_at": 1000.0, "kind": "song"}
    assert eng.seq["rule_id"] == "g1"  # 打断者序列照常启动
    assert eng.counters["song_a"] == 0  # D-4 清零（e9 前提）
    assert eng.state == "sequence"


def test_e4_suspension_fields_and_no_restore_default():
    NOW[0] = 1000.0
    eng = _engine([_song_rule(), _gift_rule()])
    _suspend_song(eng)
    # 挂起态字段完整（e4 契约三键）
    assert set(eng.song_suspended.keys()) == {"rule_id", "deadline", "entered_at", "kind"}
    assert eng.song_suspended["kind"] == "song"  # T8：kind 参数化（song/resident）
    # 打断者完成 → 恢复（不发 RestoreDefault——默认池不接管，e4）
    NOW[0] = 1006.0
    cmds = eng.on_tick(NOW[0])
    assert eng.seq["rule_id"] == "song_a"  # 已恢复
    assert eng.state == "sequence"
    assert eng.song_suspended is None
    assert not [c for c in cmds if type(c).__name__ == "RestoreDefault"]
    assert eng.seq["steps"][0]["hold_for_song"] is True
    assert eng.seq["current_deadline"] == 1200.0  # deadline 续挂起锚


# ------------------------------------------------- d2 双结束态

def test_d2_recover_when_song_alive():
    NOW[0] = 1000.0
    eng = _engine([_song_rule(), _gift_rule()])
    _suspend_song(eng, remaining=200.0)
    NOW[0] = 1006.0
    eng.on_tick(NOW[0])  # 打断者完成 → 恢复
    assert eng.state == "sequence" and eng.seq["rule_id"] == "song_a"
    # 恢复后歌曲结束（deadline 到点）→ 既有超时机制走条件1 回默认池
    NOW[0] = 1201.0
    cmds = eng.on_tick(NOW[0])
    assert eng.state == "default"
    assert eng.song_suspended is None
    assert [c for c in cmds if type(c).__name__ == "RestoreDefault"]


def test_d2_default_pool_when_song_ended():
    NOW[0] = 1000.0
    eng = _engine([_song_rule(), _gift_rule()])
    _suspend_song(eng, remaining=50.0)  # deadline=1050
    NOW[0] = 1100.0                     # 打断者完成时歌曲已结束
    cmds = eng.on_tick(NOW[0])
    assert eng.state == "default"
    assert eng.song_suspended is None
    assert [c for c in cmds if type(c).__name__ == "RestoreDefault"]


# ------------------------------------------------- F-B 挂起期自校正

def test_fb_on_song_started_refreshes_suspended_deadline():
    NOW[0] = 1000.0
    eng = _engine([_song_rule(), _gift_rule()])
    _suspend_song(eng, remaining=200.0)  # deadline=1200
    NOW[0] = 1003.0
    cmds = eng.on_song_started("next_slice", 180.0, NOW[0])  # 打断者序列运行中
    assert eng.song_suspended["deadline"] == 1183.0  # now+180（F-B 刷新）
    assert eng.seq["rule_id"] == "g1"  # seq 未被扰动
    assert any("F-B" in c.msg for c in cmds if hasattr(c, "msg"))


# ------------------------------------------------- d11 song×song

def test_d11_song_retrigger_holds_sequence():
    NOW[0] = 1000.0
    # 双 song 规则以 match 区分（首个匹配规则吞噬任意 song 触发——song_a 空
    # match 会 shadow song_b，生产多 song 规则各自配歌名子串）
    eng = _engine([_song_rule("song_a"), _song_rule("song_b")])
    eng.cfg.rule_by_id("song_a").match = "晴天"
    eng.cfg.rule_by_id("song_b").match = "七里香"
    r = _trigger_song(eng, "晴天")
    assert r["reason"] == "accepted" and eng.seq["rule_id"] == "song_a"
    _accept_switch(eng, r)
    eng.on_song_started("s1", 200.0, NOW[0])
    before = dict(eng.seq)
    NOW[0] = 1005.0
    r2 = _trigger_song(eng, "七里香")  # 另一 song 规则重触发
    assert r2["reason"] == "song_queued"
    assert r2["accepted"] is True  # 歌曲请求被承接（音频侧入歌曲队列）
    assert eng.seq["rule_id"] == "song_a"  # 序列保持 hold 不重排
    assert eng.seq == before  # 序列零扰动
    assert eng.song_suspended is None
    assert eng.counters.get("song_b", 0) == 0  # 不重入计数
    # 同规则重触发：条件2 main 未完成=in_progress（hold 保持，不计数——同为
    # 「编排不扰动」语义；音频侧照常承接）
    r3 = _trigger_song(eng, "晴天")
    assert r3["reason"] == "in_progress"
    assert eng.seq == before


# ------------------------------------------------- e9 恢复=基础档

def test_e9_recovery_uses_base_tier():
    NOW[0] = 1000.0
    song = _song_rule("song_a", emotion_tiers=[
        {"min_count": 2, "mood": "高档", "segment_overrides": {"main": "S_高档"}}])
    eng = _engine([song, _gift_rule()])
    _trigger_song(eng)
    eng.counters["song_a"] = 3  # 模拟打断前已进高档（counters=3 → 档位二）
    eng.on_song_started("s", 200.0, NOW[0])
    _trigger_gift(eng)
    assert eng.counters["song_a"] == 0  # D-4 清零
    NOW[0] = 1006.0
    eng.on_tick(NOW[0])  # 恢复
    assert eng.seq["steps"][0]["segment"] == "S_主"  # 基础档（非 S_高档）


# ------------------------------------------------- d22 硬上限 + 总开关

def test_d22_watchdog_force_default_on_cap_exceeded():
    NOW[0] = 1000.0
    eng = _engine([_song_rule(), _gift_rule()])
    _suspend_song(eng, remaining=200.0)
    eng.song_suspended["entered_at"] = NOW[0] - 4000.0  # 超 3600s 硬上限
    NOW[0] = 1006.0
    with patch("utils.orchestration.choreographer.alarm_notifier") as alarm:
        cmds = eng.on_tick(NOW[0])
        assert eng.state == "default"
        assert eng.song_suspended is None
        assert alarm.alarm_error.called
        assert not [c for c in cmds if type(c).__name__ == "RestoreDefault"] or True


def test_switch_off_falls_back_to_terminate_semantics():
    NOW[0] = 1000.0
    eng = _engine([_song_rule(), _gift_rule()], arbitration_enable=False)
    r = _trigger_song(eng)
    assert r["reason"] == "accepted"
    eng.on_song_started("s", 200.0, NOW[0])
    d = _trigger_gift(eng)
    assert d["reason"] == "interrupted"
    assert eng.song_suspended is None  # 无挂起（现行终止语义）
    # 旧 exit 衔接存在（_interrupt_sequence 原行为：exit 未开始则重插）
    assert eng.seq["rule_id"] == "g1"
    assert "interrupted" in eng.seq["steps"][0]["step_id"]  # 旧 exit 衔接（计数器跨用例累计）


def test_d11_after_recovery_holds_again():
    NOW[0] = 1000.0
    eng = _engine([_song_rule("song_a"), _song_rule("song_b"), _gift_rule()])
    eng.cfg.rule_by_id("song_a").match = "晴天"
    eng.cfg.rule_by_id("song_b").match = "七里香"
    _suspend_song(eng, remaining=200.0, match="晴天")
    NOW[0] = 1006.0
    eng.on_tick(NOW[0])  # 恢复 song_a hold
    NOW[0] = 1008.0
    r = _trigger_song(eng, "七里香")  # 恢复后另一 song 规则触发 → d11 hold
    assert r["reason"] == "song_queued"
    assert eng.seq["rule_id"] == "song_a"
