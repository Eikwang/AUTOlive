# -*- coding: utf-8 -*-
"""builtin 播放器与字幕管理器单元测试（整合计划 E-10 清单）

运行：runtime312 的 python -m pytest tests/features/test_builtin_captions.py -v
说明：.gitignore 的 test_*.py 规则使本文件不入库（TODOS #9 既有问题），
本地执行有效。
"""
import json
import os
import queue as pyqueue
import sys
import tempfile
import threading
import time
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import pytest


# ---------- 工具：构造不经 PyAudio 的播放器实例（纯逻辑测试） ----------

def make_player(config_data, config_file=None):
    from utils.audio.builtin_play_center import AUDIO_PLAY_CENTER
    p = AUDIO_PLAY_CENTER.__new__(AUDIO_PLAY_CENTER)
    p.config_data = config_data
    p.config_path = config_file or "config.json"
    p.stream = None
    p.pause_event = threading.Event()
    p.audio_data_event = threading.Event()
    p._done_event = threading.Event()
    p._skip_event = threading.Event()
    p._stop_flag = threading.Event()
    p.audio_json_list = []
    p.list_lock = threading.Lock()
    p._current = None
    p.last_error = None
    p._error_seq = 0
    return p


def write_config(tmpdir, cfg):
    path = os.path.join(tmpdir, "config.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False)
    return path


# ---------- E-10-1：priority_mapping 三语义 ----------

def test_priority_unrecognized_type_goes_tail():
    p = make_player({"priority_mapping": {"comment": 30}})
    p.audio_json_list = [{"type": "song"}, {"type": "gift"}]
    p._data_priority_insert({"type": "unknown_type"})
    assert p.audio_json_list[-1] == {"type": "unknown_type"}


def test_priority_ordering():
    p = make_player({"priority_mapping": {"comment": 30, "gift": 20, "copywriting": 1}})
    p.audio_json_list = [{"type": "copywriting"}]
    p._data_priority_insert({"type": "gift"})       # 排在 copywriting 前
    p._data_priority_insert({"type": "comment"})    # 排在最前
    assert [d["type"] for d in p.audio_json_list] == ["comment", "gift", "copywriting"]


def test_priority_insert_index_explicit():
    p = make_player({"priority_mapping": {}})
    p.play({"voice_path": "a.wav", "type": "comment"})
    p.play({"voice_path": "b.wav", "type": "comment", "insert_index": 0})
    assert p.audio_json_list[0]["voice_path"] == "b.wav"
    p.play({"voice_path": "c.wav", "type": "comment", "insert_index": -1})
    assert p.audio_json_list[-1]["voice_path"] == "c.wav"


# ---------- E-10-2：队列上限背压 / 字幕丢最旧 ----------

def test_player_queue_full_blocks_then_consumes():
    p = make_player({"priority_mapping": {}})
    p._hot_value = lambda key, default: 2 if key == "queue_max" else default  # 测试：上限=2
    p.audio_json_list = [{"x": i} for i in range(2)]
    # queue_max=2：第三次入队应阻塞，消费一条后放行
    result = {}

    def blocked():
        result["ok"] = p.play({"voice_path": "c.wav"})

    t = threading.Thread(target=blocked, daemon=True)
    t.start()
    time.sleep(0.4)
    assert t.is_alive(), "满载时 play 应阻塞背压"
    with p.list_lock:
        p.audio_json_list.pop(0)
    t.join(timeout=2)
    assert result.get("ok") is True


def test_captions_queue_drop_oldest():
    from utils.web_captions.captions_manager import CaptionsManager
    with tempfile.TemporaryDirectory() as tmp:
        cfg_path = write_config(tmp, {"web_captions_printer": {"enable": True, "queue_max": 2}})
        m = CaptionsManager.__new__(CaptionsManager)
        m.config = None
        m.config_path = cfg_path
        m._config_mtime = 0.0
        m._captions_cfg = {"enable": True, "queue_max": 2}
        m.message_queue = pyqueue.Queue()
        m.lock = threading.Lock()
        m.last_message_time = None
        for i in range(3):
            m._enqueue(f"msg{i}", 0, 100)
        items = []
        while True:
            try:
                items.append(m.message_queue.get_nowait()[0])
            except pyqueue.Empty:
                break
        assert items == ["msg1", "msg2"], "满载应丢最旧"


# ---------- E-1：start_delay 强制 0 ----------

def test_captions_start_delay_zero_when_align(tmp_path=None):
    from utils.web_captions.captions_manager import CaptionsManager
    with tempfile.TemporaryDirectory() as tmp:
        cfg_path = write_config(tmp, {"web_captions_printer": {"enable": True, "keep_time_align_audio": True}})
        m = CaptionsManager.__new__(CaptionsManager)
        m.config = None
        m.config_path = cfg_path
        m._config_mtime = 0.0
        m._captions_cfg = {"enable": True, "keep_time_align_audio": True}
        m.lock = threading.Lock()
        m.last_message_time = None
        keep, delay = m._compute_timing({"voice_path": "不存在.wav", "speed": 1}, "你好世界")
        assert delay == 0, "对齐模式 start_delay 必须为 0（E-1）"
        assert keep > 0


# ---------- E-2：变速 keep_time 折算 ----------

def test_captions_keep_time_speed_adjust(tmp_path=None):
    """变速 2x 时 keep_time 折算减半（用真实 wav 验证）"""
    import wave as wavemod
    import struct
    from utils.web_captions.captions_manager import CaptionsManager
    with tempfile.TemporaryDirectory() as tmp:
        wav_path = os.path.join(tmp, "t.wav")
        with wavemod.open(wav_path, "w") as wf:
            wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(16000)
            wf.writeframes(struct.pack("<h", 0) * 16000 * 2)  # 2 秒静音
        cfg_path = write_config(tmp, {"web_captions_printer": {"enable": True, "keep_time_align_audio": True}})
        m = CaptionsManager.__new__(CaptionsManager)
        m.config = None
        m.config_path = cfg_path
        m._config_mtime = 0.0
        m._captions_cfg = {"enable": True, "keep_time_align_audio": True}
        m.lock = threading.Lock()
        m.last_message_time = None
        keep_fast, _ = m._compute_timing({"voice_path": wav_path, "speed": 2.0}, "字")
        keep_slow, _ = m._compute_timing({"voice_path": wav_path, "speed": 0.5}, "字")
        assert keep_fast < 1000 * 2.2, f"2x 变速应折算到 ~1s，实测 {keep_fast}ms"
        assert keep_slow > 1000 * 3.0, f"0.5x 变速应折算到 ~4s，实测 {keep_slow}ms"


# ---------- E-10-5：keep_time 降级不阻塞 ----------

def test_captions_keep_time_degradation():
    from utils.web_captions.captions_manager import CaptionsManager
    with tempfile.TemporaryDirectory() as tmp:
        cfg_path = write_config(tmp, {"web_captions_printer": {"enable": True, "keep_time_align_audio": True}})
        m = CaptionsManager.__new__(CaptionsManager)
        m.config = None
        m.config_path = cfg_path
        m._config_mtime = 0.0
        m._captions_cfg = {"enable": True, "keep_time_align_audio": True}
        m.lock = threading.Lock()
        m.last_message_time = None
        keep, delay = m._compute_timing({"voice_path": "不存在.wav"}, "字" * 10)
        assert keep >= 1000  # 降级自动计算：len*80+2000


# ---------- push 入口：disable 短路 / 空内容跳过（S4） ----------

def test_captions_push_disabled_and_empty():
    from utils.web_captions.captions_manager import CaptionsManager
    import asyncio
    with tempfile.TemporaryDirectory() as tmp:
        cfg_path = write_config(tmp, {"web_captions_printer": {"enable": False}})
        m = CaptionsManager.__new__(CaptionsManager)
        m.config = None
        m.config_path = cfg_path
        m._config_mtime = 0.0
        m._captions_cfg = {"enable": False}
        m.message_queue = pyqueue.Queue()
        asyncio.run(m.push({"content": "x"}))
        assert m.message_queue.empty(), "enable=false 应零入队"
        m._captions_cfg = {"enable": True}
        asyncio.run(m.push({"content": ""}))
        assert m.message_queue.empty(), "空内容不推字幕"


# ---------- F6：stop_current_audio 三模式分发 ----------

def test_stop_current_audio_dispatch():
    from utils.audio.playback_manager import PlaybackMixin
    calls = []

    class StubPlayer:
        def skip_current_stream(self):
            calls.append("skip")

    mixin = types.SimpleNamespace()
    mixin.audio_player = StubPlayer()
    for mode in ("builtin", "audio_player", "audio_player_v2"):
        mixin.config = types.SimpleNamespace(
            get=lambda *keys, _m=mode: _m if keys == ("play_audio", "player") else None)
        PlaybackMixin.stop_current_audio(mixin)
    assert calls == ["skip", "skip", "skip"], "三模式都应走 skip_current_stream（F6/E 修订）"

    # EDTalk 激活：静默跳过
    mixin.config = types.SimpleNamespace(
        get=lambda *keys: (True if keys == ("visual_body",) else
                           (True if keys == ("edtalk_realtime", "enable") else
                            ("builtin" if keys == ("play_audio", "player") else None))))
    # is_edtalk_active(config) 需要 config.get 支持双键形态
    class Cfg:
        def get(self, *keys):
            if keys == ("visual_body",):
                return "edtalk_realtime"
            if keys == ("edtalk_realtime", "enable"):
                return True
            if keys == ("play_audio", "player"):
                return "builtin"
            return None
    mixin.config = Cfg()
    PlaybackMixin.stop_current_audio(mixin)
    assert calls == ["skip", "skip", "skip"], "EDTalk 激活时不追加调用"


# ---------- E-10-6：ensure-default 迁移 ----------

def test_ensure_defaults_migration():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "settings_mod",
        os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                     "frontend", "config", "settings.py"))
    # settings.py 依赖较多，这里仅验证默认值表内容与键覆盖
    from frontend.config import settings as settings_mod
    assert "device_index" in settings_mod._AUDIO_PLAYER_DEFAULTS
    assert settings_mod._AUDIO_PLAYER_DEFAULTS["priority_mapping"].get("comment") == 30
    assert "keep_time_align_audio" in settings_mod._WEB_CAPTIONS_PRINTER_DEFAULTS
    assert "api_ip_port" not in settings_mod._WEB_CAPTIONS_PRINTER_DEFAULTS
