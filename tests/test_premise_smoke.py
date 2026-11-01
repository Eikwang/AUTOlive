# -*- coding: utf-8 -*-
"""T4 前提表→运行时冒烟断言守卫（/autoplan main-20261031-201249 终门批准批次）。

TODOS 2026-10-20 #3 到期义务（native CEO F1/F3/F5——不留开放式 defer）。

两层：
- 静态断言（本文件）：前提表"出边有消费端"类的当前有效子集——生产 type 键面/
  路由产线注册/报警模块开关面三组单源一致性（trends_copywriting 型断链的防复发锚）
- 运行期冒烟（utils/startup_smoke.py + TestStartupSmoke）：路径可达/开关存在类
  部署态检查（A1-A4 例证），启动线程 warn 一次

前提表溯源：docs/flowchart-detail.md Review record 0A 节 F1-F26（2026-10-20 快照，
大部分断链前提已被 T5-T9/助播/T10 等后续批次闭环——本断言固化的是"当前仍会
静默失效"的持续有效子集）。
"""
import json
import os
import re
import unittest
from pathlib import Path
from unittest import mock

from utils.startup_smoke import (
    KNOWN_PRODUCER_TYPES, run_startup_smoke, check_badwords_files,
    check_priority_mapping_coverage, check_serial_refs,
)


class TestProducerTypesCovered(unittest.TestCase):
    """生产 type 键面 ⊆ priority_mapping（出边有消费端：入队类型必须可排序）。

    未入键面=按未识别排队（队尾+满则丢弃）——trends_copywriting 断链例证
    （task_scheduler 构造 "copywriting" 不在映射表，UI 优先级不生效）。
    """

    def test_example_config_covers_all_producer_types(self):
        cfg = json.loads(Path("config.example.json").read_text(encoding="utf-8"))
        pm = cfg.get("filter", {}).get("priority_mapping", {})
        missing = sorted(KNOWN_PRODUCER_TYPES - set(pm.keys()))
        self.assertEqual(missing, [], f"config.example priority_mapping 缺键: {missing}")

    def test_producer_types_literal_grep(self):
        """生产点字面 grep：清单外的新 "type": "xxx" 入队构造须登记
        （新产线上线同步三处：KNOWN_PRODUCER_TYPES/静态断言/语义矩阵）。"""
        prod_files = [
            "utils/handlers/feature_handler.py",
            "utils/handlers/event_handler.py",
            "utils/handlers/assistant_router.py",
            "utils/task_scheduler.py",
        ]
        found = set()
        for f in prod_files:
            src = Path(f).read_text(encoding="utf-8")
            found |= set(re.findall(r'"type":\s*"([a-z_0-9]+)"', src))
        # 这些文件里出现的 type 字面必须全部已被键面覆盖或属已知非入队豁免
        known_non_queue = {"local_qa"}  # chat_type 判定值非队列 type
        unknown = sorted(found - KNOWN_PRODUCER_TYPES - known_non_queue)
        self.assertEqual(unknown, [],
                         f"发现未登记的 type 字面（确认入队则补 KNOWN_PRODUCER_TYPES+键面，"
                         f"确认非入队则加豁免）: {unknown}")


class TestRouteProducersRegistered(unittest.TestCase):
    """assistant_route 调用面 ⊆ ROUTE_TABLE 键（调用不存在产线=路由恒 False=断链）。"""

    def test_call_sites_subset_of_table(self):
        from utils.handlers.assistant_router import ROUTE_TABLE
        prod_files = [
            "utils/handlers/comment_handler.py",
            "utils/handlers/chat_handler.py",
            "utils/handlers/event_handler.py",
            "utils/handlers/feature_handler.py",
        ]
        called = set()
        for f in prod_files:
            src = Path(f).read_text(encoding="utf-8")
            called |= set(re.findall(
                r'assistant_route\((?:self\.my_handle\.)?(?:self\.)?config,\s*"([a-z_]+)"', src))
        self.assertTrue(called, "未发现任何 assistant_route 调用——调用面扫描失效自查")
        unknown = sorted(called - set(ROUTE_TABLE.keys()))
        self.assertEqual(unknown, [], f"调用面存在未注册产线（路由恒 False 断链）: {unknown}")


