# -*- coding: UTF-8 -*-
"""
积分标签页模块
从 webui-bak.py 的 integral_page 拆分而来

布局规范：
- 设置项用最多 3 列网格排布，组件宽度跟随列宽（width:100%）

2026-10-31 积分审查批次：
- 「通用」卡补 integral.enable 总开关（此前空壳，总开关无处渲染）
- 补「关注」配置卡（后端 integral.follow.* 键此前无 UI，靠缺省值运行）
- 查询卡补「无记录文案」（后端 integral.crud.query.no_record_copywriting）
- 全部 textarea 补 split_lines=True——此前 UI 保存把文案/命令词列表污染成
  整串字符串（后端 random.choice 按字符迭代、cmd 匹配退化为子串语义）
- 签到/入场卡标注 12h 隐形限频说明（机制本身无配置项）

2026-11-17 积分播报批次：
- 签到/礼物/入场/关注四模块卡各补「播报」开关（integral.<module>.broadcast，
  默认关=保持现状只写库不播报）与「播报文案」输入框（integral.<module>.broadcast_copy，
  支持 {username}/{integral} 变量，{integral}=本次累计后的当前总积分）
"""
from nicegui import ui
from typing import Dict, Any, Callable

from frontend.ui.components.config_helper import get_nested_value
from frontend.utils.common import textarea_data_change


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

    # 通用（2026-11-17 一致性批次：页内"启用"开关移除——二级菜单侧边栏总开关
    # （frontend/config/enable_paths.py "integral" → integral.enable）承接，
    # 消除同键双开关漂移；本页仅保留子开关与参数）

    # 签到
    with ui.card().style(card_css):
        ui.label("签到")
        with ui.grid(columns=3):
            with ui.column().style("width:100%;"):
                _auto_save(ui.switch('启用', value=get_nested_value(config, "integral", "sign", "enable")).style(switch_internal_css), ("integral", "sign", "enable"))
                _auto_save(ui.input(label='获得积分数', value=get_nested_value(config, "integral", "sign", "get_integral"), placeholder='签到成功可以获得的积分数，请填写正整数！').style("width:100%;"), ("integral", "sign", "get_integral"))
                _auto_save(ui.switch('播报', value=bool(get_nested_value(config, "integral", "sign", "broadcast"))).style(switch_internal_css).tooltip('开启后用户签到积分累计成功时，按下方播报文案进行语音播报；关闭则保持现状只计分不播报（默认关）'), ("integral", "sign", "broadcast"))
                _auto_save(ui.input(label='播报文案', value=get_nested_value(config, "integral", "sign", "broadcast_copy") or "", placeholder='签到积分累计成功时的播报文案，如：感谢 {username} 签到，积分+1').style("width:100%;").tooltip('仅「播报」开启时生效；支持变量：{username}=用户名（数字自动转中文，口径同回复文案）、{integral}=本次累计后的当前总积分'), ("integral", "sign", "broadcast_copy"))
                _auto_save(ui.textarea(label='命令', value=textarea_data_change(get_nested_value(config, "integral", "sign", "cmd") or []), placeholder='弹幕发送以下命令可以触发签到功能，换行分隔命令').style("width:100%;"), ("integral", "sign", "cmd"), split_lines=True)
        ui.label('隐形限制：每 24 小时最多计分一次，窗口内重复签到不计分不回复（防刷分，无需配置）').style("opacity:.6;font-size:12px;")
        with ui.card().style(card_css):
            ui.label("文案")
            for index, integral_sign_copywriting in enumerate(get_nested_value(config, "integral", "sign", "copywriting") or []):
                with ui.grid(columns=3):
                    _auto_save(ui.input(label=f"签到数区间#{index}", value=integral_sign_copywriting.get("sign_num_interval", ""), placeholder='限制在此区间内的签到数来触发对应的文案，用-号来进行区间划分').style("width:100%;"), ("integral", "sign", "copywriting", index, "sign_num_interval"))
                    _auto_save(ui.textarea(label=f"文案#{index}", value=textarea_data_change(integral_sign_copywriting.get("copywriting", [])), placeholder='在此签到区间内，触发的文案内容，换行分隔').style("width:100%;"), ("integral", "sign", "copywriting", index, "copywriting"), split_lines=True)

    # 礼物
    with ui.card().style(card_css):
        ui.label("礼物")
        with ui.grid(columns=3):
            with ui.column().style("width:100%;"):
                _auto_save(ui.switch('启用', value=get_nested_value(config, "integral", "gift", "enable")).style(switch_internal_css), ("integral", "gift", "enable"))
                _auto_save(ui.input(label='获得积分比例', value=get_nested_value(config, "integral", "gift", "get_integral_proportion"), placeholder='此比例和礼物真实金额（元）挂钩，默认就是1元=10积分').style("width:100%;"), ("integral", "gift", "get_integral_proportion"))
                _auto_save(ui.switch('播报', value=bool(get_nested_value(config, "integral", "gift", "broadcast"))).style(switch_internal_css).tooltip('开启后用户礼物积分累计成功时，按下方播报文案进行语音播报；关闭则保持现状只计分不播报（默认关）'), ("integral", "gift", "broadcast"))
                _auto_save(ui.input(label='播报文案', value=get_nested_value(config, "integral", "gift", "broadcast_copy") or "", placeholder='礼物积分累计成功时的播报文案，如：{username} 的礼物已记入积分，当前 {integral} 分').style("width:100%;").tooltip('仅「播报」开启时生效；支持变量：{username}=用户名（数字自动转中文，口径同回复文案）、{integral}=本次累计后的当前总积分'), ("integral", "gift", "broadcast_copy"))
        with ui.card().style(card_css):
            ui.label("文案")
            for index, integral_gift_copywriting in enumerate(get_nested_value(config, "integral", "gift", "copywriting") or []):
                with ui.grid(columns=3):
                    _auto_save(ui.input(label=f"礼物价格区间#{index}", value=integral_gift_copywriting.get("gift_price_interval", ""), placeholder='限制在此区间内的礼物价格来触发对应的文案，用-号来进行区间划分').style("width:100%;"), ("integral", "gift", "copywriting", index, "gift_price_interval"))
                    _auto_save(ui.textarea(label=f"文案#{index}", value=textarea_data_change(integral_gift_copywriting.get("copywriting", [])), placeholder='在此礼物区间内，触发的文案内容，换行分隔').style("width:100%;"), ("integral", "gift", "copywriting", index, "copywriting"), split_lines=True)

    # 入场
    with ui.card().style(card_css):
        ui.label("入场")
        with ui.grid(columns=3):
            with ui.column().style("width:100%;"):
                _auto_save(ui.switch('启用', value=get_nested_value(config, "integral", "entrance", "enable")).style(switch_internal_css), ("integral", "entrance", "enable"))
                _auto_save(ui.input(label='获得积分数', value=get_nested_value(config, "integral", "entrance", "get_integral"), placeholder='入场成功可以获得的积分数，请填写正整数！').style("width:100%;"), ("integral", "entrance", "get_integral"))
                _auto_save(ui.switch('播报', value=bool(get_nested_value(config, "integral", "entrance", "broadcast"))).style(switch_internal_css).tooltip('开启后用户入场积分累计成功时，按下方播报文案进行语音播报；关闭则保持现状只计分不播报（默认关）'), ("integral", "entrance", "broadcast"))
                _auto_save(ui.input(label='播报文案', value=get_nested_value(config, "integral", "entrance", "broadcast_copy") or "", placeholder='入场积分累计成功时的播报文案，如：欢迎 {username} 回来，积分+1').style("width:100%;").tooltip('仅「播报」开启时生效；支持变量：{username}=用户名（数字自动转中文，口径同回复文案）、{integral}=本次累计后的当前总积分'), ("integral", "entrance", "broadcast_copy"))
        ui.label('隐形限制：每 12 小时最多计分一次，窗口内重复入场不计分不回复（防刷分，无需配置）').style("opacity:.6;font-size:12px;")
        with ui.card().style(card_css):
            ui.label("文案")
            for index, integral_entrance_copywriting in enumerate(get_nested_value(config, "integral", "entrance", "copywriting") or []):
                with ui.grid(columns=3):
                    _auto_save(ui.input(label=f"入场数区间#{index}", value=integral_entrance_copywriting.get("entrance_num_interval", ""), placeholder='限制在此区间内的入场数来触发对应的文案，用-号来进行区间划分').style("width:100%;"), ("integral", "entrance", "copywriting", index, "entrance_num_interval"))
                    _auto_save(ui.textarea(label=f"文案#{index}", value=textarea_data_change(integral_entrance_copywriting.get("copywriting", [])), placeholder='在此入场区间内，触发的文案内容，换行分隔').style("width:100%;"), ("integral", "entrance", "copywriting", index, "copywriting"), split_lines=True)

    # 关注
    with ui.card().style(card_css):
        ui.label("关注")
        with ui.grid(columns=3):
            with ui.column().style("width:100%;"):
                _auto_save(ui.switch('启用', value=get_nested_value(config, "integral", "follow", "enable")).style(switch_internal_css), ("integral", "follow", "enable"))
                _auto_save(ui.input(label='获得积分数', value=get_nested_value(config, "integral", "follow", "get_integral"), placeholder='用户关注时可以获得的积分数，请填写正整数！').style("width:100%;"), ("integral", "follow", "get_integral"))
                _auto_save(ui.switch('播报', value=bool(get_nested_value(config, "integral", "follow", "broadcast"))).style(switch_internal_css).tooltip('开启后用户关注积分累计成功时，按下方播报文案进行语音播报；关闭则保持现状只计分不播报（默认关）'), ("integral", "follow", "broadcast"))
                _auto_save(ui.input(label='播报文案', value=get_nested_value(config, "integral", "follow", "broadcast_copy") or "", placeholder='关注积分累计成功时的播报文案，如：感谢 {username} 关注，积分+1').style("width:100%;").tooltip('仅「播报」开启时生效；支持变量：{username}=用户名（数字自动转中文，口径同回复文案）、{integral}=本次累计后的当前总积分'), ("integral", "follow", "broadcast_copy"))
                _auto_save(ui.textarea(label='文案', value=textarea_data_change(get_nested_value(config, "integral", "follow", "copywriting") or []), placeholder='用户关注时回复的文案内容，换行分隔').style("width:100%;"), ("integral", "follow", "copywriting"), split_lines=True)

    # 增删改查
    with ui.card().style(card_css):
        ui.label("增删改查")
        with ui.card().style(card_css):
            ui.label("查询")
            with ui.grid(columns=3):
                with ui.column().style("width:100%;"):
                    _auto_save(ui.switch('启用', value=get_nested_value(config, "integral", "crud", "query", "enable")).style(switch_internal_css), ("integral", "crud", "query", "enable"))
                    _auto_save(ui.textarea(label="命令", value=textarea_data_change(get_nested_value(config, "integral", "crud", "query", "cmd") or []), placeholder='弹幕发送以下命令可以触发查询功能，换行分隔命令').style("width:100%;"), ("integral", "crud", "query", "cmd"), split_lines=True)
                    _auto_save(ui.textarea(label="文案", value=textarea_data_change(get_nested_value(config, "integral", "crud", "query", "copywriting") or []), placeholder='触发查询功能后返回的文案内容，换行分隔').style("width:100%;"), ("integral", "crud", "query", "copywriting"), split_lines=True)
                    _auto_save(ui.textarea(label="无记录文案", value=textarea_data_change(get_nested_value(config, "integral", "crud", "query", "no_record_copywriting") or []), placeholder='用户没有积分记录时返回的文案内容，换行分隔').style("width:100%;"), ("integral", "crud", "query", "no_record_copywriting"), split_lines=True)
