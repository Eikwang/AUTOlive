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

def create_account_manage_tab(config, theme_config, set_config_callback):
    """创建账号管理页面"""
    card_css = theme_config.get("card", "")
    switch_internal_css = theme_config.get("switch_internal", "")
    _auto_save = _get_auto_save(config, set_config_callback, tab_key="account_manage")

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
    """创建动态配置页面（/autoplan 批准批次：保存回调补全+模板默认路径固化）。

    任务书指令：模板保存到系统默认路径（config_templates/），保存/调用均直接
    选取不再填写路径；配置模板页迁移至此（旧 tab 移除延后至门禁 A 删除批）。
    应用模板走 set_config 同通道（与 _auto_save 一致）；各模板差异项的生效
    语义（即时/需重启）须在 D-20 参数生效语义矩阵登记（D-21）。
    """
    import os

    card_css = theme_config.get("card", "")
    button_internal_color = theme_config.get("button_internal_color", "primary")
    button_internal_css = theme_config.get("button_internal_css", "")
    _auto_save = _get_auto_save(config, set_config_callback, tab_key="trends_config")

    def _templates_dir() -> str:
        from frontend.config.paths import get_base_path
        d = os.path.join(str(get_base_path()), "config_templates")
        os.makedirs(d, exist_ok=True)
        return d

    def _list_templates() -> dict:
        d = _templates_dir()
        return {f[:-5]: f[:-5] for f in os.listdir(d) if f.endswith(".json")}

    with ui.card().style(card_css):
        ui.label('动态配置')
        ui.label('按观看人数段保存配置模板，运行中按人数自动切换（D-21：新事件走新模板，在途任务自然流干）').classes('text-xs')

        # 人数段→模板绑定（既有数据结构保留，路径改为模板名引用默认目录）
        for index, trends_config_path in enumerate(config.get("trends_config", {}).get("path", [])):
            with ui.grid(columns=3):
                _auto_save(
                    ui.input(label="在线人数范围", value=trends_config_path.get("online_num", "0-999999999"), placeholder='在线人数范围').style("width:100%;"),
                    ("trends_config", "path", index, "online_num"))
                _auto_save(
                    ui.input(label="模板名", value=os.path.splitext(os.path.basename(trends_config_path.get("path", "")))[0], placeholder='模板名（存于 config_templates/）').style("width:100%;"),
                    ("trends_config", "path", index, "path"))

        ui.separator()
        with ui.grid(columns=3):
            template_name = ui.input(label='模板名', placeholder='例如：高人数档', value='').style("width:100%;")
            ui.button('保存当前配置为模板', color=button_internal_color, on_click=lambda: _save_template(template_name.value)).style(button_internal_css)
            template_select = ui.select(label='选择模板', options=_list_templates(), value=None, with_input=True).style("width:100%;")
            ui.button('应用模板（慎点，覆盖当前配置）', color=button_internal_color, on_click=lambda: _apply_template(template_select.value)).style(button_internal_css)

    def _save_template(name: str):
        import json
        from nicegui import ui as _ui
        name = (name or "").strip()
        if not name:
            _ui.notify(position="top", type="warning", message="请先填写模板名")
            return
        path = os.path.join(_templates_dir(), f"{name}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
        template_select.options = _list_templates()
        template_select.update()
        _ui.notify(position="top", type="positive", message=f"模板已保存：config_templates/{name}.json")

    def _apply_template(name: str):
        import json
        from nicegui import ui as _ui
        if not name:
            _ui.notify(position="top", type="warning", message="请先选择模板")
            return
        path = os.path.join(_templates_dir(), f"{name}.json")
        if not os.path.exists(path):
            _ui.notify(position="top", type="negative", message=f"模板不存在：{path}")
            return
        with open(path, "r", encoding="utf-8") as f:
            template = json.load(f)
        applied = 0

        def _walk(src, dst, prefix=()):
            nonlocal applied
            if not isinstance(src, dict):
                return
            for k, v in src.items():
                if isinstance(v, dict) and isinstance(dst.get(k), dict):
                    _walk(v, dst[k], prefix + (k,))
                elif k in dst:
                    if dst[k] != v:
                        set_config_callback(*prefix, k, value=v)
                        applied += 1

        _walk(template, config)
        _ui.notify(position="top", type="positive",
                   message=f"模板已应用（更新 {applied} 项）。重启后完全生效；各差异项生效语义见参数矩阵（D-20）")


# ============================================================
# 异常报警
# ============================================================
def create_abnormal_alarm_tab(config, theme_config, set_config_callback):
    """创建异常报警页面

    D1/D-14（2026-10-19 批次切片 2）：新增报警通道配置（notify_enable/sound_enable），
    控制子进程监控告警的弹出通道；最近异常明细（脱敏后）在侧边栏底部运行状态条
    实时呈现。既有 6 类错误计数配置保留（存量行为不变）。
    """
    card_css = theme_config.get("card", "")
    switch_internal_css = theme_config.get("switch_internal", "")
    _auto_save = _get_auto_save(config, set_config_callback, tab_key="abnormal_alarm")

    with ui.card().style(card_css):
        ui.label('报警通道')
        ui.label('子进程崩溃/启动失败时是否弹出告警通知；最近异常明细见侧边栏底部运行状态条').classes('text-xs')
        with ui.grid(columns=3):
            _auto_save(
                ui.switch('弹出告警通知', value=get_nested_value(config, "abnormal_alarm", "notify_enable", default=True)).style(switch_internal_css),
                ("abnormal_alarm", "notify_enable"))
            _auto_save(
                ui.switch('报警声音', value=get_nested_value(config, "abnormal_alarm", "sound_enable", default=False)).style(switch_internal_css),
                ("abnormal_alarm", "sound_enable"))
        ui.label('注：声音报警通道暂未接线，开关配置先行').classes('text-xs')

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
# 兼容旧接口 - 默认显示webui配置
# ============================================================
def create_system_settings_tab(config, theme_config, set_config_callback):
    """创建系统设置标签页（默认显示webui配置）"""
    create_webui_config_tab(config, theme_config, set_config_callback)
