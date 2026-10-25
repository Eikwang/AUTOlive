# -*- coding: utf-8 -*-
"""DanmakuListener 进程内托管层单测（整合计划第二阶段：P1-1~P1-3 + 回归契约）

退役（整合计划"Tests to Retire"）：test_write_serve_toml / test_write_serve_toml_file_token
——serve TOML 生成随进程内化删除（Test Plan Artifact 2026-10-06 登记）。
保留回归契约：聚合器/文本净化/优先级丢弃（迁移到 _BoundedDropQueue）/登录队列/事件环/QR 安全。
新增：GIFT 增量换算（含首见/LRU 淘汰）/有界队列满载丢弃/下播裁剪/事件去重窗口/活跃路由。
"""
import json
import os
import sys
import unittest
import unittest.mock as _mock
from collections import OrderedDict
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils import danmaku_consumer as dc


def _msg(mtype="DANMU", seq=1, room="23058", platform="bilibili", payload=None, version="1.0.0"):
    return {
        "envelope": {"contract_version": version, "category": "business", "type": mtype,
                     "platform": platform, "room_id": room, "seq": seq, "timestamp": 1,
                     "engine": "protocol:bilibili"},
        "payload": payload or {"type": mtype, "user_name": "测试用户", "content": "你好"},
    }


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


class TestBoundedDropQueue(unittest.TestCase):
    """Eng E1：deque+Condition 原子结构（替代 queue.Queue 排空-回灌竞态）。"""

    def test_put_get_fifo(self):
        q = dc._BoundedDropQueue(3)
        q.put(("DANMU", 1))
        q.put(("DANMU", 2))
        self.assertEqual(q.get(timeout=0.1), ("DANMU", 1))
        self.assertEqual(q.get(timeout=0.1), ("DANMU", 2))
        self.assertIsNone(q.get(timeout=0.05))

    def test_overflow_drops_lowest_priority(self):
        q = dc._BoundedDropQueue(3)
        q.put(("GIFT", "g"))
        q.put(("SUPER_CHAT", "sc"))
        q.put(("DANMU", "d1"))
        q.put(("LIKE", "l"))          # 满载：已有最低优先级 DANMU 被淘汰腾位
        self.assertEqual(q.qsize(), 3)
        self.assertEqual(q.dropped_count, 1)
        kinds = [it[0] for it in [q.get(timeout=0.05) for _ in range(3)]]
        self.assertNotIn("DANMU", kinds)     # 已有最低优先级被淘汰
        self.assertIn("LIKE", kinds)         # 新条腾位后保留
        self.assertIn("SUPER_CHAT", kinds)   # 保礼物类（背压同序）
        self.assertIn("GIFT", kinds)

    def test_overflow_drops_newest_when_exhausted(self):
        """R3-2 兜底：可丢类型穷尽（队列仅剩 GIFT/SUPER_CHAT 保类型）→ 丢最新（本条）。"""
        q = dc._BoundedDropQueue(2)
        q.put(("GIFT", 1))
        q.put(("SUPER_CHAT", 2))
        q.put(("GIFT", 3))            # 队列全是保类型 → 新条被丢
        self.assertEqual(q.qsize(), 2)
        self.assertEqual(q.dropped_count, 1)
        self.assertEqual(q.get(timeout=0.05), ("GIFT", 1))     # 原有保类型完整
        self.assertEqual(q.get(timeout=0.05), ("SUPER_CHAT", 2))

    def test_prune_keep_offline_clip(self):
        """下播裁剪：仅保留 GIFT/SUPER_CHAT（Eng E1 同构原子操作）。"""
        q = dc._BoundedDropQueue(10)
        for t in ("DANMU", "LIKE", "GIFT", "ENTER_ROOM", "SUPER_CHAT"):
            q.put((t, t))
        dropped = q.prune_keep({"GIFT", "SUPER_CHAT"})
        self.assertEqual(dropped, 3)
        self.assertEqual(q.qsize(), 2)


