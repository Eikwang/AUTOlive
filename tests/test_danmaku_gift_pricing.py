# -*- coding: UTF-8 -*-
"""礼物计价契约测试（autoplan 2026-10-22 批次 A2/A3/A4/A5 义务）。

契约：gift_value = 单价(元) × payload.gift_count（字段原值；平台别名表——
抖音=totalCount 累计 / B站=num 本条 / 快手=batchSize×comboCount / 斗鱼=gn /
虎牙=item_count / 小红书=count / 视频号=combo_product_count）。消费者按累计
差值去重入账（首见视为 1、重复丢弃），total_price = 单价 × 增量。

no_claim=真实直播间连击语义（mock/手工编码载荷；soak TODO 见 TODOS.md）。
"""
import base64
import unittest
from types import SimpleNamespace
from unittest import mock

sys_path = r"D:\AI\AUTOlive"
if sys_path not in __import__("sys").path:
    __import__("sys").path.insert(0, sys_path)

from danmaku_listener.engines.protocol import bilibili_codec, douyu_codec, huya_codec
from danmaku_listener.engines.protocol import kuaishou_codec, xiaohongshu
from danmaku_listener.engines.protocol import bili_gifts, douyin_gifts, douyu_gifts
from danmaku_listener.engines.protocol import huya_gifts, kuaishou_gifts
from danmaku_listener.engines.protocol import xhs_gift_prices, wxsp_gift_prices
from danmaku_listener.engines.protocol.douyin_assets import douyin_pb2 as dy_pb2
from danmaku_listener.engines.protocol import ks_pb2
from danmaku_listener.engines import wechat_channels
import utils.danmaku_consumer as dc


def _varint(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)


class TestTableAnchors(unittest.TestCase):
    """A3：元口径公开锚点（每平台≥1，公开可查价格）。"""

    def test_bili_anchor(self):
        self.assertEqual(bili_gifts.lookup_gift(31036), ("小花花", 0.1))
        self.assertEqual(bili_gifts.lookup_gift(34636), ("舰长一号", 198))

    def test_douyin_anchor(self):
        name, price = douyin_gifts.lookup_gift(463)  # 小心心 0.1 元
        self.assertEqual(price, 0.1)

    def test_douyu_anchor(self):
        self.assertEqual(douyu_gifts.lookup_gift(20002), ("办卡", 6))

    def test_huya_anchor(self):
        name, price = huya_gifts.lookup_gift(20114)  # 小月亮 0.1 元
        self.assertEqual(price, 0.1)

    def test_kuaishou_anchor(self):
        name, price = kuaishou_gifts.lookup_gift(9)  # 啤酒 1 元
        self.assertEqual(price, 1)

    def test_xhs_anchor(self):
        self.assertEqual(xhs_gift_prices.lookup_price("人气票"), 0.1)

    def test_wxsp_anchor(self):
        self.assertEqual(wxsp_gift_prices.lookup_price("小星星"), 0.1)

    def test_table_sizes(self):
        """转录完整性：各表条数与 2026-10-22 文档一致（69/102/79/189/95/115/69）。"""
        self.assertEqual(len(bili_gifts.BILI_GIFT_MAP), 69)
        self.assertEqual(len(douyin_gifts.DY_GIFT_MAP), 102)
        self.assertEqual(len(douyu_gifts.DOUYU_GIFT_MAP), 79)
        self.assertEqual(len(huya_gifts.HUYA_GIFT_MAP), 189)
        self.assertEqual(len(kuaishou_gifts.KS_GIFT_MAP), 95)
        self.assertEqual(len(xhs_gift_prices.XHS_GIFT_PRICE), 115)
        self.assertEqual(len(wxsp_gift_prices.WXSP_GIFT_PRICE), 69)


