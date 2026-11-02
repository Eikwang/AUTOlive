# -*- coding: UTF-8 -*-
"""
动态文案功能审查批次守卫测试（2026-10-30，任务书「动态文案」节）

覆盖：
1. 提示词转换=人设替换语义落实（核心审计发现——原实现转换内容拼进 user
   消息且系统人设照旧注入，替换语义未落实）：
   - custom_llm：请求级 system_prompt 覆盖（替换而非拼接，双路径 get/stream）
   - fastrag：客户端载荷 system_role + 服务端请求级人设替换贯穿（5 处签名）
   - task_scheduler：按通道下发对应覆盖键，user 消息=文案原文
2. LLM类型枚举 {custom_llm, fastrag}（存量 chatgpt 降级）
3. 随机生成/生成间隔（play_interval 尊重用户设定，10s 暗置下限修正）
4. 布局：随机生成+生成间隔移到 LLM 类型下方单列显示
5. 回归：custom_llm._build_body 位置参数兼容、弹幕路径无覆盖键行为不变
"""
from pathlib import Path
from unittest.mock import MagicMock

import pytest

REPO = Path(__file__).resolve().parents[1]


def _src(rel: str) -> str:
    return (REPO / rel).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. custom_llm 请求级人设替换（提示词转换语义）
# ---------------------------------------------------------------------------

def _make_custom_llm(system_prompt="系统人设A"):
    from utils.gpt_model.custom_llm import Custom_LLM
    return Custom_LLM({"base_url": "http://127.0.0.1:11434/v1", "model": "test-model",
                       "api_key": "", "system_prompt": system_prompt})


class TestCustomLlmPersonaOverride:
    def test_override_replaces_configured(self):
        """覆盖非空 → 替换（而非拼接）配置人设。"""
        llm = _make_custom_llm("系统人设A")
        body = llm._build_body("文案原文", system_prompt_override="转换人设B")
        assert body["messages"][0] == {"role": "system", "content": "转换人设B"}
        assert body["messages"][1] == {"role": "user", "content": "文案原文"}
        assert len(body["messages"]) == 2  # 人设不叠加

    def test_no_configured_override_alone(self):
        llm = _make_custom_llm("")
        body = llm._build_body("文案", system_prompt_override="转换人设B")
        assert body["messages"][0]["content"] == "转换人设B"

    def test_no_override_configured_kept(self):
        """弹幕/语音路径不传覆盖键 → 配置人设照旧（行为不变）。"""
        llm = _make_custom_llm("系统人设A")
        body = llm._build_body("早上好")
        assert body["messages"][0] == {"role": "system", "content": "系统人设A"}

    def test_empty_override_configured_kept(self):
        llm = _make_custom_llm("系统人设A")
        body = llm._build_body("早上好", system_prompt_override="   ")
        assert body["messages"][0]["content"] == "系统人设A"

    def test_positional_compat(self):
        """回归：既有调用 _build_body(prompt) 位置参数形态不受影响。"""
        llm = _make_custom_llm("人设")
        assert llm._build_body("hi")["messages"][1]["content"] == "hi"

    def test_get_and_stream_pass_override(self):
        """get_resp/stream_resp 双路径均透传 data.system_prompt。"""
        src = _src("utils/gpt_model/custom_llm.py")
        assert src.count('data.get("system_prompt") if isinstance(data, dict) else None') == 2
        assert '_build_body(prompt, system_prompt_override=system_prompt_override)' in src


# ---------------------------------------------------------------------------
# 2. fastrag 请求级 system_role 通道（客户端载荷 + 服务端贯穿）
# ---------------------------------------------------------------------------

