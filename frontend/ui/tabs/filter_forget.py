# -*- coding: UTF-8 -*-
"""
消息遗忘&保留标签页模块
从 common_config.py 拆分而来

布局规范：
- 设置项用最多 3 列网格排布，组件宽度跟随列宽（width:100%）
"""
from nicegui import ui
from typing import Dict, Any, Callable
from frontend.ui.components.config_helper import get_nested_value


def create_filter_forget_tab(
    config: Dict[str, Any],
    theme_config: Dict[str, str],
    set_config_callback: Callable
):
    """创建消息遗忘&保留标签页"""
    card_css = theme_config.get("card", "")

    # 导入自动绑定函数
    from frontend.utils.config_auto_save import create_auto_save
    _auto_save = create_auto_save(config, set_config_callback, tab_key="filter_forget")

    if get_nested_value(config, "webui", "show_card", "common_config", "filter"):
        with ui.card().style(card_css):
            with ui.expansion('消息遗忘&保留设置', icon="settings", value=True).classes('w-full'):
                with ui.element('div').classes('p-2 bg-blue-100'):
                    ui.label("遗忘间隔 指的是每隔这个间隔时间（秒），就会丢弃这个间隔时间中接收到的数据，但会保留最新的n个数据；保留数 指的是保留最新收到的数据的数量")
                with ui.grid(columns=3):
                    _auto_save(ui.input(
                        label='弹幕遗忘间隔',
                        placeholder='例：1',
                        value=get_nested_value(config, "filter", "comment_forget_duration")
                    ).style("width:100%;").tooltip('指的是每隔这个间隔时间（秒），就会丢弃这个间隔时间中接收到的数据，\n保留数据在以下配置中可以自定义'), keys=("filter", "comment_forget_duration"))
                    _auto_save(ui.input(label='弹幕保留数', placeholder='保留最新收到的数据的数量', value=get_nested_value(config, "filter", "comment_forget_reserve_num")).style("width:100%;").tooltip('保留最新收到的数据的数量'), keys=("filter", "comment_forget_reserve_num"))
                    _auto_save(ui.input(label='礼物遗忘间隔', placeholder='指的是每隔这个间隔时间（秒），就会丢弃这个间隔时间中接收到的数据，\n保留数据在以下配置中可以自定义', value=get_nested_value(config, "filter", "gift_forget_duration")).style("width:100%;").tooltip('指的是每隔这个间隔时间（秒），就会丢弃这个间隔时间中接收到的数据，\n保留数据在以下配置中可以自定义'), keys=("filter", "gift_forget_duration"))
                    _auto_save(ui.input(label='礼物保留数', placeholder='保留最新收到的数据的数量', value=get_nested_value(config, "filter", "gift_forget_reserve_num")).style("width:100%;").tooltip('保留最新收到的数据的数量'), keys=("filter", "gift_forget_reserve_num"))
                with ui.grid(columns=3):
                    _auto_save(ui.input(label='入场遗忘间隔', placeholder='指的是每隔这个间隔时间（秒），就会丢弃这个间隔时间中接收到的数据，\n保留数据在以下配置中可以自定义', value=get_nested_value(config, "filter", "entrance_forget_duration")).style("width:100%;").tooltip('指的是每隔这个间隔时间（秒），就会丢弃这个间隔时间中接收到的数据，\n保留数据在以下配置中可以自定义'), keys=("filter", "entrance_forget_duration"))
                    _auto_save(ui.input(label='入场保留数', placeholder='保留最新收到的数据的数量', value=get_nested_value(config, "filter", "entrance_forget_reserve_num")).style("width:100%;").tooltip('保留最新收到的数据的数量'), keys=("filter", "entrance_forget_reserve_num"))
                    _auto_save(ui.input(label='关注遗忘间隔', placeholder='指的是每隔这个间隔时间（秒），就会丢弃这个间隔时间中接收到的数据，\n保留数据在以下配置中可以自定义', value=get_nested_value(config, "filter", "follow_forget_duration")).style("width:100%;").tooltip('指的是每隔这个间隔时间（秒），就会丢弃这个间隔时间中接收到的数据，\n保留数据在以下配置中可以自定义'), keys=("filter", "follow_forget_duration"))
                    _auto_save(ui.input(label='关注保留数', placeholder='保留最新收到的数据的数量', value=get_nested_value(config, "filter", "follow_forget_reserve_num")).style("width:100%;").tooltip('保留最新收到的数据的数量'), keys=("filter", "follow_forget_reserve_num"))
                with ui.grid(columns=3):
                    _auto_save(ui.input(label='聊天遗忘间隔', placeholder='指的是每隔这个间隔时间（秒），就会丢弃这个间隔时间中接收到的数据，\n保留数据在以下配置中可以自定义', value=get_nested_value(config, "filter", "talk_forget_duration")).style("width:100%;").tooltip('指的是每隔这个间隔时间（秒），就会丢弃这个间隔时间中接收到的数据，\n保留数据在以下配置中可以自定义'), keys=("filter", "talk_forget_duration"))
                    _auto_save(ui.input(label='聊天保留数', placeholder='保留最新收到的数据的数量', value=get_nested_value(config, "filter", "talk_forget_reserve_num")).style("width:100%;").tooltip('保留最新收到的数据的数量'), keys=("filter", "talk_forget_reserve_num"))
                    _auto_save(ui.input(label='定时遗忘间隔', placeholder='指的是每隔这个间隔时间（秒），就会丢弃这个间隔时间中接收到的数据，\n保留数据在以下配置中可以自定义', value=get_nested_value(config, "filter", "schedule_forget_duration")).style("width:100%;").tooltip('指的是每隔这个间隔时间（秒），就会丢弃这个间隔时间中接收到的数据，\n保留数据在以下配置中可以自定义'), keys=("filter", "schedule_forget_duration"))
                    _auto_save(ui.input(label='定时保留数', placeholder='保留最新收到的数据的数量', value=get_nested_value(config, "filter", "schedule_forget_reserve_num")).style("width:100%;").tooltip('保留最新收到的数据的数量'), keys=("filter", "schedule_forget_reserve_num"))
                with ui.grid(columns=3):
                    _auto_save(ui.input(label='闲时任务遗忘间隔', placeholder='指的是每隔这个间隔时间（秒），就会丢弃这个间隔时间中接收到的数据，\n保留数据在以下配置中可以自定义', value=get_nested_value(config, "filter", "idle_time_task_forget_duration")).style("width:100%;").tooltip('指的是每隔这个间隔时间（秒），就会丢弃这个间隔时间中接收到的数据，\n保留数据在以下配置中可以自定义'), keys=("filter", "idle_time_task_forget_duration"))
                    _auto_save(ui.input(label='闲时任务保留数', placeholder='保留最新收到的数据的数量', value=get_nested_value(config, "filter", "idle_time_task_forget_reserve_num")).style("width:100%;").tooltip('保留最新收到的数据的数量'), keys=("filter", "idle_time_task_forget_reserve_num"))
                    _auto_save(ui.input(label='图像识别遗忘间隔', placeholder='指的是每隔这个间隔时间（秒），就会丢弃这个间隔时间中接收到的数据，\n保留数据在以下配置中可以自定义', value=get_nested_value(config, "filter", "image_recognition_schedule_forget_duration")).style("width:100%;").tooltip('指的是每隔这个间隔时间（秒），就会丢弃这个间隔时间中接收到的数据，\n保留数据在以下配置中可以自定义'), keys=("filter", "image_recognition_schedule_forget_duration"))
                    _auto_save(ui.input(label='图像识别保留数', placeholder='保留最新收到的数据的数量', value=get_nested_value(config, "filter", "image_recognition_schedule_forget_reserve_num")).style("width:100%;").tooltip('保留最新收到的数据的数量'), keys=("filter", "image_recognition_schedule_forget_reserve_num"))
