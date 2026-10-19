# -*- coding: UTF-8 -*-
"""
Task-13: 实现搜索结果高亮和跳转 测试
验收意图：搜索结果展示与点击跳转。

门禁A 2026-10-19 重写：highlight_search_results 关键字高亮已随旧搜索实现移除（现行
_show_search_results 直接显示结果文本，frontend/ui/layout.py:943）；navigate_to_result
已演化为 _on_search_result_click(function_name, group_name)（frontend/ui/layout.py:1062）。
"""

import pytest


class TestSearchHighlightAndJump:
    """测试搜索结果高亮和跳转"""

    @pytest.mark.skip(reason="退役：highlight_search_results 关键字高亮已随旧搜索实现移除，"
                            "现行 _show_search_results 直接显示结果文本（门禁A 2026-10-19）")
    def test_drawer_layout_has_highlight_search_results_method(self):
        """测试 DrawerLayout 有 highlight_search_results 方法"""

    def test_drawer_layout_has_navigate_to_result_method(self):
        """测试 DrawerLayout 有搜索结果跳转方法（现行 API：_on_search_result_click）"""
        # Given: DrawerLayout 类
        # When: 检查方法
        # Then: _on_search_result_click 方法存在
        from frontend.ui.layout import DrawerLayout
        assert hasattr(DrawerLayout, '_on_search_result_click')

    @pytest.mark.skip(reason="退役：highlight_search_results 关键字高亮已随旧搜索实现移除，"
                            "现行 _show_search_results 直接显示结果文本（门禁A 2026-10-19）")
    def test_highlight_search_results_returns_highlighted_text(self):
        """测试 highlight_search_results 返回高亮文本"""

    @pytest.mark.skip(reason="退役：highlight_search_results 关键字高亮已随旧搜索实现移除，"
                            "现行 _show_search_results 直接显示结果文本（门禁A 2026-10-19）")
    def test_highlight_search_results_highlights_keyword(self):
        """测试 highlight_search_results 高亮关键字"""

    @pytest.mark.skip(reason="退役：highlight_search_results 关键字高亮已随旧搜索实现移除，"
                            "现行 _show_search_results 直接显示结果文本（门禁A 2026-10-19）")
    def test_highlight_search_results_for_no_match(self):
        """测试 highlight_search_results 无匹配时返回原文"""

    def test_navigate_to_result_for_function(self):
        """测试搜索结果点击跳转到功能（现行：_on_search_result_click 设分组并渲染）"""
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
        # When: 创建 DrawerLayout 实例并点击功能搜索结果
        from frontend.ui.layout import DrawerLayout
        layout = DrawerLayout(config)
        layout._on_search_result_click("通用配置", "基础设置")
        # Then: current_group 更新
        assert layout.current_group == "基础设置"

    def test_navigate_to_result_for_config_item(self):
        """测试搜索结果点击跳转到配置项所在功能（config 结果点击传入 parent_func + group）"""
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
        # When: 创建 DrawerLayout 实例并点击配置项搜索结果
        from frontend.ui.layout import DrawerLayout
        layout = DrawerLayout(config)
        layout._on_search_result_click("通用配置", "基础设置")
        # Then: current_group 更新
        assert layout.current_group == "基础设置"

    def test_navigate_to_result_for_unknown_result(self):
        """测试搜索结果点击处理未知/无匹配结果不抛异常（现行 _on_search_result_click 无类型分派）"""
        # Given: 配置字典
        config = {
            "webui": {
                "theme": {"choose": "default"},
                "function_groups": [],
                "config_items": {}
            }
        }
        # When: 创建 DrawerLayout 实例并点击不存在的结果
        from frontend.ui.layout import DrawerLayout
        layout = DrawerLayout(config)
        layout._on_search_result_click("不存在", "不存在")
        # Then: 不抛出异常

    @pytest.mark.skip(reason="退役：highlight_search_results 关键字高亮已随旧搜索实现移除，"
                            "现行 _show_search_results 直接显示结果文本（门禁A 2026-10-19）")
    def test_highlight_search_results_is_case_insensitive(self):
        """测试 highlight_search_results 不区分大小写"""

    @pytest.mark.skip(reason="退役：highlight_search_results 关键字高亮已随旧搜索实现移除，"
                            "现行 _show_search_results 直接显示结果文本（门禁A 2026-10-19）")
    def test_highlight_search_results_for_empty_keyword(self):
        """测试 highlight_search_results 空关键字时返回原文"""
