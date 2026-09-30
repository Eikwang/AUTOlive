"""拼多多直播弹幕引擎（受控页面 WS 帧拦截——页面自持连接，引擎解码下行帧）

技术路线（2026-09-30 调研定型，四平台最后一站）：
拼多多直播间页内 wss 下发**二进制帧**（页面 JS 自行完成握手/入组/心跳/ACK
——上行协议未公开但受控页面无需逆向），引擎拦截 framereceived 解码下行。

下行帧结构（2025 公开协议分析实证，四层）：
1. 16 字节大端固定包头：magic i16 + cmd i16 + ctx i32 + reserve i32 + bodyLen i32
2. TitanPayload protobuf：field1=command(str) field2=protocol(varint)
   field10=body(bytes) field11=extension field14=compress(varint)
3. compress==1 → body gunzip
4. MulticastLite protobuf：field1=bizType field2=groupId field3=msgId
   field4=payload(bytes) field5=needAck
5. payload → UTF-8 → JSON 业务对象：
   message_type / live_msg_id / push_mills / checked_show_id / message_data
   弹幕在 message_data.live_chat_list[]（uid/nickname/chat_message/sub_type…）

登录：调研实证拼多多弹幕需扫码登录 cookie——未登录时页面行为待实测；
风控较严（2023 前"整个圈子没人写出"，页面路线借页面自身会话规避）。
"""

import asyncio
import gzip
import json
import struct
import time
from typing import Any, Dict, List, Optional

from loguru import logger

from danmaku_listener.contract.models import GapReason
from danmaku_listener.engines.base import BaseEngine

PROTOCOL_VERSION = "pdd-1"

SILENCE_TIMEOUT = 120.0  # 业务帧静默阈值（拼多多弹幕可能稀疏）


class PDDParseError(ValueError):
    """房间参数无法解析"""


class NeedLoginVisible(Exception):
    """需要可见窗口登录（内部信号）"""


#: 登录态候选 cookie（2026-09-30 调研实证弹幕需登录；具体 cookie 名待实测——
#: _wait_login_visible 会把登录后新增 cookie 全部打日志，供校准收紧）
LOGIN_COOKIE_CANDIDATES = ("pdd_user_id", "PassId", "PDDAccessToken", "pdd_uid")


def extract_room_id(room_spec: str) -> str:
    """房间参数归一：拼多多直播间链接原样直达 / 纯数字 show_id"""
    spec = room_spec.strip()
    if "pinduoduo.com" in spec or "yangkeduo.com" in spec or "pdd" in spec:
        return spec[:80]  # 链接形态直达（room 标识截断）
    m = __import__("re").match(r"^(\d{5,25})$", spec)
    if m:
        return m.group(1)
    raise PDDParseError(
        f"无法解析拼多多直播间参数: {spec[:60]!r}——请使用直播间分享链接或 show_id 数字")


# ---- protobuf mini reader（无 schema 动态解码，仅 varint/length-delim） ----


class ProtoReader:
    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0
        self.length = len(data)

    def eof(self) -> bool:
        return self.pos >= self.length

    def uint64(self) -> int:
        shift = 0
        value = 0
        while True:
            if self.pos >= self.length:
                raise EOFError("varint EOF")
            b = self.data[self.pos]
            self.pos += 1
            value |= (b & 0x7F) << shift
            if not (b & 0x80):
                return value
            shift += 7
            if shift > 70:
                raise ValueError("varint too long")

    def uint32(self) -> int:
        return self.uint64() & 0xFFFFFFFF

    def bytes_field(self) -> bytes:
        size = self.uint64()
        if self.pos + size > self.length:
            raise EOFError("length-delimited field exceeds buffer")
        value = self.data[self.pos:self.pos + size]
        self.pos += size
        return value

    def string(self) -> str:
        return self.bytes_field().decode("utf-8", errors="replace")

    def skip(self, wire_type: int) -> None:
        if wire_type == 0:
            self.uint64()
        elif wire_type == 1:
            self.pos += 8
        elif wire_type == 2:
            self.pos += self.uint64()
        elif wire_type == 5:
            self.pos += 4
        else:
            raise ValueError(f"unsupported wire type {wire_type}")
        if self.pos > self.length:
            raise EOFError("skip beyond buffer")


