# -*- coding: UTF-8 -*-
"""
字幕打印标签页模块（整合计划 §6.2：内置化改造；2026-10-22 审查批次 F1 改名）

义务：
- 补 enable 开关（现状 UI 缺失——断链三重奏之一）+ 微指引（D12）
- 2026-10-26 批次：移除字幕页地址回显/复制（桌面字幕窗口直接被 OBS 窗口捕捉，
  无需手拼地址；/captions 网页本身保留不受影响）
- 样式配置卡三组信息架构（D2：平铺/次级/高级折叠区默认收起）；
  2026-10-26 批次：字体/字重/文字颜色改下拉（固定常见项），字幕背景色+页面
  背景色合并为「背景色」（同值双写 bg_color+body_bg_color），新增「描边色」
  （subtitle_stroke_color，网页字幕页 -webkit-text-stroke 颜色；桌面窗口
  tk 不支持描边，仅网页生效）
- keep_time_align_audio 开关（E-1：对齐音频时长）
- 热更新语义标注（样式与开关均即时生效——dx 发现2 修订）
"""
from nicegui import ui
from typing import Dict, Any, Callable

from frontend.ui.components.config_helper import get_nested_value


# 样式下拉固定选项（2026-10-26 批次：颜色/字体收敛常见项，防自由文本写坏样式值）
_FONT_OPTIONS = {
    "Microsoft YaHei": "微软雅黑",
    "SimHei": "黑体",
    "SimSun": "宋体",
    "KaiTi": "楷体",
    "FangSong": "仿宋",
    "DengXian": "等线",
    "Arial": "Arial",
}
_FONT_WEIGHT_OPTIONS = {
    "normal": "常规",
    "bold": "加粗",
    "600": "半粗",
    "900": "特粗",
}
_FONT_COLOR_OPTIONS = {
    "#FFFFFF": "白色",
    "#FFD700": "黄色",
    "#000000": "黑色",
    "#87CEEB": "浅蓝",
    "#98FB98": "浅绿",
    "#FFA6C9": "粉色",
}
# 背景色合并语义：同值双写 bg_color（字幕条底）+ body_bg_color（窗口/页面底）；
# 透明（none）：网页全透明（OBS 叠加）、桌面窗口回退绿幕底（利于色键抠像）
_BG_COLOR_OPTIONS = {
    "rgba(0,0,0,0.6)": "半透明黑（默认）",
    "none": "透明",
    "#000000": "黑色",
    "#222222": "深灰",
    "#FFFFFF": "白色",
    "#14213D": "藏青",
}
_STROKE_COLOR_OPTIONS = {
    "#000000": "黑色",
    "#FFFFFF": "白色",
    "#444444": "深灰",
    "#8B0000": "暗红",
}


