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
            ).style("width:100%;").tooltip('念用户名时使用的文案，可以自定义编辑多个（换行分隔），实际中会随机一个使用'), keys=("read_comment", "read_username_copywriting"))
        with ui.grid(columns=3):
            # UI-16 批次 R10/DV-1e/DV-1g：触发周期 min/max 合并为单参数「触发周期」
            # （固定单值语义，后端 timer_handler 直接使用该值不再区间随机；新键
            # periodic_time 由 settings.py 存量迁移=max 回填；行为变更已告知）。
            # 纵向堆叠（周期在上，含单位标注）：触发周期→触发次数最小值→触发次数最大值
            with ui.column().style("width:100%;"):
                _auto_save(ui.switch('周期性触发启用', value=get_nested_value(config, "read_comment", "periodic_trigger", "enable")).style(switch_internal_css), keys=("read_comment", "periodic_trigger", "enable"))
                _auto_save(ui.input(
                    label='触发周期（秒）',
                    value=get_nested_value(config, "read_comment", "periodic_trigger", "periodic_time"),
                    placeholder='例如：60'
                ).style("width:100%;").tooltip('每隔这个周期的时间会触发n次此功能（固定单值，不再区间随机；正整数）'), keys=("read_comment", "periodic_trigger", "periodic_time"))
            _auto_save(ui.input(
                label='触发次数最小值',
                value=get_nested_value(config, "read_comment", "periodic_trigger", "trigger_num_min"),
                placeholder='例如：0'
            ).style("width:100%;").tooltip('周期到后，会触发n次此功能，次数从最大最小值之间随机生成'), keys=("read_comment", "periodic_trigger", "trigger_num_min"))
            _auto_save(ui.input(
                label='触发次数最大值',
                value=get_nested_value(config, "read_comment", "periodic_trigger", "trigger_num_max"),
                placeholder='例如：1'
            ).style("width:100%;").tooltip('周期到后，会触发n次此功能，次数从最大最小值之间随机生成'), keys=("read_comment", "periodic_trigger", "trigger_num_max"))
