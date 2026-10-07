"""Audio processing utilities for PCM to mel conversion and idle audio generation.

Reference: demo_change_a_video_lip.py:123-152, §4.5 in technical specification

Idle (standby) audio strategy:
    When no real audio is received, the system drives Audio2Lip with a low-volume
    noise WAV file (noice.wav, ~-48 dB). This produces lip coefficients that keep
    the mouth in a natural closed position — matching the model's training data
    distribution for quiet speech. Using zero lip coefficients or silent mel causes
    the mouth to be slightly open because the model's "closed mouth" does not
    correspond to the zero point of the lip latent space.
"""
import os
import numpy as np
import torch

# Import audio module from project
try:
    import audio
except ImportError:
    # Fallback for testing
    audio = None


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_SAMPLE_RATE = 16000
_CHUNK_SAMPLES = int(_SAMPLE_RATE * 0.2)  # 3200 samples = 200ms at 16kHz

# Path to the idle noise audio file (relative to this module)
_IDLE_AUDIO_FILENAME = 'noice.wav'


def parse_audio_length(audio_length: int, sr: int, fps: int) -> tuple:
    """Parse audio length into valid wav length and frame count.

    Args:
        audio_length: Original audio length in samples
        sr: Sample rate
        fps: Target frames per second

    Returns:
        Tuple of (wav_length, num_frames)
    """
    bit_per_frames = sr / fps
    num_frames = int(audio_length / bit_per_frames)
    audio_length = int(num_frames * bit_per_frames)
    return audio_length, num_frames


def crop_pad_audio(wav: np.ndarray, audio_length: int) -> np.ndarray:
    """Crop or pad audio to target length.

    Args:
        wav: Input audio array
        audio_length: Target length in samples

    Returns:
        Cropped or padded audio array
    """
    if len(wav) > audio_length:
        wav = wav[:audio_length]
    elif len(wav) < audio_length:
        wav = np.pad(wav, [0, audio_length - len(wav)], mode='constant', constant_values=0)
    return wav


def get_mel_from_pcm(pcm: np.ndarray, sr: int = 16000, fps: int = 25) -> tuple:
    """Convert PCM audio to mel spectrogram features.

    Args:
        pcm: PCM audio data (float32)
        sr: Sample rate
        fps: Target frames per second

    Returns:
        Tuple of (mel_input, bs, T) where:
            - mel_input: Mel spectrogram tensor [bs*T, 1, 80, 16]
            - bs: Batch size
            - T: Number of time steps
    """
    # Handle empty input
    if len(pcm) == 0:
        # Return silent mel with minimal valid shape
        silent_pcm = np.zeros(int(sr * 0.2), dtype=np.float32)
        return get_mel_from_pcm(silent_pcm, sr, fps)

    # Parse audio length
    wav_length, num_frames = parse_audio_length(len(pcm), sr, fps)

    # Crop or pad audio
    wav = crop_pad_audio(pcm, wav_length)

    # Compute mel spectrogram
    if audio is not None:
        orig_mel = audio.melspectrogram(wav).T
    else:
        # Fallback: simple STFT-based mel computation
        orig_mel = _compute_mel_fallback(wav, sr, fps)

    # Apply sliding window to get syncnet format
    spec = orig_mel.copy()
    indiv_mels = []
    syncnet_mel_step_size = 16

    # Calculate actual mel frames per second from the mel spectrogram dimensions.
    # hop_size=200 (standard Wav2Lip) → mel_fps = 16000/200 = 80.
    # This must match the training data preprocessing for correct lip coefficients.
    if audio is not None:
        mel_frames_per_sec = sr / float(audio.get_hop_size())
    else:
        mel_frames_per_sec = 80.0  # fallback, matches default

    for i in range(num_frames):
        start_frame_num = i - 2
        start_idx = int(mel_frames_per_sec * (start_frame_num / float(fps)))
        end_idx = start_idx + syncnet_mel_step_size
        seq = list(range(start_idx, end_idx))
        seq = [min(max(item, 0), orig_mel.shape[0] - 1) for item in seq]
        m = spec[seq, :]
        indiv_mels.append(m.T)

    indiv_mels = np.asarray(indiv_mels)  # T 80 16
    indiv_mels = torch.FloatTensor(indiv_mels).unsqueeze(1).unsqueeze(0)

    mel_input = indiv_mels.type(torch.FloatTensor)
    bs = mel_input.shape[0]
    T = mel_input.shape[1]

    return mel_input, bs, T


