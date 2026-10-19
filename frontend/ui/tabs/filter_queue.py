# -*- coding: UTF-8 -*-
"""
消息队列&音频队列标签页模块
从 common_config.py 拆分而来

布局规范：
- 设置项用最多 3 列网格排布，组件宽度跟随列宽（width:100%）

D5 参数页重组（2026-10-19 切片 3）：
- 优先级配置改为单一有序列表：按来源分组（直播互动/本地输入/系统任务/助播），
  按当前优先级值降序统一编号，方向恒定标注"数值越大越优先"
- 移除图像识别/文案两项（uc3 裁决随模块退役，UI 不再渲染；config.json 残留键
  filter.priority_mapping.image_recognition_schedule / .copywriting 归收尾迁移清单）
- 提供 3 个预设按钮（互动优先/内容优先/省资源），一键写入各键
"""
from nicegui import ui
from typing import Dict, Any, Callable, List, Tuple
from frontend.ui.components.config_helper import get_nested_value

# 优先级条目（键 → 中文名），按来源分组；组内顺序=同值时的展示序
PRIORITY_GROUPS: List[Tuple[str, List[Tuple[str, str]]]] = [
    ("直播互动", [
        ("comment", "弹幕回复"),
        ("read_comment", "念弹幕"),
        ("entrance", "入场欢迎"),
        ("gift", "礼物答谢"),
        ("follow", "关注答谢"),
        ("song", "点歌"),
        ("integral", "积分"),
        ("key_mapping", "按键映射"),
    ]),
    ("本地输入", [
        ("talk", "聊天（语音输入）"),
        ("local_qa_audio", "本地问答-音频"),
        ("reread", "复读"),
        ("reread_top_priority", "最高优先级复读"),
    ]),
    ("系统任务", [
        ("schedule", "定时任务"),
        ("trends_copywriting", "动态文案"),
        ("idle_time_task", "闲时任务"),
        ("abnormal_alarm", "异常报警"),
    ]),
    ("助播", [
        ("assistant_anchor_text", "助播-文本"),
        ("assistant_anchor_audio", "助播-音频"),
    ]),
]

# 预设值（数值越大越优先；异常报警与最高优先级复读各预设恒高位）
PRIORITY_PRESETS: Dict[str, Dict[str, int]] = {
    "互动优先": {
        "comment": 80, "read_comment": 75, "gift": 70, "follow": 65,
        "entrance": 55, "song": 50, "integral": 45, "key_mapping": 40,
        "talk": 35, "local_qa_audio": 30, "reread": 25,
        "assistant_anchor_text": 20, "assistant_anchor_audio": 20,
        "schedule": 15, "trends_copywriting": 12, "idle_time_task": 10,
        "abnormal_alarm": 90, "reread_top_priority": 999,
    },
    "内容优先": {
        "schedule": 70, "trends_copywriting": 65, "song": 60, "idle_time_task": 55,
        "comment": 40, "read_comment": 35, "gift": 30, "follow": 28,
        "entrance": 25, "integral": 20, "key_mapping": 18,
        "talk": 15, "local_qa_audio": 15, "reread": 12,
        "assistant_anchor_text": 10, "assistant_anchor_audio": 10,
        "abnormal_alarm": 90, "reread_top_priority": 999,
    },
    "省资源": {
        "comment": 10, "read_comment": 10, "entrance": 10, "gift": 10,
        "follow": 10, "song": 10, "integral": 10, "key_mapping": 10,
        "talk": 10, "local_qa_audio": 10, "reread": 10,
        "assistant_anchor_text": 10, "assistant_anchor_audio": 10,
        "schedule": 10, "trends_copywriting": 10, "idle_time_task": 10,
        "abnormal_alarm": 90, "reread_top_priority": 999,
    },
}


def build_priority_presets() -> Dict[str, Dict[str, int]]:
    """返回预设名 → {优先级键: 值}（纯函数，供测试与 UI 预设按钮共用）"""
    return {name: dict(vals) for name, vals in PRIORITY_PRESETS.items()}


def iter_priority_entries(config: Dict[str, Any]) -> List[Tuple[int, str, str, str, Any]]:
    """按当前值降序产出优先级条目（纯函数，供测试与 UI 渲染共用）。

    Returns:
        [(序号, 组名, 键, 中文名, 当前值), ...]；config 中不存在的键跳过（缺省过滤，
        与任务书"UI 读不到就跳过"一致）。同值条目按组与组内声明顺序稳定排列。
    """
    entries = []
    for group_name, items in PRIORITY_GROUPS:
        for key, cn_name in items:
            value = get_nested_value(config, "filter", "priority_mapping", key)
            if value is None:
                continue
            entries.append((group_name, key, cn_name, value))
    # 值降序；None/异常值按 0 处理，稳定排序保持声明序
    entries.sort(key=lambda e: -float(e[3] or 0))
    return [(i + 1, g, k, n, v) for i, (g, k, n, v) in enumerate(entries)]


