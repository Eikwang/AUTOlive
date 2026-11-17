# -*- coding: utf-8 -*-
"""段间让位：动态文案插播&切回 + barge-in 退役（2026-11-16 批次）。

覆盖裁决条款：
- barge-in 打断即弃退役：playback_manager 无 _on_edtalk_barge_in /
  INTERRUPTIBLE_COPY_TYPES；EDTalkClient 无 request_interrupt / 打断事件；
  queue_manager 无 _edtalk_barge_in_hook 钩子链（原 _maybe_barge_in_for 改名
  _maybe_priority_yield_for，仅保留 d12 builtin 让位）
- 段间让位（用户确认设计：播完当前段再插播）：动态文案切分段（权重 10）在队
  时，更高权重音频（comment=50）入队 → 队列排序高权重在前 → 消费循环先消费
  插播音频，剩余文案段自然续播（切回）——无需 999 特权机制
- 闲时门（task_scheduler._is_playback_idle）：待合成队列空+待播放队列空+
  无 EDTalk 在飞条目=空闲；动态文案生成前/入队前两查，忙则跳过本轮

风格对齐：宿主 stub 手工组装（test_audio_playback_review/test_t9_dynamic_copy
同款模式）。
"""
import asyncio
import threading
import time
from types import SimpleNamespace

import pytest

from utils.audio.playback_manager import PlaybackMixin
from utils.audio.queue_manager import QueueMixin
from utils.edtalk_realtime.edtalk_client import EDTalkClient


# ------------------------------------------- barge-in 退役断言

def test_barge_in_mechanism_retired():
    """barge-in 打断即弃整体退役：钩子方法/类型集/打断事件均不存在（防复活）。"""
    assert not hasattr(PlaybackMixin, "_on_edtalk_barge_in")
    assert not hasattr(PlaybackMixin, "_maybe_barge_in_for")
    from utils.audio import playback_manager as _pm
    assert not hasattr(_pm, "INTERRUPTIBLE_COPY_TYPES")
    client = EDTalkClient({})
    assert not hasattr(client, "request_interrupt")
    assert not hasattr(client, "_interrupt_event")
    q = QueueMixin.__new__(QueueMixin)
    assert not hasattr(q, "_edtalk_barge_in_hook")


# ------------------------------------------- 段间让位（队列排序语义）

class _QueueCfg:
    def __init__(self, mapping):
        self._m = {"filter": {"priority_mapping": mapping,
                              "voice_tmp_path_queue_max_len": 100,
                              "voice_tmp_path_queue_min_start_play": 1,
                              "message_queue_max_len": 100}}

    def get(self, *keys):
        cur = self._m
        for k in keys:
            if not isinstance(cur, dict) or k not in cur:
                return None
            cur = cur[k]
        return cur


class _QueueFixture(QueueMixin):
    def __init__(self, mapping):
        self.config = _QueueCfg(mapping)
        self.message_queue = []
        self.message_queue_lock = threading.Lock()
        self.message_queue_not_empty = threading.Condition(lock=self.message_queue_lock)
        self.voice_tmp_path_queue = []
        self.voice_tmp_path_queue_lock = threading.Lock()
        self.voice_tmp_path_queue_not_empty = threading.Condition(lock=self.voice_tmp_path_queue_lock)
        self.voice_tmp_path_queue_not_empty_flag = False
        self.audio_player = None


def _copy_segment(seq, total=3):
    """动态文案切分段条目（split_group 组绑定，权重 10）"""
    return {"type": "trends_copywriting", "voice_path": f"copy_{seq}.mp3",
            "content": f"文案第{seq + 1}段",
            "split_group": {"id": "g1", "seq": seq, "total": total}}


def test_segment_yield_high_priority_first():
    """段间让位核心：文案 3 段已在队 → comment（50）后入队 → 队列排序
    comment 在前。消费循环逐条 pop 队首：先插播 comment，播完后剩余文案段
    继续消费（切回续播），不弃段不重推。"""
    q = _QueueFixture({"trends_copywriting": 10, "comment": 50})
    for seq in range(3):
        assert q.data_priority_insert(
            "song", _copy_segment(seq))["code"] == 200
    assert q.data_priority_insert(
        "song", {"type": "comment", "voice_path": "reply.mp3",
                 "content": "弹幕回复"})["code"] == 200
    types = [e["type"] for e in q.voice_tmp_path_queue]
    assert types == ["comment", "trends_copywriting", "trends_copywriting",
                     "trends_copywriting"], \
        f"插播音频应排在全部文案段之前：{types}"


