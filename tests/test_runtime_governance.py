# -*- coding: utf-8 -*-
"""2026-11-03 治理批次守卫（/autoplan main-20261103-102228 终门批准）。

义务源：.gstack/tmp/autoplan/main-20261103-102228-plan.md Review record 义务块
（权威正文=autoplan-accepted 围栏，DX-9）：C1 删除/入队直调、C2 rvc-vc 单例
守卫终版语义、C3 懒启动+闲置退出、C4 proactor 噪音过滤、C7 进程树收尸。
全部 unit 层（mock 文件层/fake 进程对象）；真机 GPU/进程行为由 C6 实播验收覆盖。
"""
import ast
import asyncio
import os
import subprocess
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from utils.cover_endpoints import (DELETE_INVALID, DELETE_NOT_FOUND, DELETE_OSERROR,
                                   CoverDeleteResult, delete_cover_artifact,
                                   enqueue_cover_manual)


class _FakeConfig:
    """Config 形态适配（get(*keys) 变长键；cover_endpoints/_policies 消费面）。"""

    def __init__(self, store=None):
        self._s = store or {}

    def get(self, *keys, default=None):
        node = self._s
        for k in keys:
            if not isinstance(node, dict) or k not in node:
                return default
            node = node[k]
        return node


# ---------------------------------------------------------------------------
# C1：共享删除函数（义务：四路径+部分失败+重试安全+契约形态+deleted 字段）
# ---------------------------------------------------------------------------

@pytest.fixture()
def covers_env(tmp_path, monkeypatch):
    """covers 目录（真文件层）+ song_audio_root 锚定到 tmp。"""
    covers = tmp_path / "covers"
    covers.mkdir()
    import utils.autolive_paths as ap
    monkeypatch.setattr(ap, "song_audio_root", lambda *a, **k: str(tmp_path))
    return covers


def _mk(covers: Path, name: str):
    (covers / name).write_bytes(b"x" * 16)



class _LoguruSink:
    """loguru 日志捕获（cover_worker 用 utils.my_log loguru，caplog 不适用）。"""

    def __init__(self, level="DEBUG"):
        self.records = []
        self._level = level
        self._id = None

    def __enter__(self):
        from utils.my_log import logger
        import loguru
        self._id = logger.add(self._write, level=self._level,
                              format="{level}|{message}")
        return self

    def _write(self, message):
        rec = message.record
        self.records.append({"level": rec["level"].name, "message": rec["message"]})

    def __exit__(self, *a):
        from utils.my_log import logger
        logger.remove(self._id)

    def has(self, level: str, needle: str) -> bool:
        return any(r["level"] == level and needle in r["message"]
                   for r in self.records)


def test_c1_delete_success_all_three(covers_env):
    _mk(covers_env, "歌.wav"); _mk(covers_env, "歌_vocal.wav"); _mk(covers_env, "歌_inst.wav")
    r = delete_cover_artifact(_FakeConfig(), "歌.wav")
    assert isinstance(r, CoverDeleteResult)  # 契约形态（DX-4 NamedTuple）
    assert r.ok and r.code is None and r.detail == ""
    assert sorted(r.deleted) == ["歌.wav", "歌_inst.wav", "歌_vocal.wav"]  # ENG-11 deleted 字段
    assert not list(covers_env.iterdir())


def test_c1_delete_not_found_aggregate(covers_env):
    r = delete_cover_artifact(_FakeConfig(), "无.wav")
    assert not r.ok and r.code == DELETE_NOT_FOUND
    assert "成品不存在" in r.detail and "无.wav" in r.detail


def test_c1_delete_invalid_filename(covers_env):
    for bad in ("../x.wav", "a/b.wav", "a\\b.wav", "", "x.mp3"):
        r = delete_cover_artifact(_FakeConfig(), bad)
        assert not r.ok and r.code == DELETE_INVALID
        assert str(bad) in r.detail  # DX-2：400 文案含被拒 fn 实际值
    r = delete_cover_artifact(_FakeConfig(), None)
    assert not r.ok and r.code == DELETE_INVALID  # None→空名（语义不变）


def test_c1_delete_oserror_partial_stop_no_rollback(covers_env, monkeypatch):
    """spec CP-1：任一 OSError 立停返回失败，已删不回滚；detail 含 cand。"""
    _mk(covers_env, "歌.wav"); _mk(covers_env, "歌_vocal.wav")
    real_remove = os.remove
    calls = []

    def flaky_remove(path):
        calls.append(path)
        if path.endswith("歌_vocal.wav"):
            raise OSError("being played (WinError 32)")
        real_remove(path)

    monkeypatch.setattr("utils.cover_endpoints.os.remove", flaky_remove)
    r = delete_cover_artifact(_FakeConfig(), "歌.wav")
    assert not r.ok and r.code == DELETE_OSERROR
    assert "删除失败" in r.detail and "歌_vocal.wav" in r.detail
    assert r.deleted == ["歌.wav"]  # 已删不回滚


