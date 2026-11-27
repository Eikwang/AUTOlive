"""阶段三 C3 + B2 第二批（2026-11-27）：preempt 覆盖语义显式化——音频随画面取消。

背景（docs/动作编排功能报告总结.md P1-7 + 用户裁决 2026-11-27）：preempt
单槽被高优覆盖时，被覆盖者的原生答谢照播而画面被裁=「有声音无画面」错配。
用户裁决=音频随画面取消。

实施（令牌机制——new 语义礼物答谢只走原生链，编排无法代生音轨，
has_tts_audio 对 new 规则恒 song，故 takeover=True 会吞掉正常答谢）：
- 登记盖章：preempt_pending 登记向触发载荷盖章 choreo_preempt_token，
  gift_handle 答谢携带令牌入队（event_handler 透传）；
- 令牌死亡：请求被覆盖/执行/序列更替时 preempt_token_alive=False；
- 出队因果门：播放侧出队丢弃死令牌条目 + 非活跃 step 的编排条目
  （B2 第二批，撤销窗口残留收口）。

覆盖：
- t1 登记盖章+令牌活性；t2 覆盖死亡+遥测；t3 执行死亡；t4 排队回退不盖章
- t5 出队门：死令牌丢弃/活令牌放行/编排 step 孤儿丢弃/非编排条目不受影响
- t6 未注册编排器 fail-open
"""
import pytest

NOW = [1000.0]

from utils.orchestration import choreographer as chm
from utils.orchestration.choreographer import ChoreoEngine
from utils.orchestration.config_model import load_choreography_config
from utils.audio.playback_manager import PlaybackMixin


def _rule(rid, match, switch_at="immediate", **over):
    r = {"rule_id": rid, "trigger_type": "gift", "match": match,
         "interrupt": "standard", "pred_version": "new",
         "sequence": {"main": {"segment": "M_" + rid, "switch_at": switch_at,
                               "duration_s": 30}}}
    r.update(over)
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


# ------------------------------------------------- 令牌生命周期

def test_preempt_registration_stamps_token():
    """t1：preempt_pending 登记 → 载荷盖章 choreo_preempt_token + 请求携带
    令牌 + 令牌活性查询 True；takeover 保持 False（原生答谢照常生成并携带
    令牌——编排无法代生 new 规则音轨）。"""
    NOW[0] = 1000.0
    eng = _engine([_rule("g1", "小心心", switch_at="segment_end"),
                   _rule("g2", "玫瑰")])
    d = eng.decide_trigger("gift", "小心心", _gift_data())
    _accept(eng)
    data = _gift_data("玫瑰")
    d2 = eng.decide_trigger("gift", "玫瑰", data)
    assert d2["reason"] == "preempt_pending"
    assert d2["takeover"] is False
    tok = data.get("choreo_preempt_token")          # 盖章（同对象透传答谢链）
    assert tok
    assert eng.seq["preempt_request"]["token"] == tok
    assert eng.preempt_token_alive(tok) is True


def test_preempt_overwrite_kills_old_token():
    """t2：高优触发覆盖已登记请求 → 旧令牌死亡（出队门可丢弃其答谢）+
    新令牌活跃 + preempt_overwritten 遥测（P1-7 观测闭环）。"""
    NOW[0] = 1000.0
    eng = _engine([_rule("g1", "小心心", switch_at="segment_end"),
                   _rule("g2", "玫瑰"),
                   _rule("g3", "烟花")])
    eng.decide_trigger("gift", "小心心", _gift_data())
    _accept(eng)
    data_b = _gift_data("玫瑰")
    eng.decide_trigger("gift", "玫瑰", data_b)
    tok_b = data_b["choreo_preempt_token"]
    data_c = _gift_data("烟花")
    d = eng.decide_trigger("gift", "烟花", data_c)
    assert d["reason"] == "preempt_pending"
    tok_c = data_c["choreo_preempt_token"]
    assert eng.preempt_token_alive(tok_b) is False   # 旧令牌死亡（C3 核心）
    assert eng.preempt_token_alive(tok_c) is True
    assert eng.metrics["preempt_overwritten"] == 1


def test_preempt_execution_kills_token():
    """t3：延迟抢占执行（主线播完）→ 令牌死亡（其滞留答谢由出队门丢弃，
    音频随画面取消——画面已切离主线域）。"""
    NOW[0] = 1000.0
    eng = _engine([_rule("g1", "小心心", switch_at="segment_end"),
                   _rule("g2", "玫瑰")])
    eng.decide_trigger("gift", "小心心", _gift_data())
    _accept(eng)
    data = _gift_data("玫瑰")
    eng.decide_trigger("gift", "玫瑰", data)
    tok = data["choreo_preempt_token"]
    NOW[0] += 30.5
    eng.on_tick(NOW[0])                              # main 播完 → 执行抢占
    assert eng.seq["rule_id"] == "g2"
    assert eng.preempt_token_alive(tok) is False     # 执行=死亡


