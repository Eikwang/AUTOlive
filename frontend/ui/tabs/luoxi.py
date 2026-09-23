# -*- coding: UTF-8 -*-
"""
洛曦直播弹幕助手标签页模块
从 common_config.py 拆分而来

布局规范：
- 设置项用最多 3 列网格排布，组件宽度跟随列宽（width:100%）
- 开关+关联参数 为上下结构单元：开关在上，输入框在下
"""
from nicegui import ui
from typing import Dict, Any, Callable
from frontend.ui.components.config_helper import get_nested_value


def create_luoxi_tab(
    config: Dict[str, Any],
    theme_config: Dict[str, str],
    set_config_callback: Callable
):
    """创建洛曦直播弹幕助手标签页"""
    card_css = theme_config.get("card", "")
    switch_internal_css = theme_config.get("switch_internal", "")

    # 导入自动绑定函数
    from frontend.utils.config_auto_save import create_auto_save
    _auto_save = create_auto_save(config, set_config_callback, tab_key="luoxi")

    # 洛曦直播弹幕助手
    with ui.card().style(card_css):
        ui.label("洛曦 直播弹幕助手")
        with ui.grid(columns=3):
            with ui.column().style("width:100%;"):
                _auto_save(ui.select(label='对接版本', options={'V0.1.x': 'V0.1.x'}, value=get_nested_value(config, "luoxi_project", "Live_Comment_Assistant", "version")).style("width:100%;"), keys=("luoxi_project", "Live_Comment_Assistant", "version"))
                _auto_save(ui.input(label='API地址', value=get_nested_value(config, "luoxi_project", "Live_Comment_Assistant", "api_ip_port"), placeholder='洛曦 直播弹幕助手 API地址').style("width:100%;"), keys=("luoxi_project", "Live_Comment_Assistant", "api_ip_port"))
        with ui.card().style(card_css):
            ui.label("触发类型")
            with ui.row():
                luoxi_type_list = ["comment", "comment_reply", "idle_time_task", "entrance_reply", "follow_reply", "gift_reply", "reread", "schedule", "integral", "key_mapping_copywriting"]
                luoxi_type_mapping = {"comment": "弹幕消息", "comment_reply": "弹幕回复", "idle_time_task": "闲时任务", "entrance_reply": "入场回复", "follow_reply": "关注回复", "gift_reply": "礼物回复", "reread": "复读", "schedule": "定时任务", "integral": "积分消息", "key_mapping_copywriting": "按键映射-文案"}
                for lt in luoxi_type_list:
                    ui.checkbox(text=luoxi_type_mapping[lt], value=lt in (get_nested_value(config, "luoxi_project", "Live_Comment_Assistant", "type") or []))
        with ui.card().style(card_css):
            ui.label("触发位置")
            with ui.row():
                for pos in ["消息产生时", "音频播放时"]:
                    ui.checkbox(text=pos, value=pos in (get_nested_value(config, "luoxi_project", "Live_Comment_Assistant", "trigger_position") or []))
