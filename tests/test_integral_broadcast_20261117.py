# -*- coding: UTF-8 -*-
"""
积分播报守卫测试（2026-11-17 积分播报批次；2026-11-19 积分记录前移批次、
播报文案收敛批次更新）

覆盖义务（任务书）：
- 播报开关默认关闭（缺键/false）：签到/礼物/入场/关注积分累计只写库，
  broadcast 路径不产生任何播报 TTS（2026-11-19 播报开关统管修复批次起：
  档位回复一并纳入 broadcast 门，关闭=零积分播报）
- 开启后写库成功：播报文案优先级=档位文案/平铺文案（ctx 路径）→ 内置默认文案
  兜底（2026-11-19 播报文案收敛批次：播报文案独立配置项退役——{username}/
  {integral} 变量由内置默认模板填充，{integral}=本次累计后的当前总积分，
  取自写库后最新行）
- 播报入队与查询回复同通道：type=comment（复用 _integral_reply）
- 窗口限频拦截（2026-11-19 积分窗口语义批次：签到/入场=自然日固定窗口、关注=
  一生一次；同日/已计分重复行为）：静默不播报；跨日（次日 0 点起）恢复播报
- 写库失败：不播报（CAP6 partial 语义不破）

2026-11-19 积分记录前移批次：记分/播报分离——积分链（integral_handle）只记分
（写库+挂 IntegralScoreContext），播报由限入后挂载点消费 ctx 触发（签到=
integral_sign_consume；gift/entrance/follow=integral_announce_if_scored）。
本文件用例改为「记分 → 挂载点消费」两段式驱动；follow 档位文案（copywriting）
收敛纳入统一门（平铺文案优先/内置默认文案兜底/开关门控）。

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

# 内置默认文案兜底播报的期望内容直接内联在各用例断言中（渲染形态以
# feature_handler._INTEGRAL_BROADCAST_DEFAULT_COPY 为准；2026-11-19 播报文案
# 收敛批次起为唯一兜底文案源）


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
    broadcast 缺省=存量配置升级形态，播报默认关）。"""
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


def announce(handler, data, module):
    """2026-11-19 积分记录前移批次：记分/播报两段式驱动——记分（integral_handle
    同步直调形态挂 IntegralScoreContext）后由限入后挂载点消费 ctx 播报。"""
    if module == "sign":
        return handler.integral_sign_consume(data)
    return handler.integral_announce_if_scored(data, module)


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


def backdate(db, column, days):
    """把指定时间列回拨 days 天（2026-11-19 积分窗口语义批次：自然日窗口跨日
    模拟——减 1 天必落在前一自然日，次日 0 点起重新可计）。"""
    old = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S.%f")
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
        # 2026-11-19 积分记录前移批次：开关关时挂载点消费同样零播报（未消费形态
        # 也不产生残留播报）
        builtin_marks = [tpl.format(username="小明", integral=13)
                         for tpl in feature_handler_module._INTEGRAL_BROADCAST_DEFAULT_COPY.values()]
        for content in tts_contents(fake):
            for mark in builtin_marks:
                assert mark not in content, "开关缺省不得播报内置默认文案"

    def test_explicit_false_no_broadcast(self, handler, fake, db):
        """broadcast 显式 False：档位文案/平铺文案一并停播（2026-11-19 统一门：
        零积分播报，只写库）。"""
        cfg = base_config()
        cfg["integral"]["sign"].update({"broadcast": False})
        cfg["integral"]["gift"].update({"broadcast": False})
        cfg["integral"]["entrance"].update({"broadcast": False})
        cfg["integral"]["follow"].update({"broadcast": False})
        handler.my_handle.config = _StubConfig(cfg)

        data = {"platform": "dy", "username": "小明", "content": "签到"}
        assert handler.integral_handle("comment", data) is True
        # consume 返回值=终结判定（计分成功即消费该弹幕，与播报无关）；零播报由
        # tts_contents 断言（2026-11-19 积分记录前移批次）
        assert announce(handler, data, "sign") is True, "计分成功=消费该弹幕（终结判定）"
        row = db.fetch_one("SELECT integral FROM integral")
        assert row == (1,)
        assert tts_contents(fake) == [], "播报关闭=零积分播报（档位回复一并停播）"


