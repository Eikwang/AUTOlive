# -*- coding: UTF-8 -*-
"""
功能处理器模块

处理各种功能模块
"""

import os
import time
import traceback
from datetime import datetime
from ..error_handler import handle_error, safe_execute
from ..base import logger


# ============ 积分链路（C1 链路补全，/autoplan 批准切片 2） ============

# 积分 UPSERT（A1）：依赖 integral 表 UNIQUE(platform, username, uid) 唯一索引
# （迁移见 utils/db.ensure_integral_unique_index，索引缺失时本语句报错）。
# 逐列累加语义：
#   integral     累加（excluded.integral = 本次行为得分）
#   view_num     累加——字段语义=累计观看（入场）次数，入场事件传 1、其余行为传 0；
#                「覆盖或累加」二选一按字段语义定为累加（观看次数天然只增，覆盖会丢历史）
#   sign_num     条件自增——签到事件传 1、其余行为传 0，即仅签到时 +1
#   last_sign_ts 仅签到时覆盖（excluded.sign_num > 0），其余行为保留原值
#   total_price  累加（excluded.total_price = 本次礼物总价，非礼物行为传 0）
#   last_ts      每次行为覆盖
INTEGRAL_UPSERT_SQL = '''
INSERT INTO integral (platform, username, uid, integral, view_num, sign_num, last_sign_ts, total_price, last_ts)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(platform, username, uid) DO UPDATE SET
    integral = integral + excluded.integral,
    view_num = view_num + excluded.view_num,
    sign_num = sign_num + excluded.sign_num,
    last_sign_ts = CASE WHEN excluded.sign_num > 0 THEN excluded.last_sign_ts ELSE last_sign_ts END,
    total_price = total_price + excluded.total_price,
    last_ts = excluded.last_ts
'''

# 积分写库重试退避（E3/CAP6）：失败按 0.1*2^k 秒退避，最多 3 次尝试；
# WAL 与 busy_timeout 已在 utils/db.py 连接初始化处统一设置。
INTEGRAL_DB_MAX_ATTEMPTS = 3

# 内置默认档位表（D-17：阈值+文案，至少 2 档）。仅当 config 缺少
# integral.<behavior>.copywriting 时启用；config 中的 copywriting 块本身即档位表
# （<metric>_interval=阈值区间，copywriting=该档文案列表），块内可选
# "get_integral"（签到/入场/关注，绝对分）或 "get_integral_proportion"（礼物，
# 比例×礼物价值后取整）覆盖该档积分；块内不配则回退平铺规则键。
_DEFAULT_TIERS = {
    "sign": [
        {"sign_num_interval": "0-30", "get_integral": 1,
         "copywriting": ["{username}打卡成功，获得{get_integral}点积分，您已打卡{sign_num}天"]},
        {"sign_num_interval": "31-999999999", "get_integral": 2,
         "copywriting": ["{username}打卡成功喵，获得{get_integral}点积分，感谢您{sign_num}天的陪伴"]},
    ],
    "gift": [
        # 礼物积分默认按 get_integral_proportion×礼物总价 取整（不随档位变化），
        # 档位内可配 get_integral（绝对分）或 get_integral_proportion（比例）覆盖
        {"gift_price_interval": "0-10",
         "copywriting": ["感谢{username}的{gift_name}，获得{get_integral}点积分"]},
        {"gift_price_interval": "10.01-999999999",
         "copywriting": ["感谢{username}的{gift_name}，老板大气，获得{get_integral}点积分"]},
    ],
    "entrance": [
        {"entrance_num_interval": "0-30", "get_integral": 1,
         "copywriting": ["欢迎{username}，获得{get_integral}点积分"]},
        {"entrance_num_interval": "31-999999999", "get_integral": 2,
         "copywriting": ["非常感谢{username}的陪伴，获得{get_integral}点积分"]},
    ],
}

# D-12：积分查询对无记录用户的分支文案（不静默）；config 可用
# integral.crud.query.no_record_copywriting（列表）覆盖
_DEFAULT_QUERY_NO_RECORD_COPY = "{username}，您暂无积分，签到、互动都可以赚取积分哦"

# 查询库不可达时的通用失败文案（DX CAP6：查询失败不静默）
_DEFAULT_QUERY_ERROR_COPY = "积分查询失败，请稍后再试"


def parse_interval(interval_str):
    """解析 "lo-hi" 区间字符串为 (lo, hi) 浮点闭区间；格式非法返回 None。"""
    try:
        lo_str, hi_str = str(interval_str).split("-", 1)
        lo, hi = float(lo_str), float(hi_str)
        if lo > hi:
            return None
        return (lo, hi)
    except (ValueError, AttributeError):
        return None


