# -*- coding: UTF-8 -*-
"""
弹幕处理器模块

处理弹幕相关功能
"""

import os
import traceback
from ..error_handler import handle_error, safe_execute
from ..base import logger


class CommentHandler:
    """弹幕处理器"""
    
    def __init__(self, my_handle):
        """
        初始化弹幕处理器
        
        Args:
            my_handle: My_handle 实例
        """
        self.my_handle = my_handle
    
    def comment_check_and_replace(self, content):
        """
        弹幕检查和替换
        
        Args:
            content (str): 弹幕内容
            
        Returns:
            str: 处理后的弹幕内容，None表示被过滤
        """
        try:
            # 获取配置
            filter_config = self.my_handle.config.get("filter")
            
            # 检查是否启用过滤
            if not filter_config.get("enable"):
                return content
            
            # 检查弹幕长度
            if len(content) > filter_config.get("max_len", 100):
                logger.debug(f"弹幕长度超过限制：{len(content)} > {filter_config.get('max_len', 100)}")
                return None
            
            # 检查是否包含违禁词
            prohibitions = filter_config.get("prohibitions", [])
            for word in prohibitions:
                if word in content:
                    logger.debug(f"弹幕包含违禁词：{word}")
                    return None
            
            # 特殊字符替换
            replace_chars = filter_config.get("replace_chars", {})
            for old_char, new_char in replace_chars.items():
                content = content.replace(old_char, new_char)

            # （2026-10-22 批次清理，spec C3）原 filter.lang_filter 死 TODO 分支已移除——
            # 语言过滤由顶层 need_lang 键驱动（my_handle.lang_filter_pass），与此处 filter 节无关

            return content
        except Exception as e:
            handle_error(e, {'module': 'handler', 'function': 'unknown'})
            logger.error(f"弹幕检查失败：{e}")
            return content
    
    def prohibitions_handle(self, content):
        """
        违禁处理
        
        Args:
            content (str): 内容
            
        Returns:
            str: 处理后的内容，None表示被过滤
        """
        try:
            # 获取配置
            filter_config = self.my_handle.config.get("filter")
            
            # 检查是否启用过滤
            if not filter_config.get("enable"):
                return content
            
            # 检查是否包含违禁词
            prohibitions = filter_config.get("prohibitions", [])
            for word in prohibitions:
                if word in content:
                    logger.debug(f"内容包含违禁词：{word}")
                    return None
            
            return content
        except Exception as e:
            logger.error(traceback.format_exc())
            logger.error(f"违禁处理失败：{e}")
            return content
    
    def reread_handle(self, data, type=None, hub_result=None):
        """
        复读处理

        Args:
            data (dict): 数据
            type (str, optional): 复读类型（reread | reread_top_priority）。
                A6 修复（2026-10-22 批次）：原签名无此参数，web_server /send 带
                type= 调用即 TypeError。type 原样透传进消息——reread_top_priority
                经 priority_mapping 取 999 权重（999 映射保持不变，见审查记录
                R6(a)）；业务层复读去重逻辑不区分类型。
            hub_result (KeywordResult | None): 识别中枢结果。非 None=中枢已判定
                词命中（调用由 hub_result.reread_type 驱动，跳过内部词表重复匹配）。

        Returns:
            bool: 是否触发并处理
        """
        try:
            username = data["username"]
            content = data["content"]

            # 检查是否启用复读
            if not self.my_handle.config.get("reread", "enable"):
                return False

            if hub_result is None:
                # 检查是否匹配复读关键词
                # A6（2026-10-22 审查批次）：原 3 参 get("reread","keywords",[]) 在 Config
                # 遍历语义下对 list 再 .get 会 AttributeError（/send reread 二次断点），
                # 改安全形式。
                keywords = self.my_handle.config.get("reread", "keywords") or []
                # str/list 双形态（G7 先例；split_lines 归一化前存量可为 str/逗号串）
                if isinstance(keywords, str):
                    keywords = ([k.strip() for k in keywords.split("\n") if k.strip()]
                                or [k.strip() for k in keywords.split(",") if k.strip()])
                for keyword in keywords:
                    if keyword in content:
                        break
                else:
                    return False

            logger.info(f"[{username}]触发复读：{content}")

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
            logger.error(f"复读处理失败：{e}")
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

            content = self.prohibitions_handle(content)
            if content is None:
                return

            # 弹幕格式检查和特殊字符替换和指定语言过滤
            content = self.comment_check_and_replace(content)
            if content is None:
                return

            # 回复语言过滤（G3，基础功能 2026-10-22：need_lang≠none 时检测弹幕语言）
            if not self.my_handle.lang_filter_pass(content):
                return

            # 判断字符串是否全为标点符号，是的话就过滤
            if self.my_handle.common.is_punctuation_string(content):
                logger.debug(f"用户:{username}]，发送纯符号的弹幕，已过滤")
                return

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
                self.my_handle.db.execute(insert_data_sql, (username, content, datetime.now()))

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

            # 复读触发（TODOS 2026-10-22 音频批次节 #1 生产端接线；中枢已判定词命中
            # 类型：top_priority→reread_top_priority（999 权重·暂停续播链路）、普通词
            # →reread。命中即 return（复读内容直接进 TTS，不再走 LLM——复读语义=
            # 原文重读非二次回答）。
            if rec.reread_type:
                return self.reread_handle(data, type=rec.reread_type, hub_result=rec)

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

            # 2、点歌模式（中枢已判定意图：None=未触发静默交回弹幕链）
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
            elif chat_type == "reread":
                resp_content = self.my_handle.llm_handle(chat_type, data_json)
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