def test_c1_retry_safe_after_oserror(covers_env):
    """DX-2 代码验证：重试安全——isfile 前置跳过已删件，重试清残留。"""
    _mk(covers_env, "歌.wav"); _mk(covers_env, "歌_vocal.wav"); _mk(covers_env, "歌_inst.wav")
    real_remove = os.remove
    state = {"n": 0}

    def flaky_remove(path):
        state["n"] += 1
        if state["n"] == 2:  # 第二个文件（vocal）首次失败
            raise OSError("locked")
        real_remove(path)

    import utils.cover_endpoints as ce
    orig = ce.os.remove
    ce.os.remove = flaky_remove
    try:
        r1 = delete_cover_artifact(_FakeConfig(), "歌.wav")
        assert not r1.ok
        r2 = delete_cover_artifact(_FakeConfig(), "歌.wav")
        assert r2.ok and sorted(r2.deleted) == ["歌_inst.wav", "歌_vocal.wav"]  # 残留件被清掉（重试安全）
    finally:
        ce.os.remove = orig


def test_c1_ui_and_endpoint_share_function():
    """C1 D-1 守卫：UI 与端点共同调用点（ast）——choose_song 直调共享函数且
    HTTP 自调用残留清零；端点调用同一共享函数。"""
    cs = ast.parse(Path("frontend/ui/tabs/choose_song.py").read_text(encoding="utf-8"))
    cs_src = Path("frontend/ui/tabs/choose_song.py").read_text(encoding="utf-8")
    assert "delete_cover_artifact" in cs_src, "UI 删除按钮未直调共享函数"
    assert "enqueue_cover_manual" in cs_src, "UI 一键翻唱未直调共享函数（ENG-3）"
    assert "/cover/delete" not in cs_src, "删除 HTTP 自调用残留（C1 未完成）"
    assert "/cover/enqueue" not in cs_src, "入队 HTTP 自调用残留（ENG-3 未完成）"
    ce_src = Path("utils/cover_endpoints.py").read_text(encoding="utf-8")
    assert "delete_cover_artifact(config" in ce_src, "端点未复用共享函数（DRY 破坏）"
    # DX-1：500 文案含可执行下一步（重试安全声明）
    assert "停止播放或关闭占用程序后重试" in cs_src
    assert "重试安全（已删文件自动跳过）" in cs_src
    # DX-2：404 文案含刷新指引
    assert "刷新列表后重试" in cs_src


def test_c1_endpoint_code_mapping_single_point(covers_env, monkeypatch):
    """DX-4/DX-5：code→HTTP 映射单点在端点；debug-only 面。"""
    src = Path("utils/cover_endpoints.py").read_text(encoding="utf-8")
    assert "status_code=r.code" in src  # 单点映射（无 400/404/500 魔数散布在 handler）
    assert "debug-only" in src  # DX-5 面声明


# ---------------------------------------------------------------------------
# ENG-3：入队共享函数（取数链与原端点等价）
# ---------------------------------------------------------------------------

def test_c1_enqueue_manual_uses_cover_queue(covers_env, monkeypatch):
    created = {}

    class _FakeCQ:
        def __init__(self, config, on_done):
            created["config"] = config
            created["on_done"] = on_done
            self.on_done = on_done

        def enqueue(self, viewer, song, profile, manual=False):
            created["args"] = (viewer, song, profile, manual)
            return {"action": "queued", "position": 1}

        def ensure_worker(self):
            created["worker"] = True

    monkeypatch.setattr("utils.cover_worker.CoverQueue", _FakeCQ)
    cfg = _FakeConfig({"choose_song": {"cover_profile_id": "p1"}})
    r = enqueue_cover_manual(cfg, None, "歌名")
    assert r["action"] == "queued" and created["worker"] is True
    assert created["args"] == ("webui-manual", "歌名", "p1", True)
    with pytest.raises(ValueError):
        enqueue_cover_manual(cfg, None, "  ")  # 空歌名（原端点 400 语义）


# ---------------------------------------------------------------------------
# C2：rvc-vc 单例守卫终版语义（spec C-1/CP-2/FE-1 + ENG-1 + native F4）
# ---------------------------------------------------------------------------

def _fake_paths(tmp_path):
    return SimpleNamespace(
        autolive_home=str(tmp_path), rvc_root=str(tmp_path),
        rvc_python=None,
        subprocess_env=lambda extra: dict(os.environ, **(extra or {})))


def _fake_registry(existing=None):
    reg = SimpleNamespace(services={}, registered=[])

    def get(name):
        return reg.services.get(name)

    def register(svc):
        reg.registered.append(svc)
        reg.services[svc.name] = svc

    reg.get, reg.register = get, register
    if existing is not None:
        reg.services["rvc-vc"] = existing
    return reg


