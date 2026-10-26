# -*- coding: utf-8 -*-
"""异常报警重构批次单测（2026-10-26）

覆盖：
- alarm_notifier 纯函数：三平台载荷 / 钉钉加签 / 响应校验 / 文本组装
- alarm_notifier 行为：模块开关门控（缺省=开）/ 60s 冷却（报警有、通知无）/
  渠道目标物化（钉钉带密钥）/ send_test 空配置与结果收集
- 六模块接线：TTS 合成失败 / LLM 通道失败汇入口 / 编排降级（见
  test_action_choreography.py 补充）/ 训练主播结果推送 / 训练声音链 worker
"""
import base64
import hashlib
import hmac
import os
import sys
import urllib.parse
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils import alarm_notifier


@pytest.fixture(autouse=True)
def _fresh_alarm():
    """每用例复位配置注入与冷却状态。"""
    alarm_notifier._reset_for_tests()
    yield
    alarm_notifier._reset_for_tests()


def _cfg(modules=None, **webhook):
    wh = {"feishu_url": "", "wecom_url": "", "dingtalk_url": "", "dingtalk_secret": ""}
    wh.update(webhook)
    return {"abnormal_alarm": {"modules": modules or {}, "webhook": wh}}


# ---------- 载荷与加签（纯函数） ----------


def test_feishu_payload_shape():
    p = alarm_notifier.feishu_payload("hi")
    assert p == {"msg_type": "text", "content": {"text": "hi"}}


def test_wecom_payload_shape():
    p = alarm_notifier.wecom_payload("hi")
    assert p == {"msgtype": "text", "text": {"content": "hi"}}


def test_dingtalk_payload_shape():
    p = alarm_notifier.dingtalk_payload("hi")
    assert p == {"msgtype": "text", "text": {"content": "hi"}}


def test_dingtalk_signed_url_hmac_correct():
    """加签算法：sign=base64(hmac_sha256(secret, "{ts}\\n{secret}"))，URL 编码拼接。"""
    secret = "SECtest123"
    url = alarm_notifier.dingtalk_signed_url(
        "https://oapi.dingtalk.com/robot/send?access_token=abc", secret)
    assert "timestamp=" in url and "&sign=" in url
    from urllib.parse import urlparse, parse_qs
    q = parse_qs(urlparse(url).query)
    ts = q["timestamp"][0]
    expected = base64.b64encode(hmac.new(
        secret.encode(), f"{ts}\n{secret}".encode(), hashlib.sha256).digest()).decode()
    assert q["sign"][0] == expected


def test_check_response_per_platform():
    class R:
        def __init__(self, status, data):
            self.status_code = status
            self._data = data

        def json(self):
            if self._data is None:
                raise ValueError("not json")
            return self._data

    assert alarm_notifier.check_response("feishu", R(200, {"code": 0}))[0] is True
    assert alarm_notifier.check_response("feishu", R(200, {"code": 9499}))[0] is False
    assert alarm_notifier.check_response("wecom", R(200, {"errcode": 0}))[0] is True
    assert alarm_notifier.check_response("dingtalk", R(200, {"errcode": 310000}))[0] is False
    assert alarm_notifier.check_response("feishu", R(500, {}))[0] is False
    ok, detail = alarm_notifier.check_response("wecom", R(200, None))
    assert ok is False and "JSON" in detail


def test_compose_contains_label_and_hint():
    text = alarm_notifier._compose("报警", alarm_notifier.MODULE_TTS, "合成失败", fix_hint="查服务")
    assert "【AUTOlive·报警】TTS生成" in text
    assert "内容：合成失败" in text
    assert "指引：查服务" in text
    # 未知模块键回退原名
    text2 = alarm_notifier._compose("通知", "unknown_x", "m")
    assert "unknown_x" in text2


# ---------- 开关门控与冷却 ----------


def test_module_switch_off_blocks_alarm_and_notice():
    alarm_notifier.set_config_dict(_cfg(modules={"tts": False}))
    assert alarm_notifier.alarm_error(alarm_notifier.MODULE_TTS, "x") is False
    assert alarm_notifier.alarm_notice(alarm_notifier.MODULE_TTS, "x") is False


def test_module_missing_defaults_enabled():
    """缺省=全开（存量无 modules 节向后兼容）。"""
    alarm_notifier.set_config_dict(_cfg())
    assert alarm_notifier._module_enabled(alarm_notifier.MODULE_LLM) is True
    assert alarm_notifier.alarm_error(alarm_notifier.MODULE_LLM, "x") is True


