# -*- coding: UTF-8 -*-
"""C4 收敛测试：custom_llm OpenAI-compatible 固定协议（2026-10）

覆盖：
- 请求体构造（model/messages 字段；可选 max_tokens/temperature）
- 响应解析固定 schema 四分支（正常 / 非 200 / 非 JSON / 空 choices）+ 超时
- 迁移函数（旧 url+headers+body → 新三字段，幂等）
- 求值后门消除断言（data_analysis 不存在于新配置 + 源码不含 eval/exec）
- settings._ensure_defaults 磁盘迁移集成

全程 mock 网络（patch send_request），不发真实请求。
"""
import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from utils.gpt_model.custom_llm import (  # noqa: E402
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    LEGACY_KEYS,
    Custom_LLM,
    migrate_legacy_config,
)
from utils.my_log import logger as app_logger  # noqa: E402

# 存量旧形态配置（与 C4 收敛前 config.json 的 custom_llm 节一致）
LEGACY_CONFIG = {
    "url": "http://127.0.0.1:11434/v1/chat/completions",
    "headers": "Content-Type:application/json\nAuthorization:Bearer sk-legacy",
    "method": "POST",
    "proxies": "{}",
    "body_type": "json",
    "body": "{\"model\":\"qwen:latest\",\"messages\":[{\"role\":\"user\",\"content\":\"{{prompt}}\"}]}",
    "resp_data_type": "json",
    "data_analysis": "resp[\"choices\"][0][\"message\"][\"content\"]",
    "resp_template": "{data}",
}

CUSTOM_LLM_SOURCE = (PROJECT_ROOT / "utils" / "gpt_model" / "custom_llm.py").read_text(encoding="utf-8")


class _LogCapture:
    """loguru ERROR 级捕获器（验证 agent-ready 错误信息）。"""

    def __init__(self):
        self.messages = []

    def __call__(self, message):
        self.messages.append(str(message))

    def __enter__(self):
        self._sink_id = app_logger.add(self, level="ERROR")
        return self

    def __exit__(self, *args):
        app_logger.remove(self._sink_id)


def _make_llm(config=None):
    """构造 Custom_LLM 并 mock 掉网络层（send_request → AsyncMock）。"""
    llm = Custom_LLM(config if config is not None else LEGACY_CONFIG)
    llm.send_request = AsyncMock(return_value=(200, json.dumps(
        {"choices": [{"message": {"role": "assistant", "content": "你好呀"}}]}
    )))
    return llm


class TestMigration:
    """迁移函数：旧 url+headers → 新三字段。"""

    def test_legacy_to_three_fields(self):
        new_cfg, changed = migrate_legacy_config(LEGACY_CONFIG)
        assert changed is True
        assert new_cfg["base_url"] == "http://127.0.0.1:11434/v1"
        assert new_cfg["model"] == "qwen:latest"
        assert new_cfg["api_key"] == "sk-legacy"

    def test_legacy_keys_removed(self):
        new_cfg, _ = migrate_legacy_config(LEGACY_CONFIG)
        for key in LEGACY_KEYS:
            assert key not in new_cfg, f"旧键 {key} 未被移除"

    def test_idempotent_on_new_config(self):
        base = {
            "base_url": "https://api.deepseek.com/v1",
            "model": "deepseek-chat",
            "api_key": "sk-x",
            "max_tokens": "1024",
            "temperature": "0.7",
        }
        new_cfg, changed = migrate_legacy_config(base)
        assert changed is False
        assert new_cfg == base

    def test_new_config_missing_keys_filled(self):
        new_cfg, changed = migrate_legacy_config({"base_url": "http://x/v1"})
        assert changed is True
        assert new_cfg["model"] == DEFAULT_MODEL
        assert new_cfg["api_key"] == ""
        assert new_cfg["max_tokens"] == ""
        assert new_cfg["temperature"] == ""

    def test_unparsable_body_falls_back_default_model(self):
        cfg = dict(LEGACY_CONFIG, body="not-json{{")
        new_cfg, _ = migrate_legacy_config(cfg)
        assert new_cfg["model"] == DEFAULT_MODEL

    def test_url_without_suffix_kept_as_prefix(self):
        cfg = dict(LEGACY_CONFIG, url="https://api.deepseek.com/v1/chat/completions")
        new_cfg, _ = migrate_legacy_config(cfg)
        assert new_cfg["base_url"] == "https://api.deepseek.com/v1"

    def test_none_input_returns_defaults(self):
        new_cfg, changed = migrate_legacy_config(None)
        assert changed is True
        assert new_cfg["base_url"] == DEFAULT_BASE_URL


