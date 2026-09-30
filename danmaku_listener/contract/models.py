"""契约 v1 数据模型

统一信封 + 九类消息载荷。所有平台适配器必须把平台原始消息转换为此格式。
字段语义详见 docs/contract/schema.md；平台上游类型对照见 docs/contract/mapping.md。
"""

from enum import Enum
from typing import Annotated, Literal, Optional, Union

from pydantic import BaseModel, Field, field_validator, model_validator

CONTRACT_VERSION = "1.0.0"


class Category(str, Enum):
    """消息大类：业务消息 vs 系统状态消息"""

    BUSINESS = "business"
    SYSTEM = "system"


class BusinessType(str, Enum):
    """八类业务消息（对照 barrage-fly 统一协议；DANMU 为有意裁剪，对照 B 站上游 DANMU_MSG）"""

    DANMU = "DANMU"
    GIFT = "GIFT"
    SUPER_CHAT = "SUPER_CHAT"
    ENTER_ROOM = "ENTER_ROOM"
    LIKE = "LIKE"
    LIVE_STATUS_CHANGE = "LIVE_STATUS_CHANGE"
    ROOM_STATS = "ROOM_STATS"
    SOCIAL = "SOCIAL"


class GapReason(str, Enum):
    """GAP 缺口原因分类（调试三项之一）"""

    NETWORK = "network"                  # 网络断连
    PROTOCOL = "protocol"                # 协议解析失败
    RISK_CONTROL = "risk_control"        # 平台风控干预
    BACKPRESSURE = "backpressure"        # 背压丢弃
    CRASH_RECOVERY = "crash_recovery"    # 进程崩溃/重启恢复


class SystemType(str, Enum):
    """SYSTEM_STATUS 消息的事件子类型（跨路线通用行为）"""

    HEARTBEAT = "HEARTBEAT"            # 引擎存活心跳（默认 10s/条）
    ROOM_STATUS = "ROOM_STATUS"        # 房间在线状态（开播/下播）
    GAP = "GAP"                        # 断线/停机/丢弃缺口标记（必达）
    NEEDS_LOGIN = "NEEDS_LOGIN"        # 登录态缺失/过期（跨路线通用）
    ENGINE_STATUS = "ENGINE_STATUS"    # 活跃引擎标识/引擎状态变更
    BACKPRESSURE = "BACKPRESSURE"      # 背压丢弃计数（窗口级）
    ROUTE_FAILED = "ROUTE_FAILED"      # 路线失效/降级告警
    RECOVERED = "RECOVERED"            # 慢速重试后恢复


class FailureInfo(BaseModel):
    """失败三段式（失败类消息强制字段，非空校验）

    Attributes:
        reason_code: 机器可读原因码，形如 ``douyin.companion.version_mismatch``
        fix_hint: 人类可读修复指引
        docs_anchor: 文档锚点，指向平台风控手册或运维手册，如
            ``docs/platforms/douyin/runbook.md#version-mismatch``
    """

    reason_code: str = Field(min_length=1)
    fix_hint: str = Field(min_length=1)
    docs_anchor: str = Field(min_length=1)

    @field_validator("reason_code", "fix_hint", "docs_anchor")
    @classmethod
    def _reject_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("failure fields must not be blank")
        return v


class InteractiveLogin(BaseModel):
    """登录交互载荷（NEEDS_LOGIN 的可选增强，视频号扫码等场景）

    引擎进程无法展示二维码，由 AUTOlive 界面呈现并把用户操作结果回传。
    """

    qr_image_b64: Optional[str] = None
    login_url: Optional[str] = None
    expires_at: Optional[int] = None

    @model_validator(mode="after")
    def _require_one(self) -> "InteractiveLogin":
        if not self.qr_image_b64 and not self.login_url:
            raise ValueError("interactive_login requires qr_image_b64 or login_url")
        return self


# ============ 业务消息载荷（八类） ============


class DanmuPayload(BaseModel):
    """DANMU：普通弹幕"""

    type: Literal["DANMU"] = "DANMU"
    user_name: str
    content: str
    user_id: Optional[str] = None
    badge_name: Optional[str] = None      # 粉丝团/徽章名
    badge_level: Optional[int] = None
    is_admin: bool = False
    is_anchor: bool = False


class GiftPayload(BaseModel):
    """GIFT：礼物"""

    type: Literal["GIFT"] = "GIFT"
    user_name: str
    gift_name: str
    gift_count: int = 1
    gift_value: Optional[float] = None    # 平台计价单位
    gift_id: Optional[str] = None
    combo_count: Optional[int] = None     # 连击总数
    user_id: Optional[str] = None


class SuperChatPayload(BaseModel):
    """SUPER_CHAT：醒目留言"""

    type: Literal["SUPER_CHAT"] = "SUPER_CHAT"
    user_name: str
    content: str
    price: float                          # 平台计价单位
    currency: Optional[str] = None
    duration: Optional[int] = None        # 置顶时长（秒）
    user_id: Optional[str] = None


