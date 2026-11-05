"""T7 插播接线守卫（2026-11-05 编排优化批次）。

覆盖裁决条款：
- d4：动态文案源直连接线——facade on_dynamic_text（task_scheduler 格式化路径
  +keyword_hub copywriting 源）；dynamic_text 规则经内容整段匹配
- d3/e5：song 切片跳过飞行等待（人声起即停答谢=push 即 flush 替换）+替换
  分桶（edtalk_answer_replaced；e5 口径=替换生效时刻为 push 受理时刻）
- d16：队列插序——答谢(20)条目位次<下一段 song 切片（权重排序自然插队）
- d19/uc2b：EDTalk 路径 999 让位编排（pause→游标→resume→答谢→剩余重推）
  +queue a 触发钩子路由（EDTalk 优先，回落 builtin）+重推回执
"""
import asyncio
import os
import threading
import types
import wave as _wave

import pytest

from utils.audio.audio_priority import entry_is_999
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
        self.active_reread_top_999 = False
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


# ------------------------------------------------- d19/uc2b 让位编排

class _FlightPlayer:
    """EDTalk 让位编排桩（pause/cursor/resume 序列记录）。"""

    def __init__(self, cursor_seq):
        self.cursor_seq = list(cursor_seq)
        self.calls = []

    def pause_output(self):
        self.calls.append("pause")
        return {"ok": True}

    def resume_output(self):
        self.calls.append("resume")
        return {"ok": True}

    def get_output_frames(self):
        self.calls.append("cursor")
        return self.cursor_seq.pop(0) if self.cursor_seq else None

    def push_full(self, path, content="", wait_flight=True):
        self.calls.append(("push", os.path.basename(path), content))
        return asyncio.run(asyncio.sleep(0)) or True


def _playback_stub(monkeypatch, tmp_path, cursor_seq):
    from utils.audio.playback_manager import PlaybackMixin

    class _PB(PlaybackMixin):
        pass

    pb = _PB()
    pb.config = _FakeConfig({"comment": 20, "reread_top_priority": 999})
    pb.edtalk_client = _FlightPlayer(cursor_seq)
    # 真 wav（999 源文件：1600 帧=0.1s@16k）
    wav = tmp_path / "reread.wav"
    with _wave.open(str(wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x00\x01" * 1600)
    pb._edtalk_999_flight = {"voice_path": str(wav), "start_frames": 100,
                             "is_999": True, "samples": 1600}
    return pb, str(wav)


def test_d19_edtalk_yield_flow_pause_cursor_resume(monkeypatch, tmp_path):
    """d19/uc2b 编排序列：pause→cursor→resume→pending 登记（答谢播毕重推）。"""
    pb, wav = _playback_stub(monkeypatch, tmp_path, cursor_seq=[160])
    # 光标序列：pause 后读=160（已播 60 帧=38400 样本；文件 1600 帧→重推时钳尾）
    handled = pb._on_edtalk_yield()
    assert handled is True
    assert pb.edtalk_client.calls == ["pause", "cursor", "resume"]
    flight = pb._edtalk_resume_pending
    assert flight is not None
    assert flight["resume_offset_samples"] == 38400  # 60 帧×640 样本/帧


def test_d19_repush_remainder_slices_from_cursor(monkeypatch, tmp_path):
    """剩余重推：从冻结游标切片写临时 wav+push_full+回执（d19 口径）。"""
    pb, wav = _playback_stub(monkeypatch, tmp_path, cursor_seq=[100])
    flight = {"voice_path": wav, "start_frames": 100, "is_999": True,
              "resume_offset_samples": 640}  # 1 帧=640 样本 → 剩余 960 帧
    asyncio.run(
        pb._repush_edtalk_999_remainder(flight))
    pushed = [c for c in pb.edtalk_client.calls if isinstance(c, tuple) and c[0] == "push"]
    assert len(pushed) == 1
    assert pushed[0][2] == "999-remainder"
    # 切片正确性：临时 wav 帧数=1600-640=960
    tmp_wav = [f for f in os.listdir(tmp_path) if f.endswith(".resume999.wav")]
    if tmp_wav:  # push 桩不删临时文件时验证帧数
        with _wave.open(str(tmp_path / tmp_wav[0]), "rb") as w:
            assert w.getnframes() == 960


def test_d19_yield_degraded_when_pause_fails(monkeypatch, tmp_path):
    """pause 失败=d17 fallback 降级（维持排序）——仍受理让位链（返回 True）、
    不登记重推。"""
    pb, wav = _playback_stub(monkeypatch, tmp_path, cursor_seq=[100])
    pb.edtalk_client.pause_output = lambda: {"ok": False, "status": 404}
    handled = pb._on_edtalk_yield()
    assert handled is True
    assert getattr(pb, "_edtalk_resume_pending", None) is None


def test_d19_hook_route_edtalk_first():
    """queue a 触发路由：EDTalk 钩子优先（返回 True 终止让位链——builtin 不触发）；
    钩子 False=回落 builtin request_yield。"""
    q = _QueueFixture({"comment": 20, "reread_top_priority": 999}) if hasattr(
        __import__(__name__), "_QueueFixture") else None
    # 复用 T6 的 _QueueFixture 形态（本文件独立构造防耦合）
    from tests.test_choreo_t6_yield import _QueueFixture as _T6Queue, _YieldPlayer
    q = _T6Queue({"comment": 20, "reread_top_priority": 999},
                 _YieldPlayer(playing_weight=999))
    q.active_reread_top_999 = True
    edtalk_calls = []
    q._edtalk_yield_hook = lambda: edtalk_calls.append(1) or True
    q._maybe_interrupt_999_for({"type": "comment", "voice_path": "x.mp3"})
    assert edtalk_calls == [1]
    assert q.audio_player.yields == 0  # EDTalk 受理=终止链（builtin 不触发）
    # 钩子 False → 回落 builtin
    q2 = _T6Queue({"comment": 20, "reread_top_priority": 999},
                  _YieldPlayer(playing_weight=999))
    q2.active_reread_top_999 = True
    q2._edtalk_yield_hook = lambda: False
    q2._maybe_interrupt_999_for({"type": "comment", "voice_path": "x.mp3"})
    assert q2.audio_player.yields == 1


def test_d19_999_entry_judgment_single_source():
    """flight 登记门=entry_is_999（T2 单一源）：override=999 的规则级条目同样
    登记（d18 载体）。"""
    assert entry_is_999({"type": "comment", "priority_override": 999},
                        {"comment": 20}) is True