def test_error_cooldown_per_module():
    alarm_notifier.set_config_dict(_cfg())
    assert alarm_notifier.alarm_error(alarm_notifier.MODULE_TTS, "第一次") is True
    assert alarm_notifier.alarm_error(alarm_notifier.MODULE_TTS, "冷却内") is False
    # 其他模块不互扰
    assert alarm_notifier.alarm_error(alarm_notifier.MODULE_LLM, "LLM 第一次") is True
    # 冷却后可再报
    alarm_notifier._last_error_ts[alarm_notifier.MODULE_TTS] -= alarm_notifier._COOLDOWN_S + 1
    assert alarm_notifier.alarm_error(alarm_notifier.MODULE_TTS, "冷却后") is True


def test_notice_has_no_cooldown():
    alarm_notifier.set_config_dict(_cfg())
    assert alarm_notifier.alarm_notice(alarm_notifier.MODULE_TRAIN_STREAMER, "完成1") is True
    assert alarm_notifier.alarm_notice(alarm_notifier.MODULE_TRAIN_STREAMER, "完成2") is True


# ---------- 渠道目标物化与发送 ----------


def test_targets_only_configured_channels():
    alarm_notifier.set_config_dict(_cfg(
        feishu_url=" https://open.feishu.cn/open-apis/bot/v2/hook/x ",
        dingtalk_url="https://oapi.dingtalk.com/robot/send?access_token=y",
        dingtalk_secret="SECs"))
    targets = alarm_notifier._targets()
    assert [(t[0], t[1], t[2]) for t in targets] == [
        ("feishu", "https://open.feishu.cn/open-apis/bot/v2/hook/x", ""),
        ("dingtalk", "https://oapi.dingtalk.com/robot/send?access_token=y", "SECs"),
    ]


def test_dispatch_spawns_thread_per_channel(monkeypatch):
    alarm_notifier.set_config_dict(_cfg(feishu_url="u1", wecom_url="u2"))
    sent = []
    monkeypatch.setattr(alarm_notifier, "_send",
                        lambda ch, url, secret, text: sent.append((ch, url)))
    n = alarm_notifier._dispatch("text")
    assert n == 2
    # 守护线程异步发送——等待落定
    import time
    for _ in range(50):
        if len(sent) == 2:
            break
        time.sleep(0.02)
    assert sorted(sent) == [("feishu", "u1"), ("wecom", "u2")]


def test_send_test_no_targets_returns_empty():
    alarm_notifier.set_config_dict(_cfg())
    assert alarm_notifier.send_test() == {}


def test_send_test_collects_results(monkeypatch):
    alarm_notifier.set_config_dict(_cfg(feishu_url="u1", wecom_url="u2"))
    monkeypatch.setattr(alarm_notifier, "_send",
                        lambda ch, url, secret, text: (ch == "feishu", "detail"))
    results = alarm_notifier.send_test()
    assert set(results) == {"feishu", "wecom"}
    assert results["feishu"] == (True, "detail")
    assert results["wecom"] == (False, "detail")


def test_send_never_raises(monkeypatch):
    alarm_notifier.set_config_dict(_cfg(feishu_url="u1"))
    def _boom(*a, **k):
        raise RuntimeError("network down")
    monkeypatch.setattr(alarm_notifier.requests, "post", _boom)
    ok, detail = alarm_notifier._send("feishu", "u1", "", "t")
    assert ok is False and "RuntimeError" in detail


# ---------- 六模块接线 ----------


def test_tts_synthesis_failure_raises_module_alarm(monkeypatch):
    """TTS 合成失败 → MODULE_TTS 报警（synthesis_manager 接线）。"""
    from tests.test_audio_playback_review import _FakeConfig, _SynthHarness

    calls = []
    monkeypatch.setattr(
        alarm_notifier, "alarm_error",
        lambda module, message, fix_hint="": calls.append((module, message)) or True)

    h = _SynthHarness(_FakeConfig(extra={("play_audio", "enable"): False}))
    try:
        msg = {"type": "comment", "tts_type": "edge_tts", "content": " hi ",
               "config": {"max_len": 100, "max_char_len": 100}}

        async def _fail_tts(m):
            m["result"] = {"code": 500, "msg": "失败"}
            return m

        h.tts_handle = _fail_tts
        out = _run(h.my_play_voice(msg))
        assert out is False
        assert calls and calls[0][0] == alarm_notifier.MODULE_TTS
        assert "edge_tts" in calls[0][1]
    finally:
        h.close()