class TestBilibiliPricing(unittest.TestCase):
    def test_protocol_price_times_num_is_total_yuan(self):
        """协议路径：price(金瓜子)×0.001×num=元总价（A4 修复：此前缺 ×num）。"""
        mapped = bilibili_codec._map_gift(
            {"giftId": 31036, "giftName": "小花花", "price": 100, "num": 3,
             "uname": "u1", "uid": 1}, 1, 2)
        self.assertIsNotNone(mapped)
        self.assertEqual(mapped["payload"]["gift_count"], 3)
        self.assertAlmostEqual(mapped["payload"]["gift_value"], 0.1 * 3, places=6)

    def test_fallback_table_price_times_num(self):
        mapped = bilibili_codec._map_gift(
            {"giftId": 31036, "giftName": "", "num": 2, "uname": "u1"}, 1, 2)
        self.assertAlmostEqual(mapped["payload"]["gift_value"], 0.1 * 2, places=6)

    def test_v2_backfill_from_table(self):
        """V2 pb 恒 0 修复：gift_id 查元表回填（A4）。"""
        sub = (b"\x08" + _varint(31036)
               + b"\x12" + _varint(9) + "小花花".encode("utf-8")
               + b"\x18" + _varint(3))
        top = (b"\x12" + _varint(len("主播".encode("utf-8"))) + "主播".encode("utf-8")
               + b"\x52" + _varint(len(sub)) + sub)
        mapped = bilibili_codec._map_send_gift_v2(base64.b64encode(top).decode(), 1, 2)
        self.assertIsNotNone(mapped)
        self.assertEqual(mapped["payload"]["gift_name"], "小花花")
        self.assertEqual(mapped["payload"]["gift_count"], 3)
        self.assertAlmostEqual(mapped["payload"]["gift_value"], 0.3, places=6)


class TestDouyinPricing(unittest.TestCase):
    def test_totalcount_alias_yuan(self):
        g = dy_pb2.GiftMessage()
        g.giftId = 463
        g.gift.name = "小心心"
        g.user.id = 7
        g.user.nickName = "u1"
        g.totalCount = 10
        msg = SimpleNamespace(method="WebcastGiftMessage", payload=g.SerializeToString())
        mapped = None
        try:
            mapped = __import__(
                "danmaku_listener.engines.protocol.douyin", fromlist=["x"]
            ).DouyinWebProtocolEngine._map_message(None, "room1", msg, 1, 2)
        except Exception:
            mapped = None
        self.assertIsNotNone(mapped)
        self.assertEqual(mapped["payload"]["gift_count"], 10)  # 累计别名透传
        self.assertAlmostEqual(mapped["payload"]["gift_value"], 0.1 * 10, places=6)


class TestDouyuPricing(unittest.TestCase):
    def test_gn_count_yuan(self):
        mapped = douyu_codec.map_upstream(
            {"type": "dgb", "gs": "", "gfid": "20002", "nn": "u1", "uid": "9",
             "gn": "2"}, 1, 2)
        self.assertIsNotNone(mapped)
        self.assertEqual(mapped["payload"]["gift_count"], 2)
        self.assertEqual(mapped["payload"]["gift_value"], 6 * 2)


class TestKuaishouPricing(unittest.TestCase):
    def test_batch_combo_yuan(self):
        push = ks_pb2.SCWebFeedPush()
        feed = push.giftFeeds.add()
        feed.giftId = 9  # 啤酒 1 元
        feed.batchSize = 2
        feed.comboCount = 3
        feed.user.userName = "u1"
        feed.user.principalId = "7"
        data = push.SerializeToString()
        results = kuaishou_codec.map_feed_push(data, 1, 2)
        gift = [r for r in results if r.get("type") == "GIFT"]
        self.assertTrue(gift)
        self.assertEqual(gift[0]["payload"]["gift_count"], 6)  # batchSize×comboCount
        self.assertAlmostEqual(gift[0]["payload"]["gift_value"], 1 * 6, places=6)


class TestXiaohongshuPricing(unittest.TestCase):
    def test_table_primary_coins_fallback_discounted(self):
        """元表权威；coins 仅回退并折元（A4：原 coins 直透薯币低 10 倍）。"""
        cd_table = {"type": "gift_dock_and_effect",
                    "send_user_info": {"id": "s1", "nick_name": "u1"},
                    "base_gift_info": {"name": "人气票"},
                    "gift_action_info": {"count": 3}}
        mapped = xiaohongshu.map_custom_data(cd_table, 1, 2)
        self.assertAlmostEqual(mapped["payload"]["gift_value"], 0.1 * 3, places=6)

        cd_coins = {"type": "gift_dock_and_effect",
                    "send_user_info": {"id": "s1", "nick_name": "u1"},
                    "base_gift_info": {"name": "未知新礼物xyz", "coins": 5},
                    "gift_action_info": {"count": 2}}
        mapped2 = xiaohongshu.map_custom_data(cd_coins, 3, 4)
        self.assertAlmostEqual(mapped2["payload"]["gift_value"], 0.1 * 5 * 2, places=6)


