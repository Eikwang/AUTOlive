# -*- coding: UTF-8 -*-
"""
聊天标签页模块
从 webui-bak.py 的 talk_page 拆分而来

布局规范：
- 设置项用最多 3 列网格排布，组件宽度跟随列宽（width:100%）
- 开关+关联参数 为上下结构单元：开关在上，输入框在下
- 两栏骨架（2026-10-25）：左 2/3 设置区 + 右 1/3 聊天记录——嵌入式布局
  替代 position:fixed 悬浮面板（悬浮会遮挡设置内容）
- 聊天记录气泡化（2026-10-30 聊天审查批次）：用户左侧、AI 右侧（聊天工具
  样式），正文字体 22px；系统反馈（唤醒/睡眠/打断/舍弃）居中灰字
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
    # 嵌入页面右侧 1/3，替代悬浮面板；2026-11-04 滚动分离：行高锁定视口，
    # 左栏参数与右栏聊天记录各自内部滚动，互不干涉）=====
    with ui.row().style("width:100%; align-items:stretch; gap:16px; flex-wrap:wrap; height:calc(100vh - 140px);"):
        with ui.column().style("flex:2 1 480px; min-width:0; gap:12px; overflow-y:auto; height:100%; padding-right:4px;"):

            # ===== 互斥开关组（D-9，2026-10-30 聊天审查批次）：启用按键监听 与
            # 直接语音对话 互斥——开启其一自动关闭另一项，toast 明示不静默切换；
            # 直接语音对话关闭时语音唤醒与睡眠一并失去前置条件（任务书）=====
            _wakeup_gate = {"switch": None}  # 唤醒开关引用（后置创建，经此间接持有）

            def _disable_wakeup_if_on():
                """直接语音对话失去启用条件时联动关闭语音唤醒与睡眠。"""
                _w_sw = _wakeup_gate.get("switch")
                if _w_sw is not None and _w_sw.value:
                    _w_sw.value = False
                    set_config_callback("talk", "wakeup_sleep", "enable", value=False)
                    return True
                return False

            def _on_key_listener_change(e):
                # D-9：开启按键监听 → 自动关闭直接语音对话（toast 明示）
                if e.value and _direct_voice_switch.value:
                    _direct_voice_switch.value = False
                    set_config_callback("talk", "direct_run_talk", value=False)
                    if _disable_wakeup_if_on():
                        ui.notify("已自动关闭'直接语音对话'（语音唤醒与睡眠依赖该功能，已一并关闭）", type='warning')
                    else:
                        ui.notify("已自动关闭'直接语音对话'", type='warning')

            def _on_direct_voice_change(e):
                # D-9：开启直接语音对话 → 自动关闭按键监听（toast 明示）
                if e.value and _key_listener_switch.value:
                    _key_listener_switch.value = False
                    set_config_callback("talk", "key_listener_enable", value=False)
                    ui.notify("已自动关闭'启用按键监听'", type='warning')

            with ui.grid(columns=2):
                _key_listener_switch = _auto_save(
                    ui.switch('启用按键监听', value=get_nested_value(config, "talk", "key_listener_enable")).style(switch_internal_css).tooltip("启用后，可以通过键盘单击下放配置的录音按键，启动语音识别对话功能。与「直接语音对话」互斥"),
                    ("talk", "key_listener_enable"))
                _direct_voice_switch = _auto_save(
                    ui.switch('直接语音对话', value=get_nested_value(config, "talk", "direct_run_talk")).style(switch_internal_css).tooltip("如果启用了，将在首次运行时直接进行语音识别，而不需手动点击开始按键。与「启用按键监听」互斥"),
                    ("talk", "direct_run_talk"))
                # 注册互斥联动必须在开关创建之后（此前注册在创建前——构建期
                # NameError「cannot access local variable '_key_listener_switch'」
                # 中断整个 talk 页构建，前端兜底成「加载配置失败」卡）
                _key_listener_switch.on_value_change(_on_key_listener_change)
                _direct_voice_switch.on_value_change(_on_direct_voice_change)
                # 检测声卡输入设备（默认使用第一个麦克风设备——config 为空时
                # 回落显示设备列表首个，后端 device_index 为空走系统默认输入设备）
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
                if _device_index_val:
                    _device_index_default = str(_device_index_val)
                elif _device_index_options:
                    _device_index_default = next(iter(_device_index_options))
                else:
                    _device_index_default = None
                _auto_save(ui.select(
                    label='声卡输入设备',
                    options=_device_index_options,
                    value=_device_index_default
                ).style("width:100%;").tooltip('这就是语言对话输入的声卡（麦克风），默认使用第一个麦克风设备，选择你对应的麦克风即可，如果需要监听电脑声卡可以配合虚拟声卡来实现'), ("talk", "device_index"))

                with ui.column().style("width:100%;"):
                    # 2026-10-30：「播放中不进行录音的检测间隔」参数移除——判断
                    # 逻辑改为待播放音频列表为 0 时才进行录音检测（登记于
                    # docs/参数生效语义矩阵.md）
                    _auto_save(ui.switch('播放中不进行录音', value=get_nested_value(config, "talk", "no_recording_during_playback")).style(switch_internal_css).tooltip('开启后，在AI回复-音频生成-音频播放过程中，不捕获、不处理用户语音（待播放音频列表为 0 时才进行录音检测）。开启「连续对话」时本项让位'), ("talk", "no_recording_during_playback"))

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
                _auto_save(ui.input(label='CHANNELS', value=get_nested_value(config, "talk", "CHANNELS"), placeholder='录音参数').style("width:100%;").tooltip('录音的声道数，默认 1 单声道'), ("talk", "CHANNELS"))
                _auto_save(ui.input(label='RATE', value=get_nested_value(config, "talk", "RATE"), placeholder='录音参数').style("width:100%;").tooltip('采样率，默认 22050'), ("talk", "RATE"))
                _auto_save(ui.switch('流式识别（边说边转）', value=get_nested_value(config, "talk", "asr_streaming")).style(switch_internal_css).tooltip('开启后中文边说边转写，说完立即发给大模型（省去整段识别等待）；关闭回退整段识别。仅中文生效，首次使用需下载流式模型'), ("talk", "asr_streaming"))
                _auto_save(ui.switch('连续对话', value=get_nested_value(config, "talk", "continuous_talk")).style(switch_internal_css).tooltip('开启后，用户可在AI回答-音频合成-音频播放期间再次与系统对话：系统舍弃未回复、未合成的信息和未播放的音频，处理最新的信息；待播放音频 ≥2 时舍弃等待播放音频只播最新一条（最新优先）'), ("talk", "continuous_talk"))

            # 聊天框（T10 同期：三按钮接线——此前无 on_click 装饰性按钮，复读/插队链断；
            # 2026-10-30：调教按钮接线 tuning 通道——指正 AI 并加入上下文，不进行 TTS）
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
                    ui.button('直接复读', on_click=lambda: _send_chat("reread"), color=button_internal_color).style(button_internal_css).tooltip("直接合成文字并加入播放音频列表，不走AI对话系统流程")
                    ui.button('调教', on_click=lambda: _send_chat("tuning"), color=button_internal_color).style(button_internal_css).tooltip("指正AI的错误，并将该信息加入AI的上下文中用于后续对话，不进行TTS播报")
                    ui.button('直接复读-插队首', on_click=lambda: _send_chat("reread_top_priority"), color=button_internal_color).style(button_internal_css).tooltip("直接合成音频并给最大权重，优先播放（999 暂停当前播放，复读完续播）")

            # 对话打断（2026-10-30：全部控件接线自动保存——此前配置改动不落盘；
            # 打断关键词/清除类型/提示语由 conversation_manager 消费）
            with ui.expansion('对话打断', icon="settings", value=True).classes('w-full'):
                with ui.grid(columns=2):
                    with ui.column().style("width:100%;"):
                        _auto_save(ui.switch('启用', value=get_nested_value(config, "talk", "interrupt_talk", "enable")).style(switch_internal_css).tooltip('开启后触发打断关键词时，中断播放中的音频，按清除类型舍弃对应队列'), ("talk", "interrupt_talk", "enable"))
                        _auto_save(ui.textarea(label='打断关键词', placeholder='如：等一下、住嘴 多个请换行分隔', value="\n".join(get_nested_value(config, "talk", "interrupt_talk", "keywords") or [])).style("width:100%;"), ("talk", "interrupt_talk", "keywords"), split_lines=True)
                        _auto_save(ui.textarea(label='打断提示语', placeholder='触发打断后播报与显示的提示语，多个请换行分隔（留空则仅显示灰字反馈）', value="\n".join(get_nested_value(config, "talk", "interrupt_talk", "copywriting") or [])).style("width:100%;"), ("talk", "interrupt_talk", "copywriting"), split_lines=True)
                with ui.card().style(card_css):
                    ui.label("清除类型")
                    _clean_boxes = {}

                    def _save_clean_type():
                        set_config_callback("talk", "interrupt_talk", "clean_type",
                                            value=[k for k, b in _clean_boxes.items() if b.value])

                    with ui.row():
                        for key, label in [("message_queue", "待合成消息队列"), ("voice_tmp_path_queue", "待播放音频队列"), ("audio_play", "正在播放中的音频")]:
                            _cb = ui.checkbox(text=label, value=key in (get_nested_value(config, "talk", "interrupt_talk", "clean_type") or []))
                            _clean_boxes[key] = _cb
                            _cb.on_value_change(lambda _e: _save_clean_type())

            # 语音唤醒与睡眠（2026-10-30：全部控件接线自动保存 + 前置条件门控——
            # 需要开启「直接语音对话」才能启用）
            with ui.expansion('语音唤醒与睡眠', icon="settings", value=True).classes('w-full'):
                with ui.grid(columns=2):
                    with ui.column().style("width:100%;"):
                        _wakeup_enable = _auto_save(ui.switch('启用', value=get_nested_value(config, "talk", "wakeup_sleep", "enable")).style(switch_internal_css).tooltip('决定是否启用睡眠机制。需要先开启「直接语音对话」'), ("talk", "wakeup_sleep", "enable"))
                        _wakeup_gate["switch"] = _wakeup_enable

                        def _on_wakeup_enable_change(e):
                            # 任务书：语音唤醒与睡眠需要开启「直接语音对话」才能启用
                            if e.value and not get_nested_value(config, "talk", "direct_run_talk"):
                                _wakeup_enable.value = False
                                set_config_callback("talk", "wakeup_sleep", "enable", value=False)
                                ui.notify("需先开启'直接语音对话'才能启用语音唤醒与睡眠", type='warning')

                        _wakeup_enable.on_value_change(_on_wakeup_enable_change)

                        # 唤醒模式下拉（构建期 value 必须在 options 内——Quasar
                        # Invalid value 中断 tab 构建；旧值「临时唤醒」按别名显示为
                        # 单次唤醒，后端双名兼容）
                        _mode_options = {"长期唤醒": "长期唤醒", "单次唤醒": "单次唤醒"}
                        _mode_val = get_nested_value(config, "talk", "wakeup_sleep", "mode")
                        if _mode_val == "临时唤醒":
                            _mode_val = "单次唤醒"
                        if _mode_val not in _mode_options:
                            _mode_val = "长期唤醒"
                        _auto_save(ui.select(label='唤醒模式', options=_mode_options, value=_mode_val).style("width:100%").tooltip("长期唤醒：说出唤醒词后保持唤醒状态，直到说出睡眠词；单次唤醒：每完成一次对话自动进入睡眠模式，直到下次唤醒"), ("talk", "wakeup_sleep", "mode"))
                        _auto_save(ui.textarea(label='唤醒词', placeholder='如：管家 多个请换行分隔', value="\n".join(get_nested_value(config, "talk", "wakeup_sleep", "wakeup_word") or [])).style("width:100%;"), ("talk", "wakeup_sleep", "wakeup_word"), split_lines=True)
                        _auto_save(ui.textarea(label='睡眠词', placeholder='如：关机 多个请换行分隔', value="\n".join(get_nested_value(config, "talk", "wakeup_sleep", "sleep_word") or [])).style("width:100%;"), ("talk", "wakeup_sleep", "sleep_word"), split_lines=True)
                        _auto_save(ui.textarea(label='唤醒提示语', placeholder='如：在的 多个请换行分隔', value="\n".join(get_nested_value(config, "talk", "wakeup_sleep", "wakeup_copywriting") or [])).style("width:100%;"), ("talk", "wakeup_sleep", "wakeup_copywriting"), split_lines=True)
                        _auto_save(ui.textarea(label='睡眠提示语', placeholder='如：晚安 多个请换行分隔', value="\n".join(get_nested_value(config, "talk", "wakeup_sleep", "sleep_copywriting") or [])).style("width:100%;"), ("talk", "wakeup_sleep", "sleep_copywriting"), split_lines=True)

            # 2026-10-25 精简：谷歌/百度（翻译功能已在「翻译」页集成）、
            # faster_whisper/SenseVoice（ASR 引擎自动配置：按「回复语言」
            # need_lang 选模型，引擎参数由 config 默认值驱动）四个配置区块移除，
            # 聊天页只留用户需要操作的面板

        # ===== 右 1/3：聊天记录面板（嵌入卡片；栏高锁定视口，滚动区独立滚动）=====
        # 2026-11-05 修复「聊天记录与参数页联动滚动」：曾将 chat_log_container
        # 创建为 scroll_area 的兄弟节点——滚动区恒空，聊天记录直接撑高卡片
        # 溢出到页面级滚动容器，滚动页面时与左栏参数区联动。现移入
        # scroll_area 内部：记录在右栏内部滚动，scroll_to 直达最新一条。
        with ui.column().style("flex:1 1 300px; min-width:280px; height:100%; gap:8px;"):
            with ui.card().style((card_css or "") + " width:100%; height:100%; display:flex; flex-direction:column;"):
                with ui.row().style("align-items:center; gap:6px;"):
                    ui.icon("question_answer").style("color: var(--primary, #1976d2);")
                    ui.label('聊天记录').style('font-size:15px; font-weight:500;')
                with ui.scroll_area().style(
                        "width:100%; flex:1 1 0; min-height:0;") as scroll_area_chat_box:
                    chat_log_container = ui.column().style(
                        "gap:8px; width:100%; padding:4px; display:flex; "
                        "flex-direction:column; align-items:stretch;")

    _chat_rendered_count = {"n": -1}

    def _render_bubble(_entry: Dict):
        """单条聊天记录渲染（2026-10-30 气泡化）：
        问=用户左侧灰底气泡 / 答=AI 右侧主题色气泡（正文字体 22px，聊天工具
        样式）；system=居中灰字轻量反馈（D-10，唤醒/睡眠/打断/舍弃）。"""
        _ctype = _entry.get('content_type')
        _content = _entry.get('content', '')
        _src_label = {'voice': '语音', 'chatbox': '聊天框', 'comment': '弹幕',
                      'system': '系统'}.get(_entry.get('chat_source'), '')
        if _entry.get('chat_source') == 'system' or _src_label == '系统':
            # 系统事件反馈：居中灰字（轻量，不打断对话流视觉）
            ui.label(_content).style(
                'font-size:14px; color:var(--text-hint); align-self:center; '
                'text-align:center; word-break:break-all; white-space:pre-wrap; '
                'max-width:90%; padding:0 6px;')
            return
        _is_answer = _ctype == 'answer'
        _meta = (f"{_src_label or _entry.get('type', '')} · "
                 f"{'AI' if _is_answer else _entry.get('username', '')} · "
                 f"{_entry.get('timestamp', '')}")
        with ui.column().style(
                f"width:100%; gap:2px; align-items:{'flex-end' if _is_answer else 'flex-start'};"):
            ui.label(_meta).style(
                'font-size:12px; color:var(--text-hint); padding:0 6px;')
            ui.label(_content).style(
                'font-size:22px; line-height:1.5; word-break:break-all; '
                'white-space:pre-wrap; max-width:85%; padding:8px 14px; '
                'border-radius:12px; '
                + ('background:rgba(25,118,210,0.14); color:var(--text-body);'
                   if _is_answer else
                   'background:rgba(127,127,127,0.12); color:var(--text-body);'))

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
                _render_bubble(_entry)
        # 新记录自动滚到底部（仅数量变化时触发；延迟执行确保客户端 DOM
        # 已完成重渲染，否则 scroll_to 会在内容更新前触发而停不到底部）
        def _scroll_to_latest():
            try:
                scroll_area_chat_box.scroll_to(percent=1.0)
            except Exception:
                pass
        ui.timer(0.3, _scroll_to_latest, once=True)

    _refresh_chat_log()
    ui.timer(2.0, _refresh_chat_log)
