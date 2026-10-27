# -*- coding: UTF-8 -*-
"""
助播中央路由助手（/autoplan main-20261026-214845 批次 T1，义务 E-A1/A2/A3、D10）

架构约束（E-A1）：本模块为独立小模块——零兄弟 handler 依赖、只读传入 config、
路由表为纯数据结构（可脱离 handlers 生态单测）。生产点只调 assistant_route()
单查询；助播知识库链序收口在 assistant_match_chain() 单一入口（E-A3：
comment/chatbox 分流处唯一插入点，主 KB 产线只接收"助播未命中"标记，
同一条弹幕不重复过助播 KB）。

权威链序（D6）：助播KB（音频→文本）→ 主KB（对应勾选时）→ LLM 原链。
知识库前置匹配仅适用于 AI对话（chat_source=comment）与聊天（chat_source=chatbox
半边）；其余类型直 TTS 不过知识库（念弹幕=念原文）。
"""
import difflib
import os
import threading
import time
import traceback

from ..base import logger
from ..common.text_mixin import normalize_for_match, tokenize_for_match, token_similarity

# 助播消息类型（与 playback_manager.ASSISTANT_PLAYBACK_TYPES 同语义，勿改字面量）
ASSISTANT_TEXT_TYPE = "assistant_anchor_text"
ASSISTANT_AUDIO_TYPE = "assistant_anchor_audio"

# 默认助播名（assistant_anchor.username 为空时的兜底）
DEFAULT_ANCHOR_NAME = "助播"

# 数据驱动路由表（D10/E-H1）：产线 → {来源: assistant_anchor.type 勾选键}。
# 来源 None = 不分来源。新消息源接入=在此加一行（native CEO 1.2）。
ROUTE_TABLE = {
    # comment LLM 回复：chat_source 区分 AI对话（弹幕）与聊天（聊天页）
    "llm_reply": {"comment": "ai_chat", "chatbox": "chat"},
    # 主 KB 命中产线（local_qa_handle）：同上区分（D6，chat_type=local_qa 模式）
    "main_kb_hit": {"comment": "ai_chat", "chatbox": "chat"},
    # 复读归拢"聊天"（D2 终门 uc1a：勾选后助播外放不再抢占数字人；未勾选维持 999 原链）
    "reread": {None: "chat"},
    # 念弹幕（念弹幕原文，直 TTS 不过知识库——D5）
    "read_comment": {None: "read_comment"},
    "gift": {None: "gift"},
    "entrance": {None: "entrance"},
    "follow": {None: "follow"},
    "schedule": {None: "schedule"},
    "idle_time_task": {None: "idle_time_task"},
    "integral": {None: "integral"},
    "song": {None: "song"},
}

# 助播音色合法值（与 synthesis_manager 弹幕链 tts_handle 白名单对齐；E-E1：
# 仓库存在 edge_tts（规范）/edge-tts（存量）两种拼写，读取时统一规范化）
_TTS_TYPE_ALIASES = {"edge-tts": "edge_tts"}
_TTS_TYPE_WHITELIST = ("gpt_sovits", "edge_tts")
_warned_tts_values = set()


def assistant_route(config, producer_type, source=None):
    """路由查询（D10 单查询）：产线+来源 → 勾选键 → assistant_anchor.type 是否含该键。

    Args:
        config: 主进程 Config 对象（get(*keys) 变长键）
        producer_type (str): 产线标识（ROUTE_TABLE 键）
        source (str | None): 消息来源（chat_source：comment/chatbox/voice…）

    Returns:
        bool: True=该消息改走助播流程
    """
    try:
        mapping = ROUTE_TABLE.get(producer_type)
        if not mapping:
            return False
        key = mapping.get(source, mapping.get(None))
        if key is None:
            return False
        checked = config.get("assistant_anchor", "type") or []
        return key in checked
    except Exception as e:
        logger.warning(f"助播路由查询异常（退回原路）：{e}")
        return False


