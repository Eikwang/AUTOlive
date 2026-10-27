# -*- coding: UTF-8 -*-
"""
架构优化批次守卫测试（2026-10-27，flowchartTD v2.4）

对应 docs/flowchartTD.md v2.4 五项优化：
① LLM 流式回复→逐句 TTS 流水线（custom_llm.stream_resp / chat_handler.
   split_stream_sentences / stream_reply_gate / llm_stream_reply）
② 点歌切片插播（synthesis_manager._split_audio_segments / song_segment_seconds
   配置 / song 优先级 20→10 一次性迁移 / playback 末段清理）
④ 翻译目标语言一致跳过（my_translate.trans_reply / _target_lang）
"""

import os
import shutil
import tempfile
import time
import unittest
from unittest.mock import MagicMock

import pytest

from utils.audio.synthesis_manager import SynthesisMixin
from utils.handlers.chat_handler import ChatHandler, split_stream_sentences


class _FakeConfig:
    """模拟 Config.get(*keys) 变长键逐层读取（口径同 test_keyword_hub）。"""

    def __init__(self, cfg):
        self._cfg = cfg

    def get(self, *keys):
        node = self._cfg
        for k in keys:
            if not isinstance(node, dict) or k not in node:
                return None
            node = node[k]
        return node


# ==================== ① 流式分句 ====================


class TestSplitStreamSentences:
    """增量分句器：句末标点即吐句、跨 delta 累积、尾部余量、空增量跳过。"""

    def test_accumulates_across_deltas(self):
        out = list(split_stream_sentences(["你好", "呀！", "最近", "怎么样？"]))
        assert out == ["你好呀！", "最近怎么样？"]

    def test_tail_without_punctuation_flushed(self):
        assert list(split_stream_sentences(["今天天气", "不错"])) == ["今天天气不错"]

    def test_newline_is_sentence_end(self):
        assert list(split_stream_sentences(["第一句\n第二句"])) == ["第一句", "第二句"]

    def test_empty_deltas_skipped(self):
        assert list(split_stream_sentences(["", "好。", "", None])) == ["好。"]

    def test_empty_stream_no_output(self):
        assert list(split_stream_sentences([])) == []

    def test_semicolon_and_english(self):
        out = list(split_stream_sentences(["a; b! c?"]))
        assert out == ["a;", "b!", "c?"]


class TestStreamReplyGate:
    """流式链路保守门：缺省开启；翻译回复/模板/按键映射回复型/custom_llm
    stream_reply=False 任一命中即回退全文链路。"""

    def _handler(self, cfg=None):
        mh = MagicMock()
        mh.config = _FakeConfig(cfg or {})
        return ChatHandler(mh)

    def test_default_gate_open(self):
        assert self._handler().stream_reply_gate("custom_llm") is True

    def test_non_custom_llm_closed(self):
        assert self._handler().stream_reply_gate("openai") is False

    def test_stream_reply_false_closed(self):
        assert self._handler({"custom_llm": {"stream_reply": False}}) \
            .stream_reply_gate("custom_llm") is False

    def test_translate_reply_closed(self):
        assert self._handler({"translate": {"enable": True, "trans_type": "回复"}}) \
            .stream_reply_gate("custom_llm") is False

    def test_reply_template_closed(self):
        assert self._handler({"reply_template": {"enable": True}}) \
            .stream_reply_gate("custom_llm") is False

    def test_keymap_reply_closed(self):
        assert self._handler({"key_mapping": {"type": "弹幕+回复"}}) \
            .stream_reply_gate("custom_llm") is False


# ==================== ② 点歌切片插播 ====================


class TestSongSegmentSeconds:
    """切片时长读侧：缺省 45s；≤0 关闭；非法值回落 45s。"""

    def _mixin(self, raw):
        m = SynthesisMixin.__new__(SynthesisMixin)
        m.config = _FakeConfig({"play_audio": {"song_segment_seconds": raw}})
        return m

    def test_default_when_missing(self):
        m = SynthesisMixin.__new__(SynthesisMixin)
        m.config = _FakeConfig({})
        assert m._song_segment_seconds() == 45.0

    def test_zero_disables(self):
        assert self._mixin(0)._song_segment_seconds() == 0.0
        assert self._mixin(-1)._song_segment_seconds() == 0.0

    def test_invalid_falls_back(self):
        assert self._mixin("abc")._song_segment_seconds() == 45.0

    def test_positive_value_kept(self):
        assert self._mixin(30)._song_segment_seconds() == 30.0


