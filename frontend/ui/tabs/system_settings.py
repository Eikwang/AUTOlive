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
# 异常报警
# ============================================================
def create_abnormal_alarm_tab(config, theme_config, set_config_callback):
    """创建异常报警页面

    2026-10-26 报警重构批次：配置简化为报警模块开关（LLM服务/数字人服务/
    TTS生成/动作编排/训练主播/训练声音，T1 批次增补 音频播放/弹幕发送；出错崩溃即
    报警，训练主播与训练声音训练完成另有进度通知）+ WEBHOOK 通告三平台（飞书/企业微信/钉钉）。
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
        ("audio_playback", "音频播放（伴奏失败/缺失）"),
        ("danmaku_send", "弹幕发送（闲时发送失败）"),
        ("voice_change", "实时变声（自动开流失败）"),
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
