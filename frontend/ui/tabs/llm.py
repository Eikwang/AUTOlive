from frontend.ui.components.config_helper import get_nested_value
"""
大语言模型标签页模块
从 webui-bak.py 的 llm_page 拆分而来

布局规范：
- 设置项用最多 3 列网格排布，组件宽度跟随列宽（width:100%）
"""
from nicegui import ui
from typing import Dict, Any, Callable

from frontend.ui.components import FormField
from frontend.utils.common import is_url_check


def create_llm_tab(
    config: Dict[str, Any],
    theme_config: Dict[str, str],
    set_config_callback: Callable
):
    """
    创建大语言模型标签页

    Args:
        config: 配置字典
        theme_config: 主题配置
        set_config_callback: 配置设置回调函数
    """
    card_css = theme_config.get("card", "")

    # 自定义LLM配置
    if get_nested_value(config, "webui", "show_card", "llm", "custom_llm"):
        with ui.card().style(card_css):
            ui.label("自定义LLM")

            with ui.grid(columns=3):
                # API URL
                FormField.create_textarea(
                    label="API URL",
                    value=get_nested_value(config, "custom_llm", "url"),
                    placeholder='发送HTTP请求的API链接',
                    on_change=lambda e: set_config_callback("custom_llm", "url", e.value),
                    tooltip='发送HTTP请求的API链接',
                    style="width:100%;"
                )

                # API类型
                FormField.create_select(
                    label="API类型",
                    options={"GET": "GET", "POST": "POST"},
                    value=get_nested_value(config, "custom_llm", "method"),
                    on_change=lambda e: set_config_callback("custom_llm", "method", e.value),
                    tooltip='API类型',
                    style="width:100%;"
                )

                # 请求头
                FormField.create_textarea(
                    label="请求头",
                    value=get_nested_value(config, "custom_llm", "headers"),
                    placeholder='换行分隔，例：Content-Type:application/json\nAuthorization:Bearer sk',
                    on_change=lambda e: set_config_callback("custom_llm", "headers", e.value),
                    tooltip='换行分隔，例：Content-Type:application/json\nAuthorization:Bearer sk',
                    style="width:100%;"
                )

                # 代理
                FormField.create_textarea(
                    label="代理",
                    value=get_nested_value(config, "custom_llm", "proxies"),
                    placeholder='requests库代理配置方法，json数据用"双引号',
                    on_change=lambda e: set_config_callback("custom_llm", "proxies", e.value),
                    tooltip='requests库代理配置方法，json数据用"双引号',
                    style="width:100%;"
                )

            with ui.grid(columns=3):
                # 请求体类型
                FormField.create_select(
                    label="请求体类型",
                    options={"json": "json", "raw": "raw"},
                    value=get_nested_value(config, "custom_llm", "body_type"),
                    on_change=lambda e: set_config_callback("custom_llm", "body_type", e.value),
                    tooltip='请求体类型',
                    style="width:100%;"
                )

                # 请求体
                FormField.create_textarea(
                    label="请求体",
                    value=get_nested_value(config, "custom_llm", "body"),
                    placeholder='请求体，写字符串，注意变量需要两个大括号包裹{{}}，json数据的话用"双引号',
                    on_change=lambda e: set_config_callback("custom_llm", "body", e.value),
                    tooltip='请求体，写字符串，注意变量需要两个大括号包裹{{}}，json数据的话用"双引号',
                    style="width:100%;"
                )

                # 请求返回数据类型
                FormField.create_select(
                    label="请求返回数据类型",
                    options={"json": "json", "content": "content"},
                    value=get_nested_value(config, "custom_llm", "resp_data_type"),
                    on_change=lambda e: set_config_callback("custom_llm", "resp_data_type", e.value),
                    tooltip='请求返回数据类型',
                    style="width:100%;"
                )

                # 数据解析
                FormField.create_textarea(
                    label="数据解析（eval执行）",
                    value=get_nested_value(config, "custom_llm", "data_analysis"),
                    placeholder='数据解析，请不要随意修改resp变量，会被用于最后返回数据内容的解析',
                    on_change=lambda e: set_config_callback("custom_llm", "data_analysis", e.value),
                    tooltip='数据解析，请不要随意修改resp变量，会被用于最后返回数据内容的解析',
                    style="width:100%;"
                )

                # 返回内容模板
                FormField.create_textarea(
                    label="返回内容模板",
                    value=get_nested_value(config, "custom_llm", "resp_template"),
                    placeholder='请不要随意删除data变量，支持动态变量，最终会合并成完成内容进行音频合成',
                    on_change=lambda e: set_config_callback("custom_llm", "resp_template", e.value),
                    tooltip='请不要随意删除data变量，支持动态变量，最终会合并成完成内容进行音频合成',
                    style="width:100%;"
                )
