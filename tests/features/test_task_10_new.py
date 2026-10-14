# -*- coding: UTF-8 -*-
"""
Task-10: 实现配置自动保存 测试
TDD RED 阶段：先写测试，确认失败
"""

import pytest
import json
from pathlib import Path


class TestConfigAutoSave:
    """测试配置自动保存"""

    def test_drawer_layout_has_auto_save_method(self):
        """测试 DrawerLayout 有 auto_save 方法"""
        # Given: DrawerLayout 类
        # When: 检查方法
        # Then: auto_save 方法存在
        from frontend.ui.layout import DrawerLayout
        assert hasattr(DrawerLayout, 'auto_save')

    def test_auto_save_updates_config(self):
        """测试 auto_save 更新配置"""
        # Given: 配置字典
        config = {
            "webui": {
                "theme": {"choose": "default"},
                "config_items": {
                    "test_function": {
                        "label": "测试功能",
                        "type": "group",
                        "items": {
                            "test_input": {
                                "label": "测试输入",
                                "type": "input",
                                "value": "old_value"
                            }
                        }
                    }
                }
            }
        }
        # When: 创建 DrawerLayout 实例并调用 auto_save
        from frontend.ui.layout import DrawerLayout
        layout = DrawerLayout(config)
        layout.auto_save("test_function", "test_input", "new_value")
        # Then: 配置中的值更新
        assert config["webui"]["config_items"]["test_function"]["items"]["test_input"]["value"] == "new_value"

    def test_auto_save_for_switch(self):
        """测试 auto_save 保存开关值"""
        # Given: 配置字典
        config = {
            "webui": {
                "theme": {"choose": "default"},
                "config_items": {
                    "test_function": {
                        "label": "测试功能",
                        "type": "group",
                        "items": {
                            "test_switch": {
                                "label": "测试开关",
                                "type": "switch",
                                "value": False
                            }
                        }
                    }
                }
            }
        }
        # When: 创建 DrawerLayout 实例并调用 auto_save
        from frontend.ui.layout import DrawerLayout
        layout = DrawerLayout(config)
        layout.auto_save("test_function", "test_switch", True)
        # Then: 配置中的值更新
        assert config["webui"]["config_items"]["test_function"]["items"]["test_switch"]["value"] is True

    def test_auto_save_for_select(self):
        """测试 auto_save 保存下拉选择框值"""
        # Given: 配置字典
        config = {
            "webui": {
                "theme": {"choose": "default"},
                "config_items": {
                    "test_function": {
                        "label": "测试功能",
                        "type": "group",
                        "items": {
                            "test_select": {
                                "label": "测试选择",
                                "type": "select",
                                "options": ["option1", "option2"],
                                "value": "option1"
                            }
                        }
                    }
                }
            }
        }
        # When: 创建 DrawerLayout 实例并调用 auto_save
        from frontend.ui.layout import DrawerLayout
        layout = DrawerLayout(config)
        layout.auto_save("test_function", "test_select", "option2")
        # Then: 配置中的值更新
        assert config["webui"]["config_items"]["test_function"]["items"]["test_select"]["value"] == "option2"

    def test_auto_save_for_unknown_function(self):
        """测试 auto_save 处理未知功能"""
        # Given: 配置字典
        config = {
            "webui": {
                "theme": {"choose": "default"},
                "config_items": {}
            }
        }
        # When: 创建 DrawerLayout 实例并调用 auto_save
        from frontend.ui.layout import DrawerLayout
        layout = DrawerLayout(config)
        layout.auto_save("unknown_function", "test_input", "new_value")
        # Then: 不抛出异常

    def test_auto_save_for_unknown_item(self):
        """测试 auto_save 处理未知配置项"""
        # Given: 配置字典
        config = {
            "webui": {
                "theme": {"choose": "default"},
                "config_items": {
                    "test_function": {
                        "label": "测试功能",
                        "type": "group",
                        "items": {}
                    }
                }
            }
        }
        # When: 创建 DrawerLayout 实例并调用 auto_save
        from frontend.ui.layout import DrawerLayout
        layout = DrawerLayout(config)
        layout.auto_save("test_function", "unknown_item", "new_value")
        # Then: 不抛出异常

    def test_drawer_layout_has_save_notification_attribute(self):
        """测试 DrawerLayout 有 save_notification 属性"""
        # Given: 配置字典
        config = {"webui": {"theme": {"choose": "default"}}}
        # When: 创建 DrawerLayout 实例
        from frontend.ui.layout import DrawerLayout
        layout = DrawerLayout(config)
        # Then: save_notification 属性存在
        assert hasattr(layout, 'save_notification')

    def test_save_notification_initial_value_is_none(self):
        """测试 save_notification 初始值为 None"""
        # Given: 配置字典
        config = {"webui": {"theme": {"choose": "default"}}}
        # When: 创建 DrawerLayout 实例
        from frontend.ui.layout import DrawerLayout
        layout = DrawerLayout(config)
        # Then: save_notification 初始值为 None
        assert layout.save_notification is None

    def test_auto_save_updates_config_values(self):
        """测试 auto_save 更新 config_values"""
        # Given: 配置字典
        config = {
            "webui": {
                "theme": {"choose": "default"},
                "config_items": {
                    "test_function": {
                        "label": "测试功能",
                        "type": "group",
                        "items": {
                            "test_input": {
                                "label": "测试输入",
                                "type": "input",
                                "value": "old_value"
                            }
                        }
                    }
                }
            }
        }
        # When: 创建 DrawerLayout 实例并调用 auto_save
        from frontend.ui.layout import DrawerLayout
        layout = DrawerLayout(config)
        layout.config_values = {"test_input": {"type": "input", "value": "old_value", "label": "测试输入"}}
        layout.auto_save("test_function", "test_input", "new_value")
        # Then: config_values 中的值更新
        assert layout.config_values["test_input"]["value"] == "new_value"
