# -*- coding: UTF-8 -*-
"""
聊天功能修复守卫（2026-10-25 实测批次）

实测故障的回归锁：
1. 语音识别必败：funasr 1.4.12 AutoModel.generate 对 cache 参数做 len() 检查，
   传 cache=False 抛 "object of type 'bool' has no len()" → _transcribe_funasr
   不得传 cache（参考实现 funasr_asr.py 直调 generate(input=...)）
2. 聊天框/直接复读恒失败：webui 重构自 webui-bak 时 AutoliveApp 丢失
   send_chat_message → layout.py hasattr 判定 False → chat_send_callback=None
   → talk 页恒报"发送失败——请确认系统已启动"。方法必须存在且 payload 契约
   （type/data{username,content,chat_source=chatbox}）与主进程版同构。
3. 数字人推送全败：edge_tts 产物是 mp3（无 RIFF 头），wave.open 直读抛
   "file does not start with RIFF id" → push_full 前必须 RIFF 归一（pydub
   转临时 wav，用完即删）
4. ASR 冗余双模型：旧链"whisper 先转写→检测 zh→转发 FunASR"延时翻倍 →
   按「回复语言」(need_lang) 一次定模型：zh/yue→达摩 FunASR，其余→whisper
5. 聊天记录开关退役：talk.show_chat_log 键从 example 移除+退役键面登记
   （2026-10-25：聊天记录固定记录弹幕/语音/聊天框互动，无需开关）
"""

import asyncio
import inspect
import json
import os
import sys
import types
from unittest.mock import ANY, AsyncMock, MagicMock, patch

from frontend.main import AutoliveApp


class TestAsrFunasrNoCache:
    """ASR 修复守卫：_transcribe_funasr 不传 cache。"""

    def test_no_cache_arg(self):
        from utils import asr_engine
        src = inspect.getsource(asr_engine)
        assert "generate(input=wav_path, cache" not in src, \
            "funasr generate 传 cache=False 会抛 'object of type bool has no len()'（1.4.12 实测）"
        assert "model.generate(input=wav_path)" in src

    def test_funasr_result_parsing(self):
        """generate 返回结构解析：[{text}]→text；空/异常结构→空串。"""
        from utils import asr_engine
        model = MagicMock()
        model.generate.return_value = [{"text": " 你好世界 "}]
        with patch.object(asr_engine, "_get_funasr", return_value=model):
            assert asr_engine._transcribe_funasr("fake.wav", "zh") == "你好世界"
        model.generate.return_value = []
        with patch.object(asr_engine, "_get_funasr", return_value=model):
            assert asr_engine._transcribe_funasr("fake.wav", "zh") == ""


class _FakeUResp:
    def __init__(self, payload):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        import json
        return json.dumps(self._payload).encode("utf-8")


class _FakeCfg:
    def __init__(self, cfg):
        self._config = cfg


def _bare_app():
    """跳过 __init__（重活）的裸实例，仅注入 config。"""
    app = AutoliveApp.__new__(AutoliveApp)
    app.config = _FakeCfg({
        "api_ip": "127.0.0.1", "api_port": 8000,
        "talk": {"username": "主人"},
    })
    return app


class TestWebuiSendChatMessage:
    """webui 聊天发送方法守卫：存在 + payload 契约 + 结果判定。"""

    def test_method_exists_for_layout_hasattr(self):
        """layout.py 1686 行 hasattr(self.app, 'send_chat_message') 必须为真——
        False 会导致聊天框/直接复读按钮恒报"系统未启动"。"""
        assert hasattr(AutoliveApp, "send_chat_message")

    def test_payload_contract_and_success(self):
        app = _bare_app()
        with patch("urllib.request.urlopen",
                   return_value=_FakeUResp({"code": 200, "message": "成功"})) as m_open, \
             patch("urllib.request.Request", side_effect=lambda url, **kw: kw) as m_req:
            assert app.send_chat_message("comment", "你好") is True
        kwargs = m_req.call_args[1] if m_req.call_args else {}
        # Request(url, data=..., headers=...) —— url 为第一个位置参数
        args = m_req.call_args[0]
        assert args[0] == "http://127.0.0.1:8000/send"
        import json
        payload = json.loads(kwargs["data"].decode("utf-8"))
        assert payload["type"] == "comment"
        assert payload["data"]["username"] == "主人"
        assert payload["data"]["content"] == "你好"
        assert payload["data"]["chat_source"] == "chatbox"

    def test_non_200_is_failure(self):
        app = _bare_app()
        with patch("urllib.request.urlopen",
                   return_value=_FakeUResp({"code": -1, "message": "发送数据失败！"})):
            assert app.send_chat_message("comment", "你好") is False

    def test_connection_error_is_failure(self):
        app = _bare_app()
        with patch("urllib.request.urlopen", side_effect=ConnectionError("refused")):
            assert app.send_chat_message("reread", "测试") is False


