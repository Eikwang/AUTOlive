"""UnityCapture synchronous sink for virtual camera output.

This module is a thin synchronous wrapper around pyvirtualcam. Pacing and
frame buffering are owned by OutputStreamer (realtime_serve/output_buffer.py);
UnityCaptureSender only knows how to send one frame at a time to the camera.

Resolution handling: frames go out at their NATIVE resolution — no pipeline
scaling. pyvirtualcam fixes frame dimensions at Camera creation, so when the
active segment's resolution changes, update_resolution() closes and re-opens
the camera at the new size. The DH-Live Unity Capture filter reads each
frame's dimensions from the shared memory (any size up to 7680x4320 is
accepted by CheckMediaType): a consumer that stays connected sees the
filter's aspect-fit letterbox, and picks up the true new resolution the next
time it (re)negotiates with the camera.
"""
import threading
import time

import numpy as np
import cv2

try:
    import pyvirtualcam
    PYVIRTUALCAM_AVAILABLE = True
except ImportError:
    PYVIRTUALCAM_AVAILABLE = False


class UnityCaptureSender:
    """Synchronous video sink for pyvirtualcam.

    Usage:
        sender = UnityCaptureSender(width=1440, height=2560, fps=25)
        sender.send(bgr_frame)                 # blocking camera send
        sender.update_resolution(1080, 1920)   # segment switch (native res)
        sender.close()
    """

    # Rate limit for the mismatch-resize warning (transitional frames).
    _RESIZE_WARN_INTERVAL_S = 10.0

    def __init__(self, width: int = 1920, height: int = 1080, fps: int = 25,
                 backend: str = 'unitycapture', cap_num: int = 0):
        """Initialize the virtual camera.

        Args:
            width: Frame width in pixels
            height: Frame height in pixels
            fps: Target frame rate (reported to the camera backend)
            backend: Virtual camera backend ('obs', 'unitycapture', or None for default)
            cap_num: Camera number (for multi-camera setups)
        """
        if not PYVIRTUALCAM_AVAILABLE:
            raise ImportError("pyvirtualcam is required. Install with: pip install pyvirtualcam")

        self.width = width
        self.height = height
        self.fps = fps
        self.backend = backend
        self.cap_num = cap_num

        # Guards virtual_cam swaps (update_resolution) against the send()
        # consumer thread — closing a camera another thread is writing to
        # would crash the native module.
        self._cam_lock = threading.Lock()
        self._last_resize_warn = 0.0
        self._active_backend = None  # backend that actually opened

        backends_to_try = [backend] if backend else ['obs', 'unitycapture', None]
        print(f"Initializing virtual camera: {width}x{height}@{fps}fps")

        self.virtual_cam = None
        for try_backend in backends_to_try:
            try:
                print(f"尝试使用后端: {try_backend if try_backend else 'default'}")
                self.virtual_cam = self._open_camera(width, height, try_backend)
                self._active_backend = try_backend

                print(f"虚拟摄像头初始化成功 (backend: {try_backend if try_backend else 'default'})")

                test_frame = np.zeros((height, width, 3), dtype=np.uint8)
                test_frame[:] = [0, 255, 0]
                cv2.putText(test_frame, "EDTalk Ready", (50, height // 2),
                            cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255), 2)
                self.virtual_cam.send(np.ascontiguousarray(test_frame))
                print("测试帧发送成功")
                break

            except Exception as e:
                print(f"后端 {try_backend if try_backend else 'default'} 初始化失败: {e}")
                if self.virtual_cam:
                    try:
                        self.virtual_cam.close()
                    except Exception:
                        pass
                    self.virtual_cam = None
                continue

        if self.virtual_cam is None:
            raise RuntimeError("所有虚拟摄像头后端都初始化失败")

    def _open_camera(self, width: int, height: int, backend=None):
        """Open one pyvirtualcam.Camera at the given size."""
        kwargs = {
            'width': width,
            'height': height,
            'fps': self.fps,
            'fmt': pyvirtualcam.PixelFormat.BGR,
        }
        if backend:
            kwargs['backend'] = backend
        return pyvirtualcam.Camera(**kwargs)

    def update_resolution(self, width: int, height: int) -> bool:
        """Re-open the virtual camera at a new resolution (native passthrough).

        Called when the active segment's native resolution changes (producer
        thread, during the switch's preroll window while the consumer is
        paused). On failure the previous resolution is restored; if that also
        fails the camera is disabled and send() reports False.

        Returns:
            True if the camera now outputs the requested resolution.
        """
        with self._cam_lock:
            if self.virtual_cam is None:
                return False
            if (width, height) == (self.width, self.height):
                return True

            old_w, old_h = self.width, self.height
            try:
                self.virtual_cam.close()
            except Exception:
                pass
            self.virtual_cam = None

            for retry_dims in ((width, height), (old_w, old_h)):
                try:
                    self.virtual_cam = self._open_camera(*retry_dims,
                                                         backend=self._active_backend)
                except Exception as e:
                    print(f"[UnityCaptureSender] 重建摄像头失败 "
                          f"({retry_dims[0]}x{retry_dims[1]}): {e}")
                    self.virtual_cam = None
                    continue
                self.width, self.height = retry_dims
                break

            if self.virtual_cam is None:
                return False
            if (self.width, self.height) == (old_w, old_h):
                print(f"[UnityCaptureSender] 分辨率切换到 {width}x{height} 失败，"
                      f"保持 {old_w}x{old_h}（帧将缩放输出）")
            else:
                print(f"[UnityCaptureSender] 摄像头分辨率切换: "
                      f"{old_w}x{old_h} -> {self.width}x{self.height}")
            return (self.width, self.height) == (width, height)

    def _warn_resized(self, w: int, h: int) -> None:
        """Rate-limited warning for frames scaled to the camera size."""
        now = time.monotonic()
        if now - self._last_resize_warn >= self._RESIZE_WARN_INTERVAL_S:
            self._last_resize_warn = now
            print(f"[UnityCaptureSender] WARN: 帧 {w}x{h} 与摄像头 "
                  f"{self.width}x{self.height} 不符，已缩放输出"
                  f"（切换过渡帧或分辨率重建失败）")

    def send(self, bgr_frame: np.ndarray) -> bool:
        """Send one frame synchronously to the virtual camera.

        Frames are sent at their native size. A size mismatch against the
        current camera means either an in-flight frame from just before a
        resolution switch or a failed re-open — the frame is scaled to fit
        so the consumer thread never breaks (rate-limited warning).

        Args:
            bgr_frame: BGR format frame (H, W, 3) uint8

        Returns:
            True on success, False on failure.
        """
        try:
            with self._cam_lock:
                if self.virtual_cam is None:
                    return False

                if bgr_frame.dtype != np.uint8:
                    bgr_frame = bgr_frame.astype(np.uint8)
                if not bgr_frame.flags['C_CONTIGUOUS']:
                    bgr_frame = np.ascontiguousarray(bgr_frame)

                h, w = bgr_frame.shape[:2]
                if w != self.width or h != self.height:
                    self._warn_resized(w, h)
                    bgr_frame = cv2.resize(bgr_frame, (self.width, self.height))

                self.virtual_cam.send(bgr_frame)
                return True

        except Exception as e:
            print(f"[UnityCaptureSender] Error sending frame: {e}")
            import traceback
            traceback.print_exc()
            return False

    def close(self):
        """Release the virtual camera."""
        with self._cam_lock:
            if self.virtual_cam is not None:
                try:
                    self.virtual_cam.close()
                except Exception as e:
                    print(f"[UnityCaptureSender] Error closing virtual camera: {e}")
                self.virtual_cam = None
