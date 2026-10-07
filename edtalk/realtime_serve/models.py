"""Model manager for loading and inference with Audio2Lip + Generator models."""
import time

import torch
import torch.nn as nn
from networks.audio_encoder import Audio2Lip
from networks.generator_lip_pose import Generator


def conv_feat(features, k_size=3, weight=None, sigma=1.0):
    """Apply 1D convolution smoothing to features.

    This is a re-implementation of the conv_feat function from demo scripts.
    """
    c = features.shape[1]
    if weight is None:
        pad = k_size // 2
        k = torch.zeros(k_size)
        for x in range(-pad, k_size - pad):
            k[x + pad] = torch.exp(torch.tensor(-x**2 / (2 * (sigma ** 2))))
        k = k / k.sum()
    else:
        k_size = len(weight)
        k = torch.tensor(weight)
        pad = k_size // 2

    k = k.to(features.device).float().unsqueeze(0).unsqueeze(0)
    k = k.repeat(c, 1, 1)
    features = features.unsqueeze(0).permute(0, 2, 1)
    features = torch.nn.functional.conv1d(features, k, padding=pad, groups=c)
    features = features.permute(0, 2, 1).squeeze(0)

    return features


class ModelManager:
    """Manages Audio2Lip and Generator model loading and inference.

    Reference: demo_change_a_video_lip.py:160-176
    """

    def __init__(self, model_path: str, audio2lip_model_path: str, device: str = 'cuda',
                 use_fp16: bool = False):
        """Initialize ModelManager.

        Args:
            model_path: Path to Generator model weights
            audio2lip_model_path: Path to Audio2Lip model weights
            device: Device to load models on ('cuda' or 'cpu')
            use_fp16: Run Generator inference under fp16 autocast (CUDA only).
                      Default False: measured SLOWER than fp32+TF32 on
                      RTX 4080 SUPER (see config.gen_fp16 note).
        """
        self.model_path = model_path
        self.audio2lip_model_path = audio2lip_model_path
        self.device = device
        self.use_fp16 = use_fp16
        self.audio2lip = None
        self.gen = None

        # CUDA Graph state for the Generator (captured in load()).
        self._gen_graph = None
        self._gen_static_img = None
        self._gen_static_lip = None
        self._gen_static_pose = None
        self._gen_static_out = None

    @property
    def _fp16_active(self) -> bool:
        """Whether fp16 autocast is actually usable on this device."""
        return (self.use_fp16 and self.device == 'cuda'
                and torch.cuda.is_available())

    # Fixed batch size for ALL Audio2Lip inference calls (padded to this
    # size by callers). A single fixed shape keeps cudnn on one kernel
    # selection path — historically this also avoided a ~6s-per-shape
    # autotune trap when cudnn.benchmark was enabled (now disabled).
    # Must match RenderLoop's chunk padding (LIP_CHUNK_FRAMES + halo + 1).
    LIP_INFER_BATCH = 128

    def load(self):
        """Load both models onto the specified device.

        Raises:
            RuntimeError: If CUDA is unavailable or model loading fails
        """
        if self.device == 'cuda' and not torch.cuda.is_available():
            raise RuntimeError("CUDA is not available. Please use CPU mode or check your GPU setup.")

        # NOTE: cudnn.benchmark is intentionally DISABLED. On this machine
        # the autotune pass costs 6-19s PER INPUT SHAPE * PER THREAD
        # (measured: a worker thread re-pays the full cost even after a
        # main-thread warmup). The steady-state gain is only ~2ms/frame,
        # so variable shapes + threads would cause multi-second stalls.
        # TF32 has no autotune cost and is kept enabled.
        torch.backends.cudnn.benchmark = False
        torch.backends.cudnn.allow_tf32 = True
        try:
            torch.backends.cuda.matmul.allow_tf32 = True
        except Exception:
            pass

        print('==> loading Audio2Lip model')
        self.audio2lip = Audio2Lip().to(self.device)
        try:
            audio2lip_weight = torch.load(
                self.audio2lip_model_path,
                map_location=lambda storage, loc: storage,
                weights_only=False
            )['audio2lip']
            self.audio2lip.load_state_dict(audio2lip_weight)
            self.audio2lip.eval()
        except Exception as e:
            print(f"Error loading Audio2Lip model: {e}")
            raise

        print('==> loading Generator model')
        self.gen = Generator().to(self.device)
        try:
            gen_weight = torch.load(
                self.model_path,
                map_location=lambda storage, loc: storage,
                weights_only=False
            )['gen']
            self.gen.load_state_dict(gen_weight)
            self.gen.eval()
        except Exception as e:
            print(f"Error loading Generator model: {e}")
            raise

        self._warmup()

        if self.device == 'cuda':
            self._capture_generator_graph()

        print('==> models loaded successfully')

    def _capture_generator_graph(self):
        """Capture the Generator forward pass as a CUDA Graph.

        The Generator is a fixed-shape conv net invoked once per frame. In
        eager mode its ~100+ layers cost one kernel launch each, and the GPU
        idles between launches — the classic "GPU 25% busy but frames still
        slow" signature. Graph replay issues the entire forward as a single
        launch, collapsing that overhead.

        Falls back to eager inference if capture is unsupported.
        """
        try:
            self._gen_static_img = torch.zeros(1, 3, 256, 256, device=self.device)
            self._gen_static_lip = torch.zeros(1, 20, device=self.device)
            self._gen_static_pose = torch.zeros(1, 3, 256, 256, device=self.device)

            autocast = (torch.autocast(device_type='cuda', dtype=torch.float16)
                        if self._fp16_active else None)

            # Warm up on a side stream (required before capture).
            s = torch.cuda.Stream()
            s.wait_stream(torch.cuda.current_stream())
            with torch.cuda.stream(s), torch.inference_mode():
                for _ in range(3):
                    if autocast is not None:
                        with autocast:
                            _ = self.gen.test_from_audio_pose_image(
                                self._gen_static_img, self._gen_static_lip,
                                self._gen_static_pose)
                    else:
                        _ = self.gen.test_from_audio_pose_image(
                            self._gen_static_img, self._gen_static_lip,
                            self._gen_static_pose)
            torch.cuda.current_stream().wait_stream(s)
            torch.cuda.synchronize()

            g = torch.cuda.CUDAGraph()
            with torch.cuda.graph(g), torch.inference_mode():
                if autocast is not None:
                    with autocast:
                        out = self.gen.test_from_audio_pose_image(
                            self._gen_static_img, self._gen_static_lip,
                            self._gen_static_pose)
                else:
                    out = self.gen.test_from_audio_pose_image(
                        self._gen_static_img, self._gen_static_lip,
                        self._gen_static_pose)
            self._gen_graph = g
            self._gen_static_out = out
            # Sanity check: the graph must produce a real tensor of the
            # expected shape (guards against mocked/dry-run models).
            if not isinstance(out, torch.Tensor) or out.shape != (1, 3, 256, 256):
                raise RuntimeError(f"unexpected graph output type: {type(out)}")
            print('==> Generator CUDA Graph captured '
                  f'({"fp16" if self._fp16_active else "fp32"})')
        except Exception as e:
            self._gen_graph = None
            print(f'==> Generator CUDA Graph capture failed ({e}); using eager path')

    def _warmup(self):
        """Run one dummy inference per model to burn lazy CUDA init.

        Covers cudnn/cublas module loading and handle creation (~0.5-1s
        one-time) so the render loop's first frame and the first audio
        push don't pay it mid-stream.
        """
        if self.device != 'cuda':
            return
        try:
            t0 = time.perf_counter()
            with torch.inference_mode():
                dummy_mel = torch.zeros(self.LIP_INFER_BATCH, 1, 80, 16,
                                        device=self.device)
                _ = self.audio2lip(dummy_mel, 1, self.LIP_INFER_BATCH)
                dummy_img = torch.zeros(1, 3, 256, 256, device=self.device)
                dummy_lip = torch.zeros(1, 20, device=self.device)
                if self._fp16_active:
                    with torch.autocast(device_type='cuda', dtype=torch.float16):
                        _ = self.gen.test_from_audio_pose_image(
                            dummy_img, dummy_lip, dummy_img)
                    torch.cuda.synchronize()
                    # Second pass in fp32 path so a --no-gen-fp16 flip
                    # mid-session never pays kernel selection either.
                    _ = self.gen.test_from_audio_pose_image(
                        dummy_img, dummy_lip, dummy_img)
                else:
                    _ = self.gen.test_from_audio_pose_image(
                        dummy_img, dummy_lip, dummy_img)
                if torch.cuda.is_available():
                    torch.cuda.synchronize()
            print(f'==> warmup done in {time.perf_counter()-t0:.1f}s'
                  + (' (fp16)' if self._fp16_active else ''))
        except Exception as e:
            print(f'==> warmup skipped: {e}')

    def infer_lip(self, mel_input: torch.Tensor, bs: int, T: int) -> torch.Tensor:
        """Run Audio2Lip inference to get lip coefficients.

        Args:
            mel_input: Mel spectrogram input [bs*T, 1, 80, 16]
            bs: Batch size
            T: Number of time steps

        Returns:
            Lip coefficients [T, 20]
        """
        with torch.inference_mode():
            mel_input = mel_input.to(self.device)
            lip = self.audio2lip(mel_input, bs, T)[0]
            lip = conv_feat(lip, k_size=3, sigma=1)
        return lip

    def infer_frame(self, img_source: torch.Tensor, alpha_D_lip: torch.Tensor,
                    pose_img_drive: torch.Tensor, h_start=None) -> torch.Tensor:
        """Run Generator inference to get rendered frame.

        Args:
            img_source: Source image [1, 3, 256, 256]
            alpha_D_lip: Lip coefficients [1, 20]
            pose_img_drive: Pose driving image [1, 3, 256, 256]
            h_start: Optional starting position

        Returns:
            Rendered frame [1, 3, 256, 256]
        """
        with torch.inference_mode():
            img_source = img_source.to(self.device)
            alpha_D_lip = alpha_D_lip.to(self.device)
            pose_img_drive = pose_img_drive.to(self.device)

            # CUDA Graph replay path: copy inputs into the static buffers
            # captured at graph time, replay the whole forward as one launch.
            # The returned static output must be consumed before the next
            # replay — the render loop does so within the same frame
            # iteration (post-process + repair + download all synchronize).
            if (self._gen_graph is not None and h_start is None
                    and img_source.shape == self._gen_static_img.shape
                    and pose_img_drive.shape == self._gen_static_pose.shape):
                self._gen_static_img.copy_(img_source, non_blocking=True)
                self._gen_static_lip.copy_(alpha_D_lip, non_blocking=True)
                self._gen_static_pose.copy_(pose_img_drive, non_blocking=True)
                self._gen_graph.replay()
                return self._gen_static_out

            if self._fp16_active:
                # fp16 autocast: conv/matmul kernels run in half precision,
                # normalization/softmax stay fp32 (autocast handles casting).
                # Output cast back to fp32 for the CPU/compositor pipeline.
                with torch.autocast(device_type='cuda', dtype=torch.float16):
                    out = self.gen.test_from_audio_pose_image(
                        img_source, alpha_D_lip, pose_img_drive, h_start=h_start
                    )
                return out.float()
            return self.gen.test_from_audio_pose_image(
                img_source, alpha_D_lip, pose_img_drive, h_start=h_start
            )
