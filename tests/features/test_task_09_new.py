# -*- coding: UTF-8 -*-
"""
Task-09: 实现配置页面显示 测试
2026-10-19 门禁A 重写：对齐现行渲染契约——
render_config_page 已重构为"清空 config_page → 标题行 → DG-B1 状态条 →
按 tab_key 调用真实 tab 模块函数"；未知 tab_key 走"暂无配置页面"兜底。
config_items 驱动的 input/switch/select/textarea 通用渲染与 config_values
副本存储已消亡（值经 set_config 回调直接写 config，唯一状态源）。
测试环境须先 init_theme_manager（业务全局单例，_render_tab_content 依赖）。
"""

import pytest
from unittest.mock import MagicMock


@pytest.fixture(autouse=True)
def _init_theme_manager():
    """初始化全局主题管理器（render_config_page → _render_tab_content 依赖）"""
    from frontend.styles.themes import init_theme_manager
    init_theme_manager({"webui": {"theme": {"choose": "default"}}})


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
    """测试配置页面显示（现行契约：tab 模块函数渲染）"""

    def test_drawer_layout_has_render_config_page_method(self):
        """测试 DrawerLayout 有 render_config_page 方法"""
        from frontend.ui.layout import DrawerLayout
        assert hasattr(DrawerLayout, 'render_config_page')

    def test_render_config_page_clears_page_before_render(self):
        """测试 render_config_page 渲染前清空 config_page"""
        config = {"webui": {"theme": {"choose": "default"}}}
        layout = _create_layout(config)
        layout.render_config_page("test_function")
        layout.config_page.clear.assert_called()

    def test_render_config_page_survives_unknown_tab_key(self):
        """测试 render_config_page 对未知 tab_key 走兜底不抛异常"""
        config = {"webui": {"theme": {"choose": "default"}}}
        layout = _create_layout(config)
        # 不在 tab_mapping 的 tab_key 走"暂无配置页面"提示分支
        layout.render_config_page("unknown_function")

    def test_render_config_page_resets_enable_path_state(self):
        """测试 render_config_page 重置 DG-B1 启用开关状态条引用"""
        config = {"webui": {"theme": {"choose": "default"}}}
        layout = _create_layout(config)
        # 预置脏状态，渲染后应被重置（tab_key 无 ENABLE_PATHS 映射）
        layout._current_page_enable_path = ("stale", "path")
        layout.config_status_label = MagicMock()
        layout.render_config_page("test_function")
        assert layout._current_page_enable_path is None
        assert layout.config_status_label is None

    def test_render_config_page_displays_textarea(self):
        """测试 render_config_page 走真实 tab 渲染（以 common_config 为例，仅验证不抛异常）"""
        config = {"webui": {"theme": {"choose": "default"}}}
        layout = _create_layout(config)
        # 现行渲染按 tab_key 调用真实 tab 模块函数；选轻量 tab 验证链路可达
        layout.render_config_page("common_config")
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

    def test_render_config_page_does_not_store_config_values(self):
        """测试渲染不再向 config_values 存副本（现行契约：config 为唯一状态源）"""
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
        # 旧契约（渲染时把 config_items 值复制进 config_values）已消亡：
        # 现行值经 set_config 回调直接写 config，config_values 恒为空
        assert layout.config_values == {}
