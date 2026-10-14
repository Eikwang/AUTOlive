# -*- coding: UTF-8 -*-
"""
Task-12: 实现搜索结果高亮和跳转 测试
"""

import pytest


def test_webui_new_has_search_highlight():
    """测试 webui_new 包含搜索结果高亮"""
    with open("webui_new.py", "r", encoding="utf-8") as f:
        content = f.read()
    
    has_highlight = "search-highlight" in content
    has_display_results = "def display_search_results" in content
    
    assert has_highlight, "webui_new.py 缺少搜索结果高亮"
    assert has_display_results, "webui_new.py 缺少显示搜索结果函数"


def test_webui_new_has_no_results_message():
    """测试 webui_new 包含无结果提示"""
    with open("webui_new.py", "r", encoding="utf-8") as f:
        content = f.read()
    
    has_no_results = "未找到匹配项" in content
    
    assert has_no_results, "webui_new.py 缺少无结果提示"