def test_segment_group_order_stable_among_copy():
    """同权重文案段保持 FIFO 稳定（切回时按切分顺序续播，不乱序）。"""
    q = _QueueFixture({"trends_copywriting": 10, "comment": 50})
    for seq in range(3):
        q.data_priority_insert("song", _copy_segment(seq))
    contents = [e["content"] for e in q.voice_tmp_path_queue]
    assert contents == ["文案第1段", "文案第2段", "文案第3段"]


def test_priority_yield_builtin_kept_after_rename():
    """d12 builtin 让位保留（改名后语义不变）：在播权重 40 + 新音频 50 →
    request_yield 触发；新音频路由 EDTalk（同声道前提）→ 不让位。"""
    q = _QueueFixture({"comment": 50, "key_mapping": 40})
    yielded = []
    q.audio_player = SimpleNamespace(
        request_yield=lambda: yielded.append(1),
        get_playing_weight=lambda: 40)
    q._maybe_priority_yield_for({"type": "comment", "voice_path": "x.mp3"})
    assert len(yielded) == 1


def test_priority_yield_no_player_fail_soft():
    """无播放器/无 request_yield 能力 → 安全跳过（fail-soft，不抛）。"""
    q = _QueueFixture({"comment": 50})
    q.audio_player = None
    q._maybe_priority_yield_for({"type": "comment", "voice_path": "x.mp3"})  # 不抛
    q.audio_player = SimpleNamespace()  # 无 request_yield
    q._maybe_priority_yield_for({"type": "comment", "voice_path": "x.mp3"})  # 不抛


# ------------------------------------------- 闲时门（task_scheduler，保留回归）

def _idle_gate_scheduler(mh_audio):
    """TaskScheduler 最小宿主（test_t9_dynamic_copy 同款构造）。"""
    from utils.task_scheduler import TaskScheduler

    config = SimpleNamespace()
    config.get = lambda *keys: {
        ("trends_copywriting", "play_interval"): 1,
    }.get(keys)

    my_handle = SimpleNamespace(audio=mh_audio)
    ts = TaskScheduler.__new__(TaskScheduler)
    ts.config = config
    ts.my_handle = my_handle
    ts.is_running = True
    return ts


def test_is_playback_idle_matrix():
    """闲时门判定矩阵：双队列空+无在飞=空闲；任一非空=忙；宿主缺音频组件=忙。"""
    from utils.task_scheduler import TaskScheduler

    ts = _idle_gate_scheduler(SimpleNamespace(
        message_queue=[], voice_tmp_path_queue=[], _edtalk_current_entry=None))
    assert ts._is_playback_idle() is True

    busy_cases = [
        SimpleNamespace(message_queue=["待合成"], voice_tmp_path_queue=[],
                        _edtalk_current_entry=None),
        SimpleNamespace(message_queue=[], voice_tmp_path_queue=["待播放"],
                        _edtalk_current_entry=None),
        SimpleNamespace(message_queue=[], voice_tmp_path_queue=[],
                        _edtalk_current_entry={"type": "trends_copywriting"}),
    ]
    for audio in busy_cases:
        ts = _idle_gate_scheduler(audio)
        assert ts._is_playback_idle() is False

    ts = _idle_gate_scheduler(None)  # 宿主音频组件未就绪=忙（保守跳过）
    assert ts._is_playback_idle() is False


def test_trends_copywriting_skips_round_when_busy(tmp_path):
    """闲时门端到端：待播放队列非空 → run_trends_copywriting 一轮整体跳过
    （文案未读取、LLM 未调、未入队），不告警不重试。"""
    from utils.task_scheduler import TaskScheduler

    ts = _idle_gate_scheduler(SimpleNamespace(
        message_queue=[], voice_tmp_path_queue=["busy-entry"],
        _edtalk_current_entry=None))

    # 金丝雀：文案读取面/LLM 面被触碰即失败（忙=本轮应整体跳过）
    ts.common = SimpleNamespace(
        get_all_file_paths=lambda folder: (_ for _ in ()).throw(
            AssertionError("系统忙时不应读取文案目录")))

    ts.config.get = lambda *keys: {
        ("trends_copywriting", "enable"): True,
        ("trends_copywriting", "play_interval"): 1,
    }.get(keys)

    synthesis_calls = []
    ts.my_handle.audio_synthesis_handle = lambda msg: synthesis_calls.append(msg)

    # 确定性停止：忙跳过一轮（含 play_interval=1 分片睡眠）后停线程
    def stopper():
        time.sleep(1.6)
        ts.is_running = False
    threading.Thread(target=stopper, daemon=True).start()

    ts.start_trends_copywriting()
    ts.trends_copywriting_thread.join(timeout=6)

    assert synthesis_calls == [], "系统忙时动态文案不应入队播放"
