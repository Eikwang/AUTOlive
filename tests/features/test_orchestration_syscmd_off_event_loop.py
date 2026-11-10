# -*- coding: utf-8 -*-
"""遗留债收编守卫：/sys_cmd 与 /choreography/*（2026-11-11 断联根修第二批次）。

背景：webui 断联根修第一批次（b2b78eca）收编了 /edtalk_materials*、
/cover/* 与 main.py 三转发 handler——本文件补收同源类遗留债：

- /sys_cmd（webui 进程）：四命令编排体（start_programs/stop_programs
  进程树拉起与收尸秒级 + restart 的 os.execl）原在 async handler 同步
  执行冻结 webui 循环 → 收编 _sys_cmd_dispatch + to_thread；UI 侧
  layout.py 三启停 handler（_execute_start/_do_stop/_do_restart）同构收编。
- /choreography/*（主进程 uvicorn 8082）：get_state 状态锁、apply_config
  的 EDTalk 同步探测+规则重算+config.json 原子改写、simulate_trigger 与
  worker 互锁——主 API 单事件循环同样会被冻结（编排页轮询/应用期间全体
  API 变慢）→ 三 handler 阻塞体 to_thread。

守卫四层（沿 test_webui_routes_off_event_loop.py 模式）：
1. off-loop 线程 ident 断言（orchestration 三 handler 阻塞体在工作线程）。
2. 事件循环心跳断言（get_state 阻塞 2s 期间 max_gap<1.0s）。
3. AST 源码守卫——main.py api_sys_cmd 不得直接调用四命令方法（收编
   _sys_cmd_dispatch）；layout.py 三 handler 必须走 to_thread。
4. 文本守卫——run_external_program 训练拦截 notify 必须通道分派
   （api 通道经 to_thread 在工作线程执行，ui.notify 无 client 上下文必抛）。

纪律：全部 monkeypatch 桩，不触盘、不写 config.json。
"""
import ast
import asyncio
import threading
import time
from pathlib import Path

import pytest
from fastapi import FastAPI

from utils.orchestration import api as choreo_api

REPO = Path(__file__).resolve().parents[2]
MAIN_PY = REPO / "frontend" / "main.py"
LAYOUT_PY = REPO / "frontend" / "ui" / "layout.py"


class _StubChoreographer:
    """get_state/apply_config/simulate_trigger 记录执行线程 ident 的桩。"""

    def __init__(self):
        self.idents = {}

    def get_state(self):
        self.idents["get_state"] = threading.get_ident()
        return {"state": "running"}

    def apply_config(self, node):
        self.idents["apply_config"] = threading.get_ident()
        return {"applied": True}

    def simulate_trigger(self, payload):
        self.idents["simulate_trigger"] = threading.get_ident()
        return {"accepted": False, "reason": "disabled"}


@pytest.fixture(autouse=True)
def _isolate_config_path(tmp_path, monkeypatch):
    """结构级 config 隔离（choreo-config-writer 守卫要求，参照
    test_choreography_completion.py:406）：register_choreography_routes 的
    rules applied 分支会写 _config_path()（默认=cwd 相对 config.json）——
    本文件 rules 测试虽已 monkeypatch _write_choreography_section 真身，
    仍重定向 _config_path 到 tmp，防未来改动让真身触达生产 config.json。
    """
    monkeypatch.setattr(choreo_api, "_config_path",
                        lambda: str(tmp_path / "config.json"))


def _choreo_endpoints(monkeypatch, stub) -> dict:
    monkeypatch.setattr(choreo_api, "_choreographer", lambda: stub)
    app = FastAPI()
    choreo_api.register_choreography_routes(app, {"choreography": {"rules": []}})
    return {getattr(r, "path", ""): getattr(r, "endpoint", None)
            for r in app.routes
            if str(getattr(r, "path", "")).startswith("/choreography/")}


# ======================================================================
# 1) off-loop 线程 ident 断言
# ======================================================================

def test_choreo_status_off_loop(monkeypatch):
    stub = _StubChoreographer()
    eps = _choreo_endpoints(monkeypatch, stub)
    loop_ident = {}
    asyncio.run(_record_loop_ident(loop_ident, eps["/choreography/status"]))
    assert stub.idents.get("get_state") not in (None, loop_ident["ident"]), (
        "ch.get_state 在事件循环线程上同步执行")


def test_choreo_rules_off_loop(monkeypatch):
    stub = _StubChoreographer()
    eps = _choreo_endpoints(monkeypatch, stub)
    write_seen = {}
    monkeypatch.setattr(
        choreo_api, "_write_choreography_section",
        lambda node: write_seen.update({"ident": threading.get_ident()}) or True)
    loop_ident = {}
    out = asyncio.run(_record_loop_ident(
        loop_ident, eps["/choreography/rules"],
        request=None, node={"rules": []}))
    assert out.get("persisted") is True and out.get("success") is True
    assert stub.idents.get("apply_config") not in (None, loop_ident["ident"]), (
        "ch.apply_config（EDTalk 同步探测+规则重算）在事件循环线程上执行")
    assert write_seen.get("ident") not in (None, loop_ident["ident"]), (
        "_write_choreography_section（config.json 原子改写）在事件循环线程上执行")


def test_choreo_trigger_off_loop(monkeypatch):
    stub = _StubChoreographer()
    eps = _choreo_endpoints(monkeypatch, stub)
    loop_ident = {}
    asyncio.run(_record_loop_ident(
        loop_ident, eps["/choreography/trigger"],
        request=None, payload={"rule_id": "r1"}))
    assert stub.idents.get("simulate_trigger") not in (None, loop_ident["ident"]), (
        "ch.simulate_trigger（与 worker 互锁）在事件循环线程上执行")


