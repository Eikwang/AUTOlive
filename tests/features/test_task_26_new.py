"""
Task-26: 实现保存配置按钮
验收意图：文案类配置的修改能够保存到配置。

门禁A 2026-10-19 重写：保存按钮/manual_save/auto_save 已随旧实现移除，现行保存机制为
frontend/utils/config_auto_save.py 的 auto_bind/create_auto_save——组件值变化即写配置
字典并经 set_config 回调落盘（frontend/main.py AutoliveApp.set_config）；配置页渲染
（render_config_page）转交 tab 模块，DrawerLayout 仅保留 save_config（走 save_callback）。
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
                        "description": {
                            "label": "描述",
                            "type": "textarea",
                            "value": "这是一个测试描述"
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


class _ChangeEvent:
    """模拟 NiceGUI 值变化事件（仅暴露 .value）"""
    def __init__(self, value):
        self.value = value


def _bind_and_fire(component, config_dict, keys, new_value):
    """用 auto_bind 绑定组件并触发一次值变化，返回触发的 on_change 结果"""
    from frontend.utils.config_auto_save import auto_bind
    auto_bind(component, config_dict, keys)
    on_change = component.on_value_change.call_args[0][0]
    on_change(_ChangeEvent(new_value))


@pytest.mark.skip(reason="退役：文案类配置的保存按钮已移除，textarea 等组件改由 auto_bind "
                        "值变化即自动保存（frontend/utils/config_auto_save.py）（门禁A 2026-10-19）")
def test_textarea_has_save_button(layout):
    """测试 textarea 类型配置有保存按钮"""


@pytest.mark.skip(reason="退役：保存按钮与 manual_save 已消亡，保存改为 auto_bind 组件级自动保存 "
                        "+ set_config 回调落盘（frontend/main.py set_config）（门禁A 2026-10-19）")
def test_save_button_calls_manual_save(layout):
    """测试保存按钮调用手动保存"""


def test_manual_save_updates_config(layout):
    """测试保存更新配置（现行：auto_bind 组件值变化即写配置字典）"""
    _bind_and_fire(
        MagicMock(),
        layout.config["webui"],
        ("config_items", "common_config", "items", "description", "value"),
        "新描述"
    )
    # 验证配置已更新
    config_items = layout.config["webui"]["config_items"]["common_config"]["items"]
    assert config_items["description"]["value"] == "新描述"


@pytest.mark.skip(reason="退役：DrawerLayout.config_values 保存路径已随旧 manual_save 移除，"
                        "现行保存直接写 config 字典（见 test_manual_save_updates_config）"
                        "（门禁A 2026-10-19）")
def test_manual_save_updates_config_values(layout):
    """测试手动保存更新配置值"""


@pytest.mark.skip(reason="退役：保存按钮已移除，save_button_text 按钮文本更新逻辑随之消亡"
                        "（门禁A 2026-10-19）")
def test_manual_save_updates_button_text(layout):
    """测试手动保存更新按钮文本"""


def test_save_config_method_exists(layout):
    """测试 save_config 方法存在（现行：走 save_callback 保存整份配置）"""
    assert hasattr(layout, 'save_config')


@pytest.mark.skip(reason="退役：manual_save 已消亡，被 auto_bind 自动保存 + save_config "
                        "（save_callback 透传）替代（门禁A 2026-10-19）")
def test_manual_save_method_exists(layout):
    """测试 manual_save 方法存在"""


def test_auto_save_method_exists(layout):
    """测试自动保存机制存在（现行：config_auto_save.auto_bind/create_auto_save）"""
    from frontend.utils.config_auto_save import auto_bind, create_auto_save
    assert callable(auto_bind)
    auto_save = create_auto_save(layout.config["webui"])
    assert callable(auto_save)


def test_auto_save_updates_config(layout):
    """测试自动保存更新配置（现行：auto_bind 值变化回调即写配置字典）"""
    _bind_and_fire(
        MagicMock(),
        layout.config["webui"],
        ("config_items", "common_config", "items", "api_ip", "value"),
        "192.168.1.1"
    )
    # 验证配置已更新
    config_items = layout.config["webui"]["config_items"]["common_config"]["items"]
    assert config_items["api_ip"]["value"] == "192.168.1.1"
