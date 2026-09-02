# -*- coding: UTF-8 -*-
"""
限定时间段去重标签页模块
从 common_config.py 拆分而来

布局规范：
- 设置项用最多 3 列网格排布，组件宽度跟随列宽（width:100%）
- 开关+关联参数 为上下结构单元：开关在上，输入框在下
"""
from nicegui import ui
from typing import Dict, Any, Callable
from frontend.ui.components.config_helper import get_nested_value


def create_filter_dedup_tab(
    config: Dict[str, Any],
    theme_config: Dict[str, str],
    set_config_callback: Callable
):
    """创建限定时间段去重标签页"""
    card_css = theme_config.get("card", "")
    switch_internal_css = theme_config.get("switch_internal", "")

    # 导入自动绑定函数
    from frontend.utils.config_auto_save import create_auto_save
    _auto_save = create_auto_save(config, set_config_callback, tab_key="filter_dedup")

    if get_nested_value(config, "webui", "show_card", "common_config", "filter"):
        with ui.card().style(card_css):
            with ui.expansion('限定时间段内数据重复丢弃', icon="settings", value=True).classes('w-full'):
                with ui.grid(columns=3):
                    with ui.column().style("width:100%;"):
                        _auto_save(ui.switch('启用', value=get_nested_value(config, "filter", "limited_time_deduplication", "enable")).style(switch_internal_css), keys=("filter", "limited_time_deduplication", "enable"))
                        _auto_save(ui.input(label='弹幕检测周期', value=get_nested_value(config, "filter", "limited_time_deduplication", "comment"), placeholder='在这个周期时间（秒）内，重复的数据将被丢弃').style("width:100%;").tooltip('在这个周期时间（秒）内，重复的数据将被丢弃'), keys=("filter", "limited_time_deduplication", "comment"))
                    _auto_save(ui.input(label='礼物检测周期', value=get_nested_value(config, "filter", "limited_time_deduplication", "gift"), placeholder='在这个周期时间（秒）内，重复的数据将被丢弃').style("width:100%;").tooltip('在这个周期时间（秒）内，重复的数据将被丢弃'), keys=("filter", "limited_time_deduplication", "gift"))
                    _auto_save(ui.input(label='入场检测周期', value=get_nested_value(config, "filter", "limited_time_deduplication", "entrance"), placeholder='在这个周期时间（秒）内，重复的数据将被丢弃').style("width:100%;").tooltip('在这个周期时间（秒）内，重复的数据将被丢弃'), keys=("filter", "limited_time_deduplication", "entrance"))
