# -*- coding: UTF-8 -*-
"""
配置项注册表
用于全局搜索配置参数
"""
from typing import Dict, List, Any, Optional


class ConfigRegistry:
    """配置项注册表 - 用于全局搜索"""

    _instance = None
    _items: List[Dict[str, Any]] = []

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self):
        self._items = []

    def register(self, tab_key: str, label: str, tooltip: str = "", placeholder: str = "", config_keys: tuple = ()):
        """
        注册一个配置项

        Args:
            tab_key: 所属标签页的tab_key
            label: 配置项标签文本
            tooltip: 提示信息
            placeholder: 占位符文本
            config_keys: 配置键路径
        """
        self._items.append({
            "tab_key": tab_key,
            "label": label,
            "tooltip": tooltip,
            "placeholder": placeholder,
            "config_keys": config_keys,
        })

    def search(self, keyword: str) -> List[Dict[str, Any]]:
        """
        搜索配置项

        Args:
            keyword: 搜索关键词

        Returns:
            匹配的配置项列表
        """
        if not keyword:
            return []

        keyword_lower = keyword.lower()
        results = []

        for item in self._items:
            # 搜索 label、tooltip、placeholder
            searchable = f"{item['label']} {item['tooltip']} {item['placeholder']}".lower()
            if keyword_lower in searchable:
                results.append(item)

        return results

    def clear(self):
        """清空注册表"""
        self._items.clear()

    def get_all(self) -> List[Dict[str, Any]]:
        """获取所有注册的配置项"""
        return self._items.copy()


def get_config_registry() -> ConfigRegistry:
    """获取配置项注册表实例"""
    return ConfigRegistry.get_instance()
