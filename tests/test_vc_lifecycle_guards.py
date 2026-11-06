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
# T2/T3/T4/T9：svc.py 与文案源码锚点
# ----------------------------------------------------------------------

def _read(rel):
    with open(os.path.join(REPO, rel), encoding="utf-8") as f:
        return f.read()


def test_svc_failure_paths_trigger_backend_ensure():
    """缺陷4：枚举失败与启动失败分支都必须接线 _ensure_backend（后台重拉）。"""
    src = _read(os.path.join("frontend", "ui", "tabs", "svc.py"))
    assert "_ensure_backend()" in src, "缺少失败路径重拉接线"
    assert src.count("_ensure_backend()") >= 3, "枚举失败/启动失败至少两处+定义"
    assert "def _ensure_backend" in src
    # 幂等防抖
    assert "_ensure_lock" in src


def test_svc_error_messages_three_part():
    """缺陷5b+F-N3：错误文案三要素化+就绪时序预期（替代"修正后再点"误导）。"""
    src = _read(os.path.join("frontend", "ui", "tabs", "svc.py"))
    assert "def _format_vc_error" in src
    assert "修正后可直接再点启动" not in src, "误导旧文案回归"
    for part in ("问题=", "原因=", "修复="):
        assert part in src, f"三要素缺 {part}"
    assert "10-60" in src, "缺就绪时序预期（F-N3）"


def test_svc_manual_input_fallback_and_protection():
    """X9+A3+D-D1：手输能力+手输值保护+手输态提示。"""
    src = _read(os.path.join("frontend", "ui", "tabs", "svc.py"))
    assert "new_value_mode='add-unique'" in src, "设备下拉缺手输能力"
    assert src.count("new_value_mode='add-unique'") == 2, "输入/输出两个下拉都要有"
    assert '"manual"' in src, "缺手输标志（A3 补枚举保护）"
    assert "已手输设备名" in src, "缺手输态提示（D-D1）"


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
