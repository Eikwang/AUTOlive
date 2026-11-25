# -*- coding: utf-8 -*-
"""歌曲起播剩余时长时间轴口径守卫（2026-11-24 画面漂移根修）。

用户实测（log3.txt 17:12:39→51）：旧口径 (total−seq)×seg_seconds 等长近似，
语义切片（不等长人声段+间奏段）系统性低估——seg0 起播估 90s/真实 ~147s，
间奏中 song hold 与挂起文案 deadline 同时到期 → 画面回默认，下一人声段起播
才切回。用户口径：唱歌时间内消费任何消息都保持点歌画面。

守卫口径：remaining=队内同歌末片(start_s+真实时长)−当前片 start_s+2s 余量
（start_s 为歌曲时间轴坐标，天然含前序间奏）；当前即末片=当前片真实时长；
整轨直通=整文件时长；异歌切片（seg_dir 不同）不参与计算。
"""
import threading

import pytest
import soundfile as sf
import numpy as np

from utils.audio.playback_manager import PlaybackMixin


class _Host(PlaybackMixin):
    """_song_choreo_remaining_s 最小宿主（队列+锁）。"""

    def __init__(self, queue=None):
        self.voice_tmp_path_queue = queue or []
        self.voice_tmp_path_queue_lock = threading.Lock()


def _wav(tmp_path, name, seconds):
    """写真实可测 wav（soundfile 守卫面：口径依赖真实文件时长）。"""
    path = str(tmp_path / name)
    sf.write(path, np.zeros(int(44100 * seconds), dtype=np.float32), 44100)
    return path


def _slice_entry(seq, total, start_s, voice_path, seg_dir="d1"):
    return {"type": "song", "voice_path": voice_path,
            "content": "牧马城市",
            "song_segment": {"seq": seq, "total": total, "seg_dir": seg_dir,
                             "start_s": start_s}}


def test_remaining_covers_interlude_to_song_end(tmp_path):
    """核心场景（log3 复现口径）：seg0(2s) + 间奏(隐含在 seg1.start_s=102s)
    + seg1(3s) → seg0 起播剩余=102+3−0+2=107s（旧等长口径=90s 低估）。"""
    host = _Host()
    seg0 = _wav(tmp_path, "seg_00.wav", 2.0)
    seg1 = _wav(tmp_path, "seg_01.wav", 3.0)
    host.voice_tmp_path_queue = [_slice_entry(1, 2, 102.0, seg1)]
    remaining = host._song_choreo_remaining_s(
        _slice_entry(0, 2, 0.0, seg0), seg0)
    assert remaining == pytest.approx(107.0, abs=0.1)


def test_remaining_last_slice_in_queue_wins(tmp_path):
    """多后继切片取最晚片（start_s 最大者），不被中间片截短。"""
    host = _Host()
    seg0 = _wav(tmp_path, "seg_00.wav", 2.0)
    seg_mid = _wav(tmp_path, "seg_01.wav", 3.0)
    seg_last = _wav(tmp_path, "seg_02.wav", 4.0)
    host.voice_tmp_path_queue = [
        _slice_entry(1, 3, 30.0, seg_mid),
        _slice_entry(2, 3, 60.0, seg_last),
    ]
    remaining = host._song_choreo_remaining_s(
        _slice_entry(0, 3, 0.0, seg0), seg0)
    assert remaining == pytest.approx(66.0, abs=0.1)


def test_remaining_current_is_last_slice(tmp_path):
    """当前即末片（队列无同歌后继片）→ 当前片真实时长+2s。"""
    host = _Host()
    seg_last = _wav(tmp_path, "seg_01.wav", 5.0)
    remaining = host._song_choreo_remaining_s(
        _slice_entry(1, 2, 102.0, seg_last), seg_last)
    assert remaining == pytest.approx(7.0, abs=0.1)


def test_remaining_ignores_other_song_slices(tmp_path):
    """异歌切片（seg_dir 不同）不参与 song_total 计算——退当前片时长。"""
    host = _Host()
    cur = _wav(tmp_path, "seg_00.wav", 2.5)
    other = _wav(tmp_path, "other_00.wav", 90.0)
    host.voice_tmp_path_queue = [_slice_entry(0, 5, 0.0, other, seg_dir="d2")]
    remaining = host._song_choreo_remaining_s(
        _slice_entry(0, 2, 0.0, cur), cur)
    assert remaining == pytest.approx(4.5, abs=0.1)


def test_remaining_whole_track_entry(tmp_path):
    """整轨直通（无 song_segment）→ 整文件时长（不乘余量）。"""
    host = _Host()
    whole = _wav(tmp_path, "whole.wav", 6.0)
    entry = {"type": "song", "voice_path": whole, "content": "牧马城市"}
    assert host._song_choreo_remaining_s(entry, whole) == \
        pytest.approx(6.0, abs=0.1)


def test_remaining_unmeasurable_returns_none(tmp_path):
    """全程不可测（文件缺失）→ None（调用方幂等忽略，不误延 deadline）。"""
    host = _Host()
    entry = _slice_entry(0, 2, 0.0, str(tmp_path / "missing.wav"))
    assert host._song_choreo_remaining_s(entry, str(tmp_path / "missing.wav")) \
        is None


def test_remaining_later_slice_file_missing_falls_back(tmp_path):
    """末片文件不可测（已被清理）→ 退当前片真实时长+2s（自校正兜底）。"""
    host = _Host()
    cur = _wav(tmp_path, "seg_00.wav", 3.0)
    host.voice_tmp_path_queue = [
        _slice_entry(1, 2, 102.0, str(tmp_path / "gone.wav"))]
    remaining = host._song_choreo_remaining_s(
        _slice_entry(0, 2, 0.0, cur), cur)
    assert remaining == pytest.approx(5.0, abs=0.1)
