# -*- coding: utf-8 -*-
"""二期回归：保护判定快照化 + 阈值配置化/定标观测（2026-11-26 二期）。

A 快照化：入队 stamp / 激活回填 / 候选过滤读快照（TOCTOU 根治——判定与动作
同一份状态）；快照偏保守方向（TTL 过期后多保护少驱逐）。
B1 阈值配置化：reply_gate_timeout_s / reply_gate_extend_s /
reply_group_active_ttl_s 三键读侧。
B2/B3/B4 观测：record_llm / record_split_total / record_queue_depth /
group_active_ttl_lapsed 计数 / threshold_calibration 建议值。
"""
import threading
import time

import pytest

from utils.audio.queue_manager import QueueMixin
from utils.audio.playback_manager import PlaybackMixin
from utils.obs_stats import (record_llm, record_queue_depth,
                             record_split_total, snapshot as _obs_snapshot)
from utils import threshold_calibration as cal


class _FakeConfig:
    def __init__(self, max_len=4, mq_max_len=4, extra=None):
        self._data = {
            ("filter", "priority_mapping"): {"gift": 70, "read_comment": 60,
                                             "comment": 50,
                                             "trends_copywriting": 10},
            ("filter", "voice_tmp_path_queue_min_start_play"): 0,
            ("filter", "voice_tmp_path_queue_max_len"): max_len,
            ("filter", "message_queue_max_len"): mq_max_len,
            ("filter", "message_queue_replace_same_priority"): True,
            ("play_audio", "enable"): True,
        }
        if extra:
            self._data.update(extra)

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


def _seg(gid, seq, total=3, seg_type="comment", with_link=False):
    e = {"type": seg_type,
         "voice_path": f"{gid}-seg{seq}.wav",
         "content": f"段{seq}",
         "split_group": {"id": gid, "seq": seq, "total": total}}
    if with_link:
        e["reply_link"] = gid
    return e


def _plain(seg_type="gift", label="g"):
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


class TestGuardSnapshot:
    """A 快照化：stamp / 回填 / 候选过滤读快照"""

    def test_new_entry_stamped_on_enqueue(self):
        fx = _Fixture(config=_FakeConfig())
        fx.data_priority_insert("待播放音频列表", _seg("c1", 1))
        fx.data_priority_insert("等待合成消息", _seg("c2", 1))
        for e in (fx.voice_tmp_path_queue[0], fx.message_queue[0]):
            assert e.get("_guard_link_active") is False
            assert e.get("_guard_sg_active") is False

    def test_activation_backfills_queue_entries(self):
        """组激活回填：先期入队（快照 False）的段在激活后刷新为 True"""
        fx = _Fixture(config=_FakeConfig(max_len=4))
        fx.data_priority_insert("待播放音频列表", _seg("A", 1))
        fx.data_priority_insert("待播放音频列表", _seg("A", 2))
        assert all(not e.get("_guard_sg_active")
                   for e in fx.voice_tmp_path_queue)
        fx._mark_split_group_active("A")
        assert all(e.get("_guard_sg_active") is True
                   for e in fx.voice_tmp_path_queue)
        # S1 口径：同 link 条目回填
        fx.data_priority_insert("等待合成消息", _seg("B", 1, with_link=True))
        assert not fx.message_queue[0].get("_guard_link_active")
        fx.mark_reply_group_active("B")
        assert fx.message_queue[0].get("_guard_link_active") is True

    def test_candidate_filter_uses_snapshot_not_live_query(self):
        """TTL 过期后活查询=False 但快照仍=True：候选保护按快照（保守方向），
        证明过滤走快照路径而非活查询"""
        fx = _Fixture(config=_FakeConfig(max_len=2))
        fx.data_priority_insert("待播放音频列表", _seg("A", 1))
        fx.data_priority_insert("待播放音频列表", _seg("A", 2))
        fx._mark_split_group_active("A")  # 回填快照 True
        # 模拟 TTL 过期：表内时刻已超 300s（活查询 False）
        QueueMixin._split_group_active["A"] = time.monotonic() - 400.0
        assert fx._split_group_protected(fx.voice_tmp_path_queue[0]) is False
        # 新组 B 段满载入场：候选（A 段）按快照仍保护 → 无候选 → 规则4 舍弃
        result = fx.data_priority_insert("待播放音频列表", _seg("B", 1))
        assert result["code"] == 1
        assert len(fx.voice_tmp_path_queue) == 2  # 未被驱逐

    def test_mark_on_abandoned_link_grants_no_protection(self):
        """放弃链接的组激活不授予 S1 快照（与活查询 abandoned 优先语义一致）"""
        fx = _Fixture(config=_FakeConfig())
        fx.data_priority_insert("等待合成消息", _seg("L1", 1, with_link=True))
        fx.abandon_reply_link("L1")
        fx.mark_reply_group_active("L1")
        assert fx.message_queue[0].get("_guard_link_active") is False


