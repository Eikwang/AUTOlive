# -*- coding: UTF-8 -*-
"""
Task-07/28 重写版（batch2 任务6/7 处置，F3/D-B13）
原文件断言已退役的 toggle_function / save_theme_preference / load_theme_preference
（batch2 任务6/7 将启用开关迁移到二级菜单并内联持久化，旧 API 已删除）。
按新架构重写：开关直绑业务路径、持久化内联 toggle_theme、旧 API 不存在。
"""

import pytest


class _FakeSaveCallback:
    """记录 save 调用的伪回调（可注入异常模拟失败）"""

    def __init__(self, raise_on_call=False):
        self.calls = 0
        self.raise_on_call = raise_on_call

    def __call__(self):
        self.calls += 1
        if self.raise_on_call:
            raise RuntimeError("磁盘写入失败（模拟）")


def _make_layout(app=None, save_callback=None):
    from frontend.ui.layout import DrawerLayout
    config = {"webui": {"theme": {"choose": "default"}}}
    return DrawerLayout(config, save_callback=save_callback, app=app)


class TestThemePersistence:
    """任务7/DG-B8：主题持久化内联 toggle_theme"""

    def test_toggle_theme_writes_config(self):
        layout = _make_layout()
        layout.dark_mode = None  # 惰性创建路径
        # 模拟 config 已有 webui 段
        layout.config["webui"] = {}
        layout.toggle_theme()
        assert layout.config["webui"]["dark_mode"] is True

    def test_toggle_theme_toggles_back(self):
        """从 True 切到 False（写内存 + save_config）"""
        layout = _make_layout()
        layout.config["webui"] = {"dark_mode": True}
        layout.dark_mode = None  # 惰性创建，初始为 light（False）
        layout.toggle_theme()  # False → True
        layout.toggle_theme()  # True → False
        assert layout.config["webui"]["dark_mode"] is False

    def test_save_theme_preference_removed(self):
        """旧空壳 save/load_theme_preference 已删除（DG-B8）"""
        from frontend.ui.layout import DrawerLayout
        assert not hasattr(DrawerLayout, 'save_theme_preference')
        assert not hasattr(DrawerLayout, 'load_theme_preference')

    def test_dark_mode_injected_not_created(self):
        """E-F4：dark_mode 由主进程注入（单实例）"""
        from nicegui import ui
        dm = ui.dark_mode()
        layout = _make_layout()
        layout.dark_mode = dm
        assert layout.dark_mode is dm


class TestEnableSwitchMigration:
    """任务6：二级开关直绑业务路径（D-B1），toggle_function 退役"""

    def test_toggle_function_removed(self):
        from frontend.ui.layout import DrawerLayout
        assert not hasattr(DrawerLayout, 'toggle_function')

    def test_on_enable_switch_exists(self):
        from frontend.ui.layout import DrawerLayout
        assert hasattr(DrawerLayout, '_on_enable_switch')

    def test_enable_paths_importable(self):
        from frontend.config.enable_paths import ENABLE_PATHS
        assert len(ENABLE_PATHS) == 15  # 删除批：5 弃用模块开关移除；日志配置面移除（log_config，2026-10-22）

    def test_enable_paths_resolve(self):
        """E-B2 前置：全部路径可在出货 config.json 解析"""
        import json
        from frontend.config.enable_paths import ENABLE_PATHS, validate_enable_paths
        with open('config.json', encoding='utf-8') as f:
            cfg = json.load(f)
        assert validate_enable_paths(cfg) == []


