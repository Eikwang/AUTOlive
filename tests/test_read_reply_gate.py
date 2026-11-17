# -*- coding: utf-8 -*-
"""弹幕↔回复配对门控（念白单飞）守卫（2026-11-16 用户中压实测根修）。

症状：多种信息快速进入时念弹幕（read_comment=60）持续消费，AI 回复
（comment=50）堆积饿死——念了多个弹幕后才开始回答第一个弹幕。

根因：回复经 LLM+TTS 延迟入队，生产时序天然乱序（B 念先于 A 答入队），
权重排序/FIFO 均无法恢复配对语义（权重设一致不能根治）。

机制：念白条目打 reply_link，回复条目同 link；消费出口念白出队登记
pending、回复出队解除；门控期仅放行非念白条目（等待中的回复/插播音频），
念白留队。豁免：生产端 abandon_reply_link（无回复路径）、15s 超时兜底。

守卫面：
- 出队回执：念白登记 pending / 回复解除 / abandoned 豁免
- 门控：pending 在途放行非念白、念白留队、全念白轮询、无 pending 直通
- 兜底：超时强制放行转 abandoned
"""
import threading
import time

import pytest

from utils.audio.queue_manager import QueueMixin
from utils.audio.playback_manager import PlaybackMixin, _READ_REPLY_GATE_TIMEOUT


class _GateFixture(QueueMixin):
    """消费循环最小宿主：_read_reply_gate_locked 仅依赖队列与类属性状态。"""

    def __init__(self):
        self.voice_tmp_path_queue = []
        self.voice_tmp_path_queue_lock = threading.Lock()
        self.voice_tmp_path_queue_not_empty = threading.Condition(
            lock=self.voice_tmp_path_queue_lock)
        self.voice_tmp_path_queue_not_empty_flag = False

    # 绑定 PlaybackMixin 的门方法（宿主 Audio 同型：QueueMixin+PlaybackMixin）
    read_reply_gate_locked = PlaybackMixin._read_reply_gate_locked


@pytest.fixture(autouse=True)
def _reset_gate_state():
    QueueMixin._reply_gate_pending.clear()
    QueueMixin._reply_gate_abandoned.clear()
    yield
    QueueMixin._reply_gate_pending.clear()
    QueueMixin._reply_gate_abandoned.clear()


def _rc(link):
    return {"type": "read_comment", "reply_link": link, "content": "念白"}


def _reply(link):
    return {"type": "comment", "reply_link": link, "content": "回复"}


# ------------------------------------------------- 出队回执（track）

def test_track_read_comment_registers_pending():
    q = _GateFixture()
    q.reply_gate_track_popped(_rc("rc-1"))
    assert "rc-1" in QueueMixin._reply_gate_pending


def test_track_reply_releases_pending():
    q = _GateFixture()
    q.reply_gate_track_popped(_rc("rc-1"))
    q.reply_gate_track_popped(_reply("rc-1"))
    assert "rc-1" not in QueueMixin._reply_gate_pending


def test_track_producer_type_read_comment_registers():
    """助播同源念白（producer_type=read_comment，type 非念白）同样登记。"""
    q = _GateFixture()
    q.reply_gate_track_popped({"type": "assistant_anchor_text",
                               "producer_type": "read_comment",
                               "reply_link": "rc-2"})
    assert "rc-2" in QueueMixin._reply_gate_pending


def test_track_without_link_noop():
    q = _GateFixture()
    q.reply_gate_track_popped({"type": "comment"})  # 无 reply_link（旧条目）
    q.reply_gate_track_popped("not-a-dict")
    assert not QueueMixin._reply_gate_pending


def test_abandoned_link_skips_registration():
    """生产端已放弃配对（无回复路径）→ 念白出队不登记 pending（不门控）。"""
    q = _GateFixture()
    q.abandon_reply_link("rc-3")
    q.reply_gate_track_popped(_rc("rc-3"))
    assert "rc-3" not in QueueMixin._reply_gate_pending
    assert "rc-3" in QueueMixin._reply_gate_abandoned


def test_abandon_none_safe():
    q = _GateFixture()
    q.abandon_reply_link(None)  # 聊天源/无念白路径：不抛不登记
    assert not QueueMixin._reply_gate_abandoned


# ------------------------------------------------- 念白单飞门（gate）

def test_gate_pass_through_when_no_pending():
    """常态：无 pending 零开销直通（返回 0.0，调用方常规 pop 队首念白）。"""
    q = _GateFixture()
    queue = [_rc("rc-1")]
    assert q.read_reply_gate_locked(queue) == 0.0


