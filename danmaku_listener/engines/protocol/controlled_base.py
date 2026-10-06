"""受控页面引擎公共基类（2026-09-30 深度重构抽取）

五个受控页面引擎（1688/小红书/京东/拼多多/视频号）在以下维度是纯机械
重复（实测通过的代码原样抽取，无逻辑变化）：

- 房间任务管理：start/stop/restart + _room_tasks/_stop_flags
- persistent context 启动：固定反检测参数/UA/viewport，profile 目录约定
- 契约信封组装：_envelope（platform/engine_id/protocol_version 参数化）
- 系统消息：_emit_system_status / _emit_route_failed（五份完全相同实现）
- WS 帧拦截接线：page.on("websocket") → framereceived → 异步分发
- HTTP 响应拦截接线：page.on("response") → 异步分发
- 业务帧静默计时：_last_frame_box（framereceived/响应到达时刷新）

登录闭环**不在基类**：视频号是单会话长跑（登录态不静置恢复）、1688/拼多多
是 cookie 判定+双 context、小红书/京东游客即可——各引擎保留自己的实现，
基类只提供 _launch 与钩子位。
"""

import asyncio
import time
from typing import Dict, Iterable

from loguru import logger

from danmaku_listener.contract import Category, SystemType
from danmaku_listener.contract.models import (
    Envelope,
    FailureInfo,
    RouteFailedPayload,
    UnifiedMessage,
)
from danmaku_listener.engines.base import BaseEngine
from danmaku_listener.engines import login_gate as LOGIN_GATE

#: 统一反检测/稳定化启动参数（五引擎实测一致）
COMMON_LAUNCH_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--disable-setuid-sandbox",
    "--hide-crash-restore-bubble",
]

COMMON_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
             "AppleWebKit/537.36 (KHTML, like Gecko) "
             "Chrome/131.0.0.0 Safari/537.36")  # 126→131（2026-10-03 阿里登录页风控对旧 UA 敏感）


