# -*- coding: utf-8 -*-
"""统一画布合成层守卫（2026-10-27 画面输出批次，R-ceo9/R-eng11③④）。

覆盖：
- 回绕回归（R-eng11③ 必选）：apply 不得修改输入帧（预加载 orifra pristine
  纪律——GAZE_TIMING_PLAN T1/X1 同类漂移防复发）；同帧重复合成结果一致
- 统一画布政策：输出恒画布尺寸；canvas 尺寸帧零成本直通；旁路开关直通
- 段背景契约：backgrounds/<seg>.jpg 存在=ROI 合成；缺失=contain 适配；
  几何 json 覆盖生效
- 自动旁路（R-eng11④）：连续异常≥阈值→enabled=False 降级直出+计数
- 计数回显 stats()

环境注记：合成器用 cv2（EDTalk runtime 解释器环境）；AUTOlive 主环境无 cv2
时本测试整类 skip（合成器单元验证在 runtime 环境补跑——python -m pytest
以 runtime312 解释器执行）。
"""
import os

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2", reason="合成器依赖 cv2（runtime 解释器环境）")

from edtalk.realtime_serve.canvas_compositor import CanvasCompositor


@pytest.fixture()
def frame_256():
    """256×256 源帧（wy 类特写素材量级），内容可识别（角标色块）。"""
    img = np.zeros((256, 256, 3), dtype=np.uint8)
    img[:, :, 0] = 200  # B 通道填充
    img[10:20, 10:20] = (1, 2, 3)  # 指纹角标
    return img


@pytest.fixture()
def comp(tmp_path):
    return CanvasCompositor(1280, 720, character_dir=str(tmp_path))


class TestWrapAroundRegression:
    """R-eng11③ 必选：预加载 orifra pristine 纪律（回绕漂移防复发）。"""

    def test_apply_does_not_mutate_input_frame(self, comp, frame_256):
        """合成前后输入帧逐字节不变——合成只写专用画布缓冲。"""
        snapshot = frame_256.copy()
        for _ in range(3):  # 模拟回绕：同帧反复出画
            comp.apply(frame_256, "segA")
        assert np.array_equal(frame_256, snapshot), \
            "输入帧被合成层修改（pristine 纪律破洞——回绕漂移复发形态）"

    def test_repeated_apply_deterministic(self, comp, frame_256):
        """同帧重复合成输出一致（画布缓冲重写不携带上帧残影）。"""
        out1 = comp.apply(frame_256, "segA").copy()
        out2 = comp.apply(frame_256, "segA")
        assert np.array_equal(out1, out2)

    def test_output_frames_independent(self, comp, frame_256):
        """相邻两帧输出互不串扰（拷贝出帧——FrameBuffer 引用语义防撕裂）。"""
        f1 = frame_256.copy()
        out1 = comp.apply(f1, "segA")
        f2 = frame_256.copy()
        f2[:, :, 1] = 255  # 第二帧内容不同
        out2 = comp.apply(f2, "segA")
        assert not np.array_equal(out1, out2)


class TestUnifiedCanvasPolicy:
    """R-ceo9：输出恒画布尺寸；直通豁免仅限画布尺寸帧。"""

    def test_output_is_canvas_sized(self, comp, frame_256):
        out = comp.apply(frame_256, "segA")
        assert out.shape == (720, 1280, 3)

    def test_canvas_sized_frame_passthrough(self, comp):
        """画布尺寸帧=零成本直通（R-ceo9 唯一豁免）。"""
        canvas_frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        out = comp.apply(canvas_frame, "segA")
        assert out is canvas_frame  # 同对象直返（无拷贝）
        assert comp.passthrough_count == 1

    def test_disabled_bypasses(self, frame_256):
        comp = CanvasCompositor(1280, 720, enabled=False)
        out = comp.apply(frame_256, "segA")
        assert out is frame_256

    def test_invalid_canvas_rejected(self):
        with pytest.raises(ValueError):
            CanvasCompositor(32, 32)


class TestSegmentBackgroundContract:
    """R-eng2 F-H3：backgrounds/<seg> 文件=合成 opt-in 信号。"""

    def test_background_file_enables_roi_composite(self, comp, frame_256, tmp_path):
        bg_dir = tmp_path / "backgrounds"
        bg_dir.mkdir()
        bg = np.zeros((1280, 720, 3), dtype=np.uint8)
        bg[:, :, 2] = 180  # 红底（BGR R 通道）
        cv2.imwrite(str(bg_dir / "segA.jpg"), bg)
        out = comp.apply(frame_256, "segA")
        assert out.shape == (720, 1280, 3)
        # ROI 落点中心（0.5, 0.42）附近应有源帧内容（B 通道高）
        cy, cx = int(720 * 0.42), int(1280 * 0.5)
        assert out[cy, cx, 0] > 100  # 头部贴帧区非纯背景

    def test_missing_background_contain_fit(self, comp, frame_256):
        """无背景文件=contain 适配（F-M3 缺省：整帧可见，深色底填充）。"""
        out = comp.apply(frame_256, "segNoBg")
        assert out.shape == (720, 1280, 3)
        # contain：256 方帧按短边适配→居中 720×720 区域有内容，左右为底色
        assert out[360, 640, 0] > 100  # 中心=源帧
        assert out[360, 10, 0] < 100   # 左缘=底色

    def test_geometry_json_overrides(self, comp, frame_256, tmp_path):
        """几何 json 覆盖生效（head_position/head_scale）。"""
        import json
        bg_dir = tmp_path / "backgrounds"
        bg_dir.mkdir()
        cv2.imwrite(str(bg_dir / "segA.jpg"),
                    np.zeros((1280, 720, 3), dtype=np.uint8))
        (bg_dir / "segA.json").write_text(
            json.dumps({"head_position": [0.1, 0.1], "head_scale": 0.2}),
            encoding="utf-8")
        out = comp.apply(frame_256, "segA")
        # 头部落点左上角（0.1, 0.1）→ 该处有源帧内容
        cy, cx = int(720 * 0.1), int(1280 * 0.1)
        assert out[cy, cx, 0] > 100


class TestAutoBypass:
    """R-eng11④：连续异常自动旁路+计数。"""

    def test_consecutive_errors_disable(self, tmp_path):
        comp = CanvasCompositor(1280, 720, character_dir=str(tmp_path))
        calls = {"n": 0}

        def _boom(frame, seg):
            calls["n"] += 1
            raise RuntimeError("注入异常")

        comp._seg_config = _boom  # 注入失败源
        frame = np.zeros((256, 256, 3), dtype=np.uint8)
        for _ in range(comp.MAX_CONSECUTIVE_ERRORS + 1):
            out = comp.apply(frame, "segX")
            assert out is frame  # 降级直出（原帧返回）
        assert comp.enabled is False  # 自动旁路
        assert comp.error_count >= comp.MAX_CONSECUTIVE_ERRORS
        st = comp.stats()
        assert st["auto_bypassed"] is True
        # 旁路后 apply 恒直通（不再进异常路径）
        assert comp.apply(frame, "segX") is frame

    def test_error_counter_resets_on_success(self, comp, frame_256):
        comp.error_count = 5
        comp.consecutive_errors = 5
        comp.apply(frame_256, "segA")
        assert comp.consecutive_errors == 0
        assert comp.error_count == 5  # 累计错误保留（/status 观测）


class TestStats:
    def test_stats_shape(self, comp, frame_256):
        comp.apply(frame_256, "segA")
        st = comp.stats()
        assert st["enabled"] is True
        assert st["canvas"] == [1280, 720]
        assert st["applied"] >= 1
        assert "errors" in st and "passthrough" in st
