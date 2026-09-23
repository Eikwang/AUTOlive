# -*- coding: UTF-8 -*-
"""
联网搜索标签页模块
从 common_config.py 拆分而来

布局规范：
- 设置项用最多 3 列网格排布，组件宽度跟随列宽（width:100%）
- 开关+关联参数 为上下结构单元：开关在上，输入框在下
"""
from nicegui import ui
from typing import Dict, Any, Callable
from frontend.ui.components.config_helper import get_nested_value
from frontend.utils.common import textarea_data_change


def create_search_online_tab(
    config: Dict[str, Any],
    theme_config: Dict[str, str],
    set_config_callback: Callable
):
    """创建联网搜索标签页"""
    card_css = theme_config.get("card", "")
    switch_internal_css = theme_config.get("switch_internal", "")

    # 导入自动绑定函数
    from frontend.utils.config_auto_save import create_auto_save
    _auto_save = create_auto_save(config, set_config_callback, tab_key="search_online")

    # 联网搜索
    with ui.card().style(card_css):
        ui.label('联网搜索')
        with ui.grid(columns=3):
            with ui.column().style("width:100%;"):
                _auto_save(ui.switch('关键词触发', value=get_nested_value(config, "search_online", "keyword_enable")).style(switch_internal_css), keys=("search_online", "keyword_enable"))
                _auto_save(ui.textarea(label='关键词前缀', placeholder='前缀必须携带其中任一字符串才能触发联网搜索', value=textarea_data_change(get_nested_value(config, "search_online", "before_keyword"))).style("width:100%;"), keys=("search_online", "before_keyword"))
            _auto_save(ui.select(label='搜索引擎', options={'baidu': '百度搜索', 'google': '谷歌搜索'}, value=get_nested_value(config, "search_online", "engine")).style("width:100%;"), keys=("search_online", "engine"))
            _auto_save(ui.input(label='引擎ID', value=get_nested_value(config, "search_online", "engine_id"), placeholder='默认：1').style("width:100%;"), keys=("search_online", "engine_id"))
            _auto_save(ui.input(label='检索的文章数', value=get_nested_value(config, "search_online", "count"), placeholder='默认为：1').style("width:100%;"), keys=("search_online", "count"))
            _auto_save(ui.input(label='结果模板', value=get_nested_value(config, "search_online", "resp_template"), placeholder='检索后数据会以这个模板格式生成问题').style("width:100%;"), keys=("search_online", "resp_template"))
            _auto_save(ui.input(label='HTTP代理地址', value=get_nested_value(config, "search_online", "http_proxy"), placeholder='http代理地址').style("width:100%;"), keys=("search_online", "http_proxy"))
            _auto_save(ui.input(label='HTTPS代理地址', value=get_nested_value(config, "search_online", "https_proxy"), placeholder='https代理地址').style("width:100%;"), keys=("search_online", "https_proxy"))
