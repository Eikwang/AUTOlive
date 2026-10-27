# -*- coding: utf-8 -*-
"""桌面字幕窗口守卫测试（2026-10-25 字幕打印审查批次）

背景：整合计划本期丢弃了源项目 desktop_caption_window.py（tkinter 桌面窗口），
导致"开启字幕打印→启动系统→打开字幕显示窗口"预期落空（只有 /captions 网页）。
本批次移植桌面窗口（utils/web_captions/desktop_caption_window.py，子进程 +
socket.io 对接 /captions_ws），web_server 按配置自动 spawn + UI 手动打开入口。

运行：C:\\Users\\admin\\anaconda3\\python.exe -m pytest tests/features/test_desktop_captions_window.py -v
"""
import json
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import pytest


# ---------- 工具 ----------

class _StubConfig:
    """Config.get(*keys) 语义桩（逐层取值，缺失返回 None）"""

    def __init__(self, data):
        self._data = data or {}

    def get(self, *keys):
        result = self._data
        for key in keys:
            if not isinstance(result, dict):
                return None
            result = result.get(key)
        return result


def make_webserver(config_data):
    from utils.web_server import WebServer
    ws = WebServer.__new__(WebServer)  # 跳过 __init__（不建 FastAPI 应用）
    ws.config = _StubConfig(config_data)
    ws._captions_desktop_proc = None
    return ws


def write_config(tmpdir, cfg):
    path = os.path.join(str(tmpdir), "config.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False)
    return path


# ---------- 1. 自动打开门控矩阵 ----------

@pytest.mark.parametrize("cfg,expected", [
    ({"web_captions_printer": {"enable": True, "desktop_window": True}}, True),
    ({"web_captions_printer": {"enable": True}}, True),                # 缺省开启（存量配置无新键）
    ({"web_captions_printer": {"enable": True, "desktop_window": False}}, False),
    ({"web_captions_printer": {"enable": False, "desktop_window": True}}, False),
    ({"web_captions_printer": {}}, False),
    ({}, False),
])
def test_desktop_window_gate_matrix(cfg, expected):
    ws = make_webserver(cfg)
    assert ws._captions_desktop_window_enabled() is expected


# ---------- 2. spawn 行为（幂等 / 落盘实启） ----------

def test_spawn_idempotent_when_alive():
    ws = make_webserver({})

    class _AliveProc:
        def poll(self):
            return None  # 存活

    ws._captions_desktop_proc = _AliveProc()
    result = ws._spawn_desktop_captions_window()
    assert result["code"] == 200
    assert "已打开" in result["message"]


def test_spawn_launches_script(tmp_path, monkeypatch):
    ws = make_webserver({})
    captured = {}

    class _FakeProc:
        pid = 12345

        def poll(self):
            return 0

    def fake_popen(args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return _FakeProc()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    result = ws._spawn_desktop_captions_window()
    assert result["code"] == 200
    script = captured["args"][1]
    assert os.path.isfile(script), "spawn 目标脚本必须真实存在"
    assert script.replace("\\", "/").endswith("utils/web_captions/desktop_caption_window.py")
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))
    assert captured["kwargs"]["cwd"] == project_root
    if os.name == "nt":
        assert captured["kwargs"]["creationflags"] == subprocess.CREATE_NO_WINDOW
    assert ws._captions_desktop_proc is not None


# ---------- 3. web_server 接线源级守卫 ----------

def test_webserver_source_endpoint_and_autoopen():
    src_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))), "utils", "web_server.py")
    with open(src_path, "r", encoding="utf-8") as f:
        src = f.read()
    # 手动打开端点（UI 按钮 → 主系统所在机器弹窗唯一通道）
    assert '@self.app.post("/captions_desktop_window/open")' in src
    # startup 自动打开经门控调用（锚定调用点，防端点在但自动打开丢失）
    assert "if self._captions_desktop_window_enabled():" in src
    assert "self._spawn_desktop_captions_window()" in src


