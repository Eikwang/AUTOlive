# -*- coding: UTF-8 -*-
"""
虚拟身体标签页模块
从 webui-bak.py 的 visual_body_page 拆分而来

布局规范：
- 设置项用最多 3 列网格排布，组件宽度跟随列宽（width:100%）
"""
from frontend.ui.components.config_helper import get_nested_value
from nicegui import ui
from typing import Dict, Any, Callable

from frontend.ui.components import FormField
from frontend.utils.common import is_url_check


def create_visual_body_tab(
    config: Dict[str, Any],
    theme_config: Dict[str, str],
    set_config_callback: Callable
):
    """
    创建虚拟身体标签页

    Args:
        config: 配置字典
        theme_config: 主题配置
        set_config_callback: 配置设置回调函数
    """
    card_css = theme_config.get("card", "")

    # metahuman_stream配置
    with ui.card().style(card_css):
        ui.label("metahuman_stream")
        with ui.grid(columns=3):
            FormField.create_select(
                label='类型',
                options={'ernerf': 'ernerf', 'musetalk': 'musetalk', 'wav2lip': 'wav2lip'},
                value=get_nested_value(config, "metahuman_stream", "type"),
                on_change=lambda e: set_config_callback("metahuman_stream", "type", e.value),
                style="width:100%;"
            )
            FormField.create_input(
                label='API地址',
                value=get_nested_value(config, "metahuman_stream", "api_ip_port"),
                placeholder='metahuman_stream应用启动API后，监听的ip和端口',
                on_change=lambda e: set_config_callback("metahuman_stream", "api_ip_port", e.value),
                style="width:100%;"
            )
