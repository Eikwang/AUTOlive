# -*- coding: UTF-8 -*-
"""
积分获得播报统一门守卫测试（2026-11-19 播报开关统管修复批次；2026-11-19 积分记录
前移批次、播报文案收敛批次更新）

统一语义（用户裁决）：四个积分 handler 收敛为记分段（enable/身份/档位/写库/限频），
播报统一经 _integral_gain_announce 统一门（ctx 版）：
- 开启=播报一次积分获得文案（档位文案优先：sign/gift/entrance 走 ctx.blocks 档位
  选择；follow 走 ctx.copy_list 平铺文案；缺失时内置默认文案兜底——2026-11-19
  播报文案收敛批次：播报文案独立配置项退役）
- 关闭=零积分播报（档位回复一并停播），答谢照旧

2026-11-19 积分记录前移批次架构：记分/播报分离——积分链不再直接播报，播报由
限入后挂载点消费 IntegralScoreContext 触发（sign=integral_sign_consume 终结判定+
播报；gift/entrance/follow=integral_announce_if_scored 播报+答谢抑制判定）。
原 _integral_broadcast_fired 单事件标志被 ctx 播报返回值取代（挂载点在同一事件
内先播报后判定抑制，无跨事件竞态）。

覆盖义务（任务书）：
- 统一门三态 × 每模块（sign/gift/entrance）：
  1. broadcast 关 + 档位文案已配置 → 档位回复被抑制（零积分播报）+ 挂载点零播报
     （答谢照旧）
  2. broadcast 开 + 档位文案配置 → 档位文案播报一次（内置默认文案不覆盖——档位
     文案优先）
  3. broadcast 开 + 档位文案缺失（blocks 无 copywriting）→ 内置默认文案兜底播报
- follow 模块：copywriting（平铺文案）收敛纳入统一门——平铺文案优先/内置默认文案
  兜底/开关门控
- 调用点源级守卫：四个记分 handler 纯记分段（不直接播报），播报调用点唯一收敛在
  统一门（由挂载点消费方法触发）
- 播报文案配置退役守卫（2026-11-19 播报文案收敛批次）：后端不读取该配置键、
  前端不渲染「播报文案」输入框、示例配置不携带该键
- 写库失败/限频拦截 → 零播报（继承既有语义）

纪律：pytest tmp_path 独立测试 DB 文件（同 test_integral_broadcast_20261117.py）。
"""

import inspect
import re
import sys
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

# 内置默认文案兜底播报（档位块无 copywriting）的期望内容
# （feature_handler._INTEGRAL_BROADCAST_DEFAULT_COPY 渲染形态；2026-11-19 播报
# 文案收敛批次起为统一门唯一兜底文案源）
FALLBACK_TEXT = {
    "sign": "感谢 小明 签到，积分+1",
    "gift": "小明 的礼物已记入积分，当前 10 分",
    "entrance": "欢迎 小明 回来，积分+1",
    "follow": "感谢 小明 关注，积分+1",
}


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


# 三个统管模块的事件入口（sign 经 comment 型）与数据（每用例独立 dict——
# 2026-11-19 积分记录前移批次：integral_handle 会往 data 挂 IntegralScoreContext）
EVENT_TYPE = {"sign": "comment", "gift": "gift", "entrance": "entrance"}
EVENT_DATA_TPL = {
    "sign": {"platform": "dy", "username": "小明", "content": "签到"},
    "gift": {"platform": "dy", "username": "小明", "gift_name": "小花", "total_price": 1.0},
    "entrance": {"platform": "dy", "username": "小明"},
}


def event_data(module):
    """独立事件 dict（避免跨用例共享挂载 ctx）。"""
    return dict(EVENT_DATA_TPL[module])


def score_and_announce(handler, module, data):
    """2026-11-19 积分记录前移批次：记分/播报两段式驱动——记分（integral_handle
    同步直调形态挂 IntegralScoreContext）后由限入后挂载点消费 ctx 播报。

    Returns:
        (scored, announced): 记分返回值（sign/gift/entrance 均为记分成功或限频
        拦截 True）与挂载点播报结果（sign=终结判定 consume；gift/entrance=播报
        实际发生）。
    """
    scored = handler.integral_handle(EVENT_TYPE[module], data)
    announced = (handler.integral_sign_consume(data) if module == "sign"
                 else handler.integral_announce_if_scored(data, module))
    return scored, announced


