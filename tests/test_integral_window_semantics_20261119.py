# -*- coding: UTF-8 -*-
"""
积分窗口语义守卫测试（2026-11-19 积分窗口语义批次，用户裁决）

目标语义（替代原 24h 滚动限频）：
- 签到/入场=固定时间窗口（自然日）：last_sign_ts/last_entrance_ts 与当前时刻同属
  一个自然日（本地时区 date() 相同）即拦截；跨日（次日 0 点起）重新可计。
  边界：23:59:59 计分后 00:00:01（次日）再计=放行——滚动窗口 24h 形态下该场景
  仍会拦截，本批收敛为自然日一次
- 关注=一生一次：last_follow_ts 非 NULL 即拦截（哪怕模拟跨 30 天）——取关重关
  永不重得；NULL=从未计分/迁移前存量（fail-open 放行一次）
- row 值解析方式：库值可能为 datetime 或 sqlite 存储的 ISO 字符串，统一经
  parse_db_ts 解析（与原 cooldown_active 对 row 值的容错口径一致）；NULL/解析
  失败 fail-open 放行，脏数据不封死计分链
- 同日窗口拦截为隐形语义：不计分、不写库、不回复（ctx.blocked，消费点终结）

纪律：pytest tmp_path 独立测试 DB 文件（同 test_integral_review_1031.py）。
"""

import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.common import Common
from utils.db import SQLiteDB
import utils.handlers.feature_handler as feature_handler_module
from utils.handlers.feature_handler import (
    FeatureHandler,
    same_day_active,
)

