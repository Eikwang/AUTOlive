"""Protobuf 生成模块

由 protoc 从 DouyinBarrageGrab .proto 文件自动生成。
重新生成命令：
    python -m grpc_tools.protoc \
        --python_out=danmaku_listener/adapters/protocols/proto/ \
        --proto_path=DouyinBarrageGrab/BarrageGrab/proto/ \
        wss.proto message.proto
"""

from danmaku_listener.adapters.protocols.proto.wss_pb2 import WssResponse
from danmaku_listener.adapters.protocols.proto.message_pb2 import (
    Response,
    Message,
    ChatMessage,
    GiftMessage,
    LikeMessage,
    MemberMessage,
    SocialMessage,
    ControlMessage,
    RoomUserSeqMessage,
    FansclubMessage,
    Common,
    User,
    GiftStruct,
)

__all__ = [
    "WssResponse",
    "Response",
    "Message",
    "ChatMessage",
    "GiftMessage",
    "LikeMessage",
    "MemberMessage",
    "SocialMessage",
    "ControlMessage",
    "RoomUserSeqMessage",
    "FansclubMessage",
    "Common",
    "User",
    "GiftStruct",
]
