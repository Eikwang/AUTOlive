"""SYSTEM_STATUS 消息管道构造器

统一构造九类契约中的系统状态消息（心跳/GAP/登录态/引擎状态/背压/路线失效/恢复），
保证失败类消息的三段式字段（reason_code/fix_hint/docs_anchor）始终非空。
"""

from typing import Optional

from danmaku_listener.contract.models import (
    Category,
    Envelope,
    FailureInfo,
    GapPayload,
    GapReason,
    HeartbeatPayload,
    NeedsLoginPayload,
    RecoveredPayload,
    RouteFailedPayload,
    SystemType,
    UnifiedMessage,
)


class SystemStatusFactory:
    """系统状态消息工厂

    每个引擎持有其实例（绑定 platform/engine 标识），经统一消息总线推送。
    """

    def __init__(self, platform: str, engine: str):
        self.platform = platform
        self.engine = engine

    def _envelope(self, type_: SystemType, room_id: str, seq: int, ts: int) -> Envelope:
        return Envelope(
            category=Category.SYSTEM,
            type=type_.value,
            platform=self.platform,
            room_id=room_id,
            seq=seq,
            timestamp=ts,
            engine=self.engine,
        )

    def heartbeat(self, room_id: str, seq: int, ts: int, loop_lag_ms: Optional[float] = None) -> UnifiedMessage:
        return UnifiedMessage(
            envelope=self._envelope(SystemType.HEARTBEAT, room_id, seq, ts),
            payload=HeartbeatPayload(loop_lag_ms=loop_lag_ms),
        )

    def gap(
        self,
        room_id: str,
        seq: int,
        ts: int,
        window_start: int,
        window_end: int,
        reason: GapReason,
        approx: bool = False,
        dropped_estimate: Optional[int] = None,
    ) -> UnifiedMessage:
        """缺口标记（必达）：断线/停机/丢弃窗口；跨下播窗口已由调用方裁剪"""
        return UnifiedMessage(
            envelope=self._envelope(SystemType.GAP, room_id, seq, ts),
            payload=GapPayload(
                window_start=window_start,
                window_end=window_end,
                reason=reason,
                approx=approx,
                dropped_estimate=dropped_estimate,
            ),
        )

    def needs_login(
        self,
        room_id: str,
        seq: int,
        ts: int,
        reason_code: str,
        fix_hint: str,
        docs_anchor: str,
        qr_image_b64: Optional[str] = None,
        login_url: Optional[str] = None,
    ) -> UnifiedMessage:
        return UnifiedMessage(
            envelope=self._envelope(SystemType.NEEDS_LOGIN, room_id, seq, ts),
            payload=NeedsLoginPayload(
                failure=FailureInfo(reason_code=reason_code, fix_hint=fix_hint, docs_anchor=docs_anchor),
                interactive_login=(
                    {"qr_image_b64": qr_image_b64, "login_url": login_url}
                    if (qr_image_b64 or login_url)
                    else None
                ),
            ),
        )

    def route_failed(
        self, room_id: str, seq: int, ts: int, reason_code: str, fix_hint: str, docs_anchor: str
    ) -> UnifiedMessage:
        return UnifiedMessage(
            envelope=self._envelope(SystemType.ROUTE_FAILED, room_id, seq, ts),
            payload=RouteFailedPayload(
                failure=FailureInfo(reason_code=reason_code, fix_hint=fix_hint, docs_anchor=docs_anchor)
            ),
        )

    def recovered(self, room_id: str, seq: int, ts: int, after_seconds: int) -> UnifiedMessage:
        """慢速重试后恢复事件（避免静默恢复，Eng O）"""
        return UnifiedMessage(
            envelope=self._envelope(SystemType.RECOVERED, room_id, seq, ts),
            payload=RecoveredPayload(after_seconds=after_seconds),
        )
