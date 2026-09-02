# -*- coding: UTF-8 -*-
"""
答谢标签页模块
从 common_config.py 拆分而来

布局规范：
- 设置项用最多 3 列网格排布，组件宽度跟随列宽（width:100%）
- 开关+关联参数 为上下结构单元：开关在上，输入框在下
"""
from nicegui import ui
from typing import Dict, Any, Callable
from frontend.ui.components.config_helper import get_nested_value
from frontend.utils.common import textarea_data_change


def create_thanks_tab(
    config: Dict[str, Any],
    theme_config: Dict[str, str],
    set_config_callback: Callable
):
    """创建答谢标签页"""
    card_css = theme_config.get("card", "")
    switch_internal_css = theme_config.get("switch_internal", "")

    # 导入自动绑定函数
    from frontend.utils.config_auto_save import create_auto_save
    _auto_save = create_auto_save(config, set_config_callback, tab_key="thanks")

    with ui.card().style(card_css):
        ui.label('答谢')
        with ui.grid(columns=3):
            _auto_save(ui.input(label='用户名最大长度', value=get_nested_value(config, "thanks", "username_max_len"), placeholder='需要保留的用户名的最大长度，超出部分将被丢弃').style("width:100%;"), ("thanks", "username_max_len"))
        with ui.expansion('入场设置', icon="settings", value=True).classes('w-full'):
            with ui.grid(columns=3):
                with ui.column().style("width:100%;"):
                    _auto_save(ui.switch('启用入场欢迎', value=get_nested_value(config, "thanks", "entrance_enable")).style(switch_internal_css), ("thanks", "entrance_enable"))
                    _auto_save(ui.switch('随机选取', value=get_nested_value(config, "thanks", "entrance_random")).style(switch_internal_css), ("thanks", "entrance_random"))
                    _auto_save(ui.textarea(label='入场文案', value=textarea_data_change(get_nested_value(config, "thanks", "entrance_copy")), placeholder='用户进入直播间的相关文案，请勿动 {username}，此字符串用于替换用户名').style("width:100%;"), ("thanks", "entrance_copy"))
                _auto_save(ui.input(label='最低答谢礼物价格', value=get_nested_value(config, "thanks", "lowest_price"), placeholder='设置最低答谢礼物的价格（元）').style("width:100%;"), ("thanks", "lowest_price"))
            with ui.grid(columns=3):
                with ui.column().style("width:100%;"):
                    _auto_save(ui.switch('周期性触发启用', value=get_nested_value(config, "thanks", "entrance", "periodic_trigger", "enable")).style(switch_internal_css), ("thanks", "entrance", "periodic_trigger", "enable"))
                    for pt_key in ['periodic_time_min', 'periodic_time_max', 'trigger_num_min', 'trigger_num_max']:
                        _auto_save(ui.input(label=f'触发{pt_key}', value=get_nested_value(config, "thanks", "entrance", "periodic_trigger", pt_key), placeholder=f'例如：5').style("width:100%;"), ("thanks", "entrance", "periodic_trigger", pt_key))
        with ui.expansion('礼物设置', icon="settings", value=True).classes('w-full'):
            with ui.grid(columns=3):
                with ui.column().style("width:100%;"):
                    _auto_save(ui.switch('启用礼物答谢', value=get_nested_value(config, "thanks", "gift_enable")).style(switch_internal_css), ("thanks", "gift_enable"))
                    _auto_save(ui.switch('随机选取', value=get_nested_value(config, "thanks", "gift_random")).style(switch_internal_css), ("thanks", "gift_random"))
                    _auto_save(ui.textarea(label='礼物文案', value=textarea_data_change(get_nested_value(config, "thanks", "gift_copy")), placeholder='用户赠送礼物的相关文案').style("width:100%;"), ("thanks", "gift_copy"))
                _auto_save(ui.input(label='最低答谢礼物价格', value=get_nested_value(config, "thanks", "lowest_price"), placeholder='设置最低答谢礼物的价格（元）').style("width:100%;"), ("thanks", "lowest_price"))
            with ui.grid(columns=3):
                with ui.column().style("width:100%;"):
                    _auto_save(ui.switch('周期性触发启用', value=get_nested_value(config, "thanks", "gift", "periodic_trigger", "enable")).style(switch_internal_css), ("thanks", "gift", "periodic_trigger", "enable"))
                    for pt_key in ['periodic_time_min', 'periodic_time_max', 'trigger_num_min', 'trigger_num_max']:
                        _auto_save(ui.input(label=f'触发{pt_key}', value=get_nested_value(config, "thanks", "gift", "periodic_trigger", pt_key), placeholder=f'例如：5').style("width:100%;"), ("thanks", "gift", "periodic_trigger", pt_key))
        with ui.expansion('关注设置', icon="settings", value=True).classes('w-full'):
            with ui.grid(columns=3):
                with ui.column().style("width:100%;"):
                    _auto_save(ui.switch('启用关注答谢', value=get_nested_value(config, "thanks", "follow_enable")).style(switch_internal_css), ("thanks", "follow_enable"))
                    _auto_save(ui.switch('随机选取', value=get_nested_value(config, "thanks", "follow_random")).style(switch_internal_css), ("thanks", "follow_random"))
                    _auto_save(ui.textarea(label='关注文案', value=textarea_data_change(get_nested_value(config, "thanks", "follow_copy")), placeholder='用户关注时的相关文案').style("width:100%;"), ("thanks", "follow_copy"))
            with ui.grid(columns=3):
                with ui.column().style("width:100%;"):
                    _auto_save(ui.switch('周期性触发启用', value=get_nested_value(config, "thanks", "follow", "periodic_trigger", "enable")).style(switch_internal_css), ("thanks", "follow", "periodic_trigger", "enable"))
                    for pt_key in ['periodic_time_min', 'periodic_time_max', 'trigger_num_min', 'trigger_num_max']:
                        _auto_save(ui.input(label=f'触发{pt_key}', value=get_nested_value(config, "thanks", "follow", "periodic_trigger", pt_key), placeholder=f'例如：5').style("width:100%;"), ("thanks", "follow", "periodic_trigger", pt_key))
