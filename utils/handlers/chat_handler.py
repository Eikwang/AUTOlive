# -*- coding: UTF-8 -*-
"""
聊天处理器模块

处理聊天和LLM相关功能
"""

import os
import traceback
from datetime import datetime
from ..error_handler import handle_error, safe_execute
from ..base import logger
from ..gpt_model.gpt import GPT_MODEL


class ChatHandler:
    """聊天处理器"""
    
    def __init__(self, my_handle):
        """
        初始化聊天处理器

        Args:
            my_handle: My_handle 实例
        """
        self.my_handle = my_handle
        # LLM 熔断器（per-channel，ENG E-3：通道间不互扰）
        self._llm_breakers = {}

    def get_chat_model(self, chat_type, config):
        """
        获取聊天模型

        Args:
            chat_type (str): 聊天类型
            config (dict): 配置
        """
        GPT_MODEL.set_model_config(chat_type, config.get(chat_type))
        self.my_handle.__dict__[chat_type] = GPT_MODEL.get(chat_type)

    def handle_chat_type(self):
        """处理聊天类型"""
        chat_type = self.my_handle.config.get("chat_type")
        self.get_chat_model(chat_type, self.my_handle.config)

    def _get_breaker(self, channel):
        """取通道熔断器（惰性创建，per-channel 隔离）。"""
        if channel not in self._llm_breakers:
            from utils.llm_breaker import LLMBreaker
            self._llm_breakers[channel] = LLMBreaker(channel)
        return self._llm_breakers[channel]

    def _try_local_qa_fallback(self, chat_type, data_json, fail_reason):
        """本地问答托底（基础功能 2026-10-22，spec A2）。

        custom_llm/fastrag 失败（异常/None/空）后调用；local_qa.enable=false 时
        托底禁用→统一"无回复原因"出口（ENG E-6：终结全静默）。

        Args:
            chat_type (str): 失败的 LLM 通道名
            data_json (dict): 弹幕数据（含 username/content）
            fail_reason (str): LLM 失败原因链（入 log）

        Returns:
            str | None: 托底命中返回渲染后回答；未命中/禁用返回 None（log 已记原因）
        """
        username = (data_json or {}).get("username", "")
        content = (data_json or {}).get("content", "")
        if not self.my_handle.config.get("local_qa", "enable"):
            logger.info(
                f"无回复原因：LLM 通道 {chat_type} 失败（{fail_reason}）→ "
                f"本地问答托底禁用（local_qa.enable=false）→ 无回复；"
                f"fix=启用 WebUI 本地问答开关或修复 LLM 服务")
            return None
        answer = self.my_handle.feature_handler.local_qa_match(content, username=username)
        if answer:
            logger.info(f"本地问答托底命中：LLM 通道 {chat_type} 失败（{fail_reason}）→ 词库回答")
            return answer
        logger.info(
            f"无回复原因：LLM 通道 {chat_type} 失败（{fail_reason}）→ "
            f"本地问答托底未命中 → 无回复")
        return None

    def llm_handle(self, chat_type, data_json):
        """
        LLM处理（共享层：弹幕路径 comment_handle 与语音路径 talk_handle 共用，
        ENG E-1——失败→托底→熔断在此单点实现，双路径行为天然一致）

        Args:
            chat_type (str): 聊天类型（custom_llm/fastrag/local_qa）
            data_json (dict): 数据JSON

        Returns:
            str: LLM回复内容
        """
        # chat_type=local_qa：纯词库直答（不经 prompt/HTTP）
        if chat_type == "local_qa":
            username = (data_json or {}).get("username", "")
            content = (data_json or {}).get("content", "")
            if not self.my_handle.config.get("local_qa", "enable"):
                logger.info(
                    "无回复原因：chat_type=local_qa 但 local_qa.enable=false → 无回复；"
                    "fix=启用 WebUI 本地问答开关或改 chat_type")
                return None
            answer = self.my_handle.feature_handler.local_qa_match(content, username=username)
            if answer:
                return answer
            logger.info("无回复原因：本地问答词库未命中 → 无回复")
            return None

        try:
            # 熔断检查：开路期直接走托底（native F3，防故障期每条 60s 级联停顿）
            breaker = self._get_breaker(chat_type)
            if not breaker.allow_request():
                return self._try_local_qa_fallback(
                    chat_type, data_json, f"熔断开路中（连续失败）")

            # 获取模型
            model = self.my_handle.__dict__.get(chat_type)
            if model is None:
                logger.error(
                    f"模型 {chat_type} 未初始化；fix=检查 config chat_type 与对应配置节"
                    f"（custom_llm/fastrag），重启系统")
                breaker.record_failure()
                return self._try_local_qa_fallback(chat_type, data_json, "模型未初始化")

            # 调用模型（使用 get_resp_sync 保持向后兼容）
            # 优先使用 get_resp_sync 方法（异步化改造后的方法）
            if hasattr(model, 'get_resp_sync'):
                resp_content = model.get_resp_sync(data_json)
            else:
                resp_content = model.chat(data_json)

            if resp_content:
                breaker.record_success()
                return resp_content

            # 返回 None/空：通道失败→托底（DX CAP6-4/5：失败原因+通道名入 log）
            breaker.record_failure()
            return self._try_local_qa_fallback(chat_type, data_json, "通道返回空（None/空串）")
        except Exception as e:
            handle_error(e, {'module': 'handler', 'function': 'llm_handle'})
            logger.error(
                f"LLM_FALLBACK LLM 处理异常：通道 {chat_type}，"
                f"{type(e).__name__}: {e}；fix=查看下方 traceback 定位"
                f"（非预期异常附紧凑栈）")
            logger.error(traceback.format_exc(limit=8))
            try:
                self._get_breaker(chat_type).record_failure()
            except Exception:
                pass
            return self._try_local_qa_fallback(chat_type, data_json, f"异常 {type(e).__name__}: {e}")
    
    def llm_stream_handle_and_audio_synthesis(self, chat_type, data_json, type="chat"):
        """
        LLM流式处理和音频合成
        
        Args:
            chat_type (str): 聊天类型
            data_json (dict): 数据JSON
            type (str): 类型
        """
        try:
            # 获取模型
            model = self.my_handle.__dict__.get(chat_type)
            if model is None:
                logger.error(f"模型 {chat_type} 未初始化")
                return
            
            # 流式调用模型
            for chunk in model.stream_chat(data_json):
                if chunk:
                    # 音频合成
                    message = {
                        "type": "talk",
                        "tts_type": self.my_handle.config.get("audio_synthesis_type"),
                        "data": self.my_handle.config.get(self.my_handle.config.get("audio_synthesis_type")),
                        "config": self.my_handle.config.get("filter"),
                        "username": data_json.get("username", ""),
                        "content": chunk
                    }
                    self.my_handle.audio.audio_synthesis(message)
        except Exception as e:
            logger.error(traceback.format_exc())
            logger.error(f"LLM流式处理失败：{e}")
    
    def split_by_chinese_punctuation(self, text):
        """
        按中文标点分割文本
        
        Args:
            text (str): 文本
            
        Returns:
            list: 分割后的文本列表
        """
        import re
        # 按中文标点分割
        result = re.split(r'([。！？；])', text)
        # 合并标点到前面的句子
        merged = []
        for i in range(0, len(result) - 1, 2):
            if i + 1 < len(result):
                merged.append(result[i] + result[i + 1])
        if len(result) % 2 == 1:
            merged.append(result[-1])
        return merged
    
    def talk_handle(self, data):
        """
        聊天处理（语音输入）
        
        Args:
            data (dict): 包含用户名,弹幕内容
            
        Returns:
            dict: 传递给音频合成的JSON数据
        """
        try:
            username = data["username"]
            content = data["content"]

            # 黑名单拦截（T7 接线：丢弃黑名单用户的一切信息，置于写库前）
            if self.my_handle.blacklist_handle(data):
                return

            # 输出当前用户发送的弹幕消息
            logger.debug(f"[{username}]: {content}")

            """
            用户名也得过滤一下，防止炸弹人
            """
            # 用户名以及弹幕违禁判断（UC-2 写库/过滤顺序：过滤上移至写库前——
            # /autoplan 2026-10-22 流程图批次终门 uc2a，2026-10-23 TODOS 批次双链同改）
            username = self.my_handle.prohibitions_handle(username)
            if username is None:
                return

            content = self.my_handle.prohibitions_handle(content)
            if content is None:
                return

            # 弹幕格式检查和特殊字符替换和指定语言过滤
            content = self.my_handle.comment_check_and_replace(content)
            if content is None:
                return

            # 回复语言过滤（G3，基础功能 2026-10-22：need_lang≠none 时检测弹幕语言）
            if not self.my_handle.lang_filter_pass(content):
                return

            # 判断字符串是否全为标点符号，是的话就过滤
            if self.my_handle.common.is_punctuation_string(content):
                logger.debug(f"用户:{username}]，发送纯符号的弹幕，已过滤")
                return

            if self.my_handle.config.get("talk", "show_chat_log"):
                if "ori_username" not in data:
                    data["ori_username"] = data["username"]
                if "ori_content" not in data:
                    data["ori_content"] = data["content"]
                if "user_face" not in data:
                    data["user_face"] = 'https://robohash.org/ui'

                # UI-16 二轮验收（任务 14 语义修正）：本链=语音对话（talk），输入侧
                # 发送 type 字面改「语音对话」（原误导性「弹幕信息」），chat_source
                # 透传（webui /callback 白名单：仅 voice/chatbox 进聊天记录）
                return_webui_json = {
                    "type": "llm",
                    "data": {
                        "type": "语音对话",
                        "chat_source": data.get("chat_source", "voice"),
                        "username": data["ori_username"],
                        "user_face": data["user_face"],
                        "content_type": "question",
                        "content": data["ori_content"],
                        "timestamp": self.my_handle.common.get_bj_time(0)
                    }
                }
                webui_ip = "127.0.0.1" if self.my_handle.config.get("webui", "ip") == "0.0.0.0" else self.my_handle.config.get("webui", "ip")
                tmp_json = self.my_handle.common.send_request(f'http://{webui_ip}:{self.my_handle.config.get("webui", "port")}/callback', "POST", return_webui_json, timeout=10)
            

            # 记录数据库（窄 try 隔离：DB 异常仅丢日志，不中断回复链——T4/ENG H-1，
            # 此前 datetime NameError 被外层 try 吞掉导致其后整条回复链静默跳过）
            # UC-2 写库/过滤顺序：过滤已上移（违禁弹幕不入库，写库内容=过滤后弹幕）
            if self.my_handle.config.get("database", "comment_enable"):
                try:
                    insert_data_sql = '''
                    INSERT INTO danmu (username, content, ts) VALUES (?, ?, ?)
                    '''
                    self.my_handle.db.execute(insert_data_sql, (username, content, datetime.now()))
                except Exception as db_e:
                    logger.error(f"弹幕日志写库失败（不影响回复链）：{db_e}")

            # ===== 关键词识别中枢（2026-10-25 核心架构调整，flowchartTD Capture→Keyword）=====
            # 语音源经过滤后单次识别全部功能的关键词命中，后续各模块凭结果分发、
            # 零重复匹配（原实现与弹幕链各自独立扫描同一套词表）。talk 源功能集
            # 与原链一致：积分签到/查询、按键映射弹幕型、本地问答、点歌意图
            # （复读/编排订阅原语音链未开，保持不开——需统一时改 KeywordHub._TEXT_FEATURES）。
            # 审查修复（发布批次）：回写过滤后文本（同 comment 链，原文留存 ori_content）
            if "ori_content" not in data:
                data["ori_content"] = data["content"]
            data["content"] = content
            rec = self.my_handle.keyword_recognize(data, "talk")

            # 0、积分机制运转（中枢已判定命令词命中，消费侧跳过重复匹配）
            if rec.integral_sign and self.my_handle.integral_handle("comment", data, hub_result=rec):
                return
            if rec.integral_query and self.my_handle.integral_handle("crud", data, hub_result=rec):
                return

            # 判断按键映射触发类型（弹幕型：中枢已匹配命中组直接分派；type 显式门卫
            # 与原实现等价——中枢侧 ktype 路由为第一道闸，此处保留链侧显式闸）
            if self.my_handle.config.get("key_mapping", "type") == "弹幕" or self.my_handle.config.get("key_mapping", "type") == "弹幕+回复":
                if self.my_handle.key_mapping_handle("弹幕", data, hub_result=rec):
                    return

            # 1、本地问答库匹配（spec C1：前置抢答仅 chat_type=local_qa 时生效——
            # 中枢已判定命中；chat_type≠local_qa 或未启用时 rec.local_qa_answer 恒空，
            # custom_llm/fastrag 模式词库改由 llm_handle 内托底调用）
            if rec.local_qa_answer and self.my_handle.local_qa_handle(data, hub_result=rec):
                return

            # 2、点歌模式（中枢已判定意图：None=未触发静默交回语音链）
            if self.my_handle.choose_song_handle(data, hub_result=rec):
                return

            # 5、LLM处理
            chat_type = self.my_handle.config.get("chat_type")
            if chat_type in self.my_handle.chat_type_list:
                # 构建数据（prompt 走共享构建器：弹幕模板→前后缀→语言指令，ENG E-1）
                data_json = {
                    "username": username,
                    "content": content,
                    "prompt": self.my_handle.build_llm_input(username, content),
                    "ori_username": data.get("ori_username", username),
                    "ori_content": data.get("ori_content", content)
                }

                # LLM处理（失败→托底→熔断在 llm_handle 单点实现）
                resp_content = self.llm_handle(chat_type, data_json)

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
            elif chat_type == "reread":
                resp_content = self.llm_handle(chat_type, data_json)
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
            resp_content = self.my_handle.prohibitions_handle(resp_content)
            if resp_content is None:
                return

            # logger.info("resp_content=" + resp_content)

            # 回复内容是否进行翻译
            if self.my_handle.config.get("translate", "enable") and (self.my_handle.config.get("translate", "trans_type") == "回复" or \
                self.my_handle.config.get("translate", "trans_type") == "弹幕+回复"):
                tmp = self.my_handle.my_translate.trans(resp_content)
                if tmp:
                    resp_content = tmp

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

            # 音频合成时需要用到的重要数据
            message = {
                "type": "talk",
                "tts_type": self.my_handle.config.get("audio_synthesis_type"),
                "data": self.my_handle.config.get(self.my_handle.config.get("audio_synthesis_type")),
                "config": self.my_handle.config.get("filter"),
                "username": username,
                "content": resp_content
            }

            self.my_handle.audio_synthesis_handle(message)

            # UI-16 二轮验收（任务 14 语义修正）：语音对话的回复侧补发聊天记录
            # （上游缺失——只有输入侧 question，无 answer，记录只有半边）
            if self.my_handle.config.get("talk", "show_chat_log"):
                self.my_handle.webui_show_chat_log_callback(
                    "语音对话", dict(data, chat_source=data.get("chat_source", "voice")),
                    resp_content, content_type="answer")

            return message
        except Exception as e:
            logger.error(traceback.format_exc())
            return None