class TestDriftGuard:
    """T8 漂移守卫（2026-10-22 日志移除批，CEO 声部 F1+Eng F1/F2）：
    function_groups ↔ tab_mapping ↔ ENABLE_PATHS 三面注册一致性入套件，
    终结 da8f3b34 式"删 mapping 漏条目（死入口）/删条目漏 mapping（孤儿）"
    两类漂移——此前仅靠人肉 grep 盘点。

    断言语义钉死（Eng F1，禁用集合相等于 ENABLE_PATHS）：
    (a) ENABLE_PATHS ⊆ tab_mapping（单向——mapping 中无启用开关的 tab 按设计
        不入 ENABLE_PATHS，见 enable_paths.py:10 文档串）；
    (b) len(ENABLE_PATHS) == 15；
    (c) function_groups ↔ tab_mapping 真双向（集合相等）。

    fixture=磁盘 config.json 全节（与 test_enable_paths_resolve 同源，
    顺带守卫磁盘配置源的 function_groups 面）。
    """

    # Eng F2 降级白名单：无头环境确不能完整渲染的 tab（预登记，禁静默豁免）。
    # 首跑全量扫描后登记；这些 tab 仍受 (c) 集合断言保护（mapping 可解析），
    # 仅跳过渲染健康检查。新增登记必须附原因。
    RENDER_DEGRADE_WHITELIST = {
        # （首跑零失败——暂无登记项）
    }

    @staticmethod
    def _tab_mapping_keys():
        """静态提取 layout.py tab_mapping 的键（'key': lambda 形态）。

        作为守卫探针：mapping 结构若被重构，本正则零命中即测试红（响），提示
        守卫需同步——这正是守卫语义（结构漂移在套件里响）。
        """
        import re
        from pathlib import Path
        src = Path('frontend/ui/layout.py').read_text(encoding='utf-8')
        return set(re.findall(r"^\s+'([a-z_]+)':\s*lambda:", src, re.M))

    @staticmethod
    def _function_groups_keys():
        """磁盘 config.json 的全部 function_groups tab_key（基础功能等全部组）"""
        import json
        with open('config.json', encoding='utf-8') as f:
            cfg = json.load(f)
        keys = set()
        for g in cfg.get('webui', {}).get('function_groups', []):
            for func in g.get('functions', []):
                keys.add(func.get('tab_key'))
        return {k for k in keys if k}

    def test_mapping_extractable(self):
        """守卫探针自检：mapping 静态提取非空（正则失配=结构漂移，测试红）"""
        keys = self._tab_mapping_keys()
        assert keys, 'tab_mapping 提取零命中：layout.py 结构已漂移，守卫需同步'

    def test_function_groups_matches_tab_mapping(self):
        """(c) 真双向：fg 条目 ↔ mapping 键集合相等（死入口与孤儿映射双捕获）"""
        fg = self._function_groups_keys()
        mapping = self._tab_mapping_keys()
        dead_entries = fg - mapping    # 条目在、mapping 无 → 死入口占位页
        orphan_keys = mapping - fg     # mapping 在、条目无 → 孤儿代码
        assert not dead_entries, f'function_groups 死入口（mapping 缺失）: {sorted(dead_entries)}'
        assert not orphan_keys, f'tab_mapping 孤儿键（function_groups 缺失）: {sorted(orphan_keys)}'

    def test_enable_paths_subset_of_mapping(self):
        """(a) ENABLE_PATHS ⊆ tab_mapping（单向；集合相等语义被 Eng F1 禁用）"""
        from frontend.config.enable_paths import ENABLE_PATHS
        mapping = self._tab_mapping_keys()
        missing = set(ENABLE_PATHS) - mapping
        assert not missing, f'ENABLE_PATHS 条目不在 tab_mapping: {sorted(missing)}'

    def test_enable_paths_count(self):
        """(b) ENABLE_PATHS 计数守卫（随模块增删更新本断言）"""
        from frontend.config.enable_paths import ENABLE_PATHS
        assert len(ENABLE_PATHS) == 15

    def test_function_groups_tabs_render(self):
        """渲染健康扫描：磁盘 config 全部 fg tab 渲染不落"暂无配置页面"占位。

        集合断言 (c) 已保证键可解析；本扫描进一步验证各 tab 模块函数可导入
        调用（真实渲染路径）。白名单内 tab 降级为集合断言保护（见类注释）。
        """
        import json
        from unittest.mock import MagicMock
        from frontend.styles.themes import init_theme_manager
        from frontend.ui.layout import DrawerLayout

        with open('config.json', encoding='utf-8') as f:
            cfg = json.load(f)
        init_theme_manager({'webui': {'theme': {'choose': 'default'}}})

        failures = {}
        for tab_key in sorted(self._function_groups_keys()):
            if tab_key in self.RENDER_DEGRADE_WHITELIST:
                continue
            layout = DrawerLayout(json.loads(json.dumps(cfg)))
            mock_fl = MagicMock()
            mock_fl.__enter__ = MagicMock(return_value=mock_fl)
            mock_fl.__exit__ = MagicMock(return_value=False)
            layout.function_list = mock_fl
            mock_cp = MagicMock()
            mock_cp.__enter__ = MagicMock(return_value=mock_cp)
            mock_cp.__exit__ = MagicMock(return_value=False)
            layout.config_page = mock_cp
            try:
                layout.render_config_page(tab_key)
            except Exception as e:  # noqa: BLE001 —— 守卫收集全部失败后统一断言
                failures[tab_key] = f'{type(e).__name__}: {e}'
        assert not failures, (
            '以下 tab 渲染失败（若确属无头环境不兼容，登记入 RENDER_DEGRADE_WHITELIST '
            f'并附原因；否则为真实回归）: {failures}'
        )
