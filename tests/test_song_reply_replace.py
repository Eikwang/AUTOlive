# -*- coding: utf-8 -*-
"""P1 点歌期弹幕回复「顶替-保留最新」回归（2026-11-21 用户实测批次）。

用户症状：点歌后唱歌期间产生的弹幕 LLM 回复在人声结束后依次全部播报
（且不含念弹幕）——积压的旧回复组逐条补播。

设计预期（用户原话要点）：点歌触发后，前奏/间奏人声静音期只插播最新的
高权重音频；人声结束后也从最新的开始播报；唱歌状态时产生的旧音频直接
舍弃——不将这期间所有 LLM 回复全量播报。

实现：新回复组段入队（待合成层 + 待播放层）时，若处于点歌窗口状态
（song 锚在位或待播放队列存在 song 切片段），舍弃其它更旧的待处理回复
组（S1 受保护组豁免），被舍弃组经 abandon_reply_link 解除念白配对门。
"""
import threading

import pytest

from utils.audio.queue_manager import QueueMixin


class _FakeConfig:
    """queue add 路径依赖的 filter 配置桩（键元组→值）。"""

    def __init__(self, priority_mapping=None, extra=None):
        self._data = {
            ("filter", "priority_mapping"): priority_mapping or {},
            ("filter", "voice_tmp_path_queue_min_start_play"): 0,
            ("filter", "voice_tmp_path_queue_max_len"): 100,
            ("filter", "message_queue_max_len"): 100,
            ("filter", "message_queue_replace_same_priority"): True,
            ("play_audio", "enable"): True,
            ("play_audio", "text_split_enable"): False,
            ("audio_random_speed", "normal", "enable"): False,
        }
        if extra:
            self._data.update(extra)

    def get(self, *keys):
        return self._data.get(keys)


class _QueueFixture(QueueMixin):
    """手工组装 QueueMixin 依赖（不拉起 Audio 全量依赖）。"""

    def __init__(self, config=None):
        self.message_queue = []
        self.message_queue_lock = threading.Lock()
        self.message_queue_not_empty = threading.Condition(lock=self.message_queue_lock)
        self.voice_tmp_path_queue = []
        self.voice_tmp_path_queue_lock = threading.Lock()
        self.voice_tmp_path_queue_not_empty = threading.Condition(lock=self.voice_tmp_path_queue_lock)
        self.voice_tmp_path_queue_not_empty_flag = False
        if config is not None:
            self.config = config


@pytest.fixture(autouse=True)
def _reset_gate_state():
    QueueMixin._reply_gate_pending.clear()
    QueueMixin._reply_gate_abandoned.clear()
    QueueMixin._reply_gate_active.clear()
    yield
    QueueMixin._reply_gate_pending.clear()
    QueueMixin._reply_gate_abandoned.clear()
    QueueMixin._reply_gate_active.clear()


def _rc(link):
    return {"type": "read_comment", "reply_link": link, "content": "念白"}


def _reply(link):
    return {"type": "comment", "reply_link": link, "content": f"回复{link}"}


def _song_slice():
    return {"type": "song", "song_segment": {"start_s": 0.0}, "content": "歌声"}


def _gift():
    return {"type": "gift", "content": "礼物答谢"}


# ------------------------------------------------------- 歌曲窗口状态判定

def test_song_state_active_via_anchor():
    fx = _QueueFixture()
    fx._song_anchor = {"t0": 123.0}
    assert fx._song_state_active() is True


def test_song_state_active_via_voice_queue_slice():
    """点歌触发→前奏等待窗口（锚尚未建立）：待播放队列存在 song 切片段即视
    为点歌窗口。"""
    fx = _QueueFixture()
    fx._song_anchor = None
    fx.voice_tmp_path_queue.append(_song_slice())
    assert fx._song_state_active() is True


def test_song_state_inactive_when_no_anchor_no_slice():
    fx = _QueueFixture()
    fx._song_anchor = None
    fx.voice_tmp_path_queue.append(_gift())
    assert fx._song_state_active() is False


# ------------------------------------------------------- 待播放层顶替

