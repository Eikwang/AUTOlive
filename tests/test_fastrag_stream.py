# -*- coding: UTF-8 -*-
"""
fastrag 流式链路守卫测试（autoplan main-20261029-102545 批次 T6）

覆盖：
- T2 补丁三态：config.py FASTRAG__SERVER__PORT env 注入/未设置默认/非法值 fail-fast（DX F2）
- T3③ SSE 客户端契约：delta 帧产出/终帧不产出（Eng A2）/错误帧抛错/非 200 抛错/
  零产出抛错/畸形帧与 CRLF 容错
- T3④ stream_reply_gate fastrag 放行+负向矩阵（Eng T-A）
- llm_stream_reply fastrag 中途失败部分保留语义（DX F5 显式化）
- 锁跨度/断连收尾/终帧协议源级守卫（Eng E1/A2——行为级由手工全栈 bench 背书）
"""

import asyncio
import importlib.util
import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from utils.handlers.chat_handler import ChatHandler

_REPO = Path(__file__).resolve().parent.parent
FASTRAG_PY_DIR = _REPO / "edtalk" / "fastrag" / "fastrag-python"
CONFIG_PATH = FASTRAG_PY_DIR / "config.py"
NATIVE_PATH = FASTRAG_PY_DIR / "fastrag_native.py"
SERVER_PATH = FASTRAG_PY_DIR / "server.py"


class _FakeConfig:
    """模拟 Config.get(*keys) 变长键逐层读取（口径同 test_arch_opt_20261027）。"""

    def __init__(self, cfg):
        self._cfg = cfg

    def get(self, *keys):
        node = self._cfg
        for k in keys:
            if not isinstance(node, dict) or k not in node:
                return None
            node = node[k]
        return node


# ==================== T2：config.py 端口 env 注入三态 ====================


def _load_fastrag_config(env_val):
    """以独立模块名加载 fastrag config.py（每次全新 exec，env 在导入期读取）。"""
    if env_val is None:
        os.environ.pop("FASTRAG__SERVER__PORT", None)
    else:
        os.environ["FASTRAG__SERVER__PORT"] = env_val
    spec = importlib.util.spec_from_file_location(
        "fastrag_config_under_test", str(CONFIG_PATH))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestFastragConfigPort:
    def test_env_set_overrides(self):
        mod = _load_fastrag_config("12345")
        assert mod.SERVER_PORT == 12345

    def test_env_unset_keeps_default(self):
        mod = _load_fastrag_config(None)
        assert mod.SERVER_PORT == 11420

    def test_env_invalid_not_integer_fails_fast(self):
        with pytest.raises(SystemExit) as exc_info:
            _load_fastrag_config("abc")
        msg = str(exc_info.value)
        assert "FASTRAG__SERVER__PORT" in msg and "abc" in msg

    def test_env_invalid_out_of_range_fails_fast(self):
        with pytest.raises(SystemExit) as exc_info:
            _load_fastrag_config("70000")
        msg = str(exc_info.value)
        assert "1-65535" in msg


# ==================== P7：config.py 量化 env 注入域校验 ====================


class TestFastragConfigQuant:
    def test_quant_env_overrides(self):
        os.environ["FASTRAG__LLM_QUANT_BITS"] = "4"
        os.environ["FASTRAG__RERANKER_QUANT_BITS"] = "8"
        os.environ["FASTRAG__EMBEDDER_DEVICE"] = "cuda-fp16"
        try:
            mod = _load_fastrag_config(None)
            assert mod.LLM_QUANT_BITS == 4
            assert mod.RERANKER_QUANT_BITS == 8
            assert mod.EMBEDDER_DEVICE == "cuda-fp16"
        finally:
            for k in ("FASTRAG__LLM_QUANT_BITS", "FASTRAG__RERANKER_QUANT_BITS",
                      "FASTRAG__EMBEDDER_DEVICE"):
                os.environ.pop(k, None)

    def test_quant_unset_keeps_defaults(self):
        mod = _load_fastrag_config(None)
        assert mod.LLM_QUANT_BITS == 16
        assert mod.RERANKER_QUANT_BITS == 16
        assert mod.EMBEDDER_DEVICE == "cpu"

    def test_quant_invalid_fails_fast(self):
        os.environ["FASTRAG__LLM_QUANT_BITS"] = "7"
        try:
            with pytest.raises(SystemExit) as exc_info:
                _load_fastrag_config(None)
            msg = str(exc_info.value)
            assert "FASTRAG__LLM_QUANT_BITS" in msg and "7" in msg
        finally:
            os.environ.pop("FASTRAG__LLM_QUANT_BITS", None)

    def test_device_invalid_fails_fast(self):
        os.environ["FASTRAG__EMBEDDER_DEVICE"] = "tpu"
        try:
            with pytest.raises(SystemExit) as exc_info:
                _load_fastrag_config(None)
            msg = str(exc_info.value)
            assert "cuda-fp16" in msg
        finally:
            os.environ.pop("FASTRAG__EMBEDDER_DEVICE", None)


