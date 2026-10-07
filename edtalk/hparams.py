import numpy as np


class HParams:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


hparams = HParams(
    # Audio
    sample_rate=22050,
    n_fft=1024,
    hop_size=200,  # Standard Wav2Lip: 16000/200 = 80 mel fps (matches sliding window formula)
    win_size=1024,
    frame_shift_ms=None,
    num_mels=80,
    fmin=0,
    fmax=8000,
    preemphasis=0.97,
    preemphasize=True,
    min_level_db=-100,
    ref_level_db=20,
    signal_normalization=True,
    allow_clipping_in_normalization=True,
    symmetric_mels=True,
    max_abs_value=4.0,
    use_lws=False,
)