class TestFastragSystemRole:
    def _client(self):
        from utils.gpt_model.fastrag_llm import Fastrag_LLM
        return Fastrag_LLM({"host": "localhost", "port": 11420})

    def test_payload_with_override(self):
        payload = self._client()._build_payload({"content": "文案原文", "system_role": "人设B"})
        assert payload == {"question": "文案原文", "system_role": "人设B"}

    def test_payload_without_override(self):
        """弹幕/语音路径不传键 → 载荷仅 question（行为不变）。"""
        payload = self._client()._build_payload({"content": "问题"})
        assert payload == {"question": "问题"}

    def test_payload_blank_override_omitted(self):
        payload = self._client()._build_payload({"content": "问题", "system_role": "  "})
        assert payload == {"question": "问题"}

    def test_payload_non_dict(self):
        assert self._client()._build_payload("裸问题") == {"question": "裸问题"}

    def test_both_endpoints_use_payload(self):
        src = _src("utils/gpt_model/fastrag_llm.py")
        assert src.count("payload = self._build_payload(data)") == 2
        assert 'json={"question": question}' not in src

    def test_server_contract_system_role(self):
        """服务端 /query /query_stream 契约：system_role 可选字段+贯穿下发。"""
        srv = _src("edtalk/fastrag/fastrag-python/server.py")
        assert srv.count("system_role: Optional[str] = None") == 2
        assert "rag.query(request.question, system_role=request.system_role)" in srv
        assert "rag.query_stream(request.question, system_role=request.system_role)" in srv

    def test_native_threading(self):
        """fastrag_native 五处签名贯穿 + 替换语义（空值回落配置 SYSTEM_ROLE）。"""
        nat = _src("edtalk/fastrag/fastrag-python/fastrag_native.py")
        assert "async def query(self, question: str, mode: str = \"mini\", system_role: str = None)" in nat
        assert "_query_impl, question, mode, system_role" in nat
        assert "def _query_impl(self, question: str, mode: str = \"mini\", system_role: str = None)" in nat
        assert "self.generate_answer(question, context, system_role=system_role)" in nat
        assert "def generate_answer(self, question: str, context: List[Dict], system_role: str = None)" in nat
        assert "def _build_prompt_text(self, question: str, context: List[Dict], system_role: str = None)" in nat
        # 替换语义：非空替换，空值回落配置人设
        assert 'effective_system_role = str(system_role or "").strip() or SYSTEM_ROLE' in nat
        assert "return f\"\"\"{effective_system_role}请根据以下参考资料回答问题。" in nat
        # 流式路径同语义
        assert "async def query_stream(self, question: str, mode: str = \"mini\", system_role: str = None)" in nat
        assert "_build_prompt_text(question, context, system_role=system_role)" in nat


# ---------------------------------------------------------------------------
# 3. task_scheduler 改写链（人设替换语义 + 空内容守卫）
# ---------------------------------------------------------------------------

class TestTaskSchedulerRewrite:
    def _src(self):
        return _src("utils/task_scheduler.py")

    def test_channel_key_dispatch(self):
        """custom_llm→system_prompt 键；fastrag→system_role 键。"""
        src = self._src()
        assert '"system_prompt" if llm_type == "custom_llm"' in src
        assert 'else "system_role"' in src
        assert "override_key: prompt_change_content" in src

    def test_user_message_is_raw_copy(self):
        """user 消息=文案原文（原 rewrite_prompt 拼接形态退役）。"""
        src = self._src()
        assert '"content": resp_content,' in src
        # 退役形态精确匹配（宽匹配会误伤降级日志「降级播放原文案：{llm_e}」）
        assert "原文案：{resp_content}" not in src

    def test_empty_content_guard(self):
        """转换内容为空 → 跳过改写播放原文案（不裸发请求）。"""
        src = self._src()
        assert "动态文案：提示词转换已开启但内容为空，跳过改写播放原文案" in src

    def test_fallback_semantics_kept(self):
        src = self._src()
        assert "动态文案 LLM 改写无返回，降级播放原文案" in src
        assert "降级播放原文案：{llm_e}" in src

    def test_llm_type_enum_normalized(self):
        src = self._src()
        assert 'llm_type not in ("custom_llm", "fastrag")' in src
        assert 'llm_type = "custom_llm"' in src

    def test_interval_honors_user_value(self):
        """生成间隔尊重用户设定（原 10s 暗置下限修正为 1s 防忙转下限）。"""
        src = self._src()
        assert "sleep_total = max(interval, 1)" in src
        assert "sleep_total = max(interval, 10)" not in src

    def test_generation_enter_system_flow(self):
        """生成答复进入系统处理流程：关键词中枢识别 + 合成入播放链。"""
        src = self._src()
        # 2026-11-01 动作编排批次 T3：调用包 try 保护，缩进随内层 try 加深
        assert 'keyword_recognize(\n                                    {"content": resp_content}, "copywriting")' in src
        assert "audio_synthesis_handle(message)" in src

    def test_random_vs_sequential(self):
        """随机生成开关：随机取 random.choice / 顺序取游标推进（双链一致）。"""
        src = self._src()
        assert src.count('self.config.get("trends_copywriting", "random_play")') == 2


# ---------------------------------------------------------------------------
# 4. 前端布局：LLM 类型下方单列
# ---------------------------------------------------------------------------

class TestLayoutSingleColumn:
    def test_three_col_grid_removed(self):
        src = _src("frontend/ui/tabs/trends_copywriting.py")
        assert "ui.grid(columns=3)" not in src
        assert "单列显示" in src

    def test_order_llm_then_random_then_interval(self):
        src = _src("frontend/ui/tabs/trends_copywriting.py")
        _sq = chr(39)
        _llm_pos = src.find("label=" + _sq + "LLM类型" + _sq)
        _rand_pos = src.find("ui.switch(" + _sq + "随机生成" + _sq)
        _interval_pos = src.find("label=" + _sq + "生成间隔" + _sq)
        assert -1 < _llm_pos < _rand_pos < _interval_pos

    def test_interval_placeholder_matches_taskbook(self):
        src = _src("frontend/ui/tabs/trends_copywriting.py")
        assert "完成一次文案生成后，进行下次文案生成的间隔时间（秒）" in src
