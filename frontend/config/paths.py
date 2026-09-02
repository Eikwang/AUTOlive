"""
路径管理模块
负责管理应用程序的文件路径和目录
"""
from pathlib import Path
import sys


def get_base_path() -> Path:
    """
    获取基础路径
    
    Returns:
        基础路径（打包后的可执行文件目录或源代码目录）
    """
    if getattr(sys, 'frozen', False):
        # 当前是打包后的可执行文件
        bundle_dir = Path(getattr(sys, '_MEIPASS', Path(sys.executable).parent))
        return bundle_dir.resolve()
    else:
        # 当前是源代码
        return Path(__file__).parent.parent.parent.resolve()


def get_config_path() -> Path:
    """
    获取配置文件路径
    
    Returns:
        配置文件路径
    """
    return get_base_path() / 'config.json'


def get_log_dir() -> Path:
    """
    获取日志目录
    
    Returns:
        日志目录路径
    """
    log_dir = get_base_path() / 'log'
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir


def get_output_dir() -> Path:
    """
    获取输出目录
    
    Returns:
        输出目录路径
    """
    output_dir = get_base_path() / 'out'
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def ensure_dir(path: Path) -> Path:
    """
    确保目录存在
    
    Args:
        path: 目录路径
        
    Returns:
        目录路径
    """
    path.mkdir(parents=True, exist_ok=True)
    return path
