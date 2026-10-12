"""瞬态注入 DOM sender 公共基类（AutoDanmu T6——快手/斗鱼；ENG-10 与 T3 同规格单测）

形态（T1/X1 实证配方固化，tools/send_probes/ M0 探针 + common.py DOM_PLATFORMS）：
- 注入式瞬态 context（storage_state 或 cookie 文件）——无 profile，天然无锁需求
- 反检测参数 + F5 回显判定（get_by_text 穿透 shadow DOM——2026-10-07 下午起各平台
  聊天流陆续 shadow 化，content() 搜索失效；视频号/抖音同款修复）
- 登录态失效（输入框不出现/未登录信号）→ PLATFORM_REJECTED + NEEDS_LOGIN 同款
  fix_hint（重跑对应平台登录 CLI——DX-D1 凭证入口）
- 滑块/风控信号感知 → FAIL（不重试不硬刚）

平台差异全部为构造参数（URL 模板/选择器/凭证形态）——DRY：一个基类两实例。
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Optional

from loguru import logger
from playwright.async_api import async_playwright  # 模块级：测试注入点

from danmaku_listener.contract.models import SendRejectReason, SendStatus
from danmaku_listener.senders.base import BaseSender, SendResult

PAGE_TIMEOUT_MS = 30_000
ECHO_WAIT_S = 12.0

RISK_SIGNALS = ("验证码", "滑动验证", "安全验证", "禁言", "被禁言", "操作过于频繁", "稍后再试")
LOGIN_SIGNALS = ("需先登录", "请登录", "登录后")


class DomTransientSender(BaseSender):
    """瞬态注入 DOM 发送基类（快手=storage_state；斗鱼=cookie 文件——构造参数差异化）"""

    platform = ""
    room_url_template: str = ""          # {room_id} 占位
    login_hint: str = ""                 # 凭证失效 fix_hint（DX-D1 CLI 命令）
    input_selectors: tuple = ()          # 候选序（M0 探针 found 字段优先）
    button_selectors: tuple = ()         # 空则 Enter 提交
    cookie_file: str = ""                # 凭证文件路径（子类定）
    storage_state_mode: bool = False     # True=playwright storage_state 注入；False=cookie 数组注入
    home_url: str = ""                   # 注入后先访问的域首页（cookie 作用域激活）

    async def send(self, room_id: str, content: str) -> SendResult:
        if not self.room_url_template:
            return SendResult(SendStatus.FAILED, SendRejectReason.ROUTE_UNVERIFIED.value,
                              fix_hint="平台发送配方未配置（M0 探针后交付）",
                              docs_anchor="docs/testing/m0-send-probe-cards.md")
        async with async_playwright() as pw:
            # headless=new（完整 Chrome 指纹）——旧 headless（headless_shell）被平台
            # 检测/前端降级（T1 抖音实证 bd_ticket_guard；快手"请求过快"/"错误代码22"
            # 同源嫌疑）——2026-10-07 T6 实测对齐抖音形态
            context = await pw.chromium.launch_persistent_context(
                self._temp_profile_dir(), headless=False,
                user_agent=self._ua(), viewport={"width": 1280, "height": 800},
                args=["--disable-blink-features=AutomationControlled",
                      "--disable-setuid-sandbox", "--hide-crash-restore-bubble",
                      "--headless=new"])
            try:
                if not await self._inject_credentials(context):
                    return SendResult(SendStatus.FAILED, SendRejectReason.PLATFORM_REJECTED.value,
                                      detail=f"凭证文件不存在或损坏: {self.cookie_file}",
                                      fix_hint=f"{self.platform} 凭证缺失——{self.login_hint}",
                                      docs_anchor="docs/ops/send-runbook.md")
                page = context.pages[0] if context.pages else await context.new_page()
                try:
                    await page.goto(self.room_url_template.format(room_id=room_id),
                                    timeout=PAGE_TIMEOUT_MS, wait_until="domcontentloaded")
                except Exception as e:  # noqa: BLE001
                    return SendResult(SendStatus.UNKNOWN, SendRejectReason.SEND_TIMEOUT.value,
                                      detail=f"goto: {type(e).__name__}")
                await asyncio.sleep(3)
                inp = await self._find_input(page)
                if inp is None:
                    if await self._has_login_signal(page):
                        return SendResult(SendStatus.FAILED, SendRejectReason.PLATFORM_REJECTED.value,
                                          fix_hint=f"{self.platform} 登录态失效——{self.login_hint}",
                                          docs_anchor="docs/ops/send-runbook.md")
                    return SendResult(SendStatus.FAILED, SendRejectReason.PLATFORM_REJECTED.value,
                                      detail="未发现发送输入框（页面结构变更/未登录）——重跑 M0 探针",
                                      docs_anchor="docs/testing/m0-send-probe-cards.md")
                # 1688 实证配方：click 聚焦+逐键输入+发送（fill 不进框架 state 风险）
                await inp.click(timeout=10_000)
                await asyncio.sleep(0.4)
                await inp.press_sequentially(content, delay=40)
                await asyncio.sleep(0.3)
                if self.button_selectors:
                    btn = await self._find_button(page)
                    if btn is not None:
                        try:
                            await btn.click(timeout=5_000)
                        except Exception:  # noqa: BLE001
                            await inp.press("Enter")
                    else:
                        await inp.press("Enter")
                else:
                    await inp.press("Enter")
                await asyncio.sleep(2)
                # F5 判定：get_by_text 穿透 shadow DOM；风控/登录信号感知
                if await self._risk_signal(page):
                    return SendResult(SendStatus.FAILED, SendRejectReason.PLATFORM_REJECTED.value,
                                      detail="风控信号（滑块/禁言/频率）",
                                      fix_hint="平台风控拦截——降频稍后重试；勿重扫码")
                deadline = time.monotonic() + ECHO_WAIT_S
                while time.monotonic() < deadline:
                    try:
                        if await page.get_by_text(content).count() > 0:
                            return SendResult(SendStatus.SENT, sent_at=int(time.time()))
                        if await self._has_login_signal(page):
                            return SendResult(SendStatus.FAILED, SendRejectReason.PLATFORM_REJECTED.value,
                                              fix_hint=f"{self.platform} 登录态失效——{self.login_hint}",
                                              docs_anchor="docs/ops/send-runbook.md")
                    except Exception:  # noqa: BLE001
                        pass
                    await asyncio.sleep(1.5)
                return SendResult(SendStatus.UNKNOWN, SendRejectReason.SEND_TIMEOUT.value,
                                  detail="无回显无显式错误（shadow DOM/虚拟列表/慢渲染可能）")
            finally:
                try:
                    await context.close()
                except Exception:  # noqa: BLE001
                    pass

    # ---- 平台钩子/工具 ----

    def _ua(self) -> str:
        return ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36")

    def _temp_profile_dir(self) -> str:
        """瞬态 context 的空 profile（注入式无会话依赖；独立临时目录避免撞并发）"""
        import os
        import tempfile
        d = os.path.join(tempfile.gettempdir(), f"danmu-send-{self.platform}")
        os.makedirs(d, exist_ok=True)
        return d

    async def _inject_credentials(self, context) -> bool:
        """凭证注入：storage_state（快手）或 cookie 数组（斗鱼）；文件缺失→False"""
        cf = Path(self.cookie_file)
        if not cf.is_file():
            return False
        try:
            # 凭证导入=只读文件→add_cookies。**禁止 context.storage_state(path=)**——
            # 那是导出语义（会把空 context 状态覆盖写回凭证文件——2026-10-07 实损事故：
            # kuaishou_storage_state.json 被覆盖，cookies 17→9，修复后需重扫码）
            raw = json.loads(cf.read_text(encoding="utf-8"))
            cookies = (raw.get("cookies") or []) if isinstance(raw, dict) else (raw or [])
            if cookies:
                await context.add_cookies(cookies)
            if self.home_url:
                # 访问首页激活 cookie 作用域（登录态 cookie 落域后才可见输入框）
                page0 = context.pages[0] if context.pages else await context.new_page()
                try:
                    await page0.goto(self.home_url, timeout=PAGE_TIMEOUT_MS,
                                     wait_until="domcontentloaded")
                except Exception:  # noqa: BLE001
                    pass
            return True
        except Exception as e:  # noqa: BLE001 凭证损坏按缺失处理
            logger.warning(f"[{self.platform}] 凭证注入失败: {type(e).__name__}: {str(e)[:80]}")
            return False

    async def _find_input(self, page):
        for sel in self.input_selectors:
            try:
                loc = page.locator(sel).first
                if await loc.count() > 0 and await loc.is_visible():
                    return loc
            except Exception:  # noqa: BLE001
                continue
        return None

    async def _find_button(self, page):
        for sel in self.button_selectors:
            try:
                loc = page.locator(sel).first
                if await loc.count() > 0 and await loc.is_visible():
                    return loc
            except Exception:  # noqa: BLE001
                continue
        return None

    async def _has_login_signal(self, page) -> bool:
        for s in LOGIN_SIGNALS:
            try:
                if await page.get_by_text(s).count() > 0:
                    return True
            except Exception:  # noqa: BLE001
                continue
        return False

    async def _risk_signal(self, page) -> bool:
        for s in RISK_SIGNALS:
            try:
                if await page.get_by_text(s).count() > 0:
                    return True
            except Exception:  # noqa: BLE001
                continue
        return False
