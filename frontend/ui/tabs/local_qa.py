# -*- coding: UTF-8 -*-
"""
本地问答标签页模块
从 common_config.py 拆分而来
"""
from nicegui import ui
from typing import Dict, Any, Callable
from frontend.ui.components.config_helper import get_nested_value
from frontend.utils.common import textarea_data_change


def create_local_qa_tab(
    config: Dict[str, Any],
    theme_config: Dict[str, str],
    set_config_callback: Callable
):
    """创建本地问答标签页"""
    card_css = theme_config.get("card", "")
    switch_internal_css = theme_config.get("switch_internal", "")

    # 导入自动绑定函数
    from frontend.utils.config_auto_save import create_auto_save
    _auto_save = create_auto_save(config, set_config_callback, tab_key="local_qa")

    with ui.card().style(card_css):
        ui.label('本地问答')
        with ui.row():
            _auto_save(ui.switch('周期性触发启用', value=get_nested_value(config, "local_qa", "periodic_trigger", "enable")).style(switch_internal_css), ("local_qa", "periodic_trigger", "enable"))
            _auto_save(ui.input(label='触发周期最小值', value=get_nested_value(config, "local_qa", "periodic_trigger", "periodic_time_min"), placeholder='每隔这个周期的时间会触发n次此功能').style("width:100px;").tooltip('每隔这个周期的时间会触发n次此功能，周期时间从最大最小值之间随机生成'), ("local_qa", "periodic_trigger", "periodic_time_min"))
            _auto_save(ui.input(label='触发周期最大值', value=get_nested_value(config, "local_qa", "periodic_trigger", "periodic_time_max"), placeholder='每隔这个周期的时间会触发n次此功能').style("width:100px;").tooltip('每隔这个周期的时间会触发n次此功能，周期时间从最大最小值之间随机生成'), ("local_qa", "periodic_trigger", "periodic_time_max"))
            _auto_save(ui.input(label='触发次数最小值', value=get_nested_value(config, "local_qa", "periodic_trigger", "trigger_num_min"), placeholder='周期到后，会触发n次此功能').style("width:100px;").tooltip('周期到后，会触发n次此功能，次数从最大最小值之间随机生成'), ("local_qa", "periodic_trigger", "trigger_num_min"))
            _auto_save(ui.input(label='触发次数最大值', value=get_nested_value(config, "local_qa", "periodic_trigger", "trigger_num_max"), placeholder='周期到后，会触发n次此功能').style("width:100px;").tooltip('周期到后，会触发n次此功能，次数从最大最小值之间随机生成'), ("local_qa", "periodic_trigger", "trigger_num_max"))

        with ui.grid(columns=3):
            _auto_save(ui.switch('启用文本匹配', value=get_nested_value(config, "local_qa", "text", "enable")).style(switch_internal_css), ("local_qa", "text", "enable"))
            _auto_save(ui.select(
                label='弹幕日志类型',
                options={'json': '自定义json', 'text': '一问一答'},
                value=get_nested_value(config, "local_qa", "text", "type")
            ), ("local_qa", "text", "type"))
            _auto_save(ui.input(label='文本问答数据路径', placeholder='本地问答文本数据存储路径', value=get_nested_value(config, "local_qa", "text", "file_path")).style("width:200px;"), ("local_qa", "text", "file_path"))
            _auto_save(ui.input(label='文本最低相似度', placeholder='最低文本匹配相似度', value=get_nested_value(config, "local_qa", "text", "similarity")).style("width:200px;"), ("local_qa", "text", "similarity"))
            _auto_save(ui.input(label='用户名最大长度', value=get_nested_value(config, "local_qa", "text", "username_max_len"), placeholder='需要保留的用户名的最大长度').style("width:100px;"), ("local_qa", "text", "username_max_len"))
        with ui.grid(columns=3):
            _auto_save(ui.switch('启用音频匹配', value=get_nested_value(config, "local_qa", "audio", "enable")).style(switch_internal_css), ("local_qa", "audio", "enable"))
            _auto_save(ui.input(label='音频存储路径', placeholder='本地问答音频文件存储路径', value=get_nested_value(config, "local_qa", "audio", "file_path")).style("width:200px;"), ("local_qa", "audio", "file_path"))
            _auto_save(ui.input(label='音频最低相似度', placeholder='最低音频匹配相似度', value=get_nested_value(config, "local_qa", "audio", "similarity")).style("width:200px;"), ("local_qa", "audio", "similarity"))
