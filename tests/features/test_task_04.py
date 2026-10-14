# -*- coding: UTF-8 -*-
"""
Task-04: 实现主题持久化 测试
"""

import pytest


def test_webui_new_has_theme_persistence():
    """测试 webui_new 包含主题持久化功能"""
    with open("webui_new.py", "r", encoding="utf-8") as f:
        content = f.read()
    
    has_local_storage = "localStorage" in content
    has_theme_save = "setItem" in content and "theme" in content
    
    assert has_local_storage, "webui_new.py 缺少 localStorage 使用"
    assert has_theme_save, "webui_new.py 缺少主题保存功能"
