# -*- coding: UTF-8 -*-
"""
统一错误处理模块

提供统一的错误处理机制，包括：
1. 标准错误类型定义
2. 统一错误处理函数
3. 错误日志记录
4. 错误恢复机制
"""

import traceback
import logging
import time
from typing import Optional, Dict, Any, Callable
from enum import Enum

# 从 base 模块导入 logger
from .base import logger


class ErrorCode(Enum):
    """错误码枚举"""
    # 通用错误
    UNKNOWN_ERROR = 1000
    INVALID_PARAMETER = 1001
    FILE_NOT_FOUND = 1002
    PERMISSION_DENIED = 1003
    TIMEOUT_ERROR = 1004
    
    # 配置相关错误
    CONFIG_ERROR = 2000
    CONFIG_FILE_NOT_FOUND = 2001
    CONFIG_PARSE_ERROR = 2002
    CONFIG_VALIDATION_ERROR = 2003
    
    # 数据库相关错误
    DATABASE_ERROR = 3000
    DATABASE_CONNECTION_ERROR = 3001
    DATABASE_QUERY_ERROR = 3002
    DATABASE_INSERT_ERROR = 3003
    
    # 网络相关错误
    NETWORK_ERROR = 4000
    CONNECTION_ERROR = 4001
    NETWORK_TIMEOUT_ERROR = 4002
    HTTP_ERROR = 4003
    
    # 音频相关错误
    AUDIO_ERROR = 5000
    AUDIO_PLAY_ERROR = 5001
    AUDIO_SYNTHESIS_ERROR = 5002
    AUDIO_FILE_ERROR = 5003
    
    # LLM相关错误
    LLM_ERROR = 6000
    LLM_CONNECTION_ERROR = 6001
    LLM_RESPONSE_ERROR = 6002
    LLM_TIMEOUT_ERROR = 6003
    
    # 平台相关错误
    PLATFORM_ERROR = 7000
    PLATFORM_CONNECTION_ERROR = 7001
    PLATFORM_AUTH_ERROR = 7002
    PLATFORM_MESSAGE_ERROR = 7003


class LunaError(Exception):
    """Luna AI 基础异常类"""
    
    def __init__(self, message: str, error_code: ErrorCode = ErrorCode.UNKNOWN_ERROR, 
                 details: Optional[Dict[str, Any]] = None, cause: Optional[Exception] = None):
        """
        初始化异常
        
        Args:
            message: 错误消息
            error_code: 错误码
            details: 错误详情
            cause: 原始异常
        """
        super().__init__(message)
        self.message = message
        self.error_code = error_code
        self.details = details or {}
        self.cause = cause
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            'error_code': self.error_code.value,
            'error_name': self.error_code.name,
            'message': self.message,
            'details': self.details,
            'cause': str(self.cause) if self.cause else None
        }
    
    def __str__(self) -> str:
        """字符串表示"""
        return f"[{self.error_code.name}] {self.message}"


class ConfigError(LunaError):
    """配置相关错误"""
    def __init__(self, message: str, error_code: ErrorCode = ErrorCode.CONFIG_ERROR, **kwargs):
        super().__init__(message, error_code, **kwargs)


class DatabaseError(LunaError):
    """数据库相关错误"""
    def __init__(self, message: str, error_code: ErrorCode = ErrorCode.DATABASE_ERROR, **kwargs):
        super().__init__(message, error_code, **kwargs)


class NetworkError(LunaError):
    """网络相关错误"""
    def __init__(self, message: str, error_code: ErrorCode = ErrorCode.NETWORK_ERROR, **kwargs):
        super().__init__(message, error_code, **kwargs)


class AudioError(LunaError):
    """音频相关错误"""
    def __init__(self, message: str, error_code: ErrorCode = ErrorCode.AUDIO_ERROR, **kwargs):
        super().__init__(message, error_code, **kwargs)


class LLMError(LunaError):
    """LLM相关错误"""
    def __init__(self, message: str, error_code: ErrorCode = ErrorCode.LLM_ERROR, **kwargs):
        super().__init__(message, error_code, **kwargs)


class PlatformError(LunaError):
    """平台相关错误"""
    def __init__(self, message: str, error_code: ErrorCode = ErrorCode.PLATFORM_ERROR, **kwargs):
        super().__init__(message, error_code, **kwargs)


