"""DanmuCommandPipeline——单一命令管线（AutoDanmu R32；ws/REST 双入口收敛于此）

流程（每个发送命令唯一路径，F1）：
  解析校验 → 幂等（R18/F9）→ 熔断/开关 → 监听房间（F2）→ 守卫链（限速/去重/长度/关键词/
  清洗）→ 审计 intent（fail-closed R25）→ 分发 sender → 结果审计 → 回执广播（尽力推送 R11）

回执语义：status=dry_run 非成功语义；unknown 不计熔断（R36）；failed 带三段式。
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Optional

from loguru import logger
from pydantic import ValidationError

from danmaku_listener.contract.models import (Category,
                                              DanmuSendRequestPayload, DanmuSendResultPayload,
                                              Envelope, SendRejectReason, SendStatus, UnifiedMessage)
from danmaku_listener.senders.audit import SendAuditLog
from danmaku_listener.senders.base import SendResult
from danmaku_listener.senders.guard import SendGuard
from danmaku_listener.senders.registry import SenderRegistry


class DanmuCommandPipeline:
    """发送命令管线（进程级单例——web bridge 与 push 通道共用，F1）"""

    def __init__(self, registry: SenderRegistry, guard: SendGuard, audit: SendAuditLog,
                 broadcaster: Optional[Any] = None, auth_configured: bool = False,
                 source_tag: str = "pipeline"):
        self._registry = registry
        self._guard = guard
        self._audit = audit
        self._broadcaster = broadcaster        # async broadcast(wire: dict) —— DANMU_SEND_RESULT 出口
        self._auth_configured = auth_configured  # F6：token 未配置=拒绝服务
        self._source_tag = source_tag
        self._seq = 0

    def set_broadcaster(self, broadcaster: Any) -> None:
        self._broadcaster = broadcaster

    # ---- 主入口 ----

    async def handle_wire(self, data: dict, source: str = "ws") -> dict:
        """处理一条线格式命令；返回回执线格式（ws 用），REST 亦可复用"""
        try:
            msg = UnifiedMessage.from_wire(data)
        except (ValidationError, KeyError, ValueError) as e:
            return self._reject_wire("?", SendRejectReason.PLATFORM_REJECTED.value,
                                     f"命令解析失败: {str(e)[:100]}", source)
        if not isinstance(msg.payload, DanmuSendRequestPayload):
            return self._reject_wire(msg.envelope.room_id, SendRejectReason.PLATFORM_REJECTED.value,
                                     "非 DANMU_SEND_REQUEST 命令", source)
        return await self.handle_request(
            platform=msg.envelope.platform, room_id=msg.envelope.room_id,
            request_id=msg.payload.request_id, content=msg.payload.content, source=source)

    async def handle_request(self, platform: str, room_id: str, request_id: str,
                             content: str, source: str = "rest") -> dict:
        """处理一次发送请求（管线唯一业务路径）"""
        # F6：token 未配置=拒绝服务
        if not self._auth_configured:
            return await self._finish(platform, room_id, request_id, content,
                                       SendStatus.FAILED, SendRejectReason.AUTH_UNCONFIGURED.value,
                                       fix_hint="配置 ws_token_file 或 DANMAKU_TOKEN 后重启", source=source)

        # 幂等（R18/F9）：重复 request_id → 回执上次结果（非错误语义）
        # （对抗评审 F2：重放透传 seen 的 detail/fix_hint——不得覆盖降级索引里的对账上下文）
        seen = self._audit.lookup(request_id)
        if seen is not None:
            return await self._finish(
                platform, room_id, request_id, content,
                SendStatus(seen.get("status", "failed")) if seen.get("status") in ("sent", "dry_run", "failed", "unknown") else SendStatus.FAILED,
                reason_code=seen.get("reason_code") or SendRejectReason.IDEMPOTENT_REPLAY.value,
                sent_at=seen.get("sent_at"),
                fix_hint=seen.get("fix_hint") or "重复 request_id——回执上次结果（幂等）", source=source,
                detail=seen.get("detail"),
                replay=True)

        # 熔断（F3）与开关（P2 状态矩阵）
        if self._guard.is_tripped(platform):
            return await self._finish(platform, room_id, request_id, content, SendStatus.FAILED,
                                       SendRejectReason.CIRCUIT_OPEN.value,
                                       fix_hint="熔断态——人工重开开关后恢复", source=source)
        if not self._guard.is_enabled(platform):
            return await self._finish(platform, room_id, request_id, content, SendStatus.FAILED,
                                       SendRejectReason.SENDER_DISABLED.value,
                                       fix_hint="在 [send] send_enabled_platforms 中启用该平台", source=source)

        # 监听房间判定（F2：快照拒绝）
        if not self._registry.is_room_listened(platform, room_id):
            return await self._finish(platform, room_id, request_id, content, SendStatus.FAILED,
                                       SendRejectReason.ROOM_NOT_LISTENED.value,
                                       fix_hint="发送目标限定当前监听中的房间", source=source)

        # 守卫链（DX-F1：拒绝不排队）
        ok, reason, sanitized = self._guard.check(platform, room_id, content)
        if not ok:
            # hint 按原因细分（CEO-8）：DUPLICATE 是上次尝试的影子——指引对账而非盲目重试
            # （2026-10-08 实证：发送挂起后重试同内容必得 DUPLICATE）
            hints = {
                SendRejectReason.DUPLICATE.value: "同内容在去重窗口内（默认 300s）——上次尝试可能已送达："
                                                   "查看弹幕流回环确认，或按 request_id 检索 "
                                                   "persistence_data/send_audit.jsonl 对账后再决定重试",
            }
            return await self._finish(platform, room_id, request_id, content, SendStatus.FAILED,
                                       reason or SendRejectReason.PLATFORM_REJECTED.value,
                                       fix_hint=hints.get(reason, "守卫命中——AUTOlive 退避重试"), source=source)

        # 审计 intent（R25 fail-closed：实发路径审计失败=拒绝发送）
        dry_run = self._guard._settings.send_dry_run
        intent_ok = self._audit.append({
            "kind": "intent", "request_id": request_id, "platform": platform,
            "room_id": room_id, "content": sanitized, "source": source,
            "dry_run": dry_run,
        })
        if not intent_ok:
            return await self._finish(platform, room_id, request_id, content, SendStatus.FAILED,
                                       SendRejectReason.AUDIT_UNAVAILABLE.value,
                                       fix_hint="审计写入失败——实发被拒（fail-closed R25）；dry-run 路径可继续", source=source,
                                       dry_run=dry_run)

        # dry-run：记录不实发（回执 status=dry_run 非成功语义——DX-F1/R11）
        if dry_run:
            self._guard.record_attempt(platform, room_id, sanitized)
            return await self._finish(platform, room_id, request_id, content, SendStatus.DRY_RUN,
                                       source=source, dry_run=True, detail="dry-run：已记录未实发")

        # 分发 sender（R32：唯一分发点）
        sender = self._registry.get(platform)
        if sender is None:
            self._guard.record_result(platform, "failed", "未注册 sender")
            return await self._finish(platform, room_id, request_id, content, SendStatus.FAILED,
                                       SendRejectReason.SENDER_UNAVAILABLE.value,
                                       fix_hint=f"平台 {platform} 未注册发送器（M2/M3 探针定路线）", source=source)
        self._guard.record_attempt(platform, room_id, sanitized)
        try:
            # 管线级兜底超时（评审 ASK-A）：sender 内部应自有隔离（E5 catch/预算），
            # 此层兜住未来任何 sender 的新增挂点——挂起同异常一样不得穿透为 500/锁泄漏。
            # 70s > taobao 四阶段 55s+锁 3s+launch 收尾裕量，< 双倍前端预算。
            result = await asyncio.wait_for(sender.send(room_id, sanitized), timeout=70.0)
        except asyncio.TimeoutError:
            logger.warning(f"[send-pipeline] {platform}:{room_id} sender 兜底超时(70s)——挂起已被边界捕获")
            result = SendResult(SendStatus.UNKNOWN, SendRejectReason.SEND_TIMEOUT.value,
                                detail="管线兜底超时(70s)：sender 挂起（可能未返回结果）")
        except Exception as e:  # noqa: BLE001  发送围栏（CEO-3，R32 唯一分发点）：
            # sender 异常→FAILED 回执+审计 result 行，杜绝 500 与静默丢回执。
            # 语义裁定（评审 redteam#3，2026-10-08）：未捕获异常=真实故障计熔断（FAILED），
            # unknown 语义保留给 sender 内部自分类的「结果未知」（如 mtop 无响应）
            logger.warning(f"[send-pipeline] {platform}:{room_id} sender 异常: "
                           f"{type(e).__name__}: {str(e)[:120]}")
            result = SendResult(SendStatus.FAILED, SendRejectReason.SEND_TIMEOUT.value,
                                detail=f"sender 异常（围栏兜底，计熔断）: {type(e).__name__}: {str(e)[:100]}")

        # 熔断记账（R36：unknown 不计失败计告警）
        self._guard.record_result(platform, result.status.value, result.detail or "")

        return await self._finish(
            platform, room_id, request_id, content, result.status,
            reason_code=result.reason_code, fix_hint=result.fix_hint,
            docs_anchor=result.docs_anchor, sent_at=result.sent_at,
            source=source, detail=result.detail)

    # ---- 内部 ----

    def _next_seq(self, platform: str, room_id: str) -> int:
        self._seq += 1
        return self._seq

    async def _finish(self, platform: str, room_id: str, request_id: str, content: str,
                      status: SendStatus, reason_code: Optional[str] = None,
                      fix_hint: Optional[str] = None, docs_anchor: Optional[str] = None,
                      sent_at: Optional[int] = None, source: str = "rest",
                      dry_run: bool = False, replay: bool = False,
                      detail: Optional[str] = None) -> dict:
        """结果审计 + 回执构造 + 尽力推送（R11）"""
        if reason_code == SendRejectReason.AUDIT_UNAVAILABLE.value and status == SendStatus.FAILED:
            pass  # R25 fail-closed：intent 未落盘，结果行也记不进去——仅日志（上文已告警）
        else:
            self._audit.append({
                "kind": "result", "request_id": request_id, "platform": platform,
                "room_id": room_id, "status": status.value, "reason_code": reason_code,
                "sent_at": sent_at, "source": source, "dry_run": dry_run or status == SendStatus.DRY_RUN,
                # 评审 redteam#5：detail/fix_hint 入审计行→幂等索引→对账回执可 triage
                "detail": (detail or "")[:160], "fix_hint": (fix_hint or "")[:160],
            })
        wire = self._build_result_wire(platform, room_id, request_id, status,
                                       reason_code, fix_hint, docs_anchor, sent_at, content)
        await self._broadcast(wire)
        if detail:
            logger.info(f"[send-pipeline] {platform}:{room_id} {status.value} {reason_code or ''} {detail[:200]}")
        return wire

    def _build_result_wire(self, platform: str, room_id: str, request_id: str,
                           status: SendStatus, reason_code: Optional[str],
                           fix_hint: Optional[str], docs_anchor: Optional[str],
                           sent_at: Optional[int], content: str) -> dict:
        payload = DanmuSendResultPayload(
            request_id=request_id, status=status, reason_code=reason_code,
            fix_hint=fix_hint, docs_anchor=docs_anchor, sent_at=sent_at, content=content)
        msg = UnifiedMessage(
            envelope=Envelope(
                category=Category.COMMAND, type="DANMU_SEND_RESULT",
                platform=platform, room_id=room_id, seq=self._next_seq(platform, room_id),
                timestamp=int(time.time()), engine="send:pipeline"),
            payload=payload)
        return msg.to_wire()

    def _reject_wire(self, room_id: str, reason: str, detail: str, source: str) -> dict:
        """解析级拒绝（未到业务路径——不进审计/幂等）"""
        logger.warning(f"[send-pipeline] reject ({source}): {detail}")
        return self._build_result_wire("unknown", room_id, "-", SendStatus.FAILED,
                                       reason, detail, None, None, "")

    async def _broadcast(self, wire: dict) -> None:
        """DANMU_SEND_RESULT 尽力推送（广播语义，无 ack——R11）"""
        if self._broadcaster is None:
            return
        try:
            await self._broadcaster(wire)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[send-pipeline] result broadcast failed: {e}")

    def send_status_snapshot(self) -> dict:
        """前端状态行数据源（S8-1/T7）"""
        return self._guard.snapshot()
