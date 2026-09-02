# -*- coding: UTF-8 -*-
"""
过滤标签页模块
从 common_config.py 拆分而来

布局规范：
- 设置项用最多 3 列网格排布，组件宽度跟随列宽（width:100%）
- 开关+关联参数 为上下结构单元：开关在上，输入框在下
"""
from nicegui import ui
from typing import Dict, Any, Callable
from frontend.ui.components.config_helper import get_nested_value
from frontend.utils.common import textarea_data_change


def create_filter_config_tab(
    config: Dict[str, Any],
    theme_config: Dict[str, str],
    set_config_callback: Callable
):
    """创建过滤标签页"""
    card_css = theme_config.get("card", "")
    switch_internal_css = theme_config.get("switch_internal", "")

    # 导入自动绑定函数
    from frontend.utils.config_auto_save import create_auto_save
    _auto_save = create_auto_save(config, set_config_callback, tab_key="filter_config")

    if get_nested_value(config, "webui", "show_card", "common_config", "filter"):
        with ui.card().style(card_css):
            ui.label('过滤')
            # 弹幕/LLM 前后缀过滤（3列网格）
            with ui.grid(columns=3):
                _auto_save(ui.textarea(label='弹幕触发前缀', placeholder='前缀必须携带其中任一字符串才能触发\n例如：配置#，那么这个会触发：#你好', value=textarea_data_change(get_nested_value(config, "filter", "before_must_str"))).style("width:100%;").tooltip("前缀必须携带其中任一字符串才能触发\n例如：配置#，那么这个会触发：#你好"), keys=("filter", "before_must_str"))
                _auto_save(ui.textarea(label='弹幕触发后缀', placeholder='后缀必须携带其中任一字符串才能触发\n例如：配置。那么这个会触发：你好。', value=textarea_data_change(get_nested_value(config, "filter", "after_must_str"))).style("width:100%;").tooltip("后缀必须携带其中任一字符串才能触发\n例如：配置。那么这个会触发：你好。"), keys=("filter", "after_must_str"))
                _auto_save(ui.textarea(label='弹幕过滤前缀', placeholder='当前缀为其中任一字符串时，弹幕会被过滤\n例如：配置#，那么这个会被过滤：#你好', value=textarea_data_change(get_nested_value(config, "filter", "before_filter_str"))).style("width:100%;").tooltip("当前缀为其中任一字符串时，弹幕会被过滤\n例如：配置#，那么这个会被过滤：#你好"), keys=("filter", "before_filter_str"))
                _auto_save(ui.textarea(label='弹幕过滤后缀', placeholder='当后缀为其中任一字符串时，弹幕会被过滤\n例如：配置#，那么这个会被过滤：你好#', value=textarea_data_change(get_nested_value(config, "filter", "after_filter_str"))).style("width:100%;").tooltip("当后缀为其中任一字符串时，弹幕会被过滤\n例如：配置#，那么这个会被过滤：你好#"), keys=("filter", "after_filter_str"))
                _auto_save(ui.textarea(label='LLM触发前缀', placeholder='前缀必须携带其中任一字符串才能触发LLM\n例如：配置#，那么这个会触发：#你好', value=textarea_data_change(get_nested_value(config, "filter", "before_must_str_for_llm"))).style("width:100%;").tooltip("前缀必须携带其中任一字符串才能触发LLM\n例如：配置#，那么这个会触发：#你好"), keys=("filter", "before_must_str_for_llm"))
                _auto_save(ui.textarea(label='LLM触发后缀', placeholder='后缀必须携带其中任一字符串才能触发LLM\n例如：配置。那么这个会触发：你好。', value=textarea_data_change(get_nested_value(config, "filter", "after_must_str_for_llm"))).style("width:100%;").tooltip('后缀必须携带其中任一字符串才能触发LLM\n例如：配置。那么这个会触发：你好。'), keys=("filter", "after_must_str_for_llm"))

            # 长度限制与独立开关（上下结构单元）
            with ui.grid(columns=3):
                _auto_save(ui.input(label='最大单词数', placeholder='最长阅读的英文单词数（空格分隔）', value=get_nested_value(config, "filter", "max_len")).style("width:100%;").tooltip('最长阅读的英文单词数（空格分隔）'), keys=("filter", "max_len"))
                _auto_save(ui.input(label='最大字符数', placeholder='最长阅读的字符数，双重过滤，避免溢出', value=get_nested_value(config, "filter", "max_char_len")).style("width:100%;").tooltip('最长阅读的字符数，双重过滤，避免溢出'), keys=("filter", "max_char_len"))
                with ui.column().style("width:100%;"):
                    _auto_save(ui.switch('用户名中的数字转中文', value=get_nested_value(config, "filter", "username_convert_digits_to_chinese")).style(switch_internal_css).tooltip('用户名中的数字转中文'), keys=("filter", "username_convert_digits_to_chinese"))
                    _auto_save(ui.switch('弹幕表情过滤', value=get_nested_value(config, "filter", "emoji")).style(switch_internal_css), keys=("filter", "emoji"))

            # 违禁词过滤组（开关在上，关联参数在下）
            with ui.grid(columns=3):
                with ui.column().style("width:100%;"):
                    _auto_save(ui.switch('违禁词过滤', value=get_nested_value(config, "filter", "badwords", "enable")).style(switch_internal_css), keys=("filter", "badwords", "enable"))
                    _auto_save(ui.input(label='违禁词路径', value=get_nested_value(config, "filter", "badwords", "path"), placeholder='本地违禁词数据路径（你如果不需要，可以清空文件内容）').style("width:100%;").tooltip('本地违禁词数据路径（你如果不需要，可以清空文件内容）'), keys=("filter", "badwords", "path"))
                    _auto_save(ui.input(label='违禁拼音路径', value=get_nested_value(config, "filter", "badwords", "bad_pinyin_path"), placeholder='本地违禁拼音数据路径（你如果不需要，可以清空文件内容）').style("width:100%;").tooltip('本地违禁拼音数据路径（你如果不需要，可以清空文件内容）'), keys=("filter", "badwords", "bad_pinyin_path"))
                    _auto_save(ui.input(label='违禁词替换', value=get_nested_value(config, "filter", "badwords", "replace"), placeholder='在不丢弃违禁语句的前提下，将违禁词替换成此项的文本').style("width:100%;").tooltip('在不丢弃违禁语句的前提下，将违禁词替换成此项的文本'), keys=("filter", "badwords", "replace"))
                with ui.column().style("width:100%;"):
                    _auto_save(ui.switch('违禁语句丢弃', value=get_nested_value(config, "filter", "badwords", "discard")).style(switch_internal_css), keys=("filter", "badwords", "discard"))
