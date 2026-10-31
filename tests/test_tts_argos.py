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
    normalize_edge_rate, normalize_prompt_texts, normalize_ref_paths,
    speed_to_edge_rate)
from utils import translate_argos
from utils.my_log import logger

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(rel: str) -> str:
    with open(os.path.join(_REPO, rel), encoding="utf-8") as f:
        return f.read()


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

    def test_speed_to_rate_integer_emission(self):
        """2026-10-24 实测修复：edge-tts 7.3.1 校验 ^[+-]\\d+%$，小数百分比被拒
        （实证 +7.88% → Invalid rate，+8% OK）——随机语速 1.0788 必须产出整数形式"""
        assert speed_to_edge_rate(1.0788) == "+8%"
        assert speed_to_edge_rate(0.938) == "-6%"
        for speed in (0.25, 0.5, 0.8, 1.0, 1.0788, 1.3, 2.0, 4.0):
            rate = speed_to_edge_rate(speed)
            assert rate.endswith("%") and "." not in rate, rate

    def test_normalize_edge_rate_signed_normalization(self):
        """2026-10-24 实测修复：带符号值解析+整数化（原样透传会让手填小数直抵
        edge-tts 校验崩掉）；无法解析（含无 % 号裸值）返回 None 落默认"""
        assert normalize_edge_rate("110%") == "+10%"
        assert normalize_edge_rate("+20%") == "+20%"
        assert normalize_edge_rate("+7.88%") == "+8%"
        assert normalize_edge_rate("-10%") == "-10%"
        assert normalize_edge_rate("+20") is None
        assert normalize_edge_rate("abc") is None


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

    # ---------- 2026-10-29：参考文本与多音频逐行对应 ----------

    def test_normalize_prompt_texts_multiline(self):
        """多行字符串 → 逐行列表；转写含逗号/分号不可被切散。"""
        assert normalize_prompt_texts("你好，世界。\n再见；朋友。") == ["你好，世界。", "再见；朋友。"]

    def test_normalize_prompt_texts_keeps_middle_blank(self):
        """中间空行保留（第 i 行 ↔ 第 i 行音频对齐语义），仅裁尾部空行。"""
        assert normalize_prompt_texts("甲文本。\n\n丙文本。\n\n") == ["甲文本。", "", "丙文本。"]
        assert normalize_prompt_texts(None) == []
        # 列表项自带尾换行 = 显式空行（对齐语义保留）；无尾换行则不产生空行
        assert normalize_prompt_texts([" x。", "y。"]) == ["x。", "y。"]
        assert normalize_prompt_texts([" x。\n", "y。"]) == ["x。", "", "y。"]

    def test_build_inproc_refs_prompt_texts_match_main(self):
        """关闭 ref_free：主参考=第一行 → 取 prompt_texts[0] 参与合成。"""
        frag = build_inproc_refs(["a.wav", "b.wav"], ref_free=False,
                                 prompt_text="旧文本", prompt_texts=["甲文本", "乙文本"])
        assert frag == {"ref_audio_path": "a.wav",
                        "aux_ref_audio_paths": ["b.wav"],
                        "prompt_text": "甲文本"}

    def test_build_inproc_refs_prompt_texts_fallback_legacy(self):
        """prompt_texts 首行为空 → 回退旧版单值 prompt_text（存量配置兼容）。"""
        frag = build_inproc_refs(["a.wav"], ref_free=False,
                                 prompt_text="旧文本", prompt_texts=["", ""])
        assert frag == {"ref_audio_path": "a.wav", "prompt_text": "旧文本"}
        frag2 = build_inproc_refs(["a.wav"], ref_free=False, prompt_text="旧文本")
        assert frag2 == {"ref_audio_path": "a.wav", "prompt_text": "旧文本"}


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

    def test_degrade_prefers_prompt_texts_main(self, monkeypatch, tmp_path):
        """2026-10-29：降级恢复参考文本优先取 prompt_texts 首行（主参考转写）。"""
        import utils.gpt_sovits_inproc as gsv
        calls = []
        monkeypatch.setattr(gsv, "get_engine", lambda: _fake_engine(calls))
        tmpdir = str(tmp_path)
        tts = _make_tts(tmpdir, {
            "ref_audio_paths": ["D:/a.wav", "D:/b.wav"],
            "ref_free": True,
            "prompt_texts": ["新主参考转写"],
            "prompt_text": "旧参考文本",
        })
        out = asyncio.run(tts._gpt_sovits_inproc({"content": "测试", "language": "zh"}))
        assert out is not None
        assert calls[1]["prompt_text"] == "新主参考转写"

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


