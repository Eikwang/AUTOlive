"""
Task-28: 实现色彩切换功能
测试验证主题切换功能
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
                    "name": "色彩模式",
                    "icon": "palette",
                    "functions": [
                        {"name": "主题切换", "enabled": True, "tab_key": "theme"}
                    ]
                }
            ],
            "theme": {
                "choose": "light"
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
    with patch('frontend.ui.layout.ui') as mock_ui:
        mock_ui.left_drawer.return_value = mock_drawer
        from frontend.ui.layout import DrawerLayout
        layout = DrawerLayout(config)
        layout.drawer = mock_drawer
        return layout


def test_toggle_theme_method_exists(layout):
    """测试 toggle_theme 方法存在"""
    assert hasattr(layout, 'toggle_theme')


def test_toggle_theme_switches_mode(layout):
    """测试切换主题切换模式"""
    with patch('frontend.ui.layout.ui') as mock_ui:
        mock_dark_mode = MagicMock()
        mock_dark_mode.value = False
        mock_ui.dark_mode.return_value = mock_dark_mode

        layout.toggle_theme()

        # 验证 dark_mode.value 被切换
        assert mock_dark_mode.value is True


