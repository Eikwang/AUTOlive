"""
路径管理模块
负责管理应用程序的文件路径和目录

frozen 判定（发行改造阶段 C1，2026-11-22）：
- ``__compiled__`` 为 Nuitka 官方稳定标记（编译后模块命名空间存在该全局名）；
- ``sys.frozen`` 冗余兜底（standalone exe 同样成立）；
- Nuitka standalone 无 PyInstaller ``_MEIPASS`` 语义——frozen 下基根即
  exe 所在目录（AUTOlive.exe 与 runtime312/ models/ 等平铺于发行包根）。
"""
from pathlib import Path
import sys


def get_base_path() -> Path:
    """
    获取基础路径

    Returns:
        基础路径（frozen：exe 所在目录；源码：仓库根目录）
    """
    if "__compiled__" in globals() or getattr(sys, "frozen", False):
        # Nuitka 编译后的 exe / 其他 frozen 形态
        return Path(sys.executable).parent.resolve()
    # 源代码
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
    获取日志目录（薄委托注册中心：config paths.autolive_home 可整体平移）

    Returns:
        日志目录路径
    """
    from utils import resource_paths
    return resource_paths.log_dir()


def get_output_dir() -> Path:
    """
    获取输出目录（薄委托注册中心）

    Returns:
        输出目录路径
    """
    from utils import resource_paths
    return resource_paths.output_dir()


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
