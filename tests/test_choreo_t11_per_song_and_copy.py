"""⑨-B 歌队列逐歌对应 + ⑦-B resident 文案消费精确对齐守卫
（2026-11-17 动作编排审查批次，用户裁决 B/B）。

覆盖语义：
- ⑨-B：歌队列中下一首起播命中**不同** song 规则=画面直切新规则主线 hold
  （仅 hold 步，离场留给自然结束后的默认轮播）；新规则计数+1 按档位升档、
  旧规则计数保留不清零（歌队列连续承接，非 D-4 打断）；同规则重触发/无规则
  命中=保持当前画面仅刷新 deadline；总开关 off=回退既有刷新语义。
- ⑨-B 受理不收缩：on_switch_result hold 受理 deadline=max(保险丝, 预置锚点
  剩余时长)——长片期间不得提前回默认。
- ⑦-B：resident 挂起期间文案完成回执（trends_copywriting）登记 copy_done，
  恢复时跳过 hold 直回默认（防闪帧）；恢复 hold 期间文案完成回执=hold 精确
  提前结束回默认（不再只依赖 step_timeout 保险丝）；他链路音频完成不误配。
"""
from utils.orchestration.choreographer import ChoreoEngine
from utils.orchestration.config_model import load_choreography_config

NOW = [1000.0]


def _song_rule(rid="song_a", match="", seg="S_主", **over):
    r = {"rule_id": rid, "trigger_type": "song", "match": match,
         "interrupt": "standard", "pred_version": "new",
         "sequence": {"main": {"segment": seg, "switch_at": "immediate",
                               "duration_s": 30}}}
    r.update(over)
    return r


def _gift_rule(rid="g1", **over):
    r = {"rule_id": rid, "trigger_type": "gift", "match": "小心心",
         "interrupt": "standard", "pred_version": "new",
         "sequence": {"main": {"segment": "G_答", "switch_at": "immediate",
                               "duration_s": 5}}}
    r.update(over)
    return r


def _dyrule(rid="dy1", match="", **over):
    r = {"rule_id": rid, "trigger_type": "dynamic_text", "match": match,
         "interrupt": "standard", "pred_version": "new",
         "sequence": {"main": {"segment": "D_文", "switch_at": "immediate",
                               "duration_s": 2}}}
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


def _accept_switch(eng, result):
    sw = next(c for c in result["commands"] if type(c).__name__ == "SwitchSegment")
    return eng.on_switch_result(sw.step_id, True, NOW[0])


def _cmds_of_type(commands, name):
    return [c for c in commands if type(c).__name__ == name]


# ------------------------------------------------- ⑨-B 逐歌对应

def test_per_song_switch_to_different_rule():
    """队列下一首起播命中不同 song 规则=直切新规则主线 hold。"""
    NOW[0] = 1000.0
    eng = _engine([_song_rule("song_a", "晴天", "S_晴"),
                   _song_rule("song_b", "七里香", "S_七")])
    r = eng.decide_trigger("song", "晴天", {})
    assert r["reason"] == "accepted"
    _accept_switch(eng, r)
    eng.on_song_started("晴天", 200.0, NOW[0])
    assert eng.seq["rule_id"] == "song_a"
    assert eng.counters == {"song_a": 1}

    cmds = eng.on_song_started("七里香", 150.0, NOW[0])
    sw = _cmds_of_type(cmds, "SwitchSegment")
    assert len(sw) == 1 and sw[0].segment == "S_七"
    assert eng.seq["rule_id"] == "song_b"        # 画面切到新规则
    assert eng.seq["steps"][0]["segment"] == "S_七"
    assert eng.seq["hold_for_song"] is True
    assert eng.seq["current_deadline"] == 1150.0  # 锚点+剩余
    assert eng.counters == {"song_a": 1, "song_b": 1}  # 新规则+1，旧规则保留
    assert eng.song_suspended is None


