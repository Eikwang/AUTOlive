# -*- coding: utf-8 -*-
"""2026-11-17 热切机制批次守卫测试。

覆盖客户端/引擎类配置热切语义（快照固化 → 每请求现读）：
- Custom_LLM：model/api_key/base_url/temperature 每请求现读（config_obj 注入）
- Fastrag_LLM：host/port 每请求现读重建查询端点；旧签名（无 config_obj）兼容
- EDTalkClient：api_token/api_ip_port 现读 + enable 关断即时停止新发送
- AUDIO_PLAY_CENTER._hot_value：共享 Config 内存现读优先，回退链完整
- GPT_Model.set_model_config：config_obj 透传

config_obj 用 _FakeConfig 替身（.get(*keys) 接口，对齐 Config 实时读语义），
零磁盘 IO、不依赖真实 config.json。
"""

import asyncio
import time
import unittest
from unittest.mock import patch

from utils.gpt_model.custom_llm import Custom_LLM
from utils.gpt_model.fastrag_llm import Fastrag_LLM
from utils.gpt_model.gpt import GPT_Model
from utils.edtalk_realtime.edtalk_client import EDTalkClient


class _FakeConfig:
    """Config 替身：嵌套节 .get(*keys)（对齐 utils/config.py Config.get 语义）。"""

    def __init__(self, data):
        self._d = data

    def get(self, *keys):
        r = self._d
        for k in keys:
            if r is None:
                return None
            r = r.get(k) if isinstance(r, dict) else None
        return r

    def set_section(self, key, value):
        """模拟热重载后的新节（Config.reload 替换类缓存等价语义）。"""
        self._d[key] = value


class TestCustomLLMHotSwap(unittest.TestCase):
    """Custom_LLM 每请求现读（2026-11-17 热切机制批次）。"""

    def _make(self, cfg):
        return Custom_LLM(dict(cfg), config_obj=_FakeConfig({"custom_llm": dict(cfg)}))

    def test_api_key_hotswap(self):
        llm = self._make({"base_url": "http://127.0.0.1:11434/v1", "model": "qwen",
                          "api_key": "old-key"})
        self.assertEqual(llm._build_headers()["Authorization"], "Bearer old-key")
        # 密钥变更（热重载）→ 下一请求即用新值（客户端对象复用，零重建）
        llm._config_obj.set_section(
            "custom_llm", {"base_url": "http://127.0.0.1:11434/v1",
                           "model": "qwen", "api_key": "new-key"})
        self.assertEqual(llm._build_headers()["Authorization"], "Bearer new-key")

    def test_model_and_base_url_hotswap(self):
        llm = self._make({"base_url": "https://a.com/v1", "model": "m1", "api_key": ""})
        body = llm._build_body("hi")
        self.assertEqual(body["model"], "m1")
        self.assertEqual(llm._chat_completions_url(), "https://a.com/v1/chat/completions")
        llm._config_obj.set_section(
            "custom_llm", {"base_url": "https://b.com/v1", "model": "m2", "api_key": ""})
        self.assertEqual(llm._build_body("hi")["model"], "m2")
        self.assertEqual(llm._chat_completions_url(), "https://b.com/v1/chat/completions")

    def test_temperature_hotswap(self):
        llm = self._make({"base_url": "http://x/v1", "model": "m", "temperature": "0.3"})
        self.assertEqual(llm._build_body("hi")["temperature"], 0.3)
        llm._config_obj.set_section(
            "custom_llm", {"base_url": "http://x/v1", "model": "m", "temperature": "0.9"})
        self.assertEqual(llm._build_body("hi")["temperature"], 0.9)

    def test_legacy_signature_snapshot_fallback(self):
        """旧签名（无 config_obj）维持构造快照语义（兼容存量调用/测试）。"""
        llm = Custom_LLM({"base_url": "http://x/v1", "model": "m1", "api_key": "k"})
        self.assertEqual(llm._build_body("hi")["model"], "m1")

    def test_section_missing_falls_back_to_snapshot(self):
        llm = Custom_LLM({"base_url": "http://x/v1", "model": "snap", "api_key": ""},
                         config_obj=_FakeConfig({}))
        self.assertEqual(llm._build_body("hi")["model"], "snap")


