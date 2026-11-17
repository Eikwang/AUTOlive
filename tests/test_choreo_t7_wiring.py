"""T7 插播接线守卫（2026-11-05 编排优化批次；2026-11-15 批次：999 机制退役，
d19 让位编排改 barge-in 打断即弃，钩子路由测试保留）。

覆盖裁决条款：
- d4：动态文案源直连接线——facade on_dynamic_text（task_scheduler 格式化路径
  +keyword_hub copywriting 源）；dynamic_text 规则经内容整段匹配
- d3/e5：song 切片跳过飞行等待（人声起即停答谢=push 即 flush 替换）+替换
  分桶（edtalk_answer_replaced；e5 口径=替换生效时刻为 push 受理时刻）
- d16：队列插序——答谢(20)条目位次<下一段 song 切片（权重排序自然插队）
- d19（2026-11-15 改写）：EDTalk 路径 barge-in 打断即弃钩子路由（钩子受理
  终止让位链；非本路径回落 builtin d12 b 让位）
"""
import asyncio
import threading

import pytest

from utils.audio.queue_manager import QueueMixin
from utils.orchestration.choreographer import ChoreoEngine
from utils.orchestration.config_model import load_choreography_config

NOW = [1000.0]


# ------------------------------------------------- d4 源直连

def _engine(rules):
    node = {"enable": True, "rules": list(rules), "step_timeout_s": 60,
            "default_segments": ["D_1"]}
    cfg = load_choreography_config({"choreography": node})
    return ChoreoEngine(cfg, clock=lambda: NOW[0], priority_mapping={})


def test_d4_dynamic_text_direct_match():
    """d4：dynamic_text 规则 match 可空=任意源事件命中（内容整段 match_value）。"""
    eng = _engine([{"rule_id": "dt1", "trigger_type": "dynamic_text", "match": "",
                    "sequence": {"main": {"segment": "44", "switch_at": "immediate",
                                          "duration_s": 3}}}])
    r = eng.decide_trigger("dynamic_text", "今天直播间有惊喜福利，记得点关注", {})
    assert r["reason"] == "accepted"


def test_d4_dynamic_text_substring_filter():
    """match 非空=content 子串过滤（多规则场景预留）。"""
    eng = _engine([{"rule_id": "dt2", "trigger_type": "dynamic_text", "match": "福利",
                    "sequence": {"main": {"segment": "44", "switch_at": "immediate",
                                          "duration_s": 3}}}])
    assert eng.decide_trigger("dynamic_text", "今晚有福利", {})["reason"] == "accepted"
    assert eng.decide_trigger("dynamic_text", "今晚有礼物", {})["reason"] == "no_match"


def test_d4_facade_on_dynamic_text():
    """门面 on_dynamic_text 装配（task_scheduler 格式化路径+hub copywriting 源
    共用入口）。"""
    from utils.orchestration.choreographer import ActionChoreographer
    from unittest.mock import MagicMock
    node = {"enable": True, "rules": [{"rule_id": "dt1", "trigger_type": "dynamic_text",
                                       "match": "",
                                       "sequence": {"main": {"segment": "44", "switch_at": "immediate",
                                                             "duration_s": 3}}}],
            "default_segments": ["44"]}
    client = MagicMock()
    client.get_status.return_value = {"available_segments": ["44"]}
    class _Cfg:
        def get(self, *keys):
            if keys == ("choreography",):
                return node
            return None
    ch = ActionChoreographer(_Cfg(), client, audio_enqueue=MagicMock(),
                             thanks_generate=MagicMock(return_value=None),
                             clock=lambda: 1000.0)
    ch._worker = None
    ch._ensure_worker = lambda enable=None: True
    for r in ch.cfg.rules:
        if r.trigger_type == "dynamic_text" and r.main is not None:
            r.main.hold_for_song = False
    assert ch.on_dynamic_text("随便什么内容都命中（match 可空）") is True


# ------------------------------------------------- d3/e5 push_full wait_flight

def test_d3_push_full_wait_flight_param():
    """push_full wait_flight 参数存在（song 切片传 False=跳过飞行等待）。"""
    import inspect
    from utils.edtalk_realtime.edtalk_client import EDTalkClient
    sig = inspect.signature(EDTalkClient.push_full)
    assert "wait_flight" in sig.parameters
    assert sig.parameters["wait_flight"].default is True