class EnterRoomPayload(BaseModel):
    """ENTER_ROOM：进入直播间"""

    type: Literal["ENTER_ROOM"] = "ENTER_ROOM"
    user_name: str
    user_id: Optional[str] = None
    enter_type: Optional[int] = None      # 进入方式（抖音 Appid/EnterTipType 对照）


class LikePayload(BaseModel):
    """LIKE：点赞（部分平台无法归属到单个用户，user_name 可为空语义由平台适配器文档说明）"""

    type: Literal["LIKE"] = "LIKE"
    user_name: Optional[str] = None
    count: int = 1                        # 本次事件点赞数
    total_likes: Optional[int] = None     # 房间累计点赞（若平台提供）


class LiveStatusChangePayload(BaseModel):
    """LIVE_STATUS_CHANGE：直播状态变更（开播/下播；GAP 裁剪规则的边界信号）"""

    type: Literal["LIVE_STATUS_CHANGE"] = "LIVE_STATUS_CHANGE"
    live: bool                            # True=开播 False=下播
    title: Optional[str] = None


class RoomStatsPayload(BaseModel):
    """ROOM_STATS：房间统计"""

    type: Literal["ROOM_STATS"] = "ROOM_STATS"
    viewer_count: Optional[int] = None
    total_likes: Optional[int] = None
    follower_count: Optional[int] = None
    gift_count_total: Optional[int] = None


class SocialPayload(BaseModel):
    """SOCIAL：社交行为（关注/粉丝团/分享等）"""

    type: Literal["SOCIAL"] = "SOCIAL"
    user_name: str
    action: str                           # follow / fan_club_join / share / ...
    user_id: Optional[str] = None
    detail: Optional[str] = None


# ============ 系统消息载荷（SYSTEM_STATUS 子类型） ============


class HeartbeatPayload(BaseModel):
    """HEARTBEAT：引擎存活心跳（默认 10s/条，AUTOlive 侧 3 周期失联判定）"""

    type: Literal["HEARTBEAT"] = "HEARTBEAT"
    loop_lag_ms: Optional[float] = None   # 事件循环滞后采样（Eng A-2）


class RoomStatusPayload(BaseModel):
    """ROOM_STATUS：房间在线状态（配合 GAP 裁剪：下播期间缺失不计 GAP）"""

    type: Literal["ROOM_STATUS"] = "ROOM_STATUS"
    online: bool


class GapPayload(BaseModel):
    """GAP：消息缺口标记（必达；下播期间缺失不计 GAP，崩溃恢复拆分为近似区间）"""

    type: Literal["GAP"] = "GAP"
    window_start: int                     # 缺口起始（秒级时间戳，尽力近似）
    window_end: int                       # 缺口结束
    reason: GapReason
    approx: bool = False                  # True=边界时间为尽力近似（live 状态校准后仍不确定时）
    dropped_estimate: Optional[int] = None  # 预估丢失条数（可估算时给出）

    @model_validator(mode="after")
    def _validate_window(self) -> "GapPayload":
        if self.window_end < self.window_start:
            raise ValueError("window_end must be >= window_start")
        return self


class NeedsLoginPayload(BaseModel):
    """NEEDS_LOGIN：登录态缺失/过期（跨路线通用；视频号扫码等交互场景带 interactive_login）"""

    type: Literal["NEEDS_LOGIN"] = "NEEDS_LOGIN"
    failure: FailureInfo
    interactive_login: Optional[InteractiveLogin] = None


class EngineStatusPayload(BaseModel):
    """ENGINE_STATUS：活跃引擎标识/引擎状态变更（兜底切换等场景的可见性）"""

    type: Literal["ENGINE_STATUS"] = "ENGINE_STATUS"
    engine: str                           # 引擎标识，如 "protocol:bilibili" / "proxy:douyin" / "fallback:generic"
    detail: Optional[str] = None


class BackpressurePayload(BaseModel):
    """BACKPRESSURE：背压丢弃计数（窗口级，非累计）"""

    type: Literal["BACKPRESSURE"] = "BACKPRESSURE"
    window_start: int
    window_end: int
    dropped_by_type: dict[str, int]       # 按消息类型统计，如 {"DANMU": 12, "LIKE": 3}

    @model_validator(mode="after")
    def _validate_window(self) -> "BackpressurePayload":
        if self.window_end < self.window_start:
            raise ValueError("window_end must be >= window_start")
        return self


class RouteFailedPayload(BaseModel):
    """ROUTE_FAILED：平台路线失效/降级告警"""

    type: Literal["ROUTE_FAILED"] = "ROUTE_FAILED"
    failure: FailureInfo


