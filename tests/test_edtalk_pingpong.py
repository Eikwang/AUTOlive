# -*- coding: utf-8 -*-
"""帧级 pingpong 循环守卫（2026-11-24 用户裁决：主线 hold 循环改为正放↔倒放
无缝往返，替代 A-B 锯齿折返的尾帧跳回首帧硬切）。

覆盖：
- 三角波映射序列正确性（正放→倒放→正放，首尾边界零跳变）
- 时钟连续性：映射只依赖 pos % period——显示序时钟单调递增，段边界判定/
  pending 切换触发时刻语义不变
- total≤2 退化（周期不成立退模运算）
- 编排主线切段携带 loop_mode=pingpong（resident/song hold），enter/exit 保持 loop
"""
import pytest

from edtalk.realtime_serve.frame_index import pingpong_frame_idx as _pp_idx
from utils.orchestration.choreographer import ChoreoEngine, SwitchSegment
from utils.orchestration.config_model import load_choreography_config

NOW = [1000.0]


def _pingpong_frame_idx(pos, total):
    return _pp_idx(pos, total)


# ------------------------------------------------- 三角波映射

def test_pingpong_sequence_total5():
    """total=5：0,1,2,3,4,3,2,1,0,1,…——端点不重复、正放↔倒放同帧衔接。"""
    expect = [0, 1, 2, 3, 4, 3, 2, 1, 0, 1, 2]
    got = [_pingpong_frame_idx(p, 5) for p in range(len(expect))]
    assert got == expect


def test_pingpong_boundary_continuity():
    """边界连续性：正放末→倒放首差 1 帧（4→3，非 4→0 跳变）；周期回绕
    1→0 自然连续——首尾边界零跳变（对比 % total 的尾帧跳回首帧）。"""
    total = 10
    idx = [_pingpong_frame_idx(p, total) for p in range(period_len(total) * 2)]
    for a, b in zip(idx, idx[1:]):
        assert abs(a - b) == 1  # 每步恰好 ±1 帧——无跳变


def period_len(total):
    return 2 * total - 2


def test_pingpong_no_frame_repeat_at_endpoints():
    """端点不重复：正放末（total-1）与倒放首（total-2）不重复帧、倒放末
    （1）与下轮正放首（0）不重复——对齐「循环无停顿」观感。"""
    total = 6
    seq = [_pingpong_frame_idx(p, total) for p in range(period_len(total))]
    assert seq.count(total - 1) == 1
    assert seq.count(0) == 1


def test_pingpong_clock_monotonic():
    """时钟连续性：pos 单调递增映射无状态依赖——任意 pos 直接可算（含远超
    周期的大值，服务端 _video_pos 不回绕）。"""
    total = 8
    base = _pingpong_frame_idx(10 ** 6, total)
    assert base == _pingpong_frame_idx(10 ** 6 % period_len(total), total)


def test_pingpong_degenerate_totals():
    """total≤2 退化：周期不成立退模运算（单帧段恒 0、双帧段 0/1 交替）。"""
    assert _pingpong_frame_idx(0, 1) == 0
    assert _pingpong_frame_idx(7, 1) == 0
    assert _pingpong_frame_idx(0, 2) == 0
    assert _pingpong_frame_idx(1, 2) == 1
    assert _pingpong_frame_idx(2, 2) == 0


# ------------------------------------------------- 编排主线传参

def _dt_rule(rid="dt", with_enter=False, main_dur=10):
    seq = {}
    if with_enter:
        seq["enter"] = {"segment": "E_" + rid, "duration_s": 3}
    seq["main"] = {"segment": "M_" + rid, "switch_at": "segment_end",
                   "duration_s": main_dur}
    seq["exit"] = {"segment": "X_" + rid, "duration_s": 2}
    return {"rule_id": rid, "trigger_type": "dynamic_text", "match": "",
            "interrupt": "standard", "sequence": seq}


def _song_rule(rid="song"):
    return {"rule_id": rid, "trigger_type": "song", "match": "",
            "interrupt": "standard",
            "sequence": {
                "main": {"segment": "S_" + rid, "switch_at": "segment_end",
                         "duration_s": 10, "hold_for_song": True},
                "exit": {"segment": "X_" + rid, "duration_s": 2},
            }}


def _engine(rules):
    node = {"enable": True, "rules": list(rules), "step_timeout_s": 60,
            "default_segments": ["D_1"]}
    cfg = load_choreography_config({"choreography": node})
    return ChoreoEngine(cfg, clock=lambda: NOW[0], priority_mapping={})


def _switches(cmds):
    return [c for c in cmds if isinstance(c, SwitchSegment)]


def test_resident_main_switch_pingpong():
    """resident 主线切段带 loop_mode=pingpong；enter/exit 保持默认 loop。"""
    NOW[0] = 1000.0
    eng = _engine([_dt_rule(with_enter=True)])
    d = eng.decide_trigger("dynamic_text", "今天天气真好", {})
    assert d["accepted"] is True
    sw = _switches(d["commands"])
    assert sw[0].segment == "E_dt" and sw[0].loop_mode == "loop"
    # 接受 enter → 播完 → 主线切段（pingpong）
    step = eng.seq["steps"][eng.seq["idx"]]
    eng.on_switch_result(step["step_id"], True, NOW[0])
    NOW[0] += 3.01
    cmds = eng.on_tick(NOW[0])
    sw = _switches(cmds)
    assert sw and sw[0].segment == "M_dt" and sw[0].loop_mode == "pingpong"


def test_song_hold_main_switch_pingpong():
    """song hold 主线切段带 loop_mode=pingpong（口型音频驱动不受倒放影响）。"""
    NOW[0] = 1000.0
    eng = _engine([_song_rule()])
    # hold_for_song 由 sidecar 二段物化（真实系统 _resolve_new_rule_material_props）
    eng.cfg.rules[0].main.hold_for_song = True
    d = eng.decide_trigger("song", "牧马城市", {"song_name": "牧马城市"})
    assert d["accepted"] is True
    sw = _switches(d["commands"])
    assert any(c.segment == "S_song" and c.loop_mode == "pingpong"
               for c in sw)


def test_gift_main_switch_keeps_loop():
    """普通规则（gift 等，短主线单次播放）保持默认 loop——模式无感不传 pingpong。"""
    NOW[0] = 1000.0
    eng = _engine([{"rule_id": "ga", "trigger_type": "gift", "match": "小心心",
                    "interrupt": "standard",
                    "sequence": {"main": {"segment": "G_ga", "duration_s": 5}}}])
    d = eng.decide_trigger("gift", "小心心",
                           {"gift_name": "小心心", "total_price": 1})
    assert d["accepted"] is True
    sw = _switches(d["commands"])
    assert sw[0].loop_mode == "loop"
