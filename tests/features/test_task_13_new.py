# -*- coding: UTF-8 -*-
"""
Task-13: 实现搜索结果高亮和跳转 测试
TDD RED 阶段：先写测试，确认失败
"""

import pytest
import json
from pathlib import Path


class TestSearchHighlightAndJump:
    """测试搜索结果高亮和跳转"""

    def test_drawer_layout_has_highlight_search_results_method(self):
        """测试 DrawerLayout 有 highlight_search_results 方法"""
        # Given: DrawerLayout 类
        # When: 检查方法
        # Then: highlight_search_results 方法存在
        from frontend.ui.layout import DrawerLayout
        assert hasattr(DrawerLayout, 'highlight_search_results')

    def test_drawer_layout_has_navigate_to_result_method(self):
        """测试 DrawerLayout 有 navigate_to_result 方法"""
        # Given: DrawerLayout 类
        # When: 检查方法
        # Then: navigate_to_result 方法存在
        from frontend.ui.layout import DrawerLayout
        assert hasattr(DrawerLayout, 'navigate_to_result')

    def test_highlight_search_results_returns_highlighted_text(self):
        """测试 highlight_search_results 返回高亮文本"""
        # Given: DrawerLayout 实例
        config = {"webui": {"theme": {"choose": "default"}}}
        from frontend.ui.layout import DrawerLayout
        layout = DrawerLayout(config)
        # When: 调用 highlight_search_results
        result = layout.highlight_search_results("通用配置", "通用")
        # Then: 返回高亮文本
        assert "<mark>" in result
        assert "</mark>" in result

    def test_highlight_search_results_highlights_keyword(self):
        """测试 highlight_search_results 高亮关键字"""
        # Given: DrawerLayout 实例
        config = {"webui": {"theme": {"choose": "default"}}}
        from frontend.ui.layout import DrawerLayout
        layout = DrawerLayout(config)
        # When: 调用 highlight_search_results
        result = layout.highlight_search_results("通用配置", "通用")
        # Then: 关键字被高亮
        assert "通用" in result
        assert "<mark>通用</mark>" in result

    def test_highlight_search_results_for_no_match(self):
        """测试 highlight_search_results 无匹配时返回原文"""
        # Given: DrawerLayout 实例
        config = {"webui": {"theme": {"choose": "default"}}}
        from frontend.ui.layout import DrawerLayout
        layout = DrawerLayout(config)
        # When: 调用 highlight_search_results
        result = layout.highlight_search_results("通用配置", "积分")
        # Then: 返回原文
        assert result == "通用配置"

    def test_navigate_to_result_for_function(self):
        """测试 navigate_to_result 跳转到功能"""
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
        # When: 创建 DrawerLayout 实例并调用 navigate_to_result
        from frontend.ui.layout import DrawerLayout
        layout = DrawerLayout(config)
        layout.navigate_to_result({"type": "function", "name": "通用配置", "group": "基础设置", "tab_key": "common_config"})
        # Then: current_group 更新
        assert layout.current_group == "基础设置"

    def test_navigate_to_result_for_config_item(self):
        """测试 navigate_to_result 跳转到配置项"""
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
        # When: 创建 DrawerLayout 实例并调用 navigate_to_result
        from frontend.ui.layout import DrawerLayout
        layout = DrawerLayout(config)
        layout.navigate_to_result({"type": "config_item", "name": "read_comment", "label": "读取评论", "function": "common_config", "group": "通用配置"})
        # Then: current_group 更新
        assert layout.current_group == "基础设置"

    def test_navigate_to_result_for_unknown_result(self):
        """测试 navigate_to_result 处理未知结果"""
        # Given: 配置字典
        config = {
            "webui": {
                "theme": {"choose": "default"},
                "function_groups": [],
                "config_items": {}
            }
        }
        # When: 创建 DrawerLayout 实例并调用 navigate_to_result
        from frontend.ui.layout import DrawerLayout
        layout = DrawerLayout(config)
        layout.navigate_to_result({"type": "unknown", "name": "unknown"})
        # Then: 不抛出异常

    def test_highlight_search_results_is_case_insensitive(self):
        """测试 highlight_search_results 不区分大小写"""
        # Given: DrawerLayout 实例
        config = {"webui": {"theme": {"choose": "default"}}}
        from frontend.ui.layout import DrawerLayout
        layout = DrawerLayout(config)
        # When: 调用 highlight_search_results
        result = layout.highlight_search_results("Common Config", "common")
        # Then: 关键字被高亮
        assert "<mark>" in result
        assert "</mark>" in result

    def test_highlight_search_results_for_empty_keyword(self):
        """测试 highlight_search_results 空关键字时返回原文"""
        # Given: DrawerLayout 实例
        config = {"webui": {"theme": {"choose": "default"}}}
        from frontend.ui.layout import DrawerLayout
        layout = DrawerLayout(config)
        # When: 调用 highlight_search_results
        result = layout.highlight_search_results("通用配置", "")
        # Then: 返回原文
        assert result == "通用配置"
