# -*- coding: utf-8 -*-
"""2026-11-17 热切机制批次守卫测试

覆盖三面新增热切行为：
1. 二级菜单开关热切（layout.py HOT_APPLY_PATHS）：danmaku_listener.enabled 开关
   写盘成功后调用 /danmaku/service/toggle 运行面切换；main 不可达回退重启生效
   （静默、无异常）；choreography.enable 走 /choreography/rules 整节热应用；
   关闭二次确认注册表同步（danmaku 关闭须确认）。
2. 黑名单规范化集合签名缓存（feature_handler.py）：实时读语义不变（改名单即
   生效），签名命中复用缓存对象、变更重建。
3. config 内嵌问答词库签名缓存（assistant_router.py）：签名命中免重复解析+
   预分词，内容变更重建，两源分槽互不踢槽。
"""
import asyncio
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.chdir(Path(__file__).resolve().parents[1])  # 磁盘 config.json 真相口径（仓库根 cwd）


def _run(coro):
    """独立事件循环执行协程（不依赖线程当前 loop——全量套件上下文中
    get_event_loop 可能无 current loop，3.12 直接 RuntimeError）。"""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class TestLayoutHotApply(unittest.TestCase):
    """二级菜单开关热切：运行中切换调用了服务切换（守卫核心断言）。"""

    def _make_layout(self):
        from frontend.ui.layout import DrawerLayout
        lay = object.__new__(DrawerLayout)
        lay.config = {"danmaku_listener": {"enabled": False},
                      "choreography": {"enable": False}}
        lay._current_page_enable_path = None
        lay.config_status_label = None
        return lay

    def test_hot_paths_registered(self):
        """注册表：弹幕监听与编排总开关两路径在册（路径漂移即测试红）。"""
        from frontend.ui.layout import HOT_APPLY_PATHS
        self.assertIn(("danmaku_listener", "enabled"), HOT_APPLY_PATHS)
        self.assertIn(("choreography", "enable"), HOT_APPLY_PATHS)

    def test_danmaku_toggle_calls_service_toggle(self):
        """核心守卫：监听开关运行中切换 → POST /danmaku/service/toggle 携带 enabled。"""
        from frontend.ui import layout as lay_mod
        lay = self._make_layout()
        calls = []

        def fake_post(path, body, timeout=30.0):
            calls.append((path, dict(body)))
            return {"success": True, "enabled": True}

        with patch.object(lay_mod, "_hot_api_post", fake_post), \
             patch.object(lay_mod.ui, "notify", lambda **kw: None):
            _run(lay._hot_apply_enable(("danmaku_listener", "enabled"), True))
        self.assertEqual(calls, [("/danmaku/service/toggle", {"enabled": True})])

    def test_danmaku_off_stops_service(self):
        """关闭方向同样携带 enabled=false（停监听）。"""
        from frontend.ui import layout as lay_mod
        lay = self._make_layout()
        calls = []
        with patch.object(lay_mod, "_hot_api_post",
                          lambda p, b, timeout=30.0: calls.append((p, dict(b))) or {"success": True}), \
             patch.object(lay_mod.ui, "notify", lambda **kw: None):
            _run(lay._hot_apply_enable(("danmaku_listener", "enabled"), False))
        self.assertEqual(calls, [("/danmaku/service/toggle", {"enabled": False})])

    def test_main_down_fallback_silent(self):
        """main 不可达（None）=回退重启生效：静默无异常、无运行态 toast。"""
        from frontend.ui import layout as lay_mod
        lay = self._make_layout()
        toasts = []
        with patch.object(lay_mod, "_hot_api_post", lambda p, b, timeout=30.0: None), \
             patch.object(lay_mod.ui, "notify", lambda **kw: toasts.append(kw)):
            _run(lay._hot_apply_enable(("danmaku_listener", "enabled"), True))
        self.assertEqual(toasts, [])  # 现有开关 toast 语义不受热切旁路影响

    def test_toggle_failure_warns_with_fix_hint(self):
        """端点显式失败（success=false）→ warning toast 带 error+fix_hint。"""
        from frontend.ui import layout as lay_mod
        lay = self._make_layout()
        toasts = []
        with patch.object(lay_mod, "_hot_api_post",
                          lambda p, b, timeout=30.0: {
                              "success": False, "error": "监听重建失败",
                              "fix_hint": "重启系统后重试"}), \
             patch.object(lay_mod.ui, "notify", lambda **kw: toasts.append(kw)):
            _run(lay._hot_apply_enable(("danmaku_listener", "enabled"), True))
        self.assertEqual(len(toasts), 1)
        self.assertEqual(toasts[0]["type"], "warning")
        self.assertIn("监听重建失败", toasts[0]["message"])
        self.assertIn("重启系统后重试", toasts[0]["message"])

    def test_choreography_enable_posts_disk_node(self):
        """编排总开关 → /choreography/rules 整节（磁盘权威节点）热应用。"""
        from frontend.ui import layout as lay_mod
        lay = self._make_layout()
        node = {"enable": True, "rules": [], "default_segments": ["s1"]}
        calls = []
        with patch.object(lay_mod, "_disk_choreography_node", lambda: dict(node)), \
             patch.object(lay_mod, "_hot_api_post",
                          lambda p, b, timeout=30.0: calls.append((p, b)) or {"applied": True}), \
             patch.object(lay_mod.ui, "notify", lambda **kw: None):
            _run(lay._hot_apply_enable(("choreography", "enable"), True))
        self.assertEqual(calls, [("/choreography/rules", node)])

    def test_choreography_edtalk_unreachable_warns(self):
        """EDTalk 不可达（已保存暂未生效）→ warning 提示，不误报已生效。"""
        from frontend.ui import layout as lay_mod
        lay = self._make_layout()
        toasts = []
        with patch.object(lay_mod, "_disk_choreography_node", lambda: {"enable": True}), \
             patch.object(lay_mod, "_hot_api_post",
                          lambda p, b, timeout=30.0: {"applied": False, "reason": "edtalk_unreachable"}), \
             patch.object(lay_mod.ui, "notify", lambda **kw: toasts.append(kw)):
            _run(lay._hot_apply_enable(("choreography", "enable"), True))
        self.assertEqual(toasts[0]["type"], "warning")
        self.assertIn("暂未生效", toasts[0]["message"])

    def test_on_enable_switch_wires_hot_apply(self):
        """开关 handler 接线：写盘成功后调度热切（走真实 _schedule_hot_apply
        过滤——未注册路径不产生热切任务）。"""
        from frontend.ui import layout as lay_mod
        lay = self._make_layout()
        scheduled = []

        async def fake_apply(self, path, value):
            scheduled.append((tuple(path), value))

        async def scenario():
            with patch.object(lay_mod.DrawerLayout, "_create_set_config_callback",
                              lambda self: lambda *keys, value, _notify=True: True), \
                 patch.object(lay_mod.DrawerLayout, "_hot_apply_enable", fake_apply), \
                 patch.object(lay_mod.ui, "notify", lambda **kw: None):
                ev = SimpleNamespace(value=True, sender=SimpleNamespace(value=False))
                lay._on_enable_switch("弹幕监听", ("danmaku_listener", "enabled"), ev)
                # 未注册路径（如 audio_play）写盘成功但不热切
                ev2 = SimpleNamespace(value=True, sender=SimpleNamespace(value=False))
                lay._on_enable_switch("音频播放", ("play_audio", "enable"), ev2)
                await asyncio.sleep(0)  # flush create_task

        asyncio.new_event_loop().run_until_complete(scenario())
        self.assertEqual(scheduled, [(("danmaku_listener", "enabled"), True)])

    def test_schedule_skips_without_running_loop(self):
        """无事件循环上下文（测试/非 NiceGUI）静默跳过=回退重启生效语义。"""
        from frontend.ui import layout as lay_mod
        lay = self._make_layout()
        # 无 running loop：不应抛异常（asyncio.get_running_loop RuntimeError 吞掉）
        lay._schedule_hot_apply(("danmaku_listener", "enabled"), True)

    def test_danmaku_close_requires_confirm(self):
        """关闭二次确认注册表同步：danmaku_listener.enabled 关闭须走确认对话框。"""
        from frontend.ui import layout as lay_mod
        from frontend.ui.layout import CONFIRM_CLOSE_PATHS
        self.assertIn(("danmaku_listener", "enabled"), CONFIRM_CLOSE_PATHS)
        lay = self._make_layout()
        lay.config["danmaku_listener"]["enabled"] = True  # 幂等护栏要求当前值≠目标值
        opened = []
        with patch.object(lay_mod.DrawerLayout, "_open_close_confirm",
                          lambda self, fn, path, e, spec: opened.append(path)):
            ev = SimpleNamespace(value=False, sender=SimpleNamespace(value=True))
            lay._on_enable_switch("弹幕监听", ("danmaku_listener", "enabled"), ev)
        self.assertEqual(opened, [("danmaku_listener", "enabled")])


