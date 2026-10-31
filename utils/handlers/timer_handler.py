# -*- coding: UTF-8 -*-
"""
定时器处理器模块

处理定时器和周期性任务
"""

import threading
from functools import partial
from .. import my_global
from ..base import logger


class TimerHandler:
    """定时器处理器"""

    # 限流类型白名单（2026-10-31 限入审查批次）：直播间消息六类参与
    # 遗忘&保留窗口；聊天(talk)/定时任务(schedule)/闲时任务(idle_time_task)
    # 为系统交互/系统生成消息，不参与弹幕处理流程限流——配置选项已退役，
    # process_data 对这三类直通分发（跳过去重+遗忘窗口）。
    _FORGET_FLAGS = frozenset({
        "comment", "gift", "entrance", "follow", "like", "room_stats",
    })

    
    def __init__(self, my_handle):
        """
        初始化定时器处理器
        
        Args:
            my_handle: My_handle 实例
        """
        self.my_handle = my_handle
        # 每类型分发锁（防遗忘窗口重构：立即分发与窗口末补发按类型串行）
        self._dispatch_locks = {}
    
    # ~~periodic_trigger_data_handle~~周期触发已退役（2026-10-31 本地问答审查批次
    # 整体移除：task_data 的 local_qa 缓冲自始零写入点——回放永远取空列表的死
    # 机制；念弹幕/答谢缓冲已先行退役，最后一只消费者随之删除。本地问答命中
    # 改为 feature_handler.local_qa_handle 即时合成播放，与其它产线同语义）

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

    # 数据丢弃部分
    def process_data(self, data, timer_flag):
        # 非限流类型直通（2026-10-31 限入审查批次）：talk/schedule/idle_time_task
        # 为语音对话/系统生成消息——去重与遗忘窗口均不适用（语音对话被"遗忘"
        # 即丢回复；系统任务低频无需限流），立即异步分发保持与首条消息同语义
        if timer_flag not in self._FORGET_FLAGS:
            threading.Thread(target=self._dispatch, args=(timer_flag, [data]),
                             name=f"direct-dispatch-{timer_flag}", daemon=True).start()
            return

        # 弹幕黑名单统一拦截（2026-10-31 黑名单审查批次）：名单内用户的一切
        # 直播间信息（弹幕/礼物/入场/关注/点赞）直接抛弃——先于限入窗口/
        # 去重/分发/关键词识别，不占窗口名额、不触发任何联动（T7 语义
        # "不进行任何处理"；此前仅 comment/chat 两链尾部拦截，礼物/关注/
        # 入场照常触发答谢/积分/编排）。room_stats 无用户维度天然放行；
        # comment/chat 链内既有拦截保留双保险（聊天框源不经此路径）。
        if self.my_handle.blacklist_handle(data):
            return

        # 最近互动用户名记录 + 闲时计时刷新（2026-10-31 定时任务/闲时任务审查
        # 批次接线）：{last_username} 数据源此前全库零调用恒"未知用户"；
        # idle_time_auto_clear（trigger_type 消费）此前同样全库零调用——"直播
        # 间无消息更新闲时"机制下闲时计时仅被 is_handleing 瞬时标志重置（5s
        # 轮询基本错过），活跃直播间照常触发闲时任务。六类互动消息（非黑名单）
        # 到达即：勾选类型重置闲时计时（idle_time_task.trigger_type 四选项）+
        # 记录互动用户名（room_stats 为系统快照无用户维度不参与）。系统自源
        # （origin=system-idle：闲时任务弹幕触发LLM模式自发弹幕）排除——系统
        # 消息不算观众互动，不污染 {last_username}、不刷新闲时计时。
        if data.get("origin") != "system-idle":
            if timer_flag != "room_stats":
                _username = data.get("username") or ""
                if _username:
                    my_global.add_username_to_last_username_list(_username)
            my_global.idle_time_auto_clear(self.my_handle.config, timer_flag)

        with self.my_handle.data_lock:
            # 限定时间段内重复丢弃（入口统一接线，2026-10-24 审查批次）：重复数据
            # 不进入遗忘窗口（原实现遗漏调用点——live_data 只清空不写入，去重空转）
            if self.my_handle.is_data_repeat_in_limited_time(data, timer_flag):
                logger.debug(f"[{timer_flag}] 限定时间内重复数据丢弃：{data.get('username')}/{str(data.get('content'))[:30]}")
                try:
                    from utils.obs_stats import record_drop
                    record_drop("dedup_dropped")
                except Exception:
                    pass
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
                # T1 观测：窗口合并暂缓计数（非丢弃——窗口末补发最新 reserve_num-1 条）
                try:
                    from utils.obs_stats import record_drop
                    record_drop("throttle_merged")
                except Exception:
                    pass
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
        # 根据标志定义不同计时器的间隔（限流类型六类——2026-10-31 限入审查
        # 批次：聊天/定时任务/闲时任务限流选项退役，三类直通不走窗口）
        intervals = {
            "comment": self.my_handle.config.get("filter", "comment_forget_duration"),
            "gift": self.my_handle.config.get("filter", "gift_forget_duration"),
            "entrance": self.my_handle.config.get("filter", "entrance_forget_duration"),
            "follow": self.my_handle.config.get("filter", "follow_forget_duration"),
            "like": self.my_handle.config.get("filter", "like_forget_duration"),
            "room_stats": self.my_handle.config.get("filter", "room_stats_forget_duration"),
            # 根据需要添加更多计时器及其间隔，记得添加config.json中的配置项
        }

        # 默认间隔为0.1秒（缺键/非法值容错——threading.Timer 不接受 None）
        interval = intervals.get(timer_flag, 0.1)
        try:
            return float(interval)
        except (TypeError, ValueError):
            return 0.1