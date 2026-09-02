# -*- coding: UTF-8 -*-
"""
聊天标签页模块
从 webui-bak.py 的 talk_page 拆分而来

布局规范：
- 设置项用最多 3 列网格排布，组件宽度跟随列宽（width:100%）
- 开关+关联参数 为上下结构单元：开关在上，输入框在下
"""
from nicegui import ui
from typing import Dict, Any, Callable, Optional

from frontend.ui.components.config_helper import get_nested_value
from utils.my_log import logger


def create_talk_tab(
    config: Dict[str, Any],
    theme_config: Dict[str, str],
    set_config_callback: Callable,
    chat_send_callback: Optional[Callable] = None,
    start_recording_callback: Optional[Callable] = None,
    stop_recording_callback: Optional[Callable] = None
):
    """创建聊天标签页"""
    card_css = theme_config.get("card", "")
    switch_internal_css = theme_config.get("switch_internal", "")
    button_internal_color = theme_config.get("button_internal_color", "primary")
    button_internal_css = theme_config.get("button_internal_css", "")

    # 导入自动绑定函数
    from frontend.utils.config_auto_save import create_auto_save
    _auto_save = create_auto_save(config, set_config_callback, tab_key="talk")

    # 聊天记录区域
    with ui.row().style("position:fixed; top: 100px; right: 20px;"):
        with ui.expansion('聊天记录', icon="question_answer", value=True):
            scroll_area_chat_box = ui.scroll_area().style("width:100%; height:700px;")

    # 基础配置
    with ui.grid(columns=3):
        _auto_save(ui.switch('启用按键监听', value=get_nested_value(config, "talk", "key_listener_enable")).style(switch_internal_css).tooltip("启用后，可以通过键盘单击下放配置的录音按键，启动语音识别对话功能"), ("talk", "key_listener_enable"))
        _auto_save(ui.switch('直接语音对话', value=get_nested_value(config, "talk", "direct_run_talk")).style(switch_internal_css).tooltip("如果启用了，将在首次运行时直接进行语音识别，而不需手动点击开始按键。"), ("talk", "direct_run_talk"))

        # 检测声卡输入设备
        _device_index_val = get_nested_value(config, "talk", "device_index")
        _device_index_options = {}
        try:
            from utils.common import Common
            _common = Common()
            audio_device_info_list = _common.get_all_audio_device_info("in")
            logger.info(f"声卡输入设备={audio_device_info_list}")
            _device_index_options = {str(device['device_index']): device['device_info'] for device in audio_device_info_list}
            logger.debug(f"声卡输入设备={_device_index_options}")
        except Exception as e:
            logger.warning(f"声卡设备检测失败: {e}")
        # 确保config中的值在选项中（即使设备列表中没有）
        if _device_index_val and str(_device_index_val) not in _device_index_options:
            _device_index_options[str(_device_index_val)] = f"设备#{_device_index_val}"
        _auto_save(ui.select(
            label='声卡输入设备',
            options=_device_index_options,
            value=str(_device_index_val) if _device_index_val else None
        ).style("width:100%;").tooltip('这就是语言对话输入的声卡（麦克风），选择你对应的麦克风即可，如果需要监听电脑声卡可以配合虚拟声卡来实现'), ("talk", "device_index"))

        with ui.column().style("width:100%;"):
            _auto_save(ui.switch('播放中不进行录音', value=get_nested_value(config, "talk", "no_recording_during_playback")).style(switch_internal_css).tooltip('AI在播放音频的过程中不进行录音'), ("talk", "no_recording_during_playback"))
            _auto_save(ui.input(label='播放中不进行录音的检测间隔(秒)', value=get_nested_value(config, "talk", "no_recording_during_playback_sleep_interval"), placeholder='检测间隔').style("width:100%;").tooltip('这个值设置正常不需要太大'), ("talk", "no_recording_during_playback_sleep_interval"))

        _auto_save(ui.input(label='你的名字', value=get_nested_value(config, "talk", "username"), placeholder='日志中你的名字').style("width:100%;"), ("talk", "username"))
        _auto_save(ui.switch('连续对话', value=get_nested_value(config, "talk", "continuous_talk")).style(switch_internal_css).tooltip('仅需按一次录音按键，后续就不需要按了'), ("talk", "continuous_talk"))

    # 录音配置
    with ui.grid(columns=3):
        _auto_save(ui.select(
            label='录音类型',
            options={"google": "google", "baidu": "baidu", "faster_whisper": "faster_whisper", "sensevoice": "sensevoice"},
            value=get_nested_value(config, "talk", "type")
        ).style("width:100%;").tooltip('选择使用的STT类型'), ("talk", "type"))

        # 加载按键列表
        _trigger_key_val = get_nested_value(config, "talk", "trigger_key")
        _trigger_key_options = {}
        try:
            import os
            keyboard_file = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))), 'data', 'keyboard.txt')
            if os.path.exists(keyboard_file):
                with open(keyboard_file, 'r', encoding='utf-8') as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            _trigger_key_options[line] = line
        except Exception as e:
            logger.warning(f"加载按键列表失败: {e}")
        if _trigger_key_val and _trigger_key_val not in _trigger_key_options:
            _trigger_key_options[_trigger_key_val] = _trigger_key_val
        _auto_save(ui.select(
            label='录音按键',
            options=_trigger_key_options,
            value=_trigger_key_val,
            with_input=True, clearable=True
        ).style("width:100%;").tooltip('按压此按键就可以触发录音了，按一次就行了'), ("talk", "trigger_key"))

        _stop_trigger_key_val = get_nested_value(config, "talk", "stop_trigger_key")
        _stop_trigger_key_options = dict(_trigger_key_options)
        if _stop_trigger_key_val and _stop_trigger_key_val not in _stop_trigger_key_options:
            _stop_trigger_key_options[_stop_trigger_key_val] = _stop_trigger_key_val
        _auto_save(ui.select(
            label='停录按键',
            options=_stop_trigger_key_options,
            value=_stop_trigger_key_val,
            with_input=True, clearable=True
        ).style("width:100%;").tooltip('按压此按键就可以停止录音了，按一次就行了'), ("talk", "stop_trigger_key"))

        _auto_save(ui.input(label='音量阈值', value=get_nested_value(config, "talk", "volume_threshold"), placeholder='触发录音的起始音量值').style("width:100%;").tooltip('音量阈值，请根据自己的麦克风进行微调'), ("talk", "volume_threshold"))
        _auto_save(ui.input(label='停录计数', value=get_nested_value(config, "talk", "silence_threshold"), placeholder='音量低于起始值的计数').style("width:100%;").tooltip('沉默阈值，请根据自己的麦克风进行微调'), ("talk", "silence_threshold"))
        _auto_save(ui.input(label='CHANNELS', value=get_nested_value(config, "talk", "CHANNELS"), placeholder='录音参数').style("width:100%;"), ("talk", "CHANNELS"))
        _auto_save(ui.input(label='RATE', value=get_nested_value(config, "talk", "RATE"), placeholder='录音参数').style("width:100%;"), ("talk", "RATE"))
        _auto_save(ui.switch('聊天记录', value=get_nested_value(config, "talk", "show_chat_log")).style(switch_internal_css), ("talk", "show_chat_log"))

    # 聊天框
    with ui.grid(columns=1):
        with ui.row():
            ui.textarea(label='聊天框-和AI对话', value="", placeholder='此处填写对话内容可以直接进行对话').style("width:100%;")
            ui.button('发送', on_click=chat_send_callback if chat_send_callback else lambda: None, color=button_internal_color).style(button_internal_css).tooltip("发送文本给LLM，模拟弹幕触发操作")
            ui.button('直接复读', color=button_internal_color).style(button_internal_css).tooltip("发送文本给内部机制，触发TTS 复读类型的消息")
            ui.button('调教', color=button_internal_color).style(button_internal_css).tooltip("发送文本给LLM，但不会进行TTS等操作")
            ui.button('直接复读-插队首', color=button_internal_color).style(button_internal_css).tooltip("最高优先级 发送文本给内部机制，触发TTS 直接复读类型的消息")

    # 对话打断
    with ui.expansion('对话打断', icon="settings", value=True).classes('w-2/3'):
        with ui.grid(columns=3):
            with ui.column().style("width:100%;"):
                ui.switch('启用', value=get_nested_value(config, "talk", "interrupt_talk", "enable")).style(switch_internal_css)
                ui.textarea(label='打断关键词', placeholder='如：等一下、住嘴 多个请换行分隔', value="\n".join(get_nested_value(config, "talk", "interrupt_talk", "keywords") or [])).style("width:100%;")
        with ui.card().style(card_css):
            ui.label("清除类型")
            with ui.row():
                for key, label in [("message_queue", "待合成消息队列"), ("voice_tmp_path_queue", "待播放音频队列"), ("audio_play", "正在播放中的音频")]:
                    ui.checkbox(text=label, value=key in (get_nested_value(config, "talk", "interrupt_talk", "clean_type") or []))

    # 语音唤醒与睡眠
    with ui.expansion('语音唤醒与睡眠', icon="settings", value=True).classes('w-2/3'):
        with ui.grid(columns=3):
            with ui.column().style("width:100%;"):
                ui.switch('启用', value=get_nested_value(config, "talk", "wakeup_sleep", "enable")).style(switch_internal_css)
                ui.select(label='唤醒模式', options={"长期唤醒": "长期唤醒", "单次唤醒": "单次唤醒"}, value=get_nested_value(config, "talk", "wakeup_sleep", "mode")).style("width:100%").tooltip("长期唤醒：说完唤醒词后，会触发提示语；单次唤醒：每次对话都需要携带唤醒词")
                ui.textarea(label='唤醒词', placeholder='如：管家 多个请换行分隔', value="\n".join(get_nested_value(config, "talk", "wakeup_sleep", "wakeup_word") or [])).style("width:100%;")
                ui.textarea(label='睡眠词', placeholder='如：关机 多个请换行分隔', value="\n".join(get_nested_value(config, "talk", "wakeup_sleep", "sleep_word") or [])).style("width:100%;")
                ui.textarea(label='唤醒提示语', placeholder='如：在的 多个请换行分隔', value="\n".join(get_nested_value(config, "talk", "wakeup_sleep", "wakeup_copywriting") or [])).style("width:100%;")
                ui.textarea(label='睡眠提示语', placeholder='如：晚安 多个请换行分隔', value="\n".join(get_nested_value(config, "talk", "wakeup_sleep", "sleep_copywriting") or [])).style("width:100%;")

    # 谷歌STT
    with ui.expansion('谷歌', icon="settings", value=False).classes('w-2/3'):
        with ui.grid(columns=3):
            ui.select(label='目标翻译语言', options={"zh-CN": "zh-CN", "en-US": "en-US", "ja-JP": "ja-JP"}, value=get_nested_value(config, "talk", "google", "tgt_lang")).style("width:100%;")

    # 百度STT
    with ui.expansion('百度', icon="settings", value=False).classes('w-2/3'):
        with ui.grid(columns=3):
            ui.input(label='AppID', value=get_nested_value(config, "talk", "baidu", "app_id"), placeholder='百度云 语音识别应用的 AppID').style("width:100%;")
            ui.input(label='API Key', value=get_nested_value(config, "talk", "baidu", "api_key"), placeholder='百度云 语音识别应用的 API Key').style("width:100%;")
            ui.input(label='Secret Key', value=get_nested_value(config, "talk", "baidu", "secret_key"), placeholder='百度云 语音识别应用的 Secret Key').style("width:100%;")

    # faster_whisper
    with ui.expansion('faster_whisper', icon="settings", value=False).classes('w-2/3'):
        with ui.grid(columns=3):
            ui.input(label='model_size', value=get_nested_value(config, "talk", "faster_whisper", "model_size"), placeholder='Size of the model to use').style("width:100%;")
            ui.select(label='识别语言', options={l: l for l in ["自动识别", 'zh', 'en', 'ja', 'ko', 'yue']}, value=get_nested_value(config, "talk", "faster_whisper", "language")).style("width:100%;")
            ui.select(label='device', options={"cuda": "cuda", "cpu": "cpu", "auto": "auto"}, value=get_nested_value(config, "talk", "faster_whisper", "device")).style("width:100%;")
            ui.select(label='compute_type', options={"float16": "float16", "int8_float16": "int8_float16", "int8": "int8"}, value=get_nested_value(config, "talk", "faster_whisper", "compute_type")).style("width:100%;")
            ui.input(label='download_root', value=get_nested_value(config, "talk", "faster_whisper", "download_root"), placeholder='模型下载路径').style("width:100%;")
            ui.input(label='beam_size', value=get_nested_value(config, "talk", "faster_whisper", "beam_size"), placeholder='候选序列数').style("width:100%;")

    # SenseVoice
    with ui.expansion('SenseVoice', icon="settings", value=False).classes('w-2/3'):
        with ui.grid(columns=3):
            ui.input(label='ASR 模型路径', value=get_nested_value(config, "talk", "sensevoice", "asr_model_path"), placeholder='ASR模型路径').style("width:100%;").tooltip("ASR模型路径")
            ui.input(label='VAD 模型路径', value=get_nested_value(config, "talk", "sensevoice", "vad_model_path"), placeholder='VAD模型路径').style("width:100%;").tooltip("VAD模型路径")
            ui.input(label='VAD 单段最大语音时间', value=get_nested_value(config, "talk", "sensevoice", "vad_max_single_segment_time"), placeholder='VAD单段最大语音时间').style("width:100%;").tooltip("VAD单段最大语音时间")
            ui.input(label='device', value=get_nested_value(config, "talk", "sensevoice", "device"), placeholder='使用设备device').style("width:100%;").tooltip("使用设备device")
            ui.select(label='识别语言', options={'zh': 'zh', 'en': 'en', 'jp': 'jp'}, value=get_nested_value(config, "talk", "sensevoice", "language")).style("width:100%;")
            ui.input(label='text_norm', value=get_nested_value(config, "talk", "sensevoice", "text_norm"), placeholder='text_norm').style("width:100%;").tooltip("text_norm")
            ui.input(label='batch_size_s', value=get_nested_value(config, "talk", "sensevoice", "batch_size_s"), placeholder='batch_size_s').style("width:100%;").tooltip("batch_size_s")
            ui.input(label='batch_size', value=get_nested_value(config, "talk", "sensevoice", "batch_size"), placeholder='batch_size').style("width:100%;").tooltip("batch_size")
