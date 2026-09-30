"""DanmakuListener - 高性能跨平台弹幕监听解决方案

提供代理模式和浏览器模式的混合架构，支持抖音、斗鱼、B站等多平台弹幕监听。

子模块采用 PEP 562 惰性导入：`danmaku_listener.contract`、`danmaku_listener.config`
等轻量子包不触发 playwright 等重依赖的加载。
"""

from typing import Any

__version__ = "0.3.0"

__all__ = ["DanmakuListener", "DanmakuMessage", "GiftInfo", "MetricsCollector", "__version__"]

_LAZY_EXPORTS = {
    "DanmakuListener": ("danmaku_listener.listener", "DanmakuListener"),
    "DanmakuMessage": ("danmaku_listener.bus.message", "DanmakuMessage"),
    "GiftInfo": ("danmaku_listener.bus.message", "GiftInfo"),
    "MetricsCollector": ("danmaku_listener.core", "MetricsCollector"),
}


def __getattr__(name: str) -> Any:
    entry = _LAZY_EXPORTS.get(name)
    if entry is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    mod_path, attr = entry
    import importlib

    module = importlib.import_module(mod_path)
    return getattr(module, attr)
