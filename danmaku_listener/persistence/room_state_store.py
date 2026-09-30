"""每房间状态持久化（契约 v1 崩溃恢复语义）

持久化内容：seq（房间内单调序号）、live 在线状态与转换时间、last-received 时间戳。
用途：进程崩溃/重启后恢复 seq 连续性并支撑 GAP 补发的近似区间计算（Eng N）。

设计：
- 内存为权威态，JSON 文件为快照（防抖：标脏 + 按间隔刷盘 + stop 时强制刷盘）
- 持久化失败不阻断消息流（fail-open，Eng R 项口径）：写失败仅记录告警，
  恢复后 GAP 以 approx=True 降级
"""

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional

from loguru import logger


class RoomStateStore:
    """每房间状态 JSON 快照存储（单文件多房间）"""

    def __init__(self, path: str, flush_interval_seconds: float = 5.0):
        self._path = Path(path)
        self._flush_interval = flush_interval_seconds
        self._data: Dict[str, Dict[str, Any]] = {}
        self._dirty = False
        self._last_flush = 0.0
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            self._data = json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            # 快照损坏按无历史处理（fail-open）：GAP 以 approx 降级
            logger.warning(f"RoomStateStore 快照损坏，按无历史恢复: {e}")
            self._data = {}

    def update(self, room_id: str, **fields: Any) -> None:
        """更新房间状态字段并标脏"""
        entry = self._data.setdefault(room_id, {})
        entry.update(fields)
        entry["updated_at"] = int(time.time())
        self._dirty = True

    def get(self, room_id: str, key: str, default: Any = None) -> Any:
        return self._data.get(room_id, {}).get(key, default)

    def flush(self, force: bool = False) -> None:
        """按防抖间隔刷盘；force=True 立即刷（stop/关键事件）"""
        now = time.monotonic()
        if not self._dirty:
            return
        if not force and now - self._last_flush < self._flush_interval:
            return
        tmp = self._path.with_suffix(".tmp")
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps(self._data, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self._path)  # 原子替换，避免半写快照
            self._dirty = False
            self._last_flush = now
        except OSError as e:
            logger.warning(f"RoomStateStore 刷盘失败（fail-open）: {e}")

    def close(self) -> None:
        self.flush(force=True)