class TestFastragHotSwap(unittest.TestCase):
    """Fastrag_LLM host/port 每请求现读（2026-11-17 热切机制批次）。"""

    def test_host_port_hotswap(self):
        cfg_obj = _FakeConfig({"fastrag": {"host": "localhost", "port": 11420}})
        f = Fastrag_LLM({"host": "localhost", "port": 11420}, config_obj=cfg_obj)
        url, _, host_port = f._query_urls()
        self.assertEqual(url, "http://localhost:11420/query")
        cfg_obj.set_section("fastrag", {"host": "192.168.1.5", "port": 9999})
        url, _, host_port = f._query_urls()
        self.assertEqual(url, "http://192.168.1.5:9999/query")
        self.assertEqual(host_port, "192.168.1.5:9999")

    def test_legacy_signature_snapshot(self):
        f = Fastrag_LLM({"host": "192.168.1.5", "port": 9999})
        url, _, _ = f._query_urls()
        self.assertEqual(url, "http://192.168.1.5:9999/query")


class TestEDTalkClientHotSwap(unittest.TestCase):
    """EDTalkClient 连接级参数现读 + enable 即时门控（2026-11-17 热切机制批次）。"""

    def _make(self, cfg_obj=None, section=None):
        section = section if section is not None else {"api_token": "tok-a",
                                                       "api_ip_port": "http://127.0.0.1:8000"}
        return EDTalkClient(dict(section), config_obj=cfg_obj)

    def test_auth_token_hotswap(self):
        cfg_obj = _FakeConfig({"edtalk_realtime": {"api_token": "tok-a"}})
        client = self._make(cfg_obj, section={"api_token": "tok-a"})
        self.assertEqual(client._auth_headers()["Authorization"], "Bearer tok-a")
        cfg_obj.set_section("edtalk_realtime", {"api_token": "tok-b"})
        self.assertEqual(client._auth_headers()["Authorization"], "Bearer tok-b")

    def test_api_base_refresh_after_ttl(self):
        cfg_obj = _FakeConfig({"edtalk_realtime": {"api_ip_port": "http://127.0.0.1:8000"}})
        client = self._make(cfg_obj, section={"api_ip_port": "http://127.0.0.1:8000"})
        # 隔离运行时文件（resolve_api_base 优先读 data/edtalk_runtime.json），
        # 令 legacy 回退（节内 api_ip_port）可观测（源模块 patch——客户端函数内局部 import）
        import utils.edtalk_realtime.runtime as rt
        with patch.object(rt, "resolve_api_base",
                          lambda data: ((data or {}).get("edtalk_realtime", {})
                                        .get("api_ip_port")) or "http://127.0.0.1:8000"):
            # TTL 内：缓存不变（防每请求磁盘 IO）
            cfg_obj.set_section("edtalk_realtime", {"api_ip_port": "http://127.0.0.1:9999"})
            self.assertEqual(client._api_base(), "http://127.0.0.1:8000")
            # TTL 过期：现读新值
            client._api_base_at -= 61.0
            self.assertEqual(client._api_base(), "http://127.0.0.1:9999")

    def test_enable_gate_blocks_push(self):
        """enable 关断即时生效：push_full 入口现读门控，停止新发送。"""
        import utils.edtalk_realtime.edtalk_client as ec
        cfg_obj = _FakeConfig({"visual_body": "edtalk_realtime",
                               "edtalk_realtime": {"enable": True}})
        client = EDTalkClient({"enable": True}, config_obj=cfg_obj)
        # 关断（前端开关写回 → 热重载）→ push_full 即刻短路
        cfg_obj.set_section("edtalk_realtime", {"enable": False})
        with patch.object(ec, "_ensure_wav_riff") as m_ensure:
            ok = asyncio.run(client.push_full("/nonexistent.wav", content="测试"))
        self.assertFalse(ok)
        m_ensure.assert_not_called()  # 门控在文件处理前（零副作用）

    def test_enable_true_allows_push_path(self):
        import utils.edtalk_realtime.edtalk_client as ec
        cfg_obj = _FakeConfig({"visual_body": "edtalk_realtime",
                               "edtalk_realtime": {"enable": True}})
        client = EDTalkClient({"enable": True}, config_obj=cfg_obj)
        with patch.object(ec, "_ensure_wav_riff",
                          side_effect=RuntimeError("probe")) as m_ensure:
            asyncio.run(client.push_full("/nonexistent.wav", content="测试"))
            # enable=True：越过门控进入文件处理（探针异常即证）
            m_ensure.assert_called_once()

    def test_legacy_signature_no_gate(self):
        """旧签名（无 config_obj）不门控（维持旧行为：恒可推送）。"""
        client = self._make(cfg_obj=None)
        self.assertTrue(client._edtalk_enabled())


