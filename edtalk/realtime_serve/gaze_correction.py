"""Gaze correction for the realtime pipeline (Route C', probe-verified).

Corrects the eye gaze on the Generator's rendered 256x256 output BEFORE the
compositor pastes it back. The compositor mask must include the eye regions
for the correction to reach the final frame (see compositor.py eyes_enabled).

Pipeline position (GAZE_TIMING_PLAN 最终门裁决 2, 2026-09-21):
    count crop -> Generator -> rendered(256,GPU) -> [HERE: eye_source 分流] -> repair -> composite
    'ori' 模式: eye_source=pristine orifra 原生眼, 贴回 rendered——纠正后的整脸
    随后进 face repair（眼与嘴鼻同一遍修复锐化）; 'rendered' 模式: 自纠正
    （legacy Route C'，逐位现状）。
Correction applied on the Generator INPUT (count crop) was measured to be
erased by the generator redraw (eye diff 1.1x noise on the current checkpoint,
2026-09-21 re-verification vs 29x at the output side).

Design notes:
- All tensor work stays on the GPU; the caller passes the rendered [-1,1]
  RGB CHW tensor and receives a new tensor (never in-place: the preloader
  buffers are shared and frames repeat).
- Eye positions come from the precomputed per-frame facemark (68 points in
  256 space) — dlib never runs in the realtime loop.
- Blink gate: EAR state machine ported from the offline reference
  (pytorch_gaze_redirection/src/inference.py). Detection-failure frames
  HOLD the previous eye centers and keep correcting; blink frames BYPASS.
  eye_size and the movement threshold live in 256-space so the tracker's
  zoom does not drift the locked size (ENG review A-6).
- Dynamic convergence angle: set_convergence() re-builds the angle tensor;
  effective from the next frame.
"""
import math

import cv2
import numpy as np
import torch
import torch.nn.functional as F
from typing import Optional

from pytorch_gaze_redirection.src.archs import Generator

# EAR blink state machine (ported verbatim from the offline reference).
EAR_CLOSE = 0.12
EAR_OPEN = 0.16
OPEN_CONFIRM_FRAMES = 3
MAX_BLINK_FRAMES = 26

# Convergence change above which the temporal-smoothing history is dropped:
# a large angle jump would otherwise lerp old-angle eyes for ~2-3 frames
# (temporal_alpha=0.7 decays the previous frame at 0.3^n).
CONVERGENCE_GHOST_DELTA = 0.2

# Eye-source modes (GAZE_TIMING_PLAN DX 发现1 / ENG F1a):
#   'ori'      — gaze generates from the pristine orifra crop's native eyes
#                and pastes onto the repaired rendered crop AFTER face repair
#                (two-phase: un-gated native paste + gated generation).
#   'rendered' — legacy Route C' semantics, bitwise-identical to the pre-change
#                pipeline (call site BEFORE face repair, self-corrected).
_VALID_EYE_SOURCES = ('ori', 'rendered')


