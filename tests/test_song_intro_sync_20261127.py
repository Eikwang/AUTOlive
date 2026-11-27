"""前奏人声绝对对齐守卫（2026-11-27 B+A 组合方案）。

根因（用户实测：前奏期间有插播消息→人声延迟 1-2s；无插播→同步）：
R5c 前奏借位循环 await 借位全程阻塞，借位条目受理被在飞消息飞行等待
（push_full wait_flight=True 等 displayed>=produced）拖过 due——预算校验
只看候选自身时长漏掉该时滞 → 人声顺延 ≈ 在飞剩余+preroll+est 越过量。

修复：
- B 预算校准：_pick_intro_borrow_locked / 间奏窗口预检计入在飞剩余播放
  时间（_inflight_remaining_s 单源）+preroll——超预算留队；
- A due 硬切：_run_intro_borrow_window 借位改后台任务与 due 竞速，due 到
  借位仍未受理 → 取消弃推（song_intro_borrow_cancel 计数），调用方准点
  push 人声 flush 中断在飞借位（d3 既有语义）→ 绝对对齐。

覆盖：
- B：在飞消息剩余播放时间阻塞借位受理 → 超预算留队
- B：无在飞 → 原边界借位放行（回归）
- A：借位受理慢于 due → 硬切取消，不等消费完成（人声不被顺延）
- A：借位受理先于 due → 正常消费（回归）
"""
import asyncio
import time
import threading
from types import SimpleNamespace

from utils.audio.playback_manager import PlaybackMixin


def _pb(flight_accept: float = 0.0, flight_dur: float = 0.0):
    class _Impl(PlaybackMixin):
        pass

    pb = _Impl()
    lock = threading.RLock()
    pb.voice_tmp_path_queue_lock = lock
    pb.voice_tmp_path_queue_not_empty = threading.Condition(lock)
    pb.voice_tmp_path_queue = []
    if flight_accept or flight_dur:
        # 模拟在飞消息：受理时刻+时长 → _inflight_remaining_s 据此估算
        pb.edtalk_client = SimpleNamespace(
            _flight_until=flight_accept, last_duration_s=flight_dur)
    # 伴奏闪避 no-op（借位任务体调用）
    pb._backing_duck_hold = lambda: None
    pb._backing_duck_release_later = lambda entry: None
    pb._backing_duck_release_now = lambda: None
    return pb


def _comment_entry(chars=8):
    # voice_path 为空 → _entry_duration_s 文本估算兜底：est = max(1, len/4)
    return {"type": "comment", "content": "x" * chars, "voice_path": ""}


def test_inflight_playback_blocks_intro_borrow():
    """B：在飞消息剩余 ~8s，est=2s + preroll + 在飞剩余 > remain−余量 → 留队。

    旧口径（只看 est）会借位 → 受理等在飞播完 → 尾部越过 due → 人声顺延
    （实测 1-2s 的根因）。"""
    pb = _pb(flight_accept=time.monotonic() - 0.5, flight_dur=8.0)
    e = _comment_entry(chars=8)                     # est=2.0s
    pb.voice_tmp_path_queue.append(e)
    # 在飞剩余≈8.0−0.5+preroll≈7.8 > remain(10)−0.5−2.0
    assert pb._pick_intro_borrow_locked(10.0) is None
    assert pb.voice_tmp_path_queue == [e]


def test_no_inflight_borrow_boundary_regression():
    """B 回归：无在飞消息 → 预算只剩 est+preroll，边界放行行为不变。"""
    pb = _pb()
    e = _comment_entry(chars=8)                     # est=2.0s
    pb.voice_tmp_path_queue.append(e)
    # est 2.0 + preroll 0.5 = 2.5 ≤ 3.0 − 余量 0.5 → 放行
    assert pb._pick_intro_borrow_locked(3.0) is e
    assert pb.voice_tmp_path_queue == []


def _install_consume(pb, seconds: float, state: dict):
    """替换实例消费入口：模拟 push 飞行等待耗时（seconds>0=受理慢于 due）。"""

    async def _fake_consume(entry, path):
        state["started"] = True
        if seconds > 0:
            await asyncio.sleep(seconds)
        state["finished"] = True

    pb._consume_edtalk_entry = _fake_consume


def test_due_hard_cut_cancels_pending_borrow():
    """A：借位受理慢于 due（飞行等待 5s > 前奏窗口 2.8s）→ 硬切取消弃推，
    调度在 due 准点返回（不等消费完成）——人声不被顺延，绝对对齐。"""
    pb = _pb()
    state = {"started": False, "finished": False}
    _install_consume(pb, seconds=5.0, state=state)
    e = _comment_entry(chars=8)                     # est=2.0s，预算内可借
    pb.voice_tmp_path_queue.append(e)

    async def scenario():
        t0 = time.monotonic()
        # due = t0 + 3.6 − push_lead 0.5 = t0+3.1（初始 remain 3.1 ≥ 借位预算
        # preroll 0.5+est 2.0+余量 0.5 → 可借）
        await pb._run_intro_borrow_window(t0, 3.6)
        return time.monotonic() - t0

    elapsed = asyncio.run(scenario())
    assert state["started"] is True                 # 借位任务已启动（受理中）
    assert state["finished"] is False               # 未受理即被硬切取消
    assert e not in pb.voice_tmp_path_queue         # 已 pick 弹出（弃推）
    assert elapsed < 4.5                            # due≈3.1s 返回，未等 5s 消费


def test_borrow_accepted_before_due_consumes_normally():
    """A 回归：借位受理先于 due → 正常消费完成，窗口内准点返回。"""
    pb = _pb()
    state = {"started": False, "finished": False}
    _install_consume(pb, seconds=0.0, state=state)
    e = _comment_entry(chars=8)
    pb.voice_tmp_path_queue.append(e)

    async def scenario():
        t0 = time.monotonic()
        await pb._run_intro_borrow_window(t0, 3.6)

    asyncio.run(scenario())
    assert state["finished"] is True
    assert pb.voice_tmp_path_queue == []