def _decode_titan(data: bytes) -> Dict[str, Any]:
    """TitanPayload：field1=command str / field10=body / field14=compress"""
    r = ProtoReader(data)
    out: Dict[str, Any] = {"command": "", "compress": 0, "body": b""}
    while not r.eof():
        try:
            tag = r.uint64()
        except EOFError:
            break
        field_no, wire = tag >> 3, tag & 7
        try:
            if field_no == 1 and wire == 2:
                out["command"] = r.string()
            elif field_no == 10 and wire == 2:
                out["body"] = r.bytes_field()
            elif field_no == 14 and wire == 0:
                out["compress"] = r.uint64()
            else:
                r.skip(wire)
        except EOFError:
            break
    return out


def _decode_multicast(data: bytes) -> Dict[str, Any]:
    """MulticastLite：field1=bizType / field2=groupId / field3=msgId /
    field4=payload / field5=needAck"""
    r = ProtoReader(data)
    out: Dict[str, Any] = {"biz_type": 0, "group_id": "", "msg_id": "",
                           "payload": b""}
    while not r.eof():
        try:
            tag = r.uint64()
        except EOFError:
            break
        field_no, wire = tag >> 3, tag & 7
        try:
            if field_no == 1 and wire == 0:
                out["biz_type"] = r.uint32()
            elif field_no == 2 and wire == 2:
                out["group_id"] = r.string()
            elif field_no == 3 and wire == 2:
                out["msg_id"] = r.string()
            elif field_no == 4 and wire == 2:
                out["payload"] = r.bytes_field()
            else:
                r.skip(wire)
        except EOFError:
            break
    return out


def decode_pdd_frame(raw: Any) -> List[Dict[str, Any]]:
    """wss 二进制帧 → 业务 JSON 对象列表（纯函数，供单测）

    完整四层：16 字节包头 → TitanPayload → (gunzip) → MulticastLite →
    UTF-8 JSON。任何一层失败返回空列表（页内其他 WS 二进制帧自然过滤）。
    """
    if isinstance(raw, str):
        try:
            raw = raw.encode("utf-8")
        except Exception:  # noqa: BLE001
            return []
    if not isinstance(raw, (bytes, bytearray, memoryview)):
        return []
    data = bytes(raw)
    if len(data) < 16:
        return []
    try:
        _magic, _cmd, _ctx, _reserve, _body_len = struct.unpack(">hhiii", data[:16])
    except struct.error:
        return []
    try:
        titan = _decode_titan(data[16:])
        body = titan["body"]
        if titan["compress"] == 1 and body:
            body = gzip.decompress(body)
        if not body:
            return []
        mc = _decode_multicast(body)
        if not mc["payload"]:
            return []
        text = mc["payload"].decode("utf-8", errors="replace")
        parsed = json.loads(text)
    except (OSError, EOFError, ValueError, json.JSONDecodeError):
        # gzip.BadFile 是 OSError 子类；结构不符/JSON 失败 → 非弹幕帧
        return []
    if isinstance(parsed, list):
        return [o for o in parsed if isinstance(o, dict)]
    if isinstance(parsed, dict):
        return [parsed]
    return []


