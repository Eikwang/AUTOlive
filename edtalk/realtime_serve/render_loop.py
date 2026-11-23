"""Render loop for realtime streaming inference.

Key design: producer-consumer decoupling via FrameBuffer/OutputStreamer.
----------------------------------------------------------------------------
- RenderLoop (producer) renders frames as fast as possible and pushes them
  into a bounded FrameBuffer. It does NOT pace the camera output.
- OutputStreamer (consumer) owns the single output pacing clock and pulls
  frames from the FrameBuffer at a steady target_fps.
- Audio-driven lip coefficients are chosen by wall-clock audio position with
  a forward-looking buffer-depth compensation, so the lip shape displayed
  ~depth/fps seconds from now matches the audio that will be heard then.
- Audio playback is triggered by OutputStreamer when it pops the frame
  marked as the start of a new audio segment.
"""
import os
import time
import threading
from typing import Optional

import cv2
import numpy as np
import torch


class SegmentScheduler:
    """Segment playback sequence state machine (pure; no threads, no clocks).

    Owns the playback ORDER only: which segment plays next when the current
    one finishes. The completion signal and the actual switch stay in
    RenderLoop (single pacing clock principle). All state transitions are
    lock-guarded: advance() runs on the producer thread, sync_to() on API
    threads.
    """

    def __init__(self, sequence: list, mode: str = 'list', loop: str = 'forward'):
        if not sequence:
            raise ValueError("SegmentScheduler requires a non-empty sequence")
        self._lock = threading.Lock()
        self._sequence = list(sequence)
        self.mode = mode  # 'list' | 'auto'
        # 循环方式（2026-10-27 动作编排完善批次）：'forward'=顺序循环（首→尾→首）；
        # 'pingpong'=首尾往返（…2→1→0→1→2…，端点不重复播放）——单段默认画面
        # "pingpong 首尾循环"语义由编排器下发（单元素序列 pingpong 退化为顺序循环）。
        self.loop = loop if loop in ('forward', 'pingpong') else 'forward'
        self._index = 0
        self._dir = 1  # pingpong 游走方向（+1 正放 / -1 回放游标）
        self.paused = False  # True while the current segment is outside the sequence

    @property
    def sequence(self) -> list:
        with self._lock:
            return list(self._sequence)

    @property
    def position(self) -> int:
        """0-based index into the sequence; -1 while paused (segment outside)."""
        with self._lock:
            return -1 if self.paused else self._index

    def advance(self) -> str:
        with self._lock:
            if self.loop == 'pingpong' and len(self._sequence) > 1:
                nxt = self._index + self._dir
                if nxt >= len(self._sequence):
                    nxt, self._dir = len(self._sequence) - 2, -1
                elif nxt < 0:
                    nxt, self._dir = 1, 1
                self._index = nxt
            else:
                self._index = (self._index + 1) % len(self._sequence)
            self.paused = False
            return self._sequence[self._index]

    def sync_to(self, segment: str) -> None:
        """Align the scheduler after a MANUAL switch.

        Segment inside the sequence → continue the loop from there.
        Segment outside → pause the scheduler until the user switches back
        into the sequence (the current playback is NOT interrupted).
        """
        with self._lock:
            if segment in self._sequence:
                self._index = self._sequence.index(segment)
                self.paused = False
            else:
                self.paused = True


