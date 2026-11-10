"""点歌双轨失步根修守卫（2026-11-09 /investigate）。

根因：T5 语义切片 gap_after_s/start_s 元数据透传后消费侧零消费——
①段间无歌曲时间轴窗口门（间奏被背靠背推送跳过）；
②段间无播完保底（wait_flight=False 下段间 push 互踩 flush 截断）；
③前奏静音（vocal 开头静音段）未等待——人声开场即超前伴奏。

修复面：切片条目 start_s（歌曲时间轴绝对起点）→ song_segment 透传；
消费侧 _song_anchor（t0=伴奏起播/首段受理）+ _song_gate_wait_s 窗口门
（due=t0+start_s−推送预补偿，与播完保底 last_end 取大）+ 前奏对齐
（seq0 start_s>0 伴奏门提前放行+人声等待）+ 末段清锚。

覆盖（对照 T5/T7 既有守卫语义不回归）：
- start_s 三态（语义切片真实起点/固定切片等长推进/song_segment 透传）
- _song_gate_wait_s 判定矩阵（八态）
- 前奏对齐时序（seq0：伴奏门先放行→等待→push；锚建立；t0 来自伴奏回执）
- 锚更新（start_s=0 首段 t0 受理回填/last_end 播完保底/末段清锚）
"""
import asyncio
import os
import time

import pytest

from tests.test_choreo_t5_silences import _FakeConfig, _SynFixture, fake_pydub  # noqa: F401

import utils.audio.vocal_silences as vs


# ------------------------------------------------- start_s 透传三态

def test_start_s_semantic_slices_real_offsets(tmp_path, fake_pydub):
    """语义切片条目 start_s=段在歌曲时间轴的真实起点（前奏静音→首段>0）。"""
    vocal = tmp_path / "a__p1_vocal.wav"
    vocal.write_bytes(b"fake")
    (tmp_path / "a__p1.wav").write_bytes(b"fake-mix")
    sp = tmp_path / "a__p1.silences.json"
    vs.write_silences_file(str(sp), [{"start_s": 1.0, "end_s": 2.0}])
    fx = _SynFixture(seg=45)
    segs, _ = fx._semantic_or_fixed_slices(
        str(vocal), {"full_path": str(tmp_path / "a__p1.wav")})
    assert segs[0]["start_s"] == 0.0
    assert segs[1]["start_s"] == 2.0  # 静音段 [1,2] 后的人声段起点


def test_start_s_fixed_slices_equal_steps(tmp_path, fake_pydub):
    """固定切片回退条目 start_s=seq×seg_seconds（等长推进，段间窗口门同样生效）。"""
    from pydub import AudioSegment
    fake_pydub["seg"] = AudioSegment.silent(duration=100000)  # 100s → 3 段×45s
    vocal = tmp_path / "b__p1_vocal.wav"
    vocal.write_bytes(b"fake")
    (tmp_path / "b__p1.wav").write_bytes(b"fake-mix")
    fx = _SynFixture(seg=45)
    segs = fx._split_audio_segments(str(vocal), 45)
    assert len(segs) == 3
    assert [s["start_s"] for s in segs] == [0.0, 45.0, 90.0]


def test_song_segment_carries_start_s(tmp_path, fake_pydub, monkeypatch):
    """data_json 组装：start_s 进 song_segment 元数据（消费侧窗口门锚定字段）。"""
    import utils.audio.synthesis_manager as sm
    vocal = tmp_path / "c__p1_vocal.wav"
    vocal.write_bytes(b"fake")
    (tmp_path / "c__p1.wav").write_bytes(b"fake-mix")
    sp = tmp_path / "c__p1.silences.json"
    vs.write_silences_file(str(sp), [{"start_s": 1.0, "end_s": 2.0}])
    fx = _SynFixture(seg=45)
    monkeypatch.setattr(sm, "is_edtalk_active", lambda cfg: True)
    fx.audio_synthesis({"type": "song", "username": "u", "content": str(vocal),
                        "voice_path": str(vocal), "backing_path": "",
                        "full_path": str(tmp_path / "c__p1.wav")})
    song_entries = [e for e in fx.captured if e.get("type") == "song"]
    assert len(song_entries) == 2
    assert song_entries[0]["song_segment"]["start_s"] == 0.0
    assert song_entries[1]["song_segment"]["start_s"] == 2.0


# ------------------------------------------------- _song_gate_wait_s 判定矩阵

def _gate_pb():
    from utils.audio.playback_manager import PlaybackMixin

    class _Impl(PlaybackMixin):
        pass

    pb = _Impl()
    pb.config = _FakeConfig(seg=45)
    return pb


