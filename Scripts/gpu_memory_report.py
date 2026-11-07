# -*- coding: UTF-8 -*-
"""GPU 专用显存归属报告（手动诊断脚本，2026-11-07 显存残留排查批次）。

用法（模块排除法定位显存残留归属）：
    runtime312\\python.exe Scripts\\gpu_memory_report.py

排查流程（每次跑完对比增量）：
1. 冷基线：AUTOlive 全停（前端点「停止」）→ 跑一次 → 系统本底
   （OBS/浏览器/驱动等与本系统无关的占用）；
2. 只启动主系统（不点歌、不变声）→ 跑一次 → 增量 = GSV inproc 常驻；
3. 开变声 → 跑一次 → 增量 = RVC 常驻；
4. 启动 EDTalk → 跑一次 → 增量 = 数字人推理常驻。

前端「停止」操作也会自动落一条 [stop] 停止后 GPU 显存快照（log 内），
与本脚本同数据源（任务管理器「专用 GPU 内存」列）。
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from utils.gpu_probe import gpu_memory_report

if __name__ == "__main__":
    print(gpu_memory_report())