def map_pdd_message(obj: Dict[str, Any], seq: int, ts: int) -> List[Dict[str, Any]]:
    """业务对象 → 契约消息列表（一条业务对象可能携带 list 多条事件）

    2026-09-30 在播房间 450+ 条采样实测 + 用户裁定范围（拼多多无礼物
    功能，业务消息只监听**入场/弹幕/点赞**三种——关注/购买等 SOCIAL
    不映射；ROOM_STATS 统计保留供前端展示）：
    - live_chat → DANMU（message_data.live_chat_list[]，实测命中）
    - live_chat_notice：enter → ENTER_ROOM；favorite/group_open 不 emit
    - live_chat_ext_v2：sub_type 121(thumb_up_chat) → LIKE；其余不 emit
    - live_audience_num / show_thumb_up_count → ROOM_STATS（观看数/点赞总数）
    """
    md = obj.get("message_data") or {}
    out: List[Dict[str, Any]] = []
    m_type = obj.get("message_type")

    if m_type == "live_audience_num":
        num = md.get("live_audience_num")
        if isinstance(num, int):
            out.append({
                "category": "business", "type": "ROOM_STATS", "seq": seq,
                "timestamp": ts,
                "payload": {"type": "ROOM_STATS", "viewer_count": num}})
        return out

    if m_type == "show_thumb_up_count":
        total = md.get("total_count")
        if isinstance(total, int):
            out.append({
                "category": "business", "type": "ROOM_STATS", "seq": seq,
                "timestamp": ts,
                "payload": {"type": "ROOM_STATS", "like_count": total}})
        return out

    if m_type == "live_chat_notice":
        for n in md.get("live_chat_notice_list") or []:
            if not isinstance(n, dict):
                continue
            data = n.get("live_chat_notice_data") or {}
            users = data.get("user_list") or []
            user = users[0] if users and isinstance(users[0], dict) else {}
            if n.get("live_chat_notice_type") == "enter":
                out.append({
                    "category": "business", "type": "ENTER_ROOM", "seq": seq,
                    "timestamp": ts,
                    "payload": {"type": "ENTER_ROOM",
                                "user_name": str(user.get("nickname", "")),
                                "user_id": str(user.get("uid", ""))}})
            # favorite（关注）/ group_open（开团运营位）→ 用户裁定不监听
        return out

    if m_type == "live_chat_ext_v2":
        for n in md.get("live_chat_ext_list") or []:
            if not isinstance(n, dict):
                continue
            # 用户裁定只监听点赞（sub_type 121）；关注 116/购买 120 不映射
            if n.get("sub_type") == 121:
                body = n.get("body") or {}
                out.append({
                    "category": "business", "type": "LIKE", "seq": seq,
                    "timestamp": ts,
                    "payload": {"type": "LIKE",
                                "user_name": str(body.get("title", "")),
                                "count": 1}})
        return out

    # 弹幕（调研形态实测命中）：live_chat_list[] → DANMU
    chat_list = md.get("live_chat_list")
    if isinstance(chat_list, list):
        for chat in chat_list:
            if not isinstance(chat, dict):
                continue
            content = str(chat.get("chat_message") or "").strip()
            if not content:
                continue
            out.append({
                "category": "business", "type": "DANMU", "seq": seq, "timestamp": ts,
                "payload": {"type": "DANMU",
                            "user_name": str(chat.get("nickname", "")),
                            "content": content,
                            "user_id": str(chat.get("uid", ""))}})
    return out


