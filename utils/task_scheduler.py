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
    def _register_random_job(tag, min_seconds, max_seconds, task_fn):
        """
        注册自重排随机周期任务（H2 调度语义）：先执行一次 task_fn，随后按
        min~max 秒随机间隔重排自身——"每触发一次任务，等待随机周期执行下一个"

        Args:
            tag: schedule 库任务标签（唯一；存量任务=int 索引，任务组=f"grp{i}"）
            min_seconds: 最小循环周期（秒）
            max_seconds: 最大循环周期（秒）
            task_fn: 任务执行函数
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

        job()

    def run_schedule(self):
        """运行定时任务调度（存量单任务 / D-16 任务组，二选一接管）"""
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
                        lambda g=group: self._emit_copy_task(g.get("copywriting_list"), "定时任务"))
            else:
                # 存量单任务（enable/time_min/time_max/copy）——逐任务注册
                # （2026-10-31 审查批次：原实现整循环单 try，单个任务周期参数
                # 非法导致后续全部任务不注册）
                for index, task in enumerate(self.config.get("schedule") or []):
                    if not isinstance(task, dict) or not task.get("enable"):
                        continue
                    self._register_random_job(
                        index, task.get("time_min"), task.get("time_max"),
                        lambda idx=index: self.schedule_task(idx))
        except Exception as e:
            logger.error(f"定时任务调度失败: {traceback.format_exc()}")

        self.is_running = True
        while self.is_running:
            try:
                schedule.run_pending()
            except Exception:
                # 调度循环异常不再杀死线程（2026-10-31 审查批次：原实现 while
                # 无保护——job 内未捕获异常即静默停摆全部定时任务）
                logger.error(f"定时任务调度循环异常（已恢复）：{traceback.format_exc()}")
            time.sleep(1)  # 控制每次循环的间隔时间

    def start_schedule_thread(self):
        """启动定时任务线程"""
        schedule_config = self.config.get("schedule") or []
        groups = self.config.get("schedule_groups") or []
        if (any(isinstance(item, dict) and item.get("enable") for item in schedule_config)
                or bool(groups) or self.platform == "dy"):
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
            # 弹幕触发 LLM 模式（2026-10-27 架构调整：对接「发送弹幕」功能）：
            # 闲时文案先调度发送弹幕功能以真实弹幕进直播间（观众可见；守卫链/
            # 审计/dry-run/熔断全链生效，非阻塞提交，回执异步记日志），随后本地
            # 照常触发 LLM 对话（弹幕触发LLM语义保持）——监听端 R39 自联回环
            # 滤除保证自发送弹幕 ≤10s 窗口内不被二次处理；发送能力缺失（服务
            # 未运行/无监听房间/平台未启用）降级为原虚拟弹幕本地触发，功能零损失。
            comment_content = self._pop_from_list(comment_copy_list, "comment")
            if comment_content is None:
                return (last_mode, copywriting_copy_list, comment_copy_list, local_audio_path_list)
            send_result = self.my_handle.send_live_danmu(comment_content)
            if send_result is None or not send_result.get("submitted"):
                logger.info(f"[闲时任务] 真实弹幕未提交"
                            f"（{str((send_result or {}).get('reason_code') or '发送弹幕功能不可用')}）"
                            f"——降级虚拟弹幕本地触发：{comment_content[:30]}")
            data = {"platform": self.platform, "username": "闲时任务",
                    "content": comment_content,
                    "origin": "system-idle"}  # ③ 自源标记：编排跳过（keyword_hub）
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
            idle_wait = 0  # 两队列机制空闲确认后的等待累计（秒）
            while self.is_running:
                try:
                    time.sleep(5)
                    if self.my_handle is None:
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
                        try:
                            threshold = int(self.config.get("idle_time_task", "min_msg_queue_len_to_trigger") or 1)
                        except (TypeError, ValueError):
                            threshold = 1
                        queue_len = len(getattr(self.my_handle.audio, "message_queue", []) or [])
                        idle_now = queue_len <= threshold
                    elif "播放" in mechanism or "音频队列" in mechanism:
                        # 待播放音频队列闲时（同上：min/max 等待生效）
                        try:
                            threshold = int(self.config.get("idle_time_task", "min_audio_queue_len_to_trigger") or 1)
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
            logger.info("动态文案任务线程运行中...")
            file_cursor = 0
            copy_cursor = 0

            try:
                while self.is_running:
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
                    if self.my_handle is not None:
                        if t3_keyword:
                            # T3：格式化条目——关键词**无条件**投编排 on_keyword
                            # （无论命中与否：no_match/enable=false/被拒只影响画面
                            # 不影响文案——正交声明无条件适用，spec R2①）；文案部分
                            # 一律不进 keyword_hub copywriting 源识别（设计文档 Q&A
                            # 最狭义；其它 hub 消费者不接动态文案关键词）。直投
                            # on_keyword 与 hub copywriting 源语义精确等价（hub 该源
                            # 匹配器仅编排消费，keyword_hub.py:17/78），省整段子串扫描。
                            try:
                                from utils.orchestration import get_choreographer
                                _choreo = get_choreographer()
                                if _choreo is not None:
                                    _accepted = bool(_choreo.on_keyword(t3_keyword))
                                    if _accepted:
                                        logger.info(f"动态文案关键词已触发动作编排：{t3_keyword}")
                                    else:
                                        # Eng H1：no_match/未启用分支降 debug 防刷屏
                                        logger.debug(f"动态文案关键词未触发编排（未命中/未启用）：{t3_keyword}")
                                else:
                                    logger.debug("动态文案关键词编排器未装配（跳过画面触发）")
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

                    # 文案播放间隔（单位：秒，任务书口径：完成一次文案生成后，
                    # 进行下次文案生成的间隔时间）——尊重用户设定值；下限 1s
                    # 仅防 0/负值忙转（原 10s 暗置下限覆盖用户配置，语义修正）
                    try:
                        interval = int(self.config.get("trends_copywriting", "play_interval") or 60)
                    except (TypeError, ValueError):
                        interval = 60
                    sleep_total = max(interval, 1)
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
