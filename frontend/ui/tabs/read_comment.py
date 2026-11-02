# -*- coding: UTF-8 -*-
"""
念弹幕标签页模块
从 common_config.py 拆分而来

布局规范：
- 设置项用最多 3 列网格排布，组件宽度跟随列宽（width:100%）
- 开关+关联参数 为上下结构单元：开关在上，输入框在下
"""
from nicegui import ui
from typing import Dict, Any, Callable
from frontend.ui.components.config_helper import get_nested_value
from frontend.utils.common import textarea_data_change


def create_read_comment_tab(
    config: Dict[str, Any],
    theme_config: Dict[str, str],
    set_config_callback: Callable
):
    """创建念弹幕标签页"""
    card_css = theme_config.get("card", "")
    switch_internal_css = theme_config.get("switch_internal", "")

    # 导入自动绑定函数
    from frontend.utils.config_auto_save import create_auto_save
    _auto_save = create_auto_save(config, set_config_callback, tab_key="read_comment")

    with ui.card().style(card_css):
        ui.label('念弹幕')
        with ui.grid(columns=3):
            with ui.column().style("width:100%;"):
                _auto_save(ui.switch('念用户名', value=get_nested_value(config, "read_comment", "read_username_enable")).style(switch_internal_css), keys=("read_comment", "read_username_enable"))
                _auto_save(ui.input(label='用户名最大长度', value=get_nested_value(config, "read_comment", "username_max_len"), placeholder='需要保留的用户名的最大长度，超出部分将被丢弃').style("width:100%;").tooltip('需要保留的用户名的最大长度，超出部分将被丢弃'), keys=("read_comment", "username_max_len"))
            # 「变声」开关已退役（/autoplan 2026-10-22 批次 A-6：变声=独立 rvc-vc
            # 流程，主流程念弹幕音频直通播放，不再经变声环节）
        with ui.grid(columns=3):
            _auto_save(ui.textarea(
                label='念用户名文案',
                placeholder='念用户名时使用的文案，可以自定义编辑多个（换行分隔），实际中会随机一个使用',
                value=textarea_data_change(get_nested_value(config, "read_comment", "read_username_copywriting"))
            ).style("width:100%;").tooltip('念用户名时使用的文案，可以自定义编辑多个（换行分隔），实际中会随机一个使用，{username} 为用户名占位符'), keys=("read_comment", "read_username_copywriting"))
        # 周期性触发已退役（2026-10-31 念弹幕审查批次）：系统已有限入/去重/
        # 优先级队列，念弹幕在弹幕处理时即时入队（定时回放机制删除）