def interval_contains(interval_str, value):
    """值是否落在 "lo-hi" 闭区间内；区间非法视为不命中。"""
    bounds = parse_interval(interval_str)
    if bounds is None:
        return False
    lo, hi = bounds
    return lo <= float(value) <= hi


def match_tier_block(blocks, interval_key, value):
    """档位匹配（D-17）：返回第一个 interval_key 区间包含 value 的档位块，无则 None。

    区间空洞容忍：无命中块返回 None，由调用方回退平铺积分/首个含文案的档位。
    """
    if not blocks:
        return None
    for block in blocks:
        if not isinstance(block, dict):
            continue
        if interval_contains(block.get(interval_key), value):
            return block
    return None


def resolve_gift_points(block, proportion, total_price):
    """礼物积分解析：档位块 get_integral（绝对分）> get_integral_proportion（比例）> 平铺比例。

    比例计分显式取整规则 = int() 向下取整（非负价值域内 int() 即向下取整；
    D-20 矩阵登记项：浮点比例×礼物价值 → 整数积分列）。
    """
    if block is not None:
        if block.get("get_integral") is not None:
            return int(block["get_integral"])
        if block.get("get_integral_proportion") is not None:
            return int(float(block["get_integral_proportion"]) * float(total_price))
    return int(float(proportion) * float(total_price))


