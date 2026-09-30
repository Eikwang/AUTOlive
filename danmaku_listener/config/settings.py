"""DanmakuListener 配置模型

使用 Pydantic Settings 实现类型安全的配置管理，支持环境变量覆盖。
契约 v1 阶段 0 扩展：TOML 配置文件 + 四层优先级（内置默认 → 配置文件 →
环境变量 → 每房间覆盖）+ 全部有界参数的默认值与配置键（DX 交付束 B）。
"""

from functools import lru_cache
from typing import Optional

import tomllib
from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

#: 逐层优先级说明（docs/integration/autolive.md 与运维手册引用）
CONFIG_PRIORITY_DOC = "内置默认 → TOML 配置文件 → 环境变量 → 每房间覆盖"


class Settings(BaseSettings):
    """DanmakuListener 全局配置模型

    Attributes:
        log_level: 日志级别 (DEBUG/INFO/WARNING/ERROR)
        max_rooms: 同时监听的最大房间数
        browser_headless: 浏览器是否使用无头模式
        cookie_dir: Cookie 存储目录
        reconnect_max_retries: 重连最大尝试次数
        reconnect_base_delay: 重连基础延迟（秒）
        heartbeat_interval: 心跳检测间隔（秒）
        dedup_window_size: 去重窗口大小（条消息）
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # ===== 日志配置 =====
    log_level: str = "INFO"

    # ===== 浏览器引擎配置 =====
    max_rooms: int = 10
    browser_headless: bool = True

    # ===== Cookie 存储 =====
    cookie_dir: str = "./cookie"

    # ===== 重连策略 =====
    reconnect_max_retries: int = 3
    reconnect_base_delay: float = 1.0

    # ===== 心跳保活 =====
    heartbeat_interval: int = 30

    # ===== 去重窗口 =====
    dedup_window_size: int = 300

    # ===== 契约 v1 新增：推送通道与安全（DX-C / Eng S-2） =====
    ws_port: int = 8765                    # 本机 WS 推送端口
    ws_bind: str = "127.0.0.1"             # 默认仅本机（边界由 bind 保证）
    ws_token_file: Optional[str] = None    # 预共享 token 文件（受限权限）；或环境变量 DANMAKU_TOKEN
    web_port: int = 8080                   # 测试控制台端口（与 ws_port 8765 隔离，不得占用）
    credential_dir: str = "./cookie"       # 登录态凭据目录（引擎写、AUTOlive 只读）
    bilibili_cookie_file: str = "./cookie/bilibili_cookies.txt"  # B站登录 cookie（SESSDATA 等；游客限流时必需）

    # ===== 契约 v1 新增：消息总线与背压（有界参数，DX-B / Eng M/P） =====
    bus_ring_capacity: int = 10000         # 有界环形缓冲容量（每房间）
    bus_overflow_drop: str = "oldest"      # 溢出丢弃方向：oldest / newest（缓冲内）
    bus_drop_order: str = "LIKE,ENTER_ROOM,DANMU"  # 背压分级丢弃顺序（先丢在前）
    bus_dedup_window_seconds: int = 120    # 去重窗口时长（时间淘汰）
    bus_dedup_capacity: int = 4096         # 去重窗口容量上限（ID 集合有界）
    backpressure_report_seconds: int = 60  # BACKPRESSURE 窗口计数上报周期

    # ===== 契约 v1 新增：引擎心跳与重试（Eng O / CEO 0.4） =====
    engine_heartbeat_seconds: int = 10     # 引擎→AUTOlive 心跳周期
    engine_lost_periods: int = 3           # 连续 N 周期未收到判定失联
    platform_silence_timeout: int = 30     # 平台连接静默检测超时（秒）
    fast_retry_max: int = 3                # 快速退避重试次数上限
    slow_retry_cap_seconds: int = 900      # 慢速无限重试退避封顶（15 分钟）
    session_lifetime_seconds: int = 14400  # 兜底/受控页面有界会话寿命（4 小时）
    heap_rebuild_threshold_mb: int = 300   # 页面堆监控提前重建阈值

    @model_validator(mode="after")
    def _validate_bounds(self) -> "Settings":
        if self.bus_ring_capacity <= 0:
            raise ValueError("bus_ring_capacity must be positive")
        if self.bus_dedup_window_seconds <= 0 or self.bus_dedup_capacity <= 0:
            raise ValueError("dedup window bounds must be positive")
        if self.ws_bind not in ("127.0.0.1", "0.0.0.0", "localhost"):
            raise ValueError("ws_bind must be 127.0.0.1 / localhost / 0.0.0.0")
        return self

    @property
    def bus_drop_order_list(self) -> list:
        """背压分级丢弃顺序（先丢在前）"""
        return [t.strip().upper() for t in self.bus_drop_order.split(",") if t.strip()]

_override_settings: Optional[Settings] = None


def set_settings(settings: Settings) -> None:
    """植入全局配置实例（S2：config.local.toml 默认加载的传播机制，round3 L-1）

    lru_cache 无插入 API，故用模块级覆盖变量：植入后 get_settings 优先返回它，
    确保 web app/bridge/引擎全部拿到配置值。
    """
    global _override_settings
    _override_settings = settings


def reset_settings() -> None:
    """清除覆盖实例（恢复 lru_cache 默认路径；测试用）"""
    global _override_settings
    _override_settings = None
    get_settings.cache_clear()


@lru_cache
def _default_settings() -> Settings:
    return Settings()


def get_settings() -> Settings:
    """获取全局配置单例（优先返回 set_settings 植入的覆盖实例）

    Returns:
        Settings 实例
    """
    if _override_settings is not None:
        return _override_settings
    return _default_settings()


def load_toml_overrides(path: str) -> dict:
    """读取 TOML 配置文件并返回展平的覆盖字典（TOML → Settings 字段名）

    支持嵌套节与平铺键两种写法；
    优先级：内置默认 < 本函数结果 < 环境变量 < 每房间覆盖（见 CONFIG_PRIORITY_DOC）。

    Args:
        path: TOML 文件路径

    Returns:
        适用于 Settings(**overrides) 的字段字典；未知键会被忽略并记录警告
    """
    with open(path, "rb") as fh:
        data = tomllib.load(fh)
    field_names = set(Settings.model_fields.keys())
    overrides: dict = {}
    for key, value in data.items():
        if isinstance(value, dict):
            for sub, sub_val in value.items():
                flat = f"{key}_{sub}"
                if flat in field_names:
                    overrides[flat] = sub_val
        elif key in field_names:
            overrides[key] = value
    return overrides