class RenderLoop:
    """Main rendering loop that produces frames as fast as GPU allows.

    The loop continuously renders frames and pushes them into a FrameBuffer.
    Output pacing is handled by OutputStreamer, which consumes from the same
    buffer.

    Thread model: Runs in a separate thread, independent of API server and
    the OutputStreamer consumer thread.
    """

    # Seconds to keep holding the last lip coefficient after the audio
    # finishes before returning to idle (closed mouth) mode.
    AUDIO_END_GRACE_S = 1.0

    # 句尾静音填充时长（秒，原版 crop_pad_audio 机制还原）。TTS 切片常在字尾
    # 戛然而止，而 mel 滑窗对末帧向前钳制（重复最后一个语音帧）→ 收口系数停留
    # 在开口形状，句后 hold 窗口原样持有 → 句间/句尾嘴巴保持张开。句尾补静音
    # （≥ mel 滑窗 0.2s 跨度 + 2 帧偏移）让末段帧的 mel 窗完全落在静音内 →
    # 口型自然闭合；填充帧的音频 chunk 为全零，帧锁定播放听感无感。
    AUDIO_TAIL_SILENCE_S = 0.3

    # Lip frames per Audio2Lip chunk (125 = 5s at 25fps). Small enough
    # that one chunk (~100-200ms GPU) never starves the render loop.
    LIP_CHUNK_FRAMES = 125

    # Fixed Audio2Lip input batch size (LIP_CHUNK_FRAMES + 2 halo + 1).
    # MUST match ModelManager.LIP_INFER_BATCH.
    LIP_INFER_BATCH = 128

    # Buffer depth at or above which adaptive face repair is allowed.
    # The buffer depth is the real-time measure of production headroom:
    # repair costs ~9ms/frame of synchronous GPU wait, so it is only
    # affordable when production is comfortably ahead of consumption.
    REPAIR_MIN_BUFFER = 6

    def __init__(self, target_fps: int = 25):
        """Initialize RenderLoop.

        Args:
            target_fps: Target output frame rate (default: 25)
        """
        self.target_fps = target_fps
        self.frame_interval = 1.0 / target_fps
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._character_data = None
        # RLock: the scheduler path re-enters (check holds the lock, then
        # calls switch_segment which takes it again on the same thread).
        self._segment_lock = threading.RLock()
        self._frame_index = 0
        self.actual_fps = 0.0

        # Injected components (set by main.py before start())
        self.model_manager = None
        self.frame_buffer = None
        self.output_streamer = None
        self.audio_buffer = None
        self.audio_processor = None
        self.compositor = None
        self.face_repair = None
        self.face_repair_mode: str = 'off'  # 'off' | 'on' | 'adaptive'
        self.gaze_corrector = None
        self.gaze_mode: str = 'off'  # 'off' | 'on' | 'adaptive'
        # 口型推理门控（'on' | 'off'）：off=画外音——跳过 Audio2Lip GPU 推理与
        # 画面口型渲染，音频 chunk 照常帧锁定出声（/lip/params 运行中热切）。
        self.lip_mode: str = 'on'
        self.audio_player = None
        # 2026-11-23 音节内拖音修复：音频预缓冲 FIFO（OutputBuffer.AudioPreBuffer）。
        # 产帧时把 audio_meta 同时入队，OutputStreamer 每 tick 取未来帧垫底，
        # 吸收软件时钟抖动。None=未装配（退化旧行为）。段切换时 flush。
        self.audio_prebuffer = None
        # Optional callback(width, height) fired after a successful segment
        # switch: re-opens the virtual camera at the new segment's native
        # resolution (wired by main.py to UnityCaptureSender.update_resolution).
        self.resolution_change_cb = None

        # self._clock is injectable (FPS/status timing; tests substitute a
        # fake). The video position itself is display-order driven — see
        # _video_idx / _video_base below.
        self._clock = time.perf_counter

        # 2026-11-23 开口跳帧根治：视频位置改为「显示序」驱动——
        #   显示序位置 = _frame_index(累计产出) − _flushed_frames(flush 累计丢弃)
        #              − _video_base(段基线)
        # 显示顺序恒等于产出顺序（FIFO 帧缓冲 + put 满载背压零丢帧），被 flush
        # 丢弃的帧永不显示，故该位置与实际已显示帧数严格一致：每句冷激活的
        # flush/停顿/追赶后，下一帧体态自动接续最后显示帧（零跳变）；产出抖动
        # 也不再造成 wall-clock int 截断的重复/跳位。（替代旧 _video_start_time
        # wall-clock 时钟——任何输出停顿都会造成体态前跳。）
        self._flushed_frames = 0
        self._video_base = 0
        # staged 帧缓冲排空（2026-11-23 显示序时钟）：API/HTTP 线程置位，
        # 渲染线程在下一帧头（idx 计算前）执行 flush + _flushed_frames 记账
        # ——保证不存在「idx 已按 flush 前基线算好、帧却被 flush」或反过来的
        # 竞态，显示序位置与实际显示严格一致。见 _flush_buffer/_do_frame_flush。
        self._pending_frame_flush = False

        # Segment scheduling (None = legacy single-segment loop)
        self._scheduler: Optional[SegmentScheduler] = None
        # Queued at=segment_end switch (M16): fires at the next loop/segment
        # boundary, BEFORE the scheduler guard — works in legacy mode too.
        self._pending_switch: Optional[str] = None
        # Frame-boundary gaze param application slot (M2): API threads write,
        # the render thread applies at frame head — no cross-stream races
        # with CUDA graph replay.
        self._pending_gaze_params: Optional[dict] = None

        # Frame-error circuit breaker (producer-thread protection)
        self._frame_errors = 0
        self.last_error: str = ""

        # Audio-driven lip state — FRAME-LOCKED SYNC: the CONSUMER paces
        # audio. Each produced frame consumes the next lip coefficient and
        # carries its 640-sample audio chunk (16kHz / 25fps); the
        # OutputStreamer pushes the chunk to the player exactly when the
        # frame is DISPLAYED. Audio position ≡ displayed-frame position ≡
        # lip position by construction (lead = DAC latency ≤ ~40ms); when
        # inference is behind, NO chunk is allocated and the audio waits —
        # lips can never lag the audio.
        self._lip_coeffs: Optional[torch.Tensor] = None
        self._audio_chunks: Optional[list] = None   # per-frame 640-sample PCM
        self._total_lip_frames: int = 0
        self._lip_filled: int = 0
        self._last_rendered_lip_idx: int = -1
        self._audio_arm_start: bool = False   # first advancing frame → audio_start
        self._audio_hold_frames: int = int(self.AUDIO_END_GRACE_S * target_fps)
        self._audio_lock = threading.Lock()

        # Serializes full-audio pushes (each push resets the player).
        self._push_lock = threading.Lock()

        # Idle lip coefficient cache
        self._idle_lip_cache: Optional[torch.Tensor] = None
        self._idle_lip_cache_valid: bool = False

        # P3 静音填充（2026-11-21 用户实测批次）：最近产出的闭口 idle 帧
        # 缓存（合成整帧引用，零拷贝）——供 OutputStreamer 在 preroll 等待/
        # underrun 期间喂 sink，避免冻结在上一句开口尾帧。
        self._last_idle_frame: Optional[np.ndarray] = None

        # 句段音频完成度计数（2026-11-21 音频结尾撕裂修复）：push_full 受理时
        # 归零基线，chunk 帧产出时递增 produced，OutputStreamer 显示 chunk 帧
        # 时递增 displayed（经 stats 汇入 /status）。客户端 wait_flight 以
        # produced>=total 且 displayed>=produced 精确判定上一句播放完毕——
        # 替代「buffer 深度≤阈值」启发式（生产滞后时深度骤降会提前放行 →
        # flush 遗弃未产出句尾 → 音频结尾撕裂/尾字丢失）。
        self._seg_chunk_total = 0
        self._seg_chunk_produced = 0
        # pending 自动衔接（2026-11-23 段间空档/拖音修复）：客户端在上段有效
        # 语音生成完（produced>=total）即提前 push 下段，服务端在此预推理下段
        # （与上段显示并行，HTTP 线程），渲染线程在上段 displayed>=total 的
        # 同一帧激活下段——省掉客户端轮询检测 + HTTP 往返 ~100ms，段间 gap
        # 从 ~120ms 压到首帧延迟 ~20ms。激活严格等上段 displayed>=total（不
        # 丢尾）。_seg_id 每激活一段递增，客户端据此串行（等期望 seg_id 完成
        # 才推下一段，避免覆盖 pending）。
        self._seg_id = 0
        self._next_ready = False
        self._next_lip_coeffs: Optional[torch.Tensor] = None
        self._next_audio_chunks: Optional[list] = None
        self._next_total = 0
        self._next_tail_frames = 0
        self._next_pcm_duration = 0.0

        # 视线修正宽限（2026-11-21 视线异常修复，批次五深化为「宽限至深度
        # 重建」）：flush 清空/抽低帧缓冲后，深度低于自适应阈值期间 generate
        # 门控拒绝生成（ori 模式只贴原帧未修正眼）。原固定 6 帧宽限不够——
        # 阈值 1 的中流 re-arm 下深度以每帧 ~0.2-0.3 净速爬升，约 1s 后才
        # 过阈，期间每帧都退回未修正眼（用户实测：段首视线回退仍存在）。
        # 改为 armed 标志：深度 ≥ REPAIR_MIN_BUFFER 或帧数兜底（150 帧
        # ≈6s，防生产端长期欠速时长开）后回归深度门控（GPU 余量保护不丢）。
        self._gaze_grace = 0
        self._gaze_grace_max = 0

        self._debug_audio_chunks = 0

        # Frame timing histogram (last N samples)
        self._timing_window = 240
        self._timings = {
            'gen': [],
            'gaze': [],
            'comp': [],
            'repair': [],
            'put': [],
            'total': [],
        }
        self._timing_lock = threading.Lock()

    def set_character_data(self, character_data):
        """Set or replace the character data pack."""
        with self._segment_lock:
            self._character_data = character_data
            # 显示序时钟：新 pack 从位置 0 开始（等效旧 wall-clock 重锚）。
            self._video_base = self._frame_index - self._flushed_frames

    def get_current_pack(self):
        """Get the current character data pack (thread-safe)."""
        with self._segment_lock:
            return self._character_data

    def switch_segment(self, segment: str, source: str = 'manual',
                       seamless: bool = False,
                       prefetched=None) -> bool:
        """Switch to a different segment within the current character.

        Args:
            segment: Target segment name.
            source: 'manual' (API/CLI) aligns the scheduler to the new
                    segment; 'pending' (queued segment_end, same sync rule)
                    and 'scheduler' (auto-advance) / 'startup' (schedule
                    installation) manage the scheduler index themselves.
            seamless: True for boundary continuations (M8): buffered tail
                    frames of the old segment ARE the legitimate continuation,
                    so nothing is flushed and preroll is NOT re-armed — output
                    keeps ticking through the switch. False (immediate) keeps
                    the E2 hard cut: flush + preroll re-arm, ≤~0.3s gap.
            prefetched: Optional ready PrefetchLoader result — published into
                    the preloader's slot 0 so the new segment serves frames
                    from cache with no cold-start disk-fallback window (M15).

        Returns:
            Snapshot dict {'segment', 'frame_count', 'resolution'} built
            inside the switch transaction (E3), or None if the pack refused.
        """
        with self._segment_lock:
            if self._character_data is None:
                raise ValueError("No character data loaded")
            # L3: any switch supersedes a queued segment_end switch.
            self._pending_switch = None
            if prefetched is not None:
                # M15 seamless path: publish the prefetched batch 0 (kwarg
                # omitted otherwise — keeps the pack's base contract and
                # every existing caller/double unchanged).
                ok = self._character_data.switch_segment(segment,
                                                         prefetched=prefetched)
            else:
                ok = self._character_data.switch_segment(segment)
            if ok:
                # E3/E4: response snapshot built INSIDE the switch transaction
                # (_segment_lock) — concurrent switches cannot cross-wire
                # another caller's response.
                snapshot = {
                    'segment': segment,
                    'frame_count': self._character_data.total_frames,
                    'resolution': list(self._character_data.resolution),
                }
                # 显示序时钟：新段从位置 0 开始（等效旧 wall-clock 重锚）。
                self._video_base = self._frame_index - self._flushed_frames
                # (Old S7 re-mark removed with the wall-clock sync: the
                # audio-start arm flag survives flushes by design — the
                # first advancing lip frame after a push carries the reset
                # marker regardless of intervening switches.)
                if seamless:
                    # M8 boundary continuation: keep the buffer and the output
                    # clock; buffered old-segment tail frames play out, then
                    # new-segment frames follow. No freeze, no jump.
                    pass
                else:
                    # E2: drop ALL buffered tail frames of the old segment and
                    # re-arm a controlled preroll (flush(5)+reset_preroll(4) was
                    # a no-op — depth 5 >= threshold 4 passed instantly).
                    # 2026-11-23 显示序时钟：flush 改为帧头 staged 执行（见
                    # _pending_frame_flush）——与 _flushed_frames 记账原子地
                    # 发生在渲染线程两帧之间，消除 in-flight 帧 idx 竞态。
                    self._flush_buffer()
                    if self.output_streamer is not None:
                        self.output_streamer.reset_preroll()
                    # 段首视线修正宽限（深度重建前放行，见 _gaze_allowed 注释）
                    self._arm_gaze_grace()
                if self.gaze_corrector is not None:
                    self.gaze_corrector.reset()
                # Native-resolution passthrough: the consumer side is paused
                # (immediate) or at a natural boundary (seamless), so the
                # camera can be re-opened at the new segment's size.
                if self.resolution_change_cb is not None and                         getattr(self, 'canvas_compositor', None) is None:
                    # 统一画布政策（R-ceo9）：合成层在场时出帧恒画布尺寸，
                    # 逐段分辨率重建退役（摄像头重建仅会话启动——消灭实播抖动源）
                    try:
                        h, w = self._character_data.resolution
                        self.resolution_change_cb(w, h)
                    except Exception as e:
                        print(f"[Scheduler] 摄像头分辨率切换失败: {e}")
                if self._scheduler is not None and source in ('manual', 'pending'):
                    self._scheduler.sync_to(segment)
                print(f"[Scheduler] segment -> '{segment}' (source: {source})")
                return snapshot
            return None

    def set_segment_sequence(self, sequence: list, mode: str = 'list',
                             align: bool = True, loop: str = 'forward') -> None:
        """Install/replace a segment playback schedule (list/auto mode).

        Scheduler replacement happens ENTIRELY under _segment_lock (M10/E6:
        an unlocked swap let advance() land on the new scheduler and skip
        its first segment). `align=False` (runtime replaces, M10) installs
        the order only — the current segment keeps playing and the new order
        takes effect at the next boundary; `align=True` (startup semantics)
        additionally switches to sequence[0] when it differs.

        Any queued segment_end switch is cleared (M3): a replaced schedule
        supersedes pending intent.
        """
        with self._segment_lock:
            self._scheduler = SegmentScheduler(sequence, mode=mode, loop=loop)
            self._pending_switch = None
            cd = self._character_data
            if align and cd is not None and sequence[0] != cd.current_segment:
                self.switch_segment(sequence[0], source='startup')

    # ------------------------------------------------------------------
    # Queued segment_end switch (M16)
    # ------------------------------------------------------------------

    def queue_segment_switch(self, segment: str) -> None:
        """Queue a switch that fires at the next segment boundary.

        The caller MUST validate the segment name at request time (M16) —
        this queue stores intent only. Last-write-wins; any immediate
        switch clears it.
        """
        self._pending_switch = segment

    def clear_pending_switch(self) -> None:
        self._pending_switch = None

    @property
    def pending_switch(self) -> Optional[str]:
        return self._pending_switch

    # ------------------------------------------------------------------
    # Frame-boundary gaze param application (M2)
    # ------------------------------------------------------------------

    def queue_gaze_params(self, convergence: Optional[float] = None,
                          smooth_alpha: Optional[float] = None,
                          mode: Optional[str] = None,
                          eye_source: Optional[str] = None) -> None:
        """Stage gaze param changes; the render thread applies them at the
        next frame head (no cross-stream copy_/replay races). Values must be
        validated by the caller BEFORE queueing."""
        params = {}
        if convergence is not None:
            params['convergence'] = convergence
        if smooth_alpha is not None:
            params['smooth_alpha'] = smooth_alpha
        if mode is not None:
            params['mode'] = mode
        if eye_source is not None:
            params['eye_source'] = eye_source
        if params:
            self._pending_gaze_params = params

    def _apply_pending_params(self) -> None:
        """Apply staged gaze params at frame head (render thread only)."""
        pending = self._pending_gaze_params
        if not pending:
            return
        self._pending_gaze_params = None
        mode = pending.get('mode')
        if mode is not None and mode != self.gaze_mode:
            self.gaze_mode = mode
            # on→off must restore native eyes (mask loses eye regions);
            # off→on re-enables them. Mask epoch invalidation (M18) covers
            # the already-loaded batches.
            if self._character_data is not None:
                self._character_data.set_eyes_enabled(mode != 'off')
        gc = self.gaze_corrector
        if gc is not None:
            if 'convergence' in pending:
                gc.set_convergence(pending['convergence'])
            if 'smooth_alpha' in pending:
                gc.smooth_alpha = pending['smooth_alpha']
            if 'eye_source' in pending:
                gc.set_eye_source(pending['eye_source'])

    def start(self):
        """Start the rendering loop in a separate thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        """Stop the rendering loop.

        Closing the frame buffer first wakes any threads blocked on put/get,
        so the producer and consumer can shut down cleanly.
        """
        self._running = False
        if self.frame_buffer is not None:
            try:
                self.frame_buffer.close()
            except Exception as e:
                print(f"[RenderLoop] Error closing frame buffer: {e}")
        if self._thread:
            self._thread.join(timeout=1.0)
            self._thread = None

    def _run(self):
        """Producer loop: render as fast as possible."""
        frame_count = 0
        _fps_timer_start = self._clock()
        _fps_frame_count = 0
        _last_status_time = self._clock()

        while self._running:
            # Producer-thread protection (ENG E3/F3): a single frame failure
            # must never kill the thread — log (rate-limited), count, and
            # trip a circuit breaker after sustained failure.
            try:
                self._check_segment_schedule()
                self._process_frame()
            except Exception as e:
                self._frame_errors += 1
                self.last_error = f"{type(e).__name__}: {e}"
                if self._frame_errors <= 3 or self._frame_errors % 100 == 0:
                    import traceback
                    print(f"[RenderLoop] frame error #{self._frame_errors}: {e}")
                    traceback.print_exc()
                if self._frame_errors >= 750:  # ~30s sustained: give up loudly
                    print(f"[RenderLoop] FATAL: {self._frame_errors} consecutive "
                          f"frame errors, stopping producer. Last: {self.last_error}")
                    self._running = False
                    break
                time.sleep(0.02)
                continue
            else:
                self._frame_errors = 0

            frame_count += 1
            _fps_frame_count += 1

            # Calculate production FPS over 1-second windows
            fps_elapsed = time.perf_counter() - _fps_timer_start
            if fps_elapsed >= 1.0:
                self.actual_fps = _fps_frame_count / fps_elapsed
                _fps_frame_count = 0
                _fps_timer_start = time.perf_counter()

            # Print status every 5 seconds
            now = time.perf_counter()
            if now - _last_status_time >= 5.0:
                _last_status_time = now
                mode = "audio" if self._lip_coeffs is not None else "idle"
                depth = 0
                if self.frame_buffer is not None:
                    depth = self.frame_buffer.depth
                timing = self._timing_summary()
                print(f"[RenderLoop] FPS:{self.actual_fps:.1f}  "
                      f"frame:{frame_count}  mode:{mode}  buffer:{depth}  {timing}")

    # ------------------------------------------------------------------
    # Audio processing
    # ------------------------------------------------------------------

    def _segment_active(self) -> bool:
        """上段有效语音是否仍在显示（未到可激活 pending 的时刻）。

        调用者应持 _push_lock（保证读到的 total 与装载状态一致）。"""
        if self._seg_chunk_total == 0 or self._lip_coeffs is None:
            return False
        if self.output_streamer is None:
            return False
        return self.output_streamer.seg_displayed_count() < self._seg_chunk_total

    @staticmethod
    def _trim_silence(pcm: np.ndarray, sr: int = 16000) -> np.ndarray:
        """裁掉 PCM 首尾近静音（迟滞双阈值：core 定位语音 + edge 扩展边界）。

        2026-11-23 段间卡顿修复：源 wav（GPT-SoVITS）自带长首尾真数字静音
        + 噪声底，不裁剪会被当作有效语音帧播完才激活下段 → 段间 1–3s 死寂。
        迟滞：core(2% 窗峰值)稳健定位语音主体，edge(0.3%)向外扩展保留弱起
        止/混响尾，仅在真静音(<edge)处停止——避免单阈值(1%)误剪句首句尾弱
        辅音（实测 1% 阈值会剪掉 file 78 达 260ms 弱语音，听感截断）。全静
        音段返回空数组（调用方判 len==0 忽略）。"""
        if pcm is None or len(pcm) == 0:
            return pcm
        win = sr // 50  # 20ms
        arr = np.asarray(pcm, dtype=np.float32)
        n = len(arr)
        nwin = n // win
        if nwin < 2:
            return arr
        usable = nwin * win
        rms_w = np.sqrt(np.mean(arr[:usable].reshape(nwin, win) ** 2, axis=1))
        pk = float(rms_w.max()) or 1e-9
        if pk <= 0:
            return arr[:0]
        core_thr = max(pk * 0.02, 1e-4)   # 2% — definite speech core
        edge_thr = max(pk * 0.003, 1e-4)  # 0.3% — soft onsets/offsets + reverb tail
        core = np.where(rms_w >= core_thr)[0]
        if len(core) == 0:
            return arr[:0]
        s = int(core[0])
        while s > 0 and rms_w[s - 1] >= edge_thr:
            s -= 1
        e = int(core[-1])
        while e < nwin - 1 and rms_w[e + 1] >= edge_thr:
            e += 1
        start = s * win
        end = min((e + 1) * win, n)
        if start >= end:
            return arr[:0]
        return arr[start:end]

    def _seamless(self) -> bool:
        """连续流接替实验开关（EDTALK_SEAMLESS=1）。

        段间跳帧根因：每段激活时 ap.reset() 清软件缓冲但 sounddevice 硬件
        FIFO 仍在播上段尾静音 → 80–240ms 可听缺口；叠加 AUDIO_TAIL_SILENCE_S
        0.3s 填充与 flush/preroll 的 1 帧画面冻结。开启后让 audio→audio 段切
        换像 idle→audio 首启一样连续：不 reset player、不 flush/preroll、下段
        chunk 经 FIFO 尾随上段末 chunk 出声，段间音频零缺口、画面无冻结。
        默认开；EDTALK_SEAMLESS=0 可回退原行为用于 A/B。"""
        return os.environ.get('EDTALK_SEAMLESS', '1') == '1'

    def _idle_onset_ramp_s(self) -> float:
        """idle→audio 首段口型起音渐变静音时长（秒）。

        根因：_trim_silence 裁掉源音频首静音后，首帧 mel 直接落在语音起始
        → Audio2Lip 首帧口型从 idle 缓存闭嘴形状突变（实测帧间 L2 Δ 1.24，
        超 p95），2 帧内 snapping 到开口 → 1–2 帧画面跳变。违背原设计"服务
        器填充静音垫底、音频接替时口型自然张开"。仅对 idle→audio（非活跃段、
        非 immediate 打断）在推理前预置该静音 ramp：mel 窗从静音渐变到语
        音，前 ~ramp*25 帧口型由闭渐开，消除 snapping。audio→audio 不加（会
        在段间插入死空气隙）。EDTALK_IDLE_RAMP_S=0 可关，seamless 关时恒 0。"""
        if not self._seamless():
            return 0.0
        try:
            return max(0.0, float(os.environ.get('EDTALK_IDLE_RAMP_S', '0.1')))
        except (TypeError, ValueError):
            return 0.1

    def _infer_segment(self, pcm_float: np.ndarray, from_idle: bool = False):
        """纯推理下段：返回 (lip_all, chunks, T, tail_frames, pcm_duration)。

        不触碰当前活跃段的状态字段——预推理与上段显示并行（HTTP 线程），
        结果暂存 _next_*，渲染线程激活时才装载（_activate_segment）。
        注意：绝不能作废 _idle_lip_cache（2026-11-21 空闲张嘴根因）。"""
        # 2026-11-23 段间卡顿根因修复：GPT-SoVITS 产出的 wav 自带 0.6–1.4s
        # 尾静音、0.2–0.56s 首静音，流水线不裁剪 → 当作有效语音帧播完才激活
        # 下段 → 每句之间 1–3s 死寂（听感卡顿/跳帧）。先裁掉源首尾静音，段尾
        # 再统一补 AUDIO_TAIL_SILENCE_S 供收口。
        pcm_float = self._trim_silence(pcm_float)
        if len(pcm_float) == 0:
            print("[RenderLoop] _infer_segment: all-silence after trim, ignored")
            return None
        if from_idle:
            # idle→audio 起音渐变：在裁过首静音的语音前预置 ramp 静音，mel 窗
            # 从静音渐变到语音 → 前若干帧口型由闭渐开，消除闭嘴→开口 snapping。
            ramp_s = self._idle_onset_ramp_s()
            if ramp_s > 0:
                ramp = np.zeros(int(round(ramp_s * 16000)),
                                dtype=pcm_float.dtype)
                pcm_float = np.concatenate([ramp, pcm_float])
        # 句尾静音填充（AUDIO_TAIL_SILENCE_S，原版机制还原）后再转 mel：
        # 末段帧的 mel 窗完全落入静音 → 收口系数闭合，hold 窗口不再持有
        # 开口末帧。
        # 连续流（EDTALK_SEAMLESS）下段 pending 就绪时，_maybe_activate_pending
        # 在 produced>=total（语音刚渲染完）即提前激活，抢先于 tail 帧产出 → tail
        # 静音不会在段间播出、不制造缺口；仅在真正句末（无 pending）时 tail 帧
        # 产出，用于 mel 窗自然收口。故两种模式都保留尾静音填充。
        tail_frames = int(round(self.AUDIO_TAIL_SILENCE_S * self.target_fps))
        if tail_frames > 0:
            pcm_float = np.pad(
                pcm_float, (0, tail_frames * (16000 // self.target_fps)))
        mel_input, bs, T = self.audio_processor.pcm_to_mel(pcm_float)
        if mel_input is None or T <= 0:
            print("[RenderLoop] _infer_segment: empty audio, ignored")
            return None

        pcm_duration = len(pcm_float) / 16000.0
        lip_duration = T / self.target_fps
        print(f"[RenderLoop] infer: pcm={pcm_duration:.3f}s, T={T} frames, "
              f"lip_dur={lip_duration:.3f}s")

        # Frame-locked audio chunks: one 640-sample slice per lip frame.
        samples_per_frame = 16000 // self.target_fps
        needed = T * samples_per_frame
        pcm_f = np.asarray(pcm_float, dtype=np.float32)
        if len(pcm_f) < needed:
            pcm_f = np.pad(pcm_f, (0, needed - len(pcm_f)))
        chunks = [pcm_f[i * samples_per_frame:(i + 1) * samples_per_frame]
                  for i in range(T)]

        # Progressive lip buffer（结果累积到局部 lip_all，激活时一次性装载）
        lip_all = torch.zeros(T, 20, dtype=torch.float32)
        chunk = self.LIP_CHUNK_FRAMES
        if self.lip_mode != 'on':
            # 口型推理关闭（画外音）：跳过 Audio2Lip GPU 推理，系数置零
            # （闭嘴）；音频 chunk 已按帧切好，帧锁定出声不受影响。
            print("[RenderLoop] 口型推理关闭（画外音）——跳过 Audio2Lip，"
                  "音频照常帧锁定播放")
        else:
            for s in range(0, T, chunk):
                e = min(s + chunk, T)
                hs, he = max(0, s - 1), min(T, e + 1)
                mel_chunk = mel_input[0, hs:he]
                mel_chunk = self._pad_mel_chunk(mel_chunk)
                lip = self.model_manager.infer_lip(
                    mel_chunk.reshape(-1, 1, 80, 16), 1, mel_chunk.shape[0])
                keep = lip[s - hs: s - hs + (e - s)]
                lip_all[s:e] = keep.detach().to(torch.float32)
                # Yield to the render thread between chunks
                if e < T:
                    time.sleep(0.004)
        return lip_all, chunks, T, tail_frames, pcm_duration

    def _activate_segment(self, lip_all, chunks, T, tail_frames, pcm_duration,
                          continuous: bool = False):
        """装载已推理好的下段为当前活跃段。调用者必须已持 _push_lock。

        flush 上段未显示尾（tail 静音/hold/idle 尾帧，听感无损）→ 装载状态
        → seg_id++。激活时下段已全部推理就绪 → 首帧立即可显示，段间零间隙。
        任何激活（直接/immediate 打断/pending 兑现）都使之前缓冲的 pending
        失效：时间线已改变，旧 pending 绝不能在本段之后被错误激活播放。

        continuous=True（仅由 _maybe_activate_pending 在 EDTALK_SEAMLESS 下调
        用）：连续流接替——不 flush frame_buffer（上段未显示语音帧保留、顺序
        播出，下段帧尾随其后）、不清空 audio_prebuffer FIFO（上段尾音 chunk
        仍待播，下段 chunk 紧随 → 音频零缺口）、不 reset_preroll（输出不中
        断 → 无 1 帧画面冻结）、不 reset 音频/不 prime。上段残留帧数计入
        leftover 偏移，防止误判下段提前播完。"""
        cont = bool(continuous and self._seamless())
        # 保存上段 total（下段装载会覆盖 _seg_chunk_total），用于计算残留偏移。
        prev_total = int(self._seg_chunk_total)
        self._next_ready = False
        self._next_lip_coeffs = None
        self._next_audio_chunks = None
        if not cont:
            # 2026-11-23 显示序时钟：flush 改为帧头 staged 执行（含
            # audio_prebuffer FIFO 同步排空）——与 _flushed_frames 记账原子地
            # 发生在渲染线程两帧之间，消除 in-flight 帧 idx 竞态；新段首帧
            # 体态位置自动接续最后显示帧（零跳变）。在此之前的 1-2 tick 由
            # OutputStreamer 中流 re-arm 非阻塞路径（underrun 重发）垫画面。
            self._flush_buffer()
        # 2026-11-23 拖音修复 + 显示序时钟：段切换同步清空音频预缓冲 FIFO，防
        # 旧段垫底音频在新段激活后外泄。清空动作并入帧头 staged flush（见
        # _do_frame_flush）——与 frame_buffer flush、_flushed_frames 记账原子
        # 执行，且保证新段首帧的 chunk 在清空之后才入队。连续流例外：不清空
        # ——上段尾音（真实语音，tail=0 无填充）仍在 FIFO 待播，清空会截断句
        # 尾；下段 chunk 尾随其后经 topup 顺序推送 → 零缺口。
        # 段首视线修正宽限（深度不足时放行，见 _gaze_allowed 注释）
        self._arm_gaze_grace()
        if self.output_streamer is not None and not cont:
            # preroll(1)：段间最小间隙（40ms）。首帧 underrun 仅 1 块（20ms，
            # 听感不可察）且 OutputStreamer keepalive 兜底；不可用更大 preroll——
            # pending 偶发未触发时下段走冷激活，preroll 过大会把 40ms 间隙放大成
            # 120ms 可听卡顿（违背段间无缝目标）。连续流不 re-arm → 无冻结。
            self.output_streamer.reset_preroll(1)

        with self._audio_lock:
            self._lip_coeffs = lip_all
            self._audio_chunks = chunks
            self._total_lip_frames = T
            self._lip_filled = T
            self._last_rendered_lip_idx = -1
            # seamless 下段首帧 audio_start=False → _topup_audio 不 reset player
            # （reset 清软件缓冲但硬件 FIFO 仍播上段尾音 → 段间缺口）。不 prime、
            # 不 flush：首帧 chunk 经 audio_prebuffer FIFO 按生产序尾随上段末
            # chunk，topup 顺序推送 → 音频首尾相接零缺口。
            self._audio_arm_start = not self._seamless()
            # total 只计有效语音帧（不含 tail 静音）：displayed>=total 在有效
            # 语音播完即成立——不必等 ~0.3s tail 静音也显示完，消除段间空档。
            # tail 帧仍推 DAC 保活但不打 seg、不计完成度（见 _process_frame）；
            # 下段激活时 flush 丢弃它们（静音，听感无损）且不计 dropped。
            self._seg_chunk_total = max(0, T - tail_frames)
            self._seg_chunk_produced = 0
            if self.output_streamer is not None:
                # 连续流：上段残留语音帧 = 上段 total − 上段已显（已扣除前次
                # leftover）。作为偏移传入，防止这些帧先于下段显示时把下段已显
                # 计数撑高 → 误判下段提前播完 → 级联丢尾音。
                if cont:
                    _disp = self.output_streamer.seg_displayed_count()
                    _left = max(0, prev_total - int(_disp))
                else:
                    _left = 0
                self.output_streamer.reset_seg_displayed(leftover=_left)
        if (not self._seamless()
                and self.output_streamer is not None
                and self.audio_prebuffer is not None
                and os.environ.get('EDTALK_PRIME_BOUNDARY', '0') == '1'):
            # 2026-11-23 段间无缝（prime 预推，默认关）：实测 Fix 4 源静音裁剪
            # 已根治段间间隙且 prime 反而引入声领先口型 ~39ms 回归。seamless
            # 模式不走 prime——下段 chunk 经 FIFO 按序自然接续上段。
            self.output_streamer.prime_new_segment_audio(chunks)
        self._seg_id += 1
        duration_s = T / self.target_fps
        print(f"[RenderLoop] Ready seg#{self._seg_id}: T={T} lip coeffs, "
              f"voice_total={self._seg_chunk_total}, "
              f"audio={pcm_duration:.2f}s, lip={duration_s:.2f}s")

    def process_full_audio(self, pcm_float: np.ndarray, immediate: bool = False):
        """Run Audio2Lip on a full audio segment（pending 自动衔接入口）。

        上段有效语音仍在显示 → 下段预推理存 _next_*（pending，与上段显示并
        行），渲染线程在上段 displayed>=total 时激活（段间零间隙，省客户端
        轮询检测+HTTP ~100ms）；上段已结束/空闲 → 直接激活。
        immediate=True（song 切片/打断，客户端 wait_flight=False）：立即激活
        替换当前段（原 d3 替换语义），不存 pending——避免连续切片被缓冲覆盖
        丢句。帧锁定音频：PCM 切为每帧 640 采样挂 frame meta，OutputStreamer
        在显示时刻 push 给 player → 音频位置 ≡ 显示帧位置。
        """
        if self.audio_processor is None or self.model_manager is None:
            return

        with self._push_lock:
            self._debug_audio_chunks += 1
            if not immediate and self._segment_active():
                res = self._infer_segment(pcm_float)
                if res is None:
                    return
                lip_all, chunks, T, tail_frames, pcm_duration = res
                self._next_lip_coeffs = lip_all
                self._next_audio_chunks = chunks
                self._next_total = T
                self._next_tail_frames = tail_frames
                self._next_pcm_duration = pcm_duration
                self._next_ready = True
                print(f"[RenderLoop] pending buffered: T={T} "
                      f"voice={max(0, T - tail_frames)}")
                return
            # 直接激活（idle→audio 首启 / immediate 打断）：仅"真 idle"
            # （_lip_coeffs is None，闭嘴）走起音渐变 ramp——posthold 内嘴仍
            # 保持张开，若强加 ramp 会先闭合再张开造成多余跳变；immediate 打
            # 断也不加 ramp。_lip_coeffs 引用读受 GIL 保护，仅用于决定渐变。
            from_idle = (not immediate) and (self._lip_coeffs is None)
            res = self._infer_segment(pcm_float, from_idle=from_idle)
            if res is None:
                return
            self._activate_segment(*res)

    def _maybe_activate_pending(self) -> None:
        """渲染线程每帧检查：上段有效语音显示完且有 pending → 立即激活下段。

        默认严格等 displayed>=total 才激活（防丢上段尾帧）。下段已预推理就绪
        → 激活零推理，首帧本帧/下一帧立即可显示，段间 gap ≈ 首帧延迟。

        EDTALK_SEAMLESS 连续流：上段语音渲染一结束（produced>=total）即提前
        激活下段。渲染比显示超前约 1 个 buffer 深度（~320ms），若等 displayed
        追上，该窗口内 player 会播空补零（上段尾音垫底仅 ~120ms）→ 段间缺
        口。提前激活让下段帧/音频紧跟上段，零窗口零缺口；上段残留未显示帧由
        _activate_segment(continuous=True) 计入 leftover 偏移，不丢音。"""
        with self._push_lock:
            if not self._next_ready:
                return
            seamless = self._seamless()
            if self._segment_active():
                if not (seamless
                        and self._seg_chunk_total > 0
                        and self._seg_chunk_produced >= self._seg_chunk_total):
                    return
            lip_all = self._next_lip_coeffs
            chunks = self._next_audio_chunks
            T = self._next_total
            tail_frames = self._next_tail_frames
            pcm_duration = self._next_pcm_duration
            self._next_ready = False
            self._next_lip_coeffs = None
            self._next_audio_chunks = None
            self._activate_segment(lip_all, chunks, T, tail_frames, pcm_duration,
                                   continuous=seamless)

    def _next_lip_frame(self) -> Optional[tuple]:
        """Consume the next lip frame — FRAME-LOCKED audio (consumer-paced).

        Called once per PRODUCED frame. Returns one of:
          None                          — no audio stream active (idle mouth)
          ('hold',)                     — inference behind: keep the idle
                                          (closed) mouth this frame and
                                          allocate NO audio chunk — the audio
                                          waits for the lips (never lags)
          ('play', coeff, idx, first,   — advance: coeff[idx] for the frame,
                 chunk)                   chunk[idx] rides the frame's meta;
                                          first=True only on the first
                                          advancing frame after a push
                                          (carries the player reset marker);
                                          chunk=None on the post-speech hold

        The OutputStreamer pushes chunk[idx] to the player at DISPLAY time
        (its precision 40ms clock), so audio position ≡ displayed-frame
        position ≡ lip position by construction. The old wall-clock scheme
        (audio free-runs on the device, lips chase it via elapsed-time
        compensation) froze/zeroed the lips whenever inference lagged —
        the 1-2s desync this replaces.
        """
        with self._audio_lock:
            if self._lip_coeffs is None or self._total_lip_frames == 0:
                return None
            nxt = self._last_rendered_lip_idx + 1
            total = self._total_lip_frames
            if nxt >= total + self._audio_hold_frames:
                # speech finished and the hold window elapsed → back to idle
                self._lip_coeffs = None
                self._audio_chunks = None
                return None
            if nxt >= total:
                # speech ended — hold the final mouth shape ~1s (no audio)
                self._last_rendered_lip_idx = nxt
                return ('play', self._lip_coeffs[total - 1:total],
                        total - 1, False, None)
            if nxt >= self._lip_filled:
                # progressive inference behind this frame: HOLD — no advance,
                # no chunk (the audio will wait for the lips)
                return ('hold',)
            self._last_rendered_lip_idx = nxt
            first = self._audio_arm_start
            self._audio_arm_start = False
            chunk = (self._audio_chunks[nxt]
                     if self._audio_chunks is not None else None)
            return ('play', self._lip_coeffs[nxt:nxt + 1], nxt, first, chunk)

    def _pad_mel_chunk(self, mel_rows: torch.Tensor) -> torch.Tensor:
        """Pad a mel chunk [n, 1, 80, 16] to the fixed inference batch size."""
        target = self.LIP_INFER_BATCH
        n = mel_rows.shape[0]
        if n >= target:
            return mel_rows
        pad = mel_rows[-1:].expand(target - n, *mel_rows.shape[1:])
        return torch.cat([mel_rows, pad], dim=0)

    def _get_idle_lip_coeff(self) -> Optional[torch.Tensor]:
        """Get a closed-mouth lip coefficient for idle mode."""
        if self._idle_lip_cache_valid and self._idle_lip_cache is not None:
            return self._idle_lip_cache

        if (self.audio_processor is not None
                and self.model_manager is not None):
            # 固定窗口（位置 0）而非推进式消费：重算必须确定性复现同一闭嘴
            # 系数（见 process_full_audio 内注释，2026-11-21 空闲张嘴根因）。
            idle_pcm = self.audio_processor.idle_pcm_window()
            mel_input, bs, mel_T = self.audio_processor.pcm_to_mel(idle_pcm)

            if mel_input is not None:
                mel_input_flat = self._pad_mel_chunk(
                    mel_input.view(-1, 1, 80, 16))
                lip_coeffs = self.model_manager.infer_lip(
                    mel_input_flat, 1, mel_input_flat.shape[0])
                lip_coeffs = lip_coeffs[:1]
                self._idle_lip_cache = lip_coeffs.detach().clone()
                self._idle_lip_cache_valid = True
                return self._idle_lip_cache

        return None

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    def _check_segment_schedule(self) -> None:
        """Fire queued segment_end switches, then advance the scheduler.

        MUST run at frame HEAD (before _process_frame): the completion check
        and the switch transaction both land between two produced frames, so
        the display-order clock never overshoots `total` and no wrap-around
        frame can flash 40ms of the old segment's frame 0 after the switch
        (ENG F5 — guaranteed by construction now, see _video_idx).

        Ordering (M16/L1/L2): the queued segment_end switch is checked
        BEFORE the scheduler None/paused early-return — it must fire in
        legacy mode too — and BEFORE scheduler.advance(), which would
        otherwise steal the boundary.

        The completion check shares the display-order clock (_video_base) —
        re-anchoring the base by a manual switch inside the lock resets the
        position to 0 and naturally cancels this advance (ENG S3).
        """
        if self._pending_switch is not None:
            with self._segment_lock:
                nxt = self._pending_switch
                cd = self._character_data
                if cd is None:
                    return
                total = cd.total_frames
                if self._video_pos() < total:
                    return
                self._pending_switch = None
                try:
                    self.switch_segment(nxt, source='pending', seamless=True)
                except Exception as e:
                    # Same log-storm guard as the scheduler path: re-anchor.
                    self._video_base = self._frame_index - self._flushed_frames
                    print(f"[Scheduler] pending switch to '{nxt}' failed "
                          f"({e}); restarting current segment")
                return

        if self._scheduler is None or self._scheduler.paused:
            return
        with self._segment_lock:
            cd = self._character_data
            if cd is None:
                return
            total = cd.total_frames
            if total <= 0:
                return
            if self._video_pos() < total:
                return
            nxt = self._scheduler.advance()
            try:
                self.switch_segment(nxt, source='scheduler', seamless=True)
            except Exception as e:
                # Re-anchor the CURRENT segment so this failure doesn't
                # re-trigger every frame (log-storm / chain-advance guard);
                # advance() already moved the index — the next boundary
                # naturally skips the broken segment.
                self._video_base = self._frame_index - self._flushed_frames
                print(f"[Scheduler] switch to '{nxt}' failed ({e}); "
                      f"restarting current segment")

    def _video_pos(self) -> int:
        """Display-order position in the current segment (unwrapped).

        2026-11-23 开口跳帧根治：位置 = 累计产出 − flush 累计丢弃 − 段基线。
        显示顺序恒等于产出顺序（FIFO 帧缓冲 + put 满载背压零丢帧），被 flush
        丢弃的帧永不显示，故该值与「本段内实际已显示帧数」严格一致——flush/
        停顿/追赶后下一帧自动接续最后显示帧的体态位置（零跳变）。
        """
        return self._frame_index - self._flushed_frames - self._video_base

    def _video_idx(self, total_frames) -> int:
        """Display-order position in the current segment (wraps at the end)."""
        try:
            total = int(total_frames)
        except (TypeError, ValueError):
            return max(0, self._video_pos())
        if total <= 0:
            return 0
        return self._video_pos() % total

    def _process_frame(self) -> None:
        """Render one frame and push it into the output buffer."""
        t_total_0 = time.perf_counter()
        timings = {}

        with self._segment_lock:
            character_data = self._character_data

        if character_data is None:
            print("[RenderLoop] ERROR: No character data!")
            return

        # Frame-boundary param application (M2): staged by API threads,
        # applied here on the render thread — serialized with graph replay.
        self._apply_pending_params()

        # pending 自动衔接（2026-11-23）：上段有效语音显示完且有预推理好的
        # pending → 立即激活下段（零推理），使本帧即可消费下段首帧，段间零间隙。
        self._maybe_activate_pending()

        # staged 帧头 flush（2026-11-23 显示序时钟）：必须在 video_idx 计算
        # 之前执行——flush 与 _flushed_frames 记账原子地落在两帧之间，保证
        # 本帧体态位置严格接续最后显示帧（零跳变）。见 _flush_buffer。
        self._do_frame_flush()

        total = character_data.total_frames
        video_idx = self._video_idx(total)

        # Pick lip coefficient: frame-locked audio consumption or idle.
        # The consumed frame's audio chunk rides the frame meta — the
        # OutputStreamer pushes it to the player at display time, so the
        # audio position IS the displayed lip position (never lags).
        audio_meta = None
        lip_result = self._next_lip_frame()
        if lip_result is None:
            alpha_D_lip = self._get_idle_lip_coeff()
        elif lip_result[0] == 'hold':
            # Progressive inference behind this frame: closed mouth, no
            # chunk allocated — the audio waits for the lips.
            alpha_D_lip = self._get_idle_lip_coeff()
            # 帧锁定音频保活（2026-11-21 撕裂修复）：hold 帧推进静音块喂
            # DAC——此前 hold 期间零推送造成音频设备断流爆音（声音撕裂）。
            # 静音块不计入句段完成度（非句子 chunk），时间线语义不变。
            audio_meta = {'audio': np.zeros(16000 // self.target_fps,
                                            dtype=np.float32),
                          'audio_start': False}
        else:
            _, alpha_D_lip, _lip_idx, first, chunk = lip_result
            if chunk is not None:
                # 2026-11-23 拖音修复：tail 静音帧（_lip_idx >= 有效语音总数）
                # 仍推 chunk 给 DAC 保活（口型收口 + 设备不断流），但不打 seg
                # 标、不计完成度——它们不是有效语音，不应阻塞下一句推送；下段
                # flush 丢弃它们也不计入 dropped（无尾音截断）。
                is_voice = _lip_idx < self._seg_chunk_total
                audio_meta = {'audio': chunk, 'audio_start': bool(first),
                              'seg': is_voice}
                if is_voice:
                    self._seg_chunk_produced += 1
            else:
                # 句后 hold（nxt 越过 total）：保持收口口型显示，但向 DAC 喂
                # 等长静音块保活——批次六补全：句后 hold 此前返回 chunk=None
                # 致 audio_meta 为空、零推送，speech→idle 边界 player 缓冲跑
                # 空补零（欠载）。与句前 hold 对称处理；不计入 seg 完成度、
                # 不打 seg 标（时间线语义不变）。
                if self._seamless() and self._next_ready:
                    # 连续流接替：pending 已就绪、下段即将激活——不向 DAC 推
                    # 句后静音（渲染超前产出的 ~8 帧静音会先于下段播出，制造
                    # 段间缺口）。audio_meta=None 不入 prebuffer；player 靠上
                    # 段尾音垫底，下段首帧 chunk 紧随其后零缺口衔接。
                    audio_meta = None
                else:
                    audio_meta = {'audio': np.zeros(
                        16000 // self.target_fps, dtype=np.float32),
                        'audio_start': False}

        # 2026-11-23 音节内拖音修复：本帧 audio_meta 同时入音频预缓冲 FIFO，
        # 供 OutputStreamer 提前垫底（吸收软件时钟抖动）。idle 帧 audio_meta
        # 为 None（无音频段）不入队。
        if audio_meta is not None and self.audio_prebuffer is not None:
            self.audio_prebuffer.put(audio_meta)

        # Load frame data (never blocks on batch I/O)
        character_data.ensure_loaded(video_idx)
        orifra, cropped, meta, facemark, pre_mask = character_data.get_frame(video_idx)

        # 画外音直通（无人脸帧 / 口型推理关闭）：跳过生成器/视线/修复/合成全套
        # GPU 推理，原帧直出。音频 chunk 已挂 frame_meta（帧锁定出声不受影响）
        # ——切到无人脸素材时不再报错，也不再空烧 GPU（口型系数无处可贴）。
        # 判定口径：预处理对未检出人脸的帧写全 NaN 关键点，任一 NaN 即视为
        # 本帧无脸（与 compositor 的 NaN 拒绝口径一致）。
        if (self.lip_mode == 'off' or facemark is None
                or bool(np.isnan(facemark).any())):
            t_put_0 = time.perf_counter()
            out_frame = self._composite_out(orifra)
            if lip_result is None:
                self._last_idle_frame = out_frame  # P3 静音填充：闭口帧缓存
            if self.frame_buffer is not None:
                self.frame_buffer.put(out_frame, meta=audio_meta)
            timings['put'] = (time.perf_counter() - t_put_0) * 1000.0
            timings['total'] = (time.perf_counter() - t_total_0) * 1000.0
            self._record_timings(timings)
            self._frame_index += 1
            return

        # Generator inference — output stays ON GPU (CHW BGR uint8) so face
        # repair can run GPU-inline (IOBinding, zero copy). Single GPU->CPU
        # download happens once, after repair, below.
        # Pristine 256 crop (GAZE_TIMING_PLAN T1/X1): the decode-time copy of
        # the orifra crop region — blend base (fixes the loop-wrap mouth
        # drift) AND the 'ori' eye source. None → blend uses prepare_crop's
        # own crop and the ori-mode gaze skips this frame's paste.
        pristine = None
        if self.compositor is not None:
            pristine = character_data.get_pristine_crop(video_idx, orifra, meta)

        rendered_gpu_chw = None
        t_gen_0 = time.perf_counter()
        rendered_c = None
        if self.model_manager is not None and cropped is not None and alpha_D_lip is not None:
            rendered = self.model_manager.infer_frame(cropped, alpha_D_lip, cropped)
            rendered_c = rendered.clamp(-1, 1)

            # Gaze correction — 单调用点，repair 之前（GAZE_TIMING_PLAN 最终门
            # 裁决 2：全脸统一修复）。两种 eye_source 的差异只剩眼源张量：
            #   'rendered' = legacy Route C'（自纠正，整体门控，逐位现状/U11）
            #   'ori'      = 原生眼贴回 rendered（调用不受门控/ENG F2，生成段
            #                受 _gaze_allowed）——纠正后的整脸随后进 GPEN，
            #                眼与嘴鼻获得同一遍修复锐化。
            if self.gaze_corrector is not None:
                gaze_frame = character_data.get_gaze_frame(video_idx) \
                    if hasattr(character_data, 'get_gaze_frame') else None
                if self.gaze_corrector.eye_source_mode == 'rendered':
                    if self._gaze_allowed():
                        t_gaze_0 = time.perf_counter()
                        rendered_c = self.gaze_corrector.correct(
                            rendered_c[0], rendered_c[0], facemark, gaze_frame).unsqueeze(0)
                        timings['gaze'] = (time.perf_counter() - t_gaze_0) * 1000.0
                elif pristine is not None:
                    t_gaze_0 = time.perf_counter()
                    try:
                        # uint8 BGR HWC CPU → float32 RGB CHW [-1,1] GPU (~0.3ms H2D)
                        eye_source = torch.from_numpy(
                            np.ascontiguousarray(
                                cv2.cvtColor(pristine, cv2.COLOR_BGR2RGB)
                                .astype(np.float32).transpose(2, 0, 1)
                            ) / 127.5 - 1.0
                        ).to(self.model_manager.device)
                        corrected = self.gaze_corrector.correct(
                            eye_source, rendered_c[0], facemark, gaze_frame,
                            generate=self._gaze_allowed())
                        rendered_c = corrected.unsqueeze(0)
                    except Exception as e:
                        # 降级链：该帧维持 Generator 眼（corrector 内部异常
                        # 由其自计数，此处只计转换/上传侧，首帧打栈）。
                        self.gaze_corrector.error_skips += 1
                        if self.gaze_corrector.error_skips == 1:
                            import traceback
                            print(f"[RenderLoop] ori-mode gaze failed ({e}); "
                                  f"frame keeps rendered eyes. Traceback (once):")
                            traceback.print_exc()
                    timings['gaze'] = (time.perf_counter() - t_gaze_0) * 1000.0

            rendered_post = (rendered_c[0] * 127.5 + 127.5).to(torch.uint8)
            rendered_gpu_chw = rendered_post[[2, 1, 0], :, :].contiguous()
        if 'gaze' not in timings:
            timings['gaze'] = 0.0
        timings['gen'] = (time.perf_counter() - t_gen_0) * 1000.0

        # CPU prep: resolve mask and resize ori crop for blending.
        t_comp_0 = time.perf_counter()
        prepared_crop = None
        mask_256 = None
        if self.compositor is not None and facemark is not None:
            mask_256 = self._resolve_mask(facemark, pre_mask)
            prepared_crop = self.compositor.prepare_crop(orifra, meta)
            if prepared_crop is not None and pristine is not None:
                # X1: blend against the pristine 256 crop instead of the
                # blend-mutated orifra buffer — the paste-back base no longer
                # drifts across loop wraps.
                prepared_crop = (pristine, prepared_crop[1])
        timings['comp'] = (time.perf_counter() - t_comp_0) * 1000.0

        # Face repair — 整脸统一处理（GAZE_TIMING_PLAN 最终门裁决 2）：视线
        # 纠正后的眼睛随整脸一起进 GPEN，眼与嘴鼻获得同一遍修复锐化。
        # （GPEN 为保结构修复网——legacy rendered 模式数周生产验证纠正方向
        # 经修复保留。）
        t_repair_0 = time.perf_counter()
        if (self.face_repair is not None and rendered_gpu_chw is not None
                and self._repair_allowed()):
            # GPU-inline: tensor in, tensor out; no numpy round trip.
            rendered_gpu_chw = self.face_repair.repair_gpu(rendered_gpu_chw)
        timings['repair'] = (time.perf_counter() - t_repair_0) * 1000.0

        # Single GPU->CPU download of the (optionally repaired) render.
        rendered_np = None
        if rendered_gpu_chw is not None:
            rendered_np = rendered_gpu_chw.permute(1, 2, 0).cpu().numpy()

        # Composite mouth onto original frame
        if self.compositor is not None and rendered_np is not None and mask_256 is not None:
            composited = self.compositor.blend(orifra, rendered_np, mask_256, prepared_crop)
        else:
            composited = orifra

        # Frame meta: the audio chunk rides the frame — the OutputStreamer
        # pushes it at display time (frame-locked sync; no wall clock).
        frame_meta = audio_meta

        # Push into the output buffer (blocking when full — backpressure)
        composited = self._composite_out(composited)
        if lip_result is None:
            self._last_idle_frame = composited  # P3 静音填充：闭口帧缓存
        t_put_0 = time.perf_counter()
        if self.frame_buffer is not None:
            self.frame_buffer.put(composited, meta=frame_meta)
        timings['put'] = (time.perf_counter() - t_put_0) * 1000.0

        timings['total'] = (time.perf_counter() - t_total_0) * 1000.0
        self._record_timings(timings)

        self._frame_index += 1

    def _resolve_mask(self, facemark, precomputed_mask):
        """Resolve mask via compositor or return precomputed."""
        if precomputed_mask is not None:
            return precomputed_mask
        if self.compositor is not None:
            return self.compositor._resolve_mask(facemark, None)
        return None

    def _composite_out(self, frame):
        """统一出帧出口（R-eng11①：直通与 blend 两口均改道——2026-10-27 画面输出批次）。

        canvas_compositor 缺省缺席（上游兼容）：无合成器时原帧直出，行为与
        分叉前逐字节一致。合成异常在 compositor 内部降级直出并计数
        （R-eng11④），不进本循环的逐帧熔断（降级语义=直出而非停线）。
        """
        comp = getattr(self, 'canvas_compositor', None)
        if comp is None or not comp.enabled:
            return frame
        try:
            with self._segment_lock:
                seg = (self._character_data.current_segment
                       if self._character_data is not None else None)
            return comp.apply(frame, seg)
        except Exception as e:
            print(f"[RenderLoop] 画布合成外层异常（直出）: {e}")
            return frame

    def _repair_allowed(self) -> bool:
        """Whether face repair may run on this frame.

        'off': never (runtime hot switch may set this while the FaceRepair
        model stays constructed — the gate, not the model, is what turns).
        'on': every frame. 'adaptive': only while the output buffer has
        headroom (production comfortably ahead of consumption) — the
        ~9ms synchronous GPU wait is unaffordable otherwise.
        """
        if self.face_repair_mode == 'off':
            return False
        if self.face_repair_mode != 'adaptive':
            return True
        if self.frame_buffer is None:
            return True
        return self.frame_buffer.depth >= self.REPAIR_MIN_BUFFER

    def _gaze_allowed(self) -> bool:
        """Whether gaze correction may run on this frame (adaptive mode).

        'off': never (runtime hot switch may set this while the corrector
        stays constructed). 'on': every frame. 'adaptive': gaze costs
        ~2-3ms/frame of GPU work; adaptive shares the repair headroom
        threshold (REPAIR_MIN_BUFFER) — at that depth production is
        comfortably ahead of the 25fps output line.
        """
        if self.gaze_mode == 'off':
            return False
        if self.gaze_mode != 'adaptive':
            return True
        if self.frame_buffer is None:
            return True
        # 段首宽限（批次五：宽限至深度重建）：flush 后深度从 0/低位重建，
        # 固定 N 帧宽限无法覆盖深度爬升期——期间每帧退回未修正眼（用户实
        # 测段首视线回退）。armed 期间强制放行；深度达标（≥REPAIR_MIN_BUFFER）
        # 或帧数兜底耗尽后回归深度门控（GPU 余量保护语义不丢）。
        if self._gaze_grace > 0:
            self._gaze_grace_max -= 1
            if (self.frame_buffer.depth >= self.REPAIR_MIN_BUFFER
                    or self._gaze_grace_max <= 0):
                self._gaze_grace = 0
            return True
        return self.frame_buffer.depth >= self.REPAIR_MIN_BUFFER

    def _record_timings(self, timings: dict):
        """Append timings to rolling histogram."""
        with self._timing_lock:
            for k, v in timings.items():
                self._timings[k].append(v)
                if len(self._timings[k]) > self._timing_window:
                    self._timings[k].pop(0)

    def _timing_summary(self) -> str:
        """Return p50/p95/p99 summary string for the last frames."""
        import numpy as np
        with self._timing_lock:
            if not self._timings['total']:
                return ""
            parts = []
            for key in ['gen', 'gaze', 'comp', 'repair', 'put', 'total']:
                vals = self._timings[key]
                if not vals:
                    continue
                p50, p95, p99 = np.percentile(vals, [50, 95, 99])
                parts.append(f"{key}=p50:{p50:.1f}/p95:{p95:.1f}/p99:{p99:.1f}ms")
            return " ".join(parts)

    def _arm_gaze_grace(self) -> None:
        """段首视线修正宽限重臂（flush 双路径调用；语义见 _gaze_allowed 注释）。"""
        self._gaze_grace = 1
        self._gaze_grace_max = 150

    def _flush_buffer(self) -> None:
        """Stage a frame-buffer drain for execution at the next frame head.

        2026-11-23 显示序时钟：flush 必须与 _flushed_frames 记账原子地发生在
        渲染线程两帧之间。若在 API/HTTP 线程直接 flush：正处于渲染中的帧
        （idx 已按 flush 前基线算好）会在 flush 后完成入队并幸存显示，其实际
        显示序位置比 idx 落后 Q_flush 帧 → 体态前跳 Q 帧。渲染线程帧头执行时
        不存在 in-flight 帧（上一帧已入队被丢弃、本帧 idx 在 flush 之后计算）
        → 显示序位置与实际显示严格一致。audio_prebuffer FIFO 同步排空（拖音
        修复语义），并保证新段首帧 chunk 在清空之后才入队。连续流（seamless
        cont）不经过此路径——不 flush，位置自然连续。
        """
        self._pending_frame_flush = True

    def _do_frame_flush(self) -> None:
        """Execute the staged frame-buffer drain (render thread, frame head).

        MUST run before the frame's video_idx computation — see
        _flush_buffer / _video_pos.
        """
        if not self._pending_frame_flush:
            return
        self._pending_frame_flush = False
        if self.frame_buffer is not None:
            dropped = self.frame_buffer.flush_for_new_audio()
            # 显示序时钟记账：被丢弃的帧永不显示，计入累计丢弃，使下一帧
            # 体态位置严格接续最后显示帧（零跳变）。
            self._flushed_frames += self.frame_buffer.last_flush_total
            if dropped:
                print(f"[RenderLoop] Flushed {dropped} unplayed speech frames")
        # 拖音修复语义：段切换同步排空音频预缓冲 FIFO，防旧段垫底音频在新段
        # 激活后外泄；新段首帧 chunk 在本帧头之后才入队（_next_lip_frame 消费
        # 在 flush 之后）。
        if self.audio_prebuffer is not None:
            self.audio_prebuffer.flush()

    def get_idle_frame(self) -> Optional[np.ndarray]:
        """P3 静音填充供给（OutputStreamer.set_idle_frame_provider 消费）。

        无音频段在播（_lip_coeffs=None——flush 后空闲/句后 hold 期满）时
        返回最近产出的闭口 idle 帧；音频段在播返回 None（streamer 维持
        _last_sent 重发语义，口型连续）。异常自守不抛。"""
        try:
            if self._lip_coeffs is not None:
                return None
            return self._last_idle_frame
        except Exception:
            return None

    @property
    def frame_index(self) -> int:
        """Get current frame index."""
        return self._frame_index
