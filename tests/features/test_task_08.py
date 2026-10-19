# -*- coding: UTF-8 -*-
"""
Task-08: 创建配置项数据结构 测试
"""

import pytest


@pytest.mark.skip(reason="退役：webui_new.py 已随模块化重构移除（现行入口 webui.py + frontend/ 包），源码文本断言对象消亡，功能由 test_task_*_new.py 验收（门禁A 2026-10-19）")
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


@pytest.mark.skip(reason="退役：webui_new.py 已随模块化重构移除（现行入口 webui.py + frontend/ 包），源码文本断言对象消亡，功能由 test_task_*_new.py 验收（门禁A 2026-10-19）")
def test_webui_new_has_config_path():
    """测试 webui_new 包含配置路径"""
    with open("webui_new.py", "r", encoding="utf-8") as f:
        content = f.read()
    
    has_path = '"path":' in content
    
    assert has_path, "webui_new.py 缺少配置路径定义"
