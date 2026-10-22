# -*- coding: UTF-8 -*-
"""
My_handle 主类

重构后使用各个 handler 模块
"""

import os
import sys
import threading
import json
import random
import difflib
from datetime import datetime
import traceback
from .error_handler import handle_error, safe_execute
import importlib
import asyncio

import copy
import re
from functools import partial


from .config import Config
from .common import Common
from .audio import Audio
from .gpt_model.gpt import GPT_MODEL
from .base import logger
from .db import SQLiteDB, ensure_integral_unique_index
from .my_translate import My_Translate

# 导入 handler 模块
from .handlers.audio_handler import AudioHandler
from .handlers.chat_handler import ChatHandler
from .handlers.comment_handler import CommentHandler
from .handlers.timer_handler import TimerHandler
from .handlers.feature_handler import FeatureHandler
from .handlers.event_handler import EventHandler
from .handlers.utils_handler import UtilsHandler


"""
	___ _                       
	|_ _| | ____ _ _ __ ___  ___ 
	 | || |/ / _` | '__/ _ \/ __|
	 | ||   < (_| | | | (_) \__ \
	|___|_|\_\\__,_|_|  \___/|___/

"""
class SingletonMeta(type):
    _instances = {}
    _lock = threading.Lock()

    def __call__(cls, *args, **kwargs):
        with cls._lock:
            if cls not in cls._instances:
                cls._instances[cls] = super(SingletonMeta, cls).__call__(*args, **kwargs)
            return cls._instances[cls]


