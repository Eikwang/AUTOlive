"""Thread-safe audio buffer for streaming PCM data.

Reference: §4.4, §2.4 in technical specification

Two push modes:
1. push(): Push a small chunk with sequence number (for streaming TTS)
2. push_full(): Push an entire audio segment at once (for file-based push)

The buffer stores all pushed audio and lets the render loop consume
it in fixed-size chunks (200ms). When a full segment is pushed, the
buffer is cleared first to avoid mixing old and new audio.
"""
import threading
from collections import deque
import numpy as np


class AudioBuffer:
    """Thread-safe audio buffer for streaming PCM data.

    The buffer supports:
    - push(): Push a small chunk with sequence ordering (streaming TTS)
    - push_full(): Push an entire audio segment at once (file-based push)
    - consume_chunk(): Inference thread calls to get 200ms chunks
    - Silent mode when buffer is empty (AC-008)

    Constants:
        CHUNK_SAMPLES: Chunk size for batch inference (200ms at 16kHz)
    """

    SAMPLE_RATE = 16000
    CHUNK_DURATION_MS = 200  # Batch inference unit
    CHUNK_SAMPLES = int(SAMPLE_RATE * CHUNK_DURATION_MS / 1000)  # 3200 samples

    def __init__(self, max_buffer_seconds: float = 0):
        """Initialize AudioBuffer.

        Args:
            max_buffer_seconds: Maximum buffer duration in seconds.
                0 means unlimited (for full-segment push mode).
                Set >0 to limit buffer size (for streaming TTS mode).
        """
        self._buf = deque()
        self._lock = threading.Lock()
        self._max_samples = int(self.SAMPLE_RATE * max_buffer_seconds) if max_buffer_seconds > 0 else 0

        # Sequence ordering: track the next expected sequence number
        # and hold out-of-order chunks until their predecessors arrive
        self._next_seq = 0
        self._pending = {}  # seq -> pcm_bytes (arrived out of order)
        self._max_pending = 100  # Safety limit

    def push(self, pcm: bytes, seq: int = 0):
        """Push a small PCM chunk into the buffer (streaming TTS mode).

        If buffer exceeds max size, oldest data is discarded.

        Args:
            pcm: PCM audio data (16kHz, mono, int16)
            seq: Sequence number for ordering (0-based, incrementing)
        """
        with self._lock:
            if seq < self._next_seq:
                # Duplicate or stale — discard
                return

            if seq == self._next_seq:
                # In order — enqueue immediately
                self._enqueue(pcm)
                self._next_seq += 1

                # Flush any pending chunks that are now in order
                while self._next_seq in self._pending:
                    self._enqueue(self._pending.pop(self._next_seq))
                    self._next_seq += 1
            else:
                # Out of order — hold until predecessors arrive
                if len(self._pending) < self._max_pending:
                    self._pending[seq] = pcm

    def push_full(self, pcm: bytes):
        """Push an entire audio segment at once (file-based push mode).

        Clears any existing buffer data and sequence state, then loads
        the full PCM segment. The render loop will consume it in 200ms
        chunks at its own pace. This ensures continuous, gapless audio
        without any chunk-boundary artifacts.

        Args:
            pcm: Full PCM audio data (16kHz, mono, int16)
        """
        with self._lock:
            # Clear existing data — new segment replaces old
            self._buf.clear()
            self._pending.clear()
            self._next_seq = 0

            # Convert and load entire segment
            samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
            self._buf.extend(samples.tolist())

    def _enqueue(self, pcm: bytes):
        """Convert PCM bytes to float32 samples and add to buffer."""
        samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
        self._buf.extend(samples.tolist())

        # Discard oldest data if exceeding max size (0 = unlimited)
        if self._max_samples > 0:
            while len(self._buf) > self._max_samples:
                self._buf.popleft()

    def consume_chunk(self, num_samples: int = 0) -> np.ndarray | None:
        """Consume a chunk of audio data for inference.

        Called by the inference thread. Returns None if buffer doesn't have
        enough data for a full chunk → AC-008 (silent mode).

        Args:
            num_samples: Number of samples to consume. If 0, uses CHUNK_SAMPLES
                (200ms). Can be set larger for longer inference windows (e.g.
                2 seconds for better lip coefficient continuity).

        Returns:
            np.ndarray of float32 samples, or None if buffer is empty.
        """
        n = num_samples if num_samples > 0 else self.CHUNK_SAMPLES
        with self._lock:
            if len(self._buf) < n:
                return None  # → AC-008: Not enough data, use silent mode

            # Pop n samples from the front
            chunk = np.array([self._buf.popleft() for _ in range(n)])
            return chunk

    def buffer_duration_ms(self) -> float:
        """Get current buffer duration in milliseconds.

        Returns:
            Duration in milliseconds
        """
        with self._lock:
            return len(self._buf) / self.SAMPLE_RATE * 1000

    def clear(self):
        """Clear all buffered audio data."""
        with self._lock:
            self._buf.clear()
            self._pending.clear()
            self._next_seq = 0
