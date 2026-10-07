"""路线 C 关键验证 probe：Generator 眼睛保留度（GAZE_CORRECTION_PLAN T0 扩展）。

链路: count crop(256, GPU) --gaze纠正--> 纠正后 crop --EDTalk Generator--> 输出
验证: Generator 输出的眼睛是否跟随输入端的纠正（路线 C 的因果前提）。

输出（--out 目录）:
    crop_gen_{idx}.jpg    三联眼区放大: 原 crop 经 Gen | 纠正 crop 经 Gen | 原 crop 原图
    crop_full_{idx}.jpg   整帧 256 对比
    report.md             量化差异（眼区像素位移估计）+ 目检指引

用法:
    python -m realtime_serve.gaze_crop_probe --character-dir wy --segment 1 \
        --frames 3000,3010,3020 --convergence 0.05
"""
import argparse
import os

import cv2
import numpy as np
import torch
import torch.nn.functional as F

from pytorch_gaze_redirection.src.archs import Generator
from realtime_serve.gaze_quality_probe import color_transfer_meanstd


class CropGazeCorrector:
    """路线 C 核心（雏形）：在 256×256 count crop 上做双眼视线纠正，全程 GPU。

    复用 T0 probe 已验证的推理栈（gaze Generator + μ/σ 颜色迁移 + 圆形 mask），
    眼位置来自预处理 facemark（256 空间，无需 dlib）。
    """

    L_EYE = list(range(36, 42))
    R_EYE = list(range(42, 48))

    def __init__(self, ckpt_path: str, device: str = 'cuda',
                 convergence_h: float = 0.05, image_size: int = 64):
        self.device = torch.device(device)
        self.convergence_h = convergence_h
        self.image_size = image_size

        self.generator = Generator(style_dim=3)
        ckpt = torch.load(ckpt_path, map_location=self.device, weights_only=True)
        self.generator.load_state_dict(ckpt['current_generator'])
        self.generator = self.generator.to(self.device)
        self.generator.eval()

        self._angles = torch.tensor(
            [[-convergence_h, 0.0, -1.0],
             [convergence_h, 0.0, 1.0]], device=self.device)

        size = image_size
        mask = np.zeros((size, size), dtype=np.float32)
        cv2.circle(mask, (size // 2, size // 2), size // 2 - 2, 1.0, thickness=-1)
        mask = cv2.GaussianBlur(mask, (7, 7), 0)
        self._mask64 = torch.from_numpy(mask[..., np.newaxis]).to(self.device)

    @torch.inference_mode()
    def correct_crop(self, crop_rgb: torch.Tensor, facemark_256: np.ndarray) -> torch.Tensor:
        """纠正 256 crop 的双眼。

        Args:
            crop_rgb: (3,256,256) RGB float [-1,1]，GPU。
            facemark_256: (68,2) 256 空间关键点（numpy）。
        Returns:
            (3,256,256) RGB float [-1,1]，GPU（新 tensor，不修改输入）。
        """
        out = crop_rgb.clone()
        boxes = []
        for ids in (self.L_EYE, self.R_EYE):
            pts = facemark_256[ids]
            x1, y1 = pts.min(axis=0)
            x2, y2 = pts.max(axis=0)
            cx, cy = (x1 + x2) / 2.0, (y1 + y2) / 2.0
            eye_size = int(1.5 * max(x2 - x1, y2 - y1))
            eye_size = max(16, min(eye_size, 128))
            boxes.append((cx, cy, eye_size))

        tensors = []
        coords = []
        for cx, cy, eye_size in boxes:
            half = eye_size / 2.0
            x1 = int(round(cx - half)); y1 = int(round(cy - half))
            x1c = max(0, x1); y1c = max(0, y1)
            x2c = min(256, x1 + eye_size); y2c = min(256, y1 + eye_size)
            region = crop_rgb[:, y1c:y2c, x1c:x2c].unsqueeze(0)
            # [-1,1] 域直接 interpolate（与训练 Normalize(0.5,0.5) 等价）
            t = F.interpolate(region, size=(self.image_size, self.image_size),
                              mode='bilinear', antialias=True)
            tensors.append(t)
            coords.append((x1c, y1c, x2c - x1c, y2c - y1c))

        batch = torch.cat(tensors, dim=0)  # [2,3,64,64]
        gen = self.generator(batch, self._angles)  # [2,3,64,64] [-1,1]

        for i, (x1c, y1c, w, h) in enumerate(coords):
            eye64 = gen[i]  # (3,64,64)
            # μ/σ 颜色迁移（T7 已验证与 skimage 不可分辨）：
            # 在 [-1,1] 域按通道对齐到原 crop 眼区
            target = crop_rgb[:, y1c:y1c + h, x1c:x1c + w]
            for c in range(3):
                s_mean = eye64[c].mean(); s_std = eye64[c].std() + 1e-6
                d_mean = target[c].mean(); d_std = target[c].std() + 1e-6
                eye64[c] = (eye64[c] - s_mean) * (d_std / s_std) + d_mean
            eye64 = eye64.clamp(-1, 1)

            eye_resized = F.interpolate(eye64.unsqueeze(0), size=(h, w),
                                        mode='bilinear', antialias=True)[0]
            # 圆形 mask（resize 到眼区尺寸）blend，写回输出
            m = F.interpolate(self._mask64.permute(2, 0, 1).unsqueeze(0),
                              size=(h, w), mode='bilinear',
                              antialias=True)[0]  # (1,h,w)
            region_out = out[:, y1c:y1c + h, x1c:x1c + w]
            blended = eye_resized * m + region_out * (1.0 - m)
            out[:, y1c:y1c + h, x1c:x1c + w] = blended

        return out


def _to_uint8_hwc(t: torch.Tensor) -> np.ndarray:
    """(3,256,256) RGB [-1,1] GPU → (256,256,3) BGR uint8 numpy。"""
    x = ((t.clamp(-1, 1) + 1.0) * 127.5).to(torch.uint8)
    x = x.permute(1, 2, 0).cpu().numpy()
    return cv2.cvtColor(x, cv2.COLOR_RGB2BGR)


def main():
    parser = argparse.ArgumentParser(description="Route-C probe: generator eye retention")
    parser.add_argument("--character-dir", default="wy")
    parser.add_argument("--segment", default="1")
    parser.add_argument("--frames", default="3000,3010,3020,3030",
                        help="逗号分隔的段内帧号")
    parser.add_argument("--gaze-ckpt", default="realtime_serve/weights/current_model.ckpt")
    parser.add_argument("--convergence", type=float, default=0.05)
    parser.add_argument("--out", default="realtime_serve/gaze_crop_probe_out")
    args = parser.parse_args()

    os.makedirs(args.out, exist_ok=True)
    frame_ids = [int(x) for x in args.frames.split(",")]

    from realtime_serve.models import ModelManager

    seg_dir = os.path.join(args.character_dir, "orifra", args.segment)
    count_dir = os.path.join(args.character_dir, "count", args.segment)
    meta = np.load(os.path.join(args.character_dir, "metadata", f"{args.segment}.npy"))
    facemarks = np.load(os.path.join(args.character_dir, "facemark", f"{args.segment}.npy"))

    def _key(name):
        stem = os.path.splitext(name)[0]
        return (0, int(stem)) if stem.isdigit() else (1, stem)

    count_files = sorted((f for f in os.listdir(count_dir) if f.endswith(".jpg")), key=_key)

    print("[probe] loading gaze generator + EDTalk models...")
    corrector = CropGazeCorrector(args.gaze_ckpt, convergence_h=args.convergence)
    mm = ModelManager(
        os.path.join(args.character_dir, "checkpoint", "new.pt"),
        os.path.join(args.character_dir, "checkpoint", "audio2lip.pt"),
        device='cuda')
    mm.load()

    report = ["# Route-C Probe: Generator Eye Retention", ""]

    for idx in frame_ids:
        count_path = os.path.join(count_dir, count_files[idx])
        img = cv2.imread(count_path)
        if img is None:
            print(f"[probe] cannot read {count_path}")
            continue
        rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        crop = torch.from_numpy(
            np.ascontiguousarray(rgb.astype(np.float32).transpose(2, 0, 1)) / 127.5 - 1.0
        ).to('cuda')

        fm = facemarks[idx]
        if np.any(np.isnan(fm)):
            print(f"[probe] frame {idx}: facemark NaN, skip")
            continue

        corrected = corrector.correct_crop(crop, fm)

        zero_lip = torch.zeros(1, 20, device='cuda')
        # CUDA Graph 路径返回 static buffer 引用：第二次 replay 会覆盖它，
        # 必须在第二次调用前 clone 第一次的结果。
        gen_orig = mm.infer_frame(crop.unsqueeze(0), zero_lip, crop.unsqueeze(0)).clone()
        gen_corr = mm.infer_frame(corrected.unsqueeze(0), zero_lip, corrected.unsqueeze(0)).clone()

        # C' 变体验证：纠正挂在 Generator 输出（rendered）上，
        # 不再经过 Generator（规避重绘抹除）。这是"口型推理后纠正"路线的核心步骤。
        rendered = gen_orig[0]  # (3,256,256) [-1,1] RGB GPU
        rendered_corrected = corrector.correct_crop(rendered, fm)

        orig_u8 = _to_uint8_hwc(crop)
        corr_u8 = _to_uint8_hwc(corrected)
        gen_orig_u8 = _to_uint8_hwc(gen_orig[0])
        gen_corr_u8 = _to_uint8_hwc(gen_corr[0])
        rendered_corr_u8 = _to_uint8_hwc(rendered_corrected)

        # 量化：纠正是否穿透 Generator（眼区像素差）
        fm_pts = facemarks[idx]
        diff_eye = np.abs(gen_corr_u8.astype(np.int16) - gen_orig_u8.astype(np.int16)).mean()
        # 眼区外（嘴区，landmark 48-59 外扩）差异作对照
        m = np.zeros((256, 256), dtype=bool)
        for ids in (CropGazeCorrector.L_EYE, CropGazeCorrector.R_EYE):
            pts = fm_pts[ids]
            x1, y1 = pts.min(axis=0).astype(int) - 8
            x2, y2 = pts.max(axis=0).astype(int) + 8
            m[max(0, y1):y2, max(0, x1):x2] = True
        diff_outside = np.abs((gen_corr_u8.astype(np.int16) - gen_orig_u8.astype(np.int16))[~m]).mean()

        # C' 量化：rendered 纠正前后的眼区差异（应为显著信号）
        diff_rendered_eye = np.abs(
            (rendered_corr_u8.astype(np.int16) - gen_orig_u8.astype(np.int16))[m]).mean()

        # 眼区放大四联: 原经Gen | C(输入端纠正经Gen) | C'(rendered后纠正) | 纠正输入参考
        x1, y1 = fm_pts[36:48, 0].min() - 15, fm_pts[36:48, 1].min() - 15
        x2, y2 = fm_pts[36:48, 0].max() + 15, fm_pts[36:48, 1].max() + 15
        x1, y1 = max(0, int(x1)), max(0, int(y1))
        x2, y2 = min(256, int(x2)), min(256, int(y2))
        scale = 4
        tri = np.hstack([
            gen_orig_u8[y1:y2, x1:x2], gen_corr_u8[y1:y2, x1:x2],
            rendered_corr_u8[y1:y2, x1:x2], corr_u8[y1:y2, x1:x2],
        ])
        tri = cv2.resize(tri, ((x2 - x1) * 4 * scale // 4, (y2 - y1) * scale),
                         interpolation=cv2.INTER_NEAREST)
        cv2.imwrite(os.path.join(args.out, f"crop_gen_{idx}.jpg"), tri)

        full_tri = np.hstack([gen_orig_u8, rendered_corr_u8, orig_u8])
        cv2.imwrite(os.path.join(args.out, f"crop_full_{idx}.jpg"), full_tri)

        report.append(
            f"- frame {idx}: "
            f"C(输入端)眼区差 {diff_eye:.2f} vs 眼区外 {diff_outside:.2f}"
            f"（{diff_eye / max(diff_outside, 1e-6):.1f}x）；"
            f"C'(rendered端)眼区差 {diff_rendered_eye:.2f}"
            f"（{diff_rendered_eye / max(diff_outside, 1e-6):.1f}x）")
        print(f"[probe] frame {idx}: eye_diff={diff_eye:.2f} outside_diff={diff_outside:.2f}")

    report.append("")
    report.append("## 目检指引")
    report.append("")
    report.append("- crop_gen_*.jpg 四联: 1)原经Gen 2)C输入端纠正经Gen 3)C'rendered后纠正 4)纠正输入参考")
    report.append("- **C 验证**（第1 vs 2列）: 已实测——2列不跟随纠正（Generator 抹除），路线 C 输入端失效")
    report.append("- **C' 验证**（第1 vs 3列）: 3列瞳孔应明显朝向正视（区别于 1 列原视向）")
    report.append("  - C' 有效且画质可接受 → 按 C' 实施（Gen输出后纠正 + mask 扩展双眼贴回）")
    report.append("- 同时检查 3 列眼睛质量：色差/伪影/与邻域融合度（mask 扩展后将以此眼替换原生眼）")

    with open(os.path.join(args.out, "report.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(report) + "\n")
    print(f"[probe] report: {os.path.join(args.out, 'report.md')}")


if __name__ == "__main__":
    main()
