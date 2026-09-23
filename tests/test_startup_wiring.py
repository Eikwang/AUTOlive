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
    """空配置加载后自动补齐 edtalk_realtime/fastrag 全部默认键（§4.1.4）。"""
    from frontend.config.settings import Settings, init_config

    cfg_path = tmp_path / "config.json"
    cfg_path.write_text("{}", encoding="utf-8")
    settings = init_config(str(cfg_path))
    # 默认键已补齐
    assert settings.get("edtalk_realtime", "enable") is False
    assert settings.get("edtalk_realtime", "port") == 8000
    assert "interpreter_path" in settings.get("edtalk_realtime")
    assert settings.get("fastrag", "port") == 11420
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
        # 关闭新服务开关（隔离被测分支）
        "visual_body": "metahuman_stream",
        "fastrag": {"enable": False},
    }
    monkeypatch.setattr("frontend.main.subprocess.Popen", FakeProc)
    monkeypatch.setattr("frontend.main.os.path", os.path)  # 保持 os.path 真实
    app.start_programs()
    assert captured["cmd"] == ["E:/srv/python.exe", "E:/srv/app.py"]
    assert "legacy_app" in app.my_subprocesses


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
        "visual_body": "metahuman_stream",
        "fastrag": {"enable": False},
    }
    monkeypatch.setattr("frontend.main.subprocess.Popen", FakeProc)
    app.start_programs()
    assert captured["cmd"] == ["py.exe", "app.py", "--port", "9999"]


# ---------- 3. EDTalk 拉起：开关/校验/参数映射/幂等 ----------


def _ed_config(**overrides):
    base = {
        "visual_body": "edtalk_realtime",
        "edtalk_realtime": {
            "enable": True, "port": 8000, "host": "127.0.0.1",
            "character_dir": "wy", "target_fps": 25, "buffer_frames": 16,
            "preroll_frames": 8, "face_repair": "adaptive",
            "gaze_correction": "adaptive", "gaze_convergence": 0.05,
            "segments": "auto", "api_token": "",
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
    cfg2["visual_body"] = "metahuman_stream"
    app.config._config = cfg2
    app._start_edtalk_realtime(app.config._config)
    assert launched == {}


def test_edtalk_cmd_mapping_and_shell_false(tmp_path, monkeypatch):
    """拉起命令映射 RUNBOOK 参数表 + shell=False（native H1）。"""
    app = _fake_autolive(tmp_path)
    monkeypatch.setattr("frontend.main.get_base_path", lambda: str(tmp_path))
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


def test_edtalk_port_conflict_identity_check(tmp_path, monkeypatch):
    """8000 被占用但 /status 响应非 realtime_serve → 拒绝拉起（native E5）。"""
    app = _fake_autolive(tmp_path)
    monkeypatch.setattr("frontend.main.get_base_path", lambda: str(tmp_path))
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
    assert launched == {}  # 身份不符 → 不拉起


def test_edtalk_already_running_skip(tmp_path, monkeypatch):
    """已在运行（/status 响应含 available_segments）→ 跳过拉起（幂等）。"""
    app = _fake_autolive(tmp_path)
    monkeypatch.setattr("frontend.main.get_base_path", lambda: str(tmp_path))
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
