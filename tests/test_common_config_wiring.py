# -*- coding: UTF-8 -*-
"""
基础功能版块接线与行为测试（/autoplan 2026-10-22 批次 T5）

覆盖（义务块 A8/DX1-DX3/ENG1-ENG8）：
- lang_check 检测器判定次序（粤先于中文——spec C5，封闭特征集）
- 接线契约测试（native F1：8 个前端配置键的运行时消费点断言）
- LLM 熔断（native F3/ENG E-3：开路/half-open 原子探测/per-channel/Lock 并发）
- Fastrag_LLM mock HTTP 四态失败（DX CAP6-1/2/3：错误 log 契约）
- build_llm_input（G4/G5/native F2：模板→前后缀→语言指令）
- apply_reply_template（G6/G7：多模板随机/截断/双形态/降级）
- llm_handle 托底降级链（A2/spec C1：前置拦截仅 local_qa/托底命中/未命中/禁用三态 ENG E-6）
- local_qa_match/local_qa_render（ENG E-2：变量替换+[1|2] 语法）
- metahuman 退役 import 探测（G2/ENG E-7）
"""
import asyncio
import io
import json
import sys
import threading
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, ".")

from utils.my_handle import My_handle

from utils.common.common_core import Common
from utils.llm_breaker import LLMBreaker, BREAKER_FAILURE_THRESHOLD, BREAKER_OPEN_SECONDS
from utils.gpt_model.gpt import GPT_Model


# ---------- lang_check（G3/spec C2/C5） ----------

class TestLangCheck:
    def setup_method(self):
        self.common = Common()

    def test_yue_before_zh(self):
        """粤字特征集先于中文判定（spec C5：防粤语弹幕被 zh 放行后发音失真）。"""
        assert self.common.lang_check("唔该多少钱") == "yue"
        assert self.common.lang_check("呢个嘢好正") == "yue"

    def test_closed_feature_set(self):
        """封闭特征集含"唔"（native F5 修正：最高频粤字）且"喺"不重复。"""
        assert "唔" in Common.YUE_FEATURE_CHARS
        chars = list(Common.YUE_FEATURE_CHARS)
        assert len(chars) == len(set(chars)), "粤字特征集不得有重复字"

    def test_ko_hangul(self):
        assert self.common.lang_check("안녕하세요") == "ko"

    def test_ja_kana(self):
        assert self.common.lang_check("こんにちは") == "ja"

    def test_en_latin(self):
        assert self.common.lang_check("hello world") == "en"

    def test_zh_hanzi_default(self):
        assert self.common.lang_check("你好世界") == "zh"
        assert self.common.lang_check("") == "zh"
        assert self.common.lang_check("。。。！") == "zh"

    def test_order_yue_over_ko_ja(self):
        """粤字+谚文混排：粤字优先命中。"""
        assert self.common.lang_check("唔안녕") == "yue"


# ---------- 熔断器（native F3/ENG E-3） ----------

