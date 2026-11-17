# -*- coding: UTF-8 -*-
"""
EDTalk 打断闭嘴守卫测试（2026-11-16 聊天审查批次）

背景：音频进入口型推理的方式=整句 push_full——服务端只要还有音频在消费
就持续推理口型。打断闭嘴的实现=推送静音触发服务端 flush 替换（d3 替换
语义），不依赖新契约端点。

覆盖义务：
- flush_silence：payload 走 /audio/push_full 唯一有效通道、静音 PCM 长度
  正确、成功后同步飞行启发式状态（last_duration_s/_flight_until）——
  下一句真实推送不被旧句飞行等待拖延
- 失败降级：HTTP 非 200 / 网络异常 → {"ok": False} 且不改客户端状态
- stop_current_audio 接线在 tests/features/test_builtin_captions.py
  （test_stop_current_audio_dispatch，2026-11-16 批次已更新）
"""

import base64
import types

import pytest

from utils.edtalk_realtime.edtalk_client import TARGET_RATE, EDTalkClient


@pytest.fixture
def client(monkeypatch):
    """测试客户端：api_ip_port 走 config 存量回退（不读运行时文件）。"""
    monkeypatch.setattr(
        "utils.edtalk_realtime.edtalk_client.resolve_api_base",
        lambda cfg: "http://127.0.0.1:8000",
        raising=False,
    )
    import utils.edtalk_realtime.edtalk_client as mod
    # resolve_api_base 在构造函数内延迟导入，补一个模块级桩以防导入路径差异
    if not hasattr(mod, "resolve_api_base"):
        monkeypatch.setattr("utils.edtalk_realtime.runtime.resolve_api_base",
                            lambda cfg: "http://127.0.0.1:8000")
    return EDTalkClient({"api_ip_port": "http://127.0.0.1:8000"})


def _stub_post(resp_status=200):
    captured = {}

    def _post(url, json=None, headers=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        captured["timeout"] = timeout
        return types.SimpleNamespace(status_code=resp_status, json=lambda: {"processing": True})

    return _post, captured


def test_flush_silence_payload_and_state_sync(client, monkeypatch):
    """成功路径：通道/静音长度正确，飞行启发式状态同步（防下一句被旧句拖延）。"""
    post, captured = _stub_post(200)
    monkeypatch.setattr("requests.post", post)

    client.last_duration_s = 7.7  # 模拟上一句 7.7s 在飞
    ret = client.flush_silence()

    assert ret == {"ok": True}
    assert captured["url"] == "http://127.0.0.1:8000/audio/push_full"
    pcm = base64.b64decode(captured["json"]["pcm_base64"])
    assert pcm == b"\x00\x00" * int(TARGET_RATE * 0.2), "payload 应为 0.2s int16 静音"
    assert client.last_duration_s == 0.2, "飞行启发式应与服务端缓冲（静音）同步"
    assert client._flight_until > 0.0
    assert client.last_push_time > 0.0


def test_flush_silence_http_failure_degrades(client, monkeypatch):
    """HTTP 非 200：降级 {"ok": False}，不改飞行状态。"""
    post, _ = _stub_post(500)
    monkeypatch.setattr("requests.post", post)

    client.last_duration_s = 7.7
    ret = client.flush_silence()
    assert ret.get("ok") is False
    assert client.last_duration_s == 7.7, "失败不得改写飞行启发式状态"


def test_flush_silence_network_error_degrades(client, monkeypatch):
    """网络异常：降级 {"ok": False} 不抛（打断主流程不被阻断）。"""
    def _boom(*a, **k):
        raise ConnectionError("refused")

    monkeypatch.setattr("requests.post", _boom)
    ret = client.flush_silence()
    assert ret.get("ok") is False
    assert "refused" in ret.get("error", "")


def test_flush_silence_custom_duration(client, monkeypatch):
    """自定义时长透传（payload 长度随 duration_s 缩放）。"""
    post, captured = _stub_post(200)
    monkeypatch.setattr("requests.post", post)

    client.flush_silence(duration_s=0.5)
    pcm = base64.b64decode(captured["json"]["pcm_base64"])
    assert pcm == b"\x00\x00" * int(TARGET_RATE * 0.5)
