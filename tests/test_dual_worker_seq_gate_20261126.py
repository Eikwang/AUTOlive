# -*- coding: utf-8 -*-
"""双 worker 乱序根修 + 配对门分级超时回归（2026-11-26 队列审查批次）。

缺陷 7b（乱序播出）：双 worker 并行合成完成序≠seq 序，待播放队列原只对
动态文案分段做 seq 有序插入（2026-11-25 修复），reply/comment 长回复切分
段漏网——乱序入库后消费侧组原子门按「第一个同组段」推进=按完成序倒序播出。
修复：① seq 有序插入单源泛化到任意 split_group 条目（含满载豁免路径）；
② 组原子门改按组内最小 seq 定位下一段。

缺陷 1（配对门错位）：满载洪峰下回复合成排队（LLM 生成+背压门+GPU 并发≤2）
可能 >15s，配对门直接放弃=念白 A 的回复错位到念白 B 之后（内容错配级）。
修复：分级超时——首次超时且组回复已在两队列可见（已入队）→ 续等一轮
10s 再放弃；已续等过或不可见 → 放弃并计数（read_reply_gate_timeout）。
"""
import threading
import time

import pytest

from utils.audio.queue_manager import QueueMixin
from utils.audio.playback_manager import PlaybackMixin
from utils.obs_stats import record_lock_wait, snapshot as _obs_snapshot


class _FakeConfig:
    def __init__(self, max_len=4, mq_max_len=4):
        self._data = {
            ("filter", "priority_mapping"): {"gift": 70, "read_comment": 60,
                                             "comment": 50,
                                             "trends_copywriting": 10},
            ("filter", "voice_tmp_path_queue_min_start_play"): 0,
            ("filter", "voice_tmp_path_queue_max_len"): max_len,
            ("filter", "message_queue_max_len"): mq_max_len,
            ("filter", "message_queue_replace_same_priority"): True,
            ("play_audio", "enable"): True,
            ("play_audio", "synth_gate_budget_s"): 15.0,
        }

    def get(self, *keys):
        return self._data.get(keys)


class _Fixture(QueueMixin, PlaybackMixin):
    def __init__(self, config=None):
        self.message_queue = []
        self.message_queue_lock = threading.Lock()
        self.message_queue_not_empty = threading.Condition(lock=self.message_queue_lock)
        self.voice_tmp_path_queue = []
        self.voice_tmp_path_queue_lock = threading.Lock()
        self.voice_tmp_path_queue_not_empty = threading.Condition(lock=self.voice_tmp_path_queue_lock)
        self.voice_tmp_path_queue_not_empty_flag = False
        self._atomic_group_ctx = None
        self._song_anchor = None
        if config is not None:
            self.config = config


def _seg(gid, seq, total=7, seg_type="comment", with_link=False):
    """长回复切分段条目（voice 面形态；with_link 模拟弹幕问答链 reply_link）"""
    e = {"type": seg_type,
         "voice_path": f"{gid}-seg{seq}.wav",
         "content": f"段{seq}",
         "split_group": {"id": gid, "seq": seq, "total": total}}
    if with_link:
        e["reply_link"] = gid
    return e


def _plain(seg_type="gift", label="g"):
    """非切分普通条目"""
    return {"type": seg_type, "voice_path": f"{label}.wav", "content": label}


@pytest.fixture(autouse=True)
def _reset_group_state():
    for table in (QueueMixin._reply_gate_pending,
                  QueueMixin._reply_gate_abandoned,
                  QueueMixin._reply_gate_active,
                  QueueMixin._split_group_active,
                  QueueMixin._reply_gate_extended):
        table.clear()
    yield
    for table in (QueueMixin._reply_gate_pending,
                  QueueMixin._reply_gate_abandoned,
                  QueueMixin._reply_gate_active,
                  QueueMixin._split_group_active,
                  QueueMixin._reply_gate_extended):
        table.clear()