class TestGiftIncrement(unittest.TestCase):
    """P1-2 义务：抖音 GIFT.count 是 totalCount 累计值透传——增量换算。"""

    def setUp(self):
        self.lru = OrderedDict()

    def test_sequence_convert_to_increment(self):
        payload = {"user_id": "u1", "gift_id": "g1", "gift_count": 1, "gift_value": 10}
        self.assertEqual(dc._gift_increment("room1", payload, self.lru), 1)
        payload["gift_count"] = 2
        self.assertEqual(dc._gift_increment("room1", payload, self.lru), 1)
        payload["gift_count"] = 3
        self.assertEqual(dc._gift_increment("room1", payload, self.lru), 1)

    def test_first_seen_high_total_counts_as_one(self):
        """首见 totalCount=5 只报 1（答谢宁可少报不可重复多报）。"""
        payload = {"user_id": "u1", "gift_id": "g1", "gift_count": 5}
        self.assertEqual(dc._gift_increment("room1", payload, self.lru), 1)

    def test_non_increasing_dropped(self):
        payload = {"user_id": "u1", "gift_id": "g1", "gift_count": 3}
        dc._gift_increment("room1", payload, self.lru)
        payload["gift_count"] = 3
        self.assertEqual(dc._gift_increment("room1", payload, self.lru), 0)
        payload["gift_count"] = 2
        self.assertEqual(dc._gift_increment("room1", payload, self.lru), 0)

    def test_lru_eviction_retriggers_first_seen(self):
        payload = {"user_id": "u0", "gift_id": "g0", "gift_count": 9}
        self.assertEqual(dc._gift_increment("room0", payload, self.lru), 1)  # 首见
        for i in range(dc.GIFT_LRU_MAX):   # 经换算函数灌洪峰（触发真实淘汰）
            flood = {"user_id": f"u{i}", "gift_id": f"g{i}", "gift_count": 9}
            dc._gift_increment("flood", flood, self.lru)
        self.assertNotIn(("room0", "u0", "g0"), self.lru)   # 已淘汰
        self.assertEqual(dc._gift_increment("room0", payload, self.lru), 1)  # 首见语义重触发


class TestDispatchRouting(unittest.TestCase):
    """2026-10-24 审查批次：ENTER_ROOM/ROOM_STATS 计数快路径撤销——三类改经
    my_handle.process_data 走遗忘&保留+去重前置（入场答谢恢复、点赞独立类别、
    关注不再绕过 follow 遗忘定时器）。"""

    class _RecHandle:
        def __init__(self):
            self.calls = []

        def process_data(self, data, type_):
            self.calls.append((type_, data))

    def _make_svc_and_stub(self):
        from utils import my_global
        svc = dc.DanmakuListenerService(lambda: {})
        h = self._RecHandle()
        old = getattr(my_global, "my_handle", None)
        my_global.my_handle = h
        return svc, h, old, my_global

    def test_enter_room_dispatched_as_entrance(self):
        svc, h, old, mg = self._make_svc_and_stub()
        try:
            svc._dispatch("ENTER_ROOM", {"envelope": {"platform": "bilibili", "room_id": "1"},
                                         "payload": {"user_name": "u1"}})
            self.assertIn("entrance", [t for t, _ in h.calls])
        finally:
            mg.my_handle = old

    def test_room_stats_dispatched_with_value_key(self):
        svc, h, old, mg = self._make_svc_and_stub()
        try:
            svc._dispatch("ROOM_STATS", {"envelope": {"platform": "bilibili", "room_id": "1"},
                                         "payload": {"type": "ROOM_STATS", "viewer_count": 10}})
            types = [t for t, _ in h.calls]
            self.assertIn("room_stats", types)
            data = next(d for t, d in h.calls if t == "room_stats")
            self.assertEqual(data["stats"], {"viewer_count": 10})
            self.assertEqual(data["content"], "viewer_count=10")
        finally:
            mg.my_handle = old

    def test_social_follow_goes_through_process_data(self):
        svc, h, old, mg = self._make_svc_and_stub()
        try:
            svc._dispatch("SOCIAL", {"envelope": {"platform": "bilibili", "room_id": "1"},
                                     "payload": {"user_name": "u1", "action": "关注"}})
            self.assertIn("follow", [t for t, _ in h.calls])
        finally:
            mg.my_handle = old

    def test_like_agg_dispatches_to_like_flag(self):
        svc, h, old, mg = self._make_svc_and_stub()
        try:
            svc._dispatch_like_agg({"envelope": {"platform": "bilibili"},
                                    "like_user_summary": "u1", "like_count": 3})
            types = [t for t, _ in h.calls]
            self.assertIn("like", types)
            self.assertNotIn("comment", types)
        finally:
            mg.my_handle = old


