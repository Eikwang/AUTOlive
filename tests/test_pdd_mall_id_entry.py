# -*- coding: UTF-8 -*-
"""守卫：拼多多 mall_id 接入 + 配置期链接归一落库（2026-11-12）

背景（用户实机测试反馈）：1688/美团/拼多多三个链接型添加平台全部失败——
- 1688：添加成功但监听失败（config.rooms 原文链接落库 → 前端轮询拼 REST
  路径被 / ? 打散 404；运行期 bridge 归一存在但落库路径未对齐）；
- 美团/拼多多：200 字符检查在链接解析之前，长分享链接直接被拒；
- 拼多多：extract_room_id 对链接 spec[:80] 截断（半成品），纯数字无 goto。

用户实测（2026-11-12）：拼多多直播间经 mall_id 进入——
https://mobile.yangkeduo.com/transac_virtual_card_pwd.html?page_from=601129&mall_id=378420135
「只需要替换 mall_id 即可进入直播间」。

守卫：
- pdd 提取器：mall_id 链接/纯数字/无 mall_id 链接报错（行为级）；
- pdd normalize_room_id 覆写（bridge.add_room 链接形态归一入口——注册表
  room_id 永不含 ://）+ URL 模板形良（行为级）；
- 前端 _validate_room 归一化落库：pdd 链接归一、200 检查在归一后、平台
  误选报错、未识别链接 confirm 兜底（行为级）+ 落库用归一 id（源码级）。
"""
import asyncio
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

#: 用户 2026-11-12 实测链接（样本原样）
USER_LINK = ("https://mobile.yangkeduo.com/transac_virtual_card_pwd.html"
             "?page_from=601129&mall_id=378420135")
MALL_ID = "378420135"


def _src(rel: str) -> str:
    return (REPO / rel).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. pdd 提取器（行为级）
# ---------------------------------------------------------------------------

class TestPddMallIdExtraction:
    def test_user_reported_link_extracts_mall_id(self):
        from danmaku_listener.engines.protocol.pdd import extract_room_id
        assert extract_room_id(USER_LINK) == MALL_ID

    def test_link_with_extra_noise_still_extracts(self):
        from danmaku_listener.engines.protocol.pdd import extract_room_id
        noisy = USER_LINK + "&utm_source=share&t=" + "x" * 250
        assert extract_room_id(noisy) == MALL_ID

    def test_link_without_mall_id_raises(self):
        from danmaku_listener.engines.protocol.pdd import extract_room_id, PDDParseError
        with pytest.raises(PDDParseError):
            extract_room_id("https://mobile.yangkeduo.com/goods.html?goods_id=1")

    def test_pure_digits_passthrough(self):
        from danmaku_listener.engines.protocol.pdd import extract_room_id
        assert extract_room_id(MALL_ID) == MALL_ID

    def test_non_link_non_digit_raises(self):
        from danmaku_listener.engines.protocol.pdd import extract_room_id, PDDParseError
        with pytest.raises(PDDParseError):
            extract_room_id("随便填的口令文本")

    def test_normalized_id_is_rest_path_safe(self):
        """归一 id 必须无 :// 与斜杠（base.py:90 REST 路径铁律）"""
        from danmaku_listener.engines.protocol.pdd import extract_room_id
        rid = extract_room_id(USER_LINK)
        assert "://" not in rid and "/" not in rid and "?" not in rid


class TestPddNormalizeAndTemplate:
    def test_engine_normalize_room_id(self):
        """bridge.add_room 链接形态入口（extract_from_link 无 pdd 模式 →
        engine.normalize_room_id）——覆写必须存在且归一到 mall_id"""
        from danmaku_listener.engines.registry import build_engine
        engine = build_engine("pdd")
        rid = asyncio.run(engine.normalize_room_id(USER_LINK))
        assert rid == MALL_ID

    def test_template_wellformed(self):
        from danmaku_listener.engines.protocol.pdd import PDD_LIVE_URL_TEMPLATE
        url = PDD_LIVE_URL_TEMPLATE.format(mall_id=MALL_ID)
        assert url.startswith("https://mobile.yangkeduo.com/")
        assert f"mall_id={MALL_ID}" in url

    def test_run_room_goto_uses_template(self):
        """纯数字 room_id 必须按 mall_id 模板拼 goto URL（原「无从打开」退役）"""
        src = _src("danmaku_listener/engines/protocol/pdd.py")
        assert "PDD_LIVE_URL_TEMPLATE.format(mall_id=raw)" in src


# ---------------------------------------------------------------------------
# 2. 前端归一化落库（行为级 + 源码级）
# ---------------------------------------------------------------------------

class TestFrontendNormalizeRoomSpec:
    def test_pdd_link_normalized(self):
        from frontend.ui.tabs.platform_config import _validate_room
        v = _validate_room("pdd", USER_LINK, [])
        assert v["ok"] is True
        assert v["normalized"] == MALL_ID

    def test_length_check_after_normalize(self):
        """超长链接归一为短 id 后必须放行（200 检查在归一后——根因 B 回归即红）"""
        from frontend.ui.tabs.platform_config import _validate_room
        long_link = ("https://mobile.yangkeduo.com/transac_virtual_card_pwd.html?foo="
                     + "x" * 300 + "&mall_id=378420135")
        v = _validate_room("pdd", long_link, [])
        assert v["ok"] is True
        assert v["normalized"] == MALL_ID

    def test_pdd_link_without_mall_id_hard_error(self):
        from frontend.ui.tabs.platform_config import _validate_room
        v = _validate_room("pdd", "https://mobile.yangkeduo.com/goods.html?id=1", [])
        assert v["ok"] is False

    def test_platform_mismatch_error(self):
        """1688 链接选了 pdd → 明确报错（不静默原文入库）"""
        from frontend.ui.tabs.platform_config import _validate_room
        v = _validate_room("pdd", "https://live.1688.com/zb/play.html?feedId=3346037815268109", [])
        assert v["ok"] is False
        assert "1688" in v["error"]

    def test_1688_link_normalized_via_parser(self):
        """六平台静态正则（含 1688 feedId）——1688 链接归一落库（本轮顺带修复）"""
        from frontend.ui.tabs.platform_config import _validate_room
        v = _validate_room("1688", "https://live.1688.com/zb/play.html?spm=x&feedId=3346037815268109", [])
        assert v["ok"] is True
        assert v["normalized"] == "3346037815268109"

    def test_unrecognized_link_other_platform_keeps_confirm(self):
        """无提取器的平台（如 kuaishou）未识别链接维持 confirm 兜底（向后兼容）"""
        from frontend.ui.tabs.platform_config import _validate_room
        v = _validate_room("kuaishou", "https://example.com/live/abc", [])
        assert v["ok"] is True
        assert v["confirm"] and v["normalized"] is None

    def test_add_room_lands_normalized_id(self):
        """落库必须用归一 id（源码级——config.rooms 原文链接落库回归即红）"""
        src = _src("frontend/ui/tabs/platform_config.py")
        assert 'room_id = v.get("normalized") or spec' in src
        assert '"room_id": room_id' in src
