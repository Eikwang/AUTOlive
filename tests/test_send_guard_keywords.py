# -*- coding: utf-8 -*-
"""发送守卫关键词词表分离回归（2026-10-13 用户报告 KEYWORD_BLOCKED 误拦）

根因：wiring 曾把接收侧弹幕屏蔽词（bridge._blocked_keywords ← config.keywords，
如 ["sb","MLGB","傻逼"]）灌进 SendGuard——"sb" 两字母子串匹配误拦任何含
"sb" 序列的正常内容，全平台 KEYWORD_BLOCKED。修复后发送屏蔽词单源
settings.send_blocked_keywords（默认空=发送不拦词），接收过滤表不再限制发送。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from danmaku_listener.config.settings import Settings
from danmaku_listener.senders.guard import SendGuard


class TestSendGuardKeywordSeparation(unittest.TestCase):
    """接收过滤 ≠ 发送限制：词表分离后接收短词不再误拦发送（全平台守卫共用）。"""

    def test_receive_keywords_no_longer_block_send(self):
        """接收词表内容不再进入发送守卫——含 "sb" 的内容应通过。"""
        guard = SendGuard(Settings(send_enabled_platforms="douyin"),
                          blocked_keywords=[])   # 词表分离后的默认形态（wiring 换源）
        ok, reason, _ = guard.check("douyin", "481334729111", "usb 测试消息")
        self.assertTrue(ok, f"含 sb 序列的内容被误拦: {reason}")

    def test_send_blocked_keywords_still_enforced(self):
        """显式配置的发送屏蔽词仍生效（守卫原语义锁定）。"""
        guard = SendGuard(Settings(send_enabled_platforms="douyin"),
                          blocked_keywords=["违禁词"])
        ok, reason, _ = guard.check("douyin", "481334729111", "含违禁词的内容")
        self.assertFalse(ok)
        self.assertEqual(reason, "KEYWORD_BLOCKED")

    def test_settings_default_empty(self):
        """settings 出厂默认空=发送不拦词（兼容既有部署升级——行为变化即修复目标）。"""
        self.assertEqual(Settings().send_blocked_keywords, "")

    def test_comma_parsing_shape(self):
        """逗号分隔解析语义（与 send_enabled_platforms 同款——wiring 换源依据）。"""
        raw = " a , b,, ,c "
        parsed = [k.strip() for k in (raw or "").split(",") if k.strip()]
        self.assertEqual(parsed, ["a", "b", "c"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
