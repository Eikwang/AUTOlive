"""Main entry point for realtime streaming inference service.

Usage:
    python -m realtime_serve.main --character-dir test_data/wy \
        --target-fps 25 \
        --cap-num 0

Important semantic change:
    --target-fps now controls ONLY the final output frame rate to the
    virtual camera. The render loop produces frames as fast as the GPU
    allows and pushes them into a bounded output buffer. This decouples
    output pacing from rendering jitter.
"""
import argparse
import os
import signal
import sys
import threading
import time
import ctypes
from contextlib import asynccontextmanager

from fastapi import FastAPI
import uvicorn

from realtime_serve.config import Config
from realtime_serve.models import ModelManager
from realtime_serve.sequence_frame_pack import CharacterDataPack
from realtime_serve.render_loop import RenderLoop
from realtime_serve.unity_capture import UnityCaptureSender
from realtime_serve.audio_buffer import AudioBuffer
from realtime_serve.audio_processor import AudioProcessor
from realtime_serve.compositor import MouthCompositor
from realtime_serve.face_repair import FaceRepair
from realtime_serve.audio_player import AudioPlayer
from realtime_serve.gaze_correction import GazeCorrector
from realtime_serve.output_buffer import FrameBuffer, OutputStreamer
from realtime_serve.service import RealtimeService
from realtime_serve.api.app import create_app as create_api_app


# Global state (shared between API server and render loop)
model_manager: ModelManager = None
render_loop: RenderLoop = None
output_streamer: OutputStreamer = None
frame_buffer: FrameBuffer = None
unity_capture: UnityCaptureSender = None
audio_buffer: AudioBuffer = None
audio_processor: AudioProcessor = None
compositor: MouthCompositor = None
face_repair: FaceRepair = None
gaze_corrector = None
audio_player: AudioPlayer = None
character_data: CharacterDataPack = None
api_app: FastAPI = None


def _set_timer_resolution(enabled: bool):
    """Set Windows timer resolution to 1ms for tighter sleep precision."""
    try:
        if enabled:
            ctypes.windll.winmm.timeBeginPeriod(1)
        else:
            ctypes.windll.winmm.timeEndPeriod(1)
    except Exception as e:
        print(f"[main] Warning: could not set Windows timer resolution: {e}")


