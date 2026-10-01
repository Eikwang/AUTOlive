"""虎牙直播弹幕协议编解码器（标准 Tars over WebSocket，2026-09 实测校准）

协议要点（barrage-fly SDK 1.5.8 对照 + 浏览器真实帧逐字节验证）：
- WS: wss://cdnws.api.huya.com:443（SDK 端点；浏览器用 wsapi.huya.com / *.va.huya.com）
- 帧 = 纯 Tars WebSocketCommand 序列化（无帧头）：
  operation(tag0) + vData(tag1, SimpleList) + lRequestId(2) + traceId(3) + ...
- opcode：3=WupReq 4=WupRsp 5/20=心跳 21=心跳应答 7=下推 MsgPushReq
  16=RegisterGroupReq 17=Rsp 33=UpdateUserInfoReq
- WupReq（vData 内容）：4B 大端长度前缀 + Tars：
  version(1)=3, packetType(2), messageType(3), requestId(4), servantName(5),
  functionName(6), uniAttribute(7, SimpleList) —— uniAttribute = map<string, byte[]>
  （tarsjce 1.7+ 单层 map，key="tReq"）
- 下推（op=7）→ WSPushMessage{ePushType=0, lUri=1, dataBytes=2}：
  uri 1400=MessageNotice（弹幕）、6501=SendItemSubBroadcastPacket（礼物）、
  6110=VipEnterBanner（进场横幅）

映射表见 docs/contract/mapping.md。
"""

import struct
import time
from typing import Any, Callable, Dict, List, Optional

from danmaku_listener.engines.protocol.huya_gifts import lookup_gift
from danmaku_listener.engines.protocol.huya_tars import (
    TarsError,
    TarsInputStream,
    TarsOutputStream,
    TarsStruct,
)

PROTOCOL_VERSION = "huya-1"

WS_URL = "wss://cdnws.api.huya.com:443"
HEARTBEAT_INTERVAL = 25.0  # SDK 默认周期（首帧 15s 延迟在引擎侧）

# opcode（HuyaOperationEnum）
OP_WUP_REQ = 3
OP_WUP_RSP = 4
OP_HEARTBEAT_REQ = 20
OP_HEARTBEAT_RSP = 21
OP_MSG_PUSH = 7
OP_MSG_PUSH_V2 = 22
OP_REGISTER_GROUP_REQ = 16
OP_REGISTER_GROUP_RSP = 17
OP_UPDATE_USER_INFO_REQ = 33
OP_UPDATE_USER_INFO_RSP = 34

# 下推 uri（HuyaCmdEnum）
URI_MESSAGE_NOTICE = 1400
URI_VIP_ENTER_BANNER = 6110
URI_SEND_ITEM_SUB_BROADCAST = 6501

VER = "0.1.0"
UA = f"webh5&{VER}&websocket"


class HuyaFrameError(ValueError):
    """帧解析失败"""


# ---- 结构体 ----

class UserId(TarsStruct):
    """UserId（SDK UserId.java）"""

    def __init__(self) -> None:
        self.l_uid = 0
        self.s_guid = ""
        self.s_token = ""
        self.s_huya_ua = UA
        self.s_cookie = ""
        self.i_token_type = 0
        self.s_device_info = "chrome"

    def write_to(self, os: TarsOutputStream) -> None:
        os.write_int(self.l_uid, 0)
        os.write_string(self.s_guid, 1)
        os.write_string(self.s_token, 2)
        os.write_string(self.s_huya_ua, 3)
        os.write_string(self.s_cookie, 4)
        os.write_int(self.i_token_type, 5)
        os.write_string(self.s_device_info, 6)

    def read_from(self, is_: TarsInputStream) -> None:
        self.l_uid = is_.read_int(0, 0)
        self.s_guid = is_.read_string(1, "")


