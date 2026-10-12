"""发送结果与 sender 抽象（AutoDanmu T4）"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

from danmaku_listener.contract.models import SendStatus


@dataclass
class SendResult:
    """单次发送结果（管线统一回执语义）"""

    status: SendStatus
    reason_code: Optional[str] = None     # SendRejectReason 值或平台细分码
    fix_hint: Optional[str] = None
    docs_anchor: Optional[str] = None
    sent_at: Optional[int] = None
    detail: Optional[str] = None

    @property
    def ok(self) -> bool:
        """成功或 dry-run（观察模式语义上完成了命令）"""
        return self.status in (SendStatus.SENT, SendStatus.DRY_RUN)


class BaseSender(ABC):
    """平台发送器抽象：管线唯一分发接口（R32——通道层不含业务逻辑）"""

    platform: str = ""

    @abstractmethod
    async def send(self, room_id: str, content: str) -> SendResult:
        """发送一条弹幕到房间；实现方负责平台侧成功判定（F5）"""
        raise NotImplementedError
