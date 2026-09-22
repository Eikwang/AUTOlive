# -*- coding: UTF-8 -*-
"""
图像识别标签页模块
从 webui-bak.py 的 image_recognition_page 拆分而来

布局规范：
- 设置项用最多 3 列网格排布，组件宽度跟随列宽（width:100%）
- 开关+关联参数 为上下结构单元：开关在上，输入框在下
"""
from nicegui import ui
from typing import Dict, Any, Callable

from frontend.ui.components.config_helper import get_nested_value


def create_image_recognition_tab(
    config: Dict[str, Any],
    theme_config: Dict[str, str],
    set_config_callback: Callable
):
    """创建图像识别标签页"""
    card_css = theme_config.get("card", "")
    switch_internal_css = theme_config.get("switch_internal", "")
    button_internal_color = theme_config.get("button_internal_color", "primary")
    button_internal_css = theme_config.get("button_internal_css", "")

    # 导入自动绑定函数
    from frontend.utils.config_auto_save import create_auto_save
    _auto_save = create_auto_save(config, set_config_callback, tab_key="image_recognition")

    with ui.card().style(card_css):
        ui.label("通用")
        with ui.grid(columns=3):
            with ui.column().style("width:100%;"):
                _auto_save(ui.switch('启用', value=get_nested_value(config, "image_recognition", "enable")).style(switch_internal_css), ("image_recognition", "enable"))
                _auto_save(ui.select(
                    label='模型',
                    options={'blip': 'blip'},
                    value=get_nested_value(config, "image_recognition", "model")
                ).style("width:100%;"), ("image_recognition", "model"))
            _auto_save(ui.input(label='截图保存路径', value=get_nested_value(config, "image_recognition", "img_save_path"), placeholder='截图保存路径，支持绝对或相对路径').style("width:100%;"), ("image_recognition", "img_save_path"))
            _auto_save(ui.input(label='携带的提示词', value=get_nested_value(config, "image_recognition", "prompt"), placeholder='图片识别时附带的提示词').style("width:100%;"), ("image_recognition", "prompt"))

        with ui.card().style(card_css):
            ui.label("电脑截图")
            with ui.grid(columns=3):
                _window_title_val = get_nested_value(config, "image_recognition", "screenshot_window_title")
                _window_title_options = {}
                if _window_title_val:
                    _window_title_options[str(_window_title_val)] = str(_window_title_val)
                _auto_save(ui.select(
                    label='截图窗口标题',
                    options=_window_title_options,
                    value=_window_title_val
                ).style("width:100%;"), ("image_recognition", "screenshot_window_title"))
                _auto_save(ui.input(label='N秒后进行截图', value=get_nested_value(config, "image_recognition", "screenshot_delay"), placeholder='截图延迟').style("width:100%;"), ("image_recognition", "screenshot_delay"))
                _auto_save(ui.input(label='N秒后自动截图', value=get_nested_value(config, "image_recognition", "loop_screenshot_delay"), placeholder='自动截图延迟').style("width:100%;"), ("image_recognition", "loop_screenshot_delay"))
                with ui.column().style("width:100%;"):
                    _auto_save(ui.switch('循环截图并发送', value=get_nested_value(config, "image_recognition", "loop_screenshot_enable")).style(switch_internal_css), ("image_recognition", "loop_screenshot_enable"))
                    ui.button('截图并发送', color=button_internal_color).style(button_internal_css)

        with ui.card().style(card_css):
            ui.label("摄像头截图")
            with ui.grid(columns=3):
                with ui.column().style("width:100%;"):
                    _auto_save(ui.switch('启用', value=get_nested_value(config, "image_recognition", "cam_screenshot_enable")).style(switch_internal_css), ("image_recognition", "cam_screenshot_enable"))
                    _cam_index_val = get_nested_value(config, "image_recognition", "cam_index")
                    _cam_index_options = {}
                    if _cam_index_val is not None:
                        _cam_index_str = str(_cam_index_val)
                        _cam_index_options[_cam_index_str] = _cam_index_str
                    else:
                        _cam_index_str = None
                    _auto_save(ui.select(
                        label='摄像头索引',
                        options=_cam_index_options,
                        value=_cam_index_str
                    ).style("width:100%;"), ("image_recognition", "cam_index"))
                    _auto_save(ui.input(label='N秒后进行截图', value=get_nested_value(config, "image_recognition", "cam_screenshot_delay"), placeholder='截图延迟').style("width:100%;"), ("image_recognition", "cam_screenshot_delay"))
                    _auto_save(ui.input(label='N秒后自动截图', value=get_nested_value(config, "image_recognition", "loop_cam_screenshot_delay"), placeholder='自动截图延迟').style("width:100%;"), ("image_recognition", "loop_cam_screenshot_delay"))
                with ui.column().style("width:100%;"):
                    _auto_save(ui.switch('循环截图并发送', value=get_nested_value(config, "image_recognition", "loop_cam_screenshot_enable")).style(switch_internal_css), ("image_recognition", "loop_cam_screenshot_enable"))
                    ui.button('截图并发送', color=button_internal_color).style(button_internal_css)

    with ui.card().style(card_css):
        ui.label("Blip")
        with ui.grid(columns=3):
            ui.select(
                label='模型',
                options={
                    'Salesforce/blip-image-captioning-large': 'Salesforce/blip-image-captioning-large',
                    'Salesforce/blip-image-captioning-base': 'Salesforce/blip-image-captioning-base',
                },
                value=get_nested_value(config, "image_recognition", "blip", "model")
            ).style("width:100%;")
