# -*- coding: utf-8 -*-
"""D1 运行状态条 + D-14 启动失败分支单测（2026-10-19 批次切片 2）

覆盖：redact 脱敏（query/Authorization/透传/混合）/ 异常环形缓冲 10 条上限 /
启动分级（关键失败不置 flag / 外围失败降级继续）/ 监控报警通道开关 /
子进程健康快照。UI 渲染部分不测（NiceGUI slot 上下文）。
"""
import os
import sys
import types
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from frontend.utils.redact import redact_exception
from frontend.main import AutoliveApp


# ---------- fixtures ----------


@pytest.fixture(autouse=True)
def _quiet_nicegui(monkeypatch):
    """run_external_program/_monitor_subprocesses 内的 ui.notify / threading.Timer
    在无 NiceGUI slot 上下文的测试进程会炸或留下真实线程——统一静默化；
    notify 调用改记入 NOTIFY_CALLS 供断言。"""
    import nicegui.ui as nicegui_ui

    calls = []

    def _record(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(nicegui_ui, "notify", _record)
    monkeypatch.setattr("frontend.main.threading.Timer",
                        lambda *a, **kw: MagicMock())
    monkeypatch.setattr(nicegui_ui, "timer", MagicMock())  # webui 通道 once-timer 静默化

    # fake train_streamer：避免导入真实重模块，pipeline.training_running=False
    fake = types.ModuleType("utils.train_streamer")
    fake.pipeline = SimpleNamespace(training_running=False)
    monkeypatch.setitem(sys.modules, "utils.train_streamer", fake)

    # 每测试独立缓冲（类级 deque 共享）
    AutoliveApp._shared_exception_buffer.clear()

    nicegui_ui.NOTIFY_CALLS = calls
    return calls


def _fake_autolive(tmp_path):
    """构造最小 AutoliveApp（绕过 NiceGUI 初始化——同 test_startup_wiring 模式）。"""
    app = AutoliveApp.__new__(AutoliveApp)
    app.config = MagicMock()
    app.config._config = {}
    app.my_subprocesses = {}
    app.running_flag = False
    app.base_path = str(tmp_path)
    return app


# ---------- 1. redact 脱敏 ----------


def test_redact_query_string():
    """URL query string 过滤：token/分页参数不再外泄。"""
    out = redact_exception("请求失败 GET http://127.0.0.1:8000/api?token=s3cr3t&page=2 500")
    assert "s3cr3t" not in out
    assert "page=2" not in out
    assert "?***" in out
    assert "http://127.0.0.1:8000/api?***" in out


def test_redact_authorization_bearer():
    """Authorization: Bearer <tok> 整段过滤（含 Bearer 前缀）。"""
    out = redact_exception("401 Unauthorized, Authorization: Bearer eyJhbGciOi.J9 过期")
    assert "eyJhbGciOi" not in out
    assert "Authorization:***" in out


def test_redact_authorization_lowercase_and_eq():
    """大小写不敏感 + 等号形态（authorization=Bearer <tok>）。"""
    out = redact_exception("请求被拒: authorization=Bearer abc123")
    assert "abc123" not in out
    assert "Authorization:***" in out


def test_redact_plain_message_passthrough():
    """正常消息透传：无 query/无 Authorization 时原样返回。"""
    msg = "fastrag 端口 11420 已被占用"
    assert redact_exception(msg) == msg


def test_redact_mixed_query_and_auth():
    """混合：query 与 Authorization 同现时双过滤。"""
    out = redact_exception("POST http://h/p?k=1 失败; Authorization: Bearer tk")
    assert "k=1" not in out
    assert "tk" not in out
    assert "?***" in out and "Authorization:***" in out


def test_redact_non_str_input():
    """非 str 输入（异常对象等）str() 化后脱敏。"""
    out = redact_exception(Exception("Authorization: Bearer zzz"))
    assert "zzz" not in out


# ---------- 2. 异常环形缓冲 ----------


def test_exception_buffer_maxlen_ten():
    """环形缓冲 10 条上限：超限挤掉最早条目。"""
    for i in range(12):
        AutoliveApp.push_exception("main", f"错误{i}")
    entries = AutoliveApp.recent_exceptions()
    assert len(entries) == 10
    assert entries[0]["message"] == "错误2"   # 0/1 被挤掉
    assert entries[-1]["message"] == "错误11"  # 最新在末尾


def test_exception_buffer_entry_shape():
    """条目结构：ts/module/message/fix_hint 齐全，模块名透传。"""
    entry = AutoliveApp.push_exception("edtalk_realtime", "解释器不存在", fix_hint="修改解释器路径")
    assert entry["module"] == "edtalk_realtime"
    assert entry["fix_hint"] == "修改解释器路径"
    assert entry["ts"] and entry["message"] == "解释器不存在"


def test_exception_buffer_redacts_on_push():
    """入缓冲即脱敏（DX 义务）：凭据不落缓冲。"""
    AutoliveApp.push_exception("main", "Authorization: Bearer leakme 已失效")
    entry = AutoliveApp.recent_exceptions()[-1]
    assert "leakme" not in entry["message"]
    assert "Authorization:***" in entry["message"]


# ---------- 3. D-14 启动分级 ----------


def test_critical_start_failure_keeps_flag_off(tmp_path, monkeypatch):
    """关键模块（main）失败：running_flag 不置 True、返回 code=-1、缓冲有记录。"""
    import nicegui.ui as nicegui_ui

    app = _fake_autolive(tmp_path)
    monkeypatch.setattr(app, "start_programs", lambda: [])
    monkeypatch.setattr(app, "_start_main_system",
                        lambda: (False, "主系统入口不存在", "检查安装完整性"))

    result = app.run_external_program(type="api")

    assert app.running_flag is False
    assert result["code"] == -1
    assert "主系统入口不存在" in result["msg"]
    entries = AutoliveApp.recent_exceptions()
    assert any(e["module"] == "main" and "入口不存在" in e["message"] for e in entries)
    # api 通道不弹 notify（通知经返回值/状态条承载）
    assert nicegui_ui.NOTIFY_CALLS == []


def test_critical_failure_webui_notifies_with_fix_hint(tmp_path, monkeypatch):
    """webui 通道关键失败：negative 通知带修复指引。"""
    import nicegui.ui as nicegui_ui

    app = _fake_autolive(tmp_path)
    monkeypatch.setattr(app, "start_programs", lambda: [])
    monkeypatch.setattr(app, "_start_main_system",
                        lambda: (False, "启动主系统进程失败", "若提示端口占用请修改端口配置"))

    result = app.run_external_program(type="webui")

    assert result["code"] == -1
    assert app.running_flag is False
    negs = [c for c in nicegui_ui.NOTIFY_CALLS if c.get("type") == "negative"]
    assert len(negs) == 1
    assert "主系统启动失败" in negs[0]["message"]
    assert "修改端口配置" in negs[0]["message"]


def test_outer_failure_degrades_and_continues(tmp_path, monkeypatch):
    """外围模块失败：降级继续（flag 置 True、code=200）、缓冲记录、warning 通知。"""
    import nicegui.ui as nicegui_ui

    app = _fake_autolive(tmp_path)
    monkeypatch.setattr(app, "start_programs",
                        lambda: [("edtalk_realtime", "EDTalk 解释器不存在", "修改解释器路径")])
    monkeypatch.setattr(app, "_start_main_system", lambda: (True, "", ""))

    result = app.run_external_program(type="webui")

    assert result["code"] == 200
    assert app.running_flag is True
    entries = AutoliveApp.recent_exceptions()
    assert any(e["module"] == "edtalk_realtime" for e in entries)
    warns = [c for c in nicegui_ui.NOTIFY_CALLS if c.get("type") == "warning"]
    assert len(warns) == 1
    assert "部分启动" in warns[0]["message"] and "EDTalk 推理" in warns[0]["message"]


def test_full_success_sets_flag_no_exception(tmp_path, monkeypatch):
    """全成功：code=200、flag True、缓冲无新增。"""
    app = _fake_autolive(tmp_path)
    monkeypatch.setattr(app, "start_programs", lambda: [])
    monkeypatch.setattr(app, "_start_main_system", lambda: (True, "", ""))

    result = app.run_external_program(type="webui")

    assert result["code"] == 200
    assert app.running_flag is True
    assert AutoliveApp.recent_exceptions() == []


def test_start_programs_collects_both_failures(tmp_path, monkeypatch):
    """start_programs 汇总两个外围失败（edtalk + fastrag）。"""
    app = _fake_autolive(tmp_path)
    monkeypatch.setattr(app, "_start_edtalk_realtime",
                        lambda cfg: (False, "EDTalk 副本缺失", "运行复制脚本"))
    monkeypatch.setattr(app, "_start_fastrag",
                        lambda cfg: (False, "fastrag 端口 11420 已被占用", "修改端口配置"))
    failures = app.start_programs()
    assert failures == [
        ("edtalk_realtime", "EDTalk 副本缺失", "运行复制脚本"),
        ("fastrag", "fastrag 端口 11420 已被占用", "修改端口配置"),
    ]


def test_start_programs_empty_when_ok_or_disabled(tmp_path, monkeypatch):
    """未启用/成功均不算失败（开关未启用返回 ok=True）。"""
    app = _fake_autolive(tmp_path)
    monkeypatch.setattr(app, "_start_edtalk_realtime", lambda cfg: (True, "", ""))
    monkeypatch.setattr(app, "_start_fastrag", lambda cfg: (True, "", ""))
    assert app.start_programs() == []


# ---------- 4. 监控报警（报警重构 2026-10-26：弹窗常开 + 外围退出并入模块报警） ----------


def test_monitor_notify_unconditional(tmp_path):
    """notify_enable 退役：弹窗告警常开，存量关闭键不再抑制。"""
    import nicegui.ui as nicegui_ui

    app = _fake_autolive(tmp_path)
    app.config._config = {"abnormal_alarm": {"notify_enable": False}}  # 存量退役键
    app.running_flag = True
    dead = MagicMock()
    dead.poll.return_value = 1
    app.my_subprocesses = {"main": dead}

    app._monitor_subprocesses()

    assert app.running_flag is False
    assert "main" not in app.my_subprocesses
    assert len(nicegui_ui.NOTIFY_CALLS) == 1
    assert nicegui_ui.NOTIFY_CALLS[0]["type"] == "negative"
    assert any(e["module"] == "main" and "退出" in e["message"]
               for e in AutoliveApp.recent_exceptions())


def test_monitor_main_death_no_module_alarm(tmp_path, monkeypatch):
    """main 退出不在六模块清单内：不触发模块报警（仅弹窗+状态条）。"""
    calls = []
    fake = SimpleNamespace(
        alarm_error=lambda *a, **k: calls.append(a) or True,
        MODULE_DIGITAL_HUMAN="digital_human", MODULE_LLM="llm")
    monkeypatch.setattr("frontend.main.alarm_notifier", fake)

    app = _fake_autolive(tmp_path)
    app.running_flag = True
    dead = MagicMock()
    dead.poll.return_value = 1
    app.my_subprocesses = {"main": dead}

    app._monitor_subprocesses()

    assert calls == []


def test_monitor_peripheral_death_module_alarm(tmp_path, monkeypatch):
    """外围退出并入模块报警：edtalk_realtime→数字人服务，fastrag→LLM服务。"""
    calls = []

    def _rec(module, message, fix_hint=""):
        calls.append((module, message, fix_hint))
        return True

    fake = SimpleNamespace(alarm_error=_rec,
                           MODULE_DIGITAL_HUMAN="digital_human", MODULE_LLM="llm")
    monkeypatch.setattr("frontend.main.alarm_notifier", fake)

    app = _fake_autolive(tmp_path)
    app.running_flag = True
    dead_et = MagicMock()
    dead_et.poll.return_value = 3
    dead_fr = MagicMock()
    dead_fr.poll.return_value = 4
    app.my_subprocesses = {"edtalk_realtime": dead_et, "fastrag": dead_fr}

    app._monitor_subprocesses()

    modules = [c[0] for c in calls]
    assert modules == ["digital_human", "llm"]


def test_monitor_auto_run_restart_success(tmp_path, monkeypatch):
    """auto_run 重拉成功：flag 恢复 True。"""
    app = _fake_autolive(tmp_path)
    app.config._config = {"webui": {"auto_run": True}}
    app.running_flag = True
    dead = MagicMock()
    dead.poll.return_value = 1
    app.my_subprocesses = {"main": dead}
    proc2 = MagicMock()
    proc2.poll.return_value = None

    def _fake_restart():
        app.my_subprocesses["main"] = proc2
        return True, "", ""

    monkeypatch.setattr(app, "_start_main_system", _fake_restart)

    app._monitor_subprocesses()

    assert app.running_flag is True


def test_monitor_auto_run_restart_failure_records_exception(tmp_path, monkeypatch):
    """auto_run 重拉失败：flag 保持 False、失败入缓冲。"""
    app = _fake_autolive(tmp_path)
    app.config._config = {"webui": {"auto_run": True}}
    app.running_flag = True
    dead = MagicMock()
    dead.poll.return_value = 1
    app.my_subprocesses = {"main": dead}
    monkeypatch.setattr(app, "_start_main_system",
                        lambda: (False, "启动主系统进程失败", "查看日志"))

    app._monitor_subprocesses()

    assert app.running_flag is False
    entries = AutoliveApp.recent_exceptions()
    assert any("自动重拉失败" in e["message"] for e in entries)


# ---------- 5. 子进程健康快照（D1 状态条数据源） ----------


def test_subprocess_health_snapshot(tmp_path):
    """快照：存活 True / 退出 False / 探测异常 None。"""
    app = _fake_autolive(tmp_path)
    alive = MagicMock()
    alive.poll.return_value = None
    dead = MagicMock()
    dead.poll.return_value = 3
    broken = MagicMock()
    broken.poll.side_effect = RuntimeError("句柄失效")
    app.my_subprocesses = {"main": alive, "fastrag": dead, "edtalk_realtime": broken}

    snap = dict(app.subprocess_health())

    assert snap["main"] is True
    assert snap["fastrag"] is False
    assert snap["edtalk_realtime"] is None


def test_subprocess_health_empty(tmp_path):
    """无已登记子进程：空列表（状态条显示"无已登记进程"）。"""
    app = _fake_autolive(tmp_path)
    assert app.subprocess_health() == []
