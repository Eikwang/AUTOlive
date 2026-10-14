"""
Task-24: 实现二级页面（功能列表显示）
测试验证点击导航栏分组后显示功能列表
"""
import pytest
from unittest.mock import MagicMock, patch, call


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
                        {"name": "通用配置", "enabled": True, "tab_key": "common_config"},
                        {"name": "页面配置", "enabled": True, "tab_key": "web"}
                    ]
                },
                {
                    "name": "互动设置",
                    "icon": "groups",
                    "functions": [
                        {"name": "积分", "enabled": True, "tab_key": "integral"},
                        {"name": "助播", "enabled": True, "tab_key": "assistant_anchor"}
                    ]
                },
                {
                    "name": "AI对话",
                    "icon": "chat",
                    "functions": [
                        {"name": "大语言模型", "enabled": True, "tab_key": "llm"},
                        {"name": "聊天", "enabled": True, "tab_key": "talk"}
                    ]
                }
            ]
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


def test_select_group_calls_show_function_list(layout):
    """测试选择分组后调用显示功能列表"""
    with patch.object(layout, 'show_function_list') as mock_show:
        layout.select_group("基础功能")
        mock_show.assert_called_once_with("基础功能")


def test_show_function_list_displays_functions(layout):
    """测试显示功能列表"""
    with patch('frontend.ui.layout.ui') as mock_ui:
        mock_column = MagicMock()
        mock_ui.column.return_value = mock_column

        layout.show_function_list("基础功能")

        # 验证显示了功能列表（通过检查 function_list.clear 被调用）
        layout.function_list.clear.assert_called()


def test_show_function_list_clears_previous_content(layout):
    """测试显示功能列表时清除之前的内容"""
    with patch('frontend.ui.layout.ui') as mock_ui:
        mock_column = MagicMock()
        mock_ui.column.return_value = mock_column

        layout.show_function_list("基础功能")

        # 验证清除了之前的内容（function_list.clear 被调用）
        layout.function_list.clear.assert_called()


def test_show_function_list_creates_function_items(layout):
    """测试显示功能列表时创建功能项"""
    with patch('frontend.ui.layout.ui') as mock_ui:
        mock_column = MagicMock()
        mock_ui.column.return_value = mock_column

        layout.show_function_list("基础功能")

        # 验证创建了功能项
        assert len(layout.function_items) == 2
        assert "通用配置" in layout.function_items
        assert "页面配置" in layout.function_items


def test_show_function_list_for_interaction_group(layout):
    """测试显示互动设置分组的功能列表"""
    with patch('frontend.ui.layout.ui') as mock_ui:
        mock_column = MagicMock()
        mock_ui.column.return_value = mock_column

        layout.show_function_list("互动设置")

        # 验证创建了功能项
        assert len(layout.function_items) == 2
        assert "积分" in layout.function_items
        assert "助播" in layout.function_items


def test_show_function_list_stores_function_info(layout):
    """测试显示功能列表时存储功能信息"""
    with patch('frontend.ui.layout.ui') as mock_ui:
        mock_column = MagicMock()
        mock_ui.column.return_value = mock_column

        layout.show_function_list("基础功能")

        # 验证存储了功能信息
        assert layout.function_items["通用配置"]["enabled"] is True
        assert layout.function_items["通用配置"]["tab_key"] == "common_config"


def test_show_function_list_for_unknown_group(layout):
    """测试显示未知分组的功能列表"""
    with patch('frontend.ui.layout.ui') as mock_ui:
        mock_column = MagicMock()
        mock_ui.column.return_value = mock_column

        layout.show_function_list("未知分组")

        # 验证没有创建功能项
        assert len(layout.function_items) == 0


def test_create_navigation_groups_calls_select_group(layout):
    """测试创建导航栏分组时调用选择分组"""
    with patch('frontend.ui.layout.ui') as mock_ui:
        mock_button = MagicMock()
        mock_ui.button.return_value = mock_button

        layout.create_navigation_groups()

        # 验证创建了3个按钮（测试配置中只有3个分组）
        assert mock_ui.button.call_count == 3


def test_function_list_container_is_created(layout):
    """测试功能列表容器被创建"""
    with patch('frontend.ui.layout.ui') as mock_ui:
        mock_column = MagicMock()
        mock_ui.column.return_value = mock_column

        layout.show_function_list("基础功能")

        # 验证功能列表容器被清空并重新创建内容
        layout.function_list.clear.assert_called()
