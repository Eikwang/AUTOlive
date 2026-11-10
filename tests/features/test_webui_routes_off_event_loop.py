# -*- coding: utf-8 -*-
"""webui 路由事件循环阻断守卫（2026-11-11 断联根修）。

背景（用户实报：数字人下级页——动作编排配置 ↔ 训练主播——切换/停留时
页面卡死后整页重载；同 2026-11-06 音频播放页断联根因类）：

- webui 进程 = NiceGUI/FastAPI 单事件循环；NiceGUI 3.16 由
  reconnect_timeout=3.0 推导 socket.io ping_interval=4s / ping_timeout=2s
  （anaconda nicegui/nicegui.py:138-139 实装确认）。
- 本进程实测：`service_status`（同步 /status 请求）服务离线时恒定吃满
  2.0s timeout（Windows 防火墙对环回 SYN 静默丢弃 → connect 重试至超时，
  而非快速拒绝）；`list_materials` 全链 2.05s/2.03s（冷/热）；delete 含
  GB 级 rmtree。
- 原 `GET /edtalk_materials` 等 async def handler 在事件循环上同步执行
  上述阻塞体——动作编排页 0.1s(进入 once)+8s(轮询) 驱动该端点，每轮冻结
  循环 ≥2s 击穿 ping_timeout → WS 判死 → 浏览器重连整页重载。

守卫三层（行为级为主，防回归不限实现细节）：
1. off-loop 断言——阻塞体必须经 asyncio.to_thread 在工作线程执行
   （monkeypatch 真身记录 threading.get_ident，与循环线程比对）。
2. 循环心跳断言——list_materials 阻塞 2s 期间，同循环心跳协程最大
   唤醒间隔 <1.0s（旧实现 ~2.0s 必挂——本断言编码真实故障模式）。
3. 源码面守卫——frontend/main.py /send /llm /get_sys_info handler 内
   禁止内联 urlopen（收编模块级 _forward_to_main + to_thread）。

纪律：全部 monkeypatch 桩，不触盘、不写 config.json（choreo 配置污染
教训，2026-11-05）。
"""
import ast
import asyncio
import io
import threading
import time
from pathlib import Path

import pytest
from fastapi import FastAPI

from utils import cover_endpoints
from utils.cover_endpoints import CoverDeleteResult
from utils.edtalk_realtime import materials

MAIN_PY = Path(__file__).resolve().parents[2] / "frontend" / "main.py"


def _material_endpoints() -> dict:
    """注册素材路由到临时 FastAPI 并返回 {path: endpoint 原函数}。

    直接取 route.endpoint（绕过依赖解析），供协程直调——off-loop 断言
    关注 handler 体本身，鉴权依赖（_require_loopback）不在本守卫范围。
    """
    app = FastAPI()
    materials.register_material_routes(app, {})
    return {getattr(r, "path", ""): getattr(r, "endpoint", None)
            for r in app.routes
            if str(getattr(r, "path", "")).startswith("/edtalk_materials")}


def _cover_endpoints() -> dict:
    app = FastAPI()
    cover_endpoints.register_cover_routes(app, {})
    return {getattr(r, "path", ""): getattr(r, "endpoint", None)
            for r in app.routes
            if str(getattr(r, "path", "")).startswith("/cover/")}


# ======================================================================
# 1) off-loop 断言：/edtalk_materials 五 handler + /cover delete/enqueue
# ======================================================================

def _assert_off_loop(ep, monkeypatch, module, attr, stub_ret, **kwargs):
    """直调 handler 协程：桩记录执行线程 ident，须 ≠ 事件循环线程。"""
    seen = {}

    def _stub(*_a, **_k):
        seen["ident"] = threading.get_ident()
        return stub_ret

    monkeypatch.setattr(module, attr, _stub)
    loop_seen = {}

    async def _main():
        loop_seen["ident"] = threading.get_ident()
        return await ep(**kwargs)

    result = asyncio.run(_main())
    assert "ident" in seen, f"{attr} 桩未被调用"
    assert seen["ident"] != loop_seen["ident"], (
        f"{attr} 在事件循环线程上同步执行（旧缺陷形态：>2s 阻塞 → WS 判死 "
        "→ 页面重载循环）")
    return result


def test_materials_list_off_loop(monkeypatch):
    eps = _material_endpoints()
    _assert_off_loop(eps["/edtalk_materials"], monkeypatch,
                     materials, "list_materials", {"items": []})


def test_materials_publish_off_loop(monkeypatch):
    eps = _material_endpoints()
    r = _assert_off_loop(
        eps["/edtalk_materials/publish"], monkeypatch,
        materials, "publish_material", {},
        payload={"video_path": "x.mp4", "segment": "seg"})
    assert r == {"accepted": True, "task": {}}


def test_materials_set_meta_off_loop(monkeypatch):
    eps = _material_endpoints()
    _assert_off_loop(
        eps["/edtalk_materials/set_meta"], monkeypatch,
        materials, "set_material_meta", {},
        payload={"segment": "seg", "type": "main"})


