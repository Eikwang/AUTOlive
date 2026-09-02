# -*- coding: UTF-8 -*-
"""
聊天处理器模块

处理聊天和LLM相关功能
"""

import os
import traceback
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
    
    def get_chat_model(self, chat_type, config):
        """
        获取聊天模型
        
        Args:
            chat_type (str): 聊天类型
            config (dict): 配置
        """
        GPT_MODEL.set_model_config(chat_type, config.get(chat_type))
        self.my_handle.__dict__[chat_type] = GPT_MODEL.get(chat_type)
    
    def get_vision_model(self, chat_type, config):
        """
        获取视觉模型
        
        Args:
            chat_type (str): 聊天类型
            config (dict): 配置
        """
        GPT_MODEL.set_vision_model_config(chat_type, config)
        self.my_handle.image_recognition_model = GPT_MODEL.get(chat_type)
    
    def handle_chat_type(self):
        """处理聊天类型"""
        chat_type = self.my_handle.config.get("chat_type")
        self.get_chat_model(chat_type, self.my_handle.config)
    
    def llm_handle(self, chat_type, data_json, type="chat"):
        """
        LLM处理
        
        Args:
            chat_type (str): 聊天类型
            data_json (dict): 数据JSON
            type (str): 类型
            
        Returns:
            str: LLM回复内容
        """
        try:
            # 获取模型
            model = self.my_handle.__dict__.get(chat_type)
            if model is None:
                logger.error(f"模型 {chat_type} 未初始化")
                return None
            
            # 调用模型（使用 get_resp_sync 保持向后兼容）
            if type == "vision":
                resp_content = model.vision_chat(data_json)
            else:
                # 优先使用 get_resp_sync 方法（异步化改造后的方法）
                if hasattr(model, 'get_resp_sync'):
                    resp_content = model.get_resp_sync(data_json)
                else:
                    resp_content = model.chat(data_json)
            
            return resp_content
        except Exception as e:
            handle_error(e, {'module': 'handler', 'function': 'unknown'})
            logger.error(f"LLM处理失败：{e}")
            return None
    
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

            # 输出当前用户发送的弹幕消息
            logger.debug(f"[{username}]: {content}")

            if self.my_handle.config.get("talk", "show_chat_log"):
                if "ori_username" not in data:
                    data["ori_username"] = data["username"]
                if "ori_content" not in data:
                    data["ori_content"] = data["content"]
                if "user_face" not in data:
                    data["user_face"] = 'https://robohash.org/ui'

                # 返回给webui的数据
                return_webui_json = {
                    "type": "llm",
                    "data": {
                        "type": "弹幕信息",
                        "username": data["ori_username"],
                        "user_face": data["user_face"],
                        "content_type": "question",
                        "content": data["ori_content"],
                        "timestamp": self.my_handle.common.get_bj_time(0)
                    }
                }
                webui_ip = "127.0.0.1" if self.my_handle.config.get("webui", "ip") == "0.0.0.0" else self.my_handle.config.get("webui", "ip")
                tmp_json = self.my_handle.common.send_request(f'http://{webui_ip}:{self.my_handle.config.get("webui", "port")}/callback', "POST", return_webui_json, timeout=10)
            

            # 记录数据库
            if self.my_handle.config.get("database", "comment_enable"):
                insert_data_sql = '''
                INSERT INTO danmu (username, content, ts) VALUES (?, ?, ?)
                '''
                self.my_handle.db.execute(insert_data_sql, (username, content, datetime.now()))

            # 0、积分机制运转
            if self.my_handle.integral_handle("comment", data):
                return
            if self.my_handle.integral_handle("crud", data):
                return

            """
            用户名也得过滤一下，防止炸弹人
            """
            # 用户名以及弹幕违禁判断
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
            
            # 判断字符串是否全为标点符号，是的话就过滤
            if self.my_handle.common.is_punctuation_string(content):
                logger.debug(f"用户:{username}]，发送纯符号的弹幕，已过滤")
                return
            
            # 判断按键映射触发类型
            if self.my_handle.config.get("key_mapping", "type") == "弹幕" or self.my_handle.config.get("key_mapping", "type") == "弹幕+回复":
                # 按键映射 触发后不执行后面的其他功能
                if self.my_handle.key_mapping_handle("弹幕", data):
                    return
            
            # 判断自定义命令触发类型
            if self.my_handle.config.get("custom_cmd", "type") == "弹幕" or self.my_handle.config.get("custom_cmd", "type") == "弹幕+回复":
                # 自定义命令 触发后不执行后面的其他功能
                if self.my_handle.custom_cmd_handle("弹幕", data):
                    return
            
            # 1、本地问答库匹配
            if self.my_handle.local_qa_handle(data):
                return
            
            # 2、点歌模式
            if self.my_handle.choose_song_handle(data):
                return
            
            # 3、SD模式
            if self.my_handle.sd_handle(data):
                return
            
            # 4、在线搜索
            if self.my_handle.search_online_handle(data):
                return
            
            # 5、LLM处理
            chat_type = self.my_handle.config.get("chat_type")
            if chat_type in self.my_handle.chat_type_list:
                # 构建数据
                data_json = {
                    "username": username,
                    "content": content,
                    "ori_username": data.get("ori_username", username),
                    "ori_content": data.get("ori_content", content)
                }
                
                # LLM处理
                resp_content = self.llm_handle(chat_type, data_json)
                
                if resp_content:
                    logger.info(f"[AI回复{username}]：{resp_content}")
                else:
                    resp_content = ""
                    logger.warning(f"警告：{chat_type}无返回")
            elif chat_type == "game":
                if self.my_handle.config.get("game", "enable"):
                    self.my_handle.game.parse_keys_and_simulate_keys_press(content.split(), 2)
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

            self.my_handle.write_to_comment_log(resp_content, {"username": username, "content": content})

            # 判断按键映射触发类型
            if self.my_handle.config.get("key_mapping", "type") == "回复" or self.my_handle.config.get("key_mapping", "type") == "弹幕+回复":
                # 替换内容
                data["content"] = resp_content
                # 按键映射 触发后不执行后面的其他功能
                if self.my_handle.key_mapping_handle("回复", data):
                    pass

            # 判断自定义命令触发类型
            if self.my_handle.config.get("custom_cmd", "type") == "回复" or self.my_handle.config.get("custom_cmd", "type") == "弹幕+回复":
                # 替换内容
                data["content"] = resp_content
                # 自定义命令 触发后不执行后面的其他功能
                if self.my_handle.custom_cmd_handle("回复", data):
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

            return message
        except Exception as e:
            logger.error(traceback.format_exc())
            return None