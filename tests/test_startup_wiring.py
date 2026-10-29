# -*- coding: utf-8 -*-
"""启动接线单测（Eng 任务 T3）

覆盖：cmd 透传回归（R5 铁律）/ 8000 端口幂等探测 + 身份验证 / interpreter 校验 /
ensure-default 迁移
"""
import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ---------- 1. ensure-default 迁移 ----------


def test_ensure_defaults_fills_missing_sections(tmp_path):
    """空配置加载后自动补齐 edtalk_realtime/fastrag 默认键（§4.1.4）。

    2026-10-26 配置面收敛：端口/解释器/段调度为系统内部量（runtime.py
    自动解析+记忆），不再入默认表——确保-default 不再注入这些键。
    """
    from frontend.config.settings import Settings, init_config

    cfg_path = tmp_path / "config.json"
    cfg_path.write_text("{}", encoding="utf-8")
    settings = init_config(str(cfg_path))
    # 默认键已补齐
    assert settings.get("edtalk_realtime", "enable") is False
    assert settings.get("edtalk_realtime", "character_dir") == "wy"
    assert settings.get("fastrag", "port") == 11420
    assert settings.get("fastrag", "stream_reply") is True
    # 内部量键不再注入
    ed = settings.get("edtalk_realtime")
    for internal in ("port", "api_ip_port", "interpreter_path", "segments",
                     "host", "api_token"):
        assert internal not in ed, internal
    # 且已落盘
    on_disk = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert "edtalk_realtime" in on_disk and "fastrag" in on_disk


def test_ensure_defaults_preserves_user_values(tmp_path):
    """用户已配置的值不被覆盖（只补缺失键）。"""
    from frontend.config.settings import init_config

    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(
        json.dumps({"edtalk_realtime": {"enable": True, "port": 9000}}),
        encoding="utf-8",
    )
    settings = init_config(str(cfg_path))
    assert settings.get("edtalk_realtime", "enable") is True
    assert settings.get("edtalk_realtime", "port") == 9000
    # 缺失键仍补齐
    assert "character_dir" in settings.get("edtalk_realtime")


# ---------- 2. cmd 透传回归（R5 铁律） ----------


def _fake_autolive(tmp_path):
    """构造最小 AutoliveApp（绕过 NiceGUI 初始化——直接测方法逻辑）。"""
    from frontend.main import AutoliveApp

    app = AutoliveApp.__new__(AutoliveApp)
    app.config = MagicMock()
    app.config._config = {}
    app.my_subprocesses = {}
    app.running_flag = False
    return app


@pytest.mark.skip(reason="退役：coordination_program 配置驱动拉起已随联动程序模块退役移除（start/stop_programs 重构保留 EDTalk/fastrag/main 分支）（门禁A/删除批 2026-10-19）")
def test_coordination_program_passthrough_regression(tmp_path, monkeypatch):
    """R5 回归：既有 coordination_program 条目（parameters 仅 app_path）
    在透传修复后 cmd = [executable, app_path]——行为不变。"""
    captured = {}

    class FakeProc:
        def __init__(self, cmd, **kw):
            captured["cmd"] = cmd
            captured["cwd"] = kw.get("cwd")
            captured["shell"] = kw.get("shell")
            self.pid = 42

        def poll(self):
            return None

    app = _fake_autolive(tmp_path)
    app.config._config = {
        "coordination_program": [{
            "enable": True, "name": "legacy_app",
            "executable": "E:/srv/python.exe",
            "parameters": ["E:/srv/app.py"],
        }],
        # 关闭新服务开关（隔离被测分支；2026-10-22 批次 G2：metahuman 值退役改 edtalk+enable=false）
        "visual_body": "edtalk_realtime",
        "edtalk_realtime": {"enable": False},
        "fastrag": {"enable": False},
    }
    monkeypatch.setattr("frontend.main.subprocess.Popen", FakeProc)
    monkeypatch.setattr("frontend.main.os.path", os.path)  # 保持 os.path 真实
    app.start_programs()
    assert captured["cmd"] == ["E:/srv/python.exe", "E:/srv/app.py"]
    assert "legacy_app" in app.my_subprocesses


@pytest.mark.skip(reason="退役：coordination_program 配置驱动拉起已随联动程序模块退役移除（start/stop_programs 重构保留 EDTalk/fastrag/main 分支）（门禁A/删除批 2026-10-19）")
def test_coordination_program_multi_params_passthrough(tmp_path, monkeypatch):
    """透传修复：parameters[1:] 不再被丢弃（对接纪要 A2 主诉求）。"""
    captured = {}

    class FakeProc:
        def __init__(self, cmd, **kw):
            captured["cmd"] = cmd
            self.pid = 43

        def poll(self):
            return None

    app = _fake_autolive(tmp_path)
    app.config._config = {
        "coordination_program": [{
            "enable": True, "name": "multi",
            "executable": "py.exe",
            "parameters": ["app.py", "--port", "9999"],
        }],
        "visual_body": "edtalk_realtime",
        "edtalk_realtime": {"enable": False},
        "fastrag": {"enable": False},
    }
    monkeypatch.setattr("frontend.main.subprocess.Popen", FakeProc)
    app.start_programs()
    assert captured["cmd"] == ["py.exe", "app.py", "--port", "9999"]