# 各模块本次累计积分（写库断言用）
SCORED = {"sign": 1, "gift": 10, "entrance": 1}
# 档位文案（broadcast 开 + 档位文案配置 → 档位路径播报的期望内容）
TIER_TEXT = {
    "sign": "小明打卡成功，获得1点积分",
    "gift": "感谢小明的小花，获得10点积分",
    "entrance": "欢迎小明，获得1点积分",
}
# 各模块档位区间键（构造无 copywriting 的档位块用）
INTERVAL_KEY = {"sign": "sign_num_interval", "gift": "gift_price_interval",
                "entrance": "entrance_num_interval"}


def tts_contents(fake):
    """取 TTS 消息（type=comment）的回复内容列表（webui 日志不入）。"""
    return [r.get("content") for r in fake.replies
            if isinstance(r, dict) and r.get("type") == "comment"]


@pytest.fixture
def db(tmp_path):
    """新装形态测试库。"""
    database = SQLiteDB(str(tmp_path / "integral_gain_gate.db"))
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


# ============ 门态 1：broadcast 关 + 档位文案已配置 → 档位回复被抑制 ============

class TestGateClosedZeroBroadcast:
    """关闭=零积分播报（档位回复一并停播），答谢照旧（挂载点零播报）。"""

    @pytest.mark.parametrize("module", ["sign", "gift", "entrance"])
    def test_broadcast_off_tier_reply_suppressed_no_announce(self, handler, fake, db, module):
        """broadcast 关 + 档位文案已配置：档位回复被抑制（零积分播报）+ 挂载点
        零播报（2026-11-19 积分记录前移批次：fired 标志由挂载点返回值取代）。"""
        cfg = base_config()
        cfg["integral"][module].update({"broadcast": False})
        handler.my_handle.config = _StubConfig(cfg)

        data = event_data(module)
        scored, announced = score_and_announce(handler, module, data)
        assert scored is True, "计分成功（sign 形态=终结判定 True）"
        assert announced is (module == "sign"), \
            "sign=计分成功终结判定 True；gift/entrance=零播报（答谢照旧）"
        assert db.fetch_one("SELECT integral FROM integral") == (SCORED[module],), \
            "积分写库照常（仅播报被抑制）"
        assert tts_contents(fake) == [], "档位回复一并停播：零积分播报"


# ============ 门态 2：broadcast 开 + 档位文案配置 → 档位文案播报一次 ============

class TestGateOpenTierPriority:
    """开启=档位文案优先播报一次；内置默认文案不覆盖档位文案。"""

    @pytest.mark.parametrize("module", ["sign", "gift", "entrance"])
    def test_broadcast_on_tier_copy_broadcasts_once(self, handler, fake, db, module):
        cfg = base_config()
        cfg["integral"][module].update({"broadcast": True})
        handler.my_handle.config = _StubConfig(cfg)

        data = event_data(module)
        scored, announced = score_and_announce(handler, module, data)
        assert scored is True
        assert announced is True, "挂载点消费：播报实际发生（答谢抑制判定依据）"
        assert tts_contents(fake) == [TIER_TEXT[module]], "档位文案播报一次（唯一积分获得播报）"


# ============ 门态 3：broadcast 开 + 档位文案缺失 → 内置默认文案兜底 ============

class TestGateOpenFallback:
    """档位文案缺失（blocks 无 copywriting）→ 内置默认文案兜底播报。"""

    @pytest.mark.parametrize("module", ["sign", "gift", "entrance"])
    def test_tier_copy_missing_falls_back_to_builtin(self, handler, fake, db, module):
        """档位块不配 copywriting：内置默认文案兜底播报一次。"""
        cfg = base_config()
        cfg["integral"][module].update({"broadcast": True})
        # 档位块无 copywriting → _integral_tier_reply 拿不到文案返回 False → 统一门走兜底
        cfg["integral"][module]["copywriting"] = [{INTERVAL_KEY[module]: "0-999999999"}]
        handler.my_handle.config = _StubConfig(cfg)

        data = event_data(module)
        scored, announced = score_and_announce(handler, module, data)
        assert scored is True
        assert announced is True
        assert tts_contents(fake) == [FALLBACK_TEXT[module]], "内置默认文案兜底播报一次"


# ============ follow 模块：档位文案收敛纳入统一门（档位文案优先/兜底/开关门控） ============