def test_voice_layer_replace_drops_older_reply_groups():
    """新回复段插入后（歌曲窗口中）：更旧回复组段被舍弃、S1 受保护组豁免、
    念白/礼物/song 切片不受影响，被舍弃组解除配对门。

    调用序与真实 add 路径一致：先插入新条目，再调用清扫。"""
    fx = _QueueFixture()
    fx._song_anchor = {"t0": 1.0}
    fx.voice_tmp_path_queue.extend([
        _song_slice(),
        _rc("rc-1"), _reply("rc-1"), _reply("rc-2"), _gift(),
        _reply("rc-3"),
        _reply("rc-4"),  # 新回复段（add 路径已插入）
    ])
    fx.mark_reply_group_active("rc-3")  # S1：消费中组受保护
    fx._discard_stale_replies_for_song(_reply("rc-4"))

    kept = [(e["type"], e.get("reply_link")) for e in fx.voice_tmp_path_queue]
    assert kept == [
        ("song", None), ("read_comment", "rc-1"), ("gift", None),
        ("comment", "rc-3"), ("comment", "rc-4"),
    ]
    # 被舍弃组解除念白配对门（防止 15s 空等阻塞后续念白）
    assert "rc-1" in QueueMixin._reply_gate_abandoned
    assert "rc-2" in QueueMixin._reply_gate_abandoned
    assert "rc-3" not in QueueMixin._reply_gate_abandoned


def test_voice_layer_replace_keeps_newest_group_intact():
    """最新组自身多段入队：次段插入后清扫不再丢弃同组段（更旧组已清空）。"""
    fx = _QueueFixture()
    fx._song_anchor = {"t0": 1.0}
    fx.voice_tmp_path_queue.append(_reply("rc-A"))      # 首段（已插入）
    fx._discard_stale_replies_for_song(_reply("rc-A"))
    fx.voice_tmp_path_queue.append(_reply("rc-A"))      # 次段（已插入）
    fx._discard_stale_replies_for_song(_reply("rc-A"))
    assert [e["reply_link"] for e in fx.voice_tmp_path_queue] == ["rc-A", "rc-A"]


def test_voice_layer_replace_inactive_outside_song():
    """非歌曲窗口：回复照常排队（积压属常态消费语义，不顶替）。"""
    fx = _QueueFixture()
    fx._song_anchor = None
    fx.voice_tmp_path_queue.extend([_reply("rc-1"), _reply("rc-2"), _reply("rc-3")])
    fx._discard_stale_replies_for_song(_reply("rc-3"))
    assert [e["reply_link"] for e in fx.voice_tmp_path_queue] == ["rc-1", "rc-2", "rc-3"]


def test_voice_layer_noop_for_non_reply_entry():
    """礼物答谢/念白等非回复组条目：歌曲窗口中照常入队，不触发顶替。"""
    fx = _QueueFixture()
    fx._song_anchor = {"t0": 1.0}
    fx.voice_tmp_path_queue.extend([_reply("rc-1"), _gift(), _gift()])
    fx._discard_stale_replies_for_song(_gift())
    fx._discard_stale_replies_for_song(_rc("rc-2"))
    assert [e["type"] for e in fx.voice_tmp_path_queue] == ["comment", "gift", "gift"]


# ------------------------------------------------------- 待合成层顶替（add 路径）

def _mq_fixture():
    mapping = {"comment": 50, "read_comment": 60, "gift": 70, "song": 80}
    return _QueueFixture(config=_FakeConfig(priority_mapping=mapping))


def test_message_queue_layer_replace_drops_older_replies():
    """add 路径（等待合成消息）：歌曲窗口中新回复入队即舍弃更旧回复——
    合成成本前移节省，唱歌结束后残留回复不再依次全量播报。"""
    fx = _mq_fixture()
    fx._song_anchor = {"t0": 1.0}
    fx.message_queue.extend([
        _rc("rc-1"), _reply("rc-1"), _reply("rc-2"), _gift(),
    ])
    r = fx.data_priority_insert("等待合成消息", _reply("rc-3"))
    assert r["code"] == 200
    kept = [(e["type"], e.get("reply_link")) for e in fx.message_queue]
    assert kept == [("read_comment", "rc-1"), ("gift", None), ("comment", "rc-3")]
    assert "rc-1" in QueueMixin._reply_gate_abandoned
    assert "rc-2" in QueueMixin._reply_gate_abandoned


def test_message_queue_layer_replace_inactive_outside_song():
    """非歌曲窗口：待合成层不顶替（常态积压语义不变）。"""
    fx = _mq_fixture()
    fx._song_anchor = None
    fx.message_queue.extend([_reply("rc-1")])
    fx.data_priority_insert("等待合成消息", _reply("rc-2"))
    assert [e["reply_link"] for e in fx.message_queue if e.get("reply_link")] == ["rc-1", "rc-2"]


def test_message_queue_layer_protected_group_exempt():
    """S1 受保护组（消费中）：待合成层顶替同样豁免。"""
    fx = _mq_fixture()
    fx._song_anchor = {"t0": 1.0}
    fx.message_queue.extend([_reply("rc-1")])
    fx.mark_reply_group_active("rc-1")
    fx.data_priority_insert("等待合成消息", _reply("rc-2"))
    links = [e["reply_link"] for e in fx.message_queue if e.get("reply_link")]
    assert "rc-1" in links and "rc-2" in links
    assert "rc-1" not in QueueMixin._reply_gate_abandoned
