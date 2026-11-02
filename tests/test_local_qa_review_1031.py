# -*- coding: UTF-8 -*-
"""
本地问答功能审查守卫（2026-10-31 本地问答审查批次）

任务书落实：
1. 周期性触发全链退役（触发周期/触发次数最小最大值）——
   timer_handler.periodic_trigger_data_handle、my_handle.task_data 缓冲、
   UI 周期区、config periodic_trigger 子节全删 + _RETIRED_CONFIG_KEYS 登记；
   本地问答命中即时合成播放（task_data["local_qa"] 缓冲自始零写入点，死机制）。
2. 用户名最大长度退役（local_qa.text.username_max_len 后端零消费死键）。
3. 文本问答数据路径退役（local_qa.text.file_path / 助播同构）——迁移为前端
   textarea 直编实时保存（与闲时任务-文案列表同构：textarea_data_change +
   split_lines=True）；存量未重存配置经迁移桥（data 缺失→file_path）回退文件词库。
4. 音频匹配算法（包含关系=精确+包含 / 相似度匹配=精确+包含+difflib）与
   音频存储路径目录语义（音频文件名即问题）守卫。
5. 接入链守卫：chat_type=local_qa 直答、LLM 失败托底、主 KB（keyword_hub）、
   local_qa_handle（文本+音频）四消费点统一走 local_qa_match。
"""

import json
import os
import tempfile

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def _src(rel):
    with open(os.path.join(_ROOT, rel), encoding="utf-8") as f:
        return f.read()


class TestPeriodicTriggerRetired:
    """周期性触发全链退役守卫。"""

    def test_timer_handler_method_removed(self):
        src = _src("utils/handlers/timer_handler.py")
        assert "def periodic_trigger_data_handle" not in src
        assert ".task_data" not in src  # 缓冲读写点随之消失（注释内标识符不算）
        assert "周期触发已退役" in src  # 退役注释在位（答谢批次同短语锚定）

    def test_my_handle_buffer_removed(self):
        src = _src("utils/my_handle.py")
        assert "def periodic_trigger_data_handle" not in src
        assert "task_data = {" not in src  # 类属性缓冲已删（退役注释不含赋值）

    def test_ui_controls_removed(self):
        src = _src("frontend/ui/tabs/local_qa.py")
        assert "periodic_trigger" not in src
        assert "触发周期" not in src and "触发次数" not in src

    def test_template_clean_and_retired_registered(self):
        example = json.loads(_src("config.example.json"))
        assert "periodic_trigger" not in example["local_qa"]
        src = _src("frontend/main.py")
        assert '("local_qa", "periodic_trigger")' in src


class TestUsernameMaxLenRetired:
    """用户名最大长度退役守卫（后端零消费死键）。"""

    def test_ui_removed(self):
        src = _src("frontend/ui/tabs/local_qa.py")
        assert "username_max_len" not in src
        assert "用户名最大长度" not in src

    def test_template_clean_and_retired_registered(self):
        example = json.loads(_src("config.example.json"))
        assert "username_max_len" not in example["local_qa"].get("text", {})
        src = _src("frontend/main.py")
        assert '("local_qa", "text", "username_max_len")' in src

    def test_backend_zero_consumption(self):
        assert "username_max_len" not in _src("utils/handlers/feature_handler.py")


class TestTextQaDataFrontend:
    """文本问答数据迁移前端 textarea 直编实时保存（核心优化目标）。"""

    def test_main_ui_textarea_wired(self):
        src = _src("frontend/ui/tabs/local_qa.py")
        # text file_path 输入框退役（音频 file_path 保留不受影响）
        assert '("local_qa", "text", "file_path")' not in src
        # textarea 直编 + 实时保存（与闲时任务-文案列表同构）
        assert "textarea_data_change" in src
        assert "label='文本问答数据'" in src
        assert '("local_qa", "text", "data"), split_lines=True' in src

    def test_assistant_ui_textarea_wired(self):
        src = _src("frontend/ui/tabs/assistant_anchor.py")
        assert '("assistant_anchor", "local_qa", "text", "file_path")' not in src
        assert "textarea_data_change" in src
        assert "label='文本问答数据'" in src
        assert '("assistant_anchor", "local_qa", "text", "data"), split_lines=True' in src

    def test_template_data_inline(self):
        example = json.loads(_src("config.example.json"))
        lq_text = example["local_qa"]["text"]
        assert "file_path" not in lq_text
        assert isinstance(lq_text["data"], list) and lq_text["data"]
        assert all("|" in line for line in lq_text["data"])
        aa_text = example["assistant_anchor"]["local_qa"]["text"]
        assert "file_path" not in aa_text
        assert isinstance(aa_text["data"], list) and aa_text["data"]

    def test_backend_reads_data_key(self):
        assert 'config.get("local_qa", "text", "data")' in \
            _src("utils/handlers/feature_handler.py")
        assert 'config.get("assistant_anchor", "local_qa", "text", "data")' in \
            _src("utils/handlers/assistant_router.py")

    def test_legacy_file_path_bridge(self):
        """存量迁移桥：data 缺失（未重存旧配置）→ file_path 回退。"""
        fh_src = _src("utils/handlers/feature_handler.py")
        assert '"local_qa", "text", "file_path"' in fh_src
        assert "存量迁移桥" in fh_src
        ar_src = _src("utils/handlers/assistant_router.py")
        assert '"assistant_anchor", "local_qa", "text", "file_path"' in ar_src

    def test_retired_keys_registered(self):
        src = _src("frontend/main.py")
        assert '("local_qa", "text", "file_path")' in src
        assert '("assistant_anchor", "local_qa", "text", "file_path")' in src


