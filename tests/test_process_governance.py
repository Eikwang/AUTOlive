# -*- coding: utf-8 -*-
"""
进程治理守卫（2026-11-07 批次：停止零残留——用户诉求）

- stop_programs 四层全覆盖：registry 全停 / 外围（edtalk/fastrag）/ main 树 /
  收尸兜底——原实现只停 main（残留直接根源）
- ManagedProcess.stop 树杀（taskkill /F /T）——terminate 不带子树=孤儿根源
- 收尸标记不含 api_v2（backend=api 用户自备，F11 不杀）
- 重启闭环：layout 重启=stop_programs+run_external_program 串联（既有），全停
  后重启自动成立
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


def _read(rel):
    with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), rel),
              encoding="utf-8") as f:
        return f.read()


def test_stop_programs_four_layers():
    """停止零残留：四层顺序齐全（registry/外围/main/收尸）。"""
    src = _read("frontend/main.py")
    assert "for svc in ServiceRegistry.instance().all()" in src, "缺①registry 全停"
    assert 'for name in ("edtalk_realtime", "fastrag")' in src, "缺②外围停止（残留直接根源）"
    assert 'self.stop_program("main")' in src, "缺③主系统树"
    assert "_reap_orphan_processes()" in src, "缺④收尸兜底"


def test_managedprocess_stop_tree_kill():
    """ManagedProcess.stop 树杀：taskkill /F /T 连带子进程树（harvest 池等）。"""
    src = _read(os.path.join("utils", "service_orchestrator.py"))
    assert '["taskkill", "/F", "/T", "/PID", str(self.proc.pid)]' in src, "stop 缺树杀"
    assert src.count('["taskkill", "/F", "/T", "/PID", str(self.proc.pid)]') >= 2, \
        "stop 与 _auto_restart 都要树杀"


def test_reap_markers_exclude_api_v2():
    """收尸标记不含 api_v2（backend=api 用户自备，F11 不拉起不托管——杀用户自备服务违背预期）。"""
    src = _read("frontend/main.py")
    reap_seg = src[src.find("def _reap_orphan_processes"):src.find("def _reap_orphan_processes") + 2000]
    assert 'markers = ("rvc_adapter.py", "realtime_serve", "fastrag")' in reap_seg
    assert '"api_v2.py"' not in reap_seg, "api_v2 不得进收尸标记（用户自备服务）"


def test_restart_chain_reuses_stop_programs():
    """重启闭环：layout 重启=stop_external_program+run_external_program 串联——
    stop_programs 全面化后重启自动全停全启。"""
    src = _read(os.path.join("frontend", "ui", "layout.py"))
    assert "stop_external_program" in src and "run_external_program" in src
    # main.py 停止按钮也走 stop_programs
    src2 = _read("frontend/main.py")
    assert "self.stop_programs()" in src2
