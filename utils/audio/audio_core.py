"""
音频核心模块 - 整合所有音频子模块的主要类
"""

import re
import threading
import asyncio
from copy import deepcopy
import aiohttp
import os, random
import copy
import traceback

from pydub import AudioSegment

from ..common import Common
from ..my_log import logger
from ..config import Config
from utils.audio_handle.my_tts import MY_TTS
from utils.audio_handle.audio_player import AUDIO_PLAYER
from utils.edtalk_realtime import EDTalkClient, is_edtalk_active
from utils.edtalk_realtime.edtalk_client import register_client

# 导入子模块
from .queue_manager import QueueMixin
from .playback_manager import PlaybackMixin
from .synthesis_manager import SynthesisMixin
from .processing_manager import ProcessingMixin
from .utils_manager import UtilsMixin
from .alarm_manager import AlarmMixin


class Audio(QueueMixin, PlaybackMixin, SynthesisMixin, ProcessingMixin, UtilsMixin, AlarmMixin):
    """
    音频处理主类 - 通过Mixin模式组合各个功能模块
    """
    
    # 文案播放标志 0手动暂停 1临时暂停  2循环播放
    copywriting_play_flag = -1

    # pygame.mixer实例
    mixer_normal = None
    mixer_copywriting = None

    # 全局变量用于保存恢复文案播放计时器对象
    unpause_copywriting_play_timer = None

    audio_player = None

    # 消息列表，存储待合成音频的json数据
    message_queue = []
    message_queue_lock = threading.Lock()
    message_queue_not_empty = threading.Condition(lock=message_queue_lock)
    # 创建待播放音频路径队列
    voice_tmp_path_queue = []
    voice_tmp_path_queue_lock = threading.Lock()
    voice_tmp_path_queue_not_empty = threading.Condition(lock=voice_tmp_path_queue_lock)

    # 第一次触发voice_tmp_path_queue_not_empty标志
    voice_tmp_path_queue_not_empty_flag = False

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

    def __init__(self, config_path, type=1):
        self.config_path = config_path  
        self.config = Config(config_path)
        self.common = Common()
        self.my_tts = MY_TTS(config_path)

        # 文案模式
        if type == 2:
            logger.info("文案模式的Audio初始化...")
            return
    
        # 文案单独一个线程排队播放
        self.only_play_copywriting_thread = None

        if self.config.get("play_audio", "player") in ["pygame"]:
            import pygame

            # 初始化多个pygame.mixer实例
            Audio.mixer_normal = pygame.mixer
            Audio.mixer_copywriting = pygame.mixer

        # 旧版同步写法
        # threading.Thread(target=self.message_queue_thread).start()
        # 改异步
        threading.Thread(target=lambda: asyncio.run(self.message_queue_thread())).start()

        # 音频合成单独一个线程排队播放
        threading.Thread(target=lambda: asyncio.run(self.only_play_audio())).start()

        # 文案单独一个线程排队播放
        if self.only_play_copywriting_thread == None:
            self.only_play_copywriting_thread = threading.Thread(target=self.start_only_play_copywriting)
            self.only_play_copywriting_thread.start()

        Audio.audio_player =  AUDIO_PLAYER(self.config.get("audio_player"))

        # EDTalk 实时推理客户端（EDTalk功能集成计划 §4.3.1）：
        # 无论是否激活都创建（构造廉价），is_edtalk_active() 在使用点判定；
        # 注册到模块级 holder，供 main.py 运行时 /edtalk_status 健康度端点读取。
        self.edtalk_client = EDTalkClient(self.config.get("edtalk_realtime"))
        register_client(self.edtalk_client)
