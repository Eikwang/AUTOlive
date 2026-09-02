"""
对话管理模块
提供对话处理、唤醒检查、对话运行等功能
"""

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
    
    def talk_handle(self, content: str) -> Dict:
        """
        处理聊天逻辑
        
        Args:
            content: 聊天内容
            
        Returns:
            Dict: 处理结果
        """
        # 检查唤醒状态
        awake_result = self.check_talk_awake(content)
        
        if awake_result["ret"] == -1:
            # 不需要触发
            return {"success": False, "reason": "未唤醒"}
        
        if self.my_handle is None:
            logger.error("处理器未初始化，无法处理对话")
            return {"success": False, "reason": "处理器未初始化"}
        
        try:
            # 处理聊天内容
            self.my_handle.process_data(content, "comment")
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
            }
        
        mode = self.config.get("talk", "wakeup_sleep", "mode")
        
        if mode == "长期唤醒":
            return self._check_long_term_awake(content)
        elif mode == "临时唤醒":
            return self._check_temporary_awake(content)
        else:
            logger.warning(f"未知的唤醒模式: {mode}")
            return {
                "ret": 0,
                "is_talk_awake": self.is_talk_awake,
                "first": False,
                "trigger_word": None,
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
                }
            return {
                "ret": -1,
                "is_talk_awake": self.is_talk_awake,
                "first": False,
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
                }
            return {
                "ret": 0,
                "is_talk_awake": self.is_talk_awake,
                "first": False,
            }
    
    def _check_temporary_awake(self, content: str) -> Dict:
        """
        检查临时唤醒模式
        
        Args:
            content: 聊天内容
            
        Returns:
            Dict: 唤醒检查结果
        """
        # 临时唤醒模式：每次都需要唤醒词
        trigger_word = self.common.find_substring_in_list(
            content, self.config.get("talk", "wakeup_sleep", "wakeup_word")
        )
        if trigger_word:
            self.is_talk_awake = True
            logger.info("[临时唤醒成功]")
            return {
                "ret": 0,
                "is_talk_awake": self.is_talk_awake,
                "first": True,
                "trigger_word": trigger_word,
            }
        return {
            "ret": -1,
            "is_talk_awake": self.is_talk_awake,
            "first": False,
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
            ret = self.my_handle.stop_audio("pygame", True, True)
    
    def do_listen_and_comment(self, continuous: bool = False):
        """
        监听和评论功能
        
        Args:
            continuous: 是否连续对话
        """
        # 这里需要集成音频处理器的功能
        # 由于依赖复杂，这里先提供框架
        logger.info(f"开始监听和评论，连续模式: {continuous}")
        
        try:
            while not self.stop_do_listen_and_comment_thread_event.is_set():
                # 这里应该调用音频处理器的监听功能
                # 然后处理识别到的语音内容
                time.sleep(0.1)  # 临时实现
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