class RecoveredPayload(BaseModel):
    """RECOVERED：慢速重试后恢复（避免静默恢复）"""

    type: Literal["RECOVERED"] = "RECOVERED"
    after_seconds: int                    # 故障持续时长


# ============ 统一信封与消息 ============


class Envelope(BaseModel):
    """统一消息信封

    Attributes:
        contract_version: 契约版本（additive-only 演进，消费者忽略未知字段）
        category: business / system 大类（AUTOlive 按此分流，不做字符串前缀判断）
        type: 消息类型（BusinessType 或 SystemType 值）
        platform: 平台标识（bilibili/douyu/huya/kuaishou/douyin/wechat_channels）
        room_id: 房间 ID（平台原生 ID 字符串）
        seq: 房间内单调递增序号（顺序保证与缺口检测的依据）
        timestamp: 平台消息时间戳（秒级；无平台时间戳时为引擎接收时刻，测量口径见契约文档）
        engine: 活跃引擎标识
        msg_id: 平台原生消息 ID（跨路线语义去重键；平台提供时必填）
    """

    contract_version: str = CONTRACT_VERSION
    category: Category
    type: str
    platform: str
    room_id: str
    seq: int
    timestamp: int
    engine: str
    msg_id: Optional[str] = None
    protocol_version: Optional[str] = None  # 平台协议版本元数据（调试三项：协议变更定位）

    @field_validator("seq")
    @classmethod
    def _validate_seq(cls, v: int) -> int:
        if v < 0:
            raise ValueError("seq must be non-negative")
        return v


#: 判别联合：按 type 字段路由到具体载荷（消费者忽略未知类型并计数上报）
Payload = Annotated[
    Union[
        # business
        DanmuPayload,
        GiftPayload,
        SuperChatPayload,
        EnterRoomPayload,
        LikePayload,
        LiveStatusChangePayload,
        RoomStatsPayload,
        SocialPayload,
        # system
        HeartbeatPayload,
        RoomStatusPayload,
        GapPayload,
        NeedsLoginPayload,
        EngineStatusPayload,
        BackpressurePayload,
        RouteFailedPayload,
        RecoveredPayload,
    ],
    Field(discriminator="type"),
]

_BUSINESS_TYPES = {t.value for t in BusinessType}
_SYSTEM_TYPES = {t.value for t in SystemType}


class UnifiedMessage(BaseModel):
    """统一消息 = 信封 + 判别载荷

    构造校验 category 与 type 的一致性；系统消息携带 FailureInfo 时强制三段式非空。
    """

    envelope: Envelope
    payload: Payload

    @model_validator(mode="after")
    def _validate_consistency(self) -> "UnifiedMessage":
        et = self.envelope.type
        if self.envelope.category == Category.BUSINESS and et not in _BUSINESS_TYPES:
            raise ValueError(f"category=business requires business type, got {et!r}")
        if self.envelope.category == Category.SYSTEM and et not in _SYSTEM_TYPES:
            raise ValueError(f"category=system requires system type, got {et!r}")
        if et != self.payload.type:
            raise ValueError(f"envelope.type {et!r} != payload.type {self.payload.type!r}")
        if self.envelope.category == Category.SYSTEM and et in (
            SystemType.NEEDS_LOGIN.value,
            SystemType.ROUTE_FAILED.value,
        ):
            failure = getattr(self.payload, "failure", None)
            if failure is not None:
                # FailureInfo 字段已有 min_length=1 校验，这里做整体存在性兜底
                failure.model_validate(failure.model_dump())
        return self

    @property
    def is_business(self) -> bool:
        return self.envelope.category == Category.BUSINESS

    @property
    def is_system(self) -> bool:
        return self.envelope.category == Category.SYSTEM

    def to_wire(self) -> dict:
        """序列化为线格式（WS 推送 JSON 结构）"""
        return {
            "contract_version": self.envelope.contract_version,
            "category": self.envelope.category.value,
            "type": self.envelope.type,
            "platform": self.envelope.platform,
            "room_id": self.envelope.room_id,
            "seq": self.envelope.seq,
            "timestamp": self.envelope.timestamp,
            "engine": self.envelope.engine,
            "msg_id": self.envelope.msg_id,
            "payload": self.payload.model_dump(exclude={"type"}),
        }

    @classmethod
    def from_wire(cls, data: dict) -> "UnifiedMessage":
        """从线格式反序列化（AUTOlive 侧同构）"""
        payload = dict(data.get("payload") or {})
        payload["type"] = data.get("type")
        return cls(
            envelope=Envelope(
                contract_version=data.get("contract_version", CONTRACT_VERSION),
                category=Category(data.get("category")),
                type=data["type"],
                platform=data["platform"],
                room_id=data["room_id"],
                seq=data["seq"],
                timestamp=data["timestamp"],
                engine=data["engine"],
                msg_id=data.get("msg_id"),
            ),
            payload=payload,
        )
