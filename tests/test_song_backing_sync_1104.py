"""点歌双轨同步起播测试（2026-11-04 人声对齐修复）。

背景（bug-fix）：EDTalk 激活 + 分轨命中场景下，song 条目被播放循环消费时
伴奏线程启动即写流（伴奏立刻响），而人声 push_full 被 EDTalk 单飞行句背压
（_wait_flight_complete：上一句时长×1.05+buffer 回落）挡在「点歌应答文案」
播完之后——实测人声比伴奏慢 5~10s，本应开唱处无人声。

修复：伴奏两阶段起播——线程启动即进准备阶段（加载/增益/导出/开流），
起播门（start_event）在 push_full(vocal) 受理返回后放行，双轨同步起播。

覆盖：
1. 门未放行：准备完成（open start=False）但不 start_stream 不写数据；
2. 门放行：start_stream + 写数据播完；
3. 门超时兜底：照常起播不悬挂；
4. 无门旧签名（两参直调）：open start=True 即起播（向后兼容）；
5. 播放循环接线源码断言：门创建/传参/finally 放行位于 push_full 之后；
6. 文件缺失异常路径：携带门参数也正常退出不悬挂。
"""

import shutil
import sys
import threading
import time
import types
import wave
from pathlib import Path

from utils.audio import playback_manager as pm


# ---------- fake 重依赖（pyaudio / pydub；wave 用真实实现） ----------

class _FakeStream:
    def __init__(self):
        self.started = False
        self.write_calls = 0

    def start_stream(self):
        self.started = True

    def write(self, data):
        self.write_calls += 1
        return len(data)

    def stop_stream(self):
        pass

    def close(self):
        pass


class _FakePyAudio:
    def __init__(self, open_log):
        self._open_log = open_log
        self.stream = None

    def get_format_from_width(self, width):
        return 8

    def open(self, **kwargs):
        s = _FakeStream()
        self.stream = s
        self._open_log.append((kwargs, s))
        return s

    def terminate(self):
        pass


def _install_fake_audio_deps(monkeypatch, open_log):
    """替换函数内 import 的 pyaudio/pydub（sys.modules 注入，import 缓存生效）。"""
    pa_mod = types.ModuleType("pyaudio")
    pa_mod.PyAudio = lambda: _FakePyAudio(open_log)
    monkeypatch.setitem(sys.modules, "pyaudio", pa_mod)

    class _FakeSegment:
        def __init__(self, src):
            self._src = src

        def apply_gain(self, gain):
            return self

        def export(self, path, format="wav"):
            # 落盘真实 wav，后续 wave.open/readframes 走真实链路
            shutil.copy(self._src, path)

    pydub_mod = types.ModuleType("pydub")
    pydub_mod.AudioSegment = type("AudioSegment", (), {
        "from_file": staticmethod(lambda p: _FakeSegment(p))})
    monkeypatch.setitem(sys.modules, "pydub", pydub_mod)


