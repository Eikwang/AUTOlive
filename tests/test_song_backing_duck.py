"""伴奏闪避生命周期与挂点守卫（2026-11-12 伴奏闪避批次）。

覆盖（AC 映射见 specs/features/伴奏闪避_技术方案.md §9）：
- 生命周期原语（T2）：_set_backing_duck 双形态/缺失 no-op/clamp；hold/
  release_later/release_now 幂等与定时恢复；hold 取消未触发恢复（AC-003
  连续借位保持）；时长未知不定时（fail-soft 降级）
- 伴奏编排接线（T2）：_start_backing_player 新建原语（新歌全量起播 AC-007）
  +降级 dict 形态；_stop_backing_player 清原语/定时器（AC-007）
- 降级线程（同型回归）：_play_backing_local duck_box 缩放
- 消费循环挂点（T3）：借位 pop duck 0.5→恢复 1.0 / 连续借位保持 / song 段
  不触发（AC-004）/ 异常不悬挂（AC-006，内层 finally + 外层 except 双兜底）
- 前奏借位挂点（T3）：R5c 调度循环内插播闪避（AC-001 前奏面）
"""
import asyncio
import threading
import time
import types

import pytest

import utils.audio.playback_manager as pm
from utils.audio.playback_manager import PlaybackMixin
from utils.audio.queue_manager import QueueMixin


class _Impl(QueueMixin, PlaybackMixin):
    pass


class _DuckVal:
    """mp.Value('d') 形态替身（.value 属性）。"""

    def __init__(self, v=1.0):
        self.value = v


def _host():
    return _Impl()


# ---------------------------------------------------------------------------
# T2：生命周期原语
# ---------------------------------------------------------------------------

def test_set_backing_duck_dual_form_and_missing_noop():
    """双形态原语（dict["v"] / Value.value）；原语缺失 no-op 不抛（整轨模式/
    停止清理后语义）。"""
    h = _host()
    h._backing_duck = {"v": 1.0}
    h._set_backing_duck(0.5)
    assert h._backing_duck["v"] == 0.5
    val = _DuckVal()
    h._backing_duck = val
    h._set_backing_duck(0.5)
    assert val.value == 0.5
    h._set_backing_duck(1.0)
    assert val.value == 1.0
    del h._backing_duck
    h._set_backing_duck(0.5)  # 缺失=no-op 不抛


def test_set_backing_duck_clamps_out_of_range():
    """越界系数 clamp [0,1]（防脏值直通缩放）。"""
    h = _host()
    val = _DuckVal()
    h._backing_duck = val
    h._set_backing_duck(7.0)
    assert val.value == 1.0
    h._set_backing_duck(-3)
    assert val.value == 0.0


def test_hold_release_later_restores_after_duration(monkeypatch):
    """借位→hold 0.5→release_later（受理+时长+余量）→定时恢复 1.0（AC-002）。"""
    h = _host()
    h._backing_duck = {"v": 1.0}
    monkeypatch.setattr(PlaybackMixin, "_entry_duration_s",
                        lambda self, entry: 0.15)
    h._backing_duck_hold()
    assert h._backing_duck["v"] == 0.5
    h._backing_duck_release_later({"voice_path": "x"})
    assert h._backing_duck_restore_timer is not None
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline and h._backing_duck["v"] != 1.0:
        time.sleep(0.02)
    assert h._backing_duck["v"] == 1.0, "定时恢复未生效（AC-002）"
    assert h._backing_duck_restore_timer is None


def test_hold_cancels_pending_restore_consecutive(monkeypatch):
    """AC-003：连续借位——第二次 hold 取消未触发的恢复定时器，全程保持 0.5，
    最后一条播完（release_later 定时）才恢复。"""
    h = _host()
    h._backing_duck = {"v": 1.0}
    monkeypatch.setattr(PlaybackMixin, "_entry_duration_s",
                        lambda self, entry: 0.2)
    h._backing_duck_hold()           # 借位 A
    h._backing_duck_release_later({})
    h._backing_duck_hold()           # 借位 B（取消 A 的恢复定时器）
    time.sleep(0.8)                  # > 定时周期 0.7s——未取消则已翻转
    assert h._backing_duck["v"] == 0.5, \
        "恢复定时器未被第二次 hold 取消（AC-003 连续借位被音量泵动打断）"
    h._backing_duck_release_later({})  # 最后一条播完 → 调度恢复
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline and h._backing_duck["v"] != 1.0:
        time.sleep(0.02)
    assert h._backing_duck["v"] == 1.0


