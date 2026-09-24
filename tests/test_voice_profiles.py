# -*- coding: UTF-8 -*-
"""
P2-5 单元测试：voice_profiles 数据层（P2-4）

覆盖：slug ID 生成与冲突序号（DX-7）、原子写入不破坏其余节（E-4）、
损坏文件容错、更新不换 ID、删除。
"""
import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from utils.voice_profiles import (  # noqa: E402
    delete_profile, get_profile, load_profiles, upsert_profile,
)


def _cfg(tmp_path):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"webui": {"port": 8086}}), encoding="utf-8")
    return str(p)


def test_create_with_slug_id(tmp_path):
    """DX-7：ID = 数据集目录名 slug 化。"""
    cfg = _cfg(tmp_path)
    pid = upsert_profile({"name": "my voice v2"}, cfg)
    assert pid == "my_voice_v2"
    assert get_profile(pid, cfg)["name"] == "my voice v2"


def test_conflict_appends_index(tmp_path):
    """DX-7：重名冲突 → 追加序号，不覆盖。"""
    cfg = _cfg(tmp_path)
    p1 = upsert_profile({"name": "ikaros"}, cfg)
    p2 = upsert_profile({"name": "ikaros"}, cfg)
    assert p1 == "ikaros" and p2 == "ikaros_2"
    assert len(load_profiles(cfg)) == 2


def test_update_keeps_id(tmp_path):
    """改显示名不改 ID（DX-7 核心语义）。"""
    cfg = _cfg(tmp_path)
    pid = upsert_profile({"name": "old_name", "note": "v1"}, cfg)
    upsert_profile({"id": pid, "name": "new_name", "note": "v2"}, cfg)
    prof = get_profile(pid, cfg)
    assert prof["name"] == "new_name" and prof["note"] == "v2"
    assert len(load_profiles(cfg)) == 1


def test_atomic_write_preserves_other_sections(tmp_path):
    """E-4：写入只动 voice_profiles，其余节字节级保留。"""
    cfg = _cfg(tmp_path)
    upsert_profile({"name": "a"}, cfg)
    data = json.load(open(cfg, encoding="utf-8"))
    assert data["webui"] == {"port": 8086}  # 其余节无损
    assert data["voice_profiles"]["a"]["name"] == "a"


def test_corrupt_file_returns_empty_and_not_destroy(tmp_path):
    """损坏文件 → 读返回空且不改写原文件（训练链可继续）。"""
    cfg = tmp_path / "config.json"
    cfg.write_text("{broken json!!", encoding="utf-8")
    assert load_profiles(str(cfg)) == {}
    assert "broken" in cfg.read_text(encoding="utf-8")  # 原文件未被改写


def test_delete(tmp_path):
    cfg = _cfg(tmp_path)
    pid = upsert_profile({"name": "gone"}, cfg)
    assert delete_profile(pid, cfg) is True
    assert get_profile(pid, cfg) is None
    assert delete_profile(pid, cfg) is False
