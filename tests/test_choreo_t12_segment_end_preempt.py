"""T12 主线无缝保护（Gap A，2026-11-24 动作编排完善批次）守卫。

用户设计语义：当前主线 switch_at=segment_end（无缝切换）→ 其它规则触发
不立即抢占，登记 preempt_request 延迟到主线完整播完，然后播旧 exit 再切
新规则入场；switch_at=immediate（立刻切换）→ 其它规则可立刻抢占主线。

覆盖：
- segment_end 主线期更高权重触发 → preempt_pending + 槽位登记（计数不变）
- 主线播完 → [旧 exit + 新规则 steps] 替换 + D-4 旧规则计数清零 + 新规则计数+1
- 高优触发覆盖槽位 / 低优触发走 pending_queue（与消息/音频队列权重一致）
- 同权重触发覆盖槽位（后者赢，对齐 O3 同权抢占语义）
- immediate 主线对照：立即打断（回归）
- exit 步（main_done）期触发：立即打断不受无缝门约束
- 槽位规则禁用/删除 → 主线播完弃请求正常推进
- 权重门先于无缝门：低权重触发直接排队不经无缝门
"""
from utils.orchestration.choreographer import ChoreoEngine, SwitchSegment
from utils.orchestration.config_model import load_choreography_config

NOW = [1000.0]


def _kw_rule(rid="ka", match="点歌了", main_switch_at="segment_end", main_dur=10,
             with_exit=True, **over):
    seq = {"main": {"segment": "K_" + rid, "switch_at": main_switch_at,
                    "duration_s": main_dur}}
    if with_exit:
        seq["exit"] = {"segment": "X_" + rid, "duration_s": 4}
    r = {"rule_id": rid, "trigger_type": "keyword", "match": match,
         "interrupt": "standard", "sequence": seq}
    r.update(over)
    return r


def _gift_rule(rid="ga", match="小心心", main_dur=5, **over):
    r = {"rule_id": rid, "trigger_type": "gift", "match": match,
         "interrupt": "standard",
         "sequence": {"main": {"segment": "G_" + rid, "duration_s": main_dur}}}
    r.update(over)
    return r


def _dt_rule(rid="dt", **over):
    r = {"rule_id": rid, "trigger_type": "dynamic_text", "match": "",
         "interrupt": "standard",
         "sequence": {"main": {"segment": "M_" + rid, "duration_s": 10}}}
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


def _start_kw_main(eng, match="帮我点歌了", kw_rid="ka"):
    """启动 keyword 规则序列并接受主线切换（主线在播，segment_end 保护中）。"""
    d = _hit(eng, "keyword", match)
    assert d["accepted"] is True
    assert eng.seq["steps"][eng.seq["idx"]]["name"] == "main"
    _accept_current(eng)  # main 接受（deadline=now+素材时长）
    return d


# ------------------------------------------------- 无缝门登记与执行

def test_segment_end_main_registers_preempt_pending():
    """segment_end 主线期更高权重触发：登记延迟抢占（画面保持、计数不变）。"""
    NOW[0] = 1000.0
    eng = _engine([_kw_rule(), _gift_rule()])
    _start_kw_main(eng)
    d = _hit(eng, "gift", "小心心", {"gift_name": "小心心", "total_price": 1})
    assert d["accepted"] is False
    assert d["reason"] == "preempt_pending"
    assert eng.seq["preempt_request"]["rule_id"] == "ga"
    assert eng.counters["ka"] == 1          # 触发不计数（未启动序列）
    assert eng.counters.get("ga", 0) == 0
    assert eng.seq["rule_id"] == "ka"       # 画面保持原主线
    assert eng.get_state()["preempt_request"] == "ga"


def test_preempt_executes_after_main_completes():
    """主线完整播完 → 旧 exit 衔接新规则序列（D-4 清零 + 新规则计数+1）。"""
    NOW[0] = 1000.0
    eng = _engine([_kw_rule(), _gift_rule()])
    _start_kw_main(eng)
    _hit(eng, "gift", "小心心", {"gift_name": "小心心", "total_price": 1})
    cmds = _finish_current(eng)             # 主线播完 → 抢占执行点
    assert eng.seq["rule_id"] == "ga"
    assert eng.counters["ka"] == 0          # D-4 他键触发清零
    assert eng.counters["ga"] == 1
    names = [s["name"] for s in eng.seq["steps"]]
    assert names == ["exit", "main"]        # 旧 exit 衔接 + 新规则主线
    sw = [c for c in cmds if isinstance(c, SwitchSegment)]
    assert len(sw) == 1
    assert sw[0].segment == "X_ka"          # 先切旧离场
    assert sw[0].at == "immediate"
    _accept_current(eng)                    # 旧 exit 接受
    cmds = _finish_current(eng)             # exit 播完 → 新主线
    assert eng.seq["steps"][eng.seq["idx"]]["segment"] == "G_ga"


