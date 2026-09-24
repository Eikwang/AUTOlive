# -*- coding: UTF-8 -*-
"""
P1-6 单元测试：AutolivePaths 路径解析（P1-2）

覆盖：config 缺省回落、PYTHONPATH 注入清单（users.pth 收编）、
子进程环境构造、tts_infer yaml 缺省位置。
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from utils.autolive_paths import AutolivePaths  # noqa: E402
from utils.config import Config  # noqa: E402


def _paths(tmp_path, monkeypatch, paths_cfg=None):
    cfg_file = tmp_path / "config.json"
    import json
    data = {"paths": paths_cfg} if paths_cfg else {}
    cfg_file.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(Config, "config", None, raising=False)
    return AutolivePaths(Config(str(cfg_file)))


def test_defaults_fall_back_when_unconfigured(tmp_path, monkeypatch):
    """config 无 paths 节 → 全部回落本机缺省（并产生 autolive_home 兜底）。"""
    p = _paths(tmp_path, monkeypatch)
    assert os.path.isabs(p.autolive_home)
    assert p.gpt_sovits_root.lower().endswith("gpt-sovits")
    assert p.gpt_sovits_python.lower().endswith("python.exe")


def test_config_overrides_apply(tmp_path, monkeypatch):
    """config paths 节显式配置 → 覆盖缺省（去硬编码的核心验收）。"""
    custom_root = str(tmp_path / "custom-gsv")
    custom_py = str(tmp_path / "custom-python.exe")
    p = _paths(tmp_path, monkeypatch, {
        "gpt_sovits_root": custom_root,
        "gpt_sovits_python": custom_py,
    })
    assert p.gpt_sovits_root == os.path.abspath(custom_root)
    assert p.gpt_sovits_python == os.path.abspath(custom_py)


def test_pythonpath_entries_userspth_takeover(tmp_path, monkeypatch):
    """PYTHONPATH 注入清单与原 users.pth 六条路径对应；不存在目录跳过不炸。"""
    root = tmp_path / "gsv"
    (root / "GPT_SoVITS" / "BigVGAN").mkdir(parents=True)
    (root / "tools" / "asr").mkdir(parents=True)
    (root / "tools" / "uvr5").mkdir(parents=True)
    p = _paths(tmp_path, monkeypatch, {"gpt_sovits_root": str(root)})
    entries = p.gpt_sovits_pythonpath_entries()
    assert len(entries) == 6  # 根 + GPT_SoVITS + BigVGAN + tools + asr + uvr5
    assert os.path.normcase(os.path.normpath(str(root))) in [
        os.path.normcase(os.path.normpath(e)) for e in entries
    ]
    assert any("BigVGAN" in e for e in entries)


def test_subprocess_env_injects_pythonpath(tmp_path, monkeypatch, monkeypatch_del=None):
    """子进程环境：PYTHONPATH 被注入（users.pth 收编的核心验收）。"""
    root = tmp_path / "gsv"
    root.mkdir()
    p = _paths(tmp_path, monkeypatch, {"gpt_sovits_root": str(root)})
    env = p.subprocess_env()
    assert str(root) in env.get("PYTHONPATH", "")


def test_default_yaml_location(tmp_path, monkeypatch):
    """tts_infer yaml 缺省位置 = AUTOlive/config/tts_infer_probe.yaml（Step 0 验证件）。"""
    p = _paths(tmp_path, monkeypatch)
    assert p.default_tts_infer_yaml().endswith("tts_infer_probe.yaml")
