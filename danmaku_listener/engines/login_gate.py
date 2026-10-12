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
#: 登录后保窗宽限（2026-10-09 小红书验收实证：登录落地即关窗杀安全验证——
#: 淘宝/抖音同款缺陷第四例；共享函数一处修复覆盖全部受控页面平台）
LOGIN_POST_GRACE_S = 30.0

#: 各平台登录态判定 cookie（平台 → 候选名集合；任一存在且有值即视为已登录）
LOGIN_COOKIE_NAMES = {
    "taobao": ("unb",),                      # 阿里系统一账号标识（live1688 同源）
    "1688": ("unb",),
    "jd": ("thor", "pin"),                   # 京东现代登录令牌（2026-10-10 用户真实扫码
                                             # 登录的 Cookies 库取证：thor/pin 均持久
                                             # 至 2027-11-14；pt_key/pt_pin 为老体系
                                             # 从未出现——此前判定恒假致门/发送全废）
    "xiaohongshu": ("id_token",),            # 小红书登录凭证（2026-10-06 实机 probe
                                             # 实证：游客态就带 web_session 匿名会话——
                                             # 用它判定即虚假登录；登录后才新增 id_token JWT）
    "douyu": ("acf_uid", "acf_auth"),        # 斗鱼账号 UID/auth（2026-10-06 实机 probe
                                             # 实证 36 cookie 无 dedeuserid——acf_* 家族）
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

    # 3. 可见窗口（**非持久 browser+context**——2026-10-06 用户实测：persistent
    #    profile 会恢复上次会话标签页（旧房间页）且 goto 失败被吞——登录窗无需
    #    持久 profile，cookie 文件才是持久层；每次全新 context 无会话恢复）
    await emit_status(
        f"{LOGIN_EVENT_FIRST_LOGIN}: {platform} 登录增强——已弹出浏览器，"
        "请登录账号（当前协议直连不使用登录态，登录凭证存档备用）")
    baseline_names: set = set()
    async with async_playwright() as pw:
        try:
            browser = await pw.chromium.launch(
                headless=False,
                args=["--disable-blink-features=AutomationControlled",
                      "--hide-crash-restore-bubble"])
            context = await browser.new_context(
                user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/131.0.0.0 Safari/537.36"))
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
            last_cookies = []
            while clock() < deadline:
                if stop_flags.get(room_id):  # Eng F3
                    return "stopped"
                try:
                    cookies = await context.cookies()
                except Exception as e:  # noqa: BLE001  # Eng F4
                    logger.info(f"[{platform}] room {room_id} login window closed "
                                f"by user ({type(e).__name__})")
                    # 登录后直接关窗（2026-10-06 用户实测：轮询 2s 间隔可能没
                    # 抓到登录 cookie 就关窗）——用最后已知 cookie 判定+存档，
                    # 登录态不丢，避免重复弹窗
                    cookies = last_cookies
                    if has_login_cookie(cookies, names):
                        names_all = sorted(c.get("name", "") for c in cookies if c.get("name"))
                        hit = next((c for c in cookies if c.get("name") in names
                                    and (c.get("value") or "").strip()), None)
                        logger.info(f"[{platform}] room {room_id} login ok (recovered "
                                    f"from window close): cookies={names_all} "
                                    f"hit_mask={mask_cookie((hit or {}).get('value', ''))}")
                        cookie_file.parent.mkdir(parents=True, exist_ok=True)
                        cookie_file.write_text(
                            json.dumps({"cookies": cookies}, ensure_ascii=False, indent=2),
                            encoding="utf-8")
                        return "logged_in"
                    return "window_closed"
                last_cookies = cookies
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
                await browser.close()  # 非持久 browser——连同浏览器进程关闭
            except Exception:  # noqa: BLE001
                pass
    return "timeout"
