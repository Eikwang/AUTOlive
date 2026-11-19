# -*- coding: UTF-8 -*-
"""
积分播报守卫测试（2026-11-17 积分播报批次）

覆盖义务（任务书）：
- 播报开关默认关闭（缺键/false）：签到/礼物/入场/关注积分累计只写库，
  broadcast 路径不产生任何播报 TTS（既有档位回复现状不变）
- 开启后写库成功：按 integral.<module>.broadcast_copy 播报，
  {username}/{integral} 变量填充正确（{integral}=本次累计后的当前总积分，
  取自写库后最新行）；缺 broadcast_copy 键回退内置默认文案（warn_once）
- 播报入队与查询回复同通道：type=comment（复用 _integral_reply）
- 限频拦截（24h 窗口内重复签到/入场/关注）：静默不播报；超窗恢复播报
- 写库失败：不播报（CAP6 partial 语义不破）

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
from utils.handlers.feature_handler import FeatureHandler

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

# 可识别的播报模板标记（与档位回复文案无交集，便于精确断言播报是否入队）
BC_SIGN = "播报标记{username}签到当前{integral}分"
BC_GIFT = "播报标记{username}礼物当前{integral}分"
BC_ENTRANCE = "播报标记{username}入场当前{integral}分"
BC_FOLLOW = "播报标记{username}关注当前{integral}分"


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
    """最小 my_handle 替身：仅提供积分链路依赖的接口。"""

    def __init__(self, config_mapping, db):
        self.config = _StubConfig(config_mapping)
        self.db = db
        self.common = Common()
        self.replies = []

    def audio_synthesis_handle(self, message):
        self.replies.append(message)

    def webui_show_chat_log_callback(self, data_type, data, resp_content):
        self.replies.append({"type": "webui_log", "data_type": data_type, "content": resp_content})


class _BrokenWriteDB:
    """写库失败替身：execute 恒 False（读接口透传），模拟 DB 故障。"""

    def __init__(self, db):
        self._db = db

    def execute(self, sql, args=None):
        return False

    def fetch_one(self, sql, args=None):
        return self._db.fetch_one(sql, args)

    def fetch_all(self, sql, args=None):
        return self._db.fetch_all(sql, args)


def base_config():
    """镜像 config.json 积分节的最小配置（enable 全开 + 档位文案固定单条；
    broadcast/broadcast_copy 缺省=存量配置升级形态，播报默认关）。"""
    return {
        "integral": {
            "enable": True,
            "sign": {"enable": True, "cmd": ["签到", "打卡"], "get_integral": 1,
                     "copywriting": [{"sign_num_interval": "0-999999",
                                      "copywriting": ["{username}打卡成功，获得{get_integral}点积分"]}]},
            "gift": {"enable": True, "get_integral_proportion": 10.0,
                     "copywriting": [{"gift_price_interval": "0-999999999",
                                      "copywriting": ["感谢{username}的{gift_name}，获得{get_integral}点积分"]}]},
            "entrance": {"enable": True, "get_integral": 1,
                         "copywriting": [{"entrance_num_interval": "0-999999",
                                          "copywriting": ["欢迎{username}，获得{get_integral}点积分"]}]},
            "follow": {"enable": True, "get_integral": 1,
                       "copywriting": ["{username}感谢关注，获得{get_integral}点积分"]},
            "crud": {"query": {"enable": True, "cmd": ["我的积分", "查询积分"],
                               "copywriting": ["{username}，您当前的积分是{integral}"]}},
        },
        "audio_synthesis_type": "edge-tts",
        "edge-tts": {},
        "filter": {},
    }


def tts_contents(fake):
    """取 TTS 消息（type=comment）的回复内容列表（webui 日志不入）。"""
    return [r.get("content") for r in fake.replies
            if isinstance(r, dict) and r.get("type") == "comment"]


@pytest.fixture
def db(tmp_path):
    """新装形态测试库。"""
    database = SQLiteDB(str(tmp_path / "integral_broadcast.db"))
    assert database.execute(FRESH_INTEGRAL_DDL)
    yield database
    database.close()


@pytest.fixture
def fake(db):
    return _FakeMyHandle(base_config(), db)


@pytest.fixture
def handler(fake):
    return FeatureHandler(fake)


@pytest.fixture(autouse=True)
def fast_retry_sleep(monkeypatch):
    monkeypatch.setattr(feature_handler_module.time, "sleep", lambda *_: None)


def backdate(db, column, hours):
    """把指定时间列回拨 hours 小时（模拟超窗状态）。"""
    old = (datetime.now() - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S.%f")
    assert db.execute(f"UPDATE integral SET {column} = ?", (old,))
    return old


# ============ 开关默认关：只写库不播报 ============

class TestBroadcastDisabledByDefault:
    def test_missing_keys_no_broadcast(self, handler, fake, db):
        """broadcast 键缺省（存量配置升级形态）：四类累计只写库，无任何播报。"""
        assert handler.integral_handle("comment", {"platform": "dy", "username": "小明", "content": "签到"}) is True
        assert handler.integral_handle("gift", {"platform": "dy", "username": "小明",
                                                "gift_name": "小花", "total_price": 1.0}) is True
        assert handler.integral_handle("entrance", {"platform": "dy", "username": "小明"}) is True
        assert handler.integral_handle("follow", {"platform": "dy", "username": "小明"}) is True

        row = db.fetch_one("SELECT integral FROM integral")
        assert row == (1 + 10 + 1 + 1,), "四类累计均写库成功"
        builtin_marks = [tpl.format(username="小明", integral=13)
                         for tpl in feature_handler_module._INTEGRAL_BROADCAST_DEFAULT_COPY.values()]
        for content in tts_contents(fake):
            for mark in builtin_marks:
                assert mark not in content, "开关缺省不得播报内置默认文案"

    def test_explicit_false_no_broadcast(self, handler, fake, db):
        """broadcast 显式 False：配置了播报文案也不播报（只写库）。"""
        cfg = base_config()
        cfg["integral"]["sign"].update({"broadcast": False, "broadcast_copy": BC_SIGN})
        cfg["integral"]["gift"].update({"broadcast": False, "broadcast_copy": BC_GIFT})
        cfg["integral"]["entrance"].update({"broadcast": False, "broadcast_copy": BC_ENTRANCE})
        cfg["integral"]["follow"].update({"broadcast": False, "broadcast_copy": BC_FOLLOW})
        handler.my_handle.config = _StubConfig(cfg)

        assert handler.integral_handle("comment", {"platform": "dy", "username": "小明", "content": "签到"}) is True
        row = db.fetch_one("SELECT integral FROM integral")
        assert row == (1,)
        contents = tts_contents(fake)
        assert len(contents) == 1, "只保留既有档位回复一条，无额外播报"
        assert "播报标记" not in contents[0]


# ============ 开启播报：写库成功 → 播报（变量填充正确） ============

class TestBroadcastEnabled:
    def test_sign_broadcast_filled_variables(self, handler, fake, db):
        """签到播报：{username}/{integral} 变量填充正确。"""
        cfg = base_config()
        cfg["integral"]["sign"].update({"broadcast": True, "broadcast_copy": BC_SIGN})
        handler.my_handle.config = _StubConfig(cfg)

        assert handler.integral_handle("comment", {"platform": "dy", "username": "小明", "content": "签到"}) is True
        row = db.fetch_one("SELECT integral FROM integral")
        assert row == (1,)
        assert "播报标记小明签到当前1分" in tts_contents(fake), \
            "播报文案变量填充正确（{integral}=本次累计后的当前总积分）"

    def test_gift_broadcast_total_after_accumulation(self, handler, fake, db):
        """礼物播报：{integral} 取写库后累计总积分（存量 1 分 + 礼物 10 分 = 11）。"""
        cfg = base_config()
        cfg["integral"]["gift"].update({"broadcast": True, "broadcast_copy": BC_GIFT})
        handler.my_handle.config = _StubConfig(cfg)

        assert handler.integral_handle("comment", {"platform": "dy", "username": "小明", "content": "签到"}) is True
        handler.my_handle.replies.clear()
        assert handler.integral_handle("gift", {"platform": "dy", "username": "小明",
                                                "gift_name": "小花", "total_price": 1.0}) is True
        assert "播报标记小明礼物当前11分" in tts_contents(fake)

    def test_entrance_and_follow_broadcast(self, handler, fake, db):
        """入场/关注播报：各累计成功后按各自开关播报。"""
        cfg = base_config()
        cfg["integral"]["entrance"].update({"broadcast": True, "broadcast_copy": BC_ENTRANCE})
        cfg["integral"]["follow"].update({"broadcast": True, "broadcast_copy": BC_FOLLOW})
        handler.my_handle.config = _StubConfig(cfg)

        assert handler.integral_handle("entrance", {"platform": "dy", "username": "小明"}) is True
        assert handler.integral_handle("follow", {"platform": "dy", "username": "小明"}) is True
        contents = tts_contents(fake)
        assert "播报标记小明入场当前1分" in contents
        assert "播报标记小明关注当前2分" in contents
        assert db.fetch_one("SELECT integral FROM integral") == (2,)

    def test_broadcast_channel_aligned_with_query_reply(self, handler, fake, db):
        """播报消息与查询回复同通道：type=comment 且消息字段结构一致。"""
        cfg = base_config()
        cfg["integral"]["sign"].update({"broadcast": True, "broadcast_copy": BC_SIGN})
        handler.my_handle.config = _StubConfig(cfg)

        assert handler.integral_handle("comment", {"platform": "dy", "username": "小明", "content": "签到"}) is True
        broadcast = [r for r in fake.replies if isinstance(r, dict)
                     and r.get("content") == "播报标记小明签到当前1分"]
        assert broadcast, "开启播报应有播报消息入队"
        assert broadcast[0].get("type") == "comment", "播报与查询回复同 type=comment 口径"

        assert handler.integral_handle("crud", {"platform": "dy", "username": "小明", "content": "查询积分"}) is True
        query_msg = [r for r in fake.replies if isinstance(r, dict) and r.get("type") == "comment"
                     and "您当前的积分是" in str(r.get("content"))]
        assert query_msg, "查询回复通道可用（对照）"
        assert set(broadcast[0].keys()) == set(query_msg[-1].keys()), "播报复用查询回复的消息结构"

    def test_missing_broadcast_copy_falls_back_to_builtin(self, handler, fake, db):
        """开启播报但缺 broadcast_copy 键：回退内置默认文案（变量填充）。"""
        cfg = base_config()
        cfg["integral"]["follow"].update({"broadcast": True})
        handler.my_handle.config = _StubConfig(cfg)

        assert handler.integral_handle("follow", {"platform": "dy", "username": "小明"}) is True
        assert "感谢 小明 关注，积分+1" in tts_contents(fake)

    def test_per_module_switch_independent(self, handler, fake, db):
        """开关按模块独立：仅签到开启时，礼物累计不播报。"""
        cfg = base_config()
        cfg["integral"]["sign"].update({"broadcast": True, "broadcast_copy": BC_SIGN})
        cfg["integral"]["gift"].update({"broadcast_copy": BC_GIFT})  # 开关缺省=关
        handler.my_handle.config = _StubConfig(cfg)

        assert handler.integral_handle("comment", {"platform": "dy", "username": "小明", "content": "签到"}) is True
        assert handler.integral_handle("gift", {"platform": "dy", "username": "小明",
                                                "gift_name": "小花", "total_price": 1.0}) is True
        contents = tts_contents(fake)
        assert "播报标记小明签到当前1分" in contents
        assert not any("播报标记小明礼物" in c for c in contents), "礼物开关关闭不播报"


# ============ 限频拦截：不播报 ============

class TestBroadcastCooldownBlocked:
    def test_sign_cooldown_no_broadcast(self, handler, fake, db):
        """24h 窗口内重复签到：静默拦截（返回 True 消费弹幕），不播报。"""
        cfg = base_config()
        cfg["integral"]["sign"].update({"broadcast": True, "broadcast_copy": BC_SIGN})
        handler.my_handle.config = _StubConfig(cfg)

        assert handler.integral_handle("comment", {"platform": "dy", "username": "小明", "content": "签到"}) is True
        assert any("播报标记" in c for c in tts_contents(fake)), "首次签到应有播报（对照）"

        fake.replies.clear()
        assert handler.integral_handle("comment", {"platform": "dy", "username": "小明", "content": "签到"}) is True
        assert fake.replies == [], "限频拦截为隐形语义：不播报也不回复"
        assert db.fetch_one("SELECT integral FROM integral") == (1,), "窗口内重复签到不计分"

    def test_entrance_and_follow_cooldown_no_broadcast(self, handler, fake, db):
        """24h 窗口内重复入场/关注：静默拦截不播报。"""
        cfg = base_config()
        cfg["integral"]["entrance"].update({"broadcast": True, "broadcast_copy": BC_ENTRANCE})
        cfg["integral"]["follow"].update({"broadcast": True, "broadcast_copy": BC_FOLLOW})
        handler.my_handle.config = _StubConfig(cfg)

        assert handler.integral_handle("entrance", {"platform": "dy", "username": "小明"}) is True
        assert handler.integral_handle("follow", {"platform": "dy", "username": "小明"}) is True

        fake.replies.clear()
        assert handler.integral_handle("entrance", {"platform": "dy", "username": "小明"}) is True
        assert handler.integral_handle("follow", {"platform": "dy", "username": "小明"}) is True
        assert fake.replies == [], "窗口内重复入场/关注静默拦截不播报"
        assert db.fetch_one("SELECT integral FROM integral") == (2,)

    def test_cooldown_expiry_broadcast_resumes(self, handler, fake, db):
        """超窗（回拨 25 小时）后播报恢复，{integral} 反映累计后总积分。"""
        cfg = base_config()
        cfg["integral"]["sign"].update({"broadcast": True, "broadcast_copy": BC_SIGN})
        handler.my_handle.config = _StubConfig(cfg)

        assert handler.integral_handle("comment", {"platform": "dy", "username": "小明", "content": "签到"}) is True
        backdate(db, "last_sign_ts", 25)
        fake.replies.clear()
        assert handler.integral_handle("comment", {"platform": "dy", "username": "小明", "content": "签到"}) is True
        assert "播报标记小明签到当前2分" in tts_contents(fake)
        assert db.fetch_one("SELECT integral FROM integral") == (2,)


# ============ 写库失败：不播报 ============

class TestBroadcastDbFailure:
    def test_sign_db_write_failure_no_broadcast(self, handler, fake, db):
        """写库失败（重试耗尽）：返回 False，不播报也不回复。"""
        cfg = base_config()
        cfg["integral"]["sign"].update({"broadcast": True, "broadcast_copy": BC_SIGN})
        handler.my_handle.config = _StubConfig(cfg)
        handler.my_handle.db = _BrokenWriteDB(db)

        assert handler.integral_handle("comment", {"platform": "dy", "username": "小明", "content": "签到"}) is False
        assert fake.replies == [], "写库失败不播报"
        assert db.fetch_one("SELECT integral FROM integral") is None, "写库未发生"

    def test_gift_db_write_failure_no_broadcast(self, handler, fake, db):
        """礼物写库失败：不播报。"""
        cfg = base_config()
        cfg["integral"]["gift"].update({"broadcast": True, "broadcast_copy": BC_GIFT})
        handler.my_handle.config = _StubConfig(cfg)
        handler.my_handle.db = _BrokenWriteDB(db)

        assert handler.integral_handle("gift", {"platform": "dy", "username": "小明",
                                                "gift_name": "小花", "total_price": 1.0}) is False
        assert fake.replies == []
        assert db.fetch_one("SELECT integral FROM integral") is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