def test_mount_tolerant_socketio_wrapper():
    """Starlette 1.x 全路径 Mount 语义守卫（runtime312 1.3.1 实测根因）：
    挂载子应用必须兼容全路径与剥离前缀两种 scope['path'] 形态，否则 engineio
    判定 startswith('/socket.io/') 恒 404——网页/桌面窗口全部连接失败。"""
    from fastapi import FastAPI
    from utils.web_server import WebServer

    ws = WebServer.__new__(WebServer)
    ws.app = FastAPI()
    ws._captions_desktop_proc = None
    ws._setup_web_captions()

    mount = next(r for r in ws.app.routes if getattr(r, "path", "") == "/captions_ws")
    inner = mount.app
    assert type(inner).__name__ == "_MountTolerantSocketIOASGI"

    # 新语义（starlette 1.x 实测）：全路径原样放行
    full = {"type": "http", "path": "/captions_ws/socket.io/"}
    assert inner._normalize_scope_path(full) is full
    # 旧语义：剥离前缀路径回写全路径（engineio 才能命中）
    stripped = {"type": "http", "path": "/socket.io/"}
    out = inner._normalize_scope_path(stripped)
    assert out is not stripped
    assert out["path"] == "/captions_ws/socket.io/"
    # websocket 升级请求同样处理
    ws_scope = {"type": "websocket", "path": "/socket.io/"}
    assert inner._normalize_scope_path(ws_scope)["path"] == "/captions_ws/socket.io/"
    # 无关路径不动（交给 engineio 自身 404）
    other = {"type": "http", "path": "/foo"}
    assert inner._normalize_scope_path(other) is other


# ---------- 4. 窗口脚本纯逻辑 ----------

@pytest.fixture
def dcw():
    import utils.web_captions.desktop_caption_window as mod
    return mod


def test_build_socket_url(dcw):
    # engineio 客户端丢弃 url 内路径（_get_engineio_url 只用 netloc+socketio_path），
    # 因此 base URL 不带路径，完整路径由 SOCKETIO_PATH 提供
    assert dcw.build_socket_url("0.0.0.0", 8082) == "http://127.0.0.1:8082"
    assert dcw.build_socket_url("192.168.1.5", 9000) == "http://192.168.1.5:9000"
    assert dcw.build_socket_url(None, None) == "http://127.0.0.1:8082"
    # 挂载前缀必须进 socketio_path，否则客户端请求 /socket.io → FastAPI 404
    assert dcw.SOCKETIO_PATH == "/captions_ws/socket.io"


def test_load_window_config_merges_section(tmp_path, dcw):
    path = write_config(tmp_path, {
        "api_ip": "0.0.0.0", "api_port": 1234,
        "web_captions_printer": {"enable": True, "subtitle_font_size": 32}})
    cfg = dcw.load_window_config(path)
    assert cfg["enable"] is True
    assert cfg["subtitle_font_size"] == 32
    assert cfg["subtitle_font_family"] == dcw.DEFAULT_CONFIG["subtitle_font_family"]  # 未配键回退默认
    ip, port = dcw.load_main_api(path)
    assert (ip, port) == ("0.0.0.0", 1234)


def test_load_window_config_missing_file_fallback(dcw, tmp_path):
    cfg = dcw.load_window_config(os.path.join(str(tmp_path), "__no_such__.json"))
    assert cfg == dcw.DEFAULT_CONFIG


def test_px_int_and_colors(dcw):
    assert dcw._px_int("600px", 1) == 600
    assert dcw._px_int(400, 1) == 400
    assert dcw._px_int("junk", 7) == 7
    assert dcw.tk_color("rgba(0,0,0,0.6)", "#111111") == "#000000"
    assert dcw.tk_color("none", "#111111") == "#111111"
    assert dcw.tk_color("", "#111111") == "#111111"
    assert dcw.tk_color("#219175", "#111111") == "#219175"
    assert dcw.tk_weight("bold") == "bold"
    assert dcw.tk_weight("600") == "bold"
    assert dcw.tk_weight("400") == "normal"


