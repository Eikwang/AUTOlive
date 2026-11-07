# -*- coding: UTF-8 -*-
"""
变声服务生命周期守卫（T10，/autoplan main-20261106-173018 批次）

守卫矩阵（Eng S3 codepath-to-test）：
- 缺陷1 根修：_start_monitor 幂等——存活监控线程不重复启动（线程增殖防回归）
- #18 收编：ServiceRegistry.register 同名覆盖先 stop 旧实例（孤儿防回归）
- 缺陷3 根修：ensure_rvc_vc 对 ST_RED 在册实例触发 manual_restart 复活（而非
  静默复用死实例）——用户报障「点多少次都 10061」的行为级守卫
- T2/缺陷4/5b：svc.py 失败路径重拉接线+三要素文案源码锚点
- T4/X9+A3+D-D1：设备手输兜底（new_value_mode）+manual 标志+手输态提示锚点
- T3/缺陷5a+D-D2：状态回调接线+休眠态文案锚点
- T9/X8：双队列参数文案锚点（环节+机制标注）

svc.py 为 NiceGUI 闭包结构，UI 行为用源码锚点断言（项目先例：
test_flowchart_alignment 图锚点 lint / F-1a ast 样式守卫）。
"""
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from utils.service_orchestrator import (  # noqa: E402
    ManagedProcess,
    ServiceRegistry,
    ST_RED,
    ST_STOPPED,
)

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


# ----------------------------------------------------------------------
# 缺陷1：_start_monitor 幂等
# ----------------------------------------------------------------------

def test_start_monitor_idempotent(tmp_path):
    """并发/重复 start 只允许一个存活监控线程（缺陷1 根修守卫）。

    回归场景：_monitor_loop 内 _auto_restart→start() 成功路径曾无条件重建
    监控线程，旧线程 continue 存活→线程增殖→共享 fail_count 加速置红。"""
    log = tmp_path / "svc.log"
    log.write_bytes(b"")
    svc = ManagedProcess(
        name="guard-idem",
        command=[sys.executable, "-c", "import time; time.sleep(60)"],
        cwd=str(tmp_path),
        health_url="http://127.0.0.1:1/status",  # 不可达——start 会走超时，无关本测
        port=1,
    )
    try:
        # 手工造一个存活"监控线程"占位（真实监控会拉进程；这里只验幂等判定）
        import threading
        svc._monitor_stop.clear()
        sentinel = threading.Event()
        holder = threading.Thread(target=sentinel.wait, daemon=True)
        holder.start()
        svc._monitor_thread = holder
        assert holder.is_alive()
        svc._start_monitor()  # 幂等：不得替换/新建
        assert svc._monitor_thread is holder, "存活监控线程被替换=增殖回归"
        assert sum(1 for t in threading.enumerate()
                   if t.name == "svc-monitor-guard-idem") == 0, "出现第二个监控线程"
    finally:
        sentinel.set()
        svc._monitor_stop.set()


def test_start_monitor_allows_restart_after_death(tmp_path):
    """stop() 后线程死亡时 _start_monitor 必须能新建（幂等不得变成永不重启）。"""
    import threading
    svc = ManagedProcess(
        name="guard-restart",
        command=[sys.executable, "-c", "pass"],
        cwd=str(tmp_path),
        health_url="http://127.0.0.1:1/status",
        port=1,
    )
    dead = threading.Thread(target=lambda: None, daemon=True)
    dead.start()
    dead.join(timeout=2)
    assert not dead.is_alive()
    svc._monitor_thread = dead
    svc._start_monitor()  # 死线程→新建
    assert svc._monitor_thread is not dead
    assert svc._monitor_thread.is_alive()
    svc._monitor_stop.set()


# ----------------------------------------------------------------------
# #18 收编：register 同名覆盖 stop 旧实例
# ----------------------------------------------------------------------

