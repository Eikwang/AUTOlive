# -*- coding: utf-8 -*-
"""builtin 播放后端守卫（2026-11-13 助播撕裂卡顿根修，第二轮）。

症状：助播音频必定撕裂卡顿，日志 `builtin 输出 underrun ×11`；数字人偶发。

根因（本机对照实验实证）：PortAudio 回调/MCI 之外的阻塞写都需要 Python 线程
供数，主进程 GIL 争用（TTS/LLM/队列处理等纯 Python 工作）使供数线程超过缓冲
时长拿不到执行机会：
- 20 个同进程忙线程：回调 underflow 4-10 次，墙钟 2-6 倍（=撕裂卡顿）
- 跨进程满载：0 欠载（排除 CPU 因素）
- MCI 原生播放（winmm，Python 只下发指令）：满载下 3.14s/3.07s 播完 3s 音频

修法：本地播放优先走 MCI 原生后端（默认设备场景）；用户显式指定输出设备
（device_index>=0，MCI 仅支持系统默认设备）或 MCI 不可用时回退 PortAudio。
让位暂停&回退机制保留（2026-11-15 批次：999 机制退役，服务 d12 泛化让位）：
暂停=mci pause，回退=seek(暂停点-1s)，续播=resume。

守卫面：
A. PortAudio 回退路径：内存回调内容正确 / 缓冲 ~100ms / underrun 可观测 /
   让位内存游标回退
B. MCI 后端选择：默认设备走 mci、指定设备回退 portaudio
C. MCI 让位语义：暂停点毫秒登记 + 续播 seek 回退 1 秒 + 续播完成
"""
import os
import threading
import time
import wave

import pyaudio
import pytest

import utils.audio.builtin_play_center as bpc
from utils.audio.builtin_play_center import AUDIO_PLAY_CENTER as BuiltinPlayCenter

RATE = 48000
FRAME_WIDTH = 2  # 16bit mono
N_FRAMES = RATE * 2  # 2 秒
PCM = bytes((i * 7 + 3) % 256 for i in range(N_FRAMES * FRAME_WIDTH))


