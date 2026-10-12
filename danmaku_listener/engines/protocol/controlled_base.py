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
from typing import Any, Dict, Iterable, Optional

from loguru import logger

from danmaku_listener.contract import Category, SystemType
from danmaku_listener.contract.models import (
    Envelope,
    FailureInfo,
    RouteFailedPayload,
    UnifiedMessage,
)
from danmaku_listener.engines import login_state_store
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

#: 无感模式后台发送参数（2026-10-10 探针实证：登录态 + headless=new 实发 SENT
#: ——旧"headless 提交被吞"实为游客态混杂因素；--headless=new 完整 Blink 指纹，
#: douyin CEO-F5 同款形态；--mute-audio 静音后台直播流）
SEND_BG_EXTRA_ARGS = ["--headless=new", "--mute-audio"]


class ControlledPageEngine(BaseEngine):
    """受控页面引擎公共基类"""

    platform: str = ""
    profile_name: str = ""       # cookie/<profile_name> 目录名
    protocol_version: str = "1.0"

    # ---- F2 发送/监听 profile 互斥（2026-10-10 xhs 移植 jd 时上收基类）----
    # 同 profile 双 persistent context 必然 TargetClosedError（Windows
    # ProcessSingleton）——发送前关停监听 context，监听经 _send_active 门让位
    _send_active: bool = False
    _listen_ctx: Any = None

    async def _close_listen_ctx(self) -> None:
        """F2：发送前关停监听 context（同 profile 单实例——TargetClosedError 实证）"""
        ctx = self._listen_ctx
        self._listen_ctx = None
        if ctx is not None:
            try:
                await ctx.close()
                logger.info(f"[{self.platform}] 发送前关停监听 context（F2 profile 单实例让位）")
            except Exception:  # noqa: BLE001  已死 context——忽略
                pass

    async def _ctx_has_login(self, context) -> bool:
        """context cookie 登录判定（各平台登录名——LOGIN_COOKIE_NAMES[self.platform]）"""
        try:
            cookies = await context.cookies()
        except Exception:  # noqa: BLE001
            return False
        return LOGIN_GATE.has_login_cookie(
            cookies, LOGIN_GATE.LOGIN_COOKIE_NAMES.get(self.platform, ()))

    async def _wait_login_interactive(self, context, timeout_s: float) -> bool:
        """可见窗登录等待（F1 快照 + 关窗即止）。

        每 2s 轮询登录 cookie：出现→快照→10s 安全验证宽限→True；关窗/超时→
        False。关窗竞态（登录落地后 2s 内手动关窗）由调用方以 profile 复检
        兜底——登录 cookie 随浏览器干净关闭落盘，重开一次会话即可复检。
        """
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            await asyncio.sleep(2)
            try:
                pages = context.pages
                if not pages or await pages[0].is_closed():
                    logger.info(f"[{self.platform}] 登录窗已关闭——结束等待")
                    break
            except Exception:  # noqa: BLE001  context 已死
                break
            if await self._ctx_has_login(context):
                await self._snapshot_login_state(context)  # F1：登录落地立即快照
                await asyncio.sleep(10)  # 安全验证宽限（046c3e0 同源教训）
                return True
        return False

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
                            # F1：登录落地立即快照（保窗期硬杀也保得住登录态）
                            await self._snapshot_login_state(context)
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
                            # 登录后保窗宽限（2026-10-09 小红书验收用户实证：登录落地
                            # 即关窗杀安全验证——淘宝/抖音同款缺陷第四例，共享函数一处
                            # 修复覆盖 xiaohongshu/jd/1688 全部受控页面平台）
                            grace_deadline = clock() + LOGIN_GATE.LOGIN_POST_GRACE_S
                            while clock() < grace_deadline:
                                if self._stopped(room_id):
                                    logger.info(f"[{self.platform}] room {room_id} "
                                                f"login grace stopped by user")
                                    return "stopped"
                                try:
                                    cookies = await _cookies() or []
                                except Exception as e:  # noqa: BLE001
                                    logger.info(f"[{self.platform}] room {room_id} "
                                                f"post-login window closed during grace")
                                    return "logged_in"
                                if not LOGIN_GATE.has_login_cookie(cookies, cookie_names):
                                    break  # 会话被服务端作废——降级游客继续
                                await sleep_fn(poll_interval)
                            logger.info(f"[{self.platform}] room {room_id} "
                                        f"login grace ended ({LOGIN_GATE.LOGIN_POST_GRACE_S:.0f}s)")
                            await self._snapshot_login_state(context)  # F1：宽限期后补快照（令牌可能已刷新）
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

    def _login_state_path(self) -> str:
        """登录态快照路径（F1——2026-10-10 xhs profile 重测裁定）"""
        return f"{self._cookie_dir}/{self.profile_name}_login_state.json"

    async def _snapshot_login_state(self, context) -> bool:
        """登录 cookie 出现瞬间快照 storage_state 落盘（F1——共享实现见
        engines/login_state_store.py，背景与机制注释在彼处）"""
        return await login_state_store.snapshot_login_state(
            context, names=LOGIN_GATE.LOGIN_COOKIE_NAMES.get(self.platform, ()),
            path=self._login_state_path(), label=self.platform)

    async def _restore_login_state(self, context) -> bool:
        """profile 丢登录 → 从快照恢复（F1 共享实现）"""
        return await login_state_store.restore_login_state(
            context, names=LOGIN_GATE.LOGIN_COOKIE_NAMES.get(self.platform, ()),
            path=self._login_state_path(), label=self.platform)

    async def _launch(self, pw, headless: bool = True,
                      extra_args: Optional[list] = None):
        """persistent context 启动（统一反检测参数；子类可用 _launch_args 扩展）"""
        context = await pw.chromium.launch_persistent_context(
            self._profile_dir(),
            headless=headless,
            user_agent=self._user_agent(),
            viewport={"width": 1280, "height": 800},
            args=COMMON_LAUNCH_ARGS + self._launch_args() + (extra_args or []),
        )
        await self._restore_login_state(context)  # F1：profile 丢登录时快照自愈
        return context

    def _user_agent(self) -> str:
        return COMMON_UA

    def _launch_args(self) -> list:
        return []

    # ---- AutoDanmu send 钩子（E5/S4-1/F5——引擎唯一新增出口，异常边界隔离） ----

    #: 发送 UI 选择器（子类按 M0 探针结论覆写；None=探针未完成→ROUTE_UNVERIFIED）
    SEND_INPUT_SELECTORS: list = []
    SEND_BUTTON_SELECTORS: list = []

    def _send_room_url(self, room_id: str) -> str:
        """发送导航 URL（子类实现：由房间号重建直播间 URL）"""
        raise NotImplementedError

    async def _send_navigate(self, page, room_id: str) -> None:
        """发送导航扩展位（默认 no-op；视频号等后台型平台覆写为进入直播间面板）"""
        return None

    async def send_danmu(self, room_id: str, content: str):
        """在引擎持有的 profile 上发送弹幕（E5：与监听/重登共用 per-profile Lock 串行）

        瞬态 context（与登录/凭证提取同款模式）→ 导航直播间 → DOM 发送 → 回显判定（F5）。
        锁 acquire 带超时（S4-1：监听重登长动作不饿死发送，超时回执 busy）。
        """
        from danmaku_listener.contract.models import SendRejectReason, SendStatus
        from danmaku_listener.senders.base import SendResult

        if not self.SEND_INPUT_SELECTORS:
            return SendResult(SendStatus.FAILED, SendRejectReason.ROUTE_UNVERIFIED.value,
                              fix_hint="该平台发送选择器未经 M0 探针验证——先跑 tools/send_probes/",
                              docs_anchor="docs/testing/m0-send-probe-cards.md")
        try:
            await asyncio.wait_for(self._profile_lock.acquire(),
                                   timeout=3.0)  # S4-1/R14
        except asyncio.TimeoutError:
            return SendResult(SendStatus.FAILED, SendRejectReason.PLATFORM_REJECTED.value,
                              fix_hint="profile 锁被监听操作占用——稍后重试（busy）")
        try:
            from playwright.async_api import async_playwright

            url = self._send_room_url(room_id)
            async with async_playwright() as pw:
                context = await self._launch(pw, headless=True)
                try:
                    page = context.pages[0] if context.pages else await context.new_page()
                    try:
                        await page.goto(url, timeout=45000, wait_until="domcontentloaded")
                    except Exception as e:  # noqa: BLE001
                        return SendResult(SendStatus.UNKNOWN, SendRejectReason.SEND_TIMEOUT.value,
                                          detail=f"goto: {type(e).__name__}")
                    await asyncio.sleep(3)
                    try:
                        await self._send_navigate(page, room_id)  # 后台型平台扩展位（视频号）
                        await asyncio.sleep(1)
                    except Exception as e:  # noqa: BLE001
                        logger.debug(f"[{self.platform}] send navigate warning: {e}")
                    # 输入框发现（发现模式：候选序+可见性）
                    input_sel, btn_sel = None, None
                    for sel in self.SEND_INPUT_SELECTORS:
                        try:
                            loc = page.locator(sel).first
                            if await loc.count() > 0 and await loc.is_visible():
                                input_sel = sel; break
                        except Exception:  # noqa: BLE001
                            continue
                    if input_sel is None:
                        return SendResult(SendStatus.FAILED, SendRejectReason.PLATFORM_REJECTED.value,
                                          fix_hint="未发现发送输入框（页面结构变更/未登录）——重跑 M0 探针")
                    for sel in self.SEND_BUTTON_SELECTORS:
                        try:
                            if await page.locator(sel).first.count() > 0:
                                btn_sel = sel; break
                        except Exception:  # noqa: BLE001
                            continue
                    await page.locator(input_sel).first.fill(content)
                    if btn_sel:
                        await page.locator(btn_sel).first.click()
                    else:
                        await page.locator(input_sel).first.press("Enter")
                    await asyncio.sleep(2)
                    # F5 判定：回显于页面聊天流=SUCCESS；风控信号=FAIL；否则 UNKNOWN
                    echo_deadline = time.monotonic() + 8  # 1688 实证：聊天列表渲染延迟可达数秒
                    sent_echo = False
                    while time.monotonic() < echo_deadline:
                        if content in await page.content():
                            sent_echo = True; break
                        await asyncio.sleep(1.5)
                    if sent_echo:
                        return SendResult(SendStatus.SENT, sent_at=int(time.time()))
                    for sel in ("text=禁言", "text=验证码", "text=操作过于频繁"):
                        try:
                            if await page.locator(sel).first.count() > 0 and await page.locator(sel).first.is_visible():
                                return SendResult(SendStatus.FAILED, SendRejectReason.PLATFORM_REJECTED.value,
                                                  fix_hint=f"平台风控信号：{sel}", detail=sel)
                        except Exception:  # noqa: BLE001
                            continue
                    return SendResult(SendStatus.UNKNOWN, SendRejectReason.SEND_TIMEOUT.value,
                                      detail="无回显无显式错误（虚拟列表/慢渲染）")
                finally:
                    try:
                        await context.close()
                    except Exception:  # noqa: BLE001
                        pass
        except Exception as e:  # noqa: BLE001  E5 隔离：异常不出引擎边界
            logger.warning(f"[{self.platform}] send_danmu error: {type(e).__name__}: {str(e)[:100]}")
            return SendResult(SendStatus.FAILED, SendRejectReason.PLATFORM_REJECTED.value,
                              detail=f"{type(e).__name__}: {str(e)[:100]}")
        finally:
            self._profile_lock.release()

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
