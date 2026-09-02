# -*- coding: UTF-8 -*-
"""
工具处理器模块

处理工具和杂项功能
"""

import os
import traceback
from ..error_handler import handle_error, safe_execute
from ..base import logger


class UtilsHandler:
    """工具处理器"""
    
    def __init__(self, my_handle):
        """
        初始化工具处理器
        
        Args:
            my_handle: My_handle 实例
        """
        self.my_handle = my_handle
    
    def webui_show_chat_log_callback(self, data_type: str, data: dict, resp_content: str):
        """
        回传给webui，用于聊天内容显示
        
        Args:
            data_type (str): 数据内容的类型（多指LLM）
            data (dict): 数据JSON
            resp_content (str): 显示的聊天内容的文本
        """
        try:
            if self.my_handle.config.get("talk", "show_chat_log") == True: 
                if "ori_username" not in data:
                    data["ori_username"] = data["username"]
                if "ori_content" not in data:
                    data["ori_content"] = data["content"]
                    
                # 返回给webui的数据
                return_webui_json = {
                    "type": "llm",
                    "data": {
                        "type": data_type,
                        "username": data["ori_username"], 
                        "content_type": "answer",
                        "content": f"错误：{data_type}无返回，请查看日志" if resp_content is None else resp_content,
                        "timestamp": self.my_handle.common.get_bj_time(0)
                    }
                }

                webui_ip = "127.0.0.1" if self.my_handle.config.get("webui", "ip") == "0.0.0.0" else self.my_handle.config.get("webui", "ip")
                tmp_json = self.my_handle.common.send_request(f'http://{webui_ip}:{self.my_handle.config.get("webui", "port")}/callback', "POST", return_webui_json, timeout=30)
        except Exception as e:
            handle_error(e, {'module': 'handler', 'function': 'unknown'})
    
    def get_room_id(self):
        """
        获取房间号
        
        Returns:
            str: 房间号
        """
        return self.my_handle.config.get("room_display_id")
    
    def get_copywriting_and_audio_synthesis(self, type, data):
        """
        获取文案和音频合成
        
        Args:
            type (str): 类型
            data (dict): 数据
        """
        try:
            username = data["username"]
            
            # 获取文案
            copywriting_list = self.my_handle.config.get("copywriting", type, "list")
            if not copywriting_list:
                return
            
            # 随机选择一个文案
            import random
            copywriting = random.choice(copywriting_list)
            
            # 替换变量
            variables = {
                'username': username,
                'gift_name': data.get("gift_name", ""),
                'gift_num': str(data.get("gift_num", "")),
                'cur_time': self.my_handle.common.get_bj_time(5)
            }
            
            for var, value in variables.items():
                copywriting = copywriting.replace(f'{{{var}}}', value)
            
            # 构建消息
            message = {
                "type": type,
                "tts_type": self.my_handle.config.get("audio_synthesis_type"),
                "data": self.my_handle.config.get(self.my_handle.config.get("audio_synthesis_type")),
                "config": self.my_handle.config.get("filter"),
                "username": username,
                "content": copywriting
            }
            
            # 音频合成
            self.my_handle.audio_synthesis_handle(message)
            
            # WebUI显示
            self.webui_show_chat_log_callback(type, data, copywriting)
        except Exception as e:
            logger.error(traceback.format_exc())
            logger.error(f"获取文案和音频合成失败：{e}")
    
    def get_a_copywriting_and_audio_synthesis(self, type, data):
        """
        获取一个文案和音频合成
        
        Args:
            type (str): 类型
            data (dict): 数据
        """
        try:
            username = data["username"]
            
            # 获取文案
            copywriting_list = self.my_handle.config.get("copywriting", type, "list")
            if not copywriting_list:
                return
            
            # 随机选择一个文案
            import random
            copywriting = random.choice(copywriting_list)
            
            # 替换变量
            variables = {
                'username': username,
                'cur_time': self.my_handle.common.get_bj_time(5)
            }
            
            for var, value in variables.items():
                copywriting = copywriting.replace(f'{{{var}}}', value)
            
            # 构建消息
            message = {
                "type": type,
                "tts_type": self.my_handle.config.get("audio_synthesis_type"),
                "data": self.my_handle.config.get(self.my_handle.config.get("audio_synthesis_type")),
                "config": self.my_handle.config.get("filter"),
                "username": username,
                "content": copywriting
            }
            
            # 音频合成
            self.my_handle.audio_synthesis_handle(message)
            
            # WebUI显示
            self.webui_show_chat_log_callback(type, data, copywriting)
        except Exception as e:
            logger.error(traceback.format_exc())
            logger.error(f"获取一个文案和音频合成失败：{e}")
    
    def get_a_local_audio_and_audio_play(self, type, data):
        """
        获取本地音频和播放
        
        Args:
            type (str): 类型
            data (dict): 数据
        """
        try:
            username = data["username"]
            
            # 获取音频路径
            audio_path = data.get("file_path")
            if not audio_path:
                return
            
            # 构建消息
            message = {
                "type": type,
                "tts_type": self.my_handle.config.get("audio_synthesis_type"),
                "data": self.my_handle.config.get(self.my_handle.config.get("audio_synthesis_type")),
                "config": self.my_handle.config.get("filter"),
                "username": username,
                "content": os.path.abspath(audio_path)
            }
            
            # 音频合成
            self.my_handle.audio_synthesis_handle(message)
            
            # WebUI显示
            self.webui_show_chat_log_callback(type, data, audio_path)
        except Exception as e:
            logger.error(traceback.format_exc())
            logger.error(f"获取本地音频和播放失败：{e}")
    
    def get_a_serial_send_data_and_send(self, type, data):
        """
        获取串口数据并发送
        
        Args:
            type (str): 类型
            data (dict): 数据
        """
        try:
            # TODO: 实现串口数据发送逻辑
            pass
        except Exception as e:
            logger.error(traceback.format_exc())
            logger.error(f"获取串口数据并发送失败：{e}")
    
    def connect_serial_and_send_data(self, data):
        """
        连接串口并发送数据
        
        Args:
            data (dict): 数据
        """
        try:
            # TODO: 实现连接串口并发送数据逻辑
            pass
        except Exception as e:
            logger.error(traceback.format_exc())
            logger.error(f"连接串口并发送数据失败：{e}")
    
    def get_serial_config(self):
        """
        获取串口配置
        
        Returns:
            dict: 串口配置
        """
        try:
            return self.my_handle.config.get("serial", {})
        except Exception as e:
            logger.error(traceback.format_exc())
            logger.error(f"获取串口配置失败：{e}")
            return {}
    
    def get_a_img_path_and_send(self, type, data):
        """
        获取图片路径并发送
        
        Args:
            type (str): 类型
            data (dict): 数据
        """
        try:
            # TODO: 实现获取图片路径并发送逻辑
            pass
        except Exception as e:
            logger.error(traceback.format_exc())
            logger.error(f"获取图片路径并发送失败：{e}")
    
    def keyword_handle_trigger(self, type, data):
        """
        关键词处理触发
        
        Args:
            type (str): 类型
            data (dict): 数据
            
        Returns:
            bool: 是否触发并处理
        """
        try:
            content = data["content"]
            
            # 检查是否启用关键词处理
            if not self.my_handle.config.get("keyword", "enable"):
                return False
            
            # 检查是否匹配关键词
            keywords = self.my_handle.config.get("keyword", "list", [])
            for keyword_config in keywords:
                keyword = keyword_config.get("keyword", "")
                if keyword in content:
                    logger.info(f"触发关键词处理：{keyword}")
                    
                    # TODO: 实现关键词处理逻辑
                    
                    return True
            
            return False
        except Exception as e:
            logger.error(traceback.format_exc())
            logger.error(f"关键词处理触发失败：{e}")
            return False
    
    def gift_handle_trigger(self, type, data):
        """
        礼物处理触发
        
        Args:
            type (str): 类型
            data (dict): 数据
            
        Returns:
            bool: 是否触发并处理
        """
        try:
            # 检查是否启用礼物处理
            if not self.my_handle.config.get("gift_trigger", "enable"):
                return False
            
            # TODO: 实现礼物处理触发逻辑
            
            return False
        except Exception as e:
            logger.error(traceback.format_exc())
            logger.error(f"礼物处理触发失败：{e}")
            return False
    
    def is_data_repeat_in_limited_time(self, data, type):
        """
        限定时间内数据是否重复
        
        Args:
            data (dict): 数据
            type (str): 类型
            
        Returns:
            bool: 是否重复
        """
        try:
            # 获取配置
            filter_config = self.my_handle.config.get("filter")
            
            # 检查是否启用去重
            if not filter_config.get("limited_time_deduplication", "enable"):
                return False
            
            # 获取去重时间
            deduplication_time = filter_config.get("limited_time_deduplication", type, 60)
            
            # 获取当前时间
            import time
            current_time = time.time()
            
            # 检查是否重复
            if type in self.my_handle.live_data:
                for item in self.my_handle.live_data[type]:
                    if current_time - item.get("time", 0) < deduplication_time:
                        if item.get("username") == data.get("username") and item.get("content") == data.get("content"):
                            return True
                
                # 添加到去重列表
                self.my_handle.live_data[type].append({
                    "username": data.get("username"),
                    "content": data.get("content"),
                    "time": current_time
                })
                
                # 限制列表大小
                if len(self.my_handle.live_data[type]) > 100:
                    self.my_handle.live_data[type].pop(0)
            
            return False
        except Exception as e:
            logger.error(traceback.format_exc())
            logger.error(f"限定时间内数据是否重复检查失败：{e}")
            return False