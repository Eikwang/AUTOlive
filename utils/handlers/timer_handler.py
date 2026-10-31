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
        # 每类型分发锁（防遗忘窗口重构：立即分发与窗口末补发按类型串行）
        self._dispatch_locks = {}
    
    def periodic_trigger_data_handle(self):
        """周期性触发数据处理，每秒执行一次，进行计时"""
        def get_last_n_items(data_list: list, num: int):
            # 返回最后的 n 个元素，如果不足 n 个则返回实际元素个数
            return data_list[-num:] if num > 0 else []
        
        if self.my_handle.config.get("read_comment", "periodic_trigger", "enable"):
            type = "read_comment"
            # 计时+1
            self.my_handle.task_data[type]["time"] += 1

            # UI-16 批次 R10/EV-1g：触发周期合并为固定单值 periodic_time（不再区间
            # 随机）；缺省 60 秒+正整数校验（存量 min/max 键已由 settings.py 迁移
            # 退役）。本分支为 read_comment 专用——local_qa/thanks 等分支保持
            # min/max+randint 不变（分支隔离约束）。
            try:
                periodic_time = max(1, int(self.my_handle.config.get(type, "periodic_trigger", "periodic_time")))
            except (TypeError, ValueError):
                periodic_time = 60
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
                    # 念弹幕路由（十生产端接线之念弹幕回放，D5：念原文直 TTS 不过
                    # 知识库；勾选 read_comment→助播消息，未勾选原路）
                    from .assistant_router import assistant_route, assistant_copy_message
                    _rc_routed = assistant_route(self.my_handle.config, "read_comment")
                    for data in data_list:
                        if _rc_routed:
                            self.my_handle.audio_synthesis_handle(assistant_copy_message(
                                self.my_handle.config, data.get("content", ""),
                                producer_type="read_comment", hit_layer="direct"))
                        else:
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

            # UI-16 二轮验收（任务 10）：触发周期合并为固定单值（不再区间随机）；
            # 缺省 60 秒+正整数校验；与 thanks.entrance/gift/follow 三节共用（type2 区分）
            try:
                periodic_time = max(1, int(self.my_handle.config.get(type, type2, "periodic_trigger", "periodic_time")))
            except (TypeError, ValueError):
                periodic_time = 60
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

            # UI-16 二轮验收（任务 10）：触发周期合并为固定单值（不再区间随机）；
            # 缺省 60 秒+正整数校验；与 thanks.entrance/gift/follow 三节共用（type2 区分）
            try:
                periodic_time = max(1, int(self.my_handle.config.get(type, type2, "periodic_trigger", "periodic_time")))
            except (TypeError, ValueError):
                periodic_time = 60
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

            # UI-16 二轮验收（任务 10）：触发周期合并为固定单值（不再区间随机）；
            # 缺省 60 秒+正整数校验；与 thanks.entrance/gift/follow 三节共用（type2 区分）
            try:
                periodic_time = max(1, int(self.my_handle.config.get(type, type2, "periodic_trigger", "periodic_time")))
            except (TypeError, ValueError):
                periodic_time = 60
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

            # 新增类型去重清空定时器（2026-10-24 审查批次：like/follow/room_stats）
            _like_dedup = self.my_handle.config.get("filter", "limited_time_deduplication", "like")
            if _like_dedup is not None:
                self.my_handle.like_check_timer = threading.Timer(int(_like_dedup), partial(self.clear_live_data, "like"))
                self.my_handle.like_check_timer.start()

            _follow_dedup = self.my_handle.config.get("filter", "limited_time_deduplication", "follow")
            if _follow_dedup is not None:
                self.my_handle.follow_check_timer = threading.Timer(int(_follow_dedup), partial(self.clear_live_data, "follow"))
                self.my_handle.follow_check_timer.start()

            _room_stats_dedup = self.my_handle.config.get("filter", "limited_time_deduplication", "room_stats")
            if _room_stats_dedup is not None:
                self.my_handle.room_stats_check_timer = threading.Timer(int(_room_stats_dedup), partial(self.clear_live_data, "room_stats"))
                self.my_handle.room_stats_check_timer.start()

            logger.info("启动 限定时间直播数据去重 定时器")

        self.my_handle.periodic_trigger_timer = threading.Timer(1, partial(self.periodic_trigger_data_handle))
        self.my_handle.periodic_trigger_timer.start()
        logger.info("启动 周期性触发 定时器")

    # 数据丢弃部分
    def process_data(self, data, timer_flag):
        with self.my_handle.data_lock:
            # 限定时间段内重复丢弃（入口统一接线，2026-10-24 审查批次）：重复数据
            # 不进入遗忘窗口（原实现遗漏调用点——live_data 只清空不写入，去重空转）
            if self.my_handle.is_data_repeat_in_limited_time(data, timer_flag):
                logger.debug(f"[{timer_flag}] 限定时间内重复数据丢弃：{data.get('username')}/{str(data.get('content'))[:30]}")
                return

            timer = self.my_handle.timers.get(timer_flag)
            window_open = (timer is not None and timer.is_alive()
                           and hasattr(timer, "last_data"))
            immediate_data = None
            if not window_open:
                # 防遗忘窗口重构（2026-10-28 优化批次）：首条消息立即分发下游处理，
                # 遗忘窗口仅用于合并窗口期内后续同类消息——窗口到期时补发最新
                # reserve_num-1 条（首条已处理，不重复）。单条消息延迟从
                # 「固定遗忘间隔」降为 0（首条即处理）。
                new_timer = threading.Timer(self.get_interval(timer_flag), self.process_last_data, args=(timer_flag,))
                new_timer.last_data = []
                self.my_handle.timers[timer_flag] = new_timer
                new_timer.start()
                immediate_data = [data]

            if not window_open:
                # /send 为 async 路由：立即分发必须脱离事件循环线程（LLM/TTS 为秒级
                # 阻塞调用，inline 执行会卡死 HTTP 服务）；窗口末路径由 Timer 线程执行
                threading.Thread(target=self._dispatch, args=(timer_flag, immediate_data),
                                 name=f"immediate-dispatch-{timer_flag}", daemon=True).start()
            else:
                timer.last_data.append(data)
                # 这里需要注意配置命名!!!
                # 保留数据数量（缺键/非法值容错默认 1——存量用户 config 可能缺新键）
                try:
                    reserve_num = int(self.my_handle.config.get("filter", timer_flag + "_forget_reserve_num"))
                except (TypeError, ValueError):
                    reserve_num = 1
                # 首条已立即处理，窗口末最多补发 reserve_num-1 条（保留最新）。
                # 语义示例：comment 间隔 10s+保留数 2 → 首条"666"立即处理，
                # 10s 窗口内再来的在窗口末补发最新 1 条；保留数 1 → 窗口末不补发。
                tail_keep = max(0, reserve_num - 1)
                if len(timer.last_data) > tail_keep:
                    timer.last_data = timer.last_data[-tail_keep:] if tail_keep else []

    def process_last_data(self, timer_flag):
        with self.my_handle.data_lock:
            timer = self.my_handle.timers.get(timer_flag)
            if not timer or not hasattr(timer, "last_data"):
                return
            data_list = list(timer.last_data)
            # 清空数据
            timer.last_data = []
        self._dispatch(timer_flag, data_list)

    def _dispatch(self, timer_flag, data_list):
        """统一分发出口：按类型串行处理（立即分发与窗口末补发不并发）。"""
        if not data_list:
            return
        # 每类型一把锁：同类型消息严格串行（保持 is_handleing 语义与处理顺序），
        # 不同类型互不阻塞；setdefault 取锁（CPython 原子，免额外守卫锁）
        with self._dispatch_locks.setdefault(timer_flag, threading.Lock()):
            logger.debug(f"预处理定时器触发 type={timer_flag}，data={data_list}")

            self.my_handle.is_handleing = 1
            try:
                handler_names = {
                    "comment": "comment_handle",
                    "gift": "gift_handle",
                    "entrance": "entrance_handle",
                    "follow": "follow_handle",
                    "talk": "talk_handle",  # 聊天暂时共用弹幕处理逻辑
                    "schedule": "schedule_handle",  # 定时任务处理
                    "idle_time_task": "idle_time_task_handle",  # 定时任务处理
                    "like": "like_handle",  # 点赞处理（仅计数不播报，2026-10-24 审查批次）
                    "room_stats": "room_stats_handle",  # 房间信息处理（更新内部人气数据，不播报）
                }
                handler = getattr(self.my_handle, handler_names.get(timer_flag, ""), None)
                if handler is not None:
                    for data in data_list:
                        handler(data)
            finally:
                self.my_handle.is_handleing = 0

    def get_interval(self, timer_flag):
        # 根据标志定义不同计时器的间隔
        intervals = {
            "comment": self.my_handle.config.get("filter", "comment_forget_duration"),
            "gift": self.my_handle.config.get("filter", "gift_forget_duration"),
            "entrance": self.my_handle.config.get("filter", "entrance_forget_duration"),
            "follow": self.my_handle.config.get("filter", "follow_forget_duration"),
            "like": self.my_handle.config.get("filter", "like_forget_duration"),
            "room_stats": self.my_handle.config.get("filter", "room_stats_forget_duration"),
            "talk": self.my_handle.config.get("filter", "talk_forget_duration"),
            "schedule": self.my_handle.config.get("filter", "schedule_forget_duration"),
            "idle_time_task": self.my_handle.config.get("filter", "idle_time_task_forget_duration")
            # 根据需要添加更多计时器及其间隔，记得添加config.json中的配置项
        }

        # 默认间隔为0.1秒（缺键/非法值容错——threading.Timer 不接受 None）
        interval = intervals.get(timer_flag, 0.1)
        try:
            return float(interval)
        except (TypeError, ValueError):
            return 0.1