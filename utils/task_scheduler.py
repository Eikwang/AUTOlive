"""
任务调度模块
提供定时任务、随机任务调度、闲时任务等功能
"""

import asyncio
import copy
import os
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
    
    def _build_time_desc(self) -> str:
        """当前时间文案描述（凌晨X点Y分…，全时段覆盖）"""
        hour, min = self.common.get_bj_time(6)
        if 0 <= hour < 6:
            return f"凌晨{hour}点{min}分"
        if 6 <= hour < 9:
            return f"早晨{hour}点{min}分"
        if 9 <= hour < 12:
            return f"上午{hour}点{min}分"
        if hour == 12:
            return f"中午{hour}点{min}分"
        if 13 <= hour < 18:
            return f"下午{hour - 12}点{min}分"
        if 18 <= hour < 20:
            return f"傍晚{hour - 12}点{min}分"
        return f"晚上{hour - 12}点{min}分"

    def _build_variables(self) -> dict:
        """
        构建系统任务公共变量表（定时任务/闲时任务共用）

        - {time}：当前时间文案描述（报时）
        - {user_num}：当前直播间用户数量（监听端 ROOM_STATS 统一契约键
          viewer_count，全平台生效；未就绪容错 "N"）
        - {last_username}：最近一个互动的用户名（timer_handler.process_data
          互动记录；黑名单/系统自源/room_stats 不记录）

        Returns:
            dict: 变量名→值
        """
        user_num = "N"
        liveroom_data = my_global.last_liveroom_data
        if isinstance(liveroom_data, dict):
            user_num = liveroom_data.get("viewer_count", "N")
        return {
            "time": self._build_time_desc(),
            "user_num": user_num,
            "last_username": my_global.last_username_list[-1] if my_global.last_username_list else "未知用户",
        }

    @staticmethod
    def _replace_variables(text: str, variables: dict) -> str:
        """逐变量替换（str.format → str.replace，2026-10-31 定时任务审查批次）：
        文案含孤立花括号/未知占位符时 format 抛 KeyError/ValueError/IndexError
        且异常可打死调度线程；replace 语义=未知占位符原样保留，与答谢链
        dynamic_variable_replacement 同口径。"""
        content = str(text)
        for var, value in variables.items():
            content = content.replace("{" + var + "}", str(value))
        return content

    def _emit_copy_task(self, copy_list, source_label: str = "定时任务"):
        """
        定时任务/任务组共用执行体：从文案列表随机取一条→变量替换→入合成队列
        （经 process_data 直通分发：系统生成消息不走限流窗口）

        Args:
            copy_list: 文案列表
            source_label: 消息 username 标识

        Returns:
            str: 替换后的文案；列表为空返回 None
        """
        if not isinstance(copy_list, list) or not copy_list:
            logger.warning(f"[{source_label}] 文案列表为空，本轮跳过")
            return None
        random_copy = random.choice(copy_list)

        content = self._replace_variables(random_copy, self._build_variables())

        content = self.common.brackets_text_randomize(content)

        data = {"platform": self.platform, "username": source_label, "content": content}

        logger.info(f"{source_label}：{content}")

        if self.my_handle:
            self.my_handle.process_data(data, "schedule")
        return content

    def schedule_task(self, index: int):
        """
        执行存量单任务（按索引，config.schedule list 消费——任务组接管后不再调用）

        Args:
            index: 任务索引
        """
        logger.debug("定时任务执行中...")
        schedule_config = self.config.get("schedule")
        if not isinstance(schedule_config, list) or index >= len(schedule_config):
            logger.warning(f"定时任务#{index} 不存在（配置已变更），本轮跳过")
            return None
        return self._emit_copy_task(schedule_config[index].get("copy"), "定时任务")

    @staticmethod
    def _register_random_job(tag, min_seconds, max_seconds, task_fn, run_now: bool = True):
        """
        注册自重排随机周期任务（H2 调度语义）：先执行一次 task_fn，随后按
        min~max 秒随机间隔重排自身——"每触发一次任务，等待随机周期执行下一个"

        Args:
            tag: schedule 库任务标签（唯一；存量任务=int 索引，任务组=f"grp{i}"）
            min_seconds: 最小循环周期（秒）
            max_seconds: 最大循环周期（秒）
            task_fn: 任务执行函数
            run_now: 注册后是否立即执行一次（首次启动注册=True 保持既有语义；
                运行中配置变化重注册=False——改配置不应导致任务立即插播）
        """
        try:
            lo = max(1, int(float(min_seconds)))
            hi = max(1, int(float(max_seconds)))
        except (TypeError, ValueError):
            logger.warning(f"定时任务 tag={tag} 循环周期非法（{min_seconds}/{max_seconds}），跳过注册")
            return
        lo, hi = min(lo, hi), max(lo, hi)

        def job():
            schedule.clear(tag)
            # 在min_seconds和max_seconds之间随机选择下一次任务执行的时间
            next_time = random.randint(lo, hi)

            try:
                task_fn()
            except Exception:
                # 单次执行异常不中断重排（2026-10-31 审查批次：原实现任务异常
                # 沿 run_pending 传播杀死调度线程——全部定时任务静默停摆）
                logger.error(f"定时任务执行异常（tag={tag}，调度不中断）：{traceback.format_exc()}")

            schedule.every(next_time).seconds.do(job).tag(tag)

        if run_now:
            job()
        else:
            schedule.every(random.randint(lo, hi)).seconds.do(job).tag(tag)

    # 配置热同步（2026-11-14 修复）：webui 与 main 各持内存副本且互不同步
    # （config_watcher 未接线，SynthesisMixin 同款结论）——webui 保存的定时/
    # 闲时任务配置 main 永远读进程启动快照，「修改配置无法开启」即此根因。
    # mtime 门控惰性重载：文件未变仅 os.stat 零开销；变化才 Config.reload
    # （类缓存刷新，进程内全部 Config 实例可见，与合成链热同步同语义）
    _disk_config_mtime_seen = 0.0

    def _sync_config_from_disk(self):
        """调度链配置热同步（mtime 门控，未变化零开销）。"""
        try:
            cfg_path = getattr(Config, "_config_path", None) or "config.json"
            mtime = os.path.getmtime(cfg_path)
        except OSError:
            return
        if mtime == TaskScheduler._disk_config_mtime_seen:
            return
        TaskScheduler._disk_config_mtime_seen = mtime
        try:
            if Config.reload(str(cfg_path)):
                logger.info("[config] 检测到 config.json 变化，任务调度链已热重载")
        except Exception:
            logger.error(f"任务调度链配置热重载失败：{traceback.format_exc()}")

    def _schedule_signature(self):
        """调度注册签名（enable/周期/组名变化→重注册）。

        文案列表不参与签名：任务执行期经活引用/按下标重读 config，改文案
        无需重注册；避免改文案触发任务立即插播。
        """
        try:
            groups = self.config.get("schedule_groups") or []
            tasks = self.config.get("schedule") or []
            g_sig = [(str(g.get("name") or ""), g.get("min_interval"), g.get("max_interval"))
                     for g in groups if isinstance(g, dict)]
            t_sig = [(bool(t.get("enable")), t.get("time_min"), t.get("time_max"))
                     for t in tasks if isinstance(t, dict)]
            return repr((g_sig, t_sig))
        except Exception:
            return ""

    def _emit_group_task(self, group_name: str):
        """任务组执行（执行期按组名重读 config——组内文案修改运行期生效）"""
        group = next((g for g in (self.config.get("schedule_groups") or [])
                      if isinstance(g, dict) and str(g.get("name") or "").strip() == group_name), None)
        if group is None:
            logger.warning(f"任务组「{group_name}」已删除，跳过本次执行")
            return
        self._emit_copy_task(group.get("copywriting_list"), "定时任务")

    def _register_schedule_jobs(self, run_now: bool = True):
        """按当前配置注册调度任务（组接管优先，H2 语义不变；先清旧标签防重复）"""
        for tag in getattr(self, "_sched_tags", []):
            try:
                schedule.clear(tag)
            except Exception:
                pass
        tags = []
        try:
            # 任务组接管语义（H2：组循环周期=组内文案触发节奏，任务级周期字段
            # 废弃随迁移）——schedule_groups 非空时组语义接管，存量单任务 list
            # 不再消费（防迁移后双跑）；组无 enable 字段（存在即启用，删除组=停用）
            groups = self.config.get("schedule_groups") or []
            if groups:
                for gi, group in enumerate(groups):
                    if not isinstance(group, dict) or not str(group.get("name") or "").strip():
                        logger.warning(f"任务组#{gi} 缺少组名，跳过注册")
                        continue
                    self._register_random_job(
                        f"grp{gi}", group.get("min_interval"), group.get("max_interval"),
                        lambda gn=str(group.get("name") or "").strip(): self._emit_group_task(gn),
                        run_now=run_now)
                    tags.append(f"grp{gi}")
            else:
                # 存量单任务（enable/time_min/time_max/copy）——逐任务注册
                # （2026-10-31 审查批次：单个任务周期参数非法不影响后续任务注册）
                for index, task in enumerate(self.config.get("schedule") or []):
                    if not isinstance(task, dict) or not task.get("enable"):
                        continue
                    self._register_random_job(
                        index, task.get("time_min"), task.get("time_max"),
                        lambda idx=index: self.schedule_task(idx), run_now=run_now)
                    tags.append(index)
        except Exception:
            logger.error(f"定时任务调度注册失败: {traceback.format_exc()}")
        self._sched_tags = tags

    def _gate_startup_ready(self, source: str):
        """启动就绪门控（2026-11-19 批次）：慢模块（GSV/数字人）就绪前不产出任务。

        快模块线程（定时任务/闲时任务/动态文案）入口阻塞等待——就绪前门控阻断
        任务源头（无需报错降级处理）；超时兜底强制继续（一次性告警在
        readiness.wait_ready 内），单服务挂死不永久卡死快模块。键集合按当前
        config 启用状态动态确定（readiness.required_keys）。
        """
        try:
            from utils.readiness import wait_ready, required_keys, STARTUP_READY_TIMEOUT_S
            wait_ready(required_keys(self.config), STARTUP_READY_TIMEOUT_S)
        except Exception:
            logger.error(f"[{source}] 启动门控异常（放行继续，不阻断）: {traceback.format_exc()}")

    def run_schedule(self):
        """运行定时任务调度（存量单任务 / D-16 任务组，二选一接管）

        2026-11-14 修复：一次性注册改为签名比对动态重注册——webui 修改
        enable/周期保存落盘后，经 _sync_config_from_disk 热重载、签名变化
        即重注册，运行中开关/改周期即时生效（原实现仅启动时注册一次，
        运行中开启任务需重启）。
        """
        # 启动就绪门控（2026-11-19 批次）：先于首次注册——run_now=True 的
        # 立即首跑也不得早于慢模块就绪
        self._gate_startup_ready("定时任务")
        self._sched_tags = []
        self._last_sched_sig = None
        self.is_running = True
        while self.is_running:
            try:
                self._sync_config_from_disk()
                sig = self._schedule_signature()
                if sig != self._last_sched_sig:
                    if self._last_sched_sig is not None:
                        logger.info("定时任务配置变化，重新注册调度")
                    self._register_schedule_jobs(run_now=self._last_sched_sig is None)
                    self._last_sched_sig = sig
                schedule.run_pending()
            except Exception:
                # 调度循环异常不再杀死线程（2026-10-31 审查批次：原实现 while
                # 无保护——job 内未捕获异常即静默停摆全部定时任务）
                logger.error(f"定时任务调度循环异常（已恢复）：{traceback.format_exc()}")
            time.sleep(1)  # 控制每次循环的间隔时间

    def start_schedule_thread(self):
        """启动定时任务线程（常驻——enable/任务存在性判定移入调度循环，
        运行中开启任务即时生效；原实现按启动快照决定是否创建线程，启动时
        无任务则运行中永远无法开启）"""
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
        if tmp is None or tmp == "":
            return []
        # UI-16 二轮验收（任务 10）：str/list 双形态兼容（G7 先例）——存量 config
        # 为 str 时 random.choice/遍历按字符迭代（闲时播报单字符）
        if isinstance(tmp, str):
            tmp = [line for line in tmp.split("\n") if line.strip()]
        return copy.copy(tmp)
    
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
            # 闲时文案：随机/顺序取文案 → 变量替换+brackets 随机段 → 闲时出口合成
            # （2026-10-31 闲时任务审查批次：模板文案含 {time} 与 [1|2|3] 随机段
            # ——原实现原样念出占位符/未随机化，接线与定时任务同源处理）
            copy_content = self._pop_from_list(copywriting_copy_list, "copywriting")
            if copy_content is None:
                return (last_mode, copywriting_copy_list, comment_copy_list, local_audio_path_list)
            copy_content = self._replace_variables(copy_content, self._build_variables())
            copy_content = self.common.brackets_text_randomize(copy_content)
            data = {"platform": self.platform, "username": "闲时任务", "content": copy_content}
            self.my_handle.process_data(data, "idle_time_task")

        elif mode == "comment":
            # 弹幕触发 LLM 模式（2026-11-14 回环驱动重构——设计对齐）：闲时文案
            # 以真实弹幕送进当前监听中的直播间（观众可见），LLM 对话由监听端
            # 收到自发声回环正常触发（username=直播账号昵称，与真实观众弹幕
            # 同链完全拟真）。原 2026-10-27 实现"真实弹幕+本地 process_data
            # 虚拟触发"双路径已退役——内部流通念播"闲时任务说XXX"与设计不符。
            # 发送能力缺失（服务未运行/无监听房间/管线异常）本轮跳过不降级
            # （设计语义：本模式只走真实弹幕；失败告警面在发送回执链接入）。
            comment_content = self._pop_from_list(comment_copy_list, "comment")
            if comment_content is None:
                return (last_mode, copywriting_copy_list, comment_copy_list, local_audio_path_list)
            send_result = self.my_handle.send_live_danmu(comment_content)
            if send_result is None or not send_result.get("submitted"):
                logger.warning(f"[闲时任务] 真实弹幕未提交，本轮跳过"
                               f"（{str((send_result or {}).get('reason_code') or '发送弹幕功能不可用')}）："
                               f"{comment_content[:30]}")

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
        """闲时任务驱动线程（T8 接线：三机制判定 + 闲时窗口触发 do_task）

        2026-11-14 修复：线程常驻启动（enable 门控移入循环）——原实现在此
        读一次 enable 即决定线程生死：启动时未开启则线程不创建，运行中开启
        需重启才生效；且循环以 is_running 为存活条件，定时任务线程未启动时
        该标志恒 False，闲时线程首轮即退出（闲时任务永不触发根因）。
        """

        def run_idle_time_task():
            # 启动就绪门控（2026-11-19 批次）：慢模块就绪前不触发闲时任务
            self._gate_startup_ready("闲时任务")
            logger.info("闲时任务线程运行中...")
            last_mode = 0
            idle_wait = 0  # 两队列机制空闲确认后的等待累计（秒）
            while self.is_running:
                try:
                    time.sleep(5)
                    if self.my_handle is None:
                        continue

                    # 关闭期间不累计闲时（开关运行期生效：webui 保存→调度循环
                    # mtime 热重载→此处实时读到 enable）
                    if not self.config.get("idle_time_task", "enable"):
                        my_global.global_idle_time = 0
                        idle_wait = 0
                        continue

                    # 消息处理中 → 非闲时，重置计数
                    if getattr(self.my_handle, "is_handleing", 0) == 1:
                        my_global.global_idle_time = 0
                        idle_wait = 0
                        continue

                    mechanism = str(self.config.get("idle_time_task", "type") or "")
                    try:
                        idle_min = int(self.config.get("idle_time_task", "idle_time_min") or 30)
                        idle_max = int(self.config.get("idle_time_task", "idle_time_max") or 60)
                        idle_min, idle_max = min(idle_min, idle_max), max(idle_min, idle_max)
                    except (TypeError, ValueError):
                        idle_min, idle_max = 30, 60

                    idle_now = False

                    if "无消息" in mechanism:
                        # 直播间无消息更新闲时：计数器递增（有消息处理时已被重置；
                        # 勾选的互动类型消息到达时经 idle_time_auto_clear 重置
                        # ——2026-10-31 闲时任务审查批次接线 trigger_type 消费）。
                        # 无消息持续时长即 min/max 等待时长（任务书语义）。
                        my_global.global_idle_time += 5
                        idle_now = my_global.global_idle_time >= random.randint(idle_min, idle_max)
                    elif "合成" in mechanism or "消息队列" in mechanism:
                        # 待合成消息队列闲时：队列长度 ≤ 阈值=空闲确认，持续
                        # idle_min~max 随机等待后才执行（2026-10-31 审查批次：
                        # 原实现每 5s 轮询阈值满足即触发——最小/最大闲时时间
                        # 参数对两队列机制零生效，队列持续空时 5s 一条刷屏）
                        # 2026-11-17 闲时触发参数合并批次：阈值统一读
                        # min_msg_queue_len_to_trigger（UI 名「触发闲时最小队列
                        # 长度」）——两队列机制共用同一 config 键，按机制类型
                        # 约束对应队列
                        try:
                            threshold = int(self.config.get("idle_time_task", "min_msg_queue_len_to_trigger") or 1)
                        except (TypeError, ValueError):
                            threshold = 1
                        queue_len = len(getattr(self.my_handle.audio, "message_queue", []) or [])
                        idle_now = queue_len <= threshold
                    elif "播放" in mechanism or "音频队列" in mechanism:
                        # 待播放音频队列闲时（同上：min/max 等待生效）
                        # 2026-11-17 闲时触发参数合并批次：原 min_audio_queue_len_to_trigger
                        # 退役不再读取（存量 config 保留该键仅作兼容，不消费）——
                        # 两队列机制统一读 min_msg_queue_len_to_trigger（「触发闲时
                        # 最小队列长度」），按机制类型约束待播放音频队列
                        try:
                            threshold = int(self.config.get("idle_time_task", "min_msg_queue_len_to_trigger") or 1)
                        except (TypeError, ValueError):
                            threshold = 1
                        queue_len = len(getattr(self.my_handle.audio, "voice_tmp_path_queue", []) or [])
                        idle_now = queue_len <= threshold
                    else:
                        logger.warning(f"未知闲时机制类型：{mechanism}，本轮跳过（E5 单选）")
                        continue

                    if not idle_now:
                        idle_wait = 0  # 空闲条件不满足，重置等待
                        continue

                    # 空闲确认后等待 idle_min~max 随机秒（任务书：机制触发后等待
                    # 多久开始执行，执行过后等待多久再次执行）——无消息机制计时
                    # 已含等待语义（global_idle_time 达阈值即触发），两队列机制
                    # 由 idle_wait 补齐
                    if "无消息" not in mechanism:
                        idle_wait += 5
                        if idle_wait < random.randint(idle_min, idle_max):
                            continue
                        idle_wait = 0

                    logger.info(f"闲时触发（机制={mechanism}，idle={my_global.global_idle_time}s）")
                    last_mode, self.copywriting_copy_list, self.comment_copy_list, self.local_audio_path_list = \
                        self.do_task(last_mode, self.copywriting_copy_list,
                                     self.comment_copy_list, self.local_audio_path_list)
                except Exception:
                    # 单轮异常不杀线程（2026-10-31 审查批次：原 while 无保护——
                    # 任一模式执行异常即闲时任务静默停摆）
                    logger.error(f"闲时任务轮询异常（已恢复）：{traceback.format_exc()}")

        self.idle_time_task_thread = threading.Thread(target=run_idle_time_task, daemon=True)
        self.idle_time_task_thread.start()
        logger.info("闲时任务线程已启动")

    def _is_synth_queue_idle(self) -> bool:
        """动态文案触发门（2026-11-21 用户裁决：触发机制只看待合成消息队列）：
        message_queue 为空即触发——已合成音频躺在待播放队列/数字人播报中
        **不阻止**新任务触发（分段队尾追加+插队让位由分账编组保证，播报中
        的旧内容照常消费）。原三条件闲时门（+待播放队列空+无 EDTalk 在飞）
        退役——旧门会因待播放队列积压长期判定"忙"，长文案任务完成后下一个
        任务要等整段播报结束才触发，触发节奏不透明。

        队列在宿主音频组件（my_handle.audio，2026-11-05 编排优化批次 d4/d21
        同一访问面）上；len() 读列表长度本身原子安全（库内先例：消费循环不
        持锁读长度）。宿主未就绪（my_handle/audio 缺失）= 忙（保守跳过本轮，
        防未初始化窗口期入队）。"""
        try:
            audio = getattr(self.my_handle, "audio", None)
            if audio is None:
                return False
            return len(getattr(audio, "message_queue", []) or []) == 0
        except Exception:
            return False  # 访问面异常=忙（保守跳过，不阻塞任务线程）

    def start_trends_copywriting(self):
        """启动动态文案任务（T9 实装：本地文案→可选 LLM 改写→TTS 播放）"""
        if not self.config.get("trends_copywriting", "enable"):
            return

        # ---------- "关键词|文案"拆分（T3，/autoplan main-20261101-213220 批次） ----------
        _t3_new_format_logged = [False]  # 首次采用新格式 info 提示（CEO C2/F8）

        def _split_kw_copy(entry: str):
            """「关键词|文案」拆分（T3 义务块）。

            分隔符识别=半角"|"与全角"｜"(U+FF5C) 取行内首个（Eng E2：中文输入法
            高频产出全角；义务块的"整行 NFKC 副本定位"以双 find 等价达成，避免
            NFKC 兼容映射的长度漂移）。多个分隔符=首个生效，其余归文案部分。
            返回 (keyword, copy_part)；无分隔符/关键词空/文案部分空 = None
            （向下兼容：整行走既有全链）。关键词侧 NFKC+去空白规范化（义务块）。
            """
            import unicodedata
            idx = entry.find("|")
            fw = entry.find("｜")
            if fw >= 0 and (idx < 0 or fw < idx):
                idx = fw
            if idx < 0:
                return None
            kw = unicodedata.normalize("NFKC", entry[:idx]).strip()
            copy_part = entry[idx + 1:].strip()
            if not kw or not copy_part:
                return None
            return kw, copy_part

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
            # 启动就绪门控（2026-11-19 批次）：慢模块就绪前不生成/入队文案
            self._gate_startup_ready("动态文案")
            logger.info("动态文案任务线程运行中...")
            file_cursor = 0
            copy_cursor = 0

            def _sleep_play_interval():
                """文案播放间隔（单位：秒，任务书口径：完成一次文案生成后，
                进行下次文案生成的间隔时间）——尊重用户设定值；下限 1s
                仅防 0/负值忙转（原 10s 暗置下限覆盖用户配置，语义修正）。
                分片睡眠保证 is_running 停止信号响应。"""
                try:
                    interval = int(self.config.get("trends_copywriting", "play_interval") or 60)
                except (TypeError, ValueError):
                    interval = 60
                sleep_total = max(interval, 1)
                for _ in range(sleep_total):
                    if not self.is_running:
                        break
                    time.sleep(1)

            try:
                while self.is_running:
                    # 触发门第一查（2026-11-21 用户裁决：触发机制只看待合成消
                    # 息队列——队列为空即触发；已合成音频播报中不阻止新任务，
                    # 段间让位由分账编组保证）。开始生成本轮文案前忙 → 跳过本
                    # 轮（不告警不重试，等下个 play_interval 周期再查）
                    if not self._is_synth_queue_idle():
                        logger.info("动态文案本轮跳过：系统忙")
                        _sleep_play_interval()
                        continue
                    # UI-16 批次 R5②/EV-1f：文案来源双链——优先读前端直输 copy 列表
                    # （每项=一条文案）；copy 空时回退存量 folder_path 文件链（迁移期
                    # 兼容：页面迁移完成前存量行为不变；copy 非空即走新链）。
                    # EV-1f：空文案行跳过；全空保持 D-8 空列表静默语义。
                    _copy_raw = self.config.get("trends_copywriting", "copy") or []
                    # UI-16 二轮验收：str/list 双形态（split_lines 保存前存量可为 str）
                    if isinstance(_copy_raw, str):
                        _copy_raw = _copy_raw.split("\n")
                    direct_copy_list = [str(c).strip() for c in _copy_raw if str(c).strip()]
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

                    # T3（/autoplan main-20261101-213220）：「关键词|文案」拆分
                    # 仅作用 config 文案列表链（Eng E1：copywriting 文件夹链散文
                    # 自然出现的"|"不得误拆，默认不启用格式）。
                    t3_keyword = None
                    if direct_copy_list:
                        _split = _split_kw_copy(resp_content)
                        if _split is not None:
                            t3_keyword, resp_content = _split
                            if not _t3_new_format_logged[0]:
                                _t3_new_format_logged[0] = True
                                logger.info(
                                    "动态文案启用「关键词|文案」新格式：关键词部分仅用于"
                                    "触发动作编排（不参与按键映射等其它功能），文案部分"
                                    "照常播放（RUNBOOK 案例一边界声明）")

                    # S-3 注入防护：改写输入仅限本地文案原文+配置内固定提示词，
                    # 不接入任何弹幕/事件变量；原文截断 500 字符防爆 LLM/TTS。
                    # 新链（config 文案列表）与旧链（本地文件）来源性质一致：
                    # 均为配置内固定文案，无观众输入注入点。
                    # T3/Eng E6：截断口径=作用于文案部分（关键词不计入）。
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
                        prompt_change_content = str(prompt_change_content or "").strip()
                        if not prompt_change_content:
                            # 转换内容为空=无有效人设可替换，跳过改写播放原文案
                            # （防裸文案请求退化——原实现拼接空串同样退化）
                            logger.warning("动态文案：提示词转换已开启但内容为空，跳过改写播放原文案")
                        else:
                            # 2026-10-30 审查修复（任务书「提示词转换」语义）：
                            # 转换内容=本次请求的人设提示词，**替换**系统设定的
                            # 人设（custom_llm 的 system 人设提示词 /
                            # fastrag 的 system_role 人设提示词）——原实现拼进
                            # user 消息且系统人设照旧注入，替换语义未落实。
                            # 用户消息=文案原文；失败降级播放原文案（语义不变）。
                            override_key = ("system_prompt" if llm_type == "custom_llm"
                                            else "system_role")
                            rewrite_data = {
                                "username": "系统",
                                "content": resp_content,
                                override_key: prompt_change_content,
                            }
                            try:
                                if self.my_handle is not None and llm_type in (self.my_handle.chat_type_list or []):
                                    rewritten = self.my_handle.chat_handler.llm_handle(
                                        llm_type, rewrite_data
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
                    # type=trends_copywriting（2026-10-31 队列审查批次修复断链）：
                    # 原构造 "copywriting" 不在 filter.priority_mapping——消息按
                    # 未识别类型队尾排队+满则丢弃，UI「动态文案」优先级配置不生效；
                    # 对齐映射表键（queue_manager docstring/UI 行同名）。
                    message = {
                        "type": "trends_copywriting",
                        "tts_type": self.config.get("audio_synthesis_type"),
                        "data": self.config.get(self.config.get("audio_synthesis_type")),
                        "config": self.config.get("filter"),
                        "username": "动态文案",
                        "content": resp_content,
                    }
                    # 触发门第二查（2026-11-21 用户裁决：同第一查，只看待合成
                    # 消息队列）：生成（含 LLM 改写）期间队列转忙 → 丢弃本轮
                    # （不告警不重试；宁可少播一条文案，不挤占待合成队列）
                    if not self._is_synth_queue_idle():
                        logger.info("动态文案本轮跳过：系统忙")
                        _sleep_play_interval()
                        continue
                    if self.my_handle is not None:
                        if t3_keyword:
                            # d4（2026-11-05 编排优化批次）+ T3 语义修正（2026-11-21
                            # 用户裁决批次）：「关键词|文案」的关键词仅用于触发动作
                            # 编排——T3 拆分已把关键词从文案剥离，原传 resp_content
                            #（不含关键词）导致按关键词配置的 dynamic_text 规则恒
                            # no_match（2026-11-21 10:22 实证日志"动态文案未触发
                            # 编排（no_match/未启用）"）。改为关键词优先匹配；未
                            # 命中回退整段文案（保留 d4 内容匹配规则兼容；关键词
                            # 命中即返回，不重复派发）。正交声明无条件适用：
                            # no_match/enable=false/被拒只影响画面不影响文案
                            #（文案照常入播放链）。
                            try:
                                from utils.orchestration import get_choreographer
                                _choreo = get_choreographer()
                                if _choreo is not None:
                                    _accepted = bool(_choreo.on_dynamic_text(t3_keyword))
                                    if not _accepted:
                                        _accepted = bool(_choreo.on_dynamic_text(resp_content))
                                    if _accepted:
                                        logger.info(f"动态文案已触发动作编排（关键词={t3_keyword}）")
                                    else:
                                        # Eng H1：no_match/未启用分支降 debug 防刷屏
                                        logger.debug("动态文案未触发编排（no_match/未启用）")
                                else:
                                    logger.debug("动态文案编排器未装配（跳过画面触发）")
                            except Exception as _t3_err:
                                logger.debug(f"动态文案编排触发异常（忽略，文案照常播放）：{_t3_err}")
                        else:
                            # 向下兼容：未格式化条目走既有全链（hub copywriting 源
                            # 统一识别，子串匹配语义不变）
                            try:
                                self.my_handle.keyword_recognize(
                                    {"content": resp_content}, "copywriting")
                            except Exception as _kw_err:
                                logger.debug(f"动态文案中枢识别异常（忽略）：{_kw_err}")
                        self.my_handle.audio_synthesis_handle(message)
                        logger.info(f"动态文案已入播放链：{source_path}")
                    else:
                        logger.warning("动态文案：my_handle 未就绪，本轮跳过播放")

                    _sleep_play_interval()

            except Exception as e:
                logger.error(f"动态文案任务失败: {traceback.format_exc()}")

        self.trends_copywriting_thread = threading.Thread(target=run_trends_copywriting, daemon=True)
        self.trends_copywriting_thread.start()
        logger.info("动态文案任务线程已启动")
    
    def start_all(self):
        """启动所有任务"""
        # is_running 必须在派发线程前置位：闲时/动态文案线程循环以它为存活
        # 条件——原实现仅在 run_schedule 内置位，定时任务线程未启动时恒为
        # False，闲时任务线程首轮即退出
        self.is_running = True
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
