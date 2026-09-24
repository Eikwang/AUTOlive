# -*- coding: UTF-8 -*-
"""
web字幕打印机标签页模块（整合计划 §6.2：内置化改造）

义务：
- 补 enable 开关（现状 UI 缺失——断链三重奏之一）+ 三步微指引（D12）
- 移除外部 API 地址；新增字幕页地址回显+复制（0G#3）+ 远程 OBS 说明（dx8）
- 样式配置卡 14 键三组信息架构（D2：平铺/次级/高级折叠区默认收起）
- keep_time_align_audio 开关（E-1：对齐音频时长）
- 热更新语义标注（样式与开关均即时生效——dx 发现2 修订）
- bg_color/body_bg_color 支持 rgba/none 透明语义（D10，taste 已批：透明默认）
"""
import json

from nicegui import ui
from typing import Dict, Any, Callable

from frontend.ui.components.config_helper import get_nested_value


def create_web_captions_printer_tab(
    config: Dict[str, Any],
    theme_config: Dict[str, str],
    set_config_callback: Callable
):
    """创建web字幕打印机标签页"""
    card_css = theme_config.get("card", "")
    switch_internal_css = theme_config.get("switch_internal", "")

    # 导入自动绑定函数
    from frontend.utils.config_auto_save import create_auto_save
    _auto_save = create_auto_save(config, set_config_callback, tab_key="web_captions_printer")

    def _webui_base() -> str:
        """字幕页地址 = 本 webui 地址（页面挂在主系统内部 API——注意两进程端口不同）

        字幕页实际由主系统内部 HTTP API 提供（http://<api_ip>:<api_port>/captions）；
        本页仅做地址回显，取 api_ip/api_port（0.0.0.0 回显转 127.0.0.1，先例 main.py:902）。
        """
        api_ip = get_nested_value(config, "api_ip", default="127.0.0.1") or "127.0.0.1"
        if api_ip == "0.0.0.0":
            api_ip = "127.0.0.1"
        api_port = get_nested_value(config, "api_port", default=8082)
        return f"http://{api_ip}:{api_port}"

    captions_url = f"{_webui_base()}/captions"

    with ui.card().style(card_css):
        ui.label('web字幕打印机')
        with ui.grid(columns=3):
            with ui.column().style("width:100%;"):
                _auto_save(ui.switch('启用', value=get_nested_value(config, "web_captions_printer", "enable", default=False)).style(switch_internal_css).tooltip(
                    '启用后，播放音频时会把内容推送到字幕页显示。\n'
                    '样式与开关均即时生效（无需重启）；字幕页地址见下方回显。'), ("web_captions_printer", "enable"))
                _auto_save(ui.switch('字幕时长对齐音频', value=get_nested_value(config, "web_captions_printer", "keep_time_align_audio", default=True)).style(switch_internal_css).tooltip(
                    '开启：字幕保持至音频播完（起点与语音起点对齐）。\n'
                    '关闭：按文本长度自动计算显示时长（旧行为）。'), ("web_captions_printer", "keep_time_align_audio"))
            with ui.column().style("width:100%;"):
                ui.label('使用三步：① 开启并保存 → ② 重启主系统（首次） → ③ 复制下方地址到 OBS 浏览器源').style(
                    'font-size: var(--font-size-assist, 12px);')
                with ui.row().style("align-items:center; gap:4px;"):
                    url_input = ui.input(label='字幕页地址', value=captions_url).style("width:100%; min-width:260px;").props('readonly')
                    ui.button(icon='content_copy', on_click=lambda: _copy(url_input.value)).props('dense flat').tooltip('复制字幕页地址')
                ui.label('在 OBS 中添加"浏览器"源并粘贴此地址。双机直播：请将 127.0.0.1 替换为本机局域网 IP（dx8）。').style(
                    'font-size: var(--font-size-assist, 12px);')

    def _copy(text: str):
        try:
            ui.run_javascript(f'navigator.clipboard.writeText({json.dumps(text)})')
            ui.notify('地址已复制', type='positive')
        except Exception:
            ui.notify('复制失败，请手动选择地址复制', type='warning')

    # ---------- 样式配置卡（14 键三组信息架构，D2） ----------
    with ui.card().style(card_css):
        ui.label('字幕样式')
        with ui.grid(columns=3):
            _auto_save(ui.select(
                label='显示模式',
                options={'1': '渐显', '2': '打字机'},
                value=str(get_nested_value(config, "web_captions_printer", "show_mode", default="1")),
            ).style("width:100%;"), ("web_captions_printer", "show_mode"))
            _auto_save(ui.input(label='字体', value=get_nested_value(config, "web_captions_printer", "subtitle_font_family")).style("width:100%;"), ("web_captions_printer", "subtitle_font_family"))
            _auto_save(ui.input(label='字号(px)', value=get_nested_value(config, "web_captions_printer", "subtitle_font_size")).style("width:100%;"), ("web_captions_printer", "subtitle_font_size"))
            _auto_save(ui.input(label='字重', value=get_nested_value(config, "web_captions_printer", "subtitle_font_weight")).style("width:100%;").tooltip('如 bold / 600'), ("web_captions_printer", "subtitle_font_weight"))
            _auto_save(ui.input(label='描边(px)', value=get_nested_value(config, "web_captions_printer", "subtitle_webkit_text_stroke")).style("width:100%;").tooltip('文字描边厚度，0 为无描边'), ("web_captions_printer", "subtitle_webkit_text_stroke"))
            _auto_save(ui.input(label='文字颜色', value=get_nested_value(config, "web_captions_printer", "font_color")).style("width:100%;").tooltip('支持 #RRGGBB 或 rgba(...)'), ("web_captions_printer", "font_color"))
            _auto_save(ui.input(label='字幕背景色', value=get_nested_value(config, "web_captions_printer", "bg_color")).style("width:100%;").tooltip('支持 rgba(...)/#RRGGBB；填 none 为透明'), ("web_captions_printer", "bg_color"))
            _auto_save(ui.input(label='页面背景色', value=get_nested_value(config, "web_captions_printer", "body_bg_color")).style("width:100%;").tooltip('OBS 叠加场景请保持 none（透明）；填 #RRGGBB 可用于纯浏览器观看'), ("web_captions_printer", "body_bg_color"))
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
