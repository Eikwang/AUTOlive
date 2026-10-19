"""
Task-25: 实现三级页面（配置页面显示）
测试验证点击功能项后显示配置页面
"""
import pytest
from unittest.mock import MagicMock, patch

@pytest.fixture
def config():
    """创建测试配置"""
    return {
        "webui": {
            "drawer_mini": False,
            "function_groups": [
                {
                    "name": "基础功能",
                    "icon": "settings",
                    "functions": [
                        {"name": "通用配置", "enabled": True, "tab_key": "common_config"}
                    ]
                }
            ],
            "config_items": {
                "common_config": {
                    "label": "通用配置",
                    "items": {
                        "api_ip": {
                            "label": "API IP",
                            "type": "input",
                            "value": "127.0.0.1"
                        },
                        "api_port": {
                            "label": "API 端口",
                            "type": "input",
                            "value": "8080"
                        },
                        "enable_feature": {
                            "label": "启用功能",
                            "type": "switch",
                            "value": True
                        }
                    }
                }
            }
        }
    }

@pytest.fixture
def mock_drawer():
    """创建 mock drawer 对象"""
    drawer = MagicMock()
    drawer.__enter__ = MagicMock(return_value=drawer)
    drawer.__exit__ = MagicMock(return_value=False)
    return drawer

@pytest.fixture
def layout(config, mock_drawer):
    """创建 DrawerLayout 实例"""
    from frontend.styles.themes import init_theme_manager
    init_theme_manager(config)  # _render_tab_content 依赖全局主题管理器
    with patch('frontend.ui.layout.ui') as mock_ui:
        mock_ui.left_drawer.return_value = mock_drawer
        from frontend.ui.layout import DrawerLayout
        layout = DrawerLayout(config)
        layout.drawer = mock_drawer
        # 模拟 splitter 的 before 和 after 容器
        mock_function_list = MagicMock()
        mock_function_list.__enter__ = MagicMock(return_value=mock_function_list)
        mock_function_list.__exit__ = MagicMock(return_value=False)
        layout.function_list = mock_function_list
        mock_config_page = MagicMock()
        mock_config_page.__enter__ = MagicMock(return_value=mock_config_page)
        mock_config_page.__exit__ = MagicMock(return_value=False)
        layout.config_page = mock_config_page
        return layout

def test_show_config_page_displays_config_items(layout):
    """测试显示配置页面按 tab_key 分发到对应 tab 模块"""
    # 先设置 function_items
    layout.function_items = {
        "通用配置": {"tab_key": "common_config"}
    }

    with patch('frontend.ui.tabs.common_config.create_common_config_tab') as mock_tab:
        layout.show_config_page("通用配置")

        # 验证配置渲染分发到 common_config tab 模块（现行契约：布局层只分发）
        mock_tab.assert_called_once()

def test_show_config_page_clears_previous_content(layout):
    """测试显示配置页面时清除之前的内容"""
    # 设置 function_items
    layout.function_items = {
        "通用配置": {"tab_key": "common_config"}
    }

    with patch('frontend.ui.tabs.common_config.create_common_config_tab'):
        layout.show_config_page("通用配置")

        # 验证清除了之前的内容（可能被调用多次）
        assert layout.config_page.clear.call_count >= 1

@pytest.mark.skip(reason="退役：布局层不再直接渲染 ui.input/ui.switch，配置控件已下沉到 "
                        "frontend/ui/tabs/ 各 tab 模块（任务26 tab 化），旧 config_items "
                        "通用渲染契约消亡（门禁A 2026-10-19）")
def test_show_config_page_displays_input_fields(layout):
    """测试显示配置页面显示输入框"""
    # 设置 function_items
    layout.function_items = {
        "通用配置": {"enabled": True, "tab_key": "common_config"}
    }

    with patch('frontend.ui.layout.ui') as mock_ui:
        mock_column = MagicMock()
        mock_ui.column.return_value = mock_column

        layout.show_config_page("通用配置")

        # 验证调用了 ui.input
        assert mock_ui.input.call_count >= 1

@pytest.mark.skip(reason="退役：布局层不再直接渲染 ui.input/ui.switch，配置控件已下沉到 "
                        "frontend/ui/tabs/ 各 tab 模块（任务26 tab 化），旧 config_items "
                        "通用渲染契约消亡（门禁A 2026-10-19）")
def test_show_config_page_displays_switch_fields(layout):
    """测试显示配置页面显示开关"""
    # 设置 function_items
    layout.function_items = {
        "通用配置": {"enabled": True, "tab_key": "common_config"}
    }

    with patch('frontend.ui.layout.ui') as mock_ui:
        mock_column = MagicMock()
        mock_ui.column.return_value = mock_column

        layout.show_config_page("通用配置")

        # 验证调用了 ui.switch
        assert mock_ui.switch.call_count >= 1

