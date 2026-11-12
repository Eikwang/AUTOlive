# -*- coding: UTF-8 -*-
"""
过滤标签页模块
从 common_config.py 拆分而来

布局规范：
- 设置项用最多 3 列网格排布，组件宽度跟随列宽（width:100%）
- 开关+关联参数 为上下结构单元：开关在上，输入框在下
"""
from nicegui import ui
from typing import Dict, Any, Callable
from frontend.ui.components.config_helper import get_nested_value


def create_filter_config_tab(
    config: Dict[str, Any],
    theme_config: Dict[str, str],
    set_config_callback: Callable
):
    """创建过滤标签页"""
    card_css = theme_config.get("card", "")
    switch_internal_css = theme_config.get("switch_internal", "")

    # 导入自动绑定函数
    from frontend.utils.config_auto_save import create_auto_save
    _auto_save = create_auto_save(config, set_config_callback, tab_key="filter_config")

    with ui.card().style(card_css):
        ui.label('过滤')

        # ---------- 过滤总开关（UI-16 批次 R3：参数页开关已移除） ----------
        # 总开关迁至二级菜单（enable_paths.py 注册 filter_config →
        # filter.filter_master_enable）；D-24 关闭二次确认由 layout.py
        # per-path 确认注册表（CONFIRM_CLOSE_PATHS）承接，防呆语义不变。
        # 键名与消费语义（关闭后本页全部过滤逻辑失效含违禁词过滤）保持不变。

        ui.separator()
        # 长度限制（UI-16 二轮验收任务 16：两个独立开关移至下方违禁词组同排；
        # 2026-10-30 过滤审查批次：六前缀后缀配置（弹幕/LLM 触发与过滤前后缀）
        # 退役移除——后端零消费点，语义由词表/违禁/语言过滤承担）
        with ui.grid(columns=2):
            _auto_save(ui.input(label='最大单词数', placeholder='最长阅读的英文单词数（空格分隔）', value=get_nested_value(config, "filter", "max_len")).style("width:100%;").tooltip('最长阅读的英文单词数（空格分隔），超限自动截断只保留设定值'), keys=("filter", "max_len"))
            _auto_save(ui.input(label='最大字符数', placeholder='最长阅读的字符数，双重过滤，避免溢出', value=get_nested_value(config, "filter", "max_char_len")).style("width:100%;").tooltip('最长阅读的字符数，超限自动截断只保留设定值，双重过滤避免溢出'), keys=("filter", "max_char_len"))

        # 违禁词过滤组（四开关同一行并列——违禁词过滤/违禁语句丢弃/用户名数字
        # 转文字/弹幕表情过滤；关联参数在开关行下方）
        with ui.row().style("align-items:center; gap:16px; flex-wrap:wrap;"):
            _auto_save(ui.switch('违禁词过滤', value=get_nested_value(config, "filter", "badwords", "enable")).style(switch_internal_css), keys=("filter", "badwords", "enable"))
            _auto_save(ui.switch('违禁语句丢弃', value=get_nested_value(config, "filter", "badwords", "discard")).style(switch_internal_css), keys=("filter", "badwords", "discard"))
            _auto_save(ui.switch('用户名数字转文字', value=get_nested_value(config, "filter", "username_convert_digits_to_chinese")).style(switch_internal_css).tooltip('开启后先过滤用户名中的表情、特殊字符和非回复语言文字（保留集按「通用配置-回复语言」，中文恒保留；如回复语言=中文时仅保留中文字符），再将数字转为文字（如"爱国*122"→"爱国一二二"）；全部被过滤时以"观众"替代'), keys=("filter", "username_convert_digits_to_chinese"))
            _auto_save(ui.switch('弹幕表情过滤', value=get_nested_value(config, "filter", "emoji")).style(switch_internal_css).tooltip('过滤弹幕中的表情（[爱心]、emoji）、特殊字符及非中英日韩文字，只保留可念文字'), keys=("filter", "emoji"))
        with ui.grid(columns=3):
            _auto_save(ui.input(label='违禁词路径', value=get_nested_value(config, "filter", "badwords", "path"), placeholder='本地违禁词数据路径（你如果不需要，可以清空文件内容）').style("width:100%;").tooltip('本地违禁词数据路径（txt，UTF-8 编码；你如果不需要，可以清空文件内容）'), keys=("filter", "badwords", "path"))
            _auto_save(ui.input(label='违禁拼音路径', value=get_nested_value(config, "filter", "badwords", "bad_pinyin_path"), placeholder='本地违禁拼音数据路径（你如果不需要，可以清空文件内容）').style("width:100%;").tooltip('本地违禁拼音数据路径（txt，UTF-8 编码；同音变体检测，仅「违禁语句丢弃」开启时生效）'), keys=("filter", "badwords", "bad_pinyin_path"))
            _auto_save(ui.input(label='违禁词替换', value=get_nested_value(config, "filter", "badwords", "replace"), placeholder='在不丢弃违禁语句的前提下，将违禁词替换成此项的文本').style("width:100%;").tooltip('在不丢弃违禁语句的前提下，将违禁词替换成此项的文本'), keys=("filter", "badwords", "replace"))
