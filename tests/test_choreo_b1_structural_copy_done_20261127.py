"""B1 结构化终结（2026-11-27 阶段二）：文案 copy_done 判定改结构化组事件。

主判定三条件：本组 group_pending<=0 + 其它文案组 copy_groups_pending<=0 +
一致性校验（双向包含）。尾窗启发式（min(8) 自适应尾对尾）退役——
重复尾句/末段归一化<8 越界/两文首尾重叠三类边角随之消除。

覆盖：
- t1 末段回执+本组空+全局空+内容匹配 → copy_done 落位（含 <8 字末段边角）
- t2 本组非空（早段回执）→ 不判
- t3 其它文案组 pending>0 → 不判（异组残留段回执）
- t4 内容不匹配（一致性防线）→ 不判
- t5 挂起侧同语义（⑦-B 挂起登记判定同步）
- t6 保险丝续期保持（组未空回执仍刷 loop_deadline）
"""
import pytest

from utils.orchestration.choreographer import ChoreoEngine
from utils.orchestration.config_model import load_choreography_config

NOW = [1000.0]
COPY = "哈喽大家好，欢迎来到直播间，今天天气非常好，希望我的推荐能帮到大家，谢谢大家。"
TAIL = "希望我的推荐能帮到大家。"   # 末段（归一化 12 字，旧 min(8) 窗口边界场景）
SHORT_TAIL = "谢谢大家。"          # 末段（归一化 4 字——旧 8 字窗越段边界场景）
OTHER = "大家好我是新来的主播小美，很开心认识大家。"


def _dt_rule(rid="dt", main_dur=3, **over):
    seq = {"enter": {"segment": "E_" + rid, "duration_s": 1},
           "exit": {"segment": "X_" + rid, "duration_s": 2}}
    main = {"segment": "M_" + rid, "switch_at": "segment_end", "duration_s": main_dur}
    seq["main"] = main
    r = {"rule_id": rid, "trigger_type": "dynamic_text", "match": "",
         "interrupt": "standard", "sequence": seq}
    r.update(over)
    return r


def _gift_rule(rid="ga", main_dur=30, **over):
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


def _start_resident(eng, text=COPY):
    d = eng.decide_trigger("dynamic_text", "", {}, completion_text=text)
    assert d["accepted"] is True
    step = eng.seq["steps"][eng.seq["idx"]]
    eng.on_switch_result(step["step_id"], True, NOW[0])
    # 推进到 main hold（enter 完成）
    NOW[0] = eng.seq["current_deadline"] + 0.01
    eng.on_tick(NOW[0])
    step = eng.seq["steps"][eng.seq["idx"]]
    if step["name"] == "main":
        eng.on_switch_result(step["step_id"], True, NOW[0])


def test_structural_copy_done_terminal_segment():
    """t1：末段回执+本组空+全局空 → copy_done 落位；含 <8 字末段边角（旧尾窗
    越段边界永不命中的场景）。"""
    for tail in (TAIL, SHORT_TAIL):
        NOW[0] = 1000.0
        eng = _engine([_dt_rule()])
        _start_resident(eng, COPY)
        cmds = eng.on_audio_complete("trends_copywriting", tail, NOW[0], None,
                                     group_pending=0, copy_groups_pending=0)
        assert eng.seq["copy_done"] is True
        assert eng.metrics["copy_done_structural"] == 1


def test_structural_copy_done_group_not_empty():
    """t2：本组非空（早段回执）→ 不判完成（组事件主条件）。"""
    NOW[0] = 1000.0
    eng = _engine([_dt_rule()])
    _start_resident(eng, COPY)
    mid = "哈喽大家好，欢迎来到直播间，"
    eng.on_audio_complete("trends_copywriting", mid, NOW[0], None,
                          group_pending=3, copy_groups_pending=0)
    assert eng.seq.get("copy_done") is not True


def test_structural_copy_done_other_group_pending():
    """t3：其它文案组 pending>0（异组残留段回执、全局未消费完）→ 不判完成。

    旧尾窗启发式在此场景靠文本不匹配兜底；B1 由结构条件独立闭合。"""
    NOW[0] = 1000.0
    eng = _engine([_dt_rule()])
    _start_resident(eng, COPY)
    eng.on_audio_complete("trends_copywriting", TAIL, NOW[0], None,
                          group_pending=0, copy_groups_pending=1)
    assert eng.seq.get("copy_done") is not True


def test_structural_copy_done_content_mismatch():
    """t4：内容不匹配（一致性防线）→ 不判完成。

    场景：resident 锚=B 文案，回执=A 文案末段（A 组空+全局空——A 比 B 先
    触发且先消费完）。结构条件满足但文本无包含关系 → 防线拦截。"""
    NOW[0] = 1000.0
    eng = _engine([_dt_rule()])
    _start_resident(eng, OTHER)   # 锚=OTHER
    eng.on_audio_complete("trends_copywriting", TAIL, NOW[0], None,
                          group_pending=0, copy_groups_pending=0)
    assert eng.seq.get("copy_done") is not True


def test_structural_copy_done_suspended_side():
    """t5：⑦-B 挂起侧同语义——挂起登记 copy_done 判定同步改结构化组事件。"""
    NOW[0] = 1000.0
    eng = _engine([_dt_rule("dt"), _gift_rule("ga", main_dur=60)])
    _start_resident(eng, COPY)
    # gift 打断 → resident 挂起（打断者序列在播）
    d = eng.decide_trigger("gift", "小心心", {"username": "u", "gift_name": "小心心",
                                              "gift_num": 1, "num": 1,
                                              "total_price": 1, "unit_price": 1.0})
    assert d["accepted"] is True and eng.song_suspended is not None
    assert eng.song_suspended["kind"] == "resident"
    # 挂起期间末段回执（本组空+全局空）→ susp copy_done 登记
    eng.on_audio_complete("trends_copywriting", SHORT_TAIL, NOW[0], None,
                          group_pending=0, copy_groups_pending=0)
    assert eng.song_suspended["copy_done"] is True


def test_structural_copy_loop_deadline_renewal_kept():
    """t6：保险丝续期保持——组未空回执（早段/异组）仍刷 loop_deadline
    （消费链存活证明语义，与终结判定解耦）。"""
    NOW[0] = 1000.0
    eng = _engine([_dt_rule()])
    _start_resident(eng, COPY)
    old_dl = eng.seq.get("loop_deadline")
    NOW[0] += 10.0
    eng.on_audio_complete("trends_copywriting", "哈喽大家好，", NOW[0], None,
                          group_pending=5, copy_groups_pending=0)
    assert eng.seq["loop_deadline"] > old_dl
    assert eng.seq.get("copy_done") is not True
