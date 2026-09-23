"""
音频播放管理模块 - 管理音频播放、文案播放等功能
"""

import asyncio
import threading
import traceback
import os
from ..my_log import logger
from ..edtalk_realtime import is_edtalk_active


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

                    # 如果文案标志位为2，则说明在播放中，需要暂停
                    if self.copywriting_play_flag == 2:
                        logger.debug("暂停文案播放，等待一个切换间隔")
                        # 文案暂停
                        self.pause_copywriting_play()
                        self.copywriting_play_flag = 1
                        # 等待一个切换时间
                        await asyncio.sleep(float(self.config.get("copywriting", "switching_interval")))
                        logger.debug(f"切换间隔结束，准备播放普通音频")

                    # 是否启用字幕输出
                    if captions_config["enable"]:
                        # 输出当前播放的音频文件的文本内容到字幕文件中
                        self.common.write_content_to_file(captions_config["file_path"], data_json["content"], write_log=False)


                    # 判断是否发送web字幕打印机
                    if self.config.get("web_captions_printer", "enable"):
                        await self.common.send_to_web_captions_printer(self.config.get("web_captions_printer", "api_ip_port"), data_json)

                    # 洛曦 直播弹幕助手
                    if self.config.get("luoxi_project", "Live_Comment_Assistant", "enable") and                         "音频播放时" in self.config.get("luoxi_project", "Live_Comment_Assistant", "trigger_position"):
                        from utils.luoxi_project.live_comment_assistant import send_msg_to_live_comment_assistant

                        # 将音频消息类型type 转换为 判断用的新type
                        type_mapping = {
                            "comment": "comment_reply",
                            "idle_time_task": "idle_time_task",
                            "entrance": "entrance_reply",
                            "follow": "follow_reply",
                            "gift": "gift_reply",
                            "reread": "reread",
                            "schedule": "schedule",
                        }  

                        if data_json["type"] in type_mapping:
                            tmp_type = type_mapping[data_json["type"]]
                            # 当前消息类型是使能的触发类型
                            if tmp_type in self.config.get("luoxi_project", "Live_Comment_Assistant", "type"):
                                await send_msg_to_live_comment_assistant(self.config.get("luoxi_project", "Live_Comment_Assistant"), data_json["content"])


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
                    random_speed = 1
                    if self.config.get("audio_random_speed", "normal", "enable"):
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
                    elif self.config.get("play_audio", "player") in ["audio_player", "audio_player_v2"]:
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

                    if self.copywriting_play_flag == 1:
                        # 延时执行恢复文案播放
                        self.delayed_execution_unpause_copywriting_play()
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
        if self.config.get("play_audio", "player") == "audio_player":
            self.audio_player.skip_current_stream()
        else:
            self.mixer_normal.music.fadeout(1000)

    def delayed_execution_unpause_copywriting_play(self):
        """延时执行恢复文案播放"""
        # 如果已经有计时器在运行，则取消之前的计时器
        if self.unpause_copywriting_play_timer is not None and self.unpause_copywriting_play_timer.is_alive():
            self.unpause_copywriting_play_timer.cancel()

        # 创建新的计时器并启动
        self.unpause_copywriting_play_timer = threading.Timer(float(self.config.get("copywriting", "switching_interval")), 
                                                               self.unpause_copywriting_play)
        self.unpause_copywriting_play_timer.start()


    def start_only_play_copywriting(self):
        """只进行文案播放 正经版"""
        logger.info(f"文案播放线程运行中...")
        asyncio.run(self.only_play_copywriting())


    async def only_play_copywriting(self):
        """只进行文案播放"""
        
        try:
            try:
                if self.config.get("play_audio", "player") in ["pygame"]:
                    self.mixer_copywriting.init()
            except Exception as e:
                logger.error(traceback.format_exc())
                logger.error("pygame mixer_copywriting初始化失败，文案音频将无法正常播放，请检查声卡是否正常！")

            async def random_speed_and_play(audio_path):
                """对音频进行变速和播放，内置延时，其实就是提取了公共部分

                Args:
                    audio_path (str): 音频路径
                """
                # 音频变速
                random_speed = 1
                if self.config.get("audio_random_speed", "copywriting", "enable"):
                    random_speed = self.common.get_random_value(self.config.get("audio_random_speed", "copywriting", "speed_min"),
                                                                self.config.get("audio_random_speed", "copywriting", "speed_max"))
                    audio_path = self.audio_speed_change(audio_path, random_speed)

                logger.info(f"变速后音频输出在 {audio_path}")

                # EDTalk 实时推理模式：跳过本地播放，推送变速后文件（native E6：
                # 变速发生在播放旁路，推送必须使用变速后的产物）
                if is_edtalk_active(self.config):
                    await self.edtalk_client.push_full(audio_path, content="文案播放")
                elif self.config.get("play_audio", "player") in ["audio_player", "audio_player_v2"]:
                    data_json = {
                        "type": "copywriting",
                        "voice_path": audio_path,
                        "content": audio_path,
                        "random_speed": {
                            "enable": False,
                            "max": 1.3,
                            "min": 0.8
                        },
                        "speed": 1
                    }
                    self.audio_player.play(data_json)
                else:
                    import pygame

                    try:
                        # 使用pygame播放音频
                        self.mixer_copywriting.music.load(audio_path)
                        self.mixer_copywriting.music.play()
                        while self.mixer_copywriting.music.get_busy():
                            pygame.time.Clock().tick(10)
                        self.mixer_copywriting.music.stop()

                        await self.send_audio_play_info_to_callback()
                    except pygame.error as e:
                        logger.error(traceback.format_exc())


                # 添加延时，暂停执行n秒钟
                await asyncio.sleep(float(self.config.get("copywriting", "audio_interval")))


            def reload_tmp_play_list(index, play_list_arr):
                """重载播放列表

                Args:
                    index (int): 文案索引
                """
                # 获取文案配置
                copywriting_configs = self.config.get("copywriting", "config")
                tmp_play_list = copy.copy(copywriting_configs[index]["play_list"])
                play_list_arr[index] = tmp_play_list

                # 是否开启随机列表播放
                if self.config.get("copywriting", "random_play"):
                    for play_list in play_list_arr:
                        # 随机打乱列表内容
                        random.shuffle(play_list)


            try:
                # 获取文案配置
                copywriting_configs = self.config.get("copywriting", "config")

                # 获取自动播放配置
                if self.config.get("copywriting", "auto_play"):
                    self.unpause_copywriting_play()

                file_path_arr = []
                audio_path_arr = []
                play_list_arr = []
                continuous_play_num_arr = []
                max_play_time_arr = []
                # 记录最后一次播放的音频列表的索引值
                last_index = -1

                # 重载所有数据
                def all_data_reload(file_path_arr, audio_path_arr, play_list_arr, continuous_play_num_arr, max_play_time_arr):      
                    logger.info("重载所有文案数据")

                    file_path_arr = []
                    audio_path_arr = []
                    play_list_arr = []
                    continuous_play_num_arr = []
                    max_play_time_arr = []
                    
                    # 遍历文案配置载入数组
                    for copywriting_config in copywriting_configs:
                        file_path_arr.append(copywriting_config["file_path"])
                        audio_path_arr.append(copywriting_config["audio_path"])
                        tmp_play_list = copy.copy(copywriting_config["play_list"])
                        play_list_arr.append(tmp_play_list)
                        continuous_play_num_arr.append(copywriting_config["continuous_play_num"])
                        max_play_time_arr.append(copywriting_config["max_play_time"])


                    # 是否开启随机列表播放
                    if self.config.get("copywriting", "random_play"):
                        for play_list in play_list_arr:
                            # 随机打乱列表内容
                            random.shuffle(play_list)

                    return file_path_arr, audio_path_arr, play_list_arr, continuous_play_num_arr, max_play_time_arr

                file_path_arr, audio_path_arr, play_list_arr, continuous_play_num_arr, max_play_time_arr = all_data_reload(file_path_arr, audio_path_arr, play_list_arr, continuous_play_num_arr, max_play_time_arr)

                while True:
                    # print(f"Audio.copywriting_play_flag={Audio.copywriting_play_flag}")

                    # 判断播放标志位
                    if self.copywriting_play_flag in [0, 1, -1]:
                        await asyncio.sleep(float(self.config.get("copywriting", "audio_interval")))  # 添加延迟减少循环频率
                        continue

                    # print(f"play_list_arr={play_list_arr}")

                    # 遍历 play_list_arr 中的每个 play_list
                    for index, play_list in enumerate(play_list_arr):
                        # print(f"play_list_arr={play_list_arr}")

                        # 判断播放标志位 防止播放过程中无法暂停
                        if self.copywriting_play_flag in [0, 1, -1]:
                            # print(f"Audio.copywriting_play_flag={Audio.copywriting_play_flag}")
                            file_path_arr, audio_path_arr, play_list_arr, continuous_play_num_arr, max_play_time_arr = all_data_reload(file_path_arr, audio_path_arr, play_list_arr, continuous_play_num_arr, max_play_time_arr)

                            break

                        # 判断当前播放列表的索引值是否小于上一次的索引值，小的话就下一个，用于恢复到被打断前的播放位置
                        if index < last_index:
                            continue

                        start_time = float(self.common.get_bj_time(3))

                        # 根据连续播放的文案数量进行循环
                        for i in range(0, continuous_play_num_arr[index]):
                            # print(f"continuous_play_num_arr[index]={continuous_play_num_arr[index]}")
                            # 判断播放标志位 防止播放过程中无法暂停
                            if self.copywriting_play_flag in [0, 1, -1]:
                                file_path_arr, audio_path_arr, play_list_arr, continuous_play_num_arr, max_play_time_arr = all_data_reload(file_path_arr, audio_path_arr, play_list_arr, continuous_play_num_arr, max_play_time_arr)

                                break
                            
                            # 判断当前时间是否已经超过限定的播放时间，超时则退出循环
                            if (float(self.common.get_bj_time(3)) - start_time) > max_play_time_arr[index]:
                                break

                            # 判断当前 play_list 是否有音频数据
                            if len(play_list) > 0:
                                # 移出一个音频路径
                                voice_tmp_path = play_list.pop(0)
                                audio_path = os.path.join(audio_path_arr[index], voice_tmp_path)
                                audio_path = os.path.abspath(audio_path)
                                logger.info(f"即将播放音频 {audio_path}")

                                await random_speed_and_play(audio_path)
                            else:
                                # 重载播放列表
                                reload_tmp_play_list(index, play_list_arr)

                        # 放在这一级别，只有这一索引的播放列表的音频播放完后才会记录最后一次的播放索引位置
                        last_index = index if index < (len(play_list_arr) - 1) else -1
            except Exception as e:
                logger.error(traceback.format_exc())
            
            if self.config.get("play_audio", "player") in ["pygame"]:
                self.mixer_copywriting.quit()
        except Exception as e:
            logger.error(traceback.format_exc())


    def pause_copywriting_play(self):
        """暂停文案播放"""
        logger.info("暂停文案播放")
        self.copywriting_play_flag = 0
        if self.config.get("play_audio", "player") == "audio_player":
            pass
            self.audio_player.pause_stream()
        # 由于v2的暂停不会更换音频，所以这个只暂停文案就没有意义了
        elif self.config.get("play_audio", "player") == "audio_player_v2":
            pass
            # self.audio_player.pause_stream()
        else:
            self.mixer_copywriting.music.pause()

    
    def unpause_copywriting_play(self):
        """恢复暂停文案播放"""
        logger.info("恢复文案播放")
        self.copywriting_play_flag = 2
        # print(f"Audio.copywriting_play_flag={Audio.copywriting_play_flag}")
        if self.config.get("play_audio", "player") in ["audio_player", "audio_player_v2"]:
            pass
            self.audio_player.resume_stream()
        else:
            self.mixer_copywriting.music.unpause()

    
    def stop_copywriting_play(self):
        """停止文案播放"""
        logger.info("停止文案播放")
        self.copywriting_play_flag = 0
        if self.config.get("play_audio", "player") == "audio_player":
            self.audio_player.pause_stream()
        # 由于v2的暂停不会更换音频，所以这个只暂停文案就没有意义了
        elif self.config.get("play_audio", "player") == "audio_player_v2":
            pass
            # self.audio_player.pause_stream()
        else:
            self.mixer_copywriting.music.stop()
