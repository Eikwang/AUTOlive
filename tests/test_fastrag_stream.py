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
        assert mod.EMBEDDER_DEVICE == "cuda-fp16"  # 2026-11-20 对齐上游默认

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


# ==================== P8：config.py 对话模型 env 注入域校验（2026-11-25 模型选择） ====================


class TestFastragConfigModel:
    def test_model_env_switches_path(self):
        """1.5B 选择 → LLM_MODEL_PATH 由 MODEL_DIR 相对派生（无硬编码绝对路径，
        随仓库目录布局便携）。"""
        os.environ["FASTRAG__LLM_MODEL"] = "Qwen2.5-1.5B-Instruct"
        try:
            mod = _load_fastrag_config(None)
            assert mod.LLM_MODEL_PATH.parent == mod.MODEL_DIR
            assert mod.LLM_MODEL_PATH.name == "Qwen2.5-1.5B-Instruct"
        finally:
            os.environ.pop("FASTRAG__LLM_MODEL", None)

    def test_model_unset_keeps_default(self):
        """未注入=0.5B 默认（存量部署零行为变化）。"""
        os.environ.pop("FASTRAG__LLM_MODEL", None)
        mod = _load_fastrag_config(None)
        assert mod.LLM_MODEL_PATH == mod.MODEL_DIR / "Qwen2.5-0.5B-Instruct"

    def test_model_invalid_fails_fast(self):
        """白名单域外拒绝启动（防路径拼接注入/目录名漂移静默 404）。"""
        os.environ["FASTRAG__LLM_MODEL"] = "..\\evil"
        try:
            with pytest.raises(SystemExit) as exc_info:
                _load_fastrag_config(None)
            msg = str(exc_info.value)
            assert "FASTRAG__LLM_MODEL" in msg and "evil" in msg
        finally:
            os.environ.pop("FASTRAG__LLM_MODEL", None)


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


# ==================== 2026-11-20 整合对齐：多轮 history/references 契约 ====================


class _FakeJsonResponse(_FakeResponse):
    """get_resp 契约 fake：/query 响应体（status + await text()）。"""

    def __init__(self, status, body_obj):
        import json as _json
        super().__init__(status, [])
        self._body = _json.dumps(body_obj, ensure_ascii=False)
        self.captured_json = None

    async def text(self):
        return self._body


def _fake_query_module(response, captured):
    """在流式 fake 之上捕获 post 载荷（url + json）。"""
    mod = _fake_aiohttp_module(response)
    _base = mod.ClientSession

    class _CS(_base):
        def post(self, url, json=None):
            captured.append({"url": url, "json": json})
            return self._response

    mod.ClientSession = _CS
    return mod


class TestFastragHistory:
    """消费侧多轮闭环（对齐上游 api_client.LiveStreamBot 契约）：

    - 弹幕链路成功后记录 (question, answer, references[:1])，下一请求回传 history
    - 动态文案链路（带 system_role）不参与弹幕历史（改写上下文隔离）
    - 流式终帧 references 捕获；流失败不记录（防回退全文链后重复）
    """

    def _setup(self, body_obj):
        import utils.gpt_model.fastrag_llm as fm
        resp = _FakeJsonResponse(200, body_obj)
        captured = []
        fm._get_aiohttp = lambda: _fake_query_module(resp, captured)
        return _make_llm(), captured

    def _run(self, coro):
        return asyncio.run(coro)

    def test_get_resp_records_and_next_payload_sends_history(self):
        llm, captured = self._setup(
            {"answer": "一桶二十公斤", "references": ["# 大桶 > 参数", "# 其它"]})
        out = self._run(llm.get_resp({"content": "一桶多重"}))
        assert out == "一桶二十公斤"
        # 本轮入历史（refs[:1] 对齐 LiveStreamBot）
        assert llm.history == [
            {"role": "user", "content": "一桶多重"},
            {"role": "assistant", "content": "一桶二十公斤",
             "references": ["# 大桶 > 参数"]},
        ]
        # 下一请求（追问）载荷携带 history（快照=第二问发出时的历史，此时
        # 第二问尚未记录——记录发生在响应成功后）
        snapshot = list(llm.history)
        self._run(llm.get_resp({"content": "多少钱"}))
        assert len(captured) == 2
        assert captured[1]["json"]["history"] == snapshot
        assert "history" not in captured[0]["json"]

    def test_dynamic_copy_isolated_from_history(self):
        llm, captured = self._setup({"answer": "改写文案", "references": ["# x"]})
        out = self._run(llm.get_resp(
            {"content": "文案原文", "system_role": "人设B"}))
        assert out == "改写文案"
        assert llm.history == []  # 改写不入弹幕历史
        assert "history" not in captured[0]["json"]  # 也不回传
        assert captured[0]["json"]["system_role"] == "人设B"

    def test_stream_done_refs_recorded(self):
        import utils.gpt_model.fastrag_llm as fm
        llm = _make_llm()
        fm._get_aiohttp = lambda: _fake_aiohttp_module(_FakeResponse(200, [
            b'data: {"delta": "\xe4\xba\x8c\xe5\x8d\x81\xe5\x85\xac\xe6\x96\xa4"}\n\n',
            b'data: {"done": true, "references": ["# \xe5\xa4\xa7\xe6\xa1\xb6 > \xe5\x8f\x82\xe6\x95\xb0"]}\n\n',
        ]))
        # 直接消费 stream_resp
        async def _collect():
            return [d async for d in llm.stream_resp({"content": "一桶多重"})]
        deltas = self._run(_collect())
        assert deltas == ["二十公斤"]
        assert llm.history == [
            {"role": "user", "content": "一桶多重"},
            {"role": "assistant", "content": "二十公斤",
             "references": ["# 大桶 > 参数"]},
        ]

    def test_stream_failure_not_recorded(self):
        import utils.gpt_model.fastrag_llm as fm
        llm = _make_llm()
        fm._get_aiohttp = lambda: _fake_aiohttp_module(_FakeResponse(200, [
            b'data: {"delta": "\xe5\x89\x8d\xe7\xbc\x80"}\n\n',
            b'data: {"error": "\xe7\x94\x9f\xe6\x88\x90\xe5\xa4\xb1\xe8\xb4\xa5"}\n\n',
        ]))
        async def _collect():
            out = []
            try:
                async for d in llm.stream_resp({"content": "q"}):
                    out.append(d)
            except Exception:
                pass
            return out
        self._run(_collect())
        assert llm.history == []  # 流失败不记录（回退全文链由 get_resp 记录，防重复）

    def test_history_trimmed_to_one_round(self):
        llm, _ = self._setup({"answer": "答", "references": ["# x"]})
        self._run(llm.get_resp({"content": "问1"}))
        self._run(llm.get_resp({"content": "问2"}))
        # 保留最近 1 轮（history_max_rounds=1，对齐上游 LiveStreamBot 默认）
        assert llm.history == [
            {"role": "user", "content": "问2"},
            {"role": "assistant", "content": "答", "references": ["# x"]},
        ]


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
        assert "max(16, LLM_MAX_NEW_TOKENS if route_mode" in qs, \
            "生成上限=路由模式 token 参数（2026-11-21 用户裁决：字符限为提示词软限制，不折算截断）"
        assert qs.count("int(max_len * 1.5)") == 1, "max_len×1.5 折算仅改写分支保留（kb/chat 无回复截断）"
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


