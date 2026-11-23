"""Output buffering and streaming for realtime inference.

Provides two core components:

1. FrameBuffer — a bounded, ring-buffered FIFO for produced frames.
   - Capacity is fixed at construction and slots are pre-allocated.
   - Producers block when full; consumers block when empty.
   - flush() drops old frames while keeping a small reserve.
   - close() wakes all waiters so threads can exit cleanly.

2. OutputStreamer — the single pacing clock that consumes frames from the
   FrameBuffer and forwards them to the video sink at a steady target FPS.
   - Exists unconditionally, even when no virtual camera is available.
   - FRAME-LOCKED AUDIO: each displayed frame carries its 640-sample audio
     chunk in meta; the streamer pushes it to the audio player at display
     time — audio position ≡ displayed lip position by construction (the
     audio waits for the lips when inference is behind, never the reverse).
   - Re-sends the previous frame on underrun and counts occurrences.
   - Limits catch-up rate to avoid bursts after a preroll / recovery window.
"""
import time
import threading
import collections
from typing import Optional, Callable, Any

import numpy as np


class FrameBuffer:
    """Bounded ring-buffer FIFO for video frames.

    Thread-safe. Slots are pre-allocated as a list of fixed length so that
    flush() and steady-state operation do not allocate memory.

    Args:
        capacity: Maximum number of frames held. Must be >= 1 and <= 300.
        reserve: Number of newest frames to keep on flush().
                 Capped to capacity and to a maximum of 5.
    """

    MAX_CAPACITY = 300
    MAX_RESERVE = 5

    def __init__(self, capacity: int = 8, reserve: int = 5):
        if capacity < 1:
            raise ValueError(f"FrameBuffer capacity must be >= 1, got {capacity}")
        if capacity > self.MAX_CAPACITY:
            raise ValueError(
                f"FrameBuffer capacity must be <= {self.MAX_CAPACITY}, got {capacity}"
            )
        if reserve < 0:
            reserve = 0
        reserve = min(reserve, self.MAX_RESERVE, capacity)

        self.capacity = capacity
        self.reserve = reserve

        # 最近一次 flush_for_new_audio 实际清空的帧总数（含非语音帧）。
        # 2026-11-23 显示序时钟（render_loop._video_pos）消费：被丢弃的帧
        # 永不显示，须计入丢弃累计才能保证体态位置与实际显示严格一致。
        # 在锁内赋值，调用方读取无竞态。
        self.last_flush_total = 0

        # Pre-allocated ring slots: each slot is (frame, meta) or None.
        self._slots: list = [None] * capacity
        self._head: int = 0          # next read position
        self._tail: int = 0          # next write position
        self._size: int = 0
        self._closed: bool = False

        self._lock = threading.Lock()
        self._not_empty = threading.Condition(self._lock)
        self._not_full = threading.Condition(self._lock)

    @property
    def depth(self) -> int:
        """Current number of buffered frames."""
        with self._lock:
            return self._size

    @property
    def is_full(self) -> bool:
        """Whether the buffer is currently full."""
        with self._lock:
            return self._size >= self.capacity

    @property
    def is_closed(self) -> bool:
        with self._lock:
            return self._closed

    def put(self, frame: np.ndarray, meta: Optional[dict] = None,
            timeout: Optional[float] = None) -> bool:
        """Add a frame to the buffer. Block while full.

        Args:
            frame: BGR uint8 numpy array.
            meta: Optional metadata dict; may include 'audio_start' flag.
            timeout: Max seconds to wait for space. None = infinite.

        Returns:
            True if the frame was inserted, False if the buffer was closed
            or the timeout expired.
        """
        deadline = None if timeout is None else time.perf_counter() + timeout
        with self._not_full:
            while True:
                if self._closed:
                    return False
                if self._size < self.capacity:
                    break
                # Buffer full: wait, but periodically check closed flag.
                wait = None if deadline is None else deadline - time.perf_counter()
                if wait is not None and wait <= 0:
                    return False
                self._not_full.wait(timeout=wait)

            slot_idx = self._tail
            self._slots[slot_idx] = (frame, meta)
            self._tail = (self._tail + 1) % self.capacity
            self._size += 1
            self._not_empty.notify()
            return True

    def get(self, timeout: Optional[float] = None) -> Optional[tuple]:
        """Remove and return the oldest frame.

        Returns:
            (frame, meta) tuple, or None if closed/timeout/empty.
        """
        deadline = None if timeout is None else time.perf_counter() + timeout
        with self._not_empty:
            while True:
                if self._closed:
                    return None
                if self._size > 0:
                    break
                wait = None if deadline is None else deadline - time.perf_counter()
                if wait is not None and wait <= 0:
                    return None
                self._not_empty.wait(timeout=wait)

            slot_idx = self._head
            item = self._slots[slot_idx]
            self._slots[slot_idx] = None  # release reference
            self._head = (self._head + 1) % self.capacity
            self._size -= 1
            self._not_full.notify()
            return item

    def flush(self, keep_reserve: Optional[int] = None) -> int:
        """Drop all but the newest reserve frames.

        Args:
            keep_reserve: Override number of frames to keep. Defaults to
                          the reserve passed to __init__.

        Returns:
            Number of frames dropped.
        """
        if keep_reserve is None:
            keep_reserve = self.reserve
        keep_reserve = max(0, min(keep_reserve, self.capacity, self.MAX_RESERVE))

        with self._lock:
            dropped = 0
            if self._closed:
                return 0
            while self._size > keep_reserve:
                slot_idx = self._head
                self._slots[slot_idx] = None
                self._head = (self._head + 1) % self.capacity
                self._size -= 1
                dropped += 1
            self._not_full.notify_all()
            return dropped

    def flush_seg(self) -> int:
        """Drop only buffered frames that carry a speech chunk (meta 'seg').

        新句拼接语义（2026-11-21 卡碟/跳帧修复）：顺序推送时上一句已完整
        显示，缓冲尾部全是 hold/idle 闭口帧——保留（画面/音频连续拼接）；
        打断/重叠推送时仅丢未显示的语音尾（正确截断，语音块随帧锁定不外
        播）。保持 FIFO 顺序；与 put/get 同锁，线程安全。
        """
        with self._lock:
            if self._closed:
                return 0
            items = []
            while self._size > 0:
                items.append(self._slots[self._head])
                self._slots[self._head] = None
                self._head = (self._head + 1) % self.capacity
                self._size -= 1
            dropped = 0
            for item in items:
                _, meta = item if item is not None else (None, None)
                if meta is not None and meta.get('seg'):
                    dropped += 1
                    continue
                self._slots[self._tail] = item
                self._tail = (self._tail + 1) % self.capacity
                self._size += 1
            self._not_full.notify_all()
            self._not_empty.notify_all()
            return dropped

    def flush_for_new_audio(self) -> int:
        """New-audio push prep — drop the unplayed speech tail (meta 'seg')
        AND clear the remaining hold/idle tail, so the incoming sentence does
        not queue behind a deep idle buffer.

        拖音根因修复（2026-11-21 用户实测批次六）：batch5 的 flush_seg 保留
        了 hold/idle 尾帧以维持画面连续，但新句的帧被排在这些尾帧之后——
        上一句结束时缓冲常被 idle 帧填满（深度可达 capacity），新句首音因
        此被推迟 depth×40ms（实测冷态 ~800ms）→ 两句之间出现静音空档
        （"你……好"）。这里在单次锁内清空整个尾部：语音帧按截断语义计数
        丢弃，hold/idle 尾帧一并排空。

        2026-11-23 显示序时钟（render_loop._video_pos）：位置 = 产出数 −
        丢弃累计 − 段基线，本方法清空的总帧数在锁内写入 last_flush_total
        供 render_loop 计入丢弃累计——被丢弃帧永不显示，下一帧体态位置自动
        接续最后显示帧（零跳变）；配合帧头 staged flush 与 OutputStreamer
        中流 re-arm 非阻塞路径，flush/停顿/追赶均不再造成画面跳变。
        返回丢弃的语音帧数（仅用于诊断日志）。
        """
        with self._lock:
            if self._closed:
                return 0
            items = []
            while self._size > 0:
                items.append(self._slots[self._head])
                self._slots[self._head] = None
                self._head = (self._head + 1) % self.capacity
                self._size -= 1
            dropped = 0
            for item in items:
                _, meta = item if item is not None else (None, None)
                if meta is not None and meta.get('seg'):
                    dropped += 1
            self.last_flush_total = len(items)
            # 不回列任何 item：整个尾部排空，新句帧直接入队首
            self._not_full.notify_all()
            self._not_empty.notify_all()
            return dropped

    def close(self) -> None:
        """Close the buffer and wake all waiting threads."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            # Drop remaining frames to release references.
            for i in range(self.capacity):
                self._slots[i] = None
            self._size = 0
            self._head = 0
            self._tail = 0
            self._not_empty.notify_all()
            self._not_full.notify_all()

    def stats(self) -> dict:
        """Snapshot statistics."""
        with self._lock:
            return {
                'capacity': self.capacity,
                'depth': self._size,
                'reserve': self.reserve,
                'closed': self._closed,
            }


class AudioPreBuffer:
    """Small bounded FIFO of upcoming frame-locked audio chunks (pre-buffer).

    2026-11-23 音节内拖音修复：AudioPlayer 原先每 40ms 只收 1 帧（640 采样），
    缓冲深度恰等于推送间隔 → 零余量。OutputStreamer 是软件时钟（sleep+忙等），
    GIL/调度抖动使其 tick 延迟 >20ms 时，硬件 20ms 定时的 player 回调即把缓冲
    跑空 → WASAPI 补零 → 音节中插入静音（"你……好"）。RenderLoop 产帧时把该
    帧的 audio_meta 同时入此 FIFO；OutputStreamer 每显示帧从此 FIFO 取未来 K
    帧提前推给 player 垫底，使 player 恒持 K 帧缓冲吸收抖动。稳态音画仍精确
    同步（显示 frame[i] ↔ 播放 chunk[i]），仅抖动恢复期口型短暂冻结（远好于
    拖音）。段切换时与 frame_buffer 一同 flush（防旧段音频外泄）。
    """

    def __init__(self, capacity: int = 16):
        self.capacity = max(1, int(capacity))
        self._q: collections.deque = collections.deque()
        self._lock = threading.Lock()
        self._not_full = threading.Condition(self._lock)

    def put(self, meta: dict, timeout: Optional[float] = None) -> bool:
        """Push an audio_meta dict. Blocks while full (backpressure)."""
        deadline = None if timeout is None else time.perf_counter() + timeout
        with self._not_full:
            while len(self._q) >= self.capacity:
                if deadline is not None and time.perf_counter() >= deadline:
                    return False
                self._not_full.wait(
                    timeout=None if deadline is None
                    else max(0.0, deadline - time.perf_counter()))
            self._q.append(meta)
            return True

    def pop(self) -> Optional[dict]:
        """Pop the oldest audio_meta, or None if empty."""
        with self._lock:
            if not self._q:
                return None
            return self._q.popleft()

    def flush(self) -> int:
        """Drop all buffered audio metas (segment switch). Returns count."""
        with self._lock:
            n = len(self._q)
            self._q.clear()
            self._not_full.notify_all()
            return n

    def __len__(self) -> int:
        with self._lock:
            return len(self._q)


class OutputStreamer:
    """Single pacing clock that pulls frames from a FrameBuffer and sends them
    to a video sink at a steady target FPS.

    The streamer owns the only pacing clock in the pipeline. RenderLoop (the
    producer) runs as fast as it can and writes into the FrameBuffer; this
    thread consumes at exactly target_fps, re-sending the previous frame when
    the buffer underruns.

    Args:
        frame_buffer: FrameBuffer to consume from.
        target_fps: Output frame rate. Also used to compute backfill limits.
        sender: Callable that receives a BGR frame, or None if no sink.
                When None frames are dropped after accounting.
        audio_player: Frame-locked audio sink — the 640-sample chunk riding
                      each displayed frame's meta is pushed here at display
                      time (audio position ≡ displayed lip position).
        preroll_frames: Frames required in the buffer before output starts.
                        A value of 0 disables preroll.
    """

    # Maximum catch-up multiplier after a recovery / flush window.
    BACKFILL_MAX_MULTIPLIER = 2.0

    def __init__(self,
                 frame_buffer: FrameBuffer,
                 target_fps: int = 25,
                 sender: Optional[Callable[[np.ndarray], bool]] = None,
                 audio_player: Optional[Any] = None,
                 preroll_frames: int = 4,
                 audio_prebuffer: Optional[Any] = None,
                 audio_prebuf_frames: int = 3):
        if target_fps <= 0:
            raise ValueError(f"target_fps must be positive, got {target_fps}")
        self.frame_buffer = frame_buffer
        self.target_fps = target_fps
        self.interval = 1.0 / target_fps
        self.sender = sender
        # Frame-locked audio sink (E-sync): receives the 640-sample chunk of
        # each displayed frame — audio position ≡ displayed lip position.
        self.audio_player = audio_player
        self.preroll_frames = max(0, min(preroll_frames, frame_buffer.capacity))
        # 2026-11-23 音节内拖音修复：音频预缓冲 FIFO + 目标垫底帧数。每 tick
        # 从 FIFO 取未来若干帧提前推给 player，使 player 恒持 audio_prebuf_frames
        # 帧缓冲，吸收 OutputStreamer 软件时钟的 GIL/调度抖动（>20ms tick 延迟
        # 不再让 player 跑空补零）。None = 退化到旧的"显示帧即推"行为（零垫底）。
        self.audio_prebuffer = audio_prebuffer
        self.audio_prebuf_frames = max(0, int(audio_prebuf_frames))
        # 2026-11-23 段间无缝修复：_activate_segment 直推到 player 的首 K
        # 帧 chunk 数；_topup_audio 从 FIFO 取帧时跳过这么多（已直推过，防
        # 重复）。由 _lock 保护。
        self._prebuf_skip = 0

        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

        # F-A/uc2b 任意点暂停门（2026-11-05 编排优化批次 T6 spike）：消费冻结
        # ——循环不弹出帧（FrameBuffer 内容与游标原样保留=精确帧位续播）、
        # 音频 chunk 帧锁定天然同停、画面由 sink 持帧。生产者 put 满载阻塞=
        # 背压等待（零丢帧）；恢复=从冻结帧位继续（flush/preroll 不触碰——
        # 仅属段替换语义）。静态 spike 三承重点：拉式节拍钟/put 阻塞语义/
        # 段内暂停不涉替换状态（详见批次计划 F-A 结论）。
        self._paused = False

        self._underruns = 0
        self._output_frames = 0
        self._last_sent: Optional[np.ndarray] = None
        # 句段 chunk 帧显示计数（2026-11-21 音频结尾撕裂修复）：RenderLoop 在
        # chunk 帧的 meta 打 seg 标，本循环显示时递增——经 stats 汇入 /status，
        # 客户端 wait_flight 据此精确判定上一句播放完毕（见 render_loop
        # __init__ 注释）。
        self._seg_displayed = 0
        # EDTALK_SEAMLESS 连续流：段切换时上段残留（未显示）语音帧仍会先于下
        # 段显示并计入 _seg_displayed，需扣除此偏移才得到下段真实已显帧数。
        self._seg_displayed_leftover = 0
        # P3 静音填充（2026-11-21 用户实测批次）：闭口 idle 帧供给方
        # （RenderLoop.get_idle_frame）——preroll 等待/underrun 期间以闭口帧
        # 喂 sink，不再冻结/重发在上一句开口尾帧（「音频间隙张嘴」根因）。
        self._idle_frame_provider: Optional[Callable[[], Optional[np.ndarray]]] = None
        self._preroll_ready = threading.Event()
        self._preroll_progress = (0, self.preroll_frames)

    @property
    def underruns(self) -> int:
        with self._lock:
            return self._underruns

    @property
    def output_frames(self) -> int:
        with self._lock:
            return self._output_frames

    @property
    def is_prerolling(self) -> bool:
        return not self._preroll_ready.is_set()

    def preroll_progress(self) -> tuple:
        """Return (ready, needed) preroll progress."""
        return self._preroll_progress

    def reset_preroll(self, frames: Optional[int] = None) -> None:
        """Re-arm preroll. Called on flush / new segment / recovery."""
        if frames is None:
            # Re-arm threshold is capped to a small default to avoid long freezes.
            frames = min(self.preroll_frames, 4)
        frames = max(0, min(frames, self.frame_buffer.capacity))
        self.preroll_frames = frames
        self._preroll_ready.clear()
        self._preroll_progress = (0, frames)

    def reset_seg_displayed(self, leftover: int = 0) -> None:
        """段切换基线：清零当前段显示计数（2026-11-21 拖音/跳帧修复批次）。

        _seg_displayed 此前跨段累计不重置，而 _seg_chunk_total/_produced 每段
        归零 → 客户端 wait_flight 的 displayed>=produced 判定在第二段起恒成立
        （累计值含之前所有段已显帧数）→ 段刚产出完（而非显示完）即放行 →
        重叠推送 → flush 丢未显示尾帧 → 跳帧/尾音截断。每段 push 时清零后，
        /status 三字段语义一致：total=本段总数 / produced=本段已产 /
        displayed=本段已显，wait_flight 精确判定才真正成立。

        leftover（EDTALK_SEAMLESS 连续流）：段切换不 flush 时，上段残留的未
        显示语音帧会先于下段显示并计入 _seg_displayed；记录该数量，
        seg_displayed_count 扣除后才是下段真实已显帧数，避免下段被误判提前
        播完 → 级联丢尾音。"""
        with self._lock:
            self._seg_displayed = 0
            self._seg_displayed_leftover = max(0, int(leftover))

    def seg_displayed_count(self) -> int:
        """当前段已显示的 seg 语音帧数（pending 自动衔接用，轻量读）。"""
        with self._lock:
            return max(0, int(self._seg_displayed) - int(self._seg_displayed_leftover))

    def set_paused(self, paused: bool) -> None:
        """F-A/uc2b：任意点暂停/恢复（消费冻结门；线程安全——GIL 布尔读）。"""
        self._paused = bool(paused)

    def set_idle_frame_provider(
            self, provider: Optional[Callable[[], Optional[np.ndarray]]]) -> None:
        """注册闭口 idle 帧供给方（P3 静音填充，2026-11-21 批次）。

        provider() -> np.ndarray|None：无音频段在播时返回最近产出的闭口帧；
        音频段在播（口型应连续）或尚无 idle 帧时返回 None——维持既有
        _last_sent 重发语义。异常由调用方吞掉（供给方内部自守）。"""
        self._idle_frame_provider = provider

    def _send_idle_frame(self) -> None:
        """闭口帧喂屏（P3）：preroll 等待/underrun 期间调用。无供给方/无帧
        时不动作（维持既有语义）。"""
        fn = self._idle_frame_provider
        if fn is None or self.sender is None:
            return
        try:
            idle = fn()
        except Exception:
            return
        if idle is None:
            return
        try:
            if self.sender(idle):
                self._last_sent = idle
        except Exception as e:
            print(f"[OutputStreamer] Idle frame send error: {e}")

    def is_paused(self) -> bool:
        return self._paused

    def start(self) -> None:
        """Start the output pacing thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._stream_loop, daemon=True, name='output-streamer'
        )
        self._thread.start()

    def stop(self, timeout: float = 1.0) -> None:
        """Stop the output pacing thread."""
        self._running = False
        # Wake preroll wait in case it is blocked.
        self._preroll_ready.set()
        if self._thread:
            self._thread.join(timeout=timeout)
            self._thread = None

    def _wait_preroll(self) -> bool:
        """Block until preroll is satisfied or streamer is stopped."""
        needed = self.preroll_frames
        while self._running and not self._preroll_ready.is_set():
            depth = self.frame_buffer.depth
            self._preroll_progress = (depth, needed)
            if depth >= needed:
                # 2026-11-23 段间间隙修复：首帧就绪（render 已把该帧 audio_meta
                # 也放入 audio_prebuffer FIFO）立即把新段音频推进 player 垫底，
                # 不等下一个 40ms tick。旧段 K 帧垫底（120ms）足以桥接此瞬间，
                # 消除 preroll 等待期旧垫底被消费完 → 硬件补零 → 段间静音缺口。
                self._topup_audio()
                self._preroll_ready.set()
                return True
            # Poll at 50Hz (20ms) — matches the AudioPlayer callback block
            # size so a newly-ready first frame is detected within one audio
            # block, bounding segment-boundary audio delay (2026-11-23 段间
            # 间隙修复：原 50ms 轮询使首帧就绪到 _topup_audio 最多滞后 50ms，
            # 放大段间空隙；20ms 把上限压到 1 个音频块)。
            # 2026-11-21 卡顿修复：等待期不再发送静态缓存 idle 帧——缓存帧
            # 重复 + 轮询节奏与 25fps 不齐造成画面静止/跳帧抽搐；间隙
            # 连贯性由中流 re-arm 阈值 1（render_loop）保证——生产者的实时
            # 闭口 idle 帧以原生节奏连续流出，本等待仅在流启动/极端滞后时
            # 短暂生效（闭口帧供给仅保留在 underrun 兜底分支）。
            time.sleep(0.02)
        return self._running

    def prime_new_segment_audio(self, chunks, do_reset: bool = True) -> None:
        """段激活瞬间把新段首 K 帧直推 player（2026-11-23 段间无缝）。

        旧路径下新段音频要等 preroll(1)+首帧显示 tick 才 reset/推，旧段尾静音
        （AUDIO_TAIL_SILENCE_S 填充 + 源 lead/trail）在 reset 前继续播放 → 段间
        0–160ms 可变静音缺口（听感卡顿/跳祯）。此处激活即直推首 K 帧，新音频在
        激活瞬间接续出声，消除段间空隙。直推过的 K 帧由 _prebuf_skip 标记，
        _topup_audio 从 FIFO 取到时跳过（防重复推）。

        do_reset=True（默认，EDTALK_PRIME_BOUNDARY）：reset 后直推 K=prebuf 帧，
          代价=声领先口型 ~1 帧 preroll（<40–80ms）。
        do_reset=False（EDTALK_SEAMLESS 连续流）：不 reset——reset 清软件缓冲但
          sounddevice 硬件 FIFO 仍播上段尾静音 → 80–240ms 可听缺口。仅直推首 1
          帧（k=1）把声画领先限制在 ≤1 帧，chunk 直接 append 到 player 缓冲尾，
          与上段末 chunk 无缝衔接。"""
        if self.audio_player is None or self.audio_prebuffer is None:
            return
        if self.audio_prebuf_frames <= 0 or not chunks:
            return
        if do_reset:
            k = min(self.audio_prebuf_frames, len(chunks))
        else:
            k = min(1, len(chunks))  # 连续流：仅首帧，限制领先 ≤1 帧
        pushed = 0
        try:
            if do_reset:
                self.audio_player.reset()
            for i in range(k):
                ch = chunks[i]
                if ch is not None and len(ch) > 0:
                    self.audio_player.push_pcm(ch)
                    pushed += 1
        except Exception as e:
            print(f"[OutputStreamer] prime audio error: {e}")
            return
        if pushed > 0:
            with self._lock:
                self._prebuf_skip = pushed

    def _topup_audio(self) -> None:
        """Top up the AudioPlayer pre-buffer from the AudioPreBuffer FIFO.

        2026-11-23 音节内拖音修复：每显示帧把 player 缓冲填到 audio_prebuf_frames
        帧（取未来帧的 chunk 提前推），使 player 恒持垫底深度，吸收软件时钟抖
        动。稳态下显示 frame[i] 时 player 正播放 chunk[i]（精确同步），i+1..i+K
        已垫底待播；抖动恢复期 player 继续播垫底真音频，仅口型短暂冻结。
        audio_start 项触发 reset（清旧段），随后继续填充新段。
        段切换时 _activate_segment 已通过 prime_new_segment_audio 直推首 K
        帧，本循环遇到对应 FIFO 项时跳过（_prebuf_skip），不重复推/不重复 reset。"""
        if self.audio_player is None or self.audio_prebuffer is None:
            return
        if self.audio_prebuf_frames <= 0:
            return
        target = self.audio_prebuf_frames * (16000 // self.target_fps)
        # 防御上限：单 tick 最多补 prebuf+若干帧，避免异常时疯狂消费 FIFO。
        max_pops = self.audio_prebuf_frames + 4
        pops = 0
        while pops < max_pops:
            # 段切换直推的首 K 帧已在 prime_new_segment_audio 里推进 player，
            # FIFO 中对应项必须弹出丢弃以保持游标同步（不重复推、不触发 reset）。
            with self._lock:
                skip = self._prebuf_skip
            if skip > 0:
                item = self.audio_prebuffer.pop()
                if item is None:
                    break
                pops += 1
                with self._lock:
                    if self._prebuf_skip > 0:
                        self._prebuf_skip -= 1
                continue
            try:
                if self.audio_player.buffered_samples >= target:
                    break
            except Exception:
                break
            item = self.audio_prebuffer.pop()
            if item is None:
                break
            pops += 1
            try:
                if item.get('audio_start'):
                    self.audio_player.reset()
                chunk = item.get('audio')
                if chunk is not None:
                    self.audio_player.push_pcm(chunk)
            except Exception as e:
                print(f"[OutputStreamer] prebuf audio push error: {e}")

    def _stream_loop(self) -> None:
        """Pacing loop: one output tick per target frame interval."""
        try:
            self._do_stream_loop()
        except Exception as e:
            print(f"[OutputStreamer] Fatal error in stream loop: {e}")
            import traceback
            traceback.print_exc()

    def _do_stream_loop(self) -> None:
        interval = self.interval
        next_t = time.perf_counter()
        preroll_announced = False

        while self._running:
            # F-A/uc2b 任意点暂停门（T6）：冻结消费——帧不弹出、音频不推、
            # sink 持帧。置于 preroll 门之前：preroll 期间暂停同样冻结。
            if self._paused:
                time.sleep(0.02)
                continue

            # Preroll gate (re-armable after flush / recovery).
            if self.preroll_frames > 0 and not self._preroll_ready.is_set():
                if self._output_frames == 0:
                    # 初始启动：阻塞等待首帧就绪（原行为——冷启动必须有真帧）。
                    if not preroll_announced:
                        print(f"[OutputStreamer] Preroll: waiting for {self.preroll_frames} frames")
                        preroll_announced = True
                    if not self._wait_preroll():
                        return
                    print(f"[OutputStreamer] Preroll complete ({self.preroll_frames} frames); "
                          f"resuming output at {self.target_fps}fps")
                    preroll_announced = False
                elif self.frame_buffer.depth >= self.preroll_frames:
                    # 中流 re-arm 已达标：补音频垫底并放行。
                    self._topup_audio()
                    self._preroll_ready.set()
                    preroll_announced = False
                else:
                    # 2026-11-23 开口跳帧根因修复：中流 re-arm（每句冷激活的
                    # reset_preroll(1)）不再阻塞节拍循环。旧路径 _wait_preroll
                    # 阻塞 60-100ms 期间 next_t 不推进 → 恢复后 1-2 帧以
                    # <40ms 间隔连发（catch-up burst）+ 显示冻结 60-100ms
                    # = 开口 1-2 帧跳帧的根因。改为不阻塞：本 tick 落入下方
                    # underrun 路径（重发上一帧/闭口帧 + 静音保活），next_t
                    # 正常推进，25fps 节奏不间断；深度达阈值由上一分支放行。
                    self._preroll_progress = (self.frame_buffer.depth,
                                              self.preroll_frames)

            next_t += interval
            now = time.perf_counter()
            wait = next_t - now
            if wait > 0.002:
                time.sleep(wait * 0.8)
            else:
                # Fell behind. Cap catch-up to avoid bursts.
                max_catchup = interval / self.BACKFILL_MAX_MULTIPLIER
                if wait < -max_catchup:
                    next_t = now - max_catchup

            # Busy-wait the tail for precise timing.
            while time.perf_counter() < next_t:
                pass

            # Pop one frame; short timeout to keep pacing even when empty.
            item = self.frame_buffer.get(timeout=0.01)
            frame, meta = item if item is not None else (None, None)

            # 2026-11-23 音节内拖音修复：每 tick 先把 player 预缓冲垫底（从
            # audio_prebuffer 取未来帧 chunk），吸收软件时钟抖动；垫底覆盖了
            # audio_start reset + 各帧 chunk，故启用时不再从显示帧 meta 推。
            if self.audio_prebuffer is not None:
                self._topup_audio()
            elif frame is not None and meta and self.audio_player is not None:
                # Legacy（无预缓冲）：显示帧即推其 chunk，音频位置≡显示帧位置。
                try:
                    if meta.get('audio_start'):
                        self.audio_player.reset()
                    chunk = meta.get('audio')
                    if chunk is not None:
                        self.audio_player.push_pcm(chunk)
                except Exception as e:
                    print(f"[OutputStreamer] audio push error: {e}")

            if frame is not None:
                if self.sender is not None:
                    try:
                        if self.sender(frame):
                            self._last_sent = frame
                    except Exception as e:
                        print(f"[OutputStreamer] Sender error: {e}")
                else:
                    # No sink: drop the frame but keep last_sent for underrun.
                    self._last_sent = frame

                if meta and meta.get('seg'):
                    self._seg_displayed += 1
                with self._lock:
                    self._output_frames += 1
            else:
                # Underrun: re-send previous frame to keep camera fed.
                with self._lock:
                    self._underruns += 1
                # 帧锁定音频保活：buffer 欠载时喂静音兜底。2026-11-23 修复：
                # 预缓冲启用且 FIFO 仍有真音频时，本 tick 上方 _topup_audio()
                # 已推真音频垫底（吸收抖动），无需再推静音；仅 FIFO 也空时才推
                # 静音，防 player 跑空补零。
                if (self.audio_player is not None
                        and (self.audio_prebuffer is None
                             or len(self.audio_prebuffer) == 0)):
                    try:
                        self.audio_player.push_pcm(
                            np.zeros(16000 // self.target_fps,
                                     dtype=np.float32))
                    except Exception as e:
                        print(f"[OutputStreamer] underrun silence error: {e}")
                # P3 静音填充：RenderLoop 空闲段（无音频在播）优先闭口帧
                # ——避免重发上一句开口尾帧；音频在播（供给方返回 None）
                # 维持重发语义（口型连续）。
                idle_sent = False
                if self._idle_frame_provider is not None:
                    idle = None
                    try:
                        idle = self._idle_frame_provider()
                    except Exception:
                        idle = None
                    if idle is not None and self.sender is not None:
                        try:
                            if self.sender(idle):
                                self._last_sent = idle
                            idle_sent = True
                        except Exception as e:
                            print(f"[OutputStreamer] Resend error: {e}")
                if not idle_sent and self._last_sent is not None and self.sender is not None:
                    try:
                        self.sender(self._last_sent)
                    except Exception as e:
                        print(f"[OutputStreamer] Resend error: {e}")
                # Optional: log sparingly or expose via /status.

    def stats(self) -> dict:
        """Snapshot statistics."""
        with self._lock:
            return {
                'target_fps': self.target_fps,
                'output_frames': self._output_frames,
                'underruns': self._underruns,
                'preroll_state': 'complete' if self._preroll_ready.is_set() else 'prerolling',
                'preroll_ready': self._preroll_progress[0],
                'preroll_needed': self._preroll_progress[1],
                'seg_displayed': max(0, int(self._seg_displayed) - int(self._seg_displayed_leftover)),
            }
