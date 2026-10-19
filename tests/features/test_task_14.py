# -*- coding: UTF-8 -*-
"""
Task-14: 实现系统运行/停止 测试
"""

import pytest


@pytest.mark.skip(reason="退役：webui_new.py 已随模块化重构移除（现行入口 webui.py + frontend/ 包），源码文本断言对象消亡，功能由 test_task_*_new.py 验收（门禁A 2026-10-19）")
def test_webui_new_has_toggle_system():
    """测试 webui_new 包含系统运行/停止功能"""
    with open("webui_new.py", "r", encoding="utf-8") as f:
        content = f.read()
    
    has_toggle_system = "def toggle_system" in content
    has_running_flag = "running_flag" in content
    
    assert has_toggle_system, "webui_new.py 缺少系统运行/停止函数"
    assert has_running_flag, "webui_new.py 缺少运行状态标志"


@pytest.mark.skip(reason="退役：webui_new.py 已随模块化重构移除（现行入口 webui.py + frontend/ 包），源码文本断言对象消亡，功能由 test_task_*_new.py 验收（门禁A 2026-10-19）")
def test_webui_new_has_system_state_change():
    """测试 webui_new 包含系统状态变化"""
    with open("webui_new.py", "r", encoding="utf-8") as f:
        content = f.read()
    
    has_icon_change = "icon=play_arrow" in content and "icon=stop" in content
    has_text_change = "运行系统" in content and "停止运行" in content
    
    assert has_icon_change, "webui_new.py 缺少图标变化"
    assert has_text_change, "webui_new.py 缺少文本变化"
