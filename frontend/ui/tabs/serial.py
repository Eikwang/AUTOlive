# -*- coding: UTF-8 -*-
"""
串口标签页模块
从 webui-bak.py 的 serial_page 拆分而来

布局规范：
- 设置项用最多 3 列网格排布，组件宽度跟随列宽（width:100%）

2026-10-24 配置组重构：
- 配置组改为配置驱动渲染（原实现的增删按钮只改 UI 不落盘，组字段也从不保存）
- 组内 串口名/波特率/发送数据类型 经 auto_save 按 ("serial","config",下标,字段) 落盘
- 刷新串口/打开串口/关闭串口/发送 按钮接线到本进程 SerialManager 单例
  （测试连接用：与主系统进程跨进程共享物理串口，测试完务必点「关闭串口」）
"""
from nicegui import ui
from typing import Dict, Any, Callable

from frontend.ui.components.config_helper import get_nested_value

# 新增串口配置组的默认模板（字段与 config.example.json serial.config[] 对齐）
_NEW_GROUP_TEMPLATE: Dict[str, Any] = {
    "serial_name": "COM1",
    "baudrate": "115200",
    "serial_data_type": "ASCII",
}

_BAUD_OPTIONS = {'9600': '9600', '19200': '19200', '38400': '38400', '115200': '115200'}


def _initial_port_options(current: str) -> Dict[str, str]:
    """渲染期同步枚举本机串口（不打开设备；pyserial 缺失时仅保留当前值）。"""
    options: Dict[str, str] = {}
    try:
        import serial.tools.list_ports
        for port_info in serial.tools.list_ports.comports():
            options[port_info.device] = port_info.device
    except Exception:
        pass
    if current and current not in options:
        options = {current: current, **options}
    return options


