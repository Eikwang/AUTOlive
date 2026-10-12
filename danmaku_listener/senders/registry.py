"""SenderRegistry——平台 sender 注册表 + 监听房间只读判定（AutoDanmu T6/E5/F2）"""
from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from loguru import logger

from danmaku_listener.senders.base import BaseSender, SendResult
from danmaku_listener.contract.models import SendRejectReason, SendStatus


class SenderRegistry:
    """平台 → sender 映射 + 监听房间判定（F2：只读快照，拒绝即可）"""

    def __init__(self, room_checker: Optional[Callable[[str, str], bool]] = None):
        self._senders: Dict[str, BaseSender] = {}
        self._room_checker = room_checker

    def register(self, sender: BaseSender) -> None:
        self._senders[sender.platform] = sender
        logger.info(f"[send-registry] registered sender: {sender.platform}")

    def get(self, platform: str) -> Optional[BaseSender]:
        return self._senders.get(platform)

    def platforms(self) -> list[str]:
        return sorted(self._senders)

    def is_room_listened(self, platform: str, room_id: str) -> bool:
        """监听房间判定（F2）：命令处理时刻快照，拒绝即可——无需更强一致性"""
        if self._room_checker is None:
            return False
        try:
            return bool(self._room_checker(platform, room_id))
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[send-registry] room check failed: {e}")
            return False


class EngineHookSender(BaseSender):
    """受控页面 sender 适配器（E5）：经由监听引擎实例的 send 钩子执行

    禁止另开同 profile persistent context（独占冲突——taobao.py:165 实证）。
    引擎未实现 send_danmu 或未监听该房间 → 对应回执。
    """

    def __init__(self, platform: str, engine: Any, room_checker: Callable[[str], bool]):
        self.platform = platform
        self._engine = engine
        self._room_checker = room_checker

    async def send(self, room_id: str, content: str) -> SendResult:
        checker_result = self._room_checker(room_id)
        logger.info(f"[send-registry] {self.platform}:{room_id} room_checker={checker_result} "
                    f"hook={hasattr(self._engine, 'send_danmu')}")
        if not checker_result:
            return SendResult(SendStatus.FAILED, SendRejectReason.ROOM_NOT_LISTENED.value,
                              fix_hint="先在控制台添加并启动该房间监听",
                              docs_anchor="docs/ops/adr-002-danmu-send-constraint-revision.md")
        hook = getattr(self._engine, "send_danmu", None)
        if hook is None:
            return SendResult(SendStatus.FAILED, SendRejectReason.ROUTE_UNVERIFIED.value,
                              fix_hint="该平台 send 钩子未实现（M0 探针后交付）",
                              docs_anchor="docs/testing/m0-send-probe-cards.md")
        try:
            return await hook(room_id, content)
        except Exception as e:  # noqa: BLE001  E5：send 钩子异常边界隔离——不得拖垮监听
            logger.warning(f"[{self.platform}] send_danmu hook error: {type(e).__name__}: {e}")
            return SendResult(SendStatus.FAILED, SendRejectReason.PLATFORM_REJECTED.value,
                              fix_hint="引擎 send 钩子异常（已隔离）；详情见引擎日志",
                              detail=str(e)[:120])
