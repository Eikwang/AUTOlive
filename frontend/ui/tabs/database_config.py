# -*- coding: UTF-8 -*-
"""
数据库设置标签页模块
从 common_config.py 拆分而来

布局规范：
- 设置项用最多 3 列网格排布，组件宽度跟随列宽（width:100%）
- 同类开关可堆叠在一个列单元中
"""
from nicegui import ui
from typing import Dict, Any, Callable

from frontend.ui.components.config_helper import get_nested_value


def create_database_tab(
    config: Dict[str, Any],
    theme_config: Dict[str, str],
    set_config_callback: Callable
):
    """创建数据库设置标签页"""
    card_css = theme_config.get("card", "")
    switch_internal_css = theme_config.get("switch_internal", "")

    # 导入自动绑定函数
    from frontend.utils.config_auto_save import create_auto_save
    _auto_save = create_auto_save(config, set_config_callback, tab_key="database")

    # 数据库（与原文件 webui-bak.py L3652-3659 一致）
    with ui.card().style(card_css):
        ui.label('数据库')
        # UI-16 批次 R4：三个日志开关横向排列（原纵向堆叠）
        with ui.row().style("align-items:center; gap:16px; flex-wrap:wrap;"):
            _auto_save(ui.switch('弹幕日志', value=get_nested_value(config, "database", "comment_enable")).style(switch_internal_css), ("database", "comment_enable"))
            _auto_save(ui.switch('入场日志', value=get_nested_value(config, "database", "entrance_enable")).style(switch_internal_css), ("database", "entrance_enable"))
            _auto_save(ui.switch('礼物日志', value=get_nested_value(config, "database", "gift_enable")).style(switch_internal_css), ("database", "gift_enable"))
        # UI-16 批次 R4：数据库路径系统内置，不再接受自由设置——移除输入框，
        # 改为只读展示当前生效路径（存量自定义路径保持尊重，CEO F-6 可见性；
        # DV-1a：置于原输入框位置，超长省略号+title 悬停全文）
        _db_path = str(get_nested_value(config, "database", "path") or "autolive.db（系统默认）")
        ui.label('数据库路径（系统内置，不可修改）').style(
            'font-size: var(--font-size-assist, 12px); opacity: 0.7;')
        ui.label(_db_path).style(
            'width:100%; max-width:520px; overflow:hidden; text-overflow:ellipsis; '
            'white-space:nowrap;'
        ).tooltip(_db_path)