class TestAlarmSwitchFaceSync(unittest.TestCase):
    """报警模块开关面单源：后端 MODULE_LABELS ⊆ 前端 _MODULE_SWITCHES（T1 同步防漂移）。"""

    def test_backend_labels_covered_by_frontend(self):
        from utils.alarm_notifier import MODULE_LABELS
        src = Path("frontend/ui/tabs/system_settings.py").read_text(encoding="utf-8")
        seg = src.split("_MODULE_SWITCHES")[1].split("]")[0]
        frontend_keys = set(re.findall(r'\("([a-z_]+)",', seg))
        missing = sorted(set(MODULE_LABELS.keys()) - frontend_keys)
        self.assertEqual(missing, [],
                         f"后端报警模块在前端开关页缺开关: {missing}")


class TestStartupSmoke(unittest.TestCase):
    """运行期冒烟检查项（A1-A4 例证的部署态覆盖）。"""

    def _dict_cfg(self, **kw):
        return kw

    def test_all_pass_clean_config(self):
        pm = json.loads(Path("config.example.json").read_text(encoding="utf-8")) \
            .get("filter", {}).get("priority_mapping", {})
        warns = run_startup_smoke(self._dict_cfg(
            filter={"enable": True, "badwords": {"enable": True},
                    "priority_mapping": dict(pm)},
            choose_song={"enable": False},
            local_qa={"enable": False},
            key_mapping={"enable": False},
        ))
        # badwords.enable=true 但无 path 键→不检查（与消费端"路径空跳过"同语义）
        self.assertEqual(warns, [])

    def test_badwords_missing_file_warns(self):
        """A4 例证：词表文件缺失=过滤静默失效→启动冒烟可见。"""
        warns = check_badwords_files(self._dict_cfg(
            filter={"enable": True,
                    "badwords": {"enable": True, "path": "Z:/no/such/badwords.txt"}}))
        self.assertEqual(len(warns), 1)
        self.assertIn("静默失效", warns[0])

    def test_badwords_disabled_no_warn(self):
        warns = check_badwords_files(self._dict_cfg(
            filter={"enable": True,
                    "badwords": {"enable": False, "path": "Z:/no/such/badwords.txt"}}))
        self.assertEqual(warns, [])

    def test_priority_mapping_missing_keys_warns(self):
        """存量 config 缺新键（如 local_qa_text）→按未识别排队降级可见。"""
        warns = check_priority_mapping_coverage(self._dict_cfg(
            filter={"priority_mapping": {"comment": 20}}))
        self.assertTrue(any("local_qa_text" in w for w in warns))

    def test_serial_ref_missing_warns(self):
        warns = check_serial_refs(self._dict_cfg(
            key_mapping={"enable": True,
                         "config": [{"keywords": "开大招", "serial_name": "COM99"}]},
            serial={"config": [{"serial_name": "COM3"}]}))
        self.assertEqual(len(warns), 1)
        self.assertIn("COM99", warns[0])

    def test_smoke_never_raises(self):
        """冒烟对畸形 config 永不抛（启动保护面）。"""
        for bad in [None, {}, {"filter": "not-a-dict"}, {"choose_song": {"enable": True}}]:
            try:
                run_startup_smoke(bad)
            except Exception as e:
                self.fail(f"冒烟对 {bad!r} 抛异常: {e}")

    def test_main_wiring_present(self):
        """启动编排接线在场（main.py 延时冒烟线程）。"""
        src = Path("main.py").read_text(encoding="utf-8")
        self.assertIn("run_startup_smoke", src)
        self.assertIn("startup-smoke", src)


if __name__ == "__main__":
    unittest.main()
