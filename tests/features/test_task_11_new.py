# -*- coding: UTF-8 -*-
"""
Task-11: 实现文案配置手动保存 测试
TDD RED 阶段：先写测试，确认失败
"""

import pytest
import json
from pathlib import Path


class TestTextareaManualSave:
    """测试文案配置手动保存"""

    def test_drawer_layout_has_manual_save_method(self):
        """测试 DrawerLayout 有 manual_save 方法"""
        # Given: DrawerLayout 类
        # When: 检查方法
        # Then: manual_save 方法存在
        from frontend.ui.layout import DrawerLayout
        assert hasattr(DrawerLayout, 'manual_save')

    def test_manual_save_updates_config(self):
        """测试 manual_save 更新配置"""
        # Given: 配置字典
        config = {
            "webui": {
                "theme": {"choose": "default"},
                "config_items": {
                    "copywriting": {
                        "label": "文案",
                        "type": "group",
                        "items": {
                            "content": {
                                "label": "文案内容",
                                "type": "textarea",
                                "value": "old_content"
                            }
                        }
                    }
                }
            }
        }
        # When: 创建 DrawerLayout 实例并调用 manual_save
        from frontend.ui.layout import DrawerLayout
        layout = DrawerLayout(config)
        layout.manual_save("copywriting", "content", "new_content")
        # Then: 配置中的值更新
        assert config["webui"]["config_items"]["copywriting"]["items"]["content"]["value"] == "new_content"

    def test_manual_save_for_unknown_function(self):
        """测试 manual_save 处理未知功能"""
        # Given: 配置字典
        config = {
            "webui": {
                "theme": {"choose": "default"},
                "config_items": {}
            }
        }
        # When: 创建 DrawerLayout 实例并调用 manual_save
        from frontend.ui.layout import DrawerLayout
        layout = DrawerLayout(config)
        layout.manual_save("unknown_function", "content", "new_content")
        # Then: 不抛出异常

    def test_manual_save_for_unknown_item(self):
        """测试 manual_save 处理未知配置项"""
        # Given: 配置字典
        config = {
            "webui": {
                "theme": {"choose": "default"},
                "config_items": {
                    "copywriting": {
                        "label": "文案",
                        "type": "group",
                        "items": {}
                    }
                }
            }
        }
        # When: 创建 DrawerLayout 实例并调用 manual_save
        from frontend.ui.layout import DrawerLayout
        layout = DrawerLayout(config)
        layout.manual_save("copywriting", "unknown_item", "new_content")
        # Then: 不抛出异常

    def test_manual_save_updates_config_values(self):
        """测试 manual_save 更新 config_values"""
        # Given: 配置字典
        config = {
            "webui": {
                "theme": {"choose": "default"},
                "config_items": {
                    "copywriting": {
                        "label": "文案",
                        "type": "group",
                        "items": {
                            "content": {
                                "label": "文案内容",
                                "type": "textarea",
                                "value": "old_content"
                            }
                        }
                    }
                }
            }
        }
        # When: 创建 DrawerLayout 实例并调用 manual_save
        from frontend.ui.layout import DrawerLayout
        layout = DrawerLayout(config)
        layout.config_values = {"content": {"type": "textarea", "value": "old_content", "label": "文案内容"}}
        layout.manual_save("copywriting", "content", "new_content")
        # Then: config_values 中的值更新
        assert layout.config_values["content"]["value"] == "new_content"

    def test_manual_save_for_non_textarea(self):
        """测试 manual_save 处理非 textarea 类型"""
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
        # When: 创建 DrawerLayout 实例并调用 manual_save
        from frontend.ui.layout import DrawerLayout
        layout = DrawerLayout(config)
        layout.manual_save("test_function", "test_input", "new_value")
        # Then: 配置中的值更新
        assert config["webui"]["config_items"]["test_function"]["items"]["test_input"]["value"] == "new_value"

    def test_drawer_layout_has_save_button_text_attribute(self):
        """测试 DrawerLayout 有 save_button_text 属性"""
        # Given: 配置字典
        config = {"webui": {"theme": {"choose": "default"}}}
        # When: 创建 DrawerLayout 实例
        from frontend.ui.layout import DrawerLayout
        layout = DrawerLayout(config)
        # Then: save_button_text 属性存在
        assert hasattr(layout, 'save_button_text')

    def test_save_button_text_initial_value(self):
        """测试 save_button_text 初始值"""
        # Given: 配置字典
        config = {"webui": {"theme": {"choose": "default"}}}
        # When: 创建 DrawerLayout 实例
        from frontend.ui.layout import DrawerLayout
        layout = DrawerLayout(config)
        # Then: save_button_text 初始值为 "保存"
        assert layout.save_button_text == "保存"

    def test_manual_save_changes_button_text(self):
        """测试 manual_save 改变按钮文本"""
        # Given: 配置字典
        config = {
            "webui": {
                "theme": {"choose": "default"},
                "config_items": {
                    "copywriting": {
                        "label": "文案",
                        "type": "group",
                        "items": {
                            "content": {
                                "label": "文案内容",
                                "type": "textarea",
                                "value": "old_content"
                            }
                        }
                    }
                }
            }
        }
        # When: 创建 DrawerLayout 实例并调用 manual_save
        from frontend.ui.layout import DrawerLayout
        layout = DrawerLayout(config)
        layout.manual_save("copywriting", "content", "new_content")
        # Then: save_button_text 改变为 "已保存"
        assert layout.save_button_text == "已保存"