# ==================== T3③：fastrag_llm.stream_resp SSE 客户端契约 ====================


class _FakeContent:
    def __init__(self, lines):
        self._lines = lines

    async def __aiter__(self):
        for line in self._lines:
            yield line


class _FakeResponse:
    def __init__(self, status, lines):
        self.status = status
        self.content = _FakeContent(lines)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class _FakeSession:
    def __init__(self, response, timeout=None):
        self._response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def post(self, url, json=None):
        return self._response


def _fake_aiohttp_module(response):
    class _Mod:
        @staticmethod
        def ClientTimeout(total=None):
            return object()

        ClientSession = _FakeSession

    # ClientSession 需以关键字 timeout 构造
    class _CS(_FakeSession):
        def __init__(self, timeout=None):
            super().__init__(response, timeout=timeout)

    _Mod.ClientSession = _CS
    return _Mod


def _make_llm():
    from utils.gpt_model.fastrag_llm import Fastrag_LLM
    return Fastrag_LLM({"host": "localhost", "port": 11420})


def _run_stream(lines, status=200):
    """以假 aiohttp 运行 stream_resp，返回 (deltas, error)。"""
    import utils.gpt_model.fastrag_llm as fm

    llm = _make_llm()
    fm._get_aiohttp = lambda: _fake_aiohttp_module(_FakeResponse(status, lines))

    async def _collect():
        out = []
        try:
            async for delta in llm.stream_resp({"content": "测试问题"}):
                out.append(delta)
            return out, None
        except Exception as e:  # noqa: BLE001 —— 测试需捕获任意失败形态
            return out, e

    return asyncio.run(_collect())


class TestFastragStreamRespContract:
    def test_deltas_streamed_and_done_frame_not_yielded(self):
        """Eng A2：终帧为纯终止信号——客户端零额外产出（防全文重复播报）。"""
        lines = [
            b'data: {"delta": "\xe4\xbd\xa0\xe5\xa5\xbd"}\n\n',
            b'data: {"delta": "\xe5\x91\x80\xef\xbc\x81"}\n',
            b"\n",  # 空行跳过
            b'data: {"done": true}\n\n',
        ]
        deltas, err = _run_stream(lines)
        assert err is None
        assert deltas == ["你好", "呀！"]

    def test_error_frame_raises(self):
        lines = [b'data: {"delta": "\xe5\x89\x8d\xe7\xbc\x80"}\n\n',
                 b'data: {"error": "\xe7\x94\x9f\xe6\x88\x90\xe5\xa4\xb1\xe8\xb4\xa5"}\n\n']
        deltas, err = _run_stream(lines)
        assert err is not None
        assert "生成失败" in str(err) or "fastrag" in str(err)

    def test_non_200_raises(self):
        deltas, err = _run_stream([], status=503)
        assert deltas == [] and err is not None
        assert "503" in str(err)

    def test_zero_delta_raises(self):
        """零产出按流式失败回退全文链（不变量 2）。"""
        lines = [b'data: {"done": true}\n\n']
        deltas, err = _run_stream(lines)
        assert deltas == [] and err is not None
        assert "零产出" in str(err)

    def test_malformed_frames_and_crlf_tolerated(self):
        lines = [
            b': comment\r\n',                      # SSE 注释行
            b'data: not-json\r\n',                  # 畸形 JSON 跳过
            b'data: {"delta": "a"}\r\n',
            b'garbage line\r\n',
            b'data: {"delta": "b"}\r\n',
            b'data: {"done": true}\r\n\r\n',
        ]
        deltas, err = _run_stream(lines)
        assert err is None
        assert deltas == ["a", "b"]


class TestFastragStreamRespSyncBridge:
    def test_yields_and_terminates(self):
        import utils.gpt_model.fastrag_llm as fm

        llm = _make_llm()
        fm._get_aiohttp = lambda: _fake_aiohttp_module(_FakeResponse(200, [
            b'data: {"delta": "x"}\n\n',
            b'data: {"done": true}\n\n',
        ]))
        assert list(llm.stream_resp_sync({"content": "q"})) == ["x"]

    def test_exception_propagates(self):
        import utils.gpt_model.fastrag_llm as fm

        llm = _make_llm()
        fm._get_aiohttp = lambda: _fake_aiohttp_module(_FakeResponse(500, []))
        with pytest.raises(RuntimeError):
            list(llm.stream_resp_sync({"content": "q"}))


# ==================== T3④：stream_reply_gate fastrag 放行+负向矩阵 ====================


