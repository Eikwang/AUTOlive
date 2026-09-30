"""弹幕消息数据模型

定义标准化的弹幕消息格式，所有平台的消息都转换为此格式输出。
"""

from datetime import datetime
from enum import Enum
from typing import Optional, Literal

from pydantic import BaseModel, Field, field_validator


class MessageType(str, Enum):
    """消息类型枚举"""

    NORMAL = "normal"       # 普通弹幕
    GIFT = "gift"           # 礼物消息
    SYSTEM = "system"       # 系统消息


class GiftInfo(BaseModel):
    """礼物信息

    Attributes:
        user_name: 送礼者昵称
        gift_name: 礼物名称
        gift_count: 礼物数量
        gift_value: 礼物价值（可选）
    """

    user_name: str
    gift_name: str
    gift_count: int = 1
    gift_value: Optional[int] = None


class DanmakuMessage(BaseModel):
    """弹幕消息标准格式

    Attributes:
        platform: 平台标识 (douyin, douyu, bilibili, etc.)
        room_id: 直播间 ID
        user_name: 发送者昵称
        content: 弹幕文本内容
        timestamp: 时间戳（秒级）
        message_type: 消息类型 (normal/gift/system)
        gift_info: 礼物信息（仅当 message_type=gift 时存在）
    """

    platform: str
    room_id: str
    user_name: str
    content: str
    timestamp: int
    message_type: Literal["normal", "gift", "system"]
    gift_info: Optional[GiftInfo] = None

    model_config = {"extra": "forbid"}

    @field_validator("timestamp")
    @classmethod
    def validate_timestamp(cls, v: int) -> int:
        """验证时间戳必须为正数"""
        if v < 0:
            raise ValueError("timestamp must be positive")
        return v

    @property
    def is_gift(self) -> bool:
        """判断是否为礼物消息"""
        return self.message_type == MessageType.GIFT and self.gift_info is not None

    @property
    def formatted_time(self) -> str:
        """返回格式化的时间字符串"""
        return datetime.fromtimestamp(self.timestamp).strftime("%Y-%m-%d %H:%M:%S")

    def to_dict(self) -> dict:
        """转换为字典格式"""
        return self.model_dump()