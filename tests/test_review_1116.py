# -*- coding: UTF-8 -*-
"""
2026-11-16 架构审查批次⑤ 守卫测试：

- ⑤A 未分类消息类型告警（utils/danmaku_consumer.py）：
  三处 fall-through（非 business 类别 / system 未知类型 / business 分派 switch
  兜底）在计数之外新增 logger.warning，同类型 60s 限频防刷屏，不改变分发行为。
- ⑤B entrance 表保留期治理（utils/my_handle.py）：
  database.entrance_retention_days —— >0 保留最近 N 天（启动+每 6h 裁剪）；
  0 关闭；缺省/非法默认 30 天。参数化 DELETE，只记日志不中断。

纪律：pytest tmp_path 独立测试 DB 文件（同 test_integral_review_1031.py）。
"""

import sys
import time
import unittest
from collections import OrderedDict, deque
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import utils.danmaku_consumer as dc
from utils.db import SQLiteDB


# ---------- ⑤A 未分类消息类型告警 ----------


@pytest.fixture(autouse=True)
def _reset_warn_ts():
    """每用例清空模块级限频时间戳，避免用例间串扰。"""
    dc._unknown_type_warn_ts.clear()
    yield
    dc._unknown_type_warn_ts.clear()


class TestWarnUnknownType:
    """_warn_unknown_type：同类型 60s 限频一条；异常不外抛。"""

    def test_first_call_warns(self):
        with patch.object(dc.logger, "warning") as mock_warn:
            dc._warn_unknown_type("TOTALLY_NEW_TYPE")
        assert mock_warn.call_count == 1
        assert "TOTALLY_NEW_TYPE" in mock_warn.call_args[0][0]

    def test_second_call_within_interval_silent(self):
        with patch.object(dc.logger, "warning") as mock_warn:
            dc._warn_unknown_type("FOO")
            dc._warn_unknown_type("FOO")
            dc._warn_unknown_type("FOO")
        assert mock_warn.call_count == 1

    def test_after_interval_warns_again(self):
        with patch.object(dc.logger, "warning") as mock_warn:
            dc._warn_unknown_type("BAR")
            # 模拟限频窗过期：把上次告警时间回拨到窗口之外
            dc._unknown_type_warn_ts["BAR"] = (
                time.monotonic() - dc._UNKNOWN_TYPE_WARN_INTERVAL_S - 1.0)
            dc._warn_unknown_type("BAR")
        assert mock_warn.call_count == 2

    def test_different_types_independent(self):
        with patch.object(dc.logger, "warning") as mock_warn:
            dc._warn_unknown_type("X1")
            dc._warn_unknown_type("X2")
        assert mock_warn.call_count == 2

    def test_never_raises(self):
        # 破坏内部状态模拟异常——告警助手必须全兜底（不中断消息主链）
        dc._unknown_type_warn_ts.clear()
        with patch.object(dc, "time", None):
            dc._warn_unknown_type("BOOM")   # 不得抛异常


class TestDispatchUnknownTypeFallback:
    """业务分派 switch 兜底：计数 +1 且触发限频告警，不改变分发行为。"""

    def _svc(self):
        svc = dc.DanmakuListenerService.__new__(dc.DanmakuListenerService)
        svc._queue = dc._BoundedDropQueue(100)
        import threading as _th
        svc._evt_lock = _th.Lock()
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
        svc._idle_send_request_ids = OrderedDict()
        svc._self_echo_dropped = 0
        svc._gift_lru = OrderedDict()
        svc._cfg = lambda: {"rooms": []}
        return svc

    def test_on_wire_unknown_category_warns(self):
        svc = self._svc()
        wire = {"contract_version": "1.0.0", "category": "mystery", "type": "WHAT",
                "platform": "bilibili", "room_id": "23058", "seq": 1, "timestamp": 1,
                "engine": "e", "payload": {}}
        with patch.object(dc.logger, "warning") as mock_warn:
            svc._on_wire(wire)
        assert svc._unknown_type_count == 1
        assert mock_warn.call_count == 1
        assert "WHAT" in mock_warn.call_args[0][0]

    def test_dispatch_unknown_business_type_warns(self):
        svc = self._svc()
        msg = {"envelope": {"platform": "bilibili", "room_id": "23058"}, "payload": {}}
        from utils import my_global
        saved = getattr(my_global, "my_handle", None)

        class _Dummy:
            def process_data(self, data, flag):
                pass

        my_global.my_handle = _Dummy()
        try:
            with patch.object(dc.logger, "warning") as mock_warn:
                svc._dispatch("TOTALLY_NEW_BUSINESS_TYPE", msg)
        finally:
            my_global.my_handle = saved
        assert svc._unknown_type_count == 1
        assert mock_warn.call_count == 1
        assert "TOTALLY_NEW_BUSINESS_TYPE" in mock_warn.call_args[0][0]

    def test_dispatch_known_type_no_warn(self):
        """已知类型不触发未知告警（防告警误报）。"""
        svc = self._svc()
        msg = {"envelope": {"platform": "bilibili", "room_id": "23058"}, "payload": {"user_name": "u"}}

        class _Recorder:
            def __init__(self):
                self.calls = []

            def process_data(self, data, flag):
                self.calls.append(flag)

        rec = _Recorder()
        from utils import my_global
        saved = getattr(my_global, "my_handle", None)
        my_global.my_handle = rec
        try:
            with patch.object(dc.logger, "warning") as mock_warn:
                svc._dispatch("ENTER_ROOM", msg)
        finally:
            my_global.my_handle = saved
        assert rec.calls == ["entrance"]
        assert mock_warn.call_count == 0


