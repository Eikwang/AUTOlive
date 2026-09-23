# -*- coding: UTF-8 -*-
"""
web字幕打印机标签页模块
从 common_config.py 拆分而来

布局规范：
- 设置项用最多 3 列网格排布，组件宽度跟随列宽（width:100%）
- 开关+关联参数 为上下结构单元：开关在上，输入框在下
"""
from nicegui import ui
from typing import Dict, Any, Callable

from frontend.ui.components.config_helper import get_nested_value


def create_web_captions_printer_tab(
    config: Dict[str, Any],
    theme_config: Dict[str, str],
    set_config_callback: Callable
):
    """创建web字幕打印机标签页"""
    card_css = theme_config.get("card", "")
    switch_internal_css = theme_config.get("switch_internal", "")

    # 导入自动绑定函数
    from frontend.utils.config_auto_save import create_auto_save
    _auto_save = create_auto_save(config, set_config_callback, tab_key="web_captions_printer")

    # web字幕打印机（与原文件 webui-bak.py L3620-3632 一致）
    with ui.card().style(card_css):
        ui.label('web字幕打印机')
        with ui.grid(columns=3):
            with ui.column().style("width:100%;"):
                _auto_save(ui.input(label='API地址', value=get_nested_value(config, "web_captions_printer", "api_ip_port"), placeholder='web字幕打印机的API地址').style("width:100%;"), ("web_captions_printer", "api_ip_port"))