class TestConsumerDefaults(unittest.TestCase):
    def test_thresholds_match_obligations(self):
        self.assertEqual(dc.DANMU_AGG_WINDOW_S, 5.0)
        self.assertEqual(dc.DANMU_AGG_MAX, 5)
        self.assertEqual(dc.CONTRACT_VERSION_MAJOR, 1)
        self.assertEqual(dc.QUEUE_MAX_DEFAULT, 500)          # DX 5.1 可覆盖默认
        self.assertEqual(dc.WEB_CONSOLE_PORT_DEFAULT, 8080)
        self.assertEqual(dc.STALL_THRESHOLD_S_DEFAULT, 60)
        self.assertEqual(dc.REBUILD_GUARD_MAX, 3)            # P1-6 失控防护
        self.assertEqual(dc.GIFT_LRU_MAX, 5000)              # Section 7 闭环


class _StubSvc:
    """最小 service stub（登录队列/事件环宿主——保留自上轮）。"""

    def __init__(self):
        from collections import deque
        self.events = __import__("collections").deque(maxlen=200)
        self._event_seq = 0
        self._evt_lock = __import__("threading").Lock()
        self._seen_event_keys = OrderedDict()
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

    def test_dedup_window_60s(self):
        """Eng E3：事件去重键加 60s 窗口（恢复→再次 GAP 不被吞）。"""
        svc = _StubSvc()
        svc.push_event("gap", {"r": 1}, dedup_key="gap:1:network")
        svc.push_event("gap", {"r": 1}, dedup_key="gap:1:network")
        got = svc.events_since(0)
        self.assertEqual(len([e for e in got if e["kind"] == "gap"]), 1)  # 同窗口去重
        svc._seen_event_keys.clear()   # 窗口过期（模拟 60s 后）
        svc.push_event("gap", {"r": 1}, dedup_key="gap:1:network")
        got = svc.events_since(0)
        self.assertEqual(len([e for e in got if e["kind"] == "gap"]), 2)  # 新窗口放行


class TestNormalizeWire(unittest.TestCase):
    def test_flat_and_nested(self):
        flat = {"contract_version": "1.0.0", "category": "business", "type": "DANMU",
                "platform": "bilibili", "room_id": "1", "seq": 1, "timestamp": 1,
                "engine": "e", "payload": {"content": "x"}}
        n = dc._normalize_wire(flat)
        self.assertEqual(n["type"], "DANMU")
        self.assertEqual(n["room"], "1")
        nested = {"envelope": flat, "payload": flat["payload"]}
        n2 = dc._normalize_wire(nested)
        self.assertEqual(n2["type"], "DANMU")
        self.assertIsNone(dc._normalize_wire({"bad": 1}))
        self.assertIsNone(dc._normalize_wire("not-dict"))


