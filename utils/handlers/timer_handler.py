# -*- coding: UTF-8 -*-
"""
定时器处理器模块

处理定时器和周期性任务
"""

import threading
import random
from functools import partial
from ..base import logger


class TimerHandler:
    """定时器处理器"""
    
    def __init__(self, my_handle):
        """
        初始化定时器处理器
        
        Args:
            my_handle: My_handle 实例
        """
        self.my_handle = my_handle
    
    def periodic_trigger_data_handle(self):
        """周期性触发数据处理，每秒执行一次，进行计时"""
        def get_last_n_items(data_list: list, num: int):
            # 返回最后的 n 个元素，如果不足 n 个则返回实际元素个数
            return data_list[-num:] if num > 0 else []
        
        if self.my_handle.config.get("read_comment", "periodic_trigger", "enable"):
            type = "read_comment"
            # 计时+1
            self.my_handle.task_data[type]["time"] += 1
            
            periodic_time_min = int(self.my_handle.config.get(type, "periodic_trigger", "periodic_time_min"))
            periodic_time_max = int(self.my_handle.config.get(type, "periodic_trigger", "periodic_time_max"))
            # 生成触发周期值
            periodic_time = random.randint(periodic_time_min, periodic_time_max)
            logger.debug(f"type={type}, periodic_time={periodic_time}, My_handle.task_data={self.my_handle.task_data}")

            # 计时时间是否超过限定的触发周期
            if self.my_handle.task_data[type]["time"] >= periodic_time:
                # 计时清零
                self.my_handle.task_data[type]["time"] = 0

                trigger_num_min = int(self.my_handle.config.get(type, "periodic_trigger", "trigger_num_min"))
                trigger_num_max = int(self.my_handle.config.get(type, "periodic_trigger", "trigger_num_max"))
                # 生成触发个数
                trigger_num = random.randint(trigger_num_min, trigger_num_max)
                # 获取数据
                data_list = get_last_n_items(self.my_handle.task_data[type]["data"], trigger_num)
                logger.debug(f"type={type}, trigger_num={trigger_num}")

                if data_list != []:
                    # 遍历数据 进行webui数据回传 和 音频合成播放
                    for data in data_list:
                        self.my_handle.audio_synthesis_handle(data)

                # 数据清空
                self.my_handle.task_data[type]["data"] = []
        

        if self.my_handle.config.get("local_qa", "periodic_trigger", "enable"):
            type = "local_qa"
            # 计时+1
            self.my_handle.task_data[type]["time"] += 1
            
            periodic_time_min = int(self.my_handle.config.get(type, "periodic_trigger", "periodic_time_min"))
            periodic_time_max = int(self.my_handle.config.get(type, "periodic_trigger", "periodic_time_max"))
            # 生成触发周期值
            periodic_time = random.randint(periodic_time_min, periodic_time_max)
            logger.debug(f"type={type}, periodic_time={periodic_time}, My_handle.task_data={self.my_handle.task_data}")

            # 计时时间是否超过限定的触发周期
            if self.my_handle.task_data[type]["time"] >= periodic_time:
                # 计时清零
                self.my_handle.task_data[type]["time"] = 0

                trigger_num_min = int(self.my_handle.config.get(type, "periodic_trigger", "trigger_num_min"))
                trigger_num_max = int(self.my_handle.config.get(type, "periodic_trigger", "trigger_num_max"))
                # 生成触发个数
                trigger_num = random.randint(trigger_num_min, trigger_num_max)
                # 获取数据
                data_list = get_last_n_items(self.my_handle.task_data[type]["data"], trigger_num)
                logger.debug(f"type={type}, trigger_num={trigger_num}")

                if data_list != []:
                    # 遍历数据 进行webui数据回传 和 音频合成播放
                    for data in data_list:
                        if data["type"] == "local_qa_audio":
                            self.my_handle.webui_show_chat_log_callback("本地问答-音频", data, data["file_path"])
                        else:
                            self.my_handle.webui_show_chat_log_callback("本地问答-文本", data, data["content"])

                        self.my_handle.audio_synthesis_handle(data)

                # 数据清空
                self.my_handle.task_data[type]["data"] = []
        
        if self.my_handle.config.get("thanks", "gift", "periodic_trigger", "enable"):
            type = "thanks"
            type2 = "gift"

            # 计时+1
            self.my_handle.task_data[type][type2]["time"] += 1

            periodic_time_min = int(self.my_handle.config.get(type, type2, "periodic_trigger", "periodic_time_min"))
            periodic_time_max = int(self.my_handle.config.get(type, type2, "periodic_trigger", "periodic_time_max"))
            # 生成触发周期值
            periodic_time = random.randint(periodic_time_min, periodic_time_max)
            logger.debug(f"type={type}, periodic_time={periodic_time}, My_handle.task_data={self.my_handle.task_data}")

            # 计时时间是否超过限定的触发周期
            if self.my_handle.task_data[type][type2]["time"] >= periodic_time:
                # 计时清零
                self.my_handle.task_data[type][type2]["time"] = 0

                trigger_num_min = int(self.my_handle.config.get(type, type2, "periodic_trigger", "trigger_num_min"))
                trigger_num_max = int(self.my_handle.config.get(type, type2, "periodic_trigger", "trigger_num_max"))
                # 生成触发个数
                trigger_num = random.randint(trigger_num_min, trigger_num_max)
                # 获取数据
                data_list = get_last_n_items(self.my_handle.task_data[type][type2]["data"], trigger_num)
                logger.debug(f"type={type}, trigger_num={trigger_num}")

                if data_list != []:
                    # 遍历数据 进行webui数据回传 和 音频合成播放
                    for data in data_list:
                        self.my_handle.audio_synthesis_handle(data)

                # 数据清空
                self.my_handle.task_data[type][type2]["data"] = []
        
        if self.my_handle.config.get("thanks", "entrance", "periodic_trigger", "enable"):
            type = "thanks"
            type2 = "entrance"

            # 计时+1
            self.my_handle.task_data[type][type2]["time"] += 1

            periodic_time_min = int(self.my_handle.config.get(type, type2, "periodic_trigger", "periodic_time_min"))
            periodic_time_max = int(self.my_handle.config.get(type, type2, "periodic_trigger", "periodic_time_max"))
            # 生成触发周期值
            periodic_time = random.randint(periodic_time_min, periodic_time_max)
            logger.debug(f"type={type}, periodic_time={periodic_time}, My_handle.task_data={self.my_handle.task_data}")

            # 计时时间是否超过限定的触发周期
            if self.my_handle.task_data[type][type2]["time"] >= periodic_time:
                # 计时清零
                self.my_handle.task_data[type][type2]["time"] = 0

                trigger_num_min = int(self.my_handle.config.get(type, type2, "periodic_trigger", "trigger_num_min"))
                trigger_num_max = int(self.my_handle.config.get(type, type2, "periodic_trigger", "trigger_num_max"))
                # 生成触发个数
                trigger_num = random.randint(trigger_num_min, trigger_num_max)
                # 获取数据
                data_list = get_last_n_items(self.my_handle.task_data[type][type2]["data"], trigger_num)
                logger.debug(f"type={type}, trigger_num={trigger_num}")

                if data_list != []:
                    # 遍历数据 进行webui数据回传 和 音频合成播放
                    for data in data_list:
                        self.my_handle.audio_synthesis_handle(data)

                # 数据清空
                self.my_handle.task_data[type][type2]["data"] = []

        if self.my_handle.config.get("thanks", "follow", "periodic_trigger", "enable"):
            type = "thanks"
            type2 = "follow"

            # 计时+1
            self.my_handle.task_data[type][type2]["time"] += 1

            periodic_time_min = int(self.my_handle.config.get(type, type2, "periodic_trigger", "periodic_time_min"))
            periodic_time_max = int(self.my_handle.config.get(type, type2, "periodic_trigger", "periodic_time_max"))
            # 生成触发周期值
            periodic_time = random.randint(periodic_time_min, periodic_time_max)
            logger.debug(f"type={type}, periodic_time={periodic_time}, My_handle.task_data={self.my_handle.task_data}")

            # 计时时间是否超过限定的触发周期
            if self.my_handle.task_data[type][type2]["time"] >= periodic_time:
                # 计时清零
                self.my_handle.task_data[type][type2]["time"] = 0

                trigger_num_min = int(self.my_handle.config.get(type, type2, "periodic_trigger", "trigger_num_min"))
                trigger_num_max = int(self.my_handle.config.get(type, type2, "periodic_trigger", "trigger_num_max"))
                # 生成触发个数
                trigger_num = random.randint(trigger_num_min, trigger_num_max)
                # 获取数据
                data_list = get_last_n_items(self.my_handle.task_data[type][type2]["data"], trigger_num)
                logger.debug(f"type={type}, trigger_num={trigger_num}")

                if data_list != []:
                    # 遍历数据 进行webui数据回传 和 音频合成播放
                    for data in data_list:
                        self.my_handle.audio_synthesis_handle(data)

                # 数据清空
                self.my_handle.task_data[type][type2]["data"] = []


        self.my_handle.periodic_trigger_timer = threading.Timer(1, partial(self.periodic_trigger_data_handle))
        self.my_handle.periodic_trigger_timer.start()

    # 清空live_data直播数据
    def clear_live_data(self, type: str = ""):
        if type != "" and type is not None:
            self.my_handle.live_data[type] = []

        if type == "comment":
            self.my_handle.comment_check_timer = threading.Timer(int(self.my_handle.config.get("filter", "limited_time_deduplication", "comment")), partial(self.clear_live_data, "comment"))
            self.my_handle.comment_check_timer.start()
        elif type == "gift":
            self.my_handle.gift_check_timer = threading.Timer(int(self.my_handle.config.get("filter", "limited_time_deduplication", "gift")), partial(self.clear_live_data, "gift"))
            self.my_handle.gift_check_timer.start()
        elif type == "entrance":
            self.my_handle.entrance_check_timer = threading.Timer(int(self.my_handle.config.get("filter", "limited_time_deduplication", "entrance")), partial(self.clear_live_data, "entrance"))
            self.my_handle.entrance_check_timer.start()

    # 启动定时器
    def start_timers(self):
        if self.my_handle.config.get("filter", "limited_time_deduplication", "enable"):
            # 设置定时器，每隔n秒执行一次
            self.my_handle.comment_check_timer = threading.Timer(int(self.my_handle.config.get("filter", "limited_time_deduplication", "comment")), partial(self.clear_live_data, "comment"))
            self.my_handle.comment_check_timer.start()

            self.my_handle.gift_check_timer = threading.Timer(int(self.my_handle.config.get("filter", "limited_time_deduplication", "gift")), partial(self.clear_live_data, "gift"))
            self.my_handle.gift_check_timer.start()

            self.my_handle.entrance_check_timer = threading.Timer(int(self.my_handle.config.get("filter", "limited_time_deduplication", "entrance")), partial(self.clear_live_data, "entrance"))
            self.my_handle.entrance_check_timer.start()

            logger.info("启动 限定时间直播数据去重 定时器")

        self.my_handle.periodic_trigger_timer = threading.Timer(1, partial(self.periodic_trigger_data_handle))
        self.my_handle.periodic_trigger_timer.start()
        logger.info("启动 周期性触发 定时器")

    # 数据丢弃部分
    def process_data(self, data, timer_flag):
        with self.my_handle.data_lock:
            if timer_flag not in self.my_handle.timers or not self.my_handle.timers[timer_flag].is_alive():
                self.my_handle.timers[timer_flag] = threading.Timer(self.get_interval(timer_flag), self.process_last_data, args=(timer_flag,))
                self.my_handle.timers[timer_flag].start()

            # self.my_handle.timers[timer_flag].last_data = data
            if hasattr(self.my_handle.timers[timer_flag], 'last_data'):
                self.my_handle.timers[timer_flag].last_data.append(data)
                # 这里需要注意配置命名!!!
                # 保留数据数量
                if len(self.my_handle.timers[timer_flag].last_data) > int(self.my_handle.config.get("filter", timer_flag + "_forget_reserve_num")):
                    self.my_handle.timers[timer_flag].last_data.pop(0)
            else:
                self.my_handle.timers[timer_flag].last_data = [data]

    def process_last_data(self, timer_flag):
        with self.my_handle.data_lock:
            timer = self.my_handle.timers.get(timer_flag)
            if timer and timer.last_data is not None and timer.last_data != []:
                logger.debug(f"预处理定时器触发 type={timer_flag}，data={timer.last_data}")

                self.my_handle.is_handleing = 1

                if timer_flag == "comment":
                    for data in timer.last_data:
                        self.my_handle.comment_handle(data)
                elif timer_flag == "gift":
                    for data in timer.last_data:
                        self.my_handle.gift_handle(data)
                    #self.my_handle.gift_handle(timer.last_data)
                elif timer_flag == "entrance":
                    for data in timer.last_data:
                        self.my_handle.entrance_handle(data)
                    #self.my_handle.entrance_handle(timer.last_data)
                elif timer_flag == "follow":
                    for data in timer.last_data:
                        self.my_handle.follow_handle(data)
                elif timer_flag == "talk":
                    # 聊天暂时共用弹幕处理逻辑
                    for data in timer.last_data:
                        self.my_handle.talk_handle(data)
                    #self.my_handle.comment_handle(timer.last_data)
                elif timer_flag == "schedule":
                    # 定时任务处理
                    for data in timer.last_data:
                        self.my_handle.schedule_handle(data)
                    #self.my_handle.schedule_handle(timer.last_data)
                elif timer_flag == "idle_time_task":
                    # 定时任务处理
                    for data in timer.last_data:
                        self.my_handle.idle_time_task_handle(data)
                    #self.my_handle.idle_time_task_handle(timer.last_data)
                elif timer_flag == "image_recognition_schedule":
                    # 定时任务处理
                    for data in timer.last_data:
                        self.my_handle.image_recognition_schedule_handle(data)

                self.my_handle.is_handleing = 0

                # 清空数据
                timer.last_data = []

    def get_interval(self, timer_flag):
        # 根据标志定义不同计时器的间隔
        intervals = {
            "comment": self.my_handle.config.get("filter", "comment_forget_duration"),
            "gift": self.my_handle.config.get("filter", "gift_forget_duration"),
            "entrance": self.my_handle.config.get("filter", "entrance_forget_duration"),
            "follow": self.my_handle.config.get("filter", "follow_forget_duration"),
            "talk": self.my_handle.config.get("filter", "talk_forget_duration"),
            "schedule": self.my_handle.config.get("filter", "schedule_forget_duration"),
            "idle_time_task": self.my_handle.config.get("filter", "idle_time_task_forget_duration")
            # 根据需要添加更多计时器及其间隔，记得添加config.json中的配置项
        }

        # 默认间隔为0.1秒
        return intervals.get(timer_flag, 0.1)