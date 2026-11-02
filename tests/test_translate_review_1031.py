# -*- coding: UTF-8 -*-
"""翻译功能审查批次守卫测试（2026-10-31，批次号 translate_review_1031）

审查目标（任务书）：
- 本地翻译（argos）：分发/降级单测既有（test_tts_argos.py），本批守卫 my_translate 层
- 百度/谷歌外接：baidu_trans 移除 Vtuber 特化硬编码替换表（译文不再被改写）
- 翻译功能在系统流程中正确应用：
  ①「弹幕」翻译类型此前全库零消费——本批 comment/talk 双链接线 trans_comment
   （位于 lang_filter_pass 之前：译文语言=回复语言，G3 语言过滤自然放行）
  ②热重载 reload_config 刷新全部缓存属性（此前 config_data/baidu_config/
   google_config 三缓存保持旧值，翻译类型/百度密钥热重载失效）
  ③LLM 翻译回调动态读当前通道 to_lang（此前固化 baidu.to_lang 且热重载不更新）
"""
import inspect
import json
import os
import sys
import types
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


def _read(rel):
    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(here, "..", rel), "r", encoding="utf-8") as f:
        return f.read()


def _write_config(path, payload):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)


def _make_translator(cfg_path):
    from utils.config import Config
    from utils.my_translate import My_Translate
    Config.reload(str(cfg_path))
    return My_Translate(str(cfg_path))


_BASE_CFG = {
    "need_lang": "zh",
    "translate": {
        "enable": True,
        "type": "argos",
        "baidu": {"appid": "a1", "appkey": "k1", "from_lang": "auto", "to_lang": "zh"},
        "google": {"proxy": "", "src_lang": "auto", "tgt_lang": "zh-CN"},
        "argos": {"from_lang": "auto", "to_lang": "zh"},
    },
}


# ---------- ② 热重载刷新全部缓存 ----------

class TestReloadConfigRefreshesCaches:
    def test_reload_updates_all_cached_attrs(self, tmp_path):
        cfg_path = str(tmp_path / "config.json")
        _write_config(cfg_path, _BASE_CFG)
        tr = _make_translator(cfg_path)
        assert tr.config_data["type"] == "argos"
        assert tr.baidu_config["appkey"] == "k1"

        # 用户改配置落盘（类型 argos→baidu、密钥轮换），热重载
        updated = json.loads(json.dumps(_BASE_CFG, ensure_ascii=False))
        updated["translate"]["type"] = "baidu"
        updated["translate"]["baidu"]["appkey"] = "k2"
        _write_config(cfg_path, updated)
        tr.reload_config(cfg_path)

        # 此前缺陷：三缓存保持 __init__ 旧值——trans() 仍按 argos 分发、百度密钥失效
        assert tr.config_data["type"] == "baidu"
        assert tr.baidu_config["appkey"] == "k2"

    def test_reload_distributes_by_new_type(self, tmp_path, monkeypatch):
        cfg_path = str(tmp_path / "config.json")
        _write_config(cfg_path, _BASE_CFG)
        tr = _make_translator(cfg_path)

        updated = json.loads(json.dumps(_BASE_CFG, ensure_ascii=False))
        updated["translate"]["type"] = "baidu"
        _write_config(cfg_path, updated)
        tr.reload_config(cfg_path)

        captured = {}

        class _Resp:
            @staticmethod
            def json():
                return {"trans_result": [{"dst": "HELLO"}]}

        def fake_post(url, params=None, headers=None, **kw):
            captured.update(params=params)
            return _Resp()

        import utils.my_translate as mt
        monkeypatch.setattr(mt.requests, "post", fake_post)
        assert tr.trans("hello") == "HELLO"
        assert captured["params"]["appid"] == "a1"  # 新缓存生效（appid 随请求发送，appkey 仅参与签名不上传）
        assert captured["params"]["sign"]  # appkey 参与的签名已生成


# ---------- 百度翻译：硬编码替换表移除 ----------

