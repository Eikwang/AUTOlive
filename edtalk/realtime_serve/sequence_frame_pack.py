"""Character data pack for loading and managing preprocessed video frames.

Batched sliding-window preload design:
    Frames are preloaded in batches of BATCH_SIZE. A sliding window of two
    batches is maintained: the current batch is being rendered while the
    next batch is loaded in a background thread.

    Timing contract (critical for stable 25fps):
    - The preload of batch N+1 starts the moment batch N becomes current
      (NOT when the render reaches 90% of batch N). This gives the preloader
      the ENTIRE batch duration (~4s at 25fps for 100 frames) to load the
      next ~0.5s of work.
    - JPEG decoding is parallelized across a thread pool (cv2.imread
      releases the GIL), so the preload thread does not starve the render
      thread or the audio callback.
    - The render thread NEVER performs a synchronous batch load: if the
      requested frame is outside the window, a background load is started
      and get_frame() falls back to a single-frame disk read (~25ms).

Directory structure:
    <character_dir>/
    ├── orifra/<segment_name>/     # Original frames 1080p (JPEG)
    ├── count/<segment_name>/      # Cropped frames 256x256 (JPEG)
    ├── metadata/<segment_name>.npy  # Crop coordinates (structured array)
    ├── facemark/<segment_name>.npy  # 68 landmark points (N, 68, 2) float32
    └── checkpoint/
        ├── audio2lip.pt          # Audio2Lip model weights
        └── new.pt               # Generator model weights
"""
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Tuple, List, Optional

import numpy as np
import cv2
import torch


def _transfer_crops_to_device(crop_tensor: torch.Tensor, device: str,
                              chunk_size: int) -> torch.Tensor:
    """Move crops to the device in bounded chunks (~8MB each) so one large
    transfer cannot monopolize the CUDA context and stall render kernels."""
    chunks = []
    for i in range(0, crop_tensor.shape[0], chunk_size):
        chunks.append(crop_tensor[i:i + chunk_size].to(device))
    return chunks[0] if len(chunks) == 1 else torch.cat(chunks, dim=0)


def extract_pristine_crop(orifra: np.ndarray, meta) -> Optional[np.ndarray]:
    """Extract the 256x256 pristine crop region from an orifra frame.

    Replicates MouthCompositor.prepare_crop's clamp geometry (x/y clamped to
    frame bounds, padded when the crop runs past an edge) so the result lives
    in the SAME 256 space as the count crops / facemark. Returns a FRESH array
    (cv2.resize always allocates) — callers may mutate orifra afterwards
    without aliasing this crop (GAZE_TIMING_PLAN F5).
    """
    crop_x = float(meta['crop_x'])
    crop_y = float(meta['crop_y'])
    crop_size = float(meta['crop_size'])
    if crop_size <= 0:
        return None
    size = int(round(crop_size))
    if size <= 0:
        return None
    x1, y1 = int(round(crop_x)), int(round(crop_y))
    x2, y2 = x1 + size, y1 + size
    x1c, y1c = max(0, x1), max(0, y1)
    x2c = min(orifra.shape[1], x2)
    y2c = min(orifra.shape[0], y2)
    if x2c - x1c <= 0 or y2c - y1c <= 0:
        return None
    crop = orifra[y1c:y2c, x1c:x2c]
    if crop.shape[0] < size or crop.shape[1] < size:
        padded = np.zeros((size, size, 3), dtype=np.uint8)
        padded[y1c - y1:y1c - y1 + crop.shape[0],
               x1c - x1:x1c - x1 + crop.shape[1]] = crop
        crop = padded
    return cv2.resize(crop, (256, 256), interpolation=cv2.INTER_LINEAR)


