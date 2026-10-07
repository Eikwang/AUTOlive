"""T0/T7 质量前置验证（GAZE_CORRECTION_PLAN.md 最终门裁决步骤 1）。

用原版离线实现（dlib + EAR 状态机 + Generator）对角色 orifra 帧段生成三联对比：

    原帧 | skimage 直方图匹配（原版） | μ/σ 颜色迁移（T7 A/B）

输出（--out 目录）:
    compare_%04d.jpg   三联对比帧（全分辨率，供逐帧目检）
    comparison.mp4     缩放汇总视频（供快速浏览）
    report.md          检测成功率 / 修正率 / EAR 旁路统计 / 耗时分布 / 目检指引

用法:
    python -m realtime_serve.gaze_quality_probe --character-dir wy \
        --segment 1 --start 3000 --frames 100

裁决依据（方案 §最终门）: 目检确认修正可见且有价值 → 继续路线 A/B 实施；
不可见或无价值 → 功能关闭，方案归档。
"""
import argparse
import os
import time

import cv2
import numpy as np

from pytorch_gaze_redirection.src.inference import GazeRedirectInference


def color_transfer_meanstd(src: np.ndarray, dst: np.ndarray) -> np.ndarray:
    """T7: 逐通道 μ/σ 对齐，替代 skimage.match_histograms。

    src: 生成的眼部 overlay（BGR uint8，eye_size 见方）
    dst: 目标眼区（BGR uint8）
    """
    out = np.empty_like(src, dtype=np.float32)
    for c in range(3):
        s_mean = float(src[..., c].mean())
        s_std = float(src[..., c].std()) + 1e-6
        d_mean = float(dst[..., c].mean())
        d_std = float(dst[..., c].std()) + 1e-6
        out[..., c] = (src[..., c].astype(np.float32) - s_mean) * (d_std / s_std) + d_mean
    return np.clip(out, 0, 255).astype(np.uint8)


class MuSigmaGazeInference(GazeRedirectInference):
    """仅替换贴回颜色迁移：match_histograms → μ/σ（T7 A/B 对照）。

    其余逻辑（检测/EAR/状态机/推理/裁剪）与原版完全一致，两引擎处理同一
    帧序列时状态机行为应当逐帧一致。
    """

    def _paste_eye(self, original_bgr, gen_tensor, x, y, eye_size):
        overlay = ((gen_tensor.permute(1, 2, 0).cpu().numpy() + 1.0) * 0.5 * 255).astype(np.uint8)
        overlay_bgr = cv2.cvtColor(overlay, cv2.COLOR_RGB2BGR)
        resized = cv2.resize(overlay_bgr, (eye_size, eye_size),
                             interpolation=cv2.INTER_LINEAR)

        h, w = original_bgr.shape[:2]
        if y < 0 or x < 0 or y + eye_size > h or x + eye_size > w:
            return

        target = original_bgr[y:y + eye_size, x:x + eye_size]
        matched = color_transfer_meanstd(resized, target)
        mask = self._get_mask(eye_size)
        blended = (matched.astype(np.float32) * mask +
                   target.astype(np.float32) * (1.0 - mask)).astype(np.uint8)
        original_bgr[y:y + eye_size, x:x + eye_size] = blended


def _numeric_sort_key(name: str):
    stem = os.path.splitext(name)[0]
    return (0, int(stem)) if stem.isdigit() else (1, stem)