class TestOnWireRouting(unittest.TestCase):
    """_on_wire 数据面：system 进事件环 / business 进队列 / 快路径计数。"""

    def _svc(self):
        svc = dc.DanmakuListenerService.__new__(dc.DanmakuListenerService)
        svc._queue = dc._BoundedDropQueue(100)
        svc._evt_lock = __import__("threading").Lock()
        from collections import deque
        svc.events = deque(maxlen=200)
        svc._event_seq = 0
        svc._seen_event_keys = OrderedDict()
        svc.login_queue = []
        svc.login_active = None
        svc._unknown_type_count = 0
        svc._invalid_msg_count = 0
        svc._msg_today_count = 0
        svc._last_msg_ts = 0.0
        svc._last_heartbeat = 0.0
        svc._no_active_warned = False
        svc._cfg = lambda: {"rooms": [{"platform": "bilibili", "room_id": "23058",
                                       "enabled": True, "active": True}]}
        return svc

    def test_business_routes_to_queue(self):
        svc = self._svc()
        svc._on_wire(_msg(room="23058"))
        self.assertEqual(svc._queue.qsize(), 1)

    def test_system_needs_login_enters_login_queue(self):
        svc = self._svc()
        svc._on_wire({"contract_version": "1.0.0", "category": "system", "type": "NEEDS_LOGIN",
                      "platform": "wechat_channels", "room_id": "1", "seq": 1, "timestamp": 1,
                      "engine": "e",
                      "payload": {"type": "NEEDS_LOGIN",
                                  "failure": {"reason_code": "wechat_channels.session_expired",
                                              "fix_hint": "扫码", "docs_anchor": "d"},
                                  "interactive_login": {"qr_image_b64": "AAAA"}}})
        self.assertIsNotNone(svc.login_active)

    def test_all_rooms_queued(self):
        """R2 用户裁定：多房间全互动——非活跃概念退役，所有 enabled 房间消息进队列。"""
        svc = self._svc()
        svc._on_wire(_msg(room="99999"))   # 任意房间
        self.assertEqual(svc._queue.qsize(), 1)

    def test_version_gate(self):
        svc = self._svc()
        svc._on_wire(_msg(version="2.0.0"))
        self.assertEqual(svc._queue.qsize(), 0)   # 2.x 拒绝
        self.assertEqual(len(svc.events), 1)      # contract_mismatch 事件


class TestConfigWriterMatrix(unittest.TestCase):
    """写者分配矩阵（Eng F-ENG-2 critical 回归，R2 实测修正后语义）：
    main.py 可达 → danmaku_listener 节磁盘回读保护（防覆盖窄端点写入）；
    main.py 不可达 → webui 是唯一写者，直接 dump 内存（配置期直写语义——
    首版无条件回读会覆盖 set_config 新值，实测房间/关键词"保存后消失"）。"""

    def _make(self, tmpdir: str, danmaku_sec: dict) -> Any:
        import json
        from frontend.config import settings as fs
        cfg_file = os.path.join(tmpdir, "config.json")
        disk_cfg = {"webui": {"port": 8086},
                    "danmaku_listener": danmaku_sec, "other": {"keep": 1}}
        with open(cfg_file, "w", encoding="utf-8") as f:
            json.dump(disk_cfg, f, ensure_ascii=False)
        st = fs.Settings(cfg_file)
        return st

    def test_main_reachable_preserves_section(self):
        """main.py 可达：磁盘节（窄端点写入）保留，webui 旧快照不覆盖。"""
        import json
        import tempfile
        from unittest.mock import patch
        from frontend.config import settings as fs
        tmpdir = tempfile.mkdtemp()
        st = self._make(tmpdir, {"rooms": [{"platform": "douyu", "room_id": "9",
                                            "enabled": True, "active": True}],
                                 "keywords": ["窄端点写入词"]})
        st._config["danmaku_listener"] = {}  # webui 内存旧值
        st._config["webui"]["port"] = 9090
        with patch.object(fs.Settings, "_main_reachable", return_value=True):
            st.save()
        after = json.load(open(os.path.join(tmpdir, "config.json"), encoding="utf-8"))
        self.assertEqual(after["danmaku_listener"]["rooms"][0]["room_id"], "9")
        self.assertEqual(after["danmaku_listener"]["keywords"], ["窄端点写入词"])
        self.assertEqual(after["webui"]["port"], 9090)

    def test_main_unreachable_dumps_memory(self):
        """main.py 不可达：webui 唯一写者——set_config 新值正常落盘（配置期直写语义）。"""
        import json
        import tempfile
        from unittest.mock import patch
        from frontend.config import settings as fs
        tmpdir = tempfile.mkdtemp()
        st = self._make(tmpdir, {"rooms": [], "keywords": []})  # 磁盘旧值（空）
        st._config["danmaku_listener"] = {
            "rooms": [{"platform": "bilibili", "room_id": "77", "enabled": True, "active": True}],
            "keywords": ["新词"]}
        with patch.object(fs.Settings, "_main_reachable", return_value=False):
            st.save()
        after = json.load(open(os.path.join(tmpdir, "config.json"), encoding="utf-8"))
        self.assertEqual(after["danmaku_listener"]["rooms"][0]["room_id"], "77")  # 新值落盘
        self.assertEqual(after["danmaku_listener"]["keywords"], ["新词"])