# 与 utils/my_handle.py 建表 DDL 一致（新装形态，含 last_entrance_ts + last_follow_ts）
FRESH_INTEGRAL_DDL = '''
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
    last_entrance_ts DATETIME,
    last_follow_ts DATETIME,
    UNIQUE(platform, username, uid)
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
            "crud": {"query": {"enable": True, "cmd": ["查询积分"],
                               "copywriting": ["{username}，您当前的积分是{integral}"]}},
        },
        "audio_synthesis_type": "edge-tts",
        "edge-tts": {},
        "filter": {},
    }


@pytest.fixture
def db(tmp_path):
    database = SQLiteDB(str(tmp_path / "integral_window_semantics.db"))
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


def set_ts(db, column, dt):
    """把指定时间列写为固定 datetime（ISO 字符串形态，模拟 sqlite 存储格式）。"""
    assert db.execute(f"UPDATE integral SET {column} = ?", (dt.strftime("%Y-%m-%d %H:%M:%S.%f"),))


# ============ 固定自然日窗口：边界（same_day_active 纯函数） ============

class TestNaturalDayWindowBoundaries:
    def test_day_boundary_235959_then_000001_passes(self):
        """自然日边界：23:59:59 计分后次日 00:00:01 再计=放行（原 24h 滚动窗口
        会拦截该场景，本批收敛为自然日一次）。"""
        scored_at = datetime(2026, 11, 19, 23, 59, 59)
        next_day = datetime(2026, 11, 20, 0, 0, 1)
        assert same_day_active(scored_at, scored_at) is True, "同日再计=拦截"
        assert same_day_active(scored_at, next_day) is False, "次日 00:00:01 再计=放行"
        # 字符串形态（sqlite 存储格式）等价
        assert same_day_active("2026-11-19 23:59:59", next_day) is False

    def test_same_day_any_time_blocks(self):
        """同自然日任意时刻均拦截：00:00:01 计分后 23:59:59 再计=拦截（当日一次）。"""
        assert same_day_active(datetime(2026, 11, 19, 0, 0, 1),
                               datetime(2026, 11, 19, 23, 59, 59)) is True
        assert same_day_active(datetime(2026, 11, 19, 12, 0, 0),
                               datetime(2026, 11, 19, 12, 0, 1)) is True

    def test_cross_day_passes(self):
        """跨日放行：last ts 减 1 天（前一自然日）→ 放行（不再需要减 25h）。"""
        now = datetime(2026, 11, 19, 10, 0, 0)
        assert same_day_active(now - timedelta(days=1), now) is False
        assert same_day_active(now - timedelta(days=30), now) is False

    def test_null_or_dirty_fail_open(self):
        """NULL/解析失败=从未计分（fail-open 放行），脏数据不封死计分链。"""
        now = datetime(2026, 11, 19, 10, 0, 0)
        assert same_day_active(None, now) is False
        assert same_day_active("", now) is False
        assert same_day_active("垃圾数据", now) is False


# ============ 签到：同日拦截 / 跨日放行（全链路） ============

class TestSignNaturalDayWindow:
    def test_same_day_second_sign_blocked(self, handler, db):
        """同自然日第二次签到：静默拦截（不计分/不写库/不回复，消费弹幕）。"""
        data = {"platform": "dy", "username": "小明", "content": "签到"}
        assert handler.integral_handle("comment", data) is True
        assert db.fetch_one("SELECT integral, sign_num FROM integral") == (1, 1)

        blocked = {"platform": "dy", "username": "小明", "content": "签到"}
        assert handler.integral_handle("comment", blocked) is True, "窗口拦截=消费该弹幕"
        row = db.fetch_one("SELECT integral, sign_num FROM integral")
        assert row == (1, 1), "同日重复签到不得计分/自增"
        assert handler.my_handle.replies == [], "自然日窗口拦截为隐形语义，不回复"

    def test_cross_day_sign_resumes(self, handler, db):
        """跨日放行：last_sign_ts 回拨 1 天（前一自然日）→ 次日签到重新计分。"""
        assert handler.integral_handle("comment", {"platform": "dy", "username": "小明",
                                                   "content": "签到"}) is True
        set_ts(db, "last_sign_ts", datetime.now() - timedelta(days=1))
        assert handler.integral_handle("comment", {"platform": "dy", "username": "小明",
                                                   "content": "签到"}) is True
        assert db.fetch_one("SELECT integral, sign_num FROM integral") == (2, 2)

    def test_start_of_day_sign_blocks_rest_of_day(self, handler, db):
        """当日 00:00:01 计分后，同日其余时刻（含 23:59:59）均拦截。"""
        assert handler.integral_handle("comment", {"platform": "dy", "username": "小明",
                                                   "content": "签到"}) is True
        now = datetime.now()
        set_ts(db, "last_sign_ts", now.replace(hour=0, minute=0, second=1, microsecond=0))
        assert handler.integral_handle("comment", {"platform": "dy", "username": "小明",
                                                   "content": "签到"}) is True
        assert db.fetch_one("SELECT integral, sign_num FROM integral") == (1, 1), \
            "当日 00:00:01 计分后全日拦截"


# ============ 入场：同日拦截 / 跨日放行（全链路） ============

class TestEntranceNaturalDayWindow:
    def test_same_day_second_entrance_blocked(self, handler, db):
        """同自然日第二次入场：静默拦截（view_num=计分次数口径不动）。"""
        assert handler.integral_handle("entrance", {"platform": "dy", "username": "小明"}) is True
        assert handler.integral_handle("entrance", {"platform": "dy", "username": "小明"}) is True
        assert db.fetch_one("SELECT integral, view_num FROM integral") == (1, 1), \
            "同日重复入场不得计分（view_num=总获得积分的进场次数）"

    def test_cross_day_entrance_resumes(self, handler, db):
        """跨日放行：last_entrance_ts 回拨 1 天（前一自然日）→ 次日入场重新计分。"""
        assert handler.integral_handle("entrance", {"platform": "dy", "username": "小明"}) is True
        set_ts(db, "last_entrance_ts", datetime.now() - timedelta(days=1))
        assert handler.integral_handle("entrance", {"platform": "dy", "username": "小明"}) is True
        assert db.fetch_one("SELECT integral, view_num FROM integral") == (2, 2)


# ============ 关注：一生一次（用户裁决） ============

class TestFollowOnceForLife:
    def test_follow_scores_once_then_blocked_even_after_30d(self, handler, db):
        """已计分（last_follow_ts 非 NULL）后哪怕模拟跨 30 天，再次关注仍拦截
        （取关重关永不重得；原 24h 滚动窗口超窗会恢复计分，本批收敛）。"""
        assert handler.integral_handle("follow", {"platform": "dy", "username": "小明"}) is True
        assert db.fetch_one("SELECT integral FROM integral") == (1,)

        set_ts(db, "last_follow_ts", datetime.now() - timedelta(days=30))
        assert handler.integral_handle("follow", {"platform": "dy", "username": "小明"}) is True, \
            "一生一次拦截仍消费事件（静默）"
        assert db.fetch_one("SELECT integral FROM integral") == (1,), \
            "跨 30 天重新关注仍不得计分"
        assert handler.my_handle.replies == [], "一生一次拦截为隐形语义，不回复"

    def test_follow_null_ts_fail_open_scores_once(self, handler, db):
        """NULL=从未计分/迁移前存量（fail-open 放行一次）：预置 last_follow_ts 为
        NULL 的存量行，关注计分一次后 ts 非 NULL，再关注即终身拦截。"""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")
        assert db.execute(
            "INSERT INTO integral (platform, username, uid, integral, view_num, sign_num, "
            "last_sign_ts, total_price, last_ts, last_entrance_ts, last_follow_ts) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("dy", "老关注", "", 5, 0, 0, now, 0, now, None, None))
        assert handler.integral_handle("follow", {"platform": "dy", "username": "老关注"}) is True
        assert db.fetch_one("SELECT integral FROM integral") == (6,), "NULL fail-open 放行计分一次"

        assert handler.integral_handle("follow", {"platform": "dy", "username": "老关注"}) is True
        assert db.fetch_one("SELECT integral FROM integral") == (6,), "计分一次后终身拦截"


# ============ 写库形态容错：datetime 与 ISO 字符串 row 值等价 ============

class TestRowValueParsing:
    def test_iso_string_row_value_same_semantics(self, handler, db):
        """row 值为 sqlite 存储的 ISO 字符串时窗口语义不变（与 datetime 形态等价）：
        同日字符串拦截、跨日字符串放行。"""
        assert handler.integral_handle("comment", {"platform": "dy", "username": "小明",
                                                   "content": "签到"}) is True
        # 库值经 fetch 后已是字符串形态（sqlite 存储格式）——直接再签=拦截
        assert handler.integral_handle("comment", {"platform": "dy", "username": "小明",
                                                   "content": "签到"}) is True
        assert db.fetch_one("SELECT integral, sign_num FROM integral") == (1, 1)

        # 回拨为前一日字符串 → 放行（parse_db_ts 解析字符串形态）
        old = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S.%f")
        assert db.execute("UPDATE integral SET last_sign_ts = ?", (old,))
        assert handler.integral_handle("comment", {"platform": "dy", "username": "小明",
                                                   "content": "签到"}) is True
        assert db.fetch_one("SELECT integral, sign_num FROM integral") == (2, 2)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
