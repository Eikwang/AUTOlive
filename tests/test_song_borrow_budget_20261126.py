"""前奏借位预算校验守卫（2026-11-26 人声对齐根修）。

根因：R5c 前奏借位 await 全程阻塞播完才返回，循环只在两次借位之间检查
due——超预算借位尾部越过 due，人声顺延（实测 ≤3s）与伴奏真实时间轴失步。
修复：_pick_intro_borrow_locked 借位前预算校验（est ≤ remain−余量 才 pop，
超限留队待间奏窗口/歌毕消费），t0 永不回退 → 人声与伴奏绝对对齐。

覆盖：
- 超预算条目留队（不 pop、队列原序）
- 预算内条目正常借位（边界：est=remain−余量 放行；略超不放行）
- 候选条件：song 切片段/动态文案分段跳过，普通条目命中
- 首候选超预算=整轮不借位（不改排出队顺序，R5c 修正口径）
"""
import threading

from utils.audio.playback_manager import PlaybackMixin


def _pb():
    class _Impl(PlaybackMixin):
        pass

    pb = _Impl()
    lock = threading.RLock()
    pb.voice_tmp_path_queue_lock = lock
    pb.voice_tmp_path_queue_not_empty = threading.Condition(lock)
    pb.voice_tmp_path_queue = []
    return pb


def _comment_entry(chars=8):
    # voice_path 为空 → _entry_duration_s 文本估算兜底：est = max(1, len/4)
    return {"type": "comment", "content": "x" * chars, "voice_path": ""}


def test_overbudget_entry_stays_queued():
    """估时 10s > 前奏剩余 3s−余量：留队不 pop（借位会越过 due 顺延人声）。"""
    pb = _pb()
    e = _comment_entry(chars=40)          # est=10.0s
    pb.voice_tmp_path_queue.append(e)
    assert pb._pick_intro_borrow_locked(3.0) is None
    assert pb.voice_tmp_path_queue == [e]


def test_fitting_entry_borrowed_at_boundary():
    """est = remain−余量 边界放行（est=2.0，remain=2.5，余量 0.5）。"""
    pb = _pb()
    e = _comment_entry(chars=8)           # est=2.0s
    pb.voice_tmp_path_queue.append(e)
    assert pb._pick_intro_borrow_locked(2.5) is e
    assert pb.voice_tmp_path_queue == []


def test_just_over_boundary_not_borrowed():
    """est 略超 remain−余量：不放行（余量吸收出声延迟/估时残差）。"""
    pb = _pb()
    e = _comment_entry(chars=8)           # est=2.0s
    pb.voice_tmp_path_queue.append(e)
    assert pb._pick_intro_borrow_locked(2.4) is None   # 2.0 > 2.4−0.5
    assert pb.voice_tmp_path_queue == [e]


def test_song_and_copy_skipped_fitting_comment_picked():
    """song 切片段/动态文案分段跳过（2026-11-25 问题②口径），普通条目命中。"""
    pb = _pb()
    song = {"type": "song", "content": "seg", "voice_path": "x",
            "song_segment": {"seq": 1, "total": 3, "start_s": 10.0}}
    copy = {"type": "trends_copywriting", "content": "copy", "voice_path": "",
            "split_group": {"id": "g1"}}
    fit = _comment_entry(chars=8)
    pb.voice_tmp_path_queue.extend([song, copy, fit])
    assert pb._pick_intro_borrow_locked(3.0) is fit
    assert pb.voice_tmp_path_queue == [song, copy]


def test_first_candidate_overbudget_blocks_whole_round():
    """首候选超预算=整轮不借位——不跳过改序（R5c 修正口径：保持队序）。"""
    pb = _pb()
    long = _comment_entry(chars=40)       # est=10.0s 超预算
    short = _comment_entry(chars=8)       # est=2.0s 预算内
    pb.voice_tmp_path_queue.extend([long, short])
    assert pb._pick_intro_borrow_locked(3.0) is None
    assert pb.voice_tmp_path_queue == [long, short]
