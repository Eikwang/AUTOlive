# -*- coding: UTF-8 -*-
"""
日志模块

提供日志配置和 logger
"""

import sys
import logging
from loguru import logger

# 从 base 模块导入 logger（避免循环导入）
from .base import logger as base_logger

# 配置文件路径
config_path = 'config.json'

# 配置 logger
def configure_logger(file_path, log_level, max_file_size):
    level = log_level.upper() if log_level else "INFO"
    max_size = max_file_size if max_file_size else "1024 MB"

    # 清空之前的handlers
    logger.remove()

    # 配置控制台输出
    logger.add(sys.stderr, colorize=True, level=level)

    # 配置文件输出
    logger.add(file_path, level=level, rotation=max_size)

# 获取 jieba 库的日志记录器，并设置其级别为 WARNING
jieba_logger = logging.getLogger("jieba")
jieba_logger.setLevel(logging.WARNING)

# 获取 httpx 库的日志记录器
httpx_logger = logging.getLogger("httpx")
# 设置 httpx 日志记录器的级别为 WARNING
httpx_logger.setLevel(logging.WARNING)

# 获取特定库的日志记录器
watchfiles_logger = logging.getLogger("watchfiles")
# 设置日志级别为WARNING或更高，以屏蔽INFO级别的日志消息
watchfiles_logger.setLevel(logging.WARNING)

# 获取 werkzeug 库的日志记录器
werkzeug_logger = logging.getLogger("werkzeug")
# 设置 httpx 日志记录器的级别为 WARNING
werkzeug_logger.setLevel(logging.WARNING)

# 将 loguru 与标准 logging 结合
class InterceptHandler(logging.Handler):
    def emit(self, record):
        try:
            loguru_logger = logger.bind(name=record.name)
            level = logger.level(record.levelname).name
            frame, depth = logging.currentframe(), 2
            while frame is not None and frame.f_globals["__name__"] != __name__:
                frame = frame.f_back
                depth += 1
            loguru_logger.opt(depth=depth, exception=record.exc_info).log(level, record.getMessage())
        except ValueError:
            # 处理 call stack is not deep enough 的情况
            loguru_logger = logger.bind(name=record.name)
            level = logger.level(record.levelname).name
            loguru_logger.opt(exception=record.exc_info).log(level, record.getMessage())
        except Exception:
            # 忽略日志处理错误，避免影响主程序
            pass

# 将 InterceptHandler 添加到 jieba logger
jieba_logger.addHandler(InterceptHandler())
# 将 InterceptHandler 添加到 httpx logger
httpx_logger.addHandler(InterceptHandler())
watchfiles_logger.addHandler(InterceptHandler())
werkzeug_logger.addHandler(InterceptHandler())

# 导出 logger 供其他模块使用
__all__ = ["logger"]
