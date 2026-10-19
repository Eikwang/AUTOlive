# -*- coding: UTF-8 -*-
"""
Task-04: 实现主题持久化 测试
"""

import pytest


@pytest.mark.skip(reason="退役：webui_new.py 已随模块化重构移除（现行入口 webui.py + frontend/ 包），源码文本断言对象消亡，功能由 test_task_*_new.py 验收（门禁A 2026-10-19）")
def test_webui_new_has_theme_persistence():
    """测试 webui_new 包含主题持久化功能"""
    with open("webui_new.py", "r", encoding="utf-8") as f:
        content = f.read()
    
    has_local_storage = "localStorage" in content
    has_theme_save = "setItem" in content and "theme" in content
    
    assert has_local_storage, "webui_new.py 缺少 localStorage 使用"
    assert has_theme_save, "webui_new.py 缺少主题保存功能"