# ============ 开启播报：写库成功 → 播报（变量填充正确） ============

class TestBroadcastEnabled:
    def test_sign_broadcast_filled_variables(self, handler, fake, db):
        """签到播报：档位文案缺失回退内置默认文案（{username} 变量填充正确；
        2026-11-19 播报文案收敛批次：兜底文案源收敛为内置默认文案）。"""
        cfg = base_config()
        cfg["integral"]["sign"].update({"broadcast": True})
        # 档位块无 copywriting → 统一门文案链走内置默认文案兜底
        cfg["integral"]["sign"]["copywriting"] = [{"sign_num_interval": "0-999999999"}]
        handler.my_handle.config = _StubConfig(cfg)

        data = {"platform": "dy", "username": "小明", "content": "签到"}
        assert handler.integral_handle("comment", data) is True
        assert announce(handler, data, "sign") is True, "挂载点消费触发播报"
        row = db.fetch_one("SELECT integral FROM integral")
        assert row == (1,)
        assert "感谢 小明 签到，积分+1" in tts_contents(fake), \
            "内置默认文案变量填充正确"

    def test_gift_broadcast_total_after_accumulation(self, handler, fake, db):
        """礼物播报：{integral} 取写库后累计总积分（存量 1 分 + 礼物 10 分 = 11；
        档位文案缺失回退内置默认文案）。"""
        cfg = base_config()
        cfg["integral"]["gift"].update({"broadcast": True})
        # 档位块无 copywriting → 统一门文案链走内置默认文案兜底
        cfg["integral"]["gift"]["copywriting"] = [{"gift_price_interval": "0-999999999"}]
        handler.my_handle.config = _StubConfig(cfg)

        assert handler.integral_handle("comment", {"platform": "dy", "username": "小明", "content": "签到"}) is True
        handler.my_handle.replies.clear()
        data = {"platform": "dy", "username": "小明", "gift_name": "小花", "total_price": 1.0}
        assert handler.integral_handle("gift", data) is True
        assert announce(handler, data, "gift") is True
        assert "小明 的礼物已记入积分，当前 11 分" in tts_contents(fake)

    def test_entrance_and_follow_broadcast(self, handler, fake, db):
        """入场/关注播报：各累计成功后按各自开关播报（2026-11-19 积分记录前移
        批次：follow 档位文案收敛纳入统一门——平铺文案优先/内置默认文案兜底，
        本用例清空 copywriting 走内置默认文案兜底路径验证）。"""
        cfg = base_config()
        cfg["integral"]["entrance"].update({"broadcast": True})
        # 档位块无 copywriting → 统一门文案链走内置默认文案兜底
        cfg["integral"]["entrance"]["copywriting"] = [{"entrance_num_interval": "0-999999999"}]
        cfg["integral"]["follow"].update({"broadcast": True})
        cfg["integral"]["follow"]["copywriting"] = []  # 清空平铺文案 → 内置默认文案兜底
        handler.my_handle.config = _StubConfig(cfg)

        e_data = {"platform": "dy", "username": "小明"}
        assert handler.integral_handle("entrance", e_data) is True
        assert announce(handler, e_data, "entrance") is True
        f_data = {"platform": "dy", "username": "小明"}
        assert handler.integral_handle("follow", f_data) is True
        assert announce(handler, f_data, "follow") is True
        contents = tts_contents(fake)
        assert "欢迎 小明 回来，积分+1" in contents
        assert "感谢 小明 关注，积分+1" in contents
        assert db.fetch_one("SELECT integral FROM integral") == (2,)

    def test_broadcast_channel_aligned_with_query_reply(self, handler, fake, db):
        """播报消息与查询回复同通道：type=comment 且消息字段结构一致。"""
        cfg = base_config()
        cfg["integral"]["sign"].update({"broadcast": True})
        # 档位块无 copywriting → 统一门文案链走内置默认文案兜底
        cfg["integral"]["sign"]["copywriting"] = [{"sign_num_interval": "0-999999999"}]
        handler.my_handle.config = _StubConfig(cfg)

        data = {"platform": "dy", "username": "小明", "content": "签到"}
        assert handler.integral_handle("comment", data) is True
        assert announce(handler, data, "sign") is True
        broadcast = [r for r in fake.replies if isinstance(r, dict)
                     and r.get("content") == "感谢 小明 签到，积分+1"]
        assert broadcast, "开启播报应有播报消息入队"
        assert broadcast[0].get("type") == "comment", "播报与查询回复同 type=comment 口径"

        assert handler.integral_handle("crud", {"platform": "dy", "username": "小明", "content": "查询积分"}) is True
        query_msg = [r for r in fake.replies if isinstance(r, dict) and r.get("type") == "comment"
                     and "您当前的积分是" in str(r.get("content"))]
        assert query_msg, "查询回复通道可用（对照）"
        assert set(broadcast[0].keys()) == set(query_msg[-1].keys()), "播报复用查询回复的消息结构"

    def test_tier_copy_missing_falls_back_to_builtin(self, handler, fake, db):
        """开启播报且档位/平铺文案缺失：回退内置默认文案（变量填充）。
        copywriting 全缺（档位文案优先链全空）才走内置默认兜底。"""
        cfg = base_config()
        cfg["integral"]["follow"].update({"broadcast": True})
        cfg["integral"]["follow"]["copywriting"] = []  # 档位文案优先链清空 → 内置默认兜底
        handler.my_handle.config = _StubConfig(cfg)

        data = {"platform": "dy", "username": "小明"}
        assert handler.integral_handle("follow", data) is True
        assert announce(handler, data, "follow") is True
        assert "感谢 小明 关注，积分+1" in tts_contents(fake)

    def test_per_module_switch_independent(self, handler, fake, db):
        """开关按模块独立：仅签到开启时，礼物累计不播报。"""
        cfg = base_config()
        cfg["integral"]["sign"].update({"broadcast": True})
        # 档位块无 copywriting → 统一门文案链走内置默认文案兜底
        cfg["integral"]["sign"]["copywriting"] = [{"sign_num_interval": "0-999999999"}]
        cfg["integral"]["gift"].update({"broadcast": False})  # 显式关
        handler.my_handle.config = _StubConfig(cfg)

        s_data = {"platform": "dy", "username": "小明", "content": "签到"}
        assert handler.integral_handle("comment", s_data) is True
        assert announce(handler, s_data, "sign") is True
        g_data = {"platform": "dy", "username": "小明", "gift_name": "小花", "total_price": 1.0}
        assert handler.integral_handle("gift", g_data) is True
        assert announce(handler, g_data, "gift") is False, "礼物开关关闭挂载点零播报"
        contents = tts_contents(fake)
        assert "感谢 小明 签到，积分+1" in contents
        assert not any("礼物已记入积分" in c for c in contents), "礼物开关关闭不播报"


