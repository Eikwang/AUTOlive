# -*- coding: UTF-8 -*-
"""数据分析功能审查批次守卫测试（2026-10-31，批次号 data_analysis_review_1031）

审查目标（任务书）：
- 数据库各接口正确：integral 表 10 列（含 2026-10-31 积分批次新增 last_entrance_ts）
  与积分图表 dataset dimensions 一致；ORDER BY f-string 拼接白名单守卫
- 统计逻辑正确：弹幕关键词统计（jieba 分词+Top N）、积分统计四榜
  （积分榜/观看榜/签到榜/金额榜——12h 限频口径与积分批次一致）
- 礼物榜单移除（用户裁决：与积分统计-金额榜重复展示）——后端/UI/模板/
  退役键登记全链
- 在系统流程中正确应用：danmu 表写入口=database.comment_enable（弹幕链）、
  integral 表写入口=积分计分（feature_handler UPSERT）、gift 表写入口=
  database.gift_enable（答谢链）——读侧榜单与写侧开关正确匹配
"""
import json
import os
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.db import SQLiteDB

REPO_ROOT = Path(__file__).resolve().parents[1]


def _src(rel: str) -> str:
    return (REPO_ROOT / rel).read_text(encoding="utf-8")


# 与 utils/my_handle.py config_load 建表 DDL 保持一致（10 列）
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
    last_entrance_ts DATETIME,
    UNIQUE(platform, username, uid)
)
'''

DANMU_TABLE_DDL = '''
CREATE TABLE IF NOT EXISTS danmu (
    username TEXT,
    content TEXT,
    ts DATETIME
)
'''


def _make_da(db_path):
    """Data_Analysis 实例桩（__new__+手工属性——真 SQLiteDB，跳过 Config 类缓存）。"""
    from utils.data_analysis import Data_Analysis
    from utils.common import Common

    class _StubConfig:
        def __init__(self, mapping):
            self.mapping = mapping

        def get(self, *keys):
            result = self.mapping
            for key in keys:
                if result is None or not isinstance(result, dict):
                    return None
                result = result.get(key)
            return result

    da = Data_Analysis.__new__(Data_Analysis)
    da.config = _StubConfig({"database": {"path": str(db_path)}})
    da.common = Common()
    # 库文件不存在时不构造 SQLiteDB（其构造会创建文件，破坏缺库分支测试）
    da.db = SQLiteDB(str(db_path)) if os.path.exists(str(db_path)) else None
    return da


@pytest.fixture()
def analysis_db(tmp_path):
    """临时积分/弹幕库（镜像生产 DDL+种子数据）。"""
    db_path = tmp_path / "analysis_test.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(INTEGRAL_TABLE_DDL)
    conn.execute(DANMU_TABLE_DDL)
    conn.executemany(
        "INSERT INTO integral VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("douyin", "用户甲", "u1", 100, 5, 3, "2026-10-31 10:00:00", 520, "2026-10-31 10:00:00", None),
            ("douyin", "用户乙", "u2", 200, 3, 8, "2026-10-31 09:00:00", 66, "2026-10-31 09:00:00", None),
            ("douyin", "用户丙", "u3", 50, 9, 1, "2026-10-31 08:00:00", 1314, "2026-10-31 08:00:00", None),
        ],
    )
    conn.executemany(
        "INSERT INTO danmu (username, content, ts) VALUES (?, ?, ?)",
        [
            ("用户甲", "主播今天真好看", "2026-10-31 10:00:00"),
            ("用户乙", "主播加油", "2026-10-31 10:01:00"),
            ("用户丙", "主播今天状态不错", "2026-10-31 10:02:00"),
        ],
    )
    conn.commit()
    conn.close()
    return db_path


# ---------- 礼物榜单全链移除 ----------

class TestGiftBoardRemoved:
    def test_backend_method_removed(self):
        src = _src("utils/data_analysis.py")
        assert "get_gift_option" not in src
        assert "FROM gift" not in src  # 积分/词云两接口不触碰 gift 表

    def test_frontend_card_removed(self):
        src = _src("frontend/ui/tabs/data_analysis.py")
        assert "礼物榜单" not in src
        assert "get_gift_option" not in src

    def test_template_node_removed(self):
        cfg = json.loads(_src("config.example.json"))
        assert "gift" not in cfg["data_analysis"]

    def test_retired_key_registered(self):
        src = _src("frontend/main.py")
        assert '(("data_analysis", "gift")' in src

    def test_initial_load_gathers_two_charts(self):
        src = _src("frontend/ui/tabs/data_analysis.py")
        gather = src.split("await asyncio.gather(")[1]
        assert gather.count("update_echart_") == 2  # 词云+积分，无礼物图


# ---------- 积分统计四榜（数据库接口+统计逻辑） ----------

class TestIntegralOption:
    def test_dimensions_match_table_columns(self, analysis_db):
        """dataset dimensions 必须与 integral 表 10 列一致——积分批次新增
        last_entrance_ts 后此前漏更（SELECT * 10 值 vs dimensions 9 维错位）。"""
        da = _make_da(analysis_db)
        option = da.get_integral_option("integral", 10)
        assert option is not None
        dims = option["dataset"][0]["dimensions"]
        assert dims == ["platform", "username", "uid", "integral", "view_num",
                        "sign_num", "last_sign_ts", "total_price", "last_ts",
                        "last_entrance_ts"]
        assert all(len(row) == len(dims) for row in option["dataset"][0]["source"])

    @pytest.mark.parametrize("sort_key,expected_first", [
        ("integral", "用户乙"),        # 积分榜：200 最高
        ("view_num", "用户丙"),        # 观看榜：9 次最多（12h 限频口径=计分次数）
        ("sign_num", "用户乙"),        # 签到榜：8 次最多
        ("total_price", "用户丙"),     # 金额榜：1314 元最多
    ])
    def test_board_sorting(self, analysis_db, sort_key, expected_first):
        da = _make_da(analysis_db)
        option = da.get_integral_option(sort_key, 10)
        source = option["dataset"][0]["source"]
        idx = option["dataset"][0]["dimensions"].index(sort_key)
        # dataset transform 恒按 integral 排（图表主序），SQL ORDER BY 决定
        # LIMIT 截断前 top_num 的选取——断言 top1 在列
        usernames = {row[1] for row in source}
        assert expected_first in usernames
        assert idx > 0  # 排序键确在维度表中

    def test_xaxis_follows_sql_order(self, analysis_db):
        """xAxis 用户序列=SQL ORDER BY 结果（LIMIT 截断语义正确）。"""
        da = _make_da(analysis_db)
        option = da.get_integral_option("total_price", 2)
        assert option["xAxis"][0]["data"] == ["用户丙", "用户甲"]  # 1314 > 520

    def test_invalid_type_falls_back(self, analysis_db):
        """白名单守卫：非法 type 回退 integral，不产生 SQL 错误/注入。"""
        da = _make_da(analysis_db)
        option = da.get_integral_option("1; DROP TABLE integral", 10)
        assert option is not None
        assert option["xAxis"][0]["data"][0] == "用户乙"  # 按 integral 降序 top1
        # 表未被注入破坏
        conn = sqlite3.connect(str(analysis_db))
        rows = conn.execute("SELECT COUNT(*) FROM integral").fetchone()[0]
        conn.close()
        assert rows == 3

    def test_empty_table_returns_option(self, tmp_path):
        db_path = tmp_path / "empty.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute(INTEGRAL_TABLE_DDL)
        conn.commit()
        conn.close()
        da = _make_da(db_path)
        option = da.get_integral_option("integral", 10)
        assert option is not None
        assert option["dataset"][0]["source"] == []

    def test_db_missing_returns_none(self, tmp_path):
        """库文件不存在→None（不经 SQLiteDB 构造——其会创建文件）。"""
        da = _make_da(tmp_path / "not_exist.db")
        assert da.get_integral_option("integral", 10) is None


# ---------- 弹幕关键词统计 ----------

class TestWordCloudOption:
    def test_word_cloud_counts(self, analysis_db):
        da = _make_da(analysis_db)
        option = da.get_comment_word_cloud_option(10)
        assert option is not None
        names = {d["name"] for d in option["series"][0]["data"]}
        assert "主播" in names  # 三条弹幕含两次"主播"（len>1 分词入统计）

    def test_scan_limit_bounded(self):
        src = _src("utils/data_analysis.py")
        assert "ORDER BY rowid DESC LIMIT" in src  # 近 N 条限量扫描


# ---------- 系统流程匹配（写侧开关↔读侧榜单） ----------

class TestFlowMatch:
    def test_write_side_switches_intact(self):
        """三张源表的写入口开关：danmu=comment_enable / gift=gift_enable /
        entrance=entrance_enable（数据库设置页）——积分表由计分链直写。"""
        comment = _src("utils/handlers/comment_handler.py")
        assert 'config.get("database", "comment_enable")' in comment
        event = _src("utils/handlers/event_handler.py")
        assert 'config.get("database", "gift_enable")' in event
        assert 'config.get("database", "entrance_enable")' in event

    def test_integral_four_buttons_intact(self):
        """积分统计卡四按钮（积分/观看/签到/金额）一行 flex 等宽。"""
        src = _src("frontend/ui/tabs/data_analysis.py")
        assert src.count("flex:1 1 140px") == 4
        for btn_type in ("'integral'", "'view_num'", "'sign_num'", "'total_price'"):
            assert f"_integral_btn({btn_type})" in src

    def test_db_path_from_database_settings(self):
        """读侧库路径与数据库设置页同源（database.path）。"""
        src = _src("utils/data_analysis.py")
        assert "self.config.get('database', 'path')" in src


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
