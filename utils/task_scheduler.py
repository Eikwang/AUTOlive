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
        执行闲时任务（T8 接线：三模式每周期随机执行其一——任务书 E5 语义）

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

        # 收集已启用的闲时模式，每周期随机执行其一（任务书"只随机执行其中一个"）
        active_modes = []
        if self.config.get("idle_time_task", "copywriting", "enable"):
            active_modes.append("copywriting")
        if self.config.get("idle_time_task", "comment", "enable"):
            active_modes.append("comment")
        if self.config.get("idle_time_task", "local_audio", "enable"):
            active_modes.append("local_audio")

        if not active_modes or not self.my_handle:
            return (last_mode, copywriting_copy_list, comment_copy_list, local_audio_path_list)

        mode = random.choice(active_modes)

        if mode == "copywriting":
            # 闲时文案：随机/顺序取文案 → 闲时出口合成
            copy_content = self._pop_from_list(copywriting_copy_list, "copywriting")
            if copy_content is None:
                return (last_mode, copywriting_copy_list, comment_copy_list, local_audio_path_list)
            data = {"platform": self.platform, "username": "闲时任务", "content": copy_content}
            self.my_handle.process_data(data, "idle_time_task")

        elif mode == "comment":
            # 弹幕触发 LLM 模式：随机弹幕激活系统对话
            comment_content = self._pop_from_list(comment_copy_list, "comment")
            if comment_content is None:
                return (last_mode, copywriting_copy_list, comment_copy_list, local_audio_path_list)
            data = {"platform": self.platform, "username": "闲时任务", "content": comment_content}
            self.my_handle.process_data(data, "comment")

        elif mode == "local_audio":
            # 本地音频模式：随机音频文件走 song 直通（不经 TTS，与点歌同路径）
            audio_path = self._pop_from_list(local_audio_path_list, "local_audio")
            if audio_path is None:
                return (last_mode, copywriting_copy_list, comment_copy_list, local_audio_path_list)
            message = {
                "type": "song",
                "content": audio_path,
                "username": "闲时任务",
            }
            self.my_handle.audio.audio_synthesis(message)

        last_mode = 1
        return (last_mode, copywriting_copy_list, comment_copy_list, local_audio_path_list)

    def _pop_from_list(self, data_list: List, data_type: str):
        """从列表取一项（随机/顺序），空则刷新列表；双空返回 None。"""
        random_mode = self.config.get("idle_time_task", data_type, "random")
        if random_mode and data_list:
            random.shuffle(data_list)
        if not data_list:
            refreshed = self.load_data_list(data_type)
            if random_mode:
                random.shuffle(refreshed)
            data_list.clear()
            data_list.extend(refreshed)
        if not data_list:
            logger.warning(f"闲时任务 {data_type} 数据为空，跳过本模式（D-8 静默语义）")
            return None
        return data_list.pop(0)

    def start_idle_time_task_thread(self):
        """闲时任务驱动线程（T8 接线：三机制判定 + 闲时窗口触发 do_task）"""
        if not self.config.get("idle_time_task", "enable"):
            return

        def run_idle_time_task():
            logger.info("闲时任务线程运行中...")
            last_mode = 0
            while self.is_running:
                time.sleep(5)
                if self.my_handle is None:
                    continue

                # 消息处理中 → 非闲时，重置计数
                if getattr(self.my_handle, "is_handleing", 0) == 1:
                    my_global.global_idle_time = 0
                    continue

                mechanism = str(self.config.get("idle_time_task", "type") or "")
                idle_now = False

                if "无消息" in mechanism:
                    # 直播间无消息更新闲时：计数器递增（有消息处理时已被重置）
                    my_global.global_idle_time += 5
                    try:
                        idle_min = int(self.config.get("idle_time_task", "idle_time_min") or 30)
                        idle_max = int(self.config.get("idle_time_task", "idle_time_max") or 60)
                        idle_min, idle_max = min(idle_min, idle_max), max(idle_min, idle_max)
                    except (TypeError, ValueError):
                        idle_min, idle_max = 30, 60
                    idle_now = my_global.global_idle_time >= random.randint(idle_min, idle_max)
                elif "合成" in mechanism or "消息队列" in mechanism:
                    # 待合成消息队列闲时：队列长度 ≤ 阈值触发
                    try:
                        threshold = int(self.config.get("idle_time_task", "min_msg_queue_len_to_trigger") or 1)
                    except (TypeError, ValueError):
                        threshold = 1
                    queue_len = len(getattr(self.my_handle.audio, "message_queue", []) or [])
                    idle_now = queue_len <= threshold
                elif "播放" in mechanism or "音频队列" in mechanism:
                    # 待播放音频队列闲时
                    try:
                        threshold = int(self.config.get("idle_time_task", "min_audio_queue_len_to_trigger") or 1)
                    except (TypeError, ValueError):
                        threshold = 1
                    queue_len = len(getattr(self.my_handle.audio, "voice_tmp_path_queue", []) or [])
                    idle_now = queue_len <= threshold
                else:
                    logger.warning(f"未知闲时机制类型：{mechanism}，本轮跳过（E5 单选）")
                    continue

                if idle_now:
                    logger.info(f"闲时触发（机制={mechanism}，idle={my_global.global_idle_time}s）")
                    last_mode, self.copywriting_copy_list, self.comment_copy_list, self.local_audio_path_list = \
                        self.do_task(last_mode, self.copywriting_copy_list,
                                     self.comment_copy_list, self.local_audio_path_list)

        self.idle_time_task_thread = threading.Thread(target=run_idle_time_task, daemon=True)
        self.idle_time_task_thread.start()
        logger.info("闲时任务线程已启动")

    def start_trends_copywriting(self):
        """启动动态文案任务（T9 实装：本地文案→可选 LLM 改写→TTS 播放）"""
        if not self.config.get("trends_copywriting", "enable"):
            return

        def _read_copy_file(path):
            """读文案文件：UTF-8 优先，失败回退 GBK，双双失败返回 None 不静默（E4 教训）。"""
            for encoding in ("utf-8", "gbk"):
                try:
                    content = open(path, "r", encoding=encoding).read().strip()
                    if content:
                        return content
                    return None
                except (UnicodeDecodeError, OSError):
                    continue
            logger.warning(f"动态文案文件解码失败，跳过：{path}")
            return None

        def run_trends_copywriting():
            logger.info("动态文案任务线程运行中...")
            file_cursor = 0
            copy_cursor = 0

            try:
                while self.is_running:
                    # UI-16 批次 R5②/EV-1f：文案来源双链——优先读前端直输 copy 列表
                    # （每项=一条文案）；copy 空时回退存量 folder_path 文件链（迁移期
                    # 兼容：页面迁移完成前存量行为不变；copy 非空即走新链）。
                    # EV-1f：空文案行跳过；全空保持 D-8 空列表静默语义。
                    direct_copy_list = [
                        str(c).strip() for c in (self.config.get("trends_copywriting", "copy") or [])
                        if str(c).strip()
                    ]
                    file_path_list = []
                    if not direct_copy_list:
                        # 旧链回退：copywriting[].folder_path 列表（迁移期兼容）
                        for copywriting in self.config.get("trends_copywriting", "copywriting") or []:
                            file_path_list.extend(self.common.get_all_file_paths(copywriting.get("folder_path", "")))

                    if direct_copy_list:
                        if self.config.get("trends_copywriting", "random_play"):
                            resp_content = random.choice(direct_copy_list)
                            source_path = "config 文案列表"
                        else:
                            resp_content = direct_copy_list[copy_cursor % len(direct_copy_list)]
                            copy_cursor += 1
                            source_path = "config 文案列表"
                    else:
                        if not file_path_list:
                            logger.warning("动态文案：文案目录为空或无文件，本轮跳过（D-8 空列表静默语义）")
                            time.sleep(60)
                            continue

                        if self.config.get("trends_copywriting", "random_play"):
                            source_path = random.choice(file_path_list)
                        else:
                            source_path = file_path_list[file_cursor % len(file_path_list)]
                            file_cursor += 1

                        source_content = _read_copy_file(source_path)
                        if not source_content:
                            time.sleep(60)
                            continue
                        resp_content = source_content

                    # S-3 注入防护：改写输入仅限本地文案原文+配置内固定提示词，
                    # 不接入任何弹幕/事件变量；原文截断 500 字符防爆 LLM/TTS。
                    # 新链（config 文案列表）与旧链（本地文件）来源性质一致：
                    # 均为配置内固定文案，无观众输入注入点。
                    resp_content = resp_content[:500]
                    # R5③/EV-1e：提示词转换读页级新键（trends_copywriting 根），
                    # 逐条目旧键退役；页级值同样为配置内固定提示词（S-3 性质不变）
                    prompt_change_enable = bool(self.config.get("trends_copywriting", "prompt_change_enable"))
                    prompt_change_content = self.config.get("trends_copywriting", "prompt_change_content") or ""

                    if prompt_change_enable:
                        # R5①/EV-1j：llm_type 收敛 {custom_llm, fastrag}（fastrag 走
                        # llm_handle 共享层三通道，服务不可达由共享层托底熔断）；
                        # 未知值（存量 chatgpt 等）降级 custom_llm
                        llm_type = self.config.get("trends_copywriting", "llm_type") or "custom_llm"
                        if llm_type not in ("custom_llm", "fastrag"):
                            llm_type = "custom_llm"
                        rewrite_prompt = f"{prompt_change_content}\n原文案：{resp_content}"
                        try:
                            if self.my_handle is not None and llm_type in (self.my_handle.chat_type_list or []):
                                rewritten = self.my_handle.chat_handler.llm_handle(
                                    llm_type, {"username": "系统", "content": rewrite_prompt}
                                )
                                if rewritten:
                                    resp_content = str(rewritten)[:500]
                                else:
                                    logger.warning("动态文案 LLM 改写无返回，降级播放原文案")
                            else:
                                logger.warning(f"动态文案 LLM {llm_type} 不可用，降级播放原文案")
                        except Exception as llm_e:
                            logger.warning(f"动态文案 LLM 改写失败，降级播放原文案：{llm_e}")

                    # S-3 注入安全声明：此处 resp_content 的唯一来源是本地文案文件
                    # （+ 可选 LLM 对该文件的改写），不存在弹幕/观众输入注入点
                    message = {
                        "type": "copywriting",
                        "tts_type": self.config.get("audio_synthesis_type"),
                        "data": self.config.get(self.config.get("audio_synthesis_type")),
                        "config": self.config.get("filter"),
                        "username": "动态文案",
                        "content": resp_content,
                    }
                    if self.my_handle is not None:
                        self.my_handle.audio_synthesis_handle(message)
                        logger.info(f"动态文案已入播放链：{source_path}")
                    else:
                        logger.warning("动态文案：my_handle 未就绪，本轮跳过播放")

                    # 文案播放间隔（单位：秒，任务书口径）
                    try:
                        interval = int(self.config.get("trends_copywriting", "play_interval") or 60)
                    except (TypeError, ValueError):
                        interval = 60
                    sleep_total = max(interval, 10)
                    # 分片睡眠保证 is_running 停止信号响应
                    for _ in range(sleep_total):
                        if not self.is_running:
                            break
                        time.sleep(1)

            except Exception as e:
                logger.error(f"动态文案任务失败: {traceback.format_exc()}")

        self.trends_copywriting_thread = threading.Thread(target=run_trends_copywriting, daemon=True)
        self.trends_copywriting_thread.start()
        logger.info("动态文案任务线程已启动")
    
    def start_all(self):
        """启动所有任务"""
        self.start_schedule_thread()
        self.start_trends_copywriting()
        self.start_idle_time_task_thread()
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
