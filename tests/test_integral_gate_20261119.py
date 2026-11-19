# -*- coding: UTF-8 -*-
"""
积分播报与其它链路的冲突门控守卫测试（2026-11-19 积分播报门控批次）

三个门控点（用户裁决语义）：
1. 签到终结：签到命令命中 → 积分链消费（写库+可选播报）后必须终结，同一弹幕
   不得再触发 LLM 对话。现状核实（2026-11-19）：comment_handler/chat_handler
   消费点在 integral_handle 返回 True 时已 return 终结（无泄漏），本文件以
   「单元语义 + 源级守卫」双保险固化；积分系统关/签到模块关/写库失败（CAP6）
   时返回 False——弹幕照旧走 LLM 链（既有语义，用户裁决确认）。
2. 礼物：integral.enable && integral.gift.enable && integral.gift.broadcast 开启
   且积分播报实际发生（写库成功且未限频拦截）→ 抑制礼物答谢语音（防一礼两报，
   积分播报优先）；播报未发生 → 答谢照旧。
3. 入场&关注：同礼物语义（播报实际发生抑制欢迎语/关注答谢，未发生答谢照旧）。
另守卫：编排画面规则（on_entrance/on_follow/按键映射分派）、写库等副作用不受
抑制影响。

实现位置：门控判定在答谢播报触发点（event_handler._integral_gate_suppress），
积分侧 _integral_broadcast 返回可判定标志并置 _integral_broadcast_fired。
纪律：pytest tmp_path 独立测试 DB 文件（同 test_integral_broadcast_20261117.py）。
"""

import inspect
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.common import Common
from utils.db import SQLiteDB
import utils.handlers.feature_handler as feature_handler_module
from utils.handlers.feature_handler import FeatureHandler
from utils.keyword_hub import KeywordHub, KeywordResult

# 与 utils/my_handle.py 建表 DDL 一致（新装形态）
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

# 可识别的播报模板标记（与档位回复/答谢文案无交集）
BC_SIGN = "门控播报标记{username}签到当前{integral}分"
BC_GIFT = "门控播报标记{username}礼物当前{integral}分"
BC_ENTRANCE = "门控播报标记{username}入场当前{integral}分"
BC_FOLLOW = "门控播报标记{username}关注当前{integral}分"

# 答谢文案标记（与播报标记无交集）
THANKS_GIFT = "答谢标记谢谢{username}的礼物"
THANKS_ENTRANCE = "答谢标记欢迎{username}入场"
THANKS_FOLLOW = "答谢标记感谢{username}关注"


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


def gate_config():
    """积分节（镜像 config.json 最小配置，enable 全开）+ 答谢节 + 库开关。
    broadcast/broadcast_copy 缺省=存量形态（播报默认关）。"""
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
        "thanks": {
            "gift_enable": True, "gift_random": False, "gift_copy": [THANKS_GIFT],
            "entrance_enable": True, "entrance_random": False, "entrance_copy": [THANKS_ENTRANCE],
            "follow_enable": True, "follow_random": False, "follow_copy": [THANKS_FOLLOW],
            "username_max_len": 10,
            "lowest_price": 0.1,
            "rate_limit_per_min": 10,
        },
        "audio_synthesis_type": "edge-tts",
        "edge-tts": {},
        "filter": {},
        "need_lang": [],
        "database": {"gift_enable": False, "entrance_enable": False,
                     "follow_enable": False, "comment_enable": False},
        "none": {},
    }


