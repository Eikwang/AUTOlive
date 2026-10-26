# -*- coding: UTF-8 -*-
"""
ASR 流式识别守卫（2026-10-25 方案A：仅中文 FunASR online 流式）

语音对话链「说完→发大模型」延时优化回归锁：
1. 门控工厂 create_streaming_session：talk.asr_streaming 开关（缺省开启）、
   仅 need_lang=zh 启用（粤语 UniASR 与 en/jp/ko whisper 维持整段）、
   模型不可用回退 None（调用方走原整段链）
2. StreamingAsrSession：600ms 增量窗对齐（is_final=False）、final 冲刷
   （is_final=True）、多段文本拼接、标点回补与降级、闭合后 feed 忽略
3. 接线：采集循环 on_chunk 喂入、frames_to_wav rate 一致性修复
   （此前 16k 数据恒写 44.1k 头）、连续模式每轮新建会话+final、
   main 按键模式会话生命周期（start 建会话喂入 / stop final 取文本）
4. 配置与 UI：config.json/example asr_streaming 键、talk 页开关绑定
"""

import inspect
import json
import os
from unittest.mock import MagicMock, patch

from utils import asr_engine


class _Cfg:
    """变长键配置桩（Config.get 语义：缺键返回 None）。"""

    def __init__(self, data):
        self._data = data

    def get(self, *keys):
        node = self._data
        for k in keys:
            if not isinstance(node, dict) or k not in node:
                return None
            node = node[k]
        return node


def _zh_cfg(**talk_extra):
    talk = {"asr_streaming": True}
    talk.update(talk_extra)
    return _Cfg({"talk": talk, "need_lang": "zh"})


def _make_session(model=None):
    """绕过真实 funasr 的流式会话（模型注入 MagicMock）。"""
    model = model or MagicMock()
    with patch.object(asr_engine, "_get_funasr_streaming", return_value=model):
        return asr_engine.StreamingAsrSession(_zh_cfg()), model


class TestStreamingFactory:
    """门控工厂：开关/语言/回退三道闸。"""

    def test_none_config(self):
        assert asr_engine.create_streaming_session(None) is None

    def test_switch_off(self):
        cfg = _zh_cfg(asr_streaming=False)
        assert asr_engine.create_streaming_session(cfg) is None

    def test_non_zh_disabled(self):
        cfg = _Cfg({"talk": {"asr_streaming": True}, "need_lang": "en"})
        assert asr_engine.create_streaming_session(cfg) is None

    def test_zh_default_enabled(self):
        """缺键=开启（None is not False 语义），zh 创建成功。"""
        cfg = _Cfg({"need_lang": "zh"})
        with patch.object(asr_engine, "_get_funasr_streaming", return_value=MagicMock()):
            session = asr_engine.create_streaming_session(cfg)
        assert isinstance(session, asr_engine.StreamingAsrSession)

    def test_model_unavailable_fallback(self):
        """模型加载失败→None（调用方回退整段转写，不抛异常）。"""
        with patch.object(asr_engine, "_get_funasr_streaming",
                          side_effect=RuntimeError("模型缺失")):
            assert asr_engine.create_streaming_session(_zh_cfg()) is None


class TestStreamingSession:
    """会话行为：增量窗口/拼接/标点/闭合。"""

    def test_buffer_600ms_window(self):
        """不足 600ms 不触发推理；攒够恰好消费 9600 样本。"""
        session, model = _make_session()
        model.generate.return_value = []
        half = b"\x00\x00" * 4800          # 4800 样本 = 0.3s
        session.feed(half)
        assert model.generate.call_count == 0
        session.feed(half)                 # 累计 9600 → 恰好一窗
        assert model.generate.call_count == 1
        kwargs = model.generate.call_args.kwargs
        assert kwargs["is_final"] is False
        assert len(kwargs["input"]) == asr_engine._STREAM_FEED_SAMPLES
        assert isinstance(kwargs["cache"], dict)

    def test_final_flush_and_concat(self):
        """final 冲刷尾部（is_final=True）+ 多段文本拼接 + 标点回补。"""
        session, model = _make_session()

        def gen(input, cache, is_final, **kw):
            return [{"text": "尾字" if is_final else "你好"}]

        model.generate.side_effect = gen
        punc = MagicMock()
        punc.generate.return_value = [{"text": "你好，尾字。"}]
        with patch.object(asr_engine, "_get_funasr_punc", return_value=punc):
            session.feed(b"\x00\x00" * 9600)
            assert session.final() == "你好，尾字。"
        # final 冲刷确有一次 is_final=True
        assert any(c.kwargs["is_final"] is True
                   for c in model.generate.call_args_list)

    def test_punc_failure_fallback(self):
        """标点模型异常→降级返回无标点拼接文本。"""
        session, model = _make_session()

        def gen(input, cache, is_final, **kw):
            return [{"text": "" if is_final else "你好"}]

        model.generate.side_effect = gen
        with patch.object(asr_engine, "_get_funasr_punc",
                          side_effect=RuntimeError("标点模型缺失")):
            session.feed(b"\x00\x00" * 9600)
            assert session.final() == "你好"

    def test_feed_after_final_ignored(self):
        """final 后会话闭合——迟到 chunk 不再喂入（连续模式每轮新建会话）。"""
        session, model = _make_session()
        model.generate.return_value = []
        session.feed(b"\x00\x00" * 9600)
        session.final()
        calls = model.generate.call_count
        session.feed(b"\x00\x00" * 9600)
        assert model.generate.call_count == calls

    def test_empty_session_final(self):
        """整轮无语音：final 不炸、返回空串、不调标点。"""
        session, model = _make_session()
        model.generate.return_value = []
        with patch.object(asr_engine, "_get_funasr_punc") as m_punc:
            assert session.final() == ""
            m_punc.assert_not_called()