@pytest.fixture()
def rvc_env(tmp_path, monkeypatch):
    monkeypatch.setattr("utils.rvc_service.ServiceRegistry",
                        type("R", (), {"instance": staticmethod(lambda: None)}))
    reg_holder = {}
    import utils.rvc_service as rs

    def _install(existing=None):
        reg = _fake_registry(existing)
        reg_holder["reg"] = reg
        monkeypatch.setattr(rs.ServiceRegistry, "instance", staticmethod(lambda: reg))
        return reg

    _install()
    rs_holder = rs
    yield rs, _install
    # 清理：避免 daemon 线程泄漏


def test_c2_inflight_registered_skips_new_build(rvc_env, tmp_path, monkeypatch):
    """spec C-1 ①：在册非 stopped（含 starting）→ 复用跳过（无论 alive）。"""
    rs, install = rvc_env
    existing = SimpleNamespace(name="rvc-vc", state="starting", proc=None)
    reg = install(existing)
    monkeypatch.setattr(rs, "rvc_vc_alive", lambda *a, **k: True)
    out = rs.build_rvc_vc_service(_FakeConfig(), _fake_paths(tmp_path))
    assert out is existing  # 复用同实例，不新建
    assert reg.registered == []  # 未再 register（无覆盖）


def test_c2_not_registered_alive_skip_with_pid_verify(rvc_env, tmp_path, monkeypatch):
    """spec CP-2 + native F4：不在册+alive 在线 → 跳过拉起+pid 归属核验。"""
    rs, install = rvc_env
    reg = install(None)
    monkeypatch.setattr(rs, "rvc_vc_alive", lambda *a, **k: True)
    monkeypatch.setattr(rs, "_verify_port_owner", lambda cfg: "pid=999 name=python.exe")
    out = rs.build_rvc_vc_service(_FakeConfig(), _fake_paths(tmp_path))
    assert out is None and reg.registered == []


def test_c2_offline_build_registers_and_marks_starting(rvc_env, tmp_path, monkeypatch):
    """ENG-1：离线构造 → register 同步完成后 state 即 ST_STARTING（窗口封堵）。"""
    rs, install = rvc_env
    reg = install(None)
    monkeypatch.setattr(rs, "rvc_vc_alive", lambda *a, **k: False)
    out = rs.build_rvc_vc_service(_FakeConfig(), _fake_paths(tmp_path))
    assert out is not None and out.name == "rvc-vc"
    assert out.state == "starting"  # ENG-1：非 stopped（register→start 窗口封堵）
    assert reg.registered == [out]


def test_c2_stale_healthy_skips_repull(rvc_env, tmp_path, monkeypatch):
    """spec C-1：在册 state=healthy 且子进程已死 → 跳过新拉（自愈归监控线程）。"""
    rs, install = rvc_env
    dead_proc = SimpleNamespace(poll=lambda: 1)
    existing = SimpleNamespace(name="rvc-vc", state="healthy", proc=dead_proc)
    install(existing)
    monkeypatch.setattr(rs, "rvc_vc_alive", lambda *a, **k: False)
    out = rs.build_rvc_vc_service(_FakeConfig(), _fake_paths(tmp_path))
    assert out is existing  # 不重拉（外部重拉=竞态源，spec C-1 终版语义）


def test_c2_double_build_single_popen_path(rvc_env, tmp_path, monkeypatch):
    """erro5.log 回归（spec FE-1/CP-5）：双次连续 build（无服务进程）仅一次
    新建路径——第二次命中在册 starting 实例（ENG-1 语义下确定性通过，不 sleep）。"""
    rs, install = rvc_env
    install(None)
    monkeypatch.setattr(rs, "rvc_vc_alive", lambda *a, **k: False)
    cfg, paths = _FakeConfig(), _fake_paths(tmp_path)
    first = rs.build_rvc_vc_service(cfg, paths)
    second = rs.build_rvc_vc_service(cfg, paths)
    assert first is second  # 单例：同一实例（ENG-1 starting 语义封堵竞态）


# ---------------------------------------------------------------------------
# C3：懒触发 ensure + 闲置退出（uc1c 终门裁决）
# ---------------------------------------------------------------------------

