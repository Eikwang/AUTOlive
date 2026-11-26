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
        # 阈值配置化（2026-11-26 二期）：门超时读侧依赖 config——最小宿主
        # 无 config 时走缺省常量（与生产缺省一致）
        self.config = None

    # 绑定 PlaybackMixin 的门方法（宿主 Audio 同型：QueueMixin+PlaybackMixin）
    read_reply_gate_locked = PlaybackMixin._read_reply_gate_locked
    # 阈值配置化读侧 + 分级超时可见性判定（2026-11-26 二期新增依赖，同源绑定）
    _reply_gate_timeout_s = PlaybackMixin._reply_gate_timeout_s
    _reply_gate_extend_s = PlaybackMixin._reply_gate_extend_s
    _reply_group_visible_locked = PlaybackMixin._reply_group_visible_locked
    _alarm_reply_gate_timeout = PlaybackMixin._alarm_reply_gate_timeout


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
    """常态：无 pending 零开销直通（返回 (None, 0.0, False)，调用方常规 pop
    队首念白）。2026-11-24 契约统一：门恒返回三元组。"""
    q = _GateFixture()
    queue = [_rc("rc-1")]
    entry, gate, borrowed = q.read_reply_gate_locked(queue)
    assert (entry, gate, borrowed) == (None, 0.0, False)


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
    """门控期队列全是念白 → 返回 (None, 0.5, False) 轮询等待（回复尚未合成
    入队）。"""
    q = _GateFixture()
    q.reply_gate_track_popped(_rc("rc-A"))
    queue = [_rc("rc-B"), _rc("rc-C")]
    entry, gate, borrowed = q.read_reply_gate_locked(queue)
    assert (entry, gate, borrowed) == (None, 0.5, False)
    assert len(queue) == 2  # 念白全部留队


def test_gate_holds_copy_segments_polls():
    """门控期队列是念白+文案段（回复未入队）→ 同样轮询等待：文案段让位任何
    消息（2026-11-24 用户口径），不得顶替门控放行位。"""
    q = _GateFixture()
    q.reply_gate_track_popped(_rc("rc-A"))
    queue = [_copy_seg(0), _rc("rc-B")]
    entry, gate, _ = q.read_reply_gate_locked(queue)
    assert (entry, gate) == (None, 0.5)
    assert len(queue) == 2  # 文案段与念白全部留队


def test_gate_releases_after_reply_popped():
    """闭环：回复 A 出队解除 pending 后，念白 B 恢复常规 pop（0.0 直通）。"""
    q = _GateFixture()
    q.reply_gate_track_popped(_rc("rc-A"))
    q.reply_gate_track_popped(_reply("rc-A"))
    queue = [_rc("rc-B")]
    entry, gate, borrowed = q.read_reply_gate_locked(queue)
    assert (entry, gate, borrowed) == (None, 0.0, False)


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
    entry, gate, _ = q.read_reply_gate_locked(queue)
    assert (entry, gate) == (None, 0.5)
    assert len(queue) == 1


# ------------------------------------------------- 超时兜底

def test_gate_timeout_forces_release():
    """回复迟迟未出队超 15s → 分级超时（2026-11-26 二期缺陷1 根修）：
    回复已在队列可见（已入队、仍在排队）→ 续等一轮（不放弃），且门控扫描
    直接弹出回复 A（旧契约超时放弃后返回 (None,0.0) 交常规权重 pop——念白 B
    (60) 会先于回复 A (50) 出队=错位；新契约由门弹出回复消除该错位面）。"""
    q = _GateFixture()
    q.reply_gate_track_popped(_rc("rc-A"))
    QueueMixin._reply_gate_pending["rc-A"] = (
        time.monotonic() - _READ_REPLY_GATE_TIMEOUT - 1.0)
    queue = [_rc("rc-B"), _reply("rc-A")]
    entry, gate, _ = q.read_reply_gate_locked(queue)
    # 门弹出回复 A（配对落地，念白/回复不错位）
    assert entry is not None and entry["reply_link"] == "rc-A"
    assert gate == 0.0
    assert "rc-A" in QueueMixin._reply_gate_pending  # 续等登记（未放弃）
    assert "rc-A" in QueueMixin._reply_gate_extended
    # 回复出队 → pending 解除，下一条念白直通
    q.reply_gate_track_popped(entry)
    assert "rc-A" not in QueueMixin._reply_gate_pending
    queue2 = [_rc("rc-B")]
    entry2, gate2, _ = q.read_reply_gate_locked(queue2)
    assert (entry2, gate2) == (None, 0.0)
    assert queue2 == [_rc("rc-B")]  # 念白 B 留待常规 pop


def test_gate_within_timeout_still_holds():
    """超时边界内（14s）仍维持门控等待。"""
    q = _GateFixture()
    q.reply_gate_track_popped(_rc("rc-A"))
    QueueMixin._reply_gate_pending["rc-A"] = (
        time.monotonic() - _READ_REPLY_GATE_TIMEOUT + 1.0)
    queue = [_rc("rc-B")]
    entry, gate, _ = q.read_reply_gate_locked(queue)
    assert (entry, gate) == (None, 0.5)


# ------------------------------------------------- 生产端 abandon 语义

def test_abandon_releases_inflight_pending():
    """念白已消费（pending 在途）后生产端走到无回复分支 → 立即解除等待。"""
    q = _GateFixture()
    q.reply_gate_track_popped(_rc("rc-A"))
    assert "rc-A" in QueueMixin._reply_gate_pending
    q.abandon_reply_link("rc-A")
    assert "rc-A" not in QueueMixin._reply_gate_pending
    queue = [_rc("rc-B")]
    entry, gate, borrowed = q.read_reply_gate_locked(queue)
    assert (entry, gate, borrowed) == (None, 0.0, False)


def test_gate_copy_segment_yielded_for_reply():
    """门控期文案段挡在回复前 → 文案段让位，回复直接放行（2026-11-24 用户
    口径：文案让位任何消息）。"""
    q = _GateFixture()
    q.reply_gate_track_popped(_rc("rc-A"))
    queue = [_copy_seg(0), _copy_seg(1), _reply("rc-A")]
    popped, gate, _ = q.read_reply_gate_locked(queue)
    assert gate == 0.0
    assert popped["reply_link"] == "rc-A"
    assert len(queue) == 2  # 两段文案延后留队


# ------------------------------------------------- 测试工具

def _copy_seg(seq, total=2):
    """动态文案分段条目（split_group 组绑定）"""
    return {"type": "trends_copywriting", "voice_path": f"copy_{seq}.mp3",
            "content": f"文案第{seq + 1}段",
            "split_group": {"id": "g1", "seq": seq, "total": total}}
