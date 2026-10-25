# -*- coding: UTF-8 -*-
"""
T6 答谢生产端恢复回归测试（/autoplan 批次 main-20261020-180346 断链修复）

覆盖：
1. gift/entrance/follow 三类答谢生产端恢复（event_handler → 播放链/周期缓存）
2. 最低价格门槛（thanks.lowest_price）
3. E-2 限流：即时通道每分钟上限 + 百连入场不压垮播放队列
4. 周期触发通道：缓存收数据不即时合成
5. 顺序/随机文案选择
6. 变量替换（真 Common 方法：brackets/dynamic_variable）
7. 答谢链异常不中断主流程
"""

import time
from unittest.mock import MagicMock

import pytest

from utils.handlers.event_handler import EventHandler
from utils.common_original import Common


def make_config(overrides=None):
    base = {
        "thanks": {
            "gift_enable": True,
            "gift_random": False,
            "gift_copy": ["谢谢 {username} 的 {gift_name}！", "感谢 {username}！"],
            "entrance_enable": True,
            "entrance_random": False,
            "entrance_copy": ["欢迎 {username} 进入直播间！"],
            "follow_enable": True,
            "follow_random": True,
            "follow_copy": ["感谢 {username} 关注！"],
            "username_max_len": 10,
            "lowest_price": 0.1,
            "rate_limit_per_min": 10,
            "gift": {"periodic_trigger": {"enable": False}},
            "entrance": {"periodic_trigger": {"enable": False}},
            "follow": {"periodic_trigger": {"enable": False}},
        },
        "audio_synthesis_type": "none",
        "filter": {},
        "database": {"gift_enable": False, "entrance_enable": False},
        "none": {},
    }
    if overrides:
        for k, v in overrides.items():
            if isinstance(v, dict) and isinstance(base.get(k), dict):
                base[k].update(v)
            else:
                base[k] = v
    cfg = MagicMock()
    cfg._cfg = base
    cfg.get = MagicMock(side_effect=lambda *keys: _get(base, keys))
    return cfg


def _get(node, keys):
    cur = node
    for k in keys:
        if not isinstance(cur, dict) or k not in cur:
            return None
        cur = cur[k]
    return cur


@pytest.fixture
def handler():
    mh = MagicMock()
    mh.config = make_config()
    mh.common = Common()
    mh.db = MagicMock()
    mh.integral_handle.return_value = False
    # 2026-10-25 识别中枢批次：gift 链先经 keyword_recognize 产出真实空结果
    # （MagicMock 默认返回真值 MagicMock，会被误判为编排接管/礼物命中而短路答谢）
    from utils.keyword_hub import KeywordResult
    mh.keyword_recognize.return_value = KeywordResult(source="gift")
    # task_data 用真实嵌套结构（周期缓存 append 需要 len 断言）
    mh.task_data = {
        "thanks": {
            t: {"data": [], "time": 0} for t in ("gift", "entrance", "follow")
        },
    }
    eh = EventHandler(mh)
    return eh, mh


class TestThanksProduce:
    def test_gift_thanks_produced_with_variables(self, handler):
        """礼物答谢：文案经真 Common 变量替换后进入播放链。"""
        eh, mh = handler
        msg = eh.gift_handle({
            "username": "小王", "gift_name": "火箭",
            "gift_num": 1, "unit_price": 1, "total_price": 100,
        })
        mh.audio_synthesis_handle.assert_called_once()
        sent = mh.audio_synthesis_handle.call_args[0][0]
        assert sent["content"] in ("谢谢 小王 的 火箭！", "感谢 小王！"), sent
        assert sent["type"] == "gift"

    def test_gift_below_lowest_price_not_thanked(self, handler):
        """最低价格门槛：total_price < lowest_price 不答谢。"""
        eh, mh = handler
        eh.gift_handle({
            "username": "小王", "gift_name": "小心心",
            "gift_num": 1, "unit_price": 0.01, "total_price": 0.01,
        })
        mh.audio_synthesis_handle.assert_not_called()

    def test_entrance_and_follow_thanks(self, handler):
        eh, mh = handler
        eh.entrance_handle({"username": "路人甲"})
        eh.follow_handle({"username": "路人乙"})
        assert mh.audio_synthesis_handle.call_count == 2
        contents = [c[0][0]["content"] for c in mh.audio_synthesis_handle.call_args_list]
        assert "欢迎 路人甲 进入直播间！" in contents
        assert any("路人乙" in c for c in contents)

    def test_sequential_copy_cursor(self, handler):
        """random=false 顺序取文案：两次 gift 取不同文案（游标推进）。"""
        eh, mh = handler
        for i in range(2):
            eh.gift_handle({
                "username": f"u{i}", "gift_name": "礼物",
                "gift_num": 1, "unit_price": 1, "total_price": 10,
            })
        contents = [c[0][0]["content"] for c in mh.audio_synthesis_handle.call_args_list]
        assert contents[0] == "谢谢 u0 的 礼物！"
        assert contents[1] == "感谢 u1！"


class TestRateLimit:
    def test_hundred_entrances_capped(self, handler):
        """E-2 百连入场：即时通道限 10 条/分钟，其余丢弃且不异常。"""
        eh, mh = handler
        for i in range(100):
            eh.entrance_handle({"username": f"观众{i}"})
        assert mh.audio_synthesis_handle.call_count == 10

    def test_window_slides_after_60s(self, handler):
        """滑动窗口：60s 前的记录过期，后续答谢恢复。"""
        eh, mh = handler
        for i in range(10):
            eh.entrance_handle({"username": f"a{i}"})
        assert mh.audio_synthesis_handle.call_count == 10
        # 人工老化窗口内全部记录
        eh._instant_thanks_window.clear()
        eh._instant_thanks_window.append(time.time() - 61)
        eh.entrance_handle({"username": "新观众"})
        assert mh.audio_synthesis_handle.call_count == 11

    def test_periodic_channel_buffers_instead_of_speaking(self, handler):
        """周期触发开启：入缓存不即时合成。"""
        eh, mh = handler
        mh.config._cfg["thanks"]["entrance"]["periodic_trigger"]["enable"] = True
        for i in range(30):
            eh.entrance_handle({"username": f"观众{i}"})
        mh.audio_synthesis_handle.assert_not_called()
        assert len(mh.task_data["thanks"]["entrance"]["data"]) == 30


class TestRobustness:
    def test_thanks_failure_does_not_break_main_flow(self, handler):
        """答谢链异常（如文案空）不中断积分/写库等主流程。"""
        eh, mh = handler
        mh.config._cfg["thanks"]["gift_copy"] = []  # 文案空 → _thanks_copy 返回 ""
        # 主流程继续：integral 与 db 不受影响
        assert eh.gift_handle({"username": "u", "gift_name": "g", "gift_num": 1,
                               "unit_price": 1, "total_price": 10}) is None
        mh.integral_handle.assert_called_once()

    def test_gift_disabled_no_thanks(self, handler):
        eh, mh = handler
        mh.config._cfg["thanks"]["gift_enable"] = False
        eh.gift_handle({"username": "u", "gift_name": "g", "gift_num": 1,
                        "unit_price": 1, "total_price": 10})
        mh.audio_synthesis_handle.assert_not_called()
