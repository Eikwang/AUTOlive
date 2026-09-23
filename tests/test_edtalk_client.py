# -*- coding: utf-8 -*-
"""edtalk_client 单测（Eng 任务 T1：5 条新 codepath）

覆盖：16kHz 校验与重采样 / base64 推送 / 单飞行句限流 / 降级容错 / 三段式透传
"""
import asyncio
import io
import os
import sys
import wave

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.edtalk_realtime.edtalk_client import (  # noqa: E402
    EDTalkClient,
    TARGET_RATE,
    _resample_pcm,
    load_wav_as_16k_pcm,
)


def _make_wav(rate: int, seconds: float, channels: int = 1) -> str:
    """构造测试 wav 文件（正弦近似——恒定值即可）。"""
    import struct
    buf = io.BytesIO()
    n = int(rate * seconds)
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        frames = b"".join(
            struct.pack("<h", 100) for _ in range(n * channels)
        )
        wf.writeframes(frames)
    path = os.path.join(
        os.path.dirname(__file__), f"_tmp_test_{rate}_{channels}.wav"
    )
    with open(path, "wb") as f:
        f.write(buf.getvalue())
    return path


# ---------- 1. 16kHz 校验与重采样 ----------


def test_resample_24k_to_16k():
    """edge-tts 默认 24kHz → 自动重采样到 16kHz（native E3）。"""
    src = _make_wav(24000, 1.0)
    pcm, rate = load_wav_as_16k_pcm(src)
    assert rate == 24000
    expected_len = 16000  # 1 秒 @16kHz
    assert len(pcm) // 2 == pytest.approx(expected_len, rel=0.01)
    os.remove(src)


def test_resample_16k_passthrough():
    """已是 16kHz：原样通过（不重采样）。"""
    src = _make_wav(16000, 0.5)
    pcm, rate = load_wav_as_16k_pcm(src)
    assert rate == 16000
    assert len(pcm) // 2 == 8000
    os.remove(src)


def test_resample_stereo_downmix():
    """立体声 → 取第一声道。"""
    pcm = _resample_pcm(b"\x01\x00\x02\x00" * 100, 2, 2, 16000)
    assert len(pcm) // 2 == 100


# ---------- 2. base64 推送（成功路径） ----------


def test_push_full_success(monkeypatch):
    """推送成功：POST /audio/push_full 且健康计数清零。"""
    client = EDTalkClient({"api_ip_port": "http://127.0.0.1:8000"})
    client.failure_count = 3  # 前置失败计数，成功后应清零

    sent = {}

    class FakeResp:
        status = 200

        async def json(self):
            return {"ok": True, "duration_s": 1.0, "processing": "async"}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    class FakeSession:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def post(self, url, json=None, headers=None):
            sent["url"] = url
            sent["pcm_len"] = len(json["pcm_base64"])
            return FakeResp()

        async def get(self, url, **kw):
            raise asyncio.CancelledError  # /status 探测直接失败（降级路径）

    import utils.edtalk_realtime.edtalk_client as mod
    monkeypatch.setattr(mod.aiohttp, "ClientSession", FakeSession)
    # /status 探测失败 → 降级为仅时间启发式（模拟 buffer_depth 不可用）
    monkeypatch.setattr(EDTalkClient, "_get_status", lambda self, s: asyncio.sleep(0, result={}))

    src = _make_wav(16000, 0.5)
    ok = asyncio.get_event_loop().run_until_complete(client.push_full(src, "测试"))
    assert ok is True
    assert client.failure_count == 0
    assert "/audio/push_full" in sent["url"]
    assert sent["pcm_len"] > 0
    os.remove(src)


# ---------- 3. 单飞行句限流 ----------


def test_push_full_rate_limit_blocks(monkeypatch):
    """单飞行句：上一句未完成（飞行窗口内）时下一句被阻塞等待。"""
    client = EDTalkClient({"api_ip_port": "http://127.0.0.1:8000"})
    import time as _time
    # 模拟上一句刚推送（5 秒音频 → 飞行窗口 6 秒）
    client.last_duration_s = 5.0
    client._flight_until = _time.monotonic()

    sleep_calls = []

    class FakeResp:
        status = 200

        async def json(self):
            return {"ok": True}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    class FakeSession:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def post(self, *a, **kw):
            return FakeResp()

        async def get(self, *a, **kw):
            raise asyncio.CancelledError

    async def fake_sleep(t):
        sleep_calls.append(t)

    import utils.edtalk_realtime.edtalk_client as mod
    monkeypatch.setattr(mod.aiohttp, "ClientSession", FakeSession)
    monkeypatch.setattr(mod.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(EDTalkClient, "_get_status", lambda self, s: asyncio.sleep(0, result={}))

    src = _make_wav(16000, 0.5)
    asyncio.get_event_loop().run_until_complete(client.push_full(src))
    # 应有一个 ≥ (5×1.2 - 0.5) 的阻塞 sleep（完成判定启发式生效）
    assert any(t >= 5.0 for t in sleep_calls), f"期望背压 sleep，实际 {sleep_calls}"
    os.remove(src)


# ---------- 4. 降级容错 ----------


def test_push_full_failure_degrades(monkeypatch):
    """连接失败：不抛异常，返回 False，失败计数递增。"""
    client = EDTalkClient({"api_ip_port": "http://127.0.0.1:9999"})

    class BoomSession:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            raise ConnectionError("refused")

        async def __aexit__(self, *a):
            return False

    import utils.edtalk_realtime.edtalk_client as mod
    monkeypatch.setattr(mod.aiohttp, "ClientSession", BoomSession)

    src = _make_wav(16000, 0.5)
    ok = asyncio.get_event_loop().run_until_complete(client.push_full(src))
    assert ok is False
    assert client.failure_count == 1
    assert "ConnectionError" in client.last_error
    os.remove(src)


# ---------- 5. 三段式错误透传 ----------


def test_push_full_error_passthrough(monkeypatch):
    """EDTalk 4xx 错误体 {problem,cause,fix} 原样记录（DX 义务②）。"""
    client = EDTalkClient({"api_ip_port": "http://127.0.0.1:8000"})

    class FakeResp:
        status = 413

        async def json(self):
            return {"detail": {
                "problem": "载荷超限",
                "cause": "音频 10MB 上限",
                "fix": "缩短句子或降低采样率",
            }}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    class FakeSession:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def post(self, *a, **kw):
            return FakeResp()

        async def get(self, *a, **kw):
            raise asyncio.CancelledError

    import utils.edtalk_realtime.edtalk_client as mod
    monkeypatch.setattr(mod.aiohttp, "ClientSession", FakeSession)
    monkeypatch.setattr(EDTalkClient, "_get_status", lambda self, s: asyncio.sleep(0, result={}))

    src = _make_wav(16000, 0.5)
    ok = asyncio.get_event_loop().run_until_complete(client.push_full(src))
    assert ok is False
    assert "载荷超限" in client.last_error
    os.remove(src)