def test_register_same_name_stops_old(tmp_path):
    """同名重复注册必须先 stop 旧实例（孤儿子进程防回归，TODOS #18）。"""
    reg = ServiceRegistry()
    old = ManagedProcess(
        name="dup-svc",
        command=[sys.executable, "-c", "import time; time.sleep(30)"],
        cwd=str(tmp_path),
        health_url="http://127.0.0.1:1/status",
        port=1,
    )
    old.state = ST_STOPPED  # 不经 start()——只验证 register 的 stop 调用
    old_stopped = []
    old.stop = lambda: old_stopped.append(True)  # type: ignore[method-assign]
    reg.register(old)

    new = ManagedProcess(
        name="dup-svc",
        command=[sys.executable, "-c", "pass"],
        cwd=str(tmp_path),
        health_url="http://127.0.0.1:1/status",
        port=1,
    )
    reg.register(new)
    assert old_stopped, "同名覆盖未停止旧实例（#18 回归）"
    assert reg.get("dup-svc") is new


# ----------------------------------------------------------------------
# 缺陷3：ST_RED 实例复活（ensure_rvc_vc）
# ----------------------------------------------------------------------

def test_red_state_not_reused_silently(tmp_path, monkeypatch):
    """ensure_rvc_vc 对 ST_RED 在册实例必须触发 manual_restart 复活路径。

    回归场景（用户报障）：build ①分支"在册非 stopped→复用跳过"吞掉红实例，
    监控线程已退出、无任何重拉路径——点多少次都 10061。"""
    from utils import rvc_service as rs

    red = ManagedProcess(
        name="rvc-vc",
        command=[sys.executable, "-c", "pass"],
        cwd=str(tmp_path),
        health_url="http://127.0.0.1:1/status",
        port=1,
    )
    red.state = ST_RED
    red.proc = None

    class _FakeRegistry:
        def get(self, name):
            return red

    monkeypatch.setattr(rs.ServiceRegistry, "instance", classmethod(lambda cls: _FakeRegistry()))
    monkeypatch.setattr(rs, "rvc_vc_alive", lambda config, paths, timeout=2.0: False)
    monkeypatch.setattr(rs, "start_idle_monitor", lambda svc, config: None)
    monkeypatch.setattr(rs, "_remember_interpreter", lambda svc: None)  # 记忆隔离（防污染真实 data/）

    revived = {"n": 0}
    red.manual_restart = lambda: revived.__setitem__("n", revived["n"] + 1) or True  # type: ignore[method-assign]

    config = {"rvc_vc": {"hosted": True}}
    svc = rs.ensure_rvc_vc(config, None)
    assert svc is red, "红实例应复用同一对象走复活，而非新建"
    deadline = time.time() + 5
    while revived["n"] == 0 and time.time() < deadline:
        time.sleep(0.05)
    assert revived["n"] == 1, "ST_RED 实例未被 manual_restart 复活（缺陷3 回归）"


def test_stopped_state_still_starts_normally(tmp_path, monkeypatch):
    """stopped 在册实例维持既有后台 start 语义（红态分支不得误伤 stopped）。"""
    from utils import rvc_service as rs

    stopped = ManagedProcess(
        name="rvc-vc",
        command=[sys.executable, "-c", "pass"],
        cwd=str(tmp_path),
        health_url="http://127.0.0.1:1/status",
        port=1,
    )
    stopped.state = ST_STOPPED
    stopped.proc = None

    class _FakeRegistry:
        def get(self, name):
            return stopped

    monkeypatch.setattr(rs.ServiceRegistry, "instance", classmethod(lambda cls: _FakeRegistry()))
    monkeypatch.setattr(rs, "rvc_vc_alive", lambda config, paths, timeout=2.0: False)
    monkeypatch.setattr(rs, "_remember_interpreter", lambda svc: None)

    started = {"n": 0}
    stopped.start = lambda: started.__setitem__("n", started["n"] + 1) or True  # type: ignore[method-assign]
    monkeypatch.setattr(rs, "start_idle_monitor", lambda svc, config: None)

    config = {"rvc_vc": {"hosted": True}}
    svc = rs.ensure_rvc_vc(config, None)
    assert svc is stopped
    deadline = time.time() + 5
    while started["n"] == 0 and time.time() < deadline:
        time.sleep(0.05)
    assert started["n"] == 1