class TestQrSafety(unittest.TestCase):
    def test_raw_b64_wrapped_as_png(self):
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
        for kind in ("route_failed", "backpressure", "gap", "recovered",
                     "contract_mismatch", "component_recovered"):
            self.assertIn(kind, n, f"缺少 {kind} 的通知映射")
            self.assertIsNotNone(n[kind], f"{kind} 不应静默")
        for kind in ("count", "room", "service_state"):
            self.assertIsNone(n.get(kind))


class TestR39SelfEchoDedup(unittest.TestCase):
    """R39 自联回环去重（v0.7.0 合并 ENG-M4/M5）：sent_at+content 登记、≤10s 窗口收紧、LRU 有界、异常降级。"""

    import time as _time

    def _svc(self):
        svc = dc.DanmakuListenerService.__new__(dc.DanmakuListenerService)
        svc._queue = dc._BoundedDropQueue(100)
        svc._evt_lock = __import__("threading").Lock()
        from collections import deque
        svc.events = deque(maxlen=200)
        svc._event_seq = 0
        svc._seen_event_keys = OrderedDict()
        svc.login_queue = []
        svc.login_active = None
        svc._unknown_type_count = 0
        svc._invalid_msg_count = 0
        svc._msg_today_count = 0
        svc._last_msg_ts = 0.0
        svc._last_heartbeat = 0.0
        svc._no_active_warned = False
        svc._self_send = OrderedDict()
        svc._self_echo_dropped = 0
        svc._cfg = lambda: {"rooms": [{"platform": "bilibili", "room_id": "23058",
                                       "enabled": True, "active": True}]}
        return svc

    @staticmethod
    def _result_wire(sent_at, content, status="sent"):
        """DANMU_SEND_RESULT 尽力推送 wire（平铺形态，_normalize_wire 可解析）。"""
        return {"contract_version": "1.0.0", "category": "command", "type": "DANMU_SEND_RESULT",
                "platform": "bilibili", "room_id": "23058", "seq": 1, "timestamp": sent_at,
                "engine": "send:pipeline",
                "payload": {"type": "DANMU_SEND_RESULT", "request_id": "r1", "status": status,
                            "sent_at": sent_at, "content": content}}

    # 断言 1（ENG-M5）：同键二次派发被吞——回执登记后 ≤10s 内同 content DANMU 不入队
    def test_same_key_second_dispatch_swallowed(self):
        svc = self._svc()
        now = int(self._time.time())
        svc._on_wire(self._result_wire(now, "回环内容"))
        svc._on_wire(_msg(payload={"type": "DANMU", "user_name": "主播", "content": "回环内容"}))
        self.assertEqual(svc._queue.qsize(), 0)
        self.assertEqual(svc._self_echo_dropped, 1)

    # 断言 2（ENG-M5）：不同 sent_at 放行——旧 sent_at 窗口外放行；重登记新 sent_at 生效（同 content 覆盖）
    def test_different_sent_at_passes(self):
        svc = self._svc()
        svc._record_self_send({"sent_at": int(self._time.time()) - 60, "content": "x"})
        self.assertFalse(svc._is_self_echo("x"))     # 旧 sent_at 窗口外 → 放行
        svc._record_self_send({"sent_at": int(self._time.time()), "content": "x"})  # 重发登记
        self.assertTrue(svc._is_self_echo("x"))      # 新 sent_at 生效，不被旧值卡死

    # 断言 3（ENG-M5）：窗口外放行——|now-sent_at| > 10s 不判回环，弹幕正常入队
    def test_outside_window_passes(self):
        svc = self._svc()
        stale = int(self._time.time()) - (dc.SELF_SEND_WINDOW_S + 2)
        svc._on_wire(self._result_wire(stale, "y"))
        svc._on_wire(_msg(payload={"type": "DANMU", "user_name": "u", "content": "y"}))
        self.assertEqual(svc._queue.qsize(), 1)
        self.assertEqual(svc._self_echo_dropped, 0)

    # 断言 4（ENG-M5）：LRU 容量有界——超 SELF_SEND_LRU_MAX 淘汰最旧键
    def test_lru_capacity_bounded(self):
        svc = self._svc()
        now = int(self._time.time())
        for i in range(dc.SELF_SEND_LRU_MAX + 10):
            svc._record_self_send({"sent_at": now, "content": f"c{i}"})
        self.assertEqual(len(svc._self_send), dc.SELF_SEND_LRU_MAX)
        self.assertNotIn("c0", svc._self_send)       # 最旧被淘汰
        self.assertIn(f"c{dc.SELF_SEND_LRU_MAX + 9}", svc._self_send)

    # 附加：dry_run 回执不登记（仅 status=sent）；去重内部异常降级放行（监听主链不断）
    def test_dry_run_not_recorded_and_degraded(self):
        svc = self._svc()
        svc._on_wire(self._result_wire(int(self._time.time()), "z", status="dry_run"))
        self.assertEqual(len(svc._self_send), 0)
        svc._self_send = None                        # 模拟内部状态异常
        self.assertFalse(svc._is_self_echo("z"))     # 降级放行


