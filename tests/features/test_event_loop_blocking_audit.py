# -*- coding: utf-8 -*-
"""前端事件循环阻塞审查守卫（2026-11-09 断联审查批次）

机制（audio_play 2026-11-06/训练主播 2026-11-09 两度实报）：NiceGUI 3.16
socket.io ping_timeout=2.0s（reconnect_timeout=3.0 推导）——事件循环上任何
>2s 同步阻塞（同步 HTTP/subprocess/磁盘扫描/torch import）即 WS 判死→
断联→浏览器重载回初始页。

守卫清单（发现→修复→防回归）：
- frontend/main.py：tab 模块后台预热（train_streamer torch 链首次进页同步加载 5.19s）
- llm.py 模板应用：wmic 停服（timeout=15s）+health 探测（timeout=2）原在
  事件循环同步执行 → asyncio.to_thread
- train_voice._poll_gpu：nvidia-smi 准入探测（卡顿可达秒级）→ run.io_bound
"""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _src(rel: str) -> str:
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


def test_tab_modules_prewarmed_at_startup():
    """tab 模块启动期后台预热（torch 链 import 不在进页路径上）。"""
    src = _src("frontend/main.py")
    assert "_prewarm_tab_modules" in src
    assert "tab-prewarm" in src, "预热线程应挂启动路径（daemon）"


def test_llm_template_apply_blocking_off_loop():
    """llm 模板应用：wmic 停服+health 探测走 to_thread（async 回调）。"""
    src = _src("frontend/ui/tabs/llm.py")
    assert "async def _apply_template_and_restart" in src
    assert "asyncio.to_thread(_blocking_restart)" in src
    assert "import asyncio" in src


def test_train_voice_gpu_poll_off_loop():
    """train_voice GPU 准入轮询：nvidia-smi 探测走 run.io_bound。"""
    src = _src("frontend/ui/tabs/train_voice.py")
    assert "async def _poll_gpu" in src
    assert "await run.io_bound(" in src
    assert "from nicegui import run, ui" in src
