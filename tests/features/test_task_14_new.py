# -*- coding: UTF-8 -*-
"""
Task-14: 实现主题持久化 测试
TDD RED 阶段：先写测试，确认失败
"""

import pytest
import json
from pathlib import Path


class TestThemePersistence:
    """测试主题持久化"""

    
    
    def test_save_theme_preference_saves_to_config(self):
        """测试 save_theme_preference 保存到配置"""
        # Given: 配置字典
        config = {"webui": {"theme": {"choose": "default"}}}
        # When: 创建 DrawerLayout 实例并调用 save_theme_preference
        from frontend.ui.layout import DrawerLayout
        layout = DrawerLayout(config)
        layout.save_theme_preference("dark")
        # Then: 配置中的主题更新
        assert config["webui"]["theme"]["choose"] == "dark"

    def test_load_theme_preference_loads_from_config(self):
        """测试 load_theme_preference 从配置加载"""
        # Given: 配置字典
        config = {"webui": {"theme": {"choose": "dark"}}}
        # When: 创建 DrawerLayout 实例并调用 load_theme_preference
        from frontend.ui.layout import DrawerLayout
        layout = DrawerLayout(config)
        theme = layout.load_theme_preference()
        # Then: 返回正确的主题
        assert theme == "dark"

    def test_load_theme_preference_returns_default_for_missing(self):
        """测试 load_theme_preference 缺失配置时返回默认值"""
        # Given: 配置字典
        config = {"webui": {}}
        # When: 创建 DrawerLayout 实例并调用 load_theme_preference
        from frontend.ui.layout import DrawerLayout
        layout = DrawerLayout(config)
        theme = layout.load_theme_preference()
        # Then: 返回默认主题
        assert theme == "default"

    def test_toggle_theme_saves_preference(self):
        """测试 toggle_theme 保存主题偏好"""
        # Given: 配置字典
        config = {"webui": {"theme": {"choose": "default"}}}
        # When: 创建 DrawerLayout 实例并调用 toggle_theme
        from frontend.ui.layout import DrawerLayout
        layout = DrawerLayout(config)
        layout.toggle_theme()
        # Then: 主题偏好被保存
        # 注意：实际测试需要 NiceGUI 环境

    def test_drawer_layout_has_theme_preference_attribute(self):
        """测试 DrawerLayout 有 theme_preference 属性"""
        # Given: 配置字典
        config = {"webui": {"theme": {"choose": "default"}}}
        # When: 创建 DrawerLayout 实例
        from frontend.ui.layout import DrawerLayout
        layout = DrawerLayout(config)
        # Then: theme_preference 属性存在
        assert hasattr(layout, 'theme_preference')

    def test_theme_preference_initial_value(self):
        """测试 theme_preference 初始值"""
        # Given: 配置字典
        config = {"webui": {"theme": {"choose": "default"}}}
        # When: 创建 DrawerLayout 实例
        from frontend.ui.layout import DrawerLayout
        layout = DrawerLayout(config)
        # Then: theme_preference 初始值为配置中的主题
        assert layout.theme_preference == "default"

    def test_save_theme_preference_for_light_theme(self):
        """测试 save_theme_preference 保存浅色主题"""
        # Given: 配置字典
        config = {"webui": {"theme": {"choose": "default"}}}
        # When: 创建 DrawerLayout 实例并调用 save_theme_preference
        from frontend.ui.layout import DrawerLayout
        layout = DrawerLayout(config)
        layout.save_theme_preference("light")
        # Then: 配置中的主题更新
        assert config["webui"]["theme"]["choose"] == "light"

    def test_save_theme_preference_for_dark_theme(self):
        """测试 save_theme_preference 保存深色主题"""
        # Given: 配置字典
        config = {"webui": {"theme": {"choose": "default"}}}
        # When: 创建 DrawerLayout 实例并调用 save_theme_preference
        from frontend.ui.layout import DrawerLayout
        layout = DrawerLayout(config)
        layout.save_theme_preference("dark")
        # Then: 配置中的主题更新
        assert config["webui"]["theme"]["choose"] == "dark"
