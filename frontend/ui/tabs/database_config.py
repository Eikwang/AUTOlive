# -*- coding: UTF-8 -*-
"""
数据库设置标签页模块
从 common_config.py 拆分而来
"""
from nicegui import ui
from typing import Dict, Any, Callable

from frontend.ui.components.config_helper import get_nested_value


def create_database_tab(
    config: Dict[str, Any],
    theme_config: Dict[str, str],
    set_config_callback: Callable
):
    """创建数据库设置标签页"""
    card_css = theme_config.get("card", "")
    switch_internal_css = theme_config.get("switch_internal", "")

    # 导入自动绑定函数
    from frontend.utils.config_auto_save import create_auto_save
    _auto_save = create_auto_save(config, set_config_callback, tab_key="database")

    # 数据库（与原文件 webui-bak.py L3652-3659 一致）
    if get_nested_value(config, "webui", "show_card", "common_config", "database"):
        with ui.card().style(card_css):
            ui.label('数据库')
            with ui.grid(columns=3):
                _auto_save(ui.switch('弹幕日志', value=get_nested_value(config, "database", "comment_enable")).style(switch_internal_css), ("database", "comment_enable"))
                _auto_save(ui.switch('入场日志', value=get_nested_value(config, "database", "entrance_enable")).style(switch_internal_css), ("database", "entrance_enable"))
                _auto_save(ui.switch('礼物日志', value=get_nested_value(config, "database", "gift_enable")).style(switch_internal_css), ("database", "gift_enable"))
                _auto_save(ui.input(label='数据库路径', value=get_nested_value(config, "database", "path"), placeholder='数据库文件存储路径').style("width:200px;"), ("database", "path"))
