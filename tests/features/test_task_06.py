# -*- coding: UTF-8 -*-
"""
Task-06: 实现功能列表显示 测试
"""

import pytest


def test_webui_new_has_show_group_function():
    """测试 webui_new 包含显示功能分组函数"""
    with open("webui_new.py", "r", encoding="utf-8") as f:
        content = f.read()
    
    has_show_group = "def show_group" in content
    has_function_list = "function_list_container" in content
    
    assert has_show_group, "webui_new.py 缺少显示功能分组函数"
    assert has_function_list, "webui_new.py 缺少功能列表容器"


def test_webui_new_has_navigation_buttons():
    """测试 webui_new 包含导航按钮"""
    with open("webui_new.py", "r", encoding="utf-8") as f:
        content = f.read()
    
    has_navigation = "for group in config_groups" in content
    has_button = "ui.button" in content
    
    assert has_navigation, "webui_new.py 缺少导航按钮循环"
    assert has_button, "webui_new.py 缺少按钮组件"
