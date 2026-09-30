"""断线重连管理器

实现指数退避重连策略，确保连接异常时自动恢复。
"""

import asyncio
from typing import Any, Callable, Coroutine, Optional

from loguru import logger

from danmaku_listener.config.settings import get_settings


# 回调函数类型
ReconnectCallback = Callable[[], Coroutine[Any, Any, bool]]


class ReconnectManager:
    """断线重连管理器

    使用指数退避策略执行重连，最大重试次数后通知上层应用。
    """

    def __init__(
        self,
        max_retries: Optional[int] = None,
        base_delay: Optional[float] = None,
    ):
        """初始化重连管理器

        Args:
            max_retries: 最大重试次数（默认从配置读取）
            base_delay: 基础延迟秒数（默认从配置读取）
        """
        settings = get_settings()
        self.max_retries = max_retries or settings.reconnect_max_retries
        self.base_delay = base_delay or settings.reconnect_base_delay
        self._current_attempt = 0

    async def reconnect(
        self,
        callback: ReconnectCallback,
        room_id: str,
    ) -> bool:
        """执行重连逻辑

        Args:
            callback: 重连尝试函数，返回 True 表示成功
            room_id: 房间 ID（用于日志）

        Returns:
            True 如果重连成功，False 如果所有重试都失败
        """
        self._current_attempt = 0

        while self._current_attempt < self.max_retries:
            self._current_attempt += 1
            delay = self.base_delay * (2 ** (self._current_attempt - 1))

            logger.info(
                f"Reconnect attempt {self._current_attempt}/{self.max_retries} "
                f"for room {room_id}, waiting {delay}s..."
            )

            await asyncio.sleep(delay)

            try:
                success = await callback()
                if success:
                    logger.info(f"Successfully reconnected to room {room_id}")
                    self._current_attempt = 0
                    return True
            except Exception as e:
                logger.warning(f"Reconnect attempt {self._current_attempt} failed: {e}")

        # 所有重试失败
        logger.error(
            f"Failed to reconnect to room {room_id} after {self.max_retries} attempts"
        )
        return False

    def reset(self) -> None:
        """重置重连计数器"""
        self._current_attempt = 0

    @property
    def current_attempt(self) -> int:
        """获取当前重试次数"""
        return self._current_attempt