def resolve_assistant_tts(config):
    """助播音色解析（E-E1）：读取 assistant_anchor.audio_synthesis_type，
    连字符值规范化（edge-tts→edge_tts），非法/空/参数节缺失回落主链
    audio_synthesis_type（保持 F3 热修前现状语义）并 warn 一次。

    Returns:
        tuple: (tts_type, params_section_or_None)
    """
    try:
        anchor_type = config.get("assistant_anchor", "audio_synthesis_type")
        anchor_type = _TTS_TYPE_ALIASES.get(anchor_type, anchor_type)
        if anchor_type in _TTS_TYPE_WHITELIST:
            params = config.get(anchor_type)
            if params:
                return anchor_type, params
            # 引擎合法但参数节缺失——同非法值回落（合成侧会失败）
        if anchor_type and anchor_type not in _warned_tts_values:
            _warned_tts_values.add(anchor_type)
            logger.warning(
                f"助播音色配置 assistant_anchor.audio_synthesis_type={anchor_type!r} "
                f"非法或参数节缺失（合法值：{_TTS_TYPE_WHITELIST}），回落主链音色（E-E1，本值仅告警一次）")
    except Exception as e:
        logger.warning(f"助播音色解析异常，回落主链音色：{e}")
    main_type = config.get("audio_synthesis_type")
    return main_type, config.get(main_type)


def render_answer(text, username=None):
    """词库回答共享渲染（DRY：feature_handler.local_qa_render 委托至此——
    变量替换 {username}/{cur_time} + [1|2] 随机括号语法）。
    F11 修复（2026-10-27 实施中发现）：原 local_qa_render 调用不存在的
    random_bracket_value → [1|2] 随机语法静默失效（异常降级原文）——
    实际方法为 brackets_text_randomize。"""
    try:
        from ..common import Common
        common = Common()
        variables = {
            "cur_time": common.get_bj_time(5),
            "username": username or "",
        }
        if any(var in text for var in variables):
            text = text.format(**{var: value for var, value in variables.items() if var in text})
        return common.brackets_text_randomize(text)
    except Exception as e:
        logger.error(f"助播词库渲染失败（降级原文）：{e}")
        return text


# ---------- 助播知识库（文本 + 音频匹配，F4 接线）----------

# 词库文件缓存：path -> (mtime, entries)。E-E3：文件删除→缓存失效→空词条集。
_text_cache = {}
# 音频目录缓存：dir -> (mtime, [filenames])
_audio_cache = {}


def _split_kb_line(line):
    """E-S1 管线顺序：加载器先切分——剥离 [1|2] 随机括号后定位首个 `|` 分隔符
    （括号内的 | 不参与切分），返回 (question, answer) 或 None（脏行）。"""
    bracket_spans = []
    depth = 0
    start = -1
    for i, ch in enumerate(line):
        if ch == "[":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "]":
            if depth > 0:
                depth -= 1
                if depth == 0:
                    bracket_spans.append((start, i))
    def in_bracket(idx):
        return any(s <= idx <= e for s, e in bracket_spans)
    for idx, ch in enumerate(line):
        if ch == "|" and not in_bracket(idx):
            return line[:idx].strip(), line[idx + 1:].strip()
    return None


def load_assistant_text_kb(file_path):
    """加载助播文本词库（E-E3）：UTF-8(errors='replace')、按行、脏行跳过并计数；
    mtime 缓存；文件删除→缓存失效→空词条集。"""
    try:
        if not file_path or not os.path.exists(file_path):
            if file_path:
                cached = _text_cache.get(file_path)
                if cached is not None and cached[0] is not None:
                    logger.warning(f"助播词库文件已删除：{file_path}（按空词条集处理，E-E3）")
                _text_cache[file_path] = (None, [])
            return []
        mtime = os.path.getmtime(file_path)
        cached = _text_cache.get(file_path)
        if cached is not None and cached[0] == mtime:
            return cached[1]
        entries = []
        dirty = 0
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            for raw in f:
                line = raw.strip()
                if not line:
                    continue
                pair = _split_kb_line(line)
                if pair is None:
                    dirty += 1
                    continue
                q, a = pair
                if not q or not a:
                    dirty += 1
                    continue
                # D4：词条加载时分词一次并随 mtime 缓存（运行时仅对查询分词）
                entries.append((q, a, tokenize_for_match(q)))
        if dirty:
            logger.warning(f"助播词库脏行跳过 {dirty} 行：{file_path}（E-E3 计数）")
        _text_cache[file_path] = (mtime, entries)
        return entries
    except Exception as e:
        logger.error(f"助播词库加载失败（按空词条集处理）：{file_path}，{e}")
        return []


