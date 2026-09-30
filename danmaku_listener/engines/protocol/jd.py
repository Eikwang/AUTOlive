"""京东直播弹幕引擎（受控页面 WS 帧拦截——咚咚 IM 群聊体系）

技术路线（2026-09-30 调研定型，四平台第三站）：
京东直播间页内 WebSocket 下发**明文 JSON**（咚咚 IM 群聊体系，直播间=群）。
wss 直连需 liveauth token（App 端 sign 走 JNI 需 frida）——受控页面路线
让页面自己完成鉴权与订阅，引擎只拦截 framereceived 解析（免签名）。

帧结构（2020 App 端调研数据点，网页端待实测校准）：
- 订阅帧 type=join_live_broadcast（aid=dongdong, appid=jd.mall, groupid=房间号）
- 下行消息 type=chat_group_message，弹幕含 nickName/content 字段
引擎对页内全部 WS 挂 framereceived，宽容解析 + raw_hook 诊断钩子
（京东页面可能有多个 WS——帧形态各异，解析失败自然跳过）。

房间参数：京东直播间链接（页面直达，保留全部风控参数）/ 纯数字。
下播检测：业务帧静默 120s（京东直播弹幕稀疏阈值放宽）。
"""

import asyncio
import json
import re
import time
from typing import Any, Dict, List, Optional

from loguru import logger

from danmaku_listener.contract.models import GapReason
from danmaku_listener.engines.base import BaseEngine

PROTOCOL_VERSION = "jd-1"

SILENCE_TIMEOUT = 120.0  # 业务帧静默阈值（京东直播间弹幕稀疏，阈值放宽）

# 2026-09-30 用户实测发现京东直播独立站（游客可看，页面自建 live-ws4 连接）
LIVE_URL_TEMPLATE = "https://zhibo.jd.com/liveroom?liveId={room_id}"


class JDParseError(ValueError):
    """房间参数无法解析"""


def extract_room_id(room_spec: str) -> str:
    """房间参数归一：zhibo.jd.com/liveroom?liveId= / 各类京东链接 / 纯数字

    2026-09-30 用户实测发现京东直播独立站：zhibo.jd.com/liveroom?liveId=
    ——游客可看、页面自建 wss://live-ws4.jd.com 连接（免 liveauth 签名）。
    链接形态整体直达（保留风控参数），room 标识取 liveId/popId/id。
    """
    spec = room_spec.strip()
    if "jd.com" in spec or "jd.hk" in spec or "3.cn" in spec:
        m = (re.search(r"liveId=(\d+)", spec) or re.search(r"popId=(\d+)", spec)
             or re.search(r"liveid=(\d+)", spec) or re.search(r"[\?&]id=(\d+)", spec))
        return m.group(1) if m else spec[:64]  # 无数字特征：链接截断作标识
    m = re.match(r"^(\d{5,25})$", spec)
    if m:
        return m.group(1)
    raise JDParseError(
        f"无法解析京东直播间参数: {spec[:60]!r}——请使用直播间分享链接或房间数字 ID"
        "（推荐 zhibo.jd.com/liveroom?liveId=xxx）")


JD_MSG_TYPES = ("chat_group_message", "join_live_broadcast", "group_message",
                "live_message")


def _is_jd_business(obj: Dict[str, Any]) -> bool:
    """业务对象特征：type 枚举命中，或弹幕特征组合（nickName+content）"""
    if not isinstance(obj, dict):
        return False
    if obj.get("type") in JD_MSG_TYPES:
        return True
    return bool(obj.get("nickName") and obj.get("content"))


def parse_jd_frame(raw) -> List[Dict[str, Any]]:
    """WS 帧宽容解析 → 消息对象列表（纯函数，供单测）

    咚咚 IM 明文 JSON：弹幕特征在 body 内（nickName/content/type），
    顶层为信封（aid/from/type）。收集顶层 + body + msgs[]，业务特征过滤；
    其他页面 WS 的 JSON 帧自然过滤。实测样本后收敛精确结构。
    """
    if isinstance(raw, bytes):
        try:
            raw = raw.decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            return []
    if not isinstance(raw, str):
        return []
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(data, dict):
        return []

    candidates: List[Dict[str, Any]] = [data]
    body = data.get("body")
    if isinstance(body, dict):
        candidates.append(body)
        # body 内嵌套（咚咚 ext/msg 形态，一层即可）
        candidates.extend(v for v in body.values() if isinstance(v, dict))
    msgs = data.get("msgs")
    if isinstance(msgs, list):
        candidates.extend(m for m in msgs if isinstance(m, dict))
    # 去重保序
    seen_ids = set()
    out = []
    for c in candidates:
        cid = id(c)
        if cid not in seen_ids and _is_jd_business(c):
            seen_ids.add(cid)
            out.append(c)
    return out


