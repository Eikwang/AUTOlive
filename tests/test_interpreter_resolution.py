# -*- coding: UTF-8 -*-
"""
解释器副本优先候选链守卫（T10，/autoplan main-20261106-173018 批次 T5）

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

from utils import autolive_paths as ap  # noqa: E402


@pytest.fixture
def mem_file(tmp_path, monkeypatch):
    """把记忆文件指到 tmp，隔离真实 data/。"""
    f = tmp_path / "interpreter_memory.json"
    monkeypatch.setattr(ap, "_interpreter_memory_file", lambda: str(f))
    return f


def _resolve(config, tmp_path, monkeypatch, *, repo_exists=True, external_exists=True,
             repo_name="repo_python.exe", external=r"D:\ext\python.exe"):
    """跑 resolve_python_interpreter，用 monkeypatch 控制 isfile 世界。"""
    real_isfile = os.path.isfile

    def fake_isfile(p):
        p = str(p)
        if os.path.normpath(p) == os.path.normpath(str(tmp_path / repo_name)):
            return repo_exists
        if os.path.normpath(p) == os.path.normpath(external):
            return external_exists
        if p.startswith("config-only-"):
            return True
        if p.startswith("mem-only-"):
            return True
        return real_isfile(p)

    monkeypatch.setattr(ap.os.path, "isfile", fake_isfile)
    monkeypatch.setattr("frontend.config.paths.get_base_path", lambda: str(tmp_path))
    return ap.resolve_python_interpreter(
        config,
        keys=[("paths", "rvc_python")],
        repo_rel=repo_name,
        external_default=external,
        memory_key="rvc_python",
    )


def test_order_config_override_wins(tmp_path, mem_file, monkeypatch):
    """覆盖键存在且指向真实文件 → 最高优先。"""
    mem_file.write_text('{"rvc_python": "mem-only-mem.exe"}', encoding="utf-8")
    cfg = {"paths": {"rvc_python": "config-only-cfg.exe"}}
    got = _resolve(cfg, tmp_path, monkeypatch, repo_exists=False, external_exists=False)
    assert got == os.path.abspath("config-only-cfg.exe")


def test_order_memory_then_repo_then_external(tmp_path, mem_file, monkeypatch):
    """无覆盖键：记忆 > 仓库副本 > 外部树。"""
    mem_file.write_text('{"rvc_python": "mem-only-mem.exe"}', encoding="utf-8")
    cfg = {}
    got = _resolve(cfg, tmp_path, monkeypatch, repo_exists=False, external_exists=False)
    assert got == os.path.abspath("mem-only-mem.exe")

    mem_file.write_text("{}", encoding="utf-8")
    got = _resolve(cfg, tmp_path, monkeypatch, repo_exists=True, external_exists=True)
    assert got == os.path.abspath(str(tmp_path / "repo_python.exe"))

    got = _resolve(cfg, tmp_path, monkeypatch, repo_exists=False, external_exists=True)
    assert got == os.path.abspath(r"D:\ext\python.exe")


def test_config_override_missing_falls_through(tmp_path, mem_file, monkeypatch):
    """覆盖键指向不存在路径 → 告警并回退候选链（不失败）。"""
    mem_file.write_text("{}", encoding="utf-8")
    cfg = {"paths": {"rvc_python": r"D:\ghost\not-there\python.exe"}}
    got = _resolve(cfg, tmp_path, monkeypatch, repo_exists=False, external_exists=True)
    assert got == os.path.abspath(r"D:\ext\python.exe")


def test_all_missing_returns_empty_string(tmp_path, mem_file, monkeypatch):
    """全候选缺失 → 空串（调用方三要素报错，不抛异常）。"""
    mem_file.write_text("{}", encoding="utf-8")
    cfg = {}
    got = _resolve(cfg, tmp_path, monkeypatch, repo_exists=False, external_exists=False)
    assert got == ""


def test_corrupt_memory_skipped(tmp_path, mem_file, monkeypatch):
    """记忆文件损坏 → 跳过记忆继续候选链。"""
    mem_file.write_text("{not json", encoding="utf-8")
    cfg = {}
    got = _resolve(cfg, tmp_path, monkeypatch, repo_exists=True, external_exists=False)
    assert got == os.path.abspath(str(tmp_path / "repo_python.exe"))


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


def test_memory_write_and_read_roundtrip(tmp_path, monkeypatch):
    """记忆写读回环（write_interpreter_memory/read_interpreter_memory）。"""
    f = tmp_path / "interpreter_memory.json"
    monkeypatch.setattr(ap, "_interpreter_memory_file", lambda: str(f))
    ap.write_interpreter_memory("rvc_python", r"D:\x\python.exe")
    assert ap.read_interpreter_memory()["rvc_python"] == r"D:\x\python.exe"
