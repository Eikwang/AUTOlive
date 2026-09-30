"""1688 直播弹幕引擎（独立平台，受控页面响应拦截——视频号同构路线）

技术路线（2026-09-29 定型）：1688 直播弹幕的 powermsg 通道依赖浏览器会话
（安全 cookie/订阅前置/安全头）——手动 mtop 轮询无法稳定复现。改为
**常驻 Playwright 页面打开 1688 直播间**，页面自身管理会话与弹幕拉取，
引擎在网络层拦截页面的 `powermsg.h5.msg.pullnativemsg` 响应并解析。

消息结构（与淘宝 powermsg 同构，JSON 对象流）：
- viewCountFormat/pageViewCount/totalCount → ROOM_STATS
- subType==10001 → DANMU（nick/content/userid）；subType==10002 → GIFT
- nick+flowSourceText → ENTER_ROOM（identify.fanLevel）
- value.dig → LIKE；未映射对象丢弃+debug（不耗 seq、不产 GAP）

房间参数：feedId（场次级——下播即失效，需重新复制直播间链接）或完整链接。
下播检测：页面含"已结束/下播/回放" → 三段式错误。
"""

import asyncio
import json
import re
import time
from typing import Any, Dict, Optional

from loguru import logger

from danmaku_listener.contract.models import GapReason
from danmaku_listener.engines.base import BaseEngine

PROTOCOL_VERSION = "1688-1"

LIVE_URL_TEMPLATE = "https://live.1688.com/zb/play.html?feedId={feed_id}"
PULL_API_ANCHOR = "pullnativemsg"
SILENCE_TIMEOUT = 90.0  # 弹幕接口响应静默阈值（页面统计约 30s 一条）

HEARTBEAT_FRAME = b"\x3a\x02hb"


class Live1688ParseError(ValueError):
    """房间参数无法解析"""


class NeedLoginVisible(Exception):
    """需要可见窗口登录（内部信号）"""


def extract_feed_id(room_spec: str) -> str:
    """feedId 归一：数字 / 完整直播间链接（feedId=）"""
    if "1688.com" in room_spec:
        m = re.search(r"feedId=(\d+)", room_spec)
        if m:
            return m.group(1)
    m = re.match(r"^(\d{8,25})$", room_spec.strip())
    if m:
        return m.group(1)
    raise Live1688ParseError(
        f"无法解析 1688 直播间 feedId: {room_spec[:60]!r}——"
        "请使用直播间链接或 feedId 数字")


def parse_base64_mixed_message(base64_data: str) -> list:
    """powermsg 消息体：base64 → protobuf+JSON 混合字节流 → 扫描提取 JSON 对象

    （括号计数 + 字符串转义感知；淘宝/1688 同构解析器）
    """
    import base64

    decoded = base64.b64decode(base64_data)
    json_objects = []
    pos = 0
    while pos < len(decoded):
        json_start = -1
        for i in range(pos, len(decoded)):
            if decoded[i] == 0x7B:  # '{'
                json_start = i
                break
        if json_start == -1:
            break
        brace = 0
        in_string = False
        escaped = False
        json_end = -1
        for i in range(json_start, len(decoded)):
            ch = chr(decoded[i]) if decoded[i] < 128 else "?"
            if not in_string:
                if ch == "{":
                    brace += 1
                elif ch == "}":
                    brace -= 1
                elif ch == '"':
                    in_string = True
            else:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_string = False
            if brace == 0 and i > json_start:
                json_end = i
                break
        if json_end == -1:
            break
        try:
            json_objects.append(
                json.loads(decoded[json_start : json_end + 1].decode("utf-8", errors="ignore")))
        except json.JSONDecodeError:
            pass
        pos = json_end + 1
    return json_objects


