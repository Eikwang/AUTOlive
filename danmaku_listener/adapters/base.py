"""平台适配器抽象基类

定义所有平台适配器必须实现的接口，确保不同平台的解析逻辑统一。
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional

from danmaku_listener.bus.message import DanmakuMessage


class BaseAdapter(ABC):
    """平台适配器抽象基类

    每个平台需要实现自己的适配器来解析原始数据并转换为标准格式。
    """

    @property
    @abstractmethod
    def platform(self) -> str:
        """返回平台标识"""
        pass

    @abstractmethod
    async def parse(self, raw_data: Any, context: Optional[Dict] = None) -> Optional[DanmakuMessage]:
        """解析原始数据为标准消息格式

        Args:
            raw_data: 原始数据（可能是 bytes、dict、str 等）
            context: 可选的上下文信息（如 room_id、process_name 等）

        Returns:
            DanmakuMessage 实例，如果解析失败返回 None
        """
        pass

    @abstractmethod
    def can_parse(self, data: Any) -> bool:
        """判断是否能处理该数据

        Args:
            data: 待检测的数据

        Returns:
            True 如果适配器能处理此数据
        """
        pass