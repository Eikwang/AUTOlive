# -*- coding: UTF-8 -*-
"""
C1 积分链路补全单测（/autoplan 批准切片 2）

覆盖义务：
- A1 表迁移：UNIQUE(platform, username, uid) 幂等创建 + 存量重复行折叠
- UPSERT 逐列语义：同用户重复行为不产生重复行、integral/view_num/total_price 累加、
  sign_num 条件自增、last_sign_ts 仅签到覆盖、last_ts 覆盖
- 重复签到并发（sqlite 同进程两连接顺序写）：不产生重复行
- CAP6 失败语义：写库失败不抛出到主流程（integral_handle 返回 False）
- 取整规则：比例计分 int() 向下取整（D-20 矩阵登记项）
- D-17 档位：档位切换积分取值 + config 缺键内置默认 2 档
- D-12：积分查询无记录回复「暂无积分」
- E3：WAL 日志模式（连接初始化处 PRAGMA）

纪律：使用 pytest tmp_path 独立测试 DB 文件（禁用 data/test_unit.db——路径竞争已修过一次）。
"""

import re
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.common import Common
from utils.db import SQLiteDB, ensure_integral_unique_index, INTEGRAL_UNIQUE_INDEX_SQL
import utils.handlers.feature_handler as feature_handler_module
from utils.handlers.feature_handler import (
    FeatureHandler,
    INTEGRAL_UPSERT_SQL,
    INTEGRAL_DB_MAX_ATTEMPTS,
    parse_interval,
    interval_contains,
    match_tier_block,
    resolve_gift_points,
)

# 与 utils/my_handle.py config_load 建表 DDL 保持一致
# 建表 DDL 中的 UNIQUE 行（迁移测试构造"历史无约束库"时移除该行）
# 注意正则形式：测试文件为 CRLF，行尾 \\r\\n 需容忍（字面 \n 匹配不上会留下悬空逗号）
UNIQUE_LINE_RE = re.compile(r",?\s*UNIQUE\(platform, username, uid\)", re.MULTILINE)

