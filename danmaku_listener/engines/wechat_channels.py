"""视频号受控后台引擎（2026-09-29 按参考项目 wxlivespy 实测校准）

路线：受控浏览器打开官方直播管理后台（liveBuild），网络层拦截弹幕轮询接口
（wxlivespy 验证的路线：后台页面自身轮询 `mmfinderassistant-bin/live/msg`）。

**接口结构（wxlivespy 同构校准，2026-09-29）**：
- 锚点：URL 含 `mmfinderassistant-bin/live/msg`（排除 mmdata/promote_info/get_live_info）
- `data.liveInfo`：liveId/liveStatus/onlineCnt/likeCnt → ROOM_STATS / LIVE_STATUS_CHANGE
- `data.msgList`：type==1 弹幕（clientMsgId/username/nickname/content/seq）、
  type==10005 进房 → DANMU / ENTER_ROOM
- `data.appMsgList`：msgType 20009=礼物（payload 为 base64 JSON：
  reward_product_id/reward_product_count/reward_amount_in_wecoin）、20013=连击礼物、
  20006=点赞、20031=粉丝等级提升 → GIFT / LIKE / SOCIAL
- 用户 ID：`decoded_openid`/msgId 尾段（同一主播跨场次不变——wxlivespy README）

登录闭环（对齐 B站/快手模式）：无登录态 → 弹可见浏览器 → 用户微信扫码 →
storage_state 持久化 → 自动开始监听。有界会话：4h 事件驱动重建（契约 v1）。

合规：监听主播**自己**的后台（用户本人账号登录），只读页面数据、不发送。
"""

import asyncio
import base64
import io
import json
import time
from typing import Any, Dict, Optional

from loguru import logger

from danmaku_listener.contract import Category, SystemType
from danmaku_listener.contract.models import (
    Envelope,
    FailureInfo,
    NeedsLoginPayload,
    UnifiedMessage,
)
from danmaku_listener.engines.base import BaseEngine

BACKEND_URL = "https://channels.weixin.qq.com/platform/live/liveBuild"
#: 弹幕轮询接口锚点（wxlivespy 同款）
FEED_API_PATTERN = "mmfinderassistant-bin/live/msg"
#: 排除名单（wxlivespy 同款：非弹幕数据接口）
FEED_API_EXCLUDE = (
    "helper/hepler_merlin_mmdata",
    "finder_live_get_promote_info_list",
    "get_live_info",
)
SESSION_LIFETIME_SECONDS = 14400  # 4 小时（契约 v1 有界会话）

#: msgList.type 枚举
TYPE_COMMENT = 1
TYPE_ENTER = 10005
#: appMsgList.msgType 枚举
MSGTYPE_GIFT = 20009
MSGTYPE_COMBO_GIFT = 20013
MSGTYPE_LIKE = 20006
MSGTYPE_LEVEL_UP = 20031


