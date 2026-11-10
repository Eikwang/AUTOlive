# -*- coding: UTF-8 -*-
"""
GPU 专用显存归属探针（2026-11-07 翻唱显存残留排查批次）

背景：用户翻唱后见 1.5G 显存残留；翻唱前/后 nvidia-smi compute 快照
（cover_worker 诊断批）证明翻唱进程退出即清零——但快照 used_memory 列
全为 [N/A]（Windows WDDM 下 nvidia-smi per-process 显存的已知缺陷），
"1.5G 归属哪个进程"在 nvidia-smi 路永远现不了形。

本模块双路取数：
- PDH 路（主）：Get-Counter '\\GPU Process Memory(*)\\Local Usage'——
  任务管理器「专用 GPU 内存」同源数据，不受 nvidia-smi 权限限制，
  PID+字节直接可得；InstanceName 的 pid_ 段解析后进程名经 psutil 映射。
- nvidia-smi 路（辅）：compute 进程 PID/名清单，作归属印证（显存列
  Windows 常为 N/A，仅用于确认进程在 compute 上下文）。

消费方：
- Scripts/gpu_memory_report.py 手动诊断（模块排除法：冷基线→逐模块激活）
- cover_worker._gpu_compute_snapshot（翻唱前/后快照，升级为双路）
- frontend/main.stop_programs 尾部（停止后快照——残留归属现形）
"""
import subprocess
import time
from typing import List, Optional, Tuple


def _pdh_command() -> str:
    """PowerShell 聚合命令：GPU 专用显存（Local Usage=dedicated）按 pid 求和，
    输出 'pid,bytes' 行（降序）。进程名映射放 python 侧（psutil），
    避免 PS 逐 pid try/catch 与管道编码问题。"""
    return (
        "$ErrorActionPreference='Stop';"
        "$s=(Get-Counter '\\GPU Process Memory(*)\\Local Usage').CounterSamples"
        "| Where-Object {$_.CookedValue -gt 0};"
        "$h=@{};"
        "foreach($x in $s){if($x.InstanceName -match 'pid_(\\d+)_'){"
        "$p=[int]$Matches[1];$h[$p]=[int64]$h[$p]+[int64]$x.CookedValue}};"
        "$h.GetEnumerator()|Sort-Object Value -Descending|"
        "ForEach-Object{'{0},{1}' -f $_.Key,$_.Value}"
    )


def _parse_pdh_output(text: str) -> List[Tuple[int, int]]:
    """解析 'pid,bytes' 行 → [(pid, bytes)] 字节降序；垃圾行静默跳过。"""
    rows: List[Tuple[int, int]] = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or "," not in line:
            continue
        pid_s, _, bytes_s = line.partition(",")
        pid_s = pid_s.strip()
        bytes_s = bytes_s.strip()
        if not pid_s.isdigit() or not bytes_s.isdigit():
            continue
        rows.append((int(pid_s), int(bytes_s)))
    rows.sort(key=lambda t: t[1], reverse=True)
    return rows


def _proc_name(pid: int) -> str:
    """pid → 进程名（psutil；退出/权限不足 → '?'）。"""
    try:
        import psutil
        return psutil.Process(pid).name()
    except Exception:
        return "?"


def pdh_memory_pids() -> Optional[List[Tuple[int, int]]]:
    """PDH 路 raw rows：[(pid, 专用显存字节)] 字节降序。

    Returns:
        rows 列表（可能为空=可用且无占用）；**None=PDH 路不可用**
        （区别于空列表，调用方可区分降级路径）。
    """
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command", _pdh_command()],
            capture_output=True, encoding="utf-8", errors="replace", timeout=40)
    except Exception:
        return None
    return _parse_pdh_output(r.stdout or "")


def pdh_memory_lines(top_n: int = 12) -> Tuple[List[str], bool]:
    """PDH 路：(报告行列表, 是否可用)。失败不抛——返回标注行。"""
    rows = pdh_memory_pids()
    if rows is None:
        return ["  （PDH 路不可用——GPU 计数器读取失败）"], False
    if not rows:
        return ["  （无 GPU 专用显存占用进程）"], True
    total_mb = sum(n for _, n in rows) / (1024 * 1024)
    lines = []
    for pid, nbytes in rows[:top_n]:
        mb = nbytes / (1024 * 1024)
        lines.append(f"  PID {pid:<7} {_proc_name(pid):<22} {mb:9.1f} MB")
    if len(rows) > top_n:
        lines.append(f"  （其余 {len(rows) - top_n} 个进程略）")
    lines.append(f"  合计 {total_mb:.0f} MB（{len(rows)} 个进程）")
    return lines, True


def nvidia_smi_lines() -> List[str]:
    """nvidia-smi compute 进程清单（归属印证；显存列 Windows 常 N/A）。"""
    try:
        r = subprocess.run(
            ["nvidia-smi", "--query-compute-apps=pid,used_memory,process_name",
             "--format=csv,noheader"], capture_output=True, text=True, timeout=10)
        out = (r.stdout or "").strip()
        if not out:
            return ["  （无 compute 进程）"]
        return ["  " + ln.strip() for ln in out.splitlines() if ln.strip()]
    except Exception as e:
        return [f"  （nvidia-smi 不可用: {e}）"]


def gpu_memory_report(top_n: int = 12) -> str:
    """双路 GPU 显存归属报告（多行文本；必不抛——各路独立容错）。"""
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    pdh_lines, ok = pdh_memory_lines(top_n)
    head = ("=== GPU 专用显存归属（PDH 路/任务管理器同源）==="
            if ok else "=== GPU 专用显存归属（PDH 路不可用）===")
    return "\n".join([
        f"{head} {ts}",
        *pdh_lines,
        "--- nvidia-smi compute 进程（显存列 Windows 常 N/A，归属印证）---",
        *nvidia_smi_lines(),
    ])
