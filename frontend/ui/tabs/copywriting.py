from frontend.ui.components.config_helper import get_nested_value
"""
文案标签页模块
从 webui-bak.py 的 copywriting_page 拆分而来

布局规范：
- 设置项用最多 3 列网格排布，组件宽度跟随列宽（width:100%）
- 开关+关联参数 为上下结构单元：开关在上，输入框在下
"""
from nicegui import ui
from typing import Dict, Any, Callable, Optional

from frontend.ui.components import FormField
from frontend.utils.common import textarea_data_change


def create_copywriting_tab(
    config: Dict[str, Any],
    theme_config: Dict[str, str],
    set_config_callback: Callable,
    add_callback: Optional[Callable] = None,
    del_callback: Optional[Callable] = None,
    text_load_callback: Optional[Callable] = None,
    save_text_callback: Optional[Callable] = None,
    audio_synthesis_callback: Optional[Callable] = None
):
    """
    创建文案标签页

    Args:
        config: 配置字典
        theme_config: 主题配置
        set_config_callback: 配置设置回调函数
        add_callback: 添加文案组的回调
        del_callback: 删除文案组的回调
        text_load_callback: 加载文本的回调
        save_text_callback: 保存文案的回调
        audio_synthesis_callback: 音频合成的回调
    """
    card_css = theme_config.get("card", "")
    switch_internal_css = theme_config.get("switch_internal", "")
    button_internal_color = get_nested_value(config, "button_internal_color", "primary")
    button_internal_css = theme_config.get("button_internal_css", "")

    # 音频合成类型选项
    audio_synthesis_type_options = {
        'edge-tts': 'Edge-TTS',
        'gpt_sovits': 'GPT-SoVITS',
        'vits': 'VITS',
        'tts_api': 'TTS API',
        'other': '其他',
    }

    with ui.grid(columns=3):
        with ui.column().style("width:100%;"):
            ui.switch(
                '自动播放',
                value=get_nested_value(config, "copywriting", "auto_play"),
                on_change=lambda e: set_config_callback("copywriting", "auto_play", e.value)
            ).style(switch_internal_css)
            ui.switch(
                '音频随机播放',
                value=get_nested_value(config, "copywriting", "random_play"),
                on_change=lambda e: set_config_callback("copywriting", "random_play", e.value)
            ).style(switch_internal_css)
        FormField.create_input(
            label='音频播放间隔',
            value=get_nested_value(config, "copywriting", "audio_interval"),
            placeholder='文案音频播放之间的间隔时间。就是前一个文案播放完成后，到后一个文案开始播放之间的间隔时间。',
            on_change=lambda e: set_config_callback("copywriting", "audio_interval", e.value),
            tooltip='文案音频播放之间的间隔时间。就是前一个文案播放完成后，到后一个文案开始播放之间的间隔时间。',
            style="width:100%;"
        )
        FormField.create_input(
            label='音频切换间隔',
            value=get_nested_value(config, "copywriting", "switching_interval"),
            placeholder='文案音频切换到弹幕音频的切换间隔时间（反之一样）。',
            on_change=lambda e: set_config_callback("copywriting", "switching_interval", e.value),
            tooltip='文案音频切换到弹幕音频的切换间隔时间（反之一样）。',
            style="width:100%;"
        )

    with ui.row():
        FormField.create_input(
            label='文案索引',
            value="",
            placeholder='文案组的排序号，就是说第一个组是1，第二个组是2，以此类推。请填写纯正整数'
        )
        ui.button(
            '增加文案组',
            on_click=add_callback if add_callback else lambda: None,
            color=button_internal_color
        ).style(button_internal_css)
        ui.button(
            '删除文案组',
            on_click=del_callback if del_callback else lambda: None,
            color=button_internal_color
        ).style(button_internal_css)

    # 文案配置列表
    for index, copywriting_config in enumerate(get_nested_value(config, "copywriting", "config", default=[])):
        with ui.card().style(card_css):
            with ui.grid(columns=3):
                FormField.create_input(
                    label=f"文案存储路径#{index + 1}",
                    value=get_nested_value(config, "file_path"),
                    placeholder='文案文件存储路径。不建议更改。',
                    on_change=lambda e, i=index: set_config_callback("copywriting", "config", i, "file_path", e.value),
                    tooltip='文案文件存储路径。不建议更改。',
                    style="width:100%;"
                )
                FormField.create_input(
                    label=f"音频存储路径#{index + 1}",
                    value=get_nested_value(config, "audio_path"),
                    placeholder='文案音频文件存储路径。不建议更改。',
                    on_change=lambda e, i=index: set_config_callback("copywriting", "config", i, "audio_path", e.value),
                    tooltip='文案音频文件存储路径。不建议更改。',
                    style="width:100%;"
                )
                FormField.create_input(
                    label=f"连续播放数#{index + 1}",
                    value=get_nested_value(config, "continuous_play_num"),
                    placeholder='文案播放列表中连续播放的音频文件个数，如果超过了这个个数就会切换下一个文案列表',
                    on_change=lambda e, i=index: set_config_callback("copywriting", "config", i, "continuous_play_num", e.value),
                    tooltip='文案播放列表中连续播放的音频文件个数，如果超过了这个个数就会切换下一个文案列表',
                    style="width:100%;"
                )
                FormField.create_input(
                    label=f"连续播放时间#{index + 1}",
                    value=get_nested_value(config, "max_play_time"),
                    placeholder='文案播放列表中连续播放音频的时长，如果超过了这个时长就会切换下一个文案列表',
                    on_change=lambda e, i=index: set_config_callback("copywriting", "config", i, "max_play_time", e.value),
                    tooltip='文案播放列表中连续播放音频的时长，如果超过了这个时长就会切换下一个文案列表',
                    style="width:100%;"
                )
                FormField.create_textarea(
                    label=f"播放列表#{index + 1}",
                    value=textarea_data_change(get_nested_value(config, "play_list")),
                    placeholder='此处填写需要播放的音频文件全名，填写完毕后点击 保存配置。文件全名从音频列表中复制，换行分隔，请勿随意填写',
                    on_change=lambda e, i=index: set_config_callback("copywriting", "config", i, "play_list", e.value.split('\n')),
                    tooltip='此处填写需要播放的音频文件全名，填写完毕后点击 保存配置。文件全名从音频列表中复制，换行分隔，请勿随意填写',
                    style="width:100%;"
                )

    # 文案音频合成
    with ui.card().style(card_css):
        ui.label("文案音频合成")
        with ui.grid(columns=3):
            FormField.create_input(
                label='文案文本路径',
                value=get_nested_value(config, "copywriting", "text_path"),
                placeholder='待合成的文案文本文件的路径',
                on_change=lambda e: set_config_callback("copywriting", "text_path", e.value),
                tooltip='待合成的文案文本文件的路径',
                style="width:100%;"
            )
            ui.button(
                '加载文本',
                on_click=text_load_callback if text_load_callback else lambda: None,
                color=button_internal_color
            ).style(button_internal_css)
            FormField.create_input(
                label='音频存储路径',
                value=get_nested_value(config, "copywriting", "audio_save_path"),
                placeholder='音频合成后存储的路径',
                on_change=lambda e: set_config_callback("copywriting", "audio_save_path", e.value),
                tooltip='音频合成后存储的路径',
                style="width:100%;"
            )
            FormField.create_select(
                label='语音合成',
                options=audio_synthesis_type_options,
                value=get_nested_value(config, "copywriting", "audio_synthesis_type"),
                on_change=lambda e: set_config_callback("copywriting", "audio_synthesis_type", e.value),
                style="width:100%;"
            )
        with ui.grid(columns=1):
            FormField.create_textarea(
                label='文案文本',
                value='',
                placeholder='此处对需要合成文案音频的文本内容进行编辑。文案会自动根据逻辑进行切分，然后根据配置合成完整的一个音频文件。',
                tooltip='此处对需要合成文案音频的文本内容进行编辑。文案会自动根据逻辑进行切分，然后根据配置合成完整的一个音频文件。',
                style="width:100%;"
            )
        with ui.row():
            ui.button(
                '保存文案',
                on_click=save_text_callback if save_text_callback else lambda: None,
                color=button_internal_color
            ).style(button_internal_css)
            ui.button(
                '合成音频',
                on_click=audio_synthesis_callback if audio_synthesis_callback else lambda: None,
                color=button_internal_color
            ).style(button_internal_css)
        copywriting_audio_card = ui.card()
        with copywriting_audio_card.style(card_css):
            with ui.row():
                ui.label("此处显示生成的文案音频，仅显示最新合成的文案音频，可以在此操作删除合成的音频")