class TestStreamReplyGateFastrag:
    def _handler(self, cfg=None):
        mh = MagicMock()
        mh.config = _FakeConfig(cfg or {})
        return ChatHandler(mh)

    def test_fastrag_default_open(self):
        assert self._handler().stream_reply_gate("fastrag") is True

    def test_fastrag_own_switch_false_closed(self):
        """fastrag 独立流式开关（2026-10-29 用户裁决：fastrag.stream_reply）。"""
        assert self._handler({"fastrag": {"stream_reply": False}}) \
            .stream_reply_gate("fastrag") is False

    def test_fastrag_independent_of_custom_llm_switch(self):
        """独立键语义：custom_llm.stream_reply=False 不再影响 fastrag 通道。"""
        assert self._handler({"custom_llm": {"stream_reply": False}}) \
            .stream_reply_gate("fastrag") is True

    def test_custom_llm_own_switch_false_closed(self):
        """custom_llm 通道仍读自身键（语义不变）。"""
        assert self._handler({"custom_llm": {"stream_reply": False}}) \
            .stream_reply_gate("custom_llm") is False

    def test_fastrag_translate_reply_closed(self):
        assert self._handler({"translate": {"enable": True, "trans_type": "回复"}}) \
            .stream_reply_gate("fastrag") is False

    def test_fastrag_reply_template_closed(self):
        assert self._handler({"reply_template": {"enable": True}}) \
            .stream_reply_gate("fastrag") is False

    def test_fastrag_keymap_reply_closed(self):
        assert self._handler({"key_mapping": {"type": "回复"}}) \
            .stream_reply_gate("fastrag") is False

    def test_local_qa_still_closed(self):
        assert self._handler().stream_reply_gate("local_qa") is False

    def test_unknown_channel_still_closed(self):
        assert self._handler().stream_reply_gate("openai") is False

    def test_custom_llm_default_open_unchanged(self):
        assert self._handler().stream_reply_gate("custom_llm") is True


# ==================== llm_stream_reply fastrag 中途失败部分保留 ====================


class TestLlmStreamReplyFastragPartial:
    def _handler(self):
        mh = MagicMock()
        mh.prohibitions_handle = MagicMock(side_effect=lambda s: s)
        mh.audio_synthesis_handle = MagicMock()
        return ChatHandler(mh)

    def test_partial_output_kept_on_midstream_failure(self):
        """DX F5 显式化：中途失败（已播部分句子）→部分产出保留、不回退重播。"""
        handler = self._handler()

        class _FlakyModel:
            def stream_resp_sync(self, data):
                yield "第一句内容呀。"
                yield "第二"
                raise RuntimeError("mid-stream")

        handler.my_handle.fastrag = _FlakyModel()
        spoken = handler.llm_stream_reply("fastrag", {}, lambda c: {"content": c})
        assert spoken == "第一句内容呀。"
        assert handler.my_handle.audio_synthesis_handle.call_count == 1

    def test_zero_yield_returns_none(self):
        """零产出→None→调用方回退 llm_handle 全文链（不变量 2）。"""
        handler = self._handler()

        class _DeadModel:
            def stream_resp_sync(self, data):
                raise RuntimeError("boom")
                yield  # pragma: no cover —— 使其成为生成器

        handler.my_handle.fastrag = _DeadModel()
        assert handler.llm_stream_reply("fastrag", {}, lambda c: {"content": c}) is None

    def test_model_without_stream_resp_sync_returns_none(self):
        handler = self._handler()
        handler.my_handle.fastrag = object()
        assert handler.llm_stream_reply("fastrag", {}, lambda c: {"content": c}) is None


# ==================== 锁跨度/断连收尾/终帧协议源级守卫（Eng E1/A2） ====================


class TestStreamSafetySourceGuards:
    """行为级并发/断连守卫需真实 GPU（手工全栈 bench 背书，F3 tripwire）；
    此处源级锚定关键不变量，防实施者"顺手"破坏。"""

    def test_query_stream_lock_spans_whole_generation(self):
        src = NATIVE_PATH.read_text(encoding="utf-8")
        qs = src[src.index("async def query_stream"):src.index("    def save(")]
        assert "async with self._inference_lock:" in qs, "锁必须横跨整段流式生成"
        assert "StoppingCriteriaList" in qs, "断连收尾需 StoppingCriteria（Eng E1）"
        assert "stop_flag[0] = True" in qs, "finally 置停止信号（Eng E1）"
        assert "threading.Thread" in qs, "TextIteratorStreamer 需后台 generate 线程"
        assert "max(16, min(LLM_MAX_NEW_TOKENS" in qs, "H1：截断转 max_new_tokens 收口"
        assert "_stream_head_filter" in qs, "H1：头部泄漏前缀 buffer 甄别"

    def test_generate_answer_postprocess_unchanged(self):
        """全文链后处理完整保留（H1 只改流式变体，全文链行为零变化）。"""
        src = NATIVE_PATH.read_text(encoding="utf-8")
        ga = src[src.index("def generate_answer"):src.index("async def query_stream")]
        assert "MAX_ANSWER_LENGTH" in ga and "客服回答" in ga

    def test_server_terminal_frame_is_pure_signal(self):
        src = SERVER_PATH.read_text(encoding="utf-8")
        assert "'done': True" in src, "终帧=纯终止信号（Eng A2）"
        assert "text/event-stream" in src
        assert "async def query_stream" in src
