"""Face repair module using ONNX Runtime (GPEN-BFR-256).

Repairs rendered face images to reduce artifacts and improve visual quality
before compositing onto the original frame.

Two call paths:
  repair_gpu()  — GPU-inline (preferred). The input is a PyTorch GPU tensor;
                  its data_ptr is bound directly as the ORT input (zero copy:
                  PyTorch and ORT share the same CUDA primary context), the
                  output stays in GPU memory and is wrapped back into a torch
                  tensor via dlpack. No GPU<->CPU round trips.
  repair()      — legacy CPU path (numpy in / numpy out), kept as fallback
                  and for the batch pre-processing script.

The final full frame still returns to CPU once for pyvirtualcam — that copy
is unavoidable; what this module removes is the per-frame double round trip
through numpy.
"""
import numpy as np
import cv2
import torch
import onnxruntime as ort


class FaceRepair:
    """Face repair using ONNX Runtime with GPEN-BFR-256 model.

    A one-time warmup pass eliminates the first-inference CUDA kernel
    launch spike. GPU-inline IOBinding removes per-frame CPU round trips.
    """

    def __init__(self, model_path: str = 'weights/facefix256.onnx',
                 alpha: float = 0.7, use_cuda: bool = True):
        """Initialize FaceRepair.

        Args:
            model_path: Path to the ONNX model file
            alpha: Repair strength (0.0 = no repair/keep original,
                   1.0 = full repair output)
            use_cuda: If True, prefer CUDA execution provider
        """
        providers = (['CUDAExecutionProvider', 'CPUExecutionProvider']
                     if use_cuda
                     else ['CPUExecutionProvider'])

        sess_options = ort.SessionOptions()
        sess_options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL

        self._session = ort.InferenceSession(
            model_path, sess_options=sess_options, providers=providers
        )
        self._input_name = self._session.get_inputs()[0].name
        self._output_name = self._session.get_outputs()[0].name
        self._alpha = alpha

        # GPU-inline IOBinding state (lazy setup, falls back to CPU).
        self._io = None
        self.gpu_inline_ready = False

        active_provider = self._session.get_providers()[0]
        print(f"[FaceRepair] Model loaded: {model_path}")
        print(f"[FaceRepair] Provider: {active_provider}")
        print(f"[FaceRepair] Alpha (repair strength): {alpha}")

    # ------------------------------------------------------------------
    # Warmup / GPU-inline setup
    # ------------------------------------------------------------------

    def warmup(self):
        """Burn lazy CUDA init on both call paths."""
        dummy = np.zeros((256, 256, 3), dtype=np.uint8)
        _ = self.repair(dummy)
        if torch.cuda.is_available():
            try:
                dummy_gpu = torch.zeros(3, 256, 256, dtype=torch.uint8,
                                        device='cuda')
                _ = self.repair_gpu(dummy_gpu)
                print(f"[FaceRepair] GPU-inline IOBinding: "
                      f"{'active' if self.gpu_inline_ready else 'unavailable (CPU fallback)'}")
            except Exception as e:
                print(f"[FaceRepair] GPU-inline probe skipped: {e}")
        print("[FaceRepair] Warmup complete")

    def _try_setup_gpu_inline(self):
        """Create the IOBinding object. Raises on failure."""
        if not torch.cuda.is_available():
            raise RuntimeError("CUDA not available")
        self._io = self._session.io_binding()
        self.gpu_inline_ready = True

    # ------------------------------------------------------------------
    # GPU-inline path
    # ------------------------------------------------------------------

    def repair_gpu(self, gpu_chw_bgr: torch.Tensor) -> torch.Tensor:
        """Repair a (3, 256, 256) uint8 BGR tensor without leaving the GPU.

        Args:
            gpu_chw_bgr: uint8 BGR image in CHW layout on any device.

        Returns:
            Repaired (3, 256, 256) uint8 BGR tensor. Device is GPU on the
            inline path, CPU on the fallback path — callers must treat it
            as device-agnostic.
        """
        try:
            if not self.gpu_inline_ready:
                self._try_setup_gpu_inline()
            return self._repair_via_iobinding(gpu_chw_bgr)
        except Exception as e:
            print(f"[FaceRepair] GPU inline failed ({e}); using CPU path")
            self.gpu_inline_ready = False
            frame_bgr = gpu_chw_bgr.permute(1, 2, 0).cpu().numpy()
            repaired = self.repair(frame_bgr)
            return torch.from_numpy(np.ascontiguousarray(repaired.transpose(2, 0, 1)))

    def _repair_via_iobinding(self, gpu_chw_bgr: torch.Tensor) -> torch.Tensor:
        """Zero-copy GPU inference: input AND output are torch GPU memory.

        Note: we deliberately do NOT use OrtValue/dlpack here — OrtValue's
        dlpack capsule can be consumed only once, and get_outputs() returns
        the same OrtValue every frame. Binding torch-allocated GPU buffers
        by data_ptr avoids that entirely.
        """
        # ORT runs on its own stream: make sure pending torch kernels that
        # produced gpu_chw_bgr are complete before ORT reads the memory.
        torch.cuda.synchronize()

        # uint8 BGR CHW -> fp32 RGB NCHW in [-1, 1]
        x = (gpu_chw_bgr.float() / 127.5 - 1.0).flip(0).unsqueeze(0).contiguous()
        # Output tensor owned by torch; ORT writes directly into its memory.
        out = torch.empty((1, 3, 256, 256), dtype=torch.float32, device=x.device)

        self._io.bind_input(
            self._input_name,
            'cuda', 0,
            np.float32,
            list(x.shape),
            x.data_ptr(),   # zero copy: torch GPU memory shared with ORT
        )
        self._io.bind_output(
            self._output_name,
            'cuda', 0,
            np.float32,
            list(out.shape),
            out.data_ptr(),
        )
        self._session.run_with_iobinding(self._io)

        y = out[0]  # (3,256,256) RGB fp32, torch tensor
        y = ((y * 0.5 + 0.5) * 255.0).round().clamp_(0, 255).to(torch.uint8).flip(0)  # BGR

        if self._alpha < 1.0:
            y = (y.float() * self._alpha
                 + gpu_chw_bgr.float() * (1.0 - self._alpha)).round().to(torch.uint8)

        return y.contiguous()

    # ------------------------------------------------------------------
    # Legacy CPU path
    # ------------------------------------------------------------------

    def repair(self, frame_bgr: np.ndarray) -> np.ndarray:
        """Repair a 256x256 rendered face image (CPU numpy path).

        Args:
            frame_bgr: Input frame (256, 256, 3) BGR uint8

        Returns:
            Repaired frame (256, 256, 3) BGR uint8
        """
        # BGR → RGB, normalize to [-1, 1]
        img_t = (frame_bgr[..., ::-1].astype(np.float32) / 255.0 - 0.5) / 0.5
        # HWC → NCHW
        img_t = img_t.transpose(2, 0, 1)[np.newaxis, ...]

        # Run inference
        ort_out = self._session.run(None, {self._input_name: img_t})[0]

        # NCHW → HWC, RGB → BGR, denormalize to [0, 255]
        out = (ort_out[0].transpose(1, 2, 0)[..., ::-1] * 0.5 + 0.5) * 255.0
        repaired = np.clip(out, 0, 255).astype(np.uint8)

        # Alpha blending with original
        if self._alpha < 1.0:
            repaired = cv2.addWeighted(repaired, self._alpha, frame_bgr, 1.0 - self._alpha, 0.0)

        return repaired