def _seg_entry(seq=1, total=3, start_s=10.0):
    return {"type": "song", "voice_path": "x", "content": "s",
            "song_segment": {"seq": seq, "total": total, "seg_dir": "",
                             "start_s": start_s}}


def test_gate_non_song_and_first_segment_pass():
    """非 song/无 song_segment/首段 seq=0 恒 0（首段受理即零点，门不设）。"""
    pb = _gate_pb()
    assert pb._song_gate_wait_s({"type": "comment", "voice_path": "x"}) == 0.0
    assert pb._song_gate_wait_s({"type": "song", "voice_path": "x"}) == 0.0
    assert pb._song_gate_wait_s(_seg_entry(seq=0)) == 0.0


def test_gate_no_anchor_fail_open():
    """无锚/t0 未回填（异常态）fail-open=0——窗口门不阻塞异常序列。"""
    pb = _gate_pb()
    assert pb._song_gate_wait_s(_seg_entry()) == 0.0  # 无锚
    pb._song_anchor = {"t0": None, "last_end": 0.0}
    assert pb._song_gate_wait_s(_seg_entry()) == 0.0  # 首段未受理（t0 None）


def test_gate_waits_until_due():
    """due=t0+start_s−推送预补偿：未到→剩余>0；已过→0。"""
    from utils.audio.playback_manager import _SONG_PUSH_LEAD
    pb = _gate_pb()
    t0 = time.monotonic()
    pb._song_anchor = {"t0": t0, "last_end": 0.0}
    due = t0 + 10.0 - _SONG_PUSH_LEAD
    wait = pb._song_gate_wait_s(_seg_entry(start_s=10.0))
    assert 0 < wait <= due - t0 + 0.05
    pb._song_anchor = {"t0": t0 - 20.0, "last_end": 0.0}
    assert pb._song_gate_wait_s(_seg_entry(start_s=10.0)) == 0.0  # 已过期


def test_gate_playthrough_floor_wins_over_due():
    """播完保底 last_end>due：宁可人声延后（e13 方向）不 flush 截断上一段。"""
    pb = _gate_pb()
    t0 = time.monotonic() - 100.0  # due 远在过去的段
    pb._song_anchor = {"t0": t0, "last_end": time.monotonic() + 2.0}
    wait = pb._song_gate_wait_s(_seg_entry(start_s=10.0))
    assert 1.5 < wait <= 2.0  # 保底主导（下段等上一段播完）


# ------------------------------------------------- 消费函数：前奏+锚

class _PushRecorder:
    def __init__(self, duration=2.0):
        self.calls = []
        self.last_duration_s = duration

    async def push_full(self, path, content="", wait_flight=True):
        self.calls.append({"path": os.path.basename(path), "content": content,
                           "wait_flight": wait_flight})
        return True


def _consume_pb(tmp_path, duration=2.0):
    from utils.audio.playback_manager import PlaybackMixin
    from utils.audio.queue_manager import QueueMixin

    class _Impl(QueueMixin, PlaybackMixin):
        pass

    pb = _Impl()
    pb.config = _FakeConfig(seg=45)
    pb.edtalk_client = _PushRecorder(duration)
    wav = tmp_path / "seg00.mp3"
    wav.write_bytes(b"fake")
    return pb, str(wav)


def test_consume_seq0_prelude_backing_gate_first(monkeypatch, tmp_path):
    """前奏对齐时序（seq0 start_s>0）：伴奏门提前放行→真实起播回执为 t0→
    人声等待前奏后推送；锚建立+push wait_flight=False 保持（d3 flush 答谢）。"""
    import threading as _th
    import utils.audio.playback_manager as pm

    backing_calls = []
    box = {"t": 0.0}
    started = _th.Event()

    def fake_backing(path, vol, start_event=None, started_event=None,
                     started_box=None):
        backing_calls.append({"gate_wait": start_event is not None})
        if started_box is not None:
            started_box["t"] = time.monotonic()  # 伴奏真实起播时刻
        if started_event is not None:
            started_event.set()
        if start_event is not None:
            start_event.wait(timeout=5)  # 模拟线程侧门等待
        backing_calls.append({"gate_released": bool(
            start_event is None or start_event.is_set())})

    monkeypatch.setattr(pm, "_play_backing_local", fake_backing)
    pb, wav = _consume_pb(tmp_path)
    entry = {"type": "song", "voice_path": wav, "content": "s",
             "backing_path": str(tmp_path / "inst.wav"),
             "backing_volume": 0.8,
             "song_segment": {"seq": 0, "total": 2, "seg_dir": "",
                              "start_s": 0.2}}
    (tmp_path / "inst.wav").write_bytes(b"fake")
    asyncio.run(pb._consume_edtalk_entry(entry, wav))
    assert len(backing_calls) == 2  # 伴奏线程被启动并回执
    assert pb.edtalk_client.calls[0]["wait_flight"] is False  # d3 语义保持
    # 锚：t0=伴奏起播回执（前奏对齐零点）；push 后 last_end=受理+时长×margin
    #（seq0 非末段，锚存活）
    assert pb._song_anchor is not None
    assert pb._song_anchor["t0"] is not None
    assert pb._song_anchor["last_end"] > 0


