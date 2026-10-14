# -*- coding: UTF-8 -*-
"""
Task-10: 实现配置保存 测试
"""

import pytest


def test_webui_new_has_auto_save():
    """测试 webui_new 包含自动保存功能"""
    with open("webui_new.py", "r", encoding="utf-8") as f:
        content = f.read()
    
    has_auto_save = "def auto_save" in content
    has_config_set = "config.set" in content
    
    assert has_auto_save, "webui_new.py 缺少自动保存函数"
    assert has_config_set, "webui_new.py 缺少配置保存功能"


def test_webui_new_has_textarea_save():
    """测试 webui_new 包含文案保存功能"""
    with open("webui_new.py", "r", encoding="utf-8") as f:
        content = f.read()
    
    has_textarea_save = "def save_textarea" in content
    has_save_button = "保存" in content
    
    assert has_textarea_save, "webui_new.py 缺少文案保存函数"
    assert has_save_button, "webui_new.py 缺少保存按钮"


def test_webui_new_has_save_notification():
    """测试 webui_new 包含保存通知"""
    with open("webui_new.py", "r", encoding="utf-8") as f:
        content = f.read()
    
    has_notify = "ui.notify" in content
    has_saved = "已保存" in content
    
    assert has_notify, "webui_new.py 缺少通知功能"
    assert has_saved, "webui_new.py 缺少保存成功提示"
