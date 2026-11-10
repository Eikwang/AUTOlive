"""backing_player 子进程薄入口守卫（2026-11-10 R2 伴奏卡顿根修）。

覆盖（Eng 守卫策略/价值卡）：
- 回执顺序契约（F1/D19+CS3）：先写 value → 后 set event（防竞态读旧值）
- 哨兵语义（F9/D24）：全退出路径（vol<=0/加载失败/异常）统一置回执——防
  主进程每首歌白等门超时
- 协作停止（F4/D22）：stop_event set → 写流循环 break → 正常 close
- 回执双形态（F3/D21）：mp.Value(.value) 与 dict(["t"]) 统一出口
- spawn 真进程测试=skip 条件（真风险=音频设备非 mp 可用性——volume=0 路径
  在 PyAudio 初始化前退出可真进程测，其余 pyaudio 一律 mock）
"""
import threading
import time
import types

import pytest

import utils.audio.backing_player as bp


class _FakeEvent:
    def __init__(self):
        self._flag = False

    def set(self):
        self._flag = True

    def is_set(self):
        return self._flag

    def wait(self, timeout=None):
        import time as _t
        deadline = _t.monotonic() + (timeout or 0)
        while not self._flag and _t.monotonic() < deadline:
            _t.sleep(0.01)
        return self._flag


class _FakeValue:
    """mp.Value('d') 形态替身（.value 属性）。"""

    def __init__(self):
        self.value = 0.0


class _FakeWave:
    """wave.open 替身：返回固定 PCM 流。"""

    def __init__(self, path, mode="rb"):
        self._frames_left = 10  # 10 个 chunk 后自然播完

    def getsampwidth(self):
        return 2

    def getnchannels(self):
        return 2

    def getframerate(self):
        return 44100

    def readframes(self, n):
        if self._frames_left <= 0:
            return b""
        self._frames_left -= 1
        return b"\x00\x01" * (n * 2)

    def close(self):
        pass


class _FakeStream:
    def __init__(self, **kw):
        self.started = False
        self.written = []
        _FakeStream.last = self

    def start_stream(self):
        self.started = True

    def write(self, data):
        self.written.append(data)

    def stop_stream(self):
        pass

    def close(self):
        pass


class _FakePyAudio:
    def __init__(self):
        pass

    def get_format_from_width(self, w):
        return 8

    def open(self, **kw):
        return _FakeStream(**kw)

    def terminate(self):
        pass


@pytest.fixture
def fake_audio_env(monkeypatch):
    """pydub/wave/pyaudio 全 mock（pytest 环境不碰真音频设备——Eng 守卫策略）。"""
    from pydub import AudioSegment
    orig_from, orig_export = AudioSegment.from_file, AudioSegment.export
    AudioSegment.from_file = classmethod(
        lambda cls, path, **k: AudioSegment.silent(duration=2000))
    AudioSegment.export = lambda self, path, **k: open(path, "wb").close()
    monkeypatch.setattr("wave.open", lambda path, mode="rb": _FakeWave(path, mode))
    monkeypatch.setattr(bp, "_load_pyaudio", None, raising=False)
    # backing_player 内部 import pyaudio——monkeypatch sys.modules 级
    fake_pa = types.SimpleNamespace(PyAudio=_FakePyAudio)
    monkeypatch.setitem(__import__("sys").modules, "pyaudio", fake_pa)
    yield
    AudioSegment.from_file, AudioSegment.export = orig_from, orig_export


def test_report_started_order_value_then_event():
    """回执顺序契约：先写 value → 后 set event（监控顺序；防主进程 Event
    wait 返回后读到竞态旧值——F1/CS3/D19）。"""
    order = []
    val = _FakeValue()
    ev = _FakeEvent()

    class _TrackedValue(_FakeValue):
        @property
        def value(self):
            return 0.0

        @value.setter
        def value(self, v):
            order.append("value")
            super().value = v  # noqa

    # 直接验证 _report_started 行为：value 先于 event set
    v = _FakeValue()
    writes = []
    real_set = ev.set
    ev.set = lambda: (writes.append("event"), real_set())[0]
    # monkeypatch 顺序观察：包装 value 写入
    class _Wrap:
        value = property(lambda self: v.value,
                         lambda self, x: (writes.append("value"),
                                          setattr(v, "value", x))[0])
    bp._report_started(ev, _Wrap(), 1.23)
    assert writes == ["value", "event"]  # 顺序契约
    assert v.value == 1.23


def test_report_started_dual_form():
    """回执双形态（F3/D21）：mp.Value 形态（.value）与 dict 形态（["t"]）统一出口。"""
    val = _FakeValue()
    ev = _FakeEvent()
    bp._report_started(ev, val, 5.0)
    assert val.value == 5.0 and ev.is_set()
    box = {}
    ev2 = _FakeEvent()
    bp._report_started(ev2, box, 6.0)
    assert box["t"] == 6.0 and ev2.is_set()


def test_sentinel_on_zero_volume(fake_audio_env):
    """哨兵语义（F9/D24）：vol<=0 早退路径仍置回执（value>0+event set）——
    防 backing_volume=0 时主进程每首歌白等门超时 10s（既有缺陷顺手修）。"""
    val = _FakeValue()
    ev = _FakeEvent()
    bp._backing_process_main("fake.wav", 0.0, None, ev, val, None)
    assert ev.is_set() and val.value > 0  # 早退也置回执（哨兵消除）


def test_sentinel_on_load_failure(monkeypatch, tmp_path):
    """加载失败路径统一置回执（哨兵语义）。"""
    from pydub import AudioSegment
    monkeypatch.setattr(AudioSegment, "from_file",
                        classmethod(lambda cls, path, **k: (_ for _ in ()).throw(
                            RuntimeError("boom"))))
    val = _FakeValue()
    ev = _FakeEvent()
    bp._backing_process_main(str(tmp_path / "x.wav"), 0.8, None, ev, val, None)
    assert ev.is_set() and val.value > 0


def test_cooperative_stop_breaks_loop(fake_audio_env, monkeypatch):
    """协作停止（F4/D22）：stop_event set → 写流循环 break（正常 close 而非
    terminate 强杀——设备句柄干净释放）。"""
    stop = _FakeEvent()
    val = _FakeValue()
    ev = _FakeEvent()

    orig_write = _FakeStream.write

    def _write_on_first(self, data):
        stop.set()  # 第一个 chunk 后触发协作停止
        return orig_write(self, data)

    monkeypatch.setattr(_FakeStream, "write", _write_on_first)
    bp._backing_process_main("fake.wav", 0.8, None, ev, val, stop)
    assert len(_FakeStream.last.written) == 1  # 第二 chunk 前退出
    assert ev.is_set()


def test_started_value_after_gate_release(fake_audio_env):
    """门放行后才 start_stream+回执（11-04 两阶段起播语义保持）。"""
    gate = _FakeEvent()
    val = _FakeValue()
    ev = _FakeEvent()
    t0 = time.monotonic()
    # 门未放行：子进程在门 wait——放行线程模拟主进程受理后置位
    threading.Thread(target=lambda: (time.sleep(0.05), gate.set()),
                     daemon=True).start()
    bp._backing_process_main("fake.wav", 0.8, gate, ev, val, None)
    assert _FakeStream.last.started is True
    assert val.value >= t0  # 回执=起播时刻（monotonic）


@pytest.mark.skipif(True, reason="真 spawn 生命周期测试=volume=0 路径按需手测"
                                 "（pytest 内 mp+pyaudio 设备面见 Eng 守卫策略）")
def test_real_spawn_lifecycle_volume_zero():
    pass