def map_jd_message(frame: Dict[str, Any], seq: int, ts: int) -> Optional[Dict[str, Any]]:
    """咚咚 IM 顶层帧 → 契约消息映射（纯函数）；未识别返回 None

    2026-09-30 zhibo.jd.com 实测（58 帧）：顶层 type=chat_group_message/
    get_statistics_result，业务形态在 body.type：
    - join_live_broadcast_summary → ENTER_ROOM（聚合形态"xx等16人来了"）
    - thumbs_up → LIKE（body.thumbs_up_num）
    - get_statistics_result → ROOM_STATS（current_viewer/thumbs_up_num）
    - viewer_buy_product_summary/user_places_order 等运营形态不映射
    - 弹幕：body.type=viewer_send_message（nickName+content，实测命中；游客会话即可收他人弹幕，无需登录）
    """
    top_type = frame.get("type")
    body = frame.get("body") or {}
    body_type = body.get("type")
    user_name = str(body.get("nickName") or body.get("nickname") or "")
    content = str(body.get("content") or "").strip()

    if top_type == "get_statistics_result":
        viewer = body.get("current_viewer")
        if isinstance(viewer, int):
            return {"category": "business", "type": "ROOM_STATS", "seq": seq,
                    "timestamp": ts,
                    "payload": {"type": "ROOM_STATS", "viewer_count": viewer}}
        return None

    if top_type != "chat_group_message":
        return None
    if body_type == "join_live_broadcast_summary" and user_name:
        return {"category": "business", "type": "ENTER_ROOM", "seq": seq,
                "timestamp": ts,
                "payload": {"type": "ENTER_ROOM", "user_name": user_name}}
    if body_type == "viewer_send_message" and content and user_name:
        # 2026-09-30 实测：弹幕=body.type=viewer_send_message（带 body.type，
        # 宽容路径覆盖不到——游客会话即可收其他观众弹幕，无需登录）
        return {"category": "business", "type": "DANMU", "seq": seq,
                "timestamp": ts,
                "payload": {"type": "DANMU", "user_name": user_name,
                            "content": content,
                            "user_id": str(frame.get("from", {}).get("pinmd5", ""))}}
    if body_type == "thumbs_up":
        count = body.get("thumbs_up_num", 1)
        if not isinstance(count, int) or count < 1:
            count = 1
        return {"category": "business", "type": "LIKE", "seq": seq,
                "timestamp": ts,
                "payload": {"type": "LIKE", "count": count}}
    # 弹幕：body.nickName+content（text 类 body.type 待样本，宽容兼容）
    if not body_type and content and user_name:
        return {"category": "business", "type": "DANMU", "seq": seq,
                "timestamp": ts,
                "payload": {"type": "DANMU", "user_name": user_name,
                            "content": content}}
    return None


