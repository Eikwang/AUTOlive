# -*- coding: UTF-8 -*-
"""
聊天标签页模块
从 webui-bak.py 的 talk_page 拆分而来

布局规范：
- 设置项用最多 3 列网格排布，组件宽度跟随列宽（width:100%）
- 开关+关联参数 为上下结构单元：开关在上，输入框在下
- 两栏骨架（2026-10-25）：左 2/3 设置区 + 右 1/3 聊天记录——嵌入式布局
  替代 position:fixed 悬浮面板（悬浮会遮挡设置内容）
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

    # 聊天记录区域（UI-16 二轮验收任务 14：/callback 共享缓冲接线渲染，
    # ui.timer 轮询——timer 回调只读内存无同步 IO，沿事件循环安全先例）
    from frontend.utils.chat_log_buffer import snapshot as _chat_snapshot
    # 2026-10-25：聊天记录固定记录（弹幕/语音/聊天框互动），无开关——
    # talk.show_chat_log 已退役，来源白名单在 core 发送侧生效

    # ===== 两栏骨架：左 2/3 设置区 + 右 1/3 聊天记录（2026-10-25 用户要求
    # 嵌入页面右侧 1/3，替代悬浮面板；窄窗口自动换行回落为上下布局）=====
    with ui.row().style("width:100%; align-items:flex-start; gap:16px; flex-wrap:wrap;"):
        with ui.column().style("flex:2 1 480px; min-width:0; gap:12px;"):

            # 基础配置
            with ui.grid(columns=2):
                _auto_save(ui.switch('启用按键监听', value=get_nested_value(config, "talk", "key_listener_enable")).style(switch_internal_css).tooltip("启用后，可以通过键盘单击下放配置的录音按键，启动语音识别对话功能"), ("talk", "key_listener_enable"))
                _auto_save(ui.switch('连续对话', value=get_nested_value(config, "talk", "continuous_talk")).style(switch_internal_css).tooltip('仅需按一次录音按键，后续就不需要按了'), ("talk", "continuous_talk"))

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

                _auto_save(ui.switch('直接语音对话', value=get_nested_value(config, "talk", "direct_run_talk")).style(switch_internal_css).tooltip("如果启用了，将在首次运行时直接进行语音识别，而不需手动点击开始按键。"), ("talk", "direct_run_talk"))
                _auto_save(ui.input(label='你的名字', value=get_nested_value(config, "talk", "username"), placeholder='日志中你的名字').style("width:100%;"), ("talk", "username"))

            # 录音配置（2026-10-25：录音类型选项已移除——ASR 按「回复语言」
            # (need_lang) 自动选模型：中文/粤语→达摩 FunASR，其它→fast-whisper）
            with ui.grid(columns=2):
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

            # 聊天框（T10 同期：三按钮接线——此前无 on_click 装饰性按钮，复读/插队链断）
            with ui.grid(columns=1):
                with ui.row():
                    chat_input = ui.textarea(label='聊天框-和AI对话', value="", placeholder='此处填写对话内容可以直接进行对话').style("width:100%;")

                    def _send_chat(msg_type: str):
                        text = (chat_input.value or "").strip()
                        if not text:
                            ui.notify('请先在聊天框输入内容', type='warning')
                            return
                        sent = chat_send_callback(msg_type, text) if chat_send_callback else False
                        if sent:
                            ui.notify('已发送', type='positive')
                            chat_input.value = ""
                        else:
                            ui.notify('发送失败——请确认系统已启动', type='negative')

                    ui.button('发送', on_click=lambda: _send_chat("comment"), color=button_internal_color).style(button_internal_css).tooltip("发送文本给LLM，模拟弹幕触发操作")
                    ui.button('直接复读', on_click=lambda: _send_chat("reread"), color=button_internal_color).style(button_internal_css).tooltip("发送文本给内部机制，触发TTS 复读类型的消息")
                    ui.button('调教', color=button_internal_color).style(button_internal_css).tooltip("发送文本给LLM，但不会进行TTS等操作")
                    ui.button('直接复读-插队首', on_click=lambda: _send_chat("reread_top_priority"), color=button_internal_color).style(button_internal_css).tooltip("最高优先级 发送文本给内部机制，触发TTS 直接复读类型的消息（999 暂停当前播放，复读完续播）")

            # 对话打断
            with ui.expansion('对话打断', icon="settings", value=True).classes('w-full'):
                with ui.grid(columns=2):
                    with ui.column().style("width:100%;"):
                        ui.switch('启用', value=get_nested_value(config, "talk", "interrupt_talk", "enable")).style(switch_internal_css)
                        ui.textarea(label='打断关键词', placeholder='如：等一下、住嘴 多个请换行分隔', value="\n".join(get_nested_value(config, "talk", "interrupt_talk", "keywords") or [])).style("width:100%;")
                with ui.card().style(card_css):
                    ui.label("清除类型")
                    with ui.row():
                        for key, label in [("message_queue", "待合成消息队列"), ("voice_tmp_path_queue", "待播放音频队列"), ("audio_play", "正在播放中的音频")]:
                            ui.checkbox(text=label, value=key in (get_nested_value(config, "talk", "interrupt_talk", "clean_type") or []))

            # 语音唤醒与睡眠
            with ui.expansion('语音唤醒与睡眠', icon="settings", value=True).classes('w-full'):
                with ui.grid(columns=2):
                    with ui.column().style("width:100%;"):
                        ui.switch('启用', value=get_nested_value(config, "talk", "wakeup_sleep", "enable")).style(switch_internal_css)
                        ui.select(label='唤醒模式', options={"长期唤醒": "长期唤醒", "单次唤醒": "单次唤醒"}, value=get_nested_value(config, "talk", "wakeup_sleep", "mode")).style("width:100%").tooltip("长期唤醒：说完唤醒词后，会触发提示语；单次唤醒：每次对话都需要携带唤醒词")
                        ui.textarea(label='唤醒词', placeholder='如：管家 多个请换行分隔', value="\n".join(get_nested_value(config, "talk", "wakeup_sleep", "wakeup_word") or [])).style("width:100%;")
                        ui.textarea(label='睡眠词', placeholder='如：关机 多个请换行分隔', value="\n".join(get_nested_value(config, "talk", "wakeup_sleep", "sleep_word") or [])).style("width:100%;")
                        ui.textarea(label='唤醒提示语', placeholder='如：在的 多个请换行分隔', value="\n".join(get_nested_value(config, "talk", "wakeup_sleep", "wakeup_copywriting") or [])).style("width:100%;")
                        ui.textarea(label='睡眠提示语', placeholder='如：晚安 多个请换行分隔', value="\n".join(get_nested_value(config, "talk", "wakeup_sleep", "sleep_copywriting") or [])).style("width:100%;")

            # 2026-10-25 精简：谷歌/百度（翻译功能已在「翻译」页集成）、
            # faster_whisper/SenseVoice（ASR 引擎自动配置：按「回复语言」
            # need_lang 选模型，引擎参数由 config 默认值驱动）四个配置区块移除，
            # 聊天页只留用户需要操作的面板

        # ===== 右 1/3：聊天记录面板（嵌入卡片 + sticky 跟随滚动）=====
        with ui.column().style("flex:1 1 300px; min-width:280px; position:sticky; top:12px; gap:8px;"):
            with ui.card().style((card_css or "") + " width:100%;"):
                with ui.row().style("align-items:center; gap:6px;"):
                    ui.icon("question_answer").style("color: var(--primary, #1976d2);")
                    ui.label('聊天记录').style('font-size:15px; font-weight:500;')
                scroll_area_chat_box = ui.scroll_area().style(
                    "width:100%; height:calc(100vh - 200px); min-height:360px; max-height:760px;")
                chat_log_container = ui.column().style("gap:4px; width:100%; padding:4px;")

    _chat_rendered_count = {"n": -1}

    def _refresh_chat_log():
        entries = _chat_snapshot()
        if len(entries) == _chat_rendered_count["n"]:
            return
        _chat_rendered_count["n"] = len(entries)
        chat_log_container.clear()
        with chat_log_container:
            if not entries:
                ui.label('暂无聊天记录——弹幕/语音对话/聊天框互动及系统回答会实时显示在这里').style(
                    'font-size: var(--font-size-assist, 12px); color: var(--text-hint);')
            for _entry in entries:
                _ctype = _entry.get('content_type')
                _tag = {'question': '问', 'answer': '答'}.get(_ctype, '聊')
                _src_label = {'voice': '语音', 'chatbox': '聊天框', 'comment': '弹幕'}.get(
                    _entry.get('chat_source'), '')
                # 问=提示色 / 答=正文色（问答两侧视觉区分；答=AI 回复为主阅读对象）
                _color = 'var(--text-body)' if _ctype == 'answer' else 'var(--text-hint)'
                ui.label(f"[{_entry.get('timestamp', '')}] {_src_label or _entry.get('type', '')}"
                         f"｜{_entry.get('username', '')}（{_tag}）：{_entry.get('content', '')}").style(
                    f'font-size: var(--font-size-small, 12px); color: {_color}; '
                    'word-break: break-all; white-space: pre-wrap; width:100%;')
        # 新记录自动滚到底部（仅数量变化时触发；scroll_to 跨版本签名有差异，静默降级）
        try:
            scroll_area_chat_box.scroll_to(percent=1.0)
        except Exception:
            pass

    _refresh_chat_log()
    ui.timer(2.0, _refresh_chat_log)