class TestVoiceQueueSeqOrder:
    """7b-1：voice 队列 seq 有序插入泛化（乱序完成序 → 组内 seq 保序）"""

    def _paths(self, fx):
        return [d["voice_path"] for d in fx.voice_tmp_path_queue]

    def test_out_of_order_completion_seq_ordered(self):
        """双 worker 乱序完成（seg2 先于 seg1 入队）→ 组内 seq 保序"""
        fx = _Fixture(config=_FakeConfig(max_len=4))
        fx.data_priority_insert("待播放音频列表", _seg("c1", 2))  # worker B 先完成
        fx.data_priority_insert("待播放音频列表", _seg("c1", 1))  # worker A 后完成
        assert self._paths(fx) == ["c1-seg1.wav", "c1-seg2.wav"]

    def test_seq_insert_preserves_weight_order(self):
        """seq 有序插入不破坏权重排序不变式（更高权重条目保持在同组段之前）"""
        fx = _Fixture(config=_FakeConfig(max_len=4))
        fx.data_priority_insert("待播放音频列表", _plain("gift"))
        fx.data_priority_insert("待播放音频列表", _seg("c1", 2))
        fx.data_priority_insert("待播放音频列表", _seg("c1", 1))
        assert self._paths(fx) == ["g.wav", "c1-seg1.wav", "c1-seg2.wav"]

    def test_full_exempt_path_seq_ordered(self):
        """满载豁免路径（组活跃）同样按 seq 有序插入——原接队尾会倒序"""
        fx = _Fixture(config=_FakeConfig(max_len=2))
        fx.data_priority_insert("待播放音频列表", _plain("comment", "o1"))
        fx.data_priority_insert("待播放音频列表", _plain("comment", "o2"))
        assert len(fx.voice_tmp_path_queue) == 2  # 普通容量满
        fx._mark_split_group_active("c2")  # 组活跃（合成侧任一段出队即登记）
        # 乱序完成：seg3 → seg2 → seg1 依次入队
        for seq in (3, 2, 1):
            assert fx.data_priority_insert(
                "待播放音频列表", _seg("c2", seq))["code"] == 200
        assert self._paths(fx)[-3:] == ["c2-seg1.wav", "c2-seg2.wav",
                                        "c2-seg3.wav"]
        # 豁免插入不驱逐既有条目
        assert "o1.wav" in self._paths(fx) and "o2.wav" in self._paths(fx)

    def test_trends_segments_keep_seq_order(self):
        """动态文案分段（原 2026-11-25 修复面）泛化后行为不变"""
        fx = _Fixture(config=_FakeConfig(max_len=4))
        fx.data_priority_insert("待播放音频列表", _seg("t1", 1, seg_type="trends_copywriting"))
        fx.data_priority_insert("待播放音频列表", _seg("t1", 0, seg_type="trends_copywriting"))
        assert self._paths(fx) == ["t1-seg0.wav", "t1-seg1.wav"]


class TestAtomicGroupGateSeq:
    """7b-2：组原子门按组内最小 seq 定位下一段"""

    def test_gate_pops_smallest_seq_first(self):
        """乱序入库（seg3 在 seg2 前）→ 组原子门先弹 seq 最小段"""
        fx = _Fixture(config=_FakeConfig(max_len=4))
        fx._atomic_group_ctx = {"id": "g1", "type": "comment"}
        # 直接构造乱序队列：seg3 在前，高权重 gift 居中，seg2 在后
        fx.voice_tmp_path_queue = [_seg("g1", 3), _plain("gift"),
                                   _seg("g1", 2), _seg("g1", 1)]
        entry, wait, borrowed = fx._group_atomic_pop_locked()
        assert entry["voice_path"] == "g1-seg1.wav"
        assert wait == 0.0 and borrowed is False
        # 组未完成：ctx 保留，下次续弹 seg2
        assert fx._atomic_group_ctx == {"id": "g1", "type": "comment"}
        entry2, _, _ = fx._group_atomic_pop_locked()
        assert entry2["voice_path"] == "g1-seg2.wav"

    def test_gate_completes_when_no_same_group_left(self):
        """无同组剩余段 → ctx 清除回正常权重 pop"""
        fx = _Fixture(config=_FakeConfig(max_len=4))
        fx._atomic_group_ctx = {"id": "g1", "type": "comment"}
        fx.voice_tmp_path_queue = [_plain("gift")]
        assert fx._group_atomic_pop_locked() is None
        assert fx._atomic_group_ctx is None


