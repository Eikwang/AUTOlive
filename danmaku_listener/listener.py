"""DanmakuListener - 弹幕监听系统主类

提供统一的 API 接口，支持代理模式和浏览器模式的混合架构。
"""

import asyncio
from typing import Any, Callable, Coroutine, Dict, List, Optional

from loguru import logger

from danmaku_listener.config.settings import get_settings
from danmaku_listener.utils.platform_parser import parse_room_spec, RoomSpec, get_default_engine
from danmaku_listener.bus.event_bus import EventBus
from danmaku_listener.bus.message import DanmakuMessage
from danmaku_listener.bus.dedup_filter import DedupFilter
from danmaku_listener.engines.base import BaseEngine
from danmaku_listener.engines.browser_engine import BrowserEngine
from danmaku_listener.adapters.base import BaseAdapter
from danmaku_listener.adapters.douyu import DouyuAdapter
from danmaku_listener.adapters.bilibili import BilibiliAdapter
from danmaku_listener.adapters.generic import GenericAdapter
from danmaku_listener.managers.reconnect_manager import ReconnectManager


# 回调函数类型
DanmakuCallback = Callable[[DanmakuMessage], Coroutine[Any, Any, None]]
ErrorCallback = Callable[[Exception], Coroutine[Any, Any, None]]
ReconnectCallback = Callable[[str], Coroutine[Any, Any, None]]
StatusCallback = Callable[[str, str], Coroutine[Any, Any, None]]