# ---------- 3. EDTalk 拉起：开关/校验/参数映射/幂等 ----------


def _ed_config(**overrides):
    """最小 EDTalk 配置（2026-10-26 收敛后：无 port/segments 内部量键；
    interpreter_path 仅作存量兼容键传入——resolve_interpreter 对存在文件优先采纳）。"""
    base = {
        "visual_body": "edtalk_realtime",
        "edtalk_realtime": {
            "enable": True,
            "character_dir": "wy", "target_fps": 25, "buffer_frames": 16,
            "preroll_frames": 8, "face_repair": "adaptive",
            "gaze_correction": "adaptive", "gaze_convergence": 0.05,
            "interpreter_path": "fake-python.exe",
        },
        "fastrag": {"enable": False},
    }
    base["edtalk_realtime"].update(overrides)
    return base


def test_edtalk_not_started_when_disabled(tmp_path, monkeypatch):
    """enable=false 或驱动非 edtalk → 不拉起（开关双检）。"""
    app = _fake_autolive(tmp_path)
    monkeypatch.setattr("frontend.main.get_base_path", lambda: str(tmp_path))
    for cfg in [
        _ed_config(),  # enable=True 但下面覆盖为 False
    ]:
        pass
    cfg = _ed_config()
    cfg["edtalk_realtime"]["enable"] = False
    app.config._config = cfg
    launched = {}
    monkeypatch.setattr("frontend.main.subprocess.Popen",
                        lambda cmd, **kw: launched.update(cmd=cmd) or MagicMock(pid=1))
    app._start_edtalk_realtime(app.config._config)
    assert launched == {}

    cfg2 = _ed_config()
    cfg2["edtalk_realtime"]["enable"] = False  # 2026-10-22 批次 G2：visual_body 单值，改 enable=false 组合
    app.config._config = cfg2
    app._start_edtalk_realtime(app.config._config)
    assert launched == {}


def test_edtalk_cmd_mapping_and_shell_false(tmp_path, monkeypatch):
    """拉起命令映射 RUNBOOK 参数表 + shell=False（native H1）。"""
    app = _fake_autolive(tmp_path)
    # get_base_path 返回 Path（config.paths 内部用 Path 除法拼 log 目录）
    monkeypatch.setattr("frontend.main.get_base_path", lambda: tmp_path)
    monkeypatch.setattr("frontend.config.paths.get_base_path", lambda: tmp_path)  # runtime.py 用
    captured = {}

    class FakeProc:
        def __init__(self, cmd, **kw):
            captured["cmd"] = cmd
            captured["shell"] = kw.get("shell")
            captured["cwd"] = kw.get("cwd")
            self.pid = 44

        def poll(self):
            return None

    # 目录结构：edtalk/ 存在
    (tmp_path / "edtalk").mkdir()
    (tmp_path / "fake-python.exe").write_text("", encoding="utf-8")

    monkeypatch.setattr("frontend.main.subprocess.Popen", FakeProc)
    # 端口探测失败（未运行）→ 正常拉起
    import requests as _rq
    monkeypatch.setattr(_rq, "get", MagicMock(side_effect=ConnectionError))

    cfg = _ed_config(interpreter_path=str(tmp_path / "fake-python.exe"))
    app.config._config = cfg
    app._start_edtalk_realtime(app.config._config)

    cmd = captured["cmd"]
    assert cmd[0] == str(tmp_path / "fake-python.exe")
    assert cmd[1:3] == ["-m", "realtime_serve.main"]
    assert "--character-dir" in cmd and "wy" in cmd
    assert "--target-fps" in cmd and "25" in cmd
    assert "--face-repair" in cmd and "adaptive" in cmd
    assert "--segments" in cmd and "auto" in cmd
    assert "--port" in cmd and "8000" in cmd
    assert captured["shell"] is False


