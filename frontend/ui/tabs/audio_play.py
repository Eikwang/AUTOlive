# -*- coding: UTF-8 -*-
"""
音频播放标签页模块（2026-10-30 审查批次：本地播放统一 builtin 内置播放器）

布局规范：
- 设置项用最多 2 列网格排布，组件宽度跟随列宽（width:100%）
- 开关+关联参数 为上下结构单元：开关在上，输入框在下

2026-10-30 审查批次：
- 「音频播放器」选择（play_audio.player）与外部 audio_player 服务模式退役——
  builtin 进程内播放器为唯一本地播放后端，页面常驻无需切换
- 「播放间隔min/max」（play_audio.play_interval_min/max）退役移除——消费回传
  机制下由播放完成回执驱动下一条取用，无需间隔等待
- 「启用文本切分」改名「文本切分」；「普通音频变速」改名「随机变速」；两列布局

整合义务（计划 §6.1，沿用）：
- 设备下拉（异步枚举，loading/空态 D8）+ 只读优先级映射折叠区（D4，含 config
  手改逃生门注明 dx7）+ queue_max
- 控制区（D5/D6/D7/D9）：状态回显行 + 四按钮 + 1s 轮询 + 错误条
"""
import json

import requests
from nicegui import ui
from typing import Dict, Any, Callable

from frontend.ui.components.config_helper import get_nested_value


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

    with ui.card().style(card_css):
        ui.label('音频播放')
        with ui.grid(columns=2):
            _auto_save(ui.switch('文本切分', value=get_nested_value(config, "play_audio", "text_split_enable")).style(switch_internal_css).tooltip('启用后会将LLM等待合成音频的消息按四句一切的方式分段（每段最多四句），以便TTS快速合成'), ("play_audio", "text_split_enable"))
            _auto_save(ui.switch('随机变速', value=get_nested_value(config, "audio_random_speed", "normal", "enable")).style(switch_internal_css).tooltip('启用后针对弹幕回复、助播等 TTS 合成音频，在语速上下限之间随机取值，于 TTS 合成时调整语速参数（gpt-sovits speed_factor / edge-tts rate+pitch 联动），无失真。本地音频与点歌歌曲不变速'), ("audio_random_speed", "normal", "enable"))
            _auto_save(ui.input(label='速度下限', value=get_nested_value(config, "audio_random_speed", "normal", "speed_min")).style("width:100%;").tooltip('语速倍率下限（1.0=正常语速），最终语速在上下限之间随机取值；合法域 [0.25, 4.0]，非法值自动回落 1.0'), ("audio_random_speed", "normal", "speed_min"))
            _auto_save(ui.input(label='速度上限', value=get_nested_value(config, "audio_random_speed", "normal", "speed_max")).style("width:100%;").tooltip('语速倍率上限（1.0=正常语速），最终语速在上下限之间随机取值；合法域 [0.25, 4.0]，非法值自动回落 1.0'), ("audio_random_speed", "normal", "speed_max"))
            # 2026-10-30 审查批次：「播放间隔min/max」与「音频播放器」选择随
            # 消费回传机制/统一 builtin 播放器退役移除（见模块 docstring）。
            # UC2a（2026-10-22 终门裁决）：「音频输出路径」用户可配置性移除——
            # out_path 保留为系统内部透明临时目录（TTS 产物落盘必需）。

    # ---------- 内置播放器卡（唯一本地播放后端，常驻） ----------
    with ui.card().style(card_css):
        ui.label('内置播放器')
        with ui.grid(columns=3):
            device_select = ui.select(
                label='输出设备', options={'-1': '系统默认'}, value='-1',
            ).style("width:100%;").tooltip('枚举系统输出声卡；打开失败时自动回退系统默认设备并报错（R1）')
            _auto_save(ui.input(label='待播队列上限', value=str(get_nested_value(config, "audio_player", "queue_max", default=50) or 50)).style("width:100%;").tooltip('满载后新音频阻塞等待消费（背压，不丢弃台词）'), ("audio_player", "queue_max"))
            with ui.column().style("width:100%;"):
                _auto_save(ui.switch('播放后随机间隔', value=get_nested_value(config, "audio_player", "random_audio_interval", "enable", default=False)).style(switch_internal_css).tooltip('每条播放完后在上下限之间随机休眠（秒）。默认关闭：下一条音频由播放完成回执驱动立即取用'), ("audio_player", "random_audio_interval", "enable"))
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

    # ---------- 行为逻辑 ----------
    def _refresh_visibility():
        # 2026-10-30 审查批次：builtin 为唯一本地播放后端——播放器显隐切换退役，
        # 控制区/设备区常驻，仅保留设备枚举触发
        control_card.visible = True
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
        """1s 轮询（D7：轻量摘要，不持锁全量拷贝；builtin 常驻）"""
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

    _refresh_visibility()
    ui.timer(1.0, _poll_status)