class Live1688Engine(BaseEngine):
    """1688 直播弹幕引擎（受控页面响应拦截，独立平台）"""

    platform = "1688"

    def __init__(self, state_store=None, cookie_dir: str = "./cookie",
                 session_lifetime: int = 14400):
        super().__init__(state_store=state_store)
        self._cookie_dir = cookie_dir
        self._session_lifetime = session_lifetime
        self._room_tasks: Dict[str, asyncio.Task] = {}
        self._stop_flags: Dict[str, bool] = {}

    @property
    def engine_id(self) -> str:
        return "page:1688"

    def _profile_dir(self) -> str:
        import os

        d = f"{self._cookie_dir}/1688_profile"
        os.makedirs(d, exist_ok=True)
        return d

    def validate_room_id(self, room_id: str) -> None:
        """add_room 预校验：feedId 可解析"""
        try:
            extract_feed_id(room_id)
        except Live1688ParseError as e:
            raise ValueError(str(e)) from e

    # ---- 公开契约 ----

    async def start(self, room_id: str) -> None:
        if room_id in self._room_tasks and not self._room_tasks[room_id].done():
            return
        self._stop_flags[room_id] = False
        self._room_tasks[room_id] = asyncio.create_task(
            self._run_room(room_id), name=f"1688-room-{room_id}")

    async def stop(self, room_id: str) -> None:
        self._stop_flags[room_id] = True
        task = self._room_tasks.pop(room_id, None)
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

    # ---- 房间任务主循环（有界会话）----

    async def _run_room(self, room_id: str) -> None:
        feed_id = self._extract_feed_id(room_id)
        logger.info(f"[1688] room {room_id} connecting (feed_id={feed_id})")
        backoff = 15.0
        headless = True
        while not self._stop_flags.get(room_id):
            session_start = int(time.time())
            try:
                await self._run_session(room_id, feed_id, headless=headless)
                backoff = 15.0
            except asyncio.CancelledError:
                raise
            except NeedLoginVisible:
                # 可见窗口登录（阻塞至登录成功/超时）→ 登录后重开正常会话
                if self._stop_flags.get(room_id):
                    return
                await self._wait_login_visible(room_id, feed_id)
                headless = True  # 登录态入 profile，后续恢复无头
                continue
            except Live1688ParseError as e:
                logger.warning(f"[1688] room {room_id} {e}")
                await self._emit_route_failed(room_id, "1688.page.parse_failed",
                                              "直播间链接无法解析——确认房间号/链接",
                                              "docs/platforms/1688/runbook.md")
                if self._stop_flags.get(room_id):
                    return
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 900.0)
            except Exception as e:
                logger.warning(f"[1688] room {room_id} error: {type(e).__name__}: {str(e)[:90]}")
                self._set_status(self.status.__class__.ERROR)
                self.mark_gap_start(room_id)
                gap = self.build_gap_message(room_id, GapReason.NETWORK)
                if gap:
                    await self._emit_system(gap)
                if self._stop_flags.get(room_id):
                    return
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 900.0)
            # 有界会话正常到期：重建事件（契约语义）
            elapsed = int(time.time()) - session_start
            if elapsed >= self._session_lifetime:
                logger.info(f"[1688] room {room_id} session rebuilt after {elapsed}s")

    @staticmethod
    def _extract_feed_id(room_id: str) -> str:
        return extract_feed_id(room_id)

    async def _run_session(self, room_id: str, feed_id: str,
                           headless: bool = True) -> None:
        """单次有界会话：常驻页面 + 弹幕响应拦截

        登录闭环（2026-09-29 实测：1688 聊天弹幕只推给登录会话——游客 pull
        只有统计/等级/系统消息）：未登录时改可见窗口等用户阿里账号登录
        （unb cookie 出现即成功；persistent profile 持久登录态，后续启动免登录）。
        """
        from playwright.async_api import async_playwright

        deadline = time.monotonic() + self._session_lifetime
        async with async_playwright() as pw:
            context = await pw.chromium.launch_persistent_context(
                self._profile_dir(),
                headless=headless,
                user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/126.0.0.0 Safari/537.36"),
                viewport={"width": 1280, "height": 800},
                args=["--disable-blink-features=AutomationControlled",
                      "--disable-setuid-sandbox",
                      "--hide-crash-restore-bubble"],
            )
            try:
                page = context.pages[0] if context.pages else await context.new_page()
                last_pull_box = {"t": time.monotonic()}

                async def on_response(response) -> None:
                    # pull 响应到达 = 页面弹幕通道存活（同时刷新静默计时）
                    if PULL_API_ANCHOR in response.url:
                        last_pull_box["t"] = time.monotonic()
                        self.mark_received(room_id, int(time.time()))
                    try:
                        await self._on_response(room_id, response)
                    except asyncio.CancelledError:
                        raise
                    except Exception as e:  # noqa: BLE001
                        logger.debug(f"[1688] room {room_id} response parse error: {e}")

                page.on("response", lambda r: asyncio.create_task(on_response(r)))

                try:
                    await page.goto(
                        LIVE_URL_TEMPLATE.format(feed_id=feed_id),
                        timeout=30000, wait_until="domcontentloaded")
                except Exception as e:  # noqa: BLE001
                    logger.debug(f"[1688] room {room_id} goto warning: {e}")

                logger.info(f"[1688] room {room_id} live page ready")

                # 登录闭环：聊天弹幕只推给登录会话（2026-09-29 实测）——
                # 未登录（无 unb cookie）且当前为无头时改可见窗口等用户登录
                if not await self._has_login_cookie(context) and headless:
                    logger.info(f"[1688] room {room_id} not logged in — visible window for login")
                    await self._emit_system_status(
                        room_id, "1688 需要登录（聊天弹幕仅登录可见）——"
                                 "已弹出浏览器，请用阿里账号/淘宝账号扫码登录")
                    raise NeedLoginVisible()

                self._set_status(self.status.__class__.RUNNING)

                # DOM 弹幕轮询任务（2026-09-30 实测：1688 聊天弹幕不走任何 HTTP
                # 响应通道——发送与拉取均无网络回显——唯一可靠通道 = DOM 弹幕区
                # 读取；pull 拦截并行保留做 ROOM_STATS）
                dom_task = asyncio.create_task(self._poll_dom_danmu(room_id, page))
                try:
                    # 有界会话循环 + 响应流静默检测：
                    # 下播/风控后页面的弹幕轮询停止 → SILENCE_TIMEOUT 无 pull 响应 → 三段式
                    while time.monotonic() < deadline and not self._stop_flags.get(room_id):
                        await asyncio.sleep(2)
                        if time.monotonic() - last_pull_box["t"] > SILENCE_TIMEOUT:
                            raise Live1688ParseError(
                                "1688.session.silent: 90s 无弹幕接口响应"
                                "（可能未开播/已下播/风控——feedId 场次级，"
                                "开播后重新复制直播间链接）")
                finally:
                    dom_task.cancel()
                    try:
                        await dom_task
                    except (asyncio.CancelledError, Exception):
                        pass
            finally:
                await context.close()

    async def _poll_dom_danmu(self, room_id: str, page) -> None:
        """DOM 弹幕区读取（1688 弹幕唯一可靠通道——实测网络响应无弹幕回显）

        弹幕 DOM（页面 JS 渲染源码实证，cmod-pc-web-living-room 弹幕组件）：
        div.comment-message 下**昵称/内容分节点**——
        - .from     = 昵称（观众脱敏渲染形态 `首***尾:` 带尾冒号，主播原名无冒号）
        - .msg-text = 内容（恒为纯内容，不含昵称前缀）
        每 2s 全量读取，新指纹条目 emit DANMU。昵称缺失时置空——
        绝不复用内容当昵称（用户实测教训：fallback 曾致 user_name==content）。
        """
        seen: set = set()  # 已见弹幕指纹（昵称+内容 hash；有界防内存涨）
        seen_list: list = []
        while not self._stop_flags.get(room_id):
            try:
                items = await page.evaluate(
                    """() => {
                        const out = [];
                        document.querySelectorAll(
                            '.pc-living-room-message .comment-message-list .comment-message'
                        ).forEach(el => {
                            const fromEl = el.querySelector('.from');
                            const textEl = el.querySelector('.msg-text') || el;
                            const nick = (((fromEl && fromEl.textContent) || '')
                                          .trim().replace(/:$/, '')).trim();
                            const text = (textEl.textContent || '').trim();
                            if (text) out.push({nick: nick, text: text});
                        });
                        return out;
                    }""")
                ts = int(time.time())
                for item in items:
                    nick = (item.get("nick") or "").strip()
                    content = (item.get("text") or "").strip()
                    # 防御：.from 缺失时从文本拆 `昵称:内容`（两段均非空才拆；
                    # 标准渲染 .from 恒存在，此路径仅兜底回显形态）
                    if not nick and ":" in content:
                        n, _, c = content.partition(":")
                        if n.strip() and c.strip():
                            nick, content = n.strip(), c.strip()
                    if not content:
                        continue
                    h = hash(f"{nick}\x00{content}")
                    if h in seen:
                        continue
                    seen.add(h)
                    seen_list.append(h)
                    if len(seen_list) > 500:
                        old_h = seen_list.pop(0)
                        seen.discard(old_h)
                    self.mark_received(room_id, ts)
                    mapped = {
                        "category": "business", "type": "DANMU",
                        "seq": self.next_seq(room_id), "timestamp": ts,
                        "payload": {"type": "DANMU",
                                    "user_name": nick,
                                    "content": content},
                    }
                    await self._emit_message(self._envelope(room_id, mapped))
                await asyncio.sleep(2)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[1688] room {room_id} dom poll error: "
                               f"{type(e).__name__}: {str(e)[:60]}")
                await asyncio.sleep(5)

    @staticmethod
    async def _has_login_cookie(context) -> bool:
        """登录态判定：unb cookie（阿里系账号标识）存在且有值

        persistent profile 登录后 unb 持久化——遍历全部 cookie 判定。
        （占位符实现曾永远返回 False 导致登录循环——2026-09-30 修复）
        """
        try:
            cookies = await context.cookies()
            return any(c.get("name") == "unb" and c.get("value") for c in cookies)
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[1688] login cookie check error: {e}")
            return False

    async def _wait_login_visible(self, room_id: str, feed_id: str) -> None:
        """可见窗口登录流程：用户登录（unb cookie 出现）→ 登录态入 profile → 返回"""
        from playwright.async_api import async_playwright

        async with async_playwright() as pw:
            context = await pw.chromium.launch_persistent_context(
                self._profile_dir(),
                headless=False,
                user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/126.0.0.0 Safari/537.36"),
                viewport={"width": 1280, "height": 800},
                args=["--disable-blink-features=AutomationControlled"])
            try:
                page = context.pages[0] if context.pages else await context.new_page()
                try:
                    await page.goto(
                        LIVE_URL_TEMPLATE.format(feed_id=feed_id),
                        timeout=30000, wait_until="domcontentloaded")
                except Exception:  # noqa: BLE001
                    pass
                deadline = time.monotonic() + 300.0
                logged = False
                while time.monotonic() < deadline:
                    try:
                        for c in await context.cookies(
                                "https://live.1688.com" if False else
                                ["https://live.1688.com", "https://www.1688.com",
                                 "https://login.1688.com"]):
                            if c.get("name") == "unb" and c.get("value"):
                                logged = True
                                break
                    except Exception:  # noqa: BLE001
                        pass
                    if logged:
                        break
                    await asyncio.sleep(2)
                if logged:
                    await self._emit_system_status(
                        room_id, "1688 登录成功——开始监听")
                    logger.info(f"[1688] room {room_id} login ok (unb cookie)")
                else:
                    logger.warning(f"[1688] room {room_id} login window timeout")
            finally:
                await context.close()

    # ---- 响应拦截 → 契约映射 ----

    async def _on_response(self, room_id: str, response) -> None:
        url = response.url
        if PULL_API_ANCHOR not in url:
            return
        try:
            body = await response.text()
            s, e = body.find("("), body.rfind(")")
            if s == -1 or e == -1:
                return
            result = json.loads(body[s + 1 : e])
        except Exception:  # noqa: BLE001
            return
        tlist = (result.get("data") or {}).get("timestampList") or []
        ts = int(time.time())
        for td in tlist:
            b64 = td.get("data", "")
            if not b64:
                continue
            try:
                for obj in parse_base64_mixed_message(b64):
                    if not isinstance(obj, dict):
                        continue
                    mapped = self._map_message(room_id, obj,
                                               self.next_seq(room_id), ts)
                    if mapped:
                        await self._emit_message(self._envelope(room_id, mapped))
            except Exception as e:  # noqa: BLE001
                logger.debug(f"[1688] room {room_id} msg parse error: {e}")

    @staticmethod
    def _envelope(room_id: str, mapped: dict) -> dict:
        """契约信封组装（全键；platform=1688 如实标注）"""
        return {
            "contract_version": "1.0.0",
            "category": mapped["category"],
            "type": mapped["type"],
            "platform": "1688",
            "room_id": room_id,
            "seq": mapped["seq"],
            "timestamp": mapped["timestamp"],
            "engine": "page:1688",
            "protocol_version": PROTOCOL_VERSION,
            "payload": mapped["payload"],
        }

    # ---- 上游消息 → 契约 v1（显式清单，与 test_1688_protocol.py 对齐）----

    @staticmethod
    def _map_message(room_id: str, obj: Dict[str, Any], seq: int,
                     ts: int) -> Optional[Dict[str, Any]]:
        sub_type = obj.get("subType")
        if sub_type == 10001:
            content = obj.get("content", obj.get("text", ""))
            if not content:
                return None
            return {"category": "business", "type": "DANMU", "seq": seq,
                    "timestamp": ts,
                    "payload": {"type": "DANMU", "user_name": obj.get("nick", ""),
                                "content": content,
                                "user_id": str(obj.get("userid", obj.get("userId", "")))}}
        if sub_type == 10002:
            gift_name = obj.get("giftName", obj.get("itemName", ""))
            if not gift_name:
                return None
            return {"category": "business", "type": "GIFT", "seq": seq, "timestamp": ts,
                    "payload": {"type": "GIFT", "user_name": obj.get("nick", ""),
                                "gift_name": gift_name,
                                "gift_count": obj.get("count", obj.get("num", 1))}}
        if "viewCountFormat" in obj or "pageViewCount" in obj or "totalCount" in obj:
            current = obj.get("onlineCount", obj.get("current_viewers", 0))
            total = obj.get("totalCount", obj.get("total_viewers", 0))
            return {"category": "business", "type": "ROOM_STATS", "seq": seq,
                    "timestamp": ts,
                    "payload": {"type": "ROOM_STATS", "viewer_count": current,
                                "total_view_count": total}}
        if "nick" in obj and "flowSourceText" in obj:
            identify = obj.get("identify") or {}
            fan_level = identify.get("fanLevel", 0)
            if isinstance(fan_level, str) and fan_level.isdigit():
                fan_level = int(fan_level)
            elif not isinstance(fan_level, int):
                fan_level = 0
            return {"category": "business", "type": "ENTER_ROOM", "seq": seq,
                    "timestamp": ts,
                    "payload": {"type": "ENTER_ROOM", "user_name": obj.get("nick", ""),
                                "user_id": str(obj.get("userid", obj.get("userId", ""))),
                                "fan_level": fan_level}}
        if "value" in obj and isinstance(obj.get("value"), dict) and "dig" in obj["value"]:
            return {"category": "business", "type": "LIKE", "seq": seq, "timestamp": ts,
                    "payload": {"type": "LIKE", "count": obj["value"].get("dig", 1)}}
        logger.debug(f"[1688] room {room_id} unmapped keys={sorted(obj)[:6]}")
        return None

    # ---- 系统消息 ----

    async def _emit_system_status(self, room_id: str, detail: str) -> None:
        from danmaku_listener.contract import Category, SystemType
        from danmaku_listener.contract.models import Envelope, UnifiedMessage

        msg = UnifiedMessage(
            envelope=Envelope(
                category=Category.SYSTEM, type=SystemType.ENGINE_STATUS.value,
                platform=self.platform, room_id=room_id, seq=self.next_seq(room_id),
                timestamp=int(time.time()), engine=self.engine_id,
            ),
            payload={"type": "ENGINE_STATUS", "engine": self.engine_id, "detail": detail},
        )
        await self._emit_message(msg.to_wire())

    async def _emit_route_failed(self, room_id: str, reason_code: str,
                                 fix_hint: str, docs_anchor: str) -> None:
        from danmaku_listener.contract import Category, SystemType
        from danmaku_listener.contract.models import (
            Envelope,
            FailureInfo,
            RouteFailedPayload,
            UnifiedMessage,
        )

        msg = UnifiedMessage(
            envelope=Envelope(
                category=Category.SYSTEM, type=SystemType.ROUTE_FAILED.value,
                platform=self.platform, room_id=room_id, seq=self.next_seq(room_id),
                timestamp=int(time.time()), engine=self.engine_id,
            ),
            payload=RouteFailedPayload(
                failure=FailureInfo(reason_code=reason_code, fix_hint=fix_hint,
                                    docs_anchor=docs_anchor)),
        )
        await self._emit_message(msg.to_wire())
