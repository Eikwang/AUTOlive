# -*- coding: UTF-8 -*-
"""
定时任务标签页模块
从 common_config.py 拆分而来

布局规范：
- 设置项用最多 3 列网格排布，组件宽度跟随列宽（width:100%）
- 开关+关联参数 为上下结构单元：开关在上，输入框在下
"""
from nicegui import ui
from typing import Dict, Any, Callable
from frontend.ui.components.config_helper import get_nested_value
from frontend.utils.common import textarea_data_change


def create_schedule_tab(
    config: Dict[str, Any],
    theme_config: Dict[str, str],
    set_config_callback: Callable
):
    """创建定时任务标签页"""
    card_css = theme_config.get("card", "")
    switch_internal_css = theme_config.get("switch_internal", "")

    # 导入自动绑定函数
    from frontend.utils.config_auto_save import create_auto_save
    _auto_save = create_auto_save(config, set_config_callback, tab_key="schedule")

    with ui.card().style(card_css):
        ui.label('定时任务')
        schedule_var = {}
        schedule_config_card = ui.card()
        for index, schedule in enumerate(config.get("schedule", [])):
            with schedule_config_card.style(card_css):
                with ui.grid(columns=3):
                    with ui.column().style("width:100%;"):
                        schedule_var[str(4 * index)] = ui.switch(text=f"启用任务#{index}", value=schedule.get("enable", False)).style(switch_internal_css)
                        schedule_var[str(4 * index + 1)] = ui.input(label=f"最小循环周期#{index}", value=schedule.get("time_min", 60), placeholder='定时任务循环的周期最小时长（秒）').style("width:100%;")
                        schedule_var[str(4 * index + 2)] = ui.input(label=f"最大循环周期#{index}", value=schedule.get("time_max", 120), placeholder='定时任务循环的周期最大时长（秒）').style("width:100%;")
                    schedule_var[str(4 * index + 3)] = ui.textarea(label=f"文案列表#{index}", value=textarea_data_change(schedule.get("copy", [])), placeholder='存放文案的列表，通过空格或换行分割').style("width:100%;")
