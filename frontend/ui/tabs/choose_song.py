# -*- coding: UTF-8 -*-
"""
点歌模式标签页模块
从 common_config.py 拆分而来

布局规范：
- 设置项用最多 3 列网格排布，组件宽度跟随列宽（width:100%）
- 开关+关联参数 为上下结构单元：开关在上，输入框在下
"""
from nicegui import ui
from typing import Dict, Any, Callable
from frontend.ui.components.config_helper import get_nested_value
from frontend.utils.common import textarea_data_change


def create_choose_song_tab(
    config: Dict[str, Any],
    theme_config: Dict[str, str],
    set_config_callback: Callable
):
    """创建点歌模式标签页"""
    card_css = theme_config.get("card", "")
    switch_internal_css = theme_config.get("switch_internal", "")

    # 导入自动绑定函数
    from frontend.utils.config_auto_save import create_auto_save
    _auto_save = create_auto_save(config, set_config_callback, tab_key="choose_song")

    # 点歌模式
    with ui.card().style(card_css):
        ui.label('点歌模式')
        with ui.grid(columns=3):
            with ui.column().style("width:100%;"):
                _auto_save(ui.textarea(label='点歌触发命令', value=textarea_data_change(get_nested_value(config, "choose_song", "start_cmd")), placeholder='点歌触发命令，换行分隔').style("width:100%;"), keys=("choose_song", "start_cmd"))
            _auto_save(ui.textarea(label='取消点歌命令', value=textarea_data_change(get_nested_value(config, "choose_song", "stop_cmd")), placeholder='停止点歌命令，换行分隔').style("width:100%;"), keys=("choose_song", "stop_cmd"))
            _auto_save(ui.textarea(label='随机点歌命令', value=textarea_data_change(get_nested_value(config, "choose_song", "random_cmd")), placeholder='随机点歌命令，换行分隔').style("width:100%;"), keys=("choose_song", "random_cmd"))
        with ui.grid(columns=3):
            _auto_save(ui.input(label='歌曲路径', value=get_nested_value(config, "choose_song", "song_path"), placeholder='歌曲音频存放的路径').style("width:100%;"), keys=("choose_song", "song_path"))
            _auto_save(ui.input(label='匹配失败文案', value=get_nested_value(config, "choose_song", "match_fail_copy"), placeholder='匹配失败返回的音频文案').style("width:100%;"), keys=("choose_song", "match_fail_copy"))
            _auto_save(ui.input(label='匹配最低相似度', value=get_nested_value(config, "choose_song", "similarity"), placeholder='最低音频匹配相似度').style("width:100%;"), keys=("choose_song", "similarity"))