class TestConfigurableThresholds:
    """B1 阈值配置化读侧"""

    def test_reply_gate_timeout_from_config(self):
        fx = _Fixture(config=_FakeConfig(extra={
            ("play_audio", "reply_gate_timeout_s"): 20.0,
            ("play_audio", "reply_gate_extend_s"): 12.0}))
        assert fx._reply_gate_timeout_s() == 20.0
        assert fx._reply_gate_extend_s() == 12.0

    def test_reply_gate_defaults_without_config(self):
        fx = _Fixture(config=_FakeConfig())
        assert fx._reply_gate_timeout_s() == 15.0
        assert fx._reply_gate_extend_s() == 10.0

    def test_group_ttl_from_config(self):
        fx = _Fixture(config=_FakeConfig(extra={
            ("filter", "reply_group_active_ttl_s"): 100.0}))
        assert fx._group_active_ttl_s() == 100.0

    def test_group_ttl_default_without_config(self):
        fx = _Fixture(config=_FakeConfig())
        assert fx._group_active_ttl_s() == 300.0

    def test_ttl_lapse_counter(self):
        """B4：TTL 保险丝失效计数（组停顿超 TTL 后恢复登记）"""
        fx = _Fixture(config=_FakeConfig())
        fx._mark_split_group_active("A")
        QueueMixin._split_group_active["A"] = time.monotonic() - 400.0
        fx._mark_split_group_active("A")  # 恢复登记 → lapsed 计数
        assert _obs_snapshot()["drops"].get("group_active_ttl_lapsed", 0) >= 1


class TestCalibrationAndObs:
    """B2/B3：LLM/段数/深度观测 + 定标器建议值"""

    def test_record_llm_and_split_total_and_depth(self):
        record_llm(2.5, True)
        record_llm(4.0, False)
        record_split_total(12)
        record_queue_depth("message_queue", 35, 20)
        info = _obs_snapshot()
        assert info["llm_synth"]["n"] >= 2
        assert info["llm_synth"]["ok_n"] >= 1
        assert info["split_total_max"] >= 12
        assert info["queue_depth"]["message_queue"]["real_max"] >= 35

    def test_recommended_reply_gate_timeout(self):
        info = {"llm_synth": {"n": 50, "p95_s": 6.0},
                "tts_synth": {"n": 50, "p95_s": 2.0}}
        rec = cal.recommended_reply_gate_timeout_s(info)
        assert rec == 15.0  # 6 + 2*2 + 5
        assert cal.recommended_reply_gate_extend_s(info, rec) == 7.5

    def test_recommended_returns_none_when_insufficient(self):
        info = {"llm_synth": {"n": 2, "p95_s": 6.0}}
        assert cal.recommended_reply_gate_timeout_s(info) is None

    def test_recommended_group_ttl(self):
        info = {"tts_synth": {"n": 50, "p95_s": 2.0}, "split_total_max": 100}
        assert cal.recommended_group_active_ttl_s(info) == 240.0  # 2*1.2*100

    def test_run_calibration_does_not_raise(self):
        fx = _Fixture(config=_FakeConfig())
        cal.run_calibration(fx.config)  # 全兜底，不抛即可
        cal.maybe_run_calibration(fx.config)