def main():
    parser = argparse.ArgumentParser(description="Gaze correction quality probe (T0/T7)")
    parser.add_argument("--character-dir", default="wy")
    parser.add_argument("--segment", default="1")
    parser.add_argument("--start", type=int, default=3000,
                        help="起始帧号（段内索引）")
    parser.add_argument("--frames", type=int, default=100)
    parser.add_argument("--ckpt", default="realtime_serve/weights/current_model.ckpt")
    parser.add_argument("--out", default="realtime_serve/gaze_probe_out")
    parser.add_argument("--video-fps", type=int, default=25)
    args = parser.parse_args()

    os.makedirs(args.out, exist_ok=True)

    orifra_dir = os.path.join(args.character_dir, "orifra", args.segment)
    files = sorted((f for f in os.listdir(orifra_dir) if f.endswith(".jpg")),
                   key=_numeric_sort_key)
    total_in_segment = len(files)
    picked = files[args.start:args.start + args.frames]
    if not picked:
        raise SystemExit(f"no frames: start={args.start} in {orifra_dir} ({total_in_segment} total)")
    print(f"[probe] segment={args.segment} total={total_in_segment} "
          f"range=[{args.start}, {args.start + len(picked)}) frames={len(picked)}")

    print("[probe] loading models (dlib + gaze generator)...")
    t0 = time.perf_counter()
    infer_hist = GazeRedirectInference(args.ckpt)
    infer_mu = MuSigmaGazeInference(args.ckpt)
    print(f"[probe] loaded in {time.perf_counter() - t0:.1f}s")

    times_hist, times_mu = [], []
    corrected = 0
    bypassed = []
    writer = None

    for i, fname in enumerate(picked):
        frame = cv2.imread(os.path.join(orifra_dir, fname))
        if frame is None:
            print(f"[probe] WARN: cannot read {fname}")
            continue

        t0 = time.perf_counter()
        out_hist = infer_hist.process_frame(frame)
        times_hist.append(time.perf_counter() - t0)

        t0 = time.perf_counter()
        out_mu = infer_mu.process_frame(frame)
        times_mu.append(time.perf_counter() - t0)

        # 旁路判定：process_frame 未修正时返回原帧副本（逐位相等）。
        changed = not np.array_equal(out_hist, frame)
        if changed:
            corrected += 1
        else:
            bypassed.append((args.start + i, fname))

        triptych = np.hstack([frame, out_hist, out_mu])
        cv2.imwrite(os.path.join(args.out, f"compare_{i:04d}.jpg"), triptych,
                    [cv2.IMWRITE_JPEG_QUALITY, 90])

        if writer is None:
            vh, vw = frame.shape[:2]
            scale = 900.0 / vw  # 三联缩放到 ~2700 宽以内
            vw2, vh2 = int(vw * scale), int(vh * scale)
            writer = cv2.VideoWriter(
                os.path.join(args.out, "comparison.mp4"),
                cv2.VideoWriter_fourcc(*"mp4v"),
                args.video_fps, (vw2 * 3, vh2))
        triptych_small = cv2.resize(triptych,
                                    (vw2 * 3, vh2), interpolation=cv2.INTER_AREA)
        writer.write(triptych_small)

        if (i + 1) % 10 == 0 or i == 0:
            print(f"[probe] {i + 1}/{len(picked)}  hist={times_hist[-1] * 1000:.0f}ms  "
                  f"mu={times_mu[-1] * 1000:.0f}ms  corrected={corrected}")

    if writer is not None:
        writer.release()

    def pct(vals, p):
        if not vals:
            return 0.0
        s = sorted(vals)
        return s[min(int(len(s) * p / 100.0), len(s) - 1)] * 1000.0

    report = []
    report.append("# Gaze Quality Probe Report (T0/T7)")
    report.append("")
    report.append(f"- segment: {args.segment} (total {total_in_segment} frames)")
    report.append(f"- frame range: [{args.start}, {args.start + len(picked)})")
    report.append(f"- frames processed: {len(picked)}")
    report.append(f"- corrected frames: {corrected} ({100.0 * corrected / max(1, len(picked)):.1f}%)")
    report.append(f"- bypassed (unchanged) frames: {len(bypassed)}")
    if bypassed:
        idx_list = ", ".join(str(b[0]) for b in bypassed[:20])
        more = f" ... (+{len(bypassed) - 20})" if len(bypassed) > 20 else ""
        report.append(f"  - bypassed frame indices: {idx_list}{more}")
    report.append("")
    report.append("## Per-frame latency (ms)")
    report.append("")
    report.append("| engine | p50 | p95 | max |")
    report.append("|---|---|---|---|")
    report.append(f"| skimage match (原版) | {pct(times_hist, 50):.0f} | {pct(times_hist, 95):.0f} | "
                  f"{max(times_hist) * 1000:.0f} |")
    report.append(f"| mu/sigma (T7) | {pct(times_mu, 50):.0f} | {pct(times_mu, 95):.0f} | "
                  f"{max(times_mu) * 1000:.0f} |")
    report.append("")
    report.append("## T7 A/B: mu/sigma vs skimage match")
    report.append("")
    report.append("对比 compare_*.jpg 中列 2（skimage）与列 3（mu/sigma）的眼部区域：")
    report.append("若肉眼不可分辨 → 实时/离线实现均可采用更快的 mu/sigma（免 skimage 依赖）。")
    report.append("")
    report.append("## 目检指引（裁决依据）")
    report.append("")
    report.append("1. 修正可见性：对比列 1（原帧）与列 2/3 的瞳孔朝向——常量收敛角修正（约 ±2.9°）")
    report.append("   在该资产上是否可感知？")
    report.append("2. 画质：生成眼有无色差 / 贴片感 / 边缘伪影？两引擎差异？")
    report.append("3. 旁路帧：bypassed indices 对应的画面是否合理（眨眼/检测失败）？")
    report.append("4. 裁决：可见且有价值 → 进入路线 A/B 实施；否则功能关闭，方案归档。")

    report_path = os.path.join(args.out, "report.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(report) + "\n")

    print(f"[probe] done: {len(picked)} frames, corrected={corrected}, bypassed={len(bypassed)}")
    print(f"[probe] report: {report_path}")
    print(f"[probe] triptychs: {args.out}/compare_*.jpg, video: {args.out}/comparison.mp4")


if __name__ == "__main__":
    main()