def test_materials_reprocess_off_loop(monkeypatch):
    eps = _material_endpoints()
    _assert_off_loop(
        eps["/edtalk_materials/reprocess"], monkeypatch,
        materials, "_reprocess_material", {},
        payload={"segment": "seg", "lip_flag": True})


def test_materials_delete_off_loop(monkeypatch):
    eps = _material_endpoints()
    r = _assert_off_loop(
        eps["/edtalk_materials/delete"], monkeypatch,
        materials, "delete_material", {"ok": True},
        payload={"segment": "seg"})
    assert r == {"ok": True}


def test_cover_delete_off_loop(monkeypatch):
    eps = _cover_endpoints()
    _assert_off_loop(
        eps["/cover/delete"], monkeypatch,
        cover_endpoints, "delete_cover_artifact",
        CoverDeleteResult(True, None, "", ["a.wav"]),
        req={"file": "a.wav"})


def test_cover_enqueue_off_loop(monkeypatch):
    eps = _cover_endpoints()
    r = _assert_off_loop(
        eps["/cover/enqueue"], monkeypatch,
        cover_endpoints, "enqueue_cover_manual", {"action": "queued"},
        req={"song": "晴天"})
    assert r == {"action": "queued"}


# ======================================================================
# 2) 循环心跳断言：阻塞体在 While 循环保持响应（真实故障模式编码）
# ======================================================================

def test_event_loop_stays_responsive_during_materials_list(monkeypatch):
    eps = _material_endpoints()

    def _slow_list(*_a, **_k):
        """模拟服务离线时 service_status 吃满 2.0s timeout 的真身。"""
        time.sleep(2.0)
        return {"items": []}

    monkeypatch.setattr(materials, "list_materials", _slow_list)

    async def _main():
        gaps = []

        async def _heartbeat():
            last = time.monotonic()
            for _ in range(10):  # 10×0.25s=2.5s > 阻塞体 2.0s，全程覆盖
                await asyncio.sleep(0.25)
                now = time.monotonic()
                gaps.append(now - last)
                last = now

        hb = asyncio.create_task(_heartbeat())
        t0 = time.monotonic()
        result = await eps["/edtalk_materials"]()
        took = time.monotonic() - t0
        await hb
        return result, took, gaps

    result, took, gaps = asyncio.run(_main())
    assert took >= 2.0, "阻塞体未真实执行（桩提前返回？）"
    assert result == {"items": []}
    # 旧实现（同步体在循环上）：心跳最大间隔 ≈2.0s（=ping_timeout 判死线）
    assert max(gaps) < 1.0, (
        f"事件循环被阻塞 max_gap={max(gaps):.2f}s——to_thread 未生效")


# ======================================================================
# 3) 源码面守卫：frontend/main.py 三个转发 handler 禁止内联 urlopen
# ======================================================================

def _load_forward_to_main():
    """从 frontend/main.py 抽取模块级 _forward_to_main 单函数 exec 加载
    （避免整体 import frontend.main——模块级拉起全部 tab 模块链）。"""
    tree = ast.parse(MAIN_PY.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "_forward_to_main":
            ns = {}
            exec(compile(ast.Module(body=[node], type_ignores=[]),
                         str(MAIN_PY), "exec"), ns)
            return ns["_forward_to_main"]
    raise AssertionError(
        "_forward_to_main 未在 frontend/main.py 模块级定义（转发收编回退？）")


def test_forward_handlers_no_inline_urlopen():
    """/send /llm /get_sys_info：必须 await asyncio.to_thread，禁止内联
    urlopen——原实现主 API 无响应时同步冻结事件循环可达 60s。"""
    tree = ast.parse(MAIN_PY.read_text(encoding="utf-8"))
    names = {"api_send", "api_llm", "api_get_sys_info"}
    found = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and node.name in names:
            found[node.name] = node
    assert set(found) == names, f"转发 handler 缺失: {names - set(found)}"
    for name, fn in found.items():
        offloaded = [
            n for n in ast.walk(fn)
            if isinstance(n, ast.Await) and isinstance(n.value, ast.Call)
            and getattr(n.value.func, "attr", "") == "to_thread"
        ]
        assert offloaded, f"{name} 未走 asyncio.to_thread（同步阻塞事件循环）"
        inline = [
            n for n in ast.walk(fn)
            if isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "urlopen"
        ]
        assert not inline, f"{name} handler 内联 urlopen（60s 冻结风险）"


def test_forward_to_main_wildcard_ip_rewrite(monkeypatch):
    """_forward_to_main 保持原转发语义：api_ip=0.0.0.0 → 127.0.0.1 重写。"""
    captured = {}

    class _FakeResp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def _fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["timeout"] = timeout
        return _FakeResp(b'{"ok": 1}')

    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
    fn = _load_forward_to_main()
    out = fn({"api_ip": "0.0.0.0", "api_port": 8082}, "/get_sys_info",
             method="GET")
    assert captured["url"] == "http://127.0.0.1:8082/get_sys_info"
    assert captured["timeout"] == 60
    assert out == {"ok": 1}
