"""fixtures 录制器：从消息流录制 JSONL（回放器同格式）"""

import json
import time
from pathlib import Path
from typing import Optional


class FixtureRecorder:
    """把统一消息（线格式 dict）录制为 fixtures JSONL

    每行：{"delay_ms": <距上一条毫秒>, "wire": {...}}
    与 replayer.load_jsonl/replay 同格式，支持离线演示与回归回放。
    """

    def __init__(self, path: str, platform: str = "unknown"):
        self._path = Path(path)
        self._platform = platform
        self._last_t: Optional[float] = None
        self._count = 0
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(self._path, "w", encoding="utf-8")
        self._fh.write(f"# fixtures recorded at {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} platform={platform}\n")

    def record(self, wire: dict) -> None:
        now = time.monotonic()
        delay_ms = 0 if self._last_t is None else int((now - self._last_t) * 1000)
        self._last_t = now
        self._fh.write(json.dumps({"delay_ms": delay_ms, "wire": wire}, ensure_ascii=False) + "\n")
        self._count += 1

    async def record_async(self, wire: dict) -> None:
        self.record(wire)

    def close(self) -> int:
        self._fh.close()
        return self._count

    @property
    def count(self) -> int:
        return self._count
