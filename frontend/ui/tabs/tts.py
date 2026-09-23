from frontend.ui.components.config_helper import get_nested_value
"""
文本转语音标签页模块
从 webui-bak.py 的 tts_page 拆分而来
"""
from nicegui import ui
from typing import Dict, Any, Callable, Optional
import logging

from frontend.ui.components import FormField
from frontend.utils.common import is_url_check
from frontend.utils.audio import Audio

logger = logging.getLogger(__name__)


def create_tts_tab(
    config: Dict[str, Any],
    theme_config: Dict[str, str],
    set_config_callback: Callable,
    audio: Optional[Audio] = None
):
    """
    创建文本转语音标签页
    
    Args:
        config: 配置字典
        theme_config: 主题配置
        set_config_callback: 配置设置回调函数
        audio: 音频处理实例
    """
    card_css = theme_config.get("card", "")
    switch_internal_css = theme_config.get("switch_internal", "")
    button_internal_color = get_nested_value(config, "button_internal_color", "primary")
    button_internal_css = theme_config.get("button_internal_css", "")
    
    # 音频合成类型选项
    audio_synthesis_type_options = {
        'vits': 'VITS',
        'gpt_sovits': 'GPT-SoVITS',
        'tts_api': 'TTS API',
        'other': '其他',
    }
    
    # 通用-合成试听音频
    async def tts_common_audio_synthesis():
        ui.notify(position="top", type="warning", message="音频合成中，将会阻塞其他任务运行，请勿做其他操作，查看日志情况，耐心等待")
        logger.warning("音频合成中，将会阻塞其他任务运行，请勿做其他操作，查看日志情况，耐心等待")
        
        content = input_tts_common_text.value
        audio_synthesis_type = select_tts_common_audio_synthesis_type.value

        # 使用本地配置进行音频合成，返回音频路径
        if audio:
            file_path = await audio.audio_synthesis_use_local_config(content, audio_synthesis_type)
        else:
            logger.error("音频处理实例未初始化")
            ui.notify(position="top", type="negative", message="音频处理实例未初始化")
            return

        if file_path:
            logger.info(f"音频合成成功，存储于：{file_path}")
            ui.notify(position="top", type="positive", message=f"音频合成成功，存储于：{file_path}")
        else:
            logger.error(f"音频合成失败！请查看日志排查问题")
            ui.notify(position="top", type="negative", message=f"音频合成失败！请查看日志排查问题")
            return

        def clear_tts_common_audio_card(file_path):
            tts_common_audio_card.clear()
            import os
            if os.path.exists(file_path):
                os.remove(file_path)
                ui.notify(position="top", type="positive", message=f"删除文件成功：{file_path}")
            else:
                ui.notify(position="top", type="negative", message=f"删除文件失败：{file_path}")
        
        # 清空card
        tts_common_audio_card.clear()
        tmp_label = ui.label(f"音频合成成功，存储于：{file_path}")
        tmp_label.move(tts_common_audio_card)
        audio_tmp = ui.audio(src=file_path)
        audio_tmp.move(tts_common_audio_card)
        button_audio_del = ui.button('删除音频', on_click=lambda: clear_tts_common_audio_card(file_path), color=button_internal_color).style(button_internal_css)
        button_audio_del.move(tts_common_audio_card)
    
    # 合成测试卡片
    with ui.card().style(card_css):
        ui.label("合成测试（只是测试，若确认使用此TTS，请前往 通用配置 配置 语音合成）")
        with ui.grid(columns=3):
            select_tts_common_audio_synthesis_type = ui.select(
                label='语音合成',
                options=audio_synthesis_type_options,
                value=get_nested_value(config, "audio_synthesis_type")
            ).style("width:100%;")
            input_tts_common_text = ui.input(label='待合成音频内容', placeholder='此处填写待合成的音频文本内容', value="此处填写待合成的音频文本内容，用于试听效果，类型切换不需要保存即可生效。").style("width:100%;")
            button_tts_common_audio_synthesis = ui.button('试听', on_click=lambda: tts_common_audio_synthesis(), color=button_internal_color).style(button_internal_css)
        tts_common_audio_card = ui.card()
        with tts_common_audio_card.style(card_css):
            with ui.grid(columns=3):
                ui.label("此处显示生成的音频，仅显示最新合成的音频，可以在此操作删除合成的音频")

    # GPT-SoVITS配置
    with ui.card().style(card_css):
        ui.label("GPT-SoVITS")
        with ui.grid(columns=3):
            FormField.create_select(
                label='API类型', 
                options={
                    'api':'api', 
                    'api_0322':'api_0322', 
                    'api_0706':'api_0706', 
                    'v2_api_0821': 'v2_api_0821', 
                    'webtts':'WebTTS', 
                    'gradio_0322':'gradio_0322',
                }, 
                value=get_nested_value(config, "gpt_sovits", "type"),
                on_change=lambda e: set_config_callback("gpt_sovits", "type", e.value),
                style="width:100%;"
            )
            FormField.create_input(
                label='Gradio API地址', 
                value=get_nested_value(config, "gpt_sovits", "gradio_ip_port"), 
                placeholder='官方webui程序启动后gradio监听的地址',
                on_change=lambda e: set_config_callback("gpt_sovits", "gradio_ip_port", e.value),
                style="width:100%;"
            )
            FormField.create_input(
                label='API地址（http）', 
                value=get_nested_value(config, "gpt_sovits", "api_ip_port"), 
                placeholder='官方API程序启动后监听的地址',
                on_change=lambda e: set_config_callback("gpt_sovits", "api_ip_port", e.value),
                style="width:100%;"
            )
            
        with ui.grid(columns=3):
            FormField.create_input(
                label='GPT模型路径', 
                value=get_nested_value(config, "gpt_sovits", "gpt_model_path"), 
                placeholder='GPT模型路径，填绝对路径',
                on_change=lambda e: set_config_callback("gpt_sovits", "gpt_model_path", e.value),
                style="width:100%;"
            )
            FormField.create_input(
                label='SOVITS模型路径', 
                value=get_nested_value(config, "gpt_sovits", "sovits_model_path"), 
                placeholder='SOVITS模型路径，填绝对路径',
                on_change=lambda e: set_config_callback("gpt_sovits", "sovits_model_path", e.value),
                style="width:100%;"
            )
            ui.button('加载模型', on_click=lambda: _load_gpt_sovits_model(config), color=button_internal_color).style(button_internal_css)
            
        # api配置
        with ui.card().style(card_css):
            ui.label("api")
            with ui.grid(columns=3):
                FormField.create_input(
                    label='参考音频路径', 
                    value=get_nested_value(config, "gpt_sovits", "ref_audio_path"), 
                    placeholder='参考音频路径，建议填绝对路径',
                    on_change=lambda e: set_config_callback("gpt_sovits", "ref_audio_path", e.value),
                    style="width:100%;"
                )
                FormField.create_input(
                    label='参考音频的文本', 
                    value=get_nested_value(config, "gpt_sovits", "prompt_text"), 
                    placeholder='参考音频的文本',
                    on_change=lambda e: set_config_callback("gpt_sovits", "prompt_text", e.value),
                    style="width:100%;"
                )
                FormField.create_select(
                    label='参考音频的语种', 
                    options={'中文':'中文', '日文':'日文', '英文':'英文'}, 
                    value=get_nested_value(config, "gpt_sovits", "prompt_language"),
                    on_change=lambda e: set_config_callback("gpt_sovits", "prompt_language", e.value),
                    style="width:100%;"
                )
                FormField.create_select(
                    label='需要合成的语种', 
                    options={'自动识别':'自动识别', '中文':'中文', '日文':'日文', '英文':'英文'}, 
                    value=get_nested_value(config, "gpt_sovits", "language"),
                    on_change=lambda e: set_config_callback("gpt_sovits", "language", e.value),
                    style="width:100%;"
                )
                FormField.create_select(
                    label='语句切分', 
                    options={
                        '不切':'不切', 
                        '凑四句一切':'凑四句一切', 
                        '凑50字一切':'凑50字一切', 
                        '按中文句号。切':'按中文句号。切', 
                        '按英文句号.切':'按英文句号.切',
                        '按标点符号切':'按标点符号切'
                    }, 
                    value=get_nested_value(config, "gpt_sovits", "cut"),
                    on_change=lambda e: set_config_callback("gpt_sovits", "cut", e.value),
                    style="width:100%;"
                )
            
        # api_0322 | gradio_0322配置
        with ui.card().style(card_css):
            ui.label("api_0322 | gradio_0322")
            with ui.grid(columns=3):
                FormField.create_input(
                    label='参考音频路径', 
                    value=get_nested_value(config, "gpt_sovits", "api_0322", "ref_audio_path"), 
                    placeholder='参考音频路径，建议填绝对路径',
                    on_change=lambda e: set_config_callback("gpt_sovits", "api_0322", "ref_audio_path", e.value),
                    style="width:100%;"
                )
                FormField.create_input(
                    label='参考音频的文本', 
                    value=get_nested_value(config, "gpt_sovits", "api_0322", "prompt_text"), 
                    placeholder='参考音频的文本',
                    on_change=lambda e: set_config_callback("gpt_sovits", "api_0322", "prompt_text", e.value),
                    style="width:100%;"
                )
                FormField.create_select(
                    label='参考音频的语种', 
                    options={'中文':'中文', '日文':'日文', '英文':'英文'}, 
                    value=get_nested_value(config, "gpt_sovits", "api_0322", "prompt_lang"),
                    on_change=lambda e: set_config_callback("gpt_sovits", "api_0322", "prompt_lang", e.value),
                    style="width:100%;"
                )
                FormField.create_select(
                    label='需要合成的语种', 
                    options={
                        '自动识别':'自动识别', 
                        '中文':'中文', 
                        '日文':'日文', 
                        '英文':'英文', 
                        '中英混合': '中英混合',
                        '日英混合': '日英混合',
                        '多语种混合': '多语种混合',
                    }, 
                    value=get_nested_value(config, "gpt_sovits", "api_0322", "text_lang"),
                    on_change=lambda e: set_config_callback("gpt_sovits", "api_0322", "text_lang", e.value),
                    style="width:100%;"
                )
                FormField.create_select(
                    label='语句切分', 
                    options={
                        '不切':'不切', 
                        '凑四句一切':'凑四句一切', 
                        '凑50字一切':'凑50字一切', 
                        '按中文句号。切':'按中文句号。切', 
                        '按英文句号.切':'按英文句号.切',
                        '按标点符号切':'按标点符号切'
                    }, 
                    value=get_nested_value(config, "gpt_sovits", "api_0322", "text_split_method"),
                    on_change=lambda e: set_config_callback("gpt_sovits", "api_0322", "text_split_method", e.value),
                    style="width:100%;"
                )
            with ui.grid(columns=3):
                FormField.create_input(
                    label='top_k', 
                    value=get_nested_value(config, "gpt_sovits", "api_0322", "top_k"), 
                    placeholder='top_k',
                    on_change=lambda e: set_config_callback("gpt_sovits", "api_0322", "top_k", e.value),
                    style="width:100%;"
                )
                FormField.create_input(
                    label='top_p', 
                    value=get_nested_value(config, "gpt_sovits", "api_0322", "top_p"), 
                    placeholder='top_p',
                    on_change=lambda e: set_config_callback("gpt_sovits", "api_0322", "top_p", e.value),
                    style="width:100%;"
                )
                FormField.create_input(
                    label='temperature', 
                    value=get_nested_value(config, "gpt_sovits", "api_0322", "temperature"), 
                    placeholder='temperature',
                    on_change=lambda e: set_config_callback("gpt_sovits", "api_0322", "temperature", e.value),
                    style="width:100%;"
                )
                FormField.create_input(
                    label='batch_size', 
                    value=get_nested_value(config, "gpt_sovits", "api_0322", "batch_size"), 
                    placeholder='batch_size',
                    on_change=lambda e: set_config_callback("gpt_sovits", "api_0322", "batch_size", e.value),
                    style="width:100%;"
                )
                FormField.create_input(
                    label='speed_factor', 
                    value=get_nested_value(config, "gpt_sovits", "api_0322", "speed_factor"), 
                    placeholder='speed_factor',
                    on_change=lambda e: set_config_callback("gpt_sovits", "api_0322", "speed_factor", e.value),
                    style="width:100%;"
                )
                FormField.create_input(
                    label='分段间隔(秒)', 
                    value=get_nested_value(config, "gpt_sovits", "api_0322", "fragment_interval"), 
                    placeholder='fragment_interval',
                    on_change=lambda e: set_config_callback("gpt_sovits", "api_0322", "fragment_interval", e.value),
                    style="width:100%;"
                )
                ui.switch(
                    'split_bucket', 
                    value=get_nested_value(config, "gpt_sovits", "api_0322", "split_bucket"),
                    on_change=lambda e: set_config_callback("gpt_sovits", "api_0322", "split_bucket", e.value)
                ).style(switch_internal_css)
                ui.switch(
                    'return_fragment', 
                    value=get_nested_value(config, "gpt_sovits", "api_0322", "return_fragment"),
                    on_change=lambda e: set_config_callback("gpt_sovits", "api_0322", "return_fragment", e.value)
                ).style(switch_internal_css)
            
        # api_0706配置
        with ui.card().style(card_css):
            ui.label("api_0706")
            with ui.grid(columns=3):
                FormField.create_input(
                    label='参考音频路径', 
                    value=get_nested_value(config, "gpt_sovits", "api_0706", "refer_wav_path"), 
                    placeholder='参考音频路径，建议填绝对路径',
                    on_change=lambda e: set_config_callback("gpt_sovits", "api_0706", "refer_wav_path", e.value),
                    style="width:100%;"
                )
                FormField.create_input(
                    label='参考音频的文本', 
                    value=get_nested_value(config, "gpt_sovits", "api_0706", "prompt_text"), 
                    placeholder='参考音频的文本',
                    on_change=lambda e: set_config_callback("gpt_sovits", "api_0706", "prompt_text", e.value),
                    style="width:100%;"
                )
                FormField.create_select(
                    label='参考音频的语种', 
                    options={'中文':'中文', '日文':'日文', '英文':'英文'}, 
                    value=get_nested_value(config, "gpt_sovits", "api_0706", "prompt_language"),
                    on_change=lambda e: set_config_callback("gpt_sovits", "api_0706", "prompt_language", e.value),
                    style="width:100%;"
                )
                FormField.create_select(
                    label='需要合成的语种', 
                    options={
                        '自动识别':'自动识别', 
                        '中文':'中文', 
                        '日文':'日文', 
                        '英文':'英文', 
                        '中英混合': '中英混合',
                        '日英混合': '日英混合',
                        '多语种混合': '多语种混合',
                    }, 
                    value=get_nested_value(config, "gpt_sovits", "api_0706", "text_language"),
                    on_change=lambda e: set_config_callback("gpt_sovits", "api_0706", "text_language", e.value),
                    style="width:100%;"
                )
                FormField.create_input(
                    label='文本切分', 
                    value=get_nested_value(config, "gpt_sovits", "api_0706", "cut_punc"), 
                    placeholder='文本切分符号设定, 符号范围,.;?!、，。？！；：…',
                    on_change=lambda e: set_config_callback("gpt_sovits", "api_0706", "cut_punc", e.value),
                    style="width:100%;"
                )
            
        # v2_api_0821配置
        with ui.card().style(card_css):
            ui.label("v2_api_0821")
            with ui.grid(columns=3):
                FormField.create_input(
                    label='参考音频路径', 
                    value=get_nested_value(config, "gpt_sovits", "v2_api_0821", "ref_audio_path"), 
                    placeholder='参考音频路径，建议填绝对路径',
                    on_change=lambda e: set_config_callback("gpt_sovits", "v2_api_0821", "ref_audio_path", e.value),
                    style="width:100%;"
                )
                FormField.create_input(
                    label='参考音频的文本', 
                    value=get_nested_value(config, "gpt_sovits", "v2_api_0821", "prompt_text"), 
                    placeholder='参考音频的文本',
                    on_change=lambda e: set_config_callback("gpt_sovits", "v2_api_0821", "prompt_text", e.value),
                    style="width:100%;"
                )
                FormField.create_select(
                    label='参考音频的语种', 
                    options={'zh':'中文', 'ja':'日文', 'en':'英文'}, 
                    value=get_nested_value(config, "gpt_sovits", "v2_api_0821", "prompt_lang"),
                    on_change=lambda e: set_config_callback("gpt_sovits", "v2_api_0821", "prompt_lang", e.value),
                    style="width:100%;"
                )
                FormField.create_select(
                    label='需要合成的语种', 
                    options={
                        "all_zh": "中文",
                        "all_yue": "粤语",
                        "en": "英文",
                        "all_ja": "日文",
                        "all_ko": "韩文",
                        "zh": "中英混合",
                        "yue": "粤英混合",
                        "ja": "日英混合",
                        "ko": "韩英混合",
                        "auto": "多语种混合",
                        "auto_yue": "多语种混合(粤语)",
                    }, 
                    value=get_nested_value(config, "gpt_sovits", "v2_api_0821", "text_lang"),
                    on_change=lambda e: set_config_callback("gpt_sovits", "v2_api_0821", "text_lang", e.value),
                    style="width:100%;"
                )
                FormField.create_select(
                    label='语句切分', 
                    options={
                        'cut0':'不切', 
                        'cut1':'凑四句一切', 
                        'cut2':'凑50字一切', 
                        'cut3':'按中文句号。切', 
                        'cut4':'按英文句号.切',
                        'cut5':'按标点符号切'
                    }, 
                    value=get_nested_value(config, "gpt_sovits", "v2_api_0821", "text_split_method"),
                    on_change=lambda e: set_config_callback("gpt_sovits", "v2_api_0821", "text_split_method", e.value),
                    style="width:100%;"
                )
            with ui.grid(columns=3):
                FormField.create_input(
                    label='top_k', 
                    value=get_nested_value(config, "gpt_sovits", "v2_api_0821", "top_k"), 
                    placeholder='top_k',
                    on_change=lambda e: set_config_callback("gpt_sovits", "v2_api_0821", "top_k", e.value),
                    style="width:100%;"
                )
                FormField.create_input(
                    label='top_p', 
                    value=get_nested_value(config, "gpt_sovits", "v2_api_0821", "top_p"), 
                    placeholder='top_p',
                    on_change=lambda e: set_config_callback("gpt_sovits", "v2_api_0821", "top_p", e.value),
                    style="width:100%;"
                )
                FormField.create_input(
                    label='temperature', 
                    value=get_nested_value(config, "gpt_sovits", "v2_api_0821", "temperature"), 
                    placeholder='temperature',
                    on_change=lambda e: set_config_callback("gpt_sovits", "v2_api_0821", "temperature", e.value),
                    style="width:100%;"
                )
                FormField.create_input(
                    label='batch_size', 
                    value=get_nested_value(config, "gpt_sovits", "v2_api_0821", "batch_size"), 
                    placeholder='batch_size',
                    on_change=lambda e: set_config_callback("gpt_sovits", "v2_api_0821", "batch_size", e.value),
                    style="width:100%;"
                )
                FormField.create_input(
                    label='batch_threshold', 
                    value=get_nested_value(config, "gpt_sovits", "v2_api_0821", "batch_threshold"), 
                    placeholder='batch_threshold',
                    on_change=lambda e: set_config_callback("gpt_sovits", "v2_api_0821", "batch_threshold", e.value),
                    style="width:100%;"
                )
                ui.switch(
                    'split_bucket', 
                    value=get_nested_value(config, "gpt_sovits", "v2_api_0821", "split_bucket"),
                    on_change=lambda e: set_config_callback("gpt_sovits", "v2_api_0821", "split_bucket", e.value)
                ).style(switch_internal_css)
                FormField.create_input(
                    label='speed_factor', 
                    value=get_nested_value(config, "gpt_sovits", "v2_api_0821", "speed_factor"), 
                    placeholder='speed_factor',
                    on_change=lambda e: set_config_callback("gpt_sovits", "v2_api_0821", "speed_factor", e.value),
                    style="width:100%;"
                )
                FormField.create_input(
                    label='分段间隔(秒)', 
                    value=get_nested_value(config, "gpt_sovits", "v2_api_0821", "fragment_interval"), 
                    placeholder='fragment_interval',
                    on_change=lambda e: set_config_callback("gpt_sovits", "v2_api_0821", "fragment_interval", e.value),
                    style="width:100%;"
                )
                FormField.create_input(
                    label='seed', 
                    value=get_nested_value(config, "gpt_sovits", "v2_api_0821", "seed"), 
                    placeholder='seed',
                    on_change=lambda e: set_config_callback("gpt_sovits", "v2_api_0821", "seed", e.value),
                    style="width:100%;"
                )
                FormField.create_input(
                    label='media_type', 
                    value=get_nested_value(config, "gpt_sovits", "v2_api_0821", "media_type"), 
                    placeholder='media_type',
                    on_change=lambda e: set_config_callback("gpt_sovits", "v2_api_0821", "media_type", e.value),
                    style="width:100%;"
                )
                ui.switch(
                    'parallel_infer', 
                    value=get_nested_value(config, "gpt_sovits", "v2_api_0821", "parallel_infer"),
                    on_change=lambda e: set_config_callback("gpt_sovits", "v2_api_0821", "parallel_infer", e.value)
                ).style(switch_internal_css)
                FormField.create_input(
                    label='repetition_penalty', 
                    value=get_nested_value(config, "gpt_sovits", "v2_api_0821", "repetition_penalty"), 
                    placeholder='repetition_penalty',
                    on_change=lambda e: set_config_callback("gpt_sovits", "v2_api_0821", "repetition_penalty", e.value),
                    style="width:100%;"
                )
            
        # WebTTS配置
        with ui.card().style(card_css):
            ui.label("WebTTS相关配置")
            with ui.grid(columns=3):
                FormField.create_select(
                    label='版本', 
                    options={
                        '1':'1', 
                        '1.4':'1.4', 
                        '2':'2'
                    }, 
                    value=get_nested_value(config, "gpt_sovits", "webtts", "version"),
                    on_change=lambda e: set_config_callback("gpt_sovits", "webtts", "version", e.value),
                    style="width:100%;"
                )
                FormField.create_input(
                    label='API地址', 
                    value=get_nested_value(config, "gpt_sovits", "webtts", "api_ip_port"), 
                    placeholder='API监听地址',
                    on_change=lambda e: set_config_callback("gpt_sovits", "webtts", "api_ip_port", e.value),
                    style="width:100%;"
                )
                FormField.create_input(
                    label='音色', 
                    value=get_nested_value(config, "gpt_sovits", "webtts", "spk"), 
                    placeholder='音色',
                    on_change=lambda e: set_config_callback("gpt_sovits", "webtts", "spk", e.value),
                    style="width:100%;"
                )
                FormField.create_select(
                    label='语言', 
                    options={
                        'zh':'中文', 
                        'en':'英文', 
                        'jp':'日文'
                    }, 
                    value=get_nested_value(config, "gpt_sovits", "webtts", "lang"),
                    on_change=lambda e: set_config_callback("gpt_sovits", "webtts", "lang", e.value),
                    style="width:100%;"
                )
                FormField.create_input(
                    label='语速', 
                    value=get_nested_value(config, "gpt_sovits", "webtts", "speed"), 
                    placeholder='语速',
                    on_change=lambda e: set_config_callback("gpt_sovits", "webtts", "speed", e.value),
                    style="width:100%;"
                )
                FormField.create_input(
                    label='情感', 
                    value=get_nested_value(config, "gpt_sovits", "webtts", "emotion"), 
                    placeholder='情感',
                    on_change=lambda e: set_config_callback("gpt_sovits", "webtts", "emotion", e.value),
                    style="width:100%;"
                )


def _load_gpt_sovits_model(config: Dict[str, Any]):
    """
    加载GPT-SoVITS模型
    
    Args:
        config: 配置字典
    """
    # TODO: 实现模型加载功能
    logger.warning("GPT-SoVITS模型加载功能待实现")