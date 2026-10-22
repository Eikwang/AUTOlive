# -*- coding: UTF-8 -*-
"""
音频播放标签页模块（audio_player/captions_printer 整合：builtin 内置播放器）

布局规范：
- 设置项用最多 3 列网格排布，组件宽度跟随列宽（width:100%）
- 开关+关联参数 为上下结构单元：开关在上，输入框在下

整合义务（计划 §6.1）：
- builtin 选项 + 设备下拉（异步枚举，loading/空态 D8）+ 播放间隔 + 只读
  优先级映射折叠区（D4，含 config 手改逃生门注明 dx7）+ queue_max
- 控制区（D5/D6/D7/D9）：状态回显行 + 四按钮 + 1s 轮询 + 错误条
- EDTalk 激活：播放器 select 禁用（D3）+ 常驻提示（预期管理 F1）
- 外部 API 地址：选 builtin/pygame 时隐藏（0G cherry-pick #4）
- 生效时机标注（D11）：播放器选择修改后重启主系统生效
"""
import json

import requests
from nicegui import ui
from typing import Dict, Any, Callable

from frontend.ui.components.config_helper import get_nested_value
from frontend.utils.common import is_url_check
from utils.edtalk_realtime.helpers import is_edtalk_active


def _main_api_base(config: Dict[str, Any]) -> str:
    """主系统内部 API 地址（0.0.0.0 回显转 127.0.0.1，先例 frontend/main.py:902）"""
    api_ip = get_nested_value(config, "api_ip", default="127.0.0.1") or "127.0.0.1"
    if api_ip == "0.0.0.0":
        api_ip = "127.0.0.1"
    api_port = get_nested_value(config, "api_port", default=8082)
    return f"http://{api_ip}:{api_port}"


