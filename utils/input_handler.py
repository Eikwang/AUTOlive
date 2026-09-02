"""
输入处理模块
提供键盘输入处理、按键监听等功能
"""

import os
import time
import threading
from typing import Callable, Optional

import keyboard

from utils.my_log import logger
from utils.config import Config


class InputHandler:
    """输入处理器类"""
    
    def __init__(self, config: Config, 
                 on_start_recording: Optional[Callable] = None,
                 on_stop_recording: Optional[Callable] = None):
        """
        初始化输入处理器
        
        Args:
            config: 配置对象
            on_start_recording: 开始录音的回调函数
            on_stop_recording: 停止录音的回调函数
        """
        self.config = config
        self.on_start_recording = on_start_recording
        self.on_stop_recording = on_stop_recording
        
        # 按键配置
        self.trigger_key = config.get("talk", "trigger_key")
        self.stop_trigger_key = config.get("talk", "stop_trigger_key")
        
        # 按键冷却
        self.last_pressed = 0
        self.cooldown = 0.5  # 0.5秒冷却时间
        
        # 监听线程
        self.listener_thread = None
        self.is_listening = False
        
        # 按键事件处理函数
        self._key_press_handler = None
        
        logger.info("输入处理器初始化完成")
    
    def _on_key_press(self, event):
        """
        按键按下事件处理函数
        
        Args:
            event: 键盘事件
        """
        # 是否启用按键监听
        if not self.config.get("talk", "key_listener_enable"):
            return
        
        # 按键CD
        current_time = time.time()
        if current_time - self.last_pressed < self.cooldown:
            return
        
        # 触发按键部分的判断
        trigger_key_lower = None
        stop_trigger_key_lower = None
        
        # trigger_key是字母, 整个小写
        if self.trigger_key.isalpha():
            trigger_key_lower = self.trigger_key.lower()
        
        # stop_trigger_key是字母, 整个小写
        if self.stop_trigger_key.isalpha():
            stop_trigger_key_lower = self.stop_trigger_key.lower()
        
        # 判断按键
        if trigger_key_lower:
            if event.name == self.trigger_key or event.name == trigger_key_lower:
                logger.info(f"检测到单击键盘 {event.name}，即将开始录音~")
                self._handle_start_recording()
            elif event.name == self.stop_trigger_key or event.name == stop_trigger_key_lower:
                logger.info(f"检测到单击键盘 {event.name}，即将停止录音~")
                self._handle_stop_recording()
            else:
                return
        else:
            if event.name == self.trigger_key:
                logger.info(f"检测到单击键盘 {event.name}，即将开始录音~")
                self._handle_start_recording()
            elif event.name == self.stop_trigger_key:
                logger.info(f"检测到单击键盘 {event.name}，即将停止录音~")
                self._handle_stop_recording()
            else:
                return
        
        # 更新按键时间
        self.last_pressed = current_time
    
    def _handle_start_recording(self):
        """处理开始录音事件"""
        if self.on_start_recording:
            try:
                self.on_start_recording()
            except Exception as e:
                logger.error(f"开始录音回调失败: {e}")
        else:
            logger.warning("未设置开始录音回调函数")
    
    def _handle_stop_recording(self):
        """处理停止录音事件"""
        if self.on_stop_recording:
            try:
                self.on_stop_recording()
            except Exception as e:
                logger.error(f"停止录音回调失败: {e}")
        else:
            logger.warning("未设置停止录音回调函数")
    
    def start_listening(self):
        """启动按键监听"""
        if self.is_listening:
            logger.warning("按键监听已在运行中")
            return
        
        # 是否启用按键监听
        if not self.config.get("talk", "key_listener_enable"):
            logger.info("按键监听未启用")
            return
        
        # 注册按键事件
        self._key_press_handler = self._on_key_press
        keyboard.on_press(self._key_press_handler)
        
        self.is_listening = True
        logger.info(f"按键监听已启动，触发键: {self.trigger_key}, 停止键: {self.stop_trigger_key}")
        
        # 启动监听线程
        def listen_task():
            try:
                # 进入监听状态，等待按键按下
                keyboard.wait()
            except KeyboardInterrupt:
                logger.info("按键监听被中断")
            except Exception as e:
                logger.error(f"按键监听异常: {e}")
            finally:
                self.is_listening = False
        
        self.listener_thread = threading.Thread(target=listen_task, daemon=True)
        self.listener_thread.start()
    
    def stop_listening(self):
        """停止按键监听"""
        if not self.is_listening:
            return
        
        # 移除按键事件处理器
        if self._key_press_handler:
            keyboard.unhook(self._key_press_handler)
            self._key_press_handler = None
        
        self.is_listening = False
        logger.info("按键监听已停止")
    
    def set_callbacks(self, on_start_recording: Callable = None, 
                     on_stop_recording: Callable = None):
        """
        设置回调函数
        
        Args:
            on_start_recording: 开始录音的回调函数
            on_stop_recording: 停止录音的回调函数
        """
        if on_start_recording:
            self.on_start_recording = on_start_recording
        if on_stop_recording:
            self.on_stop_recording = on_stop_recording
        
        logger.info("回调函数已更新")
    
    def get_key_config(self) -> dict:
        """
        获取按键配置
        
        Returns:
            dict: 按键配置信息
        """
        return {
            "trigger_key": self.trigger_key,
            "stop_trigger_key": self.stop_trigger_key,
            "key_listener_enable": self.config.get("talk", "key_listener_enable"),
            "cooldown": self.cooldown,
        }
    
    def update_key_config(self, trigger_key: str = None, 
                         stop_trigger_key: str = None,
                         cooldown: float = None):
        """
        更新按键配置
        
        Args:
            trigger_key: 触发键
            stop_trigger_key: 停止键
            cooldown: 冷却时间
        """
        if trigger_key:
            self.trigger_key = trigger_key
        if stop_trigger_key:
            self.stop_trigger_key = stop_trigger_key
        if cooldown is not None:
            self.cooldown = cooldown
        
        logger.info(f"按键配置已更新: trigger={self.trigger_key}, stop={self.stop_trigger_key}, cooldown={self.cooldown}")
    
    def simulate_key_press(self, key: str):
        """
        模拟按键按下
        
        Args:
            key: 要模拟的按键
        """
        try:
            keyboard.press_and_release(key)
            logger.info(f"模拟按键: {key}")
        except Exception as e:
            logger.error(f"模拟按键失败: {e}")
    
    def is_key_pressed(self, key: str) -> bool:
        """
        检查按键是否被按下
        
        Args:
            key: 要检查的按键
            
        Returns:
            bool: 按键是否被按下
        """
        try:
            return keyboard.is_pressed(key)
        except Exception as e:
            logger.error(f"检查按键状态失败: {e}")
            return False


# 创建全局实例（可选）
_input_handler_instance = None


def get_input_handler(config: Config = None, 
                     on_start_recording: Callable = None,
                     on_stop_recording: Callable = None) -> InputHandler:
    """
    获取输入处理器单例
    
    Args:
        config: 配置对象（首次调用时需要）
        on_start_recording: 开始录音的回调函数
        on_stop_recording: 停止录音的回调函数
    
    Returns:
        InputHandler实例
    """
    global _input_handler_instance
    if _input_handler_instance is None:
        if config is None:
            raise ValueError("首次调用需要提供config参数")
        _input_handler_instance = InputHandler(config, on_start_recording, on_stop_recording)
    return _input_handler_instance


def create_input_handler(config: Config, 
                        on_start_recording: Callable = None,
                        on_stop_recording: Callable = None) -> InputHandler:
    """
    创建输入处理器实例
    
    Args:
        config: 配置对象
        on_start_recording: 开始录音的回调函数
        on_stop_recording: 停止录音的回调函数
        
    Returns:
        InputHandler实例
    """
    return InputHandler(config, on_start_recording, on_stop_recording)
