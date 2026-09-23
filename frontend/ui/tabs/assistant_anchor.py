# -*- coding: UTF-8 -*-
"""
助播标签页模块
从 webui-bak.py 的 assistant_anchor_page 拆分而来

布局规范：
- 设置项用最多 3 列网格排布，组件宽度跟随列宽（width:100%）
- 开关+关联参数 为上下结构单元：开关在上，输入框在下
"""
from nicegui import ui
from typing import Dict, Any, Callable

from frontend.ui.components.config_helper import get_nested_value


def create_assistant_anchor_tab(
    config: Dict[str, Any],
    theme_config: Dict[str, str],
    set_config_callback: Callable
):
    """创建助播标签页"""
    card_css = theme_config.get("card", "")
    switch_internal_css = theme_config.get("switch_internal", "")

    # 导入自动绑定函数
    from frontend.utils.config_auto_save import create_auto_save
    _auto_save = create_auto_save(config, set_config_callback, tab_key="assistant_anchor")

    # 音频合成类型选项
    audio_synthesis_type_options = {
        'gpt_sovits': 'GPT_SoVITS',
    }

    with ui.grid(columns=3):
        with ui.column().style("width:100%;"):
            _auto_save(ui.input(label='助播名', value=get_nested_value(config, "assistant_anchor", "username"), placeholder='助播的用户名').style("width:100%;"), ("assistant_anchor", "username"))
        _audio_syn_val = get_nested_value(config, "assistant_anchor", "audio_synthesis_type")
        _audio_syn_options = dict(audio_synthesis_type_options)
        if _audio_syn_val and _audio_syn_val not in _audio_syn_options:
            _audio_syn_options[_audio_syn_val] = _audio_syn_val
        _auto_save(ui.select(
            label='语音合成',
            options=_audio_syn_options,
            value=_audio_syn_val
        ).style("width:100%;"), ("assistant_anchor", "audio_synthesis_type"))

    # 触发类型
    with ui.card().style(card_css):
        ui.label("触发类型")
        with ui.row():
            assistant_anchor_type_list = [
                "comment", "local_qa_audio", "song", "reread", "read_comment", "gift",
                "entrance", "follow", "idle_time_task", "reread_top_priority", "schedule",
                "image_recognition_schedule", "key_mapping", "integral"
            ]
            assistant_anchor_type_mapping = {
                "comment": "弹幕", "local_qa_audio": "本地问答-音频", "song": "点歌",
                "reread": "复读", "read_comment": "念弹幕", "gift": "礼物",
                "entrance": "入场", "follow": "关注", "idle_time_task": "闲时任务",
                "reread_top_priority": "最高优先级-复读", "schedule": "定时任务",
                "image_recognition_schedule": "图像识别定时任务", "key_mapping": "按键映射",
                "integral": "积分",
            }
            for anchor_type in assistant_anchor_type_list:
                ui.checkbox(
                    text=assistant_anchor_type_mapping[anchor_type],
                    value=anchor_type in (get_nested_value(config, "assistant_anchor", "type") or [])
                )

    # 文本匹配
    with ui.grid(columns=3):
        with ui.column().style("width:100%;"):
            _auto_save(ui.switch('启用文本匹配', value=get_nested_value(config, "assistant_anchor", "local_qa", "text", "enable")).style(switch_internal_css), ("assistant_anchor", "local_qa", "text", "enable"))
            _auto_save(ui.select(
                label='存储格式',
                options={'json': '自定义json', 'text': '一问一答'},
                value=get_nested_value(config, "assistant_anchor", "local_qa", "text", "format")
            ).style("width:100%;"), ("assistant_anchor", "local_qa", "text", "format"))
            _auto_save(ui.input(label='文本问答数据路径', value=get_nested_value(config, "assistant_anchor", "local_qa", "text", "file_path"), placeholder='本地问答文本数据存储路径').style("width:100%;"), ("assistant_anchor", "local_qa", "text", "file_path"))
            _auto_save(ui.input(label='文本最低相似度', value=get_nested_value(config, "assistant_anchor", "local_qa", "text", "similarity"), placeholder='最低文本匹配相似度').style("width:100%;"), ("assistant_anchor", "local_qa", "text", "similarity"))

    # 音频匹配
    with ui.grid(columns=3):
        with ui.column().style("width:100%;"):
            _auto_save(ui.switch('启用音频匹配', value=get_nested_value(config, "assistant_anchor", "local_qa", "audio", "enable")).style(switch_internal_css), ("assistant_anchor", "local_qa", "audio", "enable"))
            _auto_save(ui.select(
                label='匹配算法',
                options={'包含关系': '包含关系', '相似度匹配': '相似度匹配'},
                value=get_nested_value(config, "assistant_anchor", "local_qa", "audio", "type")
            ).style("width:100%;"), ("assistant_anchor", "local_qa", "audio", "type"))
            _auto_save(ui.input(label='音频存储路径', value=get_nested_value(config, "assistant_anchor", "local_qa", "audio", "file_path"), placeholder='本地问答音频文件存储路径').style("width:100%;"), ("assistant_anchor", "local_qa", "audio", "file_path"))
            _auto_save(ui.input(label='音频最低相似度', value=get_nested_value(config, "assistant_anchor", "local_qa", "audio", "similarity"), placeholder='最低音频匹配相似度').style("width:100%;"), ("assistant_anchor", "local_qa", "audio", "similarity"))