class SenderInfo(TarsStruct):
    """SenderInfo（MessageNotice.tUserInfo；SDK SenderInfo.java）"""

    def __init__(self) -> None:
        self.l_uid = 0
        self.l_imid = 0
        self.s_nick_name = ""
        self.s_avatar_url = ""

    def write_to(self, os: TarsOutputStream) -> None:
        os.write_int(self.l_uid, 0)

    def read_from(self, is_: TarsInputStream) -> None:
        self.l_uid = is_.read_int(0, 0)
        self.l_imid = is_.read_int(1, 0)
        self.s_nick_name = is_.read_string(2, "")
        self.s_avatar_url = is_.read_string(4, "")


class LiveLaunchReq(TarsStruct):
    """LiveLaunchReq（doLaunch 请求；SDK 同名类）"""

    def __init__(self) -> None:
        self.t_id = UserId()
        self.e_source = 3  # HuyaLiveSource.WEB_HUYA
        self.b_support_domain = True

    def write_to(self, os: TarsOutputStream) -> None:
        os.write_struct_begin(0)
        self.t_id.write_to(os)
        os.write_struct_end()
        # tLiveUB(tag1) 仅 eSource(tag1) 字段
        os.write_struct_begin(1)
        os.write_int(self.e_source, 1)
        os.write_struct_end()
        os.write_bool(self.b_support_domain, 2)

    def read_from(self, is_: TarsInputStream) -> None:
        pass


class WSRegisterGroupReq(TarsStruct):
    """registerGroup 请求（SDK 同名类）"""

    def __init__(self, group_ids: Optional[List[str]] = None) -> None:
        self.v_group_ids = group_ids or []

    def write_to(self, os: TarsOutputStream) -> None:
        # list<string>（_write_any 逐元素 string）
        os.write_list(self.v_group_ids, 0)

    def read_from(self, is_: TarsInputStream) -> None:
        pass


class WSUpdateUserInfoReq(TarsStruct):
    """updateUserInfo 请求（SDK 同名类；仅必需字段）"""

    def __init__(self) -> None:
        self.s_app_src = "HUYA&ZH&2052"

    def write_to(self, os: TarsOutputStream) -> None:
        os.write_string(self.s_app_src, 0)

    def read_from(self, is_: TarsInputStream) -> None:
        pass


class UserHeartBeatReq(TarsStruct):
    """心跳请求（SDK 同名类）"""

    def __init__(self) -> None:
        self.t_id = UserId()
        self.l_tid = 0
        self.l_sid = 0
        self.l_pid = 0
        self.b_watch_video = True
        self.e_line_type = -1

    def write_to(self, os: TarsOutputStream) -> None:
        os.write_struct_begin(0)
        self.t_id.write_to(os)
        os.write_struct_end()
        os.write_int(self.l_tid, 1)
        os.write_int(self.l_sid, 2)
        os.write_int(self.l_pid, 4)
        os.write_bool(self.b_watch_video, 5)
        os.write_int(self.e_line_type, 6)

    def read_from(self, is_: TarsInputStream) -> None:
        pass


# ---- 编码 ----

def _uni_attribute(payload: bytes) -> bytes:
    """uniAttribute（tarsjce）：map<string, byte[]> tag0，key="tReq"，value 带 tag1 头

    浏览器真实帧对照：08 00 01 06 04 "tReq" 1d 00 <len> <data>
    （value 头 0x1D = tag1 SimpleList——2026 版语义）
    """
    os = TarsOutputStream()
    os._header(0, 8)  # MAP tag0
    os.write_int(1, 0)  # map size=1
    os.write_string("tReq", 0)  # key
    os._header(1, 13)  # value：tag1 SimpleList
    os._buf.append(0)  # 元素类型 byte
    os.write_int(len(payload), 0)  # 长度
    os._buf.extend(payload)
    return os.to_bytes()


def _wup_encode(servant: str, func: str, req: Any) -> bytes:
    """WupReq：4B 大端长度前缀 + Tars（tags 1-7；BaseWup.encode 同款）

    req 可为 TarsStruct（write_to 序列化）或 TarsOutputStream（已构造内容）。
    """
    if isinstance(req, TarsOutputStream):
        inner = req
    else:
        inner = TarsOutputStream()
        req.write_to(inner)
    wup = TarsOutputStream()
    wup.write_int(3, 1)  # version = VERSION3
    wup.write_int(0, 2)  # packetType
    wup.write_int(0, 3)  # messageType
    wup.write_int(-1, 4)  # requestId
    wup.write_string(servant, 5)
    wup.write_string(func, 6)
    wup.write_bytes(_uni_attribute(inner.to_bytes()), 7)
    body = wup.to_bytes()
    return struct.pack(">I", 4 + len(body)) + body