class TestSendControlPlane(unittest.TestCase):
    """发送控制面服务层（O2 第一阶段）：鉴权语义同组件（503/401）+ 管线直调线格式转换。"""

    def _svc(self):
        svc = dc.DanmakuListenerService.__new__(dc.DanmakuListenerService)

        class _FakeLoop:
            def is_closed(self):
                return False
        svc._loop = _FakeLoop()                      # 非空+未关即过运行判定（_submit 本测被 stub）
        return svc

    def test_auth_503_when_unconfigured(self):
        import unittest.mock as _mock
        svc = self._svc()
        with _mock.patch("danmaku_listener.push.ws_server.load_token", return_value=None):
            r = svc.send_danmu("bilibili", "123", "hi", "r1", token="x")
        self.assertEqual(r["status_code"], 503)
        self.assertEqual(r["error"], "AUTH_UNCONFIGURED")

    def test_auth_401_on_mismatch(self):
        import unittest.mock as _mock
        svc = self._svc()
        with _mock.patch("danmaku_listener.push.ws_server.load_token", return_value="secret"):
            r = svc.send_danmu("bilibili", "123", "hi", "r1", token="wrong")
            r2 = svc.send_danmu("bilibili", "123", "hi", "r2", token="")
        self.assertEqual(r["status_code"], 401)
        self.assertEqual(r2["status_code"], 401, msg=str(r2))    # 空 token 同拒绝

    def test_send_dry_run_wire_lowercase(self):
        """管线直调：to_wire payload 为 dict+str 枚举，经 json 序列化后 status 为小写线格式。"""
        import unittest.mock as _mock
        from danmaku_listener.contract.models import SendStatus
        svc = self._svc()
        # 模拟真实 to_wire 形态：payload dict、status 为 str 枚举对象（json.dumps 输出小写值）
        wire = {"payload": {"type": "DANMU_SEND_RESULT", "request_id": "r1",
                            "status": SendStatus.DRY_RUN, "content": "hi"}}
        stub = type("P", (), {"handle_request": staticmethod(
            lambda **kw: _async_passthrough(wire))})
        svc._submit = lambda coro, timeout=60: wire  # 绕过线程亲和（本测不建环）
        with _mock.patch("danmaku_listener.push.ws_server.load_token", return_value="secret"), \
             _mock.patch("danmaku_listener.senders.wiring.get_send_pipeline", return_value=stub()):
            r = svc.send_danmu("bilibili", "123", "hi", "r1", token="secret")
        self.assertEqual(r["status_code"], 200)
        self.assertEqual(r["status"], "dry_run")     # 小写线格式（勿 str(枚举)）
        self.assertEqual(r["request_id"], "r1")

    def test_results_auth_gated(self):
        import unittest.mock as _mock
        svc = self._svc()
        with _mock.patch("danmaku_listener.push.ws_server.load_token", return_value="secret"):
            r = svc.send_results("r1", token="nope")
        self.assertEqual(r["status_code"], 401)      # 对账同鉴权（不触 audit）


