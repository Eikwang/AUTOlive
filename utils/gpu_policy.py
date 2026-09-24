# -*- coding: utf-8 -*-
"""
GPU 使用优先级策略（P2-3/P3-3 共用，E-2：单一模块，消费方只调用不实现）

探测条件 = 直播运行中（含 EDTalk 推理）OR EDTalk 训练占用中 OR 显存余量 < 阈值。
命中 → 训练拒绝启动错峰 / 翻唱排队等待（两种处置为消费方有意设计）。

信号源：
- 直播运行中：main.py 的运行状态经 set_live_flag() 注入（避免拉起全依赖链）
- EDTalk 占用：探测 8000 端口（EDTalk 服务端口，复用既有探测约定）
- 显存余量：nvidia-smi 查询（torch.cuda 在消费方进程内未必可用）
"""
import socket
from typing import Tuple

from utils.my_log import logger

_edtalk_port = 8000
_live_flag_fn = None  # main.py 注入的可调用


def set_live_flag_source(fn):
    """main.py 启动时注入直播运行状态查询（返回 bool）。"""
    global _live_flag_fn
    _live_flag_fn = fn


def _live_running() -> bool:
    try:
        return bool(_live_flag_fn()) if _live_flag_fn else False
    except Exception:
        return False


def _edtalk_busy() -> bool:
    """EDTalk 服务端口探活（推理/训练共用 8000）。"""
    try:
        with socket.create_connection(("127.0.0.1", _edtalk_port), timeout=1):
            return True
    except OSError:
        return False


def _vram_free_gb() -> float:
    """空闲显存（GB）。查询失败返回大数（不误拒）。"""
    try:
        import subprocess
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        ).stdout.strip().splitlines()
        return float(out[0]) / 1024.0 if out else 999.0
    except Exception as e:
        logger.warning(f"[gpu-policy] 显存查询失败（放行）: {e}")
        return 999.0


def is_background_task_allowed(vram_reserve_gb: float = 4.0) -> Tuple[bool, str]:
    """背景任务（音频训练/翻唱）准入判定。返回 (允许, 原因)。"""
    if _live_running():
        return False, "直播运行中"
    if _edtalk_busy():
        return False, "EDTalk 占用中（完成后可重试）"
    free = _vram_free_gb()
    if free < vram_reserve_gb:
        return False, f"显存余量 {free:.1f}GB 低于阈值 {vram_reserve_gb}GB"
    return True, "GPU 空闲"
