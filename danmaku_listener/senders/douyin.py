"""抖音 profile DOM sender（AutoDanmu T3——常驻会话形态重构）

T1 CEO-F5 探针判定（cards/douyin-ceo-f5-probe-20261007）：
- `--headless=new` 完整 Chrome 指纹下面板渲染 ✓+实发成功 ✓（f5-diag 截图：聊天流
  出现"[M0] f5-diag"（我）标记）——bd_ticket_guard 检测的是旧 headless 特征
- **常驻会话形态=headless_new（无桌面运行）**；minimized/foreground 保留作 escape
  hatch（DX-D9 send_window_mode 三件套）

判定手段（2026-10-07 下午实证升级）：
- 回显判定=**get_by_text 穿透 shadow DOM**——聊天流移入 shadow DOM 后 page.content()
  搜索失效（视频号同款盲区，M0 wxsp 卡先例）
- 登录失效信号=**"需先登录"文本**（f5-shadow2 截图实证：登录过期时前端拦截发送，
  输入框清空但无 API 请求）→ NEEDS_LOGIN 语义（CEO-F8 两路径分离）

生命周期全部委托 ResidentSendSession（E5/ENG-1/CEO-F8/DX-D7/ENG-12/ENG-4——
per-room page/同锁空闲关闭/操作超时/健康自愈/撞锁识别/退出钩子）。
旧瞬态开关窗口路径 deprecated 保留（DX-D9 与淘宝同策：T4 参照+回退）。
"""
from __future__ import annotations

import asyncio
import time
from typing import Optional

from loguru import logger

from danmaku_listener.contract.models import SendRejectReason, SendStatus
from danmaku_listener.senders.base import BaseSender, SendResult
from danmaku_listener.senders.resident_session import (
    MODE_HEADLESS_NEW,
    MODE_MINIMIZED,
    ResidentSendSession,
)

PROFILE_DIR = "cookie/douyin_profile"
INPUT_SEL = "#chatInput"  # 2026-10-08 验收用户实测定位（id 稳定；输入框在播放器浮层内）
INPUT_SEL_FALLBACK = "div[contenteditable=true]"  # 旧页面形态兜底
BUTTON_SEL = "[class*=send]"
LOGIN_SIGNAL = "需先登录"
ECHO_WAIT_S = 15.0

#: 进程级会话单例（wiring 注册单 sender——多房间共享一个 profile 会话）
_session: Optional[ResidentSendSession] = None