def create_web_captions_printer_tab(
    config: Dict[str, Any],
    theme_config: Dict[str, str],
    set_config_callback: Callable
):
    """创建字幕打印标签页"""
    card_css = theme_config.get("card", "")
    switch_internal_css = theme_config.get("switch_internal", "")

    # 导入自动绑定函数
    from frontend.utils.config_auto_save import create_auto_save
    _auto_save = create_auto_save(config, set_config_callback, tab_key="web_captions_printer")

    def _webui_base() -> str:
        """主系统内部 API 地址（桌面窗口打开端点所在；0.0.0.0 回显转 127.0.0.1）"""
        api_ip = get_nested_value(config, "api_ip", default="127.0.0.1") or "127.0.0.1"
        if api_ip == "0.0.0.0":
            api_ip = "127.0.0.1"
        api_port = get_nested_value(config, "api_port", default=8082)
        return f"http://{api_ip}:{api_port}"

    def _open_desktop_window():
        """打开桌面字幕窗口（经主系统 API 在主系统所在机器弹出；幂等）"""
        try:
            import requests
            resp = requests.post(f"{_webui_base()}/captions_desktop_window/open", timeout=3)
            message = (resp.json() or {}).get("message", "已请求")
        except Exception:
            message = '请求失败（主系统未运行？）'
        ui.notify(f'桌面字幕窗口：{message}', type='positive' if '成功' in message or '已打开' in message else 'warning')

    with ui.card().style(card_css):
        ui.label('字幕打印')
        with ui.grid(columns=3):
            with ui.column().style("width:100%;"):
                # UI-16 批次 R2：参数页「启用」开关已移除——二级菜单开关（enable_paths.py
                # 注册 web_captions_printer.enable）与本开关同键，保留二级菜单唯一入口；
                # 参数页未启用提示由 layout.py config_status_label（DG-B1）机制承接。
                _auto_save(ui.switch('字幕时长对齐音频', value=get_nested_value(config, "web_captions_printer", "keep_time_align_audio", default=True)).style(switch_internal_css).tooltip(
                    '开启：字幕保持至音频播完（起点与语音起点对齐）。\n'
                    '关闭：按文本长度自动计算显示时长（旧行为）。'), ("web_captions_printer", "keep_time_align_audio"))
                # 桌面字幕窗口（2026-10-25 批次：补齐整合计划本期丢弃的 desktop_caption_window）
                _auto_save(ui.switch('启动时打开桌面字幕窗口', value=get_nested_value(config, "web_captions_printer", "desktop_window", default=True) is not False).style(switch_internal_css).tooltip(
                    '开启：主系统启动且字幕打印启用时，自动弹出桌面字幕窗口\n'
                    '（可拖拽/置顶/调透明度，直接被 OBS 窗口捕捉）。\n'
                    '关闭：不自动弹出，需要时点右侧按钮手动打开。'), ("web_captions_printer", "desktop_window"))
            with ui.column().style("width:100%;"):
                ui.label('使用两步：① 开启并保存 → ② 重启主系统（首次，桌面字幕窗口自动弹出）。\n'
                         'OBS 添加「窗口捕捉」源并选择字幕显示窗口即可。').style(
                    'font-size: var(--font-size-assist, 12px);')
                with ui.row().style("align-items:center; gap:4px;"):
                    ui.button('打开桌面字幕窗口', on_click=_open_desktop_window, icon='desktop_windows').props('dense outline')
                    ui.label('窗口被误关时点此重开').style(
                        'font-size: var(--font-size-assist, 12px);')

    # ---------- 样式配置卡（D2 三组信息架构；2026-10-26 批次下拉化） ----------
    with ui.card().style(card_css):
        ui.label('字幕样式')
        with ui.grid(columns=3):
            _auto_save(ui.select(
                label='显示模式',
                options={'1': '渐显', '2': '打字机'},
                value=str(get_nested_value(config, "web_captions_printer", "show_mode", default="1")),
            ).style("width:100%;"), ("web_captions_printer", "show_mode"))
            _auto_save(ui.select(
                label='字体',
                options=_FONT_OPTIONS,
                value=get_nested_value(config, "web_captions_printer", "subtitle_font_family") or "Microsoft YaHei",
            ).style("width:100%;"), ("web_captions_printer", "subtitle_font_family"))
            _auto_save(ui.input(label='字号(px)', value=get_nested_value(config, "web_captions_printer", "subtitle_font_size")).style("width:100%;"), ("web_captions_printer", "subtitle_font_size"))
            _auto_save(ui.select(
                label='字重',
                options=_FONT_WEIGHT_OPTIONS,
                value=str(get_nested_value(config, "web_captions_printer", "subtitle_font_weight") or "bold"),
            ).style("width:100%;"), ("web_captions_printer", "subtitle_font_weight"))
            _auto_save(ui.input(label='描边(px)', value=get_nested_value(config, "web_captions_printer", "subtitle_webkit_text_stroke")).style("width:100%;").tooltip('文字描边厚度，0 为无描边'), ("web_captions_printer", "subtitle_webkit_text_stroke"))
            _auto_save(ui.select(
                label='描边色',
                options=_STROKE_COLOR_OPTIONS,
                value=get_nested_value(config, "web_captions_printer", "subtitle_stroke_color") or "#000000",
            ).style("width:100%;").tooltip('文字描边颜色（描边厚度>0 时生效）。\n网页字幕页渲染；桌面窗口暂不支持描边。'), ("web_captions_printer", "subtitle_stroke_color"))
            _auto_save(ui.select(
                label='文字颜色',
                options=_FONT_COLOR_OPTIONS,
                value=get_nested_value(config, "web_captions_printer", "font_color") or "#FFFFFF",
            ).style("width:100%;"), ("web_captions_printer", "font_color"))
            # 背景色合并控件（2026-10-26 批次）：同值双写字幕条底色与窗口/页面底色
            bg_select = ui.select(
                label='背景色',
                options=_BG_COLOR_OPTIONS,
                value=get_nested_value(config, "web_captions_printer", "bg_color") or "rgba(0,0,0,0.6)",
            ).style("width:100%;").tooltip(
                '字幕条与窗口/页面的统一底色（同值双写）。\n'
                '透明：网页全透明（OBS 叠加场景）、桌面窗口回退绿幕底（利于色键抠像）。')
            _auto_save(bg_select, ("web_captions_printer", "bg_color"))
            _auto_save(bg_select, ("web_captions_printer", "body_bg_color"))
            _auto_save(ui.input(label='字幕区宽', value=get_nested_value(config, "web_captions_printer", "subtitle_bg_width")).style("width:100%;"), ("web_captions_printer", "subtitle_bg_width"))
            _auto_save(ui.input(label='字幕区高(最小)', value=get_nested_value(config, "web_captions_printer", "subtitle_bg_height")).style("width:100%;"), ("web_captions_printer", "subtitle_bg_height"))

        # D2：渲染时序 4 键收高级折叠区（默认收起，防误改破坏字幕节奏）
        with ui.expansion('高级：渲染时序参数（毫秒）', icon='schedule').style("width:100%;"):
            with ui.grid(columns=3):
                _auto_save(ui.input(label='单字显示耗时', value=get_nested_value(config, "web_captions_printer", "single_char_show_time")).style("width:100%;").tooltip('打字机模式每字间隔（ms）。误改会破坏字幕节奏——节流与此处同源生效'), ("web_captions_printer", "single_char_show_time"))
                _auto_save(ui.input(label='渐显耗时', value=get_nested_value(config, "web_captions_printer", "gradient_show_time")).style("width:100%;").tooltip('渐显模式透明度过渡时长（ms）'), ("web_captions_printer", "gradient_show_time"))
                _auto_save(ui.input(label='隐藏耗时', value=get_nested_value(config, "web_captions_printer", "hide_time")).style("width:100%;").tooltip('字幕淡出动画时长（ms）'), ("web_captions_printer", "hide_time"))
                _auto_save(ui.input(label='播完保留时长', value=get_nested_value(config, "web_captions_printer", "show_over_hide_time")).style("width:100%;").tooltip('无 keep_time 时显示完毕到开始隐藏的等待（ms）'), ("web_captions_printer", "show_over_hide_time"))
                _auto_save(ui.input(label='字幕队列上限', value=str(get_nested_value(config, "web_captions_printer", "queue_max", default=100) or 100)).style("width:100%;").tooltip('满载丢弃最旧（字幕可丢，台词不丢）'), ("web_captions_printer", "queue_max"))

    ui.label('样式与开关保存后即时生效（字幕页热更新，无需刷新 OBS 源）。').style(
        'font-size: var(--font-size-assist, 12px);')
