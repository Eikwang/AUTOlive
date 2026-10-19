# -*- coding: UTF-8 -*-
"""
切片 3 静态部分测试：TTS 参数精简/变速迁移（P26）/多参考降级（P25）/argos 翻译（P27）

覆盖（任务书 tests 清单）：
- 变速映射：edge-tts rate 百分比 ↔ speed_factor 换算
- argos 语言对探测 mock：支持 / 不支持 / 未安装 三分支
- ref_free 降级逻辑：多参考/ref_free 合成失败 → 降级单参考重试
- edge-tts voice 清单过滤

P25/P26/P27 实测 spike（需 GPU/运行环境）不在本测试范围：
- inproc 通道 N>1 参考音频与 ref_free 生效性
- inproc speed_factor 变速生效性
- argos-translate 真实依赖与离线模型
"""
import asyncio
import json
import os
import sys
import tempfile
import types

import pytest

from utils.audio_handle.tts_params import (
    EDGE_TTS_DEFAULT_VOICE, EDGE_TTS_VOICES,
    build_inproc_refs, edge_rate_to_speed, filter_edge_voices,
    normalize_ref_paths, parse_edge_extra_args, speed_to_edge_rate)
from utils import translate_argos


# ---------- P26：变速映射 ----------

class TestEdgeRateSpeedMapping:
    def test_rate_to_speed_positive(self):
        """--rate=+20% 即加快 1.2 倍（任务书原文）。"""
        assert edge_rate_to_speed("+20%") == pytest.approx(1.2)
        assert edge_rate_to_speed("=+20%") == pytest.approx(1.2)

    def test_rate_to_speed_negative(self):
        """--rate=-10% 即减慢 0.9 倍（任务书原文）。"""
        assert edge_rate_to_speed("-10%") == pytest.approx(0.9)

    def test_rate_to_speed_default(self):
        assert edge_rate_to_speed("100%") == pytest.approx(1.0)
        assert edge_rate_to_speed("+0%") == pytest.approx(1.0)
        assert edge_rate_to_speed("") == 1.0
        assert edge_rate_to_speed("abc") == 1.0  # 非法输入回退系统默认

    def test_speed_to_rate_roundtrip(self):
        assert speed_to_edge_rate(1.2) == "+20%"
        assert speed_to_edge_rate(0.9) == "-10%"
        assert speed_to_edge_rate(1.0) == "+0%"
        # 往返一致性
        for speed in (0.5, 0.8, 1.0, 1.3, 2.0):
            assert edge_rate_to_speed(speed_to_edge_rate(speed)) == pytest.approx(speed)


# ---------- edge-tts voice 清单过滤 ----------

class TestEdgeVoiceFilter:
    def test_filter_by_keyword(self):
        result = filter_edge_voices("xiaoxiao")
        assert result == ["zh-CN-XiaoxiaoNeural"]

    def test_filter_case_insensitive(self):
        assert filter_edge_voices("XIAOXIAO") == ["zh-CN-XiaoxiaoNeural"]

    def test_filter_by_prefix(self):
        result = filter_edge_voices("zh-CN")
        assert result and all(v.startswith("zh-CN") for v in result)
        assert "ja-JP-NanamiNeural" not in result

    def test_empty_keyword_returns_all(self):
        assert filter_edge_voices("") == EDGE_TTS_VOICES
        assert filter_edge_voices(None) == EDGE_TTS_VOICES

    def test_no_match(self):
        assert filter_edge_voices("nonexistent-voice") == []

    def test_default_voice_declared(self):
        """D-18：默认值写明具体 voice 名。"""
        assert EDGE_TTS_DEFAULT_VOICE == "zh-CN-XiaoxiaoNeural"


# ---------- P25：多参考装配与归一化 ----------