# ============ 限频拦截：不播报 ============

class TestBroadcastCooldownBlocked:
    def test_sign_cooldown_no_broadcast(self, handler, fake, db):
        """同日窗口内重复签到（自然日固定窗口）：静默拦截（返回 True 消费弹幕），
        不播报。"""
        cfg = base_config()
        cfg["integral"]["sign"].update({"broadcast": True})
        # 档位块无 copywriting → 统一门文案链走内置默认文案兜底
        cfg["integral"]["sign"]["copywriting"] = [{"sign_num_interval": "0-999999999"}]
        handler.my_handle.config = _StubConfig(cfg)

        data = {"platform": "dy", "username": "小明", "content": "签到"}
        assert handler.integral_handle("comment", data) is True
        assert announce(handler, data, "sign") is True
        assert any("感谢 小明 签到" in c for c in tts_contents(fake)), "首次签到应有播报（对照）"

        fake.replies.clear()
        blocked = {"platform": "dy", "username": "小明", "content": "签到"}
        assert handler.integral_handle("comment", blocked) is True
        assert announce(handler, blocked, "sign") is True, "限频拦截=消费弹幕（终结判定 True）"
        assert fake.replies == [], "限频拦截为隐形语义：不播报也不回复"
        assert db.fetch_one("SELECT integral FROM integral") == (1,), "窗口内重复签到不计分"

    def test_entrance_and_follow_cooldown_no_broadcast(self, handler, fake, db):
        """同日窗口内重复入场/已计分（一生一次）重复关注：静默拦截不播报。"""
        cfg = base_config()
        cfg["integral"]["entrance"].update({"broadcast": True})
        cfg["integral"]["follow"].update({"broadcast": True})
        handler.my_handle.config = _StubConfig(cfg)

        e_data = {"platform": "dy", "username": "小明"}
        f_data = {"platform": "dy", "username": "小明"}
        assert handler.integral_handle("entrance", e_data) is True
        assert handler.integral_handle("follow", f_data) is True

        fake.replies.clear()
        e_block = {"platform": "dy", "username": "小明"}
        f_block = {"platform": "dy", "username": "小明"}
        assert handler.integral_handle("entrance", e_block) is True
        assert handler.integral_handle("follow", f_block) is True
        assert announce(handler, e_block, "entrance") is False, "窗口内重复入场未记分零播报"
        assert announce(handler, f_block, "follow") is False, "窗口内重复关注未记分零播报"
        assert fake.replies == [], "窗口内重复入场/关注静默拦截不播报"
        assert db.fetch_one("SELECT integral FROM integral") == (2,)

    def test_cooldown_expiry_broadcast_resumes(self, handler, fake, db):
        """跨日（回拨 1 天，前一自然日）后播报恢复，{integral} 反映累计后总积分
        （2026-11-19 积分窗口语义批次：自然日窗口次日重置，替代原 24h 滚动超窗）。"""
        cfg = base_config()
        cfg["integral"]["sign"].update({"broadcast": True})
        # 档位块无 copywriting → 统一门文案链走内置默认文案兜底
        cfg["integral"]["sign"]["copywriting"] = [{"sign_num_interval": "0-999999999"}]
        handler.my_handle.config = _StubConfig(cfg)

        data = {"platform": "dy", "username": "小明", "content": "签到"}
        assert handler.integral_handle("comment", data) is True
        assert announce(handler, data, "sign") is True
        backdate(db, "last_sign_ts", 1)
        fake.replies.clear()
        resumed = {"platform": "dy", "username": "小明", "content": "签到"}
        assert handler.integral_handle("comment", resumed) is True
        assert announce(handler, resumed, "sign") is True
        assert "感谢 小明 签到，积分+1" in tts_contents(fake)
        assert db.fetch_one("SELECT integral FROM integral") == (2,)


