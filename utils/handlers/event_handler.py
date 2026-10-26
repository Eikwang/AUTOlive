# -*- coding: UTF-8 -*-
"""
事件处理器模块

处理各种事件
"""

import os
import random
import time
import traceback
from collections import deque
from ..error_handler import handle_error, safe_execute
from ..base import logger


class EventHandler:
    """事件处理器"""

    def __init__(self, my_handle):
        """
        初始化事件处理器

        Args:
            my_handle: My_handle 实例
        """
        self.my_handle = my_handle
        # 即时答谢滑动窗口（ENG E-2 限流：每分钟上限，超限转周期缓存或丢弃）
        self._instant_thanks_window = deque()
        # 顺序文案游标（thanks.<type>_random=false 时按顺序取用）
        self._copy_cursors = {"gift": 0, "entrance": 0, "follow": 0}
    
    # ---------------- 答谢生产端（T6 恢复：消费端 timer_handler.py:102-202 在位） ----------------

    def _thanks_username(self, raw_username: str) -> str:
        """答谢用用户名清洗：星号合并 + 特殊字符剔除 + 截断（保留 backup 旧逻辑核心步）。"""
        common = self.my_handle.common
        username = common.merge_consecutive_asterisks(str(raw_username))
        username = common.replace_special_characters(username, "！!@#￥$%^&*_-+/——=()（）【】}|{:;<>~`\\")
        try:
            max_len = int(self.my_handle.config.get("thanks", "username_max_len"))
            if max_len > 0:
                username = username[:max_len]
        except (TypeError, ValueError):
            pass
        return username

    def _thanks_copy(self, type2: str) -> str:
        """按随机/顺序取答谢文案（thanks.<type2>_copy + <type2>_random）。"""
        copy_list = self.my_handle.config.get("thanks", f"{type2}_copy") or []
        # UI-16 二轮验收：str/list 双形态兼容（G7 先例）——存量 config 为 str 时
        # random.choice/索引按字符迭代（播报单字符），先按行拆成列表
        if isinstance(copy_list, str):
            copy_list = [line for line in copy_list.split("\n") if line.strip()]
        if not copy_list:
            return ""
        if self.my_handle.config.get("thanks", f"{type2}_random"):
            return random.choice(copy_list)
        idx = self._copy_cursors.get(type2, 0) % len(copy_list)
        self._copy_cursors[type2] = (idx + 1) % len(copy_list)
        return copy_list[idx]

    def _thanks_dispatch(self, type2: str, message: dict, username: str):
        """答谢通道分发：周期开启→缓存；否则即时合成（每分钟限流，ENG E-2）。

        超限且周期关闭时丢弃本条并告警——答谢是可丢弃的锦上添花，
        不能在答谢风暴中压垮播放队列（百连入场场景）。
        """
        periodic_on = self.my_handle.config.get("thanks", type2, "periodic_trigger", "enable")
        if periodic_on:
            self.my_handle.task_data["thanks"][type2]["data"].append(message)
            return
        now = time.time()
        bucket = self._instant_thanks_window
        bucket.append(now)
        while bucket and now - bucket[0] > 60:
            bucket.popleft()
        try:
            limit = int(self.my_handle.config.get("thanks", "rate_limit_per_min") or 10)
        except (TypeError, ValueError):
            limit = 10
        if len(bucket) > limit:
            logger.warning(f"答谢即时通道超每分钟 {limit} 条上限，丢弃本条：type={type2} user={username}（E-2 限流）")
            return
        self.my_handle.audio_synthesis_handle(message)

    def _thanks_generate(self, type2: str, data: dict, variables: dict):
        """通用答谢文案生成与分发。开关 thanks.<type2>_enable，返回 message 或 None。"""
        try:
            if not self.my_handle.config.get("thanks", f"{type2}_enable"):
                return None
            resp_content = self._thanks_copy(type2)
            if not resp_content:
                logger.warning(f"thanks.{type2}_copy 为空但开关已启用，跳过答谢")
                return None
            username = self._thanks_username(data.get("username", ""))
            variables["username"] = username
            resp_content = self.my_handle.common.brackets_text_randomize(resp_content)
            resp_content = self.my_handle.common.dynamic_variable_replacement(resp_content, variables)

            message = {
                "type": type2,
                "tts_type": self.my_handle.config.get("audio_synthesis_type"),
                "data": self.my_handle.config.get(self.my_handle.config.get("audio_synthesis_type")),
                "config": self.my_handle.config.get("filter"),
                "username": username,
                "content": resp_content,
            }
            self._thanks_dispatch(type2, message, username)
            return message
        except Exception as e:
            logger.error(f"答谢文案生成失败（{type2}，不中断主流程）：{e}")
            return None

    def gift_handle(self, data):
        """
        礼物处理

        Args:
            data (dict): 数据
        """
        try:
            username = data["username"]
            gift_name = data["gift_name"]
            # 弹幕链路（danmaku_consumer）礼物载荷键为 num（webui 侧为 gift_num），
            # 兼容取值避免 KeyError 使礼物积分计分与礼物表记录双双失效
            gift_num = data.get("gift_num", data.get("num", 1))
            total_price = data.get("total_price", 0)

            logger.info(f"[{username}]送出了 {gift_num} 个 {gift_name}")

            # 0、积分机制运转（C1：礼物行为计分；写库失败不中断礼物主流程）
            self.my_handle.integral_handle("gift", data)

            # 记录数据库
            if self.my_handle.config.get("database", "gift_enable"):
                from datetime import datetime
                insert_data_sql = '''
                INSERT INTO gift (username, gift_name, gift_num, unit_price, total_price, ts) VALUES (?, ?, ?, ?, ?, ?)
                '''
                self.my_handle.db.execute(insert_data_sql, (username, gift_name, gift_num, data.get("unit_price", 0), total_price, datetime.now()))

            # 1、礼物答谢（T6 恢复：最低价格门槛 + 变量替换，即时/周期双通道 + E-2 限流）
            # 动作编排订阅点（/autoplan 2026-10-21 批次）：在答谢门槛之前——接管判定
            # 先于答谢生成；接管=true 跳过 _thanks_generate（短路在通道分流前，周期+
            # 即时双通道一并覆盖）。编排 tts 音轨经注入引用复用原生 _thanks_generate，
            # E-2 限流窗口共享不绕过（义务 CS-1/WP-3/WP-4）。
            # ===== 关键词识别中枢（2026-10-25 核心架构调整，flowchartTD Keyword 节点）=====
            # 礼物源单次识别：按键映射礼物组 + 礼物点歌映射 + 编排接管判定，
            # 产出 rec 分发消费（原实现编排 on_gift 与点歌映射各自独立匹配）。
            rec = self.my_handle.keyword_recognize(data, "gift")

            if rec.choreo_takeover:
                logger.info(f"[{username}] 礼物 {gift_name} 由动作编排接管（画面序列含 tts 答谢）")
            # 按键映射礼物触发（2026-10-25 中枢接线：原生产链缺失——key_mapping_handle
            # 礼物分支仅有测试直调，现中枢命中组直接分派执行；降级未执行不影响答谢）
            if rec.keymap_gift_groups:
                try:
                    self.my_handle.key_mapping_handle("礼物", data, hub_result=rec)
                except Exception as _km_err:
                    logger.warning(f"按键映射礼物触发异常（降级忽略）：{_km_err}")
            # 点歌触发（/autoplan 2026-10-23 批次 R2/R5）：编排之后、答谢之前——
            # 命中=点歌播报+播放（答谢照旧在下方走），未命中=仅答谢（语义不变）。
            # 异常已在 choose_song_gift_trigger 内降级，不中断礼物主流程。
            if rec.gift_song:
                try:
                    self.my_handle.choose_song_gift_trigger(data, hub_result=rec)
                except Exception as _song_err:
                    logger.warning(f"礼物点歌触发异常（降级仅答谢）：{_song_err}")
            try:
                lowest_price = float(self.my_handle.config.get("thanks", "lowest_price") or 0)
            except (TypeError, ValueError):
                lowest_price = 0
            if not rec.choreo_takeover and float(total_price or 0) >= lowest_price:
                self._thanks_generate("gift", data, {
                    "gift_name": gift_name,
                    "gift_num": gift_num,
                    "unit_price": data.get("unit_price", 0),
                    "total_price": total_price,
                })
        except Exception as e:
            handle_error(e, {'module': 'handler', 'function': 'unknown'})
            logger.error(f"礼物处理失败：{e}")

    def entrance_handle(self, data):
        """
        入场处理

        Args:
            data (dict): 数据
        """
        try:
            username = data["username"]

            logger.info(f"[{username}]进入了直播间")

            # 0、积分机制运转（C1：入场行为计分；写库失败不中断入场主流程）
            self.my_handle.integral_handle("entrance", data)

            # 记录数据库
            if self.my_handle.config.get("database", "entrance_enable"):
                from datetime import datetime
                insert_data_sql = '''
                INSERT INTO entrance (username, ts) VALUES (?, ?)
                '''
                self.my_handle.db.execute(insert_data_sql, (username, datetime.now()))

            # 1、入场答谢（T6 恢复：即时/周期双通道 + E-2 限流）
            self._thanks_generate("entrance", data, {})
        except Exception as e:
            logger.error(traceback.format_exc())
            logger.error(f"入场处理失败：{e}")

    def follow_handle(self, data):
        """
        关注处理

        Args:
            data (dict): 数据
        """
        try:
            username = data["username"]

            logger.info(f"[{username}]关注了直播间")

            # 0、积分机制运转（C1：关注行为计分，新增规则键缺键走安全默认；写库失败不中断主流程）
            self.my_handle.integral_handle("follow", data)

            # 1、关注答谢（T6 恢复：即时/周期双通道 + E-2 限流）
            self._thanks_generate("follow", data, {})
        except Exception as e:
            logger.error(traceback.format_exc())
            logger.error(f"关注处理失败：{e}")

    def like_handle(self, data):
        """
        点赞处理（2026-10-24 审查批次：仅计数不播报）

        经 like 遗忘定时器限流后到达；不生成语音、不走 LLM。
        数据经 data_analysis 的点赞趋势观察由日志承载（无 like 表，不入库）。

        Args:
            data (dict): 数据（platform/username/count/content）
        """
        try:
            logger.info(f"[{data.get('username', '有人')}]点赞了直播 x{data.get('count', 1)}")
        except Exception as e:
            logger.error(f"点赞处理失败：{e}")

    def room_stats_handle(self, data):
        """
        房间信息处理（2026-10-24 审查批次：更新内部人气数据，不播报）

        经 room_stats 遗忘定时器限流后到达；更新 my_global.last_liveroom_data
        供定时任务变量替换（{user_num} 等）等内部消费。不写库、不合成音频。

        Args:
            data (dict): 数据（platform/room_id/stats——stats 为去掉 type 的统计键值）
        """
        try:
            from utils import my_global
            stats = data.get("stats") or {}
            snapshot = dict(stats) if isinstance(stats, dict) else {}
            snapshot.setdefault("platform", data.get("platform", ""))
            my_global.last_liveroom_data = snapshot
            logger.debug(f"[房间信息] {snapshot}")
        except Exception as e:
            logger.error(f"房间信息处理失败：{e}")

    def schedule_handle(self, data):
        """
        定时任务处理
        
        Args:
            data (dict): 数据
        """
        try:
            username = data.get("username", "系统")
            content = data.get("content", "")

            logger.info(f"[定时任务] {content}")

            # 关键词识别中枢（2026-10-25 核心架构调整，flowchartTD Timed→Keyword→Queue）：
            # 入队前统一识别（编排关键词订阅，中枢内部全兜底）
            self.my_handle.keyword_recognize(data, "schedule")

            # 构建消息
            message = {
                "type": "schedule",
                "tts_type": self.my_handle.config.get("audio_synthesis_type"),
                "data": self.my_handle.config.get(self.my_handle.config.get("audio_synthesis_type")),
                "config": self.my_handle.config.get("filter"),
                "username": username,
                "content": content
            }
            
            # 音频合成
            self.my_handle.audio_synthesis_handle(message)
        except Exception as e:
            logger.error(traceback.format_exc())
            logger.error(f"定时任务处理失败：{e}")
    
    def idle_time_task_handle(self, data):
        """
        闲时任务处理
        
        Args:
            data (dict): 数据
        """
        try:
            username = data.get("username", "系统")
            content = data.get("content", "")
            type = data.get("type", "text")

            logger.info(f"[闲时任务] {content}")

            # 关键词识别中枢（2026-10-25 核心架构调整，flowchartTD Idle→Keyword→Queue）：
            # 入队前统一识别（编排关键词订阅，中枢内部全兜底）
            self.my_handle.keyword_recognize(data, "idle_time_task")

            if type == "text":
                # 构建消息
                message = {
                    "type": "idle_time_task",
                    "tts_type": self.my_handle.config.get("audio_synthesis_type"),
                    "data": self.my_handle.config.get(self.my_handle.config.get("audio_synthesis_type")),
                    "config": self.my_handle.config.get("filter"),
                    "username": username,
                    "content": content
                }
                
                # 音频合成
                self.my_handle.audio_synthesis_handle(message)
            elif type == "local_audio":
                logger.info(f'[闲时任务-音频] {data["file_path"]}')
                
                message = {
                    "type": "idle_time_task",
                    "tts_type": self.my_handle.config.get("audio_synthesis_type"),
                    "data": self.my_handle.config.get(self.my_handle.config.get("audio_synthesis_type")),
                    "config": self.my_handle.config.get("filter"),
                    "username": username,
                    "content": content,
                    "content_type": type,
                    "file_path": os.path.abspath(data["file_path"])
                }
                
                self.my_handle.audio_synthesis_handle(message)
        except Exception as e:
            logger.error(traceback.format_exc())
            logger.error(f"闲时任务处理失败：{e}")