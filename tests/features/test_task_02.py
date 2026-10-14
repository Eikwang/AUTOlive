# -*- coding: UTF-8 -*-
"""
Task-02: 实现导航栏折叠 测试
"""

import pytest
import json
from pathlib import Path


def test_webui_new_has_drawer_toggle():
    """测试 webui_new 包含导航栏折叠功能"""
    # Given: webui_new.py 存在
    # When: 读取文件内容
    # Then: 包含导航栏折叠相关代码
    with open("webui_new.py", "r", encoding="utf-8") as f:
        content = f.read()
    
    # 检查是否包含导航栏折叠相关代码
    has_toggle_drawer = "toggle_drawer" in content
    has_drawer_mini = "drawer_mini" in content
    
    assert has_toggle_drawer, "webui_new.py 缺少导航栏折叠功能"
    assert has_drawer_mini, "webui_new.py 缺少导航栏状态变量"


def test_webui_new_has_drawer_state_persistence():
    """测试 webui_new 包含导航栏状态持久化"""
    # Given: webui_new.py 存在
    # When: 读取文件内容
    # Then: 包含状态持久化相关代码
    with open("webui_new.py", "r", encoding="utf-8") as f:
        content = f.read()
    
    # 检查是否包含状态持久化相关代码
    has_save_state = "config.set" in content and "drawer_mini" in content
    
    assert has_save_state, "webui_new.py 缺少导航栏状态持久化功能"


def test_webui_new_has_drawer_collapse_button():
    """测试 webui_new 包含导航栏折叠按钮"""
    # Given: webui_new.py 存在
    # When: 读取文件内容
    # Then: 包含折叠按钮相关代码
    with open("webui_new.py", "r", encoding="utf-8") as f:
        content = f.read()
    
    # 检查是否包含折叠按钮相关代码
    has_collapse_button = "折叠导航栏" in content
    
    assert has_collapse_button, "webui_new.py 缺少导航栏折叠按钮"


def test_webui_new_has_drawer_width_change():
    """测试 webui_new 包含导航栏宽度变化"""
    # Given: webui_new.py 存在
    # When: 读取文件内容
    # Then: 包含宽度变化相关代码
    with open("webui_new.py", "r", encoding="utf-8") as f:
        content = f.read()
    
    # 检查是否包含宽度变化相关代码
    has_width_change = "mini" in content and "props" in content
    
    assert has_width_change, "webui_new.py 缺少导航栏宽度变化功能"