def _async_passthrough(value):
    """stub 管线的 handle_request：返回 awaitable 占位（_submit 已被 stub，不真 await）。"""

    async def _coro():
        return value
    return _coro()


class TestSetServiceEnabled(unittest.TestCase):
    """服务总开关重建语义（2026-10-12 开关语义修复回归锁定）。

    settings 覆写（发送白名单/dry_run 等）只发生在 _async_start（循环线程重建
    时）——活循环 off→on 必须强制 stop+start 全量重建，否则监听不恢复、运行期
    发送配置改了也不生效；loop 从未启动/已关闭走 _resume_service 原路径。
    """

    def _make_svc(self, loop_alive: bool):
        import unittest.mock as _mock
        svc = dc.DanmakuListenerService.__new__(dc.DanmakuListenerService)
        if loop_alive:
            svc._loop = _mock.MagicMock()
            svc._loop.is_closed.return_value = False
        else:
            svc._loop = None
        svc._service_on = True
        svc._bridge = None
        return svc

    @_mock.patch("utils.danmaku_consumer._write_config_section", return_value=True)
    def test_live_loop_rebuilds_via_stop_start(self, _w):
        import unittest.mock as _mock
        svc = self._make_svc(loop_alive=True)
        with _mock.patch.object(svc, "stop") as m_stop, \
                _mock.patch.object(svc, "start", return_value=True) as m_start, \
                _mock.patch.object(svc, "_resume_service") as m_resume:
            r = svc.set_service_enabled(True)
        m_stop.assert_called_once()
        m_start.assert_called_once()
        m_resume.assert_not_called()
        self.assertTrue(r["success"])

    @_mock.patch("utils.danmaku_consumer._write_config_section", return_value=True)
    def test_dead_loop_keeps_resume_path(self, _w):
        svc = self._make_svc(loop_alive=False)
        with _mock.patch.object(svc, "stop") as m_stop, \
                _mock.patch.object(svc, "start") as m_start, \
                _mock.patch.object(svc, "_resume_service", return_value=True) as m_resume:
            r = svc.set_service_enabled(True)
        m_stop.assert_not_called()
        m_start.assert_not_called()
        m_resume.assert_called_once()
        self.assertTrue(r["success"])

    @_mock.patch("utils.danmaku_consumer._write_config_section", return_value=True)
    def test_disable_writes_config_and_marks_off(self, _w):
        svc = self._make_svc(loop_alive=True)
        with _mock.patch.object(svc, "push_event") as m_push:
            r = svc.set_service_enabled(False)
        self.assertFalse(r["enabled"])
        self.assertFalse(svc._service_on)
        m_push.assert_called_once()     # service_state 事件照推（前端 pill 联动）
        _w.assert_called_once()         # 意图落盘（重启语义一致）


if __name__ == "__main__":
    unittest.main(verbosity=2)
