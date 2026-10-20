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
            try:
                lowest_price = float(self.my_handle.config.get("thanks", "lowest_price") or 0)
            except (TypeError, ValueError):
                lowest_price = 0
            if float(total_price or 0) >= lowest_price:
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
    
    def abnormal_alarm_handle(self, type):
        """
        异常报警处理
        
        Args:
            type (str): 报警类型
            
        Returns:
            bool: True/False
        """
        try:
            self.my_handle.abnormal_alarm_data[type]["error_count"] += 1
            
            if not self.my_handle.config.get("abnormal_alarm", type, "enable"):
                return True
            
            if self.my_handle.config.get("abnormal_alarm", type, "type") == "local_audio":
                # 是否错误数大于 自动重启错误数
                if self.my_handle.abnormal_alarm_data[type]["error_count"] >= self.my_handle.config.get("abnormal_alarm", type, "auto_restart_error_num"):
                    data = {
                        "type": "restart",
                        "api_type": "api",
                        "data": {
                            "config_path": "config.json"
                        }
                    }
                    
                    webui_ip = "127.0.0.1" if self.my_handle.config.get("webui", "ip") == "0.0.0.0" else self.my_handle.config.get("webui", "ip")
                    self.my_handle.common.send_request(f'http://{webui_ip}:{self.my_handle.config.get("webui", "port")}/sys_cmd', "POST", data)
                
                # 是否错误数小于 开始报警错误数，是则不触发报警
                if self.my_handle.abnormal_alarm_data[type]["error_count"] < self.my_handle.config.get("abnormal_alarm", type, "start_alarm_error_num"):
                    return
                
                import random
                path_list = self.my_handle.common.get_all_file_paths(self.my_handle.config.get("abnormal_alarm", type, "local_audio_path"))
                
                # 随机选择列表中的一个元素
                audio_path = random.choice(path_list)
                
                message = {
                    "type": "abnormal_alarm",
                    "tts_type": self.my_handle.config.get("audio_synthesis_type"),
                    "data": self.my_handle.config.get(self.my_handle.config.get("audio_synthesis_type")),
                    "config": self.my_handle.config.get("filter"),
                    "username": "系统",
                    "content": os.path.join(self.my_handle.config.get("abnormal_alarm", type, "local_audio_path"), self.my_handle.common.extract_filename(audio_path, True))
                }
                
                logger.warning(f"【异常报警-{type}】 {self.my_handle.common.extract_filename(audio_path, False)}")
                
                self.my_handle.audio_synthesis_handle(message)
        except Exception as e:
            logger.error(traceback.format_exc())
            logger.error(f"异常报警处理失败：{e}")
            return False
        
        return True