def create_serial_tab(
    config: Dict[str, Any],
    theme_config: Dict[str, str],
    set_config_callback: Callable
):
    """创建串口标签页"""
    card_css = theme_config.get("card", "")
    button_internal_color = theme_config.get("button_internal_color", "primary")
    button_internal_css = theme_config.get("button_internal_css", "")

    # 导入自动绑定函数
    from frontend.utils.config_auto_save import create_auto_save
    _auto_save = create_auto_save(config, set_config_callback, tab_key="serial")

    # 提示信息
    with ui.element('div').classes('p-2 hint-callout'):
        ui.label('此页配置串口（波特率等），供「按键映射」的串口触发使用：命中关键词/礼物后，'
                 '系统按配置组向对应串口发送数据。\n'
                 '「打开串口/发送」仅用于本页连接测试——测试完成后务必点「关闭串口」，'
                 '否则会跨进程占用串口，导致主系统运行时无法打开。')

    def _get_groups() -> list:
        return get_nested_value(config, "serial", "config") or []

    def _persist_groups():
        """配置组整表落盘（列表对象即 config 内引用，改动已就地生效）。"""
        set_config_callback("serial", "config", value=_get_groups())

    def _add_group():
        groups = get_nested_value(config, "serial", "config")
        if groups is None:
            groups = []
            config.setdefault("serial", {})["config"] = groups
        groups.append(dict(_NEW_GROUP_TEMPLATE))
        _persist_groups()
        _render_groups()
        ui.notify(position="top", type="positive", message=f"已新增串口配置组 #{len(groups)}")

    def _delete_group(index: int):
        groups = _get_groups()
        if not (0 <= index < len(groups)):
            ui.notify(position="top", type="warning", message=f"串口配置组 #{index + 1} 不存在")
            return
        groups.pop(index)
        _persist_groups()
        _render_groups()
        ui.notify(position="top", type="positive", message=f"已删除串口配置组 #{index + 1}")

    # ---------------- 串口操作按钮（本进程 SerialManager 单例，连接测试用） ----------------
    async def _refresh_ports(port_select):
        try:
            from utils.serial_manager_instance import get_serial_manager
            ports = await get_serial_manager().list_ports()
        except Exception as e:
            ui.notify(position="top", type="negative", message=f"刷新串口列表失败（需 pyserial）：{e}")
            return
        options = {p: p for p in ports}
        current = port_select.value
        if current and current not in options:
            options = {current: current, **options}
        port_select.options = options
        port_select.update()
        ui.notify(position="top", type="positive", message=f"本机串口：{', '.join(ports) if ports else '未检测到'}")

    async def _open_port(port_select, baud_select):
        port = port_select.value
        if not port:
            ui.notify(position="top", type="warning", message="请先选择串口名")
            return
        try:
            baud = int(baud_select.value or 115200)
        except (TypeError, ValueError):
            baud = 115200
        from utils.serial_manager_instance import get_serial_manager
        result = await get_serial_manager().connect(port, baud)
        ui.notify(position="top", type="positive" if result.get("ret") else "warning", message=result.get("msg", ""))

    async def _close_port(port_select):
        port = port_select.value
        if not port:
            ui.notify(position="top", type="warning", message="请先选择串口名")
            return
        from utils.serial_manager_instance import get_serial_manager
        result = await get_serial_manager().disconnect(port)
        ui.notify(position="top", type="positive" if result.get("ret") else "warning", message=result.get("msg", ""))

    async def _send_port(port_select, type_select, data_input):
        port = port_select.value
        data = data_input.value
        if not port or not data:
            ui.notify(position="top", type="warning", message="请先选择串口并填写发送数据")
            return
        from utils.serial_manager_instance import get_serial_manager
        result = await get_serial_manager().send_data(port, data, type_select.value or "ASCII")
        ui.notify(position="top", type="positive" if result.get("ret") else "warning", message=result.get("msg", ""))

    # ---------------- 配置组渲染 ----------------
    def _render_groups():
        groups_box.clear()
        groups = _get_groups()
        with groups_box:
            if not groups:
                ui.label('暂无串口配置组，点击「增加串口配置组」创建').style('opacity:.7; padding:8px;')
            for index, serial_config in enumerate(groups):
                with ui.card().style(card_css):
                    with ui.row().style('width:100%; align-items:center; justify-content:space-between;'):
                        ui.label(f'串口配置组 #{index + 1}')
                        ui.button('删除本组', on_click=lambda _e, i=index: _delete_group(i), color=button_internal_color).style(button_internal_css)
                    with ui.grid(columns=3):
                        current_name = str(serial_config.get("serial_name", "COM1") or "")
                        port_select = _auto_save(
                            ui.select(label=f"串口名#{index + 1}", value=current_name, options=_initial_port_options(current_name)).style("width:100%;"),
                            keys=("serial", "config", str(index), "serial_name"))
                        baud_select = _auto_save(
                            ui.select(label=f"波特率#{index + 1}", value=str(serial_config.get("baudrate", "115200")), options=_BAUD_OPTIONS).style("width:100%;"),
                            keys=("serial", "config", str(index), "baudrate"))
                        type_select = _auto_save(
                            ui.select(label=f"发送数据类型#{index + 1}", value=serial_config.get("serial_data_type", "ASCII"), options={'ASCII': 'ASCII', 'HEX': 'HEX'}).style("width:100%;"),
                            keys=("serial", "config", str(index), "serial_data_type"))
                        # 测试发送内容：仅本页连接测试用，不属于配置（不落盘）
                        data_input = ui.input(label=f"发送数据#{index + 1}", value="", placeholder='填要发的内容，连接后，点 发送').style("width:100%;")
                    with ui.row():
                        ui.button('刷新串口', on_click=lambda _e, s=port_select: _refresh_ports(s), color=button_internal_color).style(button_internal_css)
                        ui.button('打开串口', on_click=lambda _e, s=port_select, b=baud_select: _open_port(s, b), color=button_internal_color).style(button_internal_css)
                        ui.button('关闭串口', on_click=lambda _e, s=port_select: _close_port(s), color=button_internal_color).style(button_internal_css)
                        ui.button('发送', on_click=lambda _e, s=port_select, t=type_select, d=data_input: _send_port(s, t, d), color=button_internal_color).style(button_internal_css)

    # 增删按钮工具栏 + 组容器 + 首次渲染
    with ui.row():
        ui.button('增加串口配置组', on_click=lambda _e: _add_group(), color=button_internal_color).style(button_internal_css)
    groups_box = ui.column().style("width:100%;")
    _render_groups()
