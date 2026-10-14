# -*- coding: UTF-8 -*-
"""
Task-01: 创建主布局结构 测试
"""

import pytest
import json
from pathlib import Path


def test_config_file_exists():
    """测试配置文件存在"""
    # Given: 项目根目录
    # When: 检查配置文件
    # Then: config.json 存在
    config_path = Path("config.json")
    assert config_path.exists(), "config.json 文件不存在"


def test_config_has_webui_section():
    """测试配置文件包含 webui 配置"""
    # Given: 配置文件存在
    # When: 读取配置
    # Then: 包含 webui 配置节
    with open("config.json", "r", encoding="utf-8") as f:
        config = json.load(f)
    
    assert "webui" in config, "配置文件缺少 webui 配置节"


def test_webui_new_file_exists():
    """测试 webui_new.py 文件存在"""
    # Given: 项目根目录
    # When: 检查文件
    # Then: webui_new.py 存在
    file_path = Path("webui_new.py")
    assert file_path.exists(), "webui_new.py 文件不存在"


def test_webui_new_has_drawer_components():
    """测试 webui_new 包含导航栏组件"""
    # Given: webui_new.py 存在
    # When: 读取文件内容
    # Then: 包含 drawer 相关代码
    with open("webui_new.py", "r", encoding="utf-8") as f:
        content = f.read()
    
    # 检查是否包含导航栏相关代码
    has_drawer = "ui.left_drawer" in content or "ui.drawer" in content
    has_splitter = "ui.splitter" in content
    
    assert has_drawer, "webui_new.py 缺少导航栏组件"
    assert has_splitter, "webui_new.py 缺少分割布局组件"


def test_webui_new_has_card_components():
    """测试 webui_new 包含卡片组件"""
    # Given: webui_new.py 存在
    # When: 读取文件内容
    # Then: 包含 card 相关代码
    with open("webui_new.py", "r", encoding="utf-8") as f:
        content = f.read()
    
    # 检查是否包含卡片相关代码
    has_card = "ui.card" in content
    
    assert has_card, "webui_new.py 缺少卡片组件"


def test_webui_new_has_theme_toggle():
    """测试 webui_new 包含主题切换功能"""
    # Given: webui_new.py 存在
    # When: 读取文件内容
    # Then: 包含主题切换相关代码
    with open("webui_new.py", "r", encoding="utf-8") as f:
        content = f.read()
    
    # 检查是否包含主题切换相关代码
    has_theme_toggle = "toggle_theme" in content
    has_dark_mode = "ui.dark_mode" in content
    
    assert has_theme_toggle, "webui_new.py 缺少主题切换功能"
    assert has_dark_mode, "webui_new.py 缺少暗色模式支持"


def test_webui_new_has_search_function():
    """测试 webui_new 包含搜索功能"""
    # Given: webui_new.py 存在
    # When: 读取文件内容
    # Then: 包含搜索相关代码
    with open("webui_new.py", "r", encoding="utf-8") as f:
        content = f.read()
    
    # 检查是否包含搜索相关代码
    has_search = "search_configs" in content
    has_search_input = "搜索配置" in content
    
    assert has_search, "webui_new.py 缺少搜索功能"
    assert has_search_input, "webui_new.py 缺少搜索输入框"


def test_webui_new_has_system_control():
    """测试 webui_new 包含系统控制功能"""
    # Given: webui_new.py 存在
    # When: 读取文件内容
    # Then: 包含系统控制相关代码
    with open("webui_new.py", "r", encoding="utf-8") as f:
        content = f.read()
    
    # 检查是否包含系统控制相关代码
    has_system_control = "toggle_system" in content
    has_run_button = "运行系统" in content or "停止运行" in content
    
    assert has_system_control, "webui_new.py 缺少系统控制功能"
    assert has_run_button, "webui_new.py 缺少运行/停止按钮"


def test_webui_new_has_config_groups():
    """测试 webui_new 包含功能分组"""
    # Given: webui_new.py 存在
    # When: 读取文件内容
    # Then: 包含功能分组相关代码
    with open("webui_new.py", "r", encoding="utf-8") as f:
        content = f.read()
    
    # 检查是否包含功能分组相关代码
    has_config_groups = "config_groups" in content
    has_basic_settings = "基础设置" in content
    has_live_interaction = "直播互动" in content
    
    assert has_config_groups, "webui_new.py 缺少功能分组定义"
    assert has_basic_settings, "webui_new.py 缺少基础设置分组"
    assert has_live_interaction, "webui_new.py 缺少直播互动分组"
