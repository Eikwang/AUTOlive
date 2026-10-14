# -*- coding: UTF-8 -*-
"""
Task-03: 实现导航栏折叠 测试
TDD RED 阶段：先写测试，确认失败
"""

import pytest
import json
from pathlib import Path


class TestDrawerCollapse:
    """测试导航栏折叠功能"""

    def test_drawer_layout_has_toggle_drawer_method(self):
        """测试 DrawerLayout 有 toggle_drawer 方法"""
        # Given: DrawerLayout 类
        # When: 检查方法
        # Then: toggle_drawer 方法存在
        from frontend.ui.layout import DrawerLayout
        assert hasattr(DrawerLayout, 'toggle_drawer')

    def test_drawer_layout_has_drawer_mini_attribute(self):
        """测试 DrawerLayout 有 drawer_mini 属性"""
        # Given: 配置字典
        config = {"webui": {"theme": {"choose": "default"}}}
        # When: 创建 DrawerLayout 实例
        from frontend.ui.layout import DrawerLayout
        layout = DrawerLayout(config)
        # Then: drawer_mini 属性存在
        assert hasattr(layout, 'drawer_mini')

    def test_drawer_mini_initial_value_is_false(self):
        """测试 drawer_mini 初始值为 False"""
        # Given: 配置字典
        config = {"webui": {"theme": {"choose": "default"}}}
        # When: 创建 DrawerLayout 实例
        from frontend.ui.layout import DrawerLayout
        layout = DrawerLayout(config)
        # Then: drawer_mini 初始值为 False
        assert layout.drawer_mini is False

    def test_toggle_drawer_changes_mini_state(self):
        """测试 toggle_drawer 切换 mini 状态"""
        # Given: DrawerLayout 实例
        config = {"webui": {"theme": {"choose": "default"}}}
        from frontend.ui.layout import DrawerLayout
        layout = DrawerLayout(config)
        # When: 调用 toggle_drawer
        layout.toggle_drawer()
        # Then: drawer_mini 变为 True
        assert layout.drawer_mini is True

    def test_toggle_drawer_twice_returns_to_original(self):
        """测试 toggle_drawer 两次返回原始状态"""
        # Given: DrawerLayout 实例
        config = {"webui": {"theme": {"choose": "default"}}}
        from frontend.ui.layout import DrawerLayout
        layout = DrawerLayout(config)
        # When: 调用 toggle_drawer 两次
        layout.toggle_drawer()
        layout.toggle_drawer()
        # Then: drawer_mini 返回 False
        assert layout.drawer_mini is False

    def test_config_has_drawer_mini_setting(self):
        """测试配置文件有 drawer_mini 设置"""
        # Given: 配置文件
        # When: 读取配置
        # Then: 包含 drawer_mini 设置
        with open("config.json", "r", encoding="utf-8") as f:
            config = json.load(f)
        assert "webui" in config
        assert "drawer_mini" in config["webui"]

    def test_drawer_mini_default_value_is_false(self):
        """测试 drawer_mini 默认值为 False"""
        # Given: 配置文件
        # When: 读取配置
        # Then: drawer_mini 默认值为 False
        with open("config.json", "r", encoding="utf-8") as f:
            config = json.load(f)
        assert config["webui"]["drawer_mini"] is False

    def test_toggle_drawer_saves_to_config(self):
        """测试 toggle_drawer 保存状态到配置"""
        # Given: DrawerLayout 实例
        config = {"webui": {"theme": {"choose": "default"}, "drawer_mini": False}}
        from frontend.ui.layout import DrawerLayout
        layout = DrawerLayout(config)
        # When: 调用 toggle_drawer
        layout.toggle_drawer()
        # Then: 配置中的 drawer_mini 更新
        assert config["webui"]["drawer_mini"] is True
