# -*- coding: UTF-8 -*-
"""
闲时任务标签页模块
从 common_config.py 拆分而来

布局规范：
- 设置项用最多 3 列网格排布，组件宽度跟随列宽（width:100%）
- 开关+关联参数 为上下结构单元：开关在上，输入框在下
"""
from nicegui import ui
from typing import Dict, Any, Callable
from frontend.ui.components.config_helper import get_nested_value
from frontend.utils.common import textarea_data_change


def create_idle_time_task_tab(
    config: Dict[str, Any],
    theme_config: Dict[str, str],
    set_config_callback: Callable
):
    """创建闲时任务标签页"""
    card_css = theme_config.get("card", "")
    switch_internal_css = theme_config.get("switch_internal", "")

    # 导入自动绑定函数
    from frontend.utils.config_auto_save import create_auto_save
    _auto_save = create_auto_save(config, set_config_callback, tab_key="idle_time_task")

    with ui.card().style(card_css):
        ui.label('闲时任务')
        with ui.grid(columns=3):
            with ui.column().style("width:100%;"):
                _auto_save(ui.select(
                    label='机制类型',
                    options={
                        '待合成消息队列更新闲时': '待合成消息队列更新闲时',
                        '待播放音频队列更新闲时': '待播放音频队列更新闲时',
                        '直播间无消息更新闲时': '直播间无消息更新闲时',
                    },
                    value=get_nested_value(config, "idle_time_task", "type")
                ).style("width:100%;").tooltip('闲时任务执行的逻辑'), ("idle_time_task", "type"))
            _auto_save(ui.input(label='待合成消息队列个数小于此值时触发', value=get_nested_value(config, "idle_time_task", "min_msg_queue_len_to_trigger"), placeholder='待合成消息队列个数小于此值时触发').style("width:100%;"), ("idle_time_task", "min_msg_queue_len_to_trigger"))
            _auto_save(ui.input(label='待播放音频队列个数小于此值时触发', value=get_nested_value(config, "idle_time_task", "min_audio_queue_len_to_trigger"), placeholder='待播放音频队列个数小于此值时触发').style("width:100%;"), ("idle_time_task", "min_audio_queue_len_to_trigger"))
            _auto_save(ui.input(label='最小闲时时间', value=get_nested_value(config, "idle_time_task", "idle_time_min"), placeholder='最小闲时间隔时间（秒）').style("width:100%;"), ("idle_time_task", "idle_time_min"))
            _auto_save(ui.input(label='最大闲时时间', value=get_nested_value(config, "idle_time_task", "idle_time_max"), placeholder='最大闲时间隔时间（秒）').style("width:100%;"), ("idle_time_task", "idle_time_max"))
            _auto_save(ui.input(label='等待播放音频数量阈值', value=get_nested_value(config, "idle_time_task", "wait_play_audio_num_threshold"), placeholder='阈值').style("width:100%;"), ("idle_time_task", "wait_play_audio_num_threshold"))
            _auto_save(ui.input(label='闲时计时减小到', value=get_nested_value(config, "idle_time_task", "idle_time_reduce_to"), placeholder='缩减值').style("width:100%;"), ("idle_time_task", "idle_time_reduce_to"))
        with ui.row():
            ui.label('刷新闲时计时的消息类型')
            idle_time_task_trigger_type_mapping = {"comment": "弹幕", "gift": "礼物", "entrance": "入场", "follow": "关注"}
            for trigger_type, trigger_label in idle_time_task_trigger_type_mapping.items():
                ui.checkbox(text=trigger_label, value=trigger_type in (get_nested_value(config, "idle_time_task", "trigger_type") or []))
        with ui.grid(columns=3):
            with ui.column().style("width:100%;"):
                _auto_save(ui.switch('文案模式', value=get_nested_value(config, "idle_time_task", "copywriting", "enable")).style(switch_internal_css), ("idle_time_task", "copywriting", "enable"))
                _auto_save(ui.switch('随机文案', value=get_nested_value(config, "idle_time_task", "copywriting", "random")).style(switch_internal_css), ("idle_time_task", "copywriting", "random"))
                _auto_save(ui.textarea(label='文案列表', value=textarea_data_change(get_nested_value(config, "idle_time_task", "copywriting", "copy")), placeholder='文案列表，换行分隔').style("width:100%;"), ("idle_time_task", "copywriting", "copy"))
            with ui.column().style("width:100%;"):
                _auto_save(ui.switch('弹幕触发LLM模式', value=get_nested_value(config, "idle_time_task", "comment", "enable")).style(switch_internal_css), ("idle_time_task", "comment", "enable"))
                _auto_save(ui.switch('随机弹幕', value=get_nested_value(config, "idle_time_task", "comment", "random")).style(switch_internal_css), ("idle_time_task", "comment", "random"))
                _auto_save(ui.textarea(label='弹幕列表', value=textarea_data_change(get_nested_value(config, "idle_time_task", "comment", "copy")), placeholder='弹幕列表，换行分隔').style("width:100%;"), ("idle_time_task", "comment", "copy"))
            with ui.column().style("width:100%;"):
                _auto_save(ui.switch('本地音频模式', value=get_nested_value(config, "idle_time_task", "local_audio", "enable")).style(switch_internal_css), ("idle_time_task", "local_audio", "enable"))
                _auto_save(ui.switch('随机本地音频', value=get_nested_value(config, "idle_time_task", "local_audio", "random")).style(switch_internal_css), ("idle_time_task", "local_audio", "random"))
                _auto_save(ui.textarea(label='本地音频路径列表', value=textarea_data_change(get_nested_value(config, "idle_time_task", "local_audio", "path")), placeholder='本地音频路径列表，换行分隔').style("width:100%;"), ("idle_time_task", "local_audio", "path"))
