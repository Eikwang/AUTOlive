"""
Task-22: 更新导航栏渲染逻辑
测试验证导航栏分组渲染和收窄/展开模式

门禁A 2026-10-19 对齐现行实现重写：
- 分组项现为可点击行（ui.row + on('click')），不再使用 ui.button；分组名渲染为
  行内 ui.label，图标经 ui.icon 渲染
- 收窄/展开模式不改变分组渲染，仅经 control_section.visible 切换启停控制区
  可见性（E9：60px 折叠态下复合按钮与 44px 点击目标数学不成立）
- select_group 现经 ui.timer(0.3) 延迟显示功能列表；create_navigation_groups
  之前 group_nav_container 不存在（None → 导航渲染早退）
"""
import pytest
from unittest.mock import MagicMock, patch, call


@pytest.fixture
def config():
    """创建测试配置（现行 7 组结构）"""
    return {
        "webui": {
            "drawer_mini": False,
            "function_groups": [
                {"name": "基础功能", "icon": "settings", "functions": []},
                {"name": "AI对话", "icon": "chat", "functions": []},
                {"name": "互动设置", "icon": "groups", "functions": []},
                {"name": "助播", "icon": "record_voice_over", "functions": []},
                {"name": "高级功能", "icon": "build", "functions": []},
                {"name": "数字人", "icon": "accessibility_new", "functions": []},
                {"name": "系统设置", "icon": "admin_panel_settings", "functions": []}
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
        # create_navigation_groups 之前分组导航容器不存在（None → 渲染早退）
        layout.group_nav_container = None
        return layout


def _get_group_click_handlers(mock_ui):
    """从 mock ui 提取分组行的 click 处理器（分组行经 ui.row().style(...) 链式创建）"""
    group_row = mock_ui.row.return_value.style.return_value
    return [c.args[1] for c in group_row.on.call_args_list
            if c.args and c.args[0] == 'click']


def test_create_navigation_groups_renders_all_groups(layout):
    """测试为每个分组渲染一个可点击导航项"""
    with patch('frontend.ui.layout.ui') as mock_ui, \
         patch.object(layout, 'select_group'):
        layout.create_navigation_groups()

        # 验证 7 个分组各注册一个 click 处理器（现行：可点击行而非按钮）
        assert len(_get_group_click_handlers(mock_ui)) == 7


def test_create_navigation_groups_with_correct_names(layout):
    """测试创建的导航项名称正确"""
    with patch('frontend.ui.layout.ui') as mock_ui:
        layout.create_navigation_groups()

        # 分组名渲染为行内 label（侧边栏标题 "AUTOlive" 也在 label 调用中）
        label_args = [c.args[0] for c in mock_ui.label.call_args_list if c.args]
        expected_names = [
            "基础功能", "AI对话", "互动设置", "助播",
            "高级功能", "数字人", "系统设置"
        ]
        for name in expected_names:
            assert name in label_args


def test_create_navigation_groups_with_icons(layout):
    """测试创建的导航项有图标"""
    with patch('frontend.ui.layout.ui') as mock_ui:
        layout.create_navigation_groups()

        # 验证每个分组都经 ui.icon 渲染了对应图标（现行：行内 icon 而非按钮 props）
        icon_args = [c.args[0] for c in mock_ui.icon.call_args_list if c.args]
        expected_icons = [
            "settings", "chat", "groups", "record_voice_over",
            "build", "accessibility_new", "admin_panel_settings"
        ]
        for icon in expected_icons:
            assert icon in icon_args


def test_navigation_groups_in_mini_mode(layout):
    """测试收窄模式下的导航栏"""
    layout.drawer_mini = True
    with patch('frontend.ui.layout.ui') as mock_ui, \
         patch.object(layout, 'select_group'):
        layout.create_navigation_groups()

        # 分组导航照常渲染，启停控制区隐藏（E9 折叠态）
        assert len(_get_group_click_handlers(mock_ui)) == 7
        assert layout.control_section.visible is False


def test_navigation_groups_in_full_mode(layout):
    """测试展开模式下的导航栏"""
    layout.drawer_mini = False
    with patch('frontend.ui.layout.ui') as mock_ui, \
         patch.object(layout, 'select_group'):
        layout.create_navigation_groups()

        # 分组导航照常渲染，启停控制区可见
        assert len(_get_group_click_handlers(mock_ui)) == 7
        assert layout.control_section.visible is True


def test_select_group_updates_current_group(layout):
    """测试选择分组更新当前分组"""
    with patch('frontend.ui.layout.ui'):
        layout.select_group("基础功能")
    assert layout.current_group == "基础功能"


def test_select_group_multiple_times(layout):
    """测试多次选择分组"""
    with patch('frontend.ui.layout.ui'):
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
