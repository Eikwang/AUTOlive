# -*- coding: UTF-8 -*-
"""
串口标签页模块
从 webui-bak.py 的 serial_page 拆分而来

布局规范：
- 设置项用最多 3 列网格排布，组件宽度跟随列宽（width:100%）
"""
from nicegui import ui
from typing import Dict, Any, Callable

from frontend.ui.components.config_helper import get_nested_value


# 存储串口配置变量
serial_config_var = {}
serial_config_card = None


def _serial_config_add(config, card_css, switch_internal_css):
    """增加串口配置组"""
    data_len = len(serial_config_var)
    tmp_config = {
        "serial_name": "COM1",
        "baudrate": "115200",
        "serial_data_type": "ASCII"
    }

    with serial_config_card.style(card_css):
        with ui.grid(columns=3):
            serial_config_var[str(data_len)] = ui.select(label=f"串口名#{int(data_len / 8) + 1}", value=tmp_config["serial_name"], options={f'{tmp_config["serial_name"]}': f'{tmp_config["serial_name"]}'}).style("width:100%;")
            serial_config_var[str(data_len + 1)] = ui.select(
                label=f"波特率#{int(data_len / 8) + 1}",
                value=tmp_config["baudrate"],
                options={'9600': '9600', '19200': '19200', '38400': '38400', '115200': '115200'}
            ).style("width:100%;")
            serial_config_var[str(data_len + 5)] = ui.select(label=f"发送数据类型#{int(data_len / 8) + 1}", value=tmp_config["serial_data_type"], options={'ASCII': 'ASCII', 'HEX': 'HEX'}).style("width:100%;")
            serial_config_var[str(data_len + 6)] = ui.input(label=f"发送数据#{int(data_len / 8) + 1}", value="", placeholder='填要发的内容，连接后，点 发送').style("width:100%;")
            with ui.row():
                serial_config_var[str(data_len + 2)] = ui.button('刷新串口')
                serial_config_var[str(data_len + 3)] = ui.button('打开串口')
                serial_config_var[str(data_len + 4)] = ui.button('关闭串口')
                serial_config_var[str(data_len + 7)] = ui.button('发送')


def _serial_config_del(index):
    """删除串口配置组"""
    try:
        serial_config_card.remove(int(index) - 1)
        keys_to_delete = [str(8 * (int(index) - 1) + i) for i in range(8)]
        for key in keys_to_delete:
            if key in serial_config_var:
                del serial_config_var[key]

        updates = {}
        for key in sorted(serial_config_var.keys(), key=int):
            new_key = str(int(key) - 8 if int(key) > int(keys_to_delete[-1]) else key)
            updates[new_key] = serial_config_var[key]

        serial_config_var.clear()
        serial_config_var.update(updates)
    except Exception as e:
        ui.notify(position="top", type="negative", message=f"错误，索引值配置有误：{e}")


def create_serial_tab(
    config: Dict[str, Any],
    theme_config: Dict[str, str],
    set_config_callback: Callable
):
    """创建串口标签页"""
    global serial_config_card
    card_css = theme_config.get("card", "")
    switch_internal_css = theme_config.get("switch_internal", "")
    button_internal_color = theme_config.get("button_internal_color", "primary")
    button_internal_css = theme_config.get("button_internal_css", "")

    # 导入自动绑定函数
    from frontend.utils.config_auto_save import create_auto_save
    _auto_save = create_auto_save(config, set_config_callback, tab_key="serial")

    # 提示信息
    with ui.element('div').classes('p-2 bg-blue-100'):
        ui.label('此页完成串口配置后，可以在"通用配置"的 串口映射配置功能\n 注意！！！此处连接测试完成后，请 关闭串口，因为程序是跨进程使用的，所以不关掉会占用串口，导致无法正常使用！')

    # 增删按钮
    with ui.row():
        input_serial_config_index = ui.input(label='串口配置索引', value="", placeholder='串口配置组的排序号')
        button_serial_config_add = ui.button('增加串口配置组', on_click=lambda: _serial_config_add(config, card_css, switch_internal_css), color=button_internal_color).style(button_internal_css)
        button_serial_config_del = ui.button('删除串口配置组', on_click=lambda: _serial_config_del(input_serial_config_index.value), color=button_internal_color).style(button_internal_css)

    serial_config_card = ui.card()

    # 加载已有配置
    for index, serial_config in enumerate(config.get("serial", {}).get("config", [])):
        with serial_config_card.style(card_css):
            with ui.grid(columns=3):
                serial_config_var[str(8 * index)] = ui.select(label=f"串口名#{index + 1}", value=serial_config.get("serial_name", "COM1"), options={serial_config.get("serial_name", "COM1"): serial_config.get("serial_name", "COM1")}).style("width:100%;")
                serial_config_var[str(8 * index + 1)] = ui.select(
                    label=f"波特率#{index + 1}",
                    value=serial_config.get("baudrate", "115200"),
                    options={'9600': '9600', '19200': '19200', '38400': '38400', '115200': '115200'}
                ).style("width:100%;")
                serial_config_var[str(8 * index + 5)] = ui.select(label=f"发送数据类型#{index + 1}", value=serial_config.get("serial_data_type", "ASCII"), options={'ASCII': 'ASCII', 'HEX': 'HEX'}).style("width:100%;")
                serial_config_var[str(8 * index + 6)] = ui.input(label=f"发送数据#{index + 1}", value="", placeholder='填要发的内容，连接后，点 发送').style("width:100%;")
                with ui.row():
                    serial_config_var[str(8 * index + 2)] = ui.button('刷新串口')
                    serial_config_var[str(8 * index + 3)] = ui.button('打开串口')
                    serial_config_var[str(8 * index + 4)] = ui.button('关闭串口')
                    serial_config_var[str(8 * index + 7)] = ui.button('发送')