def test_per_song_switch_accept_does_not_shrink_deadline():
    """受理回填 deadline=max(保险丝, 预置锚点剩余)——长片不提前回默认。"""
    NOW[0] = 1000.0
    eng = _engine([_song_rule("song_a", "晴天", "S_晴"),
                   _song_rule("song_b", "七里香", "S_七")])
    r = eng.decide_trigger("song", "晴天", {})
    _accept_switch(eng, r)
    eng.on_song_started("晴天", 200.0, NOW[0])
    cmds = eng.on_song_started("七里香", 150.0, NOW[0])
    _accept_switch(eng, {"commands": cmds})
    # 预置 1150 > 保险丝 1060 → 保持 1150
    assert eng.seq["current_deadline"] == 1150.0
    assert eng.seq["step_accepted"] is True


def test_per_song_same_rule_and_no_rule_keep_frame():
    """同规则重触发/无规则命中=保持画面仅刷新 deadline（零切换零计数扰动）。"""
    NOW[0] = 1000.0
    eng = _engine([_song_rule("song_a", "晴天", "S_晴"),
                   _song_rule("song_b", "七里香", "S_七")])
    r = eng.decide_trigger("song", "晴天", {})
    _accept_switch(eng, r)
    eng.on_song_started("晴天", 200.0, NOW[0])
    # 同规则另一片起播：仅刷新
    cmds = eng.on_song_started("晴天", 120.0, NOW[0])
    assert _cmds_of_type(cmds, "SwitchSegment") == []
    assert eng.seq["rule_id"] == "song_a"
    assert eng.seq["current_deadline"] == 1120.0
    assert eng.counters == {"song_a": 1}
    # 无规则命中歌曲：保持画面仅刷新
    cmds = eng.on_song_started("无名歌", 80.0, NOW[0])
    assert _cmds_of_type(cmds, "SwitchSegment") == []
    assert eng.seq["rule_id"] == "song_a"
    assert eng.seq["current_deadline"] == 1080.0
    assert eng.counters == {"song_a": 1}


def test_per_song_switch_disabled_when_arbitration_off():
    """总开关 off=回退既有语义（只刷新 deadline，d22）。"""
    NOW[0] = 1000.0
    eng = _engine([_song_rule("song_a", "晴天", "S_晴"),
                   _song_rule("song_b", "七里香", "S_七")],
                  arbitration_enable=False)
    r = eng.decide_trigger("song", "晴天", {})
    _accept_switch(eng, r)
    eng.on_song_started("晴天", 200.0, NOW[0])
    cmds = eng.on_song_started("七里香", 150.0, NOW[0])
    assert _cmds_of_type(cmds, "SwitchSegment") == []
    assert eng.seq["rule_id"] == "song_a"
    assert eng.seq["current_deadline"] == 1150.0


def test_per_song_switch_queue_drains_to_default():
    """逐歌切换后歌队列全部唱完（deadline 过点）→ hold 步即末步 → 直回默认。"""
    NOW[0] = 1000.0
    eng = _engine([_song_rule("song_a", "晴天", "S_晴"),
                   _song_rule("song_b", "七里香", "S_七")])
    r = eng.decide_trigger("song", "晴天", {})
    _accept_switch(eng, r)
    eng.on_song_started("晴天", 200.0, NOW[0])
    cmds = eng.on_song_started("七里香", 150.0, NOW[0])
    _accept_switch(eng, {"commands": cmds})
    NOW[0] = 1151.0
    cmds = eng.on_tick(NOW[0])
    assert eng.state == "default"
    assert _cmds_of_type(cmds, "RestoreDefault")


# ------------------------------------------------- ⑦-B resident 文案消费对齐

COPY = "我们的洗洁精分量非常大，一桶足有5公斤，非常实惠"


