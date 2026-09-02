"""
字典操作模块 - 提供字典相关操作功能
"""


class DictMixin:
    """字典操作 Mixin"""

    def find_keys_by_value(self, dictionary, target_value):
        # 返回一个包含所有具有指定值的键的列表
        return [key for key, value in dictionary.items() if value == target_value]

    def find_key_by_value(self, d: dict, value):
        # 遍历字典项
        for key, val in d.items():
            if val == value:
                return key
        return None
