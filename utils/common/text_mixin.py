"""
文本处理模块 - 提供文本处理、敏感词检测、拼音转换等功能
"""

import re
import random
import hashlib
import string
import traceback
import difflib
import ahocorasick
from pypinyin import pinyin, Style

from ..base import logger


class TextMixin:
    """文本处理 Mixin"""

    # 生成hash字符串 用于gradio请求
    def generate_session_hash(self, length: int=11):
        characters = string.ascii_letters + string.digits
        random_string = ''.join(random.choice(characters) for i in range(length))
        hash_object = hashlib.sha1(random_string.encode())
        session_hash = hash_object.hexdigest()[:length]

        return session_hash

    # 将字符串中的数字转换成中文
    def convert_digits_to_chinese(self, input_str: str):
        """将字符串中的数字转换成中文

        Args:
            input_str (str): 待转换的字符串

        Returns:
            str: 转换后的字符串
        """
        try:
            # 定义阿拉伯数字到中文数字的映射
            digit_to_chinese = {
                '0': '零',
                '1': '一',
                '2': '二',
                '3': '三',
                '4': '四',
                '5': '五',
                '6': '六',
                '7': '七',
                '8': '八',
                '9': '九'
            }

            # 遍历输入字符串并替换数字为中文数字
            result = ''.join(digit_to_chinese.get(char, char) for char in input_str)
            
            return result
        except Exception as e:
            logger.error(f"转换数字到中文时出错: {e}")
            return input_str

    # 删除多余单词
    def remove_extra_words(self, text="", max_len=30, max_char_len=50):
        words = text.split()
        if len(words) > max_len:
            words = words[:max_len]  # 列表切片，保留前30个单词
            text = ' '.join(words) + '...'  # 使用join()函数将单词列表重新组合为字符串，并在末尾添加省略号
        return text[:max_char_len]


    # 本地敏感词检测 传入敏感词库文件路径和待检查的文本
    def check_sensitive_words(self, file_path, text):
        with open(file_path, 'r', encoding='utf-8') as file:
            sensitive_words = [line.strip() for line in file.readlines()]

        for word in sensitive_words:
            if word in text:
                return True

        return False
    

    # 本地敏感词检测 Aho-Corasick 算法 传入敏感词库文件路径和待检查的文本
    def check_sensitive_words2(self, file_path, text):
        with open(file_path, 'r', encoding='utf-8') as file:
            sensitive_words = [line.strip() for line in file.readlines()]

        # 创建 Aho-Corasick 自动机
        automaton = ahocorasick.Automaton()

        # 添加违禁词到自动机中
        for word in sensitive_words:
            automaton.add_word(word, word)

        # 构建自动机的转移函数和失效函数
        automaton.make_automaton()

        # 在文本中搜索违禁词
        for _, found_word in automaton.iter(text):
            logger.warning(f"命中本地违禁词：{found_word}")
            return found_word

        return None


    # 本地敏感词转拼音检测 传入敏感词库文件路径和待检查的文本
    def check_sensitive_words3(self, file_path, text):
        with open(file_path, 'r', encoding='utf-8') as file:
            sensitive_words = [line.strip() for line in file.readlines()]

        pinyin_text = self.text2pinyin(text)

        for word in sensitive_words:
            pinyin_word = self.text2pinyin(word)
            pattern = r'\b' + re.escape(pinyin_word) + r'\b'
            if re.search(pattern, pinyin_text):
                logger.warning(f"同音违禁拼音：{pinyin_word}")
                return True

        return False


    # 中文语句切分(只根据特定符号切分)
    def split_sentences1(self, text):
        # 使用正则表达式切分句子
        sentences = re.split('([。！？!?])', text)
        result = []
        for sentence in sentences:
            if sentence not in ["。", "！", "？", ".", "!", "?", ""]:
                result.append(sentence)
        
        # 替换换行
        result = [s.replace('\n', '。') for s in result]

        return result
    

    # 文本切分算法 旧算法，有最大长度限制
    def split_sentences2(self, text):
        # 最大长度限制，超过后会强制切分
        max_limit_len = 40

        # 使用正则表达式切分句子
        sentences = re.split('([。！？!?])', text)
        result = []
        current_sentence = ""
        for i in range(len(sentences)):
            if sentences[i] not in ["。", "！", "？", ".", "!", "?", ""]:
                # 去除换行和空格
                sentence = sentences[i].replace('\n', '。')
                # 如果句子长度小于10个字，则与下一句合并
                if len(current_sentence) < 10:
                    current_sentence += sentence
                    # 如果合并后的句子长度超过max_limit_len个字，则进行二次切分
                    if len(current_sentence) > max_limit_len:
                        # 判断是否有分隔符可用于二次切分
                        if i+1 < len(sentences) and len(sentences[i+1]) > 0 and sentences[i+1][0] not in ["。", "！", "？", ".", "!", "?"]:
                            next_sentence = sentences[i+1].replace('\n', '。')
                            # 寻找常用分隔符进行二次切分
                            for separator in [",", "，", ";", "；"]:
                                if separator in next_sentence:
                                    split_index = next_sentence.index(separator) + 1
                                    current_sentence += next_sentence[:split_index]
                                    result.append(current_sentence)
                                    current_sentence = next_sentence[split_index:]
                                    break
                        else:
                            # 如果合并后的句子长度超过max_limit_len个字，进行二次切分
                            while len(current_sentence) > max_limit_len:
                                result.append(current_sentence[:max_limit_len])
                                current_sentence = current_sentence[max_limit_len:]
                else:
                    result.append(current_sentence)
                    current_sentence = sentence

        # 添加最后一句
        if current_sentence:
            result.append(current_sentence)

        # 2次切分长字符串
        result2 = []
        for string in result:
            if len(string) > max_limit_len:
                split_strings = re.split(r"[,，;；。！!]", string)
                result2.extend(split_strings)
            else:
                result2.append(string)

        return result2


    # 文本切分算法
    def split_sentences(self, text):
        # 使用正则表达式切分句子
        sentences = re.split(r'(?<=[。！？!?])', text)
        result = []
        current_sentence = ""
        
        for sentence in sentences:
            # 去除换行和空格
            sentence = sentence.replace('\n', '')
            
            # 如果句子为空则跳过
            if not sentence:
                continue
            
            # 如果句子长度小于10个字，则与下一句合并
            if len(current_sentence) < 10:
                current_sentence += sentence
            else:
                # 判断当前句子是否以标点符号结尾
                if current_sentence[-1] in ["。", "！", "？", ".", "!", "?"]:
                    result.append(current_sentence)
                    current_sentence = sentence
                else:
                    # 如果当前句子不以标点符号结尾，则进行二次切分
                    split_sentences = re.split(r'(?<=[,，;；])', current_sentence)
                    if len(split_sentences) > 1:
                        result.extend(split_sentences[:-1])
                        current_sentence = split_sentences[-1] + sentence
                    else:
                        current_sentence += sentence
        
        # 添加最后一句
        if current_sentence:
            result.append(current_sentence)
        
        return result


    # 字符串匹配算法来计算字符串之间的相似度，并选择匹配度最高的字符串作为结果
    def find_best_match(self, substring, string_list, similarity=0.5):
        """字符串匹配算法来计算字符串之间的相似度，并选择匹配度最高的字符串作为结果

        Args:
            substring (str): 要搜索的子串
            string_list (list): 字符串列表
            similarity (float): 最低相似度

        Returns:
            _type_: 匹配到的字符串 或 None
        """
        best_match = None
        best_ratio = 0
        
        for string in string_list:
            ratio = difflib.SequenceMatcher(None, substring, string).ratio()
            if ratio > best_ratio:
                best_ratio = ratio
                best_match = string
        
        # 如果相似度不到similarity，则认为匹配不成功
        if best_ratio < similarity:
            return None

        return best_match
    

    # 检查 query_string 是否包含 string_list 列表中的任意一个子字符串
    def find_substring_in_list(self, query_string, string_list):
        """
        检查 query_string 是否包含 string_list 列表中的任意一个子字符串

        Args:
            query_string (str): 待查询的字符串。
            string_list (list of str): 被查询的字符串列表。

        Returns:
            str or None: 如果找到子串，则返回该子串；否则返回 None。
        """
        for string in string_list:
            if string in query_string:
                return string
        return None


    def text2pinyin(self, text):
        """文本转拼音

        Args:
            text (str): 传入待转换的文本

        Returns:
            str: 拼音字符串
        """
        pinyin_list = []
        for char in text:
            # 把每个汉字转为拼音
            char_pinyin_list = pinyin(char, style=Style.NORMAL)
            if char_pinyin_list:
                _pinyin = char_pinyin_list[0][0]
            else:
                _pinyin = char
            
            # 将ü等转换为v
            _pinyin = re.sub(r"ü", "v", _pinyin)
            
            pinyin_list.append(_pinyin)

        return " ".join(pinyin_list)


    def merge_consecutive_asterisks(self, s):
        """合并字符串末尾连续的*

        Args:
            s (str): 待处理的字符串

        Returns:
            str: 处理完后的字符串
        """
        # 从字符串末尾开始遍历，找到连续的*的起始索引
        idx = len(s) - 1
        while idx >= 0 and s[idx] == '*':
            idx -= 1

        # 如果找到了超过3个连续的*，则进行替换
        if len(s) - 1 - idx > 3:
            s = s[:idx + 1] + '*' + s[len(s) - 1:]

        return s


    def replace_special_characters(self, input_string, special_characters):
        """
        将指定的特殊字符替换为空字符。

        Args:
            input_string (str): 要替换特殊字符的输入字符串。
            special_characters (str): 包含要替换的特殊字符的字符串。

        Returns:
            str: 替换后的字符串。
        """
        for char in special_characters:
            input_string = input_string.replace(char, "")
        
        return input_string


    # 将cookie数据字符串分割成键值对列表
    def parse_cookie_data(self, data_str, field_name):
        """将cookie数据字符串分割成键值对列表

        Args:
            data_str (str): 待提取数据的cookie字符串
            field_name (str): 要提取的键名

        Returns:
            str: 键所对应的值
        """
        # 将数据字符串分割成键值对列表
        key_value_pairs = data_str.split(';')

        # 遍历键值对列表，查找指定字段名
        for pair in key_value_pairs:
            key, value = pair.strip().split('=')
            if key == field_name:
                return value

        # 如果未找到指定字段，返回空字符串
        return ""


    # 动态变量替换
    def dynamic_variable_replacement(self, template: str, data_json: dict=None):
        """动态变量替换

        Args:
            template (str): 待替换变量的字符串
            data_json (dict): 用于替换的变量json数据

        Returns:
            str: 替换完成后的字符串
        """
        try:
            if data_json is None:
                return template

            pattern = r"{(\w+)}"
            var_names = re.findall(pattern, template)

            for var_name in var_names:
                if var_name in data_json:
                    template = template.replace("{"+var_name+"}", str(data_json[var_name]))
                else:
                    # 变量不存在,保留原样
                    pass

            logger.debug(f"template={template}")

            return template
        except Exception as e:
            logger.error(traceback.format_exc())
            return None


    # [1|2]括号语法随机获取一个值，返回取值完成后的字符串
    def brackets_text_randomize(self, text: str):
        """
        [1|2]括号语法随机获取一个值，返回取值完成后的字符串
        Args:
            text (str): 原始字符串

        Returns:
            str: 最终字符串
        """
        # 查找所有括号内的内容
        brackets_content = re.findall(r'\[([^\]]*)\]', text)
        
        for content in brackets_content:
            # 分割每个括号内的选项
            choices = content.split('|')
            # 从选项中随机选择一个
            random_choice = random.choice(choices)
            # 替换文本中的括号内容
            text = text.replace(f'[{content}]', random_choice, 1)
        
        return text

    # 从列表中随机获取一个字符串，并进行变量语法转换。如果列表为空，使用单个字符串进行转换。
    def get_random_str_in_list_and_format(self, ori_content: str = None, ori_list: list = None, var_json: dict = None) -> dict:
        """
        从列表中随机获取一个字符串，并进行变量语法转换。如果列表为空，使用单个字符串进行转换。

        参数:
            ori_content (str): 单个待处理的字符串。
            ori_list (list of str): 待处理字符串的列表。
            var_json (dict): 动态变量替换所需的键值对。

        返回:
            dict: 包含转换后内容的字典和返回码。成功时返回 {"ret": 0, "content": content}，失败时返回 {"ret": -1, "content": None}。
        """
        
        # 检查并处理字符串列表
        if ori_list:
            content = random.choice(ori_list)
        elif ori_content:
            content = ori_content
        else:
            return {"ret": -1, "content": None}

        # [1|2]括号语法随机获取一个值，返回取值完成后的字符串
        content = self.brackets_text_randomize(content)

        # 动态变量替换
        content = self.dynamic_variable_replacement(content, var_json)

        return {"ret": 0, "content": content}

    def get_list_random_or_default(self, strings: list, default_value):
        """

        从列表中随机选择一个字符串，如果列表为空，则返回默认值。

        参数:
            strings (list of str): 字符串列表。
            default_value (str): 默认值。

        返回:
            str: 随机选择的字符串或默认值。
        """
        if not strings:  # 如果列表是空的
            return default_value
        else:
            return random.choice(strings)

    # llm响应内容<> </>标签内容过滤 主要针对deepseek返回
    def llm_resp_content_filter_tags(self, text: str, filter_state: dict) -> str:
        """
        过滤标签内容的辅助函数
        
        Args:
            text: 要处理的文本
            filter_state: 过滤状态字典，包含：
                - is_filtering: 是否正在过滤
                - current_tag: 当前正在处理的标签
                - buffer: 未处理完的文本缓冲
        
        Returns:
            过滤后的文本
        """
        result = ""
        i = 0
        
        while i < len(text):
            if text[i] == '<':
                # 可能是开始标签
                tag_end = text.find('>', i)
                if tag_end != -1:
                    tag = text[i:tag_end+1]
                    if tag.startswith('</'):
                        # 结束标签
                        tag_name = tag[2:-1]
                        if filter_state['is_filtering'] and tag_name == filter_state['current_tag']:
                            filter_state['is_filtering'] = False
                            filter_state['current_tag'] = None
                    else:
                        # 开始标签
                        tag_name = tag[1:-1]
                        filter_state['is_filtering'] = True
                        filter_state['current_tag'] = tag_name
                    i = tag_end + 1
                    continue
                    
            if not filter_state['is_filtering']:
                result += text[i]
            i += 1

        return result