class DanmakuListener:
    """弹幕监听器主类

    提供简洁的 API 来启动和管理多个直播间的弹幕监听。

    Example:
        # 使用上下文管理器
        async with DanmakuListener() as listener:
            await listener.start(["douyin:123456", "bilibili:789012"])
            listener.on_danmaku(my_handler)

        # 显式控制
        listener = DanmakuListener()
        await listener.start(["douyin:123456"])
        # ... 运行中
        await listener.stop()
    """

    def __init__(self, config: Optional[dict] = None):
        """初始化监听器

        Args:
            config: 可选的配置字典（覆盖默认配置）
        """
        self._settings = get_settings()
        self._bus = EventBus()
        self._dedup = DedupFilter(window_size=self._settings.dedup_window_size)
        self._engines: Dict[str, BaseEngine] = {}
        self._adapters: Dict[str, BaseAdapter] = {}
        self._browser_engine: Optional[BrowserEngine] = None
        self._reconnect_manager: Optional[ReconnectManager] = None
        self._running = False

        # 注册内部事件处理器（同步注册，EventBus.subscribe 是 async 但初始化阶段无法 await）
        self._raw_message_handler = self._handle_raw_message
        self._error_handler = self._handle_error

    async def _ensure_subscriptions(self) -> None:
        """确保内部事件订阅已注册"""
        if not self._bus.has_subscribers("raw_message"):
            await self._bus.subscribe("raw_message", self._raw_message_handler)
        if not self._bus.has_subscribers("error"):
            await self._bus.subscribe("error", self._error_handler)

    def on_danmaku(self, callback: DanmakuCallback) -> None:
        """注册弹幕事件回调

        Args:
            callback: 异步回调函数，接收 DanmakuMessage 参数
        """
        async def handler(msg: DanmakuMessage) -> None:
            await callback(msg)

        # 同步注册到内部列表，后续 start 时确保订阅
        if not hasattr(self, '_danmaku_callbacks'):
            self._danmaku_callbacks: List = []
        self._danmaku_callbacks.append(handler)

    def on_error(self, callback: ErrorCallback) -> None:
        """注册错误事件回调

        Args:
            callback: 异步回调函数，接收 Exception 参数
        """
        async def handler(error: Exception) -> None:
            await callback(error)

        if not hasattr(self, '_error_callbacks'):
            self._error_callbacks: List = []
        self._error_callbacks.append(handler)

    def on_reconnect(self, callback: ReconnectCallback) -> None:
        """注册重连事件回调

        Args:
            callback: 异步回调函数，接收 room_id 参数
        """
        async def handler(room_id: str) -> None:
            await callback(room_id)

        if not hasattr(self, '_reconnect_callbacks'):
            self._reconnect_callbacks: List = []
        self._reconnect_callbacks.append(handler)

    def on_status_change(self, callback: StatusCallback) -> None:
        """注册状态变化事件回调

        Args:
            callback: 异步回调函数，接收 (room_id, status) 参数
        """
        async def handler(data: dict) -> None:
            await callback(data["room_id"], data["status"])

        if not hasattr(self, '_status_callbacks'):
            self._status_callbacks: List = []
        self._status_callbacks.append(handler)

    async def start(self, rooms: List[str]) -> None:
        """启动对多个房间的监听

        Args:
            rooms: 房间规格列表，格式为 ["platform:room_id", ...]

        Raises:
            ValueError: 如果房间规格格式不正确或超过最大数量
        """
        if self._running:
            logger.warning("Already running")
            return

        if len(rooms) > self._settings.max_rooms:
            raise ValueError(f"Maximum {self._settings.max_rooms} rooms allowed")

        # 确保内部订阅已注册
        await self._ensure_subscriptions()

        # 注册用户回调到事件总线
        await self._register_user_callbacks()

        # 初始化重连管理器
        if self._reconnect_manager is None:
            self._reconnect_manager = ReconnectManager()

        self._running = True
        logger.info(f"Starting monitoring for {len(rooms)} rooms...")

        for spec in rooms:
            try:
                room_spec = parse_room_spec(spec)
                logger.info(f"Adding room: {room_spec.platform}:{room_spec.room_id}")

                # 根据平台选择引擎并启动
                engine = self._get_engine(room_spec.platform)

                # 设置重连管理器
                engine.set_reconnect_manager(self._reconnect_manager)

                # 注册引擎消息和错误回调
                engine.on_message(self._create_engine_message_callback(room_spec.room_id))
                engine.on_error(self._create_engine_error_callback(room_spec.room_id))

                await engine.start(room_spec.room_id)
                self._engines[room_spec.room_id] = engine

            except ValueError as e:
                logger.error(f"Invalid room spec '{spec}': {e}")
            except NotImplementedError as e:
                logger.error(f"Platform not yet supported '{spec}': {e}")

        logger.info("All rooms started successfully")

    async def stop(self) -> None:
        """停止所有监听"""
        if not self._running:
            return

        self._running = False
        logger.info("Stopping all monitors...")

        # 逐个停止房间引擎
        for room_id, engine in list(self._engines.items()):
            try:
                await engine.stop(room_id)
            except Exception as e:
                logger.error(f"Error stopping engine for room {room_id}: {e}")

        # 停止浏览器引擎（一次性关闭所有 Context）
        if self._browser_engine:
            try:
                await self._browser_engine.stop_all()
            except Exception as e:
                logger.error(f"Error stopping browser engine: {e}")
            self._browser_engine = None

        self._engines.clear()
        logger.info("All monitors stopped")

    def _get_engine(self, platform: str) -> BaseEngine:
        """根据平台获取对应的引擎实例

        浏览器模式平台共享同一个 BrowserEngine 单例。
        （mitmproxy 代理路线已随抖音原生 Web WS 切换而移除——2026-09-29）

        Args:
            platform: 平台标识

        Returns:
            引擎实例

        Raises:
            NotImplementedError: 如果平台暂不支持
        """
        engine_type = get_default_engine(platform)

        if engine_type == "browser":
            # 浏览器引擎使用单例，所有浏览器模式平台共享
            if self._browser_engine is None:
                self._browser_engine = BrowserEngine(
                    headless=self._settings.browser_headless,
                    cookie_dir=self._settings.cookie_dir,
                )
            return self._browser_engine
        else:
            raise NotImplementedError(f"Unknown engine type '{engine_type}' for platform '{platform}'")

    def _get_adapter(self, platform: str) -> BaseAdapter:
        """根据平台获取对应的适配器实例

        浏览器模式平台使用专用适配器（如 DouyuAdapter、BilibiliAdapter）。
        （抖音已切原生 Web WS 协议引擎，不走旧管线适配器——2026-09-29）

        Args:
            platform: 平台标识

        Returns:
            适配器实例

        Raises:
            NotImplementedError: 如果平台暂不支持
        """
        if platform == "douyu":
            return DouyuAdapter()
        elif platform == "bilibili":
            return BilibiliAdapter()

        # 其他浏览器模式平台使用通用适配器
        engine_type = get_default_engine(platform)
        if engine_type == "browser":
            return GenericAdapter()

        raise NotImplementedError(
            f"Unknown engine type '{engine_type}' for platform '{platform}'"
        )

    def _create_engine_message_callback(self, room_id: str) -> Callable:
        """创建引擎消息回调，将原始消息路由到事件总线

        Args:
            room_id: 房间 ID

        Returns:
            异步回调函数
        """
        async def callback(data: dict) -> None:
            # 确保消息包含平台和房间信息
            if "room_id" not in data:
                data["room_id"] = room_id
            await self._bus.publish("raw_message", data)

        return callback

    def _create_engine_error_callback(self, room_id: str) -> Callable:
        """创建引擎错误回调，将错误路由到重连或事件总线

        如果引擎设置了重连管理器，优先尝试重连。
        否则直接发布错误事件。

        Args:
            room_id: 房间 ID

        Returns:
            异步回调函数
        """
        async def callback(error: Exception) -> None:
            engine = self._engines.get(room_id)
            if engine and engine._reconnect_manager:
                await engine._handle_error_with_reconnect(room_id, error)
            else:
                await self._bus.publish("error", error)

        return callback

    async def _register_user_callbacks(self) -> None:
        """将用户注册的回调注册到事件总线"""
        if hasattr(self, '_danmaku_callbacks'):
            for handler in self._danmaku_callbacks:
                await self._bus.subscribe("danmaku", handler)

        if hasattr(self, '_error_callbacks'):
            for handler in self._error_callbacks:
                await self._bus.subscribe("error", handler)

        if hasattr(self, '_reconnect_callbacks'):
            for handler in self._reconnect_callbacks:
                await self._bus.subscribe("reconnect", handler)

        if hasattr(self, '_status_callbacks'):
            for handler in self._status_callbacks:
                await self._bus.subscribe("status_change", handler)

    async def _handle_raw_message(self, data: dict) -> None:
        """处理原始消息

        浏览器模式消息格式：{platform, room_id, raw_data, msg_id}
        （mitmproxy 代理模式分支已随抖音原生 Web WS 切换移除——2026-09-29）

        Args:
            data: 包含 platform, room_id 等字段的字典
        """
        try:
            platform = data.get("platform") or ""
            room_id = data.get("room_id") or ""

            # 浏览器模式消息（raw_data 字段）
            raw_data = data.get("raw_data")
            msg_id = data.get("msg_id", "")

            if not platform:
                logger.warning("Raw message missing platform field, skipping")
                return

            # 去重检查
            if msg_id and self._dedup.should_filter(room_id, str(msg_id)):
                return

            # 调用适配器解析
            adapter = self._get_adapter(platform)
            message = await adapter.parse(raw_data, context={"platform": platform, "room_id": room_id})
            if message:
                await self._bus.publish("danmaku", message)

        except Exception as e:
            logger.error(f"Error handling raw message: {e}")

    async def _handle_error(self, error: Exception) -> None:
        """处理错误事件

        Args:
            error: 异常对象
        """
        logger.error(f"Unhandled error: {error}")

    async def __aenter__(self) -> "DanmakuListener":
        """进入上下文管理器"""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """退出上下文管理器"""
        await self.stop()