class TestBlacklistSignatureCache(unittest.TestCase):
    """黑名单签名缓存：实时读语义不变（变更即生效），命中复用、变更重建。"""

    def _make_fh(self, raw):
        from utils.handlers.feature_handler import FeatureHandler
        fh = object.__new__(FeatureHandler)
        fh.my_handle = SimpleNamespace(config=SimpleNamespace(
            get=lambda *keys: raw if keys == ("filter", "blacklist", "username") else True))
        return fh

    def setUp(self):
        from utils.handlers import feature_handler as fh_mod
        fh_mod.FeatureHandler._BLACKLIST_SIG = None
        fh_mod.FeatureHandler._BLACKLIST_SET = None

    def test_hit_reuses_rebuilt_on_change(self):
        fh = self._make_fh(" 张三 \n李四")
        data = {"username": "张三"}
        self.assertTrue(fh.blacklist_handle(data))
        first = fh_mod_set = None
        from utils.handlers import feature_handler as fh_mod
        first = fh_mod.FeatureHandler._BLACKLIST_SET
        # 同名单再次命中：缓存对象复用（零重建）
        self.assertTrue(fh.blacklist_handle(data))
        self.assertIs(fh_mod.FeatureHandler._BLACKLIST_SET, first)
        # 改名单：实时生效（新用户立即拦截）+集合重建
        fh2 = self._make_fh("王五")
        self.assertFalse(fh2.blacklist_handle({"username": "张三"}))
        self.assertTrue(fh2.blacklist_handle({"username": "王五"}))
        self.assertIsNot(fh_mod.FeatureHandler._BLACKLIST_SET, first)

    def test_fullwidth_casefold_still_matched(self):
        """S-2 规范化语义不变：全角/大小写绕过仍拦截。"""
        fh = self._make_fh("ＴＯＭ")
        self.assertTrue(fh.blacklist_handle({"username": "tom"}))


