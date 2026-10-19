# -*- coding: UTF-8 -*-
"""
Task-11: 实现搜索功能 测试
"""

import pytest


@pytest.mark.skip(reason="退役：webui_new.py 已随模块化重构移除（现行入口 webui.py + frontend/ 包），源码文本断言对象消亡，功能由 test_task_*_new.py 验收（门禁A 2026-10-19）")
def test_webui_new_has_search_function():
    """测试 webui_new 包含搜索功能"""
    with open("webui_new.py", "r", encoding="utf-8") as f:
        content = f.read()
    
    has_search = "def search_configs" in content
    has_search_input = "搜索配置" in content
    has_search_results = "search_results_container" in content
    
    assert has_search, "webui_new.py 缺少搜索函数"
    assert has_search_input, "webui_new.py 缺少搜索输入框"
    assert has_search_results, "webui_new.py 缺少搜索结果容器"


@pytest.mark.skip(reason="退役：webui_new.py 已随模块化重构移除（现行入口 webui.py + frontend/ 包），源码文本断言对象消亡，功能由 test_task_*_new.py 验收（门禁A 2026-10-19）")
def test_webui_new_has_realtime_search():
    """测试 webui_new 包含实时搜索"""
    with open("webui_new.py", "r", encoding="utf-8") as f:
        content = f.read()
    
    has_on_change = "on_change" in content and "search_configs" in content
    
    assert has_on_change, "webui_new.py 缺少实时搜索功能"
