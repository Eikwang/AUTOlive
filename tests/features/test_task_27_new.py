"""
Task-27: 实现搜索栏功能
测试验证搜索框和搜索结果显示
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
    """测试 search 方法存在"""
    assert hasattr(layout, 'search')


def test_search_finds_function_by_name(layout):
    """测试搜索找到功能名称"""
    layout.search("积分")
    assert len(layout.search_results) > 0
    assert any(r["name"] == "积分" for r in layout.search_results)


def test_search_finds_config_item_by_label(layout):
    """测试搜索找到配置项标签"""
    layout.search("API")
    assert len(layout.search_results) > 0
    assert any(r.get("label") == "API IP" for r in layout.search_results)


def test_search_returns_empty_for_no_match(layout):
    """测试搜索无结果返回空列表"""
    layout.search("不存在的关键字")
    assert len(layout.search_results) == 0


def test_search_is_case_insensitive(layout):
    """测试搜索不区分大小写"""
    layout.search("api")
    assert len(layout.search_results) > 0


def test_search_with_empty_keyword(layout):
    """测试空关键字搜索"""
    layout.search("")
    assert len(layout.search_results) == 0


def test_highlight_search_results_method_exists(layout):
    """测试 highlight_search_results 方法存在"""
    assert hasattr(layout, 'highlight_search_results')


def test_highlight_search_results_highlights_keyword(layout):
    """测试高亮搜索结果中的关键字"""
    result = layout.highlight_search_results("通用配置", "通用")
    assert "<mark>" in result


def test_highlight_search_results_returns_original_text(layout):
    """测试高亮搜索结果返回原始文本"""
    result = layout.highlight_search_results("通用配置", "不存在")
    assert result == "通用配置"


def test_navigate_to_result_method_exists(layout):
    """测试 navigate_to_result 方法存在"""
    assert hasattr(layout, 'navigate_to_result')


def test_navigate_to_result_for_function(layout):
    """测试跳转到功能搜索结果"""
    with patch.object(layout, 'select_group') as mock_select:
        result = {
            "type": "function",
            "name": "积分",
            "group": "互动设置",
            "tab_key": "integral"
        }
        layout.navigate_to_result(result)
        mock_select.assert_called_once_with("互动设置")


def test_navigate_to_result_for_config_item(layout):
    """测试跳转到配置项搜索结果"""
    with patch.object(layout, 'select_group') as mock_select:
        result = {
            "type": "config_item",
            "name": "api_ip",
            "label": "API IP",
            "function": "common_config",
            "group": "通用配置"
        }
        layout.navigate_to_result(result)
        mock_select.assert_called_once_with("基础功能")


def test_search_results_container_method_exists(layout):
    """测试 search_results_container 属性存在"""
    assert hasattr(layout, 'search_results_container')


def test_display_search_results_method_exists(layout):
    """测试 display_search_results 方法存在"""
    assert hasattr(layout, 'display_search_results')
