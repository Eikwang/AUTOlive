# -*- coding: UTF-8 -*-
"""
动态文案标签页模块
从 common_config.py 拆分而来

布局规范：
- 设置项用最多 3 列网格排布，组件宽度跟随列宽（width:100%）
- 开关+关联参数 为上下结构单元：开关在上，输入框在下
"""
from nicegui import ui
from typing import Dict, Any, Callable
from frontend.ui.components.config_helper import get_nested_value
from frontend.utils.common import textarea_data_change


def create_trends_copywriting_tab(
    config: Dict[str, Any],
    theme_config: Dict[str, str],
    set_config_callback: Callable
):
    """创建动态文案标签页"""
    card_css = theme_config.get("card", "")
    switch_internal_css = theme_config.get("switch_internal", "")

    # 导入自动绑定函数
    from frontend.utils.config_auto_save import create_auto_save
    _auto_save = create_auto_save(config, set_config_callback, tab_key="trends_copywriting")

    with ui.card().style(card_css):
        ui.label('动态文案')
        with ui.grid(columns=3):
            with ui.column().style("width:100%;"):
                _auto_save(ui.switch('启用', value=get_nested_value(config, "trends_copywriting", "enable")).style(switch_internal_css), ("trends_copywriting", "enable"))
                _llm_type_val = get_nested_value(config, "trends_copywriting", "llm_type")
                _llm_type_options = {'custom_llm': '自定义LLM'}
                if _llm_type_val and _llm_type_val not in _llm_type_options:
                    _llm_type_options[_llm_type_val] = _llm_type_val
                _auto_save(ui.select(label='LLM类型', options=_llm_type_options, value=_llm_type_val).style("width:100%;"), ("trends_copywriting", "llm_type"))
            with ui.column().style("width:100%;"):
                _auto_save(ui.switch('随机播放', value=get_nested_value(config, "trends_copywriting", "random_play")).style(switch_internal_css), ("trends_copywriting", "random_play"))
                _auto_save(ui.input(label='文案播放间隔', value=get_nested_value(config, "trends_copywriting", "play_interval"), placeholder='文案之间的播放间隔时间（秒）').style("width:100%;"), ("trends_copywriting", "play_interval"))

        trends_copywriting_copywriting_var = {}
        trends_copywriting_config_card = ui.card()
        for index, trends_copywriting_copywriting in enumerate(get_nested_value(config, "trends_copywriting", "copywriting") or []):
            with trends_copywriting_config_card.style(card_css):
                with ui.grid(columns=3):
                    trends_copywriting_copywriting_var[str(3 * index)] = ui.input(label=f"文案路径#{index + 1}", value=trends_copywriting_copywriting.get("folder_path", ""), placeholder='文案文件存储的文件夹路径').style("width:100%;")
                    with ui.column().style("width:100%;"):
                        trends_copywriting_copywriting_var[str(3 * index + 1)] = ui.switch(text=f"提示词转换#{index + 1}", value=trends_copywriting_copywriting.get("prompt_change_enable", False))
                        trends_copywriting_copywriting_var[str(3 * index + 2)] = ui.input(label=f"提示词转换内容#{index + 1}", value=trends_copywriting_copywriting.get("prompt_change_content", ""), placeholder='使用此提示词内容对文案内容进行转换后再进行合成').style("width:100%;")
