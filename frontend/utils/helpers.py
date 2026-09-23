"""
辅助函数模块
包含系统命令等辅助功能
"""
import os
import sys
import subprocess
import signal
import traceback
from typing import Dict, Optional, Any
from pathlib import Path


class SystemCommand:
    """系统命令执行器"""
    
    @staticmethod
    def execute(command: str, timeout: int = 30) -> Dict[str, Any]:
        """
        执行系统命令
        
        Args:
            command: 命令字符串
            timeout: 超时时间（秒）
            
        Returns:
            包含 success, stdout, stderr, returncode 的字典
        """
        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout
            )
            return {
                "success": result.returncode == 0,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "returncode": result.returncode
            }
        except subprocess.TimeoutExpired:
            return {
                "success": False,
                "stdout": "",
                "stderr": "命令执行超时",
                "returncode": -1
            }
        except Exception as e:
            return {
                "success": False,
                "stdout": "",
                "stderr": str(e),
                "returncode": -1
            }
    
    @staticmethod
    def execute_async(command: str) -> subprocess.Popen:
        """
        异步执行系统命令
        
        Args:
            command: 命令字符串
            
        Returns:
            子进程对象
        """
        return subprocess.Popen(
            command,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )


def get_file_extension(filename: str) -> str:
    """
    获取文件扩展名
    
    Args:
        filename: 文件名
        
    Returns:
        文件扩展名（小写）
    """
    return Path(filename).suffix.lower()


def is_valid_url(url: str) -> bool:
    """
    检查URL是否有效
    
    Args:
        url: URL字符串
        
    Returns:
        是否有效
    """
    import re
    url_pattern = re.compile(
        r'^https?://'  # http:// or https://
        r'(?:(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+'  # domain...
        r'(?:[A-Z]{2,6}\.?|[A-Z0-9-]{2,}\.?)|'  # host...
        r'localhost|'  # localhost...
        r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})'  # ...or ip
        r'(?::\d+)?'  # optional port
        r'(?:/?|[/?]\S+)$', re.IGNORECASE)
    return url_pattern.match(url) is not None
