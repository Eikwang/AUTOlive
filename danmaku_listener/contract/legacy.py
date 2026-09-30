"""旧版 DanmakuMessage（normal/gift/system 三类）兼容适配器

迁移指南见 docs/contract/migration.md：
- 旧字段 → 九类契约的映射表
- from_legacy / to_legacy 双向适配，保证现有消费者与 tests/ 不破坏
"""

from typing import Optional

from danmaku_listener.bus.message import DanmakuMessage
from danmaku_listener.contract.models import (
    Category,
    DanmuPayload,
    Envelope,
    GapReason,
    GapPayload,
    GiftPayload,
    SystemType,
    UnifiedMessage,
)


def legacy_to_unified(msg: DanmakuMessage, *, seq: int, engine: str) -> UnifiedMessage:
    """把旧版 DanmakuMessage 转换为 UnifiedMessage

    旧 normal → DANMU；旧 gift → GIFT（gift_info 必须存在）；
    旧 system → SYSTEM_STATUS/GAP（旧 system 语义模糊，统一视为缺口标记，
    reason=network、approx=True，上层可据此忽略细节）。

    Args:
        msg: 旧版消息
        seq: 房间内序号（旧模型无序号，由调用方分配）
        engine: 引擎标识
    """
    platform = msg.platform
    room = msg.room_id
    if msg.message_type == "normal":
        payload: object = DanmuPayload(user_name=msg.user_name, content=msg.content)
        return UnifiedMessage(
            envelope=Envelope(
                category=Category.BUSINESS,
                type="DANMU",
                platform=platform,
                room_id=room,
                seq=seq,
                timestamp=msg.timestamp,
                engine=engine,
            ),
            payload=payload,
        )
    if msg.message_type == "gift":
        info = msg.gift_info
        if info is None:
            raise ValueError("gift message requires gift_info")
        payload = GiftPayload(
            user_name=info.user_name,
            gift_name=info.gift_name,
            gift_count=info.gift_count,
            gift_value=float(info.gift_value) if info.gift_value is not None else None,
        )
        return UnifiedMessage(
            envelope=Envelope(
                category=Category.BUSINESS,
                type="GIFT",
                platform=platform,
                room_id=room,
                seq=seq,
                timestamp=msg.timestamp,
                engine=engine,
            ),
            payload=payload,
        )
    # system → GAP（尽力近似标记）
    return UnifiedMessage(
        envelope=Envelope(
            category=Category.SYSTEM,
            type="GAP",
            platform=platform,
            room_id=room,
            seq=seq,
            timestamp=msg.timestamp,
            engine=engine,
        ),
        payload=GapPayload(
            window_start=msg.timestamp,
            window_end=msg.timestamp,
            reason=GapReason.NETWORK,
            approx=True,
        ),
    )


def unified_to_legacy(msg: UnifiedMessage) -> Optional[DanmakuMessage]:
    """把 UnifiedMessage 尽量转换为旧版 DanmakuMessage

    仅 DANMU/GIFT 可无损转换；其余类型返回 None（旧模型无对应语义）。
    供未迁移的旧消费者平滑过渡。
    """
    env = msg.envelope
    if env.type == "DANMU" and isinstance(msg.payload, DanmuPayload):
        return DanmakuMessage(
            platform=env.platform,
            room_id=env.room_id,
            user_name=msg.payload.user_name,
            content=msg.payload.content,
            timestamp=env.timestamp,
            message_type="normal",
        )
    if env.type == "GIFT" and isinstance(msg.payload, GiftPayload):
        from danmaku_listener.bus.message import GiftInfo

        return DanmakuMessage(
            platform=env.platform,
            room_id=env.room_id,
            user_name=msg.payload.user_name,
            content=msg.payload.gift_name,
            timestamp=env.timestamp,
            message_type="gift",
            gift_info=GiftInfo(
                user_name=msg.payload.user_name,
                gift_name=msg.payload.gift_name,
                gift_count=msg.payload.gift_count,
                gift_value=int(msg.payload.gift_value) if msg.payload.gift_value is not None else None,
            ),
        )
    return None
