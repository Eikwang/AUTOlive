# -*- coding: UTF-8 -*-
"""
通用配置标签页模块（精简版）
仅保留：平台、直播间号、大语言模型、虚拟身体、语音合成、回复语言、提示词前缀/后缀、弹幕模板、回复模板
平台相关配置已拆分到 platform_config.py

布局规范：
- 设置项用最多 3 列网格排布，组件宽度跟随列宽（width:100%）
- 开关+关联参数 为上下结构单元：开关在上，输入框在下
"""
import json
from nicegui import ui
from typing import Dict, Any, Callable, List, Optional

from frontend.ui.components import FormField
from frontend.ui.components.config_helper import get_nested_value


def textarea_data_change(data):
    """字符串数组数据格式转换"""
    if data is None:
        return ""
    tmp_str = ""
    for tmp in data:
        tmp_str = tmp_str + tmp + chr(10)
    return tmp_str


def create_common_config_tab(
    config: Dict[str, Any],
    theme_config: Dict[str, str],
    set_config_callback: Callable
):
    """创建通用配置标签页（精简版）"""
    switch_internal_css = theme_config.get("switch_internal", "")

    # 导入自动绑定函数
    from frontend.utils.config_auto_save import create_auto_save
    _auto_save = create_auto_save(config, set_config_callback, tab_key="common_config")

    chat_type_options = {'custom_llm': '自定义LLM', 'fastrag': 'fastrag知识库', 'local_qa': '本地问答'}
    # 2026-10-22 批次（G1）：chat_type 三通道——custom_llm（OpenAI-compatible 自定义）/
    # fastrag（本地知识库 RAG）/local_qa（本地词库直答）。失败→本地问答托底（llm_handle 共享层）。
    # edtalk_realtime 为合法运行态值（EDTalk 整合，frontend/main.py 开关双检）——
    # 漏列曾致通用配置页 "Invalid value: edtalk_realtime"（2026-10-06 基线既有问题，2026-10-12 修复）。
    # 2026-10-22 批次（G2）：metahuman_stream 选项退役移除。
    visual_body_options = {'edtalk_realtime': 'EDTalk 实时驱动'}
    audio_synthesis_type_options = {'gpt_sovits': 'GPT_SoVITS', 'edge_tts': 'edge-tts'}  # D-19：与合成测试处下拉保持一致

    # 基础配置（3列网格）
    with ui.grid(columns=3):
        # 旧架构 platform 16 选 1 与 room_display_id 已退役（整合计划 P2-4）：
        # 监听房间/活跃指定统一在"平台配置"页（DanmakuListener 进程内模块）

        _auto_save(ui.select(
            label='大语言模型',
            options=chat_type_options,
            value=get_nested_value(config, "chat_type")
        ).style("width:100%;").tooltip('选用的LLM类型。相关的弹幕信息等会传递给此LLM进行推理，获取回答'), ("chat_type",))

        _auto_save(ui.select(
            label='虚拟身体',
            options=visual_body_options,
            value=get_nested_value(config, "visual_body")
        ).style("width:100%;").tooltip('选用的虚拟身体类型。如果使用VTS对接，就选其他，用什么展示身体就选什么，大部分对接的选项需要单独启动对应的服务端程序，请勿随便选择。'), ("visual_body",))

        _auto_save(ui.select(
            label='语音合成',
            options=audio_synthesis_type_options,
            value=get_nested_value(config, "audio_synthesis_type")
        ).style("width:100%;").tooltip('选用的TTS类型，所有的文本内容最终都将通过此TTS进行语音合成'), ("audio_synthesis_type",))

    # 回复配置（3列网格）
    with ui.grid(columns=3):
        _auto_save(ui.select(
            label='回复语言',
            # 2026-10-22 批次（G3）：补韩文/粤语。内部值 'jp'→引擎 'ja' 映射见
            # synthesis_manager.tts_handle（存量配置兼容，不改键值）
            options={'none': '所有', 'zh': '中文', 'en': '英文', 'jp': '日文', 'ko': '韩文', 'yue': '粤语'},
            value=get_nested_value(config, "need_lang")
        ).style("width:100%;").tooltip('限制回复的语言，如：选中中文，则只会回复中文提问，其他语言将被跳过'), ("need_lang",))

        _auto_save(ui.input(label='提示词前缀', placeholder='此配置会追加在弹幕前，再发送给LLM处理', value=get_nested_value(config, "before_prompt")).style("width:100%;").tooltip('此配置会追加在弹幕前，再发送给LLM处理'), ("before_prompt",))
        _auto_save(ui.input(label='提示词后缀', placeholder='此配置会追加在弹幕后，再发送给LLM处理', value=get_nested_value(config, "after_prompt")).style("width:100%;").tooltip('此配置会追加在弹幕后，再发送给LLM处理'), ("after_prompt",))

    # 开关+参数组合（上下结构：开关在上，输入框在下，3列网格）
    with ui.grid(columns=3):
        # 弹幕模板组
        with ui.column().style("width:100%;"):
            _auto_save(ui.switch('启用弹幕模板', value=get_nested_value(config, "comment_template", "enable")).style(switch_internal_css).tooltip('此配置会追加在弹幕后，再发送给LLM处理'), ("comment_template", "enable"))
            _auto_save(ui.input(label='弹幕模板', value=get_nested_value(config, "comment_template", "copywriting"), placeholder='此配置会对弹幕内容进行修改，{}内为变量，会被替换为指定内容，请勿随意删除变量').style("width:100%;").tooltip('此配置会对弹幕内容进行修改，{}内为变量，会被替换为指定内容，请勿随意删除变量'), ("comment_template", "copywriting"))

        # 回复模板组
        with ui.column().style("width:100%;"):
            _auto_save(ui.switch('启用回复模板', value=get_nested_value(config, "reply_template", "enable")).style(switch_internal_css).tooltip('此配置会在LLM输出的答案中进行回复内容的重新构建'), ("reply_template", "enable"))
            _auto_save(ui.input(label='回复用户名的最大长度', value=get_nested_value(config, "reply_template", "username_max_len"), placeholder='回复用户名的最大长度').style("width:100%;").tooltip('回复用户名的最大长度'), ("reply_template", "username_max_len"))
            _auto_save(ui.textarea(
                label='回复模板',
                placeholder='此配置会对LLM回复内容进行修改，{}内为变量，会被替换为指定内容，请勿随意删除变量',
                value=textarea_data_change(get_nested_value(config, "reply_template", "copywriting"))
            ).style("width:100%;").tooltip('此配置会对LLM回复内容进行修改，{}内为变量，会被替换为指定内容，请勿随意删除变量'), ("reply_template", "copywriting"), split_lines=True)
