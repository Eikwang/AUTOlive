# -*- coding: UTF-8 -*-
"""
Task-18: 集成测试和验证 测试
TDD RED 阶段：先写测试，确认失败
"""

import pytest
import json
from pathlib import Path


class TestIntegration:
    """测试集成"""

    def test_drawer_layout_has_validate_integration_method(self):
        """测试 DrawerLayout 有 validate_integration 方法"""
        # Given: DrawerLayout 类
        # When: 检查方法
        # Then: validate_integration 方法存在
        from frontend.ui.layout import DrawerLayout
        assert hasattr(DrawerLayout, 'validate_integration')

    def test_validate_integration_returns_true_for_valid_config(self):
        """测试 validate_integration 对有效配置返回 True"""
        # Given: 配置字典
        config = {
            "webui": {
                "theme": {"choose": "default"},
                "drawer_mini": False,
                "function_groups": [
                    {
                        "name": "基础设置",
                        "icon": "settings",
                        "functions": [
                            {"name": "通用配置", "enabled": True, "tab_key": "common_config"}
                        ]
                    }
                ],
                "config_items": {
                    "common_config": {
                        "label": "通用配置",
                        "type": "group",
                        "items": {
                            "read_comment": {
                                "label": "读取评论",
                                "type": "switch",
                                "value": True
                            }
                        }
                    }
                }
            }
        }
        # When: 创建 DrawerLayout 实例并调用 validate_integration
        from frontend.ui.layout import DrawerLayout
        layout = DrawerLayout(config)
        result = layout.validate_integration()
        # Then: 返回 True
        assert result is True

    def test_validate_integration_returns_false_for_missing_function_groups(self):
        """测试 validate_integration 对缺失功能分组返回 False"""
        # Given: 配置字典
        config = {
            "webui": {
                "theme": {"choose": "default"},
                "drawer_mini": False,
                "config_items": {}
            }
        }
        # When: 创建 DrawerLayout 实例并调用 validate_integration
        from frontend.ui.layout import DrawerLayout
        layout = DrawerLayout(config)
        result = layout.validate_integration()
        # Then: 返回 False
        assert result is False

    def test_validate_integration_returns_false_for_missing_config_items(self):
        """测试 validate_integration 对缺失配置项返回 False"""
        # Given: 配置字典
        config = {
            "webui": {
                "theme": {"choose": "default"},
                "drawer_mini": False,
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
        # When: 创建 DrawerLayout 实例并调用 validate_integration
        from frontend.ui.layout import DrawerLayout
        layout = DrawerLayout(config)
        result = layout.validate_integration()
        # Then: 返回 False
        assert result is False

    def test_validate_integration_returns_false_for_empty_function_groups(self):
        """测试 validate_integration 对空功能分组返回 False"""
        # Given: 配置字典
        config = {
            "webui": {
                "theme": {"choose": "default"},
                "drawer_mini": False,
                "function_groups": [],
                "config_items": {}
            }
        }
        # When: 创建 DrawerLayout 实例并调用 validate_integration
        from frontend.ui.layout import DrawerLayout
        layout = DrawerLayout(config)
        result = layout.validate_integration()
        # Then: 返回 False
        assert result is False

    def test_drawer_layout_has_get_integration_status_method(self):
        """测试 DrawerLayout 有 get_integration_status 方法"""
        # Given: DrawerLayout 类
        # When: 检查方法
        # Then: get_integration_status 方法存在
        from frontend.ui.layout import DrawerLayout
        assert hasattr(DrawerLayout, 'get_integration_status')

    def test_get_integration_status_returns_dict(self):
        """测试 get_integration_status 返回字典"""
        # Given: 配置字典
        config = {
            "webui": {
                "theme": {"choose": "default"},
                "drawer_mini": False,
                "function_groups": [
                    {
                        "name": "基础设置",
                        "icon": "settings",
                        "functions": [
                            {"name": "通用配置", "enabled": True, "tab_key": "common_config"}
                        ]
                    }
                ],
                "config_items": {
                    "common_config": {
                        "label": "通用配置",
                        "type": "group",
                        "items": {}
                    }
                }
            }
        }
        # When: 创建 DrawerLayout 实例并调用 get_integration_status
        from frontend.ui.layout import DrawerLayout
        layout = DrawerLayout(config)
        status = layout.get_integration_status()
        # Then: 返回字典
        assert isinstance(status, dict)

    def test_get_integration_status_contains_required_keys(self):
        """测试 get_integration_status 包含必需的键"""
        # Given: 配置字典
        config = {
            "webui": {
                "theme": {"choose": "default"},
                "drawer_mini": False,
                "function_groups": [
                    {
                        "name": "基础设置",
                        "icon": "settings",
                        "functions": [
                            {"name": "通用配置", "enabled": True, "tab_key": "common_config"}
                        ]
                    }
                ],
                "config_items": {
                    "common_config": {
                        "label": "通用配置",
                        "type": "group",
                        "items": {}
                    }
                }
            }
        }
        # When: 创建 DrawerLayout 实例并调用 get_integration_status
        from frontend.ui.layout import DrawerLayout
        layout = DrawerLayout(config)
        status = layout.get_integration_status()
        # Then: 包含必需的键
        assert "valid" in status
        assert "function_groups_count" in status
        assert "config_items_count" in status
        assert "functions_count" in status

    def test_get_integration_status_counts_are_correct(self):
        """测试 get_integration_status 计数正确"""
        # Given: 配置字典
        config = {
            "webui": {
                "theme": {"choose": "default"},
                "drawer_mini": False,
                "function_groups": [
                    {
                        "name": "基础设置",
                        "icon": "settings",
                        "functions": [
                            {"name": "通用配置", "enabled": True, "tab_key": "common_config"},
                            {"name": "页面配置", "enabled": True, "tab_key": "web"}
                        ]
                    }
                ],
                "config_items": {
                    "common_config": {
                        "label": "通用配置",
                        "type": "group",
                        "items": {}
                    },
                    "web": {
                        "label": "页面配置",
                        "type": "group",
                        "items": {}
                    }
                }
            }
        }
        # When: 创建 DrawerLayout 实例并调用 get_integration_status
        from frontend.ui.layout import DrawerLayout
        layout = DrawerLayout(config)
        status = layout.get_integration_status()
        # Then: 计数正确
        assert status["function_groups_count"] == 1
        assert status["config_items_count"] == 2
        assert status["functions_count"] == 2

    def test_validate_integration_handles_missing_theme(self):
        """测试 validate_integration 处理缺失主题"""
        # Given: 配置字典
        config = {
            "webui": {
                "drawer_mini": False,
                "function_groups": [
                    {
                        "name": "基础设置",
                        "icon": "settings",
                        "functions": [
                            {"name": "通用配置", "enabled": True, "tab_key": "common_config"}
                        ]
                    }
                ],
                "config_items": {
                    "common_config": {
                        "label": "通用配置",
                        "type": "group",
                        "items": {}
                    }
                }
            }
        }
        # When: 创建 DrawerLayout 实例并调用 validate_integration
        from frontend.ui.layout import DrawerLayout
        layout = DrawerLayout(config)
        result = layout.validate_integration()
        # Then: 返回 True（主题有默认值）
        assert result is True