def create_audio_play_tab(
    config: Dict[str, Any],
    theme_config: Dict[str, str],
    set_config_callback: Callable
):
    """创建音频播放标签页"""
    card_css = theme_config.get("card", "")
    switch_internal_css = theme_config.get("switch_internal", "")

    # 导入自动绑定函数
    from frontend.utils.config_auto_save import create_auto_save
    _auto_save = create_auto_save(config, set_config_callback, tab_key="audio_play")

    api_base = _main_api_base(config)
    edtalk_active = is_edtalk_active(config)

    with ui.card().style(card_css):
        ui.label('音频播放')
        with ui.grid(columns=3):
            _auto_save(ui.switch('启用文本切分', value=get_nested_value(config, "play_audio", "text_split_enable")).style(switch_internal_css).tooltip('启用后会将LLM等待合成音频的消息按四句一切的方式分段（每段最多四句），以便TTS快速合成'), ("play_audio", "text_split_enable"))
            # A3（2026-10-22 审查批次）：「音频信息回传给内部接口」参数退役移除——
            # 外接 audio_player 已弃用，且回路接收端（/callback）本就仅打日志（死链）。
            # 闲时清零如需数据走进程内 get_audio_info 直查，不经 HTTP 回调。

        with ui.grid(columns=3):
            # A3：「间隔时间重复次数最小值/最大值」退役移除（设计项3）——播放间隔改为
            # 单次随机取值；节奏变化（默认值域 0.3~1.0s → 0.3~0.5s）已入发布说明。
            # A4（设计项4）：参数名改为「播放间隔min/max」，新键 play_interval_min/max，
            # 旧键 normal_interval_min/max 读侧一次性兼容（值不丢，旧键保留）。
            _auto_save(ui.input(label='播放间隔min', value=get_nested_value(config, "play_audio", "play_interval_min", default=get_nested_value(config, "play_audio", "normal_interval_min")), placeholder='每条音频播放结束后到播放下一条之间的间隔时间下限，单位：秒').style("width:100%;").tooltip('每条音频播放结束后到播放下一条之间的间隔时间下限，单位：秒。实际间隔在 min 与 max 之间随机取值'), ("play_audio", "play_interval_min"))
            _auto_save(ui.input(label='播放间隔max', value=get_nested_value(config, "play_audio", "play_interval_max", default=get_nested_value(config, "play_audio", "normal_interval_max")), placeholder='每条音频播放结束后到播放下一条之间的间隔时间上限，单位：秒').style("width:100%;").tooltip('每条音频播放结束后到播放下一条之间的间隔时间上限，单位：秒。实际间隔在 min 与 max 之间随机取值'), ("play_audio", "play_interval_max"))
            # UC2a（2026-10-22 终门裁决）：「音频输出路径」用户可配置性移除——out_path
            # 保留为系统内部透明临时目录（TTS 产物落盘必需），不再暴露给用户配置。
            player_select = _auto_save(ui.select(
                label='音频播放器',
                options={
                    'pygame': 'pygame（系统自带默认）',
                    'builtin': '内置播放器（进程内队列：插队/暂停/续播）',
                    'audio_player_v2': 'audio_player_v2（外部服务模式）',
                    'audio_player': 'audio_player（外部服务模式）',
                },
                value=get_nested_value(config, "play_audio", "player", default='pygame')
            ).style("width:100%;").tooltip('选用的音频播放器，默认pygame不需要再安装其他程序。'
                '内置播放器=系统进程内队列播放（插队仅重排待播顺序，不打断当前播放——native F1 预期管理）。'
                'audio_player(_v2)=外部服务模式（需自行启动外部项目；兼容保留，builtin 稳定一个版本后移除——sunset 判据登记 TODOS，native F4）。'
                '修改后重启主系统生效（D11）'), ("play_audio", "player"))

        # D3：EDTalk 激活时播放器选择视觉锁死 + 常驻提示（可改但无效=认知失调路径）
        if edtalk_active:
            player_select.disable()
            ui.label('数字人模式下音频由 EDTalk 播放，播放器选择不生效').style(
                f'color: var(--color-warning, #d97706); font-size: var(--font-size-assist, 12px);')

    # ---------- 内置播放器卡（选 builtin 时相关） ----------
    with ui.card().style(card_css) as builtin_card:
        ui.label('内置播放器')
        with ui.grid(columns=3):
            device_select = ui.select(
                label='输出设备', options={'-1': '系统默认'}, value='-1',
            ).style("width:100%;").tooltip('枚举系统输出声卡；打开失败时自动回退系统默认设备并报错（R1）')
            _auto_save(ui.input(label='待播队列上限', value=str(get_nested_value(config, "audio_player", "queue_max", default=50) or 50)).style("width:100%;").tooltip('满载后新音频阻塞等待消费（背压，不丢弃台词）'), ("audio_player", "queue_max"))
            with ui.column().style("width:100%;"):
                _auto_save(ui.switch('播放后随机间隔', value=get_nested_value(config, "audio_player", "random_audio_interval", "enable", default=False)).style(switch_internal_css).tooltip('每条播放完后在上下限之间随机休眠（秒）。默认关闭：播放间隔由上方"普通音频播放间隔"负责，避免双重间隔'), ("audio_player", "random_audio_interval", "enable"))
                _auto_save(ui.input(label='随机间隔下限(秒)', value=get_nested_value(config, "audio_player", "random_audio_interval", "min", default=0.1)).style("width:100%;"), ("audio_player", "random_audio_interval", "min"))
                _auto_save(ui.input(label='随机间隔上限(秒)', value=get_nested_value(config, "audio_player", "random_audio_interval", "max", default=3)).style("width:100%;"), ("audio_player", "random_audio_interval", "max"))

        # D4：优先级映射只读折叠区（默认收起；dx7 逃生门注明）
        # A5/F-4（2026-10-22 审查批次）：数据源切换 filter.priority_mapping（权威单源），
        # audio_player.priority_mapping 配置节随 A3 清理退役。
        with ui.expansion('优先级映射（只读）', icon='low_priority').style("width:100%;"):
            priority_map = get_nested_value(config, "filter", "priority_mapping", default={}) or {}
            try:
                ranked = sorted(priority_map.items(), key=lambda kv: -float(kv[1]))
                rows = ["  ".join(f"{k} = {v}" for k, v in ranked[i:i+3]) for i in range(0, len(ranked), 3)]
                ui.label("\n".join(rows) if rows else "（未配置）").style(
                    'white-space: pre-wrap; font-size: var(--font-size-assist, 12px);')
            except Exception:
                ui.label("（映射表格式异常）")
            ui.label('数值越大越先播；未识别类型排队尾。在「互动设置-优先级配置」页调整（单源：filter.priority_mapping）。').style(
                'font-size: var(--font-size-assist, 12px);')

    # ---------- 外部服务 API 地址卡（仅外部模式显示；0G cherry-pick #4 动态显隐） ----------
    with ui.card().style(card_css) as external_card:
        ui.label('audio_player（外部服务模式）')
        with ui.grid(columns=3):
            _auto_save(ui.input(
                label='API地址',
                value=get_nested_value(config, "audio_player", "api_ip_port"),
                placeholder='audio_player的API地址，只需要 http://ip:端口 即可',
                validation={
                    '请输入正确格式的URL': lambda value: is_url_check(value),
                }
            ).style("width:100%;").tooltip('仅在 音频播放器：audio_player/audio_player_v2 情况下填写'), ("audio_player", "api_ip_port"))

    # ---------- 控制区（D5/D6/D7/D9：状态行+四按钮+1s轮询+错误条） ----------
    with ui.card().style(card_css) as control_card:
        ui.label('播放控制')
        status_label = ui.label('内置播放器未启用').style(
            'font-size: var(--font-size-body, 14px);')
        error_label = ui.label('').style(
            'color: var(--color-danger, #dc2626); font-size: var(--font-size-assist, 12px); display:none;')
        with ui.row():
            btn_pause = ui.button('暂停', on_click=lambda: _control('pause'))
            btn_resume = ui.button('续播', on_click=lambda: _control('resume'))
            btn_skip = ui.button('跳过当前', on_click=lambda: _control('skip'))
            btn_clear = ui.button('清空队列', on_click=lambda: _control('clear'))
        for b in (btn_pause, btn_resume, btn_skip, btn_clear):
            b.props('dense outline')

    # ---------- 音频随机变速卡（A2 后语义：合成侧 TTS 语速；A3 移除文案变速列） ----------
    with ui.card().style(card_css):
        ui.label('音频随机变速')
        with ui.grid(columns=3):
            _auto_save(ui.switch('普通音频变速', value=get_nested_value(config, "audio_random_speed", "normal", "enable")).style(switch_internal_css).tooltip('启用后针对弹幕回复、助播等 TTS 合成音频，在语速上下限之间随机取值，于 TTS 合成时调整语速参数（gpt-sovits speed_factor / edge-tts rate+pitch 联动），无失真。本地音频与点歌歌曲不变速'), ("audio_random_speed", "normal", "enable"))
            _auto_save(ui.input(label='速度下限', value=get_nested_value(config, "audio_random_speed", "normal", "speed_min")).style("width:100%;").tooltip('语速倍率下限（1.0=正常语速），最终语速在上下限之间随机取值；合法域 [0.25, 4.0]，非法值自动回落 1.0'), ("audio_random_speed", "normal", "speed_min"))
            _auto_save(ui.input(label='速度上限', value=get_nested_value(config, "audio_random_speed", "normal", "speed_max")).style("width:100%;").tooltip('语速倍率上限（1.0=正常语速），最终语速在上下限之间随机取值；合法域 [0.25, 4.0]，非法值自动回落 1.0'), ("audio_random_speed", "normal", "speed_max"))
            # A3（2026-10-22 审查批次）：「文案音频变速」参数列退役移除——文案模块已
            # 弃用，变速功能仅需普通音频一条逻辑（设计项7）。

    # ---------- 行为逻辑 ----------
    def _refresh_visibility():
        player_value = player_select.value
        is_builtin = player_value == 'builtin'
        is_external = player_value in ('audio_player', 'audio_player_v2')
        builtin_card.visible = is_builtin
        external_card.visible = is_external
        control_card.visible = is_builtin
        if is_builtin:
            _load_devices_once()

    def _load_devices_once():
        """设备枚举（D8：loading 态→空态/列表；只在打开页面首次枚举）"""
        if getattr(_load_devices_once, "_done", False):
            return
        _load_devices_once._done = True
        device_select.options = {'-1': '正在枚举设备…'}
        device_select.update()

        def _job():
            try:
                resp = requests.get(f"{api_base}/builtin_status", timeout=2)
                data = resp.json()
                if not data.get("builtin"):
                    device_select.options = {'-1': '运行系统后可用（当前未运行）'}
                    device_select.value = '-1'
                    device_select.update()
                    return
                try:
                    resp2 = requests.post(f"{api_base}/builtin_control",
                                          json={"action": "list_devices"}, timeout=5)
                    devices = resp2.json().get("devices", [])
                except Exception:
                    devices = []
                if devices:
                    options = {str(d["device_index"]): str(d["device_info"]) for d in devices}
                    options["-1"] = "系统默认"
                else:
                    options = {'-1': '未检测到音频设备，将使用系统默认'}
                device_select.options = options
                saved = str(get_nested_value(config, "audio_player", "device_index", default=-1))
                device_select.value = saved if saved in options else '-1'
                device_select.update()
            except Exception:
                device_select.options = {'-1': '系统默认（枚举失败：主系统未运行）'}
                device_select.value = '-1'
                device_select.update()

        ui.timer(0.5, _job, once=True)

    def _control(action: str):
        try:
            requests.post(f"{api_base}/builtin_control", json={"action": action}, timeout=3)
        except Exception:
            pass  # 失败由 1s 轮询的状态/错误条反映，不打断 UI 线程

    def _poll_status():
        """1s 轮询（D7：仅 builtin 时有意义；轻量摘要，不持锁全量拷贝）"""
        if player_select.value != 'builtin':
            return
        try:
            resp = requests.get(f"{api_base}/builtin_status", timeout=1.5)
            data = resp.json()
            if not data.get("builtin"):
                status_label.text = '运行系统后可用（内置播放器随主系统启动）'
                return
            status = data.get("status") or {}
            current = status.get("current") or {}
            if status.get("paused"):
                status_label.text = '已暂停'
            elif status.get("playing"):
                content = current.get("content", "")
                status_label.text = f'播放中：[{current.get("type", "")}] {content}…（队列 {status.get("queue_len", 0)} 条）'
            else:
                status_label.text = f'空闲（队列 {status.get("queue_len", 0)} 条）'
            btn_pause.disable(status.get("paused"))
            btn_resume.enable(status.get("paused"))
            btn_skip.disable(not (status.get("playing") or status.get("paused")))

            # 错误条（D9）：最近一次播放器错误/字幕推送失败浮出
            err = status.get("last_error") or {}
            if data.get("captions_failures", 0) >= 3:
                error_label.text = '字幕推送失败（连续 3 次）——请检查字幕页/OBS 源'
                error_label.style('color: var(--color-danger, #dc2626); font-size: var(--font-size-assist, 12px);')
            elif err.get("message"):
                error_label.text = f'最近错误：{err.get("message", "")[:120]}'
                error_label.style('color: var(--color-danger, #dc2626); font-size: var(--font-size-assist, 12px);')
            else:
                error_label.style('display:none;')
        except Exception:
            status_label.text = '运行系统后可用（内置播放器随主系统启动）'

    # 设备选择写回 config（自动保存绑定之外的动态控件）
    # on_value_change（有 .value）；底层 .on('update:value') 的事件对象无 .value，
    # int(e.value) 抛 AttributeError 被下方 except 静默吞掉 → 设备选择从不落盘
    def _on_device_change(e):
        try:
            cfg_section = config.setdefault("audio_player", {}) if isinstance(config, dict) else None
            if cfg_section is not None:
                cfg_section["device_index"] = int(e.value)
                set_config_callback()
        except Exception:
            pass
    device_select.on_value_change(_on_device_change)

    player_select.on('update:value', lambda e: _refresh_visibility())
    _refresh_visibility()
    ui.timer(1.0, _poll_status)
