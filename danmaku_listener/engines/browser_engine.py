"""浏览器引擎

基于 Playwright 实现浏览器模式的弹幕监听，支持多房间 BrowserContext 隔离。
集成心跳保活监控，页面异常时自动恢复。
"""

import asyncio
from typing import Any, Callable, Dict, Optional

from loguru import logger

from danmaku_listener.engines.base import BaseEngine, EngineStatus
from danmaku_listener.managers.cookie_manager import CookieManager
from danmaku_listener.managers.heartbeat_monitor import HeartbeatMonitor


class BrowserEngine(BaseEngine):
    """浏览器引擎

    使用 Playwright 单实例多 Context 架构，每个直播间使用独立的 BrowserContext，
    防止状态污染和内存泄漏。集成心跳保活监控，页面异常时自动恢复。

    Attributes:
        _browser: Playwright Browser 实例（单例共享）
        _contexts: room_id -> BrowserContext 映射
        _pages: room_id -> Page 映射
        _cookie_manager: Cookie 持久化管理器
        _headless: 是否使用无头模式
        _playwright: Playwright 实例
        _heartbeats: room_id -> HeartbeatMonitor 映射
    """

    def __init__(self, headless: bool = True, cookie_dir: str = "./cookie",
                 session_lifetime_seconds: int = 0):
        """初始化浏览器引擎

        Args:
            headless: 是否使用无头模式
            cookie_dir: Cookie 存储目录
        """
        super().__init__()
        self._browser = None
        self._playwright = None
        # 契约 v1 有界会话（阶段 6）：>0 时单页面生命周期到期触发事件驱动重建，
        # 替代"全局定时重启"；0=关闭（保持既有行为，默认关闭由兜底开关控制）
        self._session_lifetime_seconds = session_lifetime_seconds
        self._session_started_at: dict = {}
        self._contexts: Dict[str, Any] = {}
        self._pages: Dict[str, Any] = {}
        self._cookie_manager = CookieManager(cookie_dir=cookie_dir)
        self._headless = headless
        self._heartbeats: Dict[str, HeartbeatMonitor] = {}

    @property
    def platform(self) -> str:
        """返回平台标识"""
        return "browser"

    async def start(self, room_id: str) -> None:
        """启动对指定房间的浏览器监听

        Args:
            room_id: 房间 ID
        """
        logger.info(f"BrowserEngine starting for room: {room_id}")

        # 首次启动时创建浏览器实例
        if self._browser is None:
            await self._launch_browser()

        # 为房间创建独立的 BrowserContext
        await self._create_context(room_id)

        # 导航并注入脚本
        await self._navigate_and_inject(room_id)

        # 启动心跳监控
        await self._start_heartbeat(room_id)

        # 有界会话起点（契约 v1：事件驱动重建）
        import time as _time
        self._session_started_at[room_id] = _time.monotonic()

        self._set_status(EngineStatus.RUNNING)

    def session_expired(self, room_id: str) -> bool:
        """有界会话是否到期（兜底/受控页面的生命周期上限）"""
        import time as _time
        started = self._session_started_at.get(room_id)
        if not started or self._session_lifetime_seconds <= 0:
            return False
        return _time.monotonic() - started >= self._session_lifetime_seconds

    async def stop(self, room_id: str) -> None:
        """停止对指定房间的浏览器监听

        Args:
            room_id: 房间 ID
        """
        logger.info(f"BrowserEngine stopping for room: {room_id}")

        # 停止心跳监控
        await self._stop_heartbeat(room_id)

        # 先保存 Cookie
        await self._save_context_cookies(room_id)

        # 关闭 Context
        await self._close_context(room_id)

        # 如果没有活跃的 Context，更新状态
        if not self._contexts:
            self._set_status(EngineStatus.STOPPED)

    async def restart(self, room_id: str) -> None:
        """重启对指定房间的浏览器监听

        Args:
            room_id: 房间 ID
        """
        logger.info(f"BrowserEngine restarting for room: {room_id}")
        await self.stop(room_id)
        await self.start(room_id)

    async def stop_all(self) -> None:
        """停止所有监听并关闭浏览器实例"""
        logger.info("BrowserEngine stopping all rooms...")

        # 停止所有心跳监控
        for room_id in list(self._heartbeats.keys()):
            await self._stop_heartbeat(room_id)

        # 保存所有 Cookie
        for room_id in list(self._contexts.keys()):
            await self._save_context_cookies(room_id)

        # 关闭所有 Context
        for room_id in list(self._contexts.keys()):
            await self._close_context(room_id)

        # 关闭浏览器
        if self._browser:
            await self._browser.close()
            self._browser = None

        # 关闭 Playwright
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None

        self._set_status(EngineStatus.STOPPED)

    async def _launch_browser(self) -> None:
        """启动 Playwright 浏览器实例"""
        from playwright.async_api import async_playwright

        logger.info("Launching Playwright browser...")
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=self._headless,
        )
        logger.info(f"Browser launched (headless={self._headless})")

    async def _create_context(self, room_id: str) -> None:
        """为指定房间创建 BrowserContext

        Args:
            room_id: 房间 ID
        """
        # 加载已保存的 Cookie
        storage_state = self._cookie_manager.load_cookies(room_id)

        kwargs: Dict[str, Any] = {
            "viewport": {"width": 1280, "height": 720},
        }

        if storage_state:
            kwargs["storage_state"] = storage_state
            logger.debug(f"Loaded cookies for room: {room_id}")

        context = await self._browser.new_context(**kwargs)
        self._contexts[room_id] = context

        # 创建新页面
        page = await context.new_page()
        self._pages[room_id] = page

        logger.debug(f"Created BrowserContext for room: {room_id}")

    async def _close_context(self, room_id: str) -> None:
        """关闭指定房间的 BrowserContext

        Args:
            room_id: 房间 ID
        """
        if room_id in self._contexts:
            try:
                await self._contexts[room_id].close()
            except Exception as e:
                logger.error(f"Error closing context for room {room_id}: {e}")
            finally:
                del self._contexts[room_id]
                self._pages.pop(room_id, None)

    async def _save_context_cookies(self, room_id: str) -> None:
        """保存指定房间的 Cookie

        Args:
            room_id: 房间 ID
        """
        if room_id in self._contexts:
            try:
                state = await self._contexts[room_id].storage_state()
                self._cookie_manager.save_cookies(room_id, state)
                logger.debug(f"Saved cookies for room: {room_id}")
            except Exception as e:
                logger.error(f"Error saving cookies for room {room_id}: {e}")

    async def _navigate_and_inject(self, room_id: str) -> None:
        """导航到直播间页面并注入监听脚本

        Args:
            room_id: 房间 ID
        """
        # 默认导航逻辑，子类或外部可覆盖 URL
        url = f"https://live.example.com/{room_id}"
        await self._navigate(room_id, url)

        # 注入基础监听脚本
        await self._inject_script(room_id, self._default_script())

    async def _navigate(self, room_id: str, url: str) -> None:
        """导航到指定 URL

        Args:
            room_id: 房间 ID
            url: 目标 URL
        """
        if room_id in self._pages:
            await self._pages[room_id].goto(url, wait_until="domcontentloaded")
            logger.debug(f"Navigated to {url} for room: {room_id}")

    async def _inject_script(self, room_id: str, script: str) -> None:
        """注入 JavaScript 脚本到页面

        Args:
            room_id: 房间 ID
            script: JavaScript 代码
        """
        if room_id in self._pages:
            await self._pages[room_id].add_script_tag(content=script)
            logger.debug(f"Injected script for room: {room_id}")

    async def _expose_callback(
        self, room_id: str, name: str, callback: Callable
    ) -> None:
        """暴露 Python 回调函数到页面 JavaScript 环境

        Args:
            room_id: 房间 ID
            name: JavaScript 中可调用的函数名
            callback: Python 回调函数
        """
        if room_id in self._pages:
            await self._pages[room_id].expose_function(name, callback)
            logger.debug(f"Exposed callback '{name}' for room: {room_id}")

    async def _start_heartbeat(self, room_id: str) -> None:
        """启动指定房间的心跳监控

        Args:
            room_id: 房间 ID
        """
        monitor = HeartbeatMonitor(
            room_id=room_id,
            health_check=lambda: self._check_page_health(room_id),
            recovery=lambda: self._recover_page(room_id),
        )
        self._heartbeats[room_id] = monitor
        await monitor.start()
        logger.debug(f"Started heartbeat monitor for room: {room_id}")

    async def _stop_heartbeat(self, room_id: str) -> None:
        """停止指定房间的心跳监控

        Args:
            room_id: 房间 ID
        """
        if room_id in self._heartbeats:
            await self._heartbeats[room_id].stop()
            del self._heartbeats[room_id]
            logger.debug(f"Stopped heartbeat monitor for room: {room_id}")

    async def _check_page_health(self, room_id: str) -> bool:
        """检查页面健康状态

        通过 page.evaluate('1') 检测页面是否存活。

        Args:
            room_id: 房间 ID

        Returns:
            True 如果页面健康，False 如果异常
        """
        if room_id not in self._pages:
            return False

        try:
            await self._pages[room_id].evaluate("1")
            return True
        except Exception as e:
            logger.warning(f"Page health check failed for room {room_id}: {e}")
            return False

    async def _recover_page(self, room_id: str) -> None:
        """尝试恢复异常页面

        刷新页面并重新注入监听脚本。恢复失败时尝试重连。
        重连也失败时触发错误回调。

        Args:
            room_id: 房间 ID
        """
        if room_id not in self._pages:
            return

        try:
            logger.info(f"Attempting to recover page for room: {room_id}")
            await self._pages[room_id].reload()
            await self._inject_script(room_id, self._default_script())
            logger.info(f"Page recovered successfully for room: {room_id}")
        except Exception as e:
            logger.error(f"Failed to recover page for room {room_id}: {e}")
            # 恢复失败，尝试重连
            if self._reconnect_manager:
                await self._handle_error_with_reconnect(
                    room_id, Exception(f"Page recovery failed for room {room_id}: {e}")
                )
            else:
                await self._emit_error(
                    Exception(f"Page recovery failed for room {room_id}: {e}")
                )

    def get_page(self, room_id: str) -> Optional[Any]:
        """获取指定房间的 Page 对象

        Args:
            room_id: 房间 ID

        Returns:
            Page 对象，如果房间不存在返回 None
        """
        return self._pages.get(room_id)

    @staticmethod
    def _default_script() -> str:
        """返回默认的监听脚本

        Returns:
            JavaScript 代码字符串
        """
        return """
        // DanmakuListener default monitoring script
        // Observes DOM mutations and forwards danmaku data
        (function() {
            const target = document.body;
            const observer = new MutationObserver(function(mutations) {
                mutations.forEach(function(mutation) {
                    mutation.addedNodes.forEach(function(node) {
                        if (node.nodeType === Node.ELEMENT_NODE) {
                            const text = node.textContent?.trim();
                            if (text && typeof onDanmaku === 'function') {
                                onDanmaku(JSON.stringify({
                                    type: 'dom_mutation',
                                    content: text,
                                    element: node.tagName
                                }));
                            }
                        }
                    });
                });
            });
            observer.observe(target, { childList: true, subtree: true });
        })();
        """