INTEGRAL_TABLE_DDL = '''
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
    """最小 my_handle 替身：仅提供 integral_handle 依赖的接口。"""

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
    """镜像 config.json 积分节的最小配置（enable 全开，便于直接触发）。"""
    return {
        "integral": {
            "enable": True,
            "sign": {"enable": True, "cmd": ["签到", "打卡"], "get_integral": 1},
            "gift": {"enable": True, "get_integral_proportion": 10.0},
            "entrance": {"enable": True, "get_integral": 1},
            "crud": {"query": {"enable": True, "cmd": ["我的积分", "查询积分"],
                               "copywriting": ["{username}，您当前的积分是{integral}"]}},
        },
        "audio_synthesis_type": "edge-tts",
        "edge-tts": {},
        "filter": {},
    }


def make_db(db_file):
    """建独立测试库：integral 表 + A1 唯一索引迁移。"""
    db = SQLiteDB(str(db_file))
    assert db.execute(INTEGRAL_TABLE_DDL)
    assert ensure_integral_unique_index(db)
    return db


@pytest.fixture
def integral_db(tmp_path):
    db = make_db(tmp_path / "integral_test.db")
    yield db
    db.close()


@pytest.fixture
def handler(integral_db):
    fake = _FakeMyHandle(base_config(), integral_db)
    return FeatureHandler(fake)


@pytest.fixture(autouse=True)
def fast_retry_sleep(monkeypatch):
    """积分写库重试退避跳过 sleep，保持用例高速。"""
    monkeypatch.setattr(feature_handler_module.time, "sleep", lambda *_: None)


# ============ A1 表迁移 ============

class TestMigration:
    def test_unique_index_idempotent(self, tmp_path):
        """迁移幂等：重复执行两次均成功，索引存在。"""
        db = SQLiteDB(str(tmp_path / "mig.db"))
        try:
            db.execute(INTEGRAL_TABLE_DDL)
            assert ensure_integral_unique_index(db) is True
            assert ensure_integral_unique_index(db) is True
            row = db.fetch_one(
                "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_integral_platform_username_uid'")
            assert row is not None
        finally:
            db.close()

    def test_collapses_existing_duplicates(self, tmp_path):
        """存量重复行折叠：保留每组最后写入一行，索引建成功。"""
        # 模拟历史库：建表 DDL 无 UNIQUE（约束是本轮迁移引入的），脏数据才可能存在
        LEGACY_DDL = UNIQUE_LINE_RE.sub("", INTEGRAL_TABLE_DDL, count=1)
        db = SQLiteDB(str(tmp_path / "dup.db"))
        try:
            db.execute(LEGACY_DDL)
            # 直插三行重复 (platform, username, uid)，模拟历史无 UNIQUE 时期的脏数据
            for points in (1, 5, 9):
                db.execute(
                    "INSERT INTO integral (platform, username, uid, integral, view_num, sign_num, "
                    "last_sign_ts, total_price, last_ts) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    ("dy", "用户A", "", points, 0, 0, "2026-01-01", 0, "2026-01-01"))
            assert ensure_integral_unique_index(db) is True
            row = db.fetch_one("SELECT COUNT(*), MAX(integral) FROM integral")
            assert row[0] == 1          # 折叠为单行
            assert row[1] == 9          # 保留最后写入的一行
        finally:
            db.close()


# ============ UPSERT 语义（经 integral_handle 全链路） ============

class TestUpsertSemantics:
    def test_same_user_two_signs_single_row_accumulated(self, handler):
        """同用户两次签到：不产生重复行 + 积分累加 + sign_num 自增。"""
        for _ in range(2):
            assert handler.integral_handle("comment", {"platform": "dy", "username": "小明", "content": "签到"}) is True

        rows = handler.my_handle.db.fetch_all("SELECT integral, sign_num FROM integral")
        assert len(rows) == 1
        assert rows[0][0] == 2    # integral 累加（默认 1 分/次）
        assert rows[0][1] == 2    # sign_num 自增
        # 签到行为触发回复文案
        assert any(r.get("content") for r in handler.my_handle.replies)

    def test_mixed_behavior_column_semantics(self, handler):
        """签到+礼物+入场混合：integral/total_price/view_num 累加，last_sign_ts 仅签到覆盖。"""
        assert handler.integral_handle("comment", {"platform": "dy", "username": "小明", "content": "签到"}) is True
        row_after_sign = handler.my_handle.db.fetch_one(
            "SELECT integral, view_num, sign_num, last_sign_ts, total_price, last_ts FROM integral")
        assert row_after_sign[0] == 1 and row_after_sign[2] == 1

        assert handler.integral_handle("gift", {"platform": "dy", "username": "小明",
                                                "gift_name": "小花", "gift_num": 1,
                                                "unit_price": 1.0, "total_price": 3.0}) is True
        row_after_gift = handler.my_handle.db.fetch_one(
            "SELECT integral, view_num, sign_num, last_sign_ts, total_price, last_ts FROM integral")
        assert row_after_gift[0] == 1 + 30          # 礼物积分 = int(10.0 × 3.0)
        assert row_after_gift[2] == 1                # sign_num 不变（礼物不自增）
        assert row_after_gift[3] == row_after_sign[3]  # last_sign_ts 保留签到值
        assert abs(row_after_gift[4] - 3.0) < 1e-9   # total_price 累加
        assert row_after_gift[5] >= row_after_sign[5]  # last_ts 覆盖推进

        assert handler.integral_handle("entrance", {"platform": "dy", "username": "小明"}) is True
        row_after_entrance = handler.my_handle.db.fetch_one(
            "SELECT integral, view_num, sign_num, total_price FROM integral")
        assert row_after_entrance[0] == 32
        assert row_after_entrance[1] == 1            # view_num 累加（入场 +1）
        assert row_after_entrance[3] == row_after_gift[4]  # total_price 不变

    def test_follow_scoring_with_default_keys(self, handler, caplog):
        """关注计分（新增规则键）：config 缺键用安全默认 1 分，缺键告警仅一次。"""
        cfg = base_config()
        assert "follow" not in cfg["integral"]
        handler.my_handle.config = _StubConfig(cfg)

        assert handler.integral_handle("follow", {"platform": "dy", "username": "小明"}) is True
        assert handler.integral_handle("follow", {"platform": "dy", "username": "小明"}) is True
        row = handler.my_handle.db.fetch_one("SELECT integral, sign_num, view_num FROM integral")
        assert row[0] == 2 and row[1] == 0 and row[2] == 0

        with caplog.at_level("WARNING", logger="autolive"):
            follow_warns = [r for r in caplog.records if "integral.follow" in r.getMessage()]
        # 每个缺键（enable/get_integral/copywriting）仅告警一次，第二次关注不再新增
        assert len(follow_warns) == 3

    def test_disabled_master_switch_noop(self, handler):
        """积分总开关关闭：不写库、不回复、返回 False。"""
        cfg = base_config()
        cfg["integral"]["enable"] = False
        handler.my_handle.config = _StubConfig(cfg)
        assert handler.integral_handle("comment", {"platform": "dy", "username": "小明", "content": "签到"}) is False
        assert handler.my_handle.db.fetch_one("SELECT COUNT(*) FROM integral")[0] == 0
        assert handler.my_handle.replies == []


# ============ 并发（同进程两连接顺序写） ============

class TestConcurrency:
    def test_two_connections_sequential_writes_no_duplicate(self, tmp_path):
        """sqlite 同进程两连接顺序写 UPSERT：仍单行、积分累加。"""
        db_file = str(tmp_path / "conc.db")
        setup = sqlite3.connect(db_file)
        setup.execute(INTEGRAL_TABLE_DDL)
        setup.commit()
        setup.close()
        # 独立连接各带 busy_timeout，模拟连接池多连接
        conn1 = sqlite3.connect(db_file, timeout=5)
        conn2 = sqlite3.connect(db_file, timeout=5)
        try:
            args1 = ("dy", "小明", "", 1, 0, 1, "2026-01-01 00:00:00", 0, "2026-01-01 00:00:00")
            args2 = ("dy", "小明", "", 1, 0, 1, "2026-01-01 00:00:01", 0, "2026-01-01 00:00:01")
            conn1.execute(INTEGRAL_UPSERT_SQL, args1)
            conn1.commit()
            conn2.execute(INTEGRAL_UPSERT_SQL, args2)
            conn2.commit()
            row = conn2.execute("SELECT integral, sign_num, COUNT(*) FROM integral GROUP BY platform, username, uid").fetchone()
            assert row == (2, 2, 1)
        finally:
            conn1.close()
            conn2.close()


# ============ CAP6 失败语义 ============

class _FailingDB:
    """写库恒失败替身：execute 恒 False（或抛异常），fetch_one 恒 None。"""

    def __init__(self, raise_error=False):
        self.raise_error = raise_error
        self.calls = 0

    def execute(self, sql, args=None):
        self.calls += 1
        if self.raise_error:
            raise RuntimeError("db exploded")
        return False

    def fetch_one(self, sql, args=None):
        return None


class TestFailureSemantics:
    @pytest.mark.parametrize("raise_error", [False, True])
    def test_write_failure_returns_false_no_exception(self, raise_error):
        """写库失败（False/异常）不抛出到主流程：integral_handle 返回 False。"""
        fake = _FakeMyHandle(base_config(), _FailingDB(raise_error=raise_error))
        handler = FeatureHandler(fake)
        assert handler.integral_handle("comment", {"platform": "dy", "username": "小明", "content": "签到"}) is False
        assert handler.integral_handle("gift", {"platform": "dy", "username": "小明", "total_price": 3.0}) is False
        assert handler.integral_handle("entrance", {"platform": "dy", "username": "小明"}) is False
        assert handler.integral_handle("follow", {"platform": "dy", "username": "小明"}) is False
        # 重试次数 = INTEGRAL_DB_MAX_ATTEMPTS × 4 行为（comment/gift/entrance/follow 各自重试）
        assert fake.db.calls == INTEGRAL_DB_MAX_ATTEMPTS * 4
        # 写库失败无回复（先写库后回复定序）
        assert fake.replies == []

    def test_query_unreachable_db_replies_error_copy(self):
        """库不可达：查询回复通用失败文案（不静默），不中断主流程。"""
        fake = _FakeMyHandle(base_config(), _FailingDB())
        handler = FeatureHandler(fake)
        assert handler.integral_handle("crud", {"platform": "dy", "username": "小明", "content": "我的积分"}) is True
        contents = [r.get("content") for r in fake.replies if isinstance(r, dict)]
        assert any("查询失败" in c for c in contents)


# ============ 取整规则（D-20 登记项） ============

class TestRounding:
    def test_floor_rounding(self):
        """比例计分 int() 向下取整：10.0 比例 × 0.19 元 = 1.9 → 1 分。"""
        assert resolve_gift_points(None, 10.0, 0.19) == 1
        assert resolve_gift_points(None, 10.0, 1.5) == 15
        assert resolve_gift_points(None, 10.0, 3.0) == 30
        assert resolve_gift_points(None, 0.5, 3.9) == 1  # 1.95 → 1

    def test_tier_override_rounding(self):
        """档位块内比例/绝对分覆盖。"""
        block_proportion = {"get_integral_proportion": 2.0}
        assert resolve_gift_points(block_proportion, 10.0, 3.0) == 6
        block_absolute = {"get_integral": 100}
        assert resolve_gift_points(block_absolute, 10.0, 3.0) == 100

    def test_integral_column_stays_int(self, handler):
        """浮点比例计分后积分列仍为整数。"""
        assert handler.integral_handle("gift", {"platform": "dy", "username": "小明",
                                                "gift_name": "小花", "total_price": 0.19}) is True
        row = handler.my_handle.db.fetch_one("SELECT integral, typeof(integral) FROM integral")
        assert row[0] == 1 and row[1] == "integer"


# ============ 档位表（D-17） ============

class TestTiers:
    def test_tier_switch_points(self, handler):
        """签到档位切换：配置档位块内 get_integral，跨档积分取值变化。"""
        cfg = base_config()
        cfg["integral"]["sign"]["copywriting"] = [
            {"sign_num_interval": "0-3", "get_integral": 1, "copywriting": ["{username}打卡成功，获得{get_integral}点积分"]},
            {"sign_num_interval": "4-30", "get_integral": 5, "copywriting": ["{username}打卡成功喵，获得{get_integral}点积分"]},
        ]
        handler.my_handle.config = _StubConfig(cfg)
        for _ in range(4):
            assert handler.integral_handle("comment", {"platform": "dy", "username": "小明", "content": "签到"}) is True
        row = handler.my_handle.db.fetch_one("SELECT integral, sign_num FROM integral")
        assert row[0] == 1 + 1 + 1 + 5   # 3 次档 1 + 1 次档 2
        assert row[1] == 4

    def test_builtin_default_two_tiers_when_config_missing(self, handler):
        """config 缺 copywriting 键：内置默认 2 档，第 31 次签到档位升到 2 分。"""
        cfg = base_config()
        cfg["integral"]["sign"].pop("copywriting", None)
        handler.my_handle.config = _StubConfig(cfg)
        # 预置 sign_num=30（档 1 上限），下一次签到应落档 2（2 分）
        now = "2026-01-01 00:00:00"
        assert handler.my_handle.db.execute(INTEGRAL_UPSERT_SQL,
                                            ("dy", "小明", "", 30, 0, 30, now, 0, now))
        assert handler.integral_handle("comment", {"platform": "dy", "username": "小明", "content": "签到"}) is True
        row = handler.my_handle.db.fetch_one("SELECT integral, sign_num FROM integral")
        assert row[0] == 30 + 2
        assert row[1] == 31

    def test_interval_helpers(self):
        """区间解析与匹配（闭区间、非法区间不命中）。"""
        assert interval_contains("0-3", 3) and interval_contains("0-3", 0)
        assert not interval_contains("0-3", 3.01)
        assert interval_contains("10.01-88888888", 10.5)
        assert not interval_contains("bad", 1)
        assert parse_interval("5-1") is None  # lo > hi 非法
        assert match_tier_block([{"sign_num_interval": "4-30"}], "sign_num_interval", 2) is None
        assert match_tier_block("not-a-list", "sign_num_interval", 2) is None


# ============ 查询回复链路（D-12） ============

class TestQuery:
    def test_query_no_record_replies_no_integral(self, handler):
        """无记录用户查询：回复「暂无积分」分支文案（不静默）。"""
        assert handler.integral_handle("crud", {"platform": "dy", "username": "新人", "content": "查询积分"}) is True
        contents = [r.get("content") for r in handler.my_handle.replies if isinstance(r, dict)]
        assert any("暂无积分" in c for c in contents)

    def test_query_with_record_replies_integral(self, handler):
        """有记录用户查询：回复当前积分。"""
        assert handler.integral_handle("comment", {"platform": "dy", "username": "小明", "content": "签到"}) is True
        handler.my_handle.replies.clear()
        assert handler.integral_handle("crud", {"platform": "dy", "username": "小明", "content": "我的积分"}) is True
        contents = [r.get("content") for r in handler.my_handle.replies if isinstance(r, dict)]
        assert any("1" in c and "积分" in c for c in contents)

    def test_query_cmd_not_matched_returns_false(self, handler):
        """非查询命令词：不触发查询链路。"""
        assert handler.integral_handle("crud", {"platform": "dy", "username": "小明", "content": "大家好"}) is False

    def test_query_no_record_copy_configurable(self, handler):
        """无记录文案可配置覆盖。"""
        cfg = base_config()
        cfg["integral"]["crud"]["query"]["no_record_copywriting"] = ["{username}还没有积分哦"]
        handler.my_handle.config = _StubConfig(cfg)
        assert handler.integral_handle("crud", {"platform": "dy", "username": "新人", "content": "查询积分"}) is True
        contents = [r.get("content") for r in handler.my_handle.replies if isinstance(r, dict)]
        assert any("还没有积分" in c for c in contents)


# ============ E3：WAL ============

class TestWal:
    def test_journal_mode_is_wal(self, integral_db):
        """连接初始化处 PRAGMA journal_mode=WAL 生效（utils/db.py E3）。"""
        conn = integral_db.connection_pool._get_connection()
        try:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
            assert mode.lower() == "wal"
        finally:
            integral_db.connection_pool._release_connection(conn)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
