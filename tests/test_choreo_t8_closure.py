"""T8 收官守卫：resident 切回机制（d13/N2/C3）+观测分桶（X1/X4/e7）+矩阵格。

覆盖裁决条款：
- ③编排队列切回（d13/N2/C3）：dynamic_text 999 序列=resident——他键打断转挂起
  （镜像音频面 uc5b 字面语义：任意 standard 触发让位，权重门不适用——§3 Case B
  礼物答谢(30) 须打断 999 文案(999) 视觉）；打断者完成切回播报画面（保险丝
  到期回默认=播报已结束的诚实降级）；默念（无规则）=零编排行为（N2）
- d6 矩阵格：(a) 999×普通序列=999 抢占（权重门）；(c) 挂起×song 播完=切回
  （单槽语义：最新挂起覆盖——深层嵌套挂起栈不做，d22 看门狗兜底）
- F-F：N 条答谢竞争一间奏窗（队列序=权重；切片 no-wait push 截断尾条=d3 机制）
- X1/e7：间奏插播桶（song_interlude_insert/backlog+edtalk_answer_replaced）
- X4：lip_mode 切换观测桶（record_lip_switch）
"""
import threading
from unittest.mock import MagicMock

from utils.orchestration.choreographer import ChoreoEngine
from utils.orchestration.config_model import load_choreography_config

NOW = [1000.0]


def _rule(rid, t, match="", weight=None, **over):
    r = {"rule_id": rid, "trigger_type": t, "match": match,
         "interrupt": "standard",
         "sequence": {"main": {"segment": "M_" + rid, "switch_at": "immediate",
                               "duration_s": 5}}}
    if weight is not None:
        r["priority_override"] = weight
    r.update(over)
    return r


def _engine(rules, over=None):
    node = {"enable": True, "rules": list(rules), "step_timeout_s": 60,
            "default_segments": ["D_1"]}
    if over:
        node.update(over)
    cfg = load_choreography_config({"choreography": node})
    return ChoreoEngine(cfg, clock=lambda: NOW[0], priority_mapping={})


def _hit(eng, t, match=""):
    data = {"gift_name": match, "total_price": 1} if t == "gift" else {}
    return eng.decide_trigger(t, match, data)


def _accept(eng, result):
    sw = next(c for c in result["commands"] if type(c).__name__ == "SwitchSegment")
    return eng.on_switch_result(sw.step_id, True, NOW[0])


def _resident_engine():
    """dynamic_text 999 规则（arbitration_weights 覆盖=规则级 999 载体 d18）。"""
    return _engine([_rule("dt", "dynamic_text"),
                    _rule("g", "gift", match="小心心")],
                   over={"arbitration_weights": {"dynamic_text": 999,
                                                 "gift": 30, "song": 20,
                                                 "keyword": 10}})


# ------------------------------------------------- resident 机制

def test_resident_flag_on_999_dynamic_text():
    eng = _resident_engine()
    r = _hit(eng, "dynamic_text", "福利内容")
    assert r["reason"] == "accepted"
    _accept(eng, r)
    assert eng.seq.get("resident") is True  # 999 dynamic_text=resident


def test_resident_flag_off_for_normal_weight():
    eng = _engine([_rule("dt", "dynamic_text")])  # 默认权重 15（d10 表）
    r = _hit(eng, "dynamic_text")
    _accept(eng, r)
    assert eng.seq.get("resident") is False


def test_d13_resident_yields_to_gift_bypassing_weight_gate():
    """§3 Case B：礼物答谢(30) 打断 999 文案(999) 视觉——权重门不适用
    （镜像音频面 uc5b 字面语义：999 被任何消息暂停=画面同拍让位）。"""
    NOW[0] = 1000.0
    eng = _resident_engine()
    _accept(eng, _hit(eng, "dynamic_text", "福利"))
    NOW[0] = 1003.0
    r = _hit(eng, "gift", "小心心")
    assert r["reason"] == "interrupted"  # 非 queued（权重门被 resident 让位绕过）
    assert eng.seq["rule_id"] == "g"
    susp = eng.song_suspended
    assert susp["kind"] == "resident"
    assert susp["rule_id"] == "dt"
    assert susp["deadline"] == pytest_approx(1063.0)  # step_timeout 保险丝（1003+60）


def pytest_approx(v):
    import pytest
    return pytest.approx(v)


def test_d13_recovery_back_to_broadcast_and_fuse_expiry():
    """打断者完成→切回播报画面（resident hold+保险丝）；保险丝到期→默认池。"""
    NOW[0] = 1000.0
    eng = _resident_engine()
    _accept(eng, _hit(eng, "dynamic_text", "福利"))
    NOW[0] = 1003.0
    d = _hit(eng, "gift", "小心心")  # 挂起（deadline=1063）
    sw = next(c for c in d["commands"] if type(c).__name__ == "SwitchSegment")
    eng.on_switch_result(sw.step_id, True, NOW[0])  # 切换接受（deadline 建立前提）
    NOW[0] = 1009.0
    eng.on_tick(NOW[0])  # gift 主步完成（duration 5）→ 条件1 → 切回
    assert eng.seq["rule_id"] == "dt"
    assert eng.seq.get("resident") is True
    assert eng.seq["steps"][0]["hold_for_song"] is False
    assert eng.state == "sequence"
    # 保险丝到期（deadline=1063）→ 默认池（播报已结束的诚实降级）
    NOW[0] = 1064.0
    cmds = eng.on_tick(NOW[0])
    assert eng.state == "default"
    assert eng.song_suspended is None
    assert [c for c in cmds if type(c).__name__ == "RestoreDefault"]