def create_filter_queue_tab(
    config: Dict[str, Any],
    theme_config: Dict[str, str],
    set_config_callback: Callable
):
    """创建消息队列&音频队列标签页"""
    card_css = theme_config.get("card", "")

    # 导入自动绑定函数
    from frontend.utils.config_auto_save import create_auto_save
    _auto_save = create_auto_save(config, set_config_callback, tab_key="filter_queue")

    with ui.card().style(card_css):
        with ui.expansion('待合成音频的消息&待播放音频队列', icon="settings", value=True).classes('w-full'):
            with ui.grid(columns=3):
                _auto_save(ui.input(label='消息队列最大保留长度', placeholder='收到的消息，生成的文本内容，会根据优先级存入消息队列，当新消息的优先级低于队列中所有的消息且超过此长度时，此消息将被丢弃', value=get_nested_value(config, "filter", "message_queue_max_len")).style("width:100%;").tooltip('收到的消息，生成的文本内容，会根据优先级存入消息队列，当新消息的优先级低于队列中所有的消息且超过此长度时，此消息将被丢弃'), keys=("filter", "message_queue_max_len"))
                _auto_save(ui.input(label='音频播放队列最大保留长度', placeholder='合成后的音频，会根据优先级存入待播放音频队列，当新音频的优先级低于队列中所有的音频且超过此长度时，此音频将被丢弃', value=get_nested_value(config, "filter", "voice_tmp_path_queue_max_len")).style("width:100%;").tooltip('合成后的音频，会根据优先级存入待播放音频队列，当新音频的优先级低于队列中所有的音频且超过此长度时，此音频将被丢弃'), keys=("filter", "voice_tmp_path_queue_max_len"))
                _auto_save(ui.input(
                    label='音频播放队列首次触发播放阈值',
                    placeholder='正整数 例如：20，如果你不想开播前缓冲一定数量的音频，请配置0',
                    value=get_nested_value(config, "filter", "voice_tmp_path_queue_min_start_play")
                ).style("width:100%;").tooltip('此功能用于缓存一定数量的音频后再开始播放。如果你不想开播前缓冲一定数量的音频，请配置0；如果你想提前准备一些音频，如因为TTS合成慢的原因，可以配置此值，让TTS提前合成你的其他任务触发的内容'), keys=("filter", "voice_tmp_path_queue_min_start_play"))

    # ---------- 优先级（D5：单一有序列表+来源分组+预设按钮） ----------
    with ui.card().style(card_css):
        ui.label('优先级配置')
        with ui.element('div').classes('p-2 bg-blue-100'):
            ui.label("统一列表按当前优先级降序编号（修改数值后刷新页面重排）。方向恒定：数值越大越优先，就会优先合成音频播放。请使用正整数。")
            ui.label("另外需要注意，由于shi山原因，目前这个队列内容是文本切分后计算的长度，所以如果回复内容过长，可能会有丢数据的情况")

        # 预设按钮（D5）：一键写入各键，走 _auto_save 同通道落盘
        preset_inputs: Dict[str, Any] = {}

        def _apply_preset(name: str):
            vals = build_priority_presets().get(name) or {}
            for key, v in vals.items():
                cfg_section = config.setdefault("filter", {}).setdefault("priority_mapping", {}) if isinstance(config, dict) else None
                if cfg_section is not None:
                    cfg_section[key] = v
                inp = preset_inputs.get(key)
                if inp is not None:
                    inp.value = str(v)  # 触发 on_value_change → set_config_callback
                elif cfg_section is not None:
                    # UI 未渲染的键（理论上不会发生）：直接落盘
                    set_config_callback("filter", "priority_mapping", key, value=v)
            ui.notify(position="top", type="positive", message=f"已应用预设「{name}」（18 项优先级已写入）")

        with ui.row():
            for preset_name in build_priority_presets():
                ui.button(preset_name, on_click=lambda n=preset_name: _apply_preset(n)).props('outline dense')

        # 单一有序列表：序号 | 来源分组 | 名称 | 优先级输入（按值降序）
        entries = iter_priority_entries(config)
        for rank, group_name, key, cn_name, value in entries:
            with ui.grid(columns=4).style("width:100%;align-items:center"):
                ui.label(f"#{rank}").style(
                    "font-weight: var(--font-weight-title, 600); min-width: 3em;")
                ui.label(f"{cn_name}").tooltip(f"来源：{group_name}").style("width:100%;")
                ui.label(f"({group_name})").style(
                    "font-size: var(--font-size-assist, 12px); color: var(--text-hint, #9ca3af);")
                preset_inputs[key] = _auto_save(
                    ui.input(label='优先级', value=value, placeholder='数字越大，优先级越高').style("width:100%;").tooltip(f'来源：{group_name}。数字越大，优先级越高'),
                    keys=("filter", "priority_mapping", key))

        # uc3 裁决移除项说明（图像识别/文案不再提供配置入口）
        ui.label("图像识别、文案两项已随模块退役移除（config 残留键随收尾迁移清理）").style(
            "font-size: var(--font-size-assist, 12px); color: var(--text-hint, #9ca3af);")