class TestRequestBody:
    """请求体构造：model/messages 字段与可选参数。"""

    def test_body_structure(self):
        llm = Custom_LLM(LEGACY_CONFIG)
        body = llm._build_body("早上好")
        assert body["model"] == "qwen:latest"
        assert body["messages"] == [{"role": "user", "content": "早上好"}]

    def test_optional_params_omitted_when_empty(self):
        llm = Custom_LLM(LEGACY_CONFIG)
        body = llm._build_body("hi")
        assert "max_tokens" not in body
        assert "temperature" not in body

    def test_optional_params_included_when_set(self):
        llm = Custom_LLM(LEGACY_CONFIG)
        llm.config_data["max_tokens"] = "1024"
        llm.config_data["temperature"] = "0.7"
        body = llm._build_body("hi")
        assert body["max_tokens"] == 1024
        assert body["temperature"] == 0.7

    def test_headers_bearer_when_api_key_set(self):
        llm = Custom_LLM(LEGACY_CONFIG)
        headers = llm._build_headers()
        assert headers["Authorization"] == "Bearer sk-legacy"
        assert headers["Content-Type"] == "application/json"

    def test_headers_no_authorization_when_api_key_empty(self):
        llm = Custom_LLM({"base_url": DEFAULT_BASE_URL, "model": DEFAULT_MODEL, "api_key": ""})
        headers = llm._build_headers()
        assert "Authorization" not in headers

    def test_url_appends_chat_completions_once(self):
        llm = Custom_LLM(LEGACY_CONFIG)
        assert llm._chat_completions_url() == "http://127.0.0.1:11434/v1/chat/completions"
        llm.config_data["base_url"] = "http://127.0.0.1:11434/v1/chat/completions"
        assert llm._chat_completions_url() == "http://127.0.0.1:11434/v1/chat/completions"

    def test_prompt_from_runtime_content_field(self):
        """运行时弹幕路径：data_json 只有 content 键。"""
        llm = Custom_LLM(LEGACY_CONFIG)
        assert llm._build_prompt({"content": "弹幕文本", "username": "u"}) == "弹幕文本"
        assert llm._build_prompt({"prompt": "脚本直调"}) == "脚本直调"


class TestResponseParsing:
    """响应解析固定 schema：四分支错误信息 + 正常路径（mock 网络）。"""

    def test_normal_response(self):
        llm = _make_llm()
        assert asyncio.run(llm.get_resp({"prompt": "在吗"})) == "你好呀"

    def test_401_returns_none_with_fix_hint(self):
        llm = _make_llm()
        llm.send_request = AsyncMock(return_value=(401, '{"error": "bad key"}'))
        with _LogCapture() as capture:
            result = asyncio.run(llm.get_resp({"prompt": "hi"}))
        assert result is None
        assert any("401" in m and "api_key" in m for m in capture.messages)

    def test_non_200_returns_none_with_status(self):
        llm = _make_llm()
        llm.send_request = AsyncMock(return_value=(500, "server error"))
        with _LogCapture() as capture:
            result = asyncio.run(llm.get_resp({"prompt": "hi"}))
        assert result is None
        assert any("500" in m for m in capture.messages)

    def test_invalid_json_returns_none(self):
        llm = _make_llm()
        llm.send_request = AsyncMock(return_value=(200, "<html>not json</html>"))
        with _LogCapture() as capture:
            result = asyncio.run(llm.get_resp({"prompt": "hi"}))
        assert result is None
        assert any("JSON" in m for m in capture.messages)

    def test_empty_choices_returns_none(self):
        llm = _make_llm()
        llm.send_request = AsyncMock(return_value=(200, json.dumps({"choices": []})))
        with _LogCapture() as capture:
            result = asyncio.run(llm.get_resp({"prompt": "hi"}))
        assert result is None
        assert any("choices" in m for m in capture.messages)

    def test_timeout_returns_none(self):
        llm = _make_llm()
        llm.send_request = AsyncMock(side_effect=asyncio.TimeoutError())
        result = asyncio.run(llm.get_resp({"prompt": "hi"}))
        assert result is None

    def test_runtime_content_path_reaches_request(self):
        """运行时弹幕数据（content 键）能正确进入请求体。"""
        llm = _make_llm()
        asyncio.run(llm.get_resp({"content": "早上好", "username": "u1"}))
        kwargs = llm.send_request.call_args.kwargs
        assert kwargs["body"]["messages"][0]["content"] == "早上好"


class TestEvalBackdoorRemoved:
    """求值后门消除断言（S2 裁决）。"""

    def test_no_data_analysis_in_migrated_config(self):
        new_cfg, _ = migrate_legacy_config(LEGACY_CONFIG)
        assert "data_analysis" not in new_cfg
        assert "resp_template" not in new_cfg

    def test_source_has_no_eval_or_exec(self):
        assert "eval(" not in CUSTOM_LLM_SOURCE
        assert "exec(" not in CUSTOM_LLM_SOURCE

    def test_instance_config_is_sanitized(self):
        """实例化时旧键（含 data_analysis）不会进入运行配置。"""
        llm = Custom_LLM(LEGACY_CONFIG)
        assert not (set(LEGACY_KEYS) & set(llm.config_data.keys()))


class TestSettingsDiskMigration:
    """settings._ensure_defaults 磁盘迁移集成（前端启动路径）。"""

    def test_ensure_defaults_migrates_legacy_custom_llm(self, tmp_path):
        import frontend.config.settings as settings_module

        cfg_file = tmp_path / "config.json"
        cfg_file.write_text(json.dumps({"custom_llm": LEGACY_CONFIG}), encoding="utf-8")
        settings_module.init_config(str(cfg_file))
        try:
            on_disk = json.loads(cfg_file.read_text(encoding="utf-8"))
            section = on_disk["custom_llm"]
            assert section["base_url"] == "http://127.0.0.1:11434/v1"
            assert section["model"] == "qwen:latest"
            assert section["api_key"] == "sk-legacy"
            assert "data_analysis" not in section
            assert "url" not in section
        finally:
            settings_module._config_instance = None
