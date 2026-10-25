# -*- coding: UTF-8 -*-
"""
按键映射标签页模块
从 common_config.py 拆分而来

布局规范：
- 设置项用最多 3 列网格排布，组件宽度跟随列宽（width:100%）

2026-10-24 配置组重构：
- 配置组改为配置驱动渲染（原实现固定渲染存量组数且 9 个字段从不落盘）
- 新增「增加配置组」与每组「删除本组」按钮，增删后整表落盘并重渲染
- 组内 6 字段经 auto_save 按 ("key_mapping","config",下标,字段) 实时落盘

2026-10-24 二次精简：
- 移除 文案/本地音频/图片 三路触发（前端参数 + 配置组字段同步移除）
- 顶部参数改 2×2：命令前缀|捕获类型 / 按键触发类型|串口触发类型
- 配置组卡片双列布局（一行两组），同类输入框统一尺寸（textarea 64px）
"""
from nicegui import ui
from typing import Dict, Any, Callable
from frontend.ui.components.config_helper import get_nested_value
from frontend.utils.common import textarea_data_change

# 新增配置组的默认模板（字段与 config.example.json key_mapping.config[] 对齐）
_NEW_GROUP_TEMPLATE: Dict[str, Any] = {
    "keywords": [],
    "gift": [],
    "keys": [],
    "similarity": "1",
    "serial_name": "",
    "serial_send_data": [],
}

# 统一 textarea 高度（同类输入框等高；单行输入框保持默认高度）
_TEXTAREA_HEIGHT = 'input-style="height: 64px"'


def create_key_mapping_tab(
    config: Dict[str, Any],
    theme_config: Dict[str, str],
    set_config_callback: Callable
):
    """创建按键映射标签页"""
    card_css = theme_config.get("card", "")
    button_internal_color = theme_config.get("button_internal_color", "primary")
    button_internal_css = theme_config.get("button_internal_css", "")

    # 导入自动绑定函数
    from frontend.utils.config_auto_save import create_auto_save
    _auto_save = create_auto_save(config, set_config_callback, tab_key="key_mapping")

    # 命令前缀/捕获类型/触发类型（2×2 布局）
    with ui.card().style(card_css):
        ui.label('按键/串口 映射')
        with ui.grid(columns=2):
            _auto_save(ui.input(label='命令前缀', value=get_nested_value(config, "key_mapping", "start_cmd"), placeholder='命令起始字符串').style("width:100%;"), keys=("key_mapping", "start_cmd"))
            _auto_save(ui.select(label='捕获类型', options={'弹幕': '弹幕', '回复': '回复', '弹幕+回复': '弹幕+回复'}, value=get_nested_value(config, "key_mapping", "type")).style("width:100%;"), keys=("key_mapping", "type"))
            _auto_save(ui.select(label='按键触发类型', options={'不启用': '不启用', '关键词': '关键词', '礼物': '礼物', '关键词+礼物': '关键词+礼物'}, value=get_nested_value(config, "key_mapping", "key_trigger_type")).style("width:100%;"), keys=("key_mapping", "key_trigger_type"))
            _auto_save(ui.select(label='串口触发类型', options={'不启用': '不启用', '关键词': '关键词', '礼物': '礼物', '关键词+礼物': '关键词+礼物'}, value=get_nested_value(config, "key_mapping", "serial_trigger_type")).style("width:100%;"), keys=("key_mapping", "serial_trigger_type"))

    # ---------------- 配置组区域（双列布局，支持增删） ----------------
    def _get_groups() -> list:
        return get_nested_value(config, "key_mapping", "config") or []

    def _persist_groups():
        """配置组整表落盘（列表对象即 config 内引用，改动已就地生效）。"""
        set_config_callback("key_mapping", "config", value=_get_groups())

    def _add_group():
        groups = get_nested_value(config, "key_mapping", "config")
        if groups is None:
            groups = []
            config.setdefault("key_mapping", {})["config"] = groups
        groups.append(dict(_NEW_GROUP_TEMPLATE))
        _persist_groups()
        _render_groups()
        ui.notify(position="top", type="positive", message=f"已新增配置组 #{len(groups)}")

    def _delete_group(index: int):
        groups = _get_groups()
        if not (0 <= index < len(groups)):
            ui.notify(position="top", type="warning", message=f"配置组 #{index + 1} 不存在")
            return
        groups.pop(index)
        _persist_groups()
        _render_groups()
        ui.notify(position="top", type="positive", message=f"已删除配置组 #{index + 1}")

    def _render_groups():
        groups_box.clear()
        groups = _get_groups()
        with groups_box:
            if not groups:
                ui.label('暂无配置组，点击「增加配置组」创建').style('opacity:.7; padding:8px;')
            for index, key_mapping_config in enumerate(groups):
                with ui.card().style(card_css):
                    with ui.row().style('width:100%; align-items:center; justify-content:space-between;'):
                        ui.label(f'配置组 #{index + 1}')
                        ui.button('删除本组', on_click=lambda _e, i=index: _delete_group(i), color=button_internal_color).style(button_internal_css)
                    # 行内同类型字段同排，保证等高（3 行：文本/文本/短输入）
                    with ui.grid(columns=2):
                        _auto_save(ui.textarea(label=f"关键词#{index + 1}", value=textarea_data_change(key_mapping_config.get("keywords", [])), placeholder='触发的关键词').style("width:100%;").props(_TEXTAREA_HEIGHT), keys=("key_mapping", "config", str(index), "keywords"), split_lines=True)
                        _auto_save(ui.textarea(label=f"礼物#{index + 1}", value=textarea_data_change(key_mapping_config.get("gift", [])), placeholder='触发的礼物名').style("width:100%;").props(_TEXTAREA_HEIGHT), keys=("key_mapping", "config", str(index), "gift"), split_lines=True)
                        _auto_save(ui.textarea(label=f"按键#{index + 1}", value=textarea_data_change(key_mapping_config.get("keys", [])), placeholder='映射的按键').style("width:100%;").props(_TEXTAREA_HEIGHT), keys=("key_mapping", "config", str(index), "keys"), split_lines=True)
                        _auto_save(ui.textarea(label=f"串口发送内容#{index + 1}", value=textarea_data_change(key_mapping_config.get("serial_send_data", [])), placeholder='命中后发送到串口的数据，多行随机取一条').style("width:100%;").props(_TEXTAREA_HEIGHT), keys=("key_mapping", "config", str(index), "serial_send_data"), split_lines=True)
                        _auto_save(ui.input(label=f"相似度#{index + 1}", value=key_mapping_config.get("similarity", 1), placeholder='相似度').style("width:100%;"), keys=("key_mapping", "config", str(index), "similarity"))
                        _auto_save(ui.input(label=f"串口名#{index + 1}", value=key_mapping_config.get("serial_name", ""), placeholder='例如：COM1').style("width:100%;"), keys=("key_mapping", "config", str(index), "serial_name"))

    # 增删按钮工具栏 + 组容器（双列网格：一行两个配置组） + 首次渲染
    with ui.row():
        ui.button('增加配置组', on_click=lambda _e: _add_group(), color=button_internal_color).style(button_internal_css)
    groups_box = ui.grid(columns=2).style("width:100%;")
    _render_groups()