def test_release_later_unknown_duration_no_timer():
    """时长未知（pydub fail-soft=0）不定时——等下一次非借位 pop/停止路径恢复
    （宁可持续压低不提前放开盖住播报的降级语义）。"""
    h = _host()
    h._backing_duck = {"v": 1.0}
    h._backing_duck_hold()
    h._backing_duck_release_later({"voice_path": "missing_no_such.wav"})
    assert h._backing_duck_restore_timer is None
    assert h._backing_duck["v"] == 0.5
    h._backing_duck_release_now()
    assert h._backing_duck["v"] == 1.0


def test_release_now_idempotent_and_cancels_timer(monkeypatch):
    """立即恢复幂等 + 定时器取消（异常/人声恢复路径共用出口）。"""
    h = _host()
    h._backing_duck = {"v": 1.0}
    monkeypatch.setattr(PlaybackMixin, "_entry_duration_s",
                        lambda self, e: 0.15)
    h._backing_duck_hold()
    h._backing_duck_release_later({})
    h._backing_duck_release_now()
    assert h._backing_duck["v"] == 1.0
    assert h._backing_duck_restore_timer is None
    h._backing_duck_release_now()  # 幂等
    time.sleep(0.4)                # 定时器若未被取消会在此期间翻转
    assert h._backing_duck["v"] == 1.0


def test_stop_backing_player_clears_duck_and_timer(monkeypatch):
    """AC-007：停止链清闪避原语+定时器——此后 hold/release 全 no-op 不悬挂。"""
    h = _host()
    h._backing_duck = {"v": 1.0}
    monkeypatch.setattr(PlaybackMixin, "_entry_duration_s",
                        lambda self, e: 5.0)
    h._backing_duck_hold()
    h._backing_duck_release_later({})
    assert h._backing_duck_restore_timer is not None
    h._stop_backing_player()
    assert h._backing_duck is None
    assert h._backing_duck_restore_timer is None
    h._backing_duck_hold()    # no-op（原语已清）
    h._backing_duck_release_now()


# ---------------------------------------------------------------------------
# T2：伴奏编排接线（_start_backing_player 建原语 / _stop 清残留）
# ---------------------------------------------------------------------------

def test_start_backing_player_creates_fresh_duck(monkeypatch):
    """子进程路径：新建 mp.Value('d',1.0) 原语并随参数传给子进程；连点重建=
    新对象（AC-007：新歌天然全量起播，不残留上一首闪避态）。"""
    import multiprocessing as _mp

    created = []

    class _FakeProc:
        def __init__(self, target=None, args=(), daemon=None, name=None):
            self.args = args
            created.append(self)

        def start(self):
            pass

    monkeypatch.setattr(_mp, "Process", _FakeProc)
    h = _host()
    gate, started_ev, read_started, stop_ev, proc, degraded = \
        h._start_backing_player("fake.wav", 0.8)
    assert degraded is False
    duck = h._backing_duck
    assert duck is not None and float(duck.value) == 1.0
    assert created and created[-1].args[-1] is duck, "闪避原语未随参数传子进程"
    old = duck
    h._start_backing_player("fake2.wav", 0.8)
    assert h._backing_duck is not old


def test_start_backing_player_degraded_dict_form(monkeypatch):
    """降级线程路径（mp 不可用）：duck 载体=dict（线程无 mp 原语）。"""
    import multiprocessing as _mp

    def _boom(*a, **k):
        raise RuntimeError("mp unavailable")

    monkeypatch.setattr(_mp, "Value", _boom)
    h = _host()
    gate, started_ev, read_started, stop_ev, proc, degraded = \
        h._start_backing_player("missing_no_such.wav", 0.8)
    assert degraded is True and proc is None
    assert isinstance(h._backing_duck, dict) and h._backing_duck["v"] == 1.0