class ControlledPageEngine(BaseEngine):
    """受控页面引擎公共基类"""

    platform: str = ""
    profile_name: str = ""       # cookie/<profile_name> 目录名
    protocol_version: str = "1.0"

    def __init__(self, state_store=None, cookie_dir: str = "./cookie"):
        super().__init__(state_store=state_store)
        self._cookie_dir = cookie_dir
        self._room_tasks: Dict[str, asyncio.Task] = {}
        self._stop_flags: Dict[str, bool] = {}
        self._last_frame_box: Dict[str, Dict[str, float]] = {}
        # ---- 登录闭环状态（2026-10-06 平台登录计划；jd/xiaohongshu 首用）----
        # per-profile 并发协调（Eng F1）：persistent context 单实例串行化
        self._profile_lock = asyncio.Lock()
        # 每房间超时预算（首登/重登共用；内存态、重启清零——CEO F5）
        self._login_budgets: Dict[str, int] = {}
        # 预算耗尽锁存提示键（锁存粒度=room_id，Eng F8）
        self._budget_warned: set = set()

    # ---- 登录闭环（2026-10-06 平台登录计划）----

    class NeedLoginVisible(Exception):
        """需要可见窗口登录（内部信号——对齐 live1688 撕裂会话时序）"""

        def __init__(self, goto_url: str):
            super().__init__(goto_url)
            self.goto_url = goto_url

    def _consume_login_budget(self, room_id: str) -> bool:
        """扣减登录窗口预算；未尽返回 True（弹出前扣减——taobao 同款语义）"""
        budget = self._login_budgets.get(room_id, LOGIN_GATE.LOGIN_BUDGET)
        if budget <= 0:
            return False
        self._login_budgets[room_id] = budget - 1
        return True

    async def _wait_login_visible(
        self, room_id: str, goto_url: str, cookie_names: Iterable[str], *,
        clock=None, sleep_fn=None, cookies_fn=None,
        poll_interval: float = LOGIN_GATE.LOGIN_POLL_INTERVAL,
    ) -> str:
        """可见窗口登录（≤300s）：打开页面，用户登录（判定 cookie 出现）→ 入 profile。

        Returns:
            "logged_in" | "timeout" | "window_closed"（Eng F4）| "stopped"（Eng F3）

        Eng F6 seam：clock/sleep_fn/cookies_fn 可注入（单测不起真实浏览器）。
        """
        from playwright.async_api import async_playwright

        clock = clock or time.monotonic
        sleep_fn = sleep_fn or asyncio.sleep
        logged = False
        async with self._profile_lock:
            async with async_playwright() as pw:
                try:
                    context = await self._launch(pw, headless=False)
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"[{self.platform}] room {room_id} login window "
                                   f"launch failed: {type(e).__name__}: {str(e)[:80]}")
                    return "window_closed"
                try:
                    page = context.pages[0] if context.pages else await context.new_page()
                    try:
                        await page.goto(goto_url, timeout=30000,
                                        wait_until="domcontentloaded")
                    except Exception as e:  # noqa: BLE001
                        logger.debug(f"[{self.platform}] room {room_id} login page "
                                     f"goto warning: {e}")

                    async def _cookies():
                        # Eng F6 seam：cookie 读取可注入（同步 list 或 coroutine 均兼容）
                        if cookies_fn is not None:
                            r = cookies_fn()
                            return await r if asyncio.iscoroutine(r) else r
                        return await context.cookies()

                    try:
                        baseline_names = {c.get("name") for c in await _cookies() or []}
                    except Exception as e:  # noqa: BLE001
                        logger.info(f"[{self.platform}] room {room_id} login window "
                                    f"closed before baseline ({type(e).__name__})")
                        return "window_closed"
                    deadline = clock() + LOGIN_GATE.LOGIN_WAIT_TIMEOUT
                    while clock() < deadline:
                        if self._stopped(room_id):  # Eng F3：stop 立即中断
                            logger.info(f"[{self.platform}] room {room_id} "
                                        f"login wait stopped by user")
                            return "stopped"
                        try:
                            cookies = await _cookies() or []
                        except Exception as e:  # noqa: BLE001
                            logger.info(f"[{self.platform}] room {room_id} login window "
                                        f"closed by user ({type(e).__name__})")
                            return "window_closed"
                        if LOGIN_GATE.has_login_cookie(cookies, cookie_names):
                            logged = True
                            names = {c.get("name") for c in cookies}
                            new_names = sorted(n for n in names
                                               if n and n not in baseline_names)
                            hit = next((c for c in cookies
                                        if c.get("name") in cookie_names
                                        and (c.get("value") or "").strip()), None)
                            logger.info(
                                f"[{self.platform}] room {room_id} login ok: "
                                f"new_cookies={new_names} "
                                f"hit_mask={LOGIN_GATE.mask_cookie((hit or {}).get('value', ''))}")
                            return "logged_in"
                        await sleep_fn(poll_interval)
                    logger.info(f"[{self.platform}] room {room_id} login wait timeout "
                                f"({LOGIN_GATE.LOGIN_WAIT_TIMEOUT:.0f}s)")
                    return "timeout"
                finally:
                    try:
                        await context.close()
                    except Exception:  # noqa: BLE001  窗口已被用户关闭
                        pass
        return "logged_in" if logged else "timeout"

    @property
    def engine_id(self) -> str:
        return f"page:{self.platform}"

    # ---- 房间任务管理（契约 I：start/stop/restart） ----

    async def start(self, room_id: str) -> None:
        if room_id in self._room_tasks and not self._room_tasks[room_id].done():
            return
        self._stop_flags[room_id] = False
        self._room_tasks[room_id] = asyncio.create_task(
            self._run_room(room_id), name=f"{self.platform}-room-{room_id}")

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

    def _stopped(self, room_id: str) -> bool:
        return self._stop_flags.get(room_id, False)

    # ---- persistent context ----

    def _profile_dir(self) -> str:
        import os

        d = f"{self._cookie_dir}/{self.profile_name}"
        os.makedirs(d, exist_ok=True)
        return d

    async def _launch(self, pw, headless: bool = True):
        """persistent context 启动（统一反检测参数；子类可用 _launch_args 扩展）"""
        return await pw.chromium.launch_persistent_context(
            self._profile_dir(),
            headless=headless,
            user_agent=self._user_agent(),
            viewport={"width": 1280, "height": 800},
            args=COMMON_LAUNCH_ARGS + self._launch_args(),
        )

    def _user_agent(self) -> str:
        return COMMON_UA

    def _launch_args(self) -> list:
        return []

    # ---- 拦截接线 ----

    def _wire_ws_intercept(self, room_id: str, page) -> None:
        """WS framereceived → 子类 _on_ws_frame（异步分发；帧到达刷新静默计时）"""
        self._touch_frame(room_id)

        async def on_frame(payload) -> None:
            try:
                await self._on_ws_frame(room_id, payload)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                logger.debug(f"[{self.platform}] room {room_id} frame parse error: {e}")

        def on_websocket(ws) -> None:
            logger.debug(f"[{self.platform}] room {room_id} ws open: "
                         f"{str(ws.url)[:80]}")
            ws.on("framereceived",
                  lambda p: asyncio.create_task(on_frame(p)))

        page.on("websocket", on_websocket)

    def _wire_http_intercept(self, room_id: str, page) -> None:
        """HTTP 响应 → 子类 _on_http_response（异步分发）"""

        async def on_response(response) -> None:
            try:
                await self._on_http_response(room_id, response)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                logger.debug(f"[{self.platform}] room {room_id} response "
                             f"parse error: {e}")

        page.on("response", lambda r: asyncio.create_task(on_response(r)))

    def _touch_frame(self, room_id: str) -> None:
        """业务帧到达（静默计时刷新）——子类在确认业务帧后调用"""
        self._last_frame_box.setdefault(room_id, {"t": time.monotonic()})["t"] = \
            time.monotonic()

    def _frame_silent(self, room_id: str, timeout: float) -> bool:
        """业务帧静默超时判定"""
        box = self._last_frame_box.setdefault(room_id, {"t": time.monotonic()})
        return time.monotonic() - box["t"] > timeout

    # 子类钩子（默认空实现）
    async def _on_ws_frame(self, room_id: str, payload) -> None:
        pass

    async def _on_http_response(self, room_id: str, response) -> None:
        pass

    # ---- 契约信封与系统消息（五引擎原样统一） ----

    def _envelope(self, room_id: str, mapped: dict) -> dict:
        """契约信封组装（全键；platform/engine/protocol_version 参数化）"""
        return {
            "contract_version": "1.0.0",
            "category": mapped["category"],
            "type": mapped["type"],
            "platform": self.platform,
            "room_id": room_id,
            "seq": mapped["seq"],
            "timestamp": mapped["timestamp"],
            "engine": self.engine_id,
            "protocol_version": self.protocol_version,
            "payload": mapped["payload"],
        }

    async def _emit_system_status(self, room_id: str, detail: str) -> None:
        msg = UnifiedMessage(
            envelope=Envelope(
                category=Category.SYSTEM, type=SystemType.ENGINE_STATUS.value,
                platform=self.platform, room_id=room_id, seq=self.next_seq(room_id),
                timestamp=int(time.time()), engine=self.engine_id,
            ),
            payload={"type": "ENGINE_STATUS", "engine": self.engine_id,
                     "detail": detail},
        )
        await self._emit_message(msg.to_wire())

    async def _emit_route_failed(self, room_id: str, reason_code: str,
                                 fix_hint: str, docs_anchor: str) -> None:
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
