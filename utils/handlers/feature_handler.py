# -*- coding: UTF-8 -*-
"""
功能处理器模块

处理各种功能模块
"""

import os
import random
import time
import traceback
from collections import deque
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
    
    def local_qa_match(self, content, username=None):
        """本地问答词库纯查询（基础功能 2026-10-22 批次抽取，DRY：三消费点共用）。

        仅做匹配与渲染（变量替换+随机括号），不含 enable 检查、不播音频——
        enable 检查统一在调用点（spec R2-1）；local_qa_handle/LLM 托底/
        chat_type=local_qa 通道三消费点统一调用（ENG E-2）。

        Args:
            content (str): 弹幕内容（查询文本）
            username (str | None): 弹幕用户名（供词库 {username} 变量渲染）

        Returns:
            str | None: 渲染后的回答文本；未匹配返回 None
        """
        try:
            # 根据类型，执行不同的问答匹配算法
            if self.my_handle.config.get("local_qa", "format") == "text":
                tmp = self.find_answer(content, self.my_handle.config.get("local_qa", "file_path"), self.my_handle.config.get("local_qa", "similarity"))
            else:
                tmp = self.find_similar_answer(content, self.my_handle.config.get("local_qa", "file_path"), self.my_handle.config.get("local_qa", "similarity"))

            if tmp is None:
                return None
            return self.local_qa_render(tmp, content_username=username)
        except Exception as e:
            logger.error(traceback.format_exc())
            logger.error(f"本地问答匹配失败：{e}")
            return None

    def local_qa_render(self, text, content_username=None):
        """词库回答共享渲染（ENG E-2：变量替换 + [1|2] 随机括号语法）。

        Args:
            text (str): 词库原始回答文本
            content_username (str | None): 弹幕用户名（托底/local_qa 通道传入；
                local_qa_handle 传实际 username）

        Returns:
            str: 渲染后的回答文本
        """
        try:
            # 将问答库中设定的参数替换为指定内容，开发者可以自定义替换内容
            # 假设有多个未知变量，用户可以在此处定义动态变量
            variables = {
                'cur_time': self.my_handle.common.get_bj_time(5),
                'username': content_username or ''
            }

            # 使用字典进行字符串替换
            if any(var in text for var in variables):
                text = text.format(**{var: value for var, value in variables.items() if var in text})

            # [1|2]括号语法随机获取一个值，返回取值完成后的字符串
            return self.my_handle.common.random_bracket_value(text)
        except Exception as e:
            logger.error(f"本地问答渲染失败（降级原文）：{e}")
            return text

    def local_qa_handle(self, data):
        """
        本地问答处理（前置抢答路径——仅 chat_type=local_qa 时由调用方进入，
        spec C1：custom_llm/fastrag 模式跳过前置拦截，词库只作托底）

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

            # 匹配+渲染走共享函数（DRY，ENG E-2）
            resp_content = self.local_qa_match(content, username=username)

            if resp_content is not None:
                logger.info(f'触发本地问答库 [{username}]: {content}')

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
    
    # ---------- 点歌模式（/autoplan 2026-10-23 批次：礼物驱动重构 R1-R5/R7/R12）----------
    # 架构：双触发（弹幕意图/礼物映射）→ 纯函数匹配引擎（text_mixin.match_song_intent /
    # match_gift_to_song_entry）→ 三输出（文案播报 / 翻唱入队 / SongMode 播放）。
    # 编排订阅 on_song（WP-10）与 C-5 插播回调保留；同歌冷却+回应节流防刷屏。

    _SONG_REPLY_DEFAULT_COOLDOWN_S = 30
    _SONG_PLAY_COOLDOWN_DEFAULT_S = 300

    def _song_copy_message(self, username, copy_text):
        """R4 助播路由：assistant_anchor.type 含 "song" → 文案走助播通道
        （type=assistant_anchor_text + 助播名，享受助播优先级）；未开启走默认
        TTS（type=comment 原路）。返回可直接 audio_synthesis_handle 的消息。"""
        anchor_types = []
        try:
            anchor_types = self.my_handle.config.get("assistant_anchor", "type") or []
        except Exception:
            pass
        if "song" in anchor_types:
            anchor_name = ""
            try:
                anchor_name = self.my_handle.config.get("assistant_anchor", "username") or ""
            except Exception:
                pass
            return {
                "type": "assistant_anchor_text",
                "tts_type": self.my_handle.config.get("audio_synthesis_type"),
                "data": self.my_handle.config.get(self.my_handle.config.get("audio_synthesis_type")),
                "config": self.my_handle.config.get("filter"),
                "username": anchor_name or "助播",
                "content": copy_text,
            }
        return {
            "type": "comment",
            "tts_type": self.my_handle.config.get("audio_synthesis_type"),
            "data": self.my_handle.config.get(self.my_handle.config.get("audio_synthesis_type")),
            "config": self.my_handle.config.get("filter"),
            "username": username,
            "content": copy_text,
        }

    def _render_song_copy(self, template, user="", content="", gift=""):
        """R3 四文案渲染：统一变量池 {user}/{content}/{gift}，缺变量渲染为空串
        不抛异常（str.format_map + SafeDict——模板含未提供变量时静默留空）。"""
        if not template:
            return ""
        class _SafeDict(dict):
            def __missing__(self, key):
                return ""
        try:
            return template.format_map(_SafeDict(user=user or "", content=content or "", gift=gift or ""))
        except Exception:
            # 模板自身格式错误（如孤立 { ）不中断直播，退回原文
            return template

    def _song_song_names(self):
        """歌曲列表的 song 字段集合（R2：弹幕匹配面=映射表歌名，非磁盘目录）。"""
        mapping = self.my_handle.config.get("choose_song", "gift_song_map") or {}
        if not isinstance(mapping, dict):
            return []
        return sorted({str(v).strip() for v in mapping.values() if str(v).strip()})

    def _song_find_audio(self, song_name):
        """R2 song 字段→音频文件解析：内部根目录及 covers/ 内按归一歌名匹配
        wav/mp3（复用 CoverQueue.normalize_song 口径）。返回 (audio_path, backing_path)，
        backing_path=分轨伴奏（R6 主方案：covers/<song>__<profile>_inst.wav），无分轨为 None。"""
        from utils.autolive_paths import song_audio_root
        from utils.cover_worker import CoverQueue
        root = song_audio_root(self.my_handle.config)
        target = CoverQueue.normalize_song(song_name)
        if not target or not os.path.isdir(root):
            return None
        main_hit = backing = None
        for fn in os.listdir(root):
            base, ext = os.path.splitext(fn)
            low = ext.lower()
            if low not in (".mp3", ".wav", ".flac"):
                continue
            if CoverQueue.normalize_song(base) == target:
                main_hit = os.path.join(root, fn)
                break
        if main_hit is None:
            return None
        # 分轨伴奏：covers/ 下 <成品名>_inst.wav（T5 翻唱分轨保留，R6 主方案）
        covers_dir = os.path.join(root, "covers")
        if os.path.isdir(covers_dir):
            stem = os.path.splitext(os.path.basename(main_hit))[0]
            for fn in sorted(os.listdir(covers_dir), reverse=True):
                b, e = os.path.splitext(fn)
                if e.lower() == ".wav" and b.endswith("_inst") and CoverQueue.normalize_song(b[:-5]).startswith(target):
                    backing = os.path.join(covers_dir, fn)
                    break
        return (main_hit, backing)

    def _song_covers_hit(self, song_name):
        """covers/ 翻唱成品查找（P3-2 语义）：返回 (成品路径, 分轨伴奏或 None, base) 或 None。
        成品名=<song>__<profile>.wav；分轨=<song>__<profile>_vocal.wav/_inst.wav（R6）。"""
        from utils.autolive_paths import song_audio_root
        from utils.cover_worker import CoverQueue
        covers_dir = os.path.join(song_audio_root(self.my_handle.config), "covers")
        if not os.path.isdir(covers_dir):
            return None
        target = CoverQueue.normalize_song(song_name)
        if not target:
            return None
        for fn in sorted(os.listdir(covers_dir), reverse=True):
            base, ext = os.path.splitext(fn)
            if ext.lower() not in (".wav", ".mp3") or base.endswith("_vocal") or base.endswith("_inst"):
                continue
            if CoverQueue.normalize_song(base.split("__")[0]) == target:
                vocal = os.path.join(covers_dir, base + "_vocal.wav")
                inst = os.path.join(covers_dir, base + "_inst.wav")
                full = os.path.abspath(os.path.join(covers_dir, fn))
                if os.path.isfile(vocal) and os.path.isfile(inst):
                    return (full, vocal, inst)
                return (full, None, None)
        return None

    def _song_cooldown_check(self, song_name):
        """同歌最小冷却（native 后悔场景 1）：冷却内返回 False 防刷屏。"""
        try:
            cooldown = int(self.my_handle.config.get("choose_song", "song_cooldown_s")
                           or self._SONG_PLAY_COOLDOWN_DEFAULT_S)
        except (TypeError, ValueError):
            cooldown = self._SONG_PLAY_COOLDOWN_DEFAULT_S
        now = time.time()
        last = getattr(self, "_song_last_play_ts", {}).get(song_name, 0)
        if now - last < cooldown:
            logger.info(f"[点歌] 同歌冷却中（{cooldown}s）：{song_name}")
            return False
        if not hasattr(self, "_song_last_play_ts"):
            self._song_last_play_ts = {}
        self._song_last_play_ts[song_name] = now
        return True

    def _song_reply_throttled(self):
        """点歌判断回应节流（F-S3：既有 filter 无弹幕重复节流）。"""
        try:
            cooldown = int(self.my_handle.config.get("choose_song", "reply_cooldown_s")
                           or self._SONG_REPLY_DEFAULT_COOLDOWN_S)
        except (TypeError, ValueError):
            cooldown = self._SONG_REPLY_DEFAULT_COOLDOWN_S
        now = time.time()
        last = getattr(self, "_song_reply_last_ts", 0)
        if now - last < cooldown:
            logger.info(f"[点歌] 回应节流中（{cooldown}s）")
            return True
        self._song_reply_last_ts = now
        return False

    def _song_play_message(self, username, audio_path, backing_path, song_name):
        """SongMode 播放入队（R6/R12：song 消息扩展 backing_path 伴奏轨；
        编排订阅 on_song 保留 WP-10——画面序列接管，音频走 SongMode 原路）。"""
        try:
            from utils.orchestration import get_choreographer
            _choreo = get_choreographer()
            if _choreo is not None:
                _choreo.on_song(song_name)
        except Exception:
            pass
        message = {
            "type": "song",
            "tts_type": self.my_handle.config.get("audio_synthesis_type"),
            "data": self.my_handle.config.get(self.my_handle.config.get("audio_synthesis_type")),
            "config": self.my_handle.config.get("filter"),
            "username": username,
            "content": audio_path,
            "voice_path": audio_path,
            "backing_path": backing_path or "",
            "backing_volume": self.my_handle.config.get("choose_song", "backing_volume") or 0.8,
        }
        self.my_handle.audio_synthesis_handle(message)

    def _song_announce_and_play(self, username, song_name, gift_name):
        """礼物/点歌命中后的播报+播放主路径（R2/R5/R7）：
        ① covers 成品命中→成功文案+播放（分轨双轨或整轨）；
        ② 无成品→prepare_copy+入队翻唱（C-5 产出后自动插播）。
        返回 True=已处理（礼物触发场景答谢照旧由调用方保证）。"""
        hit = self._song_covers_hit(song_name)
        if hit is not None:
            cover_full, vocal, inst = hit
            # R6 主方案：分轨齐全→人声轨驱动+伴奏外放；缺失→整轨自动降级
            audio_path, backing = (vocal, inst) if vocal else (cover_full, None)
            copy_text = self._render_song_copy(
                self.my_handle.config.get("choose_song", "match_success_copy") or "",
                user=username, content=song_name, gift=gift_name or "")
            logger.info(f"[点歌] 命中翻唱成品: {cover_full}（分轨={'有' if vocal else '无→整轨'}）")
            self.my_handle.webui_show_chat_log_callback("点歌", {"username": username}, copy_text)
            self.my_handle.audio_synthesis_handle(self._song_copy_message(username, copy_text))
            self._song_play_message(username, audio_path, backing, song_name)
            return True
        # 无成品：prepare_copy 确认 + 异步入队翻唱（DX-5/C-5 既有语义）
        prepare_text = self._render_song_copy(
            self.my_handle.config.get("choose_song", "prepare_copy") or "",
            user=username, content=song_name, gift=gift_name or "")
        logger.info(f"[点歌] 无成品，准备翻唱: {song_name}")
        if prepare_text:
            self.my_handle.webui_show_chat_log_callback("点歌", {"username": username}, prepare_text)
            self.my_handle.audio_synthesis_handle(self._song_copy_message(username, prepare_text))
        self._song_enqueue_cover(username, song_name)
        return True

    def _song_enqueue_cover(self, username, song_name):
        """无成品→CoverQueue 入队（DX-5）；队列满/冷却静默（D6 既有语义）。"""
        try:
            from utils.cover_worker import CoverQueue
            _cq = getattr(self.my_handle, "cover_queue", None)
            if _cq is None:
                _cq = CoverQueue(self.my_handle.config, None)
                self.my_handle.cover_queue = _cq
            if getattr(_cq, "on_done", None) is None:
                _cq.on_done = self._play_cover_done
            _profile_id = self.my_handle.config.get("choose_song", "cover_profile_id") or "default"
            _r = _cq.enqueue(username, song_name, _profile_id)
            if _r["action"] == "queued":
                _cq.ensure_worker()
                logger.info(f"[点歌] 翻唱已入队（{song_name}，第 {_r['position']} 位）")
            elif _r["action"] == "queue_full":
                logger.info(f"[点歌] 队列已满，跳过翻唱入队: {song_name}")
            # cooldown/cached: 静默（D6 有意设计）
        except Exception:
            logger.error("[点歌] 翻唱入队异常（不阻塞播报）", exc_info=True)

    def choose_song_gift_trigger(self, data):
        """R2/R5 礼物触发点歌（gift_handle 前段挂钩）：归一礼物名匹配
        gift_song_map→命中=答谢照旧（调用方保证）+成功播报+播放/入队；
        未命中=返回 False（仅答谢，语义不变）。异常不中断礼物主流程。"""
        try:
            if not self.my_handle.config.get("choose_song", "enable"):
                return False
            gift_name = (data.get("gift_name") or "").strip()
            username = data.get("username") or ""
            if not gift_name:
                return False
            from utils.common.text_mixin import match_gift_to_song_entry
            mapping = self.my_handle.config.get("choose_song", "gift_song_map") or {}
            song_name = match_gift_to_song_entry(gift_name, mapping)
            if song_name is None:
                return False
            if not self._song_cooldown_check(song_name):
                return True  # 冷却内：跳过点歌但答谢照旧
            logger.info(f"[点歌] 礼物命中：[{username}] {gift_name} → {song_name}")
            return self._song_announce_and_play(username, song_name, gift_name)
        except Exception:
            logger.error("礼物点歌触发异常（降级仅答谢）", exc_info=True)
            return False

    def choose_song_handle(self, data):
        """R1/R2 弹幕点歌判断：contains 触发词命中→剥离后对歌曲列表（映射表
        song 字段）相似度匹配。命中→guide_copy 引导打赏（回应，不执行点歌播放）；
        未命中→match_fail_copy（回应，不播放）。移除 stop_cmd/random_cmd/song_path
        （/autoplan 2026-10-23 批次 R1）。回应受 reply_cooldown_s 节流（F-S3）。"""
        try:
            username = data["username"]
            content = data["content"]

            # 合并字符串末尾连续的*  主要针对获取不到用户名的情况
            username = self.my_handle.common.merge_consecutive_asterisks(username)

            if self.my_handle.config.get("choose_song", "enable"):
                from utils.common.text_mixin import match_song_intent
                trigger_words = self.my_handle.config.get("choose_song", "start_cmd") or []
                similarity = self.my_handle.config.get("choose_song", "similarity")
                song_names = self._song_song_names()
                verdict = match_song_intent(content, trigger_words, song_names, similarity)
                if verdict is None:
                    return False  # 未触发：静默交回弹幕链
                state, song_name, _ratio = verdict
                logger.info(f"[点歌] 弹幕意图{'命中' if state == 'matched' else '触发（列表无歌）'}："
                            f"[{username}] {content}" + (f" → {song_name}" if song_name else ""))
                if self._song_reply_throttled():
                    return True
                mapping = self.my_handle.config.get("choose_song", "gift_song_map") or {}
                if state == "matched":
                    gift_for_song = next((str(k) for k, v in mapping.items()
                                          if str(v).strip() == song_name), "")
                    # 场景 2：仅回应引导打赏，不执行点歌播放
                    guide_text = self._render_song_copy(
                        self.my_handle.config.get("choose_song", "guide_copy") or "",
                        user=username, content=song_name, gift=gift_for_song)
                else:
                    # 场景 4：列表无歌→失败文案，回应不播放
                    guide_text = self._render_song_copy(
                        self.my_handle.config.get("choose_song", "match_fail_copy") or "",
                        user=username, content=content, gift="")
                self.my_handle.webui_show_chat_log_callback("点歌", data, guide_text)
                self.my_handle.audio_synthesis_handle(self._song_copy_message(username, guide_text))
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
    
    # ---------------- 按键映射（T5 迁移恢复：源=my_handle.py.backup:2225-2520，T-c 四类对照后适配） ----------------

    # S-1 串口安全：全局+每用户滑动窗口限速 + 同句去重
    _serial_rate_window = None  # 类级 deque：全局窗口
    _serial_user_windows = None  # 类级 dict：每用户窗口
    _serial_last_content = None  # 同句去重：最近一次串口触发内容


    def _serial_rate_allow(self, username: str) -> bool:
        """S-1 限速：全局 key_mapping.serial_rate_limit_per_min（默认 6）+
        每用户 serial_per_user_limit_per_min（默认 2），超限拒绝串口触发。"""
        import time as _time
        now = _time.time()
        if FeatureHandler._serial_rate_window is None:
            FeatureHandler._serial_rate_window = deque()
            FeatureHandler._serial_user_windows = {}
        window = FeatureHandler._serial_rate_window
        window.append(now)
        while window and now - window[0] > 60:
            window.popleft()
        user_windows = FeatureHandler._serial_user_windows.setdefault(username, deque())
        user_windows.append(now)
        while user_windows and now - user_windows[0] > 60:
            user_windows.popleft()
        try:
            global_limit = int(self.my_handle.config.get("key_mapping", "serial_rate_limit_per_min") or 6)
            user_limit = int(self.my_handle.config.get("key_mapping", "serial_per_user_limit_per_min") or 2)
        except (TypeError, ValueError):
            global_limit, user_limit = 6, 2
        if len(window) > global_limit:
            logger.warning(f"S-1 串口限速：全局每分钟超 {global_limit} 次，拒绝触发（user={username}）")
            return False
        if len(user_windows) > user_limit:
            logger.warning(f"S-1 串口限速：用户 {username} 每分钟超 {user_limit} 次，拒绝触发")
            return False
        return True

    def _serial_send(self, key_mapping_config: dict, data: dict):
        """串口触发（S-1 限速+同句去重；asyncio 安全执行——T-c 适配点④）。"""
        username = str(data.get("username", ""))
        tmp = random.choice(key_mapping_config.get("serial_send_data") or [])
        if not tmp:
            return None
        tmp = self.my_handle.common.brackets_text_randomize(tmp)
        data_json = {
            "username": username,
            "gift_name": data.get("gift_name", ""),
            "gift_num": data.get("num", ""),
            "unit_price": data.get("unit_price", ""),
            "total_price": data.get("total_price", ""),
            "cur_time": self.my_handle.common.get_bj_time(5),
        }
        tmp = self.my_handle.common.dynamic_variable_replacement(tmp, data_json)

        # S-1 同句去重：同一弹幕内容不重复触发串口（复用 *_single_sentence_trigger_once 语义）
        if FeatureHandler._serial_last_content == tmp and data.get("content") == getattr(self, "_serial_last_source", None):
            logger.debug(f"S-1 同句去重：{tmp} 已触发过，跳过")
            return None

        if not self._serial_rate_allow(username):
            return None

        serial_name = key_mapping_config.get("serial_name", "")
        serial_config = None
        for cfg_item in self.my_handle.config.get("serial", "config") or []:
            if cfg_item.get("serial_name") == serial_name:
                serial_config = cfg_item
                break
        if not serial_config:
            logger.error(f"获取串口名：{serial_name} 的配置信息失败，请到 串口 页面检查配置是否正确！")
            return None

        async def _connect_and_send():
            from utils.serial_manager_instance import get_serial_manager
            manager = get_serial_manager()
            await manager.connect(serial_name, int(serial_config["baudrate"]))
            return await manager.send_data(serial_name, tmp, serial_config.get("serial_data_type", "ascii"))

        # T-c 适配点④：asyncio.run 在事件循环线程内会 RuntimeError——同步线程链（Timer）安全，
        # 仍以 try/except 防御性处理
        try:
            import asyncio
            resp_json = asyncio.run(_connect_and_send())
            logger.info(f"【触发按键映射】触发串口：{tmp}，{resp_json.get('msg', resp_json)}")
            FeatureHandler._serial_last_content = tmp
            self._serial_last_source = data.get("content")
            return tmp
        except RuntimeError as e:
            logger.error(f"串口发送失败（事件循环冲突）：{e}")
            return None
        except Exception as e:
            logger.error(f"串口发送失败（pyserial 缺失或设备不可达——启用串口触发需 pip install pyserial）：{e}")
            return None

    def _trigger_copywriting(self, key_mapping_config: dict, data: dict):
        """文案触发：随机文案+变量替换→播放链。"""
        copy_list = key_mapping_config.get("copywriting") or []
        if not copy_list:
            return
        tmp = random.choice(copy_list)
        tmp = self.my_handle.common.brackets_text_randomize(tmp)
        data_json = {
            "username": data.get("username", ""),
            "gift_name": data.get("gift_name", ""),
            "gift_num": data.get("num", ""),
            "unit_price": data.get("unit_price", ""),
            "total_price": data.get("total_price", ""),
            "cur_time": self.my_handle.common.get_bj_time(5),
        }
        tmp = self.my_handle.common.dynamic_variable_replacement(tmp, data_json)
        message = {
            "type": "key_mapping",
            "tts_type": self.my_handle.config.get("audio_synthesis_type"),
            "data": self.my_handle.config.get(self.my_handle.config.get("audio_synthesis_type")),
            "config": self.my_handle.config.get("filter"),
            "username": data.get("username", ""),
            "content": tmp,
        }
        logger.info(f"【触发按键映射】触发文案：{tmp}")
        self.my_handle.audio_synthesis_handle(message)
        return True

    def _trigger_local_audio(self, key_mapping_config: dict, data: dict):
        """本地音频触发（T-c 适配点②：config[].local_audio 键可能缺失，.get 防御）。"""
        audio_list = key_mapping_config.get("local_audio") or []
        if not audio_list:
            return
        tmp = random.choice(audio_list)
        message = {
            "type": "key_mapping",
            "tts_type": self.my_handle.config.get("audio_synthesis_type"),
            "data": self.my_handle.config.get(self.my_handle.config.get("audio_synthesis_type")),
            "config": self.my_handle.config.get("filter"),
            "username": data.get("username", ""),
            "content": tmp,
            "file_path": tmp,
        }
        logger.info(f"【触发映射】播放本地音频：{tmp}")
        self.my_handle.audio_synthesis_handle(message)
        return True

    def _trigger_key_press(self, key_mapping_config: dict, data: dict):
        """按键触发（pyautogui 延迟导入——未安装时降级警告，T-c 适配点②）。"""
        try:
            import pyautogui
        except ImportError:
            logger.warning("按键触发需要 pyautogui（pip install pyautogui），当前未安装，跳过本次按键模拟")
            return
        for key in key_mapping_config.get("keys") or []:
            pyautogui.keyDown(key)
        for key in key_mapping_config.get("keys") or []:
            pyautogui.keyUp(key)
        logger.info(f"【触发按键映射】按键：{key_mapping_config.get('keys')}")
        return True

    def _trigger_img(self, key_mapping_config: dict, data: dict):
        """图片触发（T-c 适配点③：现行 my_handle 无 sd 属性，降级警告）。"""
        img_list = key_mapping_config.get("img_path") or []
        sd = getattr(self.my_handle, "sd", None)
        if sd is None:
            logger.warning("图片触发依赖虚拟摄像头对象（my_handle.sd 不存在），跳过本次图片推送")
            return
        tmp = random.choice(img_list)
        sd.set_new_img(tmp)
        logger.info(f"【触发按键映射】推送图片：{tmp}")
        return True

    def key_mapping_handle(self, type, data):
        """
        按键映射处理（T5 迁移恢复）

        Args:
            type (str): 数据来源类型（弹幕/回复）
            data (dict): 平台侧传入的 data 数据

        Returns:
            bool: 是否正常触发了按键映射事件
        """
        flag = False
        try:
            if not self.my_handle.config.get("key_mapping", "enable"):
                return False

            def trigger_dispatch(trigger_type: str, key_mapping_config: dict, matched_text: str) -> bool:
                """按 trigger_type 分派（T-c 适配点①：键面 f"{prefix}_single_sentence_trigger_once"，无 _enable 后缀）。"""
                handlers_map = {
                    "key_trigger_type": lambda: self._trigger_key_press(key_mapping_config, data),
                    "copywriting_trigger_type": lambda: self._trigger_copywriting(key_mapping_config, data),
                    "local_audio_trigger_type": lambda: self._trigger_local_audio(key_mapping_config, data),
                    "serial_trigger_type": lambda: self._serial_send(key_mapping_config, data),
                    "img_path_trigger_type": lambda: self._trigger_img(key_mapping_config, data),
                }
                mode_values = ["关键词", "关键词+礼物"] if trigger_type.startswith("key") or trigger_type.startswith("copy") or trigger_type.startswith("local") or trigger_type.startswith("img") else ["礼物", "关键词+礼物"]
                _ = mode_values  # 触发模式判定在 backup 中由 config key_mapping.<trigger_type> 值决定
                trigger_mode = self.my_handle.config.get("key_mapping", trigger_type)
                if trigger_mode not in ["关键词", "礼物", "关键词+礼物"]:
                    return False
                logger.info(f"【触发按键映射】匹配：{matched_text}，类型：{trigger_type}，模式：{trigger_mode}")
                executed = handlers_map[trigger_type]()  # 降级（依赖缺失）返回 False
                if not executed:
                    return False  # 未实际执行 → 继续尝试下一触发器
                flag = True
                # once 语义：实际命中才终止后续匹配（键面适配：config 无 _enable 后缀，双键面兼容）
                prefix = trigger_type.split("_")[0]
                once = self.my_handle.config.get("key_mapping", f"{prefix}_single_sentence_trigger_once")
                if once is None:
                    once = self.my_handle.config.get("key_mapping", f"{prefix}_single_sentence_trigger_once_enable")
                return bool(once)

            key_mapping_configs = self.my_handle.config.get("key_mapping", "config") or []

            if "gift_name" in data:
                # 礼物触发：礼物名精确匹配（once=false 时继续遍历，flag 聚合）
                for key_mapping_config in key_mapping_configs:
                    for gift in key_mapping_config.get("gift") or []:
                        if gift == data["gift_name"]:
                            for trigger in ["key_trigger_type", "copywriting_trigger_type",
                                            "local_audio_trigger_type", "serial_trigger_type"]:
                                if trigger_dispatch(trigger, key_mapping_config, gift):
                                    return True
                                flag = True  # 实际执行过（executed），once=false 继续
                return flag

            content = str(data.get("content", ""))
            start_cmd = self.my_handle.config.get("key_mapping", "start_cmd")
            if start_cmd and content.startswith(start_cmd):
                content = content[len(start_cmd):]

            for key_mapping_config in key_mapping_configs:
                try:
                    similarity = float(key_mapping_config.get("similarity", 1))
                except (TypeError, ValueError):
                    similarity = 1.0
                for keyword in key_mapping_config.get("keywords") or []:
                    matched = False
                    if type == "弹幕":
                        import difflib
                        ratio = difflib.SequenceMatcher(None, content, keyword).ratio()
                        matched = ratio >= similarity
                    elif type == "回复":
                        matched = keyword in content

                    if matched:
                        for trigger in ["key_trigger_type", "copywriting_trigger_type",
                                        "local_audio_trigger_type", "serial_trigger_type",
                                        "img_path_trigger_type"]:
                            if trigger_dispatch(trigger, key_mapping_config, keyword):
                                return True
            return flag
        except Exception as e:
            logger.error(traceback.format_exc())
            logger.error(f"【触发按键映射】错误：{e}")
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
    