# ---------- ⑤B entrance 表保留期治理 ----------

ENTRANCE_DDL = '''
CREATE TABLE IF NOT EXISTS entrance (
    username TEXT NOT NULL,
    ts DATETIME NOT NULL
)
'''


class _StubConfig:
    """模拟 Config.get(*keys) 的嵌套取值语义（缺键返回 None）。"""

    def __init__(self, mapping):
        self.mapping = mapping

    def get(self, *keys):
        result = self.mapping
        for key in keys:
            if result is None or not isinstance(result, dict):
                return None
            result = result.get(key)
        return result


def _bare_my_handle(db, config_mapping):
    """绕过单例 __init__ 构造最小 My_handle：仅挂 db 与类级 config。"""
    import utils.my_handle as mh
    inst = mh.My_handle.__new__(mh.My_handle)
    inst.db = db
    saved_cfg = mh.My_handle.config
    mh.My_handle.config = _StubConfig(config_mapping)
    return inst, saved_cfg


class TestEntranceRetention:
    """entrance_retention_cleanup：按保留天数裁剪，参数化 DELETE。"""

    @pytest.fixture
    def db(self, tmp_path):
        database = SQLiteDB(str(tmp_path / "entrance_retention.db"))
        assert database.execute(ENTRANCE_DDL)
        yield database
        database.close()

    def _seed(self, db, rows):
        for username, ts in rows:
            assert db.execute("INSERT INTO entrance (username, ts) VALUES (?, ?)",
                              (username, ts))

    def test_missing_config_defaults_30d(self, db):
        """缺省键 → 默认 30 天生效（存量用户 config 缺新键也治理）。"""
        inst, saved_cfg = _bare_my_handle(db, {"database": {}})
        try:
            assert inst._entrance_retention_days() == 30
        finally:
            import utils.my_handle as mh
            mh.My_handle.config = saved_cfg

    def test_invalid_config_defaults_30d(self, db):
        inst, saved_cfg = _bare_my_handle(db, {"database": {"entrance_retention_days": "abc"}})
        try:
            assert inst._entrance_retention_days() == 30
        finally:
            import utils.my_handle as mh
            mh.My_handle.config = saved_cfg

    def test_cleanup_deletes_old_keeps_recent(self, db):
        """早于保留期的记录删除、保留期内保留（默认 30 天）。"""
        inst, saved_cfg = _bare_my_handle(db, {"database": {}})
        try:
            now = datetime.now()
            self._seed(db, [
                ("旧用户", now - timedelta(days=40)),
                ("新用户A", now - timedelta(days=1)),
                ("新用户B", now),
            ])
            inst.entrance_retention_cleanup()
            rows = db.fetch_all("SELECT username FROM entrance ORDER BY username")
            assert [r[0] for r in rows] == ["新用户A", "新用户B"]
        finally:
            import utils.my_handle as mh
            mh.My_handle.config = saved_cfg

    def test_custom_retention_days(self, db):
        """entrance_retention_days=7 —— 只保留最近 7 天。"""
        inst, saved_cfg = _bare_my_handle(db, {"database": {"entrance_retention_days": 7}})
        try:
            now = datetime.now()
            self._seed(db, [
                ("超期", now - timedelta(days=8)),
                ("在期", now - timedelta(days=6)),
            ])
            inst.entrance_retention_cleanup()
            rows = db.fetch_all("SELECT username FROM entrance")
            assert [r[0] for r in rows] == ["在期"]
        finally:
            import utils.my_handle as mh
            mh.My_handle.config = saved_cfg

    def test_zero_disables_cleanup(self, db):
        """entrance_retention_days=0 —— 关闭清理，全量历史保留。"""
        inst, saved_cfg = _bare_my_handle(db, {"database": {"entrance_retention_days": 0}})
        try:
            now = datetime.now()
            self._seed(db, [
                ("很旧", now - timedelta(days=3650)),
                ("新", now),
            ])
            inst.entrance_retention_cleanup()
            rows = db.fetch_all("SELECT username FROM entrance")
            assert {r[0] for r in rows} == {"新", "很旧"}
        finally:
            import utils.my_handle as mh
            mh.My_handle.config = saved_cfg

    def test_start_disabled_spawns_nothing(self, db):
        """days=0 时 _entrance_retention_start 直接返回（不裁剪、不建 Timer）。"""
        inst, saved_cfg = _bare_my_handle(db, {"database": {"entrance_retention_days": 0}})
        try:
            self._seed(db, [("旧", datetime.now() - timedelta(days=100))])
            inst._entrance_retention_start()
            rows = db.fetch_all("SELECT username FROM entrance")
            assert len(rows) == 1
        finally:
            import utils.my_handle as mh
            mh.My_handle.config = saved_cfg

    def test_db_none_safe(self, db):
        """db 未就绪（None）时清理静默返回不抛异常。"""
        inst, saved_cfg = _bare_my_handle(None, {"database": {}})
        try:
            inst.entrance_retention_cleanup()   # 不得抛异常
        finally:
            import utils.my_handle as mh
            mh.My_handle.config = saved_cfg


if __name__ == "__main__":
    unittest.main()
