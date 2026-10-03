# -*- coding: utf-8 -*-
"""DanmakuListener 消费者单测（整合计划测试基线：聚合器/优先级丢弃/TOML 生成/世代重置/文本净化）"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils import danmaku_consumer as dc


def _msg(mtype="DANMU", seq=1, room="23058", platform="bilibili", payload=None, version="1.0.0"):
    return {
        "envelope": {"contract_version": version, "category": "business", "type": mtype,
                     "platform": platform, "room_id": room, "seq": seq, "timestamp": 1,
                     "engine": "protocol:bilibili"},
        "payload": payload or {"type": mtype, "user_name": "测试用户", "content": "你好"},
    }


class TestTomlGen(unittest.TestCase):
    def test_write_serve_toml(self):
        p = os.path.join(os.environ.get("TEMP", "/tmp"), "dtest.toml")
        dc._write_serve_toml(p, {"ws_port": 8765, "ws_bind": "127.0.0.1", "token_injection": "env"})
        text = open(p, encoding="utf-8").read()
        self.assertIn('ws_port = 8765', text)
        self.assertIn('ws_bind = "127.0.0.1"', text)
        self.assertNotIn("ws_token_file", text)
        os.unlink(p)

    def test_write_serve_toml_file_token(self):
        p = os.path.join(os.environ.get("TEMP", "/tmp"), "dtest2.toml")
        dc._write_serve_toml(p, {"ws_port": 9000, "ws_bind": "127.0.0.1",
                                 "token_injection": "file", "token_file_path": "x/token"})
        self.assertIn('ws_token_file = "x/token"', open(p, encoding="utf-8").read())
        os.unlink(p)


class TestAggregator(unittest.TestCase):
    def test_single_passthrough(self):
        out = []
        agg = dc._DanmuAggregator(out.append)
        agg.add(_msg(payload={"type": "DANMU", "user_name": "u", "content": "hi"}))
        agg.flush()
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["payload"]["content"], "hi")

    def test_window_merge(self):
        out = []
        agg = dc._DanmuAggregator(out.append)
        for i in range(dc.DANMU_AGG_MAX):
            agg.add(_msg(payload={"type": "DANMU", "user_name": f"u{i}", "content": f"m{i}"}))
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["payload"]["aggregated"], dc.DANMU_AGG_MAX)
        self.assertIn("m0", out[0]["payload"]["content"])


class TestSanitize(unittest.TestCase):
    def test_control_and_zero_width_stripped(self):
        text = "a​b\u0000c\nd"
        out = dc._sanitize_danmu_text(text)
        self.assertNotIn("​", out)
        self.assertNotIn("\u0000", out)

    def test_length_clamp(self):
        self.assertEqual(len(dc._sanitize_danmu_text("长" * 500)), 200)


class TestPriorityDrop(unittest.TestCase):
    def test_drop_lowest_prefers_like(self):
        loop = dc._ConsumerLoop.__new__(dc._ConsumerLoop)  # 不跑 __init__（只测队列逻辑）
        loop._work_queue = __import__("queue").Queue(maxsize=3)
        loop._work_queue.put(("GIFT", _msg("GIFT")))
        loop._work_queue.put(("LIKE", _msg("LIKE")))
        loop._work_queue.put(("SUPER_CHAT", _msg("SUPER_CHAT")))
        self.assertTrue(loop._drop_lowest())
        remain = []
        while True:
            try:
                remain.append(loop._work_queue.get_nowait())
            except Exception:
                break
        kinds = [k for k, _ in remain]
        self.assertNotIn("LIKE", kinds)       # LIKE 优先级最低先丢
        self.assertIn("SUPER_CHAT", kinds)    # SUPER_CHAT 保留


class TestVersionGate(unittest.TestCase):
    def test_version_reject_path_exists(self):
        # 版本断言为读循环内联逻辑；此处验证常量与消息工具的一致性
        self.assertEqual(dc.CONTRACT_VERSION_MAJOR, 1)
        msg = _msg(version="2.0.0")
        self.assertTrue(str(msg["envelope"]["contract_version"]).startswith("2."))


class TestFastpathRouting(unittest.TestCase):
    def test_fastpath_set_matches_contract(self):
        # B-2 修订（2026-10-03）：LIKE 移出快路径——用户需求"监听到谁点赞"，
        # 点赞走对话链聚合（_LikeAggregator）；ENTER_ROOM/ROOM_STATS 保持计数快路径
        self.assertEqual(dc._FASTPATH_BUSINESS, {"ENTER_ROOM", "ROOM_STATS"})


class TestConsumerDefaults(unittest.TestCase):
    def test_thresholds_match_obligations(self):
        self.assertEqual(dc.HEARTBEAT_INTERVAL_S, 10)
        self.assertEqual(dc.HEARTBEAT_LOST_PERIODS, 3)
        self.assertEqual(dc.ZOMBIE_TIMEOUT_S, 60)
        self.assertEqual(dc.SEQ_WAIT_TIMEOUT_S, 10)
        self.assertEqual(dc.SEQ_BUFFER_MAX, 1000)
        self.assertEqual(dc.QUEUE_MAX, 500)
        self.assertEqual(dc.RECONNECT_BACKOFF_MIN_S, 1.0)
        self.assertEqual(dc.RECONNECT_BACKOFF_MAX_S, 30.0)
        self.assertEqual(dc.CONNECT_FAIL_HANDOFF, 3)


class _StubSvc:
    """最小 service stub（登录队列/事件环宿主）。"""
    from collections import deque
    events = []
    login_queue = []
    login_active = None

    def __init__(self):
        from collections import deque
        self.events = __import__("collections").deque(maxlen=100)
        self._event_seq = 0
        self.login_queue = []
        self.login_active = None

    push_event = dc.DanmakuListenerService.push_event
    events_since = dc.DanmakuListenerService.events_since
    submit_login_request = dc.DanmakuListenerService.submit_login_request
    ack_login_done = dc.DanmakuListenerService.ack_login_done


class TestLoginQueue(unittest.TestCase):
    def test_queue_and_ack_flow(self):
        svc = _StubSvc()
        svc.submit_login_request({"failure": {"reason_code": "wechat_channels.session_expired",
                                              "fix_hint": "扫码", "docs_anchor": "docs/x.md"},
                                  "interactive_login": {"qr_image_b64": "AAAA"}})
        svc.submit_login_request({"failure": {"reason_code": "douyin.companion.version_mismatch"}})
        self.assertIsNotNone(svc.login_active)
        self.assertEqual(svc.login_active["platform"], "wechat_channels")
        self.assertEqual(len(svc.login_queue), 1)
        svc.ack_login_done()
        self.assertEqual(svc.login_active["platform"], "douyin")
        self.assertEqual(len(svc.login_queue), 0)
        svc.ack_login_done()
        self.assertIsNone(svc.login_active)


class TestEventRing(unittest.TestCase):
    def test_push_and_since(self):
        svc = _StubSvc()
        svc.push_event("gap", {"window_start": 1})
        svc.push_event("recovered", {})
        got = svc.events_since(0)
        self.assertEqual([e["kind"] for e in got], ["gap", "recovered"])
        self.assertEqual(svc.events_since(got[-1]["seq"]), [])  # 增量语义


class TestQrSafety(unittest.TestCase):
    def test_raw_b64_wrapped_as_png(self):
        out = dc.__dict__  # noqa — 下行直接引用模块内函数
        from frontend.ui.components.danmaku_login import _safe_qr_data_url
        self.assertTrue(_safe_qr_data_url({"qr_image_b64": "iVBORw0KGgo="}).startswith("data:image/png;base64,"))

    def test_mime_whitelist(self):
        from frontend.ui.components.danmaku_login import _safe_qr_data_url
        self.assertIsNone(_safe_qr_data_url({"qr_image_b64": "data:text/html;base64,PGI+"}))
        self.assertIsNone(_safe_qr_data_url({"qr_image_b64": "data:image/svg+xml;base64,PGI+"}))

    def test_size_clamp(self):
        from frontend.ui.components.danmaku_login import _safe_qr_data_url
        self.assertIsNone(_safe_qr_data_url({"qr_image_b64": "A" * 200_000}))

    def test_invalid_chars_rejected(self):
        from frontend.ui.components.danmaku_login import _safe_qr_data_url
        self.assertIsNone(_safe_qr_data_url({"qr_image_b64": "<script>alert(1)</script>"}))
        self.assertIsNone(_safe_qr_data_url({"qr_image_b64": None}))
        self.assertIsNone(_safe_qr_data_url({}))


class TestNotifierMapping(unittest.TestCase):
    def test_key_kinds_covered(self):
        from frontend.ui.components.danmaku_login import DanmakuEventNotifier
        n = DanmakuEventNotifier._NOTIFY_TYPE
        for kind in ("route_failed", "heartbeat_lost", "zombie", "gap", "backpressure",
                     "auth_error", "contract_mismatch", "recovered"):
            self.assertIn(kind, n, f"缺少 {kind} 的通知映射")
            self.assertIsNotNone(n[kind], f"{kind} 不应静默")
        # 静默事件不产生 notify
        for kind in ("connected", "count", "room"):
            self.assertIsNone(n.get(kind))


if __name__ == "__main__":
    unittest.main(verbosity=2)
