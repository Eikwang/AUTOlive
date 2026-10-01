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
import json
import time
from typing import Any, Dict

from loguru import logger

from danmaku_listener.contract import Category, SystemType
from danmaku_listener.contract.models import (
    Envelope,
    FailureInfo,
    NeedsLoginPayload,
    UnifiedMessage,
)
from danmaku_listener.engines.protocol.controlled_base import ControlledPageEngine

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


class NeedLoginVisible(Exception):
    """需要可见窗口扫码登录（内部信号）"""


class WechatChannelsEngine(ControlledPageEngine):
    """视频号受控后台引擎（wxlivespy 同构解析 + 登录窗口闭环）"""

    platform = "wechat_channels"
    profile_name = "wxsp_profile"
    protocol_version = "wxsp-backend-1"

    @property
    def engine_id(self) -> str:
        return "controlled:wechat_channels"  # 受控后台（非通用 page: 前缀）

    def __init__(self, state_store=None, cookie_dir: str = "./cookie",
                 session_lifetime: int = SESSION_LIFETIME_SECONDS,
                 raw_hook=None):
        super().__init__(state_store=state_store, cookie_dir=cookie_dir)
        self._session_lifetime = session_lifetime
        self._raw_hook = raw_hook  # 诊断钩子：live/msg 响应 body（分析用）

    # ---- 登录与会话（单会话长跑模式——2026-09-30 实测：视频号后台登录态
    #      不支持静置恢复，关闭浏览器后 cookie 快速失效；登录、导航、监听
    #      必须在同一个存活的 context 里完成。wxlivespy 同为常驻浏览器）----

    @staticmethod
    def _needs_login(url: str) -> bool:
        return "login" in url or "scanlogin" in url.lower() or "/login" in url

    # ---- 房间任务：会话循环 ----

    async def _run_room(self, room_id: str) -> None:
        """会话主循环：异常退出后重建（重建时 cookie 若仍有效直接导航，
        失效则在可见窗口内就地等扫码——同一 context 完成登录与监听）"""
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
        """单会话长跑（可见窗口）：扫码登录（如需）→ 导航进入直播间中控页 →
        live/msg 轮询拦截读循环（无到期——context 关闭即登录态失效，
        重建代价=重新扫码，因此只在异常/停止时退出）"""
        from playwright.async_api import async_playwright

        # 登录扫码需可见；中控页保持可见（wxlivespy 同）——单会话长跑
        async with async_playwright() as pw:
            context = await self._launch(pw, headless=False)
            try:
                page = context.pages[0] if context.pages else await context.new_page()

                async def on_response(response) -> None:
                    try:
                        await self._on_response(room_id, response)
                    except Exception as e:  # noqa: BLE001
                        logger.debug(f"[wxsp] room {room_id} response parse error: {e}")

                page.on("response", lambda r: asyncio.create_task(on_response(r)))

                await page.goto(BACKEND_URL, wait_until="domcontentloaded",
                                timeout=25000)
                # SPA 登录跳转发生在 domcontentloaded 之后——等稳再判
                await asyncio.sleep(5)
                if self._needs_login(page.url):
                    # 就地等扫码（同一 context——登录态由存活页面续命）
                    await self._emit_system_status(
                        room_id, "请在弹出的浏览器窗口内用微信扫码登录"
                                 "视频号管理后台")
                    logger.info(f"[wxsp] room {room_id} waiting for scan login")
                    deadline = time.monotonic() + 300.0
                    while time.monotonic() < deadline:
                        await asyncio.sleep(2)
                        try:
                            url = page.url
                        except Exception:  # noqa: BLE001
                            continue
                        if not self._needs_login(url):
                            break
                    else:
                        logger.warning(f"[wxsp] room {room_id} scan login timeout")
                        return
                    await asyncio.sleep(3)  # 后台页落地
                    await self._emit_system_status(room_id, "登录成功——开始导航")
                logger.info(f"[wxsp] room {room_id} backend page ready "
                            f"(url={page.url[:70]})")

                # 导航：直播 → 直播管理 → 进入直播间（2026-09-30 用户实测
                # 路径——live/msg 轮询只在进入直播间中控页后启动）
                await self._navigate_to_live_room(room_id, page)

                # 读循环：无到期（context 关闭=登录态失效，重建代价高）
                while not self._stop_flags.get(room_id):
                    await asyncio.sleep(2)
                    self.mark_received(room_id, int(time.time()))
            finally:
                await context.close()

    # ---- 网络层拦截（wxlivespy 同构解析）----

    @staticmethod
    async def _click_text_anywhere(page, texts, timeout_s: float = 6.0) -> bool:
        """全页面范围点击文本匹配的最小可见元素（不限侧栏）"""
        import asyncio as _asyncio

        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            cands = await page.evaluate(
                """(texts) => {
                    const out = [];
                    document.querySelectorAll('div,li,span,button,a').forEach(el => {
                        const t = (el.innerText || '').trim();
                        if (texts.some(x => t === x || t.startsWith(x))
                                && t.length < 12 && el.children.length <= 1) {
                            const r = el.getBoundingClientRect();
                            if (r.width > 0 && r.height > 0)
                                out.push({x: r.x + r.width/2, y: r.y + r.height/2});
                        }
                    });
                    return out;
                }""", list(texts))
            if cands:
                c = cands[0]
                await page.mouse.click(c["x"], c["y"])
                return True
            await _asyncio.sleep(0.5)
        return False

    async def _navigate_to_live_room(self, room_id: str, page) -> bool:
        """导航：直播图标（侧栏第 4 项，纯图标无文本）→ 直播管理 → 进入直播间

        2026-09-30 用户实测路径：live/msg 轮询只在**进入直播间**后的中控页
        （URL=liveBuild）启动。侧栏菜单为纯图标（hover 才显名称），按几何
        位置点击第 4 项；每步失败有文本兜底与日志。
        """
        import asyncio as _asyncio

        # 1. 侧栏图标：menu 类容器，x<120，按 y 排序去重 → 第 4 个=直播
        icons = await page.evaluate(
            """() => {
                const raw = [];
                document.querySelectorAll('div,li').forEach(el => {
                    const r = el.getBoundingClientRect();
                    const cls = (el.className || '').toString();
                    if (r.width > 20 && r.width < 100 && r.height > 20
                            && r.height < 90 && r.x < 120 && r.y > 50
                            && r.y < 620 && /menu/i.test(cls)) {
                        raw.push({x: r.x + r.width/2, y: r.y + r.height/2,
                                  cls: cls.slice(0, 40)});
                    }
                });
                raw.sort((a, b) => a.y - b.y);
                const uniq = [];
                for (const c of raw) {
                    if (!uniq.length || c.y - uniq[uniq.length - 1].y > 30)
                        uniq.push(c);
                }
                return uniq;
            }""")
        logger.debug(f"[wxsp] room {room_id} 侧栏图标数={len(icons)} "
                     f"{[f"({i['x']:.0f},{i['y']:.0f})" for i in icons]}")
        clicked_live = False
        if len(icons) >= 4:
            c = icons[3]  # 首页/视频/消息/直播
            await page.mouse.click(c["x"], c["y"])
            clicked_live = True
        else:
            # 兜底：文本点击（某些版本菜单有名称）
            clicked_live = await self._click_text_anywhere(
                page, ["直播"], timeout_s=3.0)
        if not clicked_live:
            logger.warning(f"[wxsp] room {room_id} 侧栏直播图标未找到")
            return False
        await _asyncio.sleep(2.5)
        logger.info(f"[wxsp] room {room_id} 点直播菜单后 url={page.url[:80]}")

        # 2. 直播管理（菜单展开的子项或页面 tab；可能已在该页）
        await self._click_text_anywhere(page, ["直播管理"], timeout_s=4.0)
        await _asyncio.sleep(2.5)
        logger.info(f"[wxsp] room {room_id} 点直播管理后 url={page.url[:80]}")

        # 3. 进入直播间（进行中场次的入口按钮）
        ok = await self._click_text_anywhere(page, ["进入直播间"], timeout_s=8.0)
        if ok:
            logger.info(f"[wxsp] room {room_id} 已点击进入直播间，"
                        f"url={page.url[:80]}")
            await _asyncio.sleep(4)
        else:
            await self._emit_system_status(
                room_id, "未找到'进入直播间'入口——确认直播正在进行"
                         "（开播后刷新管理页）；也可在弹出的窗口中手动点击"
                         "进入直播间，引擎会自动开始监听")
        return ok

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
        if self._raw_hook is not None:
            try:
                self._raw_hook(body)
            except Exception:  # noqa: BLE001
                pass
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

    async def _emit_session_event(self, room_id: str, detail: str) -> None:
        """ENGINE_STATUS 会话事件（有界会话重建对上层可见）"""
        await self._emit_system_status(room_id, detail)

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