class PDDProtocolEngine(BaseEngine):
    """拼多多直播弹幕引擎（受控页面 WS 帧拦截，独立平台）"""

    platform = "pdd"

    def __init__(self, state_store=None, cookie_dir: str = "./cookie",
                 raw_hook=None, http_hook=None):
        super().__init__(state_store=state_store)
        self._cookie_dir = cookie_dir
        self._room_tasks: Dict[str, asyncio.Task] = {}
        self._stop_flags: Dict[str, bool] = {}
        self._last_frame_box: Dict[str, Dict[str, float]] = {}
        self._raw_hook = raw_hook  # 诊断钩子：解码出的业务对象（含未映射）回调
        self._http_hook = http_hook  # 诊断钩子：页面 HTTP 响应 URL（弹幕通道定位）

    @property
    def engine_id(self) -> str:
        return "page:pdd"

    def _profile_dir(self) -> str:
        import os

        d = f"{self._cookie_dir}/pdd_profile"
        os.makedirs(d, exist_ok=True)
        return d

    def validate_room_id(self, room_id: str) -> None:
        try:
            extract_room_id(room_id)
        except PDDParseError as e:
            raise ValueError(str(e)) from e

    # ---- 公开契约 ----

    async def start(self, room_id: str) -> None:
        if room_id in self._room_tasks and not self._room_tasks[room_id].done():
            return
        self._stop_flags[room_id] = False
        self._room_tasks[room_id] = asyncio.create_task(
            self._run_room(room_id), name=f"pdd-room-{room_id}")

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
        await self.stop(room_id)
        await self.start(room_id)

    # ---- 房间任务主循环 ----

    async def _run_room(self, room_id: str) -> None:
        room_key = extract_room_id(room_id)
        goto_url = (room_id.strip() if room_id.strip().startswith("http")
                    else None)
        logger.info(f"[pdd] room {room_id} connecting (key={room_key})")
        backoff = 15.0
        headless = True
        while not self._stop_flags.get(room_id):
            try:
                if goto_url is None:
                    raise PDDParseError(
                        "拼多多直播间需要页面链接——请使用直播间分享链接"
                        "（网页直播间 URL 形态待实测，纯数字 show_id 无从打开）")
                await self._run_session(room_id, room_key, goto_url, headless)
                backoff = 15.0
            except asyncio.CancelledError:
                raise
            except NeedLoginVisible:
                # 可见窗口登录（阻塞至登录成功/超时）→ 登录后重开正常会话
                if self._stop_flags.get(room_id):
                    return
                await self._wait_login_visible(room_id, goto_url)
                headless = True  # 登录态入 profile，后续恢复无头
                continue
            except PDDParseError as e:
                logger.warning(f"[pdd] room {room_id} {e}")
                await self._emit_route_failed(room_id, "pdd.page.parse_failed",
                                              str(e), "docs/platforms/pdd/runbook.md")
                return
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[pdd] room {room_id} error: {type(e).__name__}: {str(e)[:90]}")
                self._set_status(self.status.__class__.ERROR)
                self.mark_gap_start(room_id)
                gap = self.build_gap_message(room_id, GapReason.NETWORK)
                if gap:
                    await self._emit_system(gap)
                if self._stop_flags.get(room_id):
                    return
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 900.0)

    async def _run_session(self, room_id: str, room_key: str,
                           goto_url: str, headless: bool = True) -> None:
        """单次有界会话：常驻页面 + 二进制帧拦截 + 业务帧静默检测

        登录闭环（调研实证弹幕需登录会话）：无头+未登录 → NeedLoginVisible
        → 可见窗口等扫码登录（persistent profile 持久登录态，后续免登录）。
        """
        from playwright.async_api import async_playwright

        session_start = int(time.time())
        deadline = time.monotonic() + 14400.0
        frame_box = self._last_frame_box.setdefault(room_id, {"t": time.monotonic()})
        frame_box["t"] = time.monotonic()

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

                async def on_frame(ws, payload) -> None:
                    try:
                        await self._on_ws_frame(room_id, payload)
                    except asyncio.CancelledError:
                        raise
                    except Exception as e:  # noqa: BLE001
                        logger.debug(f"[pdd] room {room_id} frame parse error: {e}")

                def on_websocket(ws) -> None:
                    logger.debug(f"[pdd] room {room_id} ws open: {str(ws.url)[:80]}")
                    ws.on(
                        "framereceived",
                        lambda p: asyncio.create_task(on_frame(ws, p)))

                page.on("websocket", on_websocket)

                # HTTP 响应 URL 记录（诊断钩子——普通弹幕不在 titan wss 下行，
                # 定位页面 fetch/XHR 里是否有弹幕接口，1688 同款诊断思路）
                if self._http_hook is not None:
                    http_hook = self._http_hook

                    def on_response(response) -> None:
                        try:
                            http_hook(response.url)
                        except Exception:  # noqa: BLE001
                            pass

                    page.on("response", on_response)

                try:
                    await page.goto(goto_url,
                                    timeout=30000, wait_until="domcontentloaded")
                except Exception as e:  # noqa: BLE001
                    logger.debug(f"[pdd] room {room_id} goto warning: {e}")

                logger.info(f"[pdd] room {room_id} live page ready")

                # 登录闭环：弹幕只在登录会话推送（调研实证）——
                # 无头且未登录 → 弹可见窗口等用户扫码
                if headless and not await self._has_login_cookie(context):
                    logger.info(f"[pdd] room {room_id} not logged in — visible window for login")
                    await self._emit_system_status(
                        room_id, "拼多多需要登录（弹幕仅登录会话推送）——"
                                 "已弹出浏览器，请在页面内登录（扫码/账号）")
                    raise NeedLoginVisible()

                self._set_status(self.status.__class__.RUNNING)

                while time.monotonic() < deadline and not self._stop_flags.get(room_id):
                    await asyncio.sleep(2)
                    if time.monotonic() - frame_box["t"] > SILENCE_TIMEOUT:
                        raise PDDParseError(
                            "pdd.session.silent: 120s 无业务帧"
                            "（可能未开播/已下播/需登录/风控——确认直播中，"
                            "必要时用可见窗口登录后重试）")
            finally:
                await context.close()
        elapsed = int(time.time()) - session_start
        if elapsed >= 14400.0:
            logger.info(f"[pdd] room {room_id} session rebuilt after {elapsed}s")

    # ---- 登录闭环 ----

    @staticmethod
    async def _login_cookie_names(context) -> set:
        try:
            return {c.get("name") for c in await context.cookies()}
        except Exception:  # noqa: BLE001
            return set()

    @classmethod
    async def _has_login_cookie(cls, context) -> bool:
        """登录态判定：候选登录 cookie 任一命中（有值）

        具体登录 cookie 名待实测校准——_wait_login_visible 会把登录后
        新增 cookie 全部打日志，据此收紧候选列表。
        """
        try:
            names = await cls._login_cookie_names(context)
            if not names:
                return False
            for c in await context.cookies():
                if c.get("name") in LOGIN_COOKIE_CANDIDATES and c.get("value"):
                    return True
            return False
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[pdd] login cookie check error: {e}")
            return False

    async def _wait_login_visible(self, room_id: str, goto_url: str) -> None:
        """可见窗口登录：用户手动登录（扫码/账号）→ 登录态入 profile → 返回

        动态 cookie 差异检测：不依赖预设候选——登录后新增的 cookie 名全部
        打日志（真实登录标志可见，供校准 LOGIN_COOKIE_CANDIDATES）。
        """
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
                    await page.goto(goto_url, timeout=30000,
                                    wait_until="domcontentloaded")
                except Exception:  # noqa: BLE001
                    pass
                baseline = await self._login_cookie_names(context)
                logger.info(f"[pdd] room {room_id} login window open "
                            f"(baseline cookies={len(baseline)})")
                deadline = time.monotonic() + 300.0
                logged = False
                while time.monotonic() < deadline:
                    await asyncio.sleep(2)
                    names = await self._login_cookie_names(context)
                    if not logged and names - baseline:
                        # 首批新增 cookie：打日志（真实登录标志）
                        logger.info(f"[pdd] room {room_id} new cookies after "
                                    f"login action: {sorted(names - baseline)}")
                    for c in await context.cookies():
                        if (c.get("name") in LOGIN_COOKIE_CANDIDATES
                                and c.get("value")):
                            logged = True
                            break
                    if logged:
                        break
                if logged:
                    await self._emit_system_status(
                        room_id, "拼多多登录成功——开始监听")
                    logger.info(f"[pdd] room {room_id} login ok")
                else:
                    logger.warning(f"[pdd] room {room_id} login window timeout")
            finally:
                await context.close()

    async def _on_ws_frame(self, room_id: str, payload: Any) -> None:
        """framereceived 事件 → 解码 emit（二进制帧 payload 为 bytes）"""
        raw = payload.get("payload") if isinstance(payload, dict) else payload
        if raw is None:
            return
        last_frame = self._last_frame_box.setdefault(room_id, {"t": time.monotonic()})
        for obj in decode_pdd_frame(raw):
            last_frame["t"] = time.monotonic()
            self.mark_received(room_id, int(time.time()))
            if self._raw_hook is not None:
                try:
                    self._raw_hook(obj)
                except Exception:  # noqa: BLE001
                    pass
            for mapped in map_pdd_message(obj, self.next_seq(room_id),
                                          int(time.time())):
                await self._emit_message(self._envelope(room_id, mapped))

    # ---- 契约信封与系统消息 ----

    def _envelope(self, room_id: str, mapped: dict) -> dict:
        """契约信封组装（全键；platform=pdd 如实标注）"""
        return {
            "contract_version": "1.0.0",
            "category": mapped["category"],
            "type": mapped["type"],
            "platform": self.platform,
            "room_id": room_id,
            "seq": mapped["seq"],
            "timestamp": mapped["timestamp"],
            "engine": self.engine_id,
            "protocol_version": PROTOCOL_VERSION,
            "payload": mapped["payload"],
        }

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
