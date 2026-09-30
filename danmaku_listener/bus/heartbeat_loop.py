"""引擎运行时组件：心跳循环（含 loop lag 采样）

- HEARTBEAT 周期发送（默认 10s，AUTOlive 侧 3 周期失联判定）
- loop lag watchdog：事件循环预期 sleep 与实际耗时的偏差采样入心跳载荷
  （Eng A-2：延迟劣化定位手段）
"""

import asyncio
import time
from typing import Callable, Coroutine, List, Optional

from loguru import logger

from danmaku_listener.bus.system_status import SystemStatusFactory


class HeartbeatLoop:
    """周期性向全部活跃房间发送 HEARTBEAT 系统消息"""

    def __init__(
        self,
        factory: SystemStatusFactory,
        send: Callable[[str], Coroutine],
        interval_seconds: float = 10.0,
        seq_provider: Optional[Callable[[str], int]] = None,
        rooms_provider: Optional[Callable[[], List[str]]] = None,
    ):
        """
        Args:
            factory: SYSTEM_STATUS 消息工厂（绑定 platform/engine）
            send: 发送函数（通常是推送管线的 broadcast）
            interval_seconds: 心跳周期
            seq_provider: 房间序号分配器（缺省恒 0——心跳不占用业务 seq 时由宿主决定）
            rooms_provider: 活跃房间列表（缺省空）
        """
        self._factory = factory
        self._send = send
        self._interval = interval_seconds
        self._seq_provider = seq_provider or (lambda room_id: 0)
        self._rooms_provider = rooms_provider or (lambda: [])
        self._task: Optional[asyncio.Task] = None
        self._last_lag_ms: Optional[float] = None

    @property
    def last_loop_lag_ms(self) -> Optional[float]:
        return self._last_lag_ms

    async def _tick(self) -> None:
        expected = self._interval
        start = time.monotonic()
        await asyncio.sleep(expected)
        actual = time.monotonic() - start
        self._last_lag_ms = max(0.0, (actual - expected) * 1000)
        for room_id in self._rooms_provider():
            msg = self._factory.heartbeat(
                room_id=room_id,
                seq=self._seq_provider(room_id),
                ts=int(time.time()),
                loop_lag_ms=self._last_lag_ms,
            )
            await self._send(msg.to_wire())

    async def _loop(self) -> None:
        logger.debug(f"HeartbeatLoop started (interval={self._interval}s)")
        while True:
            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                # 心跳失败不阻断引擎主流程（下一周期重试）
                logger.warning(f"heartbeat tick failed: {e}")

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
