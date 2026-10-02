"""B站直播弹幕协议编解码器（纯函数层，离线可测）

协议要点（以抓包实测为准，fixtures 交叉验证）：
- WS: wss://broadcast-msg.chat.bilibili.com/sub
- 16 字节帧头：包长(4B) + 头长(2B,=16) + 协议版本(2B) + 操作码(4B) + 序列(4B)
- 协议版本：0=裸 JSON；1=心跳应答（4B 人气值）；2=zlib 嵌套；3=brotli 嵌套
- 操作码：2=客户端心跳；3=心跳应答；5=服务器消息（业务/认证应答）；7=客户端认证；8=关闭

认证流程：GET getDanmuInfo 获取 token → 发 AUTH(op=7) → 收 op=5 {"code":0}。
映射表见 docs/contract/mapping.md。
"""

import hashlib
import json
import struct

from danmaku_listener.engines.protocol.bili_gifts import lookup_gift
import time
import zlib
from typing import Any, Dict, List, Optional, Tuple

try:
    import brotli  # type: ignore
except ImportError:  # pragma: no cover
    brotli = None

HEADER_SIZE = 16
PROTOCOL_JSON = 0
PROTOCOL_HEARTBEAT = 1
PROTOCOL_ZLIB = 2
PROTOCOL_BROTLI = 3

OP_HEARTBEAT = 2
OP_HEARTBEAT_REPLY = 3
OP_SEND_MSG_REPLY = 5
OP_AUTH = 7
OP_AUTH_REPLY = 8

WS_URL = "wss://broadcast-msg.chat.bilibili.com/sub"
DANMU_INFO_URL = "https://api.live.bilibili.com/xlive/web-room/v1/index/getDanmuInfo?id={room_id}&type=0"

# B站上游 cmd → 契约类型（docs/contract/mapping.md）
HANDLED_CMDS = {
    "DANMU_MSG",
    "SEND_TOP_GIFT",
    "GIFT",
    "COMBO_SEND",
    "INTERACT_WORD",
    "LIKE_MSG",
    "SUPER_CHAT_MESSAGE",
    "PREPARING",
    "LIVE",
    "ONLINE_RANK_COUNT",
    "GUARD_BUY",
}


class BilibiliFrameError(ValueError):
    """帧解析失败（协议格式不符）"""


def encode_packet(op: int, body: bytes = b"", proto: int = 1, seq: int = 1) -> bytes:
    """编码一帧（认证/心跳发送）"""
    total = HEADER_SIZE + len(body)
    return struct.pack("!IHHII", total, HEADER_SIZE, proto, op, seq) + body


def decode_packets(data: bytes) -> List[Tuple[int, int, bytes]]:
    """解码一帧或多帧：返回 [(proto, op, body)]

    Raises:
        BilibiliFrameError: 长度字段异常（畸形帧）
    """
    packets: List[Tuple[int, int, bytes]] = []
    offset = 0
    total_len = len(data)
    while offset + HEADER_SIZE <= total_len:
        (packet_len, header_len, proto, op, _seq) = struct.unpack(
            "!IHHII", data[offset : offset + HEADER_SIZE]
        )
        if packet_len < header_len or offset + packet_len > total_len:
            raise BilibiliFrameError(
                f"packet_len={packet_len} header_len={header_len} 超出缓冲（offset={offset} total={total_len}）"
            )
        body = data[offset + header_len : offset + packet_len]
        packets.append((proto, op, body))
        offset += packet_len
    return packets


def decompress(proto: int, body: bytes) -> List[Tuple[int, bytes]]:
    """解压嵌套包（zlib/brotli）：返回 [(op, body)] 列表

    裸 JSON/心跳分支同样返回 (op, body)，与压缩分支语义一致。
    """
    if proto == PROTOCOL_ZLIB:
        raw = zlib.decompress(body)
    elif proto == PROTOCOL_BROTLI:
        if brotli is None:
            raise BilibiliFrameError("收到 brotli 包但未安装 brotli 库（pip install brotli）")
        raw = brotli.decompress(body)
    elif proto in (PROTOCOL_JSON, PROTOCOL_HEARTBEAT):
        return [(OP_SEND_MSG_REPLY, body)]
    else:
        raise BilibiliFrameError(f"未知协议版本 {proto}")
    return [(op, b) for _, op, b in decode_packets(raw)]


def build_auth_body(
    room_id: int,
    token: str,
    uid: int = 0,
    protover: int = 3,
    buvid: str = "",
    queue_uuid: str = "",
) -> bytes:
    """构造认证包 body（字段对齐 barrage-fly SDK UserAuthenticationMsg）

    - buvid：必须字段（2023-08-19 起，缺省被服务端限流/踢线）
    - uid：登录态必须与 token 配对（DedeUserID），恒 0 会触发踢线/限流
    - support_ack/scene/queue_uuid：2025-07-19 起新增字段
    """
    auth: Dict[str, Any] = {
        "uid": uid,
        "roomid": room_id,
        "protover": protover,
        "platform": "web",
        "type": 2,
        "key": token,
        "support_ack": True,
        "scene": "room",
    }
    if buvid:
        auth["buvid"] = buvid
    if queue_uuid:
        auth["queue_uuid"] = queue_uuid
    return json.dumps(auth, separators=(",", ":")).encode("utf-8")