# ----------------------------------------------------------------------
# 懒启动断链修复（2026-11-06 晚，用户实测：前端单独启动变声卡 starting）
# ----------------------------------------------------------------------

def test_ensure_boots_fresh_instance(tmp_path, monkeypatch):
    """懒启动路径：新构造实例必须被 boot（start 被调用）——断链防回归。

    回归场景：build 只构造（caller_starts 契约），ensure 对 state=starting 的
    新实例落"在途"分支无人 start——服务卡 starting、端口无监听，必须先启动
    主系统才由 main.py:166 承接。"""
    from utils import rvc_service as rs

    calls = {"built": 0, "started": 0, "idle": 0}

    class _FakeRegistry:
        def get(self, name):
            return None  # 不在册——build 走新构造

        def register(self, svc):
            calls["built"] += 1

    monkeypatch.setattr(rs.ServiceRegistry, "instance", classmethod(lambda cls: _FakeRegistry()))
    monkeypatch.setattr(rs, "rvc_vc_alive", lambda config, paths, timeout=2.0: False)
    monkeypatch.setattr(rs, "_remember_interpreter", lambda svc: None)
    monkeypatch.setattr(rs, "start_idle_monitor", lambda svc, config: calls.__setitem__("idle", calls["idle"] + 1))

    class _FakeSvc:
        name = "rvc-vc"
        state = rs.ST_STARTING
        proc = None
        command = [sys.executable, "-c", "pass"]

        def start(self):
            calls["started"] += 1
            return True

    def _fake_build(config, paths, on_state_change=None, *, caller_starts=True):
        svc = _FakeSvc()
        if not caller_starts:
            def _boot():
                if svc.start():
                    rs.start_idle_monitor(svc, config)
                    rs._remember_interpreter(svc)
            import threading as _t
            _t.Thread(target=_boot, daemon=True).start()
        return svc

    monkeypatch.setattr(rs, "build_rvc_vc_service", _fake_build)
    svc = rs.ensure_rvc_vc({"rvc_vc": {"hosted": True}}, None)
    assert svc is not None
    deadline = time.time() + 5
    while calls["started"] == 0 and time.time() < deadline:
        time.sleep(0.05)
    assert calls["started"] == 1, "懒启动新实例未被 boot（断链回归）"
    assert calls["idle"] == 1, "boot 成功后未接 idle monitor"


def test_build_caller_starts_default_contract(tmp_path, monkeypatch):
    """main.py 契约保持：build 默认（caller_starts=True）只构造不自动 boot。"""
    from utils import rvc_service as rs

    started = {"n": 0}

    class _FakeRegistry:
        def get(self, name):
            return None

        def register(self, svc):
            pass

    monkeypatch.setattr(rs.ServiceRegistry, "instance", classmethod(lambda cls: _FakeRegistry()))
    monkeypatch.setattr(rs, "rvc_vc_alive", lambda config, paths, timeout=2.0: False)

    class _FakeSvc:
        name = "rvc-vc"
        state = rs.ST_STARTING
        proc = None

        def start(self):
            started["n"] += 1
            return True

    monkeypatch.setattr(rs.ManagedProcess, "__init__",
                        lambda self, **kw: None)  # 不真构造
    monkeypatch.setattr(rs.ManagedProcess, "start",
                        lambda self: started.__setitem__("n", started["n"] + 1) or True)
    monkeypatch.setattr(rs, "rvc_runtime_python",
                        lambda config, paths: sys.executable)
    monkeypatch.setattr(rs, "_remember_interpreter", lambda svc: None)
    monkeypatch.setattr(rs, "start_idle_monitor", lambda svc, config: None)

    class _FakePaths:
        autolive_home = str(tmp_path)
        rvc_root = str(tmp_path)

        def subprocess_env(self, extra=None):
            return {}

    svc = rs.build_rvc_vc_service({"rvc_vc": {"hosted": True}}, _FakePaths())
    assert svc is not None
    time.sleep(0.3)  # 给可能的错误 boot 线程时间（不应存在）
    assert started["n"] == 0, "默认契约被破坏：build 自动 boot 了（main.py 会双拉）"