class _GateMyHandle:
    """事件链路测试替身：真实 FeatureHandler（积分链+播报标志）+ 录制播报。

    keyword_recognize 返回空 KeywordResult（可用 rec_override 注入命中字段）；
    key_mapping_handle 记录分派调用（副作用守卫断言用）。"""

    def __init__(self, config_mapping, db):
        self.config = _StubConfig(config_mapping)
        self.db = db
        self.common = Common()
        self.replies = []
        self.rec_override = None
        self.keymap_calls = []
        self.feature_handler = FeatureHandler(self)

    def integral_handle(self, type, data, hub_result=None):
        return self.feature_handler.integral_handle(type, data, hub_result=hub_result)

    def keyword_recognize(self, data, source):
        if self.rec_override is not None:
            return self.rec_override
        return KeywordResult(source=str(source or ""))

    def key_mapping_handle(self, type, data, hub_result=None):
        self.keymap_calls.append(type)
        return True

    def audio_synthesis_handle(self, message):
        self.replies.append(message)

    def webui_show_chat_log_callback(self, data_type, data, resp_content):
        self.replies.append({"type": "webui_log", "data_type": data_type,
                             "content": resp_content})


def tts_types(fake):
    """已入队播报消息的 type 列表（webui 日志不入）。"""
    return [r.get("type") for r in fake.replies
            if isinstance(r, dict) and r.get("type")]


def tts_contents(fake):
    return [r.get("content") for r in fake.replies
            if isinstance(r, dict) and r.get("type") != "webui_log"]


@pytest.fixture
def db(tmp_path):
    database = SQLiteDB(str(tmp_path / "integral_gate.db"))
    assert database.execute(FRESH_INTEGRAL_DDL)
    yield database
    database.close()


@pytest.fixture
def fake(db):
    return _GateMyHandle(gate_config(), db)


@pytest.fixture
def handler(fake):
    # 与事件链共用同一 FeatureHandler 实例（播报标志状态一致）
    return fake.feature_handler


@pytest.fixture
def eh(fake):
    from utils.handlers.event_handler import EventHandler
    return EventHandler(fake)


@pytest.fixture(autouse=True)
def fast_retry_sleep(monkeypatch):
    monkeypatch.setattr(feature_handler_module.time, "sleep", lambda *_: None)