def _get_session(window_mode: str, idle_timeout_s: int) -> ResidentSendSession:
    global _session
    if _session is None or _session._profile_dir != PROFILE_DIR:
        _session = ResidentSendSession(
            "douyin-send", PROFILE_DIR,
            window_mode=window_mode, idle_timeout_s=idle_timeout_s,
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/131.0.0.0 Safari/537.36"))
    return _session


class DouyinProfileSender(BaseSender):
    """抖音常驻会话发送器（headless_new 形态；per-room page；shadow DOM 回显判定）"""

    platform = "douyin"

    def __init__(self, profile_dir: str = PROFILE_DIR, settings=None):
        self._profile_dir = profile_dir
        # DX-D9 三件套：send_window_mode（默认 headless_new——T1 探针证据）+
        # send_session_idle_timeout_seconds（默认 1800）
        mode = MODE_HEADLESS_NEW
        idle = 1800
        if settings is not None:
            mode = getattr(settings, "send_window_mode", mode) or mode
            idle = int(getattr(settings, "send_session_idle_timeout_seconds", idle) or idle)
        self._session = _get_session(mode, idle)

    async def send(self, room_id: str, content: str) -> SendResult:
        url = f"https://live.douyin.com/{room_id}"
        return await self._session.send(room_id, url, self._page_action(content))

    def _page_action(self, content: str):
        """页面配方工厂（返回 async 闭包；会话锁内执行）：登录信号检测 → 聚焦逐键输入 → Enter → shadow DOM 回显"""
        async def action(page, rid: str) -> SendResult:
            # 登录失效信号（CEO-F8 登录路径——比输入框缺失更早更准）
            try:
                if await page.get_by_text(LOGIN_SIGNAL).count() > 0:
                    return SendResult(
                        SendStatus.FAILED, SendRejectReason.PLATFORM_REJECTED.value,
                        fix_hint="抖音登录态已失效（页面提示需先登录）——重跑 "
                                 "python -m danmaku_listener.engines.douyin_login <room_id> 扫码",
                        docs_anchor="docs/ops/send-runbook.md")
            except Exception:  # noqa: BLE001 信号探测失败不阻断
                pass

            inp = page.locator(INPUT_SEL).first
            try:
                await inp.click(timeout=10_000)
            except Exception:  # noqa: BLE001  主定位器未命中 → 旧形态兜底
                try:
                    inp = page.locator(INPUT_SEL_FALLBACK).first
                    await inp.click(timeout=10_000)
                except Exception as e:  # noqa: BLE001
                    return SendResult(SendStatus.FAILED, SendRejectReason.PLATFORM_REJECTED.value,
                                      detail=f"输入框不可达: {type(e).__name__}",
                                      fix_hint="聊天面板未出现——登录态失效重扫码，或页面结构变更重跑 T1 探针",
                                      docs_anchor="docs/ops/send-runbook.md")
            await asyncio.sleep(0.5)
            # 1688 实证配方：逐键输入进 React state（fill 的 DOM 值不进框架 state 风险）
            await inp.press_sequentially(content, delay=45)
            await asyncio.sleep(0.3)
            await inp.press("Enter")

            # 回显判定：get_by_text 穿透 shadow DOM（2026-10-07 下午起聊天流移入 shadow DOM——
            # page.content()/frames 搜索失效；视频号同款修复先例）
            deadline = time.monotonic() + ECHO_WAIT_S
            while time.monotonic() < deadline:
                try:
                    if await page.get_by_text(content).count() > 0:
                        return SendResult(SendStatus.SENT, sent_at=int(time.time()))
                except Exception:  # noqa: BLE001
                    pass
                await asyncio.sleep(1.5)
            # 回显未命中：区分登录拦截（二次信号检测）与未知
            try:
                if await page.get_by_text(LOGIN_SIGNAL).count() > 0:
                    return SendResult(
                        SendStatus.FAILED, SendRejectReason.PLATFORM_REJECTED.value,
                        fix_hint="抖音登录态已失效（发送后出现登录提示）——重跑 "
                                 "python -m danmaku_listener.engines.douyin_login <room_id> 扫码",
                        docs_anchor="docs/ops/send-runbook.md")
            except Exception:  # noqa: BLE001
                pass
            return SendResult(SendStatus.UNKNOWN, SendRejectReason.SEND_TIMEOUT.value,
                              detail="无回显无显式错误（shadow DOM 回显 15s 未命中——虚拟列表/慢渲染可能）")
        return action


class DouyinProfileSenderLegacy(BaseSender):
    """.. deprecated:: T3——旧瞬态开关窗口路径（DX-D9 deprecated 保留：T4 参照+回退）。

    每次发送 launch_persistent_context(headed) → goto → 发送 → close（窗口闪烁+
    冷启动行为可疑+单条全链路开销——不可运营形态，常驻会话替代）。
    回退方式：wiring 将 DouyinProfileSender 换回本类（步骤见计划 T3 交付说明）。
    """

    platform = "douyin"

    def __init__(self, profile_dir: Optional[str] = None, headed: bool = True):
        self._profile_dir = profile_dir or PROFILE_DIR
        self._headed = headed

    async def send(self, room_id: str, content: str) -> SendResult:
        from playwright.async_api import async_playwright

        try:
            async with async_playwright() as pw:
                context = await pw.chromium.launch_persistent_context(
                    self._profile_dir,
                    headless=not self._headed,
                    viewport={"width": 1280, "height": 800},
                    args=["--disable-blink-features=AutomationControlled",
                          "--disable-setuid-sandbox",
                          "--hide-crash-restore-bubble"])
                try:
                    page = context.pages[0] if context.pages else await context.new_page()
                    try:
                        await page.goto(f"https://live.douyin.com/{room_id}",
                                        timeout=45000, wait_until="domcontentloaded")
                    except Exception as e:  # noqa: BLE001
                        return SendResult(SendStatus.UNKNOWN, SendRejectReason.SEND_TIMEOUT.value,
                                          detail=f"goto: {type(e).__name__}")
                    await asyncio.sleep(3)
                    if await page.locator(INPUT_SEL).first.count() == 0:
                        try:
                            await page.screenshot(path="persistence_data/douyin-send-blocked.png")
                        except Exception:  # noqa: BLE001
                            pass
                        return SendResult(SendStatus.FAILED, SendRejectReason.PLATFORM_REJECTED.value,
                                          fix_hint="聊天面板未出现——profile 登录态失效，重跑 douyin_login 扫码",
                                          docs_anchor="docs/ops/send-runbook.md")
                    await page.locator(INPUT_SEL).first.fill(content)
                    try:
                        await page.locator(BUTTON_SEL).first.click(timeout=5000, force=True)
                    except Exception:  # noqa: BLE001
                        await page.locator(INPUT_SEL).first.press("Enter")
                    await asyncio.sleep(2.5)
                    echo_deadline = time.monotonic() + 8
                    while time.monotonic() < echo_deadline:
                        if content in await page.content():
                            return SendResult(SendStatus.SENT, sent_at=int(time.time()))
                        await asyncio.sleep(1.5)
                    return SendResult(SendStatus.UNKNOWN, SendRejectReason.SEND_TIMEOUT.value,
                                      detail="无回显无显式错误（旧瞬态路径）")
                finally:
                    try:
                        await context.close()
                    except Exception:  # noqa: BLE001
                        pass
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[douyin-legacy] send error: {type(e).__name__}: {str(e)[:100]}")
            return SendResult(SendStatus.FAILED, SendRejectReason.PLATFORM_REJECTED.value,
                              detail=f"{type(e).__name__}: {str(e)[:100]}")
