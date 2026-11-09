# 必须最先设置环境变量，禁用 torch.compile
import os
os.environ["TORCH_COMPILE"] = "0"
os.environ["PYTORCH_JIT"] = "0"

import sys
import time
import cv2
import numpy as np
from argparse import ArgumentParser
from tqdm import tqdm
import warnings
warnings.filterwarnings("ignore")

import torch
import multiprocessing
import subprocess
import tempfile
import shutil
from functools import partial

# 使用独立版 FFMPEG（更稳定，适合大规模数据处理）
# 优先使用 runtime312/ffmpeg/bin/ffmpeg.exe
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))
FFMPEG_PATH = os.path.join(PROJECT_ROOT, 'runtime312', 'ffmpeg', 'bin', 'ffmpeg.exe')

if not os.path.exists(FFMPEG_PATH):
    # 回退到 imageio-ffmpeg 或系统 ffmpeg
    try:
        import imageio_ffmpeg
        FFMPEG_PATH = imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        FFMPEG_PATH = 'ffmpeg'

print(f"[INFO] Using FFMPEG: {FFMPEG_PATH}")

# ============================================================
# S3FD 人脸检测器 (参考 crop_head_video.py 的高效实现)
# ============================================================
def _get_face_detector(device='cuda', conf_th=0.5):
    """加载 S3FD 人脸检测器"""
    try:
        import torch
        # 当前文件: data_preprocess/data_preprocess_for_train/crop_video_HDTF.py
        # 项目根目录: 向上3级
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        sys.path.insert(0, project_root)
        from ckpts.s3fd import S3FD
    except Exception as e:
        raise RuntimeError(f'Failed to import S3FD: {e}')

    if device == 'cuda' and not torch.cuda.is_available():
        device = 'cpu'

    detector = S3FD(device=device)
    return ('s3fd', detector, device, float(conf_th))


def detect_face(detector_info, frame, min_face_size=30, max_detect_size=640):
    """检测单帧中的人脸，返回最大置信度的一个"""
    det_type = detector_info[0]

    if det_type == 's3fd':
        _, detector, _, conf_th = detector_info

        h, w = frame.shape[:2]
        scale = 1.0
        if max_detect_size > 0 and min(h, w) > max_detect_size:
            scale = max_detect_size / min(h, w)
            small_frame = cv2.resize(frame, None, fx=scale, fy=scale, interpolation=cv2.INTER_LINEAR)
        else:
            small_frame = frame

        bboxes = detector.detect_faces(small_frame, conf_th=conf_th)
        if len(bboxes) == 0:
            return None

        # 选面积最大的
        areas = (bboxes[:, 2] - bboxes[:, 0]) * (bboxes[:, 3] - bboxes[:, 1])
        best_idx = int(np.argmax(areas))
        x1, y1, x2, y2, score = bboxes[best_idx]

        if scale != 1.0:
            x1, y1, x2, y2 = x1 / scale, y1 / scale, x2 / scale, y2 / scale

        w_face = float(x2 - x1)
        h_face = float(y2 - y1)
        if max(w_face, h_face) < min_face_size:
            return None
        return (float(x1), float(y1), w_face, h_face, float(score))

    return None


# ============================================================
# 头部跟踪器 (从 crop_head_video.py 移植，EMA 平滑跟踪)
# 核心原则：
# 1. 裁切框中心 = 鼻尖位置（面部几何中心近似）
# 2. 裁切框大小 = 面部尺寸 × pad_ratio（线性，无复杂平滑）
# 3. 无论人物如何移动/转头，始终保持面部在画面中心
# ============================================================