# ============ 写库失败：不播报 ============

class TestBroadcastDbFailure:
    def test_sign_db_write_failure_no_broadcast(self, handler, fake, db):
        """写库失败（重试耗尽）：返回 False，不播报也不回复。"""
        cfg = base_config()
        cfg["integral"]["sign"].update({"broadcast": True})
        handler.my_handle.config = _StubConfig(cfg)
        handler.my_handle.db = _BrokenWriteDB(db)

        data = {"platform": "dy", "username": "小明", "content": "签到"}
        assert handler.integral_handle("comment", data) is False
        assert announce(handler, data, "sign") is False, "写库失败未记分，挂载点零播报（CAP6）"
        assert fake.replies == [], "写库失败不播报"
        assert db.fetch_one("SELECT integral FROM integral") is None, "写库未发生"

    def test_gift_db_write_failure_no_broadcast(self, handler, fake, db):
        """礼物写库失败：不播报。"""
        cfg = base_config()
        cfg["integral"]["gift"].update({"broadcast": True})
        handler.my_handle.config = _StubConfig(cfg)
        handler.my_handle.db = _BrokenWriteDB(db)

        data = {"platform": "dy", "username": "小明", "gift_name": "小花", "total_price": 1.0}
        assert handler.integral_handle("gift", data) is False
        assert announce(handler, data, "gift") is False
        assert fake.replies == []
        assert db.fetch_one("SELECT integral FROM integral") is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
