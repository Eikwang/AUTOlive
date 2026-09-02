# -*- coding: UTF-8 -*-
"""
翻译标签页模块
从 webui-bak.py 的 translate_page 拆分而来

布局规范：
- 设置项用最多 3 列网格排布，组件宽度跟随列宽（width:100%）
- 开关+关联参数 为上下结构单元：开关在上，输入框在下
"""
from nicegui import ui
from typing import Dict, Any, Callable

from frontend.ui.components.config_helper import get_nested_value


def create_translate_tab(
    config: Dict[str, Any],
    theme_config: Dict[str, str],
    set_config_callback: Callable
):
    """创建翻译标签页"""
    card_css = theme_config.get("card", "")
    switch_internal_css = theme_config.get("switch_internal", "")

    # 导入自动绑定函数
    from frontend.utils.config_auto_save import create_auto_save
    _auto_save = create_auto_save(config, set_config_callback, tab_key="translate")

    with ui.grid(columns=3):
        with ui.column().style("width:100%;"):
            _auto_save(ui.switch('启用', value=get_nested_value(config, "translate", "enable")).style(switch_internal_css), ("translate", "enable"))
            _auto_save(ui.select(
                label='类型',
                options={'baidu': '百度翻译', 'google': '谷歌翻译'},
                value=get_nested_value(config, "translate", "type")
            ).style("width:100%;"), ("translate", "type"))
        _auto_save(ui.select(
            label='翻译类型',
            options={'弹幕': '弹幕', '回复': '回复', '弹幕+回复': '弹幕+回复'},
            value=get_nested_value(config, "translate", "trans_type")
        ).style("width:100%;"), ("translate", "trans_type"))

    # 百度翻译
    with ui.card().style(card_css):
        ui.label("百度翻译")
        with ui.grid(columns=3):
            _auto_save(ui.input(label='APP ID', value=get_nested_value(config, "translate", "baidu", "appid"), placeholder='翻译开放平台 开发者中心 APP ID').style("width:100%;"), ("translate", "baidu", "appid"))
            _auto_save(ui.input(label='密钥', value=get_nested_value(config, "translate", "baidu", "appkey"), placeholder='翻译开放平台 开发者中心 密钥').style("width:100%;"), ("translate", "baidu", "appkey"))
            _auto_save(ui.select(
                label='源语言',
                options={'auto': '自动检测', 'zh': '中文', 'cht': '繁体中文', 'en': '英文', 'jp': '日文', 'kor': '韩文', 'yue': '粤语', 'wyw': '文言文'},
                value=get_nested_value(config, "translate", "baidu", "from_lang")
            ).style("width:100%;"), ("translate", "baidu", "from_lang"))
            _auto_save(ui.select(
                label='目标语言',
                options={'zh': '中文', 'cht': '繁体中文', 'en': '英文', 'jp': '日文', 'kor': '韩文', 'yue': '粤语', 'wyw': '文言文'},
                value=get_nested_value(config, "translate", "baidu", "to_lang")
            ).style("width:100%;"), ("translate", "baidu", "to_lang"))

    # 谷歌翻译
    with ui.card().style(card_css):
        ui.label("谷歌翻译")
        with ui.grid(columns=3):
            _auto_save(ui.input(label='代理地址', value=get_nested_value(config, "translate", "google", "proxy"), placeholder='代理的完整地址，请携带协议').style("width:100%;"), ("translate", "google", "proxy"))
            _auto_save(ui.select(
                label='源语言',
                options={'auto': '自动', 'zh-CN': '中文', 'en': '英文', 'ja': '日文'},
                value=get_nested_value(config, "translate", "google", "src_lang")
            ).style("width:100%;"), ("translate", "google", "src_lang"))
            _auto_save(ui.select(
                label='目标语言',
                options={'zh-CN': '中文', 'en': '英文', 'ja': '日文'},
                value=get_nested_value(config, "translate", "google", "tgt_lang")
            ).style("width:100%;"), ("translate", "google", "tgt_lang"))
