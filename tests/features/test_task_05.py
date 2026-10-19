# -*- coding: UTF-8 -*-
"""
Task-05: 创建功能分组数据结构 测试
"""

import pytest


@pytest.mark.skip(reason="退役：webui_new.py 已随模块化重构移除（现行入口 webui.py + frontend/ 包），源码文本断言对象消亡，功能由 test_task_*_new.py 验收（门禁A 2026-10-19）")
def test_webui_new_has_config_groups():
    """测试 webui_new 包含功能分组数据结构"""
    with open("webui_new.py", "r", encoding="utf-8") as f:
        content = f.read()
    
    has_config_groups = "config_groups" in content
    has_basic_settings = "基础设置" in content
    has_live_interaction = "直播互动" in content
    has_ai_dialog = "AI 对话" in content or "AI对话" in content
    has_audio_settings = "音频设置" in content
    has_advanced = "高级功能" in content
    
    assert has_config_groups, "webui_new.py 缺少功能分组定义"
    assert has_basic_settings, "webui_new.py 缺少基础设置分组"
    assert has_live_interaction, "webui_new.py 缺少直播互动分组"
    assert has_ai_dialog, "webui_new.py 缺少 AI 对话分组"
    assert has_audio_settings, "webui_new.py 缺少音频设置分组"
    assert has_advanced, "webui_new.py 缺少高级功能分组"


@pytest.mark.skip(reason="退役：webui_new.py 已随模块化重构移除（现行入口 webui.py + frontend/ 包），源码文本断言对象消亡，功能由 test_task_*_new.py 验收（门禁A 2026-10-19）")
def test_webui_new_has_function_items():
    """测试 webui_new 包含功能项数据结构"""
    with open("webui_new.py", "r", encoding="utf-8") as f:
        content = f.read()
    
    has_functions = "functions" in content
    has_enabled = "enabled" in content
    has_description = "description" in content
    has_configs = "configs" in content
    
    assert has_functions, "webui_new.py 缺少功能列表"
    assert has_enabled, "webui_new.py 缺少启用状态"
    assert has_description, "webui_new.py 缺少功能描述"
    assert has_configs, "webui_new.py 缺少配置项"
