# -*- coding: UTF-8 -*-
"""
Task-17: 迁移现有 tabs 功能到新布局 测试
TDD RED 阶段：先写测试，确认失败
"""

import pytest
import json
from pathlib import Path


class TestFunctionMigration:
    """测试功能迁移"""

    def test_drawer_layout_has_migrate_tabs_method(self):
        """测试 DrawerLayout 有 migrate_tabs 方法"""
        # Given: DrawerLayout 类
        # When: 检查方法
        # Then: migrate_tabs 方法存在
        from frontend.ui.layout import DrawerLayout
        assert hasattr(DrawerLayout, 'migrate_tabs')

    def test_migrate_tabs_loads_all_functions(self):
        """测试 migrate_tabs 加载所有功能"""
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
                    },
                    {
                        "name": "直播互动",
                        "icon": "live_tv",
                        "functions": [
                            {"name": "积分", "enabled": True, "tab_key": "integral"},
                            {"name": "助播", "enabled": True, "tab_key": "assistant_anchor"}
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
                    },
                    "integral": {
                        "label": "积分",
                        "type": "group",
                        "items": {
                            "enable": {
                                "label": "启用积分",
                                "type": "switch",
                                "value": True
                            }
                        }
                    }
                }
            }
        }
        # When: 创建 DrawerLayout 实例并调用 migrate_tabs
        from frontend.ui.layout import DrawerLayout
        layout = DrawerLayout(config)
        layout.migrate_tabs()
        # Then: function_items 包含所有功能
        assert "通用配置" in layout.function_items
        assert "页面配置" in layout.function_items
        assert "积分" in layout.function_items
        assert "助播" in layout.function_items

    def test_migrate_tabs_preserves_enabled_state(self):
        """测试 migrate_tabs 保留启用状态"""
        # Given: 配置字典
        config = {
            "webui": {
                "theme": {"choose": "default"},
                "function_groups": [
                    {
                        "name": "基础设置",
                        "icon": "settings",
                        "functions": [
                            {"name": "通用配置", "enabled": False, "tab_key": "common_config"}
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
        # When: 创建 DrawerLayout 实例并调用 migrate_tabs
        from frontend.ui.layout import DrawerLayout
        layout = DrawerLayout(config)
        layout.migrate_tabs()
        # Then: function_items 保留启用状态
        assert layout.function_items["通用配置"]["enabled"] is False

    def test_migrate_tabs_preserves_tab_key(self):
        """测试 migrate_tabs 保留 tab_key"""
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
        # When: 创建 DrawerLayout 实例并调用 migrate_tabs
        from frontend.ui.layout import DrawerLayout
        layout = DrawerLayout(config)
        layout.migrate_tabs()
        # Then: function_items 保留 tab_key
        assert layout.function_items["通用配置"]["tab_key"] == "common_config"

    def test_migrate_tabs_handles_empty_groups(self):
        """测试 migrate_tabs 处理空分组"""
        # Given: 配置字典
        config = {
            "webui": {
                "theme": {"choose": "default"},
                "function_groups": [],
                "config_items": {}
            }
        }
        # When: 创建 DrawerLayout 实例并调用 migrate_tabs
        from frontend.ui.layout import DrawerLayout
        layout = DrawerLayout(config)
        layout.migrate_tabs()
        # Then: function_items 为空
        assert layout.function_items == {}

    def test_migrate_tabs_handles_missing_config_items(self):
        """测试 migrate_tabs 处理缺失的配置项"""
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
                ],
                "config_items": {}
            }
        }
        # When: 创建 DrawerLayout 实例并调用 migrate_tabs
        from frontend.ui.layout import DrawerLayout
        layout = DrawerLayout(config)
        layout.migrate_tabs()
        # Then: function_items 包含功能
        assert "通用配置" in layout.function_items

    def test_drawer_layout_has_tabs_mapping_attribute(self):
        """测试 DrawerLayout 有 tabs_mapping 属性"""
        # Given: 配置字典
        config = {"webui": {"theme": {"choose": "default"}}}
        # When: 创建 DrawerLayout 实例
        from frontend.ui.layout import DrawerLayout
        layout = DrawerLayout(config)
        # Then: tabs_mapping 属性存在
        assert hasattr(layout, 'tabs_mapping')

    def test_tabs_mapping_initial_value_is_empty_dict(self):
        """测试 tabs_mapping 初始值为空字典"""
        # Given: 配置字典
        config = {"webui": {"theme": {"choose": "default"}}}
        # When: 创建 DrawerLayout 实例
        from frontend.ui.layout import DrawerLayout
        layout = DrawerLayout(config)
        # Then: tabs_mapping 初始值为空字典
        assert layout.tabs_mapping == {}

    def test_migrate_tabs_creates_tabs_mapping(self):
        """测试 migrate_tabs 创建 tabs_mapping"""
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
        # When: 创建 DrawerLayout 实例并调用 migrate_tabs
        from frontend.ui.layout import DrawerLayout
        layout = DrawerLayout(config)
        layout.migrate_tabs()
        # Then: tabs_mapping 包含映射
        assert "common_config" in layout.tabs_mapping
        assert layout.tabs_mapping["common_config"] == "通用配置"

    def test_migrate_tabs_handles_all_function_groups(self):
        """测试 migrate_tabs 处理所有功能分组"""
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
                    },
                    {
                        "name": "直播互动",
                        "icon": "live_tv",
                        "functions": [
                            {"name": "积分", "enabled": True, "tab_key": "integral"},
                            {"name": "助播", "enabled": True, "tab_key": "assistant_anchor"},
                            {"name": "翻译", "enabled": True, "tab_key": "translate"},
                            {"name": "串口", "enabled": True, "tab_key": "serial"}
                        ]
                    },
                    {
                        "name": "AI 对话",
                        "icon": "chat",
                        "functions": [
                            {"name": "大语言模型", "enabled": True, "tab_key": "llm"},
                            {"name": "聊天", "enabled": True, "tab_key": "talk"}
                        ]
                    },
                    {
                        "name": "音频设置",
                        "icon": "audiotrack",
                        "functions": [
                            {"name": "文本转语音", "enabled": True, "tab_key": "tts"},
                            {"name": "变声", "enabled": True, "tab_key": "svc"}
                        ]
                    },
                    {
                        "name": "高级功能",
                        "icon": "build",
                        "functions": [
                            {"name": "虚拟身体", "enabled": True, "tab_key": "visual_body"},
                            {"name": "数据分析", "enabled": True, "tab_key": "data_analysis"}
                        ]
                    }
                ],
                "config_items": {}
            }
        }
        # When: 创建 DrawerLayout 实例并调用 migrate_tabs
        from frontend.ui.layout import DrawerLayout
        layout = DrawerLayout(config)
        layout.migrate_tabs()
        # Then: function_items 包含所有功能
        assert len(layout.function_items) == 12