def test_reused_starting_not_double_booted(tmp_path, monkeypatch):
    """在册 starting 实例复用不得重复 boot（防双拉——别人已在 start 在途）。"""
    from utils import rvc_service as rs

    inflight = ManagedProcess(
        name="rvc-vc",
        command=[sys.executable, "-c", "pass"],
        cwd=str(tmp_path),
        health_url="http://127.0.0.1:1/status",
        port=1,
    )
    inflight.state = rs.ST_STARTING
    inflight.proc = None

    class _FakeRegistry:
        def get(self, name):
            return inflight

    monkeypatch.setattr(rs.ServiceRegistry, "instance", classmethod(lambda cls: _FakeRegistry()))

    started = {"n": 0}
    inflight.start = lambda: started.__setitem__("n", started["n"] + 1) or True  # type: ignore[method-assign]

    svc = rs.ensure_rvc_vc({"rvc_vc": {"hosted": True}}, None)
    assert svc is inflight
    time.sleep(0.3)
    assert started["n"] == 0, "在途 starting 实例被重复 start（双拉回归）"


# ----------------------------------------------------------------------
# T2/T3/T4/T9：svc.py 与文案源码锚点
# ----------------------------------------------------------------------

def _read(rel):
    with open(os.path.join(REPO, rel), encoding="utf-8") as f:
        return f.read()


def test_svc_one_shot_start():
    """整合批次（2026-11-07，用户预期 2）：一条龙启动语义——拉服务→等就绪→
    切模型→开流，全程按钮进度反馈；不再裸 HTTP 立即报错让用户猜重试。"""
    src = _read(os.path.join("frontend", "ui", "tabs", "svc.py"))
    assert "def _wait_ready" in src, "缺等服务就绪步骤"
    assert "_wait_ready(90)" in src, "等待未接线"
    assert "ensure_rvc_vc(config, AutolivePaths(config)" in src, "拉服务未接线"
    assert "pick_default_model(config, models_dir)" in src, "预切模型未接线"
    for step in ("启动中...（服务准备）", "启动中...（准备模型）", "启动中...（开启音频流）"):
        assert step in src, f"按钮进度文案缺：{step}"
    assert "_ensure_backend" not in src, "旧失败路径重拉残留（已内联一条龙）"


def test_svc_local_device_enum():
    """整合批次（用户预期 1）：设备枚举本地 sounddevice 优先——服务零依赖。"""
    src = _read(os.path.join("frontend", "ui", "tabs", "svc.py"))
    assert "def _enum_local" in src, "缺本地枚举"
    assert "import sounddevice as sd" in src
    assert "_enum_via_service" in src, "缺服务枚举兜底"
    # 进页不再自动拉起（预期 4：未执行变声不要进程）
    assert "_lazy_ensure" not in src, "进页自动拉起残留"
    assert "background_tasks.create(_lazy_ensure())" not in src


