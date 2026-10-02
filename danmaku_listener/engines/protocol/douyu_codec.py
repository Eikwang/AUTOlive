"""斗鱼直播弹幕协议编解码器（纯函数层，离线可测）

协议要点（以抓包实测为准，fixtures 交叉验证）：
- WS: wss://danmuproxy.douyu.com:9501（8503/8506 备用）
- 包头（小端）：消息体长度(4B, = body+8) + 长度重复(4B) + 类型(2B,
  客户端→服务器 689/服务器→客户端 690) + 1B 加密(0) + 1B 保留(0)
- 消息体：UTF-8 ``key@=value/``（``@`` 赋值、``/`` 分隔），尾部 ``\0``
- 转义：value 内 ``/`` → ``@S``、``@`` → ``@A``
- 握手：loginreq → loginres → joingroup(gid=-9999) → heartbeat（45s）

映射表见 docs/contract/mapping.md。
"""

import struct
from typing import Any, Dict, List, Optional

from danmaku_listener.engines.protocol.douyu_gifts import lookup_gift

HEADER_SIZE = 8  # 头部除长度重复外的有效字段从第 8 字节起算
PACKET_HEADER = struct.Struct("<IIHBB")  # 长度 重复 类型 加密 保留
CLIENT_TYPE = 689
SERVER_TYPE = 690

WS_URL = "wss://danmuproxy.douyu.com:9501"
HEARTBEAT_INTERVAL = 45.0


class DouyuFrameError(ValueError):
    """帧解析失败"""


# ---- 转义 ----

def _escape(value: str) -> str:
    return value.replace("@", "@A").replace("/", "@S")


def _unescape(value: str) -> str:
    return value.replace("@S", "/").replace("@A", "@")


def encode_body(fields: Dict[str, Any]) -> bytes:
    """序列化消息体：key@=value/ 拼接 + 尾部 \\0"""
    parts = "".join(f"{k}@={_escape(str(v))}/" for k, v in fields.items())
    return (parts + "\0").encode("utf-8")


def decode_body(raw: bytes) -> Dict[str, str]:
    """反序列化消息体（尾部 \\0，转义还原）"""
    text = raw.decode("utf-8", errors="replace").rstrip("\0")
    fields: Dict[str, str] = {}
    for chunk in text.split("/"):
        if not chunk or "@=" not in chunk:
            continue
        key, _, value = chunk.partition("@=")
        fields[key] = _unescape(value)
    return fields


# ---- 帧 ----

def encode_packet(body: bytes, client: bool = True) -> bytes:
    msg_type = CLIENT_TYPE if client else SERVER_TYPE
    total = len(body) + 8
    return PACKET_HEADER.pack(total, total, msg_type, 0, 0) + body


def decode_packets(data: bytes) -> List[Dict[str, Any]]:
    """拆包（可能粘包）：返回 [{fields, "_packet_type"}]

    帧布局：[4B msg_len][4B repeat][2B type][1B enc][1B res][body]，
    ``msg_len = 8 + len(body)``（帧总长 = 4 + msg_len）；``_packet_type`` 标注方向。

    Raises:
        DouyuFrameError: 长度字段异常
    """
    packets: List[Dict[str, Any]] = []
    offset = 0
    total_len = len(data)
    while offset + 12 <= total_len:
        msg_len, _repeat, msg_type, _crypt, _reserved = PACKET_HEADER.unpack_from(data, offset)
        body_len = msg_len - 8
        if body_len < 0 or offset + 4 + msg_len > total_len:
            raise DouyuFrameError(f"msg_len={msg_len} 超出缓冲（offset={offset} total={total_len}）")
        body = data[offset + 12 : offset + 4 + msg_len]
        fields = decode_body(body)
        fields["_packet_type"] = msg_type
        packets.append(fields)
        offset += 4 + msg_len
    return packets


def build_login(room_id: int, username: str = "visitor_12345", dfl: str = "sn@AA=105@ASusername@AA=visitor_12345/") -> bytes:
    """登录包（loginreq）"""
    return encode_packet(encode_body({
        "type": "loginreq",
        "roomid": room_id,
        "dfl": dfl.rstrip("/") + "/",
        "username": username,
        "ct": 2,
        "sig": "0000000000000000000000000000000",
    }))


def build_join_group(room_id: int, gid: int = -9999) -> bytes:
    """加入房间弹幕分组（joingroup）"""
    return encode_packet(encode_body({"type": "joingroup", "rid": room_id, "gid": gid}))


def build_heartbeat() -> bytes:
    """平台连接心跳（45s）"""
    return encode_packet(encode_body({"type": "heartbeat"}))


# ---- 上游消息 → 契约 v1 ----

def map_upstream(fields: Dict[str, Any], seq: int, ts: int) -> Optional[Dict[str, Any]]:
    """按 type 字段映射到契约线格式片段；未知类型返回 None"""
    msg_type = fields.get("type")
    if msg_type == "chatmsg":
        return {
            "category": "business", "type": "DANMU",
            "payload": {
                "type": "DANMU",
                "user_name": fields.get("nn", ""),
                "content": fields.get("txt", ""),
                "user_id": fields.get("uid"),
                "badge_name": fields.get("bnn") or None,
                "badge_level": int(fields["bl"]) if fields.get("bl", "").isdigit() else None,
            },
            "seq": seq, "timestamp": ts,
        }
    if msg_type == "dgb":
        try:
            count = int(fields.get("gn", 1))
        except ValueError:
            count = 1
        # 礼物名/价格：协议 gs 优先，空则查静态对照表
        # （2026-10-02 用户实测 79 项，鱼翅计价；gfid=礼物 ID）
        gift_name = fields.get("gs", "") or lookup_gift(fields.get("gfid"))[0]
        unit_price = lookup_gift(fields.get("gfid"))[1]
        return {
            "category": "business", "type": "GIFT",
            "payload": {
                "type": "GIFT",
                "user_name": fields.get("nn", ""),
                "gift_name": gift_name or str(fields.get("gfid", "")),
                "gift_count": count,
                "gift_value": unit_price * count,
                "gift_id": fields.get("gfid"),
                "user_id": fields.get("uid"),
            },
            "seq": seq, "timestamp": ts,
        }
    if msg_type == "uenter":
        return {
            "category": "business", "type": "ENTER_ROOM",
            "payload": {"type": "ENTER_ROOM", "user_name": fields.get("nn", ""), "user_id": fields.get("uid")},
            "seq": seq, "timestamp": ts,
        }
    if msg_type == "rss":
        try:
            viewer = int(fields.get("vc", 0)) // 10  # 斗鱼人气值约为在线数的 10 倍口径
        except ValueError:
            viewer = None
        return {
            "category": "business", "type": "ROOM_STATS",
            "payload": {"type": "ROOM_STATS", "viewer_count": viewer},
            "seq": seq, "timestamp": ts,
        }
    return None
