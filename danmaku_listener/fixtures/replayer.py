"""fixtures 回放器：离线演示与回归测试的统一入口

JSONL 格式（每行一条）：
    {"delay_ms": <距上一条毫秒>, "wire": {契约 v1 线格式}}

用途：
1. demo CLI 离线演示（``danmaku-listen`` 的 --replay 模式）——无需真实开播房间
2. 协议解析回归测试的 fixtures 载体（录制的真实流量转换后回放）
3. 脚本化场景（重连中断 + GAP 补发 + 去重命中）按同一格式编排
"""

import asyncio
import json
from pathlib import Path
from typing import AsyncIterator, Dict, List


def load_jsonl(path: str) -> List[Dict]:
    """读取 fixtures JSONL，返回 [{delay_ms, wire}] 列表"""
    events: List[Dict] = []
    with open(path, encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError as e:
                raise ValueError(f"{path}:{line_no} 不是合法 JSON: {e}") from e
    return events


async def replay(
    path: str,
    sink,
    speed: float = 1.0,
    loop: bool = False,
) -> AsyncIterator[Dict]:
    """按录制时间差回放事件流

    Args:
        path: JSONL 文件路径
        sink: 异步回调，接收 wire dict
        speed: 回放速度倍率（>1 加速）
        loop: 循环回放（演示模式）

    Yields:
        每条事件的 wire dict（便于测试断言）
    """
    events = load_jsonl(path)
    while True:
        prev_delay = 0
        for ev in events:
            delay_ms = max(0, int(ev.get("delay_ms", 0) / speed))
            if delay_ms:
                await asyncio.sleep(delay_ms / 1000.0)
            await sink(ev["wire"])
            yield ev["wire"]
        if not loop:
            return


def validate_events(path: str) -> dict:
    """校验 fixtures 文件：线格式可反序列化为契约消息；返回统计"""
    from danmaku_listener.contract.models import UnifiedMessage

    events = load_jsonl(path)
    stats = {"total": len(events), "by_type": {}, "invalid": 0}
    for ev in events:
        wire = ev.get("wire") or {}
        try:
            UnifiedMessage.from_wire(wire)
            stats["by_type"][wire.get("type", "?")] = stats["by_type"].get(wire.get("type", "?"), 0) + 1
        except Exception:
            stats["invalid"] += 1
    return stats