def _resident_interrupted(eng):
    """构造 resident 挂起态并按步序推进打断者序列（dy main→gift main）。

    返回 (gift 触发结果, 打断者最后一个完成 on_tick 命令列表)。resident 挂起
    插播先播旧规则离场复制步（dy 无 exit 配置→old_exit=main 复制步）。"""
    NOW[0] = 1000.0
    r = eng.decide_trigger("dynamic_text", COPY, {})
    assert r["reason"] == "accepted"
    _accept_switch(eng, r)
    assert eng.seq["resident"] is True
    assert eng.seq["match_value"] == COPY
    g = eng.decide_trigger("gift", "小心心", {"gift_name": "小心心", "total_price": 1})
    assert g["reason"] == "interrupted"
    assert eng.song_suspended is not None
    assert eng.song_suspended["kind"] == "resident"
    assert eng.song_suspended["copy"] == COPY
    assert eng.song_suspended["copy_done"] is False
    # 步序推进：① 旧规则离场复制步（dy main，2s）
    sws = _cmds_of_type(g["commands"], "SwitchSegment")
    assert len(sws) == 1
    eng.on_switch_result(sws[0].step_id, True, NOW[0])
    NOW[0] = 1003.0
    cmds = eng.on_tick(NOW[0])
    # ② gift 主线（5s）
    sws = _cmds_of_type(cmds, "SwitchSegment")
    assert len(sws) == 1
    eng.on_switch_result(sws[0].step_id, True, NOW[0])
    return g


def test_copy_done_during_suspend_skips_recovery_hold():
    """文案音频在挂起期间消费完成 → 恢复时跳过 hold 直回默认（防闪帧）。"""
    eng = _engine([_dyrule(), _gift_rule()])
    _resident_interrupted(eng)
    # 挂起期间（打断者 gift main 在播）文案完成回执到达：登记 copy_done
    cmds = eng.on_audio_complete("trends_copywriting", COPY, 1004.0)
    assert cmds == []
    assert eng.song_suspended["copy_done"] is True
    assert eng.state == "sequence"          # 打断者序列不受影响
    # 打断者播完：copy_done → 跳过恢复 hold → 直回默认
    NOW[0] = 1008.0
    cmds = eng.on_tick(NOW[0])
    assert eng.state == "default"
    assert eng.song_suspended is None
    assert _cmds_of_type(cmds, "RestoreDefault")
    assert eng.seq is None


def test_copy_completion_ends_recovery_hold_precisely():
    """恢复 hold 期间文案完成回执=hold 精确提前结束回默认（不等保险丝）。"""
    eng = _engine([_dyrule(), _gift_rule()])
    _resident_interrupted(eng)
    NOW[0] = 1008.0
    cmds = eng.on_tick(NOW[0])              # 打断者播完 → 恢复 hold
    assert eng.state == "sequence"
    assert eng.seq["rule_id"] == "dy1"
    assert eng.seq["resident_copy"] == COPY
    assert eng.seq["hold_for_song"] is False
    # 恢复 hold 中他链路音频完成：不误配
    assert eng.on_audio_complete("tts", COPY, NOW[0]) == []
    assert eng.state == "sequence"
    # 文案完成回执：hold 提前结束回默认（此刻距保险丝 60s 尚远）
    cmds = eng.on_audio_complete("trends_copywriting", COPY, NOW[0] + 1)
    assert eng.state == "default"
    assert eng.seq is None
    assert _cmds_of_type(cmds, "RestoreDefault")


def test_copy_completion_normalized_match():
    """remove_extra_words 类裁剪后的完成内容仍按归一化包含匹配命中。"""
    eng = _engine([_dyrule(), _gift_rule()])
    _resident_interrupted(eng)
    NOW[0] = 1008.0
    eng.on_tick(NOW[0])
    # 下游裁剪：去掉标点/部分点缀词后的子串
    trimmed = "我们的洗洁精分量非常大一桶足有5公斤非常实惠"
    cmds = eng.on_audio_complete("trends_copywriting", trimmed, NOW[0] + 1)
    assert eng.state == "default"
    assert _cmds_of_type(cmds, "RestoreDefault")


def test_copy_completion_other_content_no_misfire():
    """他内容/他类型完成回执不结束 hold（qtype 过滤+归一化包含守卫）。"""
    eng = _engine([_dyrule(), _gift_rule()])
    _resident_interrupted(eng)
    NOW[0] = 1008.0
    eng.on_tick(NOW[0])
    assert eng.on_audio_complete("trends_copywriting", "完全无关的答谢文案", NOW[0]) == []
    assert eng.state == "sequence"
    assert eng.on_audio_complete("gift", COPY, NOW[0]) == []
    assert eng.state == "sequence"