class TestHuyaPricing(unittest.TestCase):
    def test_online_path_backfill_and_static_multiply(self):
        """在线路径回填单价×item_count + 静态路径补乘（A4：原两路径恒 0/缺乘）。"""
        payload = b""
        with mock.patch.object(huya_codec, "decode_send_item",
                               return_value={"item_type": 20114, "item_count": 3,
                                             "sender_nick": "u1", "sender_uid": "9"}):
            mapped_offline = huya_codec.map_upstream(
                payload, huya_codec.URI_SEND_ITEM_SUB_BROADCAST, 1, 2, gift_items={})
            mapped_online = huya_codec.map_upstream(
                payload, huya_codec.URI_SEND_ITEM_SUB_BROADCAST, 3, 4,
                gift_items={20114: "小月亮"})
        self.assertEqual(mapped_offline["payload"]["gift_name"], "小月亮")
        self.assertEqual(mapped_offline["payload"]["gift_count"], 3)
        self.assertAlmostEqual(mapped_offline["payload"]["gift_value"], 0.1 * 3, places=6)
        self.assertEqual(mapped_online["payload"]["gift_name"], "小月亮")
        self.assertAlmostEqual(mapped_online["payload"]["gift_value"], 0.1 * 3, places=6)


class TestWxspPricing(unittest.TestCase):
    """R2-P1：主路径 wecoin 直透豆值低 10 倍——×WECOIN_TO_YUAN 折元。"""

    def _run(self, gift_payload):
        calls = []

        async def _capture(room_id, ctype, payload, msg_id=None, ts=None):
            calls.append((ctype, payload))

        fake = SimpleNamespace(
            _is_feed_api=lambda url: True,
            _raw_hook=None,
            _last_live_status={}, _last_stats_vals={}, _msglist_types={},
            platform="wechat_channels", engine_id="wxsp-test",
            next_seq=lambda rid: 1,
            _emit_message=mock.AsyncMock(),
            _emit_business=_capture,
            _decode_gift_payload=lambda raw: gift_payload,
        )
        import asyncio
        body = {"data": {"appMsgList": [{
            "msgType": wechat_channels.MSGTYPE_GIFT,
            "payload": "x",
            "seq": 1,
            "fromUserContact": {"contact": {"nickname": "u1", "username": "uid1"}},
        }]}}
        resp = SimpleNamespace(url="feed", **{"json": mock.AsyncMock(return_value=body)})
        asyncio.run(wechat_channels.WechatChannelsEngine._on_response(
            fake, "room1", resp))
        gifts = [p for t, p in calls if t == "GIFT"]
        self.assertTrue(gifts)
        return gifts[0]

    def test_wecoin_main_path_discounted(self):
        payload = self._run({"content": "u2送了主播3个小星星",
                             "combo_product_count": 3,
                             "reward_amount_in_wecoin": 3})
        self.assertEqual(payload["gift_name"], "小星星")
        self.assertEqual(payload["gift_count"], 3)
        self.assertAlmostEqual(payload["gift_value"], 0.1 * 3, places=6)

    def test_table_fallback(self):
        payload = self._run({"content": "u2送了主播2个小星星", "reward_product_count": 2})
        self.assertEqual(payload["gift_name"], "小星星")
        self.assertAlmostEqual(payload["gift_value"], 0.1 * 2, places=6)


class TestConsumerGiftContract(unittest.TestCase):
    """A2/A5：消费者零改动恒正确——乘数≡gift_count 别名，combo 字段不做乘数。"""

    def test_dispatch_yuan_contract(self):
        svc = dc.DanmakuListenerService(lambda: {})
        captured = []

        class _Handle:
            def process_data(self, data, dtype):
                captured.append((dtype, data))

        import utils.my_global as _mg
        with mock.patch.object(_mg, "my_handle", _Handle(), create=True):
            base = {"user_name": "u1", "user_id": "u1", "gift_name": "小花花",
                    "gift_id": "31036", "combo_count": 99}
            # 首见累计 totalCount=3 → 增量 1；gift_value=单价×gift_count=0.3
            svc._dispatch("GIFT", {"payload": {**base, "gift_count": 3,
                                               "gift_value": 0.3}})
            dtype, data = captured[0]
            self.assertEqual(dtype, "gift")
            self.assertAlmostEqual(data["unit_price"], 0.1, places=6)  # per=price/count
            self.assertAlmostEqual(data["total_price"], 0.1, places=6)  # 单价×增量1
            # 累计 10 → 增量 7 → total=0.1×7（combo_count=99 不参与）
            svc._dispatch("GIFT", {"payload": {**base, "gift_count": 10,
                                               "gift_value": 1.0}})
            dtype, data = captured[1]
            self.assertAlmostEqual(data["total_price"], 0.1 * 7, places=6)


if __name__ == "__main__":
    unittest.main()