class TestBaiduTransClean:
    def test_no_vtuber_hardcoded_replacements(self):
        """Vtuber 特化替换表（パパパパ→パンパカパーン、桃ちゃん→モモイ 等）
        不属于通用翻译服务语义——译文不得被静默改写。"""
        src = inspect.getsource(
            __import__("utils.my_translate", fromlist=["My_Translate"]).My_Translate.baidu_trans)
        for marker in ("パパパパ", "パンパカパーン", "桃ちゃん", "モモイ", "アールピージー", "優香"):
            assert marker not in src

    def test_translation_returned_verbatim(self, tmp_path, monkeypatch):
        cfg_path = str(tmp_path / "config.json")
        cfg = json.loads(json.dumps(_BASE_CFG, ensure_ascii=False))
        cfg["translate"]["type"] = "baidu"
        _write_config(cfg_path, cfg)
        tr = _make_translator(cfg_path)

        class _Resp:
            @staticmethod
            def json():
                return {"trans_result": [{"dst": "Hello RPG"}]}

        monkeypatch.setattr("utils.my_translate.requests.post",
                            lambda *a, **kw: _Resp())
        assert tr.baidu_trans("Hello RPG") == "Hello RPG"  # 原样返回，无 RPG 改写

    def test_api_fail_returns_none(self, tmp_path, monkeypatch):
        cfg_path = str(tmp_path / "config.json")
        cfg = json.loads(json.dumps(_BASE_CFG, ensure_ascii=False))
        cfg["translate"]["type"] = "baidu"
        _write_config(cfg_path, cfg)
        tr = _make_translator(cfg_path)

        def _boom(*a, **kw):
            raise RuntimeError("network down")

        monkeypatch.setattr("utils.my_translate.requests.post", _boom)
        assert tr.baidu_trans("hello") is None  # 失败 None，调用方回退原文


# ---------- raw_target_lang / _target_lang（LLM 翻译目标语言单点） ----------

class TestRawTargetLang:
    def test_channels_read_own_to_lang(self, tmp_path):
        cfg_path = str(tmp_path / "config.json")
        _write_config(cfg_path, _BASE_CFG)
        tr = _make_translator(cfg_path)
        assert tr.raw_target_lang("baidu") == "zh"
        assert tr.raw_target_lang("google") == "zh-CN"
        assert tr.raw_target_lang("argos") == "zh"
        assert tr.raw_target_lang("custom_llm") == "中文"  # need_lang=zh 映射
        assert tr.raw_target_lang(None) == "zh"

    def test_custom_llm_follows_need_lang(self, tmp_path):
        cfg_path = str(tmp_path / "config.json")
        cfg = json.loads(json.dumps(_BASE_CFG, ensure_ascii=False))
        cfg["need_lang"] = "jp"
        _write_config(cfg_path, cfg)
        tr = _make_translator(cfg_path)
        assert tr.raw_target_lang("custom_llm") == "日文"

    def test_empty_fallbacks_zh(self, tmp_path):
        cfg_path = str(tmp_path / "config.json")
        cfg = json.loads(json.dumps(_BASE_CFG, ensure_ascii=False))
        cfg["need_lang"] = "none"
        cfg["translate"]["argos"]["to_lang"] = ""
        _write_config(cfg_path, cfg)
        tr = _make_translator(cfg_path)
        assert tr.raw_target_lang("argos") == "zh"
        assert tr.raw_target_lang("custom_llm") == "中文"  # none 缺省中文

    def test_target_lang_custom_llm_mappable(self, tmp_path):
        cfg_path = str(tmp_path / "config.json")
        _write_config(cfg_path, _BASE_CFG)
        tr = _make_translator(cfg_path)
        assert tr._target_lang("custom_llm") == "zh"  # 中文→zh（跳过判定可用）
        assert tr._target_lang("argos") == "zh"
        assert tr._target_lang("google") == "zh"


# ---------- trans_comment（弹幕翻译入口） ----------

