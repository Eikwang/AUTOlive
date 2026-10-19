# -*- coding: UTF-8 -*-
"""
Task-14: 实现主题持久化 测试
2026-10-19 门禁A 重写：对齐现行持久化契约——
save_theme_preference/load_theme_preference/theme_preference 空壳 API 已删除
（layout.py 任务7：持久化内联进 toggle_theme），现行形态为：
  - 保存：toggle_theme 翻转后写 config["webui"]["dark_mode"] 并经 save_config()（save_callback）落盘，
    勿触 legacy webui.theme 段；
  - 加载：启动时 main.py 读 webui.dark_mode（get_nested_value，缺失默认 False=浅色）。
"""

import pytest

from frontend.ui.components.config_helper import get_nested_value


class _FakeDarkMode:
    """模拟 NiceGUI ui.dark_mode() 单例（测试仅需 .value 布尔语义）"""

    def __init__(self, value: bool):
        self.value = value


def _make_layout(config, dark_mode_value=True, save_calls=None):
    """构造带注入 dark_mode 的 DrawerLayout（E-F4：禁再 new，测试注入 fake）"""
    from frontend.ui.layout import DrawerLayout

    if save_calls is None:
        save_calls = []
    layout = DrawerLayout(
        config,
        save_callback=lambda: save_calls.append(True),
        dark_mode=_FakeDarkMode(dark_mode_value),
    )
    return layout


class TestThemePersistence:
    """测试主题持久化（现行契约：toggle_theme 写 webui.dark_mode）"""

    def test_toggle_theme_persists_dark_to_config(self):
        """测试 toggle_theme 后深色状态持久化到 config["webui"]["dark_mode"]"""
        # Given: 配置字典与深色态（value=True）的注入 dark_mode
        config = {"webui": {}}
        layout = _make_layout(config, dark_mode_value=True)
        # When: 切换主题（翻转为浅色）
        layout.toggle_theme()
        # Then: 配置中的 dark_mode 更新为 False
        assert config["webui"]["dark_mode"] is False

    def test_toggle_theme_persists_light_to_config(self):
        """测试 toggle_theme 后浅色状态持久化到 config["webui"]["dark_mode"]"""
        # Given: 配置字典与浅色态（value=False）的注入 dark_mode
        config = {"webui": {}}
        layout = _make_layout(config, dark_mode_value=False)
        # When: 切换主题（翻转为深色）
        layout.toggle_theme()
        # Then: 配置中的 dark_mode 更新为 True
        assert config["webui"]["dark_mode"] is True

    def test_toggle_theme_calls_save_callback(self):
        """测试 toggle_theme 落盘（save_config 透传 save_callback）"""
        # Given: 配置字典与落盘调用记录
        config = {"webui": {}}
        save_calls = []
        layout = _make_layout(config, dark_mode_value=True, save_calls=save_calls)
        # When: 切换主题
        layout.toggle_theme()
        # Then: 落盘回调被调用（切换即持久化，任务7/DG-B8）
        assert len(save_calls) == 1

    def test_toggle_theme_flips_dark_mode(self):
        """测试 toggle_theme 翻转注入 dark_mode 的值"""
        # Given: 浅色态的注入 dark_mode
        config = {"webui": {}}
        layout = _make_layout(config, dark_mode_value=False)
        # When: 切换主题
        layout.toggle_theme()
        # Then: dark_mode.value 翻转为 True
        assert layout.dark_mode.value is True

    def test_load_theme_reads_webui_dark_mode(self):
        """测试启动加载读 webui.dark_mode（main.py 现行读法闭环验证）"""
        # Given: toggle_theme 写入后的配置
        config = {"webui": {}}
        layout = _make_layout(config, dark_mode_value=False)
        layout.toggle_theme()
        # When: 按 main.py 的现行读法读回
        theme = get_nested_value(config, "webui", "dark_mode", default=False)
        # Then: 读回切换后的深色态
        assert theme is True

    def test_load_theme_missing_defaults_to_light(self):
        """测试 webui.dark_mode 缺失时默认 False（浅色）"""
        # Given: 无 webui 段的配置
        config = {}
        # When: 按 main.py 的现行读法读回
        theme = get_nested_value(config, "webui", "dark_mode", default=False)
        # Then: 默认浅色（False）
        assert theme is False

    def test_toggle_theme_does_not_touch_legacy_theme_section(self):
        """测试 toggle_theme 勿触 legacy webui.theme 段（layout.py 契约）"""
        # Given: 配置含 legacy webui.theme 段
        config = {"webui": {"theme": {"choose": "default"}}}
        layout = _make_layout(config, dark_mode_value=True)
        # When: 切换主题
        layout.toggle_theme()
        # Then: legacy theme 段保持原样，dark_mode 单独落键
        assert config["webui"]["theme"] == {"choose": "default"}
        assert config["webui"]["dark_mode"] is False

    def test_removed_shell_apis_are_gone(self):
        """测试空壳 API 已删除（save_theme_preference/load_theme_preference/theme_preference）"""
        # Given: DrawerLayout 实例
        config = {"webui": {}}
        layout = _make_layout(config, dark_mode_value=True)
        # Then: 旧空壳 API 不再存在（任务7 已删除，防回归）
        assert not hasattr(layout, "save_theme_preference")
        assert not hasattr(layout, "load_theme_preference")
        assert not hasattr(layout, "theme_preference")