class HeadTracker:
    """极简跟踪器：鼻尖居中 + EMA 平滑 + 线性 pad_ratio"""

    def __init__(self, pan_alpha=0.3, zoom_alpha=0.3, pad_ratio_range=(1.05, 1.3)):
        self.pan_alpha = pan_alpha
        self.zoom_alpha = zoom_alpha
        self.pad_ratio = sum(pad_ratio_range) / 2.0

        self.crop_cx = None
        self.crop_cy = None
        self.crop_size = None
        self.first_detection = False

    def update(self, face_bbox=None, frame_w=1920, frame_h=1080):
        fh, fw = float(frame_h), float(frame_w)

        # 未检测到面部：保持上一帧状态
        if face_bbox is None:
            default_size = min(fh, fw)
            crop_size = self.crop_size if self.crop_size else default_size
            crop_cx = self.crop_cx if self.crop_cx is not None else fw / 2.0
            crop_cy = self.crop_cy if self.crop_cy is not None else fh / 2.0
            return crop_cx - crop_size / 2.0, crop_cy - crop_size / 2.0, crop_size

        x1, y1, w, h, score = face_bbox

        # 鼻尖近似位置：面部中心偏上 1/6
        face_cx = x1 + w / 2.0
        face_cy = y1 + h / 2.0
        nose_cy = face_cy - h / 6.0

        target_cx = face_cx
        target_cy = nose_cy + 60.0
        target_size = max(w, h) * self.pad_ratio

        if not self.first_detection:
            self.crop_cx = target_cx
            self.crop_cy = target_cy
            self.crop_size = target_size
            self.first_detection = True
        else:
            # EMA 平滑
            self.crop_cx = self.crop_cx + self.pan_alpha * (target_cx - self.crop_cx)
            self.crop_cy = self.crop_cy + self.pan_alpha * (target_cy - self.crop_cy)
            self.crop_size = self.crop_size + self.zoom_alpha * (target_size - self.crop_size)

        # 边界约束
        crop_size = self.crop_size
        self.crop_cx = max(crop_size / 2.0, min(self.crop_cx, fw - crop_size / 2.0))
        self.crop_cy = max(crop_size / 2.0, min(self.crop_cy, fh - crop_size / 2.0))
        crop_size = min(crop_size, min(fh, fw))

        crop_x = self.crop_cx - crop_size / 2.0
        crop_y = self.crop_cy - crop_size / 2.0

        return crop_x, crop_y, crop_size


class DetectTimeoutGuard:
    """单帧人脸检测超时防护（Windows WDDM 下 S3FD 偶发长时间卡顿）

    检测调用是同步的，无法在进程内中断，因此策略是：单帧检测耗时超过
    timeout_s 时记录超时，并在随后 cooldown_frames 帧内跳过检测
    （HeadTracker 保持上一帧状态），避免卡顿恢复后立刻再次触发慢检测。
    若检测真正死锁（永不返回），进程内无法自救，需靠 run_preprocess.py
    的进程级重试/断点续跑，或手动终止进程。
    """

    def __init__(self, timeout_s, cooldown_frames=30):
        self.timeout_s = float(timeout_s)
        self.cooldown_frames = int(cooldown_frames)
        self._started = None
        self._overrun = False
        self._skip_until = -1

    def begin(self):
        """检测调用开始前调用"""
        self._started = time.monotonic()
        self._overrun = False

    def end(self):
        """检测调用返回后调用；若耗时超过阈值则标记超时"""
        if self._started is not None:
            elapsed = time.monotonic() - self._started
            self._started = None
            if elapsed >= self.timeout_s:
                self._overrun = True

    def was_overrun(self):
        return self._overrun

    def cooldown(self, frame_idx):
        """让后续帧进入冷却期：跳过检测，跟踪器保持上一帧状态"""
        self._skip_until = frame_idx + self.cooldown_frames

    def should_skip(self, frame_idx):
        return frame_idx < self._skip_until


