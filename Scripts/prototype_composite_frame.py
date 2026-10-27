# -*- coding: utf-8 -*-
"""X5 背景合成单帧原理原型（终门 uc3a 裁决辅助；不集成——R-ceo1/native F7）。

演示合成层 A 方案（EDTalk 渲染链内）的单帧几何：头部 256 裁剪 + 背景画布 →
按 sidecar 几何契约（head_position/head_scale）合成完整画面。供终门/用户直观看
「头部特写素材+背景=完整画面」的预期效果——集成实现（R-eng11 注入点纪律）另批。

用法（AUTOlive 主环境，PIL 即可）：
  python Scripts/prototype_composite_frame.py <head_256.jpg> [background.jpg] [out.jpg]
无参=用 wy 角色 orifra/44 首帧自演示。
"""
import os
import sys

from PIL import Image

CANVAS = (1280, 720)          # R-ceo9 默认画布
DEFAULT_BG = (24, 24, 32)     # 深色默认背景（非黑——「不合成素材」=默认背景直出）
HEAD_POS = (0.5, 0.42)        # R-ceo9 sidecar 几何缺省
HEAD_SCALE = 0.5


def composite(head_path: str, bg_path: str = None,
              out_path: str = "prototype_composite.jpg") -> str:
    canvas = Image.open(bg_path).convert("RGB") if bg_path and os.path.isfile(bg_path) \
        else Image.new("RGB", CANVAS, DEFAULT_BG)
    canvas = canvas.resize(CANVAS)
    head = Image.open(head_path).convert("RGB")
    side = int(min(CANVAS) * HEAD_SCALE)
    head = head.resize((side, side))
    cx, cy = int(CANVAS[0] * HEAD_POS[0]), int(CANVAS[1] * HEAD_POS[1])
    canvas.paste(head, (max(0, cx - side // 2), max(0, cy - side // 2)))
    canvas.save(out_path, quality=90)
    return out_path


if __name__ == "__main__":
    head = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        "edtalk", "wy", "orifra", "44", "1.jpg")
    bg = sys.argv[2] if len(sys.argv) > 2 else None
    out = sys.argv[3] if len(sys.argv) > 3 else "prototype_composite.jpg"
    if not os.path.isfile(head):
        print(f"头部帧不存在：{head}（或显式传入 256 图路径）")
        sys.exit(1)
    out = composite(head, bg, out)
    print(f"合成原型已输出：{out}（画布 {CANVAS}，头部位置 {HEAD_POS}，缩放 {HEAD_SCALE}）"
          "——集成版按 sidecar 几何字段逐素材可调")