# ---------------------------------------------------------------------------
# 降级线程 duck_box（_play_backing_local 同型回归）
# ---------------------------------------------------------------------------

class _FakeWaveL:
    def __init__(self, path, mode="rb"):
        self._left = 6

    def getsampwidth(self):
        return 2

    def getnchannels(self):
        return 2

    def getframerate(self):
        return 44100

    def readframes(self, n):
        if self._left <= 0:
            return b""
        self._left -= 1
        return b"\x00\x01" * (n * 2)

    def close(self):
        pass


class _FakeStreamL:
    last = None

    def __init__(self, **kw):
        self.written = []
        _FakeStreamL.last = self

    def start_stream(self):
        pass

    def write(self, d):
        self.written.append(d)

    def stop_stream(self):
        pass

    def close(self):
        pass


class _FakePyAudioL:
    def get_format_from_width(self, w):
        return 8

    def open(self, **kw):
        return _FakeStreamL(**kw)

    def terminate(self):
        pass


def test_play_backing_local_duck_box_scales(monkeypatch):
    """降级线程：duck_box 每 chunk 缩放（与子进程 duck_value 同型语义）。"""
    from pydub import AudioSegment
    orig_from, orig_export = AudioSegment.from_file, AudioSegment.export
    AudioSegment.from_file = classmethod(
        lambda cls, path, **k: AudioSegment.silent(duration=2000))
    AudioSegment.export = lambda self, path, **k: open(path, "wb").close()
    monkeypatch.setattr("wave.open", lambda path, mode="rb": _FakeWaveL(path, mode))
    fake_pa = types.SimpleNamespace(PyAudio=_FakePyAudioL)
    monkeypatch.setitem(__import__("sys").modules, "pyaudio", fake_pa)
    try:
        duck = {"v": 0.5}
        t = threading.Thread(target=pm._play_backing_local,
                             args=("fake.wav", 0.8),
                             kwargs={"duck_box": duck}, daemon=True)
        t.start()
        t.join(timeout=5)
        assert not t.is_alive()
        import audioop as _audioop
        raw = b"\x00\x01" * (1024 * 2)
        written = _FakeStreamL.last.written
        assert written and all(c == _audioop.mul(raw, 2, 0.5) for c in written)
    finally:
        AudioSegment.from_file, AudioSegment.export = orig_from, orig_export


# ---------------------------------------------------------------------------
# T3：消费循环挂点（only_play_audio）与前奏借位挂点（_consume_edtalk_entry）
# ---------------------------------------------------------------------------

class _Cfg:
    """only_play_audio 依赖的最小配置桩（EDTalk 激活态）。"""

    def __init__(self):
        self._d = {
            ("filter", "priority_mapping"): {},
            ("edtalk_realtime", "enable"): True,
            "visual_body": "edtalk_realtime",
        }

    def get(self, *keys):
        return self._d.get(keys if len(keys) > 1 else keys[0])


class _ClientStub:
    def __init__(self):
        self.pushes = []

    async def push_full(self, path, content="", wait_flight=True):
        self.pushes.append({"path": path, "wait_flight": wait_flight})
        return True

    def get_output_frames(self):
        return 0


class _LoopHost(QueueMixin, PlaybackMixin):
    """only_play_audio 最小宿主（复用 test_audio_playback_review 驱动拓扑；
    _backing_duck 预置=模拟伴奏已起播（_start_backing_player 产物）。"""

    def __init__(self):
        self.voice_tmp_path_queue = []
        self.voice_tmp_path_queue_lock = threading.RLock()
        self.voice_tmp_path_queue_not_empty = threading.Condition(
            lock=self.voice_tmp_path_queue_lock)
        self.config = _Cfg()
        self.active_reread_top_999 = False
        self.edtalk_client = _ClientStub()
        self._backing_duck = {"v": 1.0}

    def enqueue(self, entry):
        with self.voice_tmp_path_queue_lock:
            self.voice_tmp_path_queue.append(entry)
            self.voice_tmp_path_queue_not_empty.notify()

    def duck(self):
        return self._backing_duck["v"]