class TestRefPaths:
    def test_normalize_from_multiline(self):
        paths = normalize_ref_paths("D:/a.wav\nD:/b.wav；D:/c.wav")
        assert paths == ["D:/a.wav", "D:/b.wav", "D:/c.wav"]

    def test_normalize_dedup_and_blank(self):
        paths = normalize_ref_paths("D:/a.wav\n\nD:/a.wav, ")
        assert paths == ["D:/a.wav"]

    def test_normalize_from_list(self):
        assert normalize_ref_paths(["D:/a.wav", " D:/b.wav "]) == ["D:/a.wav", "D:/b.wav"]
        assert normalize_ref_paths(None) == []

    def test_build_inproc_refs_multi(self):
        frag = build_inproc_refs(["a.wav", "b.wav", "c.wav"], ref_free=True, prompt_text="旧文本")
        assert frag == {"ref_audio_path": "a.wav",
                        "aux_ref_audio_paths": ["b.wav", "c.wav"],
                        "prompt_text": ""}  # ref_free：参考文本不透传

    def test_build_inproc_refs_single_no_free(self):
        frag = build_inproc_refs(["a.wav"], ref_free=False, prompt_text="参考文本")
        assert frag == {"ref_audio_path": "a.wav", "prompt_text": "参考文本"}

    def test_build_inproc_refs_empty(self):
        assert build_inproc_refs([], ref_free=True, prompt_text="x") == {"prompt_text": ""}


# ---------- P27：argos 语言对探测三分支（mock） ----------

def _fake_argos_api(pairs):
    """构造 argostranslate.translate 形状的假 API。

    pairs: {(src_code, dst_code): 译文函数}——仅登记已安装语言对。
    """
    class FakeTranslation:
        def __init__(self, fn):
            self._fn = fn

        def translate(self, text):
            return self._fn(text)

    class FakeLanguage:
        def __init__(self, code, pairs):
            self.code = code
            self._pairs = pairs

        def get_translation(self, other):
            fn = self._pairs.get((self.code, other.code))
            return FakeTranslation(fn) if fn else None

    langs = {code: FakeLanguage(code, pairs) for pair in pairs for code in pair}
    fake = types.SimpleNamespace()
    fake.get_installed_languages = lambda: list(langs.values())
    return fake


class TestArgosLanguagePair:
    def test_pair_supported(self, monkeypatch):
        api = _fake_argos_api({("en", "zh"): lambda t: "你好"})
        monkeypatch.setattr(translate_argos, "_load_translate_api", lambda: api)
        supported, mark = translate_argos.check_language_pair("en", "zh")
        assert supported and mark == ""
        assert translate_argos.translate_argos("hello", "en", "zh") == "你好"

    def test_pair_unsupported(self, monkeypatch):
        api = _fake_argos_api({("en", "zh"): lambda t: "你好"})  # 无 ja 模型
        monkeypatch.setattr(translate_argos, "_load_translate_api", lambda: api)
        supported, mark = translate_argos.check_language_pair("ja", "zh")
        assert not supported and mark == "pair_unsupported"
        assert translate_argos.translate_argos("こんにちは", "ja", "zh") is None

    def test_not_installed(self, monkeypatch):
        monkeypatch.setattr(translate_argos, "_load_translate_api", lambda: None)
        supported, mark = translate_argos.check_language_pair("en", "zh")
        assert not supported and mark == "not_installed"
        # T4 降级：translate_argos 返回 None 而非抛出
        assert translate_argos.translate_argos("hello", "en", "zh") is None

    def test_lang_normalize(self):
        assert translate_argos.normalize_argos_lang("zh-CN") == "zh"
        assert translate_argos.normalize_argos_lang("en-US") == "en"
        assert translate_argos.normalize_argos_lang("auto") == "auto"

    def test_detect_lang_hint(self):
        assert translate_argos.detect_lang_hint("こんにちは") == "ja"
        assert translate_argos.detect_lang_hint("你好世界") == "zh"
        assert translate_argos.detect_lang_hint("hello world") == "en"


# ---------- P25：ref_free/多参考降级（合成调用构造处） ----------

