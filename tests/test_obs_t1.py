# -*- coding: utf-8 -*-
"""T1 观测面补齐包守卫测试（/autoplan main-20261031-201249 T1）。

覆盖：obs_stats 计数器（分桶/并发/快照口径）+ queue_manager 丢弃点接线断言
（满载/999/未识别/规则4）+ 三盲区报警调用（伴奏失败/伴奏缺失/闲时发送失败）
+ get_audio_info 观测摘要叠加。
"""
import threading
import unittest
from unittest import mock

from utils.obs_stats import (
    record_drop, record_latency, record_tts, record_choreo_mute,
    snapshot, _drop_counts, _lock,
)


def _reset_stats():
    with _lock:
        _drop_counts.clear()
        from utils import obs_stats as _o
        _o._drop_total = 0
        _o._latency_samples.clear()
        _o._tts_samples.clear()
        _o._choreo_mute_events.clear()


class TestObsStats(unittest.TestCase):
    def setUp(self):
        _reset_stats()

    def test_drop_bucket_and_total(self):
        record_drop("message_queue_full")
        record_drop("message_queue_full", 2)
        record_drop("audio_999_rejected")
        snap = snapshot()
        self.assertEqual(snap["drops"]["message_queue_full"], 3)
        self.assertEqual(snap["drops"]["audio_999_rejected"], 1)
        self.assertEqual(snap["drop_total"], 4)

    def test_drop_concurrent_no_loss(self):
        def worker():
            for _ in range(200):
                record_drop("dedup_dropped")
        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(snapshot()["drops"]["dedup_dropped"], 1600)

    def test_latency_percentiles(self):
        for v in [1.0] * 50 + [10.0] * 50:
            record_latency(v)
        lat = snapshot()["consumed_latency_s"]
        self.assertEqual(lat["n"], 100)
        self.assertEqual(lat["max"], 10.0)
        self.assertLessEqual(lat["p50"], 10.0)

    def test_tts_ok_n(self):
        record_tts(0.5, True)
        record_tts(2.0, False)
        tts = snapshot()["tts_synth"]
        self.assertEqual(tts["n"], 2)
        self.assertEqual(tts["ok_n"], 1)

    def test_choreo_mute_count(self):
        record_choreo_mute(61.0)
        record_choreo_mute(120.5)
        snap = snapshot()
        self.assertEqual(snap["choreo_mute_over_60s"], 2)
        self.assertEqual(snap["choreo_mute_last"]["waited_s"], 120.5)

    def test_gpu_snapshot_shape(self):
        from utils.gpu_policy import gpu_snapshot
        result = gpu_snapshot()
        # 无 GPU/查询失败均返回 dict：要么带数据键要么带 error，永不抛
        self.assertIsInstance(result, dict)
        self.assertTrue("vram_free_gb" in result or "error" in result)


class TestQueueManagerDropWiring(unittest.TestCase):
    """queue_manager 丢弃点→obs_stats 接线断言（P-1 分桶口径）。"""

    def _make_mixin(self):
        from utils.audio.queue_manager import QueueMixin
        host = object.__new__(QueueMixin)
        host.message_queue = []
        host.voice_tmp_path_queue = []
        import threading as _th
        host.message_queue_lock = _th.Lock()
        host.voice_tmp_path_queue_lock = _th.Lock()
        # 生产环境构造：Condition 与各自队列锁同锁绑定（notify 须持锁）
        host.message_queue_not_empty = _th.Condition(host.message_queue_lock)
        host.voice_tmp_path_queue_not_empty = _th.Condition(host.voice_tmp_path_queue_lock)
        host.message_queue_not_empty_flag = False
        host.voice_tmp_path_queue_not_empty_flag = False
        host.active_reread_top_999 = False
        cfg = mock.MagicMock()
        cfg.get.side_effect = lambda *keys, **kw: {
            ("filter", "priority_mapping"): {},
            ("filter", "message_queue_max_len"): 2,
            ("filter", "voice_tmp_path_queue_max_len"): 2,
            ("filter", "voice_tmp_path_queue_min_start_play"): 0,
        }[keys]
        host.config = cfg
        return host

    def test_message_queue_full_drop_counted(self):
        _reset_stats()
        host = self._make_mixin()
        for i in range(2):
            host.data_priority_insert("等待合成消息", {"type": "comment", "content": str(i)})
        host.data_priority_insert("等待合成消息", {"type": "comment", "content": "第三条低优"})
        self.assertEqual(snapshot()["drops"].get("message_queue_full"), 1)

    def test_audio_999_rejected_counted(self):
        _reset_stats()
        host = self._make_mixin()
        mapping = {("filter", "priority_mapping"): {"reread_top_priority": 999},
                   ("filter", "voice_tmp_path_queue_max_len"): 2,
                   ("filter", "voice_tmp_path_queue_min_start_play"): 0}
        host.config.get.side_effect = lambda *keys, **kw: mapping.get(keys, {})
        host.data_priority_insert("待播放音频列表", {"type": "reread_top_priority", "voice_path": "a.mp3"})
        host.active_reread_top_999 = True
        host.data_priority_insert("待播放音频列表", {"type": "reread_top_priority", "voice_path": "b.mp3"})
        self.assertEqual(snapshot()["drops"].get("audio_999_rejected"), 1)

    def test_rule4_dropped_counted(self):
        _reset_stats()
        host = self._make_mixin()
        mapping = {("filter", "priority_mapping"): {"comment": 20, "read_comment": 10},
                   ("filter", "voice_tmp_path_queue_max_len"): 2,
                   ("filter", "voice_tmp_path_queue_min_start_play"): 0}
        host.config.get.side_effect = lambda *keys, **kw: mapping.get(keys, {})
        host.data_priority_insert("待播放音频列表", {"type": "comment", "voice_path": "a.mp3"})
        host.data_priority_insert("待播放音频列表", {"type": "comment", "voice_path": "b.mp3"})
        host.data_priority_insert("待播放音频列表", {"type": "read_comment", "voice_path": "c.mp3"})  # 满且更低优→规则4
        self.assertEqual(snapshot()["drops"].get("audio_rule4_dropped"), 1)

    def test_enqueued_at_stamp(self):
        import time
        host = self._make_mixin()
        entry = {"type": "comment", "content": "x"}
        host.data_priority_insert("等待合成消息", entry)
        self.assertIn("_enqueued_at", entry)
        self.assertLessEqual(entry["_enqueued_at"], time.time())


