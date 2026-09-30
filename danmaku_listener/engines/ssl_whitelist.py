"""SSL 白名单检查器

根据域名白名单判断是否对 HTTPS 流量进行 SSL 解密。
仅解密业务相关域名，其余域名直接转发（ssl_passthrough），避免性能开销。
"""

import fnmatch
import re
from typing import List, Optional


# 精确匹配或通配符匹配的域名列表
SSL_DECRYPT_HOSTNAMES = [
    "lf-cdn-tos.bytescm.com",           # 脚本CDN
    "live.douyin.com",                   # 直播页面
    "webcast.amemv.com",                 # 直播伴侣API
    "*-webcast-platform.bytetos.com",    # 新脚本地址
    "*webcast*",                         # 所有含 webcast 的域名
]

# 正则匹配的域名模式
SSL_DECRYPT_PATTERNS = [
    re.compile(r".*webcast.*"),
    re.compile(r"webcast\d+-ws-web-\w+\.(douyin|amemv)\.com"),
]


def check_host(hostname: str, extra_hostnames: Optional[List[str]] = None) -> bool:
    """检查域名是否在 SSL 解密白名单中

    仅白名单中的域名返回 True，其余返回 False。

    Args:
        hostname: 待检查的域名
        extra_hostnames: 额外的解密域名列表（追加到内置白名单）

    Returns:
        True 如果域名在白名单中（应解密），False 否则
    """
    if not hostname:
        return False

    hostname = hostname.strip().lower()

    # 精确匹配 / 通配符匹配
    all_hostnames = [h.lower() for h in SSL_DECRYPT_HOSTNAMES]
    if extra_hostnames:
        all_hostnames.extend(h.lower() for h in extra_hostnames)

    # 精确匹配
    if hostname in all_hostnames:
        return True

    # 通配符匹配（含 * 的域名）
    for pattern in all_hostnames:
        if "*" in pattern and fnmatch.fnmatch(hostname, pattern):
            return True

    # 正则匹配
    for regex in SSL_DECRYPT_PATTERNS:
        if regex.match(hostname):
            return True

    return False


class SSLWhitelistChecker:
    """SSL 白名单检查器

    封装 check_host 逻辑，支持追加额外域名。
    """

    def __init__(self, extra_hostnames: Optional[List[str]] = None):
        """初始化白名单检查器

        Args:
            extra_hostnames: 额外的解密域名列表（追加到内置白名单）
        """
        self.extra_hostnames = extra_hostnames or []

    def check(self, hostname: str) -> bool:
        """检查域名是否在白名单中

        Args:
            hostname: 待检查的域名

        Returns:
            True 如果域名在白名单中（应解密），False 否则
        """
        return check_host(hostname, self.extra_hostnames)
