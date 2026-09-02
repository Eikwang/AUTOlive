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
            
            # 语言过滤
            lang_filter = filter_config.get("lang_filter")
            if lang_filter and lang_filter != "none":
                # TODO: 实现语言过滤
                pass
            
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
    
    def reread_handle(self, data):
        """
        复读处理
        
        Args:
            data (dict): 数据
            
        Returns:
            bool: 是否触发并处理
        """
        try:
            username = data["username"]
            content = data["content"]
            
            # 检查是否启用复读
            if not self.my_handle.config.get("reread", "enable"):
                return False
            
            # 检查是否匹配复读关键词
            keywords = self.my_handle.config.get("reread", "keywords", [])
            for keyword in keywords:
                if keyword in content:
                    logger.info(f"[{username}]触发复读：{content}")
                    
                    # 构建消息
                    message = {
                        "type": "reread",
                        "tts_type": self.my_handle.config.get("audio_synthesis_type"),
                        "data": self.my_handle.config.get(self.my_handle.config.get("audio_synthesis_type")),
                        "config": self.my_handle.config.get("filter"),
                        "username": username,
                        "content": content
                    }
                    
                    # 音频合成
                    self.my_handle.audio_synthesis_handle(message)
                    
                    return True
            
            return False
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
    
    def write_to_comment_log(self, content, data):
        """
        写入弹幕日志
        
        Args:
            content (str): 内容
            data (dict): 数据
        """
        try:
            # 获取配置
            log_config = self.my_handle.config.get("comment_log")
            
            # 检查是否启用日志
            if not log_config.get("enable"):
                return
            
            # 写入日志
            username = data.get("username", "")
            ori_content = data.get("content", "")
            
            log_content = f"[{username}]: {ori_content}\n[AI回复]: {content}\n"
            
            with open(self.my_handle.comment_file_path, 'a', encoding='utf-8') as f:
                f.write(log_content)
            
            logger.debug(f"写入弹幕日志：{username}")
        except Exception as e:
            logger.error(traceback.format_exc())
            logger.error(f"写入弹幕日志失败：{e}")
    
    def comment_handle(self, data):
        """
        弹幕处理
        
        Args:
            data (dict): 数据
        """
        try:
            username = data["username"]
            content = data["content"]
            
            # 输出当前用户发送的弹幕消息
            logger.info(f"[{username}]: {content}")
            
            # 记录数据库
            if self.my_handle.config.get("database", "comment_enable"):
                from datetime import datetime
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
                resp_content = self.my_handle.llm_handle(chat_type, data_json)
                
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

            self.write_to_comment_log(resp_content, {"username": username, "content": content})

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
                "type": "comment",
                "tts_type": self.my_handle.config.get("audio_synthesis_type"),
                "data": self.my_handle.config.get(self.my_handle.config.get("audio_synthesis_type")),
                "config": self.my_handle.config.get("filter"),
                "username": username,
                "content": resp_content
            }

            self.my_handle.audio_synthesis_handle(message)
        except Exception as e:
            logger.error(traceback.format_exc())