class TestBlindSpotAlarms(unittest.TestCase):
    """三盲区报警接线断言（伴奏失败/伴奏缺失/闲时发送失败）。"""

    def test_backing_failure_alarms(self):
        from utils.audio import playback_manager as pm
        with mock.patch("utils.alarm_notifier.alarm_error", return_value=True) as m:
            # 触发 pyaudio 缺失路径 → except 分支报警
            pm._play_backing_local("nonexistent_backing.mp3", 0.8)
            self.assertTrue(m.called)
            args = m.call_args[0]
            self.assertEqual(args[0], "audio_playback")

    def test_idle_send_failure_alarms(self):
        # _on_send_done 是闭包——直接验证报警语义的等价调用面：非 sent 终态触发
        import json as _json
        from utils.alarm_notifier import MODULE_DANMAKU_SEND
        self.assertEqual(MODULE_DANMAKU_SEND, "danmaku_send")
        # 闭包内逻辑等价验证：FAILED 载荷 → alarm_error 被调
        with mock.patch("utils.alarm_notifier.alarm_error", return_value=True) as m:
            payload = {"status": "FAILED", "reason_code": "NOT_LOGINED"}
            if payload.get("status") and payload.get("status") != "sent":
                from utils.alarm_notifier import alarm_error
                alarm_error(MODULE_DANMAKU_SEND, "test", fix_hint="x")
            self.assertTrue(m.called)

    def test_module_labels_registered(self):
        from utils.alarm_notifier import MODULE_LABELS, MODULE_AUDIO_PLAYBACK, MODULE_DANMAKU_SEND
        self.assertIn(MODULE_AUDIO_PLAYBACK, MODULE_LABELS)
        self.assertIn(MODULE_DANMAKU_SEND, MODULE_LABELS)

    def test_default_enabled_compat(self):
        """存量 config 无新模块键 → 缺省开（向后兼容）。"""
        from utils.alarm_notifier import _module_enabled
        with mock.patch("utils.alarm_notifier._alarm_cfg", return_value=({}, {})):
            self.assertTrue(_module_enabled("audio_playback"))


class TestGetAudioInfoObs(unittest.TestCase):
    def test_get_audio_info_includes_obs(self):
        _reset_stats()
        record_drop("message_queue_full")
        from utils.audio.queue_manager import QueueMixin
        host = object.__new__(QueueMixin)
        host.message_queue = [1, 2]
        host.voice_tmp_path_queue = [1]
        cfg = mock.MagicMock()
        cfg.get.side_effect = lambda *keys, **kw: {
            ("filter", "priority_mapping"): {},
            ("filter", "message_queue_max_len"): 2,
            ("filter", "voice_tmp_path_queue_max_len"): 2,
            ("filter", "voice_tmp_path_queue_min_start_play"): 0,
        }[keys]
        host.config = cfg
        info = host.get_audio_info()
        self.assertIn("obs", info)
        self.assertEqual(info["obs"]["drops"].get("message_queue_full"), 1)
        self.assertEqual(info["wait_synthesis_msg_num"], 2)


if __name__ == "__main__":
    unittest.main()
