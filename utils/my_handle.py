# -*- coding: UTF-8 -*-
"""
My_handle 主类

重构后使用各个 handler 模块
"""

import os
import sys
import threading
import json
import random
import difflib
from datetime import datetime
import traceback
from .error_handler import handle_error, safe_execute
import importlib
import asyncio

import copy
import re
from functools import partial


from .config import Config
from .common import Common
from .audio import Audio
from .gpt_model.gpt import GPT_MODEL
from .base import logger
from .db import SQLiteDB
from .my_translate import My_Translate

from .luoxi_project.live_comment_assistant import send_msg_to_live_comment_assistant

# 导入 handler 模块
from .handlers.audio_handler import AudioHandler
from .handlers.chat_handler import ChatHandler
from .handlers.comment_handler import CommentHandler
from .handlers.timer_handler import TimerHandler
from .handlers.feature_handler import FeatureHandler
from .handlers.event_handler import EventHandler
from .handlers.utils_handler import UtilsHandler


"""
	___ _                       
	|_ _| | ____ _ _ __ ___  ___ 
	 | || |/ / _` | '__/ _ \/ __|
	 | ||   < (_| | | | (_) \__ \
	|___|_|\_\\__,_|_|  \___/|___/

"""
class SingletonMeta(type):
    _instances = {}
    _lock = threading.Lock()

    def __call__(cls, *args, **kwargs):
        with cls._lock:
            if cls not in cls._instances:
                cls._instances[cls] = super(SingletonMeta, cls).__call__(*args, **kwargs)
            return cls._instances[cls]


