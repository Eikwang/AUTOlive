# -*- coding: UTF-8 -*-
"""
积分功能审查守卫测试（2026-10-31 积分审查批次）

覆盖义务（任务书：审查数据接口/功能机制/数据库结构/整体流程）：
- 12h 隐形限频（本批新增机制）：签到/入场窗口内静默拦截（不计分/不写库/不回复），
  超窗自动恢复；礼物/关注/查询不受限频
- 限频门卫语义：sign_num>0 / view_num>0——排除非本人行为 fresh INSERT 写入的
  时间占位值（礼物先行用户不被假封锁首次签到）
- 迁移：ensure_integral_entrance_ts_column 幂等（存量 9 列库补列、新装 no-op）
- UPSERT CASE 列语义：last_entrance_ts 仅入场覆盖，与 last_sign_ts 互不干扰
- str/list 双形态归一（as_str_list）：UI 存量字符串污染容错——命令词精确匹配
  恢复（「签」不再子串误命中）、档位文案不再按字符随机
- parse_db_ts/cooldown_active 容错（fail-open：脏数据不封死计分链）

纪律：pytest tmp_path 独立测试 DB 文件（同 test_integral_upsert.py）。
"""

import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.common import Common
from utils.db import SQLiteDB
from utils.keyword_hub import KeywordHub
import utils.handlers.feature_handler as feature_handler_module
from utils.handlers.feature_handler import (
    FeatureHandler,
    as_str_list,
    cooldown_active,
    parse_db_ts,
)

# 存量库 DDL（无 last_entrance_ts 列，模拟 2026-10-31 之前的库）
LEGACY_INTEGRAL_DDL = '''
CREATE TABLE IF NOT EXISTS integral (
    platform TEXT NOT NULL,
    username TEXT NOT NULL,
    uid TEXT NOT NULL,
    integral INT NOT NULL,
    view_num INT NOT NULL,
    sign_num INT NOT NULL,
    last_sign_ts DATETIME NOT NULL,
    total_price INT NOT NULL,
    last_ts DATETIME NOT NULL,
    UNIQUE(platform, username, uid)
)
'''

# 与 utils/my_handle.py 建表 DDL 一致（新装形态，含 last_entrance_ts）
FRESH_INTEGRAL_DDL = LEGACY_INTEGRAL_DDL.replace(
    "    last_ts DATETIME NOT NULL,\n",
    "    last_ts DATETIME NOT NULL,\n    last_entrance_ts DATETIME,\n")


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


class _FakeMyHandle:
    """最小 my_handle 替身：仅提供 integral 链路依赖的接口。"""

    def __init__(self, config_mapping, db):
        self.config = _StubConfig(config_mapping)
        self.db = db
        self.common = Common()
        self.replies = []

    def audio_synthesis_handle(self, message):
        self.replies.append(message)

    def webui_show_chat_log_callback(self, data_type, data, resp_content):
        self.replies.append({"type": "webui_log", "data_type": data_type, "content": resp_content})


def base_config():
    """镜像 config.json 积分节的最小配置（enable 全开）。"""
    return {
        "integral": {
            "enable": True,
            "sign": {"enable": True, "cmd": ["签到", "打卡"], "get_integral": 1},
            "gift": {"enable": True, "get_integral_proportion": 10.0},
            "entrance": {"enable": True, "get_integral": 1},
            "follow": {"enable": True, "get_integral": 1,
                       "copywriting": ["{username}感谢关注，获得{get_integral}点积分"]},
            "crud": {"query": {"enable": True, "cmd": ["我的积分", "查询积分"],
                               "copywriting": ["{username}，您当前的积分是{integral}"]}},
        },
        "audio_synthesis_type": "edge-tts",
        "edge-tts": {},
        "filter": {},
    }