def match_assistant_text(config, content):
    """助播文本词库匹配（D4/D5）：精确短路（归一化全等）→ calculate_similarity
    最优命中（阈值=assistant_anchor.local_qa.text.similarity）。"""
    try:
        file_path = config.get("assistant_anchor", "local_qa", "text", "file_path")
        try:
            threshold = float(config.get("assistant_anchor", "local_qa", "text", "similarity"))
        except (TypeError, ValueError):
            threshold = 0.8
        entries = load_assistant_text_kb(file_path)
        if not entries:
            return None
        q_norm = normalize_for_match(content)
        q_tokens = tokenize_for_match(content)
        best_answer, best_sim = None, 0.0
        for q, a, qk_tokens in entries:
            if q_norm and normalize_for_match(q) == q_norm:
                return a
            sim = _similarity(content, q, b_tokens=qk_tokens)
            if sim > best_sim and sim >= threshold:
                best_sim, best_answer = sim, a
        return best_answer
    except Exception as e:
        logger.error(f"助播文本匹配失败（未命中处理）：{e}")
        return None


def _similarity(a, b, b_tokens=None):
    """相似度（与 Common.calculate_similarity 同源；词条预分词经 b_tokens 免逐查询分词）。"""
    from ..common import Common
    return Common.calculate_similarity(a, b, b_tokens=b_tokens)


def _list_audio_titles(dir_path):
    """音频目录文件列表（mtime 缓存；目录缺失/删除→空列表）。"""
    try:
        if not dir_path or not os.path.isdir(dir_path):
            _audio_cache[dir_path] = (None, [])
            return []
        mtime = os.path.getmtime(dir_path)
        cached = _audio_cache.get(dir_path)
        if cached is not None and cached[0] == mtime:
            return cached[1]
        names = [f for f in os.listdir(dir_path)
                 if f.lower().endswith((".mp3", ".wav", ".flac", ".m4a", ".ogg"))]
        _audio_cache[dir_path] = (mtime, names)
        return names
    except Exception as e:
        logger.error(f"助播音频目录读取失败：{dir_path}，{e}")
        return []


def _audio_title_base(filename):
    """标题归一化前置步：去扩展名（D5 共享口径的音频侧额外步骤）。"""
    base, _ = os.path.splitext(filename)
    return base


def match_assistant_audio(config, content):
    """助播音频匹配（D5 三级归一）：归一化（去扩展名/去全部空白/全半角/大小写）→
    ①精确 → ②包含 → ③difflib 相似度；同分取标题更长者。
    档位（E-T2 映射语义）：包含关系档=①+②；相似度匹配档=①+②+③。

    Returns:
        str | None: 命中的音频文件绝对路径
    """
    try:
        dir_path = config.get("assistant_anchor", "local_qa", "audio", "file_path")
        try:
            threshold = float(config.get("assistant_anchor", "local_qa", "audio", "similarity"))
        except (TypeError, ValueError):
            threshold = 0.5
        tier = config.get("assistant_anchor", "local_qa", "audio", "type") or "相似度匹配"
        names = _list_audio_titles(dir_path)
        if not names:
            return None
        q_norm = normalize_for_match(content)
        if not q_norm:
            return None
        # 归一化标题表（音频侧额外步骤仅去扩展名，其余共享口径）
        titles = [(name, normalize_for_match(_audio_title_base(name))) for name in names]
        # ① 精确
        for name, t_norm in titles:
            if t_norm and t_norm == q_norm:
                return os.path.abspath(os.path.join(dir_path, name))
        # ② 包含（任一方向）
        if tier in ("包含关系", "相似度匹配"):
            contains_hits = [(name, t_norm) for name, t_norm in titles
                             if t_norm and (t_norm in q_norm or q_norm in t_norm)]
            if contains_hits:
                best = max(contains_hits, key=lambda x: len(x[1]))
                return os.path.abspath(os.path.join(dir_path, best[0]))
        # ③ difflib 相似度（仅相似度匹配档）
        if tier == "相似度匹配":
            best_name, best_score = None, 0.0
            for name, t_norm in titles:
                if not t_norm:
                    continue
                score = difflib.SequenceMatcher(None, q_norm, t_norm).ratio()
                if score > best_score and score >= threshold:
                    best_score, best_name = score, name
            if best_name:
                return os.path.abspath(os.path.join(dir_path, best_name))
        return None
    except Exception as e:
        logger.error(f"助播音频匹配失败（未命中处理）：{e}")
        return None