def test_consume_seq0_no_prelude_t0_backfilled_after_push(tmp_path):
    """start_s=0 首段：原语义（门随 push 受理放行），t0=受理时刻回填。"""
    pb, wav = _consume_pb(tmp_path)
    entry = {"type": "song", "voice_path": wav, "content": "s",
             "song_segment": {"seq": 0, "total": 3, "seg_dir": "",
                              "start_s": 0.0}}
    asyncio.run(pb._consume_edtalk_entry(entry, wav))
    anchor = pb._song_anchor
    assert anchor is not None
    assert anchor["t0"] is not None  # push 后回填
    from utils.audio.playback_manager import _SONG_SEG_END_MARGIN
    assert anchor["last_end"] == pytest.approx(
        anchor["t0"] + 2.0 * _SONG_SEG_END_MARGIN, abs=0.5)  # 时长 2s stub


def test_consume_last_segment_clears_anchor(tmp_path):
    """末段受理完成清锚（序列收尾；残留锚不跨歌曲）。"""
    pb, wav = _consume_pb(tmp_path)
    entry = {"type": "song", "voice_path": wav, "content": "s",
             "song_segment": {"seq": 2, "total": 3, "seg_dir": "",
                              "start_s": 90.0}}
    pb._song_anchor = {"t0": time.monotonic() - 100.0, "last_end": 0.0}
    asyncio.run(pb._consume_edtalk_entry(entry, wav))
    assert getattr(pb, "_song_anchor", None) is None


def test_consume_mid_segment_updates_playthrough_floor(tmp_path):
    """中段受理：last_end=受理+段时长×margin（窗口门播完保底数据源）。"""
    pb, wav = _consume_pb(tmp_path, duration=4.0)
    entry = {"type": "song", "voice_path": wav, "content": "s",
             "song_segment": {"seq": 1, "total": 3, "seg_dir": "",
                              "start_s": 30.0}}
    t0 = time.monotonic() - 10.0
    pb._song_anchor = {"t0": t0, "last_end": 0.0}
    asyncio.run(pb._consume_edtalk_entry(entry, wav))
    from utils.audio.playback_manager import _SONG_SEG_END_MARGIN
    assert pb._song_anchor["last_end"] == pytest.approx(
        time.monotonic() + 4.0 * _SONG_SEG_END_MARGIN, abs=1.0)


def test_fixed_slice_entry_gate_zero_when_no_anchor(tmp_path):
    """固定切片条目（start_s 透传）在无锚时窗口门 fail-open 直通（回归面）。"""
    pb = _gate_pb()
    entry = _seg_entry(seq=1, start_s=45.0)
    assert pb._song_gate_wait_s(entry) == 0.0


# ------------------------------------------------- 行为级时序证明

def test_behavior_interlude_wait_before_push(tmp_path):
    """行为级修复证明：seq1 推送发生在间奏窗口耗尽后（等待≈start_s−预补偿）——
    修复前 seq1 背靠背立即推送（人声跳过间奏超前伴奏），修复后按歌曲时间轴
    推进（间奏期间伴奏走/答谢可插播）。"""
    from utils.audio.playback_manager import _SONG_PUSH_LEAD

    pb, wav = _consume_pb(tmp_path, duration=0.5)
    pb._song_anchor = {"t0": time.monotonic(), "last_end": 0.0}
    entry = {"type": "song", "voice_path": wav, "content": "s",
             "song_segment": {"seq": 1, "total": 3, "seg_dir": "",
                              "start_s": 1.0}}  # 间奏 1s
    pushed_at = []
    _orig = pb.edtalk_client.push_full

    async def rec(path, **kw):
        pushed_at.append(time.monotonic())
        return await _orig(path, **kw)

    pb.edtalk_client.push_full = rec

    async def driver():
        # 复刻 only_play_audio 出队门行为（等待→到期→消费）
        while True:
            w = pb._song_gate_wait_s(entry)
            if w <= 0:
                break
            await asyncio.sleep(min(w, 0.05))
        await pb.edtalk_client.push_full(entry["voice_path"], wait_flight=False)

    t_start = time.monotonic()
    asyncio.run(driver())
    waited = pushed_at[0] - t_start
    assert waited >= 1.0 - _SONG_PUSH_LEAD - 0.15  # 间奏等待生效（≈0.55s+）
    assert waited <= 2.0  # 无悬挂
