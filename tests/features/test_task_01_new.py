# -*- coding: UTF-8 -*-
"""
Task-01: 创建 drawer 布局结构 测试
TDD RED 阶段：先写测试，确认失败
"""

import pytest
import json
from pathlib import Path


class TestDrawerLayoutStructure:
    """测试 drawer 布局结构"""

    def test_drawer_layout_class_exists(self):
        """测试 DrawerLayout 类存在"""
        # Given: frontend.ui.layout 模块
        # When: 导入 DrawerLayout 类
        # Then: 类存在
        from frontend.ui.layout import DrawerLayout
        assert DrawerLayout is not None

    def test_drawer_layout_has_create_drawer_method(self):
        """测试 DrawerLayout 有 create_drawer 方法"""
        # Given: DrawerLayout 类
        # When: 检查方法
        # Then: create_drawer 方法存在
        from frontend.ui.layout import DrawerLayout
        assert hasattr(DrawerLayout, 'create_drawer')

    def test_drawer_layout_has_create_content_area_method(self):
        """测试 DrawerLayout 有 create_content_area 方法"""
        # Given: DrawerLayout 类
        # When: 检查方法
        # Then: create_content_area 方法存在
        from frontend.ui.layout import DrawerLayout
        assert hasattr(DrawerLayout, 'create_content_area')

    def test_drawer_layout_has_create_function_list_method(self):
        """测试 DrawerLayout 有 create_function_list 方法"""
        # Given: DrawerLayout 类
        # When: 检查方法
        # Then: create_function_list 方法存在
        from frontend.ui.layout import DrawerLayout
        assert hasattr(DrawerLayout, 'create_function_list')

    def test_drawer_layout_has_create_config_page_method(self):
        """测试 DrawerLayout 有 create_config_page 方法"""
        # Given: DrawerLayout 类
        # When: 检查方法
        # Then: create_config_page 方法存在
        from frontend.ui.layout import DrawerLayout
        assert hasattr(DrawerLayout, 'create_config_page')


class TestDrawerLayoutInitialization:
    """测试 DrawerLayout 初始化"""

    def test_drawer_layout_initializes_with_config(self):
        """测试 DrawerLayout 使用配置初始化"""
        # Given: 配置字典
        config = {"webui": {"theme": {"choose": "default"}}}
        # When: 创建 DrawerLayout 实例
        from frontend.ui.layout import DrawerLayout
        layout = DrawerLayout(config)
        # Then: 实例创建成功
        assert layout is not None

    def test_drawer_layout_stores_config(self):
        """测试 DrawerLayout 存储配置"""
        # Given: 配置字典
        config = {"webui": {"theme": {"choose": "default"}}}
        # When: 创建 DrawerLayout 实例
        from frontend.ui.layout import DrawerLayout
        layout = DrawerLayout(config)
        # Then: 配置被正确存储
        assert layout.config == config

    def test_drawer_layout_has_tabs_attribute(self):
        """测试 DrawerLayout 有 tabs 属性"""
        # Given: 配置字典
        config = {"webui": {"theme": {"choose": "default"}}}
        # When: 创建 DrawerLayout 实例
        from frontend.ui.layout import DrawerLayout
        layout = DrawerLayout(config)
        # Then: tabs 属性存在
        assert hasattr(layout, 'tabs')

    def test_drawer_layout_has_tab_panels_attribute(self):
        """测试 DrawerLayout 有 tab_panels 属性"""
        # Given: 配置字典
        config = {"webui": {"theme": {"choose": "default"}}}
        # When: 创建 DrawerLayout 实例
        from frontend.ui.layout import DrawerLayout
        layout = DrawerLayout(config)
        # Then: tab_panels 属性存在
        assert hasattr(layout, 'tab_panels')


class TestCSSVariables:
    """测试 CSS 变量定义"""

    def test_themes_module_has_css_variables(self):
        """测试 themes 模块有 CSS 变量定义"""
        # Given: frontend.styles.themes 模块
        # When: 导入模块
        # Then: 模块存在
        from frontend.styles import themes
        assert themes is not None

    def test_themes_module_has_light_theme_variables(self):
        """测试 themes 模块背景样式走现行 CSS 变量令牌"""
        # Given: frontend.styles.themes 模块
        # When: 检查内容
        # Then: 背景样式经现行令牌 --bg-page/--bg-card 引用
        # （设计令牌重构后旧 --bg-color 已退役，令牌权威定义见
        #   frontend/styles/design_tokens.py，浅深两套各含 bg-page/bg-card）
        from frontend.styles import themes
        import inspect
        source = inspect.getsource(themes)
        assert "--bg-page" in source or "--bg-card" in source

    def test_themes_module_has_dark_theme_variables(self):
        """测试 themes 模块有深色主题变量"""
        # Given: frontend.styles.themes 模块
        # When: 检查内容
        # Then: 包含深色主题变量
        from frontend.styles import themes
        import inspect
        source = inspect.getsource(themes)
        assert ".dark" in source or "dark_theme" in source


class TestBasicStyles:
    """测试基础样式"""

    def test_themes_module_has_card_style(self):
        """测试 themes 模块有卡片样式"""
        # Given: frontend.styles.themes 模块
        # When: 检查内容
        # Then: 包含卡片样式
        from frontend.styles import themes
        import inspect
        source = inspect.getsource(themes)
        assert "card" in source.lower()

    def test_themes_module_has_border_radius(self):
        """测试 themes 模块有圆角样式"""
        # Given: frontend.styles.themes 模块
        # When: 检查内容
        # Then: 包含圆角样式
        from frontend.styles import themes
        import inspect
        source = inspect.getsource(themes)
        assert "border-radius" in source or "border_radius" in source

    def test_themes_module_has_box_shadow(self):
        """测试 themes 模块有阴影样式"""
        # Given: frontend.styles.themes 模块
        # When: 检查内容
        # Then: 包含阴影样式
        from frontend.styles import themes
        import inspect
        source = inspect.getsource(themes)
        assert "box-shadow" in source or "box_shadow" in source
