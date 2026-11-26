# -*- coding: utf-8 -*-
"""动态文案消费完成触发门回归（2026-11-26 log9 实证根修）。

症状：文案 #1（25 段）~135s 全部合成完毕（待合成队列空=旧触发门放行），
但点歌 hold 冻结消费仍有 ~9 段未播；21:18:06 文案 #2 触发生成入队、
21:20:23 文案 #3 再次触发——两份文案音频在待播放队列背靠背堆积。

根因：触发门 _is_synth_queue_idle 只看待合成消息队列空，守卫语义自
2026-11-21 裁决起从「上一文案消费完成」退化为「上一文案合成完成」。

修复：新增 _trends_copy_consumed 三重判定门（两队列无文案残留 + 上一轮
入队时刻之后的 trends_copywriting 消费回执 + 600s 保险丝防无回执路径
永久卡死），第一查/第二查均须通过，入队时记录单调时刻。
"""
import inspect
import threading
import time

import pytest

from utils.audio.queue_manager import QueueMixin
from utils.task_scheduler import TaskScheduler


class _AudioStub:
    pass


class _HandleStub:
    pass


def _sched(mq=(), pq=()):
    """最小宿主：TaskScheduler.__new__ 跳过重 __init__，仅装配消费门依赖"""
    s = TaskScheduler.__new__(TaskScheduler)
    a = _AudioStub()
    a.message_queue = list(mq)
    a.voice_tmp_path_queue = list(pq)
    h = _HandleStub()
    h.audio = a
    s.my_handle = h
    return s


def _copy_entry(seq=0, total=25):
    return {"type": "trends_copywriting",
            "content": f"段{seq}",
            "split_group": {"id": "trends_copywriting_1", "seq": seq, "total": total}}


@pytest.fixture(autouse=True)
def _reset_state():
    QueueMixin._consumed_mono_by_type.clear()
    yield
    QueueMixin._consumed_mono_by_type.clear()


class TestTrendsCopyConsumedGate:
    def test_copy_left_in_synth_queue_blocks(self):
        """待合成队列残留文案段（未合成完）→ 拦截"""
        s = _sched(mq=[_copy_entry(0)])
        assert s._trends_copy_consumed() is False

    def test_copy_left_in_play_queue_blocks(self):
        """待播放队列残留文案段（已合成未播，log9 21:18:06 场景）→ 拦截"""
        s = _sched(pq=[_copy_entry(5), {"type": "song", "song_segment": True}])
        assert s._trends_copy_consumed() is False

    def test_queues_empty_never_enqueued_passes(self):
        """音频组件就绪但本轮前从未入队过文案 → 放行"""
        s = _sched()
        assert s._trends_copy_consumed() is True

    def test_audio_missing_passes(self):
        """音频组件未就绪 → 放行（忙判定由 _is_synth_queue_idle 负责）"""
        s = TaskScheduler.__new__(TaskScheduler)
        h = _HandleStub()
        h.audio = None
        s.my_handle = h
        assert s._trends_copy_consumed() is True

    def test_consumed_receipt_after_enqueue_passes(self):
        """两队列空 + 上一轮入队时刻之后存在文案消费回执（末段已播完）→ 放行"""
        s = _sched()
        s._trends_copy_enqueue_mono = time.monotonic() - 10.0
        QueueMixin._consumed_mono_by_type["trends_copywriting"] = time.monotonic()
        assert s._trends_copy_consumed() is True

    def test_no_receipt_after_enqueue_blocks(self):
        """两队列空但末段仍在数字人/播放器里在播（无入队后回执）→ 拦截"""
        s = _sched()
        s._trends_copy_enqueue_mono = time.monotonic()
        QueueMixin._consumed_mono_by_type["trends_copywriting"] = time.monotonic() - 30.0
        assert s._trends_copy_consumed() is False

    def test_no_receipt_other_type_only_blocks(self):
        """回执仅来自其它类型（如弹幕回复）不算文案消费完成 → 拦截"""
        s = _sched()
        s._trends_copy_enqueue_mono = time.monotonic()
        QueueMixin._consumed_mono_by_type["comment"] = time.monotonic()
        assert s._trends_copy_consumed() is False

    def test_fuse_forces_pass_after_timeout(self):
        """保险丝：入队后超 600s 无回执（flush/撤销等无回执路径）→ 放行防停摆"""
        s = _sched()
        s._trends_copy_enqueue_mono = time.monotonic() - (s._TRENDS_COPY_CONSUME_FUSE_S + 1.0)
        assert s._trends_copy_consumed() is True


class TestReceiptMonoTracking:
    def test_report_consumed_records_mono_by_type(self):
        """G2 扩展：report_consumed 登记分类型单调时刻（门信号源）"""
        host = QueueMixin.__new__(QueueMixin)  # Mixin 无 __init__，直接实例化
        before = QueueMixin._consumed_mono_by_type.get("trends_copywriting", 0.0)
        host.report_consumed("trends_copywriting", "测试段")
        after = QueueMixin._consumed_mono_by_type.get("trends_copywriting", 0.0)
        assert after > before
        assert after <= time.monotonic()


class TestWiring:
    def test_gate_wired_into_trends_thread(self):
        """触发门接线守卫：run_trends_copywriting 闭包含消费完成门两查与入队时刻记录"""
        src = inspect.getsource(TaskScheduler.start_trends_copywriting)
        assert "_trends_copy_consumed()" in src
        assert "_trends_copy_enqueue_mono" in src