def _make_tts(tmpdir, params):
    """构造 MY_TTS 实例（临时 config），params 写入 gpt_sovits.params 节。

    Config 为类级缓存单例——写盘后必须 reload 指向本用例文件，防用例间串数据。"""
    from utils.audio_handle.my_tts import MY_TTS
    from utils.config import Config
    cfg_path = os.path.join(tmpdir, "config.json")
    with open(cfg_path, "w", encoding="utf-8") as f:
        json.dump({"play_audio": {"out_path": tmpdir},
                   "gpt_sovits": {"backend": "inproc", "params": params}}, f)
    Config.reload(cfg_path)
    return MY_TTS(cfg_path)


def _fake_engine(calls, fail_first=True):
    """假引擎：第一次 synthesize 抛异常（模拟 inproc 不支持），第二次成功。"""
    class FakeEngine:
        state = "ready"
        ready = True

        def raw_state(self):
            return "ready"

        def synthesize(self, text, params, out_path):
            calls.append(dict(params))
            if fail_first and len(calls) == 1:
                raise RuntimeError("mock: aux_ref_audio_paths/ref_free 不支持")
            os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
            with open(out_path, "wb") as f:
                f.write(b"RIFF")
            return out_path

    return FakeEngine()


class TestRefFreeFallback:
    def test_multi_ref_degrades_to_single(self, monkeypatch, tmp_path):
        """P25 回退预案：多参考/ref_free 失败 → 降级单参考重试并成功。"""
        import utils.gpt_sovits_inproc as gsv
        calls = []
        monkeypatch.setattr(gsv, "get_engine", lambda: _fake_engine(calls))
        tmpdir = str(tmp_path)
        tts = _make_tts(tmpdir, {
            "ref_audio_paths": ["D:/a.wav", "D:/b.wav", "D:/c.wav"],
            "ref_free": True,
            "prompt_text": "旧参考文本",
        })
        out = asyncio.run(tts._gpt_sovits_inproc({"content": "测试", "language": "zh"}))
        assert out is not None  # 降级后成功
        assert len(calls) == 2
        # 第一次：多参考 + 无参考文本
        assert calls[0]["ref_audio_path"] == "D:/a.wav"
        assert calls[0]["aux_ref_audio_paths"] == ["D:/b.wav", "D:/c.wav"]
        assert calls[0]["prompt_text"] == ""
        # 第二次（降级）：单参考 + 恢复旧参考文本
        assert "aux_ref_audio_paths" not in calls[1]
        assert calls[1]["ref_audio_path"] == "D:/a.wav"
        assert calls[1]["prompt_text"] == "旧参考文本"

    def test_multi_ref_first_try_success(self, monkeypatch, tmp_path):
        """P25 生效路径：多参考首次成功不降级。"""
        import utils.gpt_sovits_inproc as gsv
        calls = []
        monkeypatch.setattr(gsv, "get_engine", lambda: _fake_engine(calls, fail_first=False))
        tmpdir = str(tmp_path)
        tts = _make_tts(tmpdir, {
            "ref_audio_paths": ["D:/a.wav", "D:/b.wav"],
            "ref_free": True,
        })
        out = asyncio.run(tts._gpt_sovits_inproc({"content": "测试", "language": "zh"}))
        assert out is not None
        assert len(calls) == 1
        assert calls[0]["aux_ref_audio_paths"] == ["D:/b.wav"]
        assert calls[0]["prompt_text"] == ""

    def test_param_whitelist_stripped(self, monkeypatch, tmp_path):
        """参数精简：top_k/top_p/temperature 等旧参数不再透传（引擎用系统默认）。"""
        import utils.gpt_sovits_inproc as gsv
        calls = []
        monkeypatch.setattr(gsv, "get_engine", lambda: _fake_engine(calls, fail_first=False))
        tmpdir = str(tmp_path)
        tts = _make_tts(tmpdir, {
            "ref_audio_path": "D:/a.wav",
            "ref_free": True,
            "top_k": 5, "top_p": 1.0, "temperature": 1.0,
            "batch_size": 1, "seed": -1,
        })
        out = asyncio.run(tts._gpt_sovits_inproc({"content": "测试", "language": "zh"}))
        assert out is not None
        for legacy in ("top_k", "top_p", "temperature", "batch_size", "seed"):
            assert legacy not in calls[0], f"旧参数 {legacy} 不应透传"

    def test_runtime_speed_overrides(self, monkeypatch, tmp_path):
        """P26：运行时 speed 参数优先写入 speed_factor。"""
        import utils.gpt_sovits_inproc as gsv
        calls = []
        monkeypatch.setattr(gsv, "get_engine", lambda: _fake_engine(calls, fail_first=False))
        tmpdir = str(tmp_path)
        tts = _make_tts(tmpdir, {"ref_audio_path": "D:/a.wav", "speed_factor": 1.0})
        out = asyncio.run(tts._gpt_sovits_inproc({"content": "测试", "language": "zh", "speed": 1.25}))
        assert out is not None
        assert calls[0]["speed_factor"] == pytest.approx(1.25)

    def test_no_ref_audio_fails_fast(self, monkeypatch, tmp_path):
        """参考音频完全缺失 → 直接失败（不空跑引擎）。"""
        import utils.gpt_sovits_inproc as gsv
        calls = []
        monkeypatch.setattr(gsv, "get_engine", lambda: _fake_engine(calls))
        tmpdir = str(tmp_path)
        tts = _make_tts(tmpdir, {})
        out = asyncio.run(tts._gpt_sovits_inproc({"content": "测试", "language": "zh"}))
        assert out is None
        assert calls == []


