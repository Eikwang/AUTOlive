# -*- coding: UTF-8 -*-
"""
本地问答标签页模块
从 common_config.py 拆分而来

布局规范：
- 设置项用最多 3 列网格排布，组件宽度跟随列宽（width:100%）
- 开关+关联参数 为上下结构单元：开关在上，输入框在下
"""
from nicegui import ui
from typing import Dict, Any, Callable
from frontend.ui.components.config_helper import get_nested_value
from frontend.utils.common import textarea_data_change


def create_local_qa_tab(
    config: Dict[str, Any],
    theme_config: Dict[str, str],
    set_config_callback: Callable
):
    """创建本地问答标签页"""
    card_css = theme_config.get("card", "")
    switch_internal_css = theme_config.get("switch_internal", "")

    # 导入自动绑定函数
    from frontend.utils.config_auto_save import create_auto_save
    _auto_save = create_auto_save(config, set_config_callback, tab_key="local_qa")

    with ui.card().style(card_css):
        ui.label('本地问答')
        # ~~周期性触发~~（2026-10-31 本地问答审查批次移除：task_data 缓冲自始
        # 零写入点，定时回放永远取空列表——死机制；命中即时合成播放）
        with ui.grid(columns=3):
            with ui.column().style("width:100%;"):
                # 存储格式下拉已退役（/autoplan main-20261026-214845 主 local_qa
                # 收敛项：唯一格式=一问一答 TXT（问题|答案），与助播知识库同构；
                # 存量 text.type 键经 settings 迁移移除——F6 实证两格式历来同一加载器）
                _auto_save(ui.switch('启用文本匹配', value=get_nested_value(config, "local_qa", "text", "enable")).style(switch_internal_css), ("local_qa", "text", "enable"))
                _auto_save(ui.input(label='文本最低相似度', placeholder='最低文本匹配相似度', value=get_nested_value(config, "local_qa", "text", "similarity")).style("width:100%;").tooltip('jieba 分词混合匹配；默认 0.6（误命中多调高，漏命中多调低）'), ("local_qa", "text", "similarity"))
            with ui.column().style("width:100%;"):
                _auto_save(ui.switch('启用音频匹配', value=get_nested_value(config, "local_qa", "audio", "enable")).style(switch_internal_css), ("local_qa", "audio", "enable"))
                _auto_save(ui.select(
                    label='匹配档位',
                    options={'包含关系': '包含关系（精确+包含）', '相似度匹配': '相似度匹配（精确+包含+相似度）'},
                    value=get_nested_value(config, "local_qa", "audio", "type")
                ).style("width:100%;"), ("local_qa", "audio", "type"))
                _auto_save(ui.input(label='音频存储路径', placeholder='音频目录路径（非单个文件）', value=get_nested_value(config, "local_qa", "audio", "file_path")).style("width:100%;").tooltip('音频目录路径：音频文件名（去扩展名）即问题；命中直接播放该音频'), ("local_qa", "audio", "file_path"))
                _auto_save(ui.input(label='音频最低相似度', placeholder='最低音频匹配相似度', value=get_nested_value(config, "local_qa", "audio", "similarity")).style("width:100%;"), ("local_qa", "audio", "similarity"))

        # 文本问答数据（2026-10-31 本地问答审查批次：file_path 迁移为前端直编
        # +实时保存——格式与闲时任务-文案列表一致，便于用户配置知识库；
        # 每行一条：问题|答案，同答案多问法写多行）
        _auto_save(ui.textarea(
            label='文本问答数据',
            value=textarea_data_change(get_nested_value(config, "local_qa", "text", "data")),
            placeholder='一问一答，每行一条：问题|答案'
        ).props('input-style="min-height: 16rem"').style("width:100%;").tooltip(
            '每行一条：问题|答案；同答案多问法写多行；支持 {username}/{cur_time} 变量与 [1|2] 随机括号'
        ), ("local_qa", "text", "data"), split_lines=True)
