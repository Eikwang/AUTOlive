# -*- coding: utf-8 -*-
"""离场跳过 bug 根修守卫（2026-11-27 实测回归：短音频动态文案跳过离场直回默认）。

根因链（调查结论）：
1. 离场步 switch_at 被 loader 强制 segment_end（过渡片段固定边界续接）；
2. EDTalk 对 segment_end 只排队（render_loop M16 pending_switch），受理≠执行
   ——实际显示要等当前素材（长主线）自然播完的边界；
3. 旧实现离场 deadline 从受理起算 → 早于边界到期 → 序列收尾
   RestoreDefault(align=True) 清掉排队离场（M3/L3 last-write-wins）→ 离场从未
   显示，主线被中途砍断直回默认。
4. 长音频「正常」是巧合对齐（消费完恰在整周期边界，segment_end 即触）。

修复语义（用户裁决 2026-11-27）：
- 立刻切换（主线 switch_at=immediate）：音频消费完成后立刻硬切离场（F1 改判）。
- 无缝切换（主线 switch_at=segment_end）：主线至少完整播放一次再切离场
  （segment_end 边界续接天然保证），离场计时锚定边界实际执行
  （on_switch_executed 回填），临时上限=前一步+本步时长兜底（看门狗）。

覆盖：
- t1 立刻模式 resident 短音频：copy_done → exit immediate（受理=执行）→ 回默认
- t2 无缝模式 resident 短音频：copy_done → exit segment_end 排队 → 边界执行
  回填 → 离场完整播放 → 回默认
- t3 无缝模式边界等待临时上限兜底：不回填 → 看门狗降级推进回默认（不悬挂）
- t4 无缝模式长音频（原「正常」场景）：主线循环后 copy_done → 边界回填 → 回默认
- t5 过期 switch_executed 事件忽略（步已推进/step_id 不匹配/非 boundary_wait）
- t6 边界等待期 mute_window=False（画面仍是主线，音频不延迟）
- t7 非 resident（gift）两模式离场 at 判定
- t8 get_state 透出 boundary_wait
"""
from utils.orchestration.choreographer import (
    ChoreoEngine, SwitchSegment, RestoreDefault)
from utils.orchestration.config_model import load_choreography_config

NOW = [1000.0]

COPY = "今天天气真好"


def _dt_rule(rid="dt", main_at="immediate", main_dur=300, **over):
    """动态文案规则：主线默认 5 分钟长素材（用户实测场景），exit 过渡段。"""
    r = {"rule_id": rid, "trigger_type": "dynamic_text", "match": "",
         "interrupt": "standard", "pred_version": "new",
         "sequence": {
             "enter": {"segment": "E_" + rid, "switch_at": "immediate",
                       "duration_s": 2},
             "main": {"segment": "M_" + rid, "switch_at": main_at,
                      "duration_s": main_dur},
             "exit": {"segment": "X_" + rid, "switch_at": "segment_end",
                      "duration_s": 2},
         }}
    r.update(over)
    return r


def _gift_rule(rid="g1", main_at="immediate", main_dur=5, **over):
    r = {"rule_id": rid, "trigger_type": "gift", "match": "小心心",
         "interrupt": "standard", "pred_version": "new",
         "sequence": {
             "main": {"segment": "G_" + rid, "switch_at": main_at,
                      "duration_s": main_dur},
             "exit": {"segment": "X_" + rid, "switch_at": "segment_end",
                      "duration_s": 2},
         }}
    r.update(over)
    return r


def _engine(rules, **over):
    node = {"enable": True, "rules": list(rules), "step_timeout_s": 60,
            "default_segments": ["D_1"]}
    node.update(over)
    cfg = load_choreography_config({"choreography": node})
    return ChoreoEngine(cfg, clock=lambda: NOW[0], priority_mapping={})


def _accept_current(eng):
    step = eng.seq["steps"][eng.seq["idx"]]
    return eng.on_switch_result(step["step_id"], True, NOW[0])


