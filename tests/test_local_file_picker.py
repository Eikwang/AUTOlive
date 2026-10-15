# -*- coding: utf-8 -*-
"""LocalFilePicker.scan_rows/format_size 纯函数测试（2026-10-15 素材选择重构）。"""
from pathlib import Path

from frontend.ui.components.local_file_picker import format_size, scan_rows


def _make(tmp_path: Path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "a.mp4").write_bytes(b"x" * 2048)
    (tmp_path / "b.MKV").write_bytes(b"x" * 3 * 1024 * 1024)
    (tmp_path / "c.txt").write_text("skip me")
    (tmp_path / ".hidden.mp4").write_bytes(b"x")
    (tmp_path / "sub" / "inner.mp4").write_bytes(b"x")


def test_scan_rows_filters_and_sorts(tmp_path):
    _make(tmp_path)
    rows, truncated = scan_rows(tmp_path)
    # 目录优先、名称不区分大小写升序；.txt 与隐藏文件过滤；扩展名大小写不敏感
    assert [r["name"] for r in rows] == ["sub", "a.mp4", "b.MKV"]
    assert rows[0]["is_dir"] is True
    assert rows[1]["size_text"] == "2.0 KB"
    assert rows[2]["size_text"] == "3.0 MB"
    assert truncated is False


def test_scan_rows_computer_page():
    """folder=None 表示"此电脑"页：列出传入盘符（目录行）。"""
    rows, truncated = scan_rows(None, drives=["C:\\", "D:\\"])
    assert [r["path"] for r in rows] == ["C:\\", "D:\\"]
    assert all(r["is_dir"] for r in rows)
    assert truncated is False


def test_scan_rows_truncation(tmp_path):
    for i in range(30):
        (tmp_path / f"v{i:03d}.mp4").write_bytes(b"x")
    rows, truncated = scan_rows(tmp_path, max_rows=10)
    assert truncated is True
    assert len(rows) == 10


def test_scan_rows_missing_dir(tmp_path):
    rows, truncated = scan_rows(tmp_path / "no_such_dir")
    assert rows == [] and truncated is False


def test_format_size():
    assert format_size(512) == "512 B"
    assert format_size(2048) == "2.0 KB"
    assert format_size(5 * 1024 ** 3) == "5.0 GB"