def _run(coro):
    import asyncio
    return asyncio.new_event_loop().run_until_complete(coro)


def test_llm_failure_fallback_hook_raises_module_alarm(monkeypatch):
    """LLM 通道失败汇入口（_try_local_qa_fallback）→ MODULE_LLM 报警。"""
    from utils.handlers.chat_handler import ChatHandler

    calls = []
    monkeypatch.setattr(
        alarm_notifier, "alarm_error",
        lambda module, message, fix_hint="": calls.append((module, message)) or True)

    class _Cfg:
        def get(self, *keys):
            return False  # local_qa.enable=false → 托底禁用路径

    my = MagicMock()
    my.config = _Cfg()
    handler = ChatHandler(my)
    out = handler._try_local_qa_fallback("custom_llm", {"username": "u"}, "通道返回空")
    assert out is None
    assert calls and calls[0][0] == alarm_notifier.MODULE_LLM
    assert "custom_llm" in calls[0][1] and "通道返回空" in calls[0][1]


def test_train_streamer_result_push():
    """训练主播结果推送：完成=通知 / 失败=报警 / 主动停止不发。"""
    calls = []
    monkey_mod = alarm_notifier
    orig_notice, orig_error = monkey_mod.alarm_notice, monkey_mod.alarm_error
    monkey_mod.alarm_notice = lambda m, msg: calls.append(("notice", m, msg)) or True
    monkey_mod.alarm_error = lambda m, msg, fix_hint="": calls.append(("error", m, msg)) or True
    try:
        from utils.train_streamer.pipeline import notify_train_result
        notify_train_result(True, "训练完成（总耗时 1.0 小时）。")
        notify_train_result(False, "训练编排异常：boom")
        notify_train_result(False, "训练未完成", stop_requested=True)
        assert calls == [
            ("notice", alarm_notifier.MODULE_TRAIN_STREAMER, "训练完成（总耗时 1.0 小时）。"),
            ("error", alarm_notifier.MODULE_TRAIN_STREAMER, "训练编排异常：boom"),
        ]
    finally:
        monkey_mod.alarm_notice, monkey_mod.alarm_error = orig_notice, orig_error


def test_choreography_switch_failure_raises_orchestration_alarm(monkeypatch):
    """编排切段失败降级 → MODULE_ORCHESTRATION 报警（choreographer 接线）。"""
    from tests.test_action_choreography import EngineHarness, switch_cmd

    calls = []
    monkeypatch.setattr(alarm_notifier, "alarm_error",
                        lambda m, msg, fix_hint="": calls.append((m, msg)) or True)
    h = EngineHarness()
    d = h.trigger_gift()
    step = switch_cmd(d["commands"])
    h.engine.on_switch_result(step.step_id, False, h.now)
    assert calls and calls[0][0] == alarm_notifier.MODULE_ORCHESTRATION


def test_choreography_restore_exhausted_raises_alarm(monkeypatch):
    """恢复默认重试耗尽 → MODULE_ORCHESTRATION 报警（一次性，重试中不报）。"""
    from tests.test_action_choreography import EngineHarness
    from utils.orchestration.choreographer import RESTORE_RETRY_MAX

    calls = []
    monkeypatch.setattr(alarm_notifier, "alarm_error",
                        lambda m, msg, fix_hint="": calls.append((m, msg)) or True)
    h = EngineHarness()
    for _ in range(RESTORE_RETRY_MAX):
        assert h.engine.on_restore_result(False, h.now)  # 重试中不报警
    assert h.engine.on_restore_result(False, h.now) == []
    assert len(calls) == 1 and calls[0][0] == alarm_notifier.MODULE_ORCHESTRATION


def test_train_voice_pipeline_outcome():
    """训练声音终态判定：stopped（停止不发）/ failed（报警）/ ok（通知）。"""
    from frontend.ui.tabs.train_voice import pipeline_outcome
    from utils.train_voice_pipeline import FAILED, PENDING, SUCCESS

    class _P:
        def __init__(self, states, stop=False):
            self._states = states
            self.stop_requested = stop

        def stage_states(self):
            return self._states

    assert pipeline_outcome(_P({1: SUCCESS})) == "ok"
    assert pipeline_outcome(_P({1: FAILED, 2: SUCCESS})) == "failed"
    assert pipeline_outcome(_P({1: PENDING})) == "failed"
    assert pipeline_outcome(_P({1: SUCCESS}, stop=True)) == "stopped"
