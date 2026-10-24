"""
通用工具模块
包含常用的辅助函数
"""
from typing import Any, Dict, List, Optional
import json


def _repair_char_split_text(value: str) -> str:
    """检测并重组「每行单字符」损坏形态的文本（三轮验收修复）。

    损坏机制（修复前版本，已根治）：旧 textarea_data_change 对 str 按字符迭代
    生成「每字符一行」的显示文本 → 旧 auto_save 把显示文本原样存回 config →
    存量值变成「当\\n前\\n时\\n…」形态。str 原样返回修复只防新损坏，对存量
    损坏值原样显示=依然每字符一行（用户三轮实测截图）。

    判据（保守，防误伤合法文案）：非空行数 >2 且**全部**非空行都只有 1 个
    字符 → 按序重组为连续字符串；含任一长行即视为合法多行文案原样返回。
    """
    lines = [line.strip() for line in value.split("\n") if line.strip()]
    if len(lines) > 2 and all(len(line) == 1 for line in lines):
        return "".join(lines)
    return value


def textarea_data_change(data: Optional[List[str]]) -> str:
    """
    字符串数组数据格式转换

    Args:
        data: 字符串数组

    Returns:
        转换后的字符串（每项一行）

    UI-16 二轮验收修复：存量配置中该键可能被存为字符串（旧数据/异常保存），
    字符串直接进 for 会按字符迭代——每个字符独占一行（用户实测「每个字符都
    换行」的根因）。字符串原样返回（含「每行单字符」损坏形态重组，见
    _repair_char_split_text），非列表非字符串返回空串。
    """
    if data is None:
        return ""
    if isinstance(data, str):
        return _repair_char_split_text(data)
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