def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Realtime Streaming Inference Service for EDTalk"
    )
    parser.add_argument(
        "--character-dir",
        type=str,
        default="test_data/wy",
        help="Path to character directory containing all data (default: test_data/wy)"
    )
    parser.add_argument(
        "--target-fps",
        type=int,
        default=25,
        help="Target output frame rate to the virtual camera (default: 25). "
             "NOTE: this now paces only camera output, not rendering."
    )
    parser.add_argument(
        "--cap-num",
        type=int,
        default=0,
        help="UnityCapture camera number (default: 0)"
    )
    parser.add_argument(
        "--canvas-w", type=int, default=1280,
        help="Unified composite canvas width (2026-10-27 fork, R-ceo9; "
             "camera opens at this size for the whole session)")
    parser.add_argument(
        "--canvas-h", type=int, default=720,
        help="Unified composite canvas height (2026-10-27 fork, R-ceo9)")
    parser.add_argument(
        "--no-composite", action="store_true",
        help="Disable the unified canvas compositor (native per-segment "
             "resolution passthrough — upstream behavior)")
    parser.add_argument(
        "--composite-default-bg", type=str, default=None,
        help="Default canvas background image (optional; dark solid if absent)")
    parser.add_argument(
        "--no-unitycapture",
        action="store_true",
        help="Disable UnityCapture (for testing without virtual camera). "
             "Output pacing still runs; frames are dropped after accounting."
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=100,
        help="Frames per preload batch (default: 100, window = 2 × batch_size). "
             "100 frames = 4s of content at 25fps, which the preloader can "
             "load in ~0.5s with parallel decode. Larger batches make the "
             "preload fall behind and freeze frames at batch boundaries."
    )
    parser.add_argument(
        "--no-face-repair",
        action="store_true",
        help="Disable face repair post-processing (default: off)"
    )
    parser.add_argument(
        "--face-repair",
        choices=["on", "adaptive"],
        default=None,
        help="Face repair mode. 'on': repair every frame (~9ms/frame "
             "synchronous GPU wait). 'adaptive': repair only while the "
             "output buffer has headroom (recommended when quality matters)."
    )
    parser.add_argument(
        "--gaze-correction",
        nargs="?",
        const="on",
        default="off",
        choices=["off", "on", "adaptive"],
        help="Gaze correction mode. 'on': every frame. 'adaptive': only when "
             "the output buffer has headroom. Bare flag = on (backward "
             "compatible). Default off — output eyes are the original video's."
    )
    parser.add_argument(
        "--gaze-convergence",
        type=float,
        default=0.05,
        help="Gaze convergence angle in [-1,1] (default 0.05). Tune per "
             "character material; negative values diverge."
    )
    parser.add_argument(
        "--gaze-ckpt",
        default="realtime_serve/weights/current_model.ckpt",
        help="Path to the gaze generator checkpoint"
    )
    parser.add_argument(
        "--gaze-eye-source",
        choices=["ori", "rendered"],
        default="ori",
        help="Gaze eye pixel source (GAZE_TIMING_PLAN). 'ori': correct the "
             "pristine orifra eyes and paste AFTER face repair — eyes stay in "
             "the native quality domain (default). 'rendered': legacy Route C' "
             "behavior (pre-repair self-correction, bitwise rollback path D)."
    )
    parser.add_argument(
        "--segments",
        nargs="+",
        default=None,
        help="Segment playback schedule. Omit: first segment loops (legacy). "
             "'--segments auto': all segments in natural order. Otherwise a "
             "list of segment names played in order and looped, e.g. "
             "'--segments 1 55 开心'. Names are the actual directory names "
             "under orifra/ (see /status available_segments)."
    )
    parser.add_argument(
        "--gen-fp16",
        action="store_true",
        help="Enable Generator fp16 autocast inference. Off by default: "
             "measured slower than fp32+TF32 on RTX 4080 SUPER."
    )
    parser.add_argument(
        "--buffer-frames",
        type=int,
        default=16,
        help="Output frame buffer capacity in frames (default: 16, max: 300). "
             "Each frame is ~11MB at 1440x2560. Deep buffers absorb larger "
             "GPU jitter but increase memory and startup latency."
    )
    parser.add_argument(
        "--preroll-frames",
        type=int,
        default=8,
        help="Frames required in the buffer before camera output starts "
             "(default: 8, must be <= --buffer-frames). Larger values trade "
             "startup latency for smoother startup."
    )
    parser.add_argument(
        "--host",
        type=str,
        default="127.0.0.1",
        help="API server bind address (default: 127.0.0.1 loopback — "
             "exposing the unauthenticated-style control plane to the LAN "
             "is discouraged; pass 0.0.0.0 explicitly AND set --api-token)"
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="API server port (default: 8000)"
    )
    parser.add_argument(
        "--api-token",
        type=str,
        default=None,
        help="Require 'Authorization: Bearer <token>' on every HTTP route "
             "(recommended when binding beyond loopback). In-process "
             "facade callers need no token."
    )
    return parser.parse_args()


def _natural_sorted(names):
    """Natural sort: '6' before '11' (digit-runs compared numerically)."""
    import re

    def key(s):
        return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", s)]
    return sorted(names, key=key)


