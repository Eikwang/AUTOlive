# -*- coding: UTF-8 -*-
"""
frozen（Nuitka standalone）语义单测（发行改造阶段 C4，2026-11-22）。

monkeypatch sys.frozen / sys.executable 模拟编译态（不真编译）：
- get_base_path：frozen → exe 所在目录；源码 → 仓库根
- 解释器三链 frozen 解析：= <base>/runtime312/python.exe
- webui 引导件：端口预检、源码模式 no-op
- main.py 三适配点源码断言（防回退）
"""
import os
import socket
import sys

import pytest

from frontend.config import paths as fpaths
from utils import resource_paths as rp


@pytest.fixture
def frozen_at(tmp_path, monkeypatch):
    """模拟 frozen exe 位于 tmp_path/AUTOlive.exe（含首启零配置前置清理）。"""
    exe = tmp_path / "AUTOlive.exe"
    exe.write_bytes(b"")  # sys.executable 仅取路径，不需真实可执行
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    # 清空注册中心 mtime 缓存（tmp 无 config.json → 无覆盖）
    monkeypatch.setattr(rp, "_OVERRIDE_CACHE", {"mtime": None, "data": {}})
    return tmp_path


def test_get_base_path_frozen(frozen_at):
    assert fpaths.get_base_path() == frozen_at


def test_get_base_path_source():
    assert fpaths.get_base_path() == \
        fpaths.Path(__file__).parent.parent.resolve()


def test_runtime_python_frozen(frozen_at):
    """EDTalk/fastrag/主系统链：frozen 下解析 = <base>/runtime312/python.exe。"""
    want = frozen_at / "runtime312" / "python.exe"
    want.parent.mkdir()
    want.write_bytes(b"")
    assert rp.runtime_python({}) == os.path.abspath(str(want))


def test_gpt_sovits_python_frozen(frozen_at):
    """GSV 链 frozen 解析 = <base>/apps/gpt-sovits/runtime/python.exe。"""
    want = frozen_at / "apps" / "gpt-sovits" / "runtime" / "python.exe"
    want.parent.mkdir(parents=True)
    want.write_bytes(b"")
    assert rp.gpt_sovits_python({}) == os.path.abspath(str(want))


def test_rvc_python_frozen(frozen_at):
    """RVC/翻唱链 frozen 解析 = <base>/runtime312/python.exe。"""
    want = frozen_at / "runtime312" / "python.exe"
    want.parent.mkdir()
    want.write_bytes(b"")
    assert rp.rvc_python({}) == os.path.abspath(str(want))


def test_gpt_sovits_runtime312_fallback(frozen_at):
    """GSV 链终级回退（2026-11-22 新增）：apps runtime 缺失 → runtime312。"""
    want = frozen_at / "runtime312" / "python.exe"
    want.parent.mkdir()
    want.write_bytes(b"")
    assert rp.gpt_sovits_python({}) == os.path.abspath(str(want))


def test_ensure_junctions_recreates(tmp_path, monkeypatch):
    """首启 junction 幂等重建：zip 解包后（无链接）三链接重建成功。"""
    from webui import _ensure_junctions
    # 物理目标
    (tmp_path / "models" / "edtalk").mkdir(parents=True)
    (tmp_path / "models" / "gpt_sovits").mkdir(parents=True)
    (tmp_path / "runtime312").mkdir()
    # runtime 占位（2026-11-27 审查修复 #8）：junction 断言的目标文件必须
    # 真实存在——原 `... or True` 恒真断言在链接重建失败时静默放过
    (tmp_path / "runtime312" / "python.exe").write_bytes(b"")
    # 发行包形态：无任何链接（edtalk/、apps/ 为纯目录树）
    (tmp_path / "edtalk").mkdir()
    (tmp_path / "apps" / "gpt-sovits" / "GPT_SoVITS").mkdir(parents=True)

    _ensure_junctions(tmp_path)
    assert (tmp_path / "edtalk" / "ckpts" / "Audio2Lip.pt").exists() or \
        (tmp_path / "edtalk" / "ckpts").is_dir()
    assert (tmp_path / "apps" / "gpt-sovits" / "GPT_SoVITS" /
            "pretrained_models").is_dir()
    assert (tmp_path / "apps" / "gpt-sovits" / "runtime" / "python.exe").exists()
    # 幂等：重跑不报错
    _ensure_junctions(tmp_path)


def test_tuber_root_frozen(frozen_at):
    """角色库 frozen 解析锚定 exe 目录（统一布局 models/tuber）。"""
    assert rp.tuber_root() == frozen_at / "models" / "tuber"


def test_tcp_free_occupied(monkeypatch):
    """端口预检：已占用 → False；空闲 → True。"""
    from webui import _tcp_free
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    port = sock.getsockname()[1]
    try:
        assert _tcp_free("127.0.0.1", port) is False
        assert _tcp_free("127.0.0.1", 0) is True  # 0=系统分配，必然空闲
    finally:
        sock.close()


def test_bootstrap_noop_in_source_mode(monkeypatch):
    """源码模式：引导块直接返回（不 chdir、不弹窗、不退出）。"""
    from webui import bootstrap_frozen
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    if hasattr(sys, "_MEIPASS"):
        monkeypatch.delattr(sys, "_MEIPASS")
    before_cwd = os.getcwd()
    bootstrap_frozen()  # 不应抛错/退出
    assert os.getcwd() == before_cwd


def test_edtalk_resolve_interpreter_delegates(tmp_path, monkeypatch):
    """EDTalk/fastrag 启动点经 resolve_interpreter → 注册中心（薄委托等价）。"""
    from utils.edtalk_realtime import runtime as ed_rt
    sentinel = str(tmp_path / "sentinel.exe")
    monkeypatch.setattr(rp, "runtime_python", lambda config=None: sentinel)
    assert ed_rt.resolve_interpreter({"edtalk_realtime": {}}) == sentinel


def test_main_system_three_adaptation_points():
    """main.py 适配点源码断言（防回退到 sys.executable 拉主系统/源码 execl）。"""
    main_py = os.path.join(os.path.dirname(__file__), "..", "frontend", "main.py")
    src = open(main_py, encoding="utf-8").read()
    # 1) 主系统拉起走注册中心解释器（frozen 下 sys.executable 是 exe 本身）
    assert "interpreter = resource_paths.runtime_python()" in src
    # 2) 重启 frozen 分支（DETACHED 新进程 + 优雅退出；源码分支 execl 保留）
    assert "DETACHED_PROCESS" in src
    assert "os.execl(python, python, *sys.argv)" in src
