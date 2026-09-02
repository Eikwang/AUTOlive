# -*- coding: UTF-8 -*-
"""
功能处理器模块

处理各种功能模块
"""

import os
import traceback
from ..error_handler import handle_error, safe_execute
from ..base import logger


class FeatureHandler:
    """功能处理器"""
    
    def __init__(self, my_handle):
        """
        初始化功能处理器
        
        Args:
            my_handle: My_handle 实例
        """
        self.my_handle = my_handle
    
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
    
    def sd_handle(self, data):
        """
        SD处理
        
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
            
            if content.startswith(self.my_handle.config.get("sd", "trigger")):
                # 违禁检测
                content = self.my_handle.prohibitions_handle(content)
                if content is None:
                    return
            
                if self.my_handle.config.get("sd", "enable") == False:
                    logger.info("您还未启用SD模式，无法使用画画功能")
                    return True
                else:
                    # 输出当前用户发送的弹幕消息
                    logger.info(f"[{username}]: {content}")
                    
                    # 删除文本中的命令前缀
                    content = content[len(self.my_handle.config.get("sd", "trigger")):]
                    
                    if self.my_handle.config.get("sd", "translate_type") != "none":
                        # 判断翻译类型 进行翻译工作
                        tmp = self.my_handle.my_translate.trans(content, self.my_handle.config.get("sd", "translate_type"))
                        if tmp:
                            content = tmp
                    
                    """
                    根据聊天类型执行不同逻辑
                    """ 
                    chat_type = self.my_handle.config.get("sd", "prompt_llm", "type")
                    if chat_type in self.my_handle.chat_type_list:
                        content = self.my_handle.config.get("sd", "prompt_llm", "before_prompt") + \
                            content + self.my_handle.config.get("after_prompt")
                        
                        # 构建数据
                        data_json = {
                            "username": username,
                            "content": content
                        }
                        
                        # LLM处理
                        resp_content = self.my_handle.llm_handle(chat_type, data_json)
                        
                        if resp_content:
                            logger.info(f"[AI回复{username}]：{resp_content}")
                        else:
                            resp_content = ""
                            logger.warning(f"警告：{chat_type}无返回")
                    elif chat_type == "none":
                        resp_content = content
                    else:
                        resp_content = content
                    
                    # 空数据结束
                    if resp_content == "" or resp_content is None:
                        return True
                    
                    """
                    双重过滤，为您保驾护航
                    """
                    resp_content = resp_content.strip()
                    
                    resp_content = resp_content.replace('\n', '。')
                    
                    # LLM回复的内容进行违禁判断
                    resp_content = self.my_handle.prohibitions_handle(resp_content)
                    if resp_content is None:
                        return True
                    
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
                        "type": "sd",
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
            logger.error(f"SD处理失败：{e}")
            return False
    
    def integral_handle(self, type, data):
        """
        积分处理
        
        Args:
            type (str): 类型
            data (dict): 数据
            
        Returns:
            bool: 是否触发并处理
        """
        try:
            # 检查是否启用积分
            if not self.my_handle.config.get("integral", "enable"):
                return False
            
            # TODO: 实现积分处理逻辑
            
            return False
        except Exception as e:
            logger.error(traceback.format_exc())
            logger.error(f"积分处理失败：{e}")
            return False
    
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
    
    def custom_cmd_handle(self, type, data):
        """
        自定义命令处理
        
        Args:
            type (str): 类型
            data (dict): 数据
            
        Returns:
            bool: 是否触发并处理
        """
        try:
            # 检查是否启用自定义命令
            if not self.my_handle.config.get("custom_cmd", "enable"):
                return False
            
            # TODO: 实现自定义命令处理逻辑
            
            return False
        except Exception as e:
            logger.error(traceback.format_exc())
            logger.error(f"自定义命令处理失败：{e}")
            return False
    
    def blacklist_handle(self, data):
        """
        黑名单处理
        
        Args:
            data (dict): 数据
            
        Returns:
            bool: 是否在黑名单中
        """
        try:
            username = data["username"]
            
            # 检查是否启用黑名单
            if not self.my_handle.config.get("blacklist", "enable"):
                return False
            
            # 检查是否在黑名单中
            blacklist = self.my_handle.config.get("blacklist", "list", [])
            if username in blacklist:
                logger.debug(f"用户 {username} 在黑名单中")
                return True
            
            return False
        except Exception as e:
            logger.error(traceback.format_exc())
            logger.error(f"黑名单处理失败：{e}")
            return False
    
    def search_online_handle(self, data):
        """
        在线搜索处理
        
        Args:
            data (dict): 数据
            
        Returns:
            bool: 是否触发并处理
        """
        try:
            username = data["username"]
            content = data["content"]
            
            # 检查是否启用在线搜索
            if not self.my_handle.config.get("search_online", "enable"):
                return False
            
            # 检查是否匹配搜索关键词
            keywords = self.my_handle.config.get("search_online", "keywords", [])
            for keyword in keywords:
                if keyword in content:
                    logger.info(f"[{username}]触发在线搜索：{content}")
                    
                    # TODO: 实现在线搜索逻辑
                    
                    return True
            
            return False
        except Exception as e:
            logger.error(traceback.format_exc())
            logger.error(f"在线搜索处理失败：{e}")
            return False