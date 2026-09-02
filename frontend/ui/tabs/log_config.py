# -*- coding: UTF-8 -*-
"""
日志标签页模块
从 common_config.py 拆分而来

布局规范：
- 设置项用最多 3 列网格排布，组件宽度跟随列宽（width:100%）
"""
from nicegui import ui
from typing import Dict, Any, Callable

from frontend.ui.components.config_helper import get_nested_value


def create_log_tab(
    config: Dict[str, Any],
    theme_config: Dict[str, str],
    set_config_callback: Callable
):
    """创建日志标签页"""
    card_css = theme_config.get("card", "")
    switch_internal_css = theme_config.get("switch_internal", "")

    # 导入自动绑定函数
    from frontend.utils.config_auto_save import create_auto_save
    _auto_save = create_auto_save(config, set_config_callback, tab_key="log")

    # 日志（与原文件 webui-bak.py L3636-3650 一致）
    if get_nested_value(config, "webui", "show_card", "common_config", "log"):
        with ui.card().style(card_css):
            ui.label('日志')
            with ui.grid(columns=3):
                with ui.column().style("width:100%;"):
                    _auto_save(ui.switch('启用', value=get_nested_value(config, "captions", "enable")).style(switch_internal_css), ("captions", "enable"))
                    _auto_save(ui.select(label='弹幕日志类型', options={'问答': '问答', '问题': '问题', '回答': '回答', '不记录': '不记录'}, value=get_nested_value(config, "comment_log_type")).style("width:100%;"), ("comment_log_type",))
                    _auto_save(ui.input(label='字幕日志路径', value=get_nested_value(config, "captions", "file_path"), placeholder='字幕日志存储路径').style("width:100%;"), ("captions", "file_path"))
                    _auto_save(ui.input(label='原文字幕日志路径', value=get_nested_value(config, "captions", "raw_file_path"), placeholder='原文字幕日志存储路径').style("width:100%;"), ("captions", "raw_file_path"))