def test_gate_holds_read_comment_releases_reply():
    """核心语义：念白 A 已消费、回复 A 未出队时——回复 A 放行（出队），
    后续念白 B 留队。队列序 [B念, A答, C念] → 弹出 A答。"""
    q = _GateFixture()
    q.reply_gate_track_popped(_rc("rc-A"))
    queue = [_rc("rc-B"), _reply("rc-A"), _rc("rc-C")]
    popped, gate, _ = q.read_reply_gate_locked(queue)
    assert gate == 0.0
    # popped 为 A 的回复条目
    assert popped["reply_link"] == "rc-A"
    assert queue == [_rc("rc-B"), _rc("rc-C")]


def test_gate_holds_all_read_comments_polls():
    """门控期队列全是念白 → 返回 0.5 轮询等待（回复尚未合成入队）。"""
    q = _GateFixture()
    q.reply_gate_track_popped(_rc("rc-A"))
    queue = [_rc("rc-B"), _rc("rc-C")]
    assert q.read_reply_gate_locked(queue) == 0.5
    assert len(queue) == 2  # 念白全部留队


def test_gate_releases_after_reply_popped():
    """闭环：回复 A 出队解除 pending 后，念白 B 恢复常规 pop（0.0 直通）。"""
    q = _GateFixture()
    q.reply_gate_track_popped(_rc("rc-A"))
    q.reply_gate_track_popped(_reply("rc-A"))
    queue = [_rc("rc-B")]
    assert q.read_reply_gate_locked(queue) == 0.0


def test_gate_non_dict_entry_passes():
    """队列含非 dict 条目（防御）→ 门控期照常放行该条目。"""
    q = _GateFixture()
    q.reply_gate_track_popped(_rc("rc-A"))
    queue = ["legacy-string", _rc("rc-B")]
    popped, gate, _ = q.read_reply_gate_locked(queue)
    assert gate == 0.0
    assert popped == "legacy-string"
    assert queue == [_rc("rc-B")]


def test_gate_no_link_entry_held_during_pending():
    """无 reply_link 的念白条目在门控期同样留队（保守：不区分新旧念白）。"""
    q = _GateFixture()
    q.reply_gate_track_popped(_rc("rc-A"))
    queue = [{"type": "read_comment"}]  # 旧条目无 link
    assert q.read_reply_gate_locked(queue) == 0.5
    assert len(queue) == 1


# ------------------------------------------------- 超时兜底

def test_gate_timeout_forces_release():
    """回复迟迟未出队（LLM 失败/竞态遗漏）超 15s → pending 转 abandoned
    强制放行，后续念白不被永久卡死。"""
    q = _GateFixture()
    q.reply_gate_track_popped(_rc("rc-A"))
    QueueMixin._reply_gate_pending["rc-A"] = (
        time.monotonic() - _READ_REPLY_GATE_TIMEOUT - 1.0)
    queue = [_rc("rc-B"), _reply("rc-A")]
    assert q.read_reply_gate_locked(queue) == 0.0
    assert "rc-A" not in QueueMixin._reply_gate_pending
    assert "rc-A" in QueueMixin._reply_gate_abandoned
    # 下一条念白直通（abandoned 豁免：其回复出队不误登记/不误解除）
    queue2 = [_rc("rc-B")]
    assert q.read_reply_gate_locked(queue2) == 0.0


def test_gate_within_timeout_still_holds():
    """超时边界内（14s）仍维持门控等待。"""
    q = _GateFixture()
    q.reply_gate_track_popped(_rc("rc-A"))
    QueueMixin._reply_gate_pending["rc-A"] = (
        time.monotonic() - _READ_REPLY_GATE_TIMEOUT + 1.0)
    queue = [_rc("rc-B")]
    assert q.read_reply_gate_locked(queue) == 0.5


# ------------------------------------------------- 生产端 abandon 语义

def test_abandon_releases_inflight_pending():
    """念白已消费（pending 在途）后生产端走到无回复分支 → 立即解除等待。"""
    q = _GateFixture()
    q.reply_gate_track_popped(_rc("rc-A"))
    assert "rc-A" in QueueMixin._reply_gate_pending
    q.abandon_reply_link("rc-A")
    assert "rc-A" not in QueueMixin._reply_gate_pending
    queue = [_rc("rc-B")]
    assert q.read_reply_gate_locked(queue) == 0.0
