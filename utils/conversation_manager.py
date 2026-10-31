"""
对话管理模块
提供对话处理、唤醒检查、对话运行等功能

2026-10-30 聊天功能审查批次：
- 对话打断接线（任务书）：打断关键词命中→按 clean_type 清队/停播，本句不进对话链
- 唤醒/睡眠提示语消费（任务书）：唤醒词/睡眠词触发时播报对应提示语（直接合成入
  播放列表，不走 AI 对话流程）+ 聊天区系统反馈（D-10）
- 唤醒模式对齐（任务书）：「单次唤醒」=每完成一次对话自动进入睡眠（旧值
  「临时唤醒」作别名兼容）；「长期唤醒」保持原语义
- 双重翻转修复：do_listen_and_comment 不再预检唤醒（原实现先查一次再在
  talk_handle 内查一次，状态翻两次——唤醒词句会被二次放行发给 LLM）
- 连续对话最新优先（D-22 输入侧）：新输入到来时舍弃未回复/未合成的 talk 消息，
  处理最新信息；被舍弃输入在聊天区给轻量反馈（D-10）
"""

import random
import re
import threading
import time
from typing import Dict, Optional, Tuple

from utils.my_log import logger
from utils.config import Config
from utils.common import Common
from utils.my_handle import My_handle
import utils.my_global as my_global