class _RouteCfg:
    """变长键 get 的极简配置桩（Config.get(*keys) 同构，逐层取值）。"""

    def __init__(self, cfg):
        self._cfg = cfg

    def get(self, *keys):
        node = self._cfg
        for k in keys:
            if not isinstance(node, dict) or k not in node:
                return None
            node = node[k]
        return node


class TestAsrNeedLangRouting:
    """ASR 语言路由重构守卫（用户指令 2026-10-25）：按「回复语言」(need_lang)
    一次定模型——中文/粤语→达摩 FunASR，其它→fast-whisper；录音后不再先
    whisper 转写再检测语言（冗余双模型，延时翻倍）。"""

    def _wav(self, tmp_path):
        p = tmp_path / "rec.wav"
        p.write_bytes(b"RIFF....")
        return str(p)

    def test_zh_yue_route_to_funasr(self, tmp_path):
        from utils import asr_engine
        wav = self._wav(tmp_path)
        for lang in ("zh", "yue"):
            with patch.object(asr_engine, "_transcribe_funasr", return_value="文本") as m_fun, \
                 patch.object(asr_engine, "_transcribe_faster_whisper") as m_fw:
                assert asr_engine.transcribe(wav, _RouteCfg({"need_lang": lang})) == "文本"
            m_fun.assert_called_once_with(wav, lang)
            m_fw.assert_not_called()

    def test_explicit_langs_route_to_whisper(self, tmp_path):
        from utils import asr_engine
        wav = self._wav(tmp_path)
        for need, whisper_lang in (("en", "en"), ("jp", "ja"), ("ko", "ko")):
            with patch.object(asr_engine, "_transcribe_funasr") as m_fun, \
                 patch.object(asr_engine, "_transcribe_faster_whisper", return_value="hi") as m_fw:
                assert asr_engine.transcribe(wav, _RouteCfg({"need_lang": need})) == "hi"
            m_fw.assert_called_once_with(wav, ANY, whisper_lang)
            m_fun.assert_not_called()

    def test_none_falls_back_to_whisper_language_config(self, tmp_path):
        """need_lang=none（所有）→ whisper 自动检测；talk.faster_whisper.language
        仍作为 whisper 页语言提示（自动识别→None）。"""
        from utils import asr_engine
        wav = self._wav(tmp_path)
        cfg = _RouteCfg({"need_lang": "none",
                         "talk": {"faster_whisper": {"language": "自动识别"}}})
        with patch.object(asr_engine, "_transcribe_faster_whisper", return_value="x") as m_fw:
            asr_engine.transcribe(wav, cfg)
        assert m_fw.call_args[0][2] is None
        cfg2 = _RouteCfg({"need_lang": "none",
                          "talk": {"faster_whisper": {"language": "en"}}})
        with patch.object(asr_engine, "_transcribe_faster_whisper", return_value="x") as m_fw:
            asr_engine.transcribe(wav, cfg2)
        assert m_fw.call_args[0][2] == "en"

    def test_cloud_type_still_ignored(self, tmp_path):
        """google/baidu 云端方案维持"明示忽略"语义（本地离线链未接入）。"""
        from utils import asr_engine
        wav = self._wav(tmp_path)
        with patch.object(asr_engine, "_transcribe_funasr") as m_fun, \
             patch.object(asr_engine, "_transcribe_faster_whisper") as m_fw:
            assert asr_engine.transcribe(wav, _RouteCfg({"talk": {"type": "baidu"}})) == ""
        m_fun.assert_not_called()
        m_fw.assert_not_called()

    def test_whisper_never_forwards_zh(self):
        """旧冗余链退役：whisper 检测为 zh 也不得转发 FunASR（路由只在 need_lang）。"""
        from utils import asr_engine
        model = MagicMock()
        seg = MagicMock()
        seg.text = " hello "
        info = MagicMock()
        info.language = "zh"
        model.transcribe.return_value = (iter([seg]), info)
        with patch.object(asr_engine, "_get_faster_whisper", return_value=model), \
             patch.object(asr_engine, "_transcribe_funasr") as m_fun:
            assert asr_engine._transcribe_faster_whisper("fake.wav", _RouteCfg({}), None) == "hello"
        m_fun.assert_not_called()


