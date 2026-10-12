"""Bilibili HTTP API sender（AutoDanmu T6-M1a；E4 复用引擎 cookie 解析器）

路线：POST api.live.bilibili.com/msg/send（cookie+csrf），可行性「高」（计划矩阵）。
成功判定（F5）：HTTP 200 且 code==0；-101 登录失效；10030 房间不可发。
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Optional

from loguru import logger

from danmaku_listener.contract.models import SendRejectReason, SendStatus
from danmaku_listener.senders.base import BaseSender, SendResult


class BilibiliSender(BaseSender):
    """B站弹幕发送器（msg/send；cookie 来自 bilibili_storage_state.json，E4 三格式解析复用）"""

    platform = "bilibili"

    SEND_URL = "https://api.live.bilibili.com/msg/send"

    def __init__(self, cookie_file: Optional[str] = None, state_path: Optional[str] = None):
        self._cookie_file = cookie_file
        self._state_path = state_path
        self._fetcher: Optional[Any] = None  # DanmuInfoFetcher——复用其 cookie 解析与 session

    def _get_fetcher(self) -> Any:
        """惰性构造 DanmuInfoFetcher（E4：复用三格式 cookie 解析与 session 头，勿复制）"""
        if self._fetcher is None:
            from danmaku_listener.engines.protocol.bilibili import DanmuInfoFetcher

            cookie_file = self._cookie_file or self._state_path
            self._fetcher = DanmuInfoFetcher(cookie_file=cookie_file)
        return self._fetcher

    async def send(self, room_id: str, content: str) -> SendResult:
        def _sync_send() -> SendResult:
            try:
                fetcher = self._get_fetcher()
                session = fetcher._get_session()  # E4 复用：UA/Referer/Origin 头+三格式 cookie 已就绪
                bili_jct = session.cookies.get("bili_jct", domain=".bilibili.com")
                if not bili_jct:
                    bili_jct = session.cookies.get("bili_jct")
                if not bili_jct:
                    return SendResult(SendStatus.FAILED, SendRejectReason.PLATFORM_REJECTED.value,
                                      fix_hint="cookie 缺 bili_jct（csrf）——重新登录 B站",
                                      docs_anchor="docs/testing/m0-send-probe-cards.md")
                form = {
                    "bubble": 0, "msg": content, "color": 16777215, "mode": 1,
                    "fontsize": 25, "rnd": str(int(time.time() * 1e6)),
                    "roomid": room_id, "csrf": bili_jct, "csrf_token": bili_jct,
                }
                resp = session.post(self.SEND_URL, data=form, timeout=10)
                data = resp.json()
                code = data.get("code")
                if code == 0:
                    return SendResult(SendStatus.SENT, sent_at=int(time.time()))
                msg = (data.get("message") or "")[:100]
                if code == -101:
                    return SendResult(SendStatus.FAILED, SendRejectReason.PLATFORM_REJECTED.value,
                                      fix_hint="登录失效——重新登录 B站（NEEDS_LOGIN 语义）",
                                      docs_anchor="docs/testing/m0-send-probe-cards.md", detail=f"code={code} {msg}")
                return SendResult(SendStatus.FAILED, SendRejectReason.PLATFORM_REJECTED.value,
                                  fix_hint=f"平台拒绝 code={code}", detail=msg or str(code))
            except Exception as e:  # noqa: BLE001
                return SendResult(SendStatus.FAILED, SendRejectReason.PLATFORM_REJECTED.value,
                                  detail=f"{type(e).__name__}: {str(e)[:100]}")

        # requests 同步 IO——线程池隔离（监听事件循环不受阻塞，E5 隔离纪律）
        return await asyncio.to_thread(_sync_send)
