# -*- coding: UTF-8 -*-
"""
Task-09: 实现配置页面显示 测试
TDD RED 阶段：先写测试，确认失败
"""

import pytest
import json
from pathlib import Path
from unittest.mock import MagicMock


def _create_layout(config):
    """创建带 mock splitter 容器的 DrawerLayout"""
    from frontend.ui.layout import DrawerLayout
    layout = DrawerLayout(config)
    mock_fl = MagicMock()
    mock_fl.__enter__ = MagicMock(return_value=mock_fl)
    mock_fl.__exit__ = MagicMock(return_value=False)
    layout.function_list = mock_fl
    mock_cp = MagicMock()
    mock_cp.__enter__ = MagicMock(return_value=mock_cp)
    mock_cp.__exit__ = MagicMock(return_value=False)
    layout.config_page = mock_cp
    return layout


class TestConfigPageDisplay:
    """测试配置页面显示"""

    def test_drawer_layout_has_render_config_page_method(self):
        """测试 DrawerLayout 有 render_config_page 方法"""
        from frontend.ui.layout import DrawerLayout
        assert hasattr(DrawerLayout, 'render_config_page')

    def test_render_config_page_displays_input(self):
        """测试 render_config_page 显示输入框"""
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
                                "value": "test_value"
                            }
                        }
                    }
                }
            }
        }
        layout = _create_layout(config)
        layout.render_config_page("test_function")
        layout.config_page.clear.assert_called()

    def test_render_config_page_displays_switch(self):
        """测试 render_config_page 显示开关"""
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
                                "value": True
                            }
                        }
                    }
                }
            }
        }
        layout = _create_layout(config)
        layout.render_config_page("test_function")
        layout.config_page.clear.assert_called()

    def test_render_config_page_displays_select(self):
        """测试 render_config_page 显示下拉选择框"""
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
        layout = _create_layout(config)
        layout.render_config_page("test_function")
        layout.config_page.clear.assert_called()

    def test_render_config_page_displays_textarea(self):
        """测试 render_config_page 显示多行文本框"""
        config = {
            "webui": {
                "theme": {"choose": "default"},
                "config_items": {
                    "test_function": {
                        "label": "测试功能",
                        "type": "group",
                        "items": {
                            "test_textarea": {
                                "label": "测试文本",
                                "type": "textarea",
                                "value": "test_content"
                            }
                        }
                    }
                }
            }
        }
        layout = _create_layout(config)
        layout.render_config_page("test_function")
        layout.config_page.clear.assert_called()

    def test_render_config_page_for_unknown_function(self):
        """测试 render_config_page 处理未知功能"""
        config = {
            "webui": {
                "theme": {"choose": "default"},
                "config_items": {}
            }
        }
        layout = _create_layout(config)
        layout.render_config_page("unknown_function")
        assert layout.config_page_container is None

    def test_render_config_page_clears_previous_content(self):
        """测试 render_config_page 清除之前的内容"""
        config = {
            "webui": {
                "theme": {"choose": "default"},
                "config_items": {
                    "function1": {
                        "label": "功能1",
                        "type": "group",
                        "items": {
                            "input1": {
                                "label": "输入1",
                                "type": "input",
                                "value": "value1"
                            }
                        }
                    },
                    "function2": {
                        "label": "功能2",
                        "type": "group",
                        "items": {
                            "input2": {
                                "label": "输入2",
                                "type": "input",
                                "value": "value2"
                            }
                        }
                    }
                }
            }
        }
        layout = _create_layout(config)
        layout.render_config_page("function1")
        layout.render_config_page("function2")
        layout.config_page.clear.assert_called()

    def test_drawer_layout_has_config_values_attribute(self):
        """测试 DrawerLayout 有 config_values 属性"""
        config = {"webui": {"theme": {"choose": "default"}}}
        from frontend.ui.layout import DrawerLayout
        layout = DrawerLayout(config)
        assert hasattr(layout, 'config_values')

    def test_config_values_initial_value_is_empty_dict(self):
        """测试 config_values 初始值为空字典"""
        config = {"webui": {"theme": {"choose": "default"}}}
        from frontend.ui.layout import DrawerLayout
        layout = DrawerLayout(config)
        assert layout.config_values == {}

    def test_render_config_page_stores_config_values(self):
        """测试 render_config_page 存储配置值"""
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
                                "value": "test_value"
                            }
                        }
                    }
                }
            }
        }
        layout = _create_layout(config)
        layout.render_config_page("test_function")
        assert "test_input" in layout.config_values
        assert layout.config_values["test_input"]["value"] == "test_value"