class _FakeAudioSeg:
    """pydub AudioSegment 桩：export 落真 wav（内容=固定 PCM）。"""

    def __init__(self, ms=2000):
        self._ms = ms

    @staticmethod
    def from_file(path):
        return _FakeAudioSeg()

    def export(self, out_path, format="wav"):
        seconds = self._ms / 1000.0
        frames = int(RATE * seconds)
        payload = (PCM * (frames // N_FRAMES + 1))[: frames * FRAME_WIDTH]
        with wave.open(out_path, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(FRAME_WIDTH)
            w.setframerate(RATE)
            w.writeframes(payload)


class _FakeStream:
    """PortAudio 流桩：start_stream 同步驱动回调（模拟实时线程喂帧）。"""

    def __init__(self, callback, owner, yield_at_call=None, underrun=False):
        self._cb = callback
        self._owner = owner
        self._yield_at_call = yield_at_call
        self._underrun = underrun
        self._active = False
        self._calls = 0
        self._round = 0
        self.output = bytearray()
        self.resume_output = bytearray()

    def start_stream(self):
        self._active = True
        self._round += 1
        while self._active:
            self._calls += 1
            status = pyaudio.paOutputUnderflow if self._underrun else 0
            data, flag = self._cb(None, 1024, None, status)
            if self._round >= 2:
                self.resume_output.extend(data)
            else:
                self.output.extend(data)
            if self._yield_at_call is not None and self._calls == self._yield_at_call:
                self._owner._interrupt_999_event.set()
                return
            if flag == pyaudio.paComplete:
                self._active = False

    def stop_stream(self):
        self._active = False

    def close(self):
        self._active = False

    def is_active(self):
        return self._active


class _FakePyAudio:
    def __init__(self, owner, yield_at_call=None, underrun=False):
        self._owner = owner
        self._yield_at_call = yield_at_call
        self._underrun = underrun
        self.open_kwargs = None
        self.stream = None

    def get_format_from_width(self, width):
        return pyaudio.paInt16

    def open(self, **kwargs):
        self.open_kwargs = kwargs
        self.stream = _FakeStream(kwargs["stream_callback"], self._owner,
                                  yield_at_call=self._yield_at_call,
                                  underrun=self._underrun)
        return self.stream


class _FakeMciDevice:
    """可控 MCI 设备桩：位置由测试显式推进（避免真实播放）。"""

    def __init__(self, path):
        self.path = path
        self.mode = "stopped"
        self.pos_ms = 0
        self.play_count = 0
        self.seek_targets = []
        self.closed = False


class _FakeMci:
    def __init__(self):
        self.devices = {}
        self.log = []

    def __call__(self, cmd):
        self.log.append(cmd)
        parts = cmd.split()
        if cmd.startswith("open "):
            path = cmd.split('"')[1]
            alias = parts[-1]
            self.devices[alias] = _FakeMciDevice(path)
            return (0, "1")
        alias = parts[1] if len(parts) > 1 else ""
        dev = self.devices.get(alias)
        if dev is None:
            return (263, "")
        if cmd.startswith("play "):
            dev.mode = "playing"
            dev.play_count += 1
            return (0, "")
        if cmd.startswith("pause "):
            dev.mode = "paused"
            return (0, "")
        if cmd.startswith("resume "):
            dev.mode = "playing"
            return (0, "")
        if cmd.startswith("stop "):
            dev.mode = "stopped"
            return (0, "")
        if cmd.startswith("close "):
            dev.closed = True
            dev.mode = "stopped"
            return (0, "")
        if cmd.startswith("seek "):
            target = int(parts[-1])
            dev.seek_targets.append(target)
            dev.pos_ms = target
            return (0, "")
        if cmd.startswith("status ") and parts[-1] == "mode":
            return (0, dev.mode)
        if cmd.startswith("status ") and parts[-1] == "position":
            return (0, str(dev.pos_ms))
        return (0, "")


def _make_player(tmp_path, monkeypatch, use_mci=False, yield_at_call=None,
                 underrun=False, device_index=-1):
    monkeypatch.setattr("utils.audio.builtin_play_center.AudioSegment", _FakeAudioSeg)
    monkeypatch.setattr(bpc, "_MCI_AVAILABLE", use_mci)
    fake_mci = _FakeMci()
    if use_mci:
        monkeypatch.setattr(bpc, "_mci_send", fake_mci)

    p = BuiltinPlayCenter.__new__(BuiltinPlayCenter)
    p.audio_out_path = str(tmp_path)
    p.config_path = str(tmp_path / "nonexistent_config.json")
    p._priority_provider = lambda: {"assistant_anchor_text": 40, "reread_top_priority": 500}
    p._hot_value = lambda key, default: device_index if key == "device_index" else default
    p._cb_loop = None
    p._completion_cb = None
    p._started_cb = None
    p._error_seq = 0
    p.last_error = None
    p.audio_json_list = []
    p.pause_event = threading.Event()
    p._skip_event = threading.Event()
    p._stop_flag = threading.Event()
    p._interrupt_999_event = threading.Event()
    p._suspended_999 = None
    p._suspended_999_at = None
    p._suspended_999_alarm_at = None
    p._suspended_play = None
    p._playing_weight = None
    p._current = None
    p._current_output_rate = 0
    p._underrun_count = 0
    p._use_mci = False
    p.stream = None
    p.audio = _FakePyAudio(p, yield_at_call=yield_at_call, underrun=underrun)
    return p, fake_mci


def _voice_file(tmp_path):
    vp = tmp_path / "voice.mp3"
    vp.write_bytes(b"stub")
    return str(vp)


# ---------------------------------------------------------------- A. PortAudio 回退

def test_memory_callback_full_playback_content(tmp_path, monkeypatch):
    """内存回调：完整播放输出与源 PCM 逐字节一致（EOF paComplete 语义保持）。"""
    p, _ = _make_player(tmp_path, monkeypatch)
    status = p._play_item({"type": "assistant_anchor_text",
                           "voice_path": _voice_file(tmp_path), "content": "t"})
    assert status == "completed"
    assert bytes(p.audio.stream.output) == PCM


def test_frames_per_buffer_explicit_100ms(tmp_path, monkeypatch):
    """缓冲显式 ~100ms（原默认 1024≈21ms @48k 抗 underrun 过薄）。"""
    p, _ = _make_player(tmp_path, monkeypatch)
    p._play_item({"type": "assistant_anchor_text",
                  "voice_path": _voice_file(tmp_path), "content": "t"})
    assert p.audio.open_kwargs["frames_per_buffer"] == max(2048, int(RATE * 0.1))


def test_underrun_counted(tmp_path, monkeypatch):
    """underrun 状态可观测（原回调静默丢弃 status）。"""
    p, _ = _make_player(tmp_path, monkeypatch, underrun=True)
    status = p._play_item({"type": "assistant_anchor_text",
                           "voice_path": _voice_file(tmp_path), "content": "t"})
    assert status == "completed"
    assert p._underrun_count > 0


def test_yield_suspend_resume_rewinds_memory_cursor(tmp_path, monkeypatch):
    """让位机制适配内存游标（2026-11-15 批次：999 退役，d12 泛化让位沿用）：
    第 60 次回调让位挂起，续播回退 1 秒。"""
    p, _ = _make_player(tmp_path, monkeypatch, yield_at_call=60)
    status = p._play_item({"type": "reread_top_priority",
                           "voice_path": _voice_file(tmp_path), "content": "t"})
    assert status == "suspended"
    held = p._suspended_play
    assert held is not None and held["pos"] is not None
    assert held["pos"][0] == 60 * 1024 * FRAME_WIDTH
    stream = p.audio.stream

    p._resume_suspended_999()
    expected_start = (60 * 1024 - RATE) * FRAME_WIDTH
    assert bytes(stream.resume_output) == PCM[expected_start:]
    assert p._suspended_999 is None


# ---------------------------------------------------------------- B. MCI 后端选择

def test_mci_backend_used_for_default_device(tmp_path, monkeypatch):
    """默认设备（device_index=-1）→ 走 MCI 原生播放，不打开 PortAudio 流。"""
    p, fake = _make_player(tmp_path, monkeypatch, use_mci=True)

    def _finish():
        time.sleep(0.2)
        for d in fake.devices.values():
            d.mode = "stopped"

    threading.Thread(target=_finish, daemon=True).start()
    status = p._play_item({"type": "assistant_anchor_text",
                           "voice_path": _voice_file(tmp_path), "content": "t"})
    assert status == "completed"
    assert p.audio.open_kwargs is None  # 未走 PortAudio
    assert any(c.startswith("play ") for c in fake.log)
    assert any(c.startswith("open ") for c in fake.log)
    assert not any("underrun" in c for c in fake.log)


def test_portaudio_when_device_explicitly_set(tmp_path, monkeypatch):
    """显式指定输出设备（>=0）→ MCI 不支持，回退 PortAudio 回调流。"""
    p, fake = _make_player(tmp_path, monkeypatch, use_mci=True, device_index=3)
    status = p._play_item({"type": "assistant_anchor_text",
                           "voice_path": _voice_file(tmp_path), "content": "t"})
    assert status == "completed"
    assert p.audio.open_kwargs is not None
    assert p._use_mci is False
    assert not any(c.startswith("open ") for c in fake.log)


# ---------------------------------------------------------------- C. MCI 让位语义

def _mci_play_item_in_thread(p, data_json, ready_evt, done_box):
    """后台跑 _play_item（MCI 桩位置不自动推进，需外部控制）。"""
    def _run():
        done_box.append(p._play_item(data_json))

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    for _ in range(200):  # 等设备 open+play 下发
        if p.stream is not None and getattr(p.stream, "_started", False):
            ready_evt.set()
            return t
        time.sleep(0.01)
    ready_evt.set()
    return t


def test_mci_yield_suspend_records_position_ms(tmp_path, monkeypatch):
    """让位（d12 泛化，2026-11-15 批次：999 退役）：MCI 暂停点毫秒登记
    （续播回退基准）。"""
    p, fake = _make_player(tmp_path, monkeypatch, use_mci=True)
    data = {"type": "reread_top_priority", "voice_path": _voice_file(tmp_path), "content": "t"}
    ready = threading.Event()
    box = []
    t = _mci_play_item_in_thread(p, data, ready, box)
    assert ready.wait(3)
    dev = next(iter(fake.devices.values()))
    dev.pos_ms = 1500          # 模拟已播 1.5 秒
    p.request_yield()          # 让位（在播条目 → 置中断事件）
    t.join(3)
    assert box == ["suspended"]
    assert p._suspended_play["pos_ms"] == 1500
    assert p._suspended_play["mci"] is True
    assert dev.closed is True  # 挂起即关设备释放文件句柄


def test_mci_resume_seeks_back_one_second(tmp_path, monkeypatch):
    """让位续播（2026-11-15 批次：999 退役，泛化语义沿用）：seek 到暂停点-1 秒
    后重新 play（回退 1 秒语义）。"""
    p, fake = _make_player(tmp_path, monkeypatch, use_mci=True)
    vp = _voice_file(tmp_path)
    p._suspended_play = {"wf": None, "tmp_audio_path": str(tmp_path / "tmp_x.wav"),
                         "pos": None, "frame_width": FRAME_WIDTH,
                         "mci": True, "pos_ms": 1500}
    p._suspended_999 = {"type": "reread_top_priority", "voice_path": vp}

    def _finish():
        time.sleep(0.2)
        for d in fake.devices.values():
            d.mode = "stopped"

    threading.Thread(target=_finish, daemon=True).start()
    p._resume_suspended_999()
    dev = next(iter(fake.devices.values()))
    assert dev.seek_targets == [500]      # 1500ms - 1000ms
    assert dev.play_count == 1            # seek 后 play 起播
    assert p._suspended_999 is None
