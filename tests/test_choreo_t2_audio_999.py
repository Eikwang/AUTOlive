"""T2 优先级单一源守卫（2026-11-05 编排优化批次 e2/d21；
2026-11-15 批次：999 特殊机制退役——原 999 单实例/让位处置格移除，
本文件保留有效权重判定与排序语义守卫，文件名保留历史批次标识）。

覆盖裁决条款：
- e2：playback/queue/builtin 三处有效权重判定统一经 utils.audio.audio_priority
  （priority_override 优先于 type 映射；防三份拷贝漂移）
- d21→改写：显式权重条目（编排插队 500 / 最高顺位复读 500 / 任意 override
  值）按有效权重排序，override 条目可并存（999 特殊单实例约束已退役）
"""
import json
import os
import tempfile
import threading

import pytest

from utils.audio.audio_priority import entry_priority
from utils.audio.playback_manager import PlaybackMixin
from utils.audio.queue_manager import QueueMixin

# 显式权重条目并存场景的映射表基座（trends_copywriting/reread_top_priority
# 生产实值=10/500，2026-11-15 批次 999 退役后对齐 config.json）
PM = {"comment": 20, "gift": 20, "song": 10,
      "trends_copywriting": 10, "reread_top_priority": 500}


# ------------------------------------------------- 单一判定源（e2）

def test_entry_priority_override_wins():
    assert entry_priority({"type": "comment", "priority_override": 500}, PM) == 500
    assert entry_priority({"type": "comment", "priority_override": 999}, PM) == 999


def test_entry_priority_mapping_fallback():
    assert entry_priority({"type": "comment"}, PM) == 20
    assert entry_priority({"type": "trends_copywriting"}, PM) == 10
    assert entry_priority({"type": "bogus"}, PM) is None
    assert entry_priority({}, PM) is None


def test_entry_priority_bad_override_falls_to_mapping():
    assert entry_priority({"type": "comment", "priority_override": "abc"}, PM) == 20


def test_entry_priority_none_safe():
    assert entry_priority(None, PM) is None
    assert entry_priority({"type": "comment"}, None) is None


# ------------------------------------------------- playback 出队判定（e2①）

class _PlaybackStub(PlaybackMixin):
    def __init__(self, mapping):
        self.config = type("C", (), {"get": lambda self, *k: mapping})()


def test_playback_entry_priority_rule_level():
    """override 权重条目出队判定按有效权重（编排插队 500/任意 override 值）。"""
    stub = _PlaybackStub(PM)
    from utils.audio.audio_priority import entry_priority
    assert entry_priority({"type": "comment", "priority_override": 500},
                          PM) == 500
    assert entry_priority({"type": "comment"}, PM) == 20


# ------------------------------------------------- queue 排序（e2③/d21 改写）

class _FakeConfig:
    def __init__(self, mapping=None, max_len=100):
        self._data = {
            ("filter", "priority_mapping"): mapping if mapping is not None else PM,
            ("filter", "message_queue_max_len"): max_len,
            ("filter", "voice_tmp_path_queue_min_start_play"): 3,
            ("filter", "voice_tmp_path_queue_max_len"): max_len,
            ("talk", "continuous_talk"): False,
        }

    def get(self, *keys):
        return self._data.get(keys)


class _QueueFixture(QueueMixin):
    """手工组装 QueueMixin 依赖（test_audio_playback_review 同款模式）"""

    def __init__(self, mapping=None, max_len=100):
        self.config = _FakeConfig(mapping, max_len)
        self.message_queue = []
        self.message_queue_lock = threading.Lock()
        self.message_queue_not_empty = threading.Condition(lock=self.message_queue_lock)
        self.voice_tmp_path_queue = []
        self.voice_tmp_path_queue_lock = threading.Lock()
        self.voice_tmp_path_queue_not_empty = threading.Condition(lock=self.voice_tmp_path_queue_lock)
        self.voice_tmp_path_queue_not_empty_flag = False
        self.audio_player = None


def _rule_top(rid="dtx", override=500):
    return {"type": "comment", "priority_override": override,
            "voice_path": f"{rid}.mp3", "content": rid}