# ==================== 回答规则保存链守卫（2026-10-30 审查批次） ====================


class TestTemplateSaveChain:
    """回答规则/人设重启丢失修复守卫：apply 必须双写——config_override.py
    （服务端运行态真源）+ config.fastrag.template.*（UI 回显真源）。此前仅写
    override，重启后 UI 回显默认、再次应用即真丢失（用户实测报告）。

    源级断言（NiceGUI 闭包函数无法脱离页面实例化）：
    - _apply_template_and_restart 内含 set_config_callback("fastrag", "template"
      六参数写回调用；
    - settings._FASTRAG_DEFAULTS 提供 template 缺省骨架（六键与 _TPL_KEYS 同源）。"""

    def test_apply_writes_back_config_template(self):
        src = (_REPO / "frontend" / "ui" / "tabs" / "llm.py").read_text(encoding="utf-8")
        apply_pos = src.index("def _apply_template_and_restart")
        end_pos = src.index("ui.button(\"应用并重启 fastrag 服务\"", apply_pos)
        body = src[apply_pos:end_pos]
        for key in ("system_role", "answer_rules", "temperature", "top_p",
                    "max_new_tokens", "max_answer_length"):
            assert f'"template", "{key}"' in body, \
                f"apply 未写回 config.fastrag.template.{key}——重启丢失回归"

    def test_settings_template_skeleton_exists(self):
        import frontend.config.settings as settings_module
        template = settings_module._FASTRAG_DEFAULTS["template"]
        assert set(template) == {"system_role", "answer_rules", "temperature",
                                 "top_p", "max_new_tokens", "max_answer_length"}
        # 2026-11-20 整合对齐批次：骨架默认回迁上游多行 8 规则块（每条一行，
        # 含 {max_answer_length} 占位符）——不再为空串
        rules = template["answer_rules"]
        assert rules.strip(), "缺省回答规则不可为空（上游 8 规则块骨架）"
        assert "1.你必须完全沉浸在你的人设中" in rules and "8.当问题涉及时间" in rules
        assert "{max_answer_length}" in rules
        assert template["system_role"], "缺省人设不可为空（UI 首开应有参考值）"

    def test_template_inputs_autosave_bound(self):
        """2026-10-30 二轮修复：模板六参输入即落盘 config.fastrag.template.*
        （此前 on_change 仅标脏——用户输入后刷新页面值丢失，『应用并重启』是
        唯一持久化路径，实测反馈）。"""
        src = (_REPO / "frontend" / "ui" / "tabs" / "llm.py").read_text(encoding="utf-8")
        assert "def _save_tpl_input" in src
        assert ('set_config_callback("fastrag", "template", key, value=value, _notify=False)' in src)
        # k=key 默认参绑定（防循环闭包晚绑定写错键）
        assert ("lambda e, k=key: (_save_tpl_input(k, e.value), _mark_tpl_dirty())" in src)
