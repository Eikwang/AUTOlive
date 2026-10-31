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
    """空闲显存（GB）。S-2 fail-closed（/autoplan main-20261031-201249 终门批准批次）：
    查询失败重试一次（间隔 0.5s），再失败返回 -1 由调用方拒绝——训练入场的代价
    远低于误放行 OOM 连带推理进程（v1 审查 S-2 建议：失败模式从"放行"改"拒绝"）。"""
    import time as _time
    last_err = None
    for attempt in range(2):
        try:
            import subprocess
            out = subprocess.run(
                ["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=5,
            ).stdout.strip().splitlines()
            if out:
                return float(out[0]) / 1024.0
            last_err = "nvidia-smi no output"
        except Exception as e:
            last_err = f"{type(e).__name__}: {e}"
            logger.warning(f"[gpu-policy] 显存查询失败（第 {attempt + 1}/2 次）: {e}")
        if attempt == 0:
            _time.sleep(0.5)
    logger.error(f"[gpu-policy] 显存查询最终失败（fail-closed，训练准入将拒绝）: {last_err}")
    return -1.0


def gpu_snapshot() -> dict:
    """GPU 观测快照（T1 观测面补齐包：空闲显存 GB + 利用率%，低频面板/诊断调用）。

    与 _vram_free_gb 的准入语义分离——本函数纯观测，查询失败返回 error 键不兜底大数。"""
    try:
        import subprocess
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.free,utilization.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        ).stdout.strip().splitlines()
        if not out:
            return {"error": "nvidia-smi no output"}
        parts = [x.strip() for x in out[0].split(",")]
        free_gb = (float(parts[0]) / 1024.0) if parts and parts[0] else None
        util = (float(parts[1]) if len(parts) > 1 and parts[1] else None)
        return {"vram_free_gb": free_gb, "util_percent": util}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def is_background_task_allowed(vram_reserve_gb: float = 4.0,
                               inproc_tts_reserve_gb: float = 2.0) -> Tuple[bool, str]:
    """背景任务（音频训练/翻唱）准入判定。返回 (允许, 原因)。

    R-6 升级：inproc_tts_reserve_gb=进程内 TTS 常驻显存的额外预留——训练 OOM 会
    拖死同进程的进程内 TTS（托管子进程时代仅是 TTS 短暂中断），故准入要求
    空闲显存 ≥ vram_reserve_gb + inproc_tts_reserve_gb（引擎就绪时才加）。
    """
    if _live_running():
        return False, "直播运行中"
    if _edtalk_busy():
        return False, "EDTalk 占用中（完成后可重试）"
    free = _vram_free_gb()
    if free < 0:
        # S-2 fail-closed：查询失败拒绝训练入场（原返回 999.0 兜底放行→OOM 连带推理）
        try:
            from utils.alarm_notifier import alarm_error, MODULE_DIGITAL_HUMAN
            alarm_error(MODULE_DIGITAL_HUMAN,
                        "GPU 显存查询失败（nvidia-smi 两次尝试均失败），背景任务准入按 fail-closed 拒绝",
                        fix_hint="检查 nvidia-smi 可用性与驱动状态；显存信息恢复后训练可重试")
        except Exception:
            pass
        return False, "显存查询失败（fail-closed，S-2：不误放行）"
    reserve = vram_reserve_gb
    if _inproc_engine_ready():
        reserve += inproc_tts_reserve_gb
    if free < reserve:
        return False, (f"显存余量 {free:.1f}GB 低于阈值 {reserve:.1f}GB"
                       f"（含进程内 TTS 常驻预留 {inproc_tts_reserve_gb}GB）"
                       "——完成后可重试，或在对话页临时设 backend=api")
    return True, "GPU 空闲"


def _inproc_engine_ready() -> bool:
    """进程内 TTS 引擎是否就绪（仅 main.py 核心进程有引擎；其他进程恒 False）。"""
    try:
        from utils.gpt_sovits_inproc import get_engine
        eng = get_engine()
        return bool(eng and eng.state == "ready")
    except Exception:
        return False
