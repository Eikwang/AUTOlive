"""平台登录门槛共享基建（2026-10-06 平台登录计划）

常量与纯函数供各引擎登录闭环复用：
- taobao.py（BaseEngine 系）持有一份同源副本——跨层级统一 defer（0G#4 面板定型后收敛）
- ControlledPageEngine（jd/xiaohongshu/1688 系）直接 import 本模块

判定边界（spec 审查 1.1）：登录 cookie 存在性判定仅用于**首次登录检测**；
会话失效重登需行为证据（pdd 2026-10-05 实证：会话作废后 cookie 仍在，
存在性判定失真）。
"""

from typing import Iterable, Optional

import asyncio
import json
import time
from pathlib import Path

from loguru import logger

# ---- ENGINE_STATUS 登录生命周期事件词表（CEO F1——taobao 首用，全平台复用）----
LOGIN_EVENT_FIRST_LOGIN = "login.first_login"
LOGIN_EVENT_RELOGIN_TRIGGERED = "login.relogin_triggered"
LOGIN_EVENT_BUDGET_EXHAUSTED = "login.timeout_budget_exhausted"
LOGIN_EVENT_DEGRADED_DETECTED = "login.degraded_detected"

#: 登录窗口等待上限（独立 deadline——不套各引擎 attempt 档位，Eng F8）
LOGIN_WAIT_TIMEOUT = 300.0
#: 每房间超时预算（首登/重登共用；内存态、App 重启清零——CEO F5）
LOGIN_BUDGET = 2
#: 登录窗口 cookie 轮询间隔
LOGIN_POLL_INTERVAL = 2.0

#: 各平台登录态判定 cookie（平台 → 候选名集合；任一存在且有值即视为已登录）
LOGIN_COOKIE_NAMES = {
    "taobao": ("unb",),                      # 阿里系统一账号标识（live1688 同源）
    "1688": ("unb",),
    "jd": ("pt_key", "pt_pin"),              # 京东账号对（pt_key 为会话、pt_pin 为账号）
    "xiaohongshu": ("web_session",),         # 小红书登录会话
    "douyu": ("dedeuserid",),                # 斗鱼账号 uid（cookie 文件形态）
    # huya：登录 cookie 名待 spike 期确认（2026-10-06 计划 T9）——多候选+日志校准
    "huya": ("yyuid", "hiido_ui", "u_db_uid"),
}


def has_login_cookie(cookies: Optional[Iterable[dict]],
                     names: Iterable[str]) -> bool:
    """登录态判定：候选 cookie 任一存在且有非空值（live1688 同判定泛化）"""
    wanted = set(names)
    for c in cookies or []:
        if c.get("name") in wanted and (c.get("value") or "").strip():
            return True
    return False


def mask_cookie(value: str) -> str:
    """cookie 值日志掩码（Eng F2）：长度 + sha256 前 8 位，不打明文"""
    import hashlib

    if not value:
        return "(empty)"
    return f"len={len(value)} sha256={hashlib.sha256(value.encode()).hexdigest()[:8]}"


