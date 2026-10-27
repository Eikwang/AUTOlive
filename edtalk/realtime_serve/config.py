"""Configuration management for realtime streaming inference service."""
from dataclasses import dataclass
from typing import Optional
import warnings
from pathlib import Path


@dataclass
class Config:
    """Configuration class for the inference service.

    Holds all configuration parameters including model paths, character directory,
    and rendering settings.
    """

    character_dir: str = "test_data/wy"
    target_fps: int = 25  # now controls only output pacing, not production
    cap_num: int = 0
    batch_size: int = 100  # frames per preload batch (window = 2 × batch_size)
    face_repair_enabled: bool = True  # enable face repair post-processing
    face_repair_mode: str = 'off'  # 'off' | 'on' | 'adaptive' (repair only with buffer headroom)
    gaze_mode: str = 'off'  # 'off' | 'on' | 'adaptive' (gaze correction on rendered output)
    gaze_convergence: float = 0.05  # convergence angle [-1,1] space (dynamic via set_convergence)
    gaze_ckpt: str = 'realtime_serve/weights/current_model.ckpt'  # gaze model weights
    gaze_eye_source: str = 'ori'  # 'ori' (原生眼源, repair 后贴回) | 'rendered' (legacy C', repair 前) — GAZE_TIMING_PLAN DX 发现1
    unitycapture_enabled: bool = True  # False = 无摄像头运行（原 --no-unitycapture）
    face_repair_weights: str = 'realtime_serve/weights/facefix256.onnx'
    api_token: Optional[str] = None  # 非空时 HTTP 面要求 Authorization: Bearer <token>
    buffer_frames: int = 16  # output frame buffer capacity (frames, ~11MB each)
    preroll_frames: int = 8  # frames required before output starts
    # Generator fp16 autocast: DISABLED by default. Measured negative on
    # RTX 4080 SUPER (gen p50 21-34ms fp32 -> constant 36ms fp16): with
    # TF32 enabled, Ada gets most of the fp16 throughput already, so
    # autocast only adds per-layer cast overhead.
    gen_fp16: bool = False
    # 统一画布合成层（2026-10-27 画面输出批次，AUTOlive 画面输出分叉新增——
    # R-ceo9：全会话单一画布，虚拟摄像头重建仅会话启动；几何单一来源=启动参数）
    canvas_w: int = 1280
    canvas_h: int = 720
    composite_enabled: bool = True
    composite_default_bg: Optional[str] = None  # 直播间默认背景图（可空=深色底）

    def __post_init__(self):
        """Validate paths after initialization."""
        # Check if character directory exists (warn but don't fail)
        if not Path(self.character_dir).exists():
            warnings.warn(
                f"Character directory does not exist: {self.character_dir}",
                UserWarning,
                stacklevel=2
            )

        # Validate required fields
        if not self.character_dir:
            raise TypeError("character_dir is required")

        if self.face_repair_mode not in ('off', 'on', 'adaptive'):
            raise ValueError(
                f"face_repair_mode must be 'off', 'on' or 'adaptive', "
                f"got '{self.face_repair_mode}'"
            )
        if self.gaze_mode not in ('off', 'on', 'adaptive'):
            raise ValueError(
                f"gaze_mode must be 'off', 'on' or 'adaptive', got '{self.gaze_mode}'"
            )
        if self.gaze_eye_source not in ('ori', 'rendered'):
            raise ValueError(
                f"gaze_eye_source must be 'ori' or 'rendered', got '{self.gaze_eye_source}'"
            )
        if not (-1.0 <= self.gaze_convergence <= 1.0):
            raise ValueError(
                f"gaze_convergence must be within [-1, 1], got {self.gaze_convergence}"
            )

        if self.buffer_frames < 1:
            raise ValueError(f"buffer_frames must be >= 1, got {self.buffer_frames}")
        if self.buffer_frames > 300:
            raise ValueError(f"buffer_frames must be <= 300, got {self.buffer_frames}")
        if self.preroll_frames < 0:
            raise ValueError(f"preroll_frames must be >= 0, got {self.preroll_frames}")
        if self.preroll_frames > self.buffer_frames:
            raise ValueError(
                f"preroll_frames ({self.preroll_frames}) must be <= "
                f"buffer_frames ({self.buffer_frames})"
            )