"""B站受控浏览器登录流程（独立分发自动 cookie 获取）

用户体验流：
1. 添加 bilibili 房间 → 检测无登录 cookie → 系统弹出可见浏览器窗口（B站登录页）
2. 用户在窗口里扫码/登录（凭证操作完全由用户执行，系统不接触密码）
3. 引擎检测登录成功（SESSDATA cookie 出现）→ 自动保存 storage_state → 窗口关闭
4. 引擎用新登录态重新获取 token → 弹幕流自动恢复

合规：专用监听小号（docs/ops/compliance-review.md §3）；登录操作由用户在
可见窗口自行完成，系统仅保存 cookie 结果。
"""

import asyncio
import json
import os
import time
from typing import Any, Callable, Dict, Optional

from loguru import logger

LOGIN_URL = "https://passport.bilibili.com/login"
DEFAULT_STATE_PATH = "cookie/bilibili_storage_state.json"
DEFAULT_TIMEOUT = 300.0  # 5 分钟登录窗口

#: 判定登录成功的标志 cookie（存在即视为已登录）
LOGIN_MARKER_COOKIES = {"SESSDATA"}


def has_login_cookie(state_path: str) -> bool:
    """检测 storage_state 是否含登录标志 cookie（SESSDATA）"""
    if not os.path.exists(state_path):
        return False
    try:
        with open(state_path, encoding="utf-8") as fh:
            state = json.load(fh)
        return any(
            c.get("name") == "SESSDATA" and c.get("value")
            for c in state.get("cookies", [])
        )
    except (json.JSONDecodeError, OSError):
        return False


async def run_login_flow(
    state_path: str = DEFAULT_STATE_PATH,
    headless: bool = False,
    timeout: float = DEFAULT_TIMEOUT,
    on_status: Optional[Callable[[str], Any]] = None,
) -> Dict[str, Any]:
    """受控浏览器登录流程（阻塞直到登录成功/超时/用户关闭）

    Args:
        state_path: storage_state 保存路径
        headless: False=可见窗口（用户操作需要）
        timeout: 登录等待上限（秒）
        on_status: 状态回调（NEEDS_LOGIN 告警用）

    Returns:
        {"status": "ok"|"timeout"|"closed", "cookies": {name: value}}
    """
    from playwright.async_api import async_playwright

    os.makedirs(os.path.dirname(state_path) or ".", exist_ok=True)

    def notify(status: str) -> None:
        if on_status:
            try:
                result = on_status(status)
                if asyncio.iscoroutine(result):
                    asyncio.ensure_future(result)
            except Exception as e:  # noqa: BLE001
                logger.debug(f"login status callback error: {e}")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=headless)
        try:
            context = await browser.new_context(viewport={"width": 1200, "height": 800})
            page = await context.new_page()
            notify("login_page_opened")
            await page.goto(LOGIN_URL, wait_until="domcontentloaded")

            deadline = time.monotonic() + timeout
            logged_in = False
            while time.monotonic() < deadline:
                cookies = await context.cookies()
                names = {c["name"] for c in cookies}
                if LOGIN_MARKER_COOKIES & names:
                    logged_in = True
                    break
                if page.is_closed():
                    break
                await asyncio.sleep(2)

            if not logged_in:
                notify("login_timeout_or_closed")
                return {"status": "timeout_or_closed", "cookies": {}}

            # 登录成功：保存 storage_state + 提取 cookie 字典
            await context.storage_state(path=state_path)
            cookie_dict = {
                c["name"]: c["value"]
                for c in await context.cookies()
                if c.get("value")
            }
            notify("login_ok")
            logger.info(f"[bilibili] login flow OK ({len(cookie_dict)} cookies saved)")
            return {"status": "ok", "cookies": cookie_dict}
        finally:
            await browser.close()