class TestSongSegmentPurge(unittest.TestCase):
    """陈旧切片目录清扫：仅清 .segs_ 前缀且 mtime 超 1h；新目录保留。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="song_purge_")
        self.m = SynthesisMixin.__new__(SynthesisMixin)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _mkdir(self, name, age_s=0):
        d = os.path.join(self.tmp, name)
        os.makedirs(d, exist_ok=True)
        if age_s:
            old = time.time() - age_s
            os.utime(d, (old, old))
        return d

    def test_stale_segs_dir_removed(self):
        stale = self._mkdir(".segs_old", age_s=3700)
        fresh = self._mkdir(".segs_new")
        other = self._mkdir("normal_dir")
        self.m._purge_stale_song_segments(self.tmp)
        assert not os.path.isdir(stale)
        assert os.path.isdir(fresh)
        assert os.path.isdir(other)

    def test_missing_base_dir_noop(self):
        self.m._purge_stale_song_segments(os.path.join(self.tmp, "nope"))  # 不抛异常


class TestSongPriorityMigration(unittest.TestCase):
    """② 存量 song 优先级一次性迁移：精确命中旧默认 20 → 10；自定义值尊重不动。"""

    def _core(self, mapping):
        from utils.audio.audio_core import Audio

        class _Host:
            config = MagicMock()
            config.config = {"filter": {"priority_mapping": dict(mapping)}}
            config_path = "x"
            _migrate_song_priority_v2_4 = Audio._migrate_song_priority_v2_4

        return _Host()

    def test_legacy_default_bumped(self):
        host = self._core({"song": 20, "comment": 20})
        host._migrate_song_priority_v2_4()
        assert host.config.config["filter"]["priority_mapping"]["song"] == 10
        assert host.config.config["filter"]["priority_mapping"]["comment"] == 20

    def test_custom_value_untouched(self):
        host = self._core({"song": 15})
        host._migrate_song_priority_v2_4()
        assert host.config.config["filter"]["priority_mapping"]["song"] == 15

    def test_already_migrated_idempotent(self):
        host = self._core({"song": 10})
        host._migrate_song_priority_v2_4()
        assert host.config.config["filter"]["priority_mapping"]["song"] == 10

    def test_no_filter_section_noop(self):
        from utils.audio.audio_core import Audio

        class _Host:
            config = MagicMock()
            config.config = {}
            config_path = "x"
            _migrate_song_priority_v2_4 = Audio._migrate_song_priority_v2_4

        host = _Host()
        host._migrate_song_priority_v2_4()  # 不抛异常
        assert host.config.config == {}


# ==================== ④ 翻译目标语言一致跳过 ====================


class _FakeTranslateHost:
    """my_translate 测试桩：config_data/common/_target_lang 别名表复用真实实现。"""

    def __init__(self, trans_type, to_lang, detected):
        self.config_data = {"type": trans_type}
        self.baidu_config = {"to_lang": to_lang}
        self.common = MagicMock()
        self.common.lang_check.return_value = detected
        self.trans = MagicMock(return_value="TRANSLATED")
        import types
        from utils.my_translate import My_Translate
        self._LANG_CODE_ALIASES = My_Translate._LANG_CODE_ALIASES
        self._target_lang = types.MethodType(My_Translate._target_lang, self)
        self.trans_reply = types.MethodType(My_Translate.trans_reply, self)


class TestTransReplySkip:
    """trans_reply：目标语言与文本一致 → 跳过翻译 API 往返；不一致 → 走 trans。"""

    def test_same_lang_skips_api(self):
        host = _FakeTranslateHost("baidu", "zh", "zh")
        out = host.trans_reply("你好世界")
        assert out == "你好世界"
        host.trans.assert_not_called()

    def test_diff_lang_calls_api(self):
        host = _FakeTranslateHost("baidu", "en", "zh")
        out = host.trans_reply("你好世界")
        assert out == "TRANSLATED"
        host.trans.assert_called_once_with("你好世界", "baidu")

    def test_unknown_target_falls_through(self):
        host = _FakeTranslateHost("baidu", "unknown_lang", "zh")
        out = host.trans_reply("你好世界")
        assert out == "TRANSLATED"  # 无法判定 → 照常翻译

    def test_api_fail_returns_original(self):
        host = _FakeTranslateHost("baidu", "en", "zh")
        host.trans = MagicMock(return_value=None)
        assert host.trans_reply("你好世界") == "你好世界"

    def test_lang_alias_mapping(self):
        from utils.my_translate import My_Translate
        assert My_Translate._LANG_CODE_ALIASES["jp"] == "ja"
        assert My_Translate._LANG_CODE_ALIASES["zh-cn"] == "zh"
        assert My_Translate._LANG_CODE_ALIASES["auto_yue"] == "yue"


if __name__ == "__main__":
    unittest.main(verbosity=2)
