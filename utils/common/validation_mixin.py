"""
数据校验模块 - 提供各种数据验证功能
"""

import re
import os
from urllib.parse import urlparse
from profanity import profanity

from ..base import logger


class ValidationMixin:
    """数据校验 Mixin"""

    # 检测是否为纯数字
    def is_pure_number(self, text):
        """检测是否为纯数字

        Args:
            text (str): 待检测的文本

        Returns:
            bool: 是否为纯数字
        """
        return text.isdigit()

    # 是否是url
    def is_url_check(self, url):
        try:
            result = urlparse(url)
            return all([result.scheme, result.netloc])
        except ValueError:
            return False
        
    # 是否是IP地址
    def is_valid_ip(self, ip):
        import ipaddress

        try:
            ipaddress.ip_address(ip)
            return True
        except ValueError:
            return False

    # 是否是端口
    def is_valid_port(self, port):
        try:
            port_num = int(port)
            return 0 < port_num <= 65535
        except ValueError:
            return False

    # 判断传入的字符串是否是文件夹路径或文件路径，且此文件夹路径或文件路径是否存在，返回bool
    def is_dir_or_file(self, path: str, type: str="all"):
        """判断传入的字符串是否是文件夹路径或文件路径，且此文件夹路径或文件路径是否存在，返回bool

        Args:
            path (str): 文件夹路径或文件路径
            type (str, optional): 检测类型. Defaults to "all".

        Returns:
            bool: 结果
        """
        if type == "dir":
            if os.path.isdir(path):
                return True
            return False
        elif type == "file":
            if os.path.isfile(path):
                return True
            return False
        else:
            if os.path.isdir(path) or os.path.isfile(path):
                return True
            return False
        
    # 识别操作系统
    def detect_os(self):
        """
        识别操作系统
        """
        import platform

        system = platform.system()
        if system == 'Linux':
            return 'Linux'
        elif system == 'Windows':
            return 'Windows'
        elif system == 'Darwin':
            return 'MacOS'
        
        return '未知系统'

    # 判断文本是否可以转为dict JSON格式
    def is_json_convertible(self, text: str) -> bool:
        """判断文本是否可以转为dict JSON格式

        Args:
            text (str): 待判断内容

        Returns:
            bool: T / F
        """
        try:
            import json
            json.loads(text)
            return True
        except json.JSONDecodeError:
            return False

    # 判断字符串是否全为标点符号
    def is_punctuation_string(self, string):
        # 使用正则表达式匹配标点符号
        pattern = r'^[^\w\s]+$'
        return re.match(pattern, string) is not None
    
    # 判断字符串是否全为空格和特殊字符
    def is_all_space_and_punct(self, text):
        pattern = r'^[\s\W]+$'
        return re.match(pattern, text) is not None

    # 违禁词校验
    def profanity_content(self, content):
        return profanity.contains_profanity(content)

    # 判断字符串是否以一个list中任意一个字符串打头
    def starts_with_any(self, string, prefixes):
        """判断字符串是否以一个list中任意一个字符串打头

        Args:
            string (str): 待判断的字符串
            prefixes (list): 匹配的字符串数组

        Returns:
            str: 命中的匹配到的字符串/None
        """
        try:
            for prefix in prefixes:
                if string.startswith(prefix):
                    return prefix
        except AttributeError as e:
            # 处理异常，例如打印错误消息或者返回 False
            logger.error(f"Error: {e}")
            return None
        
        return None
