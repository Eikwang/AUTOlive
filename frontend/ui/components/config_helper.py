"""
配置辅助工具
提供嵌套字典访问的辅助函数
"""
from typing import Any, Dict, Optional


def get_nested_value(data: Dict[str, Any], *keys: str, default: Any = None) -> Any:
    """
    安全地获取嵌套字典的值
    
    Args:
        data: 字典数据
        *keys: 键路径
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


def safe_get_theme_config(config: Dict[str, Any], element: str, default: str = "") -> str:
    """
    安全地获取主题配置

    Args:
        config: 配置字典
        element: 元素名称
        default: 默认值

    Returns:
        主题配置值
    """
    from frontend.styles.themes import get_theme_manager
    theme_manager = get_theme_manager()
    theme_config = theme_manager.get_theme_config()
    return theme_config.get(element, default)


def get_config_items(function_name: str) -> Dict[str, Any]:
    """
    获取功能的配置项

    Args:
        function_name: 功能名称

    Returns:
        配置项字典
    """
    from frontend.config.settings import get_config
    config = get_config()
    config_items = config.get("webui", "config_items", default={})
    return config_items.get(function_name, {})
