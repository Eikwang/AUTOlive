"""Loguru 日志配置模块

提供统一的日志初始化和配置功能，支持结构化输出和敏感信息遮蔽。
"""

import json
import sys
from typing import Any, Dict, Optional

from loguru import logger

from danmaku_listener.config.settings import get_settings


# 需要遮蔽的敏感字段名（小写匹配）
SENSITIVE_KEYS = {
    "cookie",
    "cookies",
    "token",
    "access_token",
    "refresh_token",
    "password",
    "passwd",
    "secret",
    "authorization",
    "api_key",
    "apikey",
    "private_key",
}

# 遮蔽后的替换值
MASKED_VALUE = "***MASKED***"


def mask_sensitive(data: Any) -> Any:
    """遮蔽字典中的敏感字段

    递归处理嵌套字典，将敏感字段的值替换为掩码。

    Args:
        data: 原始数据（字典或其他）

    Returns:
        遮蔽后的数据（保留原结构，仅敏感字段被替换）
    """
    if isinstance(data, dict):
        result = {}
        for key, value in data.items():
            if isinstance(key, str) and key.lower() in SENSITIVE_KEYS:
                result[key] = MASKED_VALUE
            else:
                result[key] = mask_sensitive(value)
        return result
    elif isinstance(data, list):
        return [mask_sensitive(item) for item in data]
    else:
        return data


def setup_logger(
    level: Optional[str] = None,
    log_file: Optional[str] = None,
) -> None:
    """配置全局日志

    Args:
        level: 日志级别，默认从配置读取
        log_file: 日志文件路径，可选
    """
    # 移除默认 handler
    logger.remove()

    # 获取日志级别
    if level is None:
        settings = get_settings()
        level = settings.log_level

    # 添加控制台输出
    logger.add(
        sys.stderr,
        level=level,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
        colorize=True,
    )

    # 添加文件输出（如果指定）
    if log_file:
        logger.add(
            log_file,
            level=level,
            format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}",
            rotation="10 MB",
            retention="30 days",
            encoding="utf-8",
        )


def setup_structured_logger(
    level: Optional[str] = None,
    log_file: Optional[str] = None,
) -> None:
    """配置结构化日志

    支持结构化输出（JSON 格式）、自动轮转、敏感信息遮蔽。

    Args:
        level: 日志级别，默认从配置读取
        log_file: 日志文件路径，可选
    """
    # 移除默认 handler
    logger.remove()

    if level is None:
        settings = get_settings()
        level = settings.log_level

    # 结构化 JSON 格式的 sink 函数
    def json_sink(message):
        """将日志记录格式化为 JSON"""
        record = message.record
        log_entry = {
            "timestamp": record["time"].strftime("%Y-%m-%d %H:%M:%S"),
            "level": record["level"].name,
            "module": record["name"],
            "function": record["function"],
            "line": record["line"],
            "message": record["message"],
        }

        # 遮蔽 extra 中的敏感字段
        extra = record.get("extra", {})
        if extra:
            log_entry["extra"] = mask_sensitive(dict(extra))

        # 异常信息
        if record.get("exception"):
            exc = record["exception"]
            log_entry["exception"] = str(exc)

        print(json.dumps(log_entry, ensure_ascii=False), file=sys.stderr)

    # 添加控制台结构化输出
    logger.add(
        json_sink,
        level=level,
    )

    # 添加文件输出（如果指定），结构化 JSON 格式 + 自动轮转
    if log_file:
        logger.add(
            log_file,
            level=level,
            format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}",
            rotation="10 MB",
            retention="30 days",
            encoding="utf-8",
        )


def get_logger(name: str) -> logger:
    """获取带名称的 logger

    Args:
        name: 模块名称

    Returns:
        Logger 实例
    """
    return logger.bind(module=name)