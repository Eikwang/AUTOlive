"""去重过滤器

基于消息 ID 的滑动窗口去重，防止重复推送。
"""

import time
from collections import deque
from typing import Deque, Dict, Optional

from loguru import logger


class DedupFilter:
    """滑动窗口去重过滤器

    使用固定大小的队列记录最近的消息 ID，新消息如果在窗口内已存在则被过滤。
    维护并行的 set 用于 O(1) 查重，避免每次从 deque 重建集合。
    """

    def __init__(self, window_size: int = 300, window_seconds: int = 300):
        """初始化去重过滤器

        Args:
            window_size: 滑动窗口大小（条消息，容量上限）
            window_seconds: 时间淘汰窗口（秒）——契约 v1 有界结构双参数（时间淘汰+容量上限）
        """
        self.window_size = window_size
        self.window_seconds = window_seconds
        # room_id -> deque of (msg_id, timestamp)
        self._cache: Dict[str, Deque[tuple[str, float]]] = {}
        # room_id -> set of msg_id（并行索引，O(1) 查重）
        self._id_sets: Dict[str, set] = {}

    def should_filter(self, room_id: str, msg_id: str) -> bool:
        """判断是否应该过滤该消息

        Args:
            room_id: 房间 ID
            msg_id: 消息 ID

        Returns:
            True 如果消息应该被过滤（重复），False 如果应该放行
        """
        if not room_id or not msg_id:
            return False

        now = time.time()

        # 获取或创建该房间的队列和 ID 集合
        if room_id not in self._cache:
            self._cache[room_id] = deque()
            self._id_sets[room_id] = set()
        if room_id not in self._id_sets:
            # 从现有队列重建 ID 集合（兼容手动插入队列的情况）
            self._id_sets[room_id] = {mid for mid, _ in self._cache[room_id]}

        queue = self._cache[room_id]
        id_set = self._id_sets[room_id]

        # 清理过期记录（超过时间窗口）
        while queue and now - queue[0][1] > self.window_seconds:
            expired_id, _ = queue.popleft()
            id_set.discard(expired_id)

        # O(1) 查重
        if msg_id in id_set:
            logger.debug(f"Dedup filter hit: room={room_id}, msg_id={msg_id}")
            return True

        # 添加新记录
        queue.append((msg_id, now))
        id_set.add(msg_id)

        # 如果队列超过窗口大小，移除最旧的
        while len(queue) > self.window_size:
            expired_id, _ = queue.popleft()
            id_set.discard(expired_id)

        return False

    def clear_room(self, room_id: str) -> None:
        """清除指定房间的缓存

        Args:
            room_id: 房间 ID
        """
        if room_id in self._cache:
            del self._cache[room_id]
            del self._id_sets[room_id]
            logger.debug(f"Cleared dedup cache for room: {room_id}")

    def clear_all(self) -> None:
        """清除所有缓存"""
        self._cache.clear()
        self._id_sets.clear()
        logger.debug("Cleared all dedup cache")

    def get_stats(self) -> Dict[str, int]:
        """获取统计信息

        Returns:
            包含房间数和总消息数的字典
        """
        total_messages = sum(len(q) for q in self._cache.values())
        return {
            "rooms": len(self._cache),
            "total_messages": total_messages,
        }