class _FakeScheduler:
    """tk root.after/after_cancel 桩（记录排程，手动触发）"""

    def __init__(self):
        self.jobs = {}
        self._seq = 0

    def after(self, ms, fn):
        self._seq += 1
        self.jobs[self._seq] = (ms, fn)
        return self._seq

    def after_cancel(self, token):
        self.jobs.pop(token, None)


def test_presenter_show_and_hide_schedule(dcw):
    sched = _FakeScheduler()
    shown, cleared = [], []
    p = dcw.CaptionPresenter(shown.append, lambda: cleared.append(True), sched,
                             default_keep_ms=4000)
    p.present({"content": "你好", "keep_time": 2500})
    assert shown == ["你好"]
    assert len(sched.jobs) == 1
    token = next(iter(sched.jobs))
    assert sched.jobs[token][0] == 2500
    sched.jobs[token][1]()  # 到点隐藏
    assert cleared == [True]


def test_presenter_keep_time_fallback_and_cancel(dcw):
    sched = _FakeScheduler()
    shown, cleared = [], []
    p = dcw.CaptionPresenter(shown.append, lambda: cleared.append(True), sched,
                             default_keep_ms=4000)
    p.present({"content": "第一条", "keep_time": 0})  # 非法 keep_time → 默认时长
    assert sched.jobs[next(iter(sched.jobs))][0] == 4000
    p.present({"content": "第二条", "keep_time": 100})  # 新消息取消未触发的隐藏
    assert cleared == []  # 第一条的隐藏被取消，未执行
    tokens = list(sched.jobs.keys())
    assert len(tokens) == 1  # 仅剩新一条的隐藏排程
    p.present({"content": "", "keep_time": 100})
    assert shown == ["第一条", "第二条"]  # 空内容不触发显示


# ---------- 5. UI 源级守卫 ----------

def test_ui_tab_source_bindings():
    src_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))), "frontend", "ui", "tabs", "web_captions_printer.py")
    with open(src_path, "r", encoding="utf-8") as f:
        src = f.read()
    # 开关绑定 desktop_window 键（config_auto_save 落盘路径）
    assert '("web_captions_printer", "desktop_window")' in src
    # 手动打开按钮走主系统 API
    assert "captions_desktop_window/open" in src
    # 2026-10-26 批次：字幕页地址输入移除（桌面窗口直接被 OBS 窗口捕捉，无需手拼地址）
    assert "ui.input(label='字幕页地址'" not in src
    assert "content_copy" not in src  # 地址复制按钮随输入一并退役
    # 样式下拉批次：描边色新键渲染绑定；背景色合并控件同值双写两键
    assert '("web_captions_printer", "subtitle_stroke_color")' in src
    assert '("web_captions_printer", "bg_color")' in src
    assert '("web_captions_printer", "body_bg_color")' in src


def test_window_connected_status_hidden(dcw):
    """连接成功后状态栏必须隐藏（2026-10-26 批次：状态字会被 OBS 窗口捕捉拍到）。

    双引号字面量断言仅命中状态栏文本（日志语句无引号包裹，不误伤）。"""
    src_path = os.path.abspath(dcw.__file__)
    with open(src_path, "r", encoding="utf-8") as f:
        src = f.read()
    assert '"已连接"' not in src
    assert "def _hide_status" in src


def test_window_source_retry_loop(dcw):
    """首连失败必须重试：python-socketio 的 reconnection 不接管首次 connect 异常；
    connect 必须用含挂载前缀的 SOCKETIO_PATH（engineio 客户端丢弃 url 内路径）"""
    src_path = os.path.abspath(dcw.__file__)
    with open(src_path, "r", encoding="utf-8") as f:
        src = f.read()
    assert "while not self._closing:" in src
    assert "wait_timeout=5" in src
    assert "time.sleep(3)" in src
    assert "import time" in src
    assert 'socketio_path=SOCKETIO_PATH' in src
    assert 'socketio_path="socket.io"' not in src  # 旧写法会丢挂载前缀 → 永远 404


def test_config_example_has_desktop_window():
    src_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))), "config.example.json")
    with open(src_path, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    assert cfg["web_captions_printer"]["desktop_window"] is True
