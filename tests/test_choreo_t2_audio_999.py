"""T2/d21 blocker 守卫：999 判定单一源收敛 + 规则级 999 全链处置。

覆盖裁决条款（2026-11-05 编排优化批次）：
- e2：playback/queue/builtin 三处 999 判定统一经 utils.audio.audio_priority
  （type 映射==999 或 priority_override==999 均触发 999 机制；防三份拷贝漂移）
- d18：规则级 999（type 映射≠999 + priority_override=999）与映射 999 同权处置
  （队列单实例 + builtin 让位判定 _item_priority + 播放出队 active 标记）
- d21：三源 999 共存矩阵（trends_copywriting=999 / reread_top_priority=999 /
  规则级 override=999 并存时单实例约束不互斥正确）
"""
import json
import os
import tempfile
import threading

import pytest

from utils.audio.audio_priority import entry_is_999, entry_priority
from utils.audio.playback_manager import PlaybackMixin
from utils.audio.queue_manager import QueueMixin

# 三源 999 共存的映射表基座（d21：trends_copywriting=999 生产实证 config.json:86）
PM = {"comment": 20, "gift": 20, "song": 10,
      "trends_copywriting": 999, "reread_top_priority": 999}


# ------------------------------------------------- 单一判定源（e2）

def test_entry_priority_override_wins():
    assert entry_priority({"type": "comment", "priority_override": 500}, PM) == 500
    assert entry_priority({"type": "comment", "priority_override": 999}, PM) == 999


def test_entry_priority_mapping_fallback():
    assert entry_priority({"type": "comment"}, PM) == 20
    assert entry_priority({"type": "trends_copywriting"}, PM) == 999
    assert entry_priority({"type": "bogus"}, PM) is None
    assert entry_priority({}, PM) is None


def test_entry_priority_bad_override_falls_to_mapping():
    assert entry_priority({"type": "comment", "priority_override": "abc"}, PM) == 20


def test_entry_priority_none_safe():
    assert entry_priority(None, PM) is None
    assert entry_priority({"type": "comment"}, None) is None


def test_entry_is_999_both_sources():
    assert entry_is_999({"type": "comment", "priority_override": 999}, PM)
    assert entry_is_999({"type": "reread_top_priority"}, PM)
    assert not entry_is_999({"type": "comment"}, PM)


# ------------------------------------------------- playback 出队判定（e2①）

class _PlaybackStub(PlaybackMixin):
    def __init__(self, mapping):
        self.config = type("C", (), {"get": lambda self, *k: mapping})()


def test_playback_is_999_entry_rule_level():
    """d18：规则级 999（type=comment + override=999）出队判定为 999——
    active_reread_top_999 让位门对其生效的前提。"""
    stub = _PlaybackStub(PM)
    assert stub._is_999_entry({"type": "comment", "priority_override": 999}) is True
    assert stub._is_999_entry({"type": "comment"}) is False
    assert stub._is_999_entry({"type": "reread_top_priority"}) is True


# ------------------------------------------------- queue 单实例+三源矩阵（e2③/d21）

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
        self.active_reread_top_999 = False
        self.audio_player = None


def _rule999(rid="dtx"):
    return {"type": "comment", "priority_override": 999,
            "voice_path": f"{rid}.mp3", "content": rid}


@pytest.mark.parametrize("first,incoming", [
    # 三源两两组合（d21）：映射 999（reread_top）× 规则级 999 × 映射 999（trends）
    ({"type": "reread_top_priority", "voice_path": "a.mp3"}, _rule999("b")),
    (_rule999("a"), {"type": "reread_top_priority", "voice_path": "b.mp3"}),
    ({"type": "trends_copywriting", "voice_path": "a.mp3"}, _rule999("b")),
    (_rule999("a"), {"type": "trends_copywriting", "voice_path": "b.mp3"}),
    (_rule999("a"), _rule999("b")),
])
def test_rule_level_999_single_instance_matrix(first, incoming):
    """三源 999 共存矩阵：任一来源的 999 在队时，另一来源的 999 一律单实例舍弃。"""
    q = _QueueFixture()
    r1 = q.data_priority_insert("song", dict(first))
    assert r1["code"] == 200
    r2 = q.data_priority_insert("song", dict(incoming))
    assert r2["code"] == 1, "第二个 999 必须被单实例约束舍弃"
    assert len(q.voice_tmp_path_queue) == 1


def test_rule_level_999_sorts_as_999():
    """规则级 999 条目按有效权重 999 排序（不被 type=comment=20 拉低）。"""
    q = _QueueFixture()
    q.data_priority_insert("song", {"type": "comment", "voice_path": "c.mp3"})
    q.data_priority_insert("song", _rule999("top"))
    assert q.voice_tmp_path_queue[0]["priority_override"] == 999


def test_rule_level_999_accepted_after_999_leaves():
    """999 消费完成（队列空+active 标记释放）后，新 999 照常入队。"""
    q = _QueueFixture()
    q.data_priority_insert("song", _rule999("a"))
    q.voice_tmp_path_queue.clear()  # 模拟消费完成
    q.active_reread_top_999 = False
    r = q.data_priority_insert("song", _rule999("b"))
    assert r["code"] == 200


def test_active_flag_blocks_999_from_any_source():
    q = _QueueFixture()
    q.active_reread_top_999 = True  # 播放器持有（出队置位，playback_manager:159）
    assert q.data_priority_insert("song", _rule999("x"))["code"] == 1
    assert q.data_priority_insert(
        "song", {"type": "reread_top_priority", "voice_path": "y.mp3"})["code"] == 1


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
    """e2②：builtin 播放侧 999 判定（_item_priority→is_999:430）对规则级 999 生效。"""
    p = _make_player(PM)
    assert p._item_priority({"type": "comment", "priority_override": 999}) == 999
    assert p._item_priority({"type": "comment"}) == 20
    assert p._item_priority({"type": "bogus"}) is None


def test_builtin_data_priority_insert_override_sorts():
    """e2②：builtin 队列插位按有效权重——规则级 999 落在 comment=20 之前。"""
    p = _make_player(PM)
    p._data_priority_insert({"type": "comment", "voice_path": "c.mp3"})
    p._data_priority_insert({"type": "comment", "priority_override": 999,
                             "voice_path": "top.mp3"})
    assert p.audio_json_list[0]["voice_path"] == "top.mp3"


def test_builtin_data_priority_insert_non_override_unchanged():
    """无 override 条目行为保真（未识别落队尾/同权接组尾）。"""
    p = _make_player(PM)
    p._data_priority_insert({"type": "comment", "voice_path": "c1.mp3"})
    p._data_priority_insert({"type": "comment", "voice_path": "c2.mp3"})
    p._data_priority_insert({"type": "bogus", "voice_path": "u.mp3"})
    assert [a["voice_path"] for a in p.audio_json_list] == ["c1.mp3", "c2.mp3", "u.mp3"]
