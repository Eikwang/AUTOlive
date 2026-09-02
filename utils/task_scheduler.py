"""
任务调度模块
提供定时任务、随机任务调度、闲时任务等功能
"""

import asyncio
import copy
import random
import threading
import time
import traceback
from typing import Dict, List, Optional

import schedule

from utils.my_log import logger
from utils.config import Config
from utils.common import Common
from utils.my_handle import My_handle
import utils.my_global as my_global


class TaskScheduler:
    """任务调度器类"""
    
    def __init__(self, config: Config, common: Common, my_handle: Optional[My_handle] = None, platform: str = ""):
        """
        初始化任务调度器
        
        Args:
            config: 配置对象
            common: 通用工具对象
            my_handle: 处理器对象
            platform: 平台标识
        """
        self.config = config
        self.common = common
        self.my_handle = my_handle
        self.platform = platform
        
        # 任务状态
        self.is_running = False
        self.scheduler_thread = None
        self.trends_copywriting_thread = None
        
        # 闲时任务数据
        self.copywriting_copy_list = []
        self.comment_copy_list = []
        self.local_audio_path_list = []
        
        logger.info("任务调度器初始化完成")
    
    def schedule_task(self, index: int):
        """
        执行定时任务
        
        Args:
            index: 任务索引
        """
        logger.debug("定时任务执行中...")
        hour, min = self.common.get_bj_time(6)
        
        # 时间描述
        if 0 <= hour < 6:
            time_desc = f"凌晨{hour}点{min}分"
        elif 6 <= hour < 9:
            time_desc = f"早晨{hour}点{min}分"
        elif 9 <= hour < 12:
            time_desc = f"上午{hour}点{min}分"
        elif hour == 12:
            time_desc = f"中午{hour}点{min}分"
        elif 13 <= hour < 18:
            time_desc = f"下午{hour - 12}点{min}分"
        elif 18 <= hour < 20:
            time_desc = f"傍晚{hour - 12}点{min}分"
        elif 20 <= hour < 24:
            time_desc = f"晚上{hour - 12}点{min}分"
        else:
            time_desc = f"{hour}点{min}分"
        
        # 根据对应索引从列表中随机获取一个值
        schedule_config = self.config.get("schedule")
        if index >= len(schedule_config) or len(schedule_config[index]["copy"]) <= 0:
            return None
        
        random_copy = random.choice(schedule_config[index]["copy"])
        
        # 假设有多个未知变量，用户可以在此处定义动态变量
        variables = {
            "time": time_desc,
            "user_num": "N",
            "last_username": my_global.last_username_list[-1] if my_global.last_username_list else "未知用户",
        }
        
        # 有用户数据情况的平台特殊处理
        if self.platform in ["dy", "tiktok"]:
            variables["user_num"] = my_global.last_liveroom_data.get("OnlineUserCount", "N")
        
        # 使用字典进行字符串替换
        if any(var in random_copy for var in variables):
            content = random_copy.format(
                **{var: value for var, value in variables.items() if var in random_copy}
            )
        else:
            content = random_copy
        
        content = self.common.brackets_text_randomize(content)
        
        data = {"platform": self.platform, "username": "定时任务", "content": content}
        
        logger.info(f"定时任务：{content}")
        
        if self.my_handle:
            self.my_handle.process_data(data, "schedule")
    
    def run_schedule(self):
        """运行定时任务调度"""
        try:
            schedule_config = self.config.get("schedule")
            for index, task in enumerate(schedule_config):
                if task["enable"]:
                    min_seconds = int(task["time_min"])
                    max_seconds = int(task["time_max"])
                    
                    def schedule_random_task(index, min_seconds, max_seconds):
                        schedule.clear(index)
                        # 在min_seconds和max_seconds之间随机选择下一次任务执行的时间
                        next_time = random.randint(min_seconds, max_seconds)
                        
                        self.schedule_task(index)
                        
                        schedule.every(next_time).seconds.do(
                            schedule_random_task, index, min_seconds, max_seconds
                        ).tag(index)
                    
                    schedule_random_task(index, min_seconds, max_seconds)
        except Exception as e:
            logger.error(f"定时任务调度失败: {traceback.format_exc()}")
        
        self.is_running = True
        while self.is_running:
            schedule.run_pending()
            time.sleep(1)  # 控制每次循环的间隔时间
    
    def start_schedule_thread(self):
        """启动定时任务线程"""
        # 检查是否有启用的定时任务
        schedule_config = self.config.get("schedule")
        if any(item["enable"] for item in schedule_config) or self.platform == "dy":
            self.scheduler_thread = threading.Thread(target=self.run_schedule, daemon=True)
            self.scheduler_thread.start()
            logger.info("定时任务线程已启动")
    
    def load_data_list(self, data_type: str) -> List:
        """
        加载数据列表
        
        Args:
            data_type: 数据类型（copywriting, comment, local_audio）
            
        Returns:
            List: 数据列表
        """
        if data_type == "copywriting":
            tmp = self.config.get("idle_time_task", "copywriting", "copy")
        elif data_type == "comment":
            tmp = self.config.get("idle_time_task", "comment", "copy")
        elif data_type == "local_audio":
            tmp = self.config.get("idle_time_task", "local_audio", "path")
        else:
            logger.warning(f"未知的数据类型: {data_type}")
            return []
        
        logger.debug(f"type={data_type}, tmp={tmp}")
        return copy.copy(tmp) if tmp else []
    
    def do_task(self, last_mode: int, copywriting_copy_list: List, 
                comment_copy_list: List, local_audio_path_list: List) -> tuple:
        """
        执行闲时任务
        
        Args:
            last_mode: 上一个模式
            copywriting_copy_list: 文案列表
            comment_copy_list: 评论列表
            local_audio_path_list: 本地音频路径列表
            
        Returns:
            tuple: (last_mode, copywriting_copy_list, comment_copy_list, local_audio_path_list)
        """
        # 闲时计数清零
        my_global.global_idle_time = 0
        
        # 闲时任务处理
        if self.config.get("idle_time_task", "copywriting", "enable"):
            if last_mode == 0:
                # 是否开启了随机触发
                if self.config.get("idle_time_task", "copywriting", "random"):
                    logger.debug("切换到文案触发模式")
                    if copywriting_copy_list:
                        # 随机打乱列表中的元素
                        random.shuffle(copywriting_copy_list)
                        copywriting_copy = copywriting_copy_list.pop(0)
                    else:
                        # 刷新list数据
                        copywriting_copy_list = self.load_data_list("copywriting")
                        # 随机打乱列表中的元素
                        random.shuffle(copywriting_copy_list)
                        if copywriting_copy_list:
                            copywriting_copy = copywriting_copy_list.pop(0)
                        else:
                            return (last_mode, copywriting_copy_list, comment_copy_list, local_audio_path_list)
                else:
                    logger.debug(copywriting_copy_list)
                    if copywriting_copy_list:
                        copywriting_copy = copywriting_copy_list.pop(0)
                    else:
                        # 刷新list数据
                        copywriting_copy_list = self.load_data_list("copywriting")
                        if copywriting_copy_list:
                            copywriting_copy = copywriting_copy_list.pop(0)
                        else:
                            return (last_mode, copywriting_copy_list, comment_copy_list, local_audio_path_list)
                
                # 处理文案
                if self.my_handle:
                    data = {"platform": self.platform, "username": "闲时任务", "content": copywriting_copy}
                    self.my_handle.process_data(data, "idle_time_task")
                
                last_mode = 1
        
        # 这里可以添加更多的闲时任务逻辑
        
        return (last_mode, copywriting_copy_list, comment_copy_list, local_audio_path_list)
    
    def start_trends_copywriting(self):
        """启动动态文案任务"""
        if not self.config.get("trends_copywriting", "enable"):
            return
        
        def run_trends_copywriting():
            logger.info("动态文案任务线程运行中...")
            
            try:
                while self.is_running:
                    # 文案文件路径列表
                    copywriting_file_path_list = []
                    
                    # 获取动态文案列表
                    for copywriting in self.config.get("trends_copywriting", "copywriting"):
                        # 获取文件夹内所有文件的文件绝对路径，包括文件扩展名
                        for tmp in self.common.get_all_file_paths(copywriting["folder_path"]):
                            copywriting_file_path_list.append(tmp)
                        
                        # 是否开启随机播放
                        if self.config.get("trends_copywriting", "random_play"):
                            random.shuffle(copywriting_file_path_list)
                        
                        # 这里可以添加播放逻辑
                        # 暂时只是记录日志
                        logger.debug(f"动态文案文件: {copywriting_file_path_list}")
                    
                    time.sleep(60)  # 每分钟检查一次
                    
            except Exception as e:
                logger.error(f"动态文案任务失败: {traceback.format_exc()}")
        
        self.trends_copywriting_thread = threading.Thread(target=run_trends_copywriting, daemon=True)
        self.trends_copywriting_thread.start()
        logger.info("动态文案任务线程已启动")
    
    def start_all(self):
        """启动所有任务"""
        self.start_schedule_thread()
        self.start_trends_copywriting()
        logger.info("所有任务已启动")
    
    def stop_all(self):
        """停止所有任务"""
        self.is_running = False
        
        # 清除所有定时任务
        schedule.clear()
        
        logger.info("所有任务已停止")
    
    def get_task_status(self) -> Dict:
        """
        获取任务状态
        
        Returns:
            Dict: 任务状态信息
        """
        return {
            "is_running": self.is_running,
            "scheduler_thread_alive": self.scheduler_thread is not None and self.scheduler_thread.is_alive(),
            "trends_copywriting_thread_alive": self.trends_copywriting_thread is not None and self.trends_copywriting_thread.is_alive(),
            "scheduled_jobs": len(schedule.get_jobs()),
        }
    
    def add_custom_task(self, task_func, interval_seconds: int, task_tag: str = None):
        """
        添加自定义任务
        
        Args:
            task_func: 任务函数
            interval_seconds: 执行间隔（秒）
            task_tag: 任务标签
        """
        if task_tag:
            schedule.every(interval_seconds).seconds.do(task_func).tag(task_tag)
        else:
            schedule.every(interval_seconds).seconds.do(task_func)
        
        logger.info(f"添加自定义任务，间隔: {interval_seconds}秒")
    
    def remove_task_by_tag(self, task_tag: str):
        """
        根据标签移除任务
        
        Args:
            task_tag: 任务标签
        """
        schedule.clear(task_tag)
        logger.info(f"移除任务标签: {task_tag}")


# 创建全局实例（可选）
_task_scheduler_instance = None


def get_task_scheduler(config: Config = None, common: Common = None, 
                      my_handle: My_handle = None, platform: str = "") -> TaskScheduler:
    """
    获取任务调度器单例
    
    Args:
        config: 配置对象（首次调用时需要）
        common: 通用工具对象（首次调用时需要）
        my_handle: 处理器对象
        platform: 平台标识
    
    Returns:
        TaskScheduler实例
    """
    global _task_scheduler_instance
    if _task_scheduler_instance is None:
        if config is None or common is None:
            raise ValueError("首次调用需要提供config和common参数")
        _task_scheduler_instance = TaskScheduler(config, common, my_handle, platform)
    return _task_scheduler_instance


def create_task_scheduler(config: Config, common: Common, 
                         my_handle: My_handle = None, platform: str = "") -> TaskScheduler:
    """
    创建任务调度器实例
    
    Args:
        config: 配置对象
        common: 通用工具对象
        my_handle: 处理器对象
        platform: 平台标识
        
    Returns:
        TaskScheduler实例
    """
    return TaskScheduler(config, common, my_handle, platform)
