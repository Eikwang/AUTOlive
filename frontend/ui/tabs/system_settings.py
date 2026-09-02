# -*- coding: UTF-8 -*-
"""
系统设置标签页模块
从 web.py 和 common_config.py 迁移而来
每个子功能有独立的页面函数

布局规范：
- 设置项用最多 3 列网格排布，组件宽度跟随列宽（width:100%）
- 开关+关联参数 为上下结构单元：开关在上，输入框在下
"""
from nicegui import ui
from typing import Dict, Any, Callable

from frontend.ui.components.config_helper import get_nested_value


def _get_auto_save(config, set_config_callback, tab_key=""):
    """获取自动绑定函数"""
    from frontend.utils.config_auto_save import create_auto_save
    return create_auto_save(config, set_config_callback, tab_key=tab_key)


# ============================================================
# webui配置
# ============================================================
def create_webui_config_tab(config, theme_config, set_config_callback):
    """创建webui配置页面"""
    card_css = theme_config.get("card", "")
    switch_internal_css = theme_config.get("switch_internal", "")
    _auto_save = _get_auto_save(config, set_config_callback, tab_key="webui_config")

    with ui.card().style(card_css):
        ui.label("webui配置")
        with ui.grid(columns=3):
            _auto_save(ui.input(label='标题', placeholder='webui的标题', value=get_nested_value(config, "webui", "title")).style("width:100%;"), ("webui", "title"))
            _auto_save(ui.input(label='IP地址', placeholder='webui监听的IP地址', value=get_nested_value(config, "webui", "ip")).style("width:100%;"), ("webui", "ip"))
            _auto_save(ui.input(label='端口', placeholder='webui监听的端口', value=get_nested_value(config, "webui", "port")).style("width:100%;"), ("webui", "port"))
            _auto_save(ui.switch('自动运行', value=get_nested_value(config, "webui", "auto_run")).style(switch_internal_css), ("webui", "auto_run"))


# ============================================================
# 本地路径指定URL路径访问
# ============================================================
def create_local_dir_endpoint_tab(config, theme_config, set_config_callback):
    """创建本地路径访问页面"""
    card_css = theme_config.get("card", "")
    switch_internal_css = theme_config.get("switch_internal", "")
    button_internal_color = theme_config.get("button_internal_color", "primary")
    button_internal_css = theme_config.get("button_internal_css", "")
    _auto_save = _get_auto_save(config, set_config_callback, tab_key="local_dir_endpoint")

    with ui.card().style(card_css):
        ui.label("本地路径指定URL路径访问")
        with ui.row():
            ui.input(label='配置索引', value="", placeholder='配置组的排序号')
            ui.button('增加配置组', color=button_internal_color).style(button_internal_css)
            ui.button('删除配置组', color=button_internal_color).style(button_internal_css)
        with ui.grid(columns=3):
            _auto_save(ui.switch('启用', value=get_nested_value(config, "webui", "local_dir_to_endpoint", "enable")).style(switch_internal_css), ("webui", "local_dir_to_endpoint", "enable"))
        with ui.grid(columns=3):
            webui_local_dir_to_endpoint_config_card = ui.card()
            for index, endpoint_config in enumerate(config.get("webui", {}).get("local_dir_to_endpoint", {}).get("config", [])):
                with webui_local_dir_to_endpoint_config_card.style(card_css):
                    with ui.grid(columns=3):
                        ui.input(label=f"URL路径#{index + 1}", value=endpoint_config.get("url_path", ""), placeholder='URL路径').style("width:100%;")
                        ui.input(label=f"本地文件夹路径#{index + 1}", value=endpoint_config.get("local_dir", ""), placeholder='本地文件夹路径').style("width:100%;")


# ============================================================
# 配置模板
# ============================================================
def create_config_template_tab(config, theme_config, set_config_callback):
    """创建配置模板页面"""
    card_css = theme_config.get("card", "")
    button_internal_color = theme_config.get("button_internal_color", "primary")
    button_internal_css = theme_config.get("button_internal_css", "")

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


# ============================================================
# 板块显示/隐藏
# ============================================================
def create_show_card_tab(config, theme_config, set_config_callback):
    """创建板块显示/隐藏页面"""
    card_css = theme_config.get("card", "")
    switch_internal_css = theme_config.get("switch_internal", "")

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


# ============================================================
# 账号管理
# ============================================================
def create_account_manage_tab(config, theme_config, set_config_callback):
    """创建账号管理页面"""
    card_css = theme_config.get("card", "")
    switch_internal_css = theme_config.get("switch_internal", "")
    _auto_save = _get_auto_save(config, set_config_callback, tab_key="config_template")

    with ui.card().style(card_css):
        ui.label("账号管理")
        with ui.grid(columns=3):
            with ui.column().style("width:100%;"):
                _auto_save(ui.switch('登录功能', value=get_nested_value(config, "login", "enable")).style(switch_internal_css), ("login", "enable"))
                _auto_save(ui.input(label='用户名', placeholder='您的账号', value=get_nested_value(config, "login", "username")).style("width:100%;"), ("login", "username"))
                _auto_save(ui.input(label='密码', password=True, placeholder='您的密码', value=get_nested_value(config, "login", "password")).style("width:100%;"), ("login", "password"))


