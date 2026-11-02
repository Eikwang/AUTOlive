# -*- coding: UTF-8 -*-
"""
弹幕处理器模块

处理弹幕相关功能
"""

import os
import random
import traceback
from ..error_handler import handle_error, safe_execute
from ..base import logger


class CommentHandler:
    """弹幕处理器"""

    # 违禁词/拼音词表缓存：path -> (mtime, words, pinyin_words)。
    # 词表每弹幕现读+逐词转拼音开销不可接受（拼音表数百词 × pypinyin 每条弹幕
    # 重算 = 秒级卡顿），按文件 mtime 惰性缓存，文件变更自动重载。
    _BADWORDS_CACHE: dict = {}

    def __init__(self, my_handle):
        """
        初始化弹幕处理器

        Args:
            my_handle: My_handle 实例
        """
        self.my_handle = my_handle

    def _load_badwords(self, path):
        """读取违禁词表（mtime 缓存）。

        Args:
            path: 词表文件路径（badwords.path / badwords.bad_pinyin_path）

        Returns:
            tuple[list, list]: (原词列表, 各词对应拼音列表)；路径为空/文件缺失/
            解码失败返回 ([], [])——解码失败时 read_sensitive_word_file 已显式
            ERROR 告警（E4：静默空表=过滤失效），此处按 mtime 缓存失败态防每条
            弹幕重复刷日志。
        """
        if not path:
            return [], []
        path = str(path)
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            return [], []
        cached = CommentHandler._BADWORDS_CACHE.get(path)
        if cached and cached[0] == mtime:
            return cached[1], cached[2]
        try:
            words = [w for w in self.my_handle.common.read_sensitive_word_file(path) if w]
        except Exception as e:
            logger.error(f"违禁词表加载失败，违禁词过滤失效：{path}：{e}")
            words = []
        pinyin_words = []
        if words:
            try:
                pinyin_words = [self.my_handle.common.text2pinyin(w) for w in words]
            except Exception as e:
                logger.error(f"违禁词表拼音转换失败：{path}：{e}")
                pinyin_words = []
        CommentHandler._BADWORDS_CACHE[path] = (mtime, words, pinyin_words)
        return words, pinyin_words

    def comment_check_and_replace(self, content):
        """
        弹幕格式检查：长度截断 + 表情过滤

        语义（2026-10-30 过滤审查批次）：
        - 最大单词数/最大字符数：超限自动截断，只保留设定值（原实现超 max_len
          整条丢弃，与参数语义"自动截断"不符——丢弃改截断）；截断统一走
          remove_extra_words（合成侧 my_play_voice 同函数再截一次，幂等）
        - 弹幕表情过滤：移除平台表情标签 [xx] 与 Unicode emoji 字符
        - 原 prohibitions 死键检查与 replace_chars 死键替换移除（违禁处理统一
          归 prohibitions_handle 的 badwords 配置组，二者均为模板外死键）

        Args:
            content (str): 弹幕内容

        Returns:
            str: 处理后的弹幕内容，None表示被过滤
        """
        try:
            # 获取配置
            filter_config = self.my_handle.config.get("filter") or {}

            # 检查是否启用过滤
            if not filter_config.get("enable"):
                return content

            # 长度截断（最大单词数/最大字符数，超限自动截断只保留设定值）
            try:
                max_len = int(filter_config.get("max_len") or 30)
                max_char_len = int(filter_config.get("max_char_len") or 50)
            except (TypeError, ValueError):
                max_len, max_char_len = 30, 50
            content = self.my_handle.common.remove_extra_words(content, max_len, max_char_len)

            # 弹幕表情过滤（开启后移除表情，只保留文字）
            if filter_config.get("emoji"):
                content = self.my_handle.common.remove_emoji(content)

            return content
        except Exception as e:
            handle_error(e, {'module': 'handler', 'function': 'unknown'})
            logger.error(f"弹幕检查失败：{e}")
            return content

    def prohibitions_handle(self, content):
        """
        违禁处理（badwords 配置组接线，2026-10-30 过滤审查批次）

        此前读取 filter.prohibitions 死键恒空表——违禁词过滤整组（UI/模板/词表
        文件齐备）后端零消费，属断链；本批次按 UI 参数语义接线：
        - filter.enable 总开关关闭 或 badwords.enable 关闭 → 原样返回
        - 命中违禁词（badwords.path 词表，精确包含匹配）：
          - badwords.discard 开 → None（整条丢弃）
          - 否则 → 命中词替换为 badwords.replace
        - 命中违禁拼音（bad_pinyin_path 同音变体，整句转拼音比对，无法定位原文
          具体词）：discard 开 → None；否则不处理（拼音变体只支持丢弃模式）
        - 词表文件缺失/解码失败 → 过滤失效（显式告警，原样返回）

        消费点：弹幕链/语音链用户名与内容、LLM 回复全文、流式分句（chat_handler）。

        Args:
            content (str): 内容

        Returns:
            str: 处理后的内容，None表示被过滤
        """
        try:
            # 获取配置
            filter_config = self.my_handle.config.get("filter") or {}

            # 检查是否启用过滤（总开关 + badwords 开关）
            if not filter_config.get("enable"):
                return content
            badwords = filter_config.get("badwords") or {}
            if not badwords.get("enable"):
                return content

            discard = bool(badwords.get("discard"))
            replace = str(badwords.get("replace") or "*")

            # 违禁词精确匹配：discard 开=整条丢弃，否则逐词替换
            words, _ = self._load_badwords(badwords.get("path"))
            if words:
                for word in words:
                    if word in content:
                        if discard:
                            logger.info(f"违禁词命中，丢弃：{word}")
                            return None
                        content = content.replace(word, replace)
                        logger.debug(f"违禁词命中，已替换：{word} → {replace}")

            # 违禁拼音匹配（同音变体）：仅丢弃模式生效（无法定位原文词做替换）
            _, pinyin_words = self._load_badwords(badwords.get("bad_pinyin_path"))
            if pinyin_words and discard:
                text_py = self.my_handle.common.text2pinyin(content)
                for pword in pinyin_words:
                    import re as _re
                    if _re.search(r'\b' + _re.escape(pword) + r'\b', text_py):
                        logger.info(f"违禁拼音命中，丢弃：{pword}")
                        return None

            return content
        except Exception as e:
            logger.error(traceback.format_exc())
            logger.error(f"违禁处理失败：{e}")
            return content
    
    def reread_handle(self, data, type=None):
        """
        直接复读处理（手动直通通道）

        2026-10-30 过滤审查批次：弹幕关键词触发复读（「复读触发」配置组：
        reread.enable/keywords/top_priority_keywords + 识别中枢 _match_reread）
        已整体退役；本函数收口为手动直通——消费点=聊天页「直接复读/直接复读-插队首」
        按钮与 HTTP /send（web_server），语义=「直接合成文字并加入播放音频列表，
        不走 AI 对话系统流程」（原 direct 标记语义恒成立，enable/词表匹配随退役删除）。

        Args:
            data (dict): 数据（username/content）
            type (str, optional): 消息类型（reread | reread_top_priority）。
                reread_top_priority 经 priority_mapping 取 999 权重（999 暂停-续播
                链路语义保持，见审查记录 R6(a)）。

        Returns:
            bool: 是否触发并处理
        """
        try:
            username = data["username"]
            content = data["content"]

            logger.info(f"[{username}]直接复读：{content}")

            # 复读归拢"聊天"（D2 终门 uc1a）：聊天 勾选→助播
            # 消息（外放绕行，不再抢占数字人——E-A4 声明的语义变更）；未勾选→原
            # reread/reread_top_priority 消息（999 暂停-续播链路语义保持）。
            from .assistant_router import assistant_route, assistant_copy_message
            _reread_src = (data or {}).get("chat_source") or "comment"
            if assistant_route(self.my_handle.config, "reread", _reread_src):
                message = assistant_copy_message(
                    self.my_handle.config, content,
                    username=username, producer_type="reread", source=_reread_src,
                    hit_layer="direct")
            else:
                # 构建消息（type 原样透传：reread_top_priority→999 权重映射）
                message = {
                    "type": type if type in ("reread", "reread_top_priority") else "reread",
                    "tts_type": self.my_handle.config.get("audio_synthesis_type"),
                    "data": self.my_handle.config.get(self.my_handle.config.get("audio_synthesis_type")),
                    "config": self.my_handle.config.get("filter"),
                    "username": username,
                    "content": content
                }

            # 音频合成
            self.my_handle.audio_synthesis_handle(message)

            return True
        except Exception as e:
            logger.error(traceback.format_exc())
            logger.error(f"直接复读处理失败：{e}")
            return False
    
    def tuning_handle(self, data):
        """
        调音处理
        
        Args:
            data (dict): 数据
            
        Returns:
            bool: 是否触发并处理
        """
        try:
            username = data["username"]
            content = data["content"]
            
            # 检查是否启用调音
            if not self.my_handle.config.get("tuning", "enable"):
                return False
            
            # 检查是否匹配调音命令
            cmd = self.my_handle.config.get("tuning", "cmd")
            if content.startswith(cmd):
                logger.info(f"[{username}]触发调音：{content}")
                
                # 解析调音参数
                params = content[len(cmd):].strip().split()
                if len(params) >= 1:
                    # 设置语速
                    if params[0] == "语速" and len(params) >= 2:
                        try:
                            speed = int(params[1])
                            self.my_handle.audio.set_speed(speed)
                            logger.info(f"设置语速：{speed}")
                        except ValueError:
                            logger.error(f"语速参数错误：{params[1]}")
                    
                    # 设置音量
                    elif params[0] == "音量" and len(params) >= 2:
                        try:
                            volume = int(params[1])
                            self.my_handle.audio.set_volume(volume)
                            logger.info(f"设置音量：{volume}")
                        except ValueError:
                            logger.error(f"音量参数错误：{params[1]}")
                    
                    # 设置音调
                    elif params[0] == "音调" and len(params) >= 2:
                        try:
                            pitch = int(params[1])
                            self.my_handle.audio.set_pitch(pitch)
                            logger.info(f"设置音调：{pitch}")
                        except ValueError:
                            logger.error(f"音调参数错误：{params[1]}")
                
                return True
            
            return False
        except Exception as e:
            logger.error(traceback.format_exc())
            logger.error(f"调音处理失败：{e}")
            return False

    def _read_comment_replay(self, _chat_src, username, content):
        """念弹幕即时复读（2026-10-31 念弹幕审查批次重构）。

        周期触发退役（用户裁决：系统已有限入/去重/优先级队列，不需要定时回放）
        ——过滤链完成的弹幕处理时直接入待合成队列，按 filter.priority_mapping
        .read_comment 优先级排序（"位于优先级排序之后"）。

        念用户名三参数接线（此前 UI 有配置后端零消费）：
        - read_username_enable 开 → 念用户名文案（随机一条，{username} 占位
          替换）+ 截断后用户名 + 弹幕内容；关 → 仅念内容
        - username_max_len 超长截断加省略号（对齐 reply_template G6 口径；
          非法值容错缺省 10）
        - read_username_copywriting 随机文案，空表/非法行过滤，兜底 "用户名说:"

        助播分流（D5 路由判定从 timer 回放点前移至即时入队点，语义不变：
        勾选 read_comment 助播触发 → 助播消息通道；未勾选 → 原路念弹幕）。
        仅弹幕源（聊天页发送消息不复读）。
        """
        if _chat_src != "comment" or not self.my_handle.config.get("read_comment", "enable"):
            return
        rc_content = content
        if self.my_handle.config.get("read_comment", "read_username_enable"):
            uname = username
            try:
                _rc_max_len = int(self.my_handle.config.get("read_comment", "username_max_len"))
            except (TypeError, ValueError):
                _rc_max_len = 10
            if _rc_max_len > 0 and len(uname) > _rc_max_len:
                uname = uname[:_rc_max_len] + "..."
            _rc_tpl = [t for t in (self.my_handle.config.get("read_comment", "read_username_copywriting") or [])
                       if isinstance(t, str) and t.strip()]
            _rc_prefix = random.choice(_rc_tpl).replace("{username}", uname) if _rc_tpl else f"{uname}说:"
            rc_content = f"{_rc_prefix}{content}"
        from .assistant_router import assistant_route, assistant_copy_message
        if assistant_route(self.my_handle.config, "read_comment"):
            self.my_handle.audio_synthesis_handle(assistant_copy_message(
                self.my_handle.config, rc_content,
                producer_type="read_comment", hit_layer="direct"))
        else:
            self.my_handle.audio_synthesis_handle({
                "type": "read_comment",
                "tts_type": self.my_handle.config.get("audio_synthesis_type"),
                "data": self.my_handle.config.get(self.my_handle.config.get("audio_synthesis_type")),
                "config": self.my_handle.config.get("filter"),
                "username": username,
                "content": rc_content,
            })

    def comment_handle(self, data):
        """
        弹幕处理
        
        Args:
            data (dict): 数据
        """
        try:
            username = data["username"]
            content = data["content"]

            # 黑名单拦截（T7 接线：丢弃黑名单用户的一切信息，置于写库前防污染数据分析）
            if self.my_handle.blacklist_handle(data):
                return

            # 输出当前用户发送的弹幕消息
            logger.info(f"[{username}]: {content}")

            # 聊天记录（2026-10-25 重构）：弹幕监听与聊天框互动全程记录——输入侧
            # question（回复侧 answer 在下方回复段）。chat_source：chatbox=聊天框
            # 发送（web_server /send 打标），comment=弹幕监听；开关已退役固定记录
            _is_chatbox = data.get("chat_source") == "chatbox"
            _chat_src = data.get("chat_source") or "comment"  # 弹幕监听默认源
            if "ori_username" not in data:
                data["ori_username"] = data["username"]
            if "ori_content" not in data:
                data["ori_content"] = data["content"]
            # dict 副本打标：不污染原 data（下游点歌/引导等发送点不受牵连）
            self.my_handle.webui_show_chat_log_callback(
                "聊天发送" if _is_chatbox else "弹幕信息",
                dict(data, chat_source=_chat_src),
                data["ori_content"], content_type="question")

            # 用户名以及弹幕违禁判断
            username = self.prohibitions_handle(username)
            if username is None:
                return

            # 用户名表情/特殊字符过滤（filter.emoji 与弹幕内容同开关，
            # 2026-10-31 过滤优化批次）：用户名含表情（[爱心]/emoji）或特殊
            # 字符时，语音合成念用户名会产生大量错误语音；先清洁后（合成侧
            # 恒有）数字转中文。纯表情用户名过滤后为空 → 保留原文兜底。
            # 写库/念弹幕缓冲/聊天记录（ori_username 留原文）/LLM 输入统一
            # 使用清理后用户名；黑名单/积分在上方已用原始 data 判定，不受影响。
            if self.my_handle.config.get("filter", "emoji"):
                username = self.my_handle.common.clean_username(username) or username

            content = self.prohibitions_handle(content)
            if content is None:
                return

            # 弹幕格式检查（长度截断+表情过滤）
            content = self.comment_check_and_replace(content)
            if content is None:
                return
            # 表情过滤后可能内容为空（纯表情弹幕）
            if not content:
                return

            # 弹幕翻译（2026-10-31 翻译审查批次：补全 trans_type=弹幕/弹幕+回复——
            # 此前「弹幕」类型全库零消费，外语弹幕原样进 LLM。任务书语义：收到的
            # 信息非「通用配置-回复语言」时翻译为设定语言再处理。位于语言过滤前：
            # 译文语言=回复语言，lang_filter 自然放行，翻译与 G3 过滤叠加语义一致）
            if self.my_handle.config.get("translate", "enable") and (self.my_handle.config.get("translate", "trans_type") == "弹幕" or \
                self.my_handle.config.get("translate", "trans_type") == "弹幕+回复"):
                content = self.my_handle.my_translate.trans_comment(content)

            # 回复语言过滤（G3，基础功能 2026-10-22：need_lang≠none 时检测弹幕语言）
            if not self.my_handle.lang_filter_pass(content):
                return

            # 判断字符串是否全为标点符号，是的话就过滤
            if self.my_handle.common.is_punctuation_string(content):
                logger.debug(f"用户:{username}]，发送纯符号的弹幕，已过滤")
                return

            # 念弹幕即时复读（2026-10-31 念弹幕审查批次重构）：周期触发退役
            # （系统已有限入/去重/优先级队列，弹幕处理时直接入待合成队列按
            # read_comment 优先级排序——"位于优先级排序之后"）；缓冲+定时回放
            # 机制删除。仅弹幕源（聊天页消息不入）；过滤链完成后到达此处的
            # 弹幕已经违禁/表情/语言/纯符号清洗。
            self._read_comment_replay(_chat_src, username, content)

            # ===== 关键词识别中枢（2026-10-25 核心架构调整，flowchartTD Keyword 节点）=====
            # 过滤链完成后单次识别全部功能的关键词命中，产出 rec 分发到各模块——
            # 消费侧零重复匹配（原实现各模块独立扫描文本：积分词表/复读词表/
            # 按键映射 difflib/本地问答全库相似度/点歌曲库 difflib/编排关键词，
            # 开启多模块时重复开销线性叠加）。UC-2 关键性质不变：过滤链先于
            # 写库（下方），违禁弹幕不入库、写库内容=过滤后弹幕。
            # 审查修复（发布批次）：回写过滤后文本——流程图 Keyword 在 Filter
            # 下游，识别输入=过滤后弹幕（WP-11/CL-3 义务）；原文留存 ori_content
            if "ori_content" not in data:
                data["ori_content"] = data["content"]
            data["content"] = content
            rec = self.my_handle.keyword_recognize(data, "comment")

            # 记录数据库（Stats：流程图 Keyword→Stats{开启→DB 写入}——中枢识别
            # 后写库，与图一致；写库内容仍为过滤后弹幕）
            if self.my_handle.config.get("database", "comment_enable"):
                from datetime import datetime
                insert_data_sql = '''
                INSERT INTO danmu (username, content, ts) VALUES (?, ?, ?)
                '''
                # 2026-10-28 优化批次（模块 2）：写库投后台队列——SQLite 连接池
                # （db.py check_same_thread=False + WAL）跨线程安全；过滤后弹幕
                # 不入库的风险由 background_tasks 满队降级同步兜底
                from ..background_tasks import submit
                submit(self.my_handle.db.execute, insert_data_sql,
                       (username, content, datetime.now()))

            # 0、积分机制运转（中枢已判定命令词命中，消费侧跳过重复匹配）
            if rec.integral_sign and self.my_handle.integral_handle("comment", data, hub_result=rec):
                return
            if rec.integral_query and self.my_handle.integral_handle("crud", data, hub_result=rec):
                return

            # 动作编排关键词订阅（/autoplan 2026-10-21 批次）：中枢识别时已判定并
            # 触发画面序列（rec.choreo_accepted 供观测），弹幕本身照常走后续流程
            # （无答谢/拦截语义）。行为差异说明：原实现在积分之后（积分命中 return
            # 即不触发编排），中枢化后识别先行——签到/查询弹幕同时命中编排词表
            # 也会触发画面序列（统一识别语义，flowchartTD Keyword 先于各模块）。

            # 判断按键映射触发类型（弹幕型：中枢已匹配命中组直接分派；type 显式门卫
            # 与原实现等价——中枢侧 ktype 路由为第一道闸，此处保留链侧显式闸）
            if self.my_handle.config.get("key_mapping", "type") == "弹幕" or self.my_handle.config.get("key_mapping", "type") == "弹幕+回复":
                if self.my_handle.key_mapping_handle("弹幕", data, hub_result=rec):
                    return

            # 助播知识库前置匹配（E-A3 唯一插入点，D6 权威链序：助播KB→主KB→LLM；
            # 十生产端接线之 comment LLM 回复的前置段）：
            # 门控=来源勾选对应选项（comment 源→AI对话、chatbox 源→聊天；D6）；
            # 命中音频→assistant_anchor_audio 直接播放；命中文本→assistant_anchor_text
            # 助播 TTS；未命中→继续主 KB/LLM 原链（下方既有流程零改动）。
            from .assistant_router import assistant_match_chain, ASSISTANT_AUDIO_TYPE
            _hit_layer, _payload = assistant_match_chain(
                self.my_handle.config, _chat_src, content, username=username)
            if _hit_layer == "assistant_audio":
                self.my_handle.audio_synthesis_handle({
                    "type": ASSISTANT_AUDIO_TYPE,
                    "tts_type": "none",
                    "voice_path": _payload,
                    "file_path": _payload,
                    "content": content,
                })
                self.my_handle.webui_show_chat_log_callback(
                    "聊天发送" if _is_chatbox else "弹幕信息",
                    dict(data, chat_source=_chat_src),
                    f"[助播音频] {_payload}", content_type="answer")
                return
            if _hit_layer == "assistant_text":
                from .assistant_router import assistant_copy_message
                self.my_handle.audio_synthesis_handle(assistant_copy_message(
                    self.my_handle.config, _payload,
                    username=username, producer_type="kb_chain", source=_chat_src,
                    hit_layer="assistant_text"))
                self.my_handle.webui_show_chat_log_callback(
                    "聊天发送" if _is_chatbox else "弹幕信息",
                    dict(data, chat_source=_chat_src),
                    _payload, content_type="answer")
                return

            # 1、本地问答库匹配（spec C1：前置抢答仅 chat_type=local_qa 时生效——
            # 中枢已判定命中；chat_type≠local_qa 或未启用时 rec.local_qa_answer 恒空，
            # custom_llm/fastrag 模式词库改由 llm_handle 内托底调用）
            if rec.local_qa_answer and self.my_handle.local_qa_handle(data, hub_result=rec):
                return

            # 2、点歌模式（中枢已判定意图：None=未触发静默交回弹幕链）
            if self.my_handle.choose_song_handle(data, hub_result=rec):
                return

            # 5、LLM处理
            chat_type = self.my_handle.config.get("chat_type")
            if chat_type in self.my_handle.chat_type_list:
                # 用户名数字转中文（filter.username_convert_digits_to_chinese）：
                # 参数语义=「数字转换为中文再发送给 LLM」——仅重绑本局部变量供
                # LLM 输入使用（写库/聊天记录/黑名单均不受影响；合成侧同名转换
                # 幂等，播报不受影响）
                if self.my_handle.config.get("filter", "username_convert_digits_to_chinese"):
                    username = self.my_handle.common.convert_digits_to_chinese(username) or username

                # 构建数据（prompt 走共享构建器：弹幕模板→语言指令，ENG E-1）
                data_json = {
                    "username": username,
                    "content": content,
                    "prompt": self.my_handle.build_llm_input(username, content),
                    "ori_username": data.get("ori_username", username),
                    "ori_content": data.get("ori_content", content)
                }

                # ① 流式回复链路（2026-10-27）：保守门通过时增量分句合成，首句
                # 音频延迟从「全文生成完」缩短为「首句生成完」；不可流式/熔断/
                # 零产出返回 None 自动回退下方全文链路（语义零变化）
                if self.my_handle.chat_handler.stream_reply_gate(chat_type):
                    def _build_stream_message(_c, _src=_chat_src, _u=username):
                        from .assistant_router import assistant_route as _ar, assistant_copy_message as _acm
                        if _ar(self.my_handle.config, "llm_reply", _src):
                            return _acm(self.my_handle.config, _c, username=_u,
                                        producer_type="llm_reply", source=_src,
                                        hit_layer="llm")
                        return {
                            "type": "comment",
                            "tts_type": self.my_handle.config.get("audio_synthesis_type"),
                            "data": self.my_handle.config.get(self.my_handle.config.get("audio_synthesis_type")),
                            "config": self.my_handle.config.get("filter"),
                            "username": _u,
                            "content": _c
                        }
                    _streamed = self.my_handle.chat_handler.llm_stream_reply(
                        chat_type, data_json, _build_stream_message)
                    if _streamed is not None:
                        logger.info(f"[AI回复{username}]：{_streamed}")
                        self.my_handle.webui_show_chat_log_callback(
                            "聊天发送" if _is_chatbox else "弹幕信息",
                            dict(data, chat_source=_chat_src),
                            _streamed, content_type="answer")
                        return

                # LLM处理（失败→托底→熔断在 llm_handle 单点实现）
                resp_content = self.my_handle.llm_handle(chat_type, data_json)

                if resp_content:
                    logger.info(f"[AI回复{username}]：{resp_content}")
                else:
                    resp_content = ""
            elif chat_type == "game":
                # 游戏控制实现已随拆分移除（GameControl 不在现行代码）——明示不可用而非
                # None.parse_keys AttributeError（T4）
                if self.my_handle.config.get("game", "enable"):
                    logger.warning("chat_type=game 已启用但游戏控制实现不存在（GameControl 已移除），忽略本次按键模拟请求")
                return
            elif chat_type == "none":
                return
            else:
                resp_content = content

            # 空数据结束
            if resp_content == "" or resp_content is None:
                return

            """
            双重过滤，为您保驾护航
            """
            resp_content = resp_content.strip()

            resp_content = resp_content.replace('\n', '。')
            
            # LLM回复的内容进行违禁判断
            # S4 合规核查（D-20 矩阵登记点）：LLM 回复（含 RAG/知识库内容
            # 进 LLM 后的回复）在 TTS 合成（下方 audio_synthesis_handle）前
            # 经过此处 prohibitions_handle 违禁词过滤（丢弃式）——回复侧
            # 过滤已有实现，本批仅标注位置，不另加过滤链。
            resp_content = self.prohibitions_handle(resp_content)
            if resp_content is None:
                return

            # logger.info("resp_content=" + resp_content)

            # 回复内容是否进行翻译（④ 2026-10-27：目标语言一致跳过 API 往返，
            # comment/talk 双链共用 trans_reply 单点）
            if self.my_handle.config.get("translate", "enable") and (self.my_handle.config.get("translate", "trans_type") == "回复" or \
                self.my_handle.config.get("translate", "trans_type") == "弹幕+回复"):
                resp_content = self.my_handle.my_translate.trans_reply(resp_content)

            # 回复模板应用（G6，基础功能 2026-10-22：违禁→翻译→模板→TTS，
            # ENG Section 4 顺序裁决——模板 {data} 吃翻译后文本；写日志环节已随
            # 日志功能全链退役移除，2026-10-22）
            resp_content = self.my_handle.apply_reply_template(resp_content, username)

            # 判断按键映射触发类型
            if self.my_handle.config.get("key_mapping", "type") == "回复" or self.my_handle.config.get("key_mapping", "type") == "弹幕+回复":
                # 替换内容
                data["content"] = resp_content
                # 按键映射 触发后不执行后面的其他功能
                if self.my_handle.key_mapping_handle("回复", data):
                    pass

            # 音频合成时需要用到的重要数据（十生产端接线之 comment LLM 回复：
            # AI对话(comment 源)/聊天(chatbox 源) 勾选→助播消息；voice 源不经过
            # 本链路（talk 走 chat_handler）；未勾选维持原路）
            from .assistant_router import assistant_route, assistant_copy_message
            if assistant_route(self.my_handle.config, "llm_reply", _chat_src):
                message = assistant_copy_message(
                    self.my_handle.config, resp_content,
                    username=username, producer_type="llm_reply", source=_chat_src,
                    hit_layer="llm")
            else:
                message = {
                    "type": "comment",
                    "tts_type": self.my_handle.config.get("audio_synthesis_type"),
                    "data": self.my_handle.config.get(self.my_handle.config.get("audio_synthesis_type")),
                    "config": self.my_handle.config.get("filter"),
                    "username": username,
                    "content": resp_content
                }

            self.my_handle.audio_synthesis_handle(message)

            # 聊天记录（2026-10-25 重构）：回复侧 answer（输入侧 question 在本函数
            # 头部；弹幕与聊天框均记录，开关已退役固定记录）
            self.my_handle.webui_show_chat_log_callback(
                "聊天发送" if _is_chatbox else "弹幕信息",
                dict(data, chat_source=data.get("chat_source") or "comment"),
                resp_content, content_type="answer")
        except Exception as e:
            logger.error(traceback.format_exc())