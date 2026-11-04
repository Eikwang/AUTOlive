# -*- coding: utf-8 -*-
"""启动失败僵尸进程回归守卫（2026-11-03 故障批次）

故障链复述（erro.log 2026-11-03 16:42-16:44）：
  main.start() 中 threading.Timer(3.0, ..., name=...) TypeError（Timer 签名仅
  interval/function/args/kwargs，不接受 name）→ start() 整体失败走 stop() →
  main() 正常返回、主线程结束 → concurrent.futures 全局 _shutdown 置位 →
  进程被非守护线程吊住滞留"半死"（uvicorn 仍收单）→ fastrag(LLM) 与
  GPT-SoVITS(TTS) 所有线程池提交永久失败
  （RuntimeError: cannot schedule new futures after interpreter shutdown）。

随批修复的相邻缺陷：
  - GptSovitsInproc 缺 stop() → main.stop() 托管循环 AttributeError 打断，其后服务不停
  - main.stop() 托管循环整循环单 try → 单服务异常打断全部
  - my_handle 编排器装配 audio_cancel=self.cancel_choreo_audio——该方法定义于
    QueueMixin（Audio 混入），My_handle 无此属性 → 编排器装配整体失败
  - GptSovitsInproc.start() 守卫用 property state（胶囊映射词 starting/healthy/red）
    对比 ST_LOADING/ST_READY 原词——永不相等，守卫恒失效
"""
import ast
import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

# ---------- 1. 根因：threading.Timer 禁 name kwarg（ast 守卫） ----------


def _parse_main():
    with open(os.path.join(ROOT, "main.py"), encoding="utf-8") as f:
        return ast.parse(f.read())


def test_main_timer_call_has_no_name_kwarg():
    """threading.Timer(...) 调用不得携带 name 关键字（TypeError 曾致启动失败）。"""
    for node in ast.walk(_parse_main()):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr == "Timer":
            for kw in node.keywords:
                assert kw.arg != "name", \
                    "threading.Timer 不接受 name kwarg（Timer(interval, function, args, kwargs)）；" \
                    "如需命名请在创建后对实例赋 .name"


def test_main_has_startup_failure_exit_guard():
    """启动失败必须显式退出进程：_start_failed 标志 + main() finally os._exit 兜底。"""
    src = open(os.path.join(ROOT, "main.py"), encoding="utf-8").read()
    assert "self._start_failed" in src, "start() 失败/未初始化路径须置 _start_failed 标志"
    assert "os._exit(1)" in src, "main() 须有 _start_failed → os._exit(1) 半死状态阻断"


def test_main_stop_isolates_service_stop_errors(monkeypatch):
    """main.stop() 托管服务逐个隔离：单服务 stop 异常不得打断其余服务的停止。"""
    from main import MainApplication

    app = MainApplication("config.json")
    # stop() 的模块级判空全走 None 分支，聚焦托管服务循环
    app.task_scheduler = None
    app.input_handler = None
    app.conversation_manager = None
    app.web_server = None

    calls = []

    class _Boom:
        name = "boom-svc"

        def stop(self):
            calls.append("boom-svc")
            raise RuntimeError("boom")

    class _Ok:
        name = "ok-svc"

        def stop(self):
            calls.append("ok-svc")

    class _FakeRegistry:
        def all(self):
            return [_Boom(), _Ok()]

    import utils.service_orchestrator as so
    monkeypatch.setattr(
        so.ServiceRegistry, "instance",
        classmethod(lambda cls: _FakeRegistry()), raising=False)

    app.stop()  # 不应抛出
    assert calls == ["boom-svc", "ok-svc"], \
        "boom 服务异常后 ok 服务仍须被停止（逐服务 try 隔离）"


# ---------- 2. GptSovitsInproc.stop 协议 + start 守卫（行为测试） ----------


class _FakeConfig:
    def get(self, *keys):
        return None


