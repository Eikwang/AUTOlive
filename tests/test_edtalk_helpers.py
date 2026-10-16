# -*- coding: utf-8 -*-
"""edtalk_realtime.helpers 检测函数单测

覆盖 query_unity_capture 的注册位置与回退顺序。背景（2026-10-16 实跑修复）：
旧实现查 HKCU\\Software\\UnityCapture\\FilterName——UnityCapture 的 DllInstall
是空实现，从不写该键；本机装有 "Hikonvision V8"（HKLM 服务 CLSID 默认值）
仍被误报"未检测到"。正确位置 = 服务类 CLSID 默认值（HKLM 提权/HKCU 免提权）。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import utils.edtalk_realtime.helpers as hlp  # noqa: E402


class _FakeWinreg:
    """winreg 最小替身：OpenKey 按预置路径表放行，QueryValueEx 读默认值。"""

    HKEY_LOCAL_MACHINE = "HKLM"
    HKEY_CURRENT_USER = "HKCU"

    def __init__(self, table):
        # table: {(hive, path): 默认值字符串}；未预置的路径按不存在抛 OSError
        self.table = table

    class _Key:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def OpenKey(self, hive, path):
        if (hive, path) not in self.table:
            raise FileNotFoundError(path)
        k = self._Key()
        k._value = self.table[(hive, path)]
        return k

    def QueryValueEx(self, key, name):
        assert name == ""  # 只读默认值（摄像头名存放处）
        return (key._value, 1)


def _clsid_paths():
    return ["Software\\Classes\\CLSID\\" + c for c in hlp._UNITY_CAPTURE_SERVICE_CLSIDS]


def test_hklm_hit_and_priority(monkeypatch):
    """HKLM 命中 → 返回其名字；HKLM 优先于 HKCU（提权安装是主流路径）。"""
    paths = _clsid_paths()
    fake = _FakeWinreg({
        ("HKLM", paths[0]): "Hikonvision V8",
        ("HKCU", paths[0]): "HKCU 名",
    })
    monkeypatch.setattr(hlp, "_winreg", fake)
    assert hlp.query_unity_capture() == (True, "Hikonvision V8")


def test_hkcu_fallback(monkeypatch):
    """HKLM 全 miss → HKCU 命中（含 32 位 CLSID 变体）。"""
    paths = _clsid_paths()
    fake = _FakeWinreg({("HKCU", paths[1]): "备用摄像头"})
    monkeypatch.setattr(hlp, "_winreg", fake)
    assert hlp.query_unity_capture() == (True, "备用摄像头")


def test_all_miss_returns_false(monkeypatch):
    """两个 hive 全部未注册 → (False, "")（缺失态警示分支）。"""
    monkeypatch.setattr(hlp, "_winreg", _FakeWinreg({}))
    assert hlp.query_unity_capture() == (False, "")


def test_blank_name_treated_missing(monkeypatch):
    """默认值为空串视同未装 → 继续尝试下一个候选。"""
    paths = _clsid_paths()
    fake = _FakeWinreg({("HKLM", paths[0]): "", ("HKLM", paths[1]): "真名"})
    monkeypatch.setattr(hlp, "_winreg", fake)
    assert hlp.query_unity_capture() == (True, "真名")


def test_winreg_unavailable(monkeypatch):
    """非 Windows（_winreg=None）→ 恒 (False, "")，不抛异常。"""
    monkeypatch.setattr(hlp, "_winreg", None)
    assert hlp.query_unity_capture() == (False, "")
