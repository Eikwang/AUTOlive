# -*- coding: UTF-8 -*-
"""
Task-06: 实现功能列表显示 测试
TDD RED 阶段：先写测试，确认失败
"""

import pytest
import json
from pathlib import Path
from unittest.mock import MagicMock, patch


def _init_theme(config):
    """初始化全局主题管理器（_render_tab_content 依赖）"""
    from frontend.styles.themes import init_theme_manager
    init_theme_manager(config)


class TestFunctionListDisplay:
    """测试功能列表显示"""

    def test_drawer_layout_has_show_function_list_method(self):
        """测试 DrawerLayout 有 show_function_list 方法"""
        # Given: DrawerLayout 类
        # When: 检查方法
        # Then: show_function_list 方法存在
        from frontend.ui.layout import DrawerLayout
        assert hasattr(DrawerLayout, 'show_function_list')

    def test_drawer_layout_has_function_list_container(self):
        """测试 DrawerLayout 有 function_list_container 属性"""
        # Given: 配置字典
        config = {"webui": {"theme": {"choose": "default"}}}
        # When: 创建 DrawerLayout 实例
        from frontend.ui.layout import DrawerLayout
        layout = DrawerLayout(config)
        # Then: function_list_container 属性存在
        assert hasattr(layout, 'function_list_container')

    def test_show_function_list_displays_functions(self):
        """测试 show_function_list 显示功能列表"""
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
        # When: 创建 DrawerLayout 实例并调用 show_function_list
        from frontend.ui.layout import DrawerLayout
        from unittest.mock import MagicMock
        layout = DrawerLayout(config)
        # 模拟 splitter 的 before 和 after 容器
        mock_fl = MagicMock()
        mock_fl.__enter__ = MagicMock(return_value=mock_fl)
        mock_fl.__exit__ = MagicMock(return_value=False)
        layout.function_list = mock_fl
        mock_cp = MagicMock()
        mock_cp.__enter__ = MagicMock(return_value=mock_cp)
        mock_cp.__exit__ = MagicMock(return_value=False)
        layout.config_page = mock_cp
        _init_theme(config)
        # 布局层按 tab_key 分发到 tab 模块（任务26），patch 掉避免真实渲染
        with patch('frontend.ui.tabs.common_config.create_common_config_tab'):
            layout.show_function_list("基础设置")
        # Then: function_list.clear 被调用
        layout.function_list.clear.assert_called()

    def test_show_function_list_clears_previous_content(self):
        """测试 show_function_list 清除之前的内容"""
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
                    },
                    {
                        "name": "直播互动",
                        "icon": "live_tv",
                        "functions": [
                            {"name": "积分", "enabled": True, "tab_key": "integral"}
                        ]
                    }
                ]
            }
        }
        # When: 创建 DrawerLayout 实例并调用 show_function_list 两次
        from frontend.ui.layout import DrawerLayout
        from unittest.mock import MagicMock
        layout = DrawerLayout(config)
        # 模拟 splitter 的 before 和 after 容器
        mock_fl = MagicMock()
        mock_fl.__enter__ = MagicMock(return_value=mock_fl)
        mock_fl.__exit__ = MagicMock(return_value=False)
        layout.function_list = mock_fl
        mock_cp = MagicMock()
        mock_cp.__enter__ = MagicMock(return_value=mock_cp)
        mock_cp.__exit__ = MagicMock(return_value=False)
        layout.config_page = mock_cp
        _init_theme(config)
        with patch('frontend.ui.tabs.common_config.create_common_config_tab'):
            layout.show_function_list("基础设置")
            layout.show_function_list("直播互动")
        # Then: function_list.clear 被调用两次
        assert mock_fl.clear.call_count >= 2

    def test_drawer_layout_has_function_items_attribute(self):
        """测试 DrawerLayout 有 function_items 属性"""
        # Given: 配置字典
        config = {"webui": {"theme": {"choose": "default"}}}
        # When: 创建 DrawerLayout 实例
        from frontend.ui.layout import DrawerLayout
        layout = DrawerLayout(config)
        # Then: function_items 属性存在
        assert hasattr(layout, 'function_items')

    def test_function_items_initial_value_is_empty_dict(self):
        """测试 function_items 初始值为空字典"""
        # Given: 配置字典
        config = {"webui": {"theme": {"choose": "default"}}}
        # When: 创建 DrawerLayout 实例
        from frontend.ui.layout import DrawerLayout
        layout = DrawerLayout(config)
        # Then: function_items 初始值为空字典
        assert layout.function_items == {}

    def test_show_function_list_creates_function_items(self):
        """测试 show_function_list 创建功能项"""
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
        # When: 创建 DrawerLayout 实例并调用 show_function_list
        from frontend.ui.layout import DrawerLayout
        from unittest.mock import MagicMock
        layout = DrawerLayout(config)
        # 模拟 splitter 的 before 和 after 容器
        mock_fl = MagicMock()
        mock_fl.__enter__ = MagicMock(return_value=mock_fl)
        mock_fl.__exit__ = MagicMock(return_value=False)
        layout.function_list = mock_fl
        mock_cp = MagicMock()
        mock_cp.__enter__ = MagicMock(return_value=mock_cp)
        mock_cp.__exit__ = MagicMock(return_value=False)
        layout.config_page = mock_cp
        _init_theme(config)
        with patch('frontend.ui.tabs.common_config.create_common_config_tab'):
            layout.show_function_list("基础设置")
        # Then: function_items 包含功能项
        assert "通用配置" in layout.function_items
        assert "页面配置" in layout.function_items

    def test_show_function_list_stores_function_info(self):
        """测试 show_function_list 存储功能信息"""
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
        # When: 创建 DrawerLayout 实例并调用 show_function_list
        from frontend.ui.layout import DrawerLayout
        from unittest.mock import MagicMock
        layout = DrawerLayout(config)
        # 模拟 splitter 的 before 和 after 容器
        mock_fl = MagicMock()
        mock_fl.__enter__ = MagicMock(return_value=mock_fl)
        mock_fl.__exit__ = MagicMock(return_value=False)
        layout.function_list = mock_fl
        mock_cp = MagicMock()
        mock_cp.__enter__ = MagicMock(return_value=mock_cp)
        mock_cp.__exit__ = MagicMock(return_value=False)
        layout.config_page = mock_cp
        _init_theme(config)
        with patch('frontend.ui.tabs.common_config.create_common_config_tab'):
            layout.show_function_list("基础设置")
        # Then: function_items 存储功能信息
        # （任务6：enabled 随迁移退役，仅存 tab_key）
        assert layout.function_items["通用配置"] == {"tab_key": "common_config"}

    def test_show_function_list_for_unknown_group(self):
        """测试 show_function_list 处理未知分组"""
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
        # When: 创建 DrawerLayout 实例并调用 show_function_list
        from frontend.ui.layout import DrawerLayout
        layout = DrawerLayout(config)
        layout.show_function_list("未知分组")
        # Then: function_items 为空
        assert layout.function_items == {}
