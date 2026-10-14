# -*- coding: UTF-8 -*-
"""
Task-08: 创建配置项数据结构 测试
TDD RED 阶段：先写测试，确认失败
"""

import pytest
import json
from pathlib import Path


class TestConfigItemDataStructure:
    """测试配置项数据结构"""

    def test_config_has_config_items_section(self):
        """测试配置文件包含配置项部分"""
        # Given: 配置文件
        # When: 读取配置
        # Then: 包含 config_items 配置
        with open("config.json", "r", encoding="utf-8") as f:
            config = json.load(f)
        assert "webui" in config
        assert "config_items" in config["webui"]

    def test_config_items_has_common_config(self):
        """测试配置项包含通用配置"""
        # Given: 配置文件
        # When: 读取配置项
        # Then: 包含通用配置
        with open("config.json", "r", encoding="utf-8") as f:
            config = json.load(f)
        config_items = config["webui"]["config_items"]
        assert "common_config" in config_items

    def test_common_config_has_required_fields(self):
        """测试通用配置有必需字段"""
        # Given: 配置文件
        # When: 读取通用配置
        # Then: 包含 label, type, items 字段
        with open("config.json", "r", encoding="utf-8") as f:
            config = json.load(f)
        common_config = config["webui"]["config_items"]["common_config"]
        assert "label" in common_config
        assert "type" in common_config
        assert "items" in common_config

    def test_config_items_has_multiple_types(self):
        """测试配置项支持多种类型"""
        # Given: 配置文件
        # When: 读取配置项
        # Then: 支持 input, switch, select, textarea 类型
        with open("config.json", "r", encoding="utf-8") as f:
            config = json.load(f)
        config_items = config["webui"]["config_items"]
        types = set()
        for item in config_items.values():
            if isinstance(item, dict) and "type" in item:
                types.add(item["type"])
            # 检查 items 中的类型
            if isinstance(item, dict) and "items" in item:
                for sub_item in item["items"].values():
                    if isinstance(sub_item, dict) and "type" in sub_item:
                        types.add(sub_item["type"])
        assert "input" in types or "switch" in types or "select" in types or "textarea" in types

    def test_config_helper_has_get_config_items_function(self):
        """测试 config_helper 有 get_config_items 函数"""
        # Given: frontend.ui.components.config_helper 模块
        # When: 检查函数
        # Then: get_config_items 函数存在
        from frontend.ui.components.config_helper import get_config_items
        assert get_config_items is not None

    def test_get_config_items_returns_dict(self):
        """测试 get_config_items 返回字典"""
        # Given: 配置实例
        # When: 调用 get_config_items
        # Then: 返回字典
        from frontend.config.settings import init_config
        from frontend.ui.components.config_helper import get_config_items
        config = init_config("config.json")
        items = get_config_items("common_config")
        assert isinstance(items, dict)

    def test_get_config_items_returns_empty_for_unknown(self):
        """测试 get_config_items 对未知功能返回空字典"""
        # Given: 配置实例
        # When: 调用 get_config_items
        # Then: 返回空字典
        from frontend.config.settings import init_config
        from frontend.ui.components.config_helper import get_config_items
        config = init_config("config.json")
        items = get_config_items("unknown_function")
        assert items == {}

    def test_config_helper_has_get_nested_value_function(self):
        """测试 config_helper 有 get_nested_value 函数"""
        # Given: frontend.ui.components.config_helper 模块
        # When: 检查函数
        # Then: get_nested_value 函数存在
        from frontend.ui.components.config_helper import get_nested_value
        assert get_nested_value is not None

    def test_get_nested_value_returns_correct_value(self):
        """测试 get_nested_value 返回正确的值"""
        # Given: 嵌套字典
        data = {"a": {"b": {"c": "value"}}}
        # When: 调用 get_nested_value
        # Then: 返回正确的值
        from frontend.ui.components.config_helper import get_nested_value
        result = get_nested_value(data, "a", "b", "c")
        assert result == "value"

    def test_get_nested_value_returns_default_for_missing(self):
        """测试 get_nested_value 对缺失的键返回默认值"""
        # Given: 嵌套字典
        data = {"a": {"b": {"c": "value"}}}
        # When: 调用 get_nested_value
        # Then: 返回默认值
        from frontend.ui.components.config_helper import get_nested_value
        result = get_nested_value(data, "a", "b", "d", default="default")
        assert result == "default"