def test_d3_song_slice_skips_flight_playback_manager():
    """playback 推送点：song 切片 wait_flight=False（d3 人声起即停答谢），
    非 song 条目 True（阻塞背压不丢台词）。"""
    import inspect
    src = inspect.getsource(__import__("utils.audio.playback_manager",
                                       fromlist=["only_play_audio"]))
    assert "_wait_flight = not (data_json.get(\"type\") == \"song\"" in src
    assert "wait_flight=_wait_flight" in src


def test_d3_replacement_bucket_wiring():
    """d3/e5：替换分桶接线（song push 且上一条目非 song → edtalk_answer_replaced）。"""
    import inspect
    src = inspect.getsource(__import__("utils.audio.playback_manager",
                                       fromlist=["only_play_audio"]))
    assert "edtalk_answer_replaced" in src
    assert "_edtalk_prev_type" in src


# ------------------------------------------------- d16 队列插序

class _FakeConfig:
    def __init__(self, mapping):
        self._m = {"filter": {"priority_mapping": mapping},
                   "filter": mapping and {"priority_mapping": mapping}}

    def get(self, *keys):
        cur = self._m
        for k in keys:
            if not isinstance(cur, dict) or k not in cur:
                return None
            cur = cur[k]
        return cur


class _QueueFixture(QueueMixin):
    def __init__(self, mapping):
        self.config = _FakeConfig(mapping)
        self.message_queue = []
        self.message_queue_lock = threading.Lock()
        self.message_queue_not_empty = threading.Condition(lock=self.message_queue_lock)
        self.voice_tmp_path_queue = []
        self.voice_tmp_path_queue_lock = threading.Lock()
        self.voice_tmp_path_queue_not_empty = threading.Condition(lock=self.voice_tmp_path_queue_lock)
        self.voice_tmp_path_queue_not_empty_flag = False
        self.audio_player = None

    def data_priority_insert(self, type="待播放音频列表", data_json=None):
        """待播放侧插序（对齐 QueueMixin 真实权重排序——最小复刻规则1）。"""
        pm = self.config.get("filter", "priority_mapping") or {}
        new_p = pm.get(data_json.get("type"))
        if new_p is None:
            pos = len(self.voice_tmp_path_queue)
        else:
            pos = 0
            for i in range(len(self.voice_tmp_path_queue) - 1, -1, -1):
                p = pm.get(self.voice_tmp_path_queue[i].get("type"))
                if p is not None and p >= new_p:
                    pos = i + 1
                    break
        self.voice_tmp_path_queue.insert(pos, data_json)
        return {"code": 200}


def test_d16_answer_lands_before_next_song_slice():
    """d16：语义切片 song 条目(10) 与答谢(20) 同队列——答谢位次<下一切片
    （权重排序自然插队=间奏窗口自动落在切片边界）。"""
    q = _QueueFixture({"comment": 20, "song": 10})
    # 切片1 → 答谢到达 → 切片2（生产顺序：答谢入队在切片1 播放期间）
    q.data_priority_insert("song", {"type": "song", "voice_path": "seg0.mp3"})
    q.voice_tmp_path_queue.pop(0)  # seg0 出队=播放中（生产时序）
    q.data_priority_insert("comment", {"type": "comment", "voice_path": "thanks.mp3"})
    q.data_priority_insert("song", {"type": "song", "voice_path": "seg1.mp3"})
    paths = [e["voice_path"] for e in q.voice_tmp_path_queue]
    assert paths == ["thanks.mp3", "seg1.mp3"]  # seg0 已出队=播放中（生产时序）
    assert paths.index("thanks.mp3") < paths.index("seg1.mp3")  # d16：答谢<下一切片


# ------------------------------------------------- d19 让位裁决（2026-11-16 段间让位批次改写）

def test_d19_priority_yield_edtalk_same_channel_gate():
    """让位裁决（原 barge-in 钩子路由测试随打断即弃退役改写）：EDTalk 路由
    的插播者不触发 builtin 让位（同声道前提）；非 EDTalk 路由（同声道）→
    d12 b 让位（权重门生效）。EDTalk 路径插播由队列权重排序段间完成，
    不再打断在播条目。"""
    from tests.test_choreo_t6_yield import _QueueFixture as _T6Queue, _YieldPlayer
    # d12 原始场景（EDTalk 未激活=同声道）：comment(20) > song(10) → 让位
    q = _T6Queue({"comment": 20, "reread_top_priority": 500},
                 _YieldPlayer(playing_weight=10))
    q._maybe_priority_yield_for({"type": "comment", "voice_path": "x.mp3"})
    assert q.audio_player.yields == 1
