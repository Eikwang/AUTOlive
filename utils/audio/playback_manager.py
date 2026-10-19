"""
音频播放管理模块 - 管理音频播放、文案播放等功能
"""

import asyncio
import copy
import random
import threading
import traceback
import os
from ..my_log import logger
from ..edtalk_realtime import is_edtalk_active
from utils.web_captions import get_captions_manager


class PlaybackMixin:
    """音频播放管理Mixin类"""
    
    def stop_audio(self, type: str="pygame", mixer_normal: bool=True, mixer_copywriting: bool=True):
        """停止音频播放"""
        try:
            if type == "pygame":
                if mixer_normal:
                    self.mixer_normal.music.stop()
                    logger.info("停止普通音频播放")
                if mixer_copywriting:
                    self.mixer_copywriting.music.stop()
                    logger.info("停止文案音频播放")
                return True
        except Exception as e:
            logger.error(traceback.format_exc())
            logger.error(f"停止音频播放失败：{e}")
            return False

    async def only_play_audio(self):
        """只进行普通音频播放"""
        try:
            captions_config = self.config.get("captions")

            try:
                if self.config.get("play_audio", "player") in ["pygame"]:
                    self.mixer_normal.init()
            except Exception as e:
                logger.error(traceback.format_exc())
                logger.error("pygame mixer_normal初始化失败，普通音频将无法正常播放，请检查声卡是否正常！")

            while True:
                try:
                    # 获取线程锁，避免同时操作
                    with self.voice_tmp_path_queue_lock:
                        while not self.voice_tmp_path_queue:
                            # 消费者在消费完一个消息后，如果列表为空，则调用wait()方法阻塞自己，直到有新消息到来
                            self.voice_tmp_path_queue_not_empty.wait()  # 阻塞直到列表非空
                        data_json = self.voice_tmp_path_queue.pop(0)
                    
                    logger.debug(f"普通音频播放队列 即将播放音频 data_json={data_json}")

                    voice_tmp_path = data_json["voice_path"]

                    # 是否启用字幕输出
                    if captions_config["enable"]:
                        # 输出当前播放的音频文件的文本内容到字幕文件中
                        self.common.write_content_to_file(captions_config["file_path"], data_json["content"], write_log=False)


                    # 判断是否发送web字幕打印机
                    # 内置字幕模块进程内直调（修复原断链：send_to_web_captions_printer
                    # 方法不存在，enable=true 即 AttributeError）；enable/样式由
                    # CaptionsManager 热读，push 内部全捕获（S4 隔离，不阻塞播放）
                    _captions_manager = get_captions_manager()
                    if _captions_manager is not None:
                        await _captions_manager.push(data_json)

                    normal_interval_min = self.config.get("play_audio", "normal_interval_min")
                    normal_interval_max = self.config.get("play_audio", "normal_interval_max")
                    normal_interval = self.common.get_random_value(normal_interval_min, normal_interval_max)

                    interval_num_min = float(self.config.get("play_audio", "interval_num_min"))
                    interval_num_max = float(self.config.get("play_audio", "interval_num_max"))
                    interval_num = int(self.common.get_random_value(interval_num_min, interval_num_max))

                    for i in range(interval_num):
                        # 不仅仅是说话间隔，还是等待文本捕获刷新数据
                        await asyncio.sleep(normal_interval)

                    # 音频变速
                    # P26 门禁：变速迁移双路径并存——合成侧已应用语速（synth_speed 标记，
                    # ≠1.0）时跳过 pydub 变速避免双重变速；pydub speedup 为回退路径，
                    # 仅对无 speed 参数的旧路径生效
                    # （P26 spike：inproc speed_factor 生效性需 GPU 环境启动 inproc 实测；
                    # 实测失败时此 pydub 路径过渡一个批次后按迁移开关退役）
                    _synth_speed = data_json.get("synth_speed")
                    random_speed = 1
                    if _synth_speed is not None and float(_synth_speed) != 1.0:
                        logger.debug(f"合成侧已应用语速 synth_speed={_synth_speed}，跳过 pydub 随机变速")
                    elif self.config.get("audio_random_speed", "normal", "enable"):
                        random_speed = self.common.get_random_value(self.config.get("audio_random_speed", "normal", "speed_min"),
                                                                    self.config.get("audio_random_speed", "normal", "speed_max"))
                        voice_tmp_path = self.audio_speed_change(voice_tmp_path, random_speed)

                    # print(voice_tmp_path)

                    # 根据接入的虚拟身体类型执行不同逻辑（仅保留metahuman_stream在合成前处理）
                    # EDTalk 实时推理模式（EDTalk功能集成计划 §4.3.3）：跳过本地播放——
                    # EDTalk 端 sounddevice 帧锁定播放（本地播放会双声），TTS 落盘
                    # （含变速）后仅推送 /audio/push_full。推送协程内阻塞等待完成
                    # 判定（背压），失败降级记录不阻塞直播主流程。
                    # 根据播放器类型进行区分
                    if is_edtalk_active(self.config):
                        await self.edtalk_client.push_full(
                            voice_tmp_path, content=data_json.get("content", "")
                        )
                    elif self.config.get("play_audio", "player") in ["audio_player", "audio_player_v2", "builtin"]:
                        if "insert_index" in data_json:
                            data_json = {
                                "type": data_json["type"],
                                "voice_path": voice_tmp_path,
                                "content": data_json["content"],
                                "random_speed": {
                                    "enable": False,
                                    "max": 1.3,
                                    "min": 0.8
                                },
                                "speed": 1,
                                "insert_index": data_json["insert_index"]
                            }
                        else:
                            data_json = {
                                "type": data_json["type"],
                                "voice_path": voice_tmp_path,
                                "content": data_json["content"],
                                "random_speed": {
                                    "enable": False,
                                    "max": 1.3,
                                    "min": 0.8
                                },
                                "speed": 1
                            }
                        self.audio_player.play(data_json)
                    else:
                        logger.debug(f"voice_tmp_path={voice_tmp_path}")
                        import pygame

                        try:
                            # 使用pygame播放音频
                            self.mixer_normal.music.load(voice_tmp_path)
                            self.mixer_normal.music.play()
                            while self.mixer_normal.music.get_busy():
                                pygame.time.Clock().tick(10)
                            self.mixer_normal.music.stop()
                            
                            await self.send_audio_play_info_to_callback()
                        except pygame.error as e:
                            logger.error(traceback.format_exc())
                            # 如果发生 pygame.error 异常，则捕获并处理它
                            logger.error(f"无法加载音频文件:{voice_tmp_path}。请确保文件格式正确且文件未损坏。可能原因是TTS配置有误或者TTS服务端有问题，可以去服务端排查一下问题")

                    # 是否启用字幕输出
                    #if captions_config["enable"]:
                        # 清空字幕文件
                        # self.common.write_content_to_file(captions_config["file_path"], "")

                except Exception as e:
                    logger.error(traceback.format_exc())
            self.mixer_normal.quit()
        except Exception as e:
            logger.error(traceback.format_exc())


    def stop_current_audio(self):
        """停止当前播放的音频

        注意（EDTalk 模式语义变化，native E6）：edtalk_realtime 激活时本地播放
        已被跳过，弹幕打断只作用于本地 mixer/audio_player；EDTalk 端无
        flush-audio 契约端点，打断不会停止其音频播放。已列入 edtalk/README
        语义变化清单。
        """
        if is_edtalk_active(self.config):
            # EDTalk 契约无音频 flush 端点——静默跳过（保持既有调用方兼容）
            return
        if self.config.get("play_audio", "player") in ["audio_player", "audio_player_v2", "builtin"]:
            self.audio_player.skip_current_stream()
        else:
            self.mixer_normal.music.fadeout(1000)