class TestFollowModuleUnifiedGate:
    def test_follow_broadcast_on_flat_copy_missing_falls_back_to_builtin(self, handler, fake, db):
        """follow broadcast 开 + copywriting 缺失：内置默认文案兜底播报（平铺
        文案优先链空）。"""
        cfg = base_config()
        cfg["integral"]["follow"].update({"broadcast": True})
        cfg["integral"]["follow"]["copywriting"] = []
        handler.my_handle.config = _StubConfig(cfg)

        data = {"platform": "dy", "username": "小明"}
        assert handler.integral_handle("follow", data) is True
        assert handler.integral_announce_if_scored(data, "follow") is True
        assert "感谢 小明 关注，积分+1" in tts_contents(fake)

    def test_follow_broadcast_on_tier_copy_priority(self, handler, fake, db):
        """follow broadcast 开 + copywriting 配置：平铺文案优先播报（内置默认
        文案不覆盖——与 sign/gift/entrance 统一门档位文案优先语义一致）。"""
        cfg = base_config()
        cfg["integral"]["follow"].update({"broadcast": True})
        handler.my_handle.config = _StubConfig(cfg)

        data = {"platform": "dy", "username": "小明"}
        assert handler.integral_handle("follow", data) is True
        assert handler.integral_announce_if_scored(data, "follow") is True
        assert "小明感谢关注，获得1点积分" in tts_contents(fake)

    def test_follow_broadcast_off_zero_broadcast(self, handler, fake, db):
        """follow broadcast 关：零积分播报（copywriting 一并停播——统一门收敛，
        原「档位回复不受开关门控」遗留语义移除）。"""
        cfg = base_config()
        cfg["integral"]["follow"].update({"broadcast": False})
        handler.my_handle.config = _StubConfig(cfg)

        data = {"platform": "dy", "username": "小明"}
        assert handler.integral_handle("follow", data) is True
        assert handler.integral_announce_if_scored(data, "follow") is False, "挂载点零播报（答谢照旧）"
        assert tts_contents(fake) == []


# ============ 调用点源级守卫：记分段纯化，播报唯一收敛在统一门 ============

class TestCallSiteSourceGuard:
    """2026-11-19 积分记录前移批次：四个记分 handler 纯记分段（不直接播报），
    播报调用点唯一收敛在 _integral_gain_announce（由挂载点消费方法触发）。"""

    @pytest.mark.parametrize("method", ["_integral_sign_handle", "_integral_gift_handle",
                                        "_integral_entrance_handle", "_integral_follow_handle"])
    def test_score_handlers_are_broadcast_free(self, method):
        """记分 handler 不得直接调播报链（统一门/档位回复/平铺回复/兜底播报）。"""
        src = inspect.getsource(getattr(FeatureHandler, method))
        for banned in ("self._integral_gain_announce(", "self._integral_tier_reply(",
                       "self._integral_format_and_reply(", "self._integral_broadcast("):
            assert banned not in src, f"{method} 记分段不得直接调 {banned}（播报收敛统一门）"

    def test_mount_points_route_through_unified_gate(self):
        """挂载点消费方法（integral_sign_consume / integral_announce_if_scored）
        必须经统一门 _integral_gain_announce 播报。"""
        for method in ("integral_sign_consume", "integral_announce_if_scored"):
            src = inspect.getsource(getattr(FeatureHandler, method))
            assert "self._integral_gain_announce(" in src, \
                f"{method} 必须经统一门 _integral_gain_announce 播报"

    def test_tier_reply_call_site_unique_in_unified_gate(self):
        """_integral_tier_reply 全源码唯一调用点位于 _integral_gain_announce 内（无旁路）。"""
        module_src = inspect.getsource(feature_handler_module)
        assert len(re.findall(r"self\._integral_tier_reply\(", module_src)) == 1, \
            "档位回复调用点必须唯一（收敛进统一门）"
        gate_src = inspect.getsource(FeatureHandler._integral_gain_announce)
        assert "self._integral_tier_reply(" in gate_src

    def test_broadcast_fired_flag_retired(self):
        """_integral_broadcast_fired/integral_broadcast_fired 已退役（挂载点播报
        返回值取代跨事件标志——同一事件内先播报后判定抑制，无竞态）。"""
        module_src = inspect.getsource(feature_handler_module)
        assert "integral_broadcast_fired" not in module_src, \
            "fired 标志已由 IntegralScoreContext 播报返回值取代，不得残留"