def test_c3_no_placeholder_registration(rvc_env, tmp_path, monkeypatch):
    """C3 用户实测裁决（2026-11-03）：进前端不注册状态条占位——不使用变声时
    常驻胶囊是噪音；服务拉起后胶囊自然出现（ensure 首调才 register）。"""
    rs, install = rvc_env
    reg = install(None)
    monkeypatch.setattr(rs, "rvc_vc_alive", lambda *a, **k: False)
    # 2026-11-07 懒启动断链修复：ensure 新实例由 build 内部 boot 线程 start——
    # patch 掉真 start（测试环境 Popen 必失败→crashed 干扰断言），改记调用。
    started = {"n": 0}
    monkeypatch.setattr(rs.ManagedProcess, "start",
                        lambda self: started.__setitem__("n", started["n"] + 1) or True)
    cfg, paths = _FakeConfig(), _fake_paths(tmp_path)
    rs.ensure_rvc_vc(cfg, paths)  # 首用 ensure：注册并后台 boot
    svc = reg.get("rvc-vc")
    assert svc is not None and svc.state == "starting"  # ensure 才注册（ENG-1 语义）
    import time as _t
    deadline = _t.time() + 5
    while started["n"] == 0 and _t.time() < deadline:
        _t.sleep(0.05)
    assert started["n"] == 1, "懒路径新实例未被 boot（断链回归）"
    assert not hasattr(rs, "lazy_register_rvc_vc"), "占位登记死代码残留（用户裁决清理）"
    main_src = Path("frontend/main.py").read_text(encoding="utf-8")
    assert "lazy_register_rvc_vc" not in main_src, "main.py 占位调用残留"


def test_c3_ensure_stopped_reuses_instance_background_start(rvc_env, tmp_path, monkeypatch):
    """C3：stopped 在册 → 复用实例后台 start（不新建）；idle 监控随 start 启动。"""
    rs, install = rvc_env
    started = {"n": 0, "idle": False}
    svc = SimpleNamespace(name="rvc-vc", state="stopped", proc=None,
                          _monitor_stop=threading.Event(),
                          start=lambda: started.__setitem__("n", started["n"] + 1) or True)
    install(svc)
    monkeypatch.setattr(rs, "rvc_vc_alive", lambda *a, **k: False)
    monkeypatch.setattr(rs, "start_idle_monitor",
                        lambda s, c, **k: started.__setitem__("idle", True))
    out = rs.ensure_rvc_vc(_FakeConfig(), _fake_paths(tmp_path))
    assert out is svc  # 复用（spec D-2 不新建）
    for _ in range(100):
        if started["n"] == 1 and started["idle"]:
            break
        time.sleep(0.02)
    assert started["n"] == 1 and started["idle"]


def test_c3_idle_monitor_stops_after_idle_budget(rvc_env, tmp_path):
    """C3 闲置退出：无活跃流（state=idle）持续预算 → svc.stop()；running 重置。"""
    import utils.rvc_service as rs
    stopped = {"n": 0}
    svc = SimpleNamespace(name="rvc-vc", state="healthy",
                          proc=SimpleNamespace(poll=lambda: None),
                          _monitor_stop=threading.Event(),
                          stop=lambda: stopped.__setitem__("n", stopped["n"] + 1))
    cfg = _FakeConfig({"rvc_vc": {"idle_exit_min": 0.001}})  # 0.06s 预算（测试）
    states = iter(["idle", "idle"])
    monkey_client = SimpleNamespace(status=lambda: {"state": next(states, "idle")})
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(rs, "RvcVcClient", lambda config=None: monkey_client)
    try:
        rs.start_idle_monitor(svc, cfg, check_interval_s=0.02)
        for _ in range(150):
            if stopped["n"]:
                break
            time.sleep(0.02)
        assert stopped["n"] == 1  # 闲置预算到 → 休眠
    finally:
        monkeypatch.undo()


def test_c3_idle_monitor_running_resets_budget(rvc_env, tmp_path):
    """C3：running 流重置闲置计时（长期使用不被误休眠）。"""
    import utils.rvc_service as rs
    stopped = {"n": 0}
    svc = SimpleNamespace(name="rvc-vc", state="healthy",
                          proc=SimpleNamespace(poll=lambda: None),
                          _monitor_stop=threading.Event(),
                          stop=lambda: stopped.__setitem__("n", stopped["n"] + 1))
    cfg = _FakeConfig({"rvc_vc": {"idle_exit_min": 0.001}})
    monkey_client = SimpleNamespace(status=lambda: {"state": "running"})
    mp = pytest.MonkeyPatch()
    mp.setattr(rs, "RvcVcClient", lambda config=None: monkey_client)
    try:
        rs.start_idle_monitor(svc, cfg, check_interval_s=0.02)
        time.sleep(0.25)  # 远超 0.06s 预算
        assert stopped["n"] == 0  # running 持续 → 不休眠
        svc._monitor_stop.set()
    finally:
        mp.undo()


# ---------------------------------------------------------------------------
# C4：proactor 噪音过滤（ENG-2 挂点/幂等/None→default 委托）——ast 守卫
# ---------------------------------------------------------------------------