def _resolve_segment_sequence(spec, character_data):
    """Resolve and VALIDATE the segment schedule (autoplan D2/S4).

    Must run AFTER CharacterDataPack construction (segment whitelist exists
    only then) and BEFORE model loading (fail-fast stays fail-fast).

    Returns (sequence_or_None, mode_str).
    """
    if not spec:
        return None, 'off'
    mode, items = spec
    if mode == 'auto':
        sequence = _natural_sorted(character_data.segments)
    else:
        bad = [s for s in items if s not in character_data.segments]
        if bad:
            raise ValueError(
                f"指定的段不存在: {bad}；可用段: {character_data.segments}"
                f"（段名区分大小写，以 orifra/ 下目录实际名称为准；"
                f"可用 /status available_segments 查看）")
        sequence = list(items)

    # Empty/unreadable segments are fatal. Mixed resolutions are an INFO +
    # continue: frames go out at each segment's NATIVE resolution — the
    # sender re-opens the camera at the segment size (no scaling, no stretch).
    import cv2
    base_res = None
    for seg in sequence:
        seg_dir = os.path.join(character_data.orifra_base, seg)
        files = character_data._build_file_list(seg_dir)
        if not files:
            raise ValueError(f"段 '{seg}' 的 orifra 目录为空: {seg_dir}——"
                             f"请重新预处理该段或从序列中移除")
        img = cv2.imread(files[0])
        if img is None:
            raise ValueError(f"段 '{seg}' 首帧无法读取: {files[0]}")
        if base_res is None:
            base_res = img.shape[:2]  # schedule head defines the startup camera res
        elif img.shape[:2] != base_res:
            print(f"[INFO] 段 '{seg}' 分辨率 {img.shape[1]}x{img.shape[0]} 与首段 "
                  f"{base_res[1]}x{base_res[0]} 不同——将按各段原生分辨率直出，"
                  f"摄像头输出分辨率随段切换自动调整（消费端重连后按实际分辨率协商）")
    return sequence, mode


