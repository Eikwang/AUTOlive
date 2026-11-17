"""T4 song 序列仲裁状态机守卫（2026-11-05 编排优化批次；2026-11-16 动态文案
插播&切回批次改写）。

覆盖裁决条款：
- rejected（2026-11-16 用户裁决口径；本批动作编排审查补齐 queue 模式）：song
  在播 + 其它规则触发（如礼物，standard/queue 均同）=放弃该触发
  （accepted=false / takeover=false / 不排队不挂起），保持点歌画面；
  礼物答谢等音频语义走原生链不受影响（编排只管画面）
- d11：song hold 中 song 重触发=点歌队列追加承接（编排序列保持 hold 不重排，
  2026-11-16 批次保持不变——承接归音频侧歌曲队列）
- d22：总开关 off=回退现行抢占终止语义（旧 exit 衔接保持）

历史 d9/e4（song hold 挂起）/d2（双结束态恢复）/e9/F-B/d22 硬上限守卫随
song hold 挂起路径退役迁移：挂起机制现仅服务 resident（kind=resident），
守卫见 test_choreo_t10_resident_insert.py。
"""
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
    """切换命令接受回填（seq 级 hold_for_song 标志在接受点置位）。"""
    sw = next(c for c in result["commands"] if type(c).__name__ == "SwitchSegment")
    return eng.on_switch_result(sw.step_id, True, NOW[0])


def _song_hold_active(eng, remaining=200.0, match=""):
    """构造 song hold 活跃态（主线 hold + 歌曲锚 deadline）。"""
    r = _trigger_song(eng, match)
    assert r["reason"] == "accepted"
    _accept_switch(eng, r)
    eng.on_song_started("test_song", remaining, NOW[0])
    assert eng.seq.get("hold_for_song") is True


# ------------------------------------------------- rejected 放弃语义（2026-11-16）

def test_rejected_gift_abandoned_during_song_hold():
    """song 在播 + 礼物规则触发=放弃该规则（不排队不挂起，保持点歌画面）。"""
    NOW[0] = 1000.0
    eng = _engine([_song_rule(), _gift_rule()])
    _song_hold_active(eng, remaining=200.0)  # deadline=1200
    before = dict(eng.seq)
    d = _trigger_gift(eng)
    assert d["reason"] == "rejected"          # 放弃（不排队不挂起）
    assert d["accepted"] is False
    assert d["takeover"] is False             # 礼物答谢音频语义走原生链（编排只管画面）
    assert eng.seq["rule_id"] == "song_a"     # 点歌画面保持
    assert eng.seq == before                  # 序列零扰动
    assert eng.song_suspended is None         # 无挂起（原 d9 挂起路径退役）
    assert eng.counters.get("g1", 0) == 0     # 不重入计数
    assert eng.pending_queue == []            # 不排队（原 O3 排队路径让位于放弃）
    assert eng.state == "sequence"
    # 无撤音指令（放弃=编排零动作，歌曲链路与答谢链路均不触碰）
    assert [c for c in d["commands"] if type(c).__name__ == "CancelAudio"] == []
    # song 结束（deadline 到点）→ 既有超时机制回默认轮播
    NOW[0] = 1201.0
    cmds = eng.on_tick(NOW[0])
    assert eng.state == "default"
    assert [c for c in cmds if type(c).__name__ == "RestoreDefault"]


def test_rejected_low_weight_keyword_also_abandoned():
    """低权重 standard 触发（keyword 10 < song 20）同样放弃——放弃先于权重门
    （不排队：歌曲期间关键词画面不接管也不排队，触发直接放弃）。"""
    NOW[0] = 1000.0
    kw = {"rule_id": "k1", "trigger_type": "keyword", "match": "任意",
          "interrupt": "standard",
          "sequence": {"main": {"segment": "K_词", "switch_at": "immediate",
                                "duration_s": 5}}}
    eng = _engine([_song_rule(), kw])
    _song_hold_active(eng, remaining=200.0)
    r = eng.decide_trigger("keyword", "任意词", {})
    assert r["reason"] == "rejected"
    assert r["accepted"] is False
    assert eng.seq["rule_id"] == "song_a"
    assert eng.pending_queue == []


def test_rejected_queue_mode_also_abandoned_during_song_hold():
    """song 在播 + interrupt=queue 规则触发=同样放弃（设计 8：动作编排无需保留
    队列，只保留再次命中的点歌规则）——queue 分支不得先于放弃分支入队，
    歌毕重放切换画面的旧行为已退役。"""
    NOW[0] = 1000.0
    kw = {"rule_id": "kq", "trigger_type": "keyword", "match": "任意",
          "interrupt": "queue",
          "sequence": {"main": {"segment": "K_队", "switch_at": "immediate",
                                "duration_s": 5}}}
    eng = _engine([_song_rule(), kw])
    _song_hold_active(eng, remaining=200.0)
    r = eng.decide_trigger("keyword", "任意词", {})
    assert r["reason"] == "rejected"
    assert r["accepted"] is False
    assert eng.seq["rule_id"] == "song_a"     # 点歌画面保持
    assert eng.pending_queue == []            # 编排队列零保留
    # song 结束回默认：排队无残留，不发生重放切换
    NOW[0] = 1201.0
    cmds = eng.on_tick(NOW[0])
    assert eng.state == "default"
    assert eng.pending_queue == []
    assert [c for c in cmds if type(c).__name__ == "LogCmd"
            and "出队" in c.msg] == []


def test_rejected_then_song_queued_still_holds():
    """放弃后 song×song 承接不受影响（d11 保持）——点歌队列照常追加。"""
    NOW[0] = 1000.0
    eng = _engine([_song_rule("song_a"), _song_rule("song_b"), _gift_rule()])
    eng.cfg.rule_by_id("song_a").match = "晴天"
    eng.cfg.rule_by_id("song_b").match = "七里香"
    _song_hold_active(eng, remaining=200.0, match="晴天")
    d = _trigger_gift(eng)
    assert d["reason"] == "rejected"
    before = dict(eng.seq)
    r = _trigger_song(eng, "七里香")  # 另一 song 规则重触发 → d11 承接
    assert r["reason"] == "song_queued"
    assert r["accepted"] is True  # 歌曲请求被承接（音频侧入歌曲队列）
    assert eng.seq == before  # 序列零扰动


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


# ------------------------------------------------- d22 总开关

def test_switch_off_falls_back_to_terminate_semantics():
    """总开关 off：song hold 中 standard 触发回退现行抢占终止语义（d22）——
    拒绝判定与挂起机制均被门控短路，旧 exit 衔接照常插入。"""
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
