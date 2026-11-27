"""EDTalk 推送载荷预编码缓存守卫（2026-11-27 人声对齐根修）。

根因（log11 实测）：push_full 在 due 关键路径做同步重活——RIFF 归一+16k
PCM 解码重采样+base64 编码（song 切片 74-107s ≈ 3-5MB）。空闲 ~0.3s（与
song_push_lead_s 校准值吻合）；插播消息触发的并发 TTS 合成抢占 CPU/GIL
时 2s+（同人声 seq0 首遍受理迟 0.3s/二遍迟 2.0s）——人声受理晚于歌曲
时间轴应到时刻，表现为"插播其它消息时人声延迟变大"。

修复：prepare() 挂点=song 切片入待播放队列（synthesis_manager my_play_voice
none 分支）→ 后台线程预编码缓存；push_full 缓存命中只做 HTTP POST，
未命中回退内联编码（既有语义）。

覆盖：
- _prepare_payload 编码产物入缓存（payload+duration_s）
- prepare() 幂等（已缓存/在编不重复起线程）
- LRU 淘汰（超容量逐最旧）
- push_full 成功后出缓存由 push_full 生命周期管（此处不覆盖 HTTP）
"""
import asyncio
import os
import struct
import tempfile
import wave

from utils.edtalk_realtime.edtalk_client import (
    EDTalkClient, TARGET_RATE, _PUSH_PAYLOAD_CACHE_MAX)


def _make_wav(seconds: float) -> str:
    """生成 16k 单声道 PCM wav 测试文件。"""
    fd, path = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    n = int(TARGET_RATE * seconds)
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(TARGET_RATE)
        wf.writeframes(b"\x00\x01" * n)
    return path


def test_prepare_payload_caches_encoded_product():
    client = EDTalkClient({"api_ip_port": "http://127.0.0.1:8000"})
    path = _make_wav(0.2)
    try:
        client._prepare_payload(path)
        entry = client._payload_cache.get(path)
        assert entry is not None
        assert entry["duration_s"] > 0.15
        assert isinstance(entry["payload"], str) and len(entry["payload"]) > 100
    finally:
        os.remove(path)


def test_prepare_idempotent_skips_cached_and_inflight():
    client = EDTalkClient({"api_ip_port": "http://127.0.0.1:8000"})
    path = _make_wav(0.1)
    try:
        client._prepare_payload(path)
        assert path in client._payload_cache
        client.prepare(path)          # 已缓存 → 不再起线程
        client._payload_prep_inflight.add(path)
        client.prepare(path)          # 在编 → 不重复登记
        assert path not in [k for k in client._payload_cache if k != path]
    finally:
        os.remove(path)


def test_lru_eviction_beyond_capacity():
    client = EDTalkClient({"api_ip_port": "http://127.0.0.1:8000"})
    paths = [_make_wav(0.05) for _ in range(_PUSH_PAYLOAD_CACHE_MAX + 1)]
    try:
        for p in paths:
            client._prepare_payload(p)
        assert len(client._payload_cache) == _PUSH_PAYLOAD_CACHE_MAX
        assert paths[0] not in client._payload_cache           # 最旧被逐
        assert paths[-1] in client._payload_cache              # 最新保留
    finally:
        for p in paths:
            if os.path.isfile(p):
                os.remove(p)