def process_video(args, detector_info):
    """处理单个视频：cv2 读取 → 间隔检测 → HeadTracker 逐帧跟踪 → 逐帧裁切写入

    核心改动：不再用 ffmpeg 静态裁切整个片段，而是逐帧根据 Tracker 输出裁切，
    确保面部移动时裁切框跟随移动。
    """
    cap = cv2.VideoCapture(args.inp)
    if not cap.isOpened():
        print(f"[ERROR] Cannot open video: {args.inp}")
        return False

    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = cap.get(cv2.CAP_PROP_FPS)
    frame_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    frame_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    out_w, out_h = args.image_shape
    fourcc = cv2.VideoWriter_fourcc(*'mp4v')
    writer = None  # 延迟初始化，等首帧有效再创建

    tracker = HeadTracker(
        pan_alpha=0.3,
        zoom_alpha=0.3,
        pad_ratio_range=(1.05, 1.3),
    )

    detect_interval = getattr(args, 'detect_interval', 3)
    min_face_size = getattr(args, 'min_face_size', 30)
    detect_timeout_s = float(getattr(args, 'detect_timeout_s', 0.0) or 0.0)
    timeout_guard = DetectTimeoutGuard(detect_timeout_s) if detect_timeout_s > 0 else None

    valid_frame_count = 0
    consecutive_misses = 0
    max_consecutive_misses = 15
    has_valid_output = False

    # disable=False 强制输出（2026-11-09 用户实报）：auto 语义在非 tty
    # （webui 管道）下自动静默=预处理全程零进度可见，用户误判卡死两度中止；
    # 训练管线 _run_step 为按块消费（\r 算行分隔），进度条实时进日志区。
    for frame_idx in tqdm(range(total_frames), desc="Processing frames", disable=False):
        ret, frame = cap.read()
        if not ret:
            break

        # 间隔检测人脸（超时防护：冷却期内跳过检测，跟踪器保持上一帧状态）
        face_result = None
        if frame_idx % detect_interval == 0:
            if timeout_guard is not None and timeout_guard.should_skip(frame_idx):
                pass
            else:
                if timeout_guard is not None:
                    timeout_guard.begin()
                face_result = detect_face(detector_info, frame,
                                          min_face_size=min_face_size, max_detect_size=640)
                if timeout_guard is not None:
                    timeout_guard.end()
                    if timeout_guard.was_overrun():
                        print(f"[WARN] 帧 {frame_idx} 人脸检测耗时 >= {timeout_guard.timeout_s:.1f}s, "
                              f"接下来 {timeout_guard.cooldown_frames} 帧跳过检测")
                        timeout_guard.cooldown(frame_idx)

        # HeadTracker 逐帧更新，返回本帧的裁切坐标（EMA 平滑）
        crop_x, crop_y, crop_size = tracker.update(
            face_bbox=face_result,
            frame_w=frame_w,
            frame_h=frame_h,
        )

        # 判断本帧是否有效
        is_valid = tracker.first_detection and crop_size > 0
        if face_result is not None:
            consecutive_misses = 0
        else:
            consecutive_misses += 1

        # 连续丢失过多帧时跳过写入（避免空白/偏移帧）
        if not is_valid or consecutive_misses > max_consecutive_misses:
            continue

        # ===== 边界安全约束 =====
        crop_x = max(0, crop_x)
        crop_y = max(0, crop_y)
        crop_size = min(crop_size, min(frame_w, frame_h))
        crop_size = max(crop_size, 50)

        x1 = int(crop_x)
        y1 = int(crop_y)
        x2 = min(int(crop_x + crop_size), frame_w)
        y2 = min(int(crop_y + crop_size), frame_h)

        # 裁切
        cropped = frame[y1:y2, x1:x2]
        if cropped.size == 0:
            continue

        # 缩放到目标尺寸
        resized = cv2.resize(cropped, (out_w, out_h), interpolation=cv2.INTER_AREA)

        # 延迟创建 VideoWriter（等首帧有效后再初始化）
        if writer is None:
            writer = cv2.VideoWriter(args.outp, fourcc, fps, (out_w, out_h))

        writer.write(resized)
        valid_frame_count += 1
        has_valid_output = True

    cap.release()
    if writer is not None:
        writer.release()

    if not has_valid_output or valid_frame_count < args.min_frames:
        # 有效帧不足，删除输出文件
        if os.path.exists(args.outp):
            os.remove(args.outp)
        print(f"[WARN] Skipped: only {valid_frame_count} valid frames (need >= {args.min_frames})")
        return False

    # ===== 合并原始音频到输出视频 =====
    _merge_audio_to_video(args.inp, args.outp)

    print(f"[OK] {valid_frame_count}/{total_frames} frames written -> {args.outp}")
    return True


def _merge_audio_to_video(source_video, output_video, temp_suffix='.tmp.mp4'):
    """用 ffmpeg 将原始视频的音频合并到裁切后的视频（无音频）中"""
    # 先将无音频的临时文件重命名
    temp_path = output_video + temp_suffix
    if os.path.exists(temp_path):
        os.remove(temp_path)
    os.rename(output_video, temp_path)

    try:
        cmd = [
            FFMPEG_PATH, '-y',
            '-i', temp_path,
            '-i', source_video,
            '-map', '0:v',           # 取临时视频的视频轨道
            '-map', '1:a?',          # 取原始视频的音频轨道（? 表示可选）
            '-c:v', 'copy',          # 视频直接拷贝，不重新编码
            '-c:a', 'aac',           # 音频编码为 aac
            '-shortest',             # 以较短的流为准
            output_video,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)

        if result.returncode != 0:
            # ffmpeg 失败，恢复无音频版本并打印警告
            print(f"[WARN] Audio merge failed, video saved without audio: {result.stderr.strip()}")
            os.rename(temp_path, output_video)
        else:
            # 成功则删除临时文件
            os.remove(temp_path)

    except FileNotFoundError:
        print("[WARN] imageio-ffmpeg not available, video saved without audio")
        os.rename(temp_path, output_video)
    except Exception as e:
        print(f"[WARN] Audio merge error ({e}), video saved without audio")
        if os.path.exists(temp_path):
            os.rename(temp_path, output_video)


def process_single_video(name, args):
    """处理单个视频文件（工作进程入口）"""
    prefix = name.split('.')[0]
    source_path = os.path.join(args.source_dir, name)
    target_path = os.path.join(args.save_dir, prefix + '.mp4')

    # 跳过已处理的视频
    if os.path.exists(target_path):
        return None

    args.inp = str(source_path)
    args.outp = str(target_path)

    try:
        # 每个工作进程独立加载检测器（避免 spawn 序列化问题）
        device = 'cpu' if args.cpu else 'cuda'
        local_detector = _get_face_detector(device=device, conf_th=0.5)

        success = process_video(args, local_detector)
        return name if success else None

    except Exception as e:
        print(f"[ERROR] Processing {name}: {e}")
        return None


if __name__ == "__main__":
    parser = ArgumentParser()
    parser.add_argument("--base_dir", required=True, help="Base directory path")
    parser.add_argument("--image_shape", default=(256, 256), type=lambda x: tuple(map(int, x.split(','))),
                        help="Output image shape (width,height)")
    parser.add_argument("--increase", default=0.1, type=float, help='Increase bbox by this amount')
    parser.add_argument("--iou_with_initial", type=float, default=0.25, help="IoU threshold (legacy, unused)")
    parser.add_argument("--min_frames", type=int, default=0, help='Minimum number of frames for a valid segment')
    parser.add_argument("--cpu", dest="cpu", action="store_true", help="Force CPU mode")
    parser.add_argument("--detect_interval", type=int, default=1,
                        help="Face detection interval (every N frames). Lower=slower but more accurate.")
    parser.add_argument("--min_face_size", type=int, default=30,
                        help="Minimum face size in pixels")
    parser.add_argument("--num_processes", type=int, default=5,
                        help="工作进程数 (<=1 时串行执行，避免 Windows spawn 重新导入 torch 的崩溃风险)")
    parser.add_argument("--detect_timeout_s", type=float, default=0.0,
                        help="单帧人脸检测超时阈值(秒)，0=禁用；超时后冷却期内跳过检测 (Windows WDDM 死锁防护)")

    args = parser.parse_args()

    HDTF_dir = os.path.join(args.base_dir, 'original_videos')
    save_HDTF_dir = os.path.join(args.base_dir, 'video')

    args.source_dir = str(HDTF_dir)
    args.save_dir = str(save_HDTF_dir)

    os.makedirs(save_HDTF_dir, exist_ok=True)

    all_data = sorted([f for f in os.listdir(HDTF_dir)
                       if f.lower().endswith(('.mp4', '.avi', '.mov', '.mkv'))])
    print(f"Found {len(all_data)} videos in {HDTF_dir}")

    num_processes = max(1, args.num_processes)
    if num_processes <= 1:
        # 串行模式：直接在主进程执行，避免 spawn 重新导入模块(含 torch)的开销与崩溃风险
        for name in all_data:
            process_single_video(name, args)
    else:
        ctx = multiprocessing.get_context('spawn')
        with ctx.Pool(processes=num_processes) as pool:
            func = partial(process_single_video, args=args)
            pool.map(func, all_data)

    print("Done.")
