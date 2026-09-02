# -*- coding: UTF-8 -*-
"""
页面配置标签页模块
从 webui-bak.py 的 web_page 拆分而来

布局规范：
- 设置项用最多 3 列网格排布，组件宽度跟随列宽（width:100%）
- 开关+关联参数 为上下结构单元：开关在上，输入框在下
"""
from nicegui import ui
from typing import Dict, Any, Callable

from frontend.ui.components.config_helper import get_nested_value


def create_web_tab(
    config: Dict[str, Any],
    theme_config: Dict[str, str],
    set_config_callback: Callable
):
    """创建页面配置标签页"""
    card_css = theme_config.get("card", "")
    switch_internal_css = theme_config.get("switch_internal", "")
    button_internal_color = theme_config.get("button_internal_color", "primary")
    button_internal_css = theme_config.get("button_internal_css", "")

    # 导入自动绑定函数
    from frontend.utils.config_auto_save import create_auto_save
    _auto_save = create_auto_save(config, set_config_callback, tab_key="web")

    # webui配置
    with ui.card().style(card_css):
        ui.label("webui配置")
        with ui.grid(columns=3):
            _auto_save(ui.input(label='标题', placeholder='webui的标题', value=get_nested_value(config, "webui", "title")).style("width:100%;"), ("webui", "title"))
            _auto_save(ui.input(label='IP地址', placeholder='webui监听的IP地址', value=get_nested_value(config, "webui", "ip")).style("width:100%;"), ("webui", "ip"))
            _auto_save(ui.input(label='端口', placeholder='webui监听的端口', value=get_nested_value(config, "webui", "port")).style("width:100%;"), ("webui", "port"))
            _auto_save(ui.switch('自动运行', value=get_nested_value(config, "webui", "auto_run")).style(switch_internal_css), ("webui", "auto_run"))

    # 本地路径指定URL路径访问
    with ui.card().style(card_css):
        ui.label("本地路径指定URL路径访问")
        with ui.row():
            input_webui_local_dir_to_endpoint_index = ui.input(label='配置索引', value="", placeholder='配置组的排序号')
            button_webui_local_dir_to_endpoint_add = ui.button('增加配置组', color=button_internal_color).style(button_internal_css)
            button_webui_local_dir_to_endpoint_del = ui.button('删除配置组', color=button_internal_color).style(button_internal_css)
        with ui.grid(columns=3):
            with ui.column().style("width:100%;"):
                _auto_save(ui.switch('启用', value=get_nested_value(config, "webui", "local_dir_to_endpoint", "enable")).style(switch_internal_css), ("webui", "local_dir_to_endpoint", "enable"))
                webui_local_dir_to_endpoint_config_card = ui.card()
                for index, endpoint_config in enumerate(config.get("webui", {}).get("local_dir_to_endpoint", {}).get("config", [])):
                    with webui_local_dir_to_endpoint_config_card.style(card_css):
                        ui.input(label=f"URL路径#{index + 1}", value=endpoint_config.get("url_path", ""), placeholder='URL路径').style("width:100%;")
                        ui.input(label=f"本地文件夹路径#{index + 1}", value=endpoint_config.get("local_dir", ""), placeholder='本地文件夹路径').style("width:100%;")

    # 主题模式
    with ui.card().style(card_css):
        ui.label("主题模式")
        with ui.row():
            ui.label("浅色/深色模式切换请使用导航栏底部的按钮")

    # 配置模板
    with ui.card().style(card_css):
        ui.label("配置模板")
        with ui.row():
            ui.select(
                label='配置模板路径',
                options={},
                value=None,
                with_input=True,
                new_value_mode='add-unique',
                clearable=True
            )
            ui.button('保存webui配置到文件', color=button_internal_color).style(button_internal_css)
            ui.button('读取模板到本地（慎点）', color=button_internal_color).style(button_internal_css)

    # 板块显示/隐藏
    with ui.card().style(card_css):
        ui.label("板块显示/隐藏")

        with ui.card().style(card_css):
            ui.label("通用配置")
            with ui.grid(columns=3):
                for key, label in [
                    ('read_comment', '念弹幕'), ('filter', '过滤'), ('thanks', '答谢'),
                    ('local_qa', '本地问答'), ('choose_song', '点歌'), ('sd', 'Stable Diffusion'),
                    ('log', '日志'), ('schedule', '定时任务'), ('idle_time_task', '闲时任务'),
                    ('trends_copywriting', '动态文案'), ('database', '数据库'), ('play_audio', '音频播放'),
                    ('web_captions_printer', 'web字幕打印机'), ('key_mapping', '按键/文案映射'),
                    ('custom_cmd', '自定义命令'), ('trends_config', '动态配置'),
                    ('abnormal_alarm', '异常报警'), ('coordination_program', '联动程序'),
                ]:
                    ui.switch(label, value=get_nested_value(config, "webui", "show_card", "common_config", key)).style(switch_internal_css)

        with ui.card().style(card_css):
            ui.label("大语言模型")
            with ui.grid(columns=3):
                ui.switch('自定义LLM', value=get_nested_value(config, "webui", "show_card", "llm", "custom_llm")).style(switch_internal_css)

        with ui.card().style(card_css):
            ui.label("文本转语音")
            with ui.grid(columns=3):
                ui.switch('gpt_sovits', value=get_nested_value(config, "webui", "show_card", "tts", "gpt_sovits")).style(switch_internal_css)

        with ui.card().style(card_css):
            ui.label("变声")
            with ui.grid(columns=3):
                ui.switch('SO-VITS-SVC', value=get_nested_value(config, "webui", "show_card", "svc", "so_vits_svc")).style(switch_internal_css)

        with ui.card().style(card_css):
            ui.label("虚拟身体")
            with ui.grid(columns=3):
                ui.switch('metahuman_stream', value=get_nested_value(config, "webui", "show_card", "visual_body", "metahuman_stream")).style(switch_internal_css)

    # 账号管理
    with ui.card().style(card_css):
        ui.label("账号管理")
        with ui.grid(columns=3):
            with ui.column().style("width:100%;"):
                ui.switch('登录功能', value=get_nested_value(config, "login", "enable")).style(switch_internal_css)
                ui.input(label='用户名', placeholder='您的账号', value=get_nested_value(config, "login", "username")).style("width:100%;")
                ui.input(label='密码', password=True, placeholder='您的密码', value=get_nested_value(config, "login", "password")).style("width:100%;")