# ---------- A2：pitch 语速联动（2026-10-30 任务书对照补测） ----------

class TestEdgePitchCoupling:
    """edge-tts 变语速时同步调 pitch 保持音高一致（任务书 L80-84 语义）。

    公式：pitch_hz = (factor - 1) * 50，可调范围 ±50Hz，整数化输出——
    +1.2 倍语速 → "+10Hz"，0.9 倍 → "-5Hz"，1.0 → "+0Hz"。"""

    def test_task_book_examples(self):
        from utils.audio_handle.tts_params import speed_to_edge_pitch
        assert speed_to_edge_pitch(1.2) == "+10Hz"   # 提高 20%（1.2 倍）
        assert speed_to_edge_pitch(0.9) == "-5Hz"    # 降低 10%（0.9 倍）
        assert speed_to_edge_pitch(1.0) == "+0Hz"    # 不变速

    def test_clamp_50hz(self):
        """可调范围 ±50Hz——上界截断（factor 2.0 → +50Hz，3.0 → +50Hz）；
        合法域下限 0.25 → -37.5Hz（未触 -50Hz 边界，整数化 -38Hz）。"""
        from utils.audio_handle.tts_params import speed_to_edge_pitch
        assert speed_to_edge_pitch(2.0) == "+50Hz"
        assert speed_to_edge_pitch(3.0) == "+50Hz"
        assert speed_to_edge_pitch(0.25) == "-38Hz"

    def test_integer_emission(self):
        """Hz 整数化（同 rate 实测修复——小数被 edge-tts 7.3.1 拒收）。"""
        from utils.audio_handle.tts_params import speed_to_edge_pitch
        assert speed_to_edge_pitch(1.0788) == "+4Hz"
        assert speed_to_edge_pitch(0.938) == "-3Hz"


# ---------- edge-tts 高级参数 JSON 透传退役（2026-10-30） ----------

class TestEdgeExtraArgs:
    """高级参数 JSON 透传（extra_args_json）退役反向守卫。

    pitch 恒由随机语速联动接管（speed_to_edge_pitch，A2），用户显式覆盖通道删除：
    - tts_params 无 parse_edge_extra_args；
    - my_tts.edge_tts_api 无 extra_args 解析/透传（**synth_kwargs 纯净）；
    - 前端无 extra_args_json 配置项；config 两文件无残留键。"""

    def test_parser_retired(self):
        import utils.audio_handle.tts_params as tp
        assert not hasattr(tp, "parse_edge_extra_args")

    def test_api_no_extra_args_passthrough(self):
        src = _read("utils/audio_handle/my_tts.py")
        seg = src[src.find("async def edge_tts_api"):src.find("async def gpt_sovits_api")]
        assert "**extra_args" not in seg, "Communicate 不再透传 extra_args"
        assert "parse_edge_extra_args" not in seg
        assert "**synth_kwargs)" in seg, "Communicate 仅接收 voice/rate/pitch"

    def test_no_config_key(self):
        """example 模板硬断言（不受运行时回写影响）；config.json 主系统运行中
        可能被内存旧态全量回写复活退役键——无读取点无害，不作硬断言。"""
        data = json.loads(_read("config.example.json"))
        assert "extra_args_json" not in data.get("edge_tts", {})
        try:
            data = json.loads(_read("config.json"))
            if "extra_args_json" in data.get("edge_tts", {}):
                logger.warning(
                    "config.json edge_tts.extra_args_json 运行态残留（主系统全量回写）"
                    "——键已无读取点，重启前端保存一次即清除")
        except FileNotFoundError:
            pass

    def test_frontend_field_retired(self):
        src = _read("frontend/ui/tabs/tts.py")
        assert 'set_config_callback("edge_tts", "extra_args_json"' not in src
        assert 'get_nested_value(config, "edge_tts", "extra_args_json")' not in src


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
