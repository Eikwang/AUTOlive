# -*- coding: UTF-8 -*-
"""
自定义命令标签页模块
从 common_config.py 拆分而来

布局规范：
- 设置项用最多 3 列网格排布，组件宽度跟随列宽（width:100%）
- 开关+关联参数 为上下结构单元：开关在上，输入框在下
"""
from nicegui import ui
from typing import Dict, Any, Callable
from frontend.ui.components.config_helper import get_nested_value
from frontend.utils.common import textarea_data_change


def create_custom_cmd_tab(
    config: Dict[str, Any],
    theme_config: Dict[str, str],
    set_config_callback: Callable
):
    """创建自定义命令标签页"""
    card_css = theme_config.get("card", "")
    switch_internal_css = theme_config.get("switch_internal", "")

    # 导入自动绑定函数
    from frontend.utils.config_auto_save import create_auto_save
    _auto_save = create_auto_save(config, set_config_callback, tab_key="custom_cmd")

    with ui.card().style(card_css):
        ui.label('自定义命令')
        with ui.grid(columns=3):
            with ui.column().style("width:100%;"):
                _auto_save(ui.select(label='类型', options={'弹幕': '弹幕'}, value=get_nested_value(config, "custom_cmd", "type")).style("width:100%;"), ("custom_cmd", "type"))
        custom_cmd_config_var = {}
        custom_cmd_config_card = ui.card()
        for index, custom_cmd_config in enumerate(config.get("custom_cmd", {}).get("config", [])):
            with custom_cmd_config_card.style(card_css):
                with ui.grid(columns=3):
                    custom_cmd_config_var[str(7 * index)] = ui.textarea(label=f"关键词#{index + 1}", value=textarea_data_change(custom_cmd_config.get("keywords", [])), placeholder='触发的关键词').style("width:100%;")
                    custom_cmd_config_var[str(7 * index + 1)] = ui.input(label=f"相似度#{index + 1}", value=custom_cmd_config.get("similarity", 1), placeholder='相似度').style("width:100%;")
                    custom_cmd_config_var[str(7 * index + 2)] = ui.textarea(label=f"API URL#{index + 1}", value=custom_cmd_config.get("api_url", ""), placeholder='API链接').style("width:100%;")
                    custom_cmd_config_var[str(7 * index + 3)] = ui.select(label=f"API类型#{index + 1}", value=custom_cmd_config.get("api_type", "GET"), options={"GET": "GET"}).style("width:100%;")
                    custom_cmd_config_var[str(7 * index + 4)] = ui.select(label=f"返回数据类型#{index + 1}", value=custom_cmd_config.get("resp_data_type", "json"), options={"json": "json", "content": "content"}).style("width:100%;")
                    custom_cmd_config_var[str(7 * index + 5)] = ui.textarea(label=f"数据解析#{index + 1}", value=custom_cmd_config.get("data_analysis", ""), placeholder='数据解析').style("width:100%;")
                    custom_cmd_config_var[str(7 * index + 6)] = ui.textarea(label=f"返回内容模板#{index + 1}", value=custom_cmd_config.get("resp_template", ""), placeholder='返回内容模板').style("width:100%;")
