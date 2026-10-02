"""快手直播弹幕协议编解码器（纯函数层，基于本地 ks.proto / ks_pb2）

协议要点（以抓包实测为准，fixtures 交叉验证）：
- WS: wss://payload.wss.kuaishou.com（需游客 token，获取方式为重点验证项——见合规清单）
- SocketMessage{PayloadType, CompressionType(NONE=1/GZIP=2/AES=3), payload}
- 心跳：CS_HEARTBEAT(1) → SC_HEARTBEAT_ACK(101)；进入房间：CS_ENTER_ROOM(200) → SC_ENTER_ROOM_ACK(300)
- 推送：SC_FEED_PUSH(310) = SCWebFeedPush{commentFeeds/likeFeeds/giftFeeds/displayWatchingCount/displayLikeCount}

映射表见 docs/contract/mapping.md。
"""

import gzip
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

from danmaku_listener.engines.protocol import ks_pb2
from danmaku_listener.engines.protocol.kuaishou_gifts import lookup_gift

CONTRACT_VERSION = "1.0.0"
PROTOCOL_VERSION = "kuaishou-1"

WS_URL = "wss://payload.wss.kuaishou.com"


def encode_socket_message(payload_type: int, payload: bytes, compression: int = 1) -> bytes:
    """构造 SocketMessage protobuf 帧（compression: 1=NONE 2=GZIP）"""
    sm = ks_pb2.SocketMessage()
    sm.payloadType = payload_type
    sm.compressionType = compression
    sm.payload = payload
    return sm.SerializeToString()


def decode_socket_message(data: bytes) -> Tuple[int, int, bytes]:
    """解析 SocketMessage：返回 (payload_type, compression, payload_bytes)"""
    sm = ks_pb2.SocketMessage()
    sm.ParseFromString(data)
    return sm.payloadType, sm.compressionType, sm.payload


def decompress_payload(compression: int, payload: bytes) -> bytes:
    if compression == 2:  # GZIP
        return gzip.decompress(payload)
    if compression == 3:  # AES——密钥经 enterRoomAck 协商，v1 标注不支持
        raise ValueError("AES 压缩需要会话密钥协商，v1 未实现（见 Open Questions）")
    return payload


def build_heartbeat(ts_ms: Optional[int] = None) -> bytes:
    """CS_HEARTBEAT 帧（2026-09 扁平结构：payload = field1 varint 时间戳，SDK 同款）"""
    ts = ts_ms if ts_ms is not None else int(time.time() * 1000)
    body = bytes([0x08]) + _pb_varint(ts)  # field1 (varint) = timestamp
    return encode_socket_message(ks_pb2.PayloadType.CS_HEARTBEAT, body)


# ---- CSWebEnterRoom 手工扁平编码（2026-09 协议校准）----
# 页面真实帧对照：enter payload 内 field1=token（string 直接开始），
# 旧 proto 的 Payload 嵌套（payload=field3）已废弃——嵌套形式被服务器
# 以 SC_ERROR code=60 拒绝。SocketMessage 外层结构未变（ptype/comp/payload）。

def _pb_varint(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            break
    return bytes(out)


def _pb_field_str(field_no: int, value: str) -> bytes:
    data = value.encode("utf-8")
    return bytes([(field_no << 3) | 2]) + _pb_varint(len(data)) + data


def build_enter_room(token: str, live_stream_id: str, page_id: str = "") -> bytes:
    """CS_ENTER_ROOM 帧（2026-09 扁平结构，页面真实帧对照校准）

    - field1 = token（websocketinfo 接口，游客/登录态均由此获取）
    - field2 = liveStreamId（liveroom XHR / __INITIAL_STATE__，非房间号）
    - field7 = pageId（随机 16 字符 + 毫秒时间戳）
    """
    body = _pb_field_str(1, token) + _pb_field_str(2, str(live_stream_id))
    if page_id:
        body += _pb_field_str(7, page_id)
    return encode_socket_message(ks_pb2.PayloadType.CS_ENTER_ROOM, body)


# ---- SC_FEED_PUSH → 契约 v1 ----

def _user_fields(user) -> Dict[str, Optional[str]]:
    return {
        "user_name": user.userName or None,
        "user_id": user.principalId or None,
    }


def map_feed_push(payload: bytes, seq_start: int, ts: int) -> List[Dict[str, Any]]:
    """SCWebFeedPush → 契约线格式片段列表（comment/like/gift + 房间统计）"""
    push = ks_pb2.SCWebFeedPush()
    push.ParseFromString(payload)
    results: List[Dict[str, Any]] = []
    seq = seq_start

    for comment in push.commentFeeds:
        seq += 1
        fields = _user_fields(comment.user)
        results.append({
            "category": "business", "type": "DANMU",
            "payload": {"type": "DANMU", "content": comment.content, **fields},
            "seq": seq, "timestamp": ts, "msg_id": comment.id or None,
        })

    for like in push.likeFeeds:
        seq += 1
        fields = _user_fields(like.user)
        results.append({
            "category": "business", "type": "LIKE",
            "payload": {"type": "LIKE", "count": 1, **fields},
            "seq": seq, "timestamp": ts, "msg_id": like.id or None,
        })

    for gift in push.giftFeeds:
        seq += 1
        fields = _user_fields(gift.user)
        # 礼物名/价格：静态映射表（2026-10-02 用户实测 92 项）——
        # SCWebFeedPush 推流只含 giftId，名称需查表（此前前端显示 undefined）
        gift_name, gift_price = lookup_gift(gift.giftId)
        results.append({
            "category": "business", "type": "GIFT",
            "payload": {
                "type": "GIFT", "user_name": fields["user_name"] or "",
                "gift_name": gift_name or str(gift.giftId),
                "gift_id": str(gift.giftId), "gift_count": gift.batchSize * max(1, gift.comboCount)
                if gift.comboCount else gift.batchSize,
                "combo_count": gift.comboCount or None,
                "gift_value": gift_price * (gift.batchSize * max(1, gift.comboCount)
                                            if gift.comboCount else gift.batchSize),
                "user_id": fields["user_id"],
            },
            "seq": seq, "timestamp": ts, "msg_id": gift.id or None,
        })

    if push.displayWatchingCount or push.displayLikeCount:
        seq += 1
        results.append({
            "category": "business", "type": "ROOM_STATS",
            "payload": {"type": "ROOM_STATS", "viewer_count": _parse_display(push.displayWatchingCount),
                        "total_likes": _parse_display(push.displayLikeCount)},
            "seq": seq, "timestamp": ts,
        })
    return results


def _parse_display(display: str) -> Optional[int]:
    """展示计数（如 "1.2万"）→ 整数；无法解析返回 None"""
    if not display:
        return None
    try:
        if display.endswith("万"):
            return int(float(display[:-1]) * 10000)
        if display.endswith("亿"):
            return int(float(display[:-1]) * 100000000)
        return int(display)
    except ValueError:
        return None


def map_socket_message(data: bytes, seq_start: int, ts: int) -> List[Dict[str, Any]]:
    """SocketMessage 帧 → 契约片段（SC_FEED_PUSH）；其他类型返回空（心跳 ack 等内部处理）"""
    payload_type, compression, payload = decode_socket_message(data)
    if payload_type == ks_pb2.PayloadType.SC_FEED_PUSH:
        try:
            inner = decompress_payload(compression, payload)
        except ValueError:
            return []
        return map_feed_push(inner, seq_start, ts)
    return []
