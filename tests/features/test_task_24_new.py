"""
Task-24: 实现二级页面（功能列表显示）
测试验证点击导航栏分组后显示功能列表

门禁A 2026-10-19 对齐现行实现重写：
- select_group 现经 ui.timer(0.3, once=True) 延迟触发 show_function_list（加载态 spinner）
- 分组导航现为可点击行（row.on('click')），不再使用 ui.button
- function_items 现仅存 tab_key（enabled 已随任务6退役）
- show_function_list 末尾默认打开第一个功能的配置页（DG-B9），链路含真实 tab
  渲染（依赖运行期初始化的 theme manager），测试中以 _render_tab_content
  打桩隔离——配置页渲染本身是后续任务的验收范围
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
        # create_navigation_groups 之前分组导航容器不存在（None → 渲染早退）
        layout.group_nav_container = None
        return layout


def test_select_group_calls_show_function_list(layout):
    """测试选择分组后（经 0.3s 延迟）调用显示功能列表"""
    with patch('frontend.ui.layout.ui') as mock_ui, \
         patch.object(layout, 'show_function_list') as mock_show:
        layout.select_group("基础功能")

        # 现行实现：先渲染加载态，再经 ui.timer(0.3, ..., once=True) 延迟显示
        mock_ui.timer.assert_called_once()
        delay, callback = mock_ui.timer.call_args[0]
        assert delay == 0.3
        assert mock_ui.timer.call_args[1].get("once") is True

        # 触发延迟回调，功能列表才真正显示
        callback()
        mock_show.assert_called_once_with("基础功能")


def test_show_function_list_displays_functions(layout):
    """测试显示功能列表"""
    with patch('frontend.ui.layout.ui'), \
         patch.object(layout, '_render_tab_content'):
        layout.show_function_list("基础功能")

        # 验证显示了功能列表（通过检查 function_list.clear 被调用）
        layout.function_list.clear.assert_called()


def test_show_function_list_clears_previous_content(layout):
    """测试显示功能列表时清除之前的内容"""
    with patch('frontend.ui.layout.ui'), \
         patch.object(layout, '_render_tab_content'):
        layout.show_function_list("基础功能")

        # 验证清除了之前的内容（function_list.clear 被调用）
        layout.function_list.clear.assert_called()


def test_show_function_list_creates_function_items(layout):
    """测试显示功能列表时创建功能项"""
    with patch('frontend.ui.layout.ui'), \
         patch.object(layout, '_render_tab_content'):
        layout.show_function_list("基础功能")

        # 验证创建了功能项
        assert len(layout.function_items) == 2
        assert "通用配置" in layout.function_items
        assert "页面配置" in layout.function_items


def test_show_function_list_for_interaction_group(layout):
    """测试显示互动设置分组的功能列表"""
    with patch('frontend.ui.layout.ui'), \
         patch.object(layout, '_render_tab_content'):
        layout.show_function_list("互动设置")

        # 验证创建了功能项
        assert len(layout.function_items) == 2
        assert "积分" in layout.function_items
        assert "助播" in layout.function_items


def test_show_function_list_stores_function_info(layout):
    """测试显示功能列表时存储功能信息"""
    with patch('frontend.ui.layout.ui'), \
         patch.object(layout, '_render_tab_content'):
        layout.show_function_list("基础功能")

        # 验证存储了功能信息（现行仅存 tab_key，enabled 已退役）
        assert layout.function_items["通用配置"] == {"tab_key": "common_config"}


def test_show_function_list_opens_first_function_config(layout):
    """测试显示功能列表后默认打开第一个功能的配置页（DG-B9）"""
    with patch('frontend.ui.layout.ui'), \
         patch.object(layout, '_render_tab_content') as mock_tab:
        layout.show_function_list("基础功能")

        # 验证默认选中第一个功能并渲染其配置内容
        assert layout.current_function == "通用配置"
        mock_tab.assert_called_once_with("common_config")


def test_show_function_list_for_unknown_group(layout):
    """测试显示未知分组的功能列表"""
    with patch('frontend.ui.layout.ui'), \
         patch.object(layout, '_render_tab_content') as mock_tab:
        layout.show_function_list("未知分组")

        # 验证没有创建功能项，也未渲染任何配置页
        assert len(layout.function_items) == 0
        mock_tab.assert_not_called()


def test_create_navigation_groups_calls_select_group(layout):
    """测试导航分组创建为可点击行，点击触发 select_group

    现行实现：分组项是 ui.row（on('click') 绑定 select_group），不再使用 ui.button。
    """
    with patch('frontend.ui.layout.ui') as mock_ui, \
         patch.object(layout, 'select_group') as mock_select:
        layout.create_navigation_groups()

        # 3 个分组各注册一个 click 处理器（mouseenter/mouseleave 为悬停效果，不计）
        # 分组行经 ui.row().style(...) 链式创建，on 注册在 style 返回的 mock 上
        group_row = mock_ui.row.return_value.style.return_value
        click_handlers = [c.args[1] for c in group_row.on.call_args_list
                          if c.args and c.args[0] == 'click']
        assert len(click_handlers) == 3

        # 逐个触发点击，验证按配置顺序绑定到 select_group
        for handler in click_handlers:
            handler(None)
        assert [c.args[0] for c in mock_select.call_args_list] == \
            ["基础功能", "互动设置", "AI对话"]


def test_function_list_container_is_created(layout):
    """测试功能列表容器被创建"""
    with patch('frontend.ui.layout.ui'), \
         patch.object(layout, '_render_tab_content'):
        layout.show_function_list("基础功能")

        # 验证功能列表容器被清空并重新创建内容
        layout.function_list.clear.assert_called()
        layout.function_list_container.clear.assert_called()