class _StubTranslate:
    """trans_comment 桩：别名表/_target_lang/trans_comment 复用真实实现。"""

    def __init__(self, need_lang="zh", detected="zh", trans_ret="译文",
                 argos_to_lang="zh"):
        self.config_data = {"type": "argos"}
        self.config = SimpleNamespace(
            get=lambda *keys: {
                ("need_lang",): need_lang,
                ("translate", "argos", "to_lang"): argos_to_lang,
            }.get(keys))
        self.common = SimpleNamespace(lang_check=lambda t: detected)
        self.trans = MagicMock(return_value=trans_ret)
        from utils.my_translate import My_Translate
        self._LANG_CODE_ALIASES = My_Translate._LANG_CODE_ALIASES
        self._target_lang = types.MethodType(My_Translate._target_lang, self)
        self.trans_comment = types.MethodType(My_Translate.trans_comment, self)


class TestTransComment:
    def test_same_lang_skips(self):
        st = _StubTranslate(need_lang="zh", detected="zh")
        assert st.trans_comment("你好世界") == "你好世界"
        st.trans.assert_not_called()

    def test_diff_lang_translates(self):
        st = _StubTranslate(need_lang="zh", detected="en", trans_ret="你好")
        assert st.trans_comment("hello") == "你好"
        st.trans.assert_called_once_with("hello", "argos")

    def test_need_lang_jp_maps_ja(self):
        st = _StubTranslate(need_lang="jp", detected="zh", trans_ret="こんにちは")
        assert st.trans_comment("你好") == "こんにちは"  # zh 弹幕→目标 ja

    def test_need_lang_none_falls_back_channel(self):
        """未设定回复语言（none）→ 回退当前翻译通道 to_lang。"""
        st = _StubTranslate(need_lang="none", detected="en",
                            trans_ret="你好", argos_to_lang="zh")
        assert st.trans_comment("hello") == "你好"

    def test_trans_fail_returns_original(self):
        st = _StubTranslate(need_lang="zh", detected="en", trans_ret=None)
        assert st.trans_comment("hello") == "hello"  # 不阻塞主链

    def test_empty_text_passthrough(self):
        st = _StubTranslate()
        assert st.trans_comment("") == ""


# ---------- ①③ 双链/回调接线守卫 ----------

class TestChainWiring:
    def test_comment_chain_wires_trans_comment_before_lang_filter(self):
        src = _read("utils/handlers/comment_handler.py")
        assert "trans_comment" in src
        assert '"弹幕+回复"' in src
        gate = src.index("trans_type\") == \"弹幕\"")
        call = src.index("my_translate.trans_comment")
        filt = src.index("lang_filter_pass(content)")
        assert gate < call < filt  # 翻译先于语言过滤

    def test_talk_chain_wires_trans_comment_before_lang_filter(self):
        src = _read("utils/handlers/chat_handler.py")
        assert "trans_comment" in src
        gate = src.index("trans_type\") == \"弹幕\"")
        call = src.index("my_translate.trans_comment")
        filt = src.index("lang_filter_pass(content)")
        assert gate < call < filt

    def test_gates_on_translate_enable(self):
        for rel in ("utils/handlers/comment_handler.py", "utils/handlers/chat_handler.py"):
            src = _read(rel)
            assert 'config.get("translate", "enable")' in src

    def test_llm_translator_reads_channel_dynamically(self):
        """LLM 翻译回调动态读当前通道 to_lang——此前固化 baidu.to_lang。"""
        src = _read("utils/my_handle.py")
        assert "raw_target_lang" in src
        assert 'My_handle.config.get("translate", "baidu", "to_lang")' not in src

    def test_reply_chain_unchanged(self):
        """回复链 trans_reply 接线保持既有（回复/弹幕+回复 两类型）。"""
        for rel in ("utils/handlers/comment_handler.py", "utils/handlers/chat_handler.py"):
            src = _read(rel)
            assert "trans_reply" in src
            assert 'trans_type") == "回复"' in src


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