def setup_service(config: Config, no_unitycapture: bool = False,
                  segment_sequence_spec=None):
    """Initialize all service components."""
    print("=" * 60)
    print("EDTalk Realtime Streaming Inference Service")
    print("=" * 60)

    memory_mb = config.buffer_frames * 11  # rough per-frame BGR bytes / 1MB
    preroll_s = config.preroll_frames / max(1, config.target_fps)
    print(f"\n[Config] target_fps={config.target_fps} output pacing only")
    print(f"[Config] buffer_frames={config.buffer_frames} (~{memory_mb}MB)")
    print(f"[Config] preroll_frames={config.preroll_frames} (~{preroll_s:.2f}s startup delay)")
    print(f"[Config] face_repair={config.face_repair_mode}  "
          f"gaze={config.gaze_mode} (convergence={config.gaze_convergence})")

    # 1. Load character data
    print(f"\n[1/7] Loading character data: {config.character_dir}...")
    character_data = CharacterDataPack(
        config.character_dir,
        batch_size=config.batch_size
    )

    # 1b. Segment schedule validation (fail-fast BEFORE the slow model load)
    resolved = _resolve_segment_sequence(segment_sequence_spec,
                                         character_data)
    scheduler_sequence, scheduler_mode = (resolved if resolved else (None, 'off'))
    if scheduler_sequence:
        print(f"  -> Segment schedule ({scheduler_mode}): {scheduler_sequence}")

    model_paths = character_data.get_model_paths()
    audio2lip_path = model_paths['audio2lip']
    generator_path = model_paths['generator']

    # 2. Load models
    print("\n[2/7] Loading models...")
    model_manager = ModelManager(
        generator_path,
        audio2lip_path,
        device='cuda',
        use_fp16=config.gen_fp16
    )
    model_manager.load()

    # 3. Initialize audio buffer and processor
    print("\n[3/7] Initializing audio processing...")
    audio_buffer = AudioBuffer(max_buffer_seconds=0)
    audio_processor = AudioProcessor()

    # 4. Initialize face repair (conditional)
    face_repair = None
    if config.face_repair_mode in ('on', 'adaptive'):
        print(f"\n[4/7] Initializing face repair (mode: {config.face_repair_mode})...")
        face_repair = FaceRepair(model_path=config.face_repair_weights,
                                 alpha=0.7, use_cuda=True)
        face_repair.warmup()
    else:
        print("\n[4/7] Face repair disabled")

    # 5. Initialize compositor (+ eye paste-back regions when gaze enabled)
    print("\n[5/7] Initializing mouth compositor...")
    compositor = MouthCompositor(blur_px=7, landmark_smooth=0.3)
    character_data.set_compositor(compositor)

    # 5b. Gaze corrector (Route C': corrects the rendered output AFTER the
    # generator — input-side corrections are erased by the redraw. The
    # compositor mask gains eye regions so corrected eyes reach the frame.)
    gaze_corrector = None
    if config.gaze_mode != 'off':
        if not os.path.exists(config.gaze_ckpt):
            raise FileNotFoundError(
                f"gaze checkpoint not found: {config.gaze_ckpt} — required by "
                f"--gaze-correction {config.gaze_mode}. Check the path or run "
                f"with the default --gaze-correction off.")
        print(f"[5b] Initializing gaze corrector (mode: {config.gaze_mode}, "
              f"convergence: {config.gaze_convergence}, "
              f"eye_source: {config.gaze_eye_source})...")
        gaze_corrector = GazeCorrector(config.gaze_ckpt,
                                       convergence_h=config.gaze_convergence,
                                       eye_source=config.gaze_eye_source)
        captured = gaze_corrector.try_capture_graph()
        print(f"[5b] gaze CUDA graph: {'captured' if captured else 'eager fallback'}")
        character_data.set_eyes_enabled(True)

    # 6. Initialize output buffer and streamer
    print("\n[6/7] Initializing output buffer and streamer...")
    frame_buffer = FrameBuffer(
        capacity=config.buffer_frames,
        reserve=min(5, config.buffer_frames)
    )

    # 7. Initialize UnityCapture sender (optional)
    print("\n[7/7] Initializing UnityCapture sender...")
    unity_capture = None
    sender_fn = None
    if not no_unitycapture:
        try:
            # 统一画布政策（2026-10-27 画面输出分叉，R-ceo9）：合成层启用时
            # 摄像头按会话画布初始化（出帧恒画布尺寸→逐段重建退役，抖动源消灭）；
            # 合成层禁用时保持上游原语义（按首段分辨率）。
            if config.composite_enabled:
                cam_w, cam_h = config.canvas_w, config.canvas_h
                print(f"Composite canvas: {cam_w}x{cam_h} -> Camera (unified, "
                      f"per-segment resolution rebuild retired)")
            else:
                resolution = character_data.resolution
                cam_h, cam_w = resolution
                print(f"Character resolution: {resolution} -> Camera: {cam_w}x{cam_h}")
            unity_capture = UnityCaptureSender(
                width=cam_w,
                height=cam_h,
                fps=config.target_fps,
                backend='unitycapture'
            )
            sender_fn = unity_capture.send
        except Exception as e:
            print(f"Warning: Could not initialize UnityCapture: {e}")
            print("Service will run without virtual camera output.")
            print("Use --no-unitycapture to suppress this warning.")
    else:
        print("UnityCapture disabled by --no-unitycapture flag.")

    # Initialize audio player
    print("\nInitializing audio player...")
    audio_player = AudioPlayer(sample_rate=16000, enabled=True)

    # Create render loop
    print("\nCreating render loop...")
    render_loop = RenderLoop(target_fps=config.target_fps)
    render_loop.set_character_data(character_data)

    # Create output streamer (frame-locked audio sink wired by the facade)
    output_streamer = OutputStreamer(
        frame_buffer=frame_buffer,
        target_fps=config.target_fps,
        sender=sender_fn,
        preroll_frames=config.preroll_frames
    )

    # 统一画布合成层（2026-10-27 画面输出分叉）：两出帧口统一改道
    canvas_compositor = None
    if config.composite_enabled:
        try:
            from realtime_serve.canvas_compositor import CanvasCompositor
            canvas_compositor = CanvasCompositor(
                canvas_w=config.canvas_w, canvas_h=config.canvas_h,
                character_dir=config.character_dir,
                default_bg_path=config.composite_default_bg)
            print(f"Canvas compositor: {config.canvas_w}x{config.canvas_h} enabled "
                  f"(character_dir={config.character_dir})")
        except Exception as e:
            print(f"Warning: Canvas compositor init failed ({e}) — 直出降级")
            canvas_compositor = None

    # Inject components into render loop
    render_loop.model_manager = model_manager
    render_loop.frame_buffer = frame_buffer
    render_loop.output_streamer = output_streamer
    render_loop.audio_buffer = audio_buffer
    render_loop.audio_processor = audio_processor
    render_loop.compositor = compositor
    render_loop.canvas_compositor = canvas_compositor
    render_loop.face_repair = face_repair
    render_loop.face_repair_mode = config.face_repair_mode
    render_loop.gaze_corrector = gaze_corrector
    render_loop.gaze_mode = config.gaze_mode
    render_loop.audio_player = audio_player
    # Native-resolution passthrough: on each segment switch the render loop
    # re-opens the camera at the new segment's native size (no scaling).
    if unity_capture is not None:
        render_loop.resolution_change_cb = unity_capture.update_resolution

    # Segment schedule installed before start(): set_segment_sequence aligns
    # playback to sequence[0] (switches off the character's default segment
    # when the schedule starts elsewhere, e.g. --segments 22 55).
    if scheduler_sequence:
        render_loop.set_segment_sequence(scheduler_sequence, mode=scheduler_mode)

    print("\nService initialized successfully!")
    print("-" * 60)

    return (model_manager, render_loop, output_streamer, frame_buffer,
            unity_capture, audio_buffer, audio_processor, compositor,
            face_repair, audio_player, character_data, gaze_corrector)


