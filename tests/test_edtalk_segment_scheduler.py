# -*- coding: utf-8 -*-
"""EDTalk SegmentScheduler 循环方式单测（2026-10-27 动作编排完善批次）。

覆盖 /segment/schedule loop 参数落地的纯逻辑：
- forward=顺序循环（既有语义回归守卫）；
- pingpong=首尾往返（端点不重复：…2→1→0→1→2…，动作编排默认画面单段模式）；
- 单元素序列 pingpong 退化为顺序循环（单段默认画面=首尾循环等价）；
- 值域外 loop 回退 forward（服务端构造器防御）。

render_loop 顶层 import torch/cv2——沿 test_rvc_rc_integration 惯例 importorskip。
"""
import os
import sys

import pytest

pytest.importorskip("torch")
pytest.importorskip("cv2")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from edtalk.realtime_serve.render_loop import SegmentScheduler  # noqa: E402


class TestSegmentSchedulerLoop:
    def test_forward_sequential(self):
        s = SegmentScheduler(["A", "B", "C"], loop="forward")
        assert [s.advance() for _ in range(5)] == ["B", "C", "A", "B", "C"]

    def test_pingpong_roundtrip(self):
        s = SegmentScheduler(["A", "B", "C"], loop="pingpong")
        # 首尾往返：0→1→2→1→0→1→2→1→0（端点不重复播放）
        assert [s.advance() for _ in range(8)] == [
            "B", "C", "B", "A", "B", "C", "B", "A"]

    def test_pingpong_single_element_degrades_to_forward(self):
        s = SegmentScheduler(["A"], loop="pingpong")
        assert [s.advance() for _ in range(3)] == ["A", "A", "A"]

    def test_invalid_loop_falls_back_forward(self):
        s = SegmentScheduler(["A", "B"], loop="whatever")
        assert s.loop == "forward"
        assert [s.advance() for _ in range(3)] == ["B", "A", "B"]

    def test_sync_to_keeps_direction_guarded(self):
        """手动切换对齐后 pingpong 继续可用（sync_to 不破坏状态机）。"""
        s = SegmentScheduler(["A", "B", "C"], loop="pingpong")
        s.advance()  # → B
        s.sync_to("A")
        assert s.advance() == "B"
