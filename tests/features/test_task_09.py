# -*- coding: UTF-8 -*-
"""
Task-09: 实现配置页面显示 测试
"""

import pytest


@pytest.mark.skip(reason="退役：webui_new.py 已随模块化重构移除（现行入口 webui.py + frontend/ 包），源码文本断言对象消亡，功能由 test_task_*_new.py 验收（门禁A 2026-10-19）")
def test_webui_new_has_show_config_page():
    """测试 webui_new 包含显示配置页面函数"""
    with open("webui_new.py", "r", encoding="utf-8") as f:
        content = f.read()
    
    has_show_config = "def show_config_page" in content
    has_config_container = "config_page_container" in content
    
    assert has_show_config, "webui_new.py 缺少显示配置页面函数"
    assert has_config_container, "webui_new.py 缺少配置页面容器"


@pytest.mark.skip(reason="退役：webui_new.py 已随模块化重构移除（现行入口 webui.py + frontend/ 包），源码文本断言对象消亡，功能由 test_task_*_new.py 验收（门禁A 2026-10-19）")
def test_webui_new_has_config_rendering():
    """测试 webui_new 包含配置渲染逻辑"""
    with open("webui_new.py", "r", encoding="utf-8") as f:
        content = f.read()
    
    has_input_render = "ui.input" in content
    has_switch_render = "ui.switch" in content
    has_select_render = "ui.select" in content
    
    assert has_input_render, "webui_new.py 缺少 input 渲染"
    assert has_switch_render, "webui_new.py 缺少 switch 渲染"
    assert has_select_render, "webui_new.py 缺少 select 渲染"
