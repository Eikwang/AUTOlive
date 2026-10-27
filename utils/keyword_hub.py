# -*- coding: UTF-8 -*-
"""
关键词识别中枢（信息承接中枢·Keyword 节点，2026-10-25 核心架构调整）

对照流程图 docs/flowchartTD.md：所有信息源（弹幕/语音聊天 Capture/系统任务
Idle·Timed·DynamicCopy）经分类处理与过滤后统一进入本中枢，单次完成全部功能
的关键词识别判断，产出 KeywordResult 分发到各模块消费——消费侧零重复匹配
（原架构各模块独立扫描文本：积分词表/复读词表/按键映射 difflib/本地问答
全库相似度/点歌曲库 difflib/编排关键词，开启多模块时重复开销线性叠加）。

源路由（recognize）：
  comment / talk                          → 全量匹配器（积分签到/查询、复读、
                                            按键映射弹幕型、本地问答、点歌意图、
                                            动作编排关键词）
  gift                                    → 礼物匹配器（按键映射礼物组、礼物点歌
                                            映射、编排接管判定）
  schedule / idle_time_task / copywriting → 系统任务匹配器（仅动作编排关键词——
                                            系统文案无互动身份，不触发积分/点歌/
                                            按键映射/本地问答，照常入队播报）

消费契约（hub_result 通道）：
  各消费函数 hub_result 参数非 None = 中枢已判定（信任结果，跳过内部匹配）；
  None = 自行匹配（直调/既有测试兼容）。回复侧按键映射（type=回复）匹配对象
  是 LLM 回复文本，识别时不存在，仍由调用点在回复生成后单点匹配（不传
  hub_result，无重复问题）。

健壮性：recognize 内部全兜底，永不抛异常——识别异常降级空结果并 ERROR 记录，
不中断直播消息链。
"""

from dataclasses import dataclass, field

from .base import logger


@dataclass
class KeywordResult:
    """单条消息的关键词识别结果（中枢一次性产出，分发到各模块消费）。"""

    source: str = ""                    # 消息源：comment/talk/gift/schedule/idle_time_task/copywriting
    content: str = ""                   # 参与识别的文本（弹幕=过滤后内容；礼物=礼物名）

    # —— 积分系统（Points：Calc 计算积分 / look 查询积分）——
    integral_sign: bool = False         # 签到命令词命中
    integral_query: bool = False        # 查询积分命令词命中

    # —— 复读（Interact）——
    reread_type: str = ""               # "reread_top_priority" | "reread" | ""（未命中）

    # —— 按键映射（Keymap→SendPort）——
    # 平铺列表 [(组配置dict, 命中文本)]，保持原语义：同组多词/重复礼物名可多次
    # 触发（once=False 时），消费侧逐条分派
    keymap_groups: list = field(default_factory=list)       # 弹幕型文本命中组
    keymap_gift_groups: list = field(default_factory=list)  # 礼物名命中组

    # —— 本地问答（Interact）——
    local_qa_answer: str = ""           # 命中答案（已渲染变量）；空串=未命中/未启用

    # —— 点歌模式（SongMode：Match 匹配成功/miss 匹配失败）——
    song_verdict: object = None         # match_song_intent 结果（None=未触发）
    gift_song: str = ""                 # 礼物→歌曲映射命中歌名；空串=未命中

    # —— 动作编排（编排关键词订阅，判定即触发画面序列）——
    choreo_accepted: bool = False       # 关键词编排已触发
    choreo_takeover: bool = False       # 礼物编排接管答谢（gift 源）

    @property
    def has_reread(self) -> bool:
        return bool(self.reread_type)