def test_override_entries_coexist_and_sort_top():
    """2026-11-15 批次改写（999 单实例约束退役）：多个显式权重条目并存入队，
    按有效权重排序（规则1 同权接组尾：后到的 override 500 排在先到的映射
    500 条目之后，两者均高于映射 20 的 comment）。"""
    q = _QueueFixture()
    r1 = q.data_priority_insert("song", {"type": "comment", "voice_path": "c.mp3"})
    r2 = q.data_priority_insert("song", {"type": "reread_top_priority", "voice_path": "a.mp3"})
    r3 = q.data_priority_insert("song", _rule_top("b"))
    assert (r1["code"], r2["code"], r3["code"]) == (200, 200, 200)
    assert len(q.voice_tmp_path_queue) == 3  # 无单实例互斥
    assert q.voice_tmp_path_queue[0]["type"] == "reread_top_priority"  # 映射 500 > comment 20
    assert q.voice_tmp_path_queue[1]["priority_override"] == 500  # 规则级 override 同权接组尾
    assert q.voice_tmp_path_queue[2]["type"] == "comment"  # 映射 20 落尾


def test_rule_level_override_sorts_as_weight():
    """规则级 override 条目按有效权重排序（不被 type=comment=20 拉低）。"""
    q = _QueueFixture()
    q.data_priority_insert("song", {"type": "comment", "voice_path": "c.mp3"})
    q.data_priority_insert("song", _rule_top("top"))
    assert q.voice_tmp_path_queue[0]["priority_override"] == 500


def test_reread_top_priority_maps_500():
    """2026-11-15 批次：reread_top_priority 映射 500（保持最高顺位、脱离 999
    域——普通权重语义，无暂停/续播/让位/单实例特殊机制）。"""
    q = _QueueFixture()
    q.data_priority_insert("song", {"type": "comment", "voice_path": "c.mp3"})
    q.data_priority_insert("song", {"type": "reread_top_priority", "voice_path": "top.mp3"})
    assert q.voice_tmp_path_queue[0]["type"] == "reread_top_priority"
    from utils.audio.audio_priority import entry_priority
    assert entry_priority({"type": "reread_top_priority"}, PM) == 500


# ------------------------------------------------- builtin 播放侧（e2②）

def _make_player(mapping):
    from utils.audio.builtin_play_center import AUDIO_PLAY_CENTER
    p = AUDIO_PLAY_CENTER.__new__(AUDIO_PLAY_CENTER)
    p.config_data = {"priority_mapping": mapping}
    p.config_path = os.path.join(tempfile.gettempdir(), "__no_such_t2__.json")
    p.stream = None
    p.pause_event = threading.Event()
    p.audio_data_event = threading.Event()
    p._done_event = threading.Event()
    p._skip_event = threading.Event()
    p._stop_flag = threading.Event()
    p.audio_json_list = []
    p.list_lock = threading.Lock()
    p._priority_provider = None
    return p


def test_builtin_item_priority_override_aware():
    """e2②：builtin 播放侧有效权重判定对 override 生效（d12 让位判定源）。"""
    p = _make_player(PM)
    assert p._item_priority({"type": "comment", "priority_override": 500}) == 500
    assert p._item_priority({"type": "comment"}) == 20
    assert p._item_priority({"type": "bogus"}) is None


def test_builtin_data_priority_insert_override_sorts():
    """e2②：builtin 队列插位按有效权重——override 条目落在 comment=20 之前。"""
    p = _make_player(PM)
    p._data_priority_insert({"type": "comment", "voice_path": "c.mp3"})
    p._data_priority_insert({"type": "comment", "priority_override": 500,
                             "voice_path": "top.mp3"})
    assert p.audio_json_list[0]["voice_path"] == "top.mp3"


def test_builtin_data_priority_insert_non_override_unchanged():
    """无 override 条目行为保真（未识别落队尾/同权接组尾）。"""
    p = _make_player(PM)
    p._data_priority_insert({"type": "comment", "voice_path": "c1.mp3"})
    p._data_priority_insert({"type": "comment", "voice_path": "c2.mp3"})
    p._data_priority_insert({"type": "bogus", "voice_path": "u.mp3"})
    assert [a["voice_path"] for a in p.audio_json_list] == ["c1.mp3", "c2.mp3", "u.mp3"]
