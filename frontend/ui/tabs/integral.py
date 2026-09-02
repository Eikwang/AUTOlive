# -*- coding: UTF-8 -*-
"""
积分标签页模块
从 webui-bak.py 的 integral_page 拆分而来

布局规范：
- 设置项用最多 3 列网格排布，组件宽度跟随列宽（width:100%）
"""
from nicegui import ui
from typing import Dict, Any, Callable

from frontend.ui.components.config_helper import get_nested_value


def create_integral_tab(
    config: Dict[str, Any],
    theme_config: Dict[str, str],
    set_config_callback: Callable
):
    """创建积分标签页"""
    card_css = theme_config.get("card", "")
    switch_internal_css = theme_config.get("switch_internal", "")

    # 导入自动绑定函数
    from frontend.utils.config_auto_save import create_auto_save
    _auto_save = create_auto_save(config, set_config_callback, tab_key="integral")

    # 通用
    with ui.card().style(card_css):
        ui.label("通用")
        with ui.grid(columns=3):
            _auto_save(ui.switch('启用', value=get_nested_value(config, "integral", "enable")).style(switch_internal_css), ("integral", "enable"))

    # 签到
    with ui.card().style(card_css):
        ui.label("签到")
        with ui.grid(columns=3):
            with ui.column().style("width:100%;"):
                _auto_save(ui.switch('启用', value=get_nested_value(config, "integral", "sign", "enable")).style(switch_internal_css), ("integral", "sign", "enable"))
                _auto_save(ui.input(label='获得积分数', value=get_nested_value(config, "integral", "sign", "get_integral"), placeholder='签到成功可以获得的积分数，请填写正整数！').style("width:100%;"), ("integral", "sign", "get_integral"))
                _auto_save(ui.textarea(label='命令', value="\n".join(get_nested_value(config, "integral", "sign", "cmd") or []), placeholder='弹幕发送以下命令可以触发签到功能，换行分隔命令').style("width:100%;"), ("integral", "sign", "cmd"))
        with ui.card().style(card_css):
            ui.label("文案")
            for index, integral_sign_copywriting in enumerate(get_nested_value(config, "integral", "sign", "copywriting") or []):
                with ui.grid(columns=3):
                    _auto_save(ui.input(label=f"签到数区间#{index}", value=integral_sign_copywriting.get("sign_num_interval", ""), placeholder='限制在此区间内的签到数来触发对应的文案，用-号来进行区间划分').style("width:100%;"), ("integral", "sign", "copywriting", index, "sign_num_interval"))
                    _auto_save(ui.textarea(label=f"文案#{index}", value="\n".join(integral_sign_copywriting.get("copywriting", [])), placeholder='在此签到区间内，触发的文案内容，换行分隔').style("width:100%;"), ("integral", "sign", "copywriting", index, "copywriting"))

    # 礼物
    with ui.card().style(card_css):
        ui.label("礼物")
        with ui.grid(columns=3):
            with ui.column().style("width:100%;"):
                _auto_save(ui.switch('启用', value=get_nested_value(config, "integral", "gift", "enable")).style(switch_internal_css), ("integral", "gift", "enable"))
                _auto_save(ui.input(label='获得积分比例', value=get_nested_value(config, "integral", "gift", "get_integral_proportion"), placeholder='此比例和礼物真实金额（元）挂钩，默认就是1元=10积分').style("width:100%;"), ("integral", "gift", "get_integral_proportion"))
        with ui.card().style(card_css):
            ui.label("文案")
            for index, integral_gift_copywriting in enumerate(get_nested_value(config, "integral", "gift", "copywriting") or []):
                with ui.grid(columns=3):
                    _auto_save(ui.input(label=f"礼物价格区间#{index}", value=integral_gift_copywriting.get("gift_price_interval", ""), placeholder='限制在此区间内的礼物价格来触发对应的文案，用-号来进行区间划分').style("width:100%;"), ("integral", "gift", "copywriting", index, "gift_price_interval"))
                    _auto_save(ui.textarea(label=f"文案#{index}", value="\n".join(integral_gift_copywriting.get("copywriting", [])), placeholder='在此礼物区间内，触发的文案内容，换行分隔').style("width:100%;"), ("integral", "gift", "copywriting", index, "copywriting"))

    # 入场
    with ui.card().style(card_css):
        ui.label("入场")
        with ui.grid(columns=3):
            with ui.column().style("width:100%;"):
                _auto_save(ui.switch('启用', value=get_nested_value(config, "integral", "entrance", "enable")).style(switch_internal_css), ("integral", "entrance", "enable"))
                _auto_save(ui.input(label='获得积分数', value=get_nested_value(config, "integral", "entrance", "get_integral"), placeholder='签到成功可以获得的积分数，请填写正整数！').style("width:100%;"), ("integral", "entrance", "get_integral"))
        with ui.card().style(card_css):
            ui.label("文案")
            for index, integral_entrance_copywriting in enumerate(get_nested_value(config, "integral", "entrance", "copywriting") or []):
                with ui.grid(columns=3):
                    _auto_save(ui.input(label=f"入场数区间#{index}", value=integral_entrance_copywriting.get("entrance_num_interval", ""), placeholder='限制在此区间内的入场数来触发对应的文案，用-号来进行区间划分').style("width:100%;"), ("integral", "entrance", "copywriting", index, "entrance_num_interval"))
                    _auto_save(ui.textarea(label=f"文案#{index}", value="\n".join(integral_entrance_copywriting.get("copywriting", [])), placeholder='在此入场区间内，触发的文案内容，换行分隔').style("width:100%;"), ("integral", "entrance", "copywriting", index, "copywriting"))

    # 增删改查
    with ui.card().style(card_css):
        ui.label("增删改查")
        with ui.card().style(card_css):
            ui.label("查询")
            with ui.grid(columns=3):
                with ui.column().style("width:100%;"):
                    _auto_save(ui.switch('启用', value=get_nested_value(config, "integral", "crud", "query", "enable")).style(switch_internal_css), ("integral", "crud", "query", "enable"))
                    _auto_save(ui.textarea(label="命令", value="\n".join(get_nested_value(config, "integral", "crud", "query", "cmd") or []), placeholder='弹幕发送以下命令可以触发查询功能，换行分隔命令').style("width:100%;"), ("integral", "crud", "query", "cmd"))
                    _auto_save(ui.textarea(label="文案", value="\n".join(get_nested_value(config, "integral", "crud", "query", "copywriting") or []), placeholder='触发查询功能后返回的文案内容，换行分隔').style("width:100%;"), ("integral", "crud", "query", "copywriting"))