def test_preempt_queue_fallback_no_stamp():
    """t4：权重 ≥ 当前序列但 < 已登记请求的触发走排队回退（不登记）→
    不盖章（画面稍后会来，原生答谢=既有 queue 模式语义）。
    构造：kw(10) 序列 → gift(30) 登记 → song(20) 回退排队。"""
    NOW[0] = 1000.0
    eng = _engine([_rule("kw", "你好", switch_at="segment_end",
                         trigger_type="keyword"),
                   _rule("g3", "烟花"),
                   _rule("song_a", "晴天", trigger_type="song")])
    eng.decide_trigger("keyword", "你好", {})
    _accept(eng)
    eng.decide_trigger("gift", "烟花", _gift_data("烟花"))   # 登记（30≥10）
    data = {}
    d = eng.decide_trigger("song", "晴天", data)             # song(20)<30 → 回退
    assert d["reason"] == "queued"
    assert "choreo_preempt_token" not in data                # 不盖章


# ------------------------------------------------- 出队因果门

class _Host(PlaybackMixin):
    """最小播放宿主（仅出队链路；_pop_select_locked 打桩直出队首）。"""

    def __init__(self, entries):
        self.voice_tmp_path_queue = list(entries)
        self._atomic_group_ctx = None
        self._song_anchor = None

    def _group_atomic_pop_locked(self):
        return None

    def _pop_select_locked(self):
        if not self.voice_tmp_path_queue:
            return None, 0.0, False
        return self.voice_tmp_path_queue.pop(0), 0.0, False


def test_dequeue_gate_tokens_and_steps():
    """t5：出队门三路——死令牌答谢丢弃、活令牌放行、孤儿 step 编排条目丢弃、
    非编排条目不受影响。"""
    NOW[0] = 1000.0
    eng = _engine([_rule("g1", "小心心", switch_at="segment_end"),
                   _rule("g2", "玫瑰"), _rule("g3", "烟花")])
    eng.decide_trigger("gift", "小心心", _gift_data())
    _accept(eng)
    data_b = _gift_data("玫瑰")
    eng.decide_trigger("gift", "玫瑰", data_b)
    tok_dead = data_b["choreo_preempt_token"]
    data_c = _gift_data("烟花")
    eng.decide_trigger("gift", "烟花", data_c)
    tok_alive = data_c["choreo_preempt_token"]
    active_step_id = eng.seq["steps"][eng.seq["idx"]]["step_id"]
    host = _Host([
        {"type": "gift", "content": "被覆盖答谢", "choreo_preempt_token": tok_dead},
        {"type": "gift", "content": "在登记答谢", "choreo_preempt_token": tok_alive},
        {"type": "gift", "content": "旧序列答谢", "choreo_step_id": "g0:main#0"},
        {"type": "gift", "content": "活跃步答谢", "choreo_step_id": active_step_id},
        {"type": "trends_copywriting", "content": "文案"},
    ])
    orig = chm.get_choreographer
    chm.get_choreographer = lambda: type("C", (), {
        "is_active_step": eng.is_active_step,
        "choreo_token_alive": eng.preempt_token_alive})()
    try:
        e1, _, _ = host._pop_next_entry_locked()
        assert e1["content"] == "在登记答谢"        # 死令牌已丢
        e2, _, _ = host._pop_next_entry_locked()
        assert e2["content"] == "活跃步答谢"        # 旧 step 已丢
        e3, _, _ = host._pop_next_entry_locked()
        assert e3["content"] == "文案"
        e4, _, _ = host._pop_next_entry_locked()
        assert e4 is None
        assert host._choreo_orphan_dropped == 2
    finally:
        chm.get_choreographer = orig


def test_dequeue_gate_fail_open_without_choreographer():
    """t6：编排器未注册 → fail-open（音频照播，不吞音频）。"""
    orig = chm.get_choreographer
    chm.get_choreographer = lambda: None
    try:
        host = _Host([
            {"type": "gift", "content": "x", "choreo_step_id": "g:main#0"},
            {"type": "gift", "content": "y", "choreo_preempt_token": "preempt#1"},
        ])
        e1, _, _ = host._pop_next_entry_locked()
        assert e1["content"] == "x"
        e2, _, _ = host._pop_next_entry_locked()
        assert e2["content"] == "y"
    finally:
        chm.get_choreographer = orig