class TestQaDataSemantics:
    """问答数据解析与匹配行为（config 内嵌行列表源）。"""

    def test_parse_qa_entries_semantics(self):
        from utils.handlers.assistant_router import parse_qa_entries
        entries = parse_qa_entries([
            "你好|你好呀", "没有分隔符的行", "只有问题|", "",
            "怎么称呼[小明|小红]|左|右",
        ], "测试")
        assert [(q, a) for q, a, _t in entries] == [
            ("你好", "你好呀"), ("怎么称呼[小明|小红]", "左|右")]
        assert all(len(e) == 3 for e in entries)  # 词条预分词三元组
        assert parse_qa_entries(None) == []
        assert parse_qa_entries([]) == []

    def test_load_qa_entries_from_config_degrades(self):
        from utils.handlers.assistant_router import load_qa_entries_from_config
        assert load_qa_entries_from_config(123) == []  # 异常降级空词条集

    def test_qa_entries_dual_source(self):
        """_qa_entries 双源：list→config 内嵌解析；str→文件加载器（别名锚点）。"""
        from utils.handlers.feature_handler import FeatureHandler
        fh = FeatureHandler.__new__(FeatureHandler)
        entries = fh._qa_entries(["怎么点歌|发送点歌歌名即可"])
        assert [(q, a) for q, a, _t in entries] == [("怎么点歌", "发送点歌歌名即可")]
        assert fh._qa_entries(None) == []
        tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, encoding="utf-8")
        tmp.write("怎么点歌|发送点歌歌名即可\n")
        tmp.close()
        try:
            entries = fh._qa_entries(tmp.name)
            assert [(q, a) for q, a, _t in entries] == [("怎么点歌", "发送点歌歌名即可")]
        finally:
            os.unlink(tmp.name)

    def _fake_config(self, d):
        from tests.test_assistant_anchor_routing import FakeConfig
        return FakeConfig(d)

    def test_match_assistant_text_data_source(self):
        """match_assistant_text 直读 assistant_anchor.local_qa.text.data。"""
        from utils.handlers.assistant_router import match_assistant_text
        cfg = self._fake_config({
            "assistant_anchor": {"local_qa": {"text": {
                "data": ["怎么点歌|发送点歌歌名即可", "主播多大了|永远18岁"],
                "similarity": 0.6}}}})
        assert match_assistant_text(cfg, "怎么点歌") == "发送点歌歌名即可"
        assert match_assistant_text(cfg, "完全无关的问题内容") is None

    def test_match_assistant_text_legacy_bridge(self):
        """迁移桥：data 缺省+file_path→文件词库命中；data=[]（用户清空）→不回退。"""
        from utils.handlers.assistant_router import match_assistant_text
        tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, encoding="utf-8")
        tmp.write("怎么点歌|发送点歌歌名即可\n")
        tmp.close()
        text_sec = {"file_path": tmp.name, "similarity": 0.6}
        try:
            cfg = self._fake_config({
                "assistant_anchor": {"local_qa": {"text": text_sec}}})
            assert match_assistant_text(cfg, "怎么点歌") == "发送点歌歌名即可"
            # 用户在前端清空 textarea → data=[] 落盘 → 不复活旧文件词库
            cfg2 = self._fake_config({
                "assistant_anchor": {"local_qa": {"text": dict(
                    text_sec, data=[])}}})
            assert match_assistant_text(cfg2, "怎么点歌") is None
        finally:
            os.unlink(tmp.name)

    def test_local_qa_match_data_source(self):
        """主链 local_qa_match 读 local_qa.text.data（经 _qa_best_match）。"""
        from tests.test_assistant_anchor_routing import FakeConfig
        from utils.common import Common
        from utils.handlers.feature_handler import FeatureHandler

        class _Stub:
            common = Common()

            def __init__(self, cfg):
                self.config = FakeConfig(cfg)

        fh = FeatureHandler.__new__(FeatureHandler)
        fh.my_handle = _Stub({
            "local_qa": {"enable": True,
                         "text": {"enable": True, "similarity": 0.6,
                                  "data": ["怎么点歌|发送点歌歌名即可"]}}})
        assert fh.local_qa_match("怎么点歌") == "发送点歌歌名即可"
        assert fh.local_qa_match("完全无关的问题内容") is None


