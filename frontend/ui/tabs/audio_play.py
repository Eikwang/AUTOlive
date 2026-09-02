# -*- coding: UTF-8 -*-
"""
音频播放标签页模块
从 common_config.py 拆分而来

布局规范：
- 设置项用最多 3 列网格排布，组件宽度跟随列宽（width:100%）
- 开关+关联参数 为上下结构单元：开关在上，输入框在下
"""
from nicegui import ui
from typing import Dict, Any, Callable

from frontend.ui.components.config_helper import get_nested_value
from frontend.utils.common import is_url_check


def create_audio_play_tab(
    config: Dict[str, Any],
    theme_config: Dict[str, str],
    set_config_callback: Callable
):
    """创建音频播放标签页"""
    card_css = theme_config.get("card", "")
    switch_internal_css = theme_config.get("switch_internal", "")

    # 导入自动绑定函数
    from frontend.utils.config_auto_save import create_auto_save
    _auto_save = create_auto_save(config, set_config_callback, tab_key="audio_play")

    if get_nested_value(config, "webui", "show_card", "common_config", "play_audio"):
        with ui.card().style(card_css):
            ui.label('音频播放')
            with ui.grid(columns=3):
                _auto_save(ui.switch('启用', value=get_nested_value(config, "play_audio", "enable")).style(switch_internal_css), ("play_audio", "enable"))
                _auto_save(ui.switch('启用文本切分', value=get_nested_value(config, "play_audio", "text_split_enable")).style(switch_internal_css).tooltip('启用后会将LLM等待合成音频的消息根据内部切分算法切分成多个短句，以便TTS快速合成'), ("play_audio", "text_split_enable"))
                _auto_save(ui.switch('音频信息回传给内部接口', value=get_nested_value(config, "play_audio", "info_to_callback")).style(switch_internal_css).tooltip('启用后，会在当前音频播放完毕后，将程序中等待播放的音频信息传递给内部接口，用于闲时任务的闲时清零功能。\n不过这个功能会一定程度的拖慢程序运行，如果你不需要闲时清零，可以关闭此功能来提高响应速度'), ("play_audio", "info_to_callback"))

            with ui.grid(columns=3):
                _auto_save(ui.input(label='间隔时间重复次数最小值', value=get_nested_value(config, "play_audio", "interval_num_min"), placeholder='普通音频播放间隔时间，重复睡眠次数最小值。会在最大最小值之间随机生成一个重复次数，就是 次数 x 时间 = 最终间隔时间').style("width:100%;").tooltip('普通音频播放间隔时间重复睡眠次数最小值。会在最大最小值之间随机生成一个重复次数，就是 次数 x 时间 = 最终间隔时间'), ("play_audio", "interval_num_min"))
                _auto_save(ui.input(label='间隔时间重复次数最大值', value=get_nested_value(config, "play_audio", "interval_num_max"), placeholder='普通音频播放间隔时间，重复睡眠次数最大值。会在最大最小值之间随机生成一个重复次数，就是 次数 x 时间 = 最终间隔时间').style("width:100%;").tooltip('普通音频播放间隔时间重复睡眠次数最大值。会在最大最小值之间随机生成一个重复次数，就是 次数 x 时间 = 最终间隔时间'), ("play_audio", "interval_num_max"))
                _auto_save(ui.input(label='普通音频播放间隔最小值', value=get_nested_value(config, "play_audio", "normal_interval_min"), placeholder='就是弹幕回复、唱歌等音频播放结束后到播放下一个音频之间的一个间隔时间，单位：秒').style("width:100%;").tooltip('就是弹幕回复、唱歌等音频播放结束后到播放下一个音频之间的一个间隔时间，单位：秒。次数 x 时间 = 最终间隔时间'), ("play_audio", "normal_interval_min"))
                _auto_save(ui.input(label='普通音频播放间隔最大值', value=get_nested_value(config, "play_audio", "normal_interval_max"), placeholder='就是弹幕回复、唱歌等音频播放结束后到播放下一个音频之间的一个间隔时间，单位：秒').style("width:100%;").tooltip('就是弹幕回复、唱歌等音频播放结束后到播放下一个音频之间的一个间隔时间，单位：秒。次数 x 时间 = 最终间隔时间'), ("play_audio", "normal_interval_max"))
                _auto_save(ui.input(label='音频输出路径', placeholder='音频文件合成后存储的路径，支持相对路径或绝对路径', value=get_nested_value(config, "play_audio", "out_path")).style("width:100%;").tooltip('音频文件合成后存储的路径，支持相对路径或绝对路径'), ("play_audio", "out_path"))
                _auto_save(ui.select(
                    label='音频播放器',
                    options={'pygame': 'pygame', 'audio_player_v2': 'audio_player_v2', 'audio_player': 'audio_player'},
                    value=get_nested_value(config, "play_audio", "player", default='pygame')
                ).style("width:100%;").tooltip('选用的音频播放器，默认pygame不需要再安装其他程序。audio player需要单独安装对接，详情看视频教程'), ("play_audio", "player"))

            with ui.card().style(card_css):
                ui.label('audio_player')
                with ui.grid(columns=3):
                    _auto_save(ui.input(
                        label='API地址',
                        value=get_nested_value(config, "audio_player", "api_ip_port"),
                        placeholder='audio_player的API地址，只需要 http://ip:端口 即可',
                        validation={
                            '请输入正确格式的URL': lambda value: is_url_check(value),
                        }
                    ).style("width:100%;").tooltip('仅在 音频播放器：audio_player等，情况下填写。audio_player的API地址，只需要 http://ip:端口 即可'), ("audio_player", "api_ip_port"))

            with ui.card().style(card_css):
                ui.label('音频随机变速')
                with ui.grid(columns=3):
                    with ui.column().style("width:100%;"):
                        _auto_save(ui.switch('普通音频变速', value=get_nested_value(config, "audio_random_speed", "normal", "enable")).style(switch_internal_css).tooltip('是否启用 针对 普通音频的音频变速功能。此功能需要安装配置ffmpeg才能使用'), ("audio_random_speed", "normal", "enable"))
                        _auto_save(ui.input(label='速度下限', value=get_nested_value(config, "audio_random_speed", "normal", "speed_min")).style("width:100%;").tooltip('音频变速的下限，最终速度会在上下限之间随机一个值进行变速'), ("audio_random_speed", "normal", "speed_min"))
                        _auto_save(ui.input(label='速度上限', value=get_nested_value(config, "audio_random_speed", "normal", "speed_max")).style("width:100%;").tooltip('音频变速的上限，最终速度会在上下限之间随机一个值进行变速'), ("audio_random_speed", "normal", "speed_max"))
                    with ui.column().style("width:100%;"):
                        _auto_save(ui.switch('文案音频变速', value=get_nested_value(config, "audio_random_speed", "copywriting", "enable")).style(switch_internal_css).tooltip('是否启用 针对 文案页音频的音频变速功能。此功能需要安装配置ffmpeg才能使用'), ("audio_random_speed", "copywriting", "enable"))
                        _auto_save(ui.input(label='速度下限', value=get_nested_value(config, "audio_random_speed", "copywriting", "speed_min")).style("width:100%;").tooltip('音频变速的下限，最终速度会在上下限之间随机一个值进行变速'), ("audio_random_speed", "copywriting", "speed_min"))
                        _auto_save(ui.input(label='速度上限', value=get_nested_value(config, "audio_random_speed", "copywriting", "speed_max")).style("width:100%;").tooltip('音频变速的上限，最终速度会在上下限之间随机一个值进行变速'), ("audio_random_speed", "copywriting", "speed_max"))
