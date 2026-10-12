"""淘宝直播弹幕 Web 协议引擎（mtop 双通道，TaobaoLiveWebFetcher 参考实现对照）

协议（2026-09-29 校准，参考实现 537 行逐段对照）：
- 凭证：Playwright 打开 tbzb.taobao.com/live?liveId={id}（游客可用）→
  拦截 iliad 请求拿 topic → 取 _m_h5_tk/_m_h5_tk_enc cookies
- 主通道：mtop.taobao.powermsg.h5.msg.pullnativemsg（appKey 12574478，10s 轮询）
  响应 data.timestampList[].data = base64(protobuf+JSON 混合) → 扫描提取 JSON 对象，
  按字段特征分发：viewCountFormat=统计 / nick+flowSourceText=进场 / value.dig=点赞 /
  subType==10001 聊天 / subType==10002 礼物 / status==3 下播
- 评论通道：mtop.taobao.iliad.comment.query.latest（appKey 34675810，服务端 delay 轮询）
  data.comments[]（publisherNick/publisherId/content）
- 30s 无消息 → 凭证过期/风控 → 整轮重建（重取凭证，契约 O）

映射：ChatMessage→DANMU、Member→ENTER_ROOM、Gift→GIFT、Like→LIKE、
统计→ROOM_STATS、Control(status==3)→LIVE_STATUS_CHANGE(live=false)。
"""

import asyncio
import json
import re
import time

import random
import requests
from typing import Any, Dict, Optional

import websockets  # noqa: F401  保留与其它协议引擎一致的导入形态
from loguru import logger

from danmaku_listener.contract.models import GapReason
from danmaku_listener.engines.base import BaseEngine
from danmaku_listener.engines.protocol.mtop import (
    MtopClient,
    MtopError,
    MtopCredential,
    extract_live_id,
    parse_base64_mixed_message,
)

PROTOCOL_VERSION = "taobao-1"

LIVE_URL = "https://tbzb.taobao.com/live?liveId={live_id}"
POWERMSG_API = "mtop.taobao.powermsg.h5.msg.pullnativemsg"
POWERMSG_VERSION = "1.0"
POWERMSG_APP_KEY = "12574478"
ILIAD_API = "mtop.taobao.iliad.comment.query.latest"
ILIAD_VERSION = "1.0"
ILIAD_APP_KEY = "34675810"

POLL_INTERVAL_POWERMSG = 10.0
NO_MESSAGE_TIMEOUT = 30.0

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36")

# ---- 登录闭环（2026-10-06 平台登录门槛计划，切片 1）----

#: ENGINE_STATUS 登录生命周期事件词表（CEO F1——taobao 首用，后续平台复用）
LOGIN_EVENT_FIRST_LOGIN = "login.first_login"
LOGIN_EVENT_RELOGIN_TRIGGERED = "login.relogin_triggered"
LOGIN_EVENT_BUDGET_EXHAUSTED = "login.timeout_budget_exhausted"
LOGIN_EVENT_DEGRADED_DETECTED = "login.degraded_detected"

#: 登录窗口等待上限（独立 deadline——不套 attempt 的 45s/240s 档位，Eng F8）
LOGIN_WAIT_TIMEOUT = 300.0
#: 每房间超时预算（首登/重登共用；内存态、App 重启清零——CEO F5）
LOGIN_BUDGET = 2
#: 登录窗口 cookie 轮询间隔
LOGIN_POLL_INTERVAL = 2.0
#: 登录成功后保窗宽限（2026-10-08 验收实证：登录 cookie 落地后淘宝常弹安全滑块，
#: 立即关窗会杀掉验证——profile 拿不到通过状态，后续瞬态发送页被 x5sec 拦。
#: 保窗 30s 供人工完成滑块；用户可手动关窗提前结束）
LOGIN_POST_GRACE_S = 30.0


def _has_login_cookie(cookies) -> bool:
    """首登判定：unb cookie 存在且有值（阿里系统一账号标识，live1688 同判定）。

    判定边界（spec 审查 1.1）：unb 是账号标识而非会话令牌——仅用于**首次登录
    检测**，不得用于会话失效重登判定（pdd 2026-10-05 实证：会话作废后 cookie
    存在性判定失真）。
    """
    return any(c.get("name") == "unb" and (c.get("value") or "").strip()
               for c in cookies or [])


def _mask_cookie(value: str) -> str:
    """cookie 值日志掩码（Eng F2）：长度 + sha256 前 8 位，不打明文。"""
    import hashlib

    if not value:
        return "(empty)"
    return f"len={len(value)} sha256={hashlib.sha256(value.encode()).hexdigest()[:8]}"


# ---- 会话失效重登（切片 2——spike 驱动定型，2026-10-06 平台登录计划）----
# T0 spike 三臂结论决定 RELOGIN_MODE：
#   "enter_alive"  臂 2 实证 enter/统计存活、弹幕/礼物停推（pdd 同构预期）
#                  → 证据窗口判定（evidence_window_degraded）
#   "all_stopped"  臂 2 实证全停推 → 重建轮失败计数（rebuild_failures 判定）
#   None（默认）   spike 未完成——重登检测禁用，仅日志提示（不弹窗）
RELOGIN_MODE: Optional[str] = None

#: 证据窗口时长（对齐 pdd is_degraded_window 观测窗）
RELOGIN_EVIDENCE_WINDOW = 90.0
#: 连续命中窗口数（Eng F5：第一窗口命中后的重建轮即无头复验，连续 2 个才弹）
RELOGIN_REQUIRED_HITS = 2
#: 触发重登确认的重建轮间隔（全停推型：连续 N 轮无任何帧）
RELOGIN_REBUILD_FAILURES = 2