@pytest.fixture
def db(tmp_path):
    """新装形态测试库（含 last_entrance_ts 列）。"""
    database = SQLiteDB(str(tmp_path / "integral_review.db"))
    assert database.execute(FRESH_INTEGRAL_DDL)
    yield database
    database.close()


@pytest.fixture
def handler(db):
    fake = _FakeMyHandle(base_config(), db)
    return FeatureHandler(fake)


@pytest.fixture(autouse=True)
def fast_retry_sleep(monkeypatch):
    monkeypatch.setattr(feature_handler_module.time, "sleep", lambda *_: None)


def backdate(db, column, hours):
    """把指定时间列回拨 hours 小时（模拟窗口外/超窗状态）。"""
    old = (datetime.now() - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S.%f")
    assert db.execute(f"UPDATE integral SET {column} = ?", (old,))
    return old


# ============ 12h 隐形限频：签到 ============

class TestSignCooldown:
    def test_first_sign_scores_then_window_blocks(self, handler, db):
        """首次签到计分；窗口内重复签到静默拦截（不计分/不写库/不回复）。"""
        assert handler.integral_handle("comment", {"platform": "dy", "username": "小明", "content": "签到"}) is True
        assert handler.my_handle.replies, "首次签到应有积分回复"

        handler.my_handle.replies.clear()
        assert handler.integral_handle("comment", {"platform": "dy", "username": "小明", "content": "签到"}) is True
        row = db.fetch_one("SELECT integral, sign_num FROM integral")
        assert row == (1, 1), "窗口内重复签到不得计分/自增"
        assert handler.my_handle.replies == [], "限频拦截为隐形语义，不回复"

    def test_window_expiry_resumes_scoring(self, handler, db):
        """超窗（回拨 13 小时）后签到恢复计分。"""
        assert handler.integral_handle("comment", {"platform": "dy", "username": "小明", "content": "签到"}) is True
        backdate(db, "last_sign_ts", 13)
        assert handler.integral_handle("comment", {"platform": "dy", "username": "小明", "content": "签到"}) is True
        row = db.fetch_one("SELECT integral, sign_num FROM integral")
        assert row == (2, 2)

    def test_sign_cooldown_gate_excludes_gift_placeholder(self, handler, db):
        """门卫 sign_num>0：礼物先行用户（fresh INSERT 写入 last_sign_ts=now 占位）
        不得被假封锁首次签到。"""
        assert handler.integral_handle("gift", {"platform": "dy", "username": "小明",
                                                "gift_name": "小花", "total_price": 1.0}) is True
        # 礼物 fresh INSERT 后立即签到——必须计分
        assert handler.integral_handle("comment", {"platform": "dy", "username": "小明", "content": "签到"}) is True
        row = db.fetch_one("SELECT integral, sign_num FROM integral")
        assert row == (10 + 1, 1)

    def test_gift_and_follow_not_rate_limited(self, handler, db):
        """礼物/关注不受 12h 限频（任务书仅签到/入场）。"""
        for _ in range(3):
            assert handler.integral_handle("gift", {"platform": "dy", "username": "小明",
                                                    "gift_name": "小花", "total_price": 1.0}) is True
            assert handler.integral_handle("follow", {"platform": "dy", "username": "小明"}) is True
        row = db.fetch_one("SELECT integral, total_price FROM integral")
        assert row == (10 * 3 + 1 * 3, 3)

    def test_query_not_rate_limited(self, handler, db):
        """查询为只读链路，连续查询均回复。"""
        for _ in range(2):
            assert handler.integral_handle("crud", {"platform": "dy", "username": "新人", "content": "查询积分"}) is True
        logs = [r for r in handler.my_handle.replies
                if isinstance(r, dict) and r.get("type") == "webui_log"]
        assert len(logs) == 2

    def test_polluted_cmd_string_no_substring_hit(self, handler):
        """命令词被 UI 污染成整串字符串时：恢复精确匹配（「签」不命中、「签到」命中）。"""
        cfg = base_config()
        cfg["integral"]["sign"]["cmd"] = "签到\n打卡"
        handler.my_handle.config = _StubConfig(cfg)
        assert handler.integral_handle("comment", {"platform": "dy", "username": "小明", "content": "签"}) is False
        assert handler.integral_handle("comment", {"platform": "dy", "username": "小明", "content": "签到"}) is True


# ============ 12h 隐形限频：入场 ============

class TestEntranceCooldown:
    def test_first_entrance_scores_then_window_blocks(self, handler, db):
        """首次入场计分（view_num+1）；窗口内重复入场静默拦截。"""
        assert handler.integral_handle("entrance", {"platform": "dy", "username": "小明"}) is True
        assert handler.my_handle.replies, "首次入场应有积分回复"

        handler.my_handle.replies.clear()
        assert handler.integral_handle("entrance", {"platform": "dy", "username": "小明"}) is True
        row = db.fetch_one("SELECT integral, view_num FROM integral")
        assert row == (1, 1), "窗口内重复入场不得计分（view_num=总获得积分的进场次数）"
        assert handler.my_handle.replies == []

    def test_window_expiry_resumes_scoring(self, handler, db):
        """超窗（回拨 13 小时）后入场恢复计分。"""
        assert handler.integral_handle("entrance", {"platform": "dy", "username": "小明"}) is True
        backdate(db, "last_entrance_ts", 13)
        assert handler.integral_handle("entrance", {"platform": "dy", "username": "小明"}) is True
        row = db.fetch_one("SELECT integral, view_num FROM integral")
        assert row == (2, 2)

    def test_legacy_row_null_entrance_ts_fails_open(self, handler, db):
        """迁移前存量行（view_num>0 且 last_entrance_ts 为 NULL）：fail-open 放行。"""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")
        assert db.execute(
            "INSERT INTO integral (platform, username, uid, integral, view_num, sign_num, "
            "last_sign_ts, total_price, last_ts, last_entrance_ts) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("dy", "老用户", "", 5, 3, 0, now, 0, now, None))
        assert handler.integral_handle("entrance", {"platform": "dy", "username": "老用户"}) is True
        row = db.fetch_one("SELECT integral, view_num FROM integral")
        assert row == (6, 4)

    def test_sign_does_not_clobber_entrance_ts(self, handler, db):
        """UPSERT CASE：签到不覆盖 last_entrance_ts，入场不覆盖 last_sign_ts。"""
        assert handler.integral_handle("entrance", {"platform": "dy", "username": "小明"}) is True
        ts_after_entrance = db.fetch_one("SELECT last_entrance_ts, last_sign_ts FROM integral")

        assert handler.integral_handle("comment", {"platform": "dy", "username": "小明", "content": "签到"}) is True
        ts_after_sign = db.fetch_one("SELECT last_entrance_ts, last_sign_ts FROM integral")
        assert ts_after_sign[0] == ts_after_entrance[0], "签到不得覆盖 last_entrance_ts"
        assert ts_after_sign[1] != ts_after_entrance[1], "签到应覆盖 last_sign_ts"

        # 窗口内入场被拦截（last_sign_ts 刚更新不影响入场限频判定）
        assert handler.integral_handle("entrance", {"platform": "dy", "username": "小明"}) is True
        row = db.fetch_one("SELECT integral, view_num, sign_num FROM integral")
        assert row == (2, 1, 1)


# ============ 迁移：ensure_integral_entrance_ts_column ============

class TestEntranceTsMigration:
    def test_legacy_db_migration_and_idempotent(self, tmp_path):
        """存量 9 列库补列幂等；补列后入场限频全链可用。"""
        database = SQLiteDB(str(tmp_path / "legacy.db"))
        try:
            assert database.execute(LEGACY_INTEGRAL_DDL)
            from utils.db import ensure_integral_entrance_ts_column
            assert ensure_integral_entrance_ts_column(database) is True
            assert ensure_integral_entrance_ts_column(database) is True  # 幂等
            cols = [row[1] for row in database.fetch_all("PRAGMA table_info(integral)")]
            assert "last_entrance_ts" in cols
        finally:
            database.close()

    def test_fresh_ddl_migration_noop(self, db):
        """新装库（DDL 已含列）：迁移 no-op 返回 True。"""
        from utils.db import ensure_integral_entrance_ts_column
        assert ensure_integral_entrance_ts_column(db) is True


# ============ 工具函数：parse_db_ts / cooldown_active / as_str_list ============

class TestHelpers:
    def test_parse_db_ts(self):
        assert parse_db_ts(None) is None
        assert parse_db_ts("") is None
        assert parse_db_ts("不是时间") is None
        dt = datetime(2026, 10, 31, 12, 0, 0)
        assert parse_db_ts(dt) is dt
        assert parse_db_ts("2026-10-31 12:00:00") == dt
        assert parse_db_ts("2026-10-31 12:00:00.123456") == dt.replace(microsecond=123456)

    def test_cooldown_active_boundaries(self):
        now = datetime(2026, 10, 31, 12, 0, 0)
        assert cooldown_active(None, now) is False                     # 从未计分
        assert cooldown_active("垃圾数据", now) is False               # fail-open
        assert cooldown_active(now - timedelta(hours=11, minutes=59), now) is True
        assert cooldown_active(now - timedelta(hours=12), now) is False   # 恰好 12h=超窗
        assert cooldown_active(now - timedelta(hours=12, seconds=1), now) is False

    def test_as_str_list(self):
        assert as_str_list("签到\n打卡") == ["签到", "打卡"]
        assert as_str_list("  签到  \n\n打卡\n") == ["签到", "打卡"]
        assert as_str_list(["a", "b"]) == ["a", "b"]
        assert as_str_list("单行") == ["单行"]
        assert as_str_list("") is None
        assert as_str_list("\n\n") is None
        assert as_str_list(None) is None
        assert as_str_list(123) is None

    def test_polluted_tier_copy_replies_whole_line(self, handler):
        """档位文案被污染成整串字符串：回复整行文案而非单字符。"""
        cfg = base_config()
        cfg["integral"]["sign"]["copywriting"] = [
            {"sign_num_interval": "0-999999", "copywriting": "打卡成功{username}"}]
        handler.my_handle.config = _StubConfig(cfg)
        assert handler.integral_handle("comment", {"platform": "dy", "username": "小明", "content": "签到"}) is True
        contents = [r.get("content") for r in handler.my_handle.replies if isinstance(r, dict)]
        assert "打卡成功小明" in contents


# ============ 中枢命令词匹配（keyword_hub._match_integral） ============

class TestKeywordHubIntegralMatch:
    def _hub(self, sign_cmd, query_cmd):
        cfg = {"integral": {
            "enable": True,
            "sign": {"enable": True, "cmd": sign_cmd},
            "crud": {"query": {"enable": True, "cmd": query_cmd}},
        }}
        return KeywordHub(_FakeMyHandle(cfg, None))

    def test_polluted_string_cmds_restored_to_exact_match(self):
        """污染字符串命令词在中枢侧同样恢复精确匹配（「签」不命中）。"""
        hub = self._hub("签到\n打卡", "查询积分\n我的积分")
        sign, query = hub._match_integral("签")
        assert (sign, query) == (False, False)
        sign, query = hub._match_integral("签到")
        assert sign is True
        sign, query = hub._match_integral("查询积分")
        assert query is True

    def test_list_cmds_exact_match(self):
        hub = self._hub(["签到"], ["我的积分"])
        assert hub._match_integral("签到啦") == (False, False)
        assert hub._match_integral("签到") == (True, False)
        assert hub._match_integral("我的积分") == (False, True)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
