"""DanmakuListener 平台适配器模块

提供各平台的消息解析适配器，将原始数据转换为标准格式。
"""

from danmaku_listener.adapters.base import BaseAdapter
from danmaku_listener.adapters.douyu import DouyuAdapter
from danmaku_listener.adapters.bilibili import BilibiliAdapter
from danmaku_listener.adapters.generic import GenericAdapter

__all__ = ["BaseAdapter", "DouyuAdapter", "BilibiliAdapter", "GenericAdapter"]