class WechatChannelsEngine(BaseEngine):
    """视频号受控后台引擎（wxlivespy 同构解析 + 登录窗口闭环）"""

    platform = "wechat_channels"

    def __init__(self, state_store=None, cookie_dir: str = "./cookie",
                 session_lifetime: int = SESSION_LIFETIME_SECONDS):
        super().__init__(state_store=state_store)
        self._cookie_dir = cookie_dir
        self._session_lifetime = session_lifetime
        self._headless = True  # 登录窗口期间临时可见
        self._room_tasks: Dict[str, asyncio.Task] = {}
        self._stop_flags: Dict[str, bool] = {}
        self._login_flows: Dict[str, asyncio.Task] = {}

    @property
    def engine_id(self) -> str:
        return "controlled:wechat_channels"

    # ---- 公开契约 ----

    async def start(self, room_id: str) -> None:
        import os

        if room_id in self._room_tasks and not self._room_tasks[room_id].done():
            return
        # 无登录态 → 登录闭环（对齐 B站/快手：弹可见浏览器扫码）
        if not os.path.exists(self._storage_state_path()):
            self._stop_flags[room_id] = False
            self._login_flows[room_id] = asyncio.create_task(
                self._login_then_start(room_id), name=f"wxsp-login-{room_id}")
            return
        self._stop_flags[room_id] = False
        self._room_tasks[room_id] = asyncio.create_task(
            self._run_room(room_id), name=f"wxsp-room-{room_id}")

    async def stop(self, room_id: str) -> None:
        self._stop_flags[room_id] = True
        for store in (self._room_tasks, self._login_flows):
            task = store.pop(room_id, None)
            if task:
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass

    async def restart(self, room_id: str) -> None:
        """仅重启该房间任务（契约 I）"""
        await self.stop(room_id)
        await self.start(room_id)

    # ---- 登录闭环 ----

    def _storage_state_path(self) -> str:
        import os

        os.makedirs(self._cookie_dir, exist_ok=True)
        return f"{self._cookie_dir}/wechat_channels_state.json"

    async def _login_then_start(self, room_id: str) -> None:
        """登录窗口闭环：可见浏览器扫码 → storage_state 保存 → 自动开始监听"""
        from playwright.async_api import async_playwright

        await self._emit_system_status(room_id, "登录窗口已打开，请用微信扫码登录视频号管理后台")
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=False)
            try:
                context = await browser.new_context(viewport={"width": 1280, "height": 800})
                page = await context.new_page()
                await page.goto(BACKEND_URL, wait_until="domcontentloaded")
                deadline = time.monotonic() + 300.0
                logged_in = False
                while time.monotonic() < deadline:
                    if not self._needs_login(page.url):
                        logged_in = True
                        break
                    await asyncio.sleep(2)
                if logged_in:
                    await context.storage_state(path=self._storage_state_path())
                    await self._emit_system_status(room_id, "登录成功——开始监听")
            finally:
                await browser.close()
        if not self._stop_flags.get(room_id):
            self._room_tasks[room_id] = asyncio.create_task(
                self._run_room(room_id), name=f"wxsp-room-{room_id}")

    @staticmethod
    def _needs_login(url: str) -> bool:
        return "login" in url or "scanlogin" in url.lower() or "/login" in url

    # ---- 房间任务：有界会话循环 ----

    async def _run_room(self, room_id: str) -> None:
        """有界会话主循环：单页面生命周期到期后事件驱动重建（替代全局定时重启）"""
        while not self._stop_flags.get(room_id):
            session_start = int(time.time())
            try:
                await self._run_session(room_id)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(f"[wxsp] room {room_id} session error: {e}")
                await self._emit_session_event(room_id, detail=f"session error: {e}")
            if self._stop_flags.get(room_id):
                return
            elapsed = int(time.time()) - session_start
            await self._emit_session_event(room_id, detail=f"session rebuilt after {elapsed}s")
            await asyncio.sleep(5)

    async def _run_session(self, room_id: str) -> None:
        """单次有界会话：浏览器打开后台 → 拦截弹幕轮询接口 → 生命周期上限退出"""
        from playwright.async_api import async_playwright

        deadline = time.monotonic() + self._session_lifetime
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=self._headless)
            try:
                context = await browser.new_context(
                    storage_state=self._storage_state_path(),
                    viewport={"width": 1280, "height": 800},
                )
                page = await context.new_page()

                async def on_response(response) -> None:
                    try:
                        await self._on_response(room_id, response)
                    except Exception as e:  # noqa: BLE001
                        logger.debug(f"[wxsp] room {room_id} response parse error: {e}")

                page.on("response", lambda r: asyncio.create_task(on_response(r)))

                await page.goto(BACKEND_URL, wait_until="domcontentloaded")
                if self._needs_login(page.url):
                    # 登录态失效：改可见窗口（外层重建会话时用户可扫码）
                    await self._emit_needs_login(room_id)
                    self._headless = False
                    return
                logger.info(f"[wxsp] room {room_id} backend page ready")

                # 有界会话读循环：到期/停止信号退出（重建由外层负责）
                while time.monotonic() < deadline and not self._stop_flags.get(room_id):
                    await asyncio.sleep(2)
                    self.mark_received(room_id, int(time.time()))
            finally:
                await browser.close()

    # ---- 网络层拦截（wxlivespy 同构解析）----

    @staticmethod
    def _is_feed_api(url: str) -> bool:
        if FEED_API_PATTERN not in url:
            return False
        return not any(x in url for x in FEED_API_EXCLUDE)

    @staticmethod
    def _decode_gift_payload(payload_b64: str) -> Dict[str, Any]:
        """appMsg payload：base64 JSON（reward_product_id/count/amount）"""
        try:
            decoded = base64.b64decode(payload_b64).decode("utf-8")
            return json.loads(decoded)
        except Exception:  # noqa: BLE001
            return {}

    async def _on_response(self, room_id: str, response) -> None:
        """网络层拦截：弹幕轮询接口响应 → 契约消息（结构=wxlivespy 校准）"""
        if not self._is_feed_api(response.url):
            return
        try:
            body = await response.json()
        except Exception:
            return
        data = (body or {}).get("data") or {}
        ts = int(time.time())

        # liveInfo → ROOM_STATS / 下播事件
        live_info = data.get("liveInfo") or {}
        if live_info:
            live_status = live_info.get("liveStatus")
            if live_status is not None:
                await self._emit_message({
                    "contract_version": "1.0.0",
                    "category": Category.SYSTEM.value,
                    "type": "LIVE_STATUS_CHANGE",
                    "platform": self.platform,
                    "room_id": room_id,
                    "seq": self.next_seq(room_id),
                    "timestamp": ts,
                    "engine": self.engine_id,
                    "protocol_version": "wxsp-backend-1",
                    "payload": {"type": "LIVE_STATUS_CHANGE",
                                "live": live_status == 4,  # liveStatus=4 直播中（wxlivespy 实测枚举）
                                "raw_status": live_status},
                })
            online = live_info.get("onlineCnt")
            if online is not None:
                await self._emit_message({
                    "contract_version": "1.0.0",
                    "category": Category.BUSINESS.value,
                    "type": "ROOM_STATS",
                    "platform": self.platform,
                    "room_id": room_id,
                    "seq": self.next_seq(room_id),
                    "timestamp": ts,
                    "engine": self.engine_id,
                    "protocol_version": "wxsp-backend-1",
                    "payload": {"type": "ROOM_STATS", "viewer_count": online,
                                "like_count": live_info.get("likeCnt")},
                })

        # msgList：type==1 弹幕 / type==10005 进房
        for item in data.get("msgList") or []:
            seq_id = item.get("seq") or item.get("clientMsgId")
            user_id = item.get("username")
            nick = item.get("nickname", "")
            content = item.get("content", "")
            msg_type = item.get("type")
            if msg_type == TYPE_COMMENT and content:
                ctype = "DANMU"
                payload = {"type": "DANMU", "user_name": nick,
                           "content": content, "user_id": user_id}
            elif msg_type == TYPE_ENTER:
                ctype = "ENTER_ROOM"
                payload = {"type": "ENTER_ROOM", "user_name": nick, "user_id": user_id}
            else:
                logger.debug(f"[wxsp] room {room_id} msgList type={msg_type} dropped")
                continue
            await self._emit_business(room_id, ctype, payload, msg_id=seq_id, ts=ts)

        # appMsgList：礼物/点赞/等级
        for item in data.get("appMsgList") or []:
            msg_type = item.get("msgType")
            nick = (item.get("fromUserContact") or {}).get("contact", {}).get("nickname", "")
            user_id = (item.get("fromUserContact") or {}).get("contact", {}).get("username")
            seq_id = item.get("seq") or item.get("clientMsgId")
            raw_payload = item.get("payload") or ""
            gift_payload = self._decode_gift_payload(raw_payload) if raw_payload else {}

            if msg_type in (MSGTYPE_GIFT, MSGTYPE_COMBO_GIFT):
                payload = {"type": "GIFT", "user_name": nick, "user_id": user_id,
                           "gift_name": gift_payload.get("reward_product_id", ""),
                           "gift_count": gift_payload.get(
                               "combo_product_count",
                               gift_payload.get("reward_product_count", 1)),
                           "gift_value": gift_payload.get("reward_amount_in_wecoin", 0),
                           "raw": gift_payload}
                await self._emit_business(room_id, "GIFT", payload, msg_id=seq_id, ts=ts)
            elif msg_type == MSGTYPE_LIKE:
                await self._emit_business(
                    room_id, "LIKE",
                    {"type": "LIKE", "count": 1, "user_name": nick, "user_id": user_id},
                    msg_id=seq_id, ts=ts)
            elif msg_type == MSGTYPE_LEVEL_UP:
                await self._emit_business(
                    room_id, "SOCIAL",
                    {"type": "SOCIAL", "action": "fans_level_up", "user_name": nick,
                     "user_id": user_id,
                     "from_level": gift_payload.get("from_level", 0),
                     "to_level": gift_payload.get("to_level", 0)},
                    msg_id=seq_id, ts=ts)
            else:
                logger.debug(f"[wxsp] room {room_id} appMsg msgType={msg_type} dropped")

    async def _emit_business(self, room_id: str, ctype: str, payload: Dict[str, Any],
                             msg_id: Any = None, ts: int = 0) -> None:
        await self._emit_message({
            "contract_version": "1.0.0",
            "category": Category.BUSINESS.value,
            "type": ctype,
            "platform": self.platform,
            "room_id": room_id,
            "seq": self.next_seq(room_id),
            "timestamp": ts,
            "engine": self.engine_id,
            "protocol_version": "wxsp-backend-1",
            "msg_id": msg_id,
            "payload": payload,
        })

    async def _emit_system_status(self, room_id: str, detail: str) -> None:
        seq = self.next_seq(room_id)
        msg = UnifiedMessage(
            envelope=Envelope(
                category=Category.SYSTEM, type=SystemType.ENGINE_STATUS.value,
                platform=self.platform, room_id=room_id, seq=seq,
                timestamp=int(time.time()), engine=self.engine_id,
            ),
            payload={"type": "ENGINE_STATUS", "engine": self.engine_id, "detail": detail},
        )
        await self._emit_message(msg.to_wire())

    # ---- NEEDS_LOGIN（契约 Eng Q：interactive_login 载荷）----

    async def _emit_needs_login(self, room_id: str) -> None:
        seq = self.next_seq(room_id)
        msg = UnifiedMessage(
            envelope=Envelope(
                category=Category.SYSTEM, type=SystemType.NEEDS_LOGIN.value,
                platform=self.platform, room_id=room_id, seq=seq,
                timestamp=int(time.time()), engine=self.engine_id,
            ),
            payload=NeedsLoginPayload(
                failure=FailureInfo(
                    reason_code="wechat_channels.login_expired",
                    fix_hint="在弹出的浏览器中用微信扫码重新登录视频号管理后台",
                    docs_anchor="docs/platforms/wechat_channels/runbook.md#relogin",
                ),
            ),
        )
        await self._emit_message(msg.to_wire())

    async def _emit_session_event(self, room_id: str, detail: str) -> None:
        """ENGINE_STATUS 会话事件（有界会话重建对上层可见）"""
        seq = self.next_seq(room_id)
        msg = UnifiedMessage(
            envelope=Envelope(
                category=Category.SYSTEM, type=SystemType.ENGINE_STATUS.value,
                platform=self.platform, room_id=room_id, seq=seq,
                timestamp=int(time.time()), engine=self.engine_id,
            ),
            payload={"type": "ENGINE_STATUS", "engine": self.engine_id, "detail": detail},
        )
        await self._emit_message(msg.to_wire())