def backdate(db, column, hours):
    old = (datetime.now() - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S.%f")
    assert db.execute(f"UPDATE integral SET {column} = ?", (old,))


def set_broadcast(fake, module, copy, on=True):
    fake.config.mapping["integral"][module].update({"broadcast": on, "broadcast_copy": copy})


# ============ 门控点 1：签到终结（积分链消费后不得进 LLM） ============

class TestSignTermination:
    def test_sign_hit_integral_on_intercepts(self, handler, fake):
        """签到命中+积分开：integral_handle 返回 True（弹幕已被积分链消费——
        comment/chat 链据此 return 终结，不再流向 LLM）。"""
        assert handler.integral_handle(
            "comment", {"platform": "dy", "username": "小明", "content": "签到"}) is True

    def test_sign_broadcast_on_intercepts_and_broadcasts(self, handler, fake):
        """播报开启路径无旁路：播报在积分链内部完成（写库成功后），链路同样终结。"""
        set_broadcast(fake, "sign", BC_SIGN)
        assert handler.integral_handle(
            "comment", {"platform": "dy", "username": "小明", "content": "签到"}) is True
        assert "门控播报标记小明签到当前1分" in tts_contents(fake), "播报开启应实际播报"
        assert handler.integral_broadcast_fired() is True

    def test_sign_cooldown_intercepts_silently(self, handler, fake):
        """24h 限频窗口内重复签到：返回 True（静默拦截消费弹幕，同样不进 LLM）。"""
        assert handler.integral_handle(
            "comment", {"platform": "dy", "username": "小明", "content": "签到"}) is True
        fake.replies.clear()
        assert handler.integral_handle(
            "comment", {"platform": "dy", "username": "小明", "content": "签到"}) is True
        assert fake.replies == [], "限频窗口内静默拦截：不计分不回复不播报"

    def test_integral_off_sign_falls_through_to_llm(self, handler, fake):
        """积分系统关：返回 False——不拦截，签到词照旧走 LLM 对话链（用户裁决）。"""
        fake.config.mapping["integral"]["enable"] = False
        assert handler.integral_handle(
            "comment", {"platform": "dy", "username": "小明", "content": "签到"}) is False

    def test_sign_module_off_falls_through_to_llm(self, handler, fake):
        """签到模块关（integral.sign.enable=False）：返回 False——照旧走 LLM。"""
        fake.config.mapping["integral"]["sign"]["enable"] = False
        assert handler.integral_handle(
            "comment", {"platform": "dy", "username": "小明", "content": "签到"}) is False

    def test_sign_write_fail_falls_through_to_llm(self, handler, fake, db):
        """写库失败（CAP6）：返回 False——积分链未消费，弹幕照旧走常规链路。"""
        fake.db = _BrokenWriteDB(db)
        assert handler.integral_handle(
            "comment", {"platform": "dy", "username": "小明", "content": "签到"}) is False

    def test_hub_integral_off_no_sign_recognition(self):
        """中枢侧同口径：integral.enable 关 → 签到词不产出 integral_sign 命中
        （弹幕链无从拦截，照旧流向 LLM）。"""
        cfg = {"integral": {"enable": False}}
        rec = KeywordHub(_HubFakeHandle(cfg)).recognize({"content": "签到"}, "comment")
        assert rec.integral_sign is False

    def test_hub_sign_module_off_no_sign_recognition(self):
        """中枢侧同口径：签到模块关 → 签到词不命中。"""
        cfg = {"integral": {"enable": True, "sign": {"enable": False}}}
        rec = KeywordHub(_HubFakeHandle(cfg)).recognize({"content": "签到"}, "comment")
        assert rec.integral_sign is False

    def test_hub_integral_on_sign_hit(self):
        """中枢侧对照：积分开+签到模块开 → 命中（消费点拦截成立的前提）。"""
        cfg = {"integral": {"enable": True, "sign": {"enable": True}}}
        rec = KeywordHub(_HubFakeHandle(cfg)).recognize({"content": "签到"}, "comment")
        assert rec.integral_sign is True


class _HubFakeHandle:
    """KeywordHub 识别测试替身（仅 config 依赖）。"""

    def __init__(self, mapping):
        self.config = _StubConfig(mapping)


class TestSignChainTerminationSourceGuard:
    """源级守卫：comment/chat 链签到积分消费点必须「命中即 return 终结」，
    弹幕不得在积分链消费后继续流向 LLM（回归=测试失败）。"""

    _GATE = (r'if rec\.integral_sign and self\.my_handle\.integral_handle'
             r'\("comment", data, hub_result=rec\):')

    def test_comment_chain_sign_gate_terminates(self):
        from utils.handlers import comment_handler as mod
        src = inspect.getsource(mod)
        assert re.search(self._GATE + r'.{0,200}?return', src, re.S), \
            "弹幕链签到积分消费点命中后必须 return 终结（不得再流向 LLM/助播链）"

    def test_chat_chain_sign_gate_terminates(self):
        from utils.handlers import chat_handler as mod
        src = inspect.getsource(mod)
        assert re.search(self._GATE + r'.{0,200}?return', src, re.S), \
            "语音链签到积分消费点命中后必须 return 终结（不得再流向 LLM/助播链）"


# ============ 门控点 2：礼物（积分播报实际发生 → 抑制答谢语音） ============

class TestGiftGate:
    def test_broadcast_fired_suppresses_gift_thanks(self, eh, fake):
        """播报实际发生（写库成功）：礼物答谢语音被抑制，无 type=gift 消息；
        积分档位回复+播报照常；写库副作用不受影响。"""
        set_broadcast(fake, "gift", BC_GIFT)
        eh.gift_handle({"platform": "dy", "username": "小明", "gift_name": "小花",
                        "gift_num": 1, "unit_price": 1, "total_price": 1})
        assert "gift" not in tts_types(fake), "积分播报实际发生应抑制礼物答谢语音"
        contents = tts_contents(fake)
        assert "门控播报标记小明礼物当前10分" in contents, "积分播报本身照常入队"
        assert "感谢小明的礼物" not in "".join(contents), "答谢文案不得出现"
        assert fake.db.fetch_one("SELECT integral FROM integral") == (10,), "积分写库副作用不受抑制影响"

    def test_broadcast_off_thanks_unchanged(self, eh, fake):
        """播报开关关闭：答谢照旧（type=gift 消息存在），积分档位回复照常、无播报。"""
        set_broadcast(fake, "gift", BC_GIFT, on=False)
        eh.gift_handle({"platform": "dy", "username": "小明", "gift_name": "小花",
                        "gift_num": 1, "unit_price": 1, "total_price": 1})
        assert tts_types(fake).count("gift") == 1, "播报关闭答谢照旧"
        assert "答谢标记谢谢小明的礼物" in tts_contents(fake)
        assert not any("门控播报标记" in c for c in tts_contents(fake)), "播报关闭不播报"

    def test_write_fail_thanks_unchanged(self, eh, fake, db):
        """写库失败（播报未发生）：答谢照旧。"""
        set_broadcast(fake, "gift", BC_GIFT)
        fake.db = _BrokenWriteDB(db)
        eh.gift_handle({"platform": "dy", "username": "小明", "gift_name": "小花",
                        "gift_num": 1, "unit_price": 1, "total_price": 1})
        assert tts_types(fake).count("gift") == 1, "播报未发生（写库失败）答谢照旧"

    def test_integral_off_thanks_unchanged(self, eh, fake):
        """积分系统关：答谢照旧。"""
        set_broadcast(fake, "gift", BC_GIFT)
        fake.config.mapping["integral"]["enable"] = False
        eh.gift_handle({"platform": "dy", "username": "小明", "gift_name": "小花",
                        "gift_num": 1, "unit_price": 1, "total_price": 1})
        assert tts_types(fake).count("gift") == 1

    def test_gift_module_off_thanks_unchanged(self, eh, fake):
        """礼物积分模块关：答谢照旧。"""
        set_broadcast(fake, "gift", BC_GIFT)
        fake.config.mapping["integral"]["gift"]["enable"] = False
        eh.gift_handle({"platform": "dy", "username": "小明", "gift_name": "小花",
                        "gift_num": 1, "unit_price": 1, "total_price": 1})
        assert tts_types(fake).count("gift") == 1

    def test_keymap_dispatch_unaffected_by_suppression(self, eh, fake):
        """按键映射礼物分派（编排类副作用）不受抑制影响：答谢被抑制时分派照常执行。"""
        set_broadcast(fake, "gift", BC_GIFT)
        fake.rec_override = KeywordResult(
            source="gift", keymap_gift_groups=[({"keywords": ["小花"], "once": False}, "小花")])
        eh.gift_handle({"platform": "dy", "username": "小明", "gift_name": "小花",
                        "gift_num": 1, "unit_price": 1, "total_price": 1})
        assert fake.keymap_calls == ["礼物"], "按键映射分派不受答谢抑制影响"
        assert "gift" not in tts_types(fake), "答谢语音仍被抑制"


# ============ 门控点 3：入场/关注（同礼物语义） ============

class TestEntranceGate:
    def test_broadcast_fired_suppresses_welcome(self, eh, fake):
        """播报实际发生：欢迎语抑制（无 type=entrance 消息），播报照常入队。"""
        set_broadcast(fake, "entrance", BC_ENTRANCE)
        eh.entrance_handle({"platform": "dy", "username": "小明"})
        assert "entrance" not in tts_types(fake), "积分播报实际发生应抑制欢迎语"
        assert "门控播报标记小明入场当前1分" in tts_contents(fake), "积分播报本身照常入队"

    def test_cooldown_broadcast_not_fired_thanks_unchanged(self, eh, fake, db):
        """24h 限频窗口内重复入场（播报未发生）：欢迎语照旧。"""
        set_broadcast(fake, "entrance", BC_ENTRANCE)
        eh.entrance_handle({"platform": "dy", "username": "小明"})
        assert "entrance" not in tts_types(fake)
        eh.entrance_handle({"platform": "dy", "username": "小明"})
        assert tts_types(fake).count("entrance") == 1, "限频窗口内播报未发生，欢迎语照旧"
        assert "答谢标记欢迎小明入场" in tts_contents(fake)

    def test_broadcast_off_thanks_unchanged(self, eh, fake):
        """播报开关关闭：欢迎语照旧、无播报。"""
        set_broadcast(fake, "entrance", BC_ENTRANCE, on=False)
        eh.entrance_handle({"platform": "dy", "username": "小明"})
        assert tts_types(fake).count("entrance") == 1
        assert "答谢标记欢迎小明入场" in tts_contents(fake)
        assert not any("门控播报标记" in c for c in tts_contents(fake))

    def test_choreo_side_effect_unaffected_by_suppression(self, eh, fake, monkeypatch):
        """入场编排画面规则不受抑制影响：欢迎语被抑制时 on_entrance 仍被调用。"""
        from utils import orchestration as orch
        choreo = MagicMock()
        monkeypatch.setattr(orch, "get_choreographer", lambda: choreo)
        set_broadcast(fake, "entrance", BC_ENTRANCE)
        eh.entrance_handle({"platform": "dy", "username": "小明"})
        choreo.on_entrance.assert_called_once()  # 编排画面规则不受抑制影响
        assert "entrance" not in tts_types(fake), "仅抑制答谢语音"


class TestFollowGate:
    def test_broadcast_fired_suppresses_follow_thanks(self, eh, fake):
        """播报实际发生：关注答谢抑制（无 type=follow 消息），播报照常入队。"""
        set_broadcast(fake, "follow", BC_FOLLOW)
        eh.follow_handle({"platform": "dy", "username": "小明"})
        assert "follow" not in tts_types(fake), "积分播报实际发生应抑制关注答谢"
        assert "门控播报标记小明关注当前1分" in tts_contents(fake), "积分播报本身照常入队"

    def test_cooldown_broadcast_not_fired_thanks_unchanged(self, eh, fake, db):
        """24h 限频窗口内重复关注（播报未发生）：关注答谢照旧。"""
        set_broadcast(fake, "follow", BC_FOLLOW)
        eh.follow_handle({"platform": "dy", "username": "小明"})
        assert "follow" not in tts_types(fake)
        eh.follow_handle({"platform": "dy", "username": "小明"})
        assert tts_types(fake).count("follow") == 1, "限频窗口内播报未发生，关注答谢照旧"
        assert "答谢标记感谢小明关注" in tts_contents(fake)

    def test_broadcast_off_thanks_unchanged(self, eh, fake):
        """播报开关关闭：关注答谢照旧、无播报。"""
        set_broadcast(fake, "follow", BC_FOLLOW, on=False)
        eh.follow_handle({"platform": "dy", "username": "小明"})
        assert tts_types(fake).count("follow") == 1
        assert "答谢标记感谢小明关注" in tts_contents(fake)
        assert not any("门控播报标记" in c for c in tts_contents(fake))

    def test_choreo_side_effect_unaffected_by_suppression(self, eh, fake, monkeypatch):
        """关注编排画面规则不受抑制影响：答谢被抑制时 on_follow 仍被调用。"""
        from utils import orchestration as orch
        choreo = MagicMock()
        monkeypatch.setattr(orch, "get_choreographer", lambda: choreo)
        set_broadcast(fake, "follow", BC_FOLLOW)
        eh.follow_handle({"platform": "dy", "username": "小明"})
        choreo.on_follow.assert_called_once()  # 编排画面规则不受抑制影响
        assert "follow" not in tts_types(fake), "仅抑制答谢语音"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