def test_edtalk_port_conflict_auto_switch(tmp_path, monkeypatch):
    """8000 被其他 HTTP 服务占用 → 自动切换到可用端口并拉起（不再拒绝）。"""
    app = _fake_autolive(tmp_path)
    monkeypatch.setattr("frontend.main.get_base_path", lambda: tmp_path)
    monkeypatch.setattr("frontend.config.paths.get_base_path", lambda: tmp_path)  # runtime.py 用
    (tmp_path / "edtalk").mkdir()
    (tmp_path / "fake-python.exe").write_text("", encoding="utf-8")
    launched = {}

    class FakeResp:
        status_code = 200
        text = "<html>other service</html>"  # 非 realtime_serve

    import requests as _rq
    monkeypatch.setattr(_rq, "get", MagicMock(return_value=FakeResp))
    monkeypatch.setattr("frontend.main.subprocess.Popen",
                        lambda cmd, **kw: launched.update(cmd=cmd) or MagicMock(pid=2))

    cfg = _ed_config(interpreter_path=str(tmp_path / "fake-python.exe"))
    app.config._config = cfg
    app._start_edtalk_realtime(app.config._config)
    assert "--port" in launched["cmd"]
    port_val = launched["cmd"][launched["cmd"].index("--port") + 1]
    assert port_val != "8000"  # 自动切换
    # 实际端口已入运行时文件（消费方统一解析依据）
    runtime_data = json.loads(
        (tmp_path / "data" / "edtalk_runtime.json").read_text(encoding="utf-8"))
    assert runtime_data["port"] == int(port_val)


def test_edtalk_already_running_skip(tmp_path, monkeypatch):
    """已在运行（/status 响应含 available_segments）→ 跳过拉起 + 运行时文件回写。"""
    app = _fake_autolive(tmp_path)
    monkeypatch.setattr("frontend.main.get_base_path", lambda: tmp_path)
    monkeypatch.setattr("frontend.config.paths.get_base_path", lambda: tmp_path)  # runtime.py 用
    (tmp_path / "edtalk").mkdir()
    (tmp_path / "fake-python.exe").write_text("", encoding="utf-8")
    launched = {}

    class FakeResp:
        status_code = 200
        text = '{"available_segments": ["1", "2"]}'

    import requests as _rq
    monkeypatch.setattr(_rq, "get", MagicMock(return_value=FakeResp))
    monkeypatch.setattr("frontend.main.subprocess.Popen",
                        lambda cmd, **kw: launched.update(cmd=cmd) or MagicMock(pid=3))

    cfg = _ed_config(interpreter_path=str(tmp_path / "fake-python.exe"))
    app.config._config = cfg
    app._start_edtalk_realtime(app.config._config)
    assert launched == {}  # 幂等跳过
    assert "edtalk_realtime" not in app.my_subprocesses
    runtime_data = json.loads(
        (tmp_path / "data" / "edtalk_runtime.json").read_text(encoding="utf-8"))
    assert runtime_data["port"] == 8000  # 复用已有服务也回写（客户端可解析）


# ---------- 4. fastrag 启动路径 python 化（autoplan main-20261029-102545 批次） ----------


class _FakeSock:
    """connect_ex 可编程的假 socket（1=占用，0=空闲）。"""

    def __init__(self, result):
        self._result = result

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def settimeout(self, t):
        pass

    def connect_ex(self, addr):
        return self._result


def _fastrag_env(tmp_path, monkeypatch, *, port=11420, index=True, data_json=True,
                 server_py=True, interpreter="fake-python.exe", connect=1):
    """装配 fastrag 预检环境：返回 (app, captured, cfg)。"""
    from frontend.main import AutoliveApp

    app = AutoliveApp.__new__(AutoliveApp)
    app.config = MagicMock()
    app.my_subprocesses = {}
    captured = {}

    class FakeProc:
        def __init__(self, cmd, **kw):
            captured["cmd"] = cmd
            captured["cwd"] = kw.get("cwd")
            captured["env"] = kw.get("env")
            captured["shell"] = kw.get("shell")
            self.pid = 77

        def poll(self):
            return None

    fastrag_dir = tmp_path / "edtalk" / "fastrag"
    py_dir = fastrag_dir / "fastrag-python"
    py_dir.mkdir(parents=True, exist_ok=True)
    if index:
        (fastrag_dir / "kbindex_py").mkdir(parents=True, exist_ok=True)
        if data_json:
            (fastrag_dir / "kbindex_py" / "data.json").write_text("{}", encoding="utf-8")
        (fastrag_dir / "kbindex_py" / "chunk_index.faiss").write_bytes(b"faiss")
    if server_py:
        (py_dir / "server.py").write_text("# server", encoding="utf-8")
    if interpreter:
        (tmp_path / interpreter).write_text("", encoding="utf-8")

    monkey_targets = {
        "frontend.main.get_base_path": lambda: tmp_path,
        "frontend.main.get_log_dir": lambda: tmp_path / "log",
        "frontend.main.subprocess.Popen": FakeProc,
        "frontend.main.socket.socket": lambda *a, **k: _FakeSock(connect),
        "utils.edtalk_realtime.runtime.resolve_interpreter":
            lambda cfg: str(tmp_path / interpreter) if interpreter else "",
    }
    for target, value in monkey_targets.items():
        monkeypatch.setattr(target, value)

    cfg = {"fastrag": {"enable": True, "port": port}}
    app.config._config = cfg
    return app, captured, cfg