# ============================================================
# 动态配置
# ============================================================
def create_trends_config_tab(config, theme_config, set_config_callback):
    """创建动态配置页面"""
    card_css = theme_config.get("card", "")
    switch_internal_css = theme_config.get("switch_internal", "")
    _auto_save = _get_auto_save(config, set_config_callback, tab_key="show_card")

    with ui.card().style(card_css):
        ui.label('动态配置')
        with ui.grid(columns=3):
            _auto_save(ui.switch('启用', value=get_nested_value(config, "trends_config", "enable")).style(switch_internal_css), ("trends_config", "enable"))
        for index, trends_config_path in enumerate(config.get("trends_config", {}).get("path", [])):
            with ui.grid(columns=3):
                ui.input(label="在线人数范围", value=trends_config_path.get("online_num", "0-999999999"), placeholder='在线人数范围').style("width:100%;")
                ui.input(label="配置路径", value=trends_config_path.get("path", "config.json"), placeholder='配置文件路径').style("width:100%;")


# ============================================================
# 异常报警
# ============================================================
def create_abnormal_alarm_tab(config, theme_config, set_config_callback):
    """创建异常报警页面"""
    card_css = theme_config.get("card", "")
    switch_internal_css = theme_config.get("switch_internal", "")
    _auto_save = _get_auto_save(config, set_config_callback, tab_key="account_manage")

    with ui.card().style(card_css):
        ui.label('异常报警')
        with ui.grid(columns=3):
            for alarm_type in ['platform', 'llm', 'tts', 'svc', 'visual_body', 'other']:
                with ui.column().style("width:100%;"):
                    _auto_save(ui.switch(f'启用{alarm_type}报警', value=get_nested_value(config, "abnormal_alarm", alarm_type, "enable")).style(switch_internal_css), ("abnormal_alarm", alarm_type, "enable"))
                    _auto_save(ui.select(label='类型', options={'local_audio': '本地音频'}, value=get_nested_value(config, "abnormal_alarm", alarm_type, "type")).style("width:100%;"), ("abnormal_alarm", alarm_type, "type"))
                    _auto_save(ui.input(label='开始报警错误数', value=get_nested_value(config, "abnormal_alarm", alarm_type, "start_alarm_error_num"), placeholder='错误数').style("width:100%;"), ("abnormal_alarm", alarm_type, "start_alarm_error_num"))
                    _auto_save(ui.input(label='自动重启错误数', value=get_nested_value(config, "abnormal_alarm", alarm_type, "auto_restart_error_num"), placeholder='错误数').style("width:100%;"), ("abnormal_alarm", alarm_type, "auto_restart_error_num"))
                    _auto_save(ui.input(label='本地音频路径', value=get_nested_value(config, "abnormal_alarm", alarm_type, "local_audio_path"), placeholder='本地音频路径').style("width:100%;"), ("abnormal_alarm", alarm_type, "local_audio_path"))


# ============================================================
# 联动程序
# ============================================================
def create_coordination_program_tab(config, theme_config, set_config_callback):
    """创建联动程序页面"""
    card_css = theme_config.get("card", "")
    switch_internal_css = theme_config.get("switch_internal", "")
    button_internal_color = theme_config.get("button_internal_color", "primary")
    button_internal_css = theme_config.get("button_internal_css", "")

    with ui.card().style(card_css):
        ui.label('联动程序')
        with ui.row():
            ui.input(label='配置索引', value="", placeholder='配置组的排序号')
            ui.button('增加配置组', color=button_internal_color).style(button_internal_css)
            ui.button('删除配置组', color=button_internal_color).style(button_internal_css)

        coordination_program_var = {}
        coordination_program_config_card = ui.card()
        for index, coordination_program in enumerate(config.get("coordination_program", [])):
            with coordination_program_config_card.style(card_css):
                with ui.grid(columns=3):
                    coordination_program_var[str(4 * index)] = ui.switch(f'启用#{index + 1}', value=coordination_program.get("enable", True)).style(switch_internal_css)
                    coordination_program_var[str(4 * index + 1)] = ui.input(label=f"程序名#{index + 1}", value=coordination_program.get("name", ""), placeholder='程序名').style("width:100%;")
                    coordination_program_var[str(4 * index + 2)] = ui.input(label=f"可执行程序#{index + 1}", value=coordination_program.get("executable", ""), placeholder='可执行程序路径').style("width:100%;")
                    coordination_program_var[str(4 * index + 3)] = ui.textarea(label=f'参数#{index + 1}', value="\n".join(coordination_program.get("parameters", [])), placeholder='参数，换行分隔').style("width:100%;")


# ============================================================
# 兼容旧接口 - 默认显示webui配置
# ============================================================
def create_system_settings_tab(config, theme_config, set_config_callback):
    """创建系统设置标签页（默认显示webui配置）"""
    create_webui_config_tab(config, theme_config, set_config_callback)
