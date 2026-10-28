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
from .utils_manager import UtilsMixin


class Audio(QueueMixin, PlaybackMixin, SynthesisMixin, UtilsMixin):
    """
    音频处理主类 - 通过Mixin模式组合各个功能模块
    """
    
    # pygame.mixer实例
    mixer_normal = None

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

    # A7/T1（2026-10-22 审查批次）：999 权重音频"在系统中"标记（播放器持有或播放中）
    active_reread_top_999 = False

    def __init__(self, config_path, type=1):
        self.config_path = config_path  
        self.config = Config(config_path)
        self.common = Common()
        self.my_tts = MY_TTS(config_path)

        # webui 前端进程轻量初始化（type=2）：仅读取配置后提前返回，
        # 不启动播放循环/播放器——双进程拓扑关键机制（完整初始化仅发生在
        # 主系统进程 my_handle），specs/integration 整合计划有载。
        # （原「文案模式」分支：copywriting 模块删除后仅保留轻量初始化语义）
        if type == 2:
            logger.info("轻量模式Audio初始化（webui进程）...")
            return

        if self.config.get("play_audio", "player") in ["pygame"]:
            import pygame

            # 初始化多个pygame.mixer实例
            Audio.mixer_normal = pygame.mixer

        # A5/F-4/F-6（2026-10-22 审查批次）：双优先级映射一次性合并迁移（禁纯静默）——
        # audio_player.priority_mapping（退役节）中的独有键（如 thanks）与冲突键
        # 迁入 filter.priority_mapping，冲突时 audio_player 值胜（R6(b)）。迁移内存
        # 即时生效（经 priority provider 供播放器消费），持久化随「互动设置-优先级
        # 配置」页下次保存落盘；启动告警列出全部迁移键。
        self._merge_legacy_priority_mapping()
        # ② 点歌切片插播（2026-10-27 架构优化批次）：存量 song=20 一次性降为 10
        # （config.example 新默认，显式存量值不自动生效——点歌需低于 comment=20
        # 才能被回复音频插播）。仅精确命中旧默认 20 时迁移，用户自定义值尊重不
        # 动；内存即时生效，落盘随优先级配置页下次保存。幂等：已是 10 不再触发。
        self._migrate_song_priority_v2_4()

        # 旧版同步写法
        # threading.Thread(target=self.message_queue_thread).start()
        # 改异步
        threading.Thread(target=lambda: asyncio.run(self.message_queue_thread())).start()

        # 音频合成单独一个线程排队播放
        threading.Thread(target=lambda: asyncio.run(self.only_play_audio())).start()

        # 播放后端分流（整合计划 §4.2）：builtin=内置播放器（进程内队列），
        # 其余=既有 AUDIO_PLAYER HTTP 客户端（外部服务模式，兼容保留）。
        # 调用点（playback_manager 两处 play/stop_current_audio）零逻辑改动。
        if self.config.get("play_audio", "player") == "builtin":
            Audio.audio_player = BUILTIN_PLAY_CENTER(
                self.config.get("audio_player") or {}, config_path=self.config_path)
            # A3（2026-10-22 审查批次）：完成回调注册随 play_audio.info_to_callback
            # 参数退役移除（回路死链；_fire_completion 对 None 回调自检跳过）。
            # A7/T1：999 离开播放系统时清除 active 标记（完成/跳过/清空/停止均触发，
            # 保证 999 单实例约束在条目释放后恢复放行）
            Audio.audio_player.set_999_release_hook(
                lambda: setattr(self, "active_reread_top_999", False))
            # A5：权威优先级表读取器（Config 内存视图——合并迁移即时可见，不等落盘）
            Audio.audio_player.set_priority_provider(
                lambda: self.config.get("filter", "priority_mapping") or {})
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
        # F3（义务 Eng T1：独立注册点，不复用 set_completion_callback——其载荷固定
        # 且被 A7 999 续播复用）：builtin 开播时推送字幕，时点=实际播放时点，
        # 载荷 content/type/voice_path 三键。仅 builtin 播放器有此回调；EDTalk/
        # pygame/外部模式字幕走 playback_manager 公共段推送（F2 门控）。
        if self.config.get("play_audio", "player") == "builtin" and Audio.audio_player is not None and hasattr(Audio.audio_player, "set_started_callback"):
            Audio.audio_player.set_started_callback(
                lambda data: self.captions_manager.push(data))
            # G1（2026-10-28 消费回传补齐，A3 死链清理后的重新注册）：builtin 完成
            # 回调转发编排 on_audio_complete——外放/助播场景编排步推进由真实消费
            # 完成驱动（替代 F1 看门狗 60s 超时兜底）。载荷 item_type/item_content
            # 由 _fire_completion 携带被播条目 (type,content)，编排侧 (type,content)+
            # 晚于入队时间匹配 inflight（CL-2），不匹配即忽略——999 续播/积压乱序安全。
            async def _choreo_completion_cb(data):
                try:
                    _item = (data or {}).get("data") or {}
                    _type, _content = _item.get("item_type", ""), _item.get("item_content", "")
                    from utils.orchestration import get_choreographer
                    _choreo = get_choreographer()
                    if _choreo is not None:
                        _choreo.on_audio_complete(_type, _content)
                    self.report_consumed(_type, _content)  # G2：Sort 侧消费回执（完成点②）
                except Exception:
                    pass  # 编排异常不回传播放器线程（沿 playback_manager 隔离惯例）

            Audio.audio_player.set_completion_callback(_choreo_completion_cb)

        # EDTalk 实时推理客户端（EDTalk功能集成计划 §4.3.1）：
        # 无论是否激活都创建（构造廉价），is_edtalk_active() 在使用点判定；
        # 注册到模块级 holder，供 main.py 运行时 /edtalk_status 健康度端点读取。
        # （2026-10-20 实测修复：原两行被缩进进 _captions_error_to_player 尾部，
        # 仅字幕错误回调触发时才创建——正常启动 self.edtalk_client 恒缺失，
        # playback_manager.only_play_audio 每条音频 AttributeError）
        self.edtalk_client = EDTalkClient(self.config.get("edtalk_realtime"))
        register_client(self.edtalk_client)

    def _merge_legacy_priority_mapping(self):
        """A5/F-4/F-6：audio_player.priority_mapping → filter.priority_mapping 一次性
        合并（独有键迁入、冲突 audio_player 值胜），启动告警禁纯静默。"""
        try:
            filter_map = self.config.get("filter", "priority_mapping")
            legacy_map = self.config.get("audio_player", "priority_mapping")
            if not isinstance(legacy_map, dict) or not legacy_map:
                return
            if not isinstance(filter_map, dict):
                filter_map = {}
            merged = {}
            for key, value in legacy_map.items():
                if key not in filter_map:
                    filter_map[key] = value
                    merged[key] = f"独有键迁入（={value}）"
                elif filter_map[key] != value:
                    merged[key] = f"冲突 {filter_map[key]} → {value}（audio_player 值胜）"
                    filter_map[key] = value
            if merged:
                base = self.config.config.get("filter")
                if not isinstance(base, dict):
                    base = {}
                    self.config.config["filter"] = base
                base["priority_mapping"] = filter_map
                detail = "；".join(f"{k}: {v}" for k, v in merged.items())
                logger.warning(
                    f"[优先级单源迁移] audio_player.priority_mapping → filter.priority_mapping "
                    f"共 {len(merged)} 键：{detail}（内存即时生效；持久化随「互动设置-优先级配置」页下次保存）")
        except Exception:
            logger.error("优先级单源迁移失败（沿用 filter 表）\n" + traceback.format_exc())

    def _migrate_song_priority_v2_4(self):
        """② 存量 song 优先级一次性迁移（2026-10-27）：20 → 10（切片插播前提：
        点歌需低于 comment=20 才能被回复音频插播）。仅精确命中旧默认 20 时迁移，
        用户自定义值尊重不动；内存即时生效，落盘随优先级配置页下次保存。"""
        try:
            base = self.config.config.get("filter")
            if not isinstance(base, dict):
                return
            pm = base.get("priority_mapping")
            if not isinstance(pm, dict) or pm.get("song") != 20:
                return
            pm["song"] = 10
            logger.warning(
                "[架构优化迁移] filter.priority_mapping.song: 20 → 10"
                "（点歌切片插播前提——点歌需低于评论优先级才能被回复音频插播；"
                "内存即时生效，落盘随「互动设置-优先级配置」页下次保存；"
                "如需恢复整曲独占播放可改回 20 并将 play_audio.song_segment_seconds 置 0）")
        except Exception:
            logger.error("song 优先级迁移失败（沿用现值）\n" + traceback.format_exc())

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
