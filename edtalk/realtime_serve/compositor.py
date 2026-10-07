"""Mouth compositor for extracting mouth region and compositing onto original frame.

Uses facemark 68-point landmarks (in 256x256 space) to define a precise mouth
region, creates a landmark-guided mask, and composites only the mouth area from
the rendered frame onto the original frame.

Mask strategy:
  - Inner region (mouth): outer lip landmarks 48-59 → convex hull → alpha=1.0
  - Outer boundary: jaw contour 0-16 (left/right/bottom) + nose alar 31-35 (top)
  - Transition: from mouth hull outward to the outer boundary, smooth falloff
  - Constraint: mask never exceeds the face contour or goes above the nose alar

This avoids the background deformation artifact that occurs when the entire 256x256
rendered face (which has distorted background outside the face) is pasted back.

IMPORTANT: facemark coordinates are expected to be in 256x256 space (i.e.,
detected on the cropped/count face image, not the original orifra frame).
This is set during preprocessing in crop_head_video.py, which detects landmarks
on the 256x256 resized crop rather than the original frame. This avoids
coordinate mapping errors and ensures pixel-accurate mask alignment.
"""
import numpy as np
import cv2
import threading


class MouthCompositor:
    """Compositor that extracts mouth region from rendered frame and composites onto original frame.

    Mask strategy — landmark-guided dual-boundary mask:
    1. Inner boundary: convex hull of outer lip landmarks (48-59) → alpha = 1.0
    2. Outer boundary: jaw contour (0-16) + nose alar (31-35) → alpha = 0.0
    3. Between inner and outer: distance-based smooth falloff
    4. EMA smoothing on landmarks to prevent edge flickering

    The mask is constrained to never exceed the face contour (jaw) or go above
    the nose alar line, ensuring only the mouth region is composited.
    """

    # dlib 68-point landmark indices
    # Jaw contour: 0-16 (face outline, used as outer boundary)
    # Nose alar: 31-35 (nose bottom, used as upper boundary)
    # Outer lip: 48-59 (mouth outline, used as inner boundary = alpha=1.0)
    # Inner lip: 60-67 (not used for mask, but part of mouth)
    JAW_IDS = list(range(0, 17))           # face contour
    NOSE_ALAR_IDS = list(range(31, 36))    # nose bottom / alar
    OUTER_LIP_IDS = list(range(48, 60))    # outer lip contour
    L_EYE_IDS = list(range(36, 42))        # left eye contour
    R_EYE_IDS = list(range(42, 48))        # right eye contour

    def __init__(self, blur_px: int = 7, landmark_smooth: float = 0.3,
                 eyes_enabled: bool = False):
        """Initialize MouthCompositor.

        Args:
            blur_px: Gaussian blur kernel size for the final mask (must be odd).
                     Provides sub-pixel smoothness to eliminate any remaining hard edges.
            landmark_smooth: EMA smoothing factor for facemark landmarks (0-1).
                             0 = no smoothing (use raw landmarks each frame)
                             0.3 = moderate smoothing (default, good anti-flicker)
                             1.0 = frozen (never updates from first frame)
            eyes_enabled: Include the eye regions in the paste-back mask.
                          Required for gaze correction to reach the output
                          frame (the rendered eye replaces the orifra eye
                          when enabled). When False the mask is mouth-only
                          and the output eyes are the original video's.
        """
        self.blur_px = blur_px if blur_px % 2 == 1 else blur_px + 1
        self.landmark_smooth = landmark_smooth
        self.eyes_enabled = eyes_enabled

        # EMA-smoothed facemark (in 256x256 space), persists across frames
        self._smoothed_facemark: np.ndarray | None = None

        # State lock: generate_mask() runs on the batch-preload thread while
        # composite()'s fallback mask path runs on the render thread — both
        # mutate the shared EMA / mask cache, so they must be serialized.
        self._state_lock = threading.Lock()

        # Mask cache: avoid recomputing expensive distance transforms every frame
        # The mask changes slowly (landmarks shift ~2-5px between frames at 25fps),
        # so we only recompute when landmarks move beyond a tolerance threshold.
        self._cached_mask: np.ndarray | None = None
        self._cached_mask_facemark: np.ndarray | None = None
        self._mask_cache_tolerance: float = 3.0  # pixels — recompute if any landmark moves more than this

    def generate_mask(self, facemark_256: np.ndarray) -> np.ndarray:
        """Generate a mouth mask from facemark landmarks (for precomputation).

        This is the standalone mask generation step, separated from composite()
        so it can be called during batch preloading.

        Args:
            facemark_256: (68, 2) float32 landmarks in 256x256 space

        Returns:
            Mask (256, 256) float32 in range [0, 1], or None if facemark is invalid
        """
        if facemark_256 is None or np.any(np.isnan(facemark_256)):
            return None

        with self._state_lock:
            facemark_256 = self._smooth_landmarks(facemark_256)
            return self._create_mouth_mask(256, 256, facemark_256)

    def _resolve_mask(self, facemark_256: np.ndarray,
                      precomputed_mask: np.ndarray | None = None) -> np.ndarray | None:
        """Resolve the mouth mask for this frame."""
        if precomputed_mask is not None:
            return precomputed_mask

        with self._state_lock:
            facemark_256 = self._smooth_landmarks(facemark_256)
            if self._cached_mask is not None and self._cached_mask_facemark is not None:
                max_shift = np.max(np.abs(facemark_256 - self._cached_mask_facemark))
                if max_shift < self._mask_cache_tolerance:
                    return self._cached_mask
                mask = self._create_mouth_mask(256, 256, facemark_256)
                self._cached_mask = mask
                self._cached_mask_facemark = facemark_256.copy()
                return mask
            mask = self._create_mouth_mask(256, 256, facemark_256)
            self._cached_mask = mask
            self._cached_mask_facemark = facemark_256.copy()
            return mask

    def prepare_crop(self, orifra_frame: np.ndarray, meta: np.ndarray):
        """Prepare the ori crop for blending.

        This step is CPU-only and can overlap with GPU->CPU transfer of the
        rendered frame. Returns the data needed by blend().

        Returns:
            (ori_crop_256, paste_coords) or None if crop is invalid.
            paste_coords = (x1_c, y1_c, x2_c, y2_c, crop_w, crop_h)
        """
        crop_x = float(meta['crop_x'])
        crop_y = float(meta['crop_y'])
        crop_size = float(meta['crop_size'])

        if crop_size <= 0:
            return None

        crop_size_int = int(round(crop_size))
        if crop_size_int <= 0:
            return None

        x1 = int(round(crop_x))
        y1 = int(round(crop_y))
        x2 = x1 + crop_size_int
        y2 = y1 + crop_size_int

        x1_c = max(0, x1)
        y1_c = max(0, y1)
        x2_c = min(orifra_frame.shape[1], x2)
        y2_c = min(orifra_frame.shape[0], y2)

        crop_w = x2_c - x1_c
        crop_h = y2_c - y1_c
        if crop_w <= 0 or crop_h <= 0:
            return None

        ori_crop_256 = cv2.resize(orifra_frame[y1_c:y2_c, x1_c:x2_c],
                                  (256, 256), interpolation=cv2.INTER_LINEAR)
        return ori_crop_256, (x1_c, y1_c, x2_c, y2_c, crop_w, crop_h)

    def blend(self, orifra_frame: np.ndarray, rendered_frame_256: np.ndarray,
              mask_256: np.ndarray, prepared_crop) -> np.ndarray:
        """Blend rendered mouth onto the prepared ori crop and paste back."""
        if prepared_crop is None:
            return orifra_frame

        ori_crop_256, (x1_c, y1_c, x2_c, y2_c, crop_w, crop_h) = prepared_crop

        mask_3ch = np.stack([mask_256] * 3, axis=-1)
        blended_256 = (rendered_frame_256.astype(np.float32) * mask_3ch +
                       ori_crop_256.astype(np.float32) * (1.0 - mask_3ch))
        blended_256 = blended_256.astype(np.uint8)

        blended_full = cv2.resize(blended_256, (crop_w, crop_h),
                                  interpolation=cv2.INTER_LINEAR)
        orifra_frame[y1_c:y2_c, x1_c:x2_c] = blended_full
        return orifra_frame

    def composite(self, orifra_frame: np.ndarray, rendered_frame_256: np.ndarray,
                  facemark: np.ndarray, meta: np.ndarray,
                  precomputed_mask: np.ndarray | None = None) -> np.ndarray:
        """Composite mouth region from rendered frame onto original frame.

        Optimized: operates only on the crop region, not the full frame.
        Avoids the expensive full-frame copy (10.8MB at 1440x2560).

        Args:
            orifra_frame: Original 1080p frame (H, W, 3) BGR uint8
            rendered_frame_256: Rendered frame (256, 256, 3) BGR uint8
            facemark: (68, 2) float32 landmark points in 256x256 space
                      (detected on the cropped/count face image)
            meta: Single metadata entry with crop_x, crop_y, crop_size fields

        Returns:
            Composited 1080p frame (H, W, 3) BGR uint8.
            NOTE: The crop region of orifra_frame is modified in-place —
                  that write IS the output frame. Loop wraps re-render the
                  same preloaded buffer, which is why the paste-back BASE
                  must come from the decode-time pristine crop
                  (BatchPreloader.get_pristine / extract_pristine_crop, see
                  GAZE_TIMING_PLAN T1/X1): blending against the mutated
                  orifra itself used to drift across wraps.

        Raises:
            ValueError: If facemark is None
        """
        if facemark is None:
            raise ValueError("facemark required")

        crop_x = float(meta['crop_x'])
        crop_y = float(meta['crop_y'])
        crop_size = float(meta['crop_size'])

        if crop_size <= 0:
            return orifra_frame

        facemark_256 = facemark
        if np.any(np.isnan(facemark_256)):
            return orifra_frame

        mask_256 = self._resolve_mask(facemark_256, precomputed_mask)
        prepared = self.prepare_crop(orifra_frame, meta)
        return self.blend(orifra_frame, rendered_frame_256, mask_256, prepared)

    def set_eyes_enabled(self, enabled: bool) -> None:
        """Toggle eye regions in the paste-back mask.

        Invalidates the cached mask; the caller must recompute the preloaded
        batch masks (CharacterDataPack.set_eyes_enabled wires this).
        """
        if self.eyes_enabled == enabled:
            return
        self.eyes_enabled = enabled
        with self._state_lock:
            self._cached_mask = None
            self._cached_mask_facemark = None

    @staticmethod
    def _expand_pts(pts: np.ndarray, factor: float) -> np.ndarray:
        """Expand a point set outward from its centroid (hull pre-scaling)."""
        center = pts.mean(axis=0)
        return center + (pts - center) * factor

    def _smooth_landmarks(self, facemark_256: np.ndarray) -> np.ndarray:
        """Apply EMA smoothing to facemark landmarks to prevent edge flickering.

        Per-frame landmark jitter causes the mask boundary to shift slightly,
        which makes the mask edge jump between frames → visible flickering.
        EMA smoothing stabilizes the boundary position.

        Args:
            facemark_256: Current frame's landmarks (68, 2) in 256x256 space

        Returns:
            Smoothed landmarks (68, 2)
        """
        if self._smoothed_facemark is None:
            # First frame: initialize
            self._smoothed_facemark = facemark_256.copy()
            return facemark_256.copy()

        alpha = self.landmark_smooth
        self._smoothed_facemark = (
            self._smoothed_facemark * (1.0 - alpha) + facemark_256 * alpha
        )
        return self._smoothed_facemark.copy()

    def _create_mouth_mask(self, width: int, height: int,
                           facemark: np.ndarray) -> np.ndarray:
        """Create a landmark-guided mouth mask with smooth falloff.

        The mask is defined by two boundaries:
        - Inner boundary: convex hull of outer lip landmarks (48-59) → alpha = 1.0
        - Outer boundary: jaw contour (0-16) + nose alar (31-35) → alpha = 0.0

        Between the two boundaries, alpha falls off smoothly based on
        normalized distance from the inner boundary to the outer boundary.

        Steps:
        1. Build inner mask: convex hull of outer lip → fillPoly
        2. Build outer mask: convex hull of jaw + nose alar → fillPoly
        3. Compute distance from inner boundary (distanceTransform on ~inner_mask)
        4. Compute distance from outer boundary (distanceTransform on outer_mask)
        5. For pixels between inner and outer: alpha = dist_to_outer / (dist_to_inner + dist_to_outer)
           → 1.0 at inner edge, 0.0 at outer edge, smooth in between
        6. Gaussian blur for sub-pixel smoothness

        Args:
            width: Mask width
            height: Mask height
            facemark: (68, 2) float32 landmarks in 256x256 space (already smoothed)

        Returns:
            Mask (H, W) float32 in range [0, 1]
        """
        # --- Extract landmark groups ---
        lip_pts = facemark[self.OUTER_LIP_IDS].astype(np.float32)   # 48-59
        jaw_pts = facemark[self.JAW_IDS].astype(np.float32)         # 0-16
        nose_pts = facemark[self.NOSE_ALAR_IDS].astype(np.float32)  # 31-35

        # --- Inner boundary: convex hull of outer lip ---
        lip_hull = cv2.convexHull(lip_pts)  # (N, 1, 2)
        inner_mask = np.zeros((height, width), dtype=np.uint8)
        cv2.fillPoly(inner_mask, [lip_hull.astype(np.int32)], 255)

        # --- Outer boundary: convex hull of jaw + nose alar ---
        # This defines the face region that constrains the mask
        outer_pts = np.vstack([jaw_pts, nose_pts]).astype(np.float32)
        outer_hull = cv2.convexHull(outer_pts)  # (N, 1, 2)
        outer_mask = np.zeros((height, width), dtype=np.uint8)
        cv2.fillPoly(outer_mask, [outer_hull.astype(np.int32)], 255)

        # --- Distance transforms ---
        # Distance from inner boundary (for pixels outside inner_mask)
        dist_from_inner = cv2.distanceTransform(
            255 - inner_mask, cv2.DIST_L2, 5
        ).astype(np.float32)

        # Distance from outer boundary (for pixels inside outer_mask)
        dist_from_outer = cv2.distanceTransform(
            outer_mask, cv2.DIST_L2, 5
        ).astype(np.float32)

        # --- Build mask ---
        mask = np.zeros((height, width), dtype=np.float32)

        # Inside lip hull: fully rendered
        mask[inner_mask > 0] = 1.0

        # Transition zone: between inner and outer boundaries
        # Pixels that are outside inner_mask but inside outer_mask
        transition_zone = (inner_mask == 0) & (outer_mask > 0)

        if np.any(transition_zone):
            d_inner = dist_from_inner[transition_zone]
            d_outer = dist_from_outer[transition_zone]
            total = d_inner + d_outer
            # Avoid division by zero
            total = np.maximum(total, 1e-6)
            # Normalized: 1.0 at inner edge, 0.0 at outer edge
            alpha = d_outer / total
            # Apply smoothstep for smoother falloff
            alpha = alpha * alpha * (3.0 - 2.0 * alpha)
            mask[transition_zone] = alpha

        # --- Eye regions (gaze correction paste-back) ---
        if self.eyes_enabled:
            for eye_ids in (self.L_EYE_IDS, self.R_EYE_IDS):
                eye_pts = facemark[eye_ids].astype(np.float32)
                eye_inner = cv2.convexHull(
                    self._expand_pts(eye_pts, 1.2)).astype(np.int32)
                eye_outer = cv2.convexHull(
                    self._expand_pts(eye_pts, 1.7)).astype(np.int32)
                eye_inner_mask = np.zeros((height, width), dtype=np.uint8)
                cv2.fillPoly(eye_inner_mask, [eye_inner], 255)
                eye_outer_mask = np.zeros((height, width), dtype=np.uint8)
                cv2.fillPoly(eye_outer_mask, [eye_outer], 255)

                d_in = cv2.distanceTransform(
                    255 - eye_inner_mask, cv2.DIST_L2, 5).astype(np.float32)
                d_out = cv2.distanceTransform(
                    eye_outer_mask, cv2.DIST_L2, 5).astype(np.float32)

                eye_mask = np.zeros((height, width), dtype=np.float32)
                eye_mask[eye_inner_mask > 0] = 1.0
                zone = (eye_inner_mask == 0) & (eye_outer_mask > 0)
                if np.any(zone):
                    di = d_in[zone]
                    do = d_out[zone]
                    a = do / np.maximum(di + do, 1e-6)
                    a = a * a * (3.0 - 2.0 * a)
                    eye_mask[zone] = a
                np.maximum(mask, eye_mask, out=mask)

        # --- Gaussian blur for sub-pixel smoothness ---
        if self.blur_px > 1:
            mask = cv2.GaussianBlur(mask, (self.blur_px, self.blur_px), 0)
            mask = np.clip(mask, 0.0, 1.0)

        return mask

    def _extract_crop_256(self, orifra_frame: np.ndarray,
                          crop_x: float, crop_y: float,
                          crop_size: float) -> np.ndarray | None:
        """Extract the crop region from orifra and resize to 256x256.

        Kept for test compatibility.
        """
        x1 = int(round(crop_x))
        y1 = int(round(crop_y))
        x2 = x1 + int(round(crop_size))
        y2 = y1 + int(round(crop_size))

        x1_c = max(0, x1)
        y1_c = max(0, y1)
        x2_c = min(orifra_frame.shape[1], x2)
        y2_c = min(orifra_frame.shape[0], y2)

        if x2_c <= x1_c or y2_c <= y1_c:
            return None

        crop = orifra_frame[y1_c:y2_c, x1_c:x2_c]

        h, w = crop.shape[:2]
        expected = int(round(crop_size))
        if h < expected or w < expected:
            padded = np.zeros((expected, expected, 3), dtype=np.uint8)
            off_y = y1_c - y1
            off_x = x1_c - x1
            padded[off_y:off_y + h, off_x:off_x + w] = crop
            crop = padded

        return cv2.resize(crop, (256, 256), interpolation=cv2.INTER_LINEAR)