def _compute_mel_fallback(wav: np.ndarray, sr: int, fps: int) -> np.ndarray:
    """Fallback mel computation without audio module.

    Args:
        wav: Audio signal
        sr: Sample rate
        fps: Frames per second

    Returns:
        Mel spectrogram matrix
    """
    # Simple STFT
    n_fft = 512
    hop_length = sr // fps

    # Pad signal
    padded = np.pad(wav, int(n_fft // 2), mode='reflect')

    # Compute STFT
    windows = np.lib.stride_tricks.sliding_window_view(padded, n_fft)
    stft = np.fft.rfft(windows * np.hanning(n_fft))

    # Compute power spectrum
    power = np.abs(stft) ** 2

    # Simple mel filterbank (80 filters)
    n_mels = 80
    fmin = 0
    fmax = sr / 2

    mel_freqs = np.linspace(fmin, fmax, n_mels + 2)
    hz_to_mel = 2595 * np.log10(1 + 440 / 700)  # Simplified

    # Create mel filterbank
    fb = np.zeros((n_mels, stft.shape[-1]))
    for i in range(n_mels):
        left = int(mel_freqs[i] * n_fft / sr)
        center = int(mel_freqs[i + 1] * n_fft / sr)
        right = int(mel_freqs[i + 2] * n_fft / sr)
        for j in range(left, center):
            fb[i, j] = (j - left) / (center - left)
        for j in range(center, right):
            fb[i, j] = 1 - (j - center) / (right - center)

    # Apply mel filterbank
    mel = np.dot(fb, power.T)
    mel = np.log(mel + 1e-6)

    # Transpose to (n_time, n_mels) to match audio.melspectrogram(wav).T format
    # which is what the sliding window code expects
    return mel.T


class AudioProcessor:
    """Audio processor for converting PCM to mel features.

    Features:
    - pcm_to_mel(): Convert PCM chunk to mel features
    - consume_idle_pcm(): Get a chunk of idle noise PCM for standby mode
    - generate_silent_mel(): Generate silent mel features (legacy, kept for tests)
    """

    _SILENT_MEL_CACHE = None

    def __init__(self):
        """Initialize AudioProcessor and load idle noise audio."""
        self._idle_pcm: np.ndarray | None = None
        self._idle_pos: int = 0
        self._load_idle_audio()

    def _load_idle_audio(self):
        """Load the idle noise WAV file for standby mode.

        The idle audio is a low-volume noise waveform (~-48 dB) that drives
        Audio2Lip to produce closed-mouth lip coefficients. When no real audio
        is available, the system loops this audio to keep the mouth naturally
        closed rather than using zero/silent mel (which causes mouth-open artifacts).
        """
        idle_path = os.path.join(os.path.dirname(__file__), _IDLE_AUDIO_FILENAME)
        if not os.path.exists(idle_path):
            print(f'[AudioProcessor] WARNING: Idle audio not found: {idle_path}')
            print('  Standby mode will use zero PCM (may cause mouth-open artifact)')
            self._idle_pcm = np.zeros(_CHUNK_SAMPLES, dtype=np.float32)
            return

        try:
            import librosa
            wav, sr = librosa.load(idle_path, sr=_SAMPLE_RATE)
            self._idle_pcm = wav.astype(np.float32)
            print(f'[AudioProcessor] Idle audio loaded: {idle_path}')
            print(f'  Duration: {len(self._idle_pcm)/_SAMPLE_RATE:.1f}s, '
                  f'RMS: {np.sqrt(np.mean(self._idle_pcm**2)):.6f}, '
                  f'~{20*np.log10(max(1e-10, np.sqrt(np.mean(self._idle_pcm**2)))):.0f} dB')
        except Exception as e:
            print(f'[AudioProcessor] WARNING: Failed to load idle audio: {e}')
            self._idle_pcm = np.zeros(_CHUNK_SAMPLES, dtype=np.float32)

    def pcm_to_mel(self, pcm: np.ndarray) -> tuple:
        """Convert PCM audio to mel features.

        Args:
            pcm: PCM audio data (float32)

        Returns:
            Tuple of (mel_input, bs, T)
        """
        return get_mel_from_pcm(pcm)

    def consume_idle_pcm(self) -> np.ndarray:
        """Get a chunk of idle noise PCM for standby mode.

        Returns a 200ms chunk of the pre-loaded idle noise audio,
        looping seamlessly when reaching the end of the file.

        Returns:
            PCM chunk of shape (_CHUNK_SAMPLES,) float32
        """
        if self._idle_pcm is None:
            return np.zeros(_CHUNK_SAMPLES, dtype=np.float32)

        total = len(self._idle_pcm)

        # Not enough data for even one chunk — just repeat
        if total < _CHUNK_SAMPLES:
            repeats = (_CHUNK_SAMPLES // total) + 1
            return np.tile(self._idle_pcm, repeats)[:_CHUNK_SAMPLES]

        # Seamless loop: extract chunk starting at current position
        if self._idle_pos + _CHUNK_SAMPLES <= total:
            chunk = self._idle_pcm[self._idle_pos:self._idle_pos + _CHUNK_SAMPLES].copy()
            self._idle_pos += _CHUNK_SAMPLES
            if self._idle_pos >= total:
                self._idle_pos = 0
        else:
            # Wrap around: concatenate tail + head
            first_part = self._idle_pcm[self._idle_pos:].copy()
            remaining = _CHUNK_SAMPLES - len(first_part)
            second_part = self._idle_pcm[:remaining].copy()
            chunk = np.concatenate([first_part, second_part])
            self._idle_pos = remaining

        return chunk

    def generate_silent_mel(self) -> tuple:
        """Generate silent mel features (cached, legacy).

        Kept for backward compatibility with tests. In production,
        consume_idle_pcm() + pcm_to_mel() should be used instead.

        Returns:
            Tuple of (mel_input, bs, T)
        """
        if self._SILENT_MEL_CACHE is not None:
            return self._SILENT_MEL_CACHE

        # Generate 200ms of silence
        silent_pcm = np.zeros(int(16000 * 0.2), dtype=np.float32)

        # Convert to mel
        mel_input, bs, T = get_mel_from_pcm(silent_pcm)

        # Cache result
        self._SILENT_MEL_CACHE = (mel_input, bs, T)
        return self._SILENT_MEL_CACHE

    @classmethod
    def reset_cache(cls):
        """Reset the silent mel cache (for testing)."""
        cls._SILENT_MEL_CACHE = None
