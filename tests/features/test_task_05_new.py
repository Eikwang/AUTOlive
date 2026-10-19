# -*- coding: UTF-8 -*-
"""
Task-05: 实现导航栏功能分组显示 测试
TDD RED 阶段：先写测试，确认失败
"""

import pytest
import json
from pathlib import Path


class TestNavigationGroupDisplay:
    """测试导航栏功能分组显示"""

    def test_drawer_layout_has_create_navigation_groups_method(self):
        """测试 DrawerLayout 有 create_navigation_groups 方法"""
        # Given: DrawerLayout 类
        # When: 检查方法
        # Then: create_navigation_groups 方法存在
        from frontend.ui.layout import DrawerLayout
        assert hasattr(DrawerLayout, 'create_navigation_groups')

    def test_drawer_layout_has_current_group_attribute(self):
        """测试 DrawerLayout 有 current_group 属性"""
        # Given: 配置字典
        config = {"webui": {"theme": {"choose": "default"}}}
        # When: 创建 DrawerLayout 实例
        from frontend.ui.layout import DrawerLayout
        layout = DrawerLayout(config)
        # Then: current_group 属性存在
        assert hasattr(layout, 'current_group')

    def test_current_group_initial_value_is_none(self):
        """测试 current_group 初始值为 None"""
        # Given: 配置字典
        config = {"webui": {"theme": {"choose": "default"}}}
        # When: 创建 DrawerLayout 实例
        from frontend.ui.layout import DrawerLayout
        layout = DrawerLayout(config)
        # Then: current_group 初始值为 None
        assert layout.current_group is None

    def test_drawer_layout_has_select_group_method(self):
        """测试 DrawerLayout 有 select_group 方法"""
        # Given: DrawerLayout 类
        # When: 检查方法
        # Then: select_group 方法存在
        from frontend.ui.layout import DrawerLayout
        assert hasattr(DrawerLayout, 'select_group')

    def test_select_group_updates_current_group(self):
        """测试 select_group 更新 current_group"""
        # Given: DrawerLayout 实例（模拟 UI 已构建的容器）
        from unittest.mock import MagicMock, patch
        config = {"webui": {"theme": {"choose": "default"}}}
        from frontend.ui.layout import DrawerLayout
        layout = DrawerLayout(config)
        # 注意：__init__ 未初始化 group_nav_container（与 function_list_container
        # 等同类容器不一致，已登记为缺陷），此处手动赋值模拟导航已渲染
        layout.group_nav_container = MagicMock()
        # When: 调用 select_group
        with patch('frontend.ui.layout.ui'):
            layout.select_group("基础设置")
        # Then: current_group 更新
        assert layout.current_group == "基础设置"

    def test_drawer_layout_has_get_group_functions_method(self):
        """测试 DrawerLayout 有 get_group_functions 方法"""
        # Given: DrawerLayout 类
        # When: 检查方法
        # Then: get_group_functions 方法存在
        from frontend.ui.layout import DrawerLayout
        assert hasattr(DrawerLayout, 'get_group_functions')

    def test_get_group_functions_returns_list(self):
        """测试 get_group_functions 返回列表"""
        # Given: 配置字典
        config = {
            "webui": {
                "theme": {"choose": "default"},
                "function_groups": [
                    {
                        "name": "基础设置",
                        "icon": "settings",
                        "functions": [
                            {"name": "通用配置", "enabled": True, "tab_key": "common_config"}
                        ]
                    }
                ]
            }
        }
        # When: 创建 DrawerLayout 实例并调用 get_group_functions
        from frontend.ui.layout import DrawerLayout
        layout = DrawerLayout(config)
        functions = layout.get_group_functions("基础设置")
        # Then: 返回列表
        assert isinstance(functions, list)

    def test_get_group_functions_returns_correct_functions(self):
        """测试 get_group_functions 返回正确的功能列表"""
        # Given: 配置字典
        config = {
            "webui": {
                "theme": {"choose": "default"},
                "function_groups": [
                    {
                        "name": "基础设置",
                        "icon": "settings",
                        "functions": [
                            {"name": "通用配置", "enabled": True, "tab_key": "common_config"},
                            {"name": "页面配置", "enabled": True, "tab_key": "web"}
                        ]
                    }
                ]
            }
        }
        # When: 创建 DrawerLayout 实例并调用 get_group_functions
        from frontend.ui.layout import DrawerLayout
        layout = DrawerLayout(config)
        functions = layout.get_group_functions("基础设置")
        # Then: 返回正确的功能列表
        assert len(functions) == 2
        assert functions[0]["name"] == "通用配置"
        assert functions[1]["name"] == "页面配置"

    def test_get_group_functions_returns_empty_for_unknown_group(self):
        """测试 get_group_functions 对未知分组返回空列表"""
        # Given: 配置字典
        config = {
            "webui": {
                "theme": {"choose": "default"},
                "function_groups": [
                    {
                        "name": "基础设置",
                        "icon": "settings",
                        "functions": [
                            {"name": "通用配置", "enabled": True, "tab_key": "common_config"}
                        ]
                    }
                ]
            }
        }
        # When: 创建 DrawerLayout 实例并调用 get_group_functions
        from frontend.ui.layout import DrawerLayout
        layout = DrawerLayout(config)
        functions = layout.get_group_functions("未知分组")
        # Then: 返回空列表
        assert functions == []
