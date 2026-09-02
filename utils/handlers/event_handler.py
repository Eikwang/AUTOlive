# -*- coding: UTF-8 -*-
"""
事件处理器模块

处理各种事件
"""

import os
import traceback
from ..error_handler import handle_error, safe_execute
from ..base import logger


class EventHandler:
    """事件处理器"""
    
    def __init__(self, my_handle):
        """
        初始化事件处理器
        
        Args:
            my_handle: My_handle 实例
        """
        self.my_handle = my_handle
    
    def gift_handle(self, data):
        """
        礼物处理
        
        Args:
            data (dict): 数据
        """
        try:
            username = data["username"]
            gift_name = data["gift_name"]
            gift_num = data["gift_num"]
            
            logger.info(f"[{username}]送出了 {gift_num} 个 {gift_name}")
            
            # 记录数据库
            if self.my_handle.config.get("database", "gift_enable"):
                from datetime import datetime
                insert_data_sql = '''
                INSERT INTO gift (username, gift_name, gift_num, unit_price, total_price, ts) VALUES (?, ?, ?, ?, ?, ?)
                '''
                self.my_handle.db.execute(insert_data_sql, (username, gift_name, gift_num, data.get("unit_price", 0), data.get("total_price", 0), datetime.now()))
            
            # 答谢处理
            if self.my_handle.config.get("thanks", "gift", "enable"):
                self.my_handle.get_copywriting_and_audio_synthesis("gift", data)
        except Exception as e:
            handle_error(e, {'module': 'handler', 'function': 'unknown'})
            logger.error(f"礼物处理失败：{e}")
    
    def entrance_handle(self, data):
        """
        入场处理
        
        Args:
            data (dict): 数据
        """
        try:
            username = data["username"]
            
            logger.info(f"[{username}]进入了直播间")
            
            # 记录数据库
            if self.my_handle.config.get("database", "entrance_enable"):
                from datetime import datetime
                insert_data_sql = '''
                INSERT INTO entrance (username, ts) VALUES (?, ?)
                '''
                self.my_handle.db.execute(insert_data_sql, (username, datetime.now()))
            
            # 答谢处理
            if self.my_handle.config.get("thanks", "entrance", "enable"):
                self.my_handle.get_copywriting_and_audio_synthesis("entrance", data)
        except Exception as e:
            logger.error(traceback.format_exc())
            logger.error(f"入场处理失败：{e}")
    
    def follow_handle(self, data):
        """
        关注处理
        
        Args:
            data (dict): 数据
        """
        try:
            username = data["username"]
            
            logger.info(f"[{username}]关注了直播间")
            
            # 答谢处理
            if self.my_handle.config.get("thanks", "follow", "enable"):
                self.my_handle.get_copywriting_and_audio_synthesis("follow", data)
        except Exception as e:
            logger.error(traceback.format_exc())
            logger.error(f"关注处理失败：{e}")
    
    def schedule_handle(self, data):
        """
        定时任务处理
        
        Args:
            data (dict): 数据
        """
        try:
            username = data.get("username", "系统")
            content = data.get("content", "")
            
            logger.info(f"[定时任务] {content}")
            
            # 构建消息
            message = {
                "type": "schedule",
                "tts_type": self.my_handle.config.get("audio_synthesis_type"),
                "data": self.my_handle.config.get(self.my_handle.config.get("audio_synthesis_type")),
                "config": self.my_handle.config.get("filter"),
                "username": username,
                "content": content
            }
            
            # 音频合成
            self.my_handle.audio_synthesis_handle(message)
        except Exception as e:
            logger.error(traceback.format_exc())
            logger.error(f"定时任务处理失败：{e}")
    
    def idle_time_task_handle(self, data):
        """
        闲时任务处理
        
        Args:
            data (dict): 数据
        """
        try:
            username = data.get("username", "系统")
            content = data.get("content", "")
            type = data.get("type", "text")
            
            logger.info(f"[闲时任务] {content}")
            
            if type == "text":
                # 构建消息
                message = {
                    "type": "idle_time_task",
                    "tts_type": self.my_handle.config.get("audio_synthesis_type"),
                    "data": self.my_handle.config.get(self.my_handle.config.get("audio_synthesis_type")),
                    "config": self.my_handle.config.get("filter"),
                    "username": username,
                    "content": content
                }
                
                # 音频合成
                self.my_handle.audio_synthesis_handle(message)
            elif type == "local_audio":
                logger.info(f'[闲时任务-音频] {data["file_path"]}')
                
                message = {
                    "type": "idle_time_task",
                    "tts_type": self.my_handle.config.get("audio_synthesis_type"),
                    "data": self.my_handle.config.get(self.my_handle.config.get("audio_synthesis_type")),
                    "config": self.my_handle.config.get("filter"),
                    "username": username,
                    "content": content,
                    "content_type": type,
                    "file_path": os.path.abspath(data["file_path"])
                }
                
                self.my_handle.audio_synthesis_handle(message)
        except Exception as e:
            logger.error(traceback.format_exc())
            logger.error(f"闲时任务处理失败：{e}")
    
    def image_recognition_schedule_handle(self, data):
        """
        图像识别定时任务处理
        
        Args:
            data (dict): 数据
        """
        try:
            username = data.get("username", "系统")
            content = self.my_handle.config.get("image_recognition", "prompt")
            type = data.get("type", "窗口截图")
            
            # 将用户名字符串中的数字转换成中文
            if self.my_handle.config.get("filter", "username_convert_digits_to_chinese"):
                username = self.my_handle.common.convert_digits_to_chinese(username)
            
            if type == "窗口截图":
                # 根据窗口名截图
                screenshot_path = self.my_handle.common.capture_window_by_title(self.my_handle.config.get("image_recognition", "img_save_path"), self.my_handle.config.get("image_recognition", "screenshot_window_title"))
            elif type == "摄像头截图":
                # 根据摄像头索引截图
                screenshot_path = self.my_handle.common.capture_image(self.my_handle.config.get("image_recognition", "img_save_path"), int(self.my_handle.config.get("image_recognition", "cam_index")))
            
            # 通用的data_json构造
            data_json = {
                "username": username,
                "content": content,
                "img_data": screenshot_path,
                "ori_username": data.get("username", username),
                "ori_content": content
            }
            
            # 调用LLM统一接口，获取返回内容
            resp_content = self.my_handle.llm_handle(self.my_handle.config.get("image_recognition", "model"), data_json, type="vision")
            
            if resp_content:
                logger.info(f"[AI回复{username}]：{resp_content}")
            else:
                logger.warning(f'警告：{self.my_handle.config.get("image_recognition", "model")}无返回')
                resp_content = ""
            
            """
            双重过滤，为您保驾护航
            """
            resp_content = resp_content.replace('\n', '。')
            
            # LLM回复的内容进行违禁判断
            resp_content = self.my_handle.prohibitions_handle(resp_content)
            if resp_content is None:
                return
            
            # logger.info("resp_content=" + resp_content)
            
            self.my_handle.write_to_comment_log(resp_content, {"username": username, "content": content})
            
            # 判断按键映射触发类型
            if self.my_handle.config.get("key_mapping", "type") == "回复" or self.my_handle.config.get("key_mapping", "type") == "弹幕+回复":
                # 替换内容
                data["content"] = resp_content
                # 按键映射 触发后不执行后面的其他功能
                if self.my_handle.key_mapping_handle("回复", data):
                    pass
            
            # 判断自定义命令触发类型
            if self.my_handle.config.get("custom_cmd", "type") == "回复" or self.my_handle.config.get("custom_cmd", "type") == "弹幕+回复":
                # 替换内容
                data["content"] = resp_content
                # 自定义命令 触发后不执行后面的其他功能
                if self.my_handle.custom_cmd_handle("回复", data):
                    pass
            
            # 音频合成时需要用到的重要数据
            message = {
                "type": "image_recognition_schedule",
                "tts_type": self.my_handle.config.get("audio_synthesis_type"),
                "data": self.my_handle.config.get(self.my_handle.config.get("audio_synthesis_type")),
                "config": self.my_handle.config.get("filter"),
                "username": username,
                "content": resp_content
            }
            
            self.my_handle.audio_synthesis_handle(message)
        except Exception as e:
            logger.error(traceback.format_exc())
            logger.error(f"图像识别定时任务处理失败：{e}")
    
    def abnormal_alarm_handle(self, type):
        """
        异常报警处理
        
        Args:
            type (str): 报警类型
            
        Returns:
            bool: True/False
        """
        try:
            self.my_handle.abnormal_alarm_data[type]["error_count"] += 1
            
            if not self.my_handle.config.get("abnormal_alarm", type, "enable"):
                return True
            
            if self.my_handle.config.get("abnormal_alarm", type, "type") == "local_audio":
                # 是否错误数大于 自动重启错误数
                if self.my_handle.abnormal_alarm_data[type]["error_count"] >= self.my_handle.config.get("abnormal_alarm", type, "auto_restart_error_num"):
                    data = {
                        "type": "restart",
                        "api_type": "api",
                        "data": {
                            "config_path": "config.json"
                        }
                    }
                    
                    webui_ip = "127.0.0.1" if self.my_handle.config.get("webui", "ip") == "0.0.0.0" else self.my_handle.config.get("webui", "ip")
                    self.my_handle.common.send_request(f'http://{webui_ip}:{self.my_handle.config.get("webui", "port")}/sys_cmd', "POST", data)
                
                # 是否错误数小于 开始报警错误数，是则不触发报警
                if self.my_handle.abnormal_alarm_data[type]["error_count"] < self.my_handle.config.get("abnormal_alarm", type, "start_alarm_error_num"):
                    return
                
                import random
                path_list = self.my_handle.common.get_all_file_paths(self.my_handle.config.get("abnormal_alarm", type, "local_audio_path"))
                
                # 随机选择列表中的一个元素
                audio_path = random.choice(path_list)
                
                message = {
                    "type": "abnormal_alarm",
                    "tts_type": self.my_handle.config.get("audio_synthesis_type"),
                    "data": self.my_handle.config.get(self.my_handle.config.get("audio_synthesis_type")),
                    "config": self.my_handle.config.get("filter"),
                    "username": "系统",
                    "content": os.path.join(self.my_handle.config.get("abnormal_alarm", type, "local_audio_path"), self.my_handle.common.extract_filename(audio_path, True))
                }
                
                logger.warning(f"【异常报警-{type}】 {self.my_handle.common.extract_filename(audio_path, False)}")
                
                self.my_handle.audio_synthesis_handle(message)
        except Exception as e:
            logger.error(traceback.format_exc())
            logger.error(f"异常报警处理失败：{e}")
            return False
        
        return True