# -*- coding: UTF-8 -*-
"""
音频处理器模块

处理所有音频相关的功能
"""

import os
import traceback
from ..error_handler import handle_error, safe_execute, AudioError, ErrorCode
from ..base import logger


class AudioHandler:
    """音频处理器"""
    
    def __init__(self, my_handle):
        """
        初始化音频处理器
        
        Args:
            my_handle: My_handle 实例
        """
        self.my_handle = my_handle
    
    def clear_queue(self, type: str = "message_queue"):
        """
        清空 待合成消息队列|待播放音频队列
        
        Args:
            type (str, optional): 队列类型. Defaults to "message_queue".
            
        Returns:
            bool: 清空结果
        """
        try:
            return self.my_handle.audio.clear_queue(type)
        except Exception as e:
            handle_error(e, {'module': 'audio_handler', 'function': 'clear_queue'})
            logger.error(f"清空{type}队列失败：{e}")
            return False
    
    def stop_audio(self, type: str = "pygame", mixer_normal: bool = True, mixer_copywriting: bool = True):
        """
        停止音频播放
        
        Args:
            type (str): 播放器类型
            mixer_normal (bool): 是否停止普通混音器
            mixer_copywriting (bool): 是否停止文案混音器
            
        Returns:
            bool: 停止结果
        """
        try:
            return self.my_handle.audio.stop_audio(type, mixer_normal, mixer_copywriting)
        except Exception as e:
            logger.error(traceback.format_exc())
            logger.error(f"停止音频播放失败：{e}")
            return False
    
    def audio_synthesis_handle(self, data_json):
        """
        音频合成处理
        
        Args:
            data_json (dict): 传递的json数据
            
            核心参数:
            type目前有
                reread_top_priority 最高优先级-复读
                talk 聊天（语音输入）
                comment 弹幕
                local_qa_text 本地问答文本
                local_qa_audio 本地问答音频
                song 歌曲
                reread 复读
                key_mapping 按键映射
                key_mapping_copywriting 按键映射-文案
                integral 积分
                read_comment 念弹幕
                gift 礼物
                entrance 用户入场
                follow 用户关注
                schedule 定时任务
                idle_time_task 闲时任务
                abnormal_alarm 异常报警
                image_recognition_schedule 图像识别定时任务
        """
        if "content" in data_json:
            if data_json['content']:
                # 替换文本内容中\n为空
                data_json['content'] = data_json['content'].replace('\n', '')

        # 如果虚拟身体-Unity，则发送数据到中转站
        if self.my_handle.config.get("visual_body") == "unity":
            # 判断 'config' 是否存在于字典中
            if 'config' in data_json:
                # 删除 'config' 对应的键值对
                data_json.pop('config')

            data_json["password"] = self.my_handle.config.get("unity", "password")

            resp_json = self.my_handle.common.send_request(self.my_handle.config.get("unity", "api_ip_port"), "POST", data_json)
            if resp_json:
                if resp_json["code"] == 200:
                    logger.info("请求unity中转站成功")
                else:
                    logger.info(f"请求unity中转站出错，{resp_json['message']}")
            else:
                logger.error("请求unity中转站失败")
        else:
            # 音频合成（edge-tts / vits_fast）并播放
            self.my_handle.audio.audio_synthesis(data_json)

            logger.debug(f'data_json={data_json}')
    
    def get_audio_info(self):
        """
        获取音频类信息
        
        Returns:
            dict: 音频信息
        """
        return self.my_handle.audio.get_audio_info()
    
    def is_audio_queue_empty(self):
        """
        判断音频队列是否为空
        
        Returns:
            bool: 是否为空
        """
        return self.my_handle.audio.is_audio_queue_empty()
    
    def is_queue_less_or_greater_than(self, type: str = "message_queue", less: int = None, greater: int = None):
        """
        判断 等待合成消息队列|待播放音频队列 数是否小于或大于某个值
        
        Args:
            type (str, optional): 队列类型. Defaults to "message_queue" | "voice_tmp_path_queue".
            less (int, optional): 小于值. Defaults to None.
            greater (int, optional): 大于值. Defaults to None.
            
        Returns:
            bool: 是否小于或大于某个值
        """
        return self.my_handle.audio.is_queue_less_or_greater_than(type, less, greater)