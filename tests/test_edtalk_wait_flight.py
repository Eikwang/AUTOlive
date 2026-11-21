# -*- coding: utf-8 -*-
"""P2 EDTalk 音频末尾截断修复回归（2026-11-21 用户实测批次）。

用户症状：数字人流程音频经常最后一个字没说完，而 TTS 生成完整。

根因：_wait_flight_complete 的 buffer_depth 轮询上限固定 1.0s——推理慢于
实时时帧锁定 pacing（'hold'）拉长真实播放时长，轮询超时强制放行 → 下一条
push_full 触发服务端 flush 替换 → 上一句尾字被截断。

修复：轮询上限改为 max(_POLL_DEADLINE, 上一句时长×_POLL_DEADLINE_SCALE)。
正常路径 buffer 播完即回落、轮询提前 break，不产生额外白等。
"""
import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import utils.edtalk_realtime.edtalk_client as ec  # noqa: E402
from utils.edtalk_realtime.edtalk_client import EDTalkClient  # noqa: E402


class _FakeTime:
    """假时钟：monotonic 同步系 + asyncio.sleep 协程系统一由测试推进。"""

    def __init__(self, start=1000.0):
        self.t = start

    def monotonic(self):
        return self.t

    def sleep(self, s):
        self.t += s

    def strftime(self, *args, **kwargs):
        return "00:00:00"

    async def a_sleep(self, s):
        self.t += s


@pytest.fixture()
def fake_time(monkeypatch):
    fake = _FakeTime()
    monkeypatch.setattr(ec.time, "monotonic", fake.monotonic)
    monkeypatch.setattr(ec.time, "sleep", fake.sleep)
    monkeypatch.setattr(ec.time, "strftime", fake.strftime)
    monkeypatch.setattr(ec.asyncio, "sleep", fake.a_sleep)
    return fake


def _client_with_stuck_buffer(fake_time, duration_s=3.0):
    client = EDTalkClient({"api_ip_port": "http://127.0.0.1:8000"})
    client._flight_until = fake_time.t
    client.last_duration_s = duration_s

    async def _stuck_status(session):
        return {"buffer_depth": 100, "preroll_frames": 8}

    client._get_status = _stuck_status  # buffer 永不回落（模拟推理慢于实时）
    return client


def test_poll_deadline_scaled_with_duration(fake_time):
    """buffer 永不回落：轮询持续到 max(1.0, 时长×scale) 才强制放行。

    旧实现固定 1s 上限 → 3s 句子总等待 ≈ 3.15+1.0=4.15s 即放行 → 下一条
    push flush 截断尾字；修复后总等待 ≥ 3.15+3.0=6.15s。"""
    client = _client_with_stuck_buffer(fake_time, duration_s=3.0)
    asyncio.run(client._wait_flight_complete(object(), 3.0))
    assert fake_time.t - client._flight_until >= 6.1


def test_poll_releases_immediately_on_buffer_drain(fake_time):
    """正常路径：buffer 回落（<= preroll）→ 轮询立即 break，无额外白等。"""
    client = _client_with_stuck_buffer(fake_time, duration_s=3.0)

    async def _drained_status(session):
        return {"buffer_depth": 5, "preroll_frames": 8}

    client._get_status = _drained_status
    asyncio.run(client._wait_flight_complete(object(), 3.0))
    # 仅 min_flight（3.15s）耗尽，轮询首查即放行
    assert fake_time.t - client._flight_until < 3.6


def test_poll_degrades_on_status_unavailable(fake_time):
    """/status 不可用：不阻塞推送（降级为仅时间启发式，返回不抛）。"""
    client = _client_with_stuck_buffer(fake_time, duration_s=1.0)

    async def _dead_status(session):
        return {}

    client._get_status = _dead_status
    asyncio.run(client._wait_flight_complete(object(), 1.0))  # 不抛即通过


# ------------------------------------------------- 精确完成判定（2026-11-21 撕裂修复）

def test_poll_releases_on_seg_completion(fake_time):
    """精确完成判定：seg_chunk 三字段齐备（produced>=total 且
    displayed>=produced）→ 立即放行——即使 buffer_depth 恒高（生产滞后时
    深度骤降/骤升均失真，深度启发式不可靠；句尾撕裂根因）。"""
    client = _client_with_stuck_buffer(fake_time, duration_s=3.0)
    calls = {'n': 0}

    async def _seg_status(session):
        calls['n'] += 1
        if calls['n'] < 2:
            return {"buffer_depth": 100, "seg_chunk_total": 75,
                    "seg_chunk_produced": 40, "seg_chunk_displayed": 30}
        return {"buffer_depth": 100, "seg_chunk_total": 75,
                "seg_chunk_produced": 75, "seg_chunk_displayed": 75}

    client._get_status = _seg_status
    asyncio.run(client._wait_flight_complete(object(), 3.0))
    # 完成判定通过即放行（旧深度启发式在 depth=100 下要白等到 6.15s 超时）
    assert fake_time.t - client._flight_until < 4.0


def test_poll_waits_when_seg_incomplete(fake_time):
    """seg 字段在但未完成（显示<产出）：不提前放行——继续轮询至按时长缩放的
    上限（保护句尾：下一条 push flush 不会遗弃未播完的 chunk 帧）。"""
    client = _client_with_stuck_buffer(fake_time, duration_s=3.0)

    async def _incomplete(session):
        return {"buffer_depth": 100, "seg_chunk_total": 75,
                "seg_chunk_produced": 75, "seg_chunk_displayed": 30}

    client._get_status = _incomplete
    asyncio.run(client._wait_flight_complete(object(), 3.0))
    assert fake_time.t - client._flight_until >= 6.1  # min_flight 3.15 + 上限 3.0