def build_websocket_command(operation: int, v_data: bytes) -> bytes:
    """WebSocketCommand：纯 Tars（浏览器真实帧同款）"""
    cmd = TarsOutputStream()
    cmd.write_int(operation, 0)
    cmd.write_bytes(v_data, 1)
    return cmd.to_bytes()


def build_do_launch() -> bytes:
    """op=3 WupReq：launch/wsLaunch（2026 浏览器版，黄金样本逐字节对照）

    浏览器帧 54B uni 内容：tId{ lUid=0, sGuid="", sHuYaUA(tag2),
    appSrc="HUYA&ZH&2052"(tag3), 子struct(tag4){""} } + tag8 zero + tag9/10 空 map。
    （SDK 2024 的 liveui/doLaunch + UserId 旧 tag 表在 2026 已不被应答）
    """
    req = TarsOutputStream()
    req.write_struct_begin(0)  # tId
    req.write_int(0, 0)  # lUid=0（游客）
    req.write_string("", 1)  # sGuid
    req.write_string(UA, 2)  # sHuYaUA（2026: tag2）
    req.write_string("HUYA&ZH&2052", 3)  # appSrc
    req.write_struct_begin(4)
    req.write_string("", 0)
    req.write_struct_end()
    req.write_struct_end()  # tId end
    req.write_int(0, 8)  # tag8
    req.write_map({}, 9)  # tag9 空 map
    req.write_map({}, 10)  # tag10 空 map
    return build_websocket_command(OP_WUP_REQ, _wup_encode("launch", "wsLaunch", req))


def build_register_group(tid: int) -> bytes:
    """op=16：注册房间组（live:{tid} + chat:{tid}）"""
    req = WSRegisterGroupReq([f"live:{tid}", f"chat:{tid}"])
    inner = TarsOutputStream()
    req.write_to(inner)
    return build_websocket_command(OP_REGISTER_GROUP_REQ, inner.to_bytes())


def build_update_user_info() -> bytes:
    """op=33：更新用户信息（开启 ack 统计）"""
    req = WSUpdateUserInfoReq()
    inner = TarsOutputStream()
    req.write_to(inner)
    return build_websocket_command(OP_UPDATE_USER_INFO_REQ, inner.to_bytes())


def build_heartbeat(tid: int) -> bytes:
    """op=20 WupReq：onlineui/OnUserHeartBeat"""
    req = UserHeartBeatReq()
    req.l_pid = tid
    return build_websocket_command(OP_HEARTBEAT_REQ, _wup_encode("onlineui", "OnUserHeartBeat", req))


class GetPropsListReq(TarsStruct):
    """礼物列表请求（servant=PropsUIServer/getPropsList；tId 用 2026 浏览器表）"""

    def __init__(self, l_yyid: int = 0) -> None:
        self.l_yyid = l_yyid
        self.i_template_type = 1  # HuyaClientTemplateTypeEnum.TPL_MIRROR

    def write_to(self, os: TarsOutputStream) -> None:
        # tId（tag1 struct；2026 浏览器 UserId 表，同 build_do_launch）
        os.write_struct_begin(1)
        os.write_int(self.l_yyid, 0)
        os.write_string("", 1)
        os.write_string(UA, 2)
        os.write_string("HUYA&ZH&2052", 3)
        os.write_struct_begin(4)
        os.write_string("", 0)
        os.write_struct_end()
        os.write_struct_end()
        os.write_int(self.i_template_type, 3)

    def read_from(self, is_: TarsInputStream) -> None:
        pass