def _sw(cmds):
    return [c for c in cmds if isinstance(c, SwitchSegment)]


def _restore(cmds):
    return [c for c in cmds if isinstance(c, RestoreDefault)]


def _trigger_dt(eng, rid="dt"):
    """触发 resident 序列并推进到 main 受理（enter 播完）。"""
    d = eng.decide_trigger("dynamic_text", COPY, {"text": COPY})
    assert d["accepted"] is True
    _accept_current(eng)                       # enter（immediate 强制）
    NOW[0] += 2.01
    cmds = eng.on_tick(NOW[0])                 # enter 播完 → main
    _accept_current(eng)                       # main 受理
    return cmds


def _copy_done(eng, content=COPY):
    """文案消费完成回执（结构化组事件：本组空+全局空）。"""
    return eng.on_audio_complete("trends_copywriting", content, NOW[0],
                                 group_pending=0, copy_groups_pending=0)


# ------------------------------------------------- t1 立刻模式

def test_immediate_mode_short_audio_exit_hard_cut():
    """立刻模式短音频：copy_done → exit immediate 硬切（受理=执行，无脱节）
    → 离场完整播放 → 回默认。"""
    NOW[0] = 1000.0
    eng = _engine([_dt_rule(main_at="immediate")])
    _trigger_dt(eng)
    NOW[0] += 5                                # 主线播放中（音频几句消费完）
    cmds = _copy_done(eng)
    assert eng.seq["copy_done"] is True
    sw = _sw(cmds)
    assert len(sw) == 1 and sw[0].at == "immediate"   # F1 立刻模式离场硬切
    assert sw[0].segment == "X_dt"
    NOW[0] += 0.01
    _accept_current(eng)                       # exit 受理=执行
    NOW[0] += 2.01                             # 离场完整播放
    end = eng.on_tick(NOW[0])
    assert eng.state == "default" and eng.seq is None
    assert _restore(end)


def test_immediate_mode_long_audio_unchanged():
    """立刻模式长音频回归（原「正常」场景保持）：主线循环至 copy_done（分段
    回执续期保险丝）→ exit immediate → 回默认。"""
    NOW[0] = 1000.0
    eng = _engine([_dt_rule(main_at="immediate", main_dur=10)])
    _trigger_dt(eng)
    NOW[0] += 10.01                            # 主线第一轮播完 → 循环延长
    eng.on_tick(NOW[0])
    assert eng.seq["steps"][eng.seq["idx"]]["name"] == "main"
    eng.on_audio_complete("trends_copywriting", COPY, NOW[0],
                          group_pending=1)     # 分段回执续期（非末段）
    NOW[0] += 10.01                            # 第二轮中继续消费
    eng.on_tick(NOW[0])
    NOW[0] += 5
    cmds = _copy_done(eng)                     # 末段消费完（≈25s，2.5 轮）
    sw = _sw(cmds)
    assert len(sw) == 1 and sw[0].at == "immediate"
    NOW[0] += 0.01
    _accept_current(eng)
    NOW[0] += 2.01
    end = eng.on_tick(NOW[0])
    assert eng.state == "default"
    assert _restore(end)


# ------------------------------------------------- t2/t3/t4 无缝模式

