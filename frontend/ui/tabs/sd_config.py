# -*- coding: UTF-8 -*-
"""
Stable Diffusion 标签页模块
从 common_config.py 拆分而来

布局规范：
- 设置项用最多 3 列网格排布，组件宽度跟随列宽（width:100%）
- 开关+关联参数 为上下结构单元：开关在上，输入框在下
"""
from nicegui import ui
from typing import Dict, Any, Callable
from frontend.ui.components.config_helper import get_nested_value
from frontend.utils.common import textarea_data_change


def create_sd_config_tab(
    config: Dict[str, Any],
    theme_config: Dict[str, str],
    set_config_callback: Callable
):
    """创建Stable Diffusion标签页"""
    card_css = theme_config.get("card", "")
    switch_internal_css = theme_config.get("switch_internal", "")

    # 导入自动绑定函数
    from frontend.utils.config_auto_save import create_auto_save
    _auto_save = create_auto_save(config, set_config_callback, tab_key="sd_config")

    with ui.card().style(card_css):
        ui.label('Stable Diffusion')
        with ui.grid(columns=3):
            with ui.column().style("width:100%;"):
                _auto_save(ui.select(label='翻译类型', options={'none': '不启用', 'baidu': '百度翻译', 'google': '谷歌翻译'}, value=get_nested_value(config, "sd", "translate_type")).style("width:100%;"), ("sd", "translate_type"))
            _sd_llm_type_val = get_nested_value(config, "sd", "prompt_llm", "type")
            _sd_llm_type_options = {'custom_llm': '自定义LLM'}
            if _sd_llm_type_val and _sd_llm_type_val not in _sd_llm_type_options:
                _sd_llm_type_options[_sd_llm_type_val] = _sd_llm_type_val
            _auto_save(ui.select(label='LLM类型', options=_sd_llm_type_options, value=_sd_llm_type_val).style("width:100%;"), ("sd", "prompt_llm", "type"))
            _auto_save(ui.input(label='提示词前缀', value=get_nested_value(config, "sd", "prompt_llm", "before_prompt"), placeholder='LLM提示词前缀').style("width:100%;"), ("sd", "prompt_llm", "before_prompt"))
            _auto_save(ui.input(label='提示词后缀', value=get_nested_value(config, "sd", "prompt_llm", "after_prompt"), placeholder='LLM提示词后缀').style("width:100%;"), ("sd", "prompt_llm", "after_prompt"))
        with ui.grid(columns=3):
            _auto_save(ui.input(label='弹幕触发前缀', value=get_nested_value(config, "sd", "trigger"), placeholder='触发的关键词').style("width:100%;"), ("sd", "trigger"))
            _auto_save(ui.input(label='IP地址', value=get_nested_value(config, "sd", "ip"), placeholder='服务运行的IP地址').style("width:100%;"), ("sd", "ip"))
            _auto_save(ui.input(label='端口', value=get_nested_value(config, "sd", "port"), placeholder='服务运行的端口').style("width:100%;"), ("sd", "port"))
            _auto_save(ui.input(label='负面提示词', value=get_nested_value(config, "sd", "negative_prompt"), placeholder='负面文本提示').style("width:100%;"), ("sd", "negative_prompt"))
            _auto_save(ui.input(label='随机种子', value=get_nested_value(config, "sd", "seed"), placeholder='随机种子').style("width:100%;"), ("sd", "seed"))
            _auto_save(ui.textarea(label='图像风格', placeholder='样式列表', value=textarea_data_change(get_nested_value(config, "sd", "styles"))).style("width:100%;"), ("sd", "styles"))
        with ui.grid(columns=3):
            _auto_save(ui.input(label='提示词相关性', value=get_nested_value(config, "sd", "cfg_scale"), placeholder='CFG Scale').style("width:100%;"), ("sd", "cfg_scale"))
            _auto_save(ui.input(label='生成图像步数', value=get_nested_value(config, "sd", "steps"), placeholder='Steps').style("width:100%;"), ("sd", "steps"))
            _auto_save(ui.input(label='图像水平像素', value=get_nested_value(config, "sd", "hr_resize_x"), placeholder='水平尺寸').style("width:100%;"), ("sd", "hr_resize_x"))
            _auto_save(ui.input(label='图像垂直像素', value=get_nested_value(config, "sd", "hr_resize_y"), placeholder='垂直尺寸').style("width:100%;"), ("sd", "hr_resize_y"))
            _auto_save(ui.input(label='去噪强度', value=get_nested_value(config, "sd", "denoising_strength"), placeholder='去噪强度').style("width:100%;"), ("sd", "denoising_strength"))
        with ui.grid(columns=3):
            with ui.column().style("width:100%;"):
                _auto_save(ui.switch('高分辨率生成', value=get_nested_value(config, "sd", "enable_hr")).style(switch_internal_css), ("sd", "enable_hr"))
                _auto_save(ui.input(label='高分辨率缩放因子', value=get_nested_value(config, "sd", "hr_scale"), placeholder='缩放因子').style("width:100%;"), ("sd", "hr_scale"))
                _auto_save(ui.input(label='高分生二次传递步数', value=get_nested_value(config, "sd", "hr_second_pass_steps"), placeholder='二次传递步数').style("width:100%;"), ("sd", "hr_second_pass_steps"))
            with ui.column().style("width:100%;"):
                _auto_save(ui.switch('保存图片到本地', value=get_nested_value(config, "sd", "save_enable")).style(switch_internal_css), ("sd", "save_enable"))
                _auto_save(ui.switch('本地图片循环覆盖', value=get_nested_value(config, "sd", "loop_cover")).style(switch_internal_css), ("sd", "loop_cover"))
                _auto_save(ui.input(label='图片保存路径', value=get_nested_value(config, "sd", "save_path"), placeholder='图片保存路径').style("width:100%;"), ("sd", "save_path"))