def build_gift_list_req(l_yyid: int = 0) -> bytes:
    """op=3 WupReq：PropsUIServer/getPropsList（连接后立即拉取礼物 ID 表）"""
    return build_websocket_command(
        OP_WUP_REQ,
        _wup_encode("PropsUIServer", "getPropsList", GetPropsListReq(l_yyid)),
    )


# ---- 解码 ----

def decode_command(data: bytes) -> Dict[str, Any]:
    """WebSocketCommand 反序列化 → {operation, v_data}"""
    is_ = TarsInputStream(data)
    return {
        "operation": is_.read_int(0, 0),
        "v_data": is_.read_bytes(1, b""),
    }


def _uni_attribute_decode(raw: bytes) -> Dict[str, bytes]:
    """uniAttribute（map<string, byte[]> tag0）→ dict"""
    is_ = TarsInputStream(raw)
    result: Dict[str, bytes] = {}
    if not is_.skip_to_tag(0):
        return result
    _tag, type_ = is_._read_header()  # 消费 MAP 头
    if type_ != 8:  # MAP
        return result
    n = is_._read_int_no_tag()
    for _ in range(n):
        key = is_._read_any()
        # value：SimpleList byte[]
        _v_tag, v_type = is_._read_header()
        if v_type == 13:  # SIMPLELIST
            is_._pos += 1  # 元素类型
            length = is_._read_int_no_tag()
            result[str(key)] = bytes(is_._buf[is_._pos:is_._pos + length])
            is_._pos += length
        elif v_type == 12:
            result[str(key)] = b""
        else:
            break
    return result


def decode_wup_rsp(v_data: bytes) -> Dict[str, Any]:
    """WupRsp（4B 长度前缀 + Tars）→ {servant, func, uni: {key: bytes}}

    注意按 tag 递增读取（skip_to_tag 只向前）：1→2→4→5→6→7。
    """
    if len(v_data) < 4:
        return {}
    body = v_data[4:]
    is_ = TarsInputStream(body)
    version = is_.read_int(1, 0)
    packet_type = is_.read_int(2, 0)
    request_id = is_.read_int(4, 0)
    servant = is_.read_string(5, "")
    func = is_.read_string(6, "")
    uni_raw = is_.read_bytes(7, b"")
    return {
        "version": version,
        "packet_type": packet_type,
        "request_id": request_id,
        "servant": servant,
        "func": func,
        "uni": _uni_attribute_decode(uni_raw) if uni_raw else {},
    }


def decode_push_message(v_data: bytes) -> Optional[Dict[str, Any]]:
    """op=7 下推 → WSPushMessage{ePushType, lUri, dataBytes}"""
    is_ = TarsInputStream(v_data)
    e_push_type = is_.read_int(0, 0)
    l_uri = is_.read_int(1, 0)
    data_bytes = is_.read_bytes(2, b"")
    return {"e_push_type": e_push_type, "uri": l_uri, "data": data_bytes}


def decode_push_message_v2(v_data: bytes) -> List[Dict[str, Any]]:
    """op=22 批量下推（MsgPushReq_V2，流量主体）→ [{uri, data}]

    布局（SDK WSPushMessage_V2/WSMsgItem）：vMsgItem(tag1, list<struct>)，
    WSMsgItem{lUri=0, sMsg=1}。
    """
    items: List[Dict[str, Any]] = []
    is_ = TarsInputStream(v_data)
    if not is_.skip_to_tag(1):
        return items
    _tag, type_ = is_._read_header()
    if type_ != 9:  # LIST
        return items
    n = is_._read_int_no_tag()
    for _ in range(n):
        _i_tag, i_type = is_._read_header()
        if i_type != 10:  # STRUCT_BEGIN
            break
        uri = is_.read_int(0, 0)
        msg = is_.read_bytes(1, b"")
        is_.skip_to_struct_end()
        if uri and msg:
            items.append({"uri": uri, "data": msg})
    return items


