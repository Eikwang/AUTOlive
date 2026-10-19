# -*- coding: UTF-8 -*-
"""
Task-12: 实现搜索功能 测试
TDD RED 阶段：先写测试，确认失败
"""

import pytest
import json
from pathlib import Path


class TestSearchFunction:
    """测试搜索功能"""

    @pytest.mark.skip(reason="退役：DrawerLayout 旧 search API（search/search_results/search_keyword）已随全局搜索重构消亡（现行 _on_global_search + config_registry），功能由 test_task_27_new 按现行 API 验收（门禁A 2026-10-19）")
    def test_drawer_layout_has_search_method(self):
        """测试 DrawerLayout 有 search 方法"""
        # Given: DrawerLayout 类
        # When: 检查方法
        # Then: search 方法存在
        from frontend.ui.layout import DrawerLayout
        assert hasattr(DrawerLayout, 'search')

    @pytest.mark.skip(reason="退役：DrawerLayout 旧 search API（search/search_results/search_keyword）已随全局搜索重构消亡（现行 _on_global_search + config_registry），功能由 test_task_27_new 按现行 API 验收（门禁A 2026-10-19）")
    def test_drawer_layout_has_search_results_attribute(self):
        """测试 DrawerLayout 有 search_results 属性"""
        # Given: 配置字典
        config = {"webui": {"theme": {"choose": "default"}}}
        # When: 创建 DrawerLayout 实例
        from frontend.ui.layout import DrawerLayout
        layout = DrawerLayout(config)
        # Then: search_results 属性存在
        assert hasattr(layout, 'search_results')

    @pytest.mark.skip(reason="退役：DrawerLayout 旧 search API（search/search_results/search_keyword）已随全局搜索重构消亡（现行 _on_global_search + config_registry），功能由 test_task_27_new 按现行 API 验收（门禁A 2026-10-19）")
    def test_search_results_initial_value_is_empty_list(self):
        """测试 search_results 初始值为空列表"""
        # Given: 配置字典
        config = {"webui": {"theme": {"choose": "default"}}}
        # When: 创建 DrawerLayout 实例
        from frontend.ui.layout import DrawerLayout
        layout = DrawerLayout(config)
        # Then: search_results 初始值为空列表
        assert layout.search_results == []

    @pytest.mark.skip(reason="退役：DrawerLayout 旧 search API（search/search_results/search_keyword）已随全局搜索重构消亡（现行 _on_global_search + config_registry），功能由 test_task_27_new 按现行 API 验收（门禁A 2026-10-19）")
    def test_search_finds_function_by_name(self):
        """测试搜索按功能名称查找"""
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
        # When: 创建 DrawerLayout 实例并调用 search
        from frontend.ui.layout import DrawerLayout
        layout = DrawerLayout(config)
        layout.search("通用")
        # Then: search_results 包含匹配项
        assert len(layout.search_results) > 0
        assert any("通用配置" in result["name"] for result in layout.search_results)

    @pytest.mark.skip(reason="退役：DrawerLayout 旧 search API（search/search_results/search_keyword）已随全局搜索重构消亡（现行 _on_global_search + config_registry），功能由 test_task_27_new 按现行 API 验收（门禁A 2026-10-19）")
    def test_search_finds_config_item_by_label(self):
        """测试搜索按配置项标签查找"""
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
        # When: 创建 DrawerLayout 实例并调用 search
        from frontend.ui.layout import DrawerLayout
        layout = DrawerLayout(config)
        layout.search("评论")
        # Then: search_results 包含匹配项
        assert len(layout.search_results) > 0
        assert any("读取评论" in result.get("label", "") for result in layout.search_results)

    @pytest.mark.skip(reason="退役：DrawerLayout 旧 search API（search/search_results/search_keyword）已随全局搜索重构消亡（现行 _on_global_search + config_registry），功能由 test_task_27_new 按现行 API 验收（门禁A 2026-10-19）")
    def test_search_returns_empty_for_no_match(self):
        """测试搜索无匹配时返回空列表"""
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
        # When: 创建 DrawerLayout 实例并调用 search
        from frontend.ui.layout import DrawerLayout
        layout = DrawerLayout(config)
        layout.search("不存在的关键字")
        # Then: search_results 为空
        assert layout.search_results == []

    @pytest.mark.skip(reason="退役：DrawerLayout 旧 search API（search/search_results/search_keyword）已随全局搜索重构消亡（现行 _on_global_search + config_registry），功能由 test_task_27_new 按现行 API 验收（门禁A 2026-10-19）")
    def test_search_returns_empty_for_empty_keyword(self):
        """测试搜索空关键字时返回空列表"""
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
        # When: 创建 DrawerLayout 实例并调用 search
        from frontend.ui.layout import DrawerLayout
        layout = DrawerLayout(config)
        layout.search("")
        # Then: search_results 为空
        assert layout.search_results == []

    @pytest.mark.skip(reason="退役：DrawerLayout 旧 search API（search/search_results/search_keyword）已随全局搜索重构消亡（现行 _on_global_search + config_registry），功能由 test_task_27_new 按现行 API 验收（门禁A 2026-10-19）")
    def test_search_is_case_insensitive(self):
        """测试搜索不区分大小写"""
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
                        "items": {
                            "test_item": {
                                "label": "Test Item",
                                "type": "input",
                                "value": ""
                            }
                        }
                    }
                }
            }
        }
        # When: 创建 DrawerLayout 实例并调用 search
        from frontend.ui.layout import DrawerLayout
        layout = DrawerLayout(config)
        layout.search("test")
        # Then: search_results 包含匹配项
        assert len(layout.search_results) > 0

    @pytest.mark.skip(reason="退役：DrawerLayout 旧 search API（search/search_results/search_keyword）已随全局搜索重构消亡（现行 _on_global_search + config_registry），功能由 test_task_27_new 按现行 API 验收（门禁A 2026-10-19）")
    def test_drawer_layout_has_search_keyword_attribute(self):
        """测试 DrawerLayout 有 search_keyword 属性"""
        # Given: 配置字典
        config = {"webui": {"theme": {"choose": "default"}}}
        # When: 创建 DrawerLayout 实例
        from frontend.ui.layout import DrawerLayout
        layout = DrawerLayout(config)
        # Then: search_keyword 属性存在
        assert hasattr(layout, 'search_keyword')

    @pytest.mark.skip(reason="退役：DrawerLayout 旧 search API（search/search_results/search_keyword）已随全局搜索重构消亡（现行 _on_global_search + config_registry），功能由 test_task_27_new 按现行 API 验收（门禁A 2026-10-19）")
    def test_search_keyword_initial_value_is_empty_string(self):
        """测试 search_keyword 初始值为空字符串"""
        # Given: 配置字典
        config = {"webui": {"theme": {"choose": "default"}}}
        # When: 创建 DrawerLayout 实例
        from frontend.ui.layout import DrawerLayout
        layout = DrawerLayout(config)
        # Then: search_keyword 初始值为空字符串
        assert layout.search_keyword == ""