def _seg(seq=1, total=3, start_s=10.0):
    return {"type": "song", "voice_path": "seg.wav", "content": "s",
            "song_segment": {"seq": seq, "total": total, "seg_dir": "",
                             "start_s": start_s}}


class _LoopDriver:
    """worker 线程驱动拓扑对齐生产（only_play_audio 专用事件循环——其空队列
    等待=Condition.wait 阻塞调用，不能与断言同线程）。"""

    def __init__(self, host):
        self.host = host
        threading.Thread(
            target=lambda: asyncio.new_event_loop().run_until_complete(
                host.only_play_audio()),
            daemon=True, name="duck-loop-worker").start()

    def wait_until(self, cond, timeout=5.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if cond():
                return True
            time.sleep(0.01)
        return False


def test_borrowed_pop_ducks_then_restores(monkeypatch):
    """AC-001/002（间奏面）：②b 窗口预检借位 pop → 播报**出声（受理）**时
    伴奏 0.5 → 播完（受理+时长估计）恢复 1.0。出队/飞行等待期不压（song6.log
    修正：②b 可在人声未唱完时出队借位条目，push 飞行等待把实际出声推迟到
    真实间奏——出队即压低会把正在唱的人声伴奏压掉）。"""
    host = _LoopHost()
    duck_during_consume = []

    async def fake_consume(self, data_json, path):
        duck_during_consume.append(self.duck())
        await asyncio.sleep(0.05)

    monkeypatch.setattr(PlaybackMixin, "_consume_edtalk_entry", fake_consume)
    monkeypatch.setattr(PlaybackMixin, "_entry_duration_s",
                        lambda self, e: 0.15)
    host._song_anchor = {"t0": time.monotonic() - 5.0, "last_end": 0.0}
    host.enqueue({"type": "comment", "voice_path": "x.wav", "content": "播报"})
    host.enqueue(_seg(seq=1, start_s=15.0))  # 窗口终点（未到期 → ②b 借位）
    driver = _LoopDriver(host)
    assert driver.wait_until(lambda: len(duck_during_consume) == 1), \
        "借位条目未被消费"
    assert duck_during_consume[0] == 1.0, "受理前（出队/飞行等待期）伴奏即被压低"
    assert driver.wait_until(lambda: host.duck() == 0.5, timeout=2.0), \
        "播报受理（出声）时伴奏未压低（AC-001）"
    assert driver.wait_until(lambda: host.duck() == 1.0, timeout=2.5), \
        "播报播完后伴奏未恢复（AC-002）"


def test_borrowed_flight_wait_does_not_duck_singing_vocal(monkeypatch):
    """回归（song6.log 实测根修）：人声段仍在唱（now < last_end 估算末点）时
    ②b 借位 pop——播报经 push 飞行等待推迟到真实间奏才出声，**等待期间
    （唱歌中）伴奏必须保持全量**。修复前出队即压低=seq0 人声后 60s 伴奏被压
    （用户实测「进入唱歌后伴奏音量并不会恢复」）。"""
    host = _LoopHost()
    duck_during_consume = []

    async def fake_consume(self, data_json, path):
        duck_during_consume.append(self.duck())
        await asyncio.sleep(0.05)

    monkeypatch.setattr(PlaybackMixin, "_consume_edtalk_entry", fake_consume)
    monkeypatch.setattr(PlaybackMixin, "_entry_duration_s",
                        lambda self, e: 0.15)
    now = time.monotonic()
    host._song_anchor = {"t0": now - 10.0,
                         "last_end": now + 20.0}  # 人声唱到 +20s（估算末点未到）
    host.enqueue({"type": "comment", "voice_path": "x.wav", "content": "播报"})
    host.enqueue(_seg(seq=1, start_s=50.0))  # due≈now+39.7 → ②b 预检通过
    driver = _LoopDriver(host)
    assert driver.wait_until(lambda: len(duck_during_consume) == 1), \
        "借位条目未被消费"
    assert duck_during_consume[0] == 1.0, \
        "人声仍在唱（播报飞行等待中）伴奏即被压低（song6.log 回归）"
    assert driver.wait_until(lambda: host.duck() == 0.5, timeout=2.0), \
        "播报受理（出声）时未压低（AC-001）"
    assert driver.wait_until(lambda: host.duck() == 1.0, timeout=2.5), \
        "播报播完后伴奏未恢复（AC-002）"


def test_consecutive_borrowed_keep_ducked(monkeypatch):
    """AC-003：连续借位——恢复定时器被下一次 hold 取消，两次 0.5 之间无 1.0，
    最后一条播完才恢复。"""
    host = _LoopHost()
    writes = []
    orig_set = PlaybackMixin._set_backing_duck

    def rec_set(self, factor):
        writes.append(float(factor))
        orig_set(self, factor)

    monkeypatch.setattr(PlaybackMixin, "_set_backing_duck", rec_set)

    async def fake_consume(self, data_json, path):
        await asyncio.sleep(0.02)

    monkeypatch.setattr(PlaybackMixin, "_consume_edtalk_entry", fake_consume)
    monkeypatch.setattr(PlaybackMixin, "_entry_duration_s",
                        lambda self, e: 0.15)
    host._song_anchor = {"t0": time.monotonic() - 5.0, "last_end": 0.0}
    host.enqueue({"type": "comment", "voice_path": "a.wav", "content": "A"})
    host.enqueue({"type": "comment", "voice_path": "b.wav", "content": "B"})
    host.enqueue(_seg(seq=1, start_s=15.0))
    driver = _LoopDriver(host)
    assert driver.wait_until(
        lambda: host.duck() == 1.0 and len(writes) >= 3, timeout=5.0), \
        f"闪避未恢复：writes={writes}"
    assert writes == [0.5, 0.5, 1.0], \
        f"闪避写入序列异常——连续借位被音量泵动打断（AC-003）：{writes}"


def test_song_segment_does_not_duck(monkeypatch):
    """AC-004：歌曲人声段（_borrowed=False，②a 到期 pop）不触发闪避。"""
    host = _LoopHost()
    writes = []
    orig_set = PlaybackMixin._set_backing_duck

    def rec_set(self, factor):
        writes.append(float(factor))
        orig_set(self, factor)

    monkeypatch.setattr(PlaybackMixin, "_set_backing_duck", rec_set)
    consumed = []

    async def fake_consume(self, data_json, path):
        consumed.append(data_json.get("type"))

    monkeypatch.setattr(PlaybackMixin, "_consume_edtalk_entry", fake_consume)
    host._song_anchor = {"t0": time.monotonic() - 10.0, "last_end": 0.0}
    host.enqueue(_seg(seq=1, start_s=5.0))  # due=now−5.3 已到期 → ②a 直推
    driver = _LoopDriver(host)
    assert driver.wait_until(lambda: consumed, timeout=5.0), "人声段未被消费"
    time.sleep(0.3)
    assert 0.5 not in writes, f"歌曲人声段触发了闪避（AC-004）：{writes}"


def test_consume_exception_restores_duck(monkeypatch):
    """AC-006（内层 finally 面）：EDTalk 分支异常逃逸 → 不压低且状态清洁
    （受理点语义：异常=播报未出声，本就不应压低；兜底恢复防线保持）。"""
    host = _LoopHost()
    writes = []
    orig_set = PlaybackMixin._set_backing_duck

    def rec_set(self, factor):
        writes.append(float(factor))
        orig_set(self, factor)

    monkeypatch.setattr(PlaybackMixin, "_set_backing_duck", rec_set)

    async def boom(self, data_json, path):
        raise RuntimeError("injected")

    monkeypatch.setattr(PlaybackMixin, "_consume_edtalk_entry", boom)
    monkeypatch.setattr(PlaybackMixin, "_entry_duration_s",
                        lambda self, e: 0.15)
    host._song_anchor = {"t0": time.monotonic() - 5.0, "last_end": 0.0}
    host.enqueue({"type": "comment", "voice_path": "x.wav", "content": "播报"})
    host.enqueue(_seg(seq=1, start_s=15.0))
    driver = _LoopDriver(host)
    assert driver.wait_until(lambda: writes, timeout=3.0), "异常兜底未触发"
    time.sleep(0.3)
    assert 0.5 not in writes, f"异常路径出现压低（AC-006）：{writes}"
    assert host.duck() == 1.0


def test_missing_voice_path_outer_except_restores(monkeypatch):
    """AC-006（外层 except 面）：出队后、分支 try 前的异常逃逸（voice_path
    缺失 KeyError）→ 外层 except 兜底，全程不压低。"""
    host = _LoopHost()
    writes = []
    orig_set = PlaybackMixin._set_backing_duck

    def rec_set(self, factor):
        writes.append(float(factor))
        orig_set(self, factor)

    monkeypatch.setattr(PlaybackMixin, "_set_backing_duck", rec_set)
    monkeypatch.setattr(PlaybackMixin, "_entry_duration_s",
                        lambda self, e: 0.15)
    host._song_anchor = {"t0": time.monotonic() - 5.0, "last_end": 0.0}
    host.enqueue({"type": "comment", "content": "缺 voice_path"})
    host.enqueue(_seg(seq=1, start_s=15.0))
    driver = _LoopDriver(host)
    assert driver.wait_until(lambda: writes, timeout=3.0), \
        "外层 except 兜底未触发（条目出队后 KeyError 应被捕获并清状态）"
    time.sleep(0.3)
    assert 0.5 not in writes, f"外层兜底路径出现压低（AC-006）：{writes}"
    assert host.duck() == 1.0


def test_prelude_borrow_ducks(monkeypatch, tmp_path):
    """AC-001/002（前奏面）：seq0 前奏等待期间 R5c 调度循环借位插播——
    **受理（出声）**时压伴奏（_start_backing_player 产物原语），播完恢复；
    出队/递归消费（飞行等待）期不压（song6.log 语义对齐）。"""
    backing_calls = []
    started_ev = threading.Event()
    started_ev.set()

    def fake_start(self, path, vol):
        backing_calls.append(path)
        return (threading.Event(), started_ev, lambda: time.monotonic(),
                threading.Event(), None, True)

    monkeypatch.setattr(PlaybackMixin, "_start_backing_player", fake_start)
    monkeypatch.setattr(PlaybackMixin, "_entry_duration_s",
                        lambda self, e: 0.15)
    host = _LoopHost()
    (tmp_path / "inst.wav").write_bytes(b"fake")
    duck_at_push = []
    writes = []
    orig_push = host.edtalk_client.push_full
    orig_set = PlaybackMixin._set_backing_duck

    async def rec_push(path, content="", wait_flight=True):
        duck_at_push.append((time.monotonic(), host.duck()))
        return await orig_push(path, content=content, wait_flight=wait_flight)

    def rec_set(self, factor):
        writes.append((time.monotonic(), float(factor)))
        orig_set(self, factor)

    host.edtalk_client.push_full = rec_push
    monkeypatch.setattr(PlaybackMixin, "_set_backing_duck", rec_set)
    host.enqueue({"type": "comment", "voice_path": "b.wav", "content": "前奏插播"})
    entry = {"type": "song", "voice_path": "seg.wav", "content": "s",
             "backing_path": str(tmp_path / "inst.wav"),
             "backing_volume": 0.8,
             "song_segment": {"seq": 0, "total": 2, "seg_dir": "",
                              "start_s": 0.6}}
    asyncio.run(host._consume_edtalk_entry(entry, "seg.wav"))
    assert backing_calls, "伴奏编排未被调用（前奏场景前提丢失）"
    assert duck_at_push and duck_at_push[0][1] == 1.0, \
        f"借位播报受理前（递归消费中）伴奏即被压低：{duck_at_push}"
    assert writes and writes[0][1] == 0.5, f"受理点未压低：{writes}"
    assert writes[0][0] >= duck_at_push[0][0], "压低早于播报受理（应恰在出声点）"
    deadline = time.monotonic() + 2.5
    while time.monotonic() < deadline and host.duck() != 1.0:
        time.sleep(0.02)
    assert host.duck() == 1.0, "前奏借位播完后伴奏未恢复（AC-002）"