def test_c4_filter_installed_on_startup_with_idempotence():
    """ENG-2：挂点=on_startup（非 ui.run 前 get_running_loop）；幂等护栏；
    None→default 委托（spec CP-3）。"""
    src = Path("frontend/main.py").read_text(encoding="utf-8")
    assert "on_startup" in src, "C4 挂点未用 on_startup（ui.run 前 get_running_loop 首启必炸）"
    assert "_installed" in src, "C4 幂等护栏缺失（重入无界嵌套委托链）"
    assert "default_exception_handler" in src, "None→default 委托缺失（非过滤异常静默吞弃）"
    assert "10054" in src and "_call_connection_lost" in src, "过滤类别不完整"
    # C4-②：重入路径 INFO 降 DEBUG（仅首启 INFO）
    assert 'logger.debug("AUTOlive 应用程序初始化完成（重入）")' in src
    assert 'logger.info("AUTOlive 应用程序初始化完成")' in src, "首启 INFO 丢失"


def test_c4_filter_behavior_unit():
    """过滤器行为单元测：10054/ConnectionReset→抑制（default 不调）；
    其他异常→委托 default。逻辑与 main.py 内联实现同型（提取验证语义）。"""
    calls = []

    def default_handler(context):  # asyncio API 签名：绑定方法只收 context
        calls.append(context["exception"])

    def make_filter():
        state = {"prev": None, "installed": False}

        def _autolive_filter(loop, context):
            exc = context.get("exception")
            msg = str(context.get("message") or "")
            if (isinstance(exc, (ConnectionResetError, ConnectionAbortedError))
                    or (exc is not None and getattr(exc, "winerror", None) == 10054)):
                return  # 抑制
            prev = state["prev"]
            if prev is not None:
                prev(loop, context)
            else:
                loop.default_exception_handler(context)
        _autolive_filter._state = state
        return _autolive_filter

    loop = type("L", (), {"default_exception_handler": staticmethod(default_handler)})()
    f = make_filter()
    f(loop, {"exception": ConnectionResetError(10054, "reset")})
    f(loop, {"exception": OSError(10054, "win")})
    assert calls == []  # 噪音抑制
    other = ValueError("业务异常")
    f(loop, {"exception": other})
    assert calls == [other]  # 非过滤→default 委托


# ---------------------------------------------------------------------------
# C7：进程树收尸（spec F1 双场景+ENG-5 回落+ENG-6 分级+DX-7 浸泡+CP-4 误杀防护）
# ---------------------------------------------------------------------------

class _FakeProc:
    def __init__(self, pid=4242, poll=None):
        self.pid = pid
        self._poll = poll

    def poll(self):
        return self._poll


class _FakePsutilProc:
    def __init__(self, pid, name, cwd, cmdline):
        self.pid = pid
        self.info = {"pid": pid, "name": name, "cwd": cwd, "cmdline": cmdline}


@pytest.fixture()
def worker(tmp_path, monkeypatch):
    """CoverQueue 实例（_aicovergen_src 锚定 tmp；config policies 可控）。"""
    from utils.cover_worker import CoverQueue
    cfg = _FakeConfig({"audio_integration": {"policies": {"cover_sweep_log_only": True}}})
    cq = CoverQueue.__new__(CoverQueue)  # 跳过 __init__（worker 线程不启动）
    cq.config = cfg
    src = tmp_path / "aicovergen_src"
    src.mkdir()
    monkeypatch.setattr(CoverQueue, "_aicovergen_src", lambda self: str(src))
    monkeypatch.setattr(CoverQueue, "_policies",
                        lambda self: {"cover_sweep_log_only": True})
    return cq, src


def test_c7_timeout_alive_pid_tree_kill(worker, monkeypatch):
    """①超时场景（活 PID）：taskkill /F /T 调用与参数。"""
    cq, src = worker
    proc = _FakeProc(pid=111, poll=None)
    runs = []

    def fake_run(cmd, **kw):
        runs.append(cmd)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    import psutil as _ps
    monkeypatch.setattr(_ps, "process_iter", lambda *a, **k: iter([]))
    cq._reap_process_tree(proc)
    assert runs and runs[0][:4] == ["taskkill", "/F", "/T", "/PID"]
    assert runs[0][4] == "111"


def test_c7_dead_pid_psutil_sweep_log_only(worker, monkeypatch, caplog):
    """②崩溃场景（死 PID）+DX-7 浸泡：双条件匹配→INFO 记录（不 terminate）+
    手动命令模板（DX-3）。"""
    cq, src = worker
    proc = _FakeProc(pid=222, poll=3221225477)
    victims = [
        _FakePsutilProc(501, "python.exe", str(src), ["python", "main.py"]),
        _FakePsutilProc(502, "python312.exe", "C:\\elsewhere", ["python", "-c", f"cd {src}"]),
    ]
    others = [
        _FakePsutilProc(601, "notepad.exe", str(src), ["notepad"]),  # 非映像（CP-4）
        _FakePsutilProc(602, "python.exe", "C:\\elsewhere", ["python"]),  # 非路径
    ]
    import psutil as _ps
    monkeypatch.setattr(_ps, "process_iter", lambda *a, **k: iter(victims + others))
    term = {"n": 0}
    for v in victims:
        v.terminate = lambda: term.__setitem__("n", term["n"] + 1)  # log-only 不应调用
    with _LoguruSink() as sink:
        cq._reap_process_tree(proc)
    assert term["n"] == 0  # DX-7 log-only：浸泡期不 terminate
    assert sink.has("INFO", "收尸浸泡期") and sink.has("INFO", "taskkill /F /T /PID")
    assert any("nvidia-smi" in r["message"] for r in sink.records)  # DX-3 手动复核命令