def test_svc_capsule_flip_and_quiet_copy():
    """整合批次（用户预期 4）：胶囊按流状态翻转+安静文案。"""
    src = _read(os.path.join("frontend", "ui", "tabs", "svc.py"))
    assert "show_in_statusbar = bool(running)" in src, "运行中胶囊翻转缺失"
    assert "变声未运行——配置后点「启动变声」开始" in src, "缺安静文案"
    # 懒实例胶囊静默（rvc_service 侧）
    src2 = _read(os.path.join("utils", "rvc_service.py"))
    assert "svc.show_in_statusbar = False" in src2, "懒实例缺静默属性"
    # 胶囊区过滤
    src3 = _read(os.path.join("frontend", "ui", "components", "service_status.py"))
    assert 'getattr(s, "show_in_statusbar", True)' in src3, "胶囊区缺过滤"


def test_main_auto_stream_on_switch():
    """整合批次（用户预期 3）：开关开+启动主系统=自动开流+失败报警。"""
    src = _read("main.py")
    assert "def _boot_and_stream" in src, "缺自动开流"
    assert "pick_default_model" in src, "自动开流缺预切模型"
    assert "client.start_stream" in src, "自动开流缺开流"
    assert "MODULE_VOICE_CHANGE" in src, "开流失败缺报警（用户裁决）"
    src2 = _read(os.path.join("utils", "alarm_notifier.py"))
    assert 'MODULE_VOICE_CHANGE: "实时变声"' in src2, "报警标签缺"


def test_svc_error_messages_three_part():
    """缺陷5b+F-N3：错误文案三要素化+就绪时序预期（替代"修正后再点"误导）。"""
    src = _read(os.path.join("frontend", "ui", "tabs", "svc.py"))
    assert "def _format_vc_error" in src
    assert "修正后可直接再点启动" not in src, "误导旧文案回归"
    for part in ("问题=", "原因=", "修复="):
        assert part in src, f"三要素缺 {part}"
    assert "10-60" in src, "缺就绪时序预期（F-N3）"


def test_svc_manual_input_fallback_and_protection():
    """X9+A3：手输能力+手输值保护（枚举本地化后手输为第三兜底）。"""
    src = _read(os.path.join("frontend", "ui", "tabs", "svc.py"))
    assert "new_value_mode='add-unique'" in src, "设备下拉缺手输能力"
    assert src.count("new_value_mode='add-unique'") == 2, "输入/输出两个下拉都要有"
    assert '"manual"' in src, "缺手输标志（A3 补枚举保护）"


def test_svc_lifecycle_state_visibility():
    """缺陷5a+D-D2：状态回调接线+休眠态文案+置红态文案。"""
    src = _read(os.path.join("frontend", "ui", "tabs", "svc.py"))
    assert "_on_svc_state_change" in src
    assert "闲置休眠" in src, "缺休眠态文案（D-D2）"
    assert "置红" in src, "缺置红态文案（缺陷5a）"
    assert "_svc_state_text" in src


def test_rvc_runtime_helpers_anchor():
    """rvc_service 侧锚点：write_interpreter_memory 接线+复活日志。"""
    src = _read(os.path.join("utils", "rvc_service.py"))
    assert "write_interpreter_memory(\"rvc_python\"" in src, "缺记忆写回（T5/F-N2）"
    assert "manual_restart" in src
    assert "rvc-vc-revive" in src, "置红复活线程缺命名"


def test_queue_labels_distinct_mechanism():
    """X8/L2：两队列参数 label/tooltip 标注队列环节与机制（文案锚点）。"""
    play = _read(os.path.join("frontend", "ui", "tabs", "audio_play.py"))
    queue = _read(os.path.join("frontend", "ui", "tabs", "filter_queue.py"))
    assert "播放缓冲队列上限（背压）" in play, "音频播放页 label 未标注"
    assert "audio_player" in play and "queue_max" in play
    assert "待播音频队列容量（优先级丢弃）" in queue, "互动设置页 label 未标注"
    assert "另见" in play and "另见" in queue, "缺跨页互引"
    assert "待播队列上限" not in play, "旧混淆 label 残留"
    assert "音频播放队列最大保留长度" not in queue, "旧混淆 label 残留"
