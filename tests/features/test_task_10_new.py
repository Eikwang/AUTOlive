# -*- coding: UTF-8 -*-
"""
Task-10: 实现配置自动保存 测试
2026-10-19 门禁A 重写：对齐现行自动保存契约——
DrawerLayout.auto_save 集中式方法已消亡，自动保存迁移至
frontend/utils/config_auto_save.py 的 auto_bind/create_auto_save：
组件值变更即沿 keys 路径写入配置（缺失中间键自动创建），并可选回调 set_config_callback。
"""

import pytest

from frontend.utils.config_auto_save import auto_bind, create_auto_save


class _FakeEvent:
    """模拟 NiceGUI 值变更事件（仅需 .value）"""

    def __init__(self, value):
        self.value = value


class _FakeComponent:
    """模拟带 on_value_change 注册能力的 NiceGUI 组件"""

    def __init__(self):
        self._handlers = []

    def on_value_change(self, handler):
        self._handlers.append(handler)
        return self

    def trigger(self, value):
        for handler in self._handlers:
            handler(_FakeEvent(value))


class TestConfigAutoSave:
    """测试配置自动保存（现行契约：auto_bind 组件绑定 + keys 路径写配置）"""

    def test_config_auto_save_module_exports_auto_bind(self):
        """测试 config_auto_save 模块提供 auto_bind/create_auto_save"""
        # Then: 现行自动保存 API 存在（auto_save 已从 DrawerLayout 迁出）
        assert callable(auto_bind)
        assert callable(create_auto_save)

    def test_auto_save_updates_config(self):
        """测试 auto_bind 组件值变更沿 keys 路径更新配置"""
        # Given: 配置字典与绑定组件
        config = {"test_function": {"test_input": "old_value"}}
        component = auto_bind(_FakeComponent(), config, ("test_function", "test_input"))
        # When: 触发值变更
        component.trigger("new_value")
        # Then: 配置中的值更新
        assert config["test_function"]["test_input"] == "new_value"

    def test_auto_save_for_switch(self):
        """测试 auto_bind 保存布尔值（switch 场景）"""
        # Given: 配置字典与绑定组件
        config = {"test_function": {"test_switch": False}}
        component = auto_bind(_FakeComponent(), config, ("test_function", "test_switch"))
        # When: 触发布尔值变更
        component.trigger(True)
        # Then: 配置中的布尔值更新
        assert config["test_function"]["test_switch"] is True

    def test_auto_save_for_select(self):
        """测试 auto_bind 保存字符串值（select 场景）"""
        # Given: 配置字典与绑定组件
        config = {"test_function": {"test_select": "option1"}}
        component = auto_bind(_FakeComponent(), config, ("test_function", "test_select"))
        # When: 触发选项变更
        component.trigger("option2")
        # Then: 配置中的选项更新
        assert config["test_function"]["test_select"] == "option2"

    def test_auto_save_creates_missing_intermediate_keys(self):
        """测试 auto_bind 对缺失中间键自动创建路径（现行增强行为）"""
        # Given: 空 config_items 的配置
        config = {"webui": {"config_items": {}}}
        component = auto_bind(_FakeComponent(), config, ("webui", "config_items", "test_function", "test_input"))
        # When: 触发值变更
        component.trigger("new_value")
        # Then: 中间 dict 自动创建且值写入
        assert config["webui"]["config_items"]["test_function"]["test_input"] == "new_value"

    def test_auto_save_for_unknown_item(self):
        """测试 auto_bind 对缺失末级键直接写入"""
        # Given: 配置字典（末级键不存在）
        config = {"test_function": {}}
        component = auto_bind(_FakeComponent(), config, ("test_function", "unknown_item"))
        # When: 触发值变更
        component.trigger("new_value")
        # Then: 末级键直接创建并写入
        assert config["test_function"]["unknown_item"] == "new_value"

    def test_auto_save_calls_set_config_callback(self):
        """测试 auto_bind 触发 set_config_callback（替代旧 config_values 更新）"""
        # Given: 配置字典与回调记录
        config = {"test_function": {"test_input": "old_value"}}
        callback_calls = []

        def set_config_callback(*keys, value=None, **kwargs):
            callback_calls.append((keys, value))

        component = auto_bind(
            _FakeComponent(), config,
            ("test_function", "test_input"),
            set_config_callback=set_config_callback,
        )
        # When: 触发值变更
        component.trigger("new_value")
        # Then: 配置更新且回调收到键路径与值
        assert config["test_function"]["test_input"] == "new_value"
        assert callback_calls == [(("test_function", "test_input"), "new_value")]

    def test_create_auto_save_binds_config_and_tab_key(self):
        """测试 create_auto_save 工厂产出绑定了配置的 _auto_save 函数"""
        # Given: 配置字典与工厂产物
        config = {"test_function": {"test_input": "old_value"}}
        auto_save = create_auto_save(config, tab_key="test_function")
        # When: 用工厂函数绑定组件并触发
        component = auto_save(_FakeComponent(), ("test_function", "test_input"))
        component.trigger("new_value")
        # Then: 配置中的值更新
        assert config["test_function"]["test_input"] == "new_value"
