"""
音频核心模块 - 整合所有音频子模块的主要类
"""

import re
import threading
import asyncio
import time
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
from utils.audio import builtin_play_center as builtin_play_center_mod
from utils.audio.builtin_play_center import AUDIO_PLAY_CENTER as BUILTIN_PLAY_CENTER
from utils.web_captions import register_captions_manager
from utils.web_captions.captions_manager import CaptionsManager
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

        # 播放后端分流（整合计划 §4.2）：builtin=内置播放器（进程内队列），
        # 其余=既有 AUDIO_PLAYER HTTP 客户端（外部服务模式，兼容保留）。
        # 调用点（playback_manager 两处 play/stop_current_audio）零逻辑改动。
        if self.config.get("play_audio", "player") == "builtin":
            Audio.audio_player = BUILTIN_PLAY_CENTER(
                self.config.get("audio_player") or {}, config_path=self.config_path)
            # 完成回调（play_audio.info_to_callback 语义保持；方法内部自检开关，
            # 经播放器专用 loop 线程提交——builtin 播放在独立线程）
            Audio.audio_player.set_completion_callback(
                lambda data: self.send_audio_play_info_to_callback(data))
            builtin_play_center_mod.register_builtin_player(Audio.audio_player)
        else:
            Audio.audio_player = AUDIO_PLAYER(self.config.get("audio_player"))

        # 内置字幕打印机（修复 playback_manager.py:74-75 断链：原
        # send_to_web_captions_printer 方法不存在，enable=true 即 AttributeError）。
        # 进程内直调；sio/loop 由 web_server 侧登记桥接（顺序无关）。
        self.captions_manager = CaptionsManager(self.config, config_path=self.config_path)
        register_captions_manager(self.captions_manager)
        self.captions_manager.set_paused_getter(self._builtin_player_paused)
        self.captions_manager.set_error_notifier(self._captions_error_to_player)

    def _builtin_player_paused(self):
        """字幕节流用：builtin 播放器是否暂停中（eng E-4；非 builtin 返回 False）"""
        player = Audio.audio_player
        try:
            if player is not None and hasattr(player, "get_status"):
                return bool(player.get_status().get("paused"))
        except Exception:
            pass
        return False

    def _captions_error_to_player(self, message):
        """字幕推送失败联动（dx 发现1）：写入 builtin 播放器 last_error，
        经 /builtin_status 浮出到控制区错误条"""
        player = Audio.audio_player
        try:
            if player is not None and hasattr(player, "last_error"):
                player._error_seq += 1
                player.last_error = {"seq": player._error_seq, "message": str(message), "ts": time.time()}
        except Exception:
            pass

        # EDTalk 实时推理客户端（EDTalk功能集成计划 §4.3.1）：
        # 无论是否激活都创建（构造廉价），is_edtalk_active() 在使用点判定；
        # 注册到模块级 holder，供 main.py 运行时 /edtalk_status 健康度端点读取。
        self.edtalk_client = EDTalkClient(self.config.get("edtalk_realtime"))
        register_client(self.edtalk_client)