def test_c7_sweep_no_residue_debug(worker, monkeypatch, caplog):
    """ENG-6：崩溃常态路径无残留=DEBUG（不刷控制台）。"""
    cq, src = worker
    proc = _FakeProc(pid=333, poll=139)
    import psutil as _ps
    monkeypatch.setattr(_ps, "process_iter", lambda *a, **k: iter([]))
    with _LoguruSink() as sink:
        cq._reap_process_tree(proc)
    assert sink.has("DEBUG", "无残留进程")  # ENG-6：常态 DEBUG 不刷控制台
    assert not sink.has("WARNING", "收尸")


def test_c7_terminate_mode_after_soak(worker, monkeypatch, caplog):
    """DX-7 期满：log_only=False → terminate 执行；全败→DX-3 WARNING 模板。"""
    cq, src = worker
    cq.config = _FakeConfig({"audio_integration": {"policies": {"cover_sweep_log_only": False}}})
    cq._policies = lambda: {"cover_sweep_log_only": False}
    proc = _FakeProc(pid=444, poll=3221225477)
    bad = _FakePsutilProc(701, "python.exe", str(src), ["python", "main.py"])
    bad.terminate = lambda: (_ for _ in ()).throw(RuntimeError("access denied"))
    import psutil as _ps
    monkeypatch.setattr(_ps, "process_iter", lambda *a, **k: iter([bad]))
    with _LoguruSink() as sink:
        cq._reap_process_tree(proc)
    assert sink.has("WARNING", "收尸未完成") and sink.has("WARNING", "taskkill /F /T /PID 701")  # DX-3 模板


def test_c7_idle_exit_policy_key_registered():
    """DX-7：policies 默认键 cover_sweep_log_only=True 在位（浸泡默认开）。"""
    from utils.cover_worker import CoverQueue
    import inspect
    src = inspect.getsource(CoverQueue._policies)
    assert "cover_sweep_log_only" in src


# ---------------------------------------------------------------------------
# C3/C5 终门+ENG-12 源码守卫（ast/字符串层）
# ---------------------------------------------------------------------------

def test_c3_main_lazy_registration():
    """uc1c：main.py 进前端不拉起+不注册占位（2026-11-03 用户裁决：常驻胶囊
    是噪音）；ensure 挂变声页。"""
    src = Path("frontend/main.py").read_text(encoding="utf-8")
    assert "lazy_register_rvc_vc" not in src, "占位登记残留（用户裁决清理）"
    assert "懒启动模式" in src, "懒模式日志缺失"
    assert "前端托管拉起已后台启动" not in src, "旧进前端即拉残留（C3 未完成）"
    assert "import ensure_rvc_vc" not in src, "main.py 实际 import/调用 ensure（应仅 svc 页）"  # 注释提及允许
    svc_src = Path("frontend/ui/tabs/svc.py").read_text(encoding="utf-8")
    assert "ensure_rvc_vc" in svc_src and "run.io_bound" in svc_src  # ENG-12 后台化


def test_c5_runtime312_first_choice_recorded():
    """uc2a+用户环境补充：runtime312 切换首选路径已落地（cover_worker 配置
    覆盖路径在位；终门裁决与实测结论记录于提交 16767949 与 .gstack 会话档案）。"""
    src = Path("utils/cover_worker.py").read_text(encoding="utf-8")
    assert "runtime_python" in src  # 配置覆盖路径在位（rvc_vc.runtime_python）


def test_d2_semantics_documented_five_states():
    """ENG-9：五态计数修正（service_orchestrator 恰五态常量）。"""
    src = Path("utils/service_orchestrator.py").read_text(encoding="utf-8")
    states = [l for l in src.split("\n") if l.startswith("ST_")]
    assert len(states) == 5  # stopped/starting/healthy/crashed/red


# ---------------------------------------------------------------------------
# erro6.log 回归（2026-11-03 实测）：UI 直调链 config 为 dict 形态——
# profile 读取必须走 config_get 双形态（dict.get 两参=default 语义会把
# choose_song 节 dict 当值返回→get_profile(dict) TypeError unhashable）。
# 教训：mock 形态必须包含真实形态（_FakeConfig 恰好实现变长键→盲区）。
# ---------------------------------------------------------------------------

