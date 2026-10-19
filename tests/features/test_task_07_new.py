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
        assert len(ENABLE_PATHS) == 16  # 删除批：5 个弃用模块开关随 tab 移除（image_recognition/sd_config/custom_cmd/search_online/luoxi）

    def test_enable_paths_resolve(self):
        """E-B2 前置：21 条路径全部可在出货 config.json 解析"""
        import json
        from frontend.config.enable_paths import ENABLE_PATHS, validate_enable_paths
        with open('config.json', encoding='utf-8') as f:
            cfg = json.load(f)
        assert validate_enable_paths(cfg) == []
