"""统一消息契约 v1

九类消息（八类业务 + SYSTEM_STATUS）× 六平台的标准化格式。
本模块是契约的单源（single source of truth）：pydantic 模型经
`scripts/export_contract.py` 导出 JSON Schema 供 AUTOlive 侧与
fixtures 回归测试使用。

设计要点（对应评审计划）：
- 信封（Envelope）承载 contract_version / category / 路由与去重字段
- 失败类系统消息强制三段式：reason_code + fix_hint + docs_anchor（非空校验）
- 契约 v1 内 additive-only：消费者必须忽略未知字段/未知类型
- 向后兼容：旧 DanmakuMessage（normal/gift/system 三类）经
  `legacy.py` 适配器无损转换
"""

from danmaku_listener.contract.models import (
    CONTRACT_VERSION,
    BackpressurePayload,
    BusinessType,
    Category,
    EngineStatusPayload,
    Envelope,
    EnterRoomPayload,
    FailureInfo,
    GapPayload,
    GapReason,
    GiftPayload,
    HeartbeatPayload,
    InteractiveLogin,
    LikePayload,
    LiveStatusChangePayload,
    NeedsLoginPayload,
    RecoveredPayload,
    RoomStatsPayload,
    RoomStatusPayload,
    RouteFailedPayload,
    SocialPayload,
    SuperChatPayload,
    SystemType,
    UnifiedMessage,
)

__all__ = [
    "CONTRACT_VERSION",
    "Category",
    "BusinessType",
    "SystemType",
    "UnifiedMessage",
    "Envelope",
    "FailureInfo",
    "GapReason",
    "InteractiveLogin",
    "DanmuPayload",
    "GiftPayload",
    "SuperChatPayload",
    "EnterRoomPayload",
    "LikePayload",
    "LiveStatusChangePayload",
    "RoomStatsPayload",
    "SocialPayload",
    "HeartbeatPayload",
    "RoomStatusPayload",
    "GapPayload",
    "NeedsLoginPayload",
    "EngineStatusPayload",
    "BackpressurePayload",
    "RouteFailedPayload",
    "RecoveredPayload",
]