class ConversationManager:
    """对话管理器类"""
    
    def __init__(self, config: Config, common: Common, my_handle: Optional[My_handle] = None):
        """
        初始化对话管理器
        
        Args:
            config: 配置对象
            common: 通用工具对象
            my_handle: 处理器对象
        """
        self.config = config
        self.common = common
        self.my_handle = my_handle
        
        # 对话状态
        self.is_talk_awake = False
        self.is_recording = False
        self.do_listen_and_comment_thread = None
        self.stop_do_listen_and_comment_thread_event = threading.Event()
        
        logger.info("对话管理器初始化完成")

    # ===== 系统反馈与提示语播报（2026-10-30 聊天审查批次） =====

    def _system_feedback(self, text: str):
        """D-10：系统事件（唤醒/睡眠/打断/舍弃）写入聊天区灰字反馈。

        经 webui_show_chat_log_callback 网关（chat_source=system，白名单
        已扩展）POST 到前端 /callback → chat_log_buffer → talk 页轮询渲染。
        """
        if not text or self.my_handle is None:
            return
        try:
            self.my_handle.webui_show_chat_log_callback(
                "系统",
                {"username": "系统", "content": text, "chat_source": "system"},
                text, content_type="answer")
        except Exception as e:
            logger.warning(f"聊天区系统反馈发送失败（不影响主流程）：{e}")

    def _speak_prompt(self, text: str):
        """提示语直接播报：构建 reread 型消息直接合成入播放列表
        （不走 AI 对话流程；绕过 reread.enable 弹幕复读开关——提示语是
        聊天功能自身语义，与弹幕复读功能无关）。"""
        if not text or self.my_handle is None:
            return
        try:
            message = {
                "type": "reread",
                "tts_type": self.config.get("audio_synthesis_type"),
                "data": self.config.get(self.config.get("audio_synthesis_type")),
                "config": self.config.get("filter"),
                "username": "系统",
                "content": text,
            }
            self.my_handle.audio_synthesis_handle(message)
        except Exception as e:
            logger.error(f"提示语播报失败：{e}")

    @staticmethod
    def _pick_from_config(config: Config, *keys) -> str:
        """安全读取文案列表并随机取一条（缺键/空列表返回空串）。"""
        try:
            arr = config.get(*keys) or []
        except Exception:
            arr = []
        if isinstance(arr, str):
            arr = [arr]
        return random.choice(arr) if arr else ""

    @staticmethod
    def _strip_words(content: str, words) -> str:
        """去除内容中的全部指定词与标点/空白，判断唤醒句是否无对话内容。"""
        rest = content or ""
        for w in words or []:
            if w:
                rest = rest.replace(w, "")
        return re.sub(r"[\s，。！？、,.!?~—…\-：:；;]+", "", rest)

    # ===== 对话打断（任务书：对话打断节） =====

    def _check_interrupt(self, content: str) -> bool:
        """对话打断检测：命中打断关键词时中断播放、按清除类型舍弃队列。

        无论唤醒状态均生效（应急停播——用户说「住嘴」不该要求先唤醒）；
        触发后本句不再进入对话链。
        D-10：触发后在聊天区给可见反馈（可配提示语 interrupt_talk.copywriting，
        复用唤醒/睡眠提示语模式；未配置时仅灰字提示）。
        """
        try:
            if not self.config.get("talk", "interrupt_talk", "enable"):
                return False
            keywords = self.config.get("talk", "interrupt_talk", "keywords") or []
        except Exception:
            return False
        if not any(k and k in content for k in keywords):
            return False

        logger.info(f"[对话打断] 命中打断关键词：{content}")
        try:
            clean = self.config.get("talk", "interrupt_talk", "clean_type") or []
        except Exception:
            clean = []
        self.clear_queue_and_stop_audio_play(
            message_queue="message_queue" in clean,
            voice_tmp_path_queue="voice_tmp_path_queue" in clean,
            stop_audio_play="audio_play" in clean,
        )
        copywriting = self._pick_from_config(
            self.config, "talk", "interrupt_talk", "copywriting")
        self._system_feedback(copywriting or "（已打断播放）")
        if copywriting:
            self._speak_prompt(copywriting)
        return True

    # ===== 连续对话最新优先（D-22 输入侧） =====

    def _discard_stale_talk_inputs(self):
        """连续对话开启时，新输入到来前舍弃未回复/未合成的 talk 消息
        （处理最新的信息），并在聊天区给轻量反馈（D-10）。"""
        if not self.config.get("talk", "continuous_talk") or self.my_handle is None:
            return
        try:
            removed = self.my_handle.audio.purge_talk_inputs()
        except AttributeError:
            return  # 音频组件未初始化（webui 轻量进程等）
        except Exception as e:
            logger.warning(f"舍弃未合成消息失败（忽略）：{e}")
            return
        if removed:
            logger.info(f"[连续对话] 舍弃 {removed} 条未回复/未合成消息，处理最新信息")
            self._system_feedback(f"已舍弃 {removed} 条未处理消息")

    def talk_handle(self, content: str) -> Dict:
        """
        处理聊天逻辑

        Args:
            content: 聊天内容

        Returns:
            Dict: 处理结果
        """
        # 0、对话打断检测（最高优先级——无论唤醒状态，命中即清队停播）
        if self._check_interrupt(content):
            return {"success": True, "interrupted": True}

        # 1、唤醒检查与状态切换（提示语播报在此处依据返回的事件标记承接）
        awake_result = self.check_talk_awake(content)

        if awake_result["ret"] == -1:
            # 不需要触发
            return {"success": False, "reason": "未唤醒"}

        # 2、唤醒/睡眠切换句承接：提示语播报 + 是否进入对话
        if awake_result.get("first"):
            prompt = awake_result.get("prompt") or ""
            if prompt:
                self._speak_prompt(prompt)
                self._system_feedback(prompt)
            event = awake_result.get("event")
            # 睡眠词句 / 纯唤醒词句（去除唤醒词后无内容）不发给 LLM；
            # 带内容的唤醒句继续走对话（如「管家，今天天气怎么样」）
            if event == "sleep" or awake_result.get("wake_only"):
                # 单次唤醒模式：纯唤醒词触发后即刻回睡（本就没有对话发生）
                if event == "wakeup" and awake_result.get("single_mode"):
                    self.is_talk_awake = False
                    logger.info("[聊天睡眠] 单次唤醒模式：纯唤醒词触发，回睡等待下次唤醒")
                return {"success": True, "awake_result": awake_result}

        if self.my_handle is None:
            logger.error("处理器未初始化，无法处理对话")
            return {"success": False, "reason": "处理器未初始化"}

        try:
            # 处理聊天内容：走 talk 通道（chat_handler.talk_handle 聊天处理链），
            # 传与弹幕路径同构的 dict——此前传 (content, "comment") 会让 str 进入
            # comment_handle 的 data["username"] 索引（T4/ENG H-2）。
            # UI-16 二轮验收（任务 14 语义修正）：chat_source=voice 来源标记，
            # 聊天记录只显示聊天功能互动（语音对话+聊天框发送）
            self.my_handle.process_data(
                {"username": self.config.get("talk", "username"), "content": content,
                 "chat_source": "voice"},
                "talk",
            )
            # 单次唤醒模式：每完成一次对话自动进入睡眠（任务书）——
            # 带内容的唤醒句也算一次对话，完成后同样回睡
            if awake_result.get("single_mode"):
                self.is_talk_awake = False
                logger.info("[聊天睡眠] 单次唤醒模式：对话完成，自动进入睡眠")
            return {"success": True, "awake_result": awake_result}
        except Exception as e:
            logger.error(f"处理聊天内容失败: {e}")
            return {"success": False, "reason": str(e)}
    
    def check_talk_awake(self, content: str) -> Dict:
        """
        检查并切换聊天唤醒状态

        Args:
            content: 聊天内容

        Returns:
            Dict: {
                ret: 是否需要触发（0: 需要, -1: 不需要）
                is_talk_awake: 当前唤醒状态
                first: 是否是第一次触发唤醒or睡眠
                trigger_word: 触发词（如果有）
                event: 事件标记（wakeup=唤醒触发 / sleep=睡眠触发，无切换为 None）
                prompt: 本次触发应播报的提示语（无则空串）
                wake_only: 唤醒句去除唤醒词后无对话内容（纯唤醒词句）
                single_mode: 是否单次唤醒模式（每完成一次对话自动睡眠）
            }
        """
        # 判断是否启动了唤醒词功能
        if not self.config.get("talk", "wakeup_sleep", "enable"):
            # 未启用唤醒词功能，直接返回需要触发
            return {
                "ret": 0,
                "is_talk_awake": self.is_talk_awake,
                "first": False,
                "trigger_word": None,
                "event": None,
                "prompt": "",
                "wake_only": False,
                "single_mode": False,
            }

        mode = self.config.get("talk", "wakeup_sleep", "mode")

        if mode == "长期唤醒":
            return self._check_long_term_awake(content)
        # 单次唤醒（旧值「临时唤醒」别名兼容——唤醒模式下拉曾用名，语义同：
        # 每次对话需携带唤醒词；任务书定名「单次唤醒」并补充自动睡眠语义）
        elif mode in ("单次唤醒", "临时唤醒"):
            return self._check_single_awake(content)
        else:
            logger.warning(f"未知的唤醒模式: {mode}")
            return {
                "ret": 0,
                "is_talk_awake": self.is_talk_awake,
                "first": False,
                "trigger_word": None,
                "event": None,
                "prompt": "",
                "wake_only": False,
                "single_mode": False,
            }
    
    def _check_long_term_awake(self, content: str) -> Dict:
        """
        检查长期唤醒模式

        Args:
            content: 聊天内容

        Returns:
            Dict: 唤醒检查结果
        """
        # 判断现在是否是唤醒状态
        if not self.is_talk_awake:
            # 判断文本内容是否包含唤醒词
            trigger_word = self.common.find_substring_in_list(
                content, self.config.get("talk", "wakeup_sleep", "wakeup_word")
            )
            if trigger_word:
                self.is_talk_awake = True
                logger.info("[聊天唤醒成功]")
                return {
                    "ret": 0,
                    "is_talk_awake": self.is_talk_awake,
                    "first": True,
                    "trigger_word": trigger_word,
                    "event": "wakeup",
                    "prompt": self._pick_from_config(
                        self.config, "talk", "wakeup_sleep", "wakeup_copywriting"),
                    "wake_only": self._strip_words(
                        content,
                        self.config.get("talk", "wakeup_sleep", "wakeup_word") or [],
                    ) == "",
                    "single_mode": False,
                }
            return {
                "ret": -1,
                "is_talk_awake": self.is_talk_awake,
                "first": False,
                "event": None,
                "prompt": "",
                "wake_only": False,
                "single_mode": False,
            }
        else:
            # 判断文本内容是否包含睡眠词
            trigger_word = self.common.find_substring_in_list(
                content, self.config.get("talk", "wakeup_sleep", "sleep_word")
            )
            if trigger_word:
                self.is_talk_awake = False
                logger.info("[聊天睡眠成功]")
                return {
                    "ret": 0,
                    "is_talk_awake": self.is_talk_awake,
                    "first": True,
                    "trigger_word": trigger_word,
                    "event": "sleep",
                    "prompt": self._pick_from_config(
                        self.config, "talk", "wakeup_sleep", "sleep_copywriting"),
                    "wake_only": False,
                    "single_mode": False,
                }
            return {
                "ret": 0,
                "is_talk_awake": self.is_talk_awake,
                "first": False,
                "event": None,
                "prompt": "",
                "wake_only": False,
                "single_mode": False,
            }

    def _check_single_awake(self, content: str) -> Dict:
        """
        检查单次唤醒模式（每完成一次对话自动进入睡眠，直到下次唤醒；
        旧值「临时唤醒」同语义别名）

        Args:
            content: 聊天内容

        Returns:
            Dict: 唤醒检查结果
        """
        # 单次唤醒模式：每次都需要唤醒词（唤醒句可带对话内容，对话完成后自动回睡）
        trigger_word = self.common.find_substring_in_list(
            content, self.config.get("talk", "wakeup_sleep", "wakeup_word")
        )
        if trigger_word:
            self.is_talk_awake = True
            logger.info("[聊天唤醒成功]（单次唤醒）")
            return {
                "ret": 0,
                "is_talk_awake": self.is_talk_awake,
                "first": True,
                "trigger_word": trigger_word,
                "event": "wakeup",
                "prompt": self._pick_from_config(
                    self.config, "talk", "wakeup_sleep", "wakeup_copywriting"),
                "wake_only": self._strip_words(
                    content,
                    self.config.get("talk", "wakeup_sleep", "wakeup_word") or [],
                ) == "",
                "single_mode": True,
            }
        return {
            "ret": -1,
            "is_talk_awake": self.is_talk_awake,
            "first": False,
            "event": None,
            "prompt": "",
            "wake_only": False,
            "single_mode": True,
        }
    
    def clear_queue_and_stop_audio_play(self, message_queue: bool = True, 
                                       voice_tmp_path_queue: bool = True, 
                                       stop_audio_play: bool = True):
        """
        清空队列或停止播放音频
        
        Args:
            message_queue: 是否清空待合成消息队列
            voice_tmp_path_queue: 是否清空待播放音频队列
            stop_audio_play: 是否停止音频播放
        """
        if self.my_handle is None:
            logger.error("处理器未初始化，无法清空队列")
            return
        
        if message_queue:
            ret = self.my_handle.clear_queue("message_queue")
            if ret:
                logger.info("清空待合成消息队列成功！")
            else:
                logger.error("清空待合成消息队列失败！")
        
        if voice_tmp_path_queue:
            ret = self.my_handle.clear_queue("voice_tmp_path_queue")
            if ret:
                logger.info("清空待播放音频队列成功！")
            else:
                logger.error("清空待播放音频队列失败！")
        
        if stop_audio_play:
            # 2026-10-30 审查批次：stop_audio("pygame",...) 链随 pygame 播放分支
            # 退役——本地播放统一 builtin，清队后打断当前流（EDTalk 激活时内部
            # 静默跳过，契约无 flush 端点）
            self.my_handle.audio.stop_current_audio()
    
    def do_listen_and_comment(self, continuous: bool = False):
        """VAD 连续对话循环——T10 语音对话链接通（2026-10-24 框架填充）。

        连续模式：audio_listen 能量 VAD 录一段 → ASR → 唤醒词检查 →
        talk_handle 对话链（LLM/TTS/聊天记录 voice 全链复用）。
        播放中不录音由 audio_listen 内守卫承接（no_recording_during_playback）。

        流式识别（2026-10-25 方案A）：need_lang=zh 且 talk.asr_streaming
        未关闭时创建流式会话，audio_listen 边录边喂（on_chunk=session.feed），
        说完 session.final() 立即出文本——整段「写盘+整段推理」延时消除；
        会话不可用（非中文/模型缺失/开关关闭）回退原整段链。

        Args:
            continuous: 是否连续对话（True=循环至停止事件；False=单轮后退出）
        """
        logger.info(f"开始监听和评论，连续模式: {continuous}")
        from utils.audio_processor import get_audio_processor
        from utils.asr_engine import create_streaming_session, transcribe

        volume_threshold = float(self.config.get("talk", "volume_threshold") or 800.0)
        silence_threshold = int(self.config.get("talk", "silence_threshold") or 15)

        try:
            ap = get_audio_processor(self.config, self.my_handle)
            rate = self.config.get("talk", "RATE")
            round_no = 0
            while not self.stop_do_listen_and_comment_thread_event.is_set():
                # 每轮新建会话（模型单例缓存，创建仅是空 cache/buffer——final 后
                # 会话已闭合，不能跨轮复用）
                session = create_streaming_session(self.config)
                if session:
                    logger.info("[t10] 流式识别已启用（边说边转写）")
                frames = ap.audio_listen(
                    volume_threshold, silence_threshold,
                    on_chunk=(session.feed if session else None))
                if self.stop_do_listen_and_comment_thread_event.is_set():
                    break
                if not frames:
                    continue
                round_no += 1
                if session:
                    text = session.final()
                else:
                    wav_path = ap.frames_to_wav(
                        frames, f"out/record_vad_{round_no}.wav", rate=rate)
                    text = transcribe(wav_path, self.config)
                if not text:
                    logger.info(f"[t10] 连续对话第 {round_no} 轮：识别无文字，跳过")
                    continue
                logger.info(f"[t10] 连续对话第 {round_no} 轮识别：{text}")
                # 唤醒检查统一在 talk_handle 内完成（2026-10-30 双重翻转修复：
                # 原此处预检一次 + talk_handle 内再查一次，唤醒状态翻两次——
                # 唤醒词句被二次放行发给 LLM）
                # D-22 输入侧：连续对话时新输入到来，舍弃未回复/未合成的旧
                # 消息处理最新信息（含聊天区轻量反馈 D-10）
                self._discard_stale_talk_inputs()
                self.talk_handle(text)
                if not continuous:
                    break
        except Exception as e:
            logger.error(f"监听和评论失败: {e}")
        finally:
            logger.info("监听和评论结束")
    
    def direct_run_talk(self):
        """直接运行对话（首次运行时直接进行语音识别）"""
        if not self.is_recording:
            # 是否启用连续对话模式
            if self.config.get("talk", "continuous_talk"):
                self.stop_do_listen_and_comment_thread_event.clear()
                self.do_listen_and_comment_thread = threading.Thread(
                    target=self.do_listen_and_comment, args=(True,)
                )
                self.do_listen_and_comment_thread.start()
            else:
                self.stop_do_listen_and_comment_thread_event.clear()
                self.do_listen_and_comment_thread = threading.Thread(
                    target=self.do_listen_and_comment, args=(False,)
                )
                self.do_listen_and_comment_thread.start()
    
    def start_key_listener(self):
        """启动按键监听"""
        import keyboard
        
        # 从配置文件中读取触发键的字符串配置
        trigger_key = self.config.get("talk", "trigger_key")
        stop_trigger_key = self.config.get("talk", "stop_trigger_key")
        
        # 是否启用了按键监听
        if self.config.get("talk", "key_listener_enable"):
            logger.info(
                f"单击键盘 {trigger_key} 按键进行录音喵~ 由于其他任务还要启动，如果按键没有反应，请等待一段时间（如果使用本地ASR，请等待模型加载完成后使用）"
            )
        
        # 是否启用了直接运行对话
        if self.config.get("talk", "direct_run_talk"):
            logger.info("直接运行对话模式，首次运行时将直接进行语音识别，而不需手动点击开始按键（如果使用本地ASR，请等待模型加载完成后使用）")
            self.direct_run_talk()
        
        # 注册按键事件
        def on_key_press(event):
            if event.name == trigger_key:
                logger.info(f"按下 {trigger_key} 键，开始录音...")
                self.direct_run_talk()
            elif event.name == stop_trigger_key:
                logger.info(f"按下 {stop_trigger_key} 键，停止录音...")
                self.stop_do_listen_and_comment_thread_event.set()
        
        keyboard.on_press(on_key_press)
        
        try:
            # 保持主线程运行
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            logger.info("按键监听已停止")
    
    def get_talk_status(self) -> Dict:
        """
        获取对话状态
        
        Returns:
            Dict: 对话状态信息
        """
        return {
            "is_talk_awake": self.is_talk_awake,
            "is_recording": self.is_recording,
            "is_listening": self.do_listen_and_comment_thread is not None and self.do_listen_and_comment_thread.is_alive(),
        }
    
    def set_talk_awake(self, awake: bool):
        """
        设置对话唤醒状态
        
        Args:
            awake: 是否唤醒
        """
        self.is_talk_awake = awake
        logger.info(f"对话唤醒状态设置为: {awake}")
    
    def stop_all(self):
        """停止所有对话相关功能"""
        self.stop_do_listen_and_comment_thread_event.set()
        if self.do_listen_and_comment_thread and self.do_listen_and_comment_thread.is_alive():
            self.do_listen_and_comment_thread.join(timeout=2)
        logger.info("对话管理器已停止")


# 创建全局实例（可选）
_conversation_manager_instance = None


def get_conversation_manager(config: Config = None, common: Common = None, 
                           my_handle: My_handle = None) -> ConversationManager:
    """
    获取对话管理器单例
    
    Args:
        config: 配置对象（首次调用时需要）
        common: 通用工具对象（首次调用时需要）
        my_handle: 处理器对象（首次调用时需要）
    
    Returns:
        ConversationManager实例
    """
    global _conversation_manager_instance
    if _conversation_manager_instance is None:
        if config is None or common is None:
            raise ValueError("首次调用需要提供config和common参数")
        _conversation_manager_instance = ConversationManager(config, common, my_handle)
    return _conversation_manager_instance


def create_conversation_manager(config: Config, common: Common, 
                              my_handle: My_handle = None) -> ConversationManager:
    """
    创建对话管理器实例
    
    Args:
        config: 配置对象
        common: 通用工具对象
        my_handle: 处理器对象
        
    Returns:
        ConversationManager实例
    """
    return ConversationManager(config, common, my_handle)
