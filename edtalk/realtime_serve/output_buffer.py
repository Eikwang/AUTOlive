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
                 preroll_frames: int = 4):
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

        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

        self._underruns = 0
        self._output_frames = 0
        self._last_sent: Optional[np.ndarray] = None
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
                self._preroll_ready.set()
                return True
            # Poll at 20Hz; FrameBuffer.put will not notify us directly.
            time.sleep(0.05)
        return self._running

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
            # Preroll gate (re-armable after flush / recovery).
            if self.preroll_frames > 0 and not self._preroll_ready.is_set():
                if not preroll_announced:
                    print(f"[OutputStreamer] Preroll: waiting for {self.preroll_frames} frames")
                    preroll_announced = True
                if not self._wait_preroll():
                    return
                print(f"[OutputStreamer] Preroll complete ({self.preroll_frames} frames); "
                      f"resuming output at {self.target_fps}fps")
                preroll_announced = False

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

            if frame is not None:
                # Frame-locked audio (E-sync): push this display tick's audio
                # chunk NOW — audio position ≡ displayed lip position, with
                # only the DAC latency (~20-40ms) between them. audio_start
                # clears any stale audio from the previous segment first.
                if meta and self.audio_player is not None:
                    try:
                        if meta.get('audio_start'):
                            self.audio_player.reset()
                        chunk = meta.get('audio')
                        if chunk is not None:
                            self.audio_player.push_pcm(chunk)
                    except Exception as e:
                        print(f"[OutputStreamer] audio push error: {e}")

                if self.sender is not None:
                    try:
                        if self.sender(frame):
                            self._last_sent = frame
                    except Exception as e:
                        print(f"[OutputStreamer] Sender error: {e}")
                else:
                    # No sink: drop the frame but keep last_sent for underrun.
                    self._last_sent = frame

                with self._lock:
                    self._output_frames += 1
            else:
                # Underrun: re-send previous frame to keep camera fed.
                with self._lock:
                    self._underruns += 1
                if self._last_sent is not None and self.sender is not None:
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
            }
