"""
Task-21: 实现导航栏状态持久化
测试验证导航栏状态保存到 config.json 的 drawer_mini 字段
"""
import json
import os
import pytest
from unittest.mock import MagicMock, patch


@pytest.fixture
def config():
    """创建测试配置"""
    return {
        "webui": {
            "drawer_mini": False,
            "function_groups": []
        }
    }


@pytest.fixture
def layout(config):
    """创建 DrawerLayout 实例"""
    with patch('frontend.ui.layout.ui') as mock_ui:
        mock_ui.left_drawer.return_value = MagicMock()
        from frontend.ui.layout import DrawerLayout
        return DrawerLayout(config)


def test_drawer_mini_initial_value_from_config(config):
    """测试从配置加载 drawer_mini 初始值"""
    config["webui"]["drawer_mini"] = True
    with patch('frontend.ui.layout.ui') as mock_ui:
        mock_ui.left_drawer.return_value = MagicMock()
        from frontend.ui.layout import DrawerLayout
        layout = DrawerLayout(config)
        assert layout.drawer_mini is True


def test_drawer_mini_default_value():
    """测试 drawer_mini 默认值为 False"""
    config = {"webui": {}}
    with patch('frontend.ui.layout.ui') as mock_ui:
        mock_ui.left_drawer.return_value = MagicMock()
        from frontend.ui.layout import DrawerLayout
        layout = DrawerLayout(config)
        assert layout.drawer_mini is False


def test_toggle_drawer_updates_config(layout):
    """测试切换导航栏状态更新配置"""
    layout.toggle_drawer()
    assert layout.config["webui"]["drawer_mini"] is True


def test_toggle_drawer_multiple_times_updates_config(layout):
    """测试多次切换导航栏状态更新配置"""
    # 初始状态
    assert layout.config["webui"]["drawer_mini"] is False

    # 第一次切换
    layout.toggle_drawer()
    assert layout.config["webui"]["drawer_mini"] is True

    # 第二次切换
    layout.toggle_drawer()
    assert layout.config["webui"]["drawer_mini"] is False


def test_save_config_called_on_toggle(layout):
    """测试切换时调用保存配置"""
    with patch.object(layout, 'save_config') as mock_save:
        layout.toggle_drawer()
        mock_save.assert_called_once()


def test_config_value_persists_after_toggle(layout):
    """测试配置值在切换后保持"""
    layout.toggle_drawer()
    # 验证配置值
    assert layout.config["webui"]["drawer_mini"] is True
    # 再次切换
    layout.toggle_drawer()
    assert layout.config["webui"]["drawer_mini"] is False


def test_drawer_mini_state_independent_of_other_config(layout):
    """测试 drawer_mini 状态独立于其他配置"""
    # 添加其他配置
    layout.config["webui"]["theme"] = {"choose": "dark"}
    layout.config["webui"]["other_setting"] = "value"

    # 切换导航栏
    layout.toggle_drawer()

    # 验证其他配置不受影响
    assert layout.config["webui"]["theme"]["choose"] == "dark"
    assert layout.config["webui"]["other_setting"] == "value"
    assert layout.config["webui"]["drawer_mini"] is True