class _FakePaths:
    autolive_home = os.getcwd()
    gpt_sovits_root = os.path.join(os.getcwd(), "apps", "gpt-sovits")
    gpt_sovits_python = "python"


def _make_engine():
    from utils.gpt_sovits_inproc import GptSovitsInproc
    return GptSovitsInproc(_FakeConfig(), _FakePaths())


def test_gsv_inproc_has_stop_protocol():
    """ManagedProcess.stop 协议必须存在——缺失曾致 main.stop() 循环 AttributeError。"""
    from utils.gpt_sovits_inproc import GptSovitsInproc
    assert callable(getattr(GptSovitsInproc, "stop", None))


def test_gsv_stop_unloads_and_sets_abort_flag():
    from utils.gpt_sovits_inproc import ST_UNINIT, ST_READY
    eng = _make_engine()
    eng._tts = MagicMock()
    eng._tts_cfg = MagicMock()
    eng._set_state(ST_READY)
    eng.stop()
    assert eng.raw_state() == ST_UNINIT
    assert eng._tts is None
    assert eng._tts_cfg is None
    assert eng._stop_requested is True
    assert not eng.ready


def test_gsv_load_honors_stop_requested():
    """加载期间收到 stop()：加载完成不得置 READY/不得残留引擎引用（防复活）。"""
    import utils.gpt_sovits_inproc as mod
    from utils.gpt_sovits_inproc import ST_UNINIT
    eng = _make_engine()
    eng._stop_requested = True
    with patch.object(mod.GptSovitsInproc, "_build_tts", return_value=MagicMock()):
        eng._load()
    assert eng.raw_state() == ST_UNINIT
    assert eng._tts is None
    assert not eng.ready


def test_gsv_start_guard_blocks_when_ready():
    """start() 守卫用 raw_state 原词判定：ready 态不得再起加载线程。"""
    from utils.gpt_sovits_inproc import ST_READY
    eng = _make_engine()
    eng._tts = MagicMock()
    eng._set_state(ST_READY)
    import utils.gpt_sovits_inproc as mod
    with patch.object(mod.threading, "Thread") as th:
        eng.start()
        th.assert_not_called(), "ready 态 start() 必须被守卫拦截（原 state 映射词守卫恒失效）"


def test_gsv_start_resets_stop_flag_and_recovers():
    """stop() 后 start() 重启：中止标志重置，加载完成正常置 READY。"""
    import utils.gpt_sovits_inproc as mod
    from utils.gpt_sovits_inproc import ST_READY, ST_UNINIT
    eng = _make_engine()
    eng._stop_requested = True
    eng._set_state(ST_UNINIT)
    with patch.object(mod.GptSovitsInproc, "_build_tts", return_value=MagicMock()):
        eng.start(blocking=True)  # 同步执行 _load
    assert eng._stop_requested is False
    assert eng.raw_state() == ST_READY


# ---------- 3. 编排器 audio_cancel 接线宿主（O8） ----------


def test_choreo_cancel_wired_to_audio_host():
    """audio_cancel 须取自 My_handle.audio（QueueMixin 宿主）——My_handle 自身
    无 cancel_choreo_audio，原写法 AttributeError 致编排器装配整体失败。"""
    src = open(os.path.join(ROOT, "utils", "my_handle.py"), encoding="utf-8").read()
    assert "audio_cancel=self.audio.cancel_choreo_audio" in src
    assert "audio_cancel=self.cancel_choreo_audio" not in src


def test_my_handle_class_has_no_cancel_choreo_audio_attr():
    """结构性对照：My_handle 类体不提供 cancel_choreo_audio（该方法唯一宿主
    是 QueueMixin/Audio）——装配点必须经 audio 实例引用。"""
    src = open(os.path.join(ROOT, "utils", "my_handle.py"), encoding="utf-8").read()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "My_handle":
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    assert item.name != "cancel_choreo_audio", \
                        "My_handle 上出现同名方法即本守卫前提失效，请重新评估装配写法"
