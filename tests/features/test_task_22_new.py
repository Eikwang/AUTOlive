"""
Task-22: 更新导航栏渲染逻辑
测试验证导航栏支持8个分组和收窄/展开模式
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
                {"name": "基础功能", "icon": "settings", "functions": []},
                {"name": "直播选项", "icon": "live_tv", "functions": []},
                {"name": "互动设置", "icon": "groups", "functions": []},
                {"name": "AI对话", "icon": "chat", "functions": []},
                {"name": "高级功能", "icon": "build", "functions": []},
                {"name": "系统设置", "icon": "admin_panel_settings", "functions": []},
                {"name": "运行状态", "icon": "monitoring", "functions": []},
                {"name": "色彩模式", "icon": "palette", "functions": []}
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
        return layout


def test_create_navigation_groups_creates_8_buttons(layout, mock_drawer):
    """测试创建8个导航分组按钮"""
    with patch('frontend.ui.layout.ui') as mock_ui:
        mock_button = MagicMock()
        mock_ui.button.return_value = mock_button

        layout.create_navigation_groups()

        # 验证创建了8个按钮
        assert mock_ui.button.call_count == 8


def test_create_navigation_groups_with_correct_names(layout, mock_drawer):
    """测试创建的按钮名称正确"""
    with patch('frontend.ui.layout.ui') as mock_ui:
        mock_button = MagicMock()
        mock_ui.button.return_value = mock_button

        layout.create_navigation_groups()

        # 验证按钮名称
        expected_names = [
            "基础功能", "直播选项", "互动设置", "AI对话",
            "高级功能", "系统设置", "运行状态", "色彩模式"
        ]
        actual_names = [call.args[0] for call in mock_ui.button.call_args_list]
        assert actual_names == expected_names


def test_create_navigation_groups_with_icons(layout, mock_drawer):
    """测试创建的按钮有图标"""
    with patch('frontend.ui.layout.ui') as mock_ui:
        mock_button = MagicMock()
        mock_ui.button.return_value = mock_button

        layout.create_navigation_groups()

        # 验证每个按钮都设置了图标
        for call_args in mock_ui.button.call_args_list:
            button = call_args.return_value
            button.props.assert_called()


def test_navigation_groups_in_mini_mode(layout, mock_drawer):
    """测试收窄模式下的导航栏"""
    layout.drawer_mini = True
    with patch('frontend.ui.layout.ui') as mock_ui:
        mock_button = MagicMock()
        mock_ui.button.return_value = mock_button

        layout.create_navigation_groups()

        # 验证收窄模式下按钮样式
        assert mock_ui.button.call_count == 8


def test_navigation_groups_in_full_mode(layout, mock_drawer):
    """测试展开模式下的导航栏"""
    layout.drawer_mini = False
    with patch('frontend.ui.layout.ui') as mock_ui:
        mock_button = MagicMock()
        mock_ui.button.return_value = mock_button

        layout.create_navigation_groups()

        # 验证展开模式下按钮样式
        assert mock_ui.button.call_count == 8


def test_select_group_updates_current_group(layout):
    """测试选择分组更新当前分组"""
    layout.select_group("基础功能")
    assert layout.current_group == "基础功能"


def test_select_group_multiple_times(layout):
    """测试多次选择分组"""
    layout.select_group("基础功能")
    assert layout.current_group == "基础功能"

    layout.select_group("AI对话")
    assert layout.current_group == "AI对话"


def test_get_group_functions_returns_correct_functions(layout):
    """测试获取分组下的功能列表"""
    functions = layout.get_group_functions("基础功能")
    assert isinstance(functions, list)


def test_get_group_functions_returns_empty_for_nonexistent_group(layout):
    """测试获取不存在的分组返回空列表"""
    functions = layout.get_group_functions("不存在的分组")
    assert functions == []
