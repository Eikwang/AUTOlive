"""背压分级缓冲（契约 v1 阶段 0 工程前置 M/P）

有界环形缓冲 + 按消息类型分级丢弃：
- 满时先挤掉 drop_order 中最前类型的最旧条目（默认 LIKE → ENTER_ROOM → DANMU）
- 无可挤条目时按 overflow 方向处理：oldest=丢最老条目（无论类型）；newest=拒绝新条目
- 窗口丢弃计数按类型聚合，由推送管线触发窗口 GAP + BACKPRESSURE 上报
"""

import time
from collections import deque
from typing import Dict, Optional, Tuple


class BackpressureBuffer:
    """有界消息缓冲（分级丢弃语义见模块文档）"""

    def __init__(self, capacity: int, drop_order: list[str], overflow: str = "oldest"):
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        if overflow not in ("oldest", "newest"):
            raise ValueError("overflow must be 'oldest' or 'newest'")
        self._capacity = capacity
        self._drop_order = [t.upper() for t in drop_order]
        self._overflow = overflow
        self._items: deque[Tuple[str, float, dict]] = deque()  # (type, enqueue_ts, wire)
        self._dropped: Dict[str, int] = {}

    def __len__(self) -> int:
        return len(self._items)

    @property
    def capacity(self) -> int:
        return self._capacity

    def put(self, msg_type: str, wire: dict) -> bool:
        """入队一条消息；返回 True=入队成功，False=新条目被丢弃（newest 溢出）"""
        if len(self._items) < self._capacity:
            self._items.append((msg_type.upper(), time.monotonic(), wire))
            return True

        # 满：按 drop_order 挤掉最低价值类型的最旧条目
        for t in self._drop_order:
            if t == msg_type.upper() and self._overflow == "newest":
                # 目标类型是它自己时，挤旧条目 vs 丢新条目等价于保留旧——仍挤旧
                pass
            for idx, (buf_type, _, _) in enumerate(self._items):
                if buf_type == t:
                    self._items.rotate(-idx)
                    dropped_type, _, _ = self._items.popleft()
                    self._items.rotate(idx)
                    self._dropped[dropped_type] = self._dropped.get(dropped_type, 0) + 1
                    self._items.append((msg_type.upper(), time.monotonic(), wire))
                    return True

        # 无 drop_order 类型可挤：按溢出方向
        if self._overflow == "newest":
            self._dropped[msg_type.upper()] = self._dropped.get(msg_type.upper(), 0) + 1
            return False
        dropped_type, _, _ = self._items.popleft()
        self._dropped[dropped_type] = self._dropped.get(dropped_type, 0) + 1
        self._items.append((msg_type.upper(), time.monotonic(), wire))
        return True

    def drain(self) -> list[dict]:
        """取出全部缓冲消息（保持顺序）"""
        wires = [wire for _, _, wire in self._items]
        self._items.clear()
        return wires

    def take_window_dropped(self) -> Dict[str, int]:
        """取走当前窗口的丢弃计数（调用方负责周期性取走并上报）"""
        taken = self._dropped
        self._dropped = {}
        return taken


class BackpressureWindow:
    """背压窗口聚合器：周期取走丢弃计数并判定是否触发 GAP/BACKPRESSURE 上报"""

    def __init__(self, report_seconds: int = 60):
        if report_seconds <= 0:
            raise ValueError("report_seconds must be positive")
        self._report_seconds = report_seconds
        self._last_report = time.monotonic()

    def should_report(self, buffer: BackpressureBuffer) -> Optional[Dict[str, int]]:
        """到达报告周期且窗口内有丢弃时，取走并返回丢弃计数；否则 None"""
        now = time.monotonic()
        if now - self._last_report < self._report_seconds:
            return None
        self._last_report = now
        dropped = buffer.take_window_dropped()
        if not dropped:
            return None
        return dropped

    @property
    def report_seconds(self) -> int:
        return self._report_seconds