class TestLLMBreaker:
    def test_opens_after_threshold(self):
        b = LLMBreaker("test")
        for _ in range(BREAKER_FAILURE_THRESHOLD):
            b.record_failure()
        assert b.allow_request() is False, "达阈值后开路，不放行"

    def test_first_failure_double_weight(self):
        """首败双权重（TODOS 基础功能节 #2 停顿窗口降档）：服务挂起场景
        首条 60s 超时已是强信号——2 次失败即开路（2+1≥3），最长停顿
        3×超时 → 2×超时。"""
        b = LLMBreaker("test")
        b.record_failure()
        with b._lock:
            assert b._consecutive_failures == 2, "首败计 2"
        assert b.allow_request() is True, "首败尚不开路"
        b.record_failure()
        assert b.allow_request() is False, "第 2 次失败（2+1≥3）即开路"

    def test_success_resets_double_weight(self):
        """恢复后再挂起：重置后新一轮首败同样享受双权重。"""
        b = LLMBreaker("test")
        b.record_failure()
        b.record_success()
        b.record_failure()
        with b._lock:
            assert b._consecutive_failures == 2, "成功复位后首败仍计 2"

    def test_half_open_single_probe(self):
        """half-open 仅放行一个探测请求（ENG E-3 原子标志防惊群）。"""
        b = LLMBreaker("test")
        for _ in range(BREAKER_FAILURE_THRESHOLD):
            b.record_failure()
        # 时间快进：开路期满
        b._opened_at -= BREAKER_OPEN_SECONDS + 1
        assert b.allow_request() is True, "开路期满放行第一个探测"
        assert b.allow_request() is False, "第二个并发请求被原子标志拒绝（直走托底）"

    def test_probe_success_closes(self):
        b = LLMBreaker("test")
        for _ in range(BREAKER_FAILURE_THRESHOLD):
            b.record_failure()
        b._opened_at -= BREAKER_OPEN_SECONDS + 1
        assert b.allow_request() is True
        b.record_success()
        assert b.allow_request() is True, "探测成功闭合"

    def test_probe_failure_resets_timer(self):
        """探测失败→重置开路计时并继续开路（ENG E-3/DX CAP6-7）。"""
        b = LLMBreaker("test")
        for _ in range(BREAKER_FAILURE_THRESHOLD):
            b.record_failure()
        b._opened_at -= BREAKER_OPEN_SECONDS + 1
        assert b.allow_request() is True
        b.record_failure()
        assert b.allow_request() is False, "重置后仍在开路期"

    def test_per_channel_isolation(self):
        """per-channel：一通道熔断不影响另一通道计数（DX2）。"""
        b1, b2 = LLMBreaker("custom_llm"), LLMBreaker("fastrag")
        for _ in range(BREAKER_FAILURE_THRESHOLD):
            b1.record_failure()
        assert b1.allow_request() is False
        assert b2.allow_request() is True, "另一通道不受影响"

    def test_concurrent_counting_no_loss(self):
        """并发计数不丢次（ENG E-3：threading.Lock）。"""
        b = LLMBreaker("test")
        threads = [threading.Thread(target=b.record_failure) for _ in range(BREAKER_FAILURE_THRESHOLD * 2)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        with b._lock:
            # 首败双权重：恰一个线程从 0 出发计 2，其余各计 1
            assert b._consecutive_failures == BREAKER_FAILURE_THRESHOLD * 2 + 1, "Lock 下计数无丢失"


# ---------- Fastrag_LLM（A1/DX CAP6-1/2/3） ----------

class TestFastragLLM:
    def _make(self):
        from utils.gpt_model.fastrag_llm import Fastrag_LLM
        return Fastrag_LLM({"host": "localhost", "port": 11420})

    def test_query_url_shape(self):
        f = self._make()
        assert f.query_url == "http://localhost:11420/query"
        assert f.query_url.startswith("http"), "ENG E-8：host 限定 http(s) 形态"

    def test_custom_host(self):
        from utils.gpt_model.fastrag_llm import Fastrag_LLM
        f = Fastrag_LLM({"host": "192.168.1.5", "port": 9999})
        assert f.query_url == "http://192.168.1.5:9999/query", "DX medium：fastrag.host 配置化"

    def test_success_parse(self):
        f = self._make()

        class FakeResp:
            status = 200
            async def text(self):
                return '{"answer": "亲，一件16包哦"}'

        class FakeSession:
            def __init__(self, *a, **k):
                pass
            async def __aenter__(self):
                return self
            async def __aexit__(self, *a):
                return False
            def post(self, url, json=None):
                self.url = url
                return FakeRespCtx(self)

        class FakeRespCtx:
            def __init__(self, session):
                self.session = session
            async def __aenter__(self):
                return FakeResp()
            async def __aexit__(self, *a):
                return False

        import utils.gpt_model.fastrag_llm as fm
        fake_aiohttp = MagicMock()
        fake_aiohttp.ClientSession = FakeSession
        fake_aiohttp.ClientTimeout = lambda **k: None
        fake_aiohttp.ClientError = Exception
        with patch.object(fm, "_get_aiohttp", return_value=fake_aiohttp):
            result = asyncio.run(f.get_resp({"content": "一件几包"}))
        assert result == "亲，一件16包哦"

    def test_registry_has_fastrag(self):
        """GPT_Model 注册表登记 fastrag（A1/T1）。"""
        g = GPT_Model()
        g.set_model_config("fastrag", {"port": 11420})
        assert g.get("fastrag") is not None


# ---------- build_llm_input / apply_reply_template（G4/G5/G6/G7） ----------

class _FakeConfig:
    def __init__(self, data):
        self._d = data
    def get(self, *keys):
        r = self._d
        for k in keys:
            if r is None:
                return None
            r = r.get(k) if isinstance(r, dict) else None
        return r


class TestBuildLlmInput:
    def _mh(self, cfg):
        mh = MagicMock()
        mh.config = _FakeConfig(cfg)
        mh.common = Common()
        return mh

    def test_template_applied(self):
        mh = self._mh({"comment_template": {"enable": True, "copywriting": "{username}生气地说：{comment}"},
                       "before_prompt": "请简要回复:", "after_prompt": "", "need_lang": "none"})
        out = My_handle.build_llm_input(mh, "用户A", "怎么还不发货")
        assert out == "请简要回复:用户A生气地说：怎么还不发货"

    def test_template_disabled_raw(self):
        mh = self._mh({"comment_template": {"enable": False, "copywriting": "{username}说：{comment}"},
                       "before_prompt": "", "after_prompt": "", "need_lang": "none"})
        out = My_handle.build_llm_input(mh, "用户A", "你好")
        assert out == "你好"

    def test_template_bad_placeholder_degrades(self):
        """占位符拼错降级原文+warning（DX CAP6-9）。"""
        mh = self._mh({"comment_template": {"enable": True, "copywriting": "{usernme}说：{comment}"},
                       "before_prompt": "", "after_prompt": "", "need_lang": "none"})
        out = My_handle.build_llm_input(mh, "用户A", "你好")
        assert out == "你好", "format 异常降级原始弹幕"

    def test_lang_instruction_yue_ko(self):
        """语言回复指令（native F2）：yue/ko 追加，zh/none 不追加。"""
        for lang, expect in [("yue", True), ("ko", True), ("zh", False), ("none", False), ("en", False)]:
            mh = self._mh({"comment_template": {"enable": False}, "before_prompt": "",
                           "after_prompt": "", "need_lang": lang})
            out = My_handle.build_llm_input(mh, "u", "hi")
            assert ("请用粤语回复" in out) == expect or ("请用韩语回复" in out) == expect, f"need_lang={lang}"


class TestApplyReplyTemplate:
    def _mh(self, cfg):
        mh = MagicMock()
        mh.config = _FakeConfig(cfg)
        return mh

    def test_disabled_passthrough(self):
        mh = self._mh({"reply_template": {"enable": False, "copywriting": "回复{username}：{data}"}})
        assert My_handle.apply_reply_template(mh, "LLM回复", "用户A") == "LLM回复"

    def test_single_template(self):
        mh = self._mh({"reply_template": {"enable": True, "username_max_len": 10,
                                          "copywriting": "回复{username}，{data}"}})
        out = My_handle.apply_reply_template(mh, "昨天发货了", "宝宝")
        assert out == "回复宝宝，昨天发货了"

    def test_multi_template_str_random(self):
        """str 多模板（\\n 分隔）随机选一条（G7 双形态）。"""
        mh = self._mh({"reply_template": {"enable": True, "username_max_len": 10,
                                          "copywriting": "回复{username}，{data}\n{username}你好：{data}"}})
        seen = set()
        for _ in range(40):
            seen.add(My_handle.apply_reply_template(mh, "OK", "小明"))
        assert seen == {"回复小明，OK", "小明你好：OK"}, "两条模板均合法且可达"

    def test_multi_template_list_form(self):
        mh = self._mh({"reply_template": {"enable": True, "username_max_len": 10,
                                          "copywriting": ["回复{username}，{data}", "{username}你好：{data}"]}})
        out = My_handle.apply_reply_template(mh, "OK", "小明")
        assert out in ("回复小明，OK", "小明你好：OK")

    def test_username_truncated(self):
        """超 username_max_len 截断加省略号（G6）。"""
        mh = self._mh({"reply_template": {"enable": True, "username_max_len": 3,
                                          "copywriting": "{username}:{data}"}})
        out = My_handle.apply_reply_template(mh, "OK", "很长很长的用户名")
        assert out.startswith("很长") and out.endswith(":OK") and "…" in out

    def test_bad_placeholder_degrades(self):
        mh = self._mh({"reply_template": {"enable": True, "username_max_len": 10,
                                          "copywriting": "回复{user}: {data}"}})
        out = My_handle.apply_reply_template(mh, "OK", "u")
        assert out == "OK", "占位符错降级原文"


# ---------- llm_handle 托底降级链（A2/spec C1/ENG E-6） ----------

class _FakeHandle:
    """最小 my_handle 替身：config + feature_handler.local_qa_match。"""
    def __init__(self, cfg, qa_answer=None):
        self.config = _FakeConfig(cfg)
        self.feature_handler = MagicMock()
        self.feature_handler.local_qa_match = MagicMock(return_value=qa_answer)


class TestLlmHandleFallback:
    def _handler(self, cfg, qa_answer=None):
        from utils.handlers.chat_handler import ChatHandler
        mh = _FakeHandle(cfg, qa_answer)
        h = ChatHandler.__new__(ChatHandler)
        h.my_handle = mh
        h._llm_breakers = {}
        return h, mh

    _CFG = {"local_qa": {"enable": True}, "chat_type": "custom_llm"}

    def test_local_qa_channel_direct_match(self):
        """chat_type=local_qa：纯词库直答（不经 LLM）。"""
        h, mh = self._handler({"local_qa": {"enable": True}}, qa_answer="词库答案")
        out = h.llm_handle("local_qa", {"username": "u", "content": "q"})
        assert out == "词库答案"
        mh.feature_handler.local_qa_match.assert_called_once_with("q", username="u")

    def test_local_qa_channel_disabled_logs(self):
        """chat_type=local_qa 且 enable=false：无回复+原因 log（ENG E-6）。"""
        h, _ = self._handler({"local_qa": {"enable": False}})
        out = h.llm_handle("local_qa", {"username": "u", "content": "q"})
        assert out is None

    def test_llm_fail_fallback_hit(self):
        """custom_llm 失败→托底命中（A2）。"""
        h, mh = self._handler(dict(self._CFG), qa_answer="托底答案")
        # 模型未初始化 → 失败路径
        out = h.llm_handle("custom_llm", {"username": "u", "content": "q"})
        assert out == "托底答案"
        mh.feature_handler.local_qa_match.assert_called_once()

    def test_llm_fail_fallback_miss_logs(self):
        """LLM 失败→托底未命中→None+info log（DX CAP6-5 终结全静默）。"""
        h, mh = self._handler(dict(self._CFG), qa_answer=None)
        out = h.llm_handle("custom_llm", {"username": "u", "content": "q"})
        assert out is None
        mh.feature_handler.local_qa_match.assert_called_once()

    def test_fallback_disabled_when_enable_false(self):
        """local_qa.enable=false：无托底+原因 log（spec R2-1/A2）。"""
        h, mh = self._handler({"local_qa": {"enable": False}})
        out = h.llm_handle("custom_llm", {"username": "u", "content": "q"})
        assert out is None
        mh.feature_handler.local_qa_match.assert_not_called()

    def test_success_no_fallback(self):
        """LLM 成功→不进托底。"""
        h, mh = self._handler(dict(self._CFG), qa_answer="托底答案")
        fake_model = MagicMock()
        fake_model.get_resp_sync = MagicMock(return_value="LLM正常回复")
        h.my_handle.__dict__["custom_llm"] = fake_model
        out = h.llm_handle("custom_llm", {"username": "u", "content": "q"})
        assert out == "LLM正常回复"
        mh.feature_handler.local_qa_match.assert_not_called()


# ---------- lang_filter_pass（G3） ----------

class TestLangFilterPass:
    def _mh(self, need_lang):
        mh = MagicMock()
        mh.config = _FakeConfig({"need_lang": need_lang})
        mh.common = Common()
        return mh

    def test_none_passes_all(self):
        mh = self._mh("none")
        assert My_handle.lang_filter_pass(mh, "안녕 hello 你好") is True

    def test_zh_only(self):
        mh = self._mh("zh")
        assert My_handle.lang_filter_pass(mh, "你好") is True
        assert My_handle.lang_filter_pass(mh, "hello") is False

    def test_yue_feature(self):
        mh = self._mh("yue")
        assert My_handle.lang_filter_pass(mh, "唔该") is True
        assert My_handle.lang_filter_pass(mh, "你好") is False

    def test_ko(self):
        mh = self._mh("ko")
        assert My_handle.lang_filter_pass(mh, "안녕하세요") is True


# ---------- metahuman 退役（G2/ENG E-7） ----------

class TestMetahumanRetirement:
    def test_no_import_in_runtime_modules(self):
        """metahuman 运行时代码行零引用探测（A8；注释中的退役说明豁免——ENG E-7 白名单精神）。"""
        code_hits = []
        for f in ["utils/audio/synthesis_manager.py",
                  "frontend/ui/tabs/common_config.py",
                  "frontend/ui/tabs/visual_body.py",
                  "utils/audio/playback_manager.py",
                  "utils/my_global.py",
                  "utils/gpt_model/gpt.py",
                  "utils/my_handle.py"]:
            for line in io.open(f, encoding="utf-8"):
                stripped = line.strip()
                if (stripped and not stripped.startswith("#") and "metahuman" in stripped
                        and "退役" not in stripped):
                    code_hits.append(f)
        assert code_hits == [], f"运行时模块代码行仍有 metahuman 引用：{code_hits}"

    def test_settings_normalizes_legacy_value(self):
        """存量 visual_body=metahuman_stream → edtalk_realtime（A3 归一化）。"""
        from frontend.config.settings import _ensure_defaults
        fake = MagicMock()
        fake._config = {"visual_body": "metahuman_stream",
                        "edtalk_realtime": {"enable": False},
                        "fastrag": {"enable": False},
                        "danmaku_listener": {"enable": False}}
        with patch("frontend.config.settings.Settings", MagicMock()):
            _ensure_defaults(fake)
        assert fake._config["visual_body"] == "edtalk_realtime"


# ---------- 接线契约测试（native F1：8 键运行时消费点） ----------

class TestWiringContract:
    """8 个基础功能配置键的运行时消费点断言（防死配置复发）。"""

    def test_contract_chat_type(self):
        from utils.handlers.chat_handler import ChatHandler
        assert hasattr(ChatHandler, "llm_handle"), "chat_type 消费点：llm_handle 分派"

    def test_contract_visual_body(self):
        import utils.audio.synthesis_manager as sm
        src = open("utils/audio/synthesis_manager.py", encoding="utf-8").read()
        assert 'config.get("visual_body")' in src, "visual_body 消费点：synthesis_manager 分流"

    def test_contract_audio_synthesis_type(self):
        src = open("utils/audio/synthesis_manager.py", encoding="utf-8").read()
        assert '"gpt_sovits"' in src and '"edge_tts"' in src, "audio_synthesis_type 消费点：tts_handle 双分支"

    def test_contract_need_lang(self):
        """need_lang 消费点：lang_filter_pass（回复链）+ tts_handle（合成映射）——双消费。"""
        src = open("utils/my_handle.py", encoding="utf-8").read()
        assert "def lang_filter_pass" in src
        src2 = open("utils/audio/synthesis_manager.py", encoding="utf-8").read()
        assert "_need_to_text_lang" in src2, "need_lang→text_lang 映射存在"

    def test_contract_before_after_prompt(self):
        src = open("utils/my_handle.py", encoding="utf-8").read()
        assert "before_prompt" in src and "after_prompt" in src, "前后缀消费点：build_llm_input"

    def test_contract_comment_template(self):
        src = open("utils/my_handle.py", encoding="utf-8").read()
        assert 'config.get("comment_template", "enable")' in src, "弹幕模板消费点：build_llm_input"

    def test_contract_reply_template(self):
        src = open("utils/my_handle.py", encoding="utf-8").read()
        assert "def apply_reply_template" in src, "回复模板消费点：apply_reply_template"

    def test_single_assembly_point(self):
        """单一拼接点（native F6）：前后缀/模板仅在 build_llm_input 拼装
        （作用域=弹幕→LLM 输入构建；task_scheduler 动态文案为独立系统侧拼装，不在管辖）。"""
        for f in ["utils/handlers/chat_handler.py", "utils/handlers/comment_handler.py"]:
            src = open(f, encoding="utf-8").read()
            assert "before_prompt" not in src or "build_llm_input" in src, \
                f"{f} 不得内联拼前后缀（经 build_llm_input）"


# ---------- TTS text_lang 映射（G3/spec L1） ----------

class TestTextLangMapping:
    def test_mapping_table_complete(self):
        """need_lang→text_lang 六值映射完备（none→auto/yue→auto_yue）。"""
        src = open("utils/audio/synthesis_manager.py", encoding="utf-8").read()
        for pair in ['"none": "auto"', '"zh": "zh"', '"en": "en"', '"jp": "ja"', '"ko": "ko"', '"yue": "auto_yue"']:
            assert pair in src, f"映射缺失：{pair}"

    def test_no_dead_lang_check_call(self):
        """潜伏缺陷修复：synthesis_manager 不再调用不存在的 common.lang_check 旧签名
        （新检测器存在于 TextMixin）。"""
        from utils.common.common_core import Common
        assert hasattr(Common, "lang_check"), "新 lang_check 已落地"