# ============ 播报文案配置退役守卫（2026-11-19 播报文案收敛批次） ============

class TestBroadcastCopyRetired:
    """2026-11-19 播报文案收敛批次：播报文案独立配置项退役——播报文案收敛为
    「文案」配置（档位回复/平铺文案，缺失时内置默认文案兜底），前端不再渲染
    「播报文案」输入框，后端不再读取该配置键，示例配置不再携带该键。"""

    def test_feature_handler_no_broadcast_copy_read(self):
        """feature_handler 全源码不得再读取/引用该配置键（兜底=内置默认文案）。"""
        module_src = inspect.getsource(feature_handler_module)
        assert "broadcast_copy" not in module_src, \
            "播报文案配置键已退役，feature_handler 不得残留读取/引用"

    def test_frontend_no_broadcast_copy_input(self):
        """integral.py 不再绑定/渲染「播报文案」输入框。"""
        tab_src = (Path(__file__).parent.parent / "frontend" / "ui" / "tabs"
                   / "integral.py").read_text(encoding="utf-8")
        assert "broadcast_copy" not in tab_src, "integral.py 不得再绑定播报文案配置键"
        assert "label='播报文案'" not in tab_src, "integral.py 不得再渲染「播报文案」输入框"

    def test_config_example_no_broadcast_copy(self):
        """config.example.json 积分四模块不再携带该配置键。"""
        example_src = (Path(__file__).parent.parent
                       / "config.example.json").read_text(encoding="utf-8")
        assert "broadcast_copy" not in example_src, \
            "config.example.json 不得再含播报文案配置键"


# ============ 写库失败/限频拦截 → 零播报（继承既有语义） ============

class TestZeroBroadcastOnBlockedOrFailed:
    """写库失败/限频拦截不进统一门播报路径（ctx 未记分）——挂载点零播报。"""

    @pytest.mark.parametrize("module", ["sign", "gift", "entrance"])
    def test_write_fail_zero_broadcast(self, handler, fake, db, module):
        """写库失败（重试耗尽）：记分返回 False，挂载点零播报。"""
        cfg = base_config()
        cfg["integral"][module].update({"broadcast": True})
        handler.my_handle.config = _StubConfig(cfg)
        handler.my_handle.db = _BrokenWriteDB(db)

        data = event_data(module)
        scored = handler.integral_handle(EVENT_TYPE[module], data)
        announced = (handler.integral_sign_consume(data) if module == "sign"
                     else handler.integral_announce_if_scored(data, module))
        assert scored is False, "写库失败=积分链未消费"
        assert announced is False, "写库失败零播报（挂载点按未记分处理）"
        assert fake.replies == []

    def test_sign_cooldown_blocked_zero_broadcast(self, handler, fake, db):
        """同日窗口内重复签到（2026-11-19 积分窗口语义批次：自然日固定窗口）：
        静默拦截（隐形语义），零播报零回复。"""
        cfg = base_config()
        cfg["integral"]["sign"].update({"broadcast": True})
        handler.my_handle.config = _StubConfig(cfg)

        data = event_data("sign")
        assert handler.integral_handle("comment", data) is True
        assert handler.integral_sign_consume(data) is True
        assert tts_contents(fake) == [TIER_TEXT["sign"]], "对照：首次签到档位文案播报"
        fake.replies.clear()
        blocked = event_data("sign")
        assert handler.integral_handle("comment", blocked) is True
        assert handler.integral_sign_consume(blocked) is True, "限频拦截=消费弹幕（终结）"
        assert fake.replies == [], "限频拦截零播报零回复"

    def test_entrance_cooldown_blocked_no_announce(self, handler, fake, db):
        """同日窗口内重复入场：静默拦截零播报，挂载点按未记分处理（答谢照旧）——
        ctx 每事件独立，无跨事件标志残留。"""
        cfg = base_config()
        cfg["integral"]["entrance"].update({"broadcast": True})
        handler.my_handle.config = _StubConfig(cfg)

        data = event_data("entrance")
        assert handler.integral_handle("entrance", data) is True
        assert handler.integral_announce_if_scored(data, "entrance") is True, "对照：首次入场播报实际发生"
        fake.replies.clear()
        blocked = event_data("entrance")
        assert handler.integral_handle("entrance", blocked) is True
        assert fake.replies == [], "限频拦截零播报"
        assert handler.integral_announce_if_scored(blocked, "entrance") is False, "未记分零播报（答谢照旧）"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