class JDProtocolEngine(BaseEngine):
    """京东直播弹幕引擎（受控页面 WS 帧拦截，独立平台）"""

    platform = "jd"

    def __init__(self, state_store=None, cookie_dir: str = "./cookie",
                 raw_hook=None):
        super().__init__(state_store=state_store)
        self._cookie_dir = cookie_dir
        self._room_tasks: Dict[str, asyncio.Task] = {}
        self._stop_flags: Dict[str, bool] = {}
        self._last_frame_box: Dict[str, Dict[str, float]] = {}
        self._raw_hook = raw_hook  # 诊断钩子：全部 JSON 帧（含未映射）回调

    @property
    def engine_id(self) -> str:
        return "page:jd"

    def _profile_dir(self) -> str:
        import os

        d = f"{self._cookie_dir}/jd_profile"
        os.makedirs(d, exist_ok=True)
        return d

    def validate_room_id(self, room_id: str) -> None:
        try:
            extract_room_id(room_id)
        except JDParseError as e:
            raise ValueError(str(e)) from e

    # ---- 公开契约 ----

    async def start(self, room_id: str) -> None:
        if room_id in self._room_tasks and not self._room_tasks[room_id].done():
            return
        self._stop_flags[room_id] = False
        self._room_tasks[room_id] = asyncio.create_task(
            self._run_room(room_id), name=f"jd-room-{room_id}")

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
        # 链接形态直达（保留风控参数）；纯数字时用户需提供直播间页链接——
        # 京东直播间页 URL 形态多样且需实测，纯数字仅作标识（runbook 说明）
        # 链接形态整体直达（保留风控参数）；纯数字 liveId 拼独立站模板
        goto_url = (room_id.strip() if room_id.strip().startswith("http")
                    else LIVE_URL_TEMPLATE.format(room_id=room_key))
        logger.info(f"[jd] room {room_id} connecting (key={room_key})")
        backoff = 15.0
        while not self._stop_flags.get(room_id):
            try:
                if goto_url is None:
                    raise JDParseError(
                        "京东直播间需要页面链接——请使用直播间分享链接"
                        "（京东直播间页 URL 形态多样，纯数字 ID 无从打开）")
                await self._run_session(room_id, room_key, goto_url)
                backoff = 15.0
            except asyncio.CancelledError:
                raise
            except JDParseError as e:
                logger.warning(f"[jd] room {room_id} {e}")
                await self._emit_route_failed(room_id, "jd.page.parse_failed",
                                              str(e), "docs/platforms/jd/runbook.md")
                return  # 参数问题：重试无意义，等用户改参数
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[jd] room {room_id} error: {type(e).__name__}: {str(e)[:90]}")
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
                           goto_url: str) -> None:
        """单次有界会话：常驻页面 + WS 帧拦截 + 业务帧静默检测"""
        from playwright.async_api import async_playwright

        session_start = int(time.time())
        deadline = time.monotonic() + 14400.0
        frame_box = self._last_frame_box.setdefault(room_id, {"t": time.monotonic()})
        frame_box["t"] = time.monotonic()

        async with async_playwright() as pw:
            context = await pw.chromium.launch_persistent_context(
                self._profile_dir(),
                headless=True,
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
                        logger.debug(f"[jd] room {room_id} frame parse error: {e}")

                def on_websocket(ws) -> None:
                    logger.debug(f"[jd] room {room_id} ws open: {str(ws.url)[:80]}")
                    ws.on(
                        "framereceived",
                        lambda p: asyncio.create_task(on_frame(ws, p)))

                page.on("websocket", on_websocket)

                try:
                    await page.goto(goto_url,
                                    timeout=30000, wait_until="domcontentloaded")
                except Exception as e:  # noqa: BLE001
                    logger.debug(f"[jd] room {room_id} goto warning: {e}")

                logger.info(f"[jd] room {room_id} live page ready")
                self._set_status(self.status.__class__.RUNNING)

                while time.monotonic() < deadline and not self._stop_flags.get(room_id):
                    await asyncio.sleep(2)
                    if time.monotonic() - frame_box["t"] > SILENCE_TIMEOUT:
                        raise JDParseError(
                            "jd.session.silent: 120s 无业务帧"
                            "（可能未开播/已下播/风控——确认直播中）")
            finally:
                await context.close()
        elapsed = int(time.time()) - session_start
        if elapsed >= 14400.0:
            logger.info(f"[jd] room {room_id} session rebuilt after {elapsed}s")

    async def _on_ws_frame(self, room_id: str, payload: Any) -> None:
        """framereceived 事件 → 解析 emit（payload 为 {"payload": str|bytes}）"""
        raw = payload.get("payload") if isinstance(payload, dict) else payload
        if raw is None:
            return
        last_frame = self._last_frame_box.setdefault(room_id, {"t": time.monotonic()})
        for obj in parse_jd_frame(raw):
            last_frame["t"] = time.monotonic()
            self.mark_received(room_id, int(time.time()))
            if self._raw_hook is not None:
                try:
                    self._raw_hook(obj)
                except Exception:  # noqa: BLE001
                    pass
            mapped = map_jd_message(obj, self.next_seq(room_id), int(time.time()))
            if mapped:
                await self._emit_message(self._envelope(room_id, mapped))

    # ---- 契约信封与系统消息 ----

    def _envelope(self, room_id: str, mapped: dict) -> dict:
        """契约信封组装（全键；platform=jd 如实标注）"""
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
