# -*- coding: UTF-8 -*-
"""
Task-13: 实现系统状态显示 测试
"""

import pytest


@pytest.mark.skip(reason="退役：webui_new.py 已随模块化重构移除（现行入口 webui.py + frontend/ 包），源码文本断言对象消亡，功能由 test_task_*_new.py 验收（门禁A 2026-10-19）")
def test_webui_new_has_system_button():
    """测试 webui_new 包含系统状态按钮"""
    with open("webui_new.py", "r", encoding="utf-8") as f:
        content = f.read()
    
    has_system_button = "system_button" in content
    has_run_text = "运行系统" in content
    has_stop_text = "停止运行" in content
    
    assert has_system_button, "webui_new.py 缺少系统状态按钮"
    assert has_run_text, "webui_new.py 缺少运行系统文本"
    assert has_stop_text, "webui_new.py 缺少停止运行文本"


@pytest.mark.skip(reason="退役：webui_new.py 已随模块化重构移除（现行入口 webui.py + frontend/ 包），源码文本断言对象消亡，功能由 test_task_*_new.py 验收（门禁A 2026-10-19）")
def test_webui_new_has_system_status_colors():
    """测试 webui_new 包含系统状态颜色"""
    with open("webui_new.py", "r", encoding="utf-8") as f:
        content = f.read()
    
    has_green = "#4caf50" in content
    has_red = "#f44336" in content
    
    assert has_green, "webui_new.py 缺少绿色状态"
    assert has_red, "webui_new.py 缺少红色状态"
