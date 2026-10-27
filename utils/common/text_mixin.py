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


# ---------- 点歌匹配引擎（/autoplan 2026-10-23 批次 R2，模块级纯函数）----------
# 双触发链路（弹幕意图/礼物映射）共用；前端预演复用同一实现消除 D4 双口径。

# 触发词命中后剥离的疑问/语气后缀（场景 2/4："主播会唱X吗？"）
_QUESTION_SUFFIX_RE = re.compile(r"[吗嘛么么呢吧呀啊呐?？!！。～~\s]+$")


def normalize_for_match(text):
    """匹配归一化（/autoplan main-20261026-214845 D4 共享口径）：
    去除全部空白字符 + 全角→半角 + 大小写折叠。文本匹配与音频标题匹配共用。"""
    if text is None:
        return ""
    text = str(text)
    out = []
    for ch in text:
        code = ord(ch)
        if code == 0x3000:  # 全角空格
            out.append(" ")
        elif 0xFF01 <= code <= 0xFF5E:  # 全角可见字符→半角
            out.append(chr(code - 0xFEE0))
        else:
            out.append(ch)
    return "".join("".join(out).split()).lower()


def match_song_intent(text: str, trigger_words, song_names, similarity: float = 0.5):
    """弹幕点歌意图匹配（R2：contains 命中——句中关键词，非前缀命令）。

    规则：
    1. 任一触发词在弹幕中 contains 命中（最长触发词优先，取首个命中点）；
    2. 歌名候选=触发词后缀段（"主播会唱X吗"）+前缀段（"X，能唱吗"），剥离
       疑问/语气后缀与衔接标点（R9 边界：触发词后置，native F1）；
    3. 候选对 song_names（歌曲列表的 song 字段集合）先子串采信（ratio=1.0），
       再 SequenceMatcher 相似度兜底（同 find_best_match 口径）。

    Returns:
        ("matched", song_name, ratio) 触发且命中——调用方播引导文案；
        ("triggered", None, None) 触发但列表无此歌——调用方播失败文案（场景 4）；
        None 未触发——静默（不处理，返回 False 交回弹幕链）。
    """
    text = (text or "").strip()
    if not text:
        return None
    hit_tw = None
    for tw in sorted((t for t in (trigger_words or []) if t), key=len, reverse=True):
        if tw in text:
            hit_tw = tw
            break
    if hit_tw is None:
        return None
    idx = text.index(hit_tw)
    rest_after = _QUESTION_SUFFIX_RE.sub("", text[idx + len(hit_tw):]).strip(" ，,、的")
    rest_before = _QUESTION_SUFFIX_RE.sub("", text[:idx]).strip(" ，,、的")
    names = [n for n in (song_names or []) if n]
    for candidate in (rest_after, rest_before):
        if not candidate:
            continue
        # ① 互为子串直接采信（"你是我的眼" in "你是我的眼（Live）"）
        for name in names:
            if name == candidate or name in candidate or candidate in name:
                return ("matched", name, 1.0)
        # ② 相似度兜底（与 find_best_match 同口径：最高 ratio 达阈值即命中）
        best, best_ratio = None, 0.0
        for name in names:
            ratio = difflib.SequenceMatcher(None, candidate, name).ratio()
            if ratio > best_ratio:
                best, best_ratio = name, ratio
        if best is not None and best_ratio >= float(similarity or 0):
            return ("matched", best, best_ratio)
    return ("triggered", None, None)


def match_gift_to_song_entry(gift_name: str, gift_song_map):
    """礼物名→歌曲映射匹配（R2/R5：归一后包含命中，最长键优先）。

    归一口径=CoverQueue.normalize_song（去空格/括注/大小写）——礼物归一与歌名
    归一同源（native F5：禁止第三套礼物匹配口径）。包含命中（"超级小火箭"命中
    "小火箭"）对齐前端 match_gift_to_song 预演语义，保证预演=实播。

    Returns:
        song_name 或 None。
    """
    if not gift_name or not isinstance(gift_song_map, dict) or not gift_song_map:
        return None
    from utils.cover_worker import CoverQueue
    target = CoverQueue.normalize_song(gift_name)
    if not target:
        return None
    # 精确（归一后相等）优先，其次包含命中（键⊂礼物名），均最长键优先
    normalized = {}
    for key, song in gift_song_map.items():
        if not key or not song:
            continue
        normalized[CoverQueue.normalize_song(key)] = (key, str(song).strip())
    for nk in sorted(normalized, key=len, reverse=True):
        if nk and nk == target:
            return normalized[nk][1]
    for nk in sorted(normalized, key=len, reverse=True):
        if nk and nk in target:
            return normalized[nk][1]
    return None