class TestQaConfigSignatureCache(unittest.TestCase):
    """config 内嵌问答词库签名缓存：命中免重建、变更重建、两源分槽。"""

    def setUp(self):
        from utils.handlers import assistant_router as ar
        ar._qa_config_cache.clear()

    def test_hit_skips_reparse(self):
        from utils.handlers import assistant_router as ar
        lines = ["你好|你好呀", "再见|拜拜"]
        first = ar.load_qa_entries_from_config(lines, "本地问答词库")
        self.assertEqual(first[0][0], "你好")
        with patch.object(ar, "parse_qa_entries",
                          side_effect=AssertionError("签名命中不应重解析")):
            again = ar.load_qa_entries_from_config(lines, "本地问答词库")
        self.assertIs(again, first)

    def test_change_rebuilds_and_separate_slots(self):
        from utils.handlers import assistant_router as ar
        lines = ["你好|你好呀"]
        ar.load_qa_entries_from_config(lines, "本地问答词库")
        # 内容变更 → 重建
        new = ar.load_qa_entries_from_config(["嗨|嗨呀"], "本地问答词库")
        self.assertEqual(new[0][1], "嗨呀")
        # 另一源（助播）独立分槽：互不踢槽
        other = ar.load_qa_entries_from_config(["问题|答案"], "助播知识库")
        self.assertEqual(other[0][1], "答案")
        cached = ar._qa_config_cache["本地问答词库"]
        self.assertEqual(cached[1][0][1], "嗨呀")

    def test_non_iterable_degrades_empty(self):
        from utils.handlers import assistant_router as ar
        self.assertEqual(ar.load_qa_entries_from_config(123), [])


if __name__ == "__main__":
    unittest.main()