# ---------- edge-tts 高级参数 JSON 透传 ----------

class TestEdgeExtraArgs:
    def test_empty_is_default(self):
        assert parse_edge_extra_args("") == ({}, None)
        assert parse_edge_extra_args(None) == ({}, None)
        assert parse_edge_extra_args("   ") == ({}, None)

    def test_valid_json(self):
        args, err = parse_edge_extra_args('{"pitch": "+0Hz", "volume": "+0%"}')
        assert err is None
        assert args == {"pitch": "+0Hz", "volume": "+0%"}

    def test_invalid_json(self):
        args, err = parse_edge_extra_args("{bad json")
        assert args == {} and err

    def test_non_object_json(self):
        args, err = parse_edge_extra_args('[1, 2]')
        assert args == {} and err


# ---------- P27：my_translate 分发 ----------

class TestMyTranslateDispatch:
    def _make_translator(self, tmpdir, trans_type):
        from utils.my_translate import My_Translate
        from utils.config import Config
        cfg_path = os.path.join(tmpdir, "config.json")
        with open(cfg_path, "w", encoding="utf-8") as f:
            json.dump({"translate": {"enable": True, "type": trans_type,
                                     "baidu": {"appid": "", "appkey": ""},
                                     "google": {"proxy": "", "src_lang": "auto", "tgt_lang": "zh"}}}, f)
        Config.reload(cfg_path)
        return My_Translate(cfg_path)

    def test_argos_branch_calls_module(self, monkeypatch, tmp_path):
        captured = {}

        def fake_translate(text, from_lang="auto", to_lang="zh"):
            captured.update(text=text, frm=from_lang, to=to_lang)
            return "译文"

        import utils.my_translate as mt_mod
        monkeypatch.setattr("utils.translate_argos.translate_argos", fake_translate)
        tr = self._make_translator(str(tmp_path), "argos")
        assert tr.trans("hello") == "译文"
        assert captured["to"] == "zh"

    def test_custom_llm_branch_returns_none(self, tmp_path):
        """接线点未接线：返回 None（调用方按翻译失败降级），不抛不占位。"""
        tr = self._make_translator(str(tmp_path), "custom_llm")
        assert tr.trans("hello") is None

    def test_argos_branch_degrades_to_none(self, monkeypatch, tmp_path):
        """T4：argos 不可用时 argos_trans 返回 None。"""
        monkeypatch.setattr(translate_argos, "_load_translate_api", lambda: None)
        tr = self._make_translator(str(tmp_path), "argos")
        assert tr.trans("hello") is None
