"""心跳保活监控器

定期检测页面/连接健康状态，异常时自动恢复。
"""

import asyncio
from typing import Any, Callable, Coroutine, Optional

from loguru import logger

from danmaku_listener.config.settings import get_settings


# 健康检查回调类型
HealthCheckCallback = Callable[[], Coroutine[Any, Any, bool]]
RecoveryCallback = Callable[[], Coroutine[Any, Any, None]]


class HeartbeatMonitor:
    """心跳保活监控器

    定期执行健康检查，如果检测到异常则触发恢复流程。
    """

    def __init__(
        self,
        room_id: str,
        health_check: HealthCheckCallback,
        recovery: RecoveryCallback,
        interval: Optional[int] = None,
    ):
        """初始化心跳监控器

        Args:
            room_id: 房间 ID
            health_check: 健康检查函数，返回 True 表示正常
            recovery: 恢复函数
            interval: 检查间隔秒数（默认从配置读取）
        """
        self.room_id = room_id
        self.health_check = health_check
        self.recovery = recovery
        settings = get_settings()
        self.interval = interval or settings.heartbeat_interval
        self._running = False
        self._task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        """启动心跳监控"""
        if self._running:
            return

        self._running = True
        self._task = asyncio.create_task(self._monitor_loop())
        logger.info(f"Started heartbeat monitor for room {self.room_id}")

    async def stop(self) -> None:
        """停止心跳监控"""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info(f"Stopped heartbeat monitor for room {self.room_id}")

    async def _monitor_loop(self) -> None:
        """监控循环"""
        while self._running:
            try:
                is_healthy = await self.health_check()

                if not is_healthy:
                    logger.warning(f"Health check failed for room {self.room_id}")
                    await self._try_recovery()

            except Exception as e:
                logger.error(f"Heartbeat error for room {self.room_id}: {e}")

            await asyncio.sleep(self.interval)

    async def _try_recovery(self) -> None:
        """尝试恢复"""
        try:
            await self.recovery()
            logger.info(f"Recovery successful for room {self.room_id}")
        except Exception as e:
            logger.error(f"Recovery failed for room {self.room_id}: {e}")

    @property
    def is_running(self) -> bool:
        """是否正在运行"""
        return self._running