def parse_heartbeat_reply(body: bytes) -> Optional[int]:
    """心跳应答（proto=1）：4 字节人气值"""
    if len(body) >= 4:
        return struct.unpack("!I", body[:4])[0]
    return None


# ============ 上游 JSON → 契约 v1（映射层） ============


def _map_danmu_msg(info: list, seq: int, ts: int) -> Optional[Dict[str, Any]]:
    """DANMU_MSG: info=[meta, content, [uid, name, ...], [badge...], ...]"""
    try:
        user_name = str(info[2][1])
        content = str(info[1])
        uid = str(info[2][0]) if info[2][0] is not None else None
    except (IndexError, TypeError):
        return None
    payload = {"type": "DANMU", "user_name": user_name, "content": content, "user_id": uid}
    # 粉丝团徽章（宽松解析）
    try:
        badge = info[3]
        if isinstance(badge, list) and badge[1]:
            payload["badge_name"] = str(badge[1])
            payload["badge_level"] = int(badge[0]) if badge[0] is not None else None
    except (IndexError, TypeError, ValueError):
        pass
    return {"category": "business", "type": "DANMU", "payload": payload, "seq": seq, "timestamp": ts}


def _map_gift(data: dict, seq: int, ts: int) -> Optional[Dict[str, Any]]:
    try:
        gift_id = data.get("giftId")
        # 礼物名/价格：协议 giftName/price 优先（price/1000=电池），空则查静态
        # 对照表（2026-10-02 用户实测 69 项，电池计价）
        gift_name = str(data.get("giftName", "")) or lookup_gift(gift_id)[0]
        price = data.get("price")
        gift_value = float(price) / 1000.0 if price else None
        if gift_value is None and gift_name:
            gift_value = lookup_gift(gift_id)[1] * int(data.get("num", 1))
        payload = {
            "type": "GIFT",
            "user_name": str(data.get("uname", "")),
            "gift_name": gift_name,
            "gift_count": int(data.get("num", 1)),
            "gift_value": gift_value,
            "gift_id": str(gift_id) if gift_id is not None else None,
            "combo_count": int(data["combo_total_num"]) if data.get("combo_total_num") else None,
            "user_id": str(data["uid"]) if data.get("uid") is not None else None,
        }
    except (TypeError, ValueError):
        return None
    return {"category": "business", "type": "GIFT", "payload": payload, "seq": seq, "timestamp": ts}


def _map_enter(data: dict, seq: int, ts: int) -> Optional[Dict[str, Any]]:
    if not data.get("uname"):
        return None
    payload = {
        "type": "ENTER_ROOM",
        "user_name": str(data["uname"]),
        "user_id": str(data["uid"]) if data.get("uid") is not None else None,
    }
    return {"category": "business", "type": "ENTER_ROOM", "payload": payload, "seq": seq, "timestamp": ts}


def _map_like(data: dict, seq: int, ts: int) -> Optional[Dict[str, Any]]:
    try:
        payload = {
            "type": "LIKE",
            "user_name": data.get("uname") or None,
            "count": int(data.get("like_num", 1)),
            "total_likes": int(data["total_like"]) if data.get("total_like") else None,
            "user_id": str(data["uid"]) if data.get("uid") is not None else None,
        }
    except (TypeError, ValueError):
        return None
    return {"category": "business", "type": "LIKE", "payload": payload, "seq": seq, "timestamp": ts}


def _map_super_chat(data: dict, seq: int, ts: int) -> Optional[Dict[str, Any]]:
    try:
        payload = {
            "type": "SUPER_CHAT",
            "user_name": str(data.get("uname", "")),
            "content": str(data.get("message", "")),
            "price": float(data.get("price", 0)),
            "currency": data.get("currency") or "CNY",
            "duration": int(data["time"]) if data.get("time") else None,
            "user_id": str(data["uid"]) if data.get("uid") is not None else None,
        }
    except (TypeError, ValueError):
        return None
    return {"category": "business", "type": "SUPER_CHAT", "payload": payload, "seq": seq, "timestamp": ts}


def _map_social(data: dict, seq: int, ts: int, action: str = "follow") -> Optional[Dict[str, Any]]:
    if not data.get("uname"):
        return None
    payload = {
        "type": "SOCIAL",
        "user_name": str(data["uname"]),
        "action": action,
        "user_id": str(data["uid"]) if data.get("uid") is not None else None,
        "detail": data.get("gift_name"),
    }
    return {"category": "business", "type": "SOCIAL", "payload": payload, "seq": seq, "timestamp": ts}


