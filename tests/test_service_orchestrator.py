# -*- coding: UTF-8 -*-
"""
P1-6 单元测试：ManagedProcess 托管生命周期（P1-1）

覆盖（对应批准计划验收）：
- 拉起/探活/健康就绪（happy path）
- 子进程意外退出 → 自动重拉 → 恢复健康（重拉计数清零）
- 失控防护：连续失败 ≥ 上限 → red 态停拉；手动重启清零回健康（CEO-D10/T-3）
- 端口占用归因（DX-1/B-4）：外部进程 → PortConflictError 三要素文案
- 健康检查超时与拉起失败共用计数器（T-3）
- E-1：命令行含 0.0.0.0 绑定 → 构造即拒绝

测试用 dummy HTTP 服务（python -m http.server）代替真实 api_v2，秒级完成。
"""
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from utils.service_orchestrator import (  # noqa: E402
    PortConflictError,
    ManagedProcess,
    ST_CRASHED,
    ST_HEALTHY,
    ST_RED,
    ST_STOPPED,
)

FAST_POLICIES = {
    "health_poll_interval_s": 1,
    "health_timeout_s": 2,
    "start_ready_timeout_s": 15,
    "crash_loop_max": 3,
}


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _http_service(port: int, name: str = "test-svc", **kw) -> ManagedProcess:
    """以 python -m http.server 作为被托管 dummy 服务（GET / 即健康）。"""
    return ManagedProcess(
        name=name,
        command=[sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1"],
        cwd=tempfile.gettempdir(),
        health_url=f"http://127.0.0.1:{port}/",
        port=port,
        policies={**FAST_POLICIES, **kw.get("policies", {})},
        **{k: v for k, v in kw.items() if k != "policies"},
    )


def test_e1_rejects_non_loopback_bind():
    """E-1 硬条款：命令含 0.0.0.0 绑定 → 构造即 ValueError。"""
    with pytest.raises(ValueError, match="E-1"):
        ManagedProcess(
            name="bad",
            command=[sys.executable, "-m", "http.server", "--bind", "0.0.0.0", "8000"],
            cwd=tempfile.gettempdir(),
            health_url="http://127.0.0.1:8000/",
            port=8000,
        )


def test_start_healthy_and_stop():
    """拉起 → 健康就绪 → 优雅停止回 stopped。"""
    port = _free_port()
    svc = _http_service(port)
    assert svc.start() is True
    assert svc.state == ST_HEALTHY
    assert svc.fail_count == 0
    svc.stop()
    assert svc.state == ST_STOPPED


def test_crash_auto_restart_recovers():
    """子进程被杀 → 自动重拉 → 恢复健康且计数清零（P1-3 验收）。"""
    port = _free_port()
    svc = _http_service(port)
    assert svc.start() is True
    assert svc.state == ST_HEALTHY

    # 杀掉子进程，模拟崩溃
    svc.proc.kill()
    svc.proc.wait(timeout=5)
    # 等监控线程完成自动重拉（轮询 1s + 重拉就绪窗口）
    deadline = time.time() + 20
    while time.time() < deadline:
        if svc.state == ST_HEALTHY and svc.proc and svc.proc.poll() is None:
            break
        time.sleep(0.5)
    assert svc.state == ST_HEALTHY, f"自动重拉后未恢复健康，当前: {svc.state}"
    assert svc.proc is not None and svc.proc.poll() is None
    assert svc.fail_count == 0  # 重拉成功清零（T-3 共用计数器语义）
    svc.stop()


def test_crash_loop_escalates_to_red_and_manual_restart_recovers():
    """连续失败 3 次 → red 停拉；手动重启换好命令 → 清零回健康（D10/T-3）。"""
    port = _free_port()
    # 必然立即退出的命令（失败路径）
    svc = _http_service(port, name="crash-loop-svc")
    svc.command = [sys.executable, "-c", "import sys; sys.exit(1)"]

    states_seen = []
    svc.on_state_change = lambda s, _: states_seen.append(s)

    assert svc.start() is False
    assert svc.start() is False
    assert svc.start() is False
    assert svc.state == ST_RED, "连续 3 次失败后应为 red 态"
    assert ST_RED in states_seen

    # red 态下 start() 拒绝自动拉起
    assert svc.start() is False
    assert svc.state == ST_RED

    # 手动重启：换回好命令，清零回健康（D10）
    port2 = _free_port()
    svc.command = [sys.executable, "-m", "http.server", str(port2), "--bind", "127.0.0.1"]
    svc.port = port2
    svc.health_url = f"http://127.0.0.1:{port2}/"
    assert svc.manual_restart() is True
    assert svc.state == ST_HEALTHY
    assert svc.fail_count == 0
    svc.stop()


def test_port_conflict_external_process_raises_with_fix_text():
    """端口被外部进程占用 → PortConflictError，文案含 problem/cause/fix 三要素与逃逸口（DX-1）。"""
    port = _free_port()
    blocker = subprocess.Popen(
        [sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1"],
        cwd=tempfile.gettempdir(),
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    try:
        time.sleep(1.5)  # 等 blocker 监听
        svc = _http_service(port, name="conflict-svc")
        assert svc.start() is False
        assert svc.state == ST_CRASHED  # 归因失败/外部占用计入计数（B-4）
        svc.stop()
        # 直接验证异常文案三要素
        occ = svc._find_port_occupier()
        assert occ is not None
        err = PortConflictError(port, occ.pid, occ.name(), own_child=False)
        msg = str(err)
        assert f"端口 {port} 已被占用" in msg       # problem
        assert "外部程序占用" in msg                 # cause
        assert "gpt_sovits.hosted=false" in msg      # fix 直连逃逸口（F11）
    finally:
        blocker.terminate()
        blocker.wait(timeout=5)


def test_port_conflict_own_residual_auto_cleaned():
    """端口被本系统残留子进程占用 → 自动清理后重试拉起成功（B-4 own 分支）。"""
    port = _free_port()
    tmpdir = tempfile.mkdtemp()
    fake_api = os.path.join(tmpdir, "api_v2.py")
    with open(fake_api, "w", encoding="utf-8") as f:
        f.write(
            "import http.server, socketserver\n"
            f"socketserver.TCPServer.allow_reuse_address = True\n"
            f"with socketserver.TCPServer(('127.0.0.1', {port}), http.server.SimpleHTTPRequestHandler) as h:\n"
            "    h.serve_forever()\n"
        )
    # 残留进程：cmdline 含 api_v2.py → 归因为 own
    residual = subprocess.Popen(
        [sys.executable, fake_api],
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    try:
        time.sleep(1.5)
        svc = _http_service(port, name="own-residual-svc")
        assert svc.start() is True, "own 残留进程应被自动清理后拉起成功"
        assert svc.state == ST_HEALTHY
        svc.stop()
    finally:
        residual.terminate()
        try:
            residual.wait(timeout=5)
        except subprocess.TimeoutExpired:
            residual.kill()


def test_health_timeout_counts_same_counter():
    """健康检查超时与拉起失败共用计数器（T-3）：不健康服务计入 fail_count。"""
    port = _free_port()
    svc = _http_service(port, name="timeout-svc")
    svc.health_url = f"http://127.0.0.1:{port}/nonexistent-404-path"
    # http.server 对 404 路径返回 404 → _health_once 视为失败
    assert svc.start() is False  # 就绪窗口内健康检查始终失败
    assert svc.fail_count == 1
    svc.stop()


def test_tail_log_returns_content():
    """日志尾可查询（状态条胶囊展示依赖）。"""
    port = _free_port()
    svc = _http_service(port, name="log-tail-svc")
    assert svc.start() is True
    time.sleep(1)  # 等子进程写日志
    tail = svc.tail_log(10)
    assert isinstance(tail, str)
    svc.stop()
