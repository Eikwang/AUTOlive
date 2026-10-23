# -*- coding: UTF-8 -*-
"""
消息遗忘&保留标签页模块
从 common_config.py 拆分而来

布局规范：
- 设置项用最多 3 列网格排布，组件宽度跟随列宽（width:100%）

D5 参数页重组（2026-10-19 切片 3）：
- 7 类消息的 遗忘间隔/保留数 由 7×2 平铺输入改为 7 行表格（行=消息类型，
  列=遗忘间隔/保留数），键名与 _auto_save 绑定不变
- 移除图像识别行（uc3 裁决随模块退役；config 残留键归收尾迁移清单）
"""
from nicegui import ui
from typing import Dict, Any, Callable
from frontend.ui.components.config_helper import get_nested_value

# 7 类消息（键前缀 → 行名）；顺序=表格行序
FORGET_ROWS = [
    ("comment", "弹幕"),
    ("gift", "礼物"),
    ("entrance", "入场"),
    ("follow", "关注"),
    ("talk", "聊天"),
    ("schedule", "定时"),
    ("idle_time_task", "闲时任务"),
]


def create_filter_forget_tab(
    config: Dict[str, Any],
    theme_config: Dict[str, str],
    set_config_callback: Callable
):
    """创建消息遗忘&保留标签页"""
    card_css = theme_config.get("card", "")

    # 导入自动绑定函数
    from frontend.utils.config_auto_save import create_auto_save
    _auto_save = create_auto_save(config, set_config_callback, tab_key="filter_forget")

    with ui.card().style(card_css):
        with ui.expansion('消息遗忘&保留设置', icon="settings", value=True).classes('w-full'):
            with ui.element('div').classes('p-2 hint-callout'):
                ui.label("遗忘间隔 指的是每隔这个间隔时间（秒），就会丢弃这个间隔时间中接收到的数据，但会保留最新的n个数据；保留数 指的是保留最新收到的数据的数量")

            # 7 行表格：行=消息类型，列=遗忘间隔/保留数（D5）
            with ui.grid(columns=3).style("width:100%;align-items:center"):
                ui.label('消息类型').style("font-weight: var(--font-weight-title, 600);")
                ui.label('遗忘间隔（秒）').style("font-weight: var(--font-weight-title, 600);")
                ui.label('保留数').style("font-weight: var(--font-weight-title, 600);")
                for prefix, row_name in FORGET_ROWS:
                    ui.label(row_name).style("width:100%;")
                    _auto_save(ui.input(
                        label='遗忘间隔',
                        placeholder='例：1',
                        value=get_nested_value(config, "filter", f"{prefix}_forget_duration")
                    ).style("width:100%;").tooltip('指的是每隔这个间隔时间（秒），就会丢弃这个间隔时间中接收到的数据，\n保留数据在保留数列中可以自定义'), keys=("filter", f"{prefix}_forget_duration"))
                    _auto_save(ui.input(label='保留数', placeholder='保留最新收到的数据的数量', value=get_nested_value(config, "filter", f"{prefix}_forget_reserve_num")).style("width:100%;").tooltip('保留最新收到的数据的数量'), keys=("filter", f"{prefix}_forget_reserve_num"))

            # uc3 裁决移除项说明（图像识别不再提供配置入口）
            ui.label("图像识别一行已随模块退役移除（config 残留键随收尾迁移清理）").style(
                "font-size: var(--font-size-assist, 12px); color: var(--text-hint, #9ca3af);")
