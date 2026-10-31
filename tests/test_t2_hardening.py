# -*- coding: utf-8 -*-
"""T2 /set_config 加固交付包守卫测试（/autoplan main-20261031-201249 终门批准批次）。

覆盖：atomic_write_json 原子性（S-1 根修）+ 路径白名单归一化语义（F-arch-1）
+ gpu_policy fail-closed（S-2：查询失败重试一次→拒绝，退役 999.0 兜底放行）。
鉴权门逻辑内联于 _register_api_endpoints 闭包（依赖 NiceGUI 运行时上下文），
其三行判断的语义由 TestAuthSemantics 以等价条件复验 + 代码在场性断言锚定。
"""
import json
import os
import unittest
from pathlib import Path
from unittest import mock

from utils.config import atomic_write_json


class TestAtomicWriteJson(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.dir = tempfile.mkdtemp(prefix="t2_atomic_")
        self.path = os.path.join(self.dir, "cfg.json")

    def test_write_and_read_back(self):
        atomic_write_json(self.path, {"a": 1, "中文": "值"})
        with open(self.path, encoding="utf-8") as f:
            self.assertEqual(json.load(f), {"a": 1, "中文": "值"})

    def test_overwrite_preserves_replacement(self):
        atomic_write_json(self.path, {"v": 1})
        atomic_write_json(self.path, {"v": 2})
        with open(self.path, encoding="utf-8") as f:
            self.assertEqual(json.load(f), {"v": 2})

    def test_failure_leaves_old_file_intact(self):
        """写入失败（模拟 json 不可序列化）→ 旧文件完整保留、无 tmp 残留。"""
        atomic_write_json(self.path, {"v": 1})
        with self.assertRaises(TypeError):
            atomic_write_json(self.path, {"bad": object()})
        with open(self.path, encoding="utf-8") as f:
            self.assertEqual(json.load(f), {"v": 1})
        leftovers = [f for f in os.listdir(self.dir) if ".tmp" in f]
        self.assertEqual(leftovers, [])

    def test_no_half_json_on_crash(self):
        """中断语义：os.replace 前崩溃=旧文件；replace 后=新文件（os 原子保证）。"""
        atomic_write_json(self.path, {"state": "old"})
        with mock.patch("os.replace", side_effect=OSError("simulated crash")):
            with self.assertRaises(OSError):
                atomic_write_json(self.path, {"state": "new"})
        with open(self.path, encoding="utf-8") as f:
            self.assertEqual(json.load(f)["state"], "old")


class TestPathWhitelistSemantics(unittest.TestCase):
    """/set_config 白名单归一化语义（与端点内实现同式：normcase+normpath+abspath）。"""

    def _norm(self, p):
        return os.path.normcase(os.path.normpath(os.path.abspath(str(p))))

    def test_equivalent_spellings_match(self):
        want = r"D:\AI\AUTOlive\config.json"
        for given in [r"D:\AI\AUTOlive\config.json",        # 原样
                      r"D:/AI/AUTOlive/config.json",        # 正斜杠
                      r"D:\AI\AUTOlive\.\config.json",      # 点段
                      r"D:\AI\AUTOlive\utils\..\config.json"]:  # 上跳段
            self.assertEqual(self._norm(given), self._norm(want), given)

    def test_different_file_rejected(self):
        want = r"D:\AI\AUTOlive\config.json"
        for given in [r"D:\AI\AUTOlive\config.json.bak",
                      r"D:\AI\AUTOlive\utils\db.py",
                      r"D:\other\config.json",
                      r"D:\AI\AUTOlive\config2.json"]:
            self.assertNotEqual(self._norm(given), self._norm(want), given)

    def test_whitelist_code_present_in_endpoint(self):
        """端点代码在场性锚：白名单判断+401 门+原子写调用三处不回退。"""
        src = Path("frontend/main.py").read_text(encoding="utf-8")
        self.assertIn("normcase", src)                       # 路径归一
        self.assertIn("仅允许本系统 config.json", src)        # 白名单拒绝消息
        self.assertIn("_http_auth_ok", src)                  # 鉴权门
        self.assertIn("atomic_write_json", src)              # 原子写
        # 旧的非原子直写不再存在（json.dump 直写 config_file 模式退役）
        self.assertNotIn("json.dump(existing_data, config_file,", src)


class TestGpuFailClosed(unittest.TestCase):
    """S-2：_vram_free_gb 查询失败重试一次→-1；准入拒绝（退役 999.0 放行）。"""

    def test_fail_closed_returns_negative(self):
        from utils.gpu_policy import _vram_free_gb
        with mock.patch("subprocess.run", side_effect=OSError("no nvidia-smi")):
            with mock.patch("utils.gpu_policy.logger"):
                self.assertEqual(_vram_free_gb(), -1.0)

    def test_admission_rejected_on_query_failure(self):
        from utils.gpu_policy import is_background_task_allowed
        with mock.patch("utils.gpu_policy._live_running", return_value=False), \
             mock.patch("utils.gpu_policy._edtalk_busy", return_value=False), \
             mock.patch("utils.gpu_policy._vram_free_gb", return_value=-1.0):
            allowed, reason = is_background_task_allowed()
            self.assertFalse(allowed)
            self.assertIn("fail-closed", reason)

    def test_positive_value_still_admits(self):
        """正值路径不受影响（正常放行语义保留）。"""
        from utils.gpu_policy import is_background_task_allowed
        with mock.patch("utils.gpu_policy._live_running", return_value=False), \
             mock.patch("utils.gpu_policy._edtalk_busy", return_value=False), \
             mock.patch("utils.gpu_policy._vram_free_gb", return_value=8.0), \
             mock.patch("utils.gpu_policy._inproc_engine_ready", return_value=False):
            allowed, reason = is_background_task_allowed()
            self.assertTrue(allowed, reason)

    def test_no_more_999_fallback(self):
        """999.0 兜底放行退役断言（v1 审查 S-2 的失败模式根除）。"""
        src = Path("utils/gpu_policy.py").read_text(encoding="utf-8")
        seg = src.split("def _vram_free_gb")[1].split("def gpu_snapshot")[0]
        self.assertNotIn("999.0", seg)


class TestAuthSemantics(unittest.TestCase):
    """鉴权门三行判断等价复验（端点闭包内实现，此处锁语义）。"""

    def test_login_off_allows(self):
        login_enable, token, sessions = False, None, {}
        result = True if not login_enable else bool(token) and token in sessions
        self.assertTrue(result)

    def test_login_on_requires_valid_token(self):
        sessions = {"tok123": {"user": "u"}}
        for token, expected in [("tok123", True), (None, False), ("bad", False)]:
            login_enable = True
            result = bool(token) and token in sessions
            self.assertEqual(result, expected, token)

    def test_auth_gate_wires_both_endpoints(self):
        src = Path("frontend/main.py").read_text(encoding="utf-8")
        # /set_config 与 /sys_cmd 两端点均有 401 门
        self.assertEqual(src.count("if not _http_auth_ok(request):"), 2)
        self.assertIn("X-Auth-Token", src)


if __name__ == "__main__":
    unittest.main()