class TestRecordingWiring:
    """采集层接线（源级）：on_chunk 喂入 + 采样率一致性。"""

    def test_record_audio_on_chunk(self):
        from utils.audio_processor import AudioProcessor
        src = inspect.getsource(AudioProcessor.record_audio)
        assert "on_chunk" in src
        assert "on_chunk(data)" in src

    def test_record_audio_rate_from_config(self):
        """采样率对齐 talk.RATE（16k——FunASR 流式要求，免重采样）。"""
        from utils.audio_processor import AudioProcessor
        src = inspect.getsource(AudioProcessor.record_audio)
        assert 'int(self.config.get("talk", "RATE"))' in src

    def test_audio_listen_on_chunk(self):
        from utils.audio_processor import AudioProcessor
        src = inspect.getsource(AudioProcessor.audio_listen)
        assert "on_chunk(data)" in src

    def test_start_recording_thread_passthrough(self):
        from utils.audio_processor import AudioProcessor
        src = inspect.getsource(AudioProcessor.start_recording_thread)
        assert "self.record_audio(on_chunk=on_chunk)" in src

    def test_frames_to_wav_rate_param(self):
        """wav 头须用采集流实际采样率（修复 16k 数据配 44.1k 头的变速失真）。"""
        from utils.audio_processor import AudioProcessor
        sig = inspect.signature(AudioProcessor.frames_to_wav)
        assert "rate" in sig.parameters
        src = inspect.getsource(AudioProcessor.frames_to_wav)
        assert "wf.setframerate(int(rate))" in src


class TestConsumerWiring:
    """消费端接线（源级）：连续模式 + main 按键模式。"""

    def test_continuous_loop_session_per_round(self):
        """连续模式：每轮新建会话（final 后闭合不可复用）+ final 取文本。"""
        from utils.conversation_manager import ConversationManager
        src = inspect.getsource(ConversationManager.do_listen_and_comment)
        assert "session = create_streaming_session(self.config)" in src
        assert "on_chunk=(session.feed if session else None)" in src
        assert "text = session.final()" in src
        # 回退链保留且采样率透传（wav 头一致性）
        assert "rate=rate" in src
        assert "transcribe(wav_path, self.config)" in src

    def test_keyboard_mode_lifecycle(self):
        """按键模式：start 建会话并喂入；stop final() 优先、整段链回退保留。"""
        import main as app_main
        start_src = inspect.getsource(app_main.MainApplication._on_start_recording)
        assert "self._stream_session = create_streaming_session(self.config)" in start_src
        assert "on_chunk=self._stream_session.feed if self._stream_session else None" in start_src
        stop_src = inspect.getsource(app_main.MainApplication._on_stop_recording)
        assert "text = session.final()" in stop_src
        assert "transcribe(wav_path, self.config)" in stop_src


class TestConfigAndUi:
    """配置与 UI：开关键落盘 + talk 页绑定。"""

    def _load(self, name):
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), name)
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def test_example_config_key(self):
        assert self._load("config.example.json")["talk"]["asr_streaming"] is True

    def test_user_config_key(self):
        assert self._load("config.json")["talk"]["asr_streaming"] is True

    def test_talk_ui_switch_bound(self):
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "frontend", "ui", "tabs", "talk.py")
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
        assert '("talk", "asr_streaming")' in src
