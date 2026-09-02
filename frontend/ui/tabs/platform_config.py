# -*- coding: UTF-8 -*-
"""
平台配置标签页模块
从 common_config.py 拆分而来
包含：哔哩哔哩、让弹幕飞、twitch 等平台相关配置

布局规范：
- 设置项用最多 3 列网格排布，组件宽度跟随列宽（width:100%）
"""
from nicegui import ui
from typing import Dict, Any, Callable

from frontend.ui.components.config_helper import get_nested_value


def create_platform_config_tab(
    config: Dict[str, Any],
    theme_config: Dict[str, str],
    set_config_callback: Callable
):
    """创建平台配置标签页"""
    card_css = theme_config.get("card", "")

    # 导入自动绑定函数
    from frontend.utils.config_auto_save import create_auto_save
    _auto_save = create_auto_save(config, set_config_callback, tab_key="platform_config")

    # 哔哩哔哩
    with ui.card().style(card_css):
        ui.label('哔哩哔哩')
        with ui.grid(columns=3):
            _auto_save(ui.select(
                label='登录方式',
                options={'cookie': 'cookie', '手机扫码': '手机扫码', '手机扫码-终端': '手机扫码-终端', '账号密码登录': '账号密码登录', 'open_live': '开放平台', '不登录': '不登录'},
                value=get_nested_value(config, 'bilibili', 'login_type', default='手机扫码')
            ).style("width:100%;"), ("bilibili", "login_type"))
            _auto_save(ui.input(label='cookie', placeholder='b站登录后F12抓网络包获取cookie，强烈建议使用小号！有封号风险，虽然实际上没听说有人被封过', value=get_nested_value(config, "bilibili", "cookie")).style("width:100%;").tooltip('b站登录后F12抓网络包获取cookie，强烈建议使用小号！有封号风险，虽然实际上没听说有人被封过'), ("bilibili", "cookie"))
            _auto_save(ui.input(label='ac_time_value', placeholder='b站登录后，F12控制台，输入window.localStorage.ac_time_value获取(如果没有，请重新登录)', value=get_nested_value(config, "bilibili", "ac_time_value")).style("width:100%;").tooltip('仅在平台：哔哩哔哩，情况下可选填写。b站登录后，F12控制台，输入window.localStorage.ac_time_value获取(如果没有，请重新登录)'), ("bilibili", "ac_time_value"))
            _auto_save(ui.input(label='账号', value=get_nested_value(config, "bilibili", "username"), placeholder='b站账号（建议使用小号）').style("width:100%;").tooltip('仅在平台：哔哩哔哩，登录方式：账号密码登录，情况下填写。b站账号（建议使用小号）'), ("bilibili", "username"))
            _auto_save(ui.input(label='密码', value=get_nested_value(config, "bilibili", "password"), placeholder='b站密码（建议使用小号）').style("width:100%;").tooltip('仅在平台：哔哩哔哩，登录方式：账号密码登录，情况下填写。b站密码（建议使用小号）'), ("bilibili", "password"))
        with ui.card().style(card_css):
            ui.label('开放平台')
            with ui.grid(columns=3):
                _auto_save(ui.input(label='ACCESS_KEY_ID', value=get_nested_value(config, "bilibili", "open_live", "ACCESS_KEY_ID"), placeholder='开放平台ACCESS_KEY_ID').style("width:100%;").tooltip('仅在平台：哔哩哔哩2，登录方式：开放平台，情况下填写。开放平台ACCESS_KEY_ID'), ("bilibili", "open_live", "ACCESS_KEY_ID"))
                _auto_save(ui.input(label='ACCESS_KEY_SECRET', value=get_nested_value(config, "bilibili", "open_live", "ACCESS_KEY_SECRET"), placeholder='开放平台ACCESS_KEY_SECRET').style("width:100%;").tooltip('仅在平台：哔哩哔哩2，登录方式：开放平台，情况下填写。开放平台ACCESS_KEY_SECRET'), ("bilibili", "open_live", "ACCESS_KEY_SECRET"))
                _auto_save(ui.input(label='项目ID', value=get_nested_value(config, "bilibili", "open_live", "APP_ID"), placeholder='开放平台 创作者服务中心 项目ID').style("width:100%;").tooltip('仅在平台：哔哩哔哩2，登录方式：开放平台，情况下填写。开放平台 创作者服务中心 项目ID'), ("bilibili", "open_live", "APP_ID"))
                _auto_save(ui.input(label='身份码', value=get_nested_value(config, "bilibili", "open_live", "ROOM_OWNER_AUTH_CODE"), placeholder='直播中心用户 身份码').style("width:100%;").tooltip('仅在平台：哔哩哔哩2，登录方式：开放平台，情况下填写。直播中心用户 身份码'), ("bilibili", "open_live", "ROOM_OWNER_AUTH_CODE"))

    # 让弹幕飞
    with ui.card().style(card_css):
        ui.label('让弹幕飞')
        with ui.grid(columns=3):
            _auto_save(ui.input(label='WebSocket地址', value=get_nested_value(config, "ordinaryroad_barrage_fly", "ws_ip_port"), placeholder='默认：ws://127.0.0.1:9898').style("width:100%;").tooltip('根据实际服务配置，填写WebSocket地址'), ("ordinaryroad_barrage_fly", "ws_ip_port"))
            _auto_save(ui.textarea(
                label='任务ID',
                placeholder='成功监听的任务在页面中可以复制对应的任务ID，可以自定义编辑多个（换行分隔）',
                value="\n".join(get_nested_value(config, "ordinaryroad_barrage_fly", "taskIds") or [])
            ).style("width:100%;").tooltip('成功监听的任务在页面中可以复制对应的任务ID，可以自定义编辑多个（换行分隔）'), ("ordinaryroad_barrage_fly", "taskIds"))

    # twitch
    with ui.card().style(card_css):
        ui.label('twitch')
        with ui.grid(columns=3):
            _auto_save(ui.input(label='token', value=get_nested_value(config, "twitch", "token"), placeholder='访问 https://twitchapps.com/tmi/ 获取，格式为：oauth:xxx').style("width:100%;"), ("twitch", "token"))
            _auto_save(ui.input(label='用户名', value=get_nested_value(config, "twitch", "user"), placeholder='你的twitch账号用户名').style("width:100%;"), ("twitch", "user"))
            _auto_save(ui.input(label='HTTP代理IP地址', value=get_nested_value(config, "twitch", "proxy_server"), placeholder='代理软件，http协议监听的ip地址，一般为：127.0.0.1').style("width:100%;"), ("twitch", "proxy_server"))
            _auto_save(ui.input(label='HTTP代理端口', value=get_nested_value(config, "twitch", "proxy_port"), placeholder='代理软件，http协议监听的端口，一般为：1080').style("width:100%;"), ("twitch", "proxy_port"))