class KeywordHub:
    """关键词识别中枢（信息承接中枢单点，My_handle 持有）。"""

    # 需要全量识别的消息源
    TEXT_SOURCES = ("comment", "talk")
    # 系统任务源（仅编排关键词识别）
    SYSTEM_SOURCES = ("schedule", "idle_time_task", "copywriting")

    def __init__(self, my_handle):
        self.my_handle = my_handle

    # ==================== 主入口 ====================

    def recognize(self, data, source):
        """统一识别入口：按源路由匹配器，一次完成全部功能的关键词判断。

        Args:
            data (dict): 消息数据（comment/talk 含 content；gift 含 gift_name）
            source (str): 消息源类型

        Returns:
            KeywordResult: 识别结果（异常降级空结果，不抛出）
        """
        try:
            data = data or {}
            if source in self.TEXT_SOURCES:
                return self._recognize_text(data, source)
            if source == "gift":
                return self._recognize_gift(data)
            if source in self.SYSTEM_SOURCES:
                return self._recognize_system_copy(data, source)
            return KeywordResult(source=str(source or ""))
        except Exception:
            logger.error("关键词识别中枢异常（降级空结果，消息链继续）", exc_info=True)
            return KeywordResult(source=str(source or ""))

    # ==================== 源识别器 ====================

    @staticmethod
    def _safe(fn, default):
        """子匹配器隔离：单项异常降级默认值，不丢弃同源其它匹配结果
        （识别是聚合操作，单项故障只损失该项）。"""
        try:
            return fn()
        except Exception as e:
            logger.error(f"关键词识别子项异常（降级默认值）：{e}", exc_info=True)
            return default

    def _recognize_text(self, data, source):
        """弹幕/语音源全量识别（comment 全功能；talk 复读不开——语音链复读自身
        无意义，编排订阅 2026-10-27 对齐流程图 Capture→Keyword→动作编排 统一
        识别语义：识别到关键词即并行触发画面序列）。"""
        content = str(data.get("content", "") or "")
        with_reread, with_choreo = self._TEXT_FEATURES.get(
            source, (True, True))

        rec = KeywordResult(source=source, content=content)
        rec.integral_sign, rec.integral_query = self._safe(
            lambda: self._match_integral(content), (False, False))
        if with_reread:
            rec.reread_type = self._safe(lambda: self._match_reread(content), "")
        rec.keymap_groups = self._safe(lambda: self._match_keymap_danmu(content), [])
        rec.local_qa_answer = self._safe(lambda: self._match_local_qa(data), "")
        rec.song_verdict = self._safe(lambda: self._match_song(content), None)
        # ③ 自源守卫（2026-10-27）：闲时任务系统源（origin=system-idle）跳过编排
        # 触发——闲时文案/LLM 触发链内容可能含编排关键词，系统自引发画面切换属
        # 非预期行为；LLM 对话触发语义保持（process_data 照常入链）。
        if with_choreo and not (source == "comment" and data.get("origin") == "system-idle"):
            rec.choreo_accepted = self._safe(lambda: self._match_choreo_keyword(content), False)
        return rec

    def _recognize_gift(self, data):
        """礼物源识别：按键映射礼物组 + 礼物点歌映射 + 编排接管判定。"""
        gift_name = str(data.get("gift_name", "") or "").strip()
        rec = KeywordResult(source="gift", content=gift_name)
        rec.keymap_gift_groups = self._safe(lambda: self._match_keymap_gift(gift_name), [])
        rec.gift_song = self._safe(lambda: self._match_gift_song(gift_name), "")
        rec.choreo_takeover = self._safe(lambda: self._match_choreo_gift(data), False)
        return rec

    def _recognize_system_copy(self, data, source):
        """系统任务源识别（闲时/定时/动态文案）：仅编排关键词判定——
        统一入链（流程图三源→Keyword→Queue），识别后照常入队播报。"""
        content = str(data.get("content", "") or "")
        rec = KeywordResult(source=source, content=content)
        rec.choreo_accepted = self._safe(lambda: self._match_choreo_keyword(content), False)
        return rec

    # talk 源功能集开关：(复读, 编排)——复读保持关闭（语音链复读自身无意义）；
    # 编排 2026-10-27 启用（flowchartTD Capture→Keyword→动作编排 统一识别语义）
    _TEXT_FEATURES = {
        "comment": (True, True),
        "talk": (False, True),
    }

    # ==================== 匹配器（严格对照原实现语义） ====================

    @staticmethod
    def _kw_list(raw, comma_fallback=True):
        """词表归一化：str/list 双形态。str 先按换行拆，空则按逗号拆
        （口径=原 reread_handle 的 str 分支——换行分隔词表在原 comment 内联
        匹配中漏判，中枢统一按消费侧口径修复）。"""
        if isinstance(raw, str):
            parts = [k.strip() for k in raw.split("\n") if k.strip()]
            if not parts and comma_fallback:
                parts = [k.strip() for k in raw.split(",") if k.strip()]
            return parts
        return [str(k).strip() for k in (raw or []) if str(k).strip()]

    # —— 积分（原 _integral_sign_handle / _integral_query_handle 的命中判断段）——

    def _match_integral(self, content):
        """积分命令词命中：(签到命中, 查询命中)。enable 缺省开（与消费侧
        _integral_*_handle 缺省语义一致，缺键告警留消费侧）。
        审查修复（发布批次）：入口 strip——原消费侧 _integral_*_handle 先
        strip 再匹配，中枢必须同口径，否则「签到 」类带空白弹幕漏判。"""
        content = str(content or "").strip()
        if not self.my_handle.config.get("integral", "enable"):
            return False, False

        sign = self._integral_cmd_hit(
            content, "sign", "cmd", ["签到", "打卡"])

        query = False
        query_cfg = self.my_handle.config.get("integral", "crud", "query") or {}
        query_enable = query_cfg.get("enable")
        if query_enable is None:
            query_enable = True
        if query_enable:
            cmds = query_cfg.get("cmd") or ["我的积分", "查询积分"]
            query = content in cmds
        return sign, query

    def _integral_cmd_hit(self, content, behavior, key, default_cmds):
        enable = self.my_handle.config.get("integral", behavior, "enable")
        if enable is None:
            enable = True
        if not enable:
            return False
        cmds = self.my_handle.config.get("integral", behavior, key)
        if not cmds:
            cmds = default_cmds
        return content in cmds

    # —— 复读（原 comment_handle 内联词表匹配 + reread_handle）——

    def _match_reread(self, content):
        """复读命中类型：top_priority 优先于普通词表。"""
        if not self.my_handle.config.get("reread", "enable"):
            return ""
        for kw in self._kw_list(self.my_handle.config.get("reread", "top_priority_keywords")):
            if kw and kw in content:
                return "reread_top_priority"
        for kw in self._kw_list(self.my_handle.config.get("reread", "keywords")):
            if kw and kw in content:
                return "reread"
        return ""

    # —— 按键映射·弹幕型（原 key_mapping_handle 文本分支匹配段）——

    def _match_keymap_danmu(self, content):
        """按键映射弹幕型命中组：start_cmd 剥离 + 组关键词 difflib 相似度
        （口径与原实现一致：SequenceMatcher(None, content, keyword).ratio()
        ≥ 组 similarity）。命中组平铺（不截断），once 语义由消费侧分派保持。"""
        if not self.my_handle.config.get("key_mapping", "enable"):
            return []
        ktype = self.my_handle.config.get("key_mapping", "type")
        if ktype not in ("弹幕", "弹幕+回复"):
            return []
        text = str(content or "")
        start_cmd = self.my_handle.config.get("key_mapping", "start_cmd")
        if start_cmd and text.startswith(start_cmd):
            text = text[len(start_cmd):]

        matched = []
        import difflib
        for group in self.my_handle.config.get("key_mapping", "config") or []:
            try:
                similarity = float(group.get("similarity", 1))
            except (TypeError, ValueError):
                similarity = 1.0
            for keyword in group.get("keywords") or []:
                if difflib.SequenceMatcher(None, text, keyword).ratio() >= similarity:
                    matched.append((group, keyword))
        return matched

    # —— 按键映射·礼物组（原 key_mapping_handle 礼物分支匹配段）——

    def _match_keymap_gift(self, gift_name):
        """按键映射礼物命中组：礼物名精确匹配（平铺不截断，保持原语义）。"""
        if not self.my_handle.config.get("key_mapping", "enable"):
            return []
        if not gift_name:
            return []
        matched = []
        for group in self.my_handle.config.get("key_mapping", "config") or []:
            for gift in group.get("gift") or []:
                if gift == gift_name:
                    matched.append((group, gift))
        return matched

    # —— 本地问答（原 local_qa_handle 匹配段，纯查询复用 local_qa_match）——

    def _match_local_qa(self, data):
        """本地问答命中答案（已渲染）；chat_type≠local_qa 或未启用返回空串。"""
        if self.my_handle.config.get("chat_type") != "local_qa":
            return ""
        if not self.my_handle.config.get("local_qa", "enable"):
            return ""
        content = str(data.get("content", "") or "")
        answer = self.my_handle.feature_handler.local_qa_match(
            content, username=data.get("username"))
        return answer or ""

    # —— 点歌意图（原 choose_song_handle 匹配段）——

    def _match_song(self, content):
        """弹幕点歌意图判定（None=未触发）。"""
        if not self.my_handle.config.get("choose_song", "enable"):
            return None
        from utils.common.text_mixin import match_song_intent
        trigger_words = self.my_handle.config.get("choose_song", "start_cmd") or []
        similarity = self.my_handle.config.get("choose_song", "similarity")
        if similarity is None:
            similarity = 0.5
        song_names = self.my_handle.feature_handler._song_song_names()
        return match_song_intent(content, trigger_words, song_names, similarity)

    # —— 礼物点歌映射（原 choose_song_gift_trigger 匹配段）——

    def _match_gift_song(self, gift_name):
        if not self.my_handle.config.get("choose_song", "enable"):
            return ""
        if not gift_name:
            return ""
        from utils.common.text_mixin import match_gift_to_song_entry
        mapping = self.my_handle.config.get("choose_song", "gift_song_map") or {}
        return match_gift_to_song_entry(gift_name, mapping) or ""

    # —— 动作编排（判定即触发，单点调用无重复）——

    def _match_choreo_keyword(self, content):
        try:
            from utils.orchestration import get_choreographer
            choreo = get_choreographer()
            if choreo is None:
                return False
            return bool(choreo.on_keyword(content))
        except Exception as e:
            logger.debug(f"编排关键词判定异常（忽略）：{e}")
            return False

    def _match_choreo_gift(self, data):
        try:
            from utils.orchestration import get_choreographer
            choreo = get_choreographer()
            if choreo is None:
                return False
            return bool(choreo.on_gift(data))
        except Exception as e:
            logger.debug(f"编排礼物判定异常（忽略）：{e}")
            return False