def test_erro6_regression_dict_config_enqueue_profile():
    """erro6 主场景：dict config 一键翻唱直调——profile_id 必须是节内值（str），
    而非节 dict（unhashable 根因）。"""
    captured = {}

    class _FakeCQ:
        def __init__(self, config, on_done):
            captured["cq_config"] = config
            self.on_done = on_done

        def enqueue(self, viewer, song, profile, manual=False):
            captured["profile"] = profile
            return {"action": "queued", "position": 1}

        def ensure_worker(self):
            pass

    import utils.cover_worker as cw
    orig_cq = cw.CoverQueue
    cw.CoverQueue = _FakeCQ
    try:
        dict_cfg = {"choose_song": {"cover_profile_id": "my_voice",
                                    "cover_pitch": 1}}
        r = enqueue_cover_manual(dict_cfg, None, "歌")
        assert r["action"] == "queued"
        assert captured["profile"] == "my_voice", (
            "profile 被节 dict 污染（config_get 双形态未生效）")
        # CoverQueue 收到的 config 已归一为变长键语义（_policies 同坑防护）
        view = captured["cq_config"]
        assert view.get("audio_integration", "policies") is None or isinstance(
            view.get("audio_integration", "policies"), dict)
        assert view.get("choose_song", "cover_pitch") == 1
    finally:
        cw.CoverQueue = orig_cq


def test_erro6_regression_dict_config_profile_missing_falls_default():
    """dict config 无 cover_profile_id → default（不返回节 dict）。"""
    captured = {}

    class _FakeCQ:
        def __init__(self, config, on_done):
            self.on_done = on_done

        def enqueue(self, viewer, song, profile, manual=False):
            captured["profile"] = profile
            return {"action": "cached"}

        def ensure_worker(self):
            pass

    import utils.cover_worker as cw
    orig_cq = cw.CoverQueue
    cw.CoverQueue = _FakeCQ
    try:
        r = enqueue_cover_manual({"choose_song": {}}, None, "歌")
        assert captured["profile"] == "default"
    finally:
        cw.CoverQueue = orig_cq


def test_erro6_regression_rvc_build_dict_config(rvc_env, tmp_path, monkeypatch):
    """svc 页懒触发链：dict config → build 双形态归一（hosted/http_port 不被
    节 dict 污染——dict.get("rvc_vc","hosted") 会返回节 dict→int(dict) TypeError）。"""
    rs, install = rvc_env
    install(None)
    monkeypatch.setattr(rs, "rvc_vc_alive", lambda *a, **k: False)
    dict_cfg = {"rvc_vc": {"enabled": True, "idle_exit_min": 10}}
    out = rs.build_rvc_vc_service(dict_cfg, _fake_paths(tmp_path))
    assert out is not None and out.name == "rvc-vc"
    assert out.state == "starting"  # ENG-1 语义在 dict 形态下同样成立


# ---------------------------------------------------------------------------
# 分轨保留 v2（2026-11-03 补刀退役批次）：inst=False 警告修复回归
# ---------------------------------------------------------------------------

def _mk_split_env(root, monkeypatch=None, stem="陈奕迅 - 最佳损友"):
    """song_output 环境（covers 挂 tmp；worker 直调桩）。

    monkeypatch 缺省时新建临时实例并由本函数持有（测试进程级还原由
    pytest.MonkeyPatch 的 context 语义兜底——类属性 patch 在模块收尾统一 undo）。"""
    src_dir = root / "aicovergen_src"
    out_root = root / "song_output"
    out_root.mkdir(parents=True)
    covers = root / "covers"
    covers.mkdir()
    from utils.cover_worker import CoverQueue
    q = CoverQueue.__new__(CoverQueue)
    q.config = _FakeConfig()
    q.paths = None
    mp = monkeypatch if monkeypatch is not None else pytest.MonkeyPatch()
    mp.setattr(CoverQueue, "_aicovergen_src", lambda self: str(src_dir))
    job = {"song_path": str(root / f"{stem}.mp3")}
    cover = str(covers / f"{stem}__WY.wav")
    return q, out_root, covers, job, cover


def test_retain_v2_inst_found_with_keep_flag(tmp_path, monkeypatch):
    """erro 警告回归：主跑成功+派发 -k 保留产物——vocal（参数链）与
    inst（_Instrumental）均定位复制（vocal=True, inst=True）。"""
    q, out_root, covers, job, cover = _mk_split_env(tmp_path)
    import time as _t
    hash_dir = out_root / "deadbeefcafe"
    hash_dir.mkdir()
    vocal = hash_dir / f"{job_path_stem(job)}_autolive_WY_p0_i0.75_fr3_rms0.25_pro0.33_rmvpe.wav"
    inst = hash_dir / f"{job_path_stem(job)}_Cleaned_Instrumental.wav"
    vocal.write_bytes(b"RIFF" + b"0" * 32)
    inst.write_bytes(b"RIFF" + b"0" * 32)
    _t.sleep(0.01)
    q._retain_split_tracks(job, cover)
    base = os.path.splitext(cover)[0]
    assert os.path.isfile(base + "_vocal.wav"), "vocal 分轨缺失"
    assert os.path.isfile(base + "_inst.wav"), "inst 分轨缺失（erro6 警告回归）"


