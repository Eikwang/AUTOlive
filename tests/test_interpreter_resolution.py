# -*- coding: UTF-8 -*-
"""
解释器候选链守卫（2026-11-22 阶段 A3 收编后：守卫对象=注册中心
utils/resource_paths 解释器三链；autolive_paths.resolve_python_interpreter
已薄委托注册中心，原通用参数化链仅为未知 memory_key 兼容壳）。

守卫矩阵（Eng S3）：
- 候选链顺序：config 覆盖键 > 上次记忆 > 仓库副本 > 外部树兜底
- config 覆盖键指向不存在路径 → 回退候选链（不失败）
- 全候选缺失 → 返回空串（不抛异常，调用方三要素报错）
- 记忆文件损坏 → 跳过记忆继续候选链
- rvc_runtime_python：rvc_vc.runtime_python 覆盖语义+不存在回退
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from utils import resource_paths as rp  # noqa: E402


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    """隔离：home 指到 tmp（注册中心经 get_base_path 解析）；记忆目录指到
    tmp/data（注册中心 data_dir() 语义）；外部兜底指到假路径
    （防本机真实外部树 D:\\AI\\EDTalk 干扰）。"""
    mem_dir = tmp_path / "data"
    mem_dir.mkdir()
    monkeypatch.setattr(rp, "data_dir", lambda: mem_dir)
    monkeypatch.setattr(rp, "_EXTERNAL_EDTALK_RUNTIME", r"D:\ext\python.exe")
    monkeypatch.setattr(rp, "_EXTERNAL_RUNTIME312_CLONE", r"D:\ext\python.exe")
    monkeypatch.setattr("frontend.config.paths.get_base_path", lambda: str(tmp_path))
    return tmp_path


def _patch_isfile(monkeypatch, tmp_path, *, repo_exists, external_exists,
                  repo_rel="runtime312/python.exe", external=r"D:\ext\python.exe"):
    """用 monkeypatch 控制 isfile 世界（仓库副本/外部兜底按 tmp_path 展开）。"""
    real_isfile = os.path.isfile

    def fake_isfile(p):
        p = str(p)
        if os.path.normpath(p) == os.path.normpath(str(tmp_path / repo_rel)):
            return repo_exists
        if os.path.normpath(p) == os.path.normpath(external):
            return external_exists
        if p.startswith("config-only-") or p.startswith("mem-only-"):
            return True
        return real_isfile(p)

    monkeypatch.setattr(os.path, "isfile", fake_isfile)


def _write_memory(tmp_path, data: str):
    d = tmp_path / "data"
    d.mkdir(exist_ok=True)
    (d / "interpreter_memory.json").write_text(data, encoding="utf-8")

def test_order_config_override_wins(tmp_path, isolated, monkeypatch):
    """覆盖键存在且指向真实文件 → 最高优先。"""
    _write_memory(tmp_path, '{"rvc_python": "mem-only-mem.exe"}')
    _patch_isfile(monkeypatch, tmp_path, repo_exists=False, external_exists=False)
    cfg = {"paths": {"rvc_python": "config-only-cfg.exe"}}
    assert rp.rvc_python(cfg) == os.path.abspath("config-only-cfg.exe")


def test_order_memory_then_repo_then_external(tmp_path, isolated, monkeypatch):
    """无覆盖键：记忆 > 仓库副本 > 外部树（rvc 链 repo copy=<home>/runtime312）。"""
    _patch_isfile(monkeypatch, tmp_path, repo_exists=False, external_exists=False)
    _write_memory(tmp_path, '{"rvc_python": "mem-only-mem.exe"}')
    assert rp.rvc_python({}) == os.path.abspath("mem-only-mem.exe")

    _write_memory(tmp_path, "{}")
    _patch_isfile(monkeypatch, tmp_path, repo_exists=True, external_exists=True)
    assert rp.rvc_python({}) == os.path.abspath(str(tmp_path / "runtime312" / "python.exe"))

    _patch_isfile(monkeypatch, tmp_path, repo_exists=False, external_exists=True)
    assert rp.rvc_python({}) == os.path.abspath(r"D:\ext\python.exe")


def test_gpt_sovits_chain_order(tmp_path, isolated, monkeypatch):
    """GSV 链同序：记忆 > apps/gpt-sovits/runtime > 外部 clone 兜底。"""
    _write_memory(tmp_path, "{}")
    repo_rel = "apps/gpt-sovits/runtime/python.exe"
    _patch_isfile(monkeypatch, tmp_path, repo_exists=True, external_exists=False,
                  repo_rel=repo_rel)
    assert rp.gpt_sovits_python({}) == os.path.abspath(str(tmp_path / repo_rel))

    _patch_isfile(monkeypatch, tmp_path, repo_exists=False, external_exists=True,
                  repo_rel=repo_rel)
    assert rp.gpt_sovits_python({}) == os.path.abspath(r"D:\ext\python.exe")


def test_config_override_missing_falls_through(tmp_path, isolated, monkeypatch):
    """覆盖键指向不存在路径 → 告警并回退候选链（不失败）。"""
    _write_memory(tmp_path, "{}")
    _patch_isfile(monkeypatch, tmp_path, repo_exists=False, external_exists=True)
    cfg = {"paths": {"rvc_python": r"D:\ghost\not-there\python.exe"}}
    assert rp.rvc_python(cfg) == os.path.abspath(r"D:\ext\python.exe")


def test_all_missing_returns_empty_string(tmp_path, isolated, monkeypatch):
    """全候选缺失 → 空串（调用方三要素报错，不抛异常）。"""
    _write_memory(tmp_path, "{}")
    _patch_isfile(monkeypatch, tmp_path, repo_exists=False, external_exists=False)
    assert rp.rvc_python({}) == ""


def test_corrupt_memory_skipped(tmp_path, isolated, monkeypatch):
    """记忆文件损坏 → 跳过记忆继续候选链。"""
    _write_memory(tmp_path, "{not json")
    _patch_isfile(monkeypatch, tmp_path, repo_exists=True, external_exists=False)
    assert rp.rvc_python({}) == os.path.abspath(str(tmp_path / "runtime312" / "python.exe"))


def test_rvc_runtime_python_override_semantics(tmp_path, monkeypatch):
    """rvc_runtime_python：rvc_vc 覆盖键存在且真实→用之；不存在→告警回退
    paths.rvc_python（候选链结果）；全缺→空串。"""
    from utils import rvc_service as rs

    class _Paths:
        rvc_python = ""

    # 覆盖键缺失 → 走 paths（空）→ 空串
    monkeypatch.setattr(rs.os.path, "isfile", lambda p: False)
    assert rs.rvc_runtime_python({"rvc_vc": {}}, _Paths()) == ""

    # 覆盖键指向真实文件 → 直接采纳
    real = tmp_path / "real_python.exe"
    real.write_bytes(b"")
    real_isfile = os.path.isfile

    def fake_isfile(p):
        if os.path.normpath(p) == os.path.normpath(str(real)):
            return True
        return real_isfile(p)

    monkeypatch.setattr(rs.os.path, "isfile", fake_isfile)
    got = rs.rvc_runtime_python(
        {"rvc_vc": {"runtime_python": str(real)}}, _Paths())
    assert got == os.path.abspath(str(real))

    # 覆盖键不存在 → 回退 paths.rvc_python（候选链）
    _Paths.rvc_python = r"D:\fallback\python.exe"
    got = rs.rvc_runtime_python(
        {"rvc_vc": {"runtime_python": r"D:\ghost\python.exe"}}, _Paths())
    assert got == r"D:\fallback\python.exe"


def test_memory_write_and_read_roundtrip(tmp_path, isolated):
    """记忆写读回环（注册中心 write_interpreter_memory/read_interpreter_memory）。"""
    rp.write_interpreter_memory("rvc_python", r"D:\x\python.exe")
    assert rp.read_interpreter_memory()["rvc_python"] == r"D:\x\python.exe"


def test_legacy_thin_delegate_equivalence(tmp_path, isolated, monkeypatch):
    """autolive_paths.resolve_python_interpreter 已知 memory_key 与注册中心同源
    （薄委托等价性守卫）。"""
    from utils import autolive_paths as ap
    _write_memory(tmp_path, "{}")
    _patch_isfile(monkeypatch, tmp_path, repo_exists=True, external_exists=False)
    got = ap.resolve_python_interpreter(
        {}, keys=[("paths", "rvc_python")],
        repo_rel="runtime312/python.exe",
        external_default=r"D:\ext\python.exe",
        memory_key="rvc_python")
    assert got == os.path.abspath(str(tmp_path / "runtime312" / "python.exe"))
