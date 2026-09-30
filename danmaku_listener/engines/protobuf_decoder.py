"""Protobuf + Gzip 解码器

完整解码链路：
WebSocket 二进制帧 → WssResponse → 检查 compress_type → Gzip 解压（如需）→ Response → 遍历 Message → List[DecodedMessage]
"""

import gzip
from dataclasses import dataclass
from typing import List

from loguru import logger

from danmaku_listener.adapters.protocols.proto.wss_pb2 import WssResponse
from danmaku_listener.adapters.protocols.proto.message_pb2 import Response


@dataclass
class DecodedMessage:
    """解码后的消息

    Attributes:
        method: 消息类型（如 "WebcastChatMessage"）
        payload: 消息体原始字节（需进一步反序列化为具体类型）
        msg_id: 消息 ID（字符串类型，用于去重）
    """
    method: str
    payload: bytes
    msg_id: str


class ProtobufDecoder:
    """Protobuf + Gzip 解码器

    解码 WebSocket 二进制帧为结构化消息列表。
    每个异常场景都容错处理：解码失败返回空列表，不崩溃。
    """

    def decode(self, raw_bytes: bytes) -> List[DecodedMessage]:
        """解码 WebSocket 二进制帧

        Args:
            raw_bytes: WebSocket 数据帧的原始字节

        Returns:
            解码后的消息列表，解码失败返回空列表
        """
        if not raw_bytes:
            return []

        # Step 1: 反序列化 WssResponse
        wss_response = WssResponse()
        try:
            wss_response.ParseFromString(raw_bytes)
        except Exception as e:
            logger.error(f"Protobuf WssResponse decode failed: {e}")
            return []

        # Step 2: 检查压缩类型
        compress_type = wss_response.headers.get("compress_type", "")
        payload = wss_response.payload

        if compress_type == "gzip":
            try:
                payload = gzip.decompress(payload)
            except Exception as e:
                logger.warning(f"Gzip decompress failed: {e}")
                return []

        # Step 3: 反序列化 Response
        response = Response()
        try:
            response.ParseFromString(payload)
        except Exception as e:
            logger.error(f"Protobuf Response decode failed: {e}")
            return []

        # Step 4: 遍历消息
        results = []
        for msg in response.messages:
            results.append(DecodedMessage(
                method=msg.method,
                payload=msg.payload,
                msg_id=str(msg.msgId),
            ))
        return results
