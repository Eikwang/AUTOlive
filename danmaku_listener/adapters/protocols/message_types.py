"""抖音平台协议定义

定义抖音 WebSocket 消息类型和解析逻辑。
"""

from enum import Enum
from typing import Optional


class DouyinMessageType(str, Enum):
    """抖音消息类型枚举

    对应抖音 WebSocket 消息中的 method 字段值。
    """

    CHAT = "chat"           # 普通弹幕
    GIFT = "gift"           # 礼物消息
    LIKE = "like"           # 点赞消息
    MEMBER = "member"       # 进房消息
    SOCIAL = "social"       # 关注消息
    ROOM_STATS = "room_stats"  # 直播间统计
    CONTROL = "control"     # 控制消息（下播等）
    FANSCLUB = "fansclub"   # 粉丝团消息
    UNKNOWN = "unknown"     # 未知类型


# 消息类型映射表
MESSAGE_TYPE_MAP = {
    "WebcastChatMessage": DouyinMessageType.CHAT,
    "WebcastGiftMessage": DouyinMessageType.GIFT,
    "WebcastLikeMessage": DouyinMessageType.LIKE,
    "WebcastMemberMessage": DouyinMessageType.MEMBER,
    "WebcastSocialMessage": DouyinMessageType.SOCIAL,
    "WebcastRoomUserSeqMessage": DouyinMessageType.ROOM_STATS,
    "WebcastControlMessage": DouyinMessageType.CONTROL,
    "WebcastFansclubMessage": DouyinMessageType.FANSCLUB,
}


def parse_message_type(method: Optional[str]) -> DouyinMessageType:
    """解析消息类型

    Args:
        method: 抖音消息中的 method 字段值

    Returns:
        对应的 DouyinMessageType 枚举值
    """
    if not method:
        return DouyinMessageType.UNKNOWN

    return MESSAGE_TYPE_MAP.get(method, DouyinMessageType.UNKNOWN)