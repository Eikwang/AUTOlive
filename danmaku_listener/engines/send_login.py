"""斗鱼/虎牙发送侧登录 CLI（T6/DX-D1——凭证生成入口，三平台从零配置闭环）

斗鱼：cookie 文件形态（cookie/douyu_login_cookies.json——acf_* 登录字段，
      复用 login_gate.ensure_cookie_file_login 闭环，非持久 context+cookie 轮询）
虎牙：persistent profile 形态（cookie/huya_login_profile——DOM 发送会话用，
      与 douyin_login 同款模式：persistent context 开登录页轮询 cookie 进 profile）

用法：
  python -m danmaku_listener.engines.douyu_login [room_id]
  python -m danmaku_listener.engines.huya_login [room_id]
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

from loguru import logger

DEFAULT_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
              "AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/131.0.0.0 Safari/537.36")
DOUYU_COOKIE_FILE = "cookie/douyu_login_cookies.json"
DOUYU_COOKIE_NAMES = ("acf_uid", "acf_auth")
HUYA_PROFILE_DIR = "cookie/huya_login_profile"
HUYA_COOKIE_NAMES = ("yyuid", "hicl_imid", "huya_uid")


async def _douyu_login(room_id: str = "", timeout: float = 240.0) -> dict:
    """斗鱼：复用 login_gate.ensure_cookie_file_login 闭环（cookie 文件形态）"""
    from danmaku_listener.engines.login_gate import ensure_cookie_file_login

    login_url = f"https://www.douyu.com/{room_id}" if room_id else "https://www.douyu.com/"
    outcome = await ensure_cookie_file_login(
        room_id=room_id or "send-cli", platform="douyu", login_url=login_url,
        cookie_path=DOUYU_COOKIE_FILE, stop_flags={}, budgets={"send-cli": 1},
        budget_warned=set(), emit_status=lambda s: print(f"[status] {s}"))
    return {"status": "ok" if outcome == "logged_in" else outcome,
            "cookie_file": DOUYU_COOKIE_FILE}


async def _huya_login(room_id: str = "", timeout: float = 240.0) -> dict:
    """虎牙：persistent profile 登录（DOM 发送会话的登录态载体）"""
    from playwright.async_api import async_playwright

    profile = Path(HUYA_PROFILE_DIR)
    profile.mkdir(parents=True, exist_ok=True)
    url = f"https://www.huya.com/{room_id}" if room_id else "https://www.huya.com/"
    async with async_playwright() as pw:
        context = await pw.chromium.launch_persistent_context(
            str(profile), headless=False, user_agent=DEFAULT_UA,
            viewport={"width": 1280, "height": 800},
            args=["--disable-blink-features=AutomationControlled",
                  "--hide-crash-restore-bubble"])
        try:
            page = context.pages[0] if context.pages else await context.new_page()
            print("[status] login_page_opened")
            try:
                await page.goto(url, timeout=45000, wait_until="domcontentloaded")
            except Exception as e:  # noqa: BLE001
                logger.debug(f"[huya-login] goto warning: {e}")
            deadline = time.monotonic() + timeout
            logged = False
            while time.monotonic() < deadline:
                try:
                    cookies = {c["name"] for c in await context.cookies() if c.get("value")}
                except Exception:  # noqa: BLE001  窗口被关
                    break
                if set(HUYA_COOKIE_NAMES) & cookies:
                    logged = True
                    # 登录后保窗宽限（2026-10-09 虎牙验收实证：登录落地即关窗杀安全
                    # 验证，会话数分钟内被服务端作废——淘宝/抖音同款缺陷第三例）
                    grace_deadline = time.monotonic() + 30.0
                    while time.monotonic() < grace_deadline:
                        try:
                            await context.cookies()
                        except Exception:
                            break
                        await asyncio.sleep(2)
                    logger.info("[huya-login] login grace ended (30s)——窗口关闭")
                    break
                if page.is_closed():
                    break
                await asyncio.sleep(2)
            if not logged:
                print("[status] login_timeout_or_closed")
                return {"status": "timeout_or_closed", "profile_dir": HUYA_PROFILE_DIR}
            print("[status] login_ok")
            return {"status": "ok", "profile_dir": HUYA_PROFILE_DIR}
        finally:
            try:
                await context.close()
            except Exception:  # noqa: BLE001
                pass


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="斗鱼/虎牙发送侧登录（DX-D1 凭证生成入口）")
    parser.add_argument("platform", choices=("douyu", "huya"))
    parser.add_argument("room_id", nargs="?", default="",
                        help="直播间号（可选）")
    parser.add_argument("--timeout", type=float, default=240.0)
    args = parser.parse_args()
    result = asyncio.run(
        _douyu_login(args.room_id, args.timeout) if args.platform == "douyu"
        else _huya_login(args.room_id, args.timeout))
    print(f"[result] {json.dumps(result, ensure_ascii=False)}")
    if result.get("status") != "ok":
        raise SystemExit(1)