class TestReplyGateGradedTimeout:
    """缺陷1：配对门分级超时（可见续等 / 真失败放弃）"""

    def test_visible_reply_extends_once(self):
        """首次超时且回复已在 message_queue 可见 → 续等一轮（不放弃）"""
        fx = _Fixture(config=_FakeConfig())
        link = "L1"
        fx.message_queue.append({"type": "comment", "reply_link": link,
                                 "content": "回复"})
        QueueMixin._reply_gate_pending[link] = time.monotonic() - 16.0
        entry, wait, _ = fx._read_reply_gate_locked(fx.voice_tmp_path_queue)
        assert entry is None and wait == 0.5  # 仍在门控等待（队列无放行条目）
        assert link in QueueMixin._reply_gate_pending  # 未放弃
        assert link in QueueMixin._reply_gate_extended  # 已登记续等
        drops = _obs_snapshot()["drops"]
        assert drops.get("read_reply_gate_extend", 0) >= 1

    def test_visible_in_voice_queue_extends(self):
        """回复已在待播放队列（合成完待播）→ 同样续等"""
        fx = _Fixture(config=_FakeConfig())
        link = "L2"
        fx.voice_tmp_path_queue.append({"type": "comment",
                                        "reply_link": link,
                                        "voice_path": "r.wav", "content": ""})
        QueueMixin._reply_gate_pending[link] = time.monotonic() - 16.0
        fx._read_reply_gate_locked(fx.voice_tmp_path_queue)
        assert link in QueueMixin._reply_gate_extended

    def test_extended_then_timeout_abandons(self):
        """续等一轮后仍超时 → 放弃（念白放行）+ 超时计数"""
        fx = _Fixture(config=_FakeConfig())
        link = "L3"
        fx.message_queue.append({"type": "comment", "reply_link": link,
                                 "content": "回复"})
        QueueMixin._reply_gate_pending[link] = time.monotonic() - 16.0
        fx._read_reply_gate_locked(fx.voice_tmp_path_queue)  # 第一次：续等
        QueueMixin._reply_gate_pending[link] = time.monotonic() - 16.0
        entry, wait, _ = fx._read_reply_gate_locked(fx.voice_tmp_path_queue)
        assert link not in QueueMixin._reply_gate_pending  # 已放弃
        assert link in QueueMixin._reply_gate_abandoned
        assert link not in QueueMixin._reply_gate_extended  # 登记同步清理
        drops = _obs_snapshot()["drops"]
        assert drops.get("read_reply_gate_timeout", 0) >= 1

    def test_invisible_reply_abandons_immediately(self):
        """回复完全不可见（两队列均无）→ 沿原 15s 口径立即放弃（不饿死消费链）"""
        fx = _Fixture(config=_FakeConfig())
        link = "L4"
        QueueMixin._reply_gate_pending[link] = time.monotonic() - 16.0
        fx._read_reply_gate_locked(fx.voice_tmp_path_queue)
        assert link in QueueMixin._reply_gate_abandoned
        assert link not in QueueMixin._reply_gate_extended

    def test_reply_pop_clears_extension(self):
        """回复出队 → pending 与续等登记同步解除"""
        fx = _Fixture(config=_FakeConfig())
        link = "L5"
        QueueMixin._reply_gate_pending[link] = time.monotonic()
        QueueMixin._reply_gate_extended[link] = time.monotonic()
        fx.reply_gate_track_popped({"type": "comment", "reply_link": link,
                                    "content": ""})
        assert link not in QueueMixin._reply_gate_pending
        assert link not in QueueMixin._reply_gate_extended


class TestLockWaitMetrics:
    """缺陷4 观测：锁等待样本记录与快照透出"""

    def test_record_lock_wait_snapshot(self):
        record_lock_wait("unit_test_lock", 0.0012)
        record_lock_wait("unit_test_lock", 0.0034)
        lw = _obs_snapshot()["lock_wait"]["unit_test_lock"]
        assert lw["n"] >= 2
        assert lw["max_s"] >= 0.0034
        assert lw["sum_s"] >= 0.0046