class TestBuiltinPlayerHotValue(unittest.TestCase):
    """AUDIO_PLAY_CENTER._hot_value 内存现读优先 + 回退链（2026-11-17 热切机制批次）。

    __new__ 式替身构造（不起 PyAudio/播放线程，对齐 chat_handler 测试惯例）。"""

    def _make(self, snapshot, cfg_obj):
        from utils.audio.builtin_play_center import AUDIO_PLAY_CENTER
        player = object.__new__(AUDIO_PLAY_CENTER)  # 不起 PyAudio/播放线程
        player.config_data = snapshot
        player.config_path = "_nonexistent_config_for_test.json"
        player._config_obj = cfg_obj
        return player

    def test_memory_read_wins(self):
        cfg_obj = _FakeConfig({"audio_player": {"queue_max": 7}})
        player = self._make({"queue_max": 50}, cfg_obj)
        self.assertEqual(player._hot_value("queue_max", 50), 7)
        # 热重载（节替换）→ 下一播放事件即用新值
        cfg_obj.set_section("audio_player", {"queue_max": 3})
        self.assertEqual(player._hot_value("queue_max", 50), 3)

    def test_missing_key_falls_back_to_snapshot(self):
        cfg_obj = _FakeConfig({"audio_player": {"device_index": -1}})
        player = self._make({"queue_max": 9}, cfg_obj)
        self.assertEqual(player._hot_value("queue_max", 50), 9)

    def test_no_config_obj_file_then_snapshot(self):
        """config_obj=None：回退文件现读（测试路径文件不存在）→ 启动快照。"""
        player = self._make({"queue_max": 11}, None)
        self.assertEqual(player._hot_value("queue_max", 50), 11)


class TestGPTModelRegistryPassthrough(unittest.TestCase):
    """set_model_config 透传 config_obj（2026-11-17 热切机制批次）。"""

    def test_config_obj_passed_to_channels(self):
        g = GPT_Model()
        cfg_obj = _FakeConfig({"custom_llm": {"base_url": "http://x/v1", "model": "m"},
                               "fastrag": {"port": 11420}})
        g.set_model_config("custom_llm", cfg_obj.get("custom_llm"), config_obj=cfg_obj)
        g.set_model_config("fastrag", cfg_obj.get("fastrag"), config_obj=cfg_obj)
        self.assertIs(g.get("custom_llm")._config_obj, cfg_obj)
        self.assertIs(g.get("fastrag")._config_obj, cfg_obj)
        # 热切行为抽查：模型名修改 → 下一请求生效
        cfg_obj.set_section("custom_llm", {"base_url": "http://x/v1", "model": "m2"})
        self.assertEqual(g.get("custom_llm")._build_body("hi")["model"], "m2")


if __name__ == "__main__":
    unittest.main()
