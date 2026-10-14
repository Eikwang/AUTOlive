# -*- coding: UTF-8 -*-
"""
Task-08: 创建配置项数据结构 测试
"""

import pytest


def test_webui_new_has_config_item_types():
    """测试 webui_new 包含配置项类型"""
    with open("webui_new.py", "r", encoding="utf-8") as f:
        content = f.read()
    
    has_input = '"type": "input"' in content
    has_switch = '"type": "switch"' in content
    has_select = '"type": "select"' in content
    has_textarea = '"type": "textarea"' in content
    
    assert has_input, "webui_new.py 缺少 input 类型配置"
    assert has_switch, "webui_new.py 缺少 switch 类型配置"
    assert has_select, "webui_new.py 缺少 select 类型配置"
    assert has_textarea, "webui_new.py 缺少 textarea 类型配置"


def test_webui_new_has_config_path():
    """测试 webui_new 包含配置路径"""
    with open("webui_new.py", "r", encoding="utf-8") as f:
        content = f.read()
    
    has_path = '"path":' in content
    
    assert has_path, "webui_new.py 缺少配置路径定义"