class TestAudioMatching:
    """音频匹配：双档位算法 + 目录路径语义。"""

    def test_ui_tier_options_and_dir_semantics(self):
        src = _src("frontend/ui/tabs/local_qa.py")
        assert "'包含关系'" in src and "'相似度匹配'" in src
        assert "音频目录路径（非单个文件）" in src  # placeholder/tooltip 目录语义

    def test_match_audio_titles_tiers(self):
        """包含关系档=精确+包含（无 difflib）；相似度匹配档追加分档。"""
        from utils.handlers.assistant_router import match_audio_titles
        d = tempfile.mkdtemp(prefix="lqa_audio_")
        open(os.path.join(d, "怎么点歌.mp3"), "wb").close()
        try:
            # 精确命中（两档一致）
            hit = match_audio_titles(d, "怎么点歌", tier="包含关系")
            assert hit is not None and hit.endswith("怎么点歌.mp3")
            # 档位差异：近似查询仅相似度匹配档命中
            assert match_audio_titles(d, "怎么点哟", tier="包含关系") is None
            hit = match_audio_titles(d, "怎么点哟", tier="相似度匹配", threshold=0.5)
            assert hit is not None and hit.endswith("怎么点歌.mp3")
        finally:
            for f in os.listdir(d):
                os.unlink(os.path.join(d, f))
            os.rmdir(d)

    def test_match_audio_titles_dir_semantics(self):
        """音频存储路径=目录路径：缺失/非目录按空词条集降级（None）。"""
        from utils.handlers.assistant_router import match_audio_titles
        assert match_audio_titles("", "怎么点歌") is None
        assert match_audio_titles(os.path.join(_ROOT, "no_such_dir_1031"),
                                  "怎么点歌") is None

    def test_backend_wiring_reads_tier_and_dir(self):
        src = _src("utils/handlers/feature_handler.py")
        assert 'config.get("local_qa", "audio", "type")' in src
        assert 'config.get("local_qa", "audio", "file_path")' in src


class TestSystemWiring:
    """接入链守卫：四消费点统一走 local_qa_match。"""

    def test_chat_type_local_qa_direct_channel(self):
        """chat_type=local_qa 直答通道（llm_handle 内纯词库直答）。"""
        src = _src("utils/handlers/chat_handler.py")
        assert 'chat_type == "local_qa"' in src
        assert "local_qa_match" in src

    def test_llm_failure_fallback_channel(self):
        """LLM 失败托底通道（_try_local_qa_fallback：LLM 通道失败唯一汇入口）。"""
        src = _src("utils/handlers/chat_handler.py")
        assert "_try_local_qa_fallback" in src
        assert "local_qa_match" in src

    def test_main_kb_channel(self):
        """主 KB 通道（keyword_hub：chat_type=local_qa 时 local_qa_match 优先）。"""
        src = _src("utils/keyword_hub.py")
        assert 'chat_type") != "local_qa"' in src
        assert "local_qa_match" in src

    def test_local_qa_handle_produce_channel(self):
        """local_qa_handle 产线：文本匹配→TTS、音频命中→直接播放。"""
        src = _src("utils/handlers/feature_handler.py")
        assert "def local_qa_handle" in src
        assert "match_audio_titles" in src
        # 弹幕/语音路径消费 rec.local_qa_answer → local_qa_handle
        assert "rec.local_qa_answer and self.my_handle.local_qa_handle" in \
            _src("utils/handlers/comment_handler.py")

    def test_assistant_audio_bridge_wired(self):
        """助播音频匹配读 assistant_anchor.local_qa.audio.*（目录路径语义同构）。"""
        src = _src("utils/handlers/assistant_router.py")
        assert 'config.get("assistant_anchor", "local_qa", "audio", "file_path")' in src
        assert 'config.get("assistant_anchor", "local_qa", "audio", "type")' in src