class BatchPreloader:
    """Batched sliding-window preloader for video frames.

    Maintains a window of two batches. The batch after the current one is
    always being preloaded in the background (wrap-aware at segment end).

    Memory layout (BATCH_SIZE=100):
        orifra frames: CPU numpy arrays (100 x ~11MB = ~1.1GB per batch)
        crop tensors:  GPU float32 (100 x 786KB = ~79MB per batch)
    """

    BATCH_SIZE = 100   # frames per batch
    NUM_BATCHES = 2    # window = current + next

    _DECODE_WORKERS = 8
    # Max frames per CPU->GPU transfer. Small chunks (~8MB) interleave with
    # render-loop kernels instead of one 79MB burst monopolizing PCIe/L2 —
    # measured as a contributor to gen p99 spikes (60-120ms).
    _TRANSFER_CHUNK = 10

    def __init__(self, segment_cache: dict, device: str = 'cuda',
                 compositor=None):
        """Initialize BatchPreloader.

        Args:
            segment_cache: Reference to CharacterDataPack._segment_cache
            device: Device for crop tensors
            compositor: MouthCompositor instance for mask precomputation
        """
        self._segment_cache = segment_cache
        self._device = device
        self._compositor = compositor

        # Batch buffers: [batch_slot][frame_offset] = data
        # Two slots for double-buffering
        self._orifra_bufs: List[Optional[List[np.ndarray]]] = [None, None]
        self._crop_bufs: List[Optional[torch.Tensor]] = [None, None]  # [B, 3, 256, 256]
        self._mask_bufs: List[Optional[List[np.ndarray]]] = [None, None]  # precomputed masks
        self._facemark_bufs: List[Optional[List[np.ndarray]]] = [None, None]  # stored facemark for mask recompute
        # Pristine 256 crop per frame ((256,256,3) BGR uint8, GAZE_TIMING_PLAN T1):
        # decoded ONCE at batch load from the untouched orifra JPEG — blend()
        # writes the composited result into the orifra buffer in place, so the
        # eye-source for gaze correction must come from a copy that blend can
        # never reach (loop-wrap double-render would otherwise stack gaze
        # corrections, ENG A-3 / GAZE_TIMING_PLAN F5).
        self._pristine_bufs: List[Optional[List[np.ndarray]]] = [None, None]

        # Each batch's start frame index
        self._batch_starts: List[int] = [-1, -1]

        # Which slot is "current" (0 or 1)
        self._current_slot: int = 0

        # Current segment name
        self._segment: Optional[str] = None

        # Preload thread control
        self._preload_thread: Optional[threading.Thread] = None
        self._preload_lock = threading.Lock()
        self._preload_done = threading.Event()
        self._preload_target_start: int = -1

        # State lock: serializes ensure_loaded decisions from the render
        # thread and API threads (segment switch / prefetch).
        self._state_lock = threading.Lock()

        # Generation counter: bumped on reset(); in-flight workers discard
        # their results if the generation changed (segment switched away).
        self._gen = 0

        # Mask epoch (M18): bumped by invalidate_masks() when a paste-back
        # setting changes (e.g. eyes_enabled). In-flight batch loads check
        # the epoch before publishing and drop setting-dependent masks that
        # were generated under the OLD setting.
        self._mask_epoch = 0

        # Persistent decode pool (cv2.imread releases the GIL)
        self._decode_pool = ThreadPoolExecutor(
            max_workers=min(self._DECODE_WORKERS, os.cpu_count() or 4)
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def reset(self, segment: str):
        """Reset preloader for a new segment (fast, non-blocking).

        Abandoning any in-flight preload is safe: the worker checks the
        generation counter before publishing its results.
        """
        with self._state_lock:
            self._reset_locked(segment)

    def ensure_loaded(self, frame_idx: int, segment: str):
        """Ensure frame_idx is available in the preloaded window.

        Call this BEFORE get_frame() for each render iteration. This method
        never blocks on disk I/O — worst case it starts a background load
        and get_frame() falls back to a single-frame disk read.

        Logic:
        1. frame_idx in current batch -> make sure next batch is preloading
        2. frame_idx in next batch -> swap to it (if loaded), keep chain going
        3. frame_idx outside window (jump / cold start) -> start a background
           load of the batch containing it; get_frame falls back meanwhile
        """
        with self._state_lock:
            if segment != self._segment:
                self._reset_locked(segment)

            data = self._segment_cache[segment]
            total = data['total_frames']
            frame_idx %= total

            cur = self._current_slot
            other = 1 - cur

            # Fast path: frame_idx is in current batch
            if self._is_in_batch(frame_idx, cur):
                self._maybe_preload_next(segment, total)
                return

            # frame_idx is in the other batch
            if self._is_in_batch(frame_idx, other):
                if self._is_preloading():
                    # Still loading — get_frame uses disk fallback
                    return
                # Preload done — swap to it
                self._current_slot = other
                self._maybe_preload_next(segment, total)
                return

            # frame_idx not in any batch (jump or cold start)
            if self._is_preloading():
                return

            target_slot = other if self._batch_starts[cur] >= 0 else cur
            start = (frame_idx // self.BATCH_SIZE) * self.BATCH_SIZE
            self._start_preload(target_slot, start, segment)

    def load_initial(self, segment: str):
        """Synchronously load batch 0 and kick off batch 1 (startup only)."""
        with self._state_lock:
            if segment != self._segment:
                self._reset_locked(segment)
            data = self._segment_cache[segment]
            total = data['total_frames']
            if self._is_in_batch(0, self._current_slot):
                return
            if self._is_in_batch(0, 1 - self._current_slot):
                self._current_slot = 1 - self._current_slot
                self._maybe_preload_next(segment, total)
                return
            self._load_batch_sync(self._current_slot, 0, segment, self._gen)
            self._maybe_preload_next(segment, total)

    def get_orifra(self, frame_idx: int) -> Optional[np.ndarray]:
        """Get orifra frame from preloaded buffer.

        Returns:
            orifra frame as BGR uint8 numpy array, or None if not loaded
        """
        slot = self._current_slot
        start = self._batch_starts[slot]
        if start < 0 or not (start <= frame_idx < start + self.BATCH_SIZE):
            return None
        offset = frame_idx - start
        bufs = self._orifra_bufs[slot]
        if bufs is None or offset >= len(bufs):
            return None
        return bufs[offset]

    def get_crop(self, frame_idx: int) -> Optional[torch.Tensor]:
        """Get crop tensor from preloaded buffer (already on GPU).

        Returns:
            crop tensor [1, 3, 256, 256] on GPU, or None if not loaded
        """
        slot = self._current_slot
        start = self._batch_starts[slot]
        if start < 0 or not (start <= frame_idx < start + self.BATCH_SIZE):
            return None
        offset = frame_idx - start
        batch = self._crop_bufs[slot]
        if batch is None or offset >= batch.shape[0]:
            return None
        return batch[offset:offset+1]  # [1, 3, 256, 256]

    def get_mask(self, frame_idx: int) -> Optional[np.ndarray]:
        """Get precomputed mouth mask from preloaded buffer.

        Returns:
            mask (256, 256) float32, or None if not available
        """
        slot = self._current_slot
        start = self._batch_starts[slot]
        if start < 0 or not (start <= frame_idx < start + self.BATCH_SIZE):
            return None
        offset = frame_idx - start
        masks = self._mask_bufs[slot]
        if masks is None or offset >= len(masks):
            return None
        return masks[offset]

    def get_pristine(self, frame_idx: int) -> Optional[np.ndarray]:
        """Get the pristine 256 crop from the preloaded buffer (T1).

        Returns:
            (256, 256, 3) BGR uint8 decoded from the untouched orifra JPEG,
            or None when the frame is outside the current window (caller
            falls back to extract_pristine_crop + pristine_fallback counter).
        """
        slot = self._current_slot
        start = self._batch_starts[slot]
        if start < 0 or not (start <= frame_idx < start + self.BATCH_SIZE):
            return None
        offset = frame_idx - start
        pristine = self._pristine_bufs[slot]
        if pristine is None or offset >= len(pristine):
            return None
        return pristine[offset]

    def recompute_current_masks(self):
        """Recompute masks for the currently loaded batch using the compositor.

        Call this after setting the compositor to generate masks for
        frames that were loaded before the compositor was available.
        """
        if self._compositor is None:
            return
        slot = self._current_slot
        facemark_data = self._facemark_bufs[slot]
        if facemark_data is None:
            return
        mask_list = []
        for fm in facemark_data:
            if fm is not None:
                mask = self._compositor.generate_mask(fm)
                mask_list.append(mask)
            else:
                mask_list.append(None)
        self._mask_bufs[slot] = mask_list

    def invalidate_masks(self) -> None:
        """Drop all computed masks after a paste-back setting change (M18).

        The two-slot window only recomputed the CURRENT slot before, and a
        batch load in flight would publish masks generated under the OLD
        setting — both left ~one batch window pasting the wrong eyes.
        Clearing everything forces the render loop's per-frame mask
        fallback (~1ms/frame) until fresh batches land; in-flight loads
        check the epoch before publishing and drop their stale masks.
        """
        with self._preload_lock:
            self._mask_epoch += 1
            self._mask_bufs = [None, None]

    @property
    def is_preloading(self) -> bool:
        """Whether a background preload is in progress."""
        return self._preload_thread is not None and self._preload_thread.is_alive()

    @property
    def stats(self) -> dict:
        """Diagnostics for logging."""
        return {
            'batch_start': self._batch_starts[self._current_slot],
            'preloading': self.is_preloading,
            'segment': self._segment,
        }

    def close(self):
        """Shut down the decode pool."""
        if self._decode_pool is not None:
            self._decode_pool.shutdown(wait=False)
            self._decode_pool = None

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _reset_locked(self, segment: str):
        """Reset all state for a new segment. Caller holds _state_lock."""
        self._gen += 1
        self._segment = segment
        self._orifra_bufs = [None, None]
        self._crop_bufs = [None, None]
        self._mask_bufs = [None, None]
        self._facemark_bufs = [None, None]
        self._pristine_bufs = [None, None]
        self._batch_starts = [-1, -1]
        self._current_slot = 0
        self._preload_target_start = -1
        self._preload_done.set()

    def _maybe_preload_next(self, segment: str, total: int):
        """Ensure the batch after the current one is (being) preloaded.

        Wrap-aware: after the last batch, the next batch is batch 0, so the
        segment loops seamlessly. Caller holds _state_lock.
        """
        start = self._batch_starts[self._current_slot]
        if start < 0:
            return
        next_start = start + self.BATCH_SIZE
        if next_start >= total:
            next_start = 0
        other = 1 - self._current_slot
        if self._batch_starts[other] == next_start:
            return  # already loaded (or being loaded) into the other slot
        if self._is_preloading():
            return
        self._start_preload(other, next_start, segment)

    def _start_preload(self, slot: int, start_idx: int, segment: str):
        """Start async preloading of a batch. Caller holds _state_lock."""
        self._preload_done.clear()
        self._preload_target_start = start_idx
        gen = self._gen
        self._preload_thread = threading.Thread(
            target=self._preload_worker,
            args=(slot, start_idx, segment, gen),
            daemon=True
        )
        self._preload_thread.start()

    def _preload_worker(self, slot: int, start_idx: int, segment: str,
                        gen: int):
        """Background thread: load next batch."""
        try:
            self._load_batch_sync(slot, start_idx, segment, gen)
        except Exception as e:
            print(f"[BatchPreloader] Preload error: {e}")
        finally:
            self._preload_done.set()

    def _get_decode_pool(self) -> ThreadPoolExecutor:
        return self._decode_pool

    @staticmethod
    def _decode_frame(job):
        """Decode one frame triple (pure function, GIL-released in cv2).

        job = (orifra_path, crop_path, meta). The third element also yields
        the pristine 256 crop (GAZE_TIMING_PLAN T1) — computed here on the
        decode pool from the freshly read, never-yet-blended orifra.
        """
        orifra_path, crop_path, meta = job
        img = cv2.imread(orifra_path)
        if img is None:
            raise ValueError(f"Failed to read orifra: {orifra_path}")
        crop_img = cv2.imread(crop_path)
        if crop_img is None:
            raise ValueError(f"Failed to read crop: {crop_path}")
        crop_rgb = cv2.cvtColor(crop_img, cv2.COLOR_BGR2RGB)
        # Normalize in numpy (avoids per-frame torch CPU ops holding the GIL)
        crop_np = np.ascontiguousarray(
            crop_rgb.astype(np.float32).transpose(2, 0, 1) / 127.5 - 1.0
        )
        pristine = extract_pristine_crop(img, meta)
        return img, crop_np, pristine

    def _load_batch_sync(self, slot: int, start_idx: int, segment: str,
                         gen: int):
        """Load a batch of frames into the specified slot.

        Args:
            slot: Buffer slot (0 or 1)
            start_idx: First frame index to load
            segment: Segment name
            gen: Generation at request time; results are discarded if the
                 preloader was reset while loading.
        """
        data = self._segment_cache[segment]
        total = data['total_frames']
        count = min(self.BATCH_SIZE, total - start_idx)
        if count <= 0:
            return

        # Mask settings snapshot (M18): if the paste-back setting changes
        # while this batch decodes, the masks generated here are stale and
        # get dropped at publish time.
        mask_epoch = self._mask_epoch

        facemark_data = data.get('facemark')  # (total_frames, 68, 2)

        # Phase 1: parallel JPEG decode (cv2 releases the GIL)
        jobs = []
        for i in range(count):
            idx = (start_idx + i) % total
            jobs.append((data['orifra_files'][idx], data['crop_files'][idx],
                         data['metadata'][idx]))
        pool = self._get_decode_pool()
        decoded = list(pool.map(self._decode_frame, jobs))

        if gen != self._gen:
            return  # reset happened while loading — discard

        orifra_list = [d[0] for d in decoded]
        crop_np_list = [d[1] for d in decoded]
        pristine_list = [d[2] for d in decoded]

        # Phase 2: mask precomputation (serial — the compositor's landmark
        # EMA is order-dependent, so masks must be generated in frame order)
        mask_list = []
        facemark_list = []
        for i in range(count):
            fm = facemark_data[(start_idx + i) % total] if facemark_data is not None else None
            facemark_list.append(fm)
            if fm is not None and self._compositor is not None:
                mask_list.append(self._compositor.generate_mask(fm))
            else:
                mask_list.append(None)

        # Phase 3: move to GPU in bounded chunks (each ~79MB, ~10ms) so a
        # large transfer cannot monopolize the CUDA context and stall the
        # render thread's kernels
        crop_np = np.stack(crop_np_list, axis=0)  # [B, 3, 256, 256]
        crop_tensor = torch.from_numpy(crop_np)
        del crop_np_list, decoded
        crop_batch = _transfer_crops_to_device(
            crop_tensor, self._device, self._TRANSFER_CHUNK)

        if gen != self._gen:
            return  # reset happened while loading — discard

        if mask_epoch != self._mask_epoch:
            # Paste-back setting changed mid-load (M18): the orifra/crop
            # frames are setting-independent and stay; the masks were
            # generated under the old setting and are dropped — the render
            # loop's per-frame fallback covers until the next batch.
            mask_list = [None] * len(mask_list)

        with self._preload_lock:
            self._orifra_bufs[slot] = orifra_list
            self._crop_bufs[slot] = crop_batch
            self._mask_bufs[slot] = mask_list
            self._facemark_bufs[slot] = facemark_list
            self._pristine_bufs[slot] = pristine_list
            self._batch_starts[slot] = start_idx

    def _is_in_batch(self, frame_idx: int, slot: int) -> bool:
        """Check if frame_idx falls within the batch in the given slot."""
        start = self._batch_starts[slot]
        if start < 0:
            return False
        return start <= frame_idx < start + self.BATCH_SIZE

    def _is_preloading(self) -> bool:
        """Whether a preload is in flight. Caller holds _state_lock."""
        return self._preload_thread is not None and self._preload_thread.is_alive()

    def publish_prefetch(self, segment: str, orifra_list: List[np.ndarray],
                         crop_batch: torch.Tensor,
                         facemark_list: List[Optional[np.ndarray]],
                         pristine_list: Optional[List[Optional[np.ndarray]]] = None) -> bool:
        """Install a prefetched batch-0 into slot 0 (M15 seamless switching).

        MUST be called right after reset(segment) and before ensure_loaded(0):
        the caller then proceeds with the normal fast path, which kicks off
        the batch-1 background preload — the cold-start disk-fallback window
        disappears entirely.

        Masks are NOT carried by the prefetch (the compositor's landmark EMA
        is order-sensitive and must not interleave two segments — H2): they
        are generated here in frame order, only when no preload worker is
        alive; otherwise slot 0 keeps mask=None and the render loop's
        per-frame fallback covers the window (~1ms/frame, self-healing).

        pristine_list (T1): the prefetcher's pristine 256 crops; None entries
        (or a missing list) leave those frames to the render loop's
        extract_pristine_crop fallback.

        Returns False when the preloader has moved to a different segment
        (stale publish — caller falls back to the normal cold path).
        """
        with self._preload_lock:
            if self._segment != segment:
                return False
            n = len(orifra_list)
            self._orifra_bufs[0] = orifra_list
            self._crop_bufs[0] = crop_batch
            self._facemark_bufs[0] = facemark_list
            self._mask_bufs[0] = [None] * n
            self._pristine_bufs[0] = (list(pristine_list)
                                      if pristine_list is not None
                                      else [None] * n)
            self._batch_starts[0] = 0

        # Lock-free mask generation in frame order (EMA is order-dependent).
        # Skipped when a preload worker is alive: its Phase-2 mask generation
        # would interleave with ours on the shared compositor EMA state.
        if (self._compositor is not None and not self._is_preloading()):
            masks = [self._compositor.generate_mask(fm) if fm is not None
                     else None for fm in facemark_list]
            with self._preload_lock:
                if (self._segment == segment and self._batch_starts[0] == 0
                        and self._mask_bufs[0] is not None
                        and len(self._mask_bufs[0]) == n
                        and all(m is None for m in self._mask_bufs[0])):
                    self._mask_bufs[0] = masks
        return True


class _PrefetchData:
    """A completed prefetch: raw materials of one segment's batch 0.

    Carries NO masks (H2): the compositor's landmark EMA is order-sensitive
    and must not see two segments interleaved — masks are generated after
    publish, in frame order, by BatchPreloader.publish_prefetch.
    pristine_list (T1) carries the pristine 256 crops (None entries allowed —
    the render loop's extract fallback covers those frames).
    """

    def __init__(self, segment: str, orifra_list: List[np.ndarray],
                 crop_batch: torch.Tensor,
                 facemark_list: List[Optional[np.ndarray]],
                 pristine_list: Optional[List[Optional[np.ndarray]]] = None):
        self.segment = segment
        self.orifra_list = orifra_list
        self.crop_batch = crop_batch
        self.facemark_list = facemark_list
        self.pristine_list = pristine_list


class PrefetchLoader:
    """Independent single-flight prefetcher for seamless segment switching (M15).

    Deliberately NOT part of BatchPreloader: the two-slot sliding window is a
    hard invariant (other = 1 - cur, single _preload_thread, single
    _preload_done) that a concurrent third buffer would break. This loader
    holds raw materials only (orifra/crop/facemark — no masks) in its own
    buffer; CharacterDataPack.switch_segment publishes them into slot 0 via
    BatchPreloader.publish_prefetch.

    Cancellation: a monotonic token per request; only the newest request's
    results are kept. Any newer start()/cancel() releases the previous
    result's references immediately (the ~1.1GB CPU / 79MB GPU transient is
    bounded and never dangles). Decode yields in chunks of BATCH_SIZE//4
    frames so the current segment's sliding-window preload keeps priority on
    the shared decode pool (E5: prefetch must not starve the window loader
    and re-create batch-boundary stalls elsewhere).
    """

    _YIELD_S = 0.002

    def __init__(self, character_data: 'CharacterDataPack',
                 preloader: BatchPreloader, device: str):
        self._pack = character_data
        self._preloader = preloader
        self._device = device
        self._lock = threading.Lock()
        self._token = 0
        self._state = 'idle'      # idle | loading | ready | discarded
        self._segment: Optional[str] = None
        self._result: Optional[_PrefetchData] = None
        self._thread: Optional[threading.Thread] = None

    def start(self, segment: str) -> None:
        """Start (or supersede) a prefetch for segment. Never blocks long."""
        with self._lock:
            if self._state == 'loading' and self._segment == segment:
                return  # already in flight for this segment
            self._token += 1
            token = self._token
            self._state = 'loading'
            self._segment = segment
            self._result = None  # release previous result references now
            self._thread = threading.Thread(
                target=self._worker, args=(segment, token), daemon=True)
            self._thread.start()

    def cancel(self) -> None:
        """Invalidate any in-flight/completed prefetch and free references."""
        with self._lock:
            self._token += 1
            self._result = None
            self._state = 'idle'
            self._segment = None

    def pop(self, segment: str) -> Optional[_PrefetchData]:
        """Consume the prefetch result for segment if it is ready and current."""
        with self._lock:
            if (self._state == 'ready' and self._result is not None
                    and self._result.segment == segment):
                data = self._result
                self._result = None
                self._state = 'idle'
                self._segment = None
                return data
        return None

    @property
    def status(self) -> dict:
        """Snapshot for /status: {'state': ..., 'segment': ...}."""
        with self._lock:
            return {'state': self._state, 'segment': self._segment}

    def _stale(self, token: int) -> bool:
        with self._lock:
            return token != self._token

    def _worker(self, segment: str, token: int) -> None:
        try:
            # Metadata/file lists are cached and idempotent; may raise if the
            # segment's data is broken — surfaced as a discarded prefetch.
            self._pack._load_segment(segment)
            data = self._pack._segment_cache[segment]
            total = data['total_frames']
            count = min(self._preloader.BATCH_SIZE, total)
            if count <= 0:
                raise ValueError(f"segment '{segment}' has no frames")

            pool = self._preloader._get_decode_pool()
            files = [(data['orifra_files'][i], data['crop_files'][i],
                      data['metadata'][i])
                     for i in range(count)]
            cap = max(1, self._preloader.BATCH_SIZE // 4)
            decoded = []
            for s in range(0, count, cap):
                if self._stale(token):
                    return
                decoded.extend(pool.map(BatchPreloader._decode_frame,
                                        files[s:s + cap]))
                time.sleep(self._YIELD_S)  # yield pool to the window loader
            if self._stale(token):
                return

            crop_np = np.stack([d[1] for d in decoded], axis=0)
            crop_batch = _transfer_crops_to_device(
                torch.from_numpy(crop_np), self._device,
                self._preloader._TRANSFER_CHUNK)
            facemark = data.get('facemark')
            facemark_list = [facemark[i] if facemark is not None else None
                             for i in range(count)]
            pristine_list = [d[2] for d in decoded]
            if self._stale(token):
                return

            with self._lock:
                if token == self._token:
                    self._result = _PrefetchData(
                        segment, [d[0] for d in decoded], crop_batch,
                        facemark_list, pristine_list)
                    self._state = 'ready'
        except Exception as e:
            # Prefetch is best-effort: a failure degrades the upcoming switch
            # to the normal cold path — never blocks, never kills anything.
            print(f"[PrefetchLoader] prefetch '{segment}' failed: {e}")
            with self._lock:
                if token == self._token:
                    self._result = None
                    self._state = 'discarded'


class CharacterDataPack:
    """Loads and manages preprocessed character data with multiple segments.

    Batched preload: Only metadata/facemark are loaded at startup. Orifra and
    crop frames are preloaded in batches via BatchPreloader — a sliding window
    of 2 x BATCH_SIZE frames is maintained, with the next batch always
    preloading in the background.

    Each segment contains:
    - orifra: Original frames at full resolution (batch-preloaded to CPU)
    - count: Cropped frames at 256x256 (batch-preloaded to GPU)
    - metadata: Crop coordinates for each frame (loaded at startup)
    - facemark: 68 facial landmark points per frame (loaded at startup)
    """

    def __init__(self, character_dir: str, device: str = 'cuda',
                 batch_size: int = 100):
        """Initialize CharacterDataPack.

        Args:
            character_dir: Path to character directory containing all data
            device: Device to load cropped frames on ('cuda' or 'cpu')
            batch_size: Number of frames per preload batch (default: 100)

        Raises:
            FileNotFoundError: If required paths don't exist
        """
        self.character_dir = character_dir
        self.device = device

        # Discover available segments by listing orifra subdirectories
        self.orifra_base = os.path.join(character_dir, "orifra")
        self.count_base = os.path.join(character_dir, "count")
        self.metadata_base = os.path.join(character_dir, "metadata")
        self.facemark_base = os.path.join(character_dir, "facemark")
        self.checkpoint_dir = os.path.join(character_dir, "checkpoint")

        if not os.path.exists(self.orifra_base):
            raise FileNotFoundError(f"orifra directory not found: {self.orifra_base}")

        self.segments: List[str] = sorted([
            d for d in os.listdir(self.orifra_base)
            if os.path.isdir(os.path.join(self.orifra_base, d))
        ])

        if not self.segments:
            raise ValueError(f"No segments found in {self.orifra_base}")

        # Default to first segment
        self.current_segment = self.segments[0]

        # Cache for loaded segment data
        self._segment_cache: dict = {}

        # Serializes concurrent switch_segment calls (double-load guard)
        self._switch_lock = threading.Lock()

        # Batch preloader (replaces LRU cache)
        BatchPreloader.BATCH_SIZE = batch_size
        self._preloader = BatchPreloader(self._segment_cache, device)

        # Seamless-switch prefetcher (M15): independent single-flight loader
        # whose results are published into the preloader's slot 0 on switch.
        self._prefetch = PrefetchLoader(self, self._preloader, device)

        # Per-segment resolution cache (avoids re-reading a frame from disk;
        # keyed by segment because segments may differ in resolution)
        self._resolution_cache: dict = {}

        # Pristine-crop fallback counter (GAZE_TIMING_PLAN DX 发现6): frames
        # whose eye source came from the extract path instead of the preloaded
        # window — exposed as /status gaze_frames.pristine_fallback.
        self.pristine_fallbacks = 0

        # Load current segment (only metadata + facemark + file lists)
        self._load_segment(self.current_segment)

        # Preload first batch for current segment (blocking — startup only),
        # then kick off the background preload of the second batch.
        self._preloader.load_initial(self.current_segment)

        print(f'==> loaded character: {character_dir}')
        print(f'  -> Available segments: {self.segments}')
        print(f'  -> Current segment: {self.current_segment}')
        print(f'  -> Batch preload enabled (batch_size={batch_size}, window={batch_size*2})')

    def set_compositor(self, compositor):
        """Set the compositor for mask precomputation.

        Call this after MouthCompositor is created to enable mask precomputation
        during batch loading.
        """
        self._preloader._compositor = compositor
        # Recompute masks for already-loaded frames
        self._preloader.recompute_current_masks()

    def set_eyes_enabled(self, enabled: bool):
        """Toggle eye regions in the precomputed paste-back masks.

        Required for gaze correction: with eyes enabled the rendered eye
        regions are pasted back onto the original frame. Masks generated
        under the previous setting are invalidated across BOTH slots and
        in-flight loads (M18 mask epoch); the current batch is recomputed
        synchronously (~10ms per 100 frames), other windows fall back to
        per-frame mask generation until fresh batches land.
        """
        compositor = self._preloader._compositor
        if compositor is None:
            return
        compositor.set_eyes_enabled(enabled)
        self._preloader.invalidate_masks()
        self._preloader.recompute_current_masks()

    def has_gaze_data(self) -> bool:
        """Whether the current segment has precomputed gaze data (step 11)."""
        data = self._segment_cache.get(self.current_segment)
        return data is not None and data.get('gaze_data') is not None

    def get_gaze_frame(self, idx: int) -> Optional[dict]:
        """Per-frame precomputed gaze data (state/eye centers/eye_size).

        Returns None when the segment has no gaze_data — the corrector then
        falls back to per-frame realtime landmark math.
        """
        data = self._segment_cache[self.current_segment]
        gd = data.get('gaze_data')
        if gd is None:
            return None
        i = idx % data['total_frames']
        return {
            'state': gd['state'][i],
            'l_center': gd['l_center'][i],
            'r_center': gd['r_center'][i],
            'eye_size': gd['eye_size'],  # segment-level scalar
        }

    def get_pristine_crop(self, idx: int, orifra: np.ndarray,
                          meta) -> Optional[np.ndarray]:
        """Pristine 256 crop for the gaze eye-source (GAZE_TIMING_PLAN T1/F5).

        Preloaded window first; on a window miss (disk-fallback frame right
        after a switch/jump) extracts from the freshly provided orifra frame
        via extract_pristine_crop (FRESH memory — cv2.resize always allocates,
        never a view of the blend-mutated buffer) and counts the fallback.

        Returns None only when the crop geometry is unusable (meta broken) —
        the caller then skips the eye paste for this frame.
        """
        pristine = self._preloader.get_pristine(idx % self._segment_cache[self.current_segment]['total_frames'])
        if pristine is not None:
            return pristine
        pristine = extract_pristine_crop(orifra, meta)
        if pristine is not None:
            self.pristine_fallbacks += 1
            print(f"[CharacterDataPack] pristine fallback "
                  f"(frame {idx}) — [{self.pristine_fallbacks}]")
        return pristine

    def _get_segment_paths(self, segment: str) -> dict:
        """Get paths for a specific segment."""
        return {
            'orifra': os.path.join(self.orifra_base, segment),
            'count': os.path.join(self.count_base, segment),
            'metadata': os.path.join(self.metadata_base, f"{segment}.npy"),
            'facemark': os.path.join(self.facemark_base, f"{segment}.npy"),
        }

    def _validate_segment(self, segment: str) -> bool:
        """Validate that a segment has all required data."""
        paths = self._get_segment_paths(segment)
        return (
            os.path.exists(paths['orifra']) and
            os.path.exists(paths['count']) and
            os.path.exists(paths['metadata']) and
            os.path.exists(paths['facemark'])
        )

    def _load_segment(self, segment: str):
        """Load a segment's metadata and file lists (NOT actual frame data).

        Frames are loaded lazily in batches via the BatchPreloader.
        Only metadata, facemark, and file path lists are loaded here.
        """
        if segment in self._segment_cache:
            return

        paths = self._get_segment_paths(segment)

        # Validate paths
        for key in ['orifra', 'count', 'metadata', 'facemark']:
            if not os.path.exists(paths[key]):
                raise FileNotFoundError(f"{key} not found: {paths[key]}")

        # Load metadata and facemark (small files, fast)
        metadata = np.load(paths['metadata'])
        facemark = np.load(paths['facemark'])

        # Optional precomputed gaze data (prepare_gaze_data.py step 11):
        # per-frame blink labels + smoothed eye trajectories for the
        # gaze corrector. Absent → corrector falls back to realtime
        # per-frame landmark math (noisy; preprocessing recommended).
        gaze_data = None
        gaze_path = os.path.join(
            os.path.dirname(paths['facemark']), '..', 'gaze_data',
            f"{segment}.npy")
        gaze_path = os.path.normpath(gaze_path)
        if os.path.exists(gaze_path):
            gaze_data = np.load(gaze_path, allow_pickle=True).item()
            if gaze_data['state'].shape[0] != facemark.shape[0]:
                print(f"  [WARN] segment '{segment}': gaze_data length mismatch, ignoring")
                gaze_data = None

        # Build sorted file lists (just paths, no image data)
        orifra_files = self._build_file_list(paths['orifra'])
        crop_files = self._build_file_list(paths['count'])

        # Validate consistency
        num_orifra = len(orifra_files)
        num_crop = len(crop_files)
        num_meta = len(metadata)
        num_facemark = facemark.shape[0]

        if num_orifra != num_crop or num_orifra != num_meta or num_orifra != num_facemark:
            raise ValueError(
                f"Data mismatch in segment '{segment}': "
                f"orifra={num_orifra}, crop={num_crop}, "
                f"metadata={num_meta}, facemark={num_facemark}"
            )

        # 无脸帧统计（画外音直通依据）：预处理对未检出人脸的帧写全 NaN 关键点
        # （crop_head_video.py 主循环）。face_ratio 供 /status 与素材管理页展示；
        # 逐帧直通判定在 RenderLoop（facemark 任一 NaN → 该帧跳过口型/视线/修复）。
        if facemark.ndim == 3:
            face_valid = ~np.isnan(facemark).all(axis=(1, 2))
        else:
            face_valid = np.zeros(num_orifra, dtype=bool)

        self._segment_cache[segment] = {
            'orifra_files': orifra_files,   # list of file paths (lazy)
            'crop_files': crop_files,       # list of file paths (lazy)
            'metadata': metadata,           # numpy array (loaded)
            'facemark': facemark,           # numpy array (loaded)
            'gaze_data': gaze_data,         # dict of arrays or None
            'total_frames': num_orifra,
            'face_frames': int(face_valid.sum()),
            'face_ratio': float(face_valid.mean()) if face_valid.size else 0.0,
        }

        print(f'  -> Segment "{segment}": {num_orifra} frames (lazy), '
              f'face_ratio={self._segment_cache[segment]["face_ratio"]:.2f}, '
              f'resolution from metadata')

    def _build_file_list(self, directory: str) -> List[str]:
        """Build a numerically sorted list of JPEG file paths."""
        files = sorted([
            f for f in os.listdir(directory) if f.endswith('.jpg')
        ], key=self._numeric_sort_key)
        return [os.path.join(directory, f) for f in files]

    @staticmethod
    def _numeric_sort_key(filename: str):
        """Sort key that extracts the numeric part from filename."""
        stem = os.path.splitext(os.path.basename(filename))[0]
        try:
            return (0, int(stem))
        except ValueError:
            return (1, stem)

    def _read_orifra_frame(self, path: str) -> np.ndarray:
        """Read a single orifra frame from disk (fallback only)."""
        img = cv2.imread(path)
        if img is None:
            raise ValueError(f"Failed to read image: {path}")
        return img

    def ensure_loaded(self, idx: int):
        """Ensure frame idx is available in the preloaded window.

        Call this BEFORE get_frame() for each render iteration.
        Never blocks on I/O; worst case get_frame() reads the single
        requested frame from disk.
        """
        actual = idx % self._segment_cache[self.current_segment]['total_frames']
        self._preloader.ensure_loaded(actual, self.current_segment)

    def switch_segment(self, segment: str,
                       prefetched: Optional[_PrefetchData] = None) -> bool:
        """Switch to a different segment.

        Atomic order (autoplan ENG E1): load fully, prepare the preloader,
        and ONLY THEN publish current_segment. The render thread observes
        either the complete old segment or the complete new segment — never
        a half-loaded one (a KeyError here used to silently kill the render
        thread when a switch landed inside the load window).

        Seamless path (M15): when `prefetched` carries this segment's batch 0
        (from PrefetchLoader.pop), it is published into slot 0 right after
        reset — the new segment serves frames from cache immediately and the
        cold-start disk-fallback window disappears. Without it (no prefetch /
        discarded / stale) the switch degrades to the normal cold path and
        still succeeds.

        Any in-flight prefetch is cancelled at entry and its references
        released (a switch supersedes it by definition).

        Args:
            segment: Segment name to switch to
            prefetched: Optional ready prefetch result for this segment

        Returns:
            True if switch was successful
        """
        if segment not in self.segments:
            raise ValueError(f"Segment '{segment}' not found. Available: {self.segments}")

        with self._switch_lock:  # concurrent /segment/switch double-load guard
            self._prefetch.cancel()                   # in-flight prefetch is moot now
            self._load_segment(segment)               # may raise → old segment stays
            self._preloader.reset(segment)
            if prefetched is not None and prefetched.segment == segment:
                self._preloader.publish_prefetch(
                    segment, prefetched.orifra_list, prefetched.crop_batch,
                    prefetched.facemark_list, prefetched.pristine_list)
            self._preloader.ensure_loaded(0, segment)
            self.current_segment = segment            # publish LAST

        return True

    # ------------------------------------------------------------------
    # Seamless-switch prefetch (M15) — orchestrated by the control plane
    # ------------------------------------------------------------------

    def prefetch_segment(self, segment: str) -> None:
        """Start prefetching a segment's batch 0 (single-flight, token-based).

        Raises ValueError for unknown segments so callers get fail-fast
        validation at REQUEST time, not at switch time (M16).
        """
        if segment not in self.segments:
            raise ValueError(f"Segment '{segment}' not found. Available: {self.segments}")
        self._prefetch.start(segment)

    def pop_prefetch(self, segment: str) -> Optional[_PrefetchData]:
        """Consume a ready prefetch for segment (None if not ready — the
        caller then switches via the normal cold path)."""
        return self._prefetch.pop(segment)

    @property
    def prefetch_status(self) -> dict:
        """Snapshot for /status: {'state': idle|loading|ready|discarded, 'segment': ...}."""
        return self._prefetch.status

    # ------------------------------------------------------------------
    # Segment hot add/remove（素材管理：四件套落盘后热注册/热卸载）
    # ------------------------------------------------------------------

    def segment_info(self, segment: str) -> dict:
        """单段元信息快照（帧数/分辨率/人脸比例；素材管理与 /status 共用）。

        Raises ValueError for unknown segments（调用方映射 404）。
        """
        if segment not in self.segments:
            raise ValueError(
                f"Segment '{segment}' not found. Available: {self.segments}")
        data = self._segment_cache.get(segment)
        if data is None:
            self._load_segment(segment)  # may raise → caller surfaces 503
            data = self._segment_cache[segment]
        return {
            'segment': segment,
            'total_frames': int(data['total_frames']),
            'resolution': list(self.segment_resolution(segment)),
            'face_ratio': float(data.get('face_ratio', 0.0)),
            'face_frames': int(data.get('face_frames', 0)),
            'is_current': segment == self.current_segment,
        }

    def add_segment(self, segment: str) -> dict:
        """注册并（惰性）加载一个已落盘的段（素材热上线；幂等）。

        四件套必须已存在于 character_dir（orifra/<seg>/ + count/<seg>/ +
        metadata/<seg>.npy + facemark/<seg>.npy）——落盘由嵌入方（AUTOlive
        素材管理预处理）负责，本方法只做校验+注册，不复制不转换。

        注意：新段不自动进入播放调度（auto 序列在启动时解析）；需要参与
        默认轮播时由调用方 /segment/schedule 重装序列。
        """
        if not segment or '/' in segment or '\\' in segment or segment in ('.', '..'):
            raise ValueError(f"非法段名: {segment!r}")
        if segment not in self.segments:
            if not self._validate_segment(segment):
                raise FileNotFoundError(
                    f"段 '{segment}' 四件套不完整（需 orifra/<seg>/、count/<seg>/、"
                    f"metadata/<seg>.npy、facemark/<seg>.npy）：{self.character_dir}")
            # 重建排序列表（整体换绑：渲染线程读旧引用安全，GIL 下原子）
            self.segments = sorted(self.segments + [segment])
        self._load_segment(segment)  # 幂等；损坏数据在此抛出
        return self.segment_info(segment)

    def remove_segment(self, segment: str) -> dict:
        """热卸载一个段（内存移除；盘上文件由嵌入方负责删除）。

        保护：当前渲染段拒绝（渲染线程正从其缓存取帧）。与 switch_segment
        共用 _switch_lock 串行化，避免与切换事务交叠。若有针对该段的在途
        prefetch 一并取消。
        """
        info = self.segment_info(segment)  # unknown → ValueError（调用方 404）
        refuse = ValueError(
            f"段 '{segment}' 正在渲染中，不能删除；先切换到其他段")
        if segment == self.current_segment:
            raise refuse
        with self._switch_lock:  # 与 switch 事务串行（ENG E1 同源）
            if segment == self.current_segment:  # 锁内复检（竞态关闭）
                raise refuse
            self.segments = [s for s in self.segments if s != segment]
            self._segment_cache.pop(segment, None)
            self._resolution_cache.pop(segment, None)
            if self._prefetch.status.get('segment') == segment:
                self._prefetch.cancel()  # 在途 prefetch 已无意义
        return info

    def get_frame(self, idx: int) -> Tuple:
        """Get frame at index from current segment (batch-preloaded).

        IMPORTANT: Call ensure_loaded(idx) before this method to ensure
        the frame data is in the preloaded buffer.

        If the frame is not in the preloaded buffer (e.g., right after a
        segment switch or an out-of-window jump), it is read from disk
        individually (~25ms) — this keeps the render loop running instead
        of freezing on a synchronous batch load.

        Args:
            idx: Frame index (can be any integer, will be modulo total_frames)

        Returns:
            Tuple of (orifra, cropped, meta, facemark, mask)
                - orifra: (H, W, 3) BGR uint8 numpy array
                - cropped: [1, 3, 256, 256] float tensor in [-1, 1]
                - meta: Single metadata entry (structured array)
                - facemark: (68, 2) float32 numpy array
                - mask: (256, 256) float32 precomputed mouth mask, or None
        """
        data = self._segment_cache[self.current_segment]
        actual = idx % data['total_frames']

        # Try preloaded buffer first
        orifra = self._preloader.get_orifra(actual)
        cropped = self._preloader.get_crop(actual)
        mask = self._preloader.get_mask(actual)

        # Last resort: single-frame disk read (bounded ~25ms, keeps the
        # render loop alive while a background batch load is in flight)
        if orifra is None:
            orifra = self._read_orifra_frame(data['orifra_files'][actual])
        if cropped is None:
            img = cv2.imread(data['crop_files'][actual])
            if img is None:
                raise ValueError(f"Failed to read crop: {data['crop_files'][actual]}")
            img_rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
            tensor = torch.from_numpy(
                np.ascontiguousarray(img_rgb.astype(np.float32).transpose(2, 0, 1)) / 127.5 - 1.0
            )
            cropped = tensor.unsqueeze(0).to(self.device)

        meta = data['metadata'][actual]
        facemark = data['facemark'][actual]

        return (orifra, cropped, meta, facemark, mask)

    @property
    def total_frames(self) -> int:
        """Get total number of frames in current segment."""
        data = self._segment_cache[self.current_segment]
        return data['total_frames']

    @property
    def resolution(self) -> tuple:
        """Get resolution of current segment frames (per-segment cache)."""
        return self.segment_resolution(self.current_segment)

    def segment_resolution(self, segment: str) -> tuple:
        """Get (H, W) of a segment's frames, cached after the first read."""
        res = self._resolution_cache.get(segment)
        if res is None:
            data = self._segment_cache[segment]
            # Read one orifra frame to determine resolution
            first_frame = self._read_orifra_frame(data['orifra_files'][0])
            res = first_frame.shape[:2]
            self._resolution_cache[segment] = res
        return res

    @property
    def preload_stats(self) -> dict:
        """Diagnostics from the batch preloader."""
        return self._preloader.stats

    def get_model_paths(self) -> dict:
        """Get paths to model checkpoints."""
        if not os.path.exists(self.checkpoint_dir):
            raise FileNotFoundError(f"Checkpoint directory not found: {self.checkpoint_dir}")

        audio2lip_path = os.path.join(self.checkpoint_dir, "audio2lip.pt")
        generator_path = os.path.join(self.checkpoint_dir, "new.pt")

        if not os.path.exists(audio2lip_path):
            raise FileNotFoundError(f"Audio2Lip model not found: {audio2lip_path}")
        if not os.path.exists(generator_path):
            raise FileNotFoundError(f"Generator model not found: {generator_path}")

        return {
            'audio2lip': audio2lip_path,
            'generator': generator_path,
        }

    def close(self):
        """Release GPU memory."""
        self._prefetch.cancel()
        self._preloader.close()
        self._preloader.reset('')
        for segment_data in self._segment_cache.values():
            segment_data.clear()
        self._segment_cache.clear()
