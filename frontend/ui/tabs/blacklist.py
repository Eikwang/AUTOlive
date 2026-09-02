# -*- coding: UTF-8 -*-
"""
弹幕黑名单标签页模块
从 common_config.py 拆分而来

布局规范：
- 设置项用最多 3 列网格排布，组件宽度跟随列宽（width:100%）
- 开关+关联参数 为上下结构单元：开关在上，输入框在下
"""
from nicegui import ui
from typing import Dict, Any, Callable

from frontend.ui.components.config_helper import get_nested_value


def _textarea_data_change(data):
    """字符串数组数据格式转换"""
    if data is None:
        return ""
    tmp_str = ""
    for tmp in data:
        tmp_str = tmp_str + tmp + chr(10)
    return tmp_str


def create_blacklist_tab(
    config: Dict[str, Any],
    theme_config: Dict[str, str],
    set_config_callback: Callable
):
    """创建弹幕黑名单标签页"""
    card_css = theme_config.get("card", "")
    switch_internal_css = theme_config.get("switch_internal", "")

    # 导入自动绑定函数
    from frontend.utils.config_auto_save import create_auto_save
    _auto_save = create_auto_save(config, set_config_callback, tab_key="blacklist")

    # 弹幕黑名单（与原文件 L762-767 一致）
    with ui.card().style(card_css):
        ui.label('弹幕黑名单')
        with ui.expansion('弹幕黑名单', icon="settings", value=True).classes('w-full'):
            with ui.grid(columns=1):
                with ui.column().style("width:100%; gap:0;"):
                    _auto_save(ui.switch('启用', value=get_nested_value(config, "filter", "blacklist", "enable")).style(switch_internal_css), ("filter", "blacklist", "enable"))
                    _auto_save(ui.textarea(label='用户名 黑名单', value=_textarea_data_change(get_nested_value(config, "filter", "blacklist", "username")), placeholder='屏蔽此名单内所有用户的弹幕，用户名以换行分隔').style("width:100%;"), ("filter", "blacklist", "username"))