def evidence_window_degraded(
    window: dict, *, min_active_business: int = 1,
) -> bool:
    """enter 存活型降级判定（纯函数，Eng F6 seam 可单测）。

    Args:
        window: 90s 证据窗口帧计数 {"danmu": n, "gift": n, "enter": n,
                "stats": n, "had_business": bool（窗口前弹幕/礼物曾流入）}
    判定（计划 §1 分支 (i)）：窗口内弹幕/礼物完全静默 + enter/统计仍存活
    + 业务帧证据门槛（窗口前弹幕/礼物曾流入——杜绝冷清房间误弹，统计不参与）。
    """
    if not window.get("had_business", False):
        return False  # 业务帧从未流入——冷清房间，不判定（防误弹）
    business_silent = (window.get("danmu", 0) == 0 and window.get("gift", 0) == 0)
    active_alive = (window.get("enter", 0) >= min_active_business
                    or window.get("stats", 0) > 0)
    return business_silent and active_alive


def rebuild_failures_degraded(failures: int) -> bool:
    """全停推型降级判定（纯函数）：连续 N 轮凭证重建后无任何帧"""
    return failures >= RELOGIN_REBUILD_FAILURES


class TaobaoWebProtocolEngine(BaseEngine):
    """淘宝直播 mtop 双通道受控页面引擎

    AutoDanmu send 钩子：E5 经引擎实例瞬态 context（per-profile Lock 串行）；
    选择器为候选集，M0 探针（tools/send_probes/）校准后修订。
    """

    # M0 发现模式实证（2026-10-07 dom_discover：聊天输入=右下 textarea，发送=BtnSend div
    # 无文本非 button——搜索框 ph='点击更换，喜欢就搜吧' 是误选陷阱，置于候选尾位防回退）
    SEND_INPUT_SELECTORS = ["textarea[class*=chatInputCenterTextarea]",
                            "textarea[placeholder*=说点什么]", "textarea"]
    SEND_BUTTON_SELECTORS = ["div[class*=chatInputCenterBtnSend]",
                             "div[class*=chatInputSendIcon]",
                             'button:has-text("发送")', 'text=发送']

    async def send_danmu(self, room_id: str, content: str):
        """AutoDanmu send 钩子（E5）：引擎 profile 锁内瞬态 context → 直播间 DOM 发送

        .. deprecated:: T2（2026-10-07）——TaobaoMtopSender（mtop page-eval）已接管淘宝发送。
        本 DOM 钩子 deprecated 保留：T4 兜底参照+回退路径（wiring 回切步骤见计划 T4）。
        T1 判定：页面 mtop 库调用（page-eval）为唯一可行 API 形态（纯 HTTP 重放被
        RGV587 拒）；DOM 路线的滑块风控问题（headless 3/5 触发）保留原状。

        与凭证提取/登录窗口共用 _profile_lock（persistent context 单实例串行）；
        acquire 带超时（S4-1，超时回执 busy）；异常不出引擎边界（E5 隔离）。
        """
        from danmaku_listener.contract.models import SendRejectReason, SendStatus
        from danmaku_listener.senders.base import SendResult

        try:
            await asyncio.wait_for(self._profile_lock.acquire(), timeout=3.0)
        except asyncio.TimeoutError:
            return SendResult(SendStatus.FAILED, SendRejectReason.PLATFORM_REJECTED.value,
                              fix_hint="profile 锁被监听操作占用——稍后重试（busy）")
        try:
            from playwright.async_api import async_playwright

            live_id = self._extract_live_id(room_id)
            live_url = self.LIVE_URL_TEMPLATE.format(live_id=live_id)
            async with async_playwright() as pw:
                context = await pw.chromium.launch_persistent_context(
                    self._profile_dir(),
                    headless=True,
                    user_agent=UA,
                    viewport={"width": 1280, "height": 800},
                    args=["--disable-blink-features=AutomationControlled",
                          "--disable-setuid-sandbox",
                          "--hide-crash-restore-bubble"],
                )
                try:
                    await context.add_init_script(
                        "Object.defineProperty(navigator, 'webdriver', "
                        "{get: () => undefined});")
                    page = context.pages[0] if context.pages else await context.new_page()
                    try:
                        await page.goto(live_url, timeout=45000, wait_until="domcontentloaded")
                    except Exception as e:  # noqa: BLE001
                        return SendResult(SendStatus.UNKNOWN, SendRejectReason.SEND_TIMEOUT.value,
                                          detail=f"goto: {type(e).__name__}")
                    await asyncio.sleep(3)
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
                                          fix_hint="未发现发送输入框（页面结构/未登录）——重跑 M0 探针校准选择器")
                    for sel in self.SEND_BUTTON_SELECTORS:
                        try:
                            if await page.locator(sel).first.count() > 0:
                                btn_sel = sel; break
                        except Exception:  # noqa: BLE001
                            continue
                    async def _fill_and_send() -> None:
                        await page.locator(input_sel).first.fill(content)
                        clicked = False
                        if btn_sel:
                            try:
                                # React 受控组件灰态/遮挡 → force click + 短超时（Enter 兜底）
                                await page.locator(btn_sel).first.click(timeout=5000, force=True)
                                clicked = True
                            except Exception as e:  # noqa: BLE001
                                logger.debug(f"[taobao] send click fallback: {e}")
                        if not clicked:
                            await page.locator(input_sel).first.press("Enter")

                    async def _slider_blocked() -> bool:
                        # noCaptcha 惯例在 iframe 内——page.locator 不穿透，须遍历 frames
                        for frame in page.frames:
                            for sel in ("text=拖动下方滑块", "text=完成验证",
                                        "text=请按住滑块", "text=安全验证",
                                        ".nc-container", "[class*=nc_wrapper]"):
                                try:
                                    if await frame.locator(sel).first.count() > 0:
                                        return True
                                except Exception:  # noqa: BLE001
                                    continue
                        return False

                    await _fill_and_send()
                    await asyncio.sleep(2)
                    # R17 风控信号：滑块验证 → 单次自动滑动（登录 spike 同款助手）→ 重发一次
                    if await _slider_blocked():
                        logger.warning(f"[taobao] send slider captcha — auto-slide once")
                        slid = await self._try_auto_slide(page)
                        await asyncio.sleep(2)
                        await _fill_and_send()  # 滑块吞掉了第一次发送——重填重发
                        await asyncio.sleep(2)
                        if await _slider_blocked():
                            return SendResult(SendStatus.FAILED, SendRejectReason.PLATFORM_REJECTED.value,
                                              fix_hint="风控滑块拦截（自动滑动未通过）——降低发送频率，稍后重试",
                                              detail=f"auto_slide={slid}")
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
                    try:
                        from pathlib import Path as _P
                        _shot = _P("persistence_data/taobao-send-unknown.png")
                        await page.screenshot(path=str(_shot))
                        logger.info(f"[taobao] unknown-state screenshot: {_shot}")
                    except Exception:  # noqa: BLE001
                        pass
                    return SendResult(SendStatus.UNKNOWN, SendRejectReason.SEND_TIMEOUT.value,
                                      detail="无回显无显式错误（虚拟列表/慢渲染）")
                finally:
                    try:
                        await context.close()
                    except Exception:  # noqa: BLE001
                        pass
        except Exception as e:  # noqa: BLE001  E5 隔离
            logger.warning(f"[taobao] send_danmu error: {type(e).__name__}: {str(e)[:100]}")
            return SendResult(SendStatus.FAILED, SendRejectReason.PLATFORM_REJECTED.value,
                              detail=f"{type(e).__name__}: {str(e)[:100]}")
        finally:
            self._profile_lock.release()

    def _send_room_url(self, room_id: str) -> str:
        return self.LIVE_URL_TEMPLATE.format(live_id=room_id)

    """淘宝直播 mtop 双通道引擎（每平台一实例、每房间独立任务）"""

    platform = "taobao"
    PROTOCOL_VERSION = PROTOCOL_VERSION  # 类属性（子类覆写自己的版本号）

    #: mtop 网关域名与直播间 URL 模板（1688 子类覆写——同协议异域名）
    MTOP_DOMAIN = "taobao.com"
    LIVE_URL_TEMPLATE = "https://tbzb.taobao.com/live?liveId={live_id}"
    #: powermsg 轮询参数（1688 页面实测：首拉 offset=0、pagesize 20、sdk h5_3.3.3）
    POWERMSG_SDK_VERSION = "h5_3.4.2"
    POWERMSG_PAGESIZE = 10
    POWERMSG_INIT_OFFSET_ZERO = False  # 淘宝=当前时间戳起拉增量；1688=0 起拉历史
    #: 页面来源（mtop 网关校验 Referer/Origin 域——1688 引擎覆写）
    PAGE_ORIGIN = "https://tbzb.taobao.com"
    #: 订阅开关与 H5 appKey（1688 页面实测：pull 前须 subscribe，否则空返回）
    POWERMSG_SUBSCRIBE = False
    POWERMSG_H5_APPKEY = "H5_1WhTdTy0y67M01"
    #: 凭证锚点：页面请求中携带 topic 的接口（子类可加 1688 subscribe）
    TOPIC_ANCHORS = (ILIAD_API, "powermsg")

    def __init__(self, state_store=None, domain: Optional[str] = None,
                 cookie_dir: str = "./cookie"):
        super().__init__(state_store=state_store)
        self._domain = domain or self.MTOP_DOMAIN
        self._cookie_dir = cookie_dir  # profile 持久化目录（wxlivespy 同款 userDataDir）
        self._room_tasks: Dict[str, asyncio.Task] = {}
        self._stop_flags: Dict[str, bool] = {}
        # 未映射消息聚合计数（防 debug 刷屏——60s 汇总一次）
        self._unmapped_stats: Dict[str, dict] = {}
        self._unmapped_last_flush = time.monotonic()
        self._last_room_stats: Dict[str, tuple] = {}  # ROOM_STATS 值去重（同 1688）
        # ---- 登录闭环状态（切片 1）----
        # per-profile 并发协调（Eng F1）：persistent context 单实例——全部 context
        # 启动（无头提取/可见窗口）在此锁内串行化
        self._profile_lock = asyncio.Lock()
        # 每房间超时预算（首登/重登共用；内存态、重启清零——CEO F5）
        self._login_budgets: Dict[str, int] = {}
        # 预算耗尽锁存提示键（锁存粒度=room_id，Eng F8——不吞其他房间提示）
        self._budget_warned: set = set()
        # ---- 会话失效重登（切片 2——RELOGIN_MODE 由 spike 臂 2 定型）----
        self._evidence_windows: Dict[str, dict] = {}   # 90s 证据窗口帧分类计数
        self._had_business: Dict[str, bool] = {}       # 业务帧证据门槛（曾流入弹幕/礼物）
        self._relogin_hits: Dict[str, int] = {}        # 连续命中窗口数（Eng F5）
        self._rebuild_failures: Dict[str, int] = {}    # 全停推型重建失败计数

    @property
    def engine_id(self) -> str:
        return "webws:taobao"

    # ---- 公开契约 ----

    async def start(self, room_id: str) -> None:
        if room_id in self._room_tasks and not self._room_tasks[room_id].done():
            return
        self._stop_flags[room_id] = False
        self._room_tasks[room_id] = asyncio.create_task(
            self._run_room(room_id), name=f"taobao-room-{room_id}")

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

    # ---- 凭证阶段（Playwright）----

    def _profile_dir(self) -> str:
        """浏览器 profile 持久化目录（wxlivespy 同款 userDataDir 模式）——
        滑块验证通过一次后设备指纹/cookie 持久保存，后续启动免验证"""
        import os

        d = f"{self._cookie_dir}/taobao_profile"
        os.makedirs(d, exist_ok=True)
        return d

    async def _fetch_credentials(self, room_id: str) -> tuple:
        """打开直播间页 → topic + cookies

        风控处理（2026-09-29 实测校准）：淘宝 noCaptcha 检测自动化指纹
        （navigator.webdriver 等）——自动化浏览器里**人工拖滑块也会失败**
        （error rTVSNv）。对策：launch_persistent_context（profile 持久化）
        + 反检测注入 + AutomationControlled 禁用；首次验证通过后 profile
        保存，后续启动免验证。等待事件驱动（headless 45s / 可见 240s）。

        登录门槛（2026-10-06 平台登录计划，切片 1）：页面打开后检测 unb
        登录态；未登录且预算未尽 → 可见窗口登录（Eng F1：锁内串行化）；
        预算耗尽 → 降级游客模式继续提凭证（保持既有行为）。
        """
        from playwright.async_api import async_playwright

        live_id = self._extract_live_id(room_id)
        live_url = self.LIVE_URL_TEMPLATE.format(live_id=live_id)

        async def attempt(wait_limit: float) -> Optional[Dict[str, Any]]:
            """单轮 headless 凭证提取：page 级请求捕获（context 级实测拿不到）

            持 profile_lock（Eng F1：persistent context 单实例——与可见
            登录窗口互斥）。
            """
            state: Dict[str, Any] = {"topic": None, "cookies": {},
                                     "login_detected": False}
            async with self._profile_lock:
                async with async_playwright() as pw:
                    context = await pw.chromium.launch_persistent_context(
                        self._profile_dir(),
                        headless=True,
                        user_agent=UA,
                        viewport={"width": 1280, "height": 800},
                        args=["--disable-blink-features=AutomationControlled",
                              "--disable-setuid-sandbox",
                              "--hide-crash-restore-bubble"],
                    )
                    try:
                        await context.add_init_script(
                            "Object.defineProperty(navigator, 'webdriver', "
                            "{get: () => undefined});")
                        page = context.pages[0] if context.pages else await context.new_page()

                        def on_request(request) -> None:
                            if state["topic"]:
                                return
                            u = request.url
                            if any(a in u for a in self.TOPIC_ANCHORS):
                                m = re.search(r"[?&]data=([^&]+)", u)
                                raw = m.group(1) if m else request.post_data
                                if raw:
                                    try:
                                        from urllib.parse import unquote
                                        data = json.loads(unquote(raw))
                                        if data.get("topic"):
                                            state["topic"] = data["topic"]
                                    except json.JSONDecodeError:
                                        pass

                        page.on("request", on_request)
                        try:
                            await page.goto(live_url, timeout=30000)
                        except Exception as e:  # noqa: BLE001
                            logger.debug(f"[taobao] room {room_id} page goto warning: {e}")

                        # 登录检测（先于 topic 等待——未登录也要触发门槛）
                        state["cookies"] = {c["name"]: c["value"]
                                            for c in await context.cookies()
                                            if c.get("value")}
                        state["login_detected"] = _has_login_cookie(
                            [{"name": n, "value": v} for n, v in state["cookies"].items()])

                        deadline = time.monotonic() + wait_limit
                        while state["topic"] is None and time.monotonic() < deadline:
                            await asyncio.sleep(1)
                        # 全量 cookie（1688 网关要求 isg 等安全 cookie 齐全；
                        # 淘宝网关仅 _m_h5_tk 必需——全量兼容两者）
                        state["cookies"] = {c["name"]: c["value"]
                                            for c in await context.cookies()
                                            if c.get("value")}
                    finally:
                        await context.close()
            return state

        # 登录门槛循环（防御上限 = 预算 + 1 轮常规提取）
        for _ in range(LOGIN_BUDGET + 1):
            state = await attempt(wait_limit=45.0)
            if self._stop_flags.get(room_id):
                raise RuntimeError("taobao.stopped: 房间已停止")
            if state["login_detected"]:
                break
            budget = self._login_budgets.get(room_id, LOGIN_BUDGET)
            if budget <= 0:
                # 预算耗尽：降级游客模式继续提凭证（CEO F5——每会话一次锁存提示，
                # 锁存键=room_id，Eng F8）
                if room_id not in self._budget_warned:
                    self._budget_warned.add(room_id)
                    await self._emit_system_status(
                        room_id,
                        f"{LOGIN_EVENT_BUDGET_EXHAUSTED}: 登录未完成——已按游客模式尝试，"
                        "可停止该房间后重新添加以再次触发登录窗口")
                break
            # 未登录且预算未尽 → 可见窗口登录（Eng F1：先关无头 context 再弹窗——
            # attempt 已 close，锁已释放，窗口内重新持锁）
            self._login_budgets[room_id] = budget - 1
            await self._emit_system_status(
                room_id,
                f"{LOGIN_EVENT_FIRST_LOGIN}: 淘宝直播间需要登录——已弹出浏览器，"
                "请登录淘宝/阿里账号（扫码）")
            outcome = await self._wait_login_visible(room_id, live_url)
            if outcome == "logged_in":
                self._login_budgets[room_id] = LOGIN_BUDGET  # 登录成功清零
                self._budget_warned.discard(room_id)
                continue  # 重试凭证提取（此时 profile 已带登录态）
            if outcome == "stopped":
                raise RuntimeError("taobao.stopped: 房间已停止")
            # timeout / window_closed：预算已在弹出前扣减；回循环重查
            # （预算未尽且仍未登录会再弹；耗尽则降级提示后 break）
        else:
            state = await attempt(wait_limit=45.0)

        if not state["topic"]:
            # 二次尝试（页面偶发加载慢/轮询冷启动）
            state = await attempt(wait_limit=45.0)

        if not state["topic"]:
            hint = ("未登录——请停止该房间后重新添加以触发登录窗口；"
                    if not state["login_detected"] else "")
            raise RuntimeError(
                "taobao.credential.topic_failed: 未能获取 topic（"
                f"{hint}未开播/风控/页面加载慢——持续失败可稍后重试）")
        if not state["cookies"].get("_m_h5_tk"):
            raise RuntimeError("taobao.credential.token_failed: 未获取 _m_h5_tk（风控升级特征）")
        logger.info(f"[taobao] room {room_id} credentials ready (topic={state['topic'][:24]}...)")
        return state["topic"], MtopCredential(state["cookies"])

    async def _wait_login_visible(
        self, room_id: str, live_url: str, *,
        clock=None, sleep_fn=None, cookies_fn=None, poll_interval: float = LOGIN_POLL_INTERVAL,
    ) -> str:
        """可见窗口登录（≤300s）：打开直播间页，用户登录（unb 出现）→ 登录态入 profile。

        Returns:
            "logged_in"    登录成功（unb 出现）
            "timeout"      等待超时（预算已在调用方扣减）
            "window_closed" 用户直接关窗（Eng F4：按关窗分支，不冒泡异常）
            "stopped"      用户停止房间（Eng F3：立即关窗中断）

        Eng F6 seam：clock/sleep_fn/cookies_fn 可注入（单测打在等待循环上，
        不起真实浏览器）。
        """
        from playwright.async_api import async_playwright

        clock = clock or time.monotonic
        sleep_fn = sleep_fn or asyncio.sleep
        baseline_names: set = set()
        logged = False
        async with self._profile_lock:
            async with async_playwright() as pw:
                try:
                    context = await pw.chromium.launch_persistent_context(
                        self._profile_dir(),
                        headless=False,
                        user_agent=UA,
                        viewport={"width": 1280, "height": 800},
                        args=["--disable-blink-features=AutomationControlled",
                              "--disable-setuid-sandbox",
                              "--hide-crash-restore-bubble"],
                    )
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"[taobao] room {room_id} login window launch failed: "
                                   f"{type(e).__name__}: {str(e)[:80]}")
                    return "window_closed"
                try:
                    page = context.pages[0] if context.pages else await context.new_page()
                    try:
                        await page.goto(live_url, timeout=30000)
                    except Exception as e:  # noqa: BLE001
                        logger.debug(f"[taobao] room {room_id} login page goto warning: {e}")

                    async def _cookies():
                        # Eng F6 seam：cookie 读取可注入（同步 list 或 coroutine 均兼容）
                        if cookies_fn is not None:
                            r = cookies_fn()
                            return await r if asyncio.iscoroutine(r) else r
                        return await context.cookies()

                    try:
                        baseline_names = {c.get("name") for c in await _cookies() or []}
                    except Exception as e:  # noqa: BLE001
                        # Eng F4：窗口在基线读取前已被用户关闭 → 按关窗分支
                        logger.info(f"[taobao] room {room_id} login window closed before "
                                    f"baseline ({type(e).__name__})")
                        return "window_closed"
                    deadline = clock() + LOGIN_WAIT_TIMEOUT
                    while clock() < deadline:
                        if self._stop_flags.get(room_id):  # Eng F3：stop 立即中断
                            logger.info(f"[taobao] room {room_id} login wait stopped by user")
                            return "stopped"
                        try:
                            cookies = await _cookies() or []
                        except Exception as e:  # noqa: BLE001
                            # Eng F4：窗口被用户直接关闭 → cookies() 抛 TargetClosedError
                            # → 按关窗分支计数，不冒泡成 topic_failed
                            logger.info(f"[taobao] room {room_id} login window closed by user "
                                        f"({type(e).__name__})")
                            return "window_closed"
                        if _has_login_cookie(cookies):
                            logged = True
                            # Eng F2：只打名与变更布尔，不打值
                            names = {c.get("name") for c in cookies}
                            new_names = sorted(n for n in names
                                               if n and n not in baseline_names)
                            unb = next((c for c in cookies if c.get("name") == "unb"), None)
                            logger.info(
                                f"[taobao] room {room_id} login ok: new_cookies={new_names} "
                                f"unb_changed=True unb_mask={_mask_cookie((unb or {}).get('value', ''))}")
                            # 登录后保窗宽限（2026-10-08 验收用户实证：登录成功即关窗
                            # 会杀掉紧随的安全滑块——profile 缺滑块通过状态，后续
                            # 发送被 x5sec 拦）。保窗 LOGIN_POST_GRACE_S 供人工完成；
                            # 用户手动关窗立即提前结束（登录态已入 profile，仍算成功）
                            grace_deadline = clock() + LOGIN_POST_GRACE_S
                            while clock() < grace_deadline:
                                if self._stop_flags.get(room_id):  # Eng F3
                                    logger.info(f"[taobao] room {room_id} login grace stopped by user")
                                    return "stopped"
                                try:
                                    await _cookies()
                                except Exception as e:
                                    logger.info(f"[taobao] room {room_id} post-login window closed "
                                                f"({type(e).__name__})——滑块宽限提前结束，登录态已入 profile")
                                    return "logged_in"
                                await sleep_fn(poll_interval)
                            logger.info(f"[taobao] room {room_id} login grace ended "
                                        f"({LOGIN_POST_GRACE_S:.0f}s)——窗口关闭")
                            return "logged_in"
                        await sleep_fn(poll_interval)
                    logger.info(f"[taobao] room {room_id} login wait timeout "
                                f"({LOGIN_WAIT_TIMEOUT:.0f}s)")
                    return "timeout"
                finally:
                    try:
                        await context.close()
                    except Exception:  # noqa: BLE001  窗口已被用户关闭——清理失败可忽略
                        pass
        # 不可达（所有分支在 try 内 return）；保守返回
        return "logged_in" if logged else "timeout"

    @staticmethod
    async def _try_auto_slide(page) -> bool:
        """自动尝试拖动风控滑块（拟人缓动轨迹；noCaptcha 常在 iframe 内）"""
        try:
            slider = None
            for frame in page.frames:
                slider = await frame.query_selector(
                    '.nc_iconfont.btn_slide, .btn_slide, [data-role="slider"], '
                    '#nc_1_n1z, .nc-lang-cnt ~ * .btn_slide')
                if slider:
                    target_page = page
                    break
            if not slider:
                logger.debug("[taobao] slider element not found")
                return False
            box = await slider.bounding_box()
            if not box:
                return False
            import random as _rand

            start_x = box["x"] + box["width"] / 2
            start_y = box["y"] + box["height"] / 2
            track = max(box["width"] * 4.5, 260)
            await page.mouse.move(start_x, start_y)
            await page.mouse.down()
            steps = 45
            for i in range(steps):
                t = i / steps
                dx = track * (1 - (1 - t) ** 2) + _rand.uniform(-1.5, 1.5)
                await page.mouse.move(start_x + dx, start_y + _rand.uniform(-1.2, 1.2))
                await asyncio.sleep(_rand.uniform(0.008, 0.028))
            await page.mouse.up()
            logger.info("[taobao] auto slide attempted")
            return True
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[taobao] auto slide error: {e}")
            return False

    async def _emit_system_status(self, room_id: str, detail: str) -> None:
        import time as _time

        from danmaku_listener.contract import Category, SystemType
        from danmaku_listener.contract.models import Envelope, UnifiedMessage

        msg = UnifiedMessage(
            envelope=Envelope(
                category=Category.SYSTEM, type=SystemType.ENGINE_STATUS.value,
                platform=self.platform, room_id=room_id, seq=self.next_seq(room_id),
                timestamp=int(_time.time()), engine=self.engine_id,
            ),
            payload={"type": "ENGINE_STATUS", "engine": self.engine_id, "detail": detail},
        )
        await self._emit_message(msg.to_wire())

    # ---- 房间任务主循环 ----

    async def _run_room(self, room_id: str) -> None:
        live_id = self._extract_live_id(room_id)
        logger.info(f"[taobao] room {room_id} connecting (live_id={live_id})")
        backoff = 15.0
        while not self._stop_flags.get(room_id):
            try:
                topic, cred = await self._fetch_credentials(room_id)

                def _make_client() -> MtopClient:
                    # 每通道独立 Session（两轮询并发跨线程共享 Session 会竞争失败）
                    s = requests.Session()
                    s.headers.update({"User-Agent": UA})
                    for k, v in cred.cookies.items():
                        s.cookies.set(k, v)
                    return MtopClient(self._domain, UA, s)

                last_msg_box = {"t": time.monotonic()}
                poll_task = asyncio.create_task(
                    self._poll_powermsg(room_id, _make_client(), cred, topic, live_id,
                                        last_msg_box))
                comment_task = asyncio.create_task(
                    self._poll_comments(room_id, _make_client(), cred, topic))
                self._set_status(self.status.__class__.RUNNING)
                try:
                    while not self._stop_flags.get(room_id):
                        await asyncio.sleep(2)
                        # 30s 无消息 → 凭证过期/风控 → 整轮重建（Eng：主通道断流检测）
                        if time.monotonic() - last_msg_box["t"] > NO_MESSAGE_TIMEOUT:
                            await self._maybe_relogin(room_id)  # 切片 2 接入点
                            raise RuntimeError(
                                "taobao.no_message_timeout: 30s 无消息（凭证过期/风控）")
                finally:
                    poll_task.cancel()
                    comment_task.cancel()
                    for tk in (poll_task, comment_task):
                        try:
                            await tk
                        except (asyncio.CancelledError, Exception):
                            pass
                backoff = 15.0
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(f"[taobao] room {room_id} error: {type(e).__name__}: {str(e)[:90]}")
                if self._is_stopping(room_id):
                    return  # stop-in-progress close is expected (sent 1000) - no GAP/ERROR

                self._set_status(self.status.__class__.ERROR)
                self.mark_gap_start(room_id)
                gap = self.build_gap_message(room_id, GapReason.NETWORK)
                if gap:
                    await self._emit_system(gap)
                if self._stop_flags.get(room_id):
                    return
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 900.0)

    # ---- 主通道：powermsg 长轮询 ----

    async def _poll_powermsg(self, room_id: str, client: MtopClient, cred: MtopCredential,
                             topic: str, live_id: str, last_msg_box: dict) -> None:
        offset = "0" if self.POWERMSG_INIT_OFFSET_ZERO else str(int(time.time() * 1000))
        loop = asyncio.get_running_loop()
        headers = {
            "x-biz-type": "powermsg",
            "x-biz-info": "namespace=1",
            "referer": self.PAGE_ORIGIN + "/",
            "origin": self.PAGE_ORIGIN,
        }
        # 订阅前置（1688 页面实测：不 subscribe 则 pull 的 timestampList 恒空）
        if self.POWERMSG_SUBSCRIBE:
            now = int(time.time() * 1000)
            sub_data = {
                "namespace": 1, "topic": topic, "role": 3,
                "sdkVersion": self.POWERMSG_SDK_VERSION, "tag": "",
                "timestamp": now, "ext": now,
                "appKey": self.POWERMSG_H5_APPKEY,
                "utdId": f"{random.randint(10**9, 10**10)}_{random.randint(100, 999)}",
                "token": "",
            }
            try:
                sub_result = await loop.run_in_executor(
                    None, client.get, "mtop.taobao.powermsg.h5.msg.subscribe",
                    "1.0", POWERMSG_APP_KEY, sub_data, cred.m_h5_tk, headers)
                logger.info(f"[taobao] room {room_id} powermsg subscribe: "
                            f"{str(sub_result.get('ret'))[:60]}")
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[taobao] room {room_id} subscribe error: {e}")
        while not self._stop_flags.get(room_id):
            try:
                data = {
                    "topic": topic, "offset": offset,
                    "pagesize": self.POWERMSG_PAGESIZE,
                    "tag": "", "bizcode": 1,
                    "sdkversion": self.POWERMSG_SDK_VERSION, "role": 3,
                }
                result = await loop.run_in_executor(
                    None, client.get, POWERMSG_API, POWERMSG_VERSION,
                    POWERMSG_APP_KEY, data, cred.m_h5_tk, headers)
                timestamps = (result.get("data") or {}).get("timestampList") or []
                if timestamps:
                    offset = timestamps[-1].get("offset", offset)
                    for td in timestamps:
                        await self._parse_powermsg_item(room_id, td, ts=int(time.time()))
                    last_msg_box["t"] = time.monotonic()
                await asyncio.sleep(POLL_INTERVAL_POWERMSG)
            except MtopError as e:
                logger.warning(f"[taobao] room {room_id} powermsg mtop error: {e}")
                await asyncio.sleep(5)
            except Exception as e:  # noqa: BLE001  task 不得静默死亡
                logger.warning(f"[taobao] room {room_id} powermsg error: "
                               f"{type(e).__name__}: {str(e)[:80]}")
                await asyncio.sleep(5)
            except asyncio.CancelledError:
                raise

    # ---- 会话失效重登（切片 2——RELOGIN_MODE 由 spike 臂 2 定型）----

    async def _maybe_relogin(self, room_id: str) -> None:
        """30s 断流时的重登检测入口（切片 2）。

        RELOGIN_MODE=None（spike 未完成）→ 仅日志，不弹窗；
        "enter_alive" → 证据窗口判定连续命中（Eng F5：第一窗口命中后的重建轮
        即无头复验）；
        "all_stopped" → 重建失败计数。
        触发时弹出可见窗口（首登/重登共用预算——CEO F5；锁存键=room_id——F8）。
        """
        # 窗口评估后立即重置（下一个窗口从零计数）
        window = self._evidence_windows.pop(room_id, None) or {}
        if RELOGIN_MODE is None:
            logger.debug(f"[taobao] room {room_id} relogin gate pending spike "
                         f"(RELOGIN_MODE=None) window={window}")
            return
        if self._stop_flags.get(room_id):
            return
        if RELOGIN_MODE == "enter_alive":
            if evidence_window_degraded({**window, "had_business":
                                         self._had_business.get(room_id, False)}):
                self._relogin_hits[room_id] = self._relogin_hits.get(room_id, 0) + 1
            else:
                self._relogin_hits[room_id] = 0
            if self._relogin_hits.get(room_id, 0) < RELOGIN_REQUIRED_HITS:
                return
        elif RELOGIN_MODE == "all_stopped":
            self._rebuild_failures[room_id] = self._rebuild_failures.get(room_id, 0) + 1
            if self._rebuild_failures.get(room_id, 0) < RELOGIN_REBUILD_FAILURES:
                return
        else:
            logger.warning(f"[taobao] room {room_id} unknown RELOGIN_MODE={RELOGIN_MODE}")
            return
        # 触发重登（预算共用——CEO F5；锁存键=room_id——Eng F8）
        budget = self._login_budgets.get(room_id, LOGIN_BUDGET)
        if budget <= 0:
            if room_id not in self._budget_warned:
                self._budget_warned.add(room_id)
                await self._emit_system_status(
                    room_id,
                    f"{LOGIN_EVENT_BUDGET_EXHAUSTED}: 消息中断疑似登录过期——"
                    "停止该房间后重新添加可重新触发登录窗口")
            return
        self._login_budgets[room_id] = budget - 1
        self._relogin_hits[room_id] = 0
        self._rebuild_failures[room_id] = 0
        live_id = self._extract_live_id(room_id)
        await self._emit_system_status(
            room_id,
            f"{LOGIN_EVENT_RELOGIN_TRIGGERED}: 消息中断——如登录已过期"
            "请在弹出窗口重新登录")
        outcome = await self._wait_login_visible(
            room_id, self.LIVE_URL_TEMPLATE.format(live_id=live_id))
        if outcome == "logged_in":
            self._login_budgets[room_id] = LOGIN_BUDGET  # 成功清零
            self._budget_warned.discard(room_id)
            await self._emit_system_status(room_id, "登录成功——恢复监听")

    async def _parse_powermsg_item(self, room_id: str, td: Dict[str, Any], ts: int) -> None:
        data_b64 = td.get("data", "")
        if not data_b64:
            return
        try:
            json_objects, _raw = parse_base64_mixed_message(data_b64)
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[taobao] room {room_id} b64 parse error: {e}")
            return
        for obj in json_objects:
            if not isinstance(obj, dict):
                continue
            # ROOM_STATS 值去重（在 next_seq 之前——seq 只在确认 emit 时消耗，
            # 跳号会触发 consumer 侧 GAP 等待；同 1688）
            if ("subType" not in obj
                    and ("viewCountFormat" in obj
                         or "pageViewCount" in obj or "totalCount" in obj)):
                sig = (obj.get("onlineCount") or 0, obj.get("totalCount") or 0,
                       obj.get("pageViewCount") or 0)
                if sig == self._last_room_stats.get(room_id):
                    continue
                self._last_room_stats[room_id] = sig
            mapped = self._map_powermsg(room_id, obj, self.next_seq(room_id), ts)
            if mapped:
                # 切片 2：证据窗口帧分类计数（enter_alive 判定输入）
                box = self._evidence_windows.setdefault(
                    room_id, {"danmu": 0, "gift": 0, "enter": 0, "stats": 0})
                mtype = mapped["type"]
                if mtype == "DANMU":
                    box["danmu"] += 1
                    self._had_business[room_id] = True
                elif mtype == "GIFT":
                    box["gift"] += 1
                    self._had_business[room_id] = True
                elif mtype == "ENTER_ROOM":
                    box["enter"] += 1
                elif mtype == "ROOM_STATS":
                    box["stats"] += 1
                await self._emit_message(self._envelope(room_id, mapped))
            else:
                # 未映射消息聚合计数（2026-10-01 用户实测：运营类消息高频，
                # 逐条 debug 刷屏——60s 汇总一次）
                keys_sig = tuple(sorted(obj.keys())[:6])
                stat = self._unmapped_stats.setdefault(room_id, {})
                stat[keys_sig] = stat.get(keys_sig, 0) + 1
                now = time.monotonic()
                if now - self._unmapped_last_flush > 60.0:
                    self._unmapped_last_flush = now
                    for rid, kv in self._unmapped_stats.items():
                        total = sum(kv.values())
                        if total:
                            top = sorted(kv.items(), key=lambda x: -x[1])[:3]
                            logger.debug(
                                f"[taobao] room {rid} unmapped {total} msgs "
                                f"in 60s, top keys: {[list(k) for k, _ in top]}")
                    self._unmapped_stats = {rid: {} for rid in self._unmapped_stats}

    # ---- 评论通道：iliad 轮询 ----

    async def _poll_comments(self, room_id: str, client: MtopClient, cred: MtopCredential,
                             topic: str) -> None:
        pagination_ctx = None
        loop = asyncio.get_running_loop()
        while not self._stop_flags.get(room_id):
            try:
                payload: Dict[str, Any] = {"topic": topic, "limit": 20, "tab": 2,
                                           "order": "asc"}
                if pagination_ctx:
                    payload["paginationContext"] = pagination_ctx
                result = await loop.run_in_executor(
                    None, client.get, ILIAD_API, ILIAD_VERSION,
                    ILIAD_APP_KEY, payload, cred.m_h5_tk, None)
                data = result.get("data") or {}
                pagination_ctx = data.get("paginationContext") or pagination_ctx
                delay_ms = data.get("delay", 6000)
                for c in data.get("comments") or []:
                    mapped = self._map_comment(room_id, c, self.next_seq(room_id),
                                               int(time.time()))
                    if mapped:
                        await self._emit_message(self._envelope(room_id, mapped))
                await asyncio.sleep(max(int(delay_ms), 2000) / 1000.0)
            except MtopError as e:
                logger.warning(f"[taobao] room {room_id} iliad mtop error: {e}")
                await asyncio.sleep(5)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001  网络异常等——task 不得静默死亡
                logger.warning(f"[taobao] room {room_id} iliad error: "
                               f"{type(e).__name__}: {str(e)[:80]}")
                await asyncio.sleep(5)

    @staticmethod
    def _extract_live_id(room_spec: str) -> str:
        return extract_live_id(room_spec)

    def validate_room_id(self, room_id: str) -> None:
        """add_room 预校验：淘宝引擎无法解析 1688 链接（提示用对前缀）"""
        try:
            self._extract_live_id(room_id)
        except ValueError as e:
            raise ValueError(
                f"{e}——若为 1688 直播间请使用 alibaba1688: 前缀添加") from e

    def _envelope(self, room_id: str, mapped: dict) -> dict:
        """契约信封组装（全键——缺键会被 bridge 兜底包装为 ENGINE_STATUS）

        platform/engine 用 self（1688 子类继承后如实标注——用户实测教训：
        静态方法硬编码导致 1688 消息被标成 taobao）
        """
        return {
            "contract_version": "1.0.0",
            "category": mapped["category"],
            "type": mapped["type"],
            "platform": self.platform,
            "room_id": room_id,
            "seq": mapped["seq"],
            "timestamp": mapped["timestamp"],
            "engine": self.engine_id,
            "protocol_version": self.PROTOCOL_VERSION,
            "payload": mapped["payload"],
        }

    # ---- 上游消息 → 契约 v1（显式清单，与 test_taobao_protocol.py 对齐）----

    @staticmethod
    def _map_powermsg(room_id: str, obj: Dict[str, Any], seq: int, ts: int) -> Optional[Dict[str, Any]]:
        # 分发顺序修正（参考实现的字段特征重叠 bug）：subType 聊天/礼物优先判定，
        # 否则 subType==10001 聊天（含 nick）会被进场分支误吞
        sub_type = obj.get("subType")
        if sub_type == 10001:
            content = obj.get("content", obj.get("text", ""))
            if not content:
                return None
            return {"category": "business", "type": "DANMU", "seq": seq, "timestamp": ts,
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
        # 统计：viewCountFormat/pageViewCount/totalCount 单键（1688 形态）
        if "viewCountFormat" in obj or "pageViewCount" in obj or "totalCount" in obj:
            # 语义校准（2026-10-03 用户实测"观看 0"，同 1688 根因）：
            # onlineCount 恒 0（平台不暴露在线数）——观看人数回退
            # totalCount（UV），累计浏览 pageViewCount（PV）
            online = obj.get("onlineCount") or 0
            uv = obj.get("totalCount") or 0
            pv = obj.get("pageViewCount") or uv or 0
            return {"category": "business", "type": "ROOM_STATS", "seq": seq,
                    "timestamp": ts,
                    "payload": {"type": "ROOM_STATS", "viewer_count": online or uv,
                                "total_view_count": pv}}
        # 进场：nick + flowSourceText（subType 已在上方处理）
        if "nick" in obj and "flowSourceText" in obj:
            identify = obj.get("identify") or {}
            fan_level = identify.get("fanLevel", 0)
            if isinstance(fan_level, str) and fan_level.isdigit():
                fan_level = int(fan_level)
            elif not isinstance(fan_level, int):
                fan_level = 0
            return {"category": "business", "type": "ENTER_ROOM", "seq": seq,
                    "timestamp": ts,
                    "payload": {"type": "ENTER_ROOM",
                                "user_name": obj.get("nick", ""),
                                "user_id": str(obj.get("userid", obj.get("userId", ""))),
                                "fan_level": fan_level}}
        # 点赞：value.dig
        if "value" in obj and isinstance(obj.get("value"), dict) and "dig" in obj["value"]:
            return {"category": "business", "type": "LIKE", "seq": seq, "timestamp": ts,
                    "payload": {"type": "LIKE", "count": obj["value"].get("dig", 1)}}
        return None

    @staticmethod
    def _map_comment(room_id: str, c: Dict[str, Any], seq: int, ts: int) -> Optional[Dict[str, Any]]:
        content = c.get("content", "")
        if not content:
            return None
        nick = c.get("publisherNick", "")
        user_id = str(c.get("publisherId", ""))
        return {"category": "business", "type": "DANMU", "seq": seq, "timestamp": ts,
                "payload": {"type": "DANMU", "user_name": nick, "content": content,
                            "user_id": user_id}}
