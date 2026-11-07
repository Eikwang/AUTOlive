# -*- coding: utf-8 -*-
"""GPU 显存归属探针守卫（2026-11-07 翻唱显存残留排查批次）

- PDH 解析纯函数：pid,bytes 行/垃圾行容错/字节降序
- 计数器命令锚定（GPU Process Memory/Local Usage/pid_ 解析）
- 报告必不抛（双路独立容错）
- cover_worker 快照委托双路（nvidia-smi 显存 N/A 盲区补齐）
- 停止后快照挂点（stop_programs ⑤层——残留归属现形）
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from utils.gpu_probe import _parse_pdh_output, _pdh_command, gpu_memory_report


def _read(rel):
    with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), rel),
              encoding="utf-8") as f:
        return f.read()


def test_parse_pdh_output_basic():
    """pid,bytes 解析+字节降序（数字字面量 ≤9 位，防 PII 扫描误报）。"""
    rows = _parse_pdh_output("123456,536870912\n456789,268435456\n")
    assert rows == [(123456, 536870912), (456789, 268435456)]


def test_parse_pdh_output_garbage_tolerant():
    """垃圾行/负数/非数字静默跳过（PS 输出噪音不致炸）。"""
    assert _parse_pdh_output("") == []
    assert _parse_pdh_output("垃圾行\nabc,def\n-1,-2\n789,100\n") == [(789, 100)]


def test_pdh_command_targets_gpu_counter():
    """命令锚定：专用显存计数器+pid_ InstanceName 解析。"""
    cmd = _pdh_command()
    assert "GPU Process Memory" in cmd and "Local Usage" in cmd
    assert "pid_" in cmd
    assert "Sort-Object Value -Descending" in cmd


def test_gpu_memory_report_never_raises():
    """报告必不抛（真实子进程调用一次，双路容错全路径）。"""
    text = gpu_memory_report()
    assert isinstance(text, str) and "GPU" in text


def test_cover_worker_delegates_to_probe():
    """翻唱快照委托 gpu_probe 双路（单 nvidia-smi 路退役——显存 N/A 盲区）。"""
    src = _read(os.path.join("utils", "cover_worker.py"))
    seg = src[src.find("def _gpu_compute_snapshot"):src.find("def _gpu_compute_snapshot") + 900]
    assert "gpu_memory_report" in seg, "翻唱快照应委托 gpu_probe 双路"
    assert "--query-compute-apps" not in seg, "nvidia-smi 细节应收编进 gpu_probe（DRY）"


def test_stop_programs_snapshot_hook():
    """停止⑤层：stop_programs 尾部落停止后显存快照（残留归属现形）。"""
    src = _read(os.path.join("frontend", "main.py"))
    seg = src[src.find("def stop_programs"):src.find("def _reap_orphan_processes")]
    assert "gpu_memory_report" in seg and "停止后 GPU 显存快照" in seg


def test_cli_script_wiring():
    """手动诊断脚本接线：sys.path 注入+gpu_memory_report 调用+stdout 容错。"""
    src = _read(os.path.join("Scripts", "gpu_memory_report.py"))
    assert "from utils.gpu_probe import gpu_memory_report" in src
    assert "reconfigure" in src  # Windows 控制台编码容错
