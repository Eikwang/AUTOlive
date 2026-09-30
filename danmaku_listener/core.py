"""性能指标收集模块

收集内存占用、消息吞吐量等运行时指标，支持按房间统计。
"""

import os
import time
from typing import Dict

from loguru import logger


class MetricsCollector:
    """性能指标收集器

    收集和暴露运行时性能指标，包括：
    - 消息统计（接收/发布/过滤）
    - 重连和错误计数
    - 活跃房间数
    - 运行时间
    - 内存占用
    - 消息吞吐量
    - 按房间的消息统计

    所有计数操作均为线程安全的简单递增。
    """

    def __init__(self):
        """初始化指标收集器"""
        self._start_time: float = time.monotonic()
        self._messages_received: int = 0
        self._messages_published: int = 0
        self._messages_filtered: int = 0
        self._reconnect_count: int = 0
        self._errors_count: int = 0
        self._active_rooms: int = 0
        self._per_room: Dict[str, int] = {}

    def record_message_received(self, room_id: str) -> None:
        """记录接收消息

        Args:
            room_id: 房间 ID
        """
        self._messages_received += 1
        if room_id not in self._per_room:
            self._per_room[room_id] = 0
        self._per_room[room_id] += 1

    def record_message_published(self) -> None:
        """记录发布消息"""
        self._messages_published += 1

    def record_message_filtered(self) -> None:
        """记录过滤消息"""
        self._messages_filtered += 1

    def record_reconnect(self, room_id: str) -> None:
        """记录重连

        Args:
            room_id: 房间 ID
        """
        self._reconnect_count += 1
        logger.debug(f"Recorded reconnect for room: {room_id}")

    def record_error(self) -> None:
        """记录错误"""
        self._errors_count += 1

    def set_active_rooms(self, count: int) -> None:
        """设置活跃房间数

        Args:
            count: 活跃房间数量
        """
        self._active_rooms = count

    def get_metrics(self) -> Dict:
        """获取所有指标

        Returns:
            包含所有指标的字典
        """
        uptime = time.monotonic() - self._start_time
        memory_mb = self._get_memory_usage()

        # 计算吞吐量
        messages_per_second = (
            self._messages_received / uptime if uptime > 0 else 0.0
        )

        return {
            "messages_received": self._messages_received,
            "messages_published": self._messages_published,
            "messages_filtered": self._messages_filtered,
            "reconnect_count": self._reconnect_count,
            "errors_count": self._errors_count,
            "active_rooms": self._active_rooms,
            "uptime_seconds": round(uptime, 1),
            "memory_mb": round(memory_mb, 1),
            "messages_per_second": round(messages_per_second, 2),
            "per_room": dict(self._per_room),
        }

    def reset(self) -> None:
        """重置所有指标"""
        self._start_time = time.monotonic()
        self._messages_received = 0
        self._messages_published = 0
        self._messages_filtered = 0
        self._reconnect_count = 0
        self._errors_count = 0
        self._active_rooms = 0
        self._per_room.clear()

    @staticmethod
    def _get_memory_usage() -> float:
        """获取当前进程的内存占用（MB）

        使用 psutil 如果可用，否则使用 /proc/self/status 或 0。

        Returns:
            内存占用 MB
        """
        try:
            import psutil
            process = psutil.Process(os.getpid())
            return process.memory_info().rss / (1024 * 1024)
        except ImportError:
            pass

        # Linux fallback
        try:
            with open("/proc/self/status", "r") as f:
                for line in f:
                    if line.startswith("VmRSS:"):
                        # VmRSS 单位是 kB
                        return int(line.split()[1]) / 1024
        except (FileNotFoundError, ValueError, IndexError):
            pass

        # Windows fallback
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            # 简单返回 0，无法获取精确值
            return 0.0
        except Exception:
            return 0.0
