# -*- coding: utf-8 -*-
"""统一画布合成层（A 方案，R-ceo9/R-eng11，2026-10-27 画面输出批次）。

AUTOlive 画面输出批次新增模块——edtalk/ realtime_serve 的首个分叉文件
（上游 D:\AI\EDTalk 无此文件；分叉治理见 edtalk/FORK_LEDGER.md）。

职责：所有素材出帧统一合成到会话级画布（R-ceo9 统一画布政策）：
  - 全会话单一画布分辨率（构造时定死；虚拟摄像头重建仅发生在会话启动——
    消灭 unity_capture.update_resolution 逐段重开的实播抖动源）；
  - 段背景按需懒加载（背景每段贴一次；头部 ROI 每帧一次——R-eng11②）；
  - 「不合成」素材=contain 适配到画布（默认背景填充，非黑）；
  - 原生直通仅当帧已为画布尺寸（零成本路径）；
  - 专用预分配画布缓冲，禁止触碰预加载 orifra（pristine 纪律，
    GAZE_TIMING_PLAN T1/X1 回绕漂移防复发——回绕回归测试必选 R-eng11③）；
  - 连续异常自动旁路（降级直出）+ 计数暴露 /status（R-eng11④）。

线程模型：apply() 由 RenderLoop 产出线程逐帧调用（单消费者）；段切换
（set_segment/换绑）由渲染线程同线程发生——背景缓存无跨线程竞争。
"""
import json
import os

import cv2
import numpy as np


