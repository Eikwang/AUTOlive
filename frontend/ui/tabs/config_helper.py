"""
UI配置提取辅助模块
用于从webui-bak.py提取配置UI元素
"""
from nicegui import ui
from typing import Dict, Any, Callable, Optional, List
import re

from frontend.ui.components import FormField
from frontend.utils.common import is_url_check, textarea_data_change


def extract_config_ui(
    config: Dict[str, Any],
    theme_config: Dict[str, str],
    set_config_callback: Callable,
    section_name: str,
    config_path: List[str],
    ui_elements: List[Dict[str, Any]]
):
    """
    提取配置UI元素
    
    Args:
        config: 配置字典
        theme_config: 主题配置
        set_config_callback: 配置设置回调函数
        section_name: 区域名称
        config_path: 配置路径
        ui_elements: UI元素配置列表
    """
    card_css = theme_config.get("card", "")
    switch_internal_css = theme_config.get("switch_internal", "")
    
    with ui.card().style(card_css):
        ui.label(section_name)
        
        for element in ui_elements:
            element_type = element.get("type")
            label = element.get("label", "")
            key = element.get("key", "")
            placeholder = element.get("placeholder", "")
            tooltip = element.get("tooltip", "")
            options = element.get("options", {})
            style = element.get("style", "width:100%;")
            
            # 构建配置路径
            full_path = config_path + [key]
            
            if element_type == "switch":
                ui.switch(
                    label,
                    value=config.get(*full_path),
                    on_change=lambda e, path=full_path: set_config_callback(*path, e.value)
                ).style(switch_internal_css).tooltip(tooltip)
            
            elif element_type == "input":
                FormField.create_input(
                    label=label,
                    value=config.get(*full_path),
                    placeholder=placeholder,
                    on_change=lambda e, path=full_path: set_config_callback(*path, e.value),
                    tooltip=tooltip,
                    style=style
                )
            
            elif element_type == "select":
                FormField.create_select(
                    label=label,
                    options=options,
                    value=config.get(*full_path),
                    on_change=lambda e, path=full_path: set_config_callback(*path, e.value),
                    tooltip=tooltip,
                    style=style
                )
            
            elif element_type == "textarea":
                FormField.create_textarea(
                    label=label,
                    value=textarea_data_change(config.get(*full_path)),
                    placeholder=placeholder,
                    on_change=lambda e, path=full_path: set_config_callback(*path, e.value.split('\n')),
                    tooltip=tooltip,
                    style=style
                )