# -*- coding: UTF-8 -*-
"""
按键映射标签页模块
从 common_config.py 拆分而来

布局规范：
- 设置项用最多 3 列网格排布，组件宽度跟随列宽（width:100%）
- 开关+关联参数 为上下结构单元：开关在上，输入框在下
"""
from nicegui import ui
from typing import Dict, Any, Callable
from frontend.ui.components.config_helper import get_nested_value
from frontend.utils.common import textarea_data_change


def create_key_mapping_tab(
    config: Dict[str, Any],
    theme_config: Dict[str, str],
    set_config_callback: Callable
):
    """创建按键映射标签页"""
    card_css = theme_config.get("card", "")
    switch_internal_css = theme_config.get("switch_internal", "")

    # 导入自动绑定函数
    from frontend.utils.config_auto_save import create_auto_save
    _auto_save = create_auto_save(config, set_config_callback, tab_key="key_mapping")

    # 按键/文案/音频/串口/图片 映射
    if get_nested_value(config, "webui", "show_card", "common_config", "key_mapping"):
        with ui.card().style(card_css):
            ui.label('按键/文案/音频/串口/图片 映射')
            with ui.grid(columns=3):
                with ui.column().style("width:100%;"):
                    _auto_save(ui.switch('启用', value=get_nested_value(config, "key_mapping", "enable")).style(switch_internal_css), keys=("key_mapping", "enable"))
                    _auto_save(ui.input(label='命令前缀', value=get_nested_value(config, "key_mapping", "start_cmd"), placeholder='命令起始字符串').style("width:100%;"), keys=("key_mapping", "start_cmd"))
                _auto_save(ui.select(label='捕获类型', options={'弹幕': '弹幕', '回复': '回复', '弹幕+回复': '弹幕+回复'}, value=get_nested_value(config, "key_mapping", "type")).style("width:100%;"), keys=("key_mapping", "type"))
            with ui.grid(columns=3):
                _auto_save(ui.select(label='按键触发类型', options={'不启用': '不启用', '关键词': '关键词', '礼物': '礼物', '关键词+礼物': '关键词+礼物'}, value=get_nested_value(config, "key_mapping", "key_trigger_type")).style("width:100%;"), keys=("key_mapping", "key_trigger_type"))
                _auto_save(ui.select(label='文案触发类型', options={'不启用': '不启用', '关键词': '关键词', '礼物': '礼物', '关键词+礼物': '关键词+礼物'}, value=get_nested_value(config, "key_mapping", "copywriting_trigger_type")).style("width:100%;"), keys=("key_mapping", "copywriting_trigger_type"))
                _auto_save(ui.select(label='本地音频触发类型', options={'不启用': '不启用', '关键词': '关键词', '礼物': '礼物', '关键词+礼物': '关键词+礼物'}, value=get_nested_value(config, "key_mapping", "local_audio_trigger_type")).style("width:100%;"), keys=("key_mapping", "local_audio_trigger_type"))
                _auto_save(ui.select(label='串口触发类型', options={'不启用': '不启用', '关键词': '关键词', '礼物': '礼物', '关键词+礼物': '关键词+礼物'}, value=get_nested_value(config, "key_mapping", "serial_trigger_type")).style("width:100%;"), keys=("key_mapping", "serial_trigger_type"))
                _auto_save(ui.select(label='图片触发类型', options={'不启用': '不启用', '关键词': '关键词', '礼物': '礼物', '关键词+礼物': '关键词+礼物'}, value=get_nested_value(config, "key_mapping", "img_path_trigger_type")).style("width:100%;"), keys=("key_mapping", "img_path_trigger_type"))

            key_mapping_config_var = {}
            key_mapping_config_card = ui.card()
            for index, key_mapping_config in enumerate(get_nested_value(config, "key_mapping", "config") or []):
                with key_mapping_config_card.style(card_css):
                    with ui.grid(columns=3):
                        num = 9
                        key_mapping_config_var[str(num * index)] = ui.textarea(label=f"关键词#{index + 1}", value=textarea_data_change(key_mapping_config.get("keywords", [])), placeholder='触发的关键词').style("width:100%;")
                        key_mapping_config_var[str(num * index + 1)] = ui.textarea(label=f"礼物#{index + 1}", value=textarea_data_change(key_mapping_config.get("gift", [])), placeholder='触发的礼物名').style("width:100%;")
                        key_mapping_config_var[str(num * index + 2)] = ui.textarea(label=f"按键#{index + 1}", value=textarea_data_change(key_mapping_config.get("keys", [])), placeholder='映射的按键').style("width:100%;")
                        key_mapping_config_var[str(num * index + 3)] = ui.input(label=f"相似度#{index + 1}", value=key_mapping_config.get("similarity", 1), placeholder='相似度').style("width:100%;")
                        key_mapping_config_var[str(num * index + 4)] = ui.textarea(label=f"文案#{index + 1}", value=textarea_data_change(key_mapping_config.get("copywriting", [])), placeholder='触发后合成的文案').style("width:100%;")
                        key_mapping_config_var[str(num * index + 5)] = ui.textarea(label=f"本地音频#{index + 1}", value=textarea_data_change(key_mapping_config.get("local_audio", [])), placeholder='触发后播放的本地音频路径').style("width:100%;")
                        key_mapping_config_var[str(num * index + 6)] = ui.input(label=f"串口名#{index + 1}", value=key_mapping_config.get("serial_name", ""), placeholder='例如：COM1').style("width:100%;")
                        key_mapping_config_var[str(num * index + 7)] = ui.textarea(label=f"串口发送内容#{index + 1}", value=textarea_data_change(key_mapping_config.get("serial_send_data", [])), placeholder='发送到串口的数据').style("width:100%;")
                        key_mapping_config_var[str(num * index + 8)] = ui.textarea(label=f"图片路径#{index + 1}", value=textarea_data_change(key_mapping_config.get("img_path", [])), placeholder='图片路径').style("width:100%;")