class FeatureHandler:
    """功能处理器"""
    
    def __init__(self, my_handle):
        """
        初始化功能处理器

        Args:
            my_handle: My_handle 实例
        """
        self.my_handle = my_handle
        # 积分缺键告警去重（每个键仅警告一次，避免直播期间刷屏）
        self._integral_warned_keys = set()
    
    def find_answer(self, question, file_path, similarity=0.6):
        """
        查找答案
        
        Args:
            question (str): 问题
            file_path (str): 文件路径
            similarity (float): 相似度阈值
            
        Returns:
            str: 答案，None表示未找到
        """
        try:
            # 加载数据
            data = self.load_data_from_file(file_path)
            if data is None:
                return None
            
            # 查找最相似的问题
            best_match = None
            best_similarity = 0
            
            for item in data:
                if len(item) >= 2:
                    q, a = item[0], item[1]
                    # 计算相似度
                    sim = self.my_handle.common.calculate_similarity(question, q)
                    if sim > best_similarity and sim >= similarity:
                        best_similarity = sim
                        best_match = a
            
            return best_match
        except Exception as e:
            handle_error(e, {'module': 'handler', 'function': 'unknown'})
            logger.error(f"查找答案失败：{e}")
            return None
    
    def find_similar_answer(self, question, file_path, similarity=0.6):
        """
        查找相似答案
        
        Args:
            question (str): 问题
            file_path (str): 文件路径
            similarity (float): 相似度阈值
            
        Returns:
            str: 答案，None表示未找到
        """
        try:
            # 加载数据
            data = self.load_data_from_file(file_path)
            if data is None:
                return None
            
            # 查找最相似的问题
            best_match = None
            best_similarity = 0
            
            for item in data:
                if len(item) >= 2:
                    q, a = item[0], item[1]
                    # 计算相似度
                    sim = self.my_handle.common.calculate_similarity(question, q)
                    if sim > best_similarity and sim >= similarity:
                        best_similarity = sim
                        best_match = a
            
            return best_match
        except Exception as e:
            logger.error(traceback.format_exc())
            logger.error(f"查找相似答案失败：{e}")
            return None
    
    def load_data_from_file(self, file_path):
        """
        从文件加载数据
        
        Args:
            file_path (str): 文件路径
            
        Returns:
            list: 数据列表
        """
        try:
            if not os.path.exists(file_path):
                logger.error(f"文件不存在：{file_path}")
                return None
            
            data = []
            with open(file_path, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line:
                        parts = line.split('|')
                        if len(parts) >= 2:
                            data.append(parts)
            
            return data
        except Exception as e:
            logger.error(traceback.format_exc())
            logger.error(f"加载数据失败：{e}")
            return None
    
    def local_qa_handle(self, data):
        """
        本地问答处理
        
        Args:
            data (dict): 数据
            
        Returns:
            bool: 是否触发并处理
        """
        try:
            username = data["username"]
            content = data["content"]
            
            # 检查是否启用本地问答
            if not self.my_handle.config.get("local_qa", "enable"):
                return False
            
            # 根据类型，执行不同的问答匹配算法
            if self.my_handle.config.get("local_qa", "format") == "text":
                tmp = self.find_answer(content, self.my_handle.config.get("local_qa", "file_path"), self.my_handle.config.get("local_qa", "similarity"))
            else:
                tmp = self.find_similar_answer(content, self.my_handle.config.get("local_qa", "file_path"), self.my_handle.config.get("local_qa", "similarity"))
            
            if tmp is not None:
                logger.info(f'触发本地问答库 [{username}]: {content}')
                # 将问答库中设定的参数替换为指定内容，开发者可以自定义替换内容
                # 假设有多个未知变量，用户可以在此处定义动态变量
                variables = {
                    'cur_time': self.my_handle.common.get_bj_time(5),
                    'username': username
                }
                
                # 使用字典进行字符串替换
                if any(var in tmp for var in variables):
                    tmp = tmp.format(**{var: value for var, value in variables.items() if var in tmp})
                
                # [1|2]括号语法随机获取一个值，返回取值完成后的字符串
                resp_content = self.my_handle.common.random_bracket_value(tmp)
                
                message = {
                    "type": "local_qa_text",
                    "tts_type": self.my_handle.config.get("audio_synthesis_type"),
                    "data": self.my_handle.config.get(self.my_handle.config.get("audio_synthesis_type")),
                    "config": self.my_handle.config.get("filter"),
                    "username": username,
                    "content": resp_content
                }
                
                self.my_handle.audio_synthesis_handle(message)
                
                return True
            
            return False
        except Exception as e:
            logger.error(traceback.format_exc())
            logger.error(f"本地问答处理失败：{e}")
            return False
    
    def choose_song_handle(self, data):
        """
        点歌处理
        
        Args:
            data (dict): 数据
            
        Returns:
            bool: 是否触发并处理
        """
        try:
            username = data["username"]
            content = data["content"]
            
            # 合并字符串末尾连续的*  主要针对获取不到用户名的情况
            username = self.my_handle.common.merge_consecutive_asterisks(username)
            
            if self.my_handle.config.get("choose_song")["enable"] == True:
                start_cmd = self.my_handle.common.starts_with_any(content, self.my_handle.config.get("choose_song", "start_cmd"))
                stop_cmd = self.my_handle.common.starts_with_any(content, self.my_handle.config.get("choose_song", "stop_cmd"))
                random_cmd = self.my_handle.common.starts_with_any(content, self.my_handle.config.get("choose_song", "random_cmd"))
                
                # 判断随机点歌命令是否正确
                if random_cmd:
                    resp_content = self.my_handle.common.random_search_a_audio_file(self.my_handle.config.get("choose_song", "song_path"))
                    if resp_content is None:
                        return True
                    
                    logger.info(f"随机到的音频路径：{resp_content}")
                    
                    message = {
                        "type": "song",
                        "tts_type": self.my_handle.config.get("audio_synthesis_type"),
                        "data": self.my_handle.config.get(self.my_handle.config.get("audio_synthesis_type")),
                        "config": self.my_handle.config.get("filter"),
                        "username": username,
                        "content": resp_content
                    }
                    
                    self.my_handle.audio_synthesis_handle(message)
                    
                    self.my_handle.webui_show_chat_log_callback("点歌", data, resp_content)
                    
                    return True
                # 判断点歌命令是否正确
                elif start_cmd:
                    logger.info(f"[{username}]: {content}")
                    
                    # 获取本地音频文件夹内所有的音频文件名（不含拓展名）
                    choose_song_song_lists = self.my_handle.audio.get_dir_audios_filename(self.my_handle.config.get("choose_song", "song_path"), 1)
                    
                    # 去除命令前缀
                    content = content[len(start_cmd):]
                    
                    # 说明用户仅发送命令，没有发送歌名，说明用户不会用
                    if content == "":
                        resp_content = f'点歌命令错误，命令为 {self.my_handle.config.get("choose_song", "start_cmd")}+歌名'
                        message = {
                            "type": "comment",
                            "tts_type": self.my_handle.config.get("audio_synthesis_type"),
                            "data": self.my_handle.config.get(self.my_handle.config.get("audio_synthesis_type")),
                            "config": self.my_handle.config.get("filter"),
                            "username": username,
                            "content": resp_content
                        }
                        
                        self.my_handle.audio_synthesis_handle(message)
                        
                        self.my_handle.webui_show_chat_log_callback("点歌", data, resp_content)
                        
                        return True
                    
                    # P3-2: 先查 covers/ 已有翻唱成品（命中直接回播，零延迟）
                    try:
                        from utils.cover_worker import CoverQueue
                        _cq = getattr(self.my_handle, "cover_queue", None)
                        if _cq is None:
                            _cq = CoverQueue(self.my_handle.config, None)
                            self.my_handle.cover_queue = _cq
                        _norm = _cq.normalize_song(content)
                        _covers_dir = _cq._covers_dir()
                        if os.path.isdir(_covers_dir):
                            for _fn in os.listdir(_covers_dir):
                                _base, _ext = os.path.splitext(_fn)
                                if _ext.lower() in (".wav", ".mp3") and _cq.normalize_song(_base.split("__")[0]) == _norm:
                                    _cover_full = os.path.abspath(os.path.join(_covers_dir, _fn))
                                    logger.info(f"[点歌] covers 命中: {_cover_full}")
                                    _msg = {"type": "song",
                                            "tts_type": self.my_handle.config.get("audio_synthesis_type"),
                                            "data": self.my_handle.config.get(self.my_handle.config.get("audio_synthesis_type")),
                                            "config": self.my_handle.config.get("filter"),
                                            "username": username, "content": _cover_full}
                                    self.my_handle.audio_synthesis_handle(_msg)
                                    self.my_handle.webui_show_chat_log_callback("点歌(翻唱)", data, _cover_full)
                                    return True
                    except Exception:
                        logger.error("[点歌] covers 检查异常（回退原逻辑）", exc_info=True)

                    # 判断是否有此歌曲
                    song_filename = self.my_handle.common.find_best_match(content, choose_song_song_lists, similarity=self.my_handle.config.get("choose_song", "similarity"))
                    if song_filename is None:
                        # resp_content = f"抱歉，我还没学会唱{content}"
                        # 根据配置的 匹配失败回复文案来进行合成
                        resp_content = self.my_handle.config.get("choose_song", "match_fail_copy").format(content=content)
                        logger.info(f"[AI回复{username}]：{resp_content}")
                        
                        message = {
                            "type": "comment",
                            "tts_type": self.my_handle.config.get("audio_synthesis_type"),
                            "data": self.my_handle.config.get(self.my_handle.config.get("audio_synthesis_type")),
                            "config": self.my_handle.config.get("filter"),
                            "username": username,
                            "content": resp_content
                        }
                        
                        self.my_handle.audio_synthesis_handle(message)
                        
                        self.my_handle.webui_show_chat_log_callback("点歌", data, resp_content)
                        
                        return True
                    
                    resp_content = self.my_handle.audio.search_files(self.my_handle.config.get('choose_song', 'song_path'), song_filename, True)
                    if resp_content == []:
                        return True
                    
                    logger.debug(f"匹配到的音频原相对路径：{resp_content[0]}")
                    
                    # 拼接音频文件路径
                    resp_content = f"{self.my_handle.config.get('choose_song', 'song_path')}/{resp_content[0]}"
                    resp_content = os.path.abspath(resp_content)
                    logger.info(f"点歌成功！匹配到的音频路径：{resp_content}")
                    # P3-2/DX-5: 异步入队翻唱（成品下次点歌零延迟）
                    try:
                        from utils.cover_worker import CoverQueue
                        _cq = getattr(self.my_handle, "cover_queue", None)
                        if _cq is None:
                            _cq = CoverQueue(self.my_handle.config, None)
                            self.my_handle.cover_queue = _cq
                        # C-5 自动插播（/autoplan 2026-10-14 终门 uc1a）：翻唱 done
                        # 即播——复用点歌播放通道（弹幕线程同构调用，通道内排队/
                        # 打断语义与点歌一致）；回调失败不影响成品落盘
                        if getattr(_cq, "on_done", None) is None:
                            _cq.on_done = self._play_cover_done
                        # C-2 修复（/autoplan 2026-10-14）：翻唱档案=RVC 专属语义，
                        # 读 choose_song.cover_profile_id（C-3 前端写、本处读）——
                        # 旧实现借 gpt_sovits.voice_profile_id（TTS 档案）当 RVC 档案。
                        _profile_id = self.my_handle.config.get("choose_song", "cover_profile_id") or "default"
                        _r = _cq.enqueue(username, song_filename, _profile_id)
                        if _r["action"] == "queued":
                            _cq.ensure_worker()
                            logger.info(f"[点歌] 翻唱已入队（{song_filename}，第 {_r['position']} 位）")
                        elif _r["action"] == "queue_full":
                            logger.info(f"[点歌] 队列已满，跳过翻唱入队: {song_filename}")
                        # cooldown/cached: 静默（D6 有意设计）
                    except Exception:
                        logger.error("[点歌] 翻唱入队异常（不阻塞播放）", exc_info=True)
                    message = {
                        "type": "song",
                        "tts_type": self.my_handle.config.get("audio_synthesis_type"),
                        "data": self.my_handle.config.get(self.my_handle.config.get("audio_synthesis_type")),
                        "config": self.my_handle.config.get("filter"),
                        "username": username,
                        "content": resp_content
                    }
                    
                    self.my_handle.webui_show_chat_log_callback("点歌", data, resp_content)
                    
                    self.my_handle.audio_synthesis_handle(message)
                    
                    return True
                # 判断取消点歌命令是否正确
                elif stop_cmd:
                    self.my_handle.audio.stop_current_audio()

                    return True

            return False
        except Exception as e:
            logger.error(traceback.format_exc())
            logger.error(f"点歌处理失败：{e}")
            return False

    def _play_cover_done(self, job: dict):
        """C-5（/autoplan 2026-10-14 终门 uc1a=立即插播）：翻唱 done 回调——
        构造 song 消息走点歌播放通道（audio_synthesis_handle），成品自动入播放。
        播放通道互斥策略=通道既有行为（与点歌手动播放一致，排队等待当前播报）。
        调用线程=cover-worker 线程（与弹幕线程同构，通道安全）。"""
        try:
            message = {
                "type": "song",
                "tts_type": self.my_handle.config.get("audio_synthesis_type"),
                "data": self.my_handle.config.get(self.my_handle.config.get("audio_synthesis_type")),
                "config": self.my_handle.config.get("filter"),
                "username": "翻唱完成",
                "content": job["cover_path"],
            }
            logger.info(f"[翻唱] 自动插播: {job['song']} → {job['cover_path']}")
            self.my_handle.audio_synthesis_handle(message)
        except Exception:
            logger.error("[翻唱] 自动插播异常（成品已落盘 song/covers/）", exc_info=True)

    def integral_handle(self, type, data):
        """
        积分处理（C1 链路补全）：签到/礼物/入场/关注四类行为计分 + 查询命令回复

        Args:
            type (str): 行为类型（comment=签到检测 / gift / entrance / follow / crud=查询）
            data (dict): 行为数据（platform/username/uid/content/gift_name/total_price 等）

        Returns:
            bool: 是否触发并处理。失败语义（CAP6）：写库失败 log 后返回 False，
                  不抛出、不中断直播主流程（False 即状态条告警标记，状态条归另一工作流消费）
        """
        try:
            # 检查是否启用积分
            if not self.my_handle.config.get("integral", "enable"):
                return False

            if type == "crud":
                return self._integral_query_handle(data)
            if type == "comment":
                return self._integral_sign_handle(data)
            if type == "gift":
                return self._integral_gift_handle(data)
            if type == "entrance":
                return self._integral_entrance_handle(data)
            if type == "follow":
                return self._integral_follow_handle(data)
            return False
        except Exception as e:
            logger.error(traceback.format_exc())
            logger.error(f"积分处理失败：{e}")
            return False

    def _integral_identity(self, data):
        """提取积分身份三元组 (platform, username, uid)。

        uid：弹幕链路当前无 uid 载荷（danmaku_consumer/webui 均只带 platform+username），
        落空串占位——唯一键退化为 (platform, username)，与现有数据面一致。
        """
        platform = str(data.get("platform", "") or "")
        username = str(data.get("username", "") or "")
        uid = str(data.get("uid", "") or "")
        return platform, username, uid

    def _integral_warn_once(self, key, message):
        """积分配置缺键告警（每个键仅一次）。"""
        if key not in self._integral_warned_keys:
            self._integral_warned_keys.add(key)
            logger.warning(message)

    def _integral_db_write(self, sql, args):
        """积分写库（E3/CAP6）：指数退避重试包住写库（0.1*2^k 秒，最多 3 次尝试）。

        最终失败返回 False 不抛出，由调用方按「写库失败不中断直播主流程」处理。
        """
        for attempt in range(INTEGRAL_DB_MAX_ATTEMPTS):
            try:
                if self.my_handle.db.execute(sql, args):
                    return True
            except Exception:
                logger.error(traceback.format_exc())
            if attempt < INTEGRAL_DB_MAX_ATTEMPTS - 1:
                time.sleep(0.1 * (2 ** attempt))  # 退避 0.1s → 0.2s
        return False

    def _integral_db_probe(self):
        """库可达性探测（区分「查询失败」与「无记录」：fetch_one 对两者都返回 None）。"""
        try:
            return bool(self.my_handle.db.execute("SELECT 1"))
        except Exception:
            logger.error(traceback.format_exc())
            return False

    def _integral_fetch_row(self, platform, username, uid):
        """查询积分行，返回 (integral, view_num, sign_num, total_price) 或 None（无记录/查询失败）。"""
        sql = ("SELECT integral, view_num, sign_num, total_price FROM integral "
               "WHERE platform = ? AND username = ? AND uid = ?")
        try:
            return self.my_handle.db.fetch_one(sql, (platform, username, uid))
        except Exception:
            logger.error(traceback.format_exc())
            return None

    def _integral_tier_blocks(self, behavior):
        """读取行为档位表（D-17）：config 的 copywriting 块即档位表，缺键回退内置默认 2 档。"""
        blocks = self.my_handle.config.get("integral", behavior, "copywriting")
        if not blocks:
            logger.debug(f"积分配置缺少 integral.{behavior}.copywriting，使用内置默认档位表（2 档）")
            return _DEFAULT_TIERS[behavior]
        return blocks

    def _integral_reply(self, data, resp_content):
        """积分回复（复用弹幕回复通道：TTS 播报 + webui 聊天日志）。"""
        message = {
            "type": "comment",
            "tts_type": self.my_handle.config.get("audio_synthesis_type"),
            "data": self.my_handle.config.get(self.my_handle.config.get("audio_synthesis_type")),
            "config": self.my_handle.config.get("filter"),
            "username": data.get("username", ""),
            "content": resp_content,
        }
        self.my_handle.audio_synthesis_handle(message)
        self.my_handle.webui_show_chat_log_callback("积分", data, resp_content)

    def _integral_format_and_reply(self, copy_list, data, variables):
        """随机选文案 + 变量替换 + 回复。

        Returns:
            bool: False=回复缺失（CAP6 partial 语义由调用方记录：积分已计入、文案缺失可接受）
        """
        result = self.my_handle.common.get_random_str_in_list_and_format(None, copy_list, variables)
        resp_content = result.get("content") if result and result.get("ret") == 0 else None
        if not resp_content:
            logger.warning("积分文案变量替换失败，本行为积分已计入但不回复")
            return False
        try:
            self._integral_reply(data, resp_content)
        except Exception:
            # CAP6 partial：写库成功而回复失败——积分已计入，log 记录，不算失败
            logger.error(traceback.format_exc())
            logger.error("积分回复失败（partial：积分已计入，回复缺失）")
            return False
        return True

    def _integral_tier_reply(self, blocks, interval_key, metric_value, data, variables):
        """按档位选取文案并回复（D-17：回复文案按档位选取）。"""
        block = match_tier_block(blocks, interval_key, metric_value)
        if block is None:
            # 区间空洞容忍：无命中档位时取首个含文案的档位（积分已按平铺规则计入）
            for candidate in blocks or []:
                if isinstance(candidate, dict) and candidate.get("copywriting"):
                    block = candidate
                    break
        copy_list = block.get("copywriting") if isinstance(block, dict) else None
        if not copy_list:
            logger.warning("积分档位文案缺失，本行为积分已计入但不回复")
            return False
        return self._integral_format_and_reply(copy_list, data, variables)

    def _integral_sign_handle(self, data):
        """签到计分：弹幕命中签到命令词 → UPSERT 计分（sign_num 自增）+ 按签到次数档位回复。"""
        enable = self.my_handle.config.get("integral", "sign", "enable")
        if enable is None:
            self._integral_warn_once("integral.sign.enable", "积分配置缺少 integral.sign.enable，按默认开启处理")
            enable = True
        if not enable:
            return False

        content = str(data.get("content", "") or "").strip()
        sign_cmds = self.my_handle.config.get("integral", "sign", "cmd")
        if not sign_cmds:
            sign_cmds = ["签到", "打卡"]
            self._integral_warn_once("integral.sign.cmd", "积分配置缺少 integral.sign.cmd，使用默认签到命令词：签到/打卡")
        # 命令词精确匹配（避免「签到啦」等前缀弹幕误触发计分回复）
        if content not in sign_cmds:
            return False

        platform, username, uid = self._integral_identity(data)
        if not username:
            return False

        row = self._integral_fetch_row(platform, username, uid)
        old_sign_num = int(row[2]) if row else 0
        new_sign_num = old_sign_num + 1

        # 积分按档位取值（D-17）：签到档位 metric=累计签到次数
        blocks = self._integral_tier_blocks("sign")
        block = match_tier_block(blocks, "sign_num_interval", new_sign_num)
        flat_points = self.my_handle.config.get("integral", "sign", "get_integral")
        if flat_points is None:
            flat_points = 1
            self._integral_warn_once("integral.sign.get_integral", "积分配置缺少 integral.sign.get_integral，使用默认值 1")
        points = int(block["get_integral"]) if (block is not None and block.get("get_integral") is not None) else int(flat_points)

        now = datetime.now()
        args = (platform, username, uid, points, 0, 1, now, 0, now)
        if not self._integral_db_write(INTEGRAL_UPSERT_SQL, args):
            # CAP6：写库失败不中断直播主流程——返回 False（状态条告警标记），弹幕继续走常规链路
            logger.error(f"签到积分写库失败（已重试 {INTEGRAL_DB_MAX_ATTEMPTS} 次），本次积分未计入：{username}")
            return False

        # CAP6 定序=先写库后回复；写库成功回复失败=partial（积分已计入，log 记录）
        self._integral_tier_reply(blocks, "sign_num_interval", new_sign_num, data, {
            "username": username,
            "get_integral": points,
            "sign_num": new_sign_num,
        })
        return True

    def _integral_gift_handle(self, data):
        """礼物计分：积分=int(比例×礼物总价)（显式取整，规则进 D-20），按礼物价值档位回复。"""
        enable = self.my_handle.config.get("integral", "gift", "enable")
        if enable is None:
            self._integral_warn_once("integral.gift.enable", "积分配置缺少 integral.gift.enable，按默认开启处理")
            enable = True
        if not enable:
            return False

        platform, username, uid = self._integral_identity(data)
        if not username:
            return False

        total_price = float(data.get("total_price", 0) or 0)

        # 积分按档位取值（D-17）：礼物档位 metric=本次礼物价值；档位可覆盖比例/绝对分
        blocks = self._integral_tier_blocks("gift")
        block = match_tier_block(blocks, "gift_price_interval", total_price)
        proportion = self.my_handle.config.get("integral", "gift", "get_integral_proportion")
        if proportion is None:
            proportion = 10.0
            self._integral_warn_once("integral.gift.get_integral_proportion",
                                     "积分配置缺少 integral.gift.get_integral_proportion，使用默认比例 10.0")
        points = resolve_gift_points(block, proportion, total_price)

        now = datetime.now()
        args = (platform, username, uid, points, 0, 0, now, total_price, now)
        if not self._integral_db_write(INTEGRAL_UPSERT_SQL, args):
            logger.error(f"礼物积分写库失败（已重试 {INTEGRAL_DB_MAX_ATTEMPTS} 次），本次积分未计入：{username}")
            return False

        self._integral_tier_reply(blocks, "gift_price_interval", total_price, data, {
            "username": username,
            "gift_name": data.get("gift_name", "礼物"),
            "gift_num": data.get("gift_num", data.get("num", 1)),
            "unit_price": data.get("unit_price", 0),
            "total_price": total_price,
            "get_integral": points,
        })
        return True

    def _integral_entrance_handle(self, data):
        """入场计分：view_num（累计观看次数）+1，积分按入场次数档位累加。"""
        enable = self.my_handle.config.get("integral", "entrance", "enable")
        if enable is None:
            self._integral_warn_once("integral.entrance.enable", "积分配置缺少 integral.entrance.enable，按默认开启处理")
            enable = True
        if not enable:
            return False

        platform, username, uid = self._integral_identity(data)
        if not username:
            return False

        row = self._integral_fetch_row(platform, username, uid)
        old_view_num = int(row[1]) if row else 0
        new_view_num = old_view_num + 1

        # 积分按档位取值（D-17）：入场档位 metric=累计入场（观看）次数
        blocks = self._integral_tier_blocks("entrance")
        block = match_tier_block(blocks, "entrance_num_interval", new_view_num)
        flat_points = self.my_handle.config.get("integral", "entrance", "get_integral")
        if flat_points is None:
            flat_points = 1
            self._integral_warn_once("integral.entrance.get_integral", "积分配置缺少 integral.entrance.get_integral，使用默认值 1")
        points = int(block["get_integral"]) if (block is not None and block.get("get_integral") is not None) else int(flat_points)

        now = datetime.now()
        args = (platform, username, uid, points, 1, 0, now, 0, now)
        if not self._integral_db_write(INTEGRAL_UPSERT_SQL, args):
            logger.error(f"入场积分写库失败（已重试 {INTEGRAL_DB_MAX_ATTEMPTS} 次），本次积分未计入：{username}")
            return False

        self._integral_tier_reply(blocks, "entrance_num_interval", new_view_num, data, {
            "username": username,
            "get_integral": points,
            "entrance_num": new_view_num,
        })
        return True

    def _integral_follow_handle(self, data):
        """关注计分（新增规则键）：config 缺 integral.follow 节时用安全默认并仅告警一次。"""
        platform, username, uid = self._integral_identity(data)
        if not username:
            return False

        enable = self.my_handle.config.get("integral", "follow", "enable")
        if enable is None:
            self._integral_warn_once("integral.follow.enable",
                                     "积分配置缺少 integral.follow 节（新增规则键），按默认开启处理")
            enable = True
        if not enable:
            return False

        points = self.my_handle.config.get("integral", "follow", "get_integral")
        if points is None:
            points = 1
            self._integral_warn_once("integral.follow.get_integral",
                                     "积分配置缺少 integral.follow.get_integral，使用默认值 1")
        points = int(points)

        copy_list = self.my_handle.config.get("integral", "follow", "copywriting")
        if not copy_list:
            copy_list = ["{username}感谢关注，获得{get_integral}点积分"]
            self._integral_warn_once("integral.follow.copywriting", "积分配置缺少 integral.follow.copywriting，使用默认关注文案")

        now = datetime.now()
        args = (platform, username, uid, points, 0, 0, now, 0, now)
        if not self._integral_db_write(INTEGRAL_UPSERT_SQL, args):
            logger.error(f"关注积分写库失败（已重试 {INTEGRAL_DB_MAX_ATTEMPTS} 次），本次积分未计入：{username}")
            return False

        self._integral_format_and_reply(copy_list, data, {
            "username": username,
            "get_integral": points,
        })
        return True

    def _integral_query_handle(self, data):
        """查询回复链路：命中查询命令 → SELECT 积分 → 文案回复；无记录回复「暂无积分」（D-12）。"""
        query_cfg = self.my_handle.config.get("integral", "crud", "query") or {}
        enable = query_cfg.get("enable")
        if enable is None:
            enable = True
        if not enable:
            return False

        content = str(data.get("content", "") or "").strip()
        cmds = query_cfg.get("cmd")
        if not cmds:
            cmds = ["我的积分", "查询积分"]
            self._integral_warn_once("integral.crud.query.cmd", "积分配置缺少 integral.crud.query.cmd，使用默认查询命令词：我的积分/查询积分")
        if content not in cmds:
            return False

        platform, username, uid = self._integral_identity(data)
        if not username:
            return False

        variables = {"username": username}
        # 查询为只读链路（无写库定序问题）；先探活区分「库不可达」与「无记录」
        # （CAP6/DX：查询失败回复通用失败文案，不静默）
        if not self._integral_db_probe():
            self._integral_format_and_reply([_DEFAULT_QUERY_ERROR_COPY], data, variables)
            return True

        row = self._integral_fetch_row(platform, username, uid)
        if row is None:
            # D-12：无记录用户回复「暂无积分」分支文案（不静默）
            copy_list = query_cfg.get("no_record_copywriting") or [_DEFAULT_QUERY_NO_RECORD_COPY]
            variables["integral"] = 0
        else:
            copy_list = query_cfg.get("copywriting") or ["{username}，您当前的积分是{integral}"]
            variables["integral"] = int(row[0])

        self._integral_format_and_reply(copy_list, data, variables)
        return True
    
    def key_mapping_handle(self, type, data):
        """
        按键映射处理
        
        Args:
            type (str): 类型
            data (dict): 数据
            
        Returns:
            bool: 是否触发并处理
        """
        try:
            # 检查是否启用按键映射
            if not self.my_handle.config.get("key_mapping", "enable"):
                return False
            
            # TODO: 实现按键映射处理逻辑
            
            return False
        except Exception as e:
            logger.error(traceback.format_exc())
            logger.error(f"按键映射处理失败：{e}")
            return False

    def blacklist_handle(self, data):
        """
        黑名单处理（T7 接线恢复：键面对齐 UI 写入键 filter.blacklist.username）

        S-2 匹配语义：平台 UID 优先，其次 NFKC+去全部空白+casefold 规范化匹配
        （防全角/零宽字符/首尾空格/大小写绕过）。

        Args:
            data (dict): 数据（含 username，可能含 uid）

        Returns:
            bool: 是否在黑名单中
        """
        try:
            # 键面对齐：前端 blacklist tab 写 filter.blacklist.username（换行分隔 textarea）
            raw_list = self.my_handle.config.get("filter", "blacklist", "username")
            if isinstance(raw_list, str):
                entries = [item.strip() for item in raw_list.splitlines() if item.strip()]
            elif isinstance(raw_list, list):
                entries = [str(item).strip() for item in raw_list if str(item).strip()]
            else:
                return False
            if not entries:
                return False  # 空名单=功能关闭（UI 无独立开关）

            normalized_entries = {self._normalize_blacklist_entry(e) for e in entries}

            # UID 优先匹配（弹幕链路载荷含 uid 时）
            uid = data.get("uid")
            if uid and str(uid).strip() and self._normalize_blacklist_entry(str(uid)) in normalized_entries:
                logger.info(f"用户 uid={uid} 在黑名单中（UID 匹配），拦截")
                return True

            # 用户名规范化匹配（S-2：防全角/零宽/空白/大小写绕过）
            username = str(data.get("username", ""))
            if self._normalize_blacklist_entry(username) in normalized_entries:
                logger.info(f"用户 {username} 在黑名单中（规范化匹配），拦截")
                return True

            return False
        except Exception as e:
            logger.error(traceback.format_exc())
            logger.error(f"黑名单处理失败：{e}")
            return False

    @staticmethod
    def _normalize_blacklist_entry(s: str) -> str:
        """S-2 规范化：NFKC（全角→半角等）→ 去全部空白（含零宽字符）→ casefold。"""
        import unicodedata
        normalized = unicodedata.normalize("NFKC", str(s))
        normalized = "".join(ch for ch in normalized if not ch.isspace() and not unicodedata.category(ch) == "Cf")
        return normalized.casefold()
    