async def _record_loop_ident(loop_ident, ep, **kwargs):
    loop_ident["ident"] = threading.get_ident()
    return await ep(**kwargs)


# ======================================================================
# 2) 事件循环心跳断言（真实故障模式编码）
# ======================================================================

def test_main_api_loop_stays_responsive_during_choreo_status(monkeypatch):
    stub = _StubChoreographer()

    def _slow_state():
        time.sleep(2.0)  # 模拟 worker 持锁/EDTalk 探测期间的状态等待
        return {"state": "running"}

    stub.get_state = _slow_state
    stub.idents = {}
    eps = _choreo_endpoints(monkeypatch, stub)

    async def _main():
        gaps = []

        async def _heartbeat():
            last = time.monotonic()
            for _ in range(10):
                await asyncio.sleep(0.25)
                now = time.monotonic()
                gaps.append(now - last)
                last = now

        hb = asyncio.create_task(_heartbeat())
        t0 = time.monotonic()
        await eps["/choreography/status"]()
        took = time.monotonic() - t0
        await hb
        return took, gaps

    took, gaps = asyncio.run(_main())
    assert took >= 2.0, "阻塞体未真实执行"
    assert max(gaps) < 1.0, (
        f"主 API 事件循环被阻塞 max_gap={max(gaps):.2f}s——to_thread 未生效")


# ======================================================================
# 3) AST 源码守卫：/sys_cmd 与 layout 启停 handler
# ======================================================================

_SYS_CMD_FORBIDDEN = {
    "run_external_program", "stop_external_program",
    "restart_application", "factory",
}


def _find_fn(tree, name):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and node.name == name:
            return node
    return None


def test_api_sys_cmd_dispatches_off_loop():
    """api_sys_cmd：编排体收编 _sys_cmd_dispatch + to_thread；四命令方法
    不得在 handler 体内直接调用（原同步冻结循环形态）。"""
    tree = ast.parse(MAIN_PY.read_text(encoding="utf-8"))
    fn = _find_fn(tree, "api_sys_cmd")
    assert fn is not None, "api_sys_cmd 缺失"
    assert any(
        isinstance(n, ast.Await) and isinstance(n.value, ast.Call)
        and getattr(n.value.func, "attr", "") == "to_thread"
        for n in ast.walk(fn)
    ), "api_sys_cmd 未走 asyncio.to_thread"
    assert any(
        isinstance(n, ast.Await) and isinstance(n.value, ast.Call)
        and getattr(n.value.func, "attr", "") == "to_thread"
        # _sys_cmd_dispatch 出现在 to_thread 调用的 args（self._sys_cmd_dispatch）
        and any(isinstance(a, ast.Attribute) and a.attr == "_sys_cmd_dispatch"
                for a in n.value.args)
        for n in ast.walk(fn)
    ), "api_sys_cmd 未收编 _sys_cmd_dispatch"
    direct = [
        n for n in ast.walk(fn)
        if isinstance(n, ast.Call) and getattr(n.func, "attr", "") in _SYS_CMD_FORBIDDEN
    ]
    assert not direct, (
        f"api_sys_cmd 内直接调用编排方法 {direct}——同步阻塞事件循环回潮")


def test_layout_start_stop_handlers_off_loop():
    """layout.py 启停三 handler：编排调用必须经 to_thread（UI notify/
    pending 更新留循环上）。"""
    tree = ast.parse(LAYOUT_PY.read_text(encoding="utf-8"))
    for name, forbidden in (
        ("_execute_start", {"run_external_program"}),
        ("_do_stop", {"stop_external_program"}),
        ("_do_restart", {"run_external_program", "stop_external_program"}),
    ):
        fn = _find_fn(tree, name)
        assert fn is not None, f"{name} 缺失"
        assert any(
            isinstance(n, ast.Await) and isinstance(n.value, ast.Call)
            and getattr(n.value.func, "attr", "") == "to_thread"
            for n in ast.walk(fn)
        ), f"{name} 未走 asyncio.to_thread"
        direct = [
            n for n in ast.walk(fn)
            if isinstance(n, ast.Call) and getattr(n.func, "attr", "") in forbidden
        ]
        assert not direct, f"{name} 内直接调用 {forbidden & {getattr(n.func, 'attr', '') for n in direct}}"


# ======================================================================
# 4) 文本守卫：run_external_program 训练拦截 notify 通道分派
# ======================================================================

def test_run_external_program_training_intercept_channel_guarded():
    """训练拦截 ui.notify 必须包在 `if type == "webui":` 内——api 通道经
    to_thread 在工作线程执行，ui.notify 无 NiceGUI client 上下文必抛
    （外层 except 复位 running_flag=启动静默失败）。"""
    src = MAIN_PY.read_text(encoding="utf-8")
    tree = ast.parse(src)
    lines = src.splitlines()
    fn = _find_fn(tree, "run_external_program")
    assert fn is not None
    marker = "训练进行中，用户尝试启动主系统——已阻止"
    hit = [i for i, ln in enumerate(lines) if marker in ln]
    assert hit, "训练拦截文案缺失"
    window = "\n".join(lines[hit[0]:hit[0] + 12])
    assert 'if type == "webui":' in window, (
        "训练拦截 ui.notify 未做通道分派（api/to_thread 通道会在工作线程内"
        "调用 ui.notify——无 client 上下文必抛）")
