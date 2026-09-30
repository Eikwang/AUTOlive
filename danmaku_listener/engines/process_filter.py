"""进程过滤器

通过 psutil 端口反查 PID → 进程名，与白名单比对。
仅拦截指定浏览器进程的流量，其余进程的流量直接放行。
"""

import time
from typing import Dict, List, Optional, Set, Tuple

import psutil
from loguru import logger


class ProcessFilter:
    """进程过滤器

    通过端口反查进程名，与白名单比对决定是否拦截。
    缓存 PID→进程名映射（TTL 30 秒），避免频繁查询。
    """

    DEFAULT_ALLOWED = [
        "chrome", "msedge", "QQBrowser", "360se",
        "firefox", "2345Explorer", "iexplore",
    ]

    def __init__(self, allowed_processes: Optional[List[str]] = None):
        """初始化进程过滤器

        Args:
            allowed_processes: 允许的进程名列表（默认使用 DEFAULT_ALLOWED）
        """
        self.allowed: Set[str] = set(
            p.lower() for p in (allowed_processes or self.DEFAULT_ALLOWED)
        )
        self._cache: Dict[int, Tuple[str, float]] = {}  # pid → (name, timestamp)
        self._cache_ttl: float = 30.0

    def is_allowed_pid(self, pid: int, process_name: Optional[str] = None) -> bool:
        """判断指定 PID 的进程是否在白名单中

        Args:
            pid: 进程 ID
            process_name: 可选的进程名（如果提供则直接使用，否则从 psutil 查询）

        Returns:
            True 如果进程在白名单中，False 否则
        """
        if process_name is None:
            process_name = self._get_process_name(pid)

        if not process_name:
            return False

        return process_name.lower() in self.allowed

    def _get_process_name(self, pid: int) -> str:
        """获取进程名（带缓存）

        Args:
            pid: 进程 ID

        Returns:
            进程名，查询失败返回空字符串
        """
        now = time.time()

        # 缓存命中
        if pid in self._cache:
            name, ts = self._cache[pid]
            if now - ts < self._cache_ttl:
                return name

        # 查询进程名
        try:
            name = psutil.Process(pid).name()
            self._cache[pid] = (name, now)
            return name
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return ""

    def _find_pid_by_port(self, port: int) -> Optional[int]:
        """根据端口号查找对应的 PID

        Args:
            port: 端口号

        Returns:
            PID，找不到返回 None
        """
        try:
            for conn in psutil.net_connections():
                if conn.laddr.port == port and conn.status == 'ESTABLISHED':
                    return conn.pid
        except (psutil.AccessDenied, psutil.NoSuchProcess):
            pass
        return None
