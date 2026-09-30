"""事件总线 (Event Bus)

异步事件分发中心，支持订阅/发布模式。
"""

import asyncio
from collections import defaultdict
from typing import Any, Callable, Coroutine, Dict, List, Optional

from loguru import logger


# 回调函数类型
Callback = Callable[[Any], Coroutine[Any, Any, None]]


class EventBus:
    """异步事件总线

    支持多个事件的订阅和发布，使用 asyncio 实现非阻塞调用。
    """

    def __init__(self):
        """初始化事件总线"""
        self._subscribers: Dict[str, List[Callback]] = defaultdict(list)
        self._lock = asyncio.Lock()

    async def subscribe(self, event: str, callback: Callback) -> None:
        """订阅事件

        Args:
            event: 事件名称
            callback: 回调函数（必须是 async 函数）
        """
        async with self._lock:
            self._subscribers[event].append(callback)
            logger.debug(f"Subscribed to event: {event}")

    async def unsubscribe(self, event: str, callback: Callback) -> None:
        """取消订阅

        Args:
            event: 事件名称
            callback: 要移除的回调函数
        """
        async with self._lock:
            if callback in self._subscribers[event]:
                self._subscribers[event].remove(callback)
                logger.debug(f"Unsubscribed from event: {event}")

    async def publish(self, event: str, data: Any = None) -> None:
        """发布事件

        Args:
            event: 事件名称
            data: 事件数据
        """
        async with self._lock:
            callbacks = self._subscribers.get(event, []).copy()

        if not callbacks:
            logger.debug(f"No subscribers for event: {event}")
            return

        # 单订阅者直接调用，避免 gather 开销
        if len(callbacks) == 1:
            await self._safe_call(callbacks[0], data)
            return

        # 多订阅者并发执行
        tasks = [self._safe_call(cb, data) for cb in callbacks]
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _safe_call(self, callback: Callback, data: Any) -> None:
        """安全调用回调函数，捕获异常防止影响其他回调"""
        try:
            await callback(data)
        except Exception as e:
            logger.error(f"Error in callback: {e}", exc_info=True)

    def has_subscribers(self, event: str) -> bool:
        """检查是否有订阅者

        Args:
            event: 事件名称

        Returns:
            True 如果有订阅者
        """
        return len(self._subscribers.get(event, [])) > 0

    def get_subscriber_count(self, event: str) -> int:
        """获取订阅者数量

        Args:
            event: 事件名称

        Returns:
            订阅者数量
        """
        return len(self._subscribers.get(event, []))