def decode_message_notice(data: bytes) -> Dict[str, Any]:
    """MessageNotice（uri 1400）→ 弹幕/进场字段

    布局（浏览器真实帧验证）：tag0=tUserInfo(SenderInfo struct)、
    tag1=lTid、tag2=lSid、tag3=sContent。
    """
    is_ = TarsInputStream(data)
    user = SenderInfo()
    if is_.skip_to_tag(0) and is_.enter_struct():
        user.read_from(is_)
        is_.skip_to_struct_end()  # 部分读取后对齐 struct 末尾
    content = is_.read_string(3, "")
    return {
        "user_name": user.s_nick_name,
        "user_id": str(user.l_uid) if user.l_uid else None,
        "content": content,
    }


def decode_send_item(data: bytes) -> Dict[str, Any]:
    """SendItemSubBroadcastPacket（uri 6501）→ 礼物字段"""
    is_ = TarsInputStream(data)
    return {
        "item_type": is_.read_int(0, 0),
        "item_count": is_.read_int(2, 0),
        "presenter_uid": is_.read_int(3, 0),
        "sender_uid": is_.read_int(4, 0),
        "presenter_nick": is_.read_string(5, ""),
        "sender_nick": is_.read_string(6, ""),
        "send_content": is_.read_string(7, ""),
    }


# ---- 上游消息 → 契约 v1 ----

def decode_gift_list(payload: bytes) -> Dict[int, str]:
    """GetPropsListRsp（uni["tRsp"]）→ {iPropsId: sPropsName}

    布局（SDK GetPropsListRsp/PropsItem）：vPropsItemList(tag1, list<struct>)，
    PropsItem{iPropsId=1, sPropsName=2}。
    """
    is_ = TarsInputStream(payload)
    gifts: Dict[int, str] = {}
    if not is_.skip_to_tag(1):
        return gifts
    _tag, type_ = is_._read_header()
    if type_ != 9:  # LIST
        return gifts
    n = is_._read_int_no_tag()
    for _ in range(n):
        _i_tag, i_type = is_._read_header()
        if i_type != 10:  # STRUCT_BEGIN
            break
        props_id = is_.read_int(1, 0)
        props_name = is_.read_string(2, "")
        if props_id:
            gifts[props_id] = props_name
        is_.skip_to_struct_end()
    return gifts


def map_upstream(payload: bytes, uri: int, seq: int, ts: int,
                 gift_items: Optional[Dict[int, str]] = None) -> Optional[Dict[str, Any]]:
    """下推消息映射到契约线格式片段；未知 uri 返回 None

    gift_items：礼物 ID 表（getPropsList 响应）；缺省时礼物名退回类型编号。
    """
    if uri == URI_MESSAGE_NOTICE:
        d = decode_message_notice(payload)
        if d.get("content"):
            return {
                "category": "business",
                "type": "DANMU",
                "seq": seq,
                "timestamp": ts,
                "payload": {
                    "type": "DANMU",
                    "user_name": d.get("user_name") or "",
                    "content": d.get("content", ""),
                    "user_id": d.get("user_id"),
                },
            }
        return None
    if uri == URI_SEND_ITEM_SUB_BROADCAST:
        d = decode_send_item(payload)
        item_type = d.get("item_type") or 0
        # 三级回退：getPropsList 在线礼物表 → 静态对照表（2026-10-01 用户
        # 实测 189 项，docs/虎牙礼物编号名称对照表.md）→ 编号本身
        gift_name = (gift_items or {}).get(item_type)
        gift_value = 0.0
        if not gift_name:
            static_name, static_price = lookup_gift(item_type)
            if static_name:
                gift_name = static_name
                gift_value = static_price
        if not gift_name:
            gift_name = str(item_type)
        return {
            "category": "business",
            "type": "GIFT",
            "seq": seq,
            "timestamp": ts,
            "payload": {
                "type": "GIFT",
                "user_name": d.get("sender_nick") or "",
                "user_id": str(d["sender_uid"]) if d.get("sender_uid") else None,
                "gift_name": gift_name,
                "gift_count": d.get("item_count", 1),
                "gift_value": gift_value,
            },
        }
    if uri == URI_VIP_ENTER_BANNER:
        # VipEnterBanner 结构未移植（进场横幅，可选映射）——v1 暂略
        return None
    return None