async def ensure_cookie_file_login(
    room_id: str, platform: str, login_url: str,
    cookie_path, stop_flags: dict, budgets: dict, budget_warned: set,
    emit_status, *,
    clock=None, sleep_fn=None, poll_interval: float = LOGIN_POLL_INTERVAL,
) -> str:
    """cookie 文件形态登录闭环（douyu/huya——TCP 直连引擎专用，2026-10-06 用户裁定 B）。

    协议直连引擎无 profile/页面会话——登录态落地为 cookie 文件（JSON），
    供未来协议支持时使用。**诚实边界**：登录态不进当前 TCP 协议连接。

    流程：cookie 文件已有登录态 → "logged_in"；无 → 预算内弹可见窗口
    打开 login_url → 轮询 cookie（候选名出现即登录）→ 存档文件 → "logged_in"。
    超时/关窗/停止/预算耗尽 语义与受控页面引擎一致（CEO F5/F8、Eng F3/F4）。

    Returns:
        "logged_in" | "timeout" | "window_closed" | "stopped" | "budget_exhausted"
    """
    from playwright.async_api import async_playwright

    clock = clock or time.monotonic
    sleep_fn = sleep_fn or asyncio.sleep
    names = LOGIN_COOKIE_NAMES.get(platform, ())

    # 1. 文件已有登录态 → 跳过
    cookie_file = Path(cookie_path)
    if cookie_file.is_file():
        try:
            stored = json.loads(cookie_file.read_text(encoding="utf-8"))
            if has_login_cookie(stored.get("cookies") or [], names):
                return "logged_in"
        except Exception:  # noqa: BLE001  损坏文件按未登录处理
            pass

    # 2. 预算检查（弹出前扣减）
    budget = budgets.get(room_id, LOGIN_BUDGET)
    if budget <= 0:
        if room_id not in budget_warned:
            budget_warned.add(room_id)
            await emit_status(
                f"{LOGIN_EVENT_BUDGET_EXHAUSTED}: 登录未完成——已按游客模式继续；"
                "停止该房间后重新添加可再次触发登录窗口")
        return "budget_exhausted"
    budgets[room_id] = budget - 1

    # 3. 可见窗口（普通 context——协议直连引擎无 persistent profile 约定）
    await emit_status(
        f"{LOGIN_EVENT_FIRST_LOGIN}: {platform} 登录增强——已弹出浏览器，"
        "请登录账号（当前协议直连不使用登录态，登录凭证存档备用）")
    baseline_names: set = set()
    async with async_playwright() as pw:
        try:
            context = await pw.chromium.launch_persistent_context(
                str(cookie_file.parent / f"{platform}_login_profile"),
                headless=False,
                args=["--disable-blink-features=AutomationControlled"],
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[{platform}] room {room_id} login window launch failed: "
                           f"{type(e).__name__}: {str(e)[:80]}")
            return "window_closed"
        try:
            page = context.pages[0] if context.pages else await context.new_page()
            try:
                await page.goto(login_url, timeout=30000, wait_until="domcontentloaded")
            except Exception as e:  # noqa: BLE001
                logger.debug(f"[{platform}] room {room_id} login page goto warning: {e}")
            try:
                baseline_names = {c.get("name") for c in await context.cookies()}
            except Exception as e:  # noqa: BLE001
                logger.info(f"[{platform}] room {room_id} login window closed before "
                            f"baseline ({type(e).__name__})")
                return "window_closed"
            deadline = clock() + LOGIN_WAIT_TIMEOUT
            while clock() < deadline:
                if stop_flags.get(room_id):  # Eng F3
                    return "stopped"
                try:
                    cookies = await context.cookies()
                except Exception as e:  # noqa: BLE001  # Eng F4
                    logger.info(f"[{platform}] room {room_id} login window closed "
                                f"by user ({type(e).__name__})")
                    return "window_closed"
                if has_login_cookie(cookies, names):
                    # 存档（Eng F2：日志只打名+掩码）
                    names_all = sorted(c.get("name", "") for c in cookies if c.get("name"))
                    hit = next((c for c in cookies if c.get("name") in names
                                and (c.get("value") or "").strip()), None)
                    logger.info(f"[{platform}] room {room_id} login ok: "
                                f"cookies={names_all} "
                                f"hit_mask={mask_cookie((hit or {}).get('value', ''))}")
                    cookie_file.parent.mkdir(parents=True, exist_ok=True)
                    cookie_file.write_text(
                        json.dumps({"cookies": cookies}, ensure_ascii=False, indent=2),
                        encoding="utf-8")
                    return "logged_in"
                await sleep_fn(poll_interval)
            return "timeout"
        finally:
            try:
                await context.close()
            except Exception:  # noqa: BLE001
                pass
    return "timeout"