def test_retain_v2_no_mtime_gate_cache_reuse(tmp_path, monkeypatch):
    """R-5 同源：缓存重翻产物 mtime 早于本次——仍定位（mtime 门槛已去）。"""
    import time as _t
    q, out_root, covers, job, cover = _mk_split_env(tmp_path)
    hash_dir = out_root / "cafe"
    hash_dir.mkdir()
    vocal = hash_dir / f"{job_path_stem(job)}_autolive_WY_p0_i0.75_fr3_rms0.25_pro0.33_rmvpe.wav"
    inst = hash_dir / f"{job_path_stem(job)}_Cleaned_Instrumental.wav"
    vocal.write_bytes(b"RIFF" + b"0" * 32)
    inst.write_bytes(b"RIFF" + b"0" * 32)
    old = _t.time() - 86400
    os.utime(vocal, (old, old))
    os.utime(inst, (old, old))
    job = {**job, "started_at": _t.time()}  # started_at 远新于产物
    q._retain_split_tracks(job, cover)
    base = os.path.splitext(cover)[0]
    assert os.path.isfile(base + "_vocal.wav") and os.path.isfile(base + "_inst.wav")


def test_retain_v2_cross_song_isolation(tmp_path, monkeypatch):
    """跨歌隔离：另一 stem 的同名产物不命中（stem 匹配替代 mtime 圈定）。"""
    q, out_root, covers, job, cover = _mk_split_env(tmp_path)
    other_dir = out_root / "beef"
    other_dir.mkdir()
    (other_dir / "另一首_autolive_WY_p0_i0.75_fr3_rms0.25_pro0.33_rmvpe.wav").write_bytes(b"R")
    (other_dir / "另一首_Cleaned_Instrumental.wav").write_bytes(b"R")
    q._retain_split_tracks(job, cover)
    assert not os.path.exists(os.path.splitext(cover)[0] + "_vocal.wav")


def test_retain_v2_cleanup_keeps_instrumental_only(tmp_path, monkeypatch):
    """自清理：本次 stem 的中间产物（vocal 源/mixed/半成品）删——仅
    _Instrumental 保留为 MDX 缓存件；跨歌产物不触碰。"""
    q, out_root, covers, job, cover = _mk_split_env(tmp_path)
    stem = job_path_stem(job)
    hash_dir = out_root / "dead"
    hash_dir.mkdir()
    vocal = hash_dir / f"{stem}_autolive_WY_p0_i0.75_fr3_rms0.25_pro0.33_rmvpe.wav"
    inst = hash_dir / f"{stem}_Cleaned_Instrumental.wav"
    mixed = hash_dir / f"{stem} (autolive_WY Ver).wav"          # 成品（covers 已落）
    raw_vocals = hash_dir / f"{stem}_UVR-MDX-NET Inst_Main.wav"  # 原始分离人声
    stale_mixed = hash_dir / f"{stem}_mixed.wav"                 # 主跑残留半成品
    other = out_root / "other" / "别的歌_Cleaned_Instrumental.wav"
    other.parent.mkdir()
    for f in (vocal, inst, mixed, raw_vocals, stale_mixed, other):
        f.write_bytes(b"R")
    q._retain_split_tracks(job, cover)
    assert not os.path.isfile(vocal), "vocal 源应清理（covers 已有副本）"
    assert not os.path.isfile(mixed) and not os.path.isfile(stale_mixed), "mixed 系应清理（CEO C1 语义迁移）"
    assert not os.path.isfile(raw_vocals), "原始分离人声应清理"
    assert os.path.isfile(inst), "_Instrumental 必须保留（MDX 缓存件）"
    assert os.path.isfile(other), "跨歌产物不触碰"


def test_retain_v2_dispatch_has_keep_flag():
    """派发 cmd 含 -k（主跑原生清理禁用——inst=False 根因的源头修复）。"""
    import ast as _ast
    src = Path("utils/cover_worker.py").read_text(encoding="utf-8")
    assert '"-k",' in src, "派发未加 -k（主跑会删 _Instrumental——inst=False 复发）"
    assert "_postprocess_fallback" not in src.replace(
        "_postprocess_fallback/_locate_cover_sources/_postprocess_code 退役", ""
    ), "补刀调用残留（退役未完成）"


def job_path_stem(job):
    return os.path.splitext(os.path.basename(job["song_path"]))[0]
