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
    import threading as _th

    class _Impl(PlaybackMixin):
        pass

    pb = _Impl()
    pb.config = _FakeConfig(seg=45)
    # R3 出队选择守卫需要队列锁/条件变量（QueueMixin.__init__ 产物，stub 手动补）
    pb.voice_tmp_path_queue_lock = _th.RLock()
    pb.voice_tmp_path_queue_not_empty = _th.Condition(pb.voice_tmp_path_queue_lock)
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
    人声等待前奏后推送；锚建立+push wait_flight=False 保持（d3 flush 答谢）。
    2026-11-10 R2：monkeypatch 编排层 _start_backing_player（原语形态由编排
    决定——主进程侧行为契约=门放行/回执读取/锚建立）。"""
    import utils.audio.playback_manager as pm

    backing_calls = []
    gate_ev = type("G", (), {"set": staticmethod(lambda: None),
                             "is_set": staticmethod(lambda: True)})()
    started_ev = type("S", (), {"set": staticmethod(lambda: None),
                                "wait": staticmethod(lambda timeout=None: True),
                                "is_set": staticmethod(lambda: True)})()

    def fake_start(self, path, vol):
        backing_calls.append({"path": path, "vol": vol})
        import time as _t
        return (gate_ev, started_ev, lambda: _t.monotonic(),
                type("X", (), {"set": staticmethod(lambda: None)})(), None, True)

    monkeypatch.setattr(pm.PlaybackMixin, "_start_backing_player", fake_start)
    pb, wav = _consume_pb(tmp_path)
    entry = {"type": "song", "voice_path": wav, "content": "s",
             "backing_path": str(tmp_path / "inst.wav"),
             "backing_volume": 0.8,
             "song_segment": {"seq": 0, "total": 2, "seg_dir": "",
                              "start_s": 0.2}}
    (tmp_path / "inst.wav").write_bytes(b"fake")
    asyncio.run(pb._consume_edtalk_entry(entry, wav))
    assert len(backing_calls) == 1  # 编排层被调用（音量/路径透传）
    assert pb.edtalk_client.calls[0]["wait_flight"] is False  # d3 语义保持
    # 锚：t0=起播回执（前奏对齐零点）；push 后 last_end=受理+时长×margin
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
    from utils.audio.playback_manager import _SONG_PREROLL_S
    assert anchor["last_end"] == pytest.approx(
        anchor["t0"] + _SONG_PREROLL_S + 2.0, abs=0.5)  # R4f 加法：受理+preroll+段长


def test_consume_last_segment_clears_anchor(tmp_path):
    """末段受理完成清锚（序列收尾；残留锚不跨歌曲）。"""
    pb, wav = _consume_pb(tmp_path)
    entry = {"type": "song", "voice_path": wav, "content": "s",
             "song_segment": {"seq": 2, "total": 3, "seg_dir": "",
                              "start_s": 90.0}}
    pb._song_anchor = {"t0": time.monotonic() - 100.0, "last_end": 0.0}
    asyncio.run(pb._consume_edtalk_entry(entry, wav))
    # R4 垃圾时间：末段受理置 wrapping（人声播完伴奏未完=自由消费），不再清锚
    anchor = getattr(pb, "_song_anchor", None)
    assert anchor is not None and anchor.get("wrapping") is True


def test_consume_mid_segment_updates_playthrough_floor(tmp_path):
    """中段受理：last_end=受理+段时长×margin（窗口门播完保底数据源）。"""
    pb, wav = _consume_pb(tmp_path, duration=4.0)
    entry = {"type": "song", "voice_path": wav, "content": "s",
             "song_segment": {"seq": 1, "total": 3, "seg_dir": "",
                              "start_s": 30.0}}
    t0 = time.monotonic() - 10.0
    pb._song_anchor = {"t0": t0, "last_end": 0.0}
    asyncio.run(pb._consume_edtalk_entry(entry, wav))
    from utils.audio.playback_manager import _SONG_PREROLL_S
    assert pb._song_anchor["last_end"] == pytest.approx(
        time.monotonic() + _SONG_PREROLL_S + 4.0, abs=1.0)  # R4f 加法化


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


# ------------------------------------------------- R3 到期优先（2026-11-10）

def _pop_pb(entries, anchor):
    """构造带队列与锚的 pb（_pop_next_entry_locked 单元）。"""
    pb = _gate_pb()
    pb.voice_tmp_path_queue = list(entries)
    pb._song_anchor = dict(anchor)
    if anchor.get("t0") is not None:
        pb._song_anchor["t0"] = anchor["t0"] - 100.0 if anchor.get("overdue") \
            else anchor["t0"]
    return pb


def test_overdue_song_segment_jumps_ahead_of_thanks():
    """到期优先：due 已到的 song 段跳过其前答谢 pop(i)（答谢留队不丢，
    skip_count+1）——人声不饿死（R3 主链）。"""
    import time as _t
    pb = _gate_pb()
    pb.voice_tmp_path_queue = [
        {"type": "comment", "voice_path": "a", "content": "thanks1"},
        {"type": "comment", "voice_path": "b", "content": "thanks2"},
        {"type": "song", "voice_path": "s1", "content": "s",
         "song_segment": {"seq": 1, "total": 3, "seg_dir": "", "start_s": 10.0}},
    ]
    pb._song_anchor = {"t0": _t.monotonic() - 100.0, "last_end": 0.0}
    with pb.voice_tmp_path_queue_lock:
        data, gw = pb._pop_next_entry_locked()
    assert gw == 0 and data["type"] == "song"  # song 段被跳前消费
    assert pb.voice_tmp_path_queue[0]["content"] == "thanks1"  # 答谢留队原序
    assert pb._song_anchor["skip_count"] == 1


def test_interlude_budget_blocks_thanks_near_window_end():
    """窗口预算预检（R4/D14 退役跳过上限）：间奏剩余<3s 不起答谢（等到期推
    人声）——用户硬性条件「不足 3 秒时无需消费音频，避免说一半」。"""
    import time as _t
    pb = _gate_pb()
    # 窗口：last_end=now，下一 song 段 due=now+4s（剩余 4s≥3s 但答谢 10s 超窗）
    pb.voice_tmp_path_queue = [
        {"type": "comment", "voice_path": "nonexistent.wav", "content": "thanks"},
        {"type": "song", "voice_path": "s1", "content": "s",
         "song_segment": {"seq": 1, "total": 3, "seg_dir": "", "start_s": 10.0}},
    ]
    pb._song_anchor = {"t0": _t.monotonic() - 8.0, "last_end": 0.0}
    # due=t0+10−0.3=now+1.7；last_end=now+2 → 窗口结束=max=now+2 → 剩余
    # 2s<3s → 硬性条件拦截（不起答谢，等到期推人声）
    pb._song_anchor["last_end"] = _t.monotonic() + 2.0
    with pb.voice_tmp_path_queue_lock:
        data, gw = pb._pop_next_entry_locked()
    assert data is None  # 剩余 2s<3s → 不起答谢（返回等待）
    assert gw > 0


def test_interlude_budget_blocks_overlong_entry():
    """预算语义：答谢音频时长>剩余窗口 → 不起下一条（消费完剩 2s 不够下一条
    则无需消费——时长预检 pydub 读 voice_path，文件缺失=fail-soft 放行）。"""
    import time as _t
    from pydub import AudioSegment
    pb = _gate_pb()
    pb.voice_tmp_path_queue = [
        {"type": "comment", "voice_path": "long_thanks.wav", "content": "t"},
        {"type": "song", "voice_path": "s1", "content": "s",
         "song_segment": {"seq": 1, "total": 3, "seg_dir": "", "start_s": 10.0}},
    ]
    now = _t.monotonic()
    # t0=now−1.7（歌曲开始于过去）；seg01 start=10 → due=now+8；last_end=
    # now+8（间奏此刻开始）→ 窗口剩余 8s≥3s；答谢 10s>8s → 拦截
    pb._song_anchor = {"t0": now - 1.7, "last_end": now + 8.0}
    _orig = AudioSegment.from_file
    AudioSegment.from_file = classmethod(
        lambda cls, path, **k: AudioSegment.silent(duration=10000))
    try:
        with pb.voice_tmp_path_queue_lock:
            data, gw = pb._pop_next_entry_locked()
    finally:
        AudioSegment.from_file = _orig
    assert data is None and gw > 0  # 超长答谢被拦截


def test_overdue_wrapping_free_consume_and_new_song_exit():
    """垃圾时间（R4）：末段受理后 wrapping=True → 非新歌全直通；队列出现
    新 song 切片段 → 退出垃圾时间走正常机制（新点歌起新唱歌时间）。"""
    import time as _t
    pb = _gate_pb()
    pb.voice_tmp_path_queue = [
        {"type": "comment", "voice_path": "a", "content": "free1"},
        {"type": "comment", "voice_path": "b", "content": "free2"},
    ]
    pb._song_anchor = {"t0": _t.monotonic() - 100.0, "last_end": 0.0,
                       "wrapping": True}
    # 无伴奏 proc（getattr 默认 None→保守 alive=True）+无新歌 → 直通
    with pb.voice_tmp_path_queue_lock:
        data, gw = pb._pop_next_entry_locked()
    assert data["content"] == "free1" and gw == 0
    # 新歌 seq0 出现 → 穿透（wrapping 关闭）
    pb.voice_tmp_path_queue.insert(0, {"type": "song", "voice_path": "s0",
                                       "content": "new",
                                       "song_segment": {"seq": 0, "total": 2,
                                                        "seg_dir": "",
                                                        "start_s": 0.0}})
    with pb.voice_tmp_path_queue_lock:
        data, gw = pb._pop_next_entry_locked()
    assert data["content"] == "new"  # 新歌段直通（seq0=重建锚路径）
    assert pb._song_anchor.get("wrapping") is False


def test_overdue_resets_on_no_skip_consumption():
    """song 段已在队首（i==0 无跳过）→ 正常消费+限流复位。"""
    import time as _t
    pb = _gate_pb()
    pb.voice_tmp_path_queue = [
        {"type": "song", "voice_path": "s1", "content": "s",
         "song_segment": {"seq": 1, "total": 3, "seg_dir": "", "start_s": 10.0}},
    ]
    pb._song_anchor = {"t0": _t.monotonic() - 100.0, "last_end": 0.0,
                       "skip_count": 2}
    with pb.voice_tmp_path_queue_lock:
        data, gw = pb._pop_next_entry_locked()
    assert data["type"] == "song" and pb._song_anchor["skip_count"] == 0


def test_overdue_999_in_skip_zone_exempts():
    """999 豁免：到期段的跳过区间含 999 条目 → 放弃本次到期优先（正常消费
    队首=999 优先级最高+让位机制与延后交互未定义从简豁免，F5/D23）。"""
    import time as _t
    pb = _gate_pb()
    class _C:
        @staticmethod
        def get(*keys):
            if len(keys) == 1:
                return {"comment": 20, "reread_top_priority": 999}.get(keys[0])
            if keys == ("filter", "priority_mapping"):
                return {"comment": 20, "reread_top_priority": 999}
            return None
    pb.config = _C()
    pb.voice_tmp_path_queue = [
        {"type": "reread_top_priority", "voice_path": "r", "content": "999"},
        {"type": "song", "voice_path": "s1", "content": "s",
         "song_segment": {"seq": 1, "total": 3, "seg_dir": "", "start_s": 10.0}},
    ]
    pb._song_anchor = {"t0": _t.monotonic() - 100.0, "last_end": 0.0}
    with pb.voice_tmp_path_queue_lock:
        data, gw = pb._pop_next_entry_locked()
    assert data["type"] == "reread_top_priority"  # 999 被正常消费（未延后）
    assert pb.voice_tmp_path_queue[0]["type"] == "song"  # song 段留队
    assert pb._song_anchor.get("skip_count", 0) == 0


def test_overdue_no_anchor_or_not_due_fail_open():
    """无锚/due 未到 → fail-open 直通（无锚 pop(0)；未到期→窗口门等待秒）。"""
    import time as _t
    pb = _gate_pb()
    entry = _seg_entry(seq=1, start_s=100.0)
    pb.voice_tmp_path_queue = [{"type": "comment", "voice_path": "a",
                                "content": "x"}, entry]
    # 无锚：直通 pop(0)（答谢优先）
    with pb.voice_tmp_path_queue_lock:
        data, gw = pb._pop_next_entry_locked()
    assert gw == 0 and data["type"] == "comment"
    # 有锚但 due 未到：队首 song 段窗口等待
    pb.voice_tmp_path_queue = [entry]
    pb._song_anchor = {"t0": _t.monotonic() + 5.0, "last_end": 0.0}
    with pb.voice_tmp_path_queue_lock:
        data, gw = pb._pop_next_entry_locked()
    assert data is None and gw > 4.0  # 等待≈5-lead


# ------------------------------------------------- R1 过滤+钳制（2026-11-10）

def test_min_interlude_filter_and_config_clamp(tmp_path, fake_pydub):
    """间奏窗口过滤（消费端）：0.5s 静音不切/1.5s 切（min_interlude_s=1.0）；
    config 键钳制 [0.3, 10]（0→回缺省、60→钳 10——F7/D26）。"""
    from pydub import AudioSegment
    fake_pydub["seg"] = AudioSegment.silent(duration=5000)  # 5s 音频承载区间
    vocal = tmp_path / "d__p1_vocal.wav"
    vocal.write_bytes(b"fake")
    (tmp_path / "d__p1.wav").write_bytes(b"fake-mix")
    sp = tmp_path / "d__p1.silences.json"
    vs.write_silences_file(str(sp), [
        {"start_s": 1.0, "end_s": 1.5},    # 0.5s——短停顿（不切）
        {"start_s": 2.0, "end_s": 3.5},    # 1.5s——间奏（切）
    ])
    fx = _SynFixture(seg=45)
    segs, _ = fx._semantic_or_fixed_slices(
        str(vocal), {"full_path": str(tmp_path / "d__p1.wav")})
    # 过滤后仅 1.5s 段为间奏 → 人声段 2 段（[0,2.0]+[3.5,3.0?]——3.5>3s 总长钳尾）
    assert len(segs) == 2
    assert segs[1]["start_s"] == 3.5
    # 钳制：0→回缺省 1.0；60→钳 10
    fx.config._data[("choose_song", "min_interlude_s")] = 0
    assert fx._min_interlude_s() == 1.0
    fx.config._data[("choose_song", "min_interlude_s")] = 60
    assert fx._min_interlude_s() == 10.0
    fx.config._data[("choose_song", "min_interlude_s")] = 0.5
    assert fx._min_interlude_s() == 0.5


# ------------------------------------------------- 映射字符串值防御（实测修复）

def test_priority_mapping_string_values_no_typeerror():
    """映射表字符串值防御（2026-11-10 实测：用户 config song="80" 等 10 个
    字符串值 → data_priority_insert int>=str TypeError 中断点歌入队整链）。
    get_priority_level 经 audio_priority 单一源数值化：数值字符串→int，
    混合映射（int+str）排序比较正常，非数值→None 落队尾。"""
    from utils.audio.queue_manager import QueueMixin

    class _Impl(QueueMixin):
        pass

    pb = _Impl()
    # 用户实测形态：song="80"（str）+comment=50（int）混合
    def _cfg_get(*keys):
        if keys == ("filter", "priority_mapping"):
            return {"song": "80", "comment": 50, "bad_type": "not_a_number"}
        if keys == ("filter", "message_queue_max_len"):
            return 50
        return None

    pb.config = type("C", (), {"get": staticmethod(_cfg_get)})()
    pb.message_queue = [
        {"type": "comment", "voice_path": "c", "content": "c1"},  # int 50
    ]
    import threading as _th
    pb.message_queue_lock = _th.RLock()
    pb.message_queue_not_empty = _th.Condition(pb.message_queue_lock)
    pb._cancelled_choreo = set()
    # song（str "80"→80>50）入队：修复前 int>=str TypeError；修复后正常插队首
    pb.data_priority_insert("等待合成消息", {
        "type": "song", "voice_path": "s", "content": "s1"})
    assert pb.message_queue[0]["type"] == "song"  # 80>50 插到 comment 前
    # 非数值映射 → None 落队尾（不炸）
    pb.data_priority_insert("等待合成消息", {
        "type": "bad_type", "voice_path": "b", "content": "b1"})
    assert pb.message_queue[-1]["type"] == "bad_type"


def test_overlong_segment_bisected(tmp_path, fake_pydub):
    """段长兜底（R4b）：换气碎静音并入产生超长段（实测 74.3s）→ 按
    song_segment_seconds 二分（45s 上限形态回归，段内自然停顿保留）。"""
    from pydub import AudioSegment
    fake_pydub["seg"] = AudioSegment.silent(duration=80000)  # 80s 音频
    vocal = tmp_path / "e2__p1_vocal.wav"
    vocal.write_bytes(b"fake")
    (tmp_path / "e2__p1.wav").write_bytes(b"fake-mix")
    sp = tmp_path / "e2__p1.silences.json"
    # 一个 74.3s「间奏」把人声切出 74.3s 超长段（前 3s+后 2.7s）
    vs.write_silences_file(str(sp), [{"start_s": 3.0, "end_s": 77.3}])
    fx = _SynFixture(seg=45)
    segs, _ = fx._semantic_or_fixed_slices(
        str(vocal), {"full_path": str(tmp_path / "e2__p1.wav")})
    # 人声段=[0,3]+[77.3,80]——无超长段；构造超长段改用两个短间奏夹长人声：
    vs.write_silences_file(str(sp), [{"start_s": 3.0, "end_s": 4.0},
                                     {"start_s": 50.0, "end_s": 51.0}])
    segs, _ = fx._semantic_or_fixed_slices(
        str(vocal), {"full_path": str(tmp_path / "e2__p1.wav")})
    # 人声段=[0,3]+[4,50]+[51,80]——中段 46s<60 不切；构造 74s 段：
    vs.write_silences_file(str(sp), [{"start_s": 3.0, "end_s": 4.0},
                                     {"start_s": 77.3, "end_s": 78.3}])
    segs, _ = fx._semantic_or_fixed_slices(
        str(vocal), {"full_path": str(tmp_path / "e2__p1.wav")})
    # 中段 [4, 77.3]=73.3s>45s → 二分为 45+28.3（+首尾两段=4 段）
    assert len(segs) == 4
    starts = [s["start_s"] for s in segs]
    assert starts == [0.0, 4.0, 49.0, 78.3]
    # 段起点经 song_segment 透传（消费侧窗口门锚定字段）
    assert all(s["start_s"] == st for s, st in zip(segs, starts))


# ------------------------------------------------- R4f 借位消费+满载豁免

def test_borrow_consume_during_window():
    """借位消费（R4f）：窗口未到期时队首歌段之后的待播音频先出队（不动歌
    段）——前奏/间奏期间低优先级音频（被插队规则压在歌段后）不再零消费。"""
    import time as _t
    pb = _gate_pb()
    pb.voice_tmp_path_queue = [
        {"type": "song", "voice_path": "s1", "content": "s",
         "song_segment": {"seq": 1, "total": 3, "seg_dir": "", "start_s": 60.0}},
        {"type": "entrance", "voice_path": "nonexistent.wav", "content": "hi"},
        {"type": "comment", "voice_path": "nonexistent.wav", "content": "thanks"},
    ]
    # 窗口：t0+60（未到期，剩 ~55s）
    pb._song_anchor = {"t0": _t.monotonic(), "last_end": 0.0}
    with pb.voice_tmp_path_queue_lock:
        data, gw = pb._pop_next_entry_locked()
    # 队首歌段未到期 → 借位消费其后的入场条目（时长未知 fail-open 放行）
    assert gw == 0 and data["type"] == "entrance"
    assert pb.voice_tmp_path_queue[0]["type"] == "song"  # 歌段留队未动


def test_borrow_budget_skips_overlong():
    """借位预算预检：条目时长已知且播完剩余 <3s → 跳过该条目看下一条；
    全部超窗 → 返回窗口等待。"""
    import time as _t
    from pydub import AudioSegment
    pb = _gate_pb()
    long_wav = None
    pb.voice_tmp_path_queue = [
        {"type": "song", "voice_path": "s1", "content": "s",
         "song_segment": {"seq": 1, "total": 3, "seg_dir": "", "start_s": 60.0}},
    ]
    pb._song_anchor = {"t0": _t.monotonic() + 55.0, "last_end": _t.monotonic()}
    # 窗口结束=last_end=now → remaining≈0<3s → 无可借位 → 等待
    with pb.voice_tmp_path_queue_lock:
        data, gw = pb._pop_next_entry_locked()
    assert data is None and gw >= 0


def test_song_slice_exempt_from_message_queue_full():
    """song 切片段豁免 message_queue 满载丢弃（R4f 实测：5 段切片第 5 段被
    丢弃=歌不完整）——满载仍插入，普通条目维持 drop-only 语义。"""
    from utils.audio.queue_manager import QueueMixin
    import threading as _th

    class _Impl(QueueMixin):
        pass

    pb = _Impl()

    def _cfg_get(*keys):
        if keys == ("filter", "priority_mapping"):
            return {"song": "80"}
        if keys == ("filter", "message_queue_max_len"):
            return 2  # 小上限逼出满载
        return None

    pb.config = type("C", (), {"get": staticmethod(_cfg_get)})()
    pb.message_queue = []
    pb.message_queue_lock = _th.RLock()
    pb.message_queue_not_empty = _th.Condition(pb.message_queue_lock)
    pb._cancelled_choreo = set()
    seg = lambda i: {"type": "song", "voice_path": f"s{i}", "content": "s",
                     "song_segment": {"seq": i, "total": 3, "seg_dir": "",
                                      "start_s": float(i)}}
    for i in range(4):  # 4 段入队（超 max_len=2）
        pb.data_priority_insert("等待合成消息", seg(i))
    song_count = sum(1 for e in pb.message_queue if e.get("type") == "song")
    assert song_count == 4  # 切片段全部保留（无丢弃）


def test_normal_entry_still_dropped_when_full():
    """普通条目满载 drop-only 语义不回归（song 豁免不扩大）。"""
    from utils.audio.queue_manager import QueueMixin
    import threading as _th

    class _Impl(QueueMixin):
        pass

    pb = _Impl()

    def _cfg_get(*keys):
        if keys == ("filter", "priority_mapping"):
            return {"song": "80", "talk": 30}
        if keys == ("filter", "message_queue_max_len"):
            return 1
        return None

    pb.config = type("C", (), {"get": staticmethod(_cfg_get)})()
    pb.message_queue = [{"type": "song", "voice_path": "s", "content": "s"}]
    pb.message_queue_lock = _th.RLock()
    pb.message_queue_not_empty = _th.Condition(pb.message_queue_lock)
    pb._cancelled_choreo = set()
    r = pb.data_priority_insert("等待合成消息", {"type": "talk", "voice_path": "t",
                                                "content": "t"})
    assert r["code"] == 1  # 低优+末尾位次 → 丢弃（既有语义）
    assert len(pb.message_queue) == 1