class My_handle(metaclass=SingletonMeta):
    common = None
    config = None
    audio = None
    my_translate = None
    
    # 是否在数据处理中
    is_handleing = 0

    # 异常报警数据
    abnormal_alarm_data = {
        "platform": {
            "error_count": 0
        },
        "llm": {
            "error_count": 0
        },
        "tts": {
            "error_count": 0
        },
        "svc": {
            "error_count": 0
        },
        "visual_body": {
            "error_count": 0
        },
        "other": {
            "error_count": 0
        }
    }

    # 直播消息存储(入场、礼物、弹幕)，用于限定时间内的去重
    live_data = {
        "comment": [],
        "gift": [],
        "entrance": [],
    }

    # 各个任务运行数据缓存 暂时用于 限定任务周期性触发
    task_data = {
        "read_comment": {
            "data": [],
            "time": 0
        },
        "local_qa": {
            "data": [],
            "time": 0
        },
        "thanks": {
            "gift": {
                "data": [],
                "time": 0
            },
            "entrance": {
                "data": [],
                "time": 0
            },
            "follow": {
                "data": [],
                "time": 0
            },
        }
    }

    # 答谢板块文案数据临时存储
    thanks_entrance_copy = []
    thanks_gift_copy = []
    thanks_follow_copy = []

    def __init__(self, config_path):
        logger.info("初始化My_handle...")

        try:
            if My_handle.common is None:
                My_handle.common = Common()
            if My_handle.config is None:
                My_handle.config = Config(config_path)
            if My_handle.audio is None:
                My_handle.audio = Audio(config_path)
            if My_handle.my_translate is None:
                My_handle.my_translate = My_Translate(config_path)

            self.proxy = None
            
            # 数据丢弃部分相关的实现
            self.data_lock = threading.Lock()
            self.timers = {}

            self.db = None

            # 设置会话初始值
            self.session_config = None
            self.sessions = {}
            self.current_key_index = 0

            # 点歌模块
            self.choose_song_song_lists = None

            self.custom_llm = None

            self.chat_type_list = ["custom_llm", "fastrag", "local_qa"]

            # 初始化 handler 模块
            self.audio_handler = AudioHandler(self)
            self.chat_handler = ChatHandler(self)
            self.comment_handler = CommentHandler(self)
            self.timer_handler = TimerHandler(self)
            self.feature_handler = FeatureHandler(self)
            self.event_handler = EventHandler(self)
            self.utils_handler = UtilsHandler(self)

            # 配置加载
            self.config_load()

            # 动作编排器（/autoplan 2026-10-21 批次 T1）：全部依赖就绪后装配
            # （config/event_handler/audio.edtalk_client 均已在位）。enable=false
            # 或 EDTalk 客户端缺失时 worker 不启动，订阅点短路直通——
            # 关闭=全链零行为变化（义务"总开关"）。
            try:
                from utils.orchestration import ActionChoreographer, register_choreographer
                self.choreographer = ActionChoreographer(
                    config=My_handle.config,
                    edtalk_client=getattr(My_handle.audio, "edtalk_client", None),
                    audio_enqueue=self.audio_synthesis_handle,
                    thanks_generate=self.event_handler._thanks_generate,
                )
                register_choreographer(self.choreographer)
            except Exception as e:
                logger.error(
                    f"动作编排器装配失败（功能关闭，主链不受影响）：problem=编排器未启用；"
                    f"cause={type(e).__name__}: {e}；fix=检查 config choreography 节与依赖后重启"
                )

            logger.info(f"配置数据加载成功。")

            # 启动定时器
            self.start_timers()
        except Exception as e:
            handle_error(e, {'module': 'my_handle', 'function': 'config_load'})     

    # 配置加载
    def config_load(self):
        # 设置GPT_Model全局模型列表（基础功能 2026-10-22：三通道按配置初始化）
        GPT_MODEL.set_model_config("custom_llm", My_handle.config.get("custom_llm"))
        GPT_MODEL.set_model_config("fastrag", My_handle.config.get("fastrag"))

        # 启动警告（spec R2-1/DX CAP6-8：通道可用性与配置开关错配时明示+fix 指引）
        _chat_type = My_handle.config.get("chat_type")
        if _chat_type == "fastrag" and not (My_handle.config.get("fastrag") or {}).get("enable"):
            logger.warning(
                "chat_type=fastrag 但 fastrag.enable=false——知识库服务不会随系统启动，"
                "运行期 /query 将失败并走本地问答托底；fix=启用 WebUI 知识库页开关或改 chat_type")
        if _chat_type == "local_qa" and not My_handle.config.get("local_qa", "enable"):
            logger.warning(
                "chat_type=local_qa 但 local_qa.enable=false——本地问答词库不会匹配，"
                "每条弹幕将无回复；fix=启用 WebUI 本地问答开关或改 chat_type")

        # uc2/LLM 翻译接线（切片 3 收尾）：GPT 通道就绪后注入翻译回调——
        # 译文目标语言跟随翻译模块既有 to_lang 配置（baidu.to_lang 缺省 zh）
        if My_handle.my_translate is not None:
            to_lang = "zh"
            try:
                to_lang = (My_handle.config.get("translate", "baidu", "to_lang") or "zh")
            except Exception:
                pass

            def _llm_translate(text, _to=to_lang):
                from utils.gpt_model.custom_llm import Custom_LLM
                llm = Custom_LLM(My_handle.config.get("custom_llm"))
                prompt = (f"将以下文本翻译为{_to}，只输出译文，不要任何解释：\n{text}")
                return llm.get_resp_sync({"prompt": prompt})

            My_handle.my_translate.set_llm_translator(_llm_translate)
            logger.info("自定义LLM翻译回调已注入（uc2 默认首选）")

        # 聊天相关类实例化
        self.handle_chat_type()

        # 日志文件路径
        self.log_file_path = "./log/log-" + My_handle.common.get_bj_time(1) + ".txt"
        if os.path.isfile(self.log_file_path):
            logger.info(f'{self.log_file_path} 日志文件已存在，跳过')
        else:
            with open(self.log_file_path, 'w') as f:
                f.write('')
                logger.info(f'{self.log_file_path} 日志文件已创建')

        # 生成弹幕文件
        self.comment_file_path = "./log/comment-" + My_handle.common.get_bj_time(1) + ".txt"
        if os.path.isfile(self.comment_file_path):
            logger.info(f'{self.comment_file_path} 弹幕文件已存在，跳过')
        else:
            with open(self.comment_file_path, 'w') as f:
                f.write('')
                logger.info(f'{self.comment_file_path} 弹幕文件已创建')

        try:
            # 数据库
            self.db = SQLiteDB(My_handle.config.get("database", "path"))
            logger.info(f'创建数据库:{My_handle.config.get("database", "path")}')

            # 创建弹幕表
            create_table_sql = '''
            CREATE TABLE IF NOT EXISTS danmu (
                username TEXT NOT NULL,
                content TEXT NOT NULL,
                ts DATETIME NOT NULL
            )
            '''
            self.db.execute(create_table_sql)
            logger.debug('创建danmu（弹幕）表')

            create_table_sql = '''
            CREATE TABLE IF NOT EXISTS entrance (
                username TEXT NOT NULL,
                ts DATETIME NOT NULL
            )
            '''
            self.db.execute(create_table_sql)
            logger.debug('创建entrance（入场）表')

            create_table_sql = '''
            CREATE TABLE IF NOT EXISTS gift (
                username TEXT NOT NULL,
                gift_name TEXT NOT NULL,
                gift_num INT NOT NULL,
                unit_price REAL NOT NULL,
                total_price REAL NOT NULL,
                ts DATETIME NOT NULL
            )
            '''
            self.db.execute(create_table_sql)
            logger.debug('创建gift（礼物）表')

            create_table_sql = '''
            CREATE TABLE IF NOT EXISTS integral (
                platform TEXT NOT NULL,
                username TEXT NOT NULL,
                uid TEXT NOT NULL,
                integral INT NOT NULL,
                view_num INT NOT NULL,
                sign_num INT NOT NULL,
                last_sign_ts DATETIME NOT NULL,
                total_price INT NOT NULL,
                last_ts DATETIME NOT NULL,
                UNIQUE(platform, username, uid)
            )
            '''
            self.db.execute(create_table_sql)
            logger.debug('创建integral（积分）表')

            # A1（/autoplan 放行前置）：integral 表幂等迁移——补 UNIQUE(platform, username, uid)
            # 唯一索引（UPSERT 计分写入依赖该索引，见 utils/db.ensure_integral_unique_index）
            ensure_integral_unique_index(self.db)
        except Exception as e:
            logger.error(traceback.format_exc())
            logger.error(f'数据库 {My_handle.config.get("database", "path")} 创建失败，请查看日志排查问题！！！')


    # 重载config
    def reload_config(self, config_path):
        My_handle.config = Config(config_path)
        My_handle.audio.reload_config(config_path)
        My_handle.my_translate.reload_config(config_path)
        self.config_load()


    # 委托给 handler 模块的方法
    
    # 音频相关
    def clear_queue(self, type: str = "message_queue"):
        return self.audio_handler.clear_queue(type)
    
    def stop_audio(self, type: str = "pygame", mixer_normal: bool = True, mixer_copywriting: bool = True):
        return self.audio_handler.stop_audio(type, mixer_normal, mixer_copywriting)
    
    def audio_synthesis_handle(self, data_json):
        return self.audio_handler.audio_synthesis_handle(data_json)
    
    def get_audio_info(self):
        return self.audio_handler.get_audio_info()
    
    def is_audio_queue_empty(self):
        return self.audio_handler.is_audio_queue_empty()
    
    def is_queue_less_or_greater_than(self, type: str = "message_queue", less: int = None, greater: int = None):
        return self.audio_handler.is_queue_less_or_greater_than(type, less, greater)
    
    # 聊天相关
    def get_chat_model(self, chat_type, config):
        return self.chat_handler.get_chat_model(chat_type, config)

    def handle_chat_type(self):
        return self.chat_handler.handle_chat_type()

    def llm_handle(self, chat_type, data_json):
        return self.chat_handler.llm_handle(chat_type, data_json)

    # ==================== 基础功能共享层（2026-10-22 批次，ENG E-1） ====================
    # 弹幕主路径（comment_handle）与语音路径（talk_handle）共用的 LLM 输入构建、
    # 语言过滤与回复模板——单点实现防双路径行为分裂（dual-code-path 教训）。

    def build_llm_input(self, username, content):
        """共享 LLM 输入构建器：弹幕模板→提示词前后缀→语言回复指令统一拼装。

        调用点：comment_handle 与 talk_handle 两处 data_json 构建同调（ENG E-1）。
        各环节异常均降级原文（DX CAP6-9：warning 含异常+原模板串+占位符对照）。

        Args:
            username (str): 弹幕用户名
            content (str): 违禁/格式检查后的弹幕内容

        Returns:
            str: 发送给 LLM 的最终 prompt
        """
        templated = content
        try:
            # 1. 弹幕模板（G5）
            if self.config.get("comment_template", "enable"):
                template = self.config.get("comment_template", "copywriting")
                if template:
                    try:
                        templated = template.format(username=username, comment=content)
                    except (KeyError, IndexError, ValueError) as e:
                        logger.warning(
                            f"弹幕模板 format 失败（降级原文）：{e}；原模板={template!r}；"
                            f"占位符对照：合法变量为 {{username}}/{{comment}}")
                        templated = content
        except Exception as e:
            logger.warning(f"弹幕模板处理异常（降级原文）：{e}")
            templated = content

        # 2. 提示词前后缀（G4，空串不拼）
        before_prompt = self.config.get("before_prompt") or ""
        after_prompt = self.config.get("after_prompt") or ""
        prompt = before_prompt + templated + after_prompt

        # 3. 语言回复指令（native F2：yue/ko 需显式指令，防普通话文本经 auto_yue/ko 合成失真）
        need_lang = self.config.get("need_lang") or "none"
        lang_instruction = {"yue": "请用粤语回复。", "ko": "请用韩语回复。"}.get(need_lang)
        if lang_instruction:
            prompt = prompt + lang_instruction
        return prompt

    def apply_reply_template(self, resp_content, username):
        """回复模板应用（G6/G7）：多条模板随机选一条套用，异常降级原文。

        调用点：两路径在违禁过滤+翻译之后、写评论日志之前（ENG Section 4 顺序裁决）。

        Args:
            resp_content (str): LLM 回复原文（已过违禁/翻译）
            username (str): 弹幕用户名（超 username_max_len 截断）

        Returns:
            str: 套模板后的回复文本
        """
        try:
            if not self.config.get("reply_template", "enable"):
                return resp_content
            copywriting = self.config.get("reply_template", "copywriting")
            if not copywriting:
                return resp_content
            # str 与 list 双形态兼容（G7：存量 config 为 str，前端按数组转换）
            if isinstance(copywriting, str):
                templates = [t for t in copywriting.split("\n") if t.strip()]
            elif isinstance(copywriting, list):
                templates = [str(t) for t in copywriting if str(t).strip()]
            else:
                return resp_content
            if not templates:
                return resp_content

            import random
            template = random.choice(templates)

            # username_max_len 截断（超长加省略号）
            max_len = self.config.get("reply_template", "username_max_len")
            try:
                max_len = int(max_len) if max_len not in (None, "") else None
            except (TypeError, ValueError):
                max_len = None
            display_username = username or ""
            if max_len and max_len > 0 and len(display_username) > max_len:
                display_username = display_username[:max_len] + "…"

            try:
                return template.format(username=display_username, data=resp_content)
            except (KeyError, IndexError, ValueError) as e:
                logger.warning(
                    f"回复模板 format 失败（降级原文）：{e}；原模板={template!r}；"
                    f"占位符对照：合法变量为 {{username}}/{{data}}")
                return resp_content
        except Exception as e:
            logger.warning(f"回复模板处理异常（降级原文）：{e}")
            return resp_content

    def lang_filter_pass(self, content):
        """回复语言过滤（G3，双路径共用）：need_lang≠none 时检测弹幕语言。

        粤语以粤字特征集判定（粤先于中文，spec C5）；不匹配丢弃+debug log；
        每 50 条一次 info 级分类计数汇总（DX medium：默认日志级别下盲区可观测）。

        Args:
            content (str): 弹幕内容

        Returns:
            bool: True=放行（need_lang=none 或语言匹配）；False=丢弃
        """
        need_lang = self.config.get("need_lang") or "none"
        if need_lang == "none":
            return True
        detected = self.common.lang_check(content)
        allowed = {"zh": "zh", "en": "en", "jp": "ja", "ko": "ko", "yue": "yue"}.get(need_lang)
        if allowed is None:
            return True
        if detected != allowed:
            logger.debug(f"语言过滤丢弃：need_lang={need_lang} 检测={detected} 弹幕={content[:50]!r}")
            return False
        self._lang_filter_count = getattr(self, "_lang_filter_count", 0) + 1
        if self._lang_filter_count % 50 == 0:
            logger.info(f"语言过滤汇总（每50条）：{self.common.lang_detect_stats_summary()}")
        return True

    def llm_stream_handle_and_audio_synthesis(self, chat_type, data_json, type="chat"):
        return self.chat_handler.llm_stream_handle_and_audio_synthesis(chat_type, data_json, type)
    
    def split_by_chinese_punctuation(self, text):
        return self.chat_handler.split_by_chinese_punctuation(text)
    
    def talk_handle(self, data):
        return self.chat_handler.talk_handle(data)
    
    # 弹幕相关
    def comment_check_and_replace(self, content):
        return self.comment_handler.comment_check_and_replace(content)
    
    def prohibitions_handle(self, content):
        return self.comment_handler.prohibitions_handle(content)
    
    def reread_handle(self, data, type=None):
        # A6 修复（2026-10-22 批次）：透传 type（reread_top_priority→999 权重映射保持）
        return self.comment_handler.reread_handle(data, type=type)
    
    def tuning_handle(self, data):
        return self.comment_handler.tuning_handle(data)
    
    def write_to_comment_log(self, content, data):
        return self.comment_handler.write_to_comment_log(content, data)
    
    def comment_handle(self, data):
        return self.comment_handler.comment_handle(data)
    
    # 定时器相关
    def periodic_trigger_data_handle(self):
        return self.timer_handler.periodic_trigger_data_handle()
    
    def start_timers(self):
        return self.timer_handler.start_timers()
    
    def clear_live_data(self, type: str = ""):
        return self.timer_handler.clear_live_data(type)
    
    def process_data(self, data, timer_flag):
        return self.timer_handler.process_data(data, timer_flag)
    
    def process_last_data(self, timer_flag):
        return self.timer_handler.process_last_data(timer_flag)
    
    def get_interval(self, timer_flag):
        return self.timer_handler.get_interval(timer_flag)
    
    # 功能相关
    def find_answer(self, question, file_path, similarity=0.6):
        return self.feature_handler.find_answer(question, file_path, similarity)
    
    def find_similar_answer(self, question, file_path, similarity=0.6):
        return self.feature_handler.find_similar_answer(question, file_path, similarity)
    
    def load_data_from_file(self, file_path):
        return self.feature_handler.load_data_from_file(file_path)
    
    def local_qa_handle(self, data):
        return self.feature_handler.local_qa_handle(data)
    
    def choose_song_handle(self, data):
        return self.feature_handler.choose_song_handle(data)

    def integral_handle(self, type, data):
        return self.feature_handler.integral_handle(type, data)
    
    def key_mapping_handle(self, type, data):
        return self.feature_handler.key_mapping_handle(type, data)

    def blacklist_handle(self, data):
        return self.feature_handler.blacklist_handle(data)
    
    
    # 事件相关
    def gift_handle(self, data):
        return self.event_handler.gift_handle(data)
    
    def entrance_handle(self, data):
        return self.event_handler.entrance_handle(data)
    
    def follow_handle(self, data):
        return self.event_handler.follow_handle(data)
    
    def schedule_handle(self, data):
        return self.event_handler.schedule_handle(data)
    
    def idle_time_task_handle(self, data):
        return self.event_handler.idle_time_task_handle(data)

    def abnormal_alarm_handle(self, type):
        return self.event_handler.abnormal_alarm_handle(type)
    
    # 工具相关
    def webui_show_chat_log_callback(self, data_type: str, data: dict, resp_content: str):
        return self.utils_handler.webui_show_chat_log_callback(data_type, data, resp_content)
    
    def get_room_id(self):
        return self.utils_handler.get_room_id()

    def get_a_local_audio_and_audio_play(self, type, data):
        return self.utils_handler.get_a_local_audio_and_audio_play(type, data)
    
    def get_a_serial_send_data_and_send(self, type, data):
        return self.utils_handler.get_a_serial_send_data_and_send(type, data)
    
    def connect_serial_and_send_data(self, data):
        return self.utils_handler.connect_serial_and_send_data(data)
    
    def get_serial_config(self):
        return self.utils_handler.get_serial_config()
    
    def get_a_img_path_and_send(self, type, data):
        return self.utils_handler.get_a_img_path_and_send(type, data)
    
    def keyword_handle_trigger(self, type, data):
        return self.utils_handler.keyword_handle_trigger(type, data)
    
    def gift_handle_trigger(self, type, data):
        return self.utils_handler.gift_handle_trigger(type, data)
    
    def is_data_repeat_in_limited_time(self, data, type):
        return self.utils_handler.is_data_repeat_in_limited_time(data, type)