def test_seamless_mode_short_audio_exit_waits_boundary():
    """无缝模式短音频（用户实测 bug 场景）：copy_done → exit segment_end
    排队 → 临时上限等待 → 边界执行回填 → 计时锚定实际显示 → 离场完整播放
    → 回默认。修复前：离场 deadline 从受理起算，早于边界到期即恢复默认
    清掉排队离场=离场从未显示。"""
    NOW[0] = 1000.0
    eng = _engine([_dt_rule(main_at="segment_end")])
    _trigger_dt(eng)
    NOW[0] += 5                                # 主线（5min 素材）播放中
    cmds = _copy_done(eng)
    sw = _sw(cmds)
    assert len(sw) == 1 and sw[0].at == "segment_end"
    NOW[0] += 0.01
    _accept_current(eng)                       # 离场受理（仅排队）
    # 临时上限 = 前一步 300s + 本步 2s；boundary_wait 置位
    assert eng.seq["boundary_wait"] is True
    assert eng.seq["current_deadline"] == NOW[0] + 302.0
    # 边界未到：离场时长窗口内不推进（修复前此处会恢复默认清掉排队离场）
    NOW[0] += 3.0
    assert eng.on_tick(NOW[0]) == []
    # 边界执行回填（EDTalk current_segment 达成）→ 计时起点=实际显示
    eng.on_switch_executed(eng.seq["steps"][eng.seq["idx"]]["step_id"], NOW[0])
    assert eng.seq["boundary_wait"] is False
    assert eng.seq["current_deadline"] == NOW[0] + 2.0
    NOW[0] += 2.01                             # 离场完整播放
    end = eng.on_tick(NOW[0])
    assert eng.state == "default" and eng.seq is None
    assert _restore(end)


def test_seamless_mode_boundary_watchdog_fallback():
    """无缝模式兜底：边界迟迟未执行（EDTalk 异常）→ 临时上限到期看门狗
    降级推进收尾回默认（不悬挂）+ 遥测计数。"""
    NOW[0] = 1000.0
    eng = _engine([_dt_rule(main_at="segment_end")])
    _trigger_dt(eng)
    NOW[0] += 5
    _copy_done(eng)
    NOW[0] += 0.01
    _accept_current(eng)
    assert eng.seq["boundary_wait"] is True
    NOW[0] += 302.01                           # 临时上限（300+2）过点
    end = eng.on_tick(NOW[0])
    assert eng.state == "default"
    assert _restore(end)
    assert eng.metrics["boundary_wait_expired"] == 1


def test_seamless_mode_long_audio_still_works():
    """无缝模式长音频回归（原「正常」场景保持）：主线循环（分段回执续期）
    至 copy_done → exit segment_end → 边界回填 → 回默认。"""
    NOW[0] = 1000.0
    eng = _engine([_dt_rule(main_at="segment_end", main_dur=10)])
    _trigger_dt(eng)
    NOW[0] += 10.01
    cmds = eng.on_tick(NOW[0])                 # 第一轮播完 → 循环延长
    assert eng.seq["steps"][eng.seq["idx"]]["name"] == "main"
    eng.on_audio_complete("trends_copywriting", COPY, NOW[0],
                          group_pending=1)     # 分段回执续期（非末段）
    NOW[0] += 10.01
    eng.on_tick(NOW[0])
    NOW[0] += 5
    cmds = _copy_done(eng)
    sw = _sw(cmds)
    assert len(sw) == 1 and sw[0].at == "segment_end"
    NOW[0] += 0.01
    _accept_current(eng)
    eng.on_switch_executed(eng.seq["steps"][eng.seq["idx"]]["step_id"], NOW[0])
    NOW[0] += 2.01
    end = eng.on_tick(NOW[0])
    assert eng.state == "default"
    assert _restore(end)


# ------------------------------------------------- t5 过期事件

def test_stale_switch_executed_ignored():
    """过期 switch_executed：步已推进（序列收尾）/step_id 不匹配/非
    boundary_wait → 忽略，不覆盖计时。"""
    NOW[0] = 1000.0
    eng = _engine([_dt_rule(main_at="segment_end")])
    # 非序列态：零动作
    assert eng.on_switch_executed("dt:exit#1", NOW[0]) == []
    _trigger_dt(eng)
    # 当前步=main（非 boundary_wait、step_id 不匹配）→ 忽略
    assert eng.on_switch_executed("dt:exit#1", NOW[0]) == []
    NOW[0] += 5
    _copy_done(eng)
    NOW[0] += 0.01
    _accept_current(eng)
    exit_id = eng.seq["steps"][eng.seq["idx"]]["step_id"]
    assert eng.seq["boundary_wait"] is True
    # step_id 不匹配 → 忽略
    assert eng.on_switch_executed("dt:exit#stale", NOW[0]) == []
    assert eng.seq["boundary_wait"] is True
    # 匹配 → 生效
    assert eng.on_switch_executed(exit_id, NOW[0]) == []
    assert eng.seq["boundary_wait"] is False
    # 收尾后再来同 id → 序列已 None，忽略
    NOW[0] += 2.01
    eng.on_tick(NOW[0])
    assert eng.seq is None
    assert eng.on_switch_executed(exit_id, NOW[0]) == []


# ------------------------------------------------- t6 静音窗口

def test_mute_window_false_during_boundary_wait():
    """边界等待期画面仍是主线素材——静音窗口不生效（常规语音不延迟）。"""
    NOW[0] = 1000.0
    eng = _engine([_dt_rule(main_at="segment_end")])
    _trigger_dt(eng)
    NOW[0] += 5
    _copy_done(eng)
    NOW[0] += 0.01
    _accept_current(eng)
    assert eng.seq["boundary_wait"] is True
    assert eng.mute_window() is False          # 修复前：离场步 lip off → True


# ------------------------------------------------- t7 非 resident 两模式

def test_gift_immediate_mode_exit_hard_cut():
    """gift 立刻模式：主线计时完 → exit immediate。"""
    NOW[0] = 1000.0
    eng = _engine([_gift_rule(main_at="immediate")])
    d = eng.decide_trigger("gift", "小心心", {"gift_name": "小心心"})
    assert d["accepted"] is True
    _accept_current(eng)                       # main（immediate）
    NOW[0] += 5.01
    cmds = eng.on_tick(NOW[0])                 # 主线播完 → exit
    sw = _sw(cmds)
    assert len(sw) == 1 and sw[0].at == "immediate"
    NOW[0] += 0.01
    _accept_current(eng)
    NOW[0] += 2.01
    end = eng.on_tick(NOW[0])
    assert eng.state == "default" and _restore(end)


def test_gift_seamless_mode_exit_boundary_gated():
    """gift 无缝模式：主线计时完 → exit segment_end 排队 → 边界回填 → 回默认
    （修复前：离场 deadline 从受理起算，主线视频长于音频时恢复默认会清掉
    排队离场=离场跳过）。"""
    NOW[0] = 1000.0
    eng = _engine([_gift_rule(main_at="segment_end", main_dur=30)])
    d = eng.decide_trigger("gift", "小心心", {"gift_name": "小心心"})
    _accept_current(eng)                       # main（segment_end 受理）
    NOW[0] += 30.01
    cmds = eng.on_tick(NOW[0])                 # 主线播完 → exit
    sw = _sw(cmds)
    assert len(sw) == 1 and sw[0].at == "segment_end"
    NOW[0] += 0.01
    _accept_current(eng)
    # 临时上限 = 前一步 30s + 本步 2s
    assert eng.seq["boundary_wait"] is True
    assert eng.seq["current_deadline"] == NOW[0] + 32.0
    eng.on_switch_executed(eng.seq["steps"][eng.seq["idx"]]["step_id"], NOW[0])
    NOW[0] += 2.01
    end = eng.on_tick(NOW[0])
    assert eng.state == "default" and _restore(end)


# ------------------------------------------------- t8 状态透出

def test_get_state_exposes_boundary_wait():
    NOW[0] = 1000.0
    eng = _engine([_dt_rule(main_at="segment_end")])
    assert eng.get_state()["boundary_wait"] is False
    _trigger_dt(eng)
    NOW[0] += 5
    _copy_done(eng)
    NOW[0] += 0.01
    _accept_current(eng)
    assert eng.get_state()["boundary_wait"] is True
    eng.on_switch_executed(eng.seq["steps"][eng.seq["idx"]]["step_id"], NOW[0])
    assert eng.get_state()["boundary_wait"] is False
