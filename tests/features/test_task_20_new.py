"""
Task-20: 实现导航栏收窄/展开切换
测试验证导航栏支持收窄/展开切换功能
"""
import pytest
from unittest.mock import MagicMock, patch


@pytest.fixture
def mock_drawer():
    """创建 mock drawer 对象"""
    drawer = MagicMock()
    drawer.style = MagicMock()
    return drawer


@pytest.fixture
def layout(mock_drawer):
    """创建 DrawerLayout 实例"""
    with patch('frontend.ui.layout.ui') as mock_ui:
        mock_ui.left_drawer.return_value = mock_drawer
        from frontend.ui.layout import DrawerLayout
        config = {
            "webui": {
                "drawer_mini": False,
                "function_groups": [
                    {"name": "基础功能", "icon": "settings", "functions": []}
                ]
            }
        }
        layout = DrawerLayout(config)
        layout.drawer = mock_drawer
        return layout


def test_toggle_drawer_to_mini(layout, mock_drawer):
    """测试收窄导航栏"""
    layout.toggle_drawer()
    assert layout.drawer_mini is True
    mock_drawer.style.assert_called_with('width: 60px')


def test_toggle_drawer_to_full(layout, mock_drawer):
    """测试展开导航栏"""
    layout.drawer_mini = True
    layout.toggle_drawer()
    assert layout.drawer_mini is False
    mock_drawer.style.assert_called_with('width: 200px')


def test_toggle_drawer_multiple_times(layout, mock_drawer):
    """测试多次切换导航栏状态"""
    # 初始状态为展开
    assert layout.drawer_mini is False

    # 第一次切换：收窄
    layout.toggle_drawer()
    assert layout.drawer_mini is True
    mock_drawer.style.assert_called_with('width: 60px')

    # 第二次切换：展开
    layout.toggle_drawer()
    assert layout.drawer_mini is False
    mock_drawer.style.assert_called_with('width: 200px')


def test_drawer_mini_state_persisted(layout):
    """测试收窄状态被保存到配置"""
    with patch.object(layout, 'save_config') as mock_save:
        layout.toggle_drawer()
        mock_save.assert_called_once()


def test_navigation_items_show_icon_only_in_mini_mode(layout):
    """测试收窄模式下只显示图标"""
    layout.drawer_mini = True
    # 验证收窄模式下的样式
    assert layout.get_drawer_width() == '60px'


def test_navigation_items_show_icon_and_text_in_full_mode(layout):
    """测试展开模式下显示图标和文字"""
    layout.drawer_mini = False
    # 验证展开模式下的样式
    assert layout.get_drawer_width() == '200px'


def test_drawer_width_methods(layout):
    """测试获取导航栏宽度的方法"""
    # 展开模式
    layout.drawer_mini = False
    assert layout.get_drawer_width() == '200px'

    # 收窄模式
    layout.drawer_mini = True
    assert layout.get_drawer_width() == '60px'
