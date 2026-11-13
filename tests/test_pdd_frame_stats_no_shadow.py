# -*- coding: UTF-8 -*-
"""守卫：pdd 多对象帧全量处理（2026-11-13 st 变量遮蔽回归根修）

背景（用户实机 2026-11-13 控制台实证）：诊断批在 _on_ws_frame 顶部复用原名
`st` 作帧统计，与循环体内降级统计块（st = self._degrade.setdefault(...)）变量
遮蔽——多对象帧第二轮迭代 st["decoded"] 打在降级字典上 → KeyError('decoded')
→ 每帧仅第 1 个对象被处理/发出、其余全丢。控制台症状：每秒一条
"frame parse error: 'decoded'" + frame stats raw=36 decoded=18。

守卫（行为级）：构造统计对象在前+弹幕在后的真实双对象帧（复刻丢帧形态），
断言两对象全部映射发出（ROOM_STATS+DANMU）且无异常——旧代码必红。
"""
import asyncio
import json
import struct

import pytest

from danmaku_listener.engines.protocol.pdd import decode_pdd_frame


# ---- protobuf 最小编码器（对齐 pdd._decode_titan/_decode_multicast 读侧） ----

def _pb_bytes_field(field_no: int, content: bytes) -> bytes:
    tag = (field_no << 3) | 2
    out = bytearray([tag])
    n = len(content)
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            break
    return bytes(out) + content


def _pb_varint_field(field_no: int, value: int) -> bytes:
    out = bytearray([(field_no << 3) | 0])
    n = value
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            break
    return bytes(out)


def _build_frame(objs: list) -> bytes:
    """16 字节包头 + TitanPayload(field1 cmd/field10 body/field14 compress=0)
    + MulticastLite(field4 payload=JSON) ——复刻 pdd titan 四层帧形态"""
    payload = json.dumps(objs).encode("utf-8")
    body = _pb_bytes_field(4, payload)          # MulticastLite field4
    titan = (_pb_bytes_field(1, b"live") + _pb_bytes_field(10, body)
             + _pb_varint_field(14, 0))
    header = struct.pack(">hhiii", 1, 2, 3, 0, len(titan))
    return header + titan


def test_decoder_roundtrip_two_objects():
    """编码器-解码器往返：双对象帧解码出 2 个业务对象"""
    frame = _build_frame([
        {"message_type": "live_audience_num", "message_data": {"live_audience_num": 1234}},
        {"message_type": "live_chat",
         "message_data": {"live_chat_list": [{"chat_message": "你好",
                                              "nickname": "测试观众", "uid": 42}]}},
    ])
    objs = decode_pdd_frame(frame)
    assert len(objs) == 2
    assert objs[0]["message_type"] == "live_audience_num"
    assert objs[1]["message_type"] == "live_chat"


def test_multi_object_frame_fully_processed_no_keyerror():
    """多对象帧全量处理：stats 对象在前、danmu 在后——两对象都必须 emit。

    旧代码（st 遮蔽）在第 2 对象 st["decoded"] 处 KeyError → danmu 丢失（红）。
    """
    from danmaku_listener.engines.registry import build_engine

    engine = build_engine("pdd")
    received: list = []
    engine.on_message(lambda wire: received.append(wire) or asyncio.sleep(0))

    room = "478389298"
    frame = _build_frame([
        {"message_type": "live_audience_num", "message_data": {"live_audience_num": 41984}},
        {"message_type": "live_chat",
         "message_data": {"live_chat_list": [{"chat_message": "直播间弹幕测试",
                                              "nickname": "观众A", "uid": 1}]}},
        {"message_type": "live_chat_notice",
         "message_data": {"live_chat_notice_list": [{"live_chat_notice_type": "enter",
                                                     "user_list": [{"nickname": "观众B", "uid": 2}]}]}},
    ])
    asyncio.run(engine._on_ws_frame(room, {"payload": frame}))

    types = sorted(r["type"] for r in received)
    assert "DANMU" in types, "第 2+ 对象被 st 遮蔽 KeyError 丢弃——DANMU 必须到达（旧代码此处红）"
    assert "ROOM_STATS" in types and "ENTER_ROOM" in types
    assert len(received) == 3
    st = engine._frame_stats[room]
    assert st["decoded"] == 3
    assert st["types"] == {"live_audience_num": 1, "live_chat": 1,
                           "live_chat_notice": 1}
