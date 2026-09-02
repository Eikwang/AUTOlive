# -*- coding: UTF-8 -*-
"""
数据分析标签页模块
从 webui-bak.py 的 data_analysis_page 拆分而来

布局规范：
- 设置项用最多 3 列网格排布，组件宽度跟随列宽（width:100%）
"""
from nicegui import ui
from typing import Dict, Any, Callable

from frontend.ui.components.config_helper import get_nested_value


def create_data_analysis_tab(
    config: Dict[str, Any],
    theme_config: Dict[str, str],
    set_config_callback: Callable,
    data_analysis=None
):
    """创建数据分析标签页"""
    card_css = theme_config.get("card", "")
    echart_css = theme_config.get("echart", "height: 300px;")

    # 导入自动绑定函数
    from frontend.utils.config_auto_save import create_auto_save
    _auto_save = create_auto_save(config, set_config_callback, tab_key="data_analysis")

    # 如果没有传入 data_analysis 实例，尝试自行创建
    if data_analysis is None:
        try:
            from utils.data_analysis import Data_Analysis
            from frontend.config.paths import get_config_path
            data_analysis = Data_Analysis(str(get_config_path()))
        except Exception as e:
            ui.label(f"数据分析模块加载失败: {e}")
            return

    # 弹幕词云
    data_analysis_comment_word_cloud_card = ui.card()
    with data_analysis_comment_word_cloud_card.style("width:100%;"):
        top_num = int(get_nested_value(config, "data_analysis", "comment_word_cloud", "top_num") or 10)
        echart_comment_word_cloud = ui.echart(
            data_analysis.get_comment_word_cloud_option(top_num)
        ).style(echart_css)

        with ui.grid(columns=3):
            input_data_analysis_comment_word_cloud_top_num = _auto_save(ui.input(
                label='前N个关键词',
                value=get_nested_value(config, "data_analysis", "comment_word_cloud", "top_num"),
                placeholder='筛选前N个弹幕关键词做为词云数据'
            ).style("width:100%;"), ("data_analysis", "comment_word_cloud", "top_num"))

            def update_echart_comment_word_cloud():
                data_analysis_comment_word_cloud_card.remove(0)
                new_echart = ui.echart(
                    data_analysis.get_comment_word_cloud_option(
                        int(input_data_analysis_comment_word_cloud_top_num.value or 10))
                ).style(echart_css)
                new_echart.move(data_analysis_comment_word_cloud_card, 0)

            ui.button('更新数据', on_click=lambda: update_echart_comment_word_cloud())

    # 积分榜
    data_analysis_integral_card = ui.card()
    with data_analysis_integral_card.style("width:100%;"):
        integral_top_num = int(get_nested_value(config, "data_analysis", "integral", "top_num") or 10)
        echart_integral = ui.echart(
            data_analysis.get_integral_option("integral", integral_top_num)
        ).style(echart_css)

        with ui.grid(columns=3):
            input_data_analysis_integral_top_num = _auto_save(ui.input(
                label='Top N个数据',
                value=get_nested_value(config, "data_analysis", "integral", "top_num"),
                placeholder='筛选Top N个数据'
            ).style("width:100%;"), ("data_analysis", "integral", "top_num"))

            def update_echart_integral(type):
                data_analysis_integral_card.remove(0)
                new_echart = ui.echart(
                    data_analysis.get_integral_option(
                        type,
                        int(input_data_analysis_integral_top_num.value or 10))
                ).style(echart_css)
                new_echart.move(data_analysis_integral_card, 0)

            ui.button('获取积分榜', on_click=lambda: update_echart_integral('integral'))
            ui.button('获取观看榜', on_click=lambda: update_echart_integral('view_num'))
            ui.button('获取签到榜', on_click=lambda: update_echart_integral('sign_num'))
            ui.button('获取金额榜', on_click=lambda: update_echart_integral('total_price'))

    # 礼物榜
    data_analysis_gift_card = ui.card()
    with data_analysis_gift_card.style("width:100%;"):
        gift_top_num = int(get_nested_value(config, "data_analysis", "gift", "top_num") or 10)
        echart_gift = ui.echart(
            data_analysis.get_gift_option(gift_top_num)
        ).style(echart_css)

        with ui.grid(columns=3):
            input_data_analysis_gift_top_num = _auto_save(ui.input(
                label='Top N个数据',
                value=get_nested_value(config, "data_analysis", "gift", "top_num"),
                placeholder='筛选Top N个数据'
            ).style("width:100%;"), ("data_analysis", "gift", "top_num"))

            def update_echart_gift():
                data_analysis_gift_card.remove(0)
                new_echart = ui.echart(
                    data_analysis.get_gift_option(
                        int(input_data_analysis_gift_top_num.value or 10))
                ).style(echart_css)
                new_echart.move(data_analysis_gift_card, 0)

            ui.button('更新数据', on_click=lambda: update_echart_gift())
