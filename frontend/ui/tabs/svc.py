# -*- coding: UTF-8 -*-
"""
变声标签页模块
从 webui-bak.py 的 svc_page 拆分而来

布局规范：
- 设置项用最多 3 列网格排布，组件宽度跟随列宽（width:100%）
- 开关+关联参数 为上下结构单元：开关在上，输入框在下
"""
from frontend.ui.components.config_helper import get_nested_value
from nicegui import ui
from typing import Dict, Any, Callable

from frontend.ui.components import FormField
from frontend.utils.common import is_url_check


def create_svc_tab(
    config: Dict[str, Any],
    theme_config: Dict[str, str],
    set_config_callback: Callable
):
    """
    创建变声标签页

    Args:
        config: 配置字典
        theme_config: 主题配置
        set_config_callback: 配置设置回调函数
    """
    card_css = theme_config.get("card", "")
    switch_internal_css = theme_config.get("switch_internal", "")

    # SO-VITS-SVC配置
    if get_nested_value(config, "webui", "show_card", "svc", "so_vits_svc"):
        with ui.card().style(card_css):
            ui.label("SO-VITS-SVC")
            with ui.grid(columns=3):
                with ui.column().style("width:100%;"):
                    ui.switch(
                        '启用',
                        value=get_nested_value(config, "so_vits_svc", "enable"),
                        on_change=lambda e: set_config_callback("so_vits_svc", "enable", e.value)
                    ).style(switch_internal_css)
                    FormField.create_input(
                        label='配置文件路径',
                        value=get_nested_value(config, "so_vits_svc", "config_path"),
                        placeholder='模型配置文件config.json的路径',
                        on_change=lambda e: set_config_callback("so_vits_svc", "config_path", e.value),
                        style="width:100%"
                    )
                FormField.create_input(
                    label='API地址',
                    value=get_nested_value(config, "so_vits_svc", "api_ip_port"),
                    placeholder='flask_api_full_song服务运行的ip端口，例如：http://127.0.0.1:1145',
                    on_change=lambda e: set_config_callback("so_vits_svc", "api_ip_port", e.value),
                    style="width:100%"
                )
                FormField.create_input(
                    label='说话人',
                    value=get_nested_value(config, "so_vits_svc", "spk"),
                    placeholder='说话人，需要和配置文件内容对应',
                    on_change=lambda e: set_config_callback("so_vits_svc", "spk", e.value),
                    style="width:100%"
                )
                FormField.create_input(
                    label='音调',
                    value=get_nested_value(config, "so_vits_svc", "tran"),
                    placeholder='音调设置，默认为1',
                    on_change=lambda e: set_config_callback("so_vits_svc", "tran", e.value),
                    style="width:100%"
                )
                FormField.create_input(
                    label='输出音频格式',
                    value=get_nested_value(config, "so_vits_svc", "wav_format"),
                    placeholder='音频合成后输出的格式',
                    on_change=lambda e: set_config_callback("so_vits_svc", "wav_format", e.value),
                    style="width:100%"
                )