class GazeCorrector:
    """GPU-inline gaze correction on the rendered 256 crop.

    Errors never kill the render loop: correct() catches its own failures
    and returns the input unchanged (counted in error_skips).
    """

    L_EYE = list(range(36, 42))
    R_EYE = list(range(42, 48))

    def __init__(self, ckpt_path: str, device: str = 'cuda',
                 convergence_h: float = 0.05, smooth_alpha: float = 0.4,
                 image_size: int = 64, eye_source: str = 'ori'):
        self.device = torch.device(device)
        if self.device.type == 'cuda' and self.device.index is None:
            # torch.device('cuda') != torch.device('cuda:0')（Python 相等比较
            # 为 False）——不钉索引会让域守卫在生产域拒绝每一帧（data.log
            # 实案；CPU 测试环境无索引差异故未拦截）。绑定当前设备。
            self.device = torch.device('cuda', torch.cuda.current_device())
        self.smooth_alpha = smooth_alpha
        self.image_size = image_size
        if eye_source not in _VALID_EYE_SOURCES:
            raise ValueError(
                f"eye_source 必须为 {_VALID_EYE_SOURCES} 之一，got '{eye_source}'")
        self._eye_source = eye_source

        self.generator = Generator(style_dim=3)
        ckpt = torch.load(ckpt_path, map_location=self.device, weights_only=True)
        if 'current_generator' not in ckpt:
            raise KeyError("not a gaze model weight: missing key 'current_generator'")
        self.generator.load_state_dict(ckpt['current_generator'])
        self.generator = self.generator.to(self.device)
        self.generator.eval()

        self._convergence_h = float(convergence_h)
        self._angles = self._build_angles(convergence_h)

        # 64x64 circular blend mask (GPU constant, matches the offline reference).
        size = image_size
        m = np.zeros((size, size), dtype=np.float32)
        cv2.circle(m, (size // 2, size // 2), size // 2 - 2, 1.0, thickness=-1)
        m = cv2.GaussianBlur(m, (7, 7), 0)
        self._mask64 = torch.from_numpy(m[..., np.newaxis]).to(self.device)

        # Smoothed eye centers + locked eye size, both in 256-space.
        self._prev_left = None
        self._prev_right = None
        self._fixed_eye_size = None

        # Blink state machine counters.
        self._low_ear_count = 0
        self._open_confirm = 0

        # Temporal smoothing of the generated eyes (GAN outputs jitter
        # frame-to-frame even for identical inputs). New-frame weight.
        self._temporal_alpha = 0.7
        self._prev_gen = None
        self._skip_run = 0

        # Observability counters (reset per segment).
        self.corrected_frames = 0
        self.blink_skips = 0
        self.nan_skips = 0
        self.error_skips = 0

        self._graph = None
        self._static_in = None
        self._static_angles = None
        self._static_out = None
        self._graph_ready = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def convergence_h(self) -> float:
        """Public read-only echo for /status (H4: never read the _private)."""
        return self._convergence_h

    @property
    def eye_source_mode(self) -> str:
        """'ori' | 'rendered' — read by the render loop to pick the call site
        (GAZE_TIMING_PLAN ENG F1a)."""
        return self._eye_source

    def set_eye_source(self, mode: str) -> None:
        """Hot-switch the eye-source mode (owner-layer rejective validation —
        ENG F9, M17 pattern: the HTTP/CLI layer is only the first gate).

        'ori' ↔ 'rendered' switches the semantic contract; the temporal
        smoothing history is invalidated (the two modes paste onto different
        domains — lerping across the switch would ghost).
        """
        if mode not in _VALID_EYE_SOURCES:
            raise ValueError(
                f"eye_source 必须为 {_VALID_EYE_SOURCES} 之一，got '{mode}'"
                f"（当前值 {self._eye_source}）")
        if mode != self._eye_source:
            self._eye_source = mode
            self._prev_gen = None

    def set_convergence(self, convergence_h: float) -> None:
        """Change the convergence angle at runtime (takes effect next frame).

        Validates REJECTIVELY (never clamps): NaN/inf would poison the CUDA
        graph (every replay reads _static_angles, so one bad copy_ turns every
        output frame into garbage with no exception raised — M17). The owner
        layer guards here so direct in-process callers are protected too, not
        just HTTP clients.
        """
        v = float(convergence_h)
        if not math.isfinite(v):
            raise ValueError(
                f"convergence 必须为有限数值（拒绝 NaN/inf），got {convergence_h}")
        if not (-1.0 <= v <= 1.0):
            raise ValueError(
                f"convergence 必须在 [-1, 1] 内，got {v}（当前值 {self._convergence_h}）")

        prev = self._convergence_h
        self._convergence_h = v
        self._angles = self._build_angles(v)
        if self._graph_ready:
            self._static_angles.copy_(self._angles)
        if prev is not None and abs(v - prev) > CONVERGENCE_GHOST_DELTA:
            self._prev_gen = None  # drop stale-angle smoothing history

    def reset(self) -> None:
        """Reset per-segment state (segment switch / character switch)."""
        self._prev_left = None
        self._prev_right = None
        self._fixed_eye_size = None
        self._low_ear_count = 0
        self._open_confirm = 0
        self._prev_gen = None
        self._skip_run = 0
        self.corrected_frames = 0
        self.blink_skips = 0
        self.nan_skips = 0
        self.error_skips = 0

    @torch.inference_mode()
    def correct(self, eye_source: torch.Tensor, canvas: torch.Tensor,
                facemark_256: Optional[np.ndarray],
                gaze_frame: Optional[dict] = None,
                generate: bool = True) -> torch.Tensor:
        """Correct both eyes using eye_source pixels pasted onto canvas.

        Dual-tensor contract (GAZE_TIMING_PLAN T2 / DX D11 — the eye is read
        from eye_source, pasted into canvas, a NEW tensor is returned):

        Args:
            eye_source: (3,256,256) RGB float [-1,1] on GPU — the eye pixel
                donor. Pristine orifra crop ('ori' mode) or the rendered crop
                itself ('rendered' legacy mode).
            canvas: (3,256,256) RGB float [-1,1] on GPU — the paste target
                (the rendered crop in 'ori' mode — both eye-source modes paste
                BEFORE face repair so the whole face shares one GPEN pass,
                最终门裁决 2). NEVER mutated.
            facemark_256: (68,2) 256-space landmarks for this frame (numpy),
                possibly NaN on detection-failure frames. Legacy geometry
                path when gaze_frame is None.
            gaze_frame: optional precomputed per-frame dict (state/l_center/
                r_center/eye_size) — the recommended geometry path.
            generate: gate the GPU forward ONLY (ENG F2 — the native-eye
                paste is un-gated). The render loop passes _gaze_allowed()
                here in 'ori' mode. Ignored in 'rendered' mode (the legacy
                call site gates the whole call, bitwise-identical semantics).

        Returns:
            NEW (3,256,256) tensor: canvas with its eye regions replaced by
            (corrected) eye_source eyes. Neither input is ever mutated (G4).
        """
        try:
            self._validate_input(eye_source, 'eye_source')
            self._validate_input(canvas, 'canvas')
            if self._eye_source == 'rendered':
                return self._correct_rendered(eye_source, canvas,
                                              facemark_256, gaze_frame)
            return self._correct_ori(eye_source, canvas, facemark_256,
                                     gaze_frame, generate)
        except Exception as e:
            self.error_skips += 1
            # Rate-limited: a silent bypass loop would hide programming
            # errors (measured to have happened during development).
            if self.error_skips == 1:
                import traceback
                print(f"[GazeCorrector] correction failed ({e}); "
                      f"bypassing. Traceback (once):")
                traceback.print_exc()
            return canvas

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _validate_input(self, t: torch.Tensor, name: str) -> None:
        """Domain guard (DX 发现4/DX-T2): shape / device / [-1,1] domain.
        Failure raises — the correct() wrapper degrades to the un-pasted
        canvas and counts error_skips."""
        if (not torch.is_tensor(t) or t.dim() != 3
                or tuple(t.shape) != (3, 256, 256)):
            raise ValueError(
                f"{name} 形状非法: {tuple(t.shape) if torch.is_tensor(t) else type(t)}"
                "（期望 (3, 256, 256)）")
        if t.device != self.device:
            raise ValueError(
                f"{name} 设备不符: {t.device}（期望 {self.device}）")
        if not bool(torch.isfinite(t).all()):
            raise ValueError(f"{name} 含 NaN/inf")
        m = float(t.abs().max())
        if m > 1.001:
            raise ValueError(f"{name} 数值域非法: |max|={m:.3f}（期望 [-1, 1]）")

    def _invalidate_history(self) -> None:
        """Drop the temporal-smoothing history (ENG F3): gate-off stretches
        and mode switches must not lerp the next generated eye against a
        stale pre-gap frame (visible ghost)."""
        self._prev_gen = None
        self._skip_run = 0

    def _update_blink_state(self, min_ear: float) -> bool:
        """Advance the EAR blink state machine (port of the legacy logic,
        shared by both geometry paths — DRY). Returns True while a blink is
        in progress (generation must bypass)."""
        if min_ear < EAR_CLOSE:
            self._low_ear_count += 1
            self._open_confirm = 0
        elif self._low_ear_count > 0:
            if min_ear < EAR_OPEN:
                self._open_confirm = 0
            else:
                self._open_confirm += 1
                if self._open_confirm >= OPEN_CONFIRM_FRAMES:
                    self._low_ear_count = 0
                    self._open_confirm = 0
        else:
            self._open_confirm = 0
        return 0 < self._low_ear_count <= MAX_BLINK_FRAMES

    def _correct_rendered(self, eye_source: torch.Tensor,
                          canvas: torch.Tensor,
                          facemark_256: Optional[np.ndarray],
                          gaze_frame: Optional[dict]) -> torch.Tensor:
        """Legacy Route C' semantics — bitwise-identical port of the
        pre-change correct() (U11 regression contract): bypass frames return
        the canvas UNTOUCHED (no native paste), generation source == paste
        base == the rendered crop itself."""
        if gaze_frame is not None:
            state = int(gaze_frame['state'])
            if state == 1:
                self.blink_skips += 1
                self._note_skip()
                return canvas
            if state == 2:
                self.nan_skips += 1
                self._note_skip()
                return canvas
            eye_size = int(gaze_frame['eye_size'])
            lx, ly = gaze_frame['l_center']
            rx, ry = gaze_frame['r_center']
            boxes = [(float(lx), float(ly), eye_size),
                     (float(rx), float(ry), eye_size)]
        else:
            if facemark_256 is None or np.any(np.isnan(facemark_256)):
                self.nan_skips += 1
                self._note_skip()
                return canvas
            l_info, r_info = self._eye_state(facemark_256)
            if l_info is None:
                # Blink in progress: bypass entirely.
                self.blink_skips += 1
                self._note_skip()
                return canvas
            boxes = [(l_info[0], l_info[1], self._fixed_eye_size),
                     (r_info[0], r_info[1], self._fixed_eye_size)]

        out = canvas.clone()
        out = self._paste_generated(out, eye_source, boxes)
        self.corrected_frames += 1
        self._skip_run = 0
        return out

    def _correct_ori(self, eye_source: torch.Tensor,
                     canvas: torch.Tensor,
                     facemark_256: Optional[np.ndarray],
                     gaze_frame: Optional[dict],
                     generate: bool) -> torch.Tensor:
        """Route B (GAZE_TIMING_PLAN §3): two-phase, un-gated native paste
        + gated generation. Bypass invariant: with gaze on, the final eye
        pixels ALWAYS come from the pristine ori crop — blink and gate-off
        frames paste the uncorrected native eyes (no Generator-eye flicker)."""
        if gaze_frame is not None:
            state = int(gaze_frame['state'])
            if state == 2:
                # 检测失败：该帧几何不可信。当前 gaze_data 格式无可回退历史
                # 中心 → 不贴回（canvas 眼保留）并清历史（ENG F3/E5 语义）。
                self.nan_skips += 1
                self._note_skip()
                self._invalidate_history()
                return canvas
            eye_size = int(gaze_frame['eye_size'])
            lx, ly = gaze_frame['l_center']
            rx, ry = gaze_frame['r_center']
            boxes = [(float(lx), float(ly), eye_size),
                     (float(rx), float(ry), eye_size)]
            blinking = state == 1
        else:
            if facemark_256 is None or np.any(np.isnan(facemark_256)):
                self.nan_skips += 1
                self._note_skip()
                self._invalidate_history()
                return canvas
            # Centers + eye_size advance even when generation is gated (F3:
            # the state machine must stay fresh for gate reopening).
            boxes = []
            for ids, prev in ((self.L_EYE, self._prev_left),
                              (self.R_EYE, self._prev_right)):
                pts = facemark_256[ids]
                x1, y1 = pts.min(axis=0)
                x2, y2 = pts.max(axis=0)
                cx = self._smooth((x1 + x2) / 2.0,
                                  prev[0] if prev else None, self.smooth_alpha)
                cy = self._smooth((y1 + y2) / 2.0,
                                  prev[1] if prev else None, self.smooth_alpha)
                if self._fixed_eye_size is None:
                    size = int(1.5 * max(x2 - x1, y2 - y1))
                    self._fixed_eye_size = max(16, min(size, 128))
                boxes.append((cx, cy, self._fixed_eye_size))
            self._prev_left, self._prev_right = boxes[0][:2], boxes[1][:2]
            min_ear = min(self._ear(facemark_256[self.L_EYE]),
                          self._ear(facemark_256[self.R_EYE]))
            blinking = self._update_blink_state(min_ear)

        out = canvas.clone()
        if blinking:
            # Blink: paste the native (closed) eyes from the SAME ori frame —
            # the pristine crop already contains the true blink appearance.
            self.blink_skips += 1
            self._note_skip()
            self._invalidate_history()
            return self._paste_native(out, eye_source, boxes)
        if not generate:
            # Adaptive gate off (production headroom low): native paste only.
            self._invalidate_history()
            return self._paste_native(out, eye_source, boxes)

        out = self._paste_native(out, eye_source, boxes)
        out = self._paste_generated(out, eye_source, boxes)
        self.corrected_frames += 1
        self._skip_run = 0
        return out

    def _paste_native(self, out: torch.Tensor, eye_source: torch.Tensor,
                      boxes) -> torch.Tensor:
        """Phase 1 (un-gated): restore the native eye_source eyes onto `out`
        with the circular mask. No generation, no color transfer — the ori
        pixels ARE the target appearance (T0-validated domain). Mutates
        `out` (the caller owns the clone)."""
        for cx, cy, eye_size in boxes:
            half = eye_size / 2.0
            x1 = int(round(cx - half))
            y1 = int(round(cy - half))
            x1c, y1c = max(0, x1), max(0, y1)
            x2c = min(256, x1 + eye_size)
            y2c = min(256, y1 + eye_size)
            if x2c - x1c < 8 or y2c - y1c < 8:
                continue
            native = eye_source[:, y1c:y2c, x1c:x2c]
            mk = F.interpolate(self._mask64.permute(2, 0, 1).unsqueeze(0),
                               size=(y2c - y1c, x2c - x1c),
                               mode='bilinear', antialias=True)[0]
            region = out[:, y1c:y2c, x1c:x2c]
            out[:, y1c:y2c, x1c:x2c] = native * mk + region * (1.0 - mk)
        return out

    def _note_skip(self) -> None:
        """Track consecutive skip frames; invalidate the temporal-smoothing
        history after a long gap so resuming correction doesn't ghost."""
        self._skip_run += 1
        if self._skip_run > 3:
            self._prev_gen = None

    def _build_angles(self, convergence_h: float) -> torch.Tensor:
        return torch.tensor([[-convergence_h, 0.0, -1.0],
                             [convergence_h, 0.0, 1.0]],
                            device=self.device)

    @staticmethod
    def _ear(pts: np.ndarray) -> float:
        v1 = np.linalg.norm(pts[1] - pts[5])
        v2 = np.linalg.norm(pts[2] - pts[4])
        h = np.linalg.norm(pts[0] - pts[3]) + 1e-6
        return (v1 + v2) / (2.0 * h)

    def _smooth(self, raw: float, prev, alpha: float) -> float:
        """EMA with reduced gain on large jumps (threshold is 256-space)."""
        if prev is None:
            return raw
        a = alpha if abs(raw - prev) < 20 else alpha * 0.3
        return a * raw + (1.0 - a) * prev

    def _eye_state(self, facemark_256: np.ndarray):
        """EAR gate + smoothed centers. Returns (l_info, r_info); both None
        while a blink is in progress."""
        l_pts = facemark_256[self.L_EYE]
        r_pts = facemark_256[self.R_EYE]
        min_ear = min(self._ear(l_pts), self._ear(r_pts))

        infos = []
        for pts, prev in ((l_pts, self._prev_left), (r_pts, self._prev_right)):
            x1, y1 = pts.min(axis=0)
            x2, y2 = pts.max(axis=0)
            cx = self._smooth((x1 + x2) / 2.0, prev[0] if prev else None,
                              self.smooth_alpha)
            cy = self._smooth((y1 + y2) / 2.0, prev[1] if prev else None,
                              self.smooth_alpha)
            infos.append((cx, cy))
            if self._fixed_eye_size is None:
                size = int(1.5 * max(x2 - x1, y2 - y1))
                self._fixed_eye_size = max(16, min(size, 128))
        self._prev_left, self._prev_right = infos[0], infos[1]

        # Blink state machine (detection failure keeps correcting via the
        # hold-above; only actual blinks gate).
        blinking = self._update_blink_state(min_ear)
        if blinking:
            return None, None
        return infos[0], infos[1]

    def _paste_generated(self, out: torch.Tensor,
                         eye_source: torch.Tensor,
                         boxes) -> torch.Tensor:
        """Phase 2: generate corrected eyes from eye_source regions and blend
        them into `out` (the caller owns the clone; may already carry the
        phase-1 native paste). Color-transfer reference = the eye_source
        region — the eye must look like the ORIGINAL person's eye (T0
        semantics); in 'rendered' mode source==canvas so this is bitwise
        identical to the legacy behavior."""
        tensors = []
        coords = []
        for cx, cy, eye_size in boxes:
            half = eye_size / 2.0
            x1 = int(round(cx - half))
            y1 = int(round(cy - half))
            x1c, y1c = max(0, x1), max(0, y1)
            x2c = min(256, x1 + eye_size)
            y2c = min(256, y1 + eye_size)
            if x2c - x1c < 8 or y2c - y1c < 8:
                continue
            region = eye_source[:, y1c:y2c, x1c:x2c].unsqueeze(0)
            t = F.interpolate(region, size=(self.image_size, self.image_size),
                              mode='bilinear', antialias=True)
            tensors.append(t)
            coords.append((x1c, y1c, x2c - x1c, y2c - y1c))

        if not tensors:
            return out

        batch = torch.cat(tensors, dim=0)  # [2,3,64,64]
        gen = self._forward(batch)

        # Temporal smoothing: lerp with the previous generated eyes. GAN
        # outputs jitter frame-to-frame even for near-identical inputs;
        # blink/skip frames don't touch the history (see _note_skip).
        if self._prev_gen is not None and self._prev_gen.shape == gen.shape:
            gen = self._temporal_alpha * gen + (1.0 - self._temporal_alpha) * self._prev_gen
        self._prev_gen = gen.detach().clone()

        for i, (x1c, y1c, w, h) in enumerate(coords):
            eye = gen[i]
            target = eye_source[:, y1c:y1c + h, x1c:x1c + w]
            for c in range(3):
                s_mean = eye[c].mean(); s_std = eye[c].std() + 1e-6
                d_mean = target[c].mean(); d_std = target[c].std() + 1e-6
                eye[c] = (eye[c] - s_mean) * (d_std / s_std) + d_mean
            eye = eye.clamp(-1, 1)

            eye_resized = F.interpolate(eye.unsqueeze(0), size=(h, w),
                                        mode='bilinear', antialias=True)[0]
            mk = F.interpolate(self._mask64.permute(2, 0, 1).unsqueeze(0),
                               size=(h, w), mode='bilinear', antialias=True)[0]
            region_out = out[:, y1c:y1c + h, x1c:x1c + w]
            out[:, y1c:y1c + h, x1c:x1c + w] = eye_resized * mk + region_out * (1.0 - mk)

        return out

    def _forward(self, batch: torch.Tensor) -> torch.Tensor:
        """Generator forward: CUDA graph when captured, eager otherwise."""
        if self._graph_ready:
            self._static_in.copy_(batch)
            self._static_angles.copy_(self._angles)
            self._graph.replay()
            return self._static_out
        with torch.inference_mode():
            return self.generator(batch, self._angles)

    def try_capture_graph(self) -> bool:
        """Best-effort CUDA graph capture. Returns True when active."""
        if self.device.type != 'cuda':
            return False
        try:
            self._static_in = torch.zeros(2, 3, self.image_size, self.image_size,
                                          device=self.device)
            self._static_angles = self._angles.clone()
            s = torch.cuda.Stream()
            s.wait_stream(torch.cuda.current_stream())
            with torch.cuda.stream(s), torch.inference_mode():
                for _ in range(3):
                    _ = self.generator(self._static_in, self._static_angles)
            torch.cuda.current_stream().wait_stream(s)
            torch.cuda.synchronize()

            g = torch.cuda.CUDAGraph()
            with torch.cuda.graph(g), torch.inference_mode():
                out = self.generator(self._static_in, self._static_angles)
            if not isinstance(out, torch.Tensor) or out.shape != (2, 3, 64, 64):
                raise RuntimeError(f"unexpected graph output {type(out)}")
            self._graph = g
            self._static_out = out
            self._graph_ready = True
            return True
        except Exception:
            self._graph = None
            self._graph_ready = False
            return False