def match_gift_to_song_preview(gift_name: str, gift_song_map, song_names, similarity: float = 0.5):
    """礼物→歌曲完整预演匹配（前端预演与 utils 实播同规则的单一事实源）：
    ① match_gift_to_song_entry 映射命中；② 目标歌名对曲库包含命中采信；
    ③ SequenceMatcher 相似度兜底（同 find_best_match 口径）。

    Returns:
        (命中歌名, 相似度, 命中方式说明) 或 None（未命中）。
    """
    target = match_gift_to_song_entry(gift_name, gift_song_map)
    if target is None:
        return None
    via_key = next((k for k, v in (gift_song_map or {}).items()
                    if str(v).strip() == target), "")
    names = [n for n in (song_names or []) if n]
    for name in names:
        if name == target or name in target or target in name:
            return (name, 1.0, f"映射[{via_key}]→包含命中")
    best, best_ratio = None, 0.0
    for name in names:
        ratio = difflib.SequenceMatcher(None, target, name).ratio()
        if ratio > best_ratio:
            best, best_ratio = name, ratio
    if best is None or best_ratio < float(similarity or 0):
        return None
    return (best, best_ratio, f"映射[{via_key}]→相似度命中")


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


    # 读取违禁词/违禁拼音词表文件（E4 编码边界：check_sensitive_words 1/2/3 共用）
    def read_sensitive_word_file(self, file_path):
        """读取违禁词/违禁拼音词表文件，返回按行拆分的词表

        编码规则（D-20 矩阵 E4 条目）：指定 utf-8-sig——容忍 UTF-8 BOM；
        Windows 下若文件实为 GBK 等非 UTF-8 编码，解码失败时**行内报错并上抛**，
        绝不静默回退空表——静默空表=违禁词过滤失效（D-24 风险：过滤形同虚设
        而系统无任何告警）。异常上抛后由调用方显式处置，保持"功能禁用"可见。
        """
        try:
            with open(file_path, 'r', encoding='utf-8-sig') as file:
                return [line.strip() for line in file.readlines()]
        except UnicodeDecodeError:
            logger.error(
                f"违禁词文件解码失败（要求 UTF-8 编码，容忍 BOM）：{file_path}"
                f"——违禁词过滤已禁用！静默空表=违禁词过滤失效，请将文件转存为 UTF-8 编码后重启"
            )
            raise

    # 本地敏感词检测 传入敏感词库文件路径和待检查的文本
    def check_sensitive_words(self, file_path, text):
        sensitive_words = self.read_sensitive_word_file(file_path)

        for word in sensitive_words:
            if word in text:
                return True

        return False


    # 本地敏感词检测 Aho-Corasick 算法 传入敏感词库文件路径和待检查的文本
    def check_sensitive_words2(self, file_path, text):
        sensitive_words = self.read_sensitive_word_file(file_path)

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
        sensitive_words = self.read_sensitive_word_file(file_path)

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


    # 文本切分算法（A10 四句一切，2026-10-22 审查批次，义务 R2-A10）
    # 语义：「句」=以句末标点（。！？!?）结尾的单元；先按句末标点分句，
    # 无标点长句（>60 字）按逗号优先切点硬切兜底，每 4 句合并为一片段
    # （设计上限硬约束：每个文本片段最多包含四句）；段内短句自然并段
    # （下限保护），末段过短时从相邻段回拉一句平衡（不破坏四句上限）。
    _SENTENCE_SPLIT_RE = r'(?<=[。！？!?])'
    _LONG_RUNON_LEN = 60
    _SENTENCES_PER_SEGMENT = 4
    _MIN_SEGMENT_LEN = 10

    def _split_long_runon(self, s):
        """无标点长句兜底（>60 字）：逗号/分号/顿号优先切点，无切点按 60 字硬切。"""
        pieces = []
        remaining = s
        while len(remaining) > self._LONG_RUNON_LEN:
            window = remaining[:self._LONG_RUNON_LEN]
            cut = -1
            for sep in ("，", ",", "；", ";", "、"):
                idx = window.rfind(sep)
                if idx > 0:
                    cut = max(cut, idx)
            if cut <= 0:
                cut = self._LONG_RUNON_LEN - 1
            pieces.append(remaining[:cut + 1])
            remaining = remaining[cut + 1:]
        if remaining:
            pieces.append(remaining)
        return [p for p in pieces if p]

    def split_sentences(self, text):
        """四句一切切分（A10，替代旧"短句合并+标点切分"算法）。

        Args:
            text (str): 待切分文本

        Returns:
            list[str]: 文本片段列表，每片段最多含 4 个句末标点单元
        """
        if not text:
            return []

        # 1) 按句末标点分句
        raw = [s for s in re.split(self._SENTENCE_SPLIT_RE, text) if s]

        # 2) 构建有序单元：(文本, 是否句末标点句)；兜底片段独立成段标记
        #    （实施轮修正：兜底切出的 ≤60 字片段若参与 4 句合并会被拼回长段，兜底失效）
        units = []
        for s in raw:
            s = s.replace('\n', '')
            if len(s) > self._LONG_RUNON_LEN:
                for p in self._split_long_runon(s):
                    units.append((p, False))
            else:
                units.append((s, True))

        # 3) 流式组装：句按 4 句一组封段；兜底片段即到即封（保持原文顺序）
        result = []
        buf = []
        for seg_text, is_sentence in units:
            if not is_sentence:
                if buf:
                    result.append("".join(buf))
                    buf = []
                result.append(seg_text)
            else:
                buf.append(seg_text)
                if len(buf) >= self._SENTENCES_PER_SEGMENT:
                    result.append("".join(buf))
                    buf = []
        if buf:

            # 4) 末段过短（<10 字）从上一句组回拉一句（下限保护，不破坏四句上限）
            if len(buf) < self._MIN_SEGMENT_LEN and result:
                last = result[-1]
                sent_count = sum(last.count(c) for c in "。！？!?")
                if sent_count >= 2:
                    parts = [p for p in re.split(self._SENTENCE_SPLIT_RE, last) if p]
                    if len(parts) >= 2:
                        moved = parts.pop()
                        result[-1] = "".join(parts)
                        buf.insert(0, moved)
            result.append("".join(buf))

        return [s for s in result if s.strip()]


    # 字符串相似度（0-1，D4/F5 修复 2026-10-27）：归一化后 difflib 字符相似度。
    # 修复记录：本方法此前从未存在——feature_handler.find_answer/find_similar_answer
    # 调用恒 AttributeError 被吞→主知识库匹配运行时 100% 失败（/autoplan
    # main-20261026-214845 F5 运行时探证实锤）。T6 将在调用侧叠加 jieba 分词项。
    @staticmethod
    def calculate_similarity(text_a, text_b):
        """归一化文本相似度（0-1）：精确全等=1.0，否则 SequenceMatcher.ratio()。

        Args:
            text_a (str): 文本 A（如弹幕问题）
            text_b (str): 文本 B（如词库问题）

        Returns:
            float: 0-1 相似度
        """
        a, b = normalize_for_match(text_a), normalize_for_match(text_b)
        if not a or not b:
            return 0.0
        if a == b:
            return 1.0
        return difflib.SequenceMatcher(None, a, b).ratio()

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

    # ==================== 语言检测（基础功能版块 2026-10-22 批次） ====================
    # 历史背景：原 lang_check 以 langid 实现，因内存泄漏风险被注释弃用
    # （utils/common_original.py:407-418，translate_argos.py:53 记"已失联"）。
    # synthesis_manager.py:325 曾调用不存在的 lang_check（潜伏 AttributeError，
    # params.text_lang 为空即触发）。本实现为无第三方依赖的轻量字符区段判定。

    # 粤语特征字封闭常量：命中任一即判粤语。判定次序中粤语先于中文——
    # 粤语弹幕被 zh 放行后会以 text_lang=zh 合成导致发音失真（spec C5）。
    # 扩充原则：常见粤语独有汉字（普通话书面语不用的粤字）。
    YUE_FEATURE_CHARS = frozenset("唔嘅咗咁喺啲冇乜佢哋嚟咩冧掂嘢係喇啱嗰哂")

    _lang_detect_counts = {"yue": 0, "ko": 0, "ja": 0, "en": 0, "zh": 0}

    def lang_check(self, text: str) -> str:
        """轻量语言检测（基础功能 2026-10-22 批次新建，替代弃用的 langid 方案）。

        判定次序：粤字特征集 → 谚文(Hangul) → 假名(Kana) → 拉丁字母 → 汉字 → 默认 zh。
        粤语先于中文：粤字弹幕被 zh 放行后将以 text_lang=zh 合成发音失真（spec C5）。

        Args:
            text (str): 待检测文本（弹幕内容）

        Returns:
            str: 'yue' | 'ko' | 'ja' | 'en' | 'zh'
        """
        try:
            if not text:
                return "zh"
            # 第一遍：粤字特征全文扫描（任一粤字出现即 yue——先于其他判定，spec C5；
            # 不用"首字符即返"：如"呢个嘢"的"呢"是普通话也用的字，须扫到"嘢"才判粤）
            for ch in text:
                if ch in self.YUE_FEATURE_CHARS:
                    self._lang_detect_counts["yue"] = self._lang_detect_counts.get("yue", 0) + 1
                    return "yue"
            # 第二遍：字符区段判定（首个可判定字符）
            for ch in text:
                if "\u1100" <= ch <= "\u11FF" or "\uAC00" <= ch <= "\uD7AF":
                    lang = "ko"
                elif "\u3040" <= ch <= "\u30FF":
                    lang = "ja"
                elif ch.isascii() and ch.isalpha():
                    lang = "en"
                elif "\u4E00" <= ch <= "\u9FFF":
                    lang = "zh"
                else:
                    continue
                self._lang_detect_counts[lang] = self._lang_detect_counts.get(lang, 0) + 1
                return lang
            return "zh"
        except Exception as e:
            logger.error(f"lang_check 检测失败（降级 zh）：problem=语言检测异常；cause={e}；fix=查看日志定位")
            return "zh"

    def lang_detect_stats_summary(self) -> str:
        """各语言分类计数汇总（日志可观测误判盲区——DX medium）。

        Returns:
            str: 如 "语言检测统计: zh=123, en=45, yue=6, ja=3, ko=0"
        """
        c = self._lang_detect_counts
        return ("语言检测统计: zh={}, en={}, yue={}, ja={}, ko={}"
                .format(c.get("zh", 0), c.get("en", 0), c.get("yue", 0), c.get("ja", 0), c.get("ko", 0)))