def main():
    """Main entry point."""
    args = parse_args()
    # Face repair mode resolution: --no-face-repair wins (back-compat alias
    # for off); otherwise --face-repair on|adaptive; otherwise off.
    if args.no_face_repair:
        repair_mode = 'off'
    elif args.face_repair is not None:
        repair_mode = args.face_repair
    else:
        repair_mode = 'off'

    config = Config(
        character_dir=args.character_dir,
        target_fps=args.target_fps,
        cap_num=args.cap_num,
        batch_size=args.batch_size,
        face_repair_enabled=(repair_mode != 'off'),
        face_repair_mode=repair_mode,
        gaze_mode=args.gaze_correction,
        gaze_convergence=args.gaze_convergence,
        gaze_ckpt=args.gaze_ckpt,
        gaze_eye_source=args.gaze_eye_source,
        buffer_frames=args.buffer_frames,
        preroll_frames=args.preroll_frames,
        gen_fp16=args.gen_fp16,
        unitycapture_enabled=(not args.no_unitycapture),
        api_token=args.api_token,
        canvas_w=args.canvas_w,
        canvas_h=args.canvas_h,
        composite_enabled=(not args.no_composite),
        composite_default_bg=args.composite_default_bg
    )

    # Set Windows timer resolution early.
    _set_timer_resolution(True)

    try:
        # Segment schedule spec parsing (syntax layer only — existence is
        # validated inside setup_service after the DataPack loads, ENG D2).
        seg_spec = None
        if args.segments:
            items = []
            for part in args.segments:
                items.extend(p.strip() for p in part.split(',') if p.strip())
            if len(items) == 1 and items[0] == 'auto':
                seg_spec = ('auto', None)
            elif items:
                seg_spec = ('list', items)
            # items empty (whitespace/commas only) → legacy default

        # Control-plane facade owns assembly, lifecycle order (M12), the
        # audio-start callback and every control method (M1 contract).
        service = RealtimeService(config, segment_sequence_spec=seg_spec)
    except Exception as e:
        print(f"\nError initializing service: {e}")
        _set_timer_resolution(False)
        sys.exit(1)

    # Create API app around the facade (constructor injection, E5)
    api_app = create_api_app(service, api_token=config.api_token)

    # Facade start: output streamer first (pacing clock), then render loop.
    service.start()

    # Setup signal handler for graceful shutdown
    def signal_handler(signum, frame):
        service.stop()
        _set_timer_resolution(False)
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)

    # Start API server (blocks until interrupted)
    print(f"\nAPI server running at http://{args.host}:{args.port}")
    if args.api_token:
        print("Auth: Bearer token required (--api-token)")
    print("Endpoints:")
    print(f"  POST /audio/push        - Push audio chunk (streaming TTS)")
    print(f"  POST /audio/push_full   - Push full audio segment (file-based)")
    print(f"  POST /segment/switch    - Switch segment (at=immediate|segment_end)")
    print(f"  POST /segment/schedule  - Replace playback schedule (align=false default)")
    print(f"  POST /gaze/params       - Hot-update gaze convergence/mode/smooth_alpha/eye_source")
    print(f"  POST /repair/params     - Hot-switch face repair mode")
    print(f"  GET  /status            - Get service status")
    print("\nPress Ctrl+C to stop.\n")

    try:
        uvicorn.run(api_app, host=args.host, port=args.port)
    finally:
        _set_timer_resolution(False)


if __name__ == "__main__":
    main()