class My_handle(metaclass=SingletonMeta):
    common = None
    config = None
    audio = None
    my_translate = None
    
    # 是否在数据处理中
    is_handleing = 0

    # 异常报警数据
    abnormal_alarm_data = {
        "platform": {
            "error_count": 0
        },
        "llm": {
            "error_count": 0
        },
        "tts": {
            "error_count": 0
        },
        "svc": {
            "error_count": 0
        },
        "visual_body": {
            "error_count": 0
        },
        "other": {
            "error_count": 0
        }
    }

    # 直播消息存储(入场、礼物、弹幕)，用于限定时间内的去重
    live_data = {
        "comment": [],
        "gift": [],
        "entrance": [],
    }

    # 各个任务运行数据缓存 暂时用于 限定任务周期性触发
    task_data = {
        "read_comment": {
            "data": [],
            "time": 0
        },
        "local_qa": {
            "data": [],
            "time": 0
        },
        "thanks": {
            "gift": {
                "data": [],
                "time": 0
            },
            "entrance": {
                "data": [],
                "time": 0
            },
            "follow": {
                "data": [],
                "time": 0
            },
        }
    }

    # 答谢板块文案数据临时存储
    thanks_entrance_copy = []
    thanks_gift_copy = []
    thanks_follow_copy = []

    def __init__(self, config_path):
        logger.info("初始化My_handle...")

        try:
            if My_handle.common is None:
                My_handle.common = Common()
            if My_handle.config is None:
                My_handle.config = Config(config_path)
            if My_handle.audio is None:
                My_handle.audio = Audio(config_path)
            if My_handle.my_translate is None:
                My_handle.my_translate = My_Translate(config_path)

            self.proxy = None
            
            # 数据丢弃部分相关的实现
            self.data_lock = threading.Lock()
            self.timers = {}

            self.db = None

            # 设置会话初始值
            self.session_config = None
            self.sessions = {}
            self.current_key_index = 0

            # 点歌模块
            self.choose_song_song_lists = None

            self.custom_llm = None

            self.image_recognition_model = None

            self.chat_type_list = ["custom_llm"]

            # 初始化 handler 模块
            self.audio_handler = AudioHandler(self)
            self.chat_handler = ChatHandler(self)
            self.comment_handler = CommentHandler(self)
            self.timer_handler = TimerHandler(self)
            self.feature_handler = FeatureHandler(self)
            self.event_handler = EventHandler(self)
            self.utils_handler = UtilsHandler(self)

            # 配置加载
            self.config_load()

            logger.info(f"配置数据加载成功。")

            # 启动定时器
            self.start_timers()
        except Exception as e:
            handle_error(e, {'module': 'my_handle', 'function': 'config_load'})     

    # 配置加载
    def config_load(self):
        # 设置GPT_Model全局模型列表
        GPT_MODEL.set_model_config("custom_llm", My_handle.config.get("custom_llm"))

        # 聊天相关类实例化
        self.handle_chat_type()

        # 判断是否使能了SD
        if My_handle.config.get("sd")["enable"]:
            from utils.sd import SD
            self.sd = SD(My_handle.config.get("sd"))
        # 特殊：在SD没有使能情况下，判断图片映射是否使能
        elif My_handle.config.get("key_mapping", "img_path_trigger_type") != "不启用":
            # 沿用SD的虚拟摄像头来展示图片
            from utils.sd import SD
            self.sd = SD({"enable": False, "visual_camera": My_handle.config.get("sd", "visual_camera")})

        # 日志文件路径
        self.log_file_path = "./log/log-" + My_handle.common.get_bj_time(1) + ".txt"
        if os.path.isfile(self.log_file_path):
            logger.info(f'{self.log_file_path} 日志文件已存在，跳过')
        else:
            with open(self.log_file_path, 'w') as f:
                f.write('')
                logger.info(f'{self.log_file_path} 日志文件已创建')

        # 生成弹幕文件
        self.comment_file_path = "./log/comment-" + My_handle.common.get_bj_time(1) + ".txt"
        if os.path.isfile(self.comment_file_path):
            logger.info(f'{self.comment_file_path} 弹幕文件已存在，跳过')
        else:
            with open(self.comment_file_path, 'w') as f:
                f.write('')
                logger.info(f'{self.comment_file_path} 弹幕文件已创建')

        try:
            # 数据库
            self.db = SQLiteDB(My_handle.config.get("database", "path"))
            logger.info(f'创建数据库:{My_handle.config.get("database", "path")}')

            # 创建弹幕表
            create_table_sql = '''
            CREATE TABLE IF NOT EXISTS danmu (
                username TEXT NOT NULL,
                content TEXT NOT NULL,
                ts DATETIME NOT NULL
            )
            '''
            self.db.execute(create_table_sql)
            logger.debug('创建danmu（弹幕）表')

            create_table_sql = '''
            CREATE TABLE IF NOT EXISTS entrance (
                username TEXT NOT NULL,
                ts DATETIME NOT NULL
            )
            '''
            self.db.execute(create_table_sql)
            logger.debug('创建entrance（入场）表')

            create_table_sql = '''
            CREATE TABLE IF NOT EXISTS gift (
                username TEXT NOT NULL,
                gift_name TEXT NOT NULL,
                gift_num INT NOT NULL,
                unit_price REAL NOT NULL,
                total_price REAL NOT NULL,
                ts DATETIME NOT NULL
            )
            '''
            self.db.execute(create_table_sql)
            logger.debug('创建gift（礼物）表')

            create_table_sql = '''
            CREATE TABLE IF NOT EXISTS integral (
                platform TEXT NOT NULL,
                username TEXT NOT NULL,
                uid TEXT NOT NULL,
                integral INT NOT NULL,
                view_num INT NOT NULL,
                sign_num INT NOT NULL,
                last_sign_ts DATETIME NOT NULL,
                total_price INT NOT NULL,
                last_ts DATETIME NOT NULL
            )
            '''
            self.db.execute(create_table_sql)
            logger.debug('创建integral（积分）表')
        except Exception as e:
            logger.error(traceback.format_exc())
            logger.error(f'数据库 {My_handle.config.get("database", "path")} 创建失败，请查看日志排查问题！！！')


    # 重载config
    def reload_config(self, config_path):
        My_handle.config = Config(config_path)
        My_handle.audio.reload_config(config_path)
        My_handle.my_translate.reload_config(config_path)
        self.config_load()


    # 委托给 handler 模块的方法
    
    # 音频相关
    def clear_queue(self, type: str = "message_queue"):
        return self.audio_handler.clear_queue(type)
    
    def stop_audio(self, type: str = "pygame", mixer_normal: bool = True, mixer_copywriting: bool = True):
        return self.audio_handler.stop_audio(type, mixer_normal, mixer_copywriting)
    
    def audio_synthesis_handle(self, data_json):
        return self.audio_handler.audio_synthesis_handle(data_json)
    
    def get_audio_info(self):
        return self.audio_handler.get_audio_info()
    
    def is_audio_queue_empty(self):
        return self.audio_handler.is_audio_queue_empty()
    
    def is_queue_less_or_greater_than(self, type: str = "message_queue", less: int = None, greater: int = None):
        return self.audio_handler.is_queue_less_or_greater_than(type, less, greater)
    
    # 聊天相关
    def get_chat_model(self, chat_type, config):
        return self.chat_handler.get_chat_model(chat_type, config)
    
    def get_vision_model(self, chat_type, config):
        return self.chat_handler.get_vision_model(chat_type, config)
    
    def handle_chat_type(self):
        return self.chat_handler.handle_chat_type()
    
    def llm_handle(self, chat_type, data_json, type="chat"):
        return self.chat_handler.llm_handle(chat_type, data_json, type)
    
    def llm_stream_handle_and_audio_synthesis(self, chat_type, data_json, type="chat"):
        return self.chat_handler.llm_stream_handle_and_audio_synthesis(chat_type, data_json, type)
    
    def split_by_chinese_punctuation(self, text):
        return self.chat_handler.split_by_chinese_punctuation(text)
    
    def talk_handle(self, data):
        return self.chat_handler.talk_handle(data)
    
    # 弹幕相关
    def comment_check_and_replace(self, content):
        return self.comment_handler.comment_check_and_replace(content)
    
    def prohibitions_handle(self, content):
        return self.comment_handler.prohibitions_handle(content)
    
    def reread_handle(self, data):
        return self.comment_handler.reread_handle(data)
    
    def tuning_handle(self, data):
        return self.comment_handler.tuning_handle(data)
    
    def write_to_comment_log(self, content, data):
        return self.comment_handler.write_to_comment_log(content, data)
    
    def comment_handle(self, data):
        return self.comment_handler.comment_handle(data)
    
    # 定时器相关
    def periodic_trigger_data_handle(self):
        return self.timer_handler.periodic_trigger_data_handle()
    
    def start_timers(self):
        return self.timer_handler.start_timers()
    
    def clear_live_data(self, type: str = ""):
        return self.timer_handler.clear_live_data(type)
    
    def process_data(self, data, timer_flag):
        return self.timer_handler.process_data(data, timer_flag)
    
    def process_last_data(self, timer_flag):
        return self.timer_handler.process_last_data(timer_flag)
    
    def get_interval(self, timer_flag):
        return self.timer_handler.get_interval(timer_flag)
    
    # 功能相关
    def find_answer(self, question, file_path, similarity=0.6):
        return self.feature_handler.find_answer(question, file_path, similarity)
    
    def find_similar_answer(self, question, file_path, similarity=0.6):
        return self.feature_handler.find_similar_answer(question, file_path, similarity)
    
    def load_data_from_file(self, file_path):
        return self.feature_handler.load_data_from_file(file_path)
    
    def local_qa_handle(self, data):
        return self.feature_handler.local_qa_handle(data)
    
    def choose_song_handle(self, data):
        return self.feature_handler.choose_song_handle(data)
    
    def sd_handle(self, data):
        return self.feature_handler.sd_handle(data)
    
    def integral_handle(self, type, data):
        return self.feature_handler.integral_handle(type, data)
    
    def key_mapping_handle(self, type, data):
        return self.feature_handler.key_mapping_handle(type, data)
    
    def custom_cmd_handle(self, type, data):
        return self.feature_handler.custom_cmd_handle(type, data)
    
    def blacklist_handle(self, data):
        return self.feature_handler.blacklist_handle(data)
    
    def search_online_handle(self, data):
        return self.feature_handler.search_online_handle(data)
    
    # 事件相关
    def gift_handle(self, data):
        return self.event_handler.gift_handle(data)
    
    def entrance_handle(self, data):
        return self.event_handler.entrance_handle(data)
    
    def follow_handle(self, data):
        return self.event_handler.follow_handle(data)
    
    def schedule_handle(self, data):
        return self.event_handler.schedule_handle(data)
    
    def idle_time_task_handle(self, data):
        return self.event_handler.idle_time_task_handle(data)
    
    def image_recognition_schedule_handle(self, data):
        return self.event_handler.image_recognition_schedule_handle(data)
    
    def abnormal_alarm_handle(self, type):
        return self.event_handler.abnormal_alarm_handle(type)
    
    # 工具相关
    def webui_show_chat_log_callback(self, data_type: str, data: dict, resp_content: str):
        return self.utils_handler.webui_show_chat_log_callback(data_type, data, resp_content)
    
    def get_room_id(self):
        return self.utils_handler.get_room_id()
    
    def get_copywriting_and_audio_synthesis(self, type, data):
        return self.utils_handler.get_copywriting_and_audio_synthesis(type, data)
    
    def get_a_copywriting_and_audio_synthesis(self, type, data):
        return self.utils_handler.get_a_copywriting_and_audio_synthesis(type, data)
    
    def get_a_local_audio_and_audio_play(self, type, data):
        return self.utils_handler.get_a_local_audio_and_audio_play(type, data)
    
    def get_a_serial_send_data_and_send(self, type, data):
        return self.utils_handler.get_a_serial_send_data_and_send(type, data)
    
    def connect_serial_and_send_data(self, data):
        return self.utils_handler.connect_serial_and_send_data(data)
    
    def get_serial_config(self):
        return self.utils_handler.get_serial_config()
    
    def get_a_img_path_and_send(self, type, data):
        return self.utils_handler.get_a_img_path_and_send(type, data)
    
    def keyword_handle_trigger(self, type, data):
        return self.utils_handler.keyword_handle_trigger(type, data)
    
    def gift_handle_trigger(self, type, data):
        return self.utils_handler.gift_handle_trigger(type, data)
    
    def is_data_repeat_in_limited_time(self, data, type):
        return self.utils_handler.is_data_repeat_in_limited_time(data, type)