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
        # UI-16 批次 R6/D-5/DV-1c：①存量重复条目在线去重 ②条目删除能力（防重复再现）
        # ③空态提示 ④条目区可刷新容器（删除后重建绑定，防 index 漂移写错位）
        _trends_path_list = list(config.get("trends_config", {}).get("path", []) or [])
        _seen_keys = set()
        _deduped = []
        for _entry in _trends_path_list:
            _key = (str((_entry or {}).get("online_num", "")), str((_entry or {}).get("path", "")))
            if _key in _seen_keys:
                continue
            _seen_keys.add(_key)
            _deduped.append(_entry)
        if len(_deduped) != len(_trends_path_list):
            config.setdefault("trends_config", {})["path"] = _deduped
            set_config_callback("trends_config", "path", value=_deduped)
            ui.notify(position="top", type="positive",
                      message=f"已清理 {len(_trends_path_list) - len(_deduped)} 条重复的人数段绑定")

        entries_container = ui.column().style("width:100%; gap:4px;")

        def _delete_entry(index: int):
            path_list = list(config.get("trends_config", {}).get("path", []) or [])
            if 0 <= index < len(path_list):
                path_list.pop(index)
                set_config_callback("trends_config", "path", value=path_list)
                ui.notify(position="top", type="positive", message="人数段绑定已删除")
                _render_entries()

        def _render_entries():
            entries_container.clear()
            with entries_container:
                path_list = list(config.get("trends_config", {}).get("path", []) or [])
                if not path_list:
                    # DV-1c：人数段条目清空后的空态提示（模板保存/应用不受影响，两机制独立）
                    ui.label("暂无人数段绑定——模板保存/应用不受影响（与人数段自动切换是两个机制）").style(
                        'font-size: var(--font-size-assist, 12px); opacity: 0.7;')
                for index, trends_config_path in enumerate(path_list):
                    with ui.row().style("width:100%; align-items:center; gap:8px; flex-wrap:wrap;"):
                        _auto_save(
                            ui.input(label="在线人数范围", value=trends_config_path.get("online_num", "0-999999999"), placeholder='在线人数范围').style("width:220px;"),
                            ("trends_config", "path", index, "online_num"))
                        _auto_save(
                            ui.input(label="模板名", value=os.path.splitext(os.path.basename(trends_config_path.get("path", "")))[0], placeholder='模板名（存于 config_templates/）').style("width:220px;"),
                            ("trends_config", "path", index, "path"))
                        ui.button(icon='delete', on_click=lambda _, i=index: _delete_entry(i)).props('flat dense').tooltip('删除此人数段绑定')

        _render_entries()

        ui.separator()
        # UI-16 批次 R6/DV-1c：布局写死完整纵向顺序=模板名→选择模板（纵向相邻，用户
        # 显式指定）→保存→应用（纵向相邻）；D-4 两按钮同尺寸（width:100% 等宽）
        with ui.grid(columns=2).style("width:100%;"):
            template_name = ui.input(label='模板名', placeholder='例如：高人数档', value='').style("width:100%;")
            ui.button('保存当前配置为模板', color=button_internal_color, on_click=lambda: _save_template(template_name.value)).style(button_internal_css + '; width:100%;')
            template_select = ui.select(label='选择模板', options=_list_templates(), value=None, with_input=True).style("width:100%;")
            ui.button('应用模板（慎点，覆盖当前配置）', color=button_internal_color, on_click=lambda: _apply_template(template_select.value)).style(button_internal_css + '; width:100%;')

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

    2026-10-26 报警重构批次：配置简化为六个报警模块开关（LLM服务/数字人服务/
    TTS生成/动作编排/训练主播/训练声音，出错崩溃即报警，训练主播与训练声音
    训练完成另有进度通知）+ WEBHOOK 通告三平台（飞书/企业微信/钉钉）。
    旧六类本地音频报警/错误计数/自动重启配置整体退役。
    """
    card_css = theme_config.get("card", "")
    switch_internal_css = theme_config.get("switch_internal", "")
    _auto_save = _get_auto_save(config, set_config_callback, tab_key="abnormal_alarm")

    _MODULE_SWITCHES = [
        ("llm", "LLM服务"),
        ("digital_human", "数字人服务"),
        ("tts", "TTS生成"),
        ("orchestration", "动作编排"),
        ("train_streamer", "训练主播"),
        ("train_voice", "训练声音"),
    ]

    with ui.card().style(card_css):
        ui.label('报警模块')
        ui.label('开启后各模块出错/崩溃即触发报警（训练主播/训练声音训练完成后另行推送进度通知）；报警经下方 WEBHOOK 推送到群机器人').classes('text-xs')
        with ui.grid(columns=3):
            for module_key, module_label in _MODULE_SWITCHES:
                _auto_save(
                    ui.switch(module_label, value=get_nested_value(
                        config, "abnormal_alarm", "modules", module_key, default=True)
                    ).style(switch_internal_css),
                    ("abnormal_alarm", "modules", module_key))

    with ui.card().style(card_css):
        ui.label('WEBHOOK 通告')
        ui.label('报警与通知经群机器人推送（飞书/企业微信/钉钉，至少填写一个即启用）；发送失败仅记日志不影响直播').classes('text-xs')
        with ui.grid(columns=1):
            _auto_save(ui.input(label='飞书机器人 WEBHOOK 地址', placeholder='https://open.feishu.cn/open-apis/bot/v2/hook/****',
                                value=get_nested_value(config, "abnormal_alarm", "webhook", "feishu_url")).style("width:100%;"),
                       ("abnormal_alarm", "webhook", "feishu_url"))
            _auto_save(ui.input(label='企业微信机器人 WEBHOOK 地址', placeholder='https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=****',
                                value=get_nested_value(config, "abnormal_alarm", "webhook", "wecom_url")).style("width:100%;"),
                       ("abnormal_alarm", "webhook", "wecom_url"))
            _auto_save(ui.input(label='钉钉机器人 WEBHOOK 地址', placeholder='https://oapi.dingtalk.com/robot/send?access_token=****',
                                value=get_nested_value(config, "abnormal_alarm", "webhook", "dingtalk_url")).style("width:100%;"),
                       ("abnormal_alarm", "webhook", "dingtalk_url"))
            _auto_save(ui.input(label='钉钉加签密钥（机器人安全设置选「加签」时必填）', password=True,
                                placeholder='SEC 开头的加签密钥',
                                value=get_nested_value(config, "abnormal_alarm", "webhook", "dingtalk_secret")).style("width:100%;"),
                       ("abnormal_alarm", "webhook", "dingtalk_secret"))

        def _send_test():
            """发送测试消息（同步等待结果，便于直接呈现成败）。"""
            from utils import alarm_notifier
            results = alarm_notifier.send_test()
            if not results:
                ui.notify(position="top", type="warning",
                          message="未配置任何 WEBHOOK 地址，请先填写至少一个平台")
                return
            ok_channels = [c for c, (ok, _) in results.items() if ok]
            if len(ok_channels) == len(results):
                ui.notify(position="top", type="positive",
                          message=f"测试消息发送成功（{'、'.join(ok_channels)}），请到群内确认")
            else:
                failed = "、".join(c for c, (ok, _) in results.items() if not ok)
                ui.notify(position="top", type="negative",
                          message=f"部分渠道失败：{failed}——详情见主程序日志")

        ui.button('发送测试消息', on_click=_send_test)


# ============================================================
# 兼容旧接口 - 默认显示webui配置
# ============================================================
def create_system_settings_tab(config, theme_config, set_config_callback):
    """创建系统设置标签页（默认显示webui配置）"""
    create_webui_config_tab(config, theme_config, set_config_callback)