class TestEdtalkRiffNormalize:
    """EDTalk 推送 RIFF 归一守卫：mp3（edge_tts 产物）→ 临时 wav 再推，用完即删。"""

    def test_riff_passthrough(self, tmp_path):
        from utils.edtalk_realtime import edtalk_client
        wav = tmp_path / "a.wav"
        wav.write_bytes(b"RIFF" + b"\x00" * 8)
        path, created = edtalk_client._ensure_wav_riff(str(wav))
        assert path == str(wav) and created is False

    def test_non_riff_transcodes_to_tmp_wav(self, tmp_path):
        from utils.edtalk_realtime import edtalk_client
        mp3 = tmp_path / "tts.mp3"
        mp3.write_bytes(b"ID3 fake-mp3")
        fake_seg = MagicMock()
        fake_mod = types.SimpleNamespace(
            AudioSegment=MagicMock(from_file=MagicMock(return_value=fake_seg)))
        with patch.dict(sys.modules, {"pydub": fake_mod}):
            path, created = edtalk_client._ensure_wav_riff(str(mp3))
        assert created is True and path.endswith("_edtalk.wav")
        fake_seg.export.assert_called_once_with(path, format="wav")

    def test_push_full_wiring_and_cleanup(self):
        """push_full 必须先 RIFF 归一再加载，临时 wav 在 finally 清理。"""
        from utils.edtalk_realtime import edtalk_client
        src = inspect.getsource(edtalk_client.EDTalkClient.push_full)
        assert "_ensure_wav_riff(pcm_path)" in src
        assert "os.remove(wav_path)" in src

    def test_push_full_mp3_end_to_end(self, tmp_path):
        """端到端：mp3 魔数文件 → pydub 转临时 wav → 加载推送成功 → 临时文件已删。"""
        import struct
        import wave as wave_mod
        from utils.edtalk_realtime import edtalk_client
        mp3 = tmp_path / "tts.mp3"
        mp3.write_bytes(b"ID3 fake")
        tmp_wav = tmp_path / "tts_edtalk.wav"

        def fake_export(target, format=None):
            assert format == "wav"
            with wave_mod.open(str(target), "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(16000)
                wf.writeframes(struct.pack("<8h", *range(-8000, 8000, 2000)))

        fake_seg = MagicMock()
        fake_seg.export.side_effect = fake_export
        fake_mod = types.SimpleNamespace(
            AudioSegment=MagicMock(from_file=MagicMock(return_value=fake_seg)))
        fake_resp = MagicMock()
        fake_resp.status = 200
        fake_resp.json = AsyncMock(return_value={"processing": False})
        fake_session = MagicMock()
        fake_session.get.side_effect = RuntimeError("no status")  # /status 不可用→降级
        post_cm = MagicMock()
        post_cm.__aenter__ = AsyncMock(return_value=fake_resp)
        post_cm.__aexit__ = AsyncMock(return_value=False)
        fake_session.post.return_value = post_cm
        session_cm = MagicMock()
        session_cm.__aenter__ = AsyncMock(return_value=fake_session)
        session_cm.__aexit__ = AsyncMock(return_value=False)
        client = edtalk_client.EDTalkClient({"api_ip_port": "http://127.0.0.1:1"})
        with patch.dict(sys.modules, {"pydub": fake_mod}), \
             patch.object(edtalk_client, "aiohttp") as m_aio, \
             patch.object(edtalk_client, "load_wav_as_16k_pcm",
                          return_value=(b"\x00\x00" * 1600, 16000)):
            m_aio.ClientSession.return_value = session_cm
            ok = asyncio.run(client.push_full(str(mp3), "测试"))
        assert ok is True
        assert client.failure_count == 0
        assert not tmp_wav.exists()  # 临时 wav 用完即删


class TestShowChatLogRetired:
    """聊天记录开关退役（2026-10-25 用户指令）：聊天记录固定记录弹幕与聊天
    互动——talk.show_chat_log 键从 example 配置移除并登记退役键面。"""

    def test_example_key_removed(self):
        cfg_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "config.example.json")
        with open(cfg_path, "r", encoding="utf-8") as f:
            assert "show_chat_log" not in json.load(f)["talk"]

    def test_retired_key_registered(self):
        from frontend.main import _RETIRED_CONFIG_KEYS
        assert (("talk", "show_chat_log")) in [k for k, _ in _RETIRED_CONFIG_KEYS]


class TestChatLogAnswerPassthrough:
    """问答成对守卫：My_handle 委托签名必须含 content_type——缺失致 talk/comment
    链 answer 侧 TypeError 被外层 try 吞掉，聊天记录只显示问不显示答
    （2026-10-25 实测根因）。"""

    def test_delegate_signature_has_content_type(self):
        from utils.my_handle import My_handle
        sig = inspect.signature(My_handle.webui_show_chat_log_callback)
        assert "content_type" in sig.parameters
        assert sig.parameters["content_type"].default == "answer"

    def test_delegate_forwards_content_type(self):
        import utils.my_handle as mh_mod
        src = inspect.getsource(mh_mod.My_handle.webui_show_chat_log_callback)
        assert "content_type" in src