def test_d6c_resident_recovery_reinterruptible():
    """切回后的 resident 再让位（循环播报期间多次打断=§3 场景常态）。"""
    NOW[0] = 1000.0
    eng = _resident_engine()
    _accept(eng, _hit(eng, "dynamic_text", "福利"))
    NOW[0] = 1003.0
    d1 = _hit(eng, "gift", "小心心")
    _accept(eng, d1)  # 切换接受（deadline 建立前提）
    NOW[0] = 1009.0
    eng.on_tick(NOW[0])  # 切回 resident
    NOW[0] = 1011.0
    r2 = _hit(eng, "gift", "小心心")
    assert r2["reason"] == "interrupted"  # 再让位
    assert eng.song_suspended["kind"] == "resident"


def test_d6a_999_preempts_normal_sequence():
    """d6(a)：999 触发在播普通序列=999 抢占（现行 standard 抢占语义）。"""
    NOW[0] = 1000.0
    eng = _engine([_rule("k", "keyword", match="点词"),
                   _rule("dt", "dynamic_text")],
                  over={"arbitration_weights": {"dynamic_text": 999, "keyword": 10}})
    _accept(eng, _hit(eng, "keyword", "点词"))
    NOW[0] = 1003.0
    r = _hit(eng, "dynamic_text", "内容")
    assert r["reason"] == "interrupted"  # 999 > 10 → 抢占
    assert eng.seq["rule_id"] == "dt"


def test_n2_no_rule_no_choreo_behavior():
    """N2：默念（无 dynamic_text 规则）=零编排行为（no_match，文案照常播报——
    回默认画面语义天然成立）。"""
    eng = _engine([_rule("g", "gift", match="小心心")])
    r = _hit(eng, "dynamic_text", "无规则内容")
    assert r["reason"] == "no_match"
    assert r["accepted"] is False


# ------------------------------------------------- F-F 矩阵格

def test_ff_n_answers_compete_queue_order():
    """F-F：N 条答谢竞争一间奏窗——队列序=权重（同权 FIFO），切片 no-wait push
    截断尾条（d3 机制承接：edtalk_answer_replaced）。"""
    from utils.audio.queue_manager import QueueMixin

    class _Cfg:
        def __init__(self):
            self._m = {"filter": {"priority_mapping": {"comment": 20, "song": 10},
                                  "voice_tmp_path_queue_max_len": 100,
                                  "message_queue_max_len": 100,
                                  "voice_tmp_path_queue_min_start_play": 3}}

        def get(self, *keys):
            cur = self._m
            for k in keys:
                if not isinstance(cur, dict) or k not in cur:
                    return None
                cur = cur[k]
            return cur

    class _Q(QueueMixin):
        def __init__(self):
            self.config = _Cfg()
            self.active_reread_top_999 = False
            self.message_queue = []
            self.message_queue_lock = threading.Lock()
            self.message_queue_not_empty = threading.Condition(lock=self.message_queue_lock)
            self.voice_tmp_path_queue = []
            self.voice_tmp_path_queue_lock = threading.Lock()
            self.voice_tmp_path_queue_not_empty = threading.Condition(
                lock=self.voice_tmp_path_queue_lock)
            self.voice_tmp_path_queue_not_empty_flag = False
            self.audio_player = None

    import threading as _th
    q = _Q()
    # 间奏窗：ans1/ans2 依次入队（同权 FIFO）→ 切片后入（权重最低→队尾）
    q.data_priority_insert("song", {"type": "comment", "voice_path": "ans1.mp3"})
    q.data_priority_insert("song", {"type": "comment", "voice_path": "ans2.mp3"})
    q.data_priority_insert("song", {"type": "song", "voice_path": "seg1.mp3"})
    paths = [e["voice_path"] for e in q.voice_tmp_path_queue]
    assert paths == ["ans1.mp3", "ans2.mp3", "seg1.mp3"]
    # 串行消费：ans1 播于窗内；ans2 越界→切片 no-wait push 截断（d3 替换；
    # 机制由 T7 wait_flight=False+edtalk_answer_replaced 承接——此处固化
    # 队列序不变量）


# ------------------------------------------------- X1/X4 观测接线

def test_x1_interlude_buckets_wired():
    """X1/e7：插播/积压/替换三桶接线（push 循环源级断言）。"""
    import inspect
    from utils.audio import playback_manager
    src = inspect.getsource(playback_manager)
    assert "song_interlude_insert" in src
    assert "song_interlude_backlog" in src
    assert "edtalk_answer_replaced" in src


def test_x4_lip_switch_bucket_wired():
    """X4：lip 切换观测接线（_apply_lip_linkage 缓存变更点）。"""
    import inspect
    from utils.orchestration import choreographer
    src = inspect.getsource(choreographer)
    assert "record_lip_switch" in src


def test_x4_record_lip_switch_function():
    from utils.obs_stats import record_lip_switch
    record_lip_switch("off", 3.2)
    record_lip_switch("on", 1.1)  # 不抛=通过（get_audio_info 快照含事件面）