def test_higher_priority_overrides_slot():
    """槽位已登记时更高权重触发覆盖（后者赢面按权重序）。"""
    NOW[0] = 1000.0
    eng = _engine([_kw_rule(), _dt_rule(), _gift_rule()])
    _start_kw_main(eng)
    _hit(eng, "dynamic_text", "任意文案")   # dt（p=15）登记
    assert eng.seq["preempt_request"]["rule_id"] == "dt"
    d = _hit(eng, "gift", "小心心", {"gift_name": "小心心", "total_price": 1})
    assert d["reason"] == "preempt_pending"
    assert eng.seq["preempt_request"]["rule_id"] == "ga"  # gift（p=30）覆盖
    assert eng.pending_queue == []


def test_lower_priority_goes_to_pending_queue():
    """槽位已登记高优时低优触发走 pending_queue（权重序与音频队列一致）。"""
    NOW[0] = 1000.0
    eng = _engine([_kw_rule(), _dt_rule(), _gift_rule()])
    _start_kw_main(eng)
    _hit(eng, "gift", "小心心", {"gift_name": "小心心", "total_price": 1})
    d = _hit(eng, "dynamic_text", "任意文案")  # dt（p=15）< 槽位 gift（p=30）
    assert d["reason"] == "queued"
    assert eng.seq["preempt_request"]["rule_id"] == "ga"
    assert len(eng.pending_queue) == 1
    assert eng.pending_queue[0]["rule_id"] == "dt"


def test_same_priority_overrides_later_wins():
    """同权重触发覆盖槽位（后者赢，对齐 O3「同权保持抢占」）。"""
    NOW[0] = 1000.0
    eng = _engine([_kw_rule(), _kw_rule("kb", match="唱歌了"),
                   _kw_rule("kd", match="关注了")])
    _start_kw_main(eng)
    _hit(eng, "keyword", "帮我点歌了")      # ka 是当前序列规则——同键走条件2
    # 换一个关键词触发另一条 keyword 规则（同权重 10）
    d = _hit(eng, "keyword", "一起唱歌了")
    assert d["reason"] == "preempt_pending"
    assert eng.seq["preempt_request"]["rule_id"] == "kb"
    d = _hit(eng, "keyword", "他关注了")
    assert d["reason"] == "preempt_pending"
    assert eng.seq["preempt_request"]["rule_id"] == "kd"  # 同权后者赢


# ------------------------------------------------- 对照与边界

def test_immediate_main_interrupts_immediately():
    """immediate 主线对照：有权触发立即抢占（既有语义回归）。"""
    NOW[0] = 1000.0
    eng = _engine([_kw_rule(main_switch_at="immediate"), _gift_rule()])
    _start_kw_main(eng)
    d = _hit(eng, "gift", "小心心", {"gift_name": "小心心", "total_price": 1})
    assert d["reason"] == "interrupted"
    assert d["accepted"] is True
    assert eng.seq["rule_id"] == "ga"


def test_exit_phase_trigger_interrupts_immediately():
    """exit 步（main_done=True）期触发：不受无缝门约束，立即打断。"""
    NOW[0] = 1000.0
    eng = _engine([_kw_rule(), _gift_rule()])
    _start_kw_main(eng)
    _finish_current(eng)                    # 主线播完（无抢占请求）→ exit
    assert eng.seq["main_done"] is True
    d = _hit(eng, "gift", "小心心", {"gift_name": "小心心", "total_price": 1})
    assert d["reason"] == "interrupted"
    assert eng.seq["rule_id"] == "ga"


def test_invalid_slot_rule_falls_through():
    """槽位规则禁用/删除 → 主线播完弃请求按原序列推进（不悬挂）。"""
    NOW[0] = 1000.0
    eng = _engine([_kw_rule(), _gift_rule()])
    _start_kw_main(eng)
    _hit(eng, "gift", "小心心", {"gift_name": "小心心", "total_price": 1})
    eng.enabled_rules = [r for r in eng.enabled_rules if r.rule_id != "ga"]
    eng.cfg.rules = list(eng.enabled_rules)
    cmds = _finish_current(eng)
    assert eng.seq["rule_id"] == "ka"       # 弃请求，正常推进 exit
    assert eng.seq["steps"][eng.seq["idx"]]["name"] == "exit"
    assert eng.seq.get("preempt_request") is None
    assert not any(isinstance(c, SwitchSegment) and c.segment == "G_ga"
                   for c in cmds)


def test_weight_gate_precedes_seamless_gate():
    """权重门先于无缝门：低权重触发直接排队（不登记槽位）。"""
    NOW[0] = 1000.0
    eng = _engine([_kw_rule(), _gift_rule()])
    # 反向：gift 主线（p=30）在播 + keyword 触发（p=10）→ O3 权重门排队
    d = _hit(eng, "gift", "小心心", {"gift_name": "小心心", "total_price": 1})
    assert d["accepted"] is True
    _accept_current(eng)
    d = _hit(eng, "keyword", "帮我点歌了")
    assert d["reason"] == "queued"
    assert eng.seq.get("preempt_request") is None
    assert len(eng.pending_queue) == 1
    assert eng.pending_queue[0]["rule_id"] == "ka"