def map_upstream_message(cmd: str, body: Any, seq: int, ts: int) -> Optional[Dict[str, Any]]:
    """上游消息 → 契约 v1 线格式片段（category/type/payload/seq/timestamp）

    未知 cmd 返回 None（消费者忽略未知类型语义在适配器层保持一致）。
    """
    if cmd == "DANMU_MSG":
        return _map_danmu_msg(body, seq, ts) if isinstance(body, list) else None
    if cmd in ("GIFT", "SEND_TOP_GIFT", "COMBO_SEND"):
        return _map_gift(body, seq, ts) if isinstance(body, dict) else None
    if cmd in ("INTERACT_WORD", "INTERACT_WORD_V2"):
        return _map_enter(body, seq, ts) if isinstance(body, dict) else None
    if cmd in ("LIKE_MSG", "LIKE_CLICK_V3"):
        return _map_like(body, seq, ts) if isinstance(body, dict) else None
    if cmd == "SUPER_CHAT_MESSAGE":
        return _map_super_chat(body, seq, ts) if isinstance(body, dict) else None
    if cmd == "GUARD_BUY":
        return _map_social(body, seq, ts, action="guard_buy") if isinstance(body, dict) else None
    if cmd == "USER_TOAST_MSG":
        return _map_social(body, seq, ts, action="follow") if isinstance(body, dict) else None
    if cmd == "LIVE":
        return {
            "category": "business",
            "type": "LIVE_STATUS_CHANGE",
            "payload": {"type": "LIVE_STATUS_CHANGE", "live": True},
            "seq": seq,
            "timestamp": ts,
        }
    if cmd == "PREPARING":
        return {
            "category": "business",
            "type": "LIVE_STATUS_CHANGE",
            "payload": {"type": "LIVE_STATUS_CHANGE", "live": False},
            "seq": seq,
            "timestamp": ts,
        }
    if cmd == "ONLINE_RANK_COUNT":
        count = body.get("count") if isinstance(body, dict) else None
        if count is None:
            return None
        return {
            "category": "business",
            "type": "ROOM_STATS",
            "payload": {"type": "ROOM_STATS", "viewer_count": int(count)},
            "seq": seq,
            "timestamp": ts,
        }
    return None


def parse_text_message(text: str, seq: int, ts: int) -> List[Dict[str, Any]]:
    """解析一条 op=5 的 JSON 文本：返回契约线格式片段列表"""
    try:
        doc = json.loads(text)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return []
    results: List[Dict[str, Any]] = []
    if isinstance(doc, dict):
        if doc.get("cmd") == "DANMU_MSG" and isinstance(doc.get("info"), list):
            mapped = map_upstream_message("DANMU_MSG", doc["info"], seq, ts)
            if mapped:
                results.append(mapped)
        else:
            mapped = map_upstream_message(str(doc.get("cmd", "")), doc.get("data"), seq, ts)
            if mapped:
                results.append(mapped)
    return results


# ============ wbi 签名（B站 2023+ 风控：getDanmuInfo 必须带 w_rid/wts） ============

#: wbi 混合索引表（B站公开算法）
WBI_MIXIN_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49,
    33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40,
    61, 26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11,
    36, 20, 34, 44, 52,
]

NAV_URL = "https://api.bilibili.com/x/web-interface/nav"


def get_mixin_key(orig_key: str) -> str:
    """wbi 混合：img_key+sub_key 按索引表重排取前 32 字符"""
    return "".join(orig_key[i] for i in WBI_MIXIN_TAB if i < len(orig_key))[:32]


def wbi_sign_params(params: Dict[str, Any], mixin_key: str) -> Dict[str, Any]:
    """对查询参数做 wbi 签名（追加 wts + w_rid）"""
    signed = dict(params)
    signed["wts"] = int(time.time())
    signed = dict(sorted(signed.items()))
    signed = {k: "".join(c for c in str(v) if c not in "!'()*") for k, v in signed.items()}
    query = "&".join(f"{k}={v}" for k, v in signed.items())
    signed["w_rid"] = hashlib.md5((query + mixin_key).encode()).hexdigest()
    return signed


def extract_wbi_keys(nav_response: Dict[str, Any]) -> Tuple[str, str]:
    """从 nav 响应提取 (img_key, sub_key)"""
    wbi = (nav_response.get("data") or {}).get("wbi_img") or {}
    img_url = wbi.get("img_url", "")
    sub_url = wbi.get("sub_url", "")
    img_key = img_url.rsplit("/", 1)[-1].split(".")[0]
    sub_key = sub_url.rsplit("/", 1)[-1].split(".")[0]
    if not img_key or not sub_key:
        raise ValueError(f"nav 响应无 wbi_img keys: code={nav_response.get('code')}")
    return img_key, sub_key


def build_danmu_info_url(room_id: int, mixin_key: str) -> str:
    """构造带 wbi 签名的 getDanmuInfo URL"""
    params = wbi_sign_params({"id": room_id, "type": 0}, mixin_key)
    query = "&".join(f"{k}={v}" for k, v in params.items())
    return f"{DANMU_INFO_URL}?{query}"