def test_fastrag_python_cmd_and_env(tmp_path, monkeypatch):
    """T1 守卫：cmd=[interpreter, server.py]（无 server.exe）、cwd=fastrag-python、
    端口 env 注入、shell=False、route=python 日志。"""
    app, captured, cfg = _fastrag_env(tmp_path, monkeypatch)
    ok, reason, fix = app._start_fastrag(cfg)
    assert ok is True, (reason, fix)
    cmd = captured["cmd"]
    assert cmd[0] == str(tmp_path / "fake-python.exe")
    assert cmd[1] == str(tmp_path / "edtalk" / "fastrag" / "fastrag-python" / "server.py")
    assert not any("server.exe" in part for part in cmd), "启动物不得再是 rust exe"
    assert captured["cwd"] == str(tmp_path / "edtalk" / "fastrag" / "fastrag-python")
    assert captured["env"]["FASTRAG__SERVER__PORT"] == "11420"
    assert captured["shell"] is False
    assert "fastrag" in app.my_subprocesses


def test_fastrag_preflight_index_missing(tmp_path, monkeypatch):
    """预检①：python 索引缺失 → ok=False + fix 指向构建知识库（DX F1 完整序列）。"""
    app, captured, cfg = _fastrag_env(tmp_path, monkeypatch, index=False)
    ok, reason, fix = app._start_fastrag(cfg)
    assert ok is False
    assert "构建知识库" in fix and "重启系统" in fix
    assert "cmd" not in captured


def test_fastrag_preflight_half_built_index(tmp_path, monkeypatch):
    """Eng E2：faiss 在而 data.json 缺（构建中断半成品）→ ok=False 不穿透。"""
    app, captured, cfg = _fastrag_env(tmp_path, monkeypatch, data_json=False)
    ok, reason, fix = app._start_fastrag(cfg)
    assert ok is False
    assert "data.json" in reason
    assert "cmd" not in captured


def test_fastrag_preflight_server_py_missing(tmp_path, monkeypatch):
    """预检②：server.py 缺失 → fix 指向 copy_edtalk.py。"""
    app, captured, cfg = _fastrag_env(tmp_path, monkeypatch, server_py=False)
    ok, reason, fix = app._start_fastrag(cfg)
    assert ok is False
    assert "copy_edtalk.py" in fix
    assert "cmd" not in captured


def test_fastrag_preflight_interpreter_missing(tmp_path, monkeypatch):
    """预检③：解释器缺失 → fix 指向系统设置页（Eng A1/DX F3）。"""
    app, captured, cfg = _fastrag_env(tmp_path, monkeypatch, interpreter="")
    ok, reason, fix = app._start_fastrag(cfg)
    assert ok is False
    assert "解释器" in fix
    assert "cmd" not in captured


def test_fastrag_invalid_port_fail_soft(tmp_path, monkeypatch):
    """Eng E3：非法端口 → ok=False fail-soft（前端），fix 含改端口指引。"""
    app, captured, cfg = _fastrag_env(tmp_path, monkeypatch, port="abc")
    ok, reason, fix = app._start_fastrag(cfg)
    assert ok is False
    assert "端口" in fix
    assert "cmd" not in captured


def test_fastrag_port_busy_other_process(tmp_path, monkeypatch):
    """Eng E4/DX F4：端口被占用且 /health 不通（其他进程）→ ok=False+改端口指引。"""
    import urllib.request
    app, captured, cfg = _fastrag_env(tmp_path, monkeypatch, connect=0)
    monkeypatch.setattr(urllib.request, "urlopen",
                        MagicMock(side_effect=ConnectionError))
    ok, reason, fix = app._start_fastrag(cfg)
    assert ok is False
    assert "端口" in reason
    assert "cmd" not in captured


def test_fastrag_already_running_idempotent(tmp_path, monkeypatch):
    """Eng E4：fastrag 自身在运行（/health 200）→ ok=True 幂等跳过，不重复拉起。"""
    import urllib.request
    app, captured, cfg = _fastrag_env(tmp_path, monkeypatch, connect=0)
    fake_resp = MagicMock()
    fake_resp.status = 200
    fake_resp.__enter__.return_value = fake_resp  # with ... as _resp 指回自身
    monkeypatch.setattr(urllib.request, "urlopen",
                        MagicMock(return_value=fake_resp))
    ok, reason, fix = app._start_fastrag(cfg)
    assert ok is True
    assert "cmd" not in captured
    assert "fastrag" not in app.my_subprocesses
