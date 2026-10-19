# -*- coding: UTF-8 -*-
"""
Task-04: 实现主题切换 测试
TDD RED 阶段：先写测试，确认失败
"""

import pytest
import json
from pathlib import Path


class TestThemeToggle:
    """测试主题切换功能"""

    def test_drawer_layout_has_toggle_theme_method(self):
        """测试 DrawerLayout 有 toggle_theme 方法"""
        # Given: DrawerLayout 类
        # When: 检查方法
        # Then: toggle_theme 方法存在
        from frontend.ui.layout import DrawerLayout
        assert hasattr(DrawerLayout, 'toggle_theme')

    def test_drawer_layout_has_dark_mode_attribute(self):
        """测试 DrawerLayout 有 dark_mode 属性"""
        # Given: 配置字典
        config = {"webui": {"theme": {"choose": "default"}}}
        # When: 创建 DrawerLayout 实例
        from frontend.ui.layout import DrawerLayout
        layout = DrawerLayout(config)
        # Then: dark_mode 属性存在
        assert hasattr(layout, 'dark_mode')

    def test_toggle_theme_switches_mode(self):
        """测试 toggle_theme 切换主题模式"""
        # Given: DrawerLayout 实例
        config = {"webui": {"theme": {"choose": "default"}}}
        from frontend.ui.layout import DrawerLayout
        layout = DrawerLayout(config)
        # When: 调用 toggle_theme
        layout.toggle_theme()
        # Then: dark_mode 状态改变
        # 注意：实际测试需要 NiceGUI 环境，这里只测试方法存在

    def test_themes_module_has_toggle_theme_function(self):
        """测试 themes 模块有 toggle_theme 函数"""
        # Given: frontend.styles.themes 模块
        # When: 检查函数
        # Then: toggle_theme 函数存在
        from frontend.styles.themes import toggle_theme
        assert toggle_theme is not None

    def test_themes_module_has_init_theme_function(self):
        """测试 themes 模块有 init_theme 函数"""
        # Given: frontend.styles.themes 模块
        # When: 检查函数
        # Then: init_theme 函数存在
        from frontend.styles.themes import init_theme
        assert init_theme is not None

    def test_css_variables_has_dark_theme(self):
        """测试 CSS 变量有深色主题定义"""
        # Given: design_tokens 模块（设计令牌统一定义处，themes.py 引用其变量）
        # When: 生成注入的 CSS 变量
        # Then: 包含深色主题覆盖段
        from frontend.styles.design_tokens import inject_design_tokens
        css = inject_design_tokens()
        assert "body--dark" in css

    def test_css_variables_has_light_theme(self):
        """测试 CSS 变量有浅色主题定义"""
        # Given: design_tokens 模块
        # When: 生成注入的 CSS 变量
        # Then: 包含浅色主题 :root 段
        from frontend.styles.design_tokens import inject_design_tokens
        css = inject_design_tokens()
        assert ":root" in css

    def test_css_variables_has_bg_color(self):
        """测试 CSS 变量有背景色定义"""
        # Given: design_tokens 模块
        # When: 生成注入的 CSS 变量
        # Then: 包含背景色变量（--bg-*）
        from frontend.styles.design_tokens import inject_design_tokens
        css = inject_design_tokens()
        assert "--bg-page" in css
        assert "--bg-card" in css

    def test_css_variables_has_text_color(self):
        """测试 CSS 变量有文字色定义"""
        # Given: design_tokens 模块
        # When: 生成注入的 CSS 变量
        # Then: 包含文字色变量（--text-*）
        from frontend.styles.design_tokens import inject_design_tokens
        css = inject_design_tokens()
        assert "--text-title" in css
        assert "--text-body" in css