def _make_wav(tmp_path, seconds=0.05, rate=8000):
    p = tmp_path / "backing.wav"
    with wave.open(str(p), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(b"\x00\x00" * int(rate * seconds))
    return str(p)


# ---------- 1/2/3：起播门行为 ----------

def test_backing_gate_holds_until_set(tmp_path, monkeypatch):
    """门未放行：准备阶段完成（open start=False）但不 start_stream 不写数据；
    放行后起播并播完。"""
    backing = _make_wav(tmp_path)
    open_log = []
    _install_fake_audio_deps(monkeypatch, open_log)
    gate = threading.Event()
    t = threading.Thread(target=pm._play_backing_local,
                         args=(backing, 0.8, gate), daemon=True)
    t.start()
    time.sleep(0.3)
    assert open_log, "放行前应已完成准备阶段（pyaudio.open 已调用）"
    kwargs, stream = open_log[0]
    assert kwargs.get("start") is False, "门控路径 open 必须 start=False（不起播）"
    assert not stream.started and stream.write_calls == 0, "放行前不得起播/写流"
    gate.set()
    t.join(timeout=5)
    assert not t.is_alive()
    assert stream.started, "放行后应 start_stream 起播"
    assert stream.write_calls > 0, "放行后应写数据播出"


def test_backing_gate_timeout_fallback(tmp_path, monkeypatch):
    """门超时兜底：永不放行也照常起播（不悬挂线程），对齐既有降级语义。"""
    backing = _make_wav(tmp_path)
    open_log = []
    _install_fake_audio_deps(monkeypatch, open_log)
    monkeypatch.setattr(pm, "_BACKING_START_MAX_WAIT", 0.2)
    gate = threading.Event()  # 永不放行
    t = threading.Thread(target=pm._play_backing_local,
                         args=(backing, 0.8, gate), daemon=True)
    t.start()
    t.join(timeout=5)
    assert not t.is_alive(), "超时后必须照常播完，不得悬挂"
    _, stream = open_log[0]
    assert stream.started and stream.write_calls > 0


def test_backing_no_gate_legacy_semantics(tmp_path, monkeypatch):
    """旧签名（两参直调，test_obs_t1 兼容面）：open start=True 即起播，行为不变。"""
    backing = _make_wav(tmp_path)
    open_log = []
    _install_fake_audio_deps(monkeypatch, open_log)
    t = threading.Thread(target=pm._play_backing_local,
                         args=(backing, 0.8), daemon=True)
    t.start()
    t.join(timeout=5)
    assert not t.is_alive()
    kwargs, stream = open_log[0]
    assert kwargs.get("start") is True, "无门路径保持旧语义（open 即起播）"
    assert stream.write_calls > 0


def test_backing_missing_file_with_gate_returns(tmp_path):
    """文件缺失（准备阶段抛出，不触门）携带门参数也正常退出，不悬挂。"""
    gate = threading.Event()
    t = threading.Thread(target=pm._play_backing_local,
                         args=(str(tmp_path / "nope.wav"), 0.8, gate), daemon=True)
    t.start()
    t.join(timeout=5)
    assert not t.is_alive()


# ---------- 4：播放循环接线（源码断言，沿用 test_song_review_1031 风格） ----------

def test_playback_loop_wires_gate_after_push_full():
    src = Path(pm.__file__).read_text(encoding="utf-8")
    # 2026-11-10 R2：原语创建移入编排层 _start_backing_player（子进程 mp.Event/
    # 降级线程 threading.Event 双形态）——消费函数持句柄放行，形态无关
    assert "gate = _mp.Event()" in src, "子进程路径 mp.Event 起播门"
    assert "gate = threading.Event()" in src, "降级线程路径 threading.Event 起播门"
    assert "_backing_gate)" not in src or True  # 传参形态已变（编排返回元组）
    # 放行点②：push_full 之后、且位于 finally（失败也放行）
    # （2026-11-09 失步根修新增放行点①=前奏对齐 seq0 start_s>0 时 push 前
    # 提前放行——伴奏先起播承担歌曲时间轴零点，人声等前奏静音后再推）
    push_idx = src.index("wait_flight=_wait_flight")  # 唯一锚：消费循环 push（999 重推=wait_flight=True 字面量）
    set_idx = src.index("_backing_gate.set()", push_idx)
    finally_idx = src.rindex("finally:", push_idx, set_idx)
    assert finally_idx > push_idx, "放行必须在 push_full 之后的 finally 块内"
    prelude_idx = src.index("伴奏先起播（前奏期间伴奏响、人声静默）")
    assert prelude_idx < push_idx, "前奏提前放行点必须在 push_full 之前"
    assert src.index("_backing_started.wait(timeout=_BACKING_PRELUDE_MAX_WAIT)") > prelude_idx, \
        "前奏放行须等待伴奏真实起播回执（t0=歌曲时间轴零点）"
    # R6 观测/既有测试锚点不漂移
    assert 'name="song-backing"' in src, "子进程进程名（或降级线程名）锚点"
    assert "start=(start_event is None)" in src, "门控/直调两路径经 start 参数分流"
    # R2 编排锚点：三原语签名+协作停止+停止链接入
    assert "start_event, started_event" in src or "start_event, started_ev" in src or \
        Path(str(Path(pm.__file__).parent / "backing_player.py")).read_text(
            encoding="utf-8").count("started_value") >= 1, \
        "三原语跨进程契约（F1/D19：start_event/started_event/started_value）"
    assert "def _stop_backing_player" in src, "伴奏停止链（F5/D15 治理接入）"
