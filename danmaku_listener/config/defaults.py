"""默认配置值常量"""

# ===== 浏览器引擎配置 =====
DEFAULT_MAX_ROOMS = 10
DEFAULT_BROWSER_HEADLESS = True

# ===== Cookie 存储 =====
DEFAULT_COOKIE_DIR = "./cookie"

# ===== 重连策略 =====
DEFAULT_RECONNECT_MAX_RETRIES = 3
DEFAULT_RECONNECT_BASE_DELAY = 1  # 秒

# ===== 心跳保活 =====
DEFAULT_HEARTBEAT_INTERVAL = 30  # 秒

# ===== 去重窗口 =====
DEFAULT_DEDUP_WINDOW_SIZE = 300  # 条消息