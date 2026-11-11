# -*- coding: UTF-8 -*-
"""守卫：登录链路失败可见 + 配置期入口可达（2026-11-11 两轮实测根修）

第一轮（重登编排链，用户实测 douyin）：
1. playwright 浏览器缺失（runtime312 playwright 1.61.0 期望 chromium-1228
   未安装）→ profile login 异常 → rec["error"] 只含 type 名，根因不可见；
2. 页面刷新/重连后 client/slot 已删除 → 终态 toast 抛 RuntimeError('The
   parent element this slot belongs to has been deleted.') 外溢杀死轮询任务
   ——用户点『登录』后零反馈（无窗口、无提示）。

第二轮（toast 修订 + 配置期断链，用户实测复测暴露）：
3. toast 存活预检误用不存在 API（nicegui.client 是模块且无 get()）→
   AttributeError 被 except 接住 → 全部 toast 被吞（比不修还糟）——守卫改为
   仅 try/except 兜底，且断言预检写法不得回归；
4. v0.8.0（1d7438bb）profile 化删除了 douyin_login.run_login_flow，但
   DanmakuLoginService._run_flow 仍按统一契约调用 → 配置期『登录』AttributeError
   断链（browser 都没起）——守卫断言入口存在且服务 flow 可构建 coro。

守卫（源码断言 + 可调用行为断言，同 test_chat_review_1030 风格）：
- platform_config._toast 必须 try/except 兜底、不得 reintroduce is_deleted 预检；
- 终态 toast 必须透传服务端 error 详情；
- relogin.py douyin/huya 失败路径 rec["error"] 必须含异常消息体（不只 type 名）；
- douyin_login.run_login_flow 入口必须存在且服务 _meta["douyin"]["flow"]() 可构建 coro。
"""
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _src(rel: str) -> str:
    return (REPO / rel).read_text(encoding="utf-8")


class TestReloginToastGuard:
    def test_toast_client_alive_guard(self):
        src = _src("frontend/ui/tabs/platform_config.py")
        # notify 必须在 try/except 内兜底（slot deleted RuntimeError 不得外溢）
        assert "前端通知失败" in src
        # 不得 reintroduce is_deleted 预检——nicegui.client 是模块且无 get()，
        # 预检 AttributeError 反致全部 toast 被吞（2026-11-11 14:05 实测）
        assert "is_deleted()" not in src
        assert "_ng_client" not in src

    def test_terminal_toast_passes_error_detail(self):
        src = _src("frontend/ui/tabs/platform_config.py")
        assert 'str(r.get("error") or "").strip()' in src
        assert "detail[:120]" in src

    def test_backend_error_includes_exception_message(self):
        src = _src("danmaku_listener/web/relogin.py")
        # douyin + huya 失败路径均须携带异常消息体（不只 type 名）
        assert src.count("{type(e).__name__}: {str(e)[:120]}") >= 2


class TestDouyinConfigPeriodLogin:
    """配置期抖音登录入口（2026-11-11 实测断链：v0.8.0 profile 化删除了
    run_login_flow，登录服务 AttributeError——『登录』点了没反应）。"""

    def test_module_entry_exists_and_callable(self):
        import asyncio
        import importlib

        mod = importlib.import_module("danmaku_listener.engines.douyin_login")
        assert hasattr(mod, "run_login_flow")
        coro = mod.run_login_flow(cookie_path="cookie/douyin_cookies.json")
        assert asyncio.iscoroutine(coro)
        coro.close()  # 仅验证可调用契约——不启动浏览器

    def test_service_meta_flow_builds_coro(self):
        """登录服务统一契约调用点（_run_flow → mod.run_login_flow）不断链。"""
        import asyncio

        from frontend.ui.components.danmaku_login_service import get_login_service

        coro = get_login_service()._meta("douyin")["flow"]()
        assert asyncio.iscoroutine(coro)
        coro.close()