def assistant_match_chain(config, source, content, username=None):
    """助播知识库匹配链（E-A3 唯一插入点；D5/D6 权威链序的助播段）。

    门控：来源须勾选对应选项（comment→AI对话 / chatbox→聊天，D6），未勾选
    直接 miss（主 KB/LLM 原链照旧）。链序=音频匹配→文本匹配；主 KB 匹配
    不在此处——调用方收到 miss 后继续既有主流程（主 KB 产线只接收"助播
    未命中"标记，同一条弹幕不重复过助播 KB）。

    Returns:
        tuple: (hit_layer, payload)
            ("assistant_audio", 音频绝对路径) —— 调用方产 assistant_anchor_audio 消息
            ("assistant_text", 渲染后答案)     —— 调用方产 assistant_anchor_text 消息
            ("miss", None)                    —— 调用方继续主 KB/LLM 原链
    """
    if not assistant_route(config, "llm_reply", source):
        return ("miss", None)
    try:
        if config.get("assistant_anchor", "local_qa", "audio", "enable"):
            audio_path = match_assistant_audio(config, content)
            if audio_path:
                _record("kb_chain", source, "assistant_audio", True)
                return ("assistant_audio", audio_path)
        if config.get("assistant_anchor", "local_qa", "text", "enable"):
            answer = match_assistant_text(config, content)
            if answer is not None:
                _record("kb_chain", source, "assistant_text", True)
                return ("assistant_text", render_answer(answer, username))
    except Exception as e:
        logger.error(f"助播知识库匹配链异常（按未命中处理）：{e}\n" + traceback.format_exc())
    _record("kb_chain", source, "miss", False)
    return ("miss", None)


def assistant_copy_message(config, content, username=None, producer_type="", source=None,
                           hit_layer="direct", extra=None):
    """构建助播消息（E-A2）：type=assistant_anchor_text、助播音色（E-E1 规范化
    回落）、助播名；返回 dict 附内部元数据（业务链不消费，仅供 D11 观测与
    守卫读取）。extra 合入业务键（如 content_type）。"""
    try:
        anchor_name = config.get("assistant_anchor", "username") or DEFAULT_ANCHOR_NAME
    except Exception:
        anchor_name = DEFAULT_ANCHOR_NAME
    tts_type, tts_params = resolve_assistant_tts(config)
    message = {
        "type": ASSISTANT_TEXT_TYPE,
        "tts_type": tts_type,
        "data": tts_params,
        "config": config.get("filter"),
        "username": anchor_name,
        "content": content,
        # 内部元数据（E-A2）：业务链不消费；观测/守卫读取
        "_anchor_meta": {
            "producer_type": producer_type,
            "source": source,
            "hit_layer": hit_layer,
            "ts": time.time(),
        },
    }
    if extra:
        message.update(extra)
    _record(producer_type, source, hit_layer, True)
    return message


# ---------- 路由观测（D11/E-A2 数据源） ----------

class _RouteRecorder:
    """线程安全路由决策记录器：每 (产线,来源) 最近一次决策 + 累计计数。"""

    def __init__(self):
        self._lock = threading.Lock()
        self._counters = {}
        self._last = {}

    def record(self, producer_type, source, hit_layer, routed):
        key = f"{producer_type}|{source or '-'}"
        with self._lock:
            self._counters[key] = self._counters.get(key, 0) + 1
            self._last[key] = {
                "producer_type": producer_type,
                "source": source,
                "hit_layer": hit_layer,
                "routed": bool(routed),
                "ts": time.time(),
            }

    def snapshot(self):
        with self._lock:
            return {
                "counters": dict(self._counters),
                "last": {k: dict(v) for k, v in self._last.items()},
            }


_recorder = _RouteRecorder()


def _record(producer_type, source, hit_layer, routed):
    try:
        _recorder.record(producer_type, source, hit_layer, routed)
    except Exception:
        pass


def get_route_stats():
    """D11 观测面板取数（只读快照）：counters + last。"""
    return _recorder.snapshot()