class ErrorHandler:
    """错误处理器"""
    
    def __init__(self):
        """初始化错误处理器"""
        self.error_callbacks: Dict[ErrorCode, Callable] = {}
        self.error_counts: Dict[ErrorCode, int] = {}
    
    def register_callback(self, error_code: ErrorCode, callback: Callable):
        """
        注册错误回调函数
        
        Args:
            error_code: 错误码
            callback: 回调函数
        """
        self.error_callbacks[error_code] = callback
    
    def handle_error(self, error: Exception, context: Optional[Dict[str, Any]] = None) -> bool:
        """
        处理错误
        
        Args:
            error: 异常对象
            context: 上下文信息
            
        Returns:
            bool: 是否成功处理
        """
        try:
            # 获取错误信息
            error_info = self._extract_error_info(error, context)
            
            # 记录错误日志
            self._log_error(error_info)
            
            # 更新错误计数
            self._update_error_count(error_info['error_code'])
            
            # 执行回调
            if error_info['error_code'] in self.error_callbacks:
                callback = self.error_callbacks[error_info['error_code']]
                callback(error_info)
            
            return True
        except Exception as e:
            logger.error(f"处理错误时发生异常: {e}")
            logger.error(traceback.format_exc())
            return False
    
    def _extract_error_info(self, error: Exception, context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        提取错误信息
        
        Args:
            error: 异常对象
            context: 上下文信息
            
        Returns:
            dict: 错误信息
        """
        error_info = {
            'error_type': type(error).__name__,
            'error_message': str(error),
            'error_code': ErrorCode.UNKNOWN_ERROR,
            'traceback': traceback.format_exc(),
            'context': context or {}
        }
        
        # 如果是 LunaError，提取更多信息
        if isinstance(error, LunaError):
            error_info.update({
                'error_code': error.error_code,
                'details': error.details,
                'cause': error.cause
            })
        
        return error_info
    
    def _log_error(self, error_info: Dict[str, Any]):
        """
        记录错误日志
        
        Args:
            error_info: 错误信息
        """
        error_code = error_info['error_code']
        error_message = error_info['error_message']
        context = error_info['context']
        
        # 构建日志消息
        log_message = f"[{error_code.name}] {error_message}"
        if context:
            log_message += f" | 上下文: {context}"
        
        # 记录日志
        logger.error(log_message)
        logger.error(error_info['traceback'])
    
    def _update_error_count(self, error_code: ErrorCode):
        """
        更新错误计数
        
        Args:
            error_code: 错误码
        """
        if error_code not in self.error_counts:
            self.error_counts[error_code] = 0
        self.error_counts[error_code] += 1
    
    def get_error_stats(self) -> Dict[str, int]:
        """
        获取错误统计
        
        Returns:
            dict: 错误统计
        """
        return {code.name: count for code, count in self.error_counts.items()}
    
    def reset_error_stats(self):
        """重置错误统计"""
        self.error_counts.clear()


# 创建全局错误处理器实例
error_handler = ErrorHandler()


def handle_error(error: Exception, context: Optional[Dict[str, Any]] = None) -> bool:
    """
    处理错误（全局函数）
    
    Args:
        error: 异常对象
        context: 上下文信息
        
    Returns:
        bool: 是否成功处理
    """
    return error_handler.handle_error(error, context)


def safe_execute(func: Callable, *args, default=None, context: Optional[Dict[str, Any]] = None, **kwargs):
    """
    安全执行函数
    
    Args:
        func: 要执行的函数
        *args: 函数参数
        default: 默认返回值
        context: 上下文信息
        **kwargs: 函数关键字参数
        
    Returns:
        函数返回值或默认值
    """
    try:
        return func(*args, **kwargs)
    except Exception as e:
        handle_error(e, context)
        return default


def retry_on_error(max_retries: int = 3, delay: float = 1.0, 
                   exceptions: tuple = (Exception,), context: Optional[Dict[str, Any]] = None):
    """
    错误重试装饰器
    
    Args:
        max_retries: 最大重试次数
        delay: 重试延迟（秒）
        exceptions: 需要重试的异常类型
        context: 上下文信息
        
    Returns:
        装饰器函数
    """
    def decorator(func):
        def wrapper(*args, **kwargs):
            last_exception = None
            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e
                    if attempt < max_retries:
                        logger.warning(f"函数 {func.__name__} 执行失败，第 {attempt + 1} 次重试: {e}")
                        time.sleep(delay)
                    else:
                        handle_error(e, context)
                        raise
            raise last_exception
        return wrapper
    return decorator


# 导出
__all__ = [
    'ErrorCode',
    'LunaError',
    'ConfigError',
    'DatabaseError',
    'NetworkError',
    'AudioError',
    'LLMError',
    'PlatformError',
    'ErrorHandler',
    'error_handler',
    'handle_error',
    'safe_execute',
    'retry_on_error'
]
