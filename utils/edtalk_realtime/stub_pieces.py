# -*- coding: utf-8 -*-
"""素材桩四件套生成器（R-eng4①：crop_head_video.py 之外的旁路脚本）。

用途：「口型推理不勾选」素材的简单处理（R-ceo2）——跳过人脸检测/关键点/gaze，
产出与四件套加载器硬校验（sequence_frame_pack.py:929 存在性 + :962-967 帧数一致性）
兼容的桩结构：
  orifra/<seg>/  原帧序列（ffmpeg 拆帧，longest_edge 缩放与主链一致）
  count/<seg>/   原帧序列 resize 256x256（256 源等价直拷；off 路径零成本）
  metadata/<seg>.npy  11 字段结构化数组（crop=含帧正方形框，非方形源运行期不被消费
                      ——facemark 全 NaN 恒走直通）
  facemark/<seg>.npy  (N,68,2) 全 NaN float32 桩

由 AUTOlive main 进程以 EDTalk runtime 解释器子进程执行（该环境有 cv2/numpy；
AUTOlive 主环境无 cv2）。不修改 crop_head_video.py（上游 md5 纪律）。
用法：
  python stub_pieces.py <stage_dir> --segment <seg> [--ffmpeg <path>]
其中 <stage_dir> 内需有 original_videos/<seg>.<ext>；产物写在 <stage_dir> 下。
"""
import argparse
import os
import subprocess
import sys

import numpy as np

try:
    import cv2
except ImportError:  # pragma: no cover - 仅 runtime 解释器运行，必有 cv2
    cv2 = None

_META_DTYPE = [
    ('frame_idx', '<i4'), ('crop_x', '<f4'), ('crop_y', '<f4'),
    ('crop_size', '<f4'), ('scale_factor', '<f4'), ('face_detected', '?'),
    ('pad_ratio', '<f4'), ('face_bbox_x', '<f4'), ('face_bbox_y', '<f4'),
    ('face_bbox_w', '<f4'), ('face_bbox_h', '<f4')]
_LONGEST_EDGE = 1920


def _find_ffmpeg(explicit: str) -> str:
    if explicit and os.path.isfile(explicit):
        return explicit
    py_dir = os.path.dirname(os.path.abspath(sys.executable))
    cand = os.path.normpath(os.path.join(py_dir, 'ffmpeg', 'bin', 'ffmpeg.exe'))
    if os.path.isfile(cand):
        return cand
    return 'ffmpeg'


def extract_frames(video: str, out_dir: str, ffmpeg: str) -> int:
    """拆帧（与 crop_head_video.extract_frames 同参：最长边 1920 不放大）。"""
    os.makedirs(out_dir, exist_ok=True)
    vf = f"scale='min({_LONGEST_EDGE},iw)':-2"
    cmd = [ffmpeg, '-y', '-i', video, '-vf', vf, '-q:v', '2',
           os.path.join(out_dir, '%d.jpg')]
    proc = subprocess.run(cmd, capture_output=True, text=True,
                          encoding='utf-8', errors='replace')
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr[-800:])
        raise RuntimeError(f'ffmpeg 拆帧失败（exit {proc.returncode}）')
    return len([f for f in os.listdir(out_dir) if f.endswith('.jpg')])


def build_stub(stage_dir: str, segment: str) -> dict:
    orifra_dir = os.path.join(stage_dir, 'orifra', segment)
    count_dir = os.path.join(stage_dir, 'count', segment)
    frames = sorted((f for f in os.listdir(orifra_dir) if f.endswith('.jpg')),
                    key=lambda n: int(os.path.splitext(n)[0]))
    if not frames:
        raise RuntimeError('拆帧产出为空（零帧视频或解码失败）')

    os.makedirs(count_dir, exist_ok=True)
    first = cv2.imread(os.path.join(orifra_dir, frames[0]))
    fh, fw = first.shape[:2]
    # crop=含帧正方形框（F-M3/R-ceo9：非方形源运行期不被消费——facemark 全 NaN 恒直通）
    crop_size = float(max(fw, fh))
    crop_x = (fw - crop_size) / 2.0
    crop_y = (fh - crop_size) / 2.0

    meta_rows = []
    for i, name in enumerate(frames):
        img = cv2.imread(os.path.join(orifra_dir, name))
        if img is None:
            raise RuntimeError(f'帧读取失败: {name}')
        resized = cv2.resize(img, (256, 256), interpolation=cv2.INTER_AREA)
        cv2.imwrite(os.path.join(count_dir, name), resized)
        meta_rows.append((i, crop_x, crop_y, crop_size, 1.0, False, 0.0,
                          0.0, 0.0, 0.0, 0.0))

    metadata = np.array(meta_rows, dtype=_META_DTYPE)
    np.save(os.path.join(stage_dir, 'metadata', f'{segment}.npy'), metadata)
    facemark = np.full((len(frames), 68, 2), np.nan, dtype=np.float32)
    np.save(os.path.join(stage_dir, 'facemark', f'{segment}.npy'), facemark)
    return {'total_frames': len(frames), 'width': fw, 'height': fh,
            'closeup': True}  # 桩按构造 crop 覆盖全帧；X1 仅挂 on 路径，此值不被消费


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('stage_dir')
    ap.add_argument('--segment', required=True)
    ap.add_argument('--ffmpeg', default='')
    opt = ap.parse_args()

    video = None
    ov = os.path.join(opt.stage_dir, 'original_videos')
    for fn in os.listdir(ov):
        if os.path.splitext(fn)[1].lower() in (
                '.mp4', '.avi', '.mov', '.mkv', '.flv', '.webm', '.wmv'):
            video = os.path.join(ov, fn)
            break
    if not video:
        print('[stub] ERROR: original_videos 下无视频文件', file=sys.stderr)
        sys.exit(1)

    ffmpeg = _find_ffmpeg(opt.ffmpeg)
    n = extract_frames(video, os.path.join(opt.stage_dir, 'orifra', opt.segment),
                       ffmpeg)
    print(f'[stub] 拆帧完成: {n} 帧')
    for d in ('metadata', 'facemark'):
        os.makedirs(os.path.join(opt.stage_dir, d), exist_ok=True)
    info = build_stub(opt.stage_dir, opt.segment)
    print(f"[stub] 桩四件套完成: {info}")


if __name__ == '__main__':
    main()
