"""Audio player for synchronized audio output during rendering.

Audio playback strategy:
------------------------
The AudioPlayer receives the FULL PCM audio at once and plays it
independently via sounddevice's callback at real-time speed. The
render loop uses wall-clock time to determine which lip coefficient
corresponds to the current audio position — it does NOT control
audio playback timing.

This is the opposite of the "frame-locked push" approach where audio
chunks were pushed per video frame. That approach failed because:
- UnityCapture backend's sleep_until_next_frame() doesn't actually sleep
- Render speed was slightly faster than real-time (35ms + 3ms < 40ms)
- This caused audio buffer accumulation and lip movement finishing
  before audio

Current approach: Audio plays at exact real-time speed independently.
Lip coefficients are selected based on wall-clock time, ensuring they
always match the audio position regardless of GPU render rate.
"""
import threading
import numpy as np

try:
    import sounddevice as sd
    SD_AVAILABLE = True
except ImportError:
    SD_AVAILABLE = False


class AudioPlayer:
    """Plays PCM audio independently via sounddevice callback.

    The AudioPlayer receives full PCM data via push_pcm() and plays
    it through sounddevice at real-time speed. The render loop queries
    buffered_ms for diagnostics but does NOT use it for sync.
    """

    SAMPLE_RATE = 16000

    def __init__(self, sample_rate: int = 16000, enabled: bool = True,
                 pre_buffer_ms: int = 0):
        """Initialize AudioPlayer.

        Args:
            sample_rate: Audio sample rate (default: 16000)
            enabled: If False, skip audio playback (silent mode)
            pre_buffer_ms: Ignored (kept for API compatibility).
        """
        self.sample_rate = sample_rate
        self.enabled = enabled and SD_AVAILABLE
        self._stream = None
        self._lock = threading.Lock()
        self._buffer = np.array([], dtype=np.float32)
        self._overflow_count = 0
        self._playback_started = False
        self._total_pushed_samples: int = 0
        # Smoothed output latency (callback consumption -> DAC), seconds.
        # The render loop subtracts this from wall-clock time so lips do
        # not lead the audible audio.
        self._output_latency_s: float = 0.0

        if self.enabled:
            try:
                self._stream = sd.OutputStream(
                    samplerate=sample_rate,
                    channels=1,
                    dtype='float32',
                    callback=self._audio_callback,
                    blocksize=int(sample_rate * 0.02),  # 20ms blocks
                    latency='low'
                )
                # Start stream immediately — it will play silence
                # until push_pcm() provides data
                self._stream.start()
                self._playback_started = True
                print(f'[AudioPlayer] Independent playback mode: {sample_rate}Hz')
            except Exception as e:
                print(f'[AudioPlayer] WARNING: Failed to configure audio stream: {e}')
                self.enabled = False
        elif not SD_AVAILABLE:
            print('[AudioPlayer] sounddevice not available, audio output disabled')
        else:
            print('[AudioPlayer] Audio output disabled')

    def push_pcm(self, pcm: np.ndarray):
        """Push PCM audio data for playback.

        Typically called once with the full audio segment.
        AudioPlayer plays it independently at real-time speed.

        Args:
            pcm: Audio data as float32 array, range [-1, 1]
        """
        if not self.enabled:
            return

        with self._lock:
            self._buffer = np.concatenate([self._buffer, pcm.astype(np.float32)])
            self._total_pushed_samples += len(pcm)

    def reset(self):
        """Reset audio player state for a new audio segment.

        Clears the internal buffer and resets counters.
        Called by render_loop.process_full_audio() before pushing
        new audio.
        """
        if not self.enabled:
            return

        with self._lock:
            self._buffer = np.array([], dtype=np.float32)
            self._total_pushed_samples = 0
            self._overflow_count = 0

        print('[AudioPlayer] Reset for new segment')

    def _audio_callback(self, outdata: np.ndarray, frames: int,
                        time_info, status):
        """sounddevice callback — fills output buffer from internal queue."""
        if status and self._overflow_count < 3:
            print(f'[AudioPlayer] Underflow: {status}')
            self._overflow_count += 1

        # Track output latency (callback consumption -> DAC) for lip sync
        try:
            dac = getattr(time_info, 'outputBufferDacTime', 0.0)
            cur = getattr(time_info, 'currentTime', 0.0)
            if dac and cur:
                lat = float(dac) - float(cur)
                if 0.0 < lat < 1.0:
                    self._output_latency_s = (
                        0.9 * self._output_latency_s + 0.1 * lat)
        except Exception:
            pass

        with self._lock:
            if len(self._buffer) >= frames:
                outdata[:, 0] = self._buffer[:frames]
                self._buffer = self._buffer[frames:]
            else:
                # Not enough data — pad with silence
                available = len(self._buffer)
                outdata[:available, 0] = self._buffer
                outdata[available:, 0] = 0.0
                self._buffer = np.array([], dtype=np.float32)

    def close(self):
        """Stop and close the audio stream."""
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
            print('[AudioPlayer] Closed')

    @property
    def buffered_ms(self) -> float:
        """Get current buffered audio duration in milliseconds."""
        with self._lock:
            return len(self._buffer) / self.sample_rate * 1000

    @property
    def output_latency_s(self) -> float:
        """Smoothed output latency in seconds (callback -> DAC)."""
        return self._output_latency_s

    @property
    def elapsed_seconds(self) -> float:
        """Get elapsed playback time in seconds.

        Based on samples consumed by the sounddevice callback.
        NOT used for sync in the current design — wall-clock time
        is used instead. Kept for diagnostics.
        """
        if not self._playback_started:
            return 0.0
        with self._lock:
            played_samples = self._total_pushed_samples - len(self._buffer)
        return max(0.0, played_samples / self.sample_rate)
