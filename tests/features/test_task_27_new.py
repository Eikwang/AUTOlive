"""
Task-27: 实现搜索栏功能
测试验证全局搜索功能：搜索框存在、命中功能/配置项、空结果、大小写不敏感、
空关键词恢复分组列表、搜索结果点击跳转。

门禁A 2026-10-19 重写：旧 API（search/highlight_search_results/navigate_to_result/
display_search_results）已消亡，现行实现为 DrawerLayout._on_global_search/
_show_search_results/_on_search_result_click + global_search_input
（frontend/ui/layout.py:738/882/943/1062）+ config_registry.search。
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
                        {"name": "通用配置", "enabled": True, "tab_key": "common_config"},
                        {"name": "页面配置", "enabled": True, "tab_key": "web"}
                    ]
                },
                {
                    "name": "互动设置",
                    "icon": "groups",
                    "functions": [
                        {"name": "积分", "enabled": True, "tab_key": "integral"}
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
                        }
                    }
                },
                "integral": {
                    "label": "积分系统",
                    "items": {
                        "enable": {
                            "label": "启用积分",
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


@pytest.fixture(autouse=True)
def clean_config_registry():
    """隔离配置项注册表（全局单例，防止跨测试污染搜索结果）"""
    from frontend.utils.config_registry import get_config_registry
    get_config_registry().clear()
    yield
    get_config_registry().clear()


@pytest.fixture
def layout(config, mock_drawer):
    """创建 DrawerLayout 实例"""
    with patch('frontend.ui.layout.ui') as mock_ui:
        mock_ui.left_drawer.return_value = mock_drawer
        from frontend.ui.layout import DrawerLayout
        layout = DrawerLayout(config)
        layout.drawer = mock_drawer
        return layout


def test_search_method_exists(layout):
    """测试全局搜索入口存在（现行 API：_on_global_search + _show_search_results）"""
    assert hasattr(layout, '_on_global_search')
    assert hasattr(layout, '_show_search_results')


def test_search_finds_function_by_name(layout):
    """测试搜索找到功能名称（现行：_on_global_search 遍历 function_groups 命中）"""
    with patch.object(layout, '_show_search_results') as mock_show:
        layout._on_global_search("积分")
        mock_show.assert_called_once()
        results, keyword = mock_show.call_args[0]
        assert keyword == "积分"
        assert any(
            r["name"] == "积分" and r["type"] == "function" and r["group"] == "互动设置"
            for r in results
        )


def test_search_finds_config_item_by_label(layout):
    """测试搜索找到配置项标签（现行：config_registry.search 命中后映射为 type=config 结果）"""
    from frontend.utils.config_registry import get_config_registry
    get_config_registry().register(
        tab_key="common_config",
        label="API IP",
        tooltip="",
        placeholder="",
        config_keys=("common_config", "api_ip")
    )
    with patch.object(layout, '_show_search_results') as mock_show:
        layout._on_global_search("API IP")
        results = mock_show.call_args[0][0]
        assert any(
            r.get("name") == "API IP" and r.get("type") == "config"
            and r.get("parent_func") == "通用配置" and r.get("group") == "基础功能"
            for r in results
        )


def test_search_returns_empty_for_no_match(layout):
    """测试搜索无结果时收到空结果列表"""
    with patch.object(layout, '_show_search_results') as mock_show:
        layout._on_global_search("不存在的关键字")
        mock_show.assert_called_once()
        results = mock_show.call_args[0][0]
        assert results == []


def test_search_is_case_insensitive(layout):
    """测试搜索不区分大小写（config_registry.search 对 label/tooltip/placeholder 做 lower 匹配）"""
    from frontend.utils.config_registry import get_config_registry
    get_config_registry().register(
        tab_key="common_config",
        label="API IP",
        tooltip="",
        placeholder="",
        config_keys=("common_config", "api_ip")
    )
    with patch.object(layout, '_show_search_results') as mock_show:
        layout._on_global_search("api")
        results = mock_show.call_args[0][0]
        assert any(r.get("name") == "API IP" for r in results)


def test_search_with_empty_keyword(layout):
    """测试空关键字搜索（现行：不进入搜索结果，恢复当前分组功能列表）"""
    layout.current_group = "基础功能"
    with patch.object(layout, '_show_search_results') as mock_show, \
         patch.object(layout, 'show_function_list') as mock_restore:
        layout._on_global_search("")
        mock_show.assert_not_called()
        mock_restore.assert_called_once_with("基础功能")


@pytest.mark.skip(reason="退役：highlight_search_results 关键字高亮已随旧搜索实现移除，"
                        "现行 _show_search_results 直接显示结果文本（门禁A 2026-10-19）")
def test_highlight_search_results_method_exists(layout):
    """测试 highlight_search_results 方法存在"""


@pytest.mark.skip(reason="退役：highlight_search_results 关键字高亮已随旧搜索实现移除，"
                        "现行 _show_search_results 直接显示结果文本（门禁A 2026-10-19）")
def test_highlight_search_results_highlights_keyword(layout):
    """测试高亮搜索结果中的关键字"""


@pytest.mark.skip(reason="退役：highlight_search_results 关键字高亮已随旧搜索实现移除，"
                        "现行 _show_search_results 直接显示结果文本（门禁A 2026-10-19）")
def test_highlight_search_results_returns_original_text(layout):
    """测试高亮搜索结果返回原始文本"""


def test_navigate_to_result_method_exists(layout):
    """测试搜索结果点击跳转方法存在（现行 API：_on_search_result_click）"""
    assert hasattr(layout, '_on_search_result_click')


def test_navigate_to_result_for_function(layout):
    """测试点击功能搜索结果跳转（现行：_on_search_result_click 设分组并渲染功能+配置页）"""
    with patch.object(layout, 'show_function_list') as mock_show_fn, \
         patch.object(layout, 'show_config_page') as mock_show_cfg:
        layout._on_search_result_click("积分", "互动设置")
        assert layout.current_group == "互动设置"
        mock_show_fn.assert_called_once_with("互动设置")
        mock_show_cfg.assert_called_once_with("积分")


def test_navigate_to_result_for_config_item(layout):
    """测试点击配置项搜索结果跳转到所在功能（config 结果点击传入 parent_func + group）"""
    with patch.object(layout, 'show_function_list'), \
         patch.object(layout, 'show_config_page') as mock_show_cfg:
        layout._on_search_result_click("通用配置", "基础功能")
        assert layout.current_group == "基础功能"
        mock_show_cfg.assert_called_once_with("通用配置")


def test_search_results_container_method_exists(layout):
    """测试 search_results_container 属性存在"""
    assert hasattr(layout, 'search_results_container')


@pytest.mark.skip(reason="退役：display_search_results 已消亡，被 _show_search_results 替代"
                        "（frontend/ui/layout.py:943）（门禁A 2026-10-19）")
def test_display_search_results_method_exists(layout):
    """测试 display_search_results 方法存在"""
