# -*- coding: UTF-8 -*-
"""
消息队列&音频队列标签页模块
从 common_config.py 拆分而来
"""
from nicegui import ui
from typing import Dict, Any, Callable
from frontend.ui.components.config_helper import get_nested_value


def create_filter_queue_tab(
    config: Dict[str, Any],
    theme_config: Dict[str, str],
    set_config_callback: Callable
):
    """创建消息队列&音频队列标签页"""
    card_css = theme_config.get("card", "")
    switch_internal_css = theme_config.get("switch_internal", "")

    # 导入自动绑定函数
    from frontend.utils.config_auto_save import create_auto_save
    _auto_save = create_auto_save(config, set_config_callback, tab_key="filter_queue")

    if get_nested_value(config, "webui", "show_card", "common_config", "filter"):
        with ui.card().style(card_css):
            with ui.expansion('待合成音频的消息&待播放音频队列', icon="settings", value=True).classes('w-full'):
                with ui.row():
                    _auto_save(ui.input(label='消息队列最大保留长度', placeholder='收到的消息，生成的文本内容，会根据优先级存入消息队列，当新消息的优先级低于队列中所有的消息且超过此长度时，此消息将被丢弃', value=get_nested_value(config, "filter", "message_queue_max_len")).style("width:160px;").tooltip('收到的消息，生成的文本内容，会根据优先级存入消息队列，当新消息的优先级低于队列中所有的消息且超过此长度时，此消息将被丢弃'), keys=("filter", "message_queue_max_len"))
                    _auto_save(ui.input(label='音频播放队列最大保留长度', placeholder='合成后的音频，会根据优先级存入待播放音频队列，当新音频的优先级低于队列中所有的音频且超过此长度时，此音频将被丢弃', value=get_nested_value(config, "filter", "voice_tmp_path_queue_max_len")).style("width:200px;").tooltip('合成后的音频，会根据优先级存入待播放音频队列，当新音频的优先级低于队列中所有的音频且超过此长度时，此音频将被丢弃'), keys=("filter", "voice_tmp_path_queue_max_len"))

                    _auto_save(ui.input(
                        label='音频播放队列首次触发播放阈值',
                        placeholder='正整数 例如：20，如果你不想开播前缓冲一定数量的音频，请配置0',
                        value=get_nested_value(config, "filter", "voice_tmp_path_queue_min_start_play")
                    ).style("width:200px;").tooltip('此功能用于缓存一定数量的音频后再开始播放。如果你不想开播前缓冲一定数量的音频，请配置0；如果你想提前准备一些音频，如因为TTS合成慢的原因，可以配置此值，让TTS提前合成你的其他任务触发的内容'), keys=("filter", "voice_tmp_path_queue_min_start_play"))

                    with ui.element('div').classes('p-2 bg-blue-100'):
                        ui.label("下方优先级配置，请使用正整数。数字越大，优先级越高，就会优先合成音频播放")
                        ui.label("另外需要注意，由于shi山原因，目前这个队列内容是文本切分后计算的长度，所以如果回复内容过长，可能会有丢数据的情况")
                with ui.grid(columns=3):
                    _auto_save(ui.input(label='闲时任务 优先级', value=get_nested_value(config, "filter", "priority_mapping", "idle_time_task"), placeholder='数字越大，优先级越高，但这个并非文本，所以暂时没啥用，预留').style("width:200px;").tooltip('数字越大，优先级越高'), keys=("filter", "priority_mapping", "idle_time_task"))
                    _auto_save(ui.input(label='图像识别 优先级', value=get_nested_value(config, "filter", "priority_mapping", "image_recognition_schedule"), placeholder='数字越大，优先级越高').style("width:200px;").tooltip('数字越大，优先级越高'), keys=("filter", "priority_mapping", "image_recognition_schedule"))
                    _auto_save(ui.input(label='本地问答-音频 优先级', value=get_nested_value(config, "filter", "priority_mapping", "local_qa_audio"), placeholder='数字越大，优先级越高').style("width:200px;").tooltip('数字越大，优先级越高'), keys=("filter", "priority_mapping", "local_qa_audio"))
                    _auto_save(ui.input(label='弹幕回复 优先级', value=get_nested_value(config, "filter", "priority_mapping", "comment"), placeholder='数字越大，优先级越高').style("width:200px;").tooltip('数字越大，优先级越高'), keys=("filter", "priority_mapping", "comment"))
                    _auto_save(ui.input(label='文案 优先级', value=get_nested_value(config, "filter", "priority_mapping", "copywriting"), placeholder='数字越大，优先级越高，文案页的文案，但这个并非文本，所以暂时没啥用，预留').style("width:200px;").tooltip('数字越大，优先级越高'), keys=("filter", "priority_mapping", "copywriting"))

                with ui.grid(columns=3):
                    _auto_save(ui.input(label='点歌 优先级', value=get_nested_value(config, "filter", "priority_mapping", "song"), placeholder='数字越大，优先级越高，但这个并非文本，所以暂时没啥用，预留').style("width:200px;").tooltip('数字越大，优先级越高'), keys=("filter", "priority_mapping", "song"))
                    _auto_save(ui.input(label='念弹幕 优先级', value=get_nested_value(config, "filter", "priority_mapping", "read_comment"), placeholder='数字越大，优先级越高').style("width:200px;").tooltip('数字越大，优先级越高'), keys=("filter", "priority_mapping", "read_comment"))
                    _auto_save(ui.input(label='入场欢迎 优先级', value=get_nested_value(config, "filter", "priority_mapping", "entrance"), placeholder='数字越大，优先级越高').style("width:200px;").tooltip('数字越大，优先级越高'), keys=("filter", "priority_mapping", "entrance"))
                    _auto_save(ui.input(label='礼物答谢 优先级', value=get_nested_value(config, "filter", "priority_mapping", "gift"), placeholder='数字越大，优先级越高').style("width:200px;").tooltip('数字越大，优先级越高'), keys=("filter", "priority_mapping", "gift"))
                    _auto_save(ui.input(label='关注答谢 优先级', value=get_nested_value(config, "filter", "priority_mapping", "follow"), placeholder='数字越大，优先级越高').style("width:200px;").tooltip('数字越大，优先级越高'), keys=("filter", "priority_mapping", "follow"))
                with ui.grid(columns=3):
                    _auto_save(ui.input(label='聊天（语音输入） 优先级', value=get_nested_value(config, "filter", "priority_mapping", "talk"), placeholder='数字越大，优先级越高，但这个并非文本，所以暂时没啥用，预留').style("width:200px;").tooltip('数字越大，优先级越高'), keys=("filter", "priority_mapping", "talk"))
                    _auto_save(ui.input(label='复读 优先级', value=get_nested_value(config, "filter", "priority_mapping", "reread"), placeholder='数字越大，优先级越高，但这个并非文本，所以暂时没啥用，预留').style("width:200px;").tooltip('数字越大，优先级越高'), keys=("filter", "priority_mapping", "reread"))
                    _auto_save(ui.input(label='按键映射 优先级', value=get_nested_value(config, "filter", "priority_mapping", "key_mapping"), placeholder='数字越大，优先级越高').style("width:200px;").tooltip('数字越大，优先级越高'), keys=("filter", "priority_mapping", "key_mapping"))
                    _auto_save(ui.input(label='积分 优先级', value=get_nested_value(config, "filter", "priority_mapping", "integral"), placeholder='数字越大，优先级越高').style("width:200px;").tooltip('数字越大，优先级越高'), keys=("filter", "priority_mapping", "integral"))
                    _auto_save(ui.input(label='最高优先级复读 优先级', value=get_nested_value(config, "filter", "priority_mapping", "reread_top_priority"), placeholder='数字越大，优先级越高').style("width:200px;").tooltip('数字越大，优先级越高'), keys=("filter", "priority_mapping", "reread_top_priority"))

                with ui.grid(columns=3):
                    _auto_save(ui.input(label='异常报警 优先级', value=get_nested_value(config, "filter", "priority_mapping", "abnormal_alarm"), placeholder='数字越大，优先级越高').style("width:200px;").tooltip('数字越大，优先级越高'), keys=("filter", "priority_mapping", "abnormal_alarm"))
                    _auto_save(ui.input(label='动态文案 优先级', value=get_nested_value(config, "filter", "priority_mapping", "trends_copywriting"), placeholder='数字越大，优先级越高').style("width:200px;").tooltip('数字越大，优先级越高'), keys=("filter", "priority_mapping", "trends_copywriting"))
                    _auto_save(ui.input(label='定时任务 优先级', value=get_nested_value(config, "filter", "priority_mapping", "schedule"), placeholder='数字越大，优先级越高').style("width:200px;").tooltip('数字越大，优先级越高'), keys=("filter", "priority_mapping", "schedule"))
                    _auto_save(ui.input(label='助播-文本 优先级', value=get_nested_value(config, "filter", "priority_mapping", "assistant_anchor_text"), placeholder='数字越大，优先级越高').style("width:200px;").tooltip('数字越大，优先级越高'), keys=("filter", "priority_mapping", "assistant_anchor_text"))
                    _auto_save(ui.input(label='助播-音频 优先级', value=get_nested_value(config, "filter", "priority_mapping", "assistant_anchor_audio"), placeholder='数字越大，优先级越高').style("width:200px;").tooltip('数字越大，优先级越高'), keys=("filter", "priority_mapping", "assistant_anchor_audio"))
