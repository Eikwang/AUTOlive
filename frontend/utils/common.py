"""
通用工具模块
包含常用的辅助函数
"""
from typing import Any, Dict, List, Optional
import json


def textarea_data_change(data: Optional[List[str]]) -> str:
    """
    字符串数组数据格式转换

    Args:
        data: 字符串数组

    Returns:
        转换后的字符串（每项一行）

    UI-16 二轮验收修复：存量配置中该键可能被存为字符串（旧数据/异常保存），
    字符串直接进 for 会按字符迭代——每个字符独占一行（用户实测「每个字符都
    换行」的根因）。字符串原样返回，非列表非字符串返回空串。
    """
    if data is None:
        return ""
    if isinstance(data, str):
        return data
    if not isinstance(data, (list, tuple)):
        return ""
    tmp_str = ""
    for tmp in data:
        tmp_str = tmp_str + str(tmp) + "\n"
    return tmp_str


def format_config_value(value: Any) -> str:
    """
    格式化配置值为字符串
    
    Args:
        value: 配置值
        
    Returns:
        格式化后的字符串
    """
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def parse_config_value(value: str) -> Any:
    """
    解析配置值字符串
    
    Args:
        value: 配置值字符串
        
    Returns:
        解析后的值
    """
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return value


def safe_get_nested(data: Dict, keys: List[str], default: Any = None) -> Any:
    """
    安全地获取嵌套字典的值
    
    Args:
        data: 字典数据
        keys: 键路径
        default: 默认值
        
    Returns:
        值，如果不存在则返回默认值
    """
    result = data
    for key in keys:
        if isinstance(result, dict):
            result = result.get(key, default)
        else:
            return default
    return result


def truncate_string(text: str, max_length: int = 100, suffix: str = "...") -> str:
    """
    截断字符串
    
    Args:
        text: 原始字符串
        max_length: 最大长度
        suffix: 后缀
        
    Returns:
        截断后的字符串
    """
    if len(text) <= max_length:
        return text
    return text[:max_length - len(suffix)] + suffix


def is_url_check(url: str) -> bool:
    """
    检查URL格式是否正确

    Args:
        url: URL字符串

    Returns:
        URL格式是否正确
    """
    import re
    url_pattern = re.compile(
        r'^https?://'  # http:// or https://
        r'(?:(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+[A-Z]{2,6}\.?|'  # domain...
        r'localhost|'  # localhost...
        r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})'  # ...or ip
        r'(?::\d+)?'  # optional port
        r'(?:/?|[/?]\S+)$', re.IGNORECASE)
    return bool(url_pattern.match(url))