class CanvasCompositor:
    """会话级统一画布合成器。

    Args:
        canvas_w/canvas_h: 会话画布分辨率（唯一分辨率来源——R-ceo9）。
        character_dir: 角色目录（段背景文件根：backgrounds/<seg>.(jpg|png)，
            可选 <seg>.json 几何覆盖 {"head_position":[x,y],"head_scale":s}）。
        default_bg_path: 直播间默认背景图（可空=深色底）。
        enabled: 总开关（False=apply 恒直通；连续异常自动置 False 降级）。
    """

    # 连续异常自动旁路阈值（~6s@25fps：降级直出优于持续劣化——R-eng11④）
    MAX_CONSECUTIVE_ERRORS = 150

    def __init__(self, canvas_w: int = 1280, canvas_h: int = 720,
                 character_dir: str = None, default_bg_path: str = None,
                 enabled: bool = True):
        if canvas_w < 64 or canvas_h < 64:
            raise ValueError(
                f"画布尺寸过小（{canvas_w}x{canvas_h}）：须 ≥64（R-ceo9 统一画布）")
        self.canvas_w = int(canvas_w)
        self.canvas_h = int(canvas_h)
        self.character_dir = character_dir
        self.enabled = bool(enabled)
        self.error_count = 0
        self.consecutive_errors = 0
        self.applied_count = 0
        self.passthrough_count = 0
        # 专用预分配画布缓冲（R-eng11②：禁碰预加载 orifra——本缓冲为合成层私有）
        self._canvas_bgr = np.zeros((self.canvas_h, self.canvas_w, 3), dtype=np.uint8)
        # 段背景缓存：{segment: (bg_bgr 或 None, head_position, head_scale, composite)}
        self._seg_cache: dict = {}
        self._default_bg = self._load_image(default_bg_path)
        if self._default_bg is not None:
            self._default_bg = cv2.resize(self._default_bg, (self.canvas_w, self.canvas_h))

    # ------------------------------------------------------------------
    # 段背景（懒加载：每段一次——R-eng11②）
    # ------------------------------------------------------------------

    def invalidate_segment(self, segment: str = None) -> None:
        """段背景缓存失效（素材重处理/背景换绑后调用；None=全清）。"""
        if segment is None:
            self._seg_cache.clear()
        else:
            self._seg_cache.pop(segment, None)

    def _load_image(self, path: str):
        if not path or not os.path.isfile(path):
            return None
        img = cv2.imread(path)
        return img

    def _seg_config(self, segment: str) -> tuple:
        """段合成配置（懒加载缓存）：(bg_bgr|None, head_pos, head_scale, composite)。

        composite=True 当且仅当 backgrounds/<seg> 图片文件存在（R-eng2 F-H3
        交付契约：background 文件约定=合成 opt-in 信号）；否则 contain 适配
        （F-M3 缺省=contain+默认背景）。
        """
        cached = self._seg_cache.get(segment)
        if cached is not None:
            return cached
        bg = None
        head_pos = (0.5, 0.42)
        head_scale = 0.5
        composite = False
        if self.character_dir:
            bg_dir = os.path.join(self.character_dir, "backgrounds")
            for ext in (".jpg", ".png"):
                p = os.path.join(bg_dir, segment + ext)
                if os.path.isfile(p):
                    bg = self._load_image(p)
                    if bg is not None:
                        bg = cv2.resize(bg, (self.canvas_w, self.canvas_h))
                        composite = True
                    break
            geo = os.path.join(bg_dir, segment + ".json")
            if os.path.isfile(geo):
                try:
                    with open(geo, encoding="utf-8") as f:
                        g = json.load(f)
                    pos = g.get("head_position") or []
                    if len(pos) == 2:
                        head_pos = (float(pos[0]), float(pos[1]))
                    head_scale = float(g.get("head_scale", head_scale))
                except (OSError, ValueError):
                    pass  # 几何文件损坏→默认几何（合成不因辅助文件失败而停）
        cfg = (bg, head_pos, head_scale, composite)
        self._seg_cache[segment] = cfg
        return cfg

    # ------------------------------------------------------------------
    # 逐帧合成（RenderLoop 两出帧口统一改道——R-eng11①）
    # ------------------------------------------------------------------

    def apply(self, frame: np.ndarray, segment: str = None) -> np.ndarray:
        """出帧统一合成入口。返回画布尺寸 BGR 帧（或原帧——直通路径）。"""
        if not self.enabled:
            return frame
        if frame is not None and frame.shape[0] == self.canvas_h \
                and frame.shape[1] == self.canvas_w:
            self.passthrough_count += 1
            return frame  # 原生直通：帧已为画布尺寸（零成本，R-ceo9 唯一豁免）
        try:
            bg, head_pos, head_scale, composite = self._seg_config(segment or "")
            # 背景打底（专用画布缓冲——禁碰预加载 orifra；R-eng11②）
            base = bg if bg is not None else self._default_bg
            if base is not None:
                np.copyto(self._canvas_bgr, base)
            else:
                self._canvas_bgr[:] = (32, 24, 24)  # 深色默认背景（非黑——R-ceo9）
            if composite and frame is not None:
                # 头部 ROI 贴帧：源帧缩放到 head_scale×画布短边，中心对齐 head_position
                side = int(min(self.canvas_w, self.canvas_h)
                           * max(0.1, min(1.0, head_scale)))
                roi = cv2.resize(frame, (side, side), interpolation=cv2.INTER_AREA)
                cx = int(self.canvas_w * max(0.0, min(1.0, head_pos[0])))
                cy = int(self.canvas_h * max(0.0, min(1.0, head_pos[1])))
                x1 = max(0, min(self.canvas_w - side, cx - side // 2))
                y1 = max(0, min(self.canvas_h - side, cy - side // 2))
                self._canvas_bgr[y1:y1 + side, x1:x1 + side] = roi
            elif frame is not None:
                # contain 适配（F-M3：全帧素材缺省——整帧可见，背景填充）
                fh, fw = frame.shape[:2]
                scale = min(self.canvas_w / fw, self.canvas_h / fh)
                nw, nh = max(1, int(fw * scale)), max(1, int(fh * scale))
                fitted = cv2.resize(frame, (nw, nh), interpolation=cv2.INTER_AREA)
                x1 = (self.canvas_w - nw) // 2
                y1 = (self.canvas_h - nh) // 2
                self._canvas_bgr[y1:y1 + nh, x1:x1 + nw] = fitted
            self.applied_count += 1
            self.consecutive_errors = 0
            # 出帧=预分配缓冲的拷贝（FrameBuffer 存引用——同一缓冲逐帧重写会撕裂
            # 显示帧；既有 blend 路径因预加载槽回绕周期极长而侥幸稳定，合成缓冲
            # 每帧都变，必须按值出帧。拷贝 2.7MB@720p ≈ 0.5ms，远低于帧预算）
            return self._canvas_bgr.copy()
        except Exception as e:
            # R-eng11④：合成异常降级直出+计数；连续异常自动旁路（熔断形态）
            self.error_count += 1
            self.consecutive_errors += 1
            if self.consecutive_errors == 1:
                import traceback
                print(f"[Canvas] 合成失败（降级直出）: {e}")
                traceback.print_exc()
            if self.consecutive_errors >= self.MAX_CONSECUTIVE_ERRORS:
                self.enabled = False
                print(f"[Canvas] 连续 {self.consecutive_errors} 帧合成失败——"
                      f"自动旁路（降级直出；/status composite.enabled=false 可观测）")
            return frame

    def stats(self) -> dict:
        """/status composite 计数（R-eng11④ 可观测面）。"""
        return {"enabled": self.enabled, "canvas": [self.canvas_w, self.canvas_h],
                "applied": self.applied_count, "passthrough": self.passthrough_count,
                "errors": self.error_count,
                "auto_bypassed": not self.enabled and self.error_count > 0}
