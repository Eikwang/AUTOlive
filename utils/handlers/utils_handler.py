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
    
    def webui_show_chat_log_callback(self, data_type: str, data: dict, resp_content: str, content_type: str = "answer"):
        """
        回传给webui，用于聊天内容显示

        Args:
            data_type (str): 数据内容的类型（多指LLM）
            data (dict): 数据JSON
            resp_content (str): 显示的聊天内容的文本
            content_type (str): question=用户输入 / answer=系统回复

        UI-16 二轮验收（任务 14 语义修正）：聊天记录只显示聊天功能互动
        （语音对话 chat_source=voice / 聊天框发送 chat_source=chatbox），
        弹幕监听链（无标记）在此网关丢弃。标记由 /send（chatbox）与
        conversation_manager（voice）入口打点，经 data 自然透传到本处。
        """
        try:
            if self.my_handle.config.get("talk", "show_chat_log") == True:
                chat_source = (data or {}).get("chat_source", "")
                if chat_source not in ("voice", "chatbox"):
                    return  # 弹幕监听等非聊天互动来源不进聊天记录
                if "ori_username" not in data:
                    data["ori_username"] = data["username"]
                if "ori_content" not in data:
                    data["ori_content"] = data["content"]

                # 返回给webui的数据
                return_webui_json = {
                    "type": "llm",
                    "data": {
                        "type": data_type,
                        "chat_source": chat_source,
                        "username": data["ori_username"],
                        "content_type": content_type,
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
        限定时间内数据是否重复（去重接线：由 timer_handler.process_data 入口统一调用）

        去重键=用户名+内容键：content 缺失时回退 gift_name（礼物按礼物名去重）、
        再回退空串（关注等仅按用户名去重；房间信息由调用方预合成 content 值键）。

        Args:
            data (dict): 数据
            type (str): 类型（comment/gift/entrance/follow/like/room_stats...）

        Returns:
            bool: 是否重复
        """
        try:
            # 获取配置（filter 节为普通 dict——嵌套键须先取节再取键；
            # 原实现 filter_config.get(a, b, c) 三参调用在 dict 上 TypeError
            # 被兜底吞掉返回 False，是去重空转的根因之一）
            filter_config = self.my_handle.config.get("filter") or {}
            dedup_cfg = filter_config.get("limited_time_deduplication") or {}

            # 检查是否启用去重
            if not dedup_cfg.get("enable"):
                return False

            # 获取去重时间（未配置的类型不参与去重——schedule/idle_time_task 等
            # 系统任务同内容重发属正常语义，不得被静默丢弃）
            period = dedup_cfg.get(type)
            if period is None:
                return False
            deduplication_time = float(period)

            # 内容键（类型容错：礼物无 content）
            content = data.get("content")
            if content is None:
                content = data.get("gift_name", "")

            # 获取当前时间
            import time
            current_time = time.time()

            # 检查是否重复（live_data 键缺失时补建——新类型 like/room_stats 等容错）
            bucket = self.my_handle.live_data.setdefault(type, [])
            for item in bucket:
                if current_time - item.get("time", 0) < deduplication_time:
                    if item.get("username") == data.get("username") and item.get("content") == content:
                        return True

            # 添加到去重列表
            bucket.append({
                "username": data.get("username"),
                "content": content,
                "time": current_time
            })

            # 限制列表大小
            if len(bucket) > 100:
                bucket.pop(0)

            return False
        except Exception as e:
            logger.error(traceback.format_exc())
            logger.error(f"限定时间内数据是否重复检查失败：{e}")
            return False