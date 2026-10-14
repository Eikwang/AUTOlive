"""抖音受控浏览器登录流程（对齐 kuaishou_login / bilibili_login 模式）

背景（2026-10-03 五轮采样实证）：抖音 Web WS 的礼物事件（WebcastGiftMessage）
**只推给登录观众**——游客连接（ttwid）收弹幕/进场/点赞/关注/房间统计，
礼物事件缺失（页面+引擎双通道验证，游客连接 4 分钟 0 条含送礼者昵称）。
登录态 cookie（sessionid）注入 WS 握手后礼物进入推送集合。

体验流（与快手登录闭环同构）：
1. 添加 douyin 房间 → 检测无登录 cookie → 系统弹出可见浏览器窗口
2. 用户在窗口里扫码/登录（凭证操作完全由用户执行，系统不接触密码）
3. 引擎检测登录成功（sessionid cookie 出现）→ 自动保存 → 窗口关闭
4. 引擎 WS 握手带登录 cookie → 完整消息集合

合规：专用监听小号（docs/ops/compliance-review.md §3）；登录操作由用户在
可见窗口自行完成，系统仅保存 cookie 结果。
"""

import asyncio
import json
import os
import time
from typing import Any, Callable, Dict, Optional

from loguru import logger

DEFAULT_COOKIE_FILE = "cookie/douyin_cookies.json"
DEFAULT_TIMEOUT = 300.0  # 5 分钟登录窗口
#: 登录后保窗宽限（2026-10-08 验收用户实证：抖音同款淘宝缺陷——登录落地即关窗
#: 杀掉安全滑块，会话半授权→发送 PLATFORM_REJECTED。保窗 30s 供人工完成验证）
LOGIN_POST_GRACE_S = 30.0

#: 判定登录成功的标志 cookie（sessionid 为主站会话身份；ss 为同值安全副本）
LOGIN_MARKER_COOKIES = {"sessionid", "sessionid_ss"}

LOGIN_PAGE_URL = "https://live.douyin.com/"


def has_login_cookie(cookie_path: str = DEFAULT_COOKIE_FILE) -> bool:
    """检测 cookie 文件是否含登录标志 cookie"""
    if not os.path.exists(cookie_path):
        return False
    try:
        with open(cookie_path, encoding="utf-8") as fh:
            cookies = json.load(fh)
        return any(
            c.get("name") in LOGIN_MARKER_COOKIES and c.get("value")
            for c in cookies
        )
    except (json.JSONDecodeError, OSError):
        return False


def load_login_cookies(cookie_path: str = DEFAULT_COOKIE_FILE) -> Dict[str, str]:
    """返回登录 cookie 字典（未登录/文件损坏返回空 dict）"""
    if not os.path.exists(cookie_path):
        return {}
    try:
        with open(cookie_path, encoding="utf-8") as fh:
            cookies = json.load(fh)
        return {c["name"]: c["value"] for c in cookies
                if c.get("name") and c.get("value")}
    except (json.JSONDecodeError, OSError):
        return {}


# ============ AutoDanmu profile 登录闭环（2026-10-07——cookie 文件注入被
# bd_ticket_guard 指纹绑定拒绝；persistent profile 指纹匹配才能建立网页登录态） ============

PROFILE_DIR = "cookie/douyin_profile"


def has_profile_login(profile_dir: str = PROFILE_DIR) -> bool:
    """profile 是否已有登录态（Chromium Cookies SQLite 存在性即初判；
    会话失效需行为证据——login_gate 同款边界）"""
    from pathlib import Path

    d = Path(profile_dir)
    return ((d / "Default" / "Network" / "Cookies").is_file()
            or (d / "Default" / "Cookies").is_file())


#: 监听侧 cookie 域白名单（Eng 发现11：douyin.com 系；排除式→白名单式。
#: F7 段匹配=全等或 endswith("." + d)——子域与带前导点 ".douyin.com" 形态由
#: endswith 覆盖；伪后缀域（notdouyin.com）与旧误收的 "douyincom" 不再命中）
_DOUYIN_COOKIE_DOMAIN_SUFFIXES = ("douyin.com",)
#: 会话级身份 cookie 排除约定（协议侧 room_init 同款排除——engines/protocol/
#: douyin.py 懒 import 本常量；2026-10-14 F13 双点维护单源）
DOUYIN_SESSION_COOKIE_NAMES = ("ttwid", "msToken")
#: 监听注入约定排除项（douyin.py:124-128——白名单之上的额外排除）
_DOUYIN_COOKIE_EXCLUDE_NAMES = set(DOUYIN_SESSION_COOKIE_NAMES)


async def _export_listen_cookies(context, export_path: str) -> str:
    """从 profile context 导出监听侧 cookie（域白名单过滤→原子落盘）

    Returns: "ok" | "failed"（失败不抛——调用方 finally 关窗不被阻断）
    """
    try:
        state = await context.storage_state()
        cookies = []
        for c in state.get("cookies", []):
            domain = str(c.get("domain", ""))
            if not any(domain == d or domain.endswith("." + d)
                       for d in _DOUYIN_COOKIE_DOMAIN_SUFFIXES):
                continue
            if c.get("name") in _DOUYIN_COOKIE_EXCLUDE_NAMES:
                continue
            if not c.get("value"):
                continue
            cookies.append(c)
        if not cookies:
            logger.warning("[douyin-login] 导出过滤后 cookie 为空——白名单未命中")
            return "failed"
        payload = json.dumps(cookies, ensure_ascii=False)
        tmp = export_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(payload)
        os.replace(tmp, export_path)
        logger.info(f"[douyin-login] 监听 cookie 导出 OK（{len(cookies)} 条）→ {export_path}")
        return "ok"
    except Exception as e:  # noqa: BLE001  导出失败不阻断关窗 finally（Eng 发现12）
        logger.warning(f"[douyin-login] 监听 cookie 导出失败: {type(e).__name__}: {str(e)[:100]}")
        return "failed"


async def run_profile_login_flow(
    room_id: str = "",
    profile_dir: str = PROFILE_DIR,
    headless: bool = False,
    timeout: float = DEFAULT_TIMEOUT,
    on_status: Optional[Callable[[str], Any]] = None,
    export_cookie_path: Optional[str] = None,
) -> Dict[str, Any]:
    """可见窗口扫码登录进 persistent profile：sessionid 出现即成功（指纹随之绑定）

    export_cookie_path（2026-10-13 会话刷新编排 S2 单窗双喂）：登录落地后、
    finally 关 context 前从该 context 导出 storage_state，按 douyin.com 域白名单
    过滤（Eng 发现11：排除式会把 profile 积累的无关 cookie 一并写入；ttwid/
    msToken 排除约定保留为白名单之上的额外排除）后原子落盘。导出失败→返回
    "export": "failed"（不阻断关窗 finally；编排层映射 refresh_failed）。

    Returns:
        {"status": "ok" | "timeout_or_closed", "profile_dir": profile_dir,
         "export": "ok" | "skipped" | "failed"（无 export_cookie_path 时=skipped）}
    """
    from playwright.async_api import async_playwright

    os.makedirs(profile_dir, exist_ok=True)
    url = f"https://live.douyin.com/{room_id}" if room_id else LOGIN_PAGE_URL

    def notify(status: str) -> None:
        if on_status:
            try:
                result = on_status(status)
                if asyncio.iscoroutine(result):
                    asyncio.ensure_future(result)
            except Exception as e:  # noqa: BLE001
                logger.debug(f"login status callback error: {e}")

    async with async_playwright() as pw:
        context = await pw.chromium.launch_persistent_context(
            profile_dir,
            headless=headless,
            viewport={"width": 1280, "height": 800},
            args=["--disable-blink-features=AutomationControlled",
                  "--disable-setuid-sandbox",
                  "--hide-crash-restore-bubble"],
        )
        try:
            page = context.pages[0] if context.pages else await context.new_page()
            # 旧态存活预检（S2：profile 已有存活会话→直接导出复用，不强迫重扫；
            # 回执 reused_existing 标注供 AUTOlive 区分「刷新了登录态」与「复用旧态」）
            reused_existing = False
            first_export = None
            try:
                pre = {c["name"] for c in await context.cookies() if c.get("value")}
                reused_existing = bool(LOGIN_MARKER_COOKIES & pre)
            except Exception:  # noqa: BLE001
                pass
            notify("login_page_opened")
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=45000)
            except Exception as e:  # noqa: BLE001
                logger.debug(f"[douyin-login] goto warning: {e}")

            deadline = time.monotonic() + timeout
            logged_in = False
            while time.monotonic() < deadline:
                try:
                    cookies = await context.cookies()
                except Exception:  # noqa: BLE001  窗口被用户关闭
                    break
                names = {c["name"] for c in cookies if c.get("value")}
                if LOGIN_MARKER_COOKIES & names:
                    logged_in = True
                    # 落地即导（2026-10-13 验收实证修复）：此刻 context 必活——
                    # 用户扫码后秒关窗（人的自然动作）会让 grace 后的导出撞上
                    # 已死 context（storage_state 抛错→export failed→监听
                    # cookie 文件不回→二次登录）。先导一次保底，grace 后活窗
                    # 再补导拿最新令牌（幂等原子落盘）。
                    if export_cookie_path:
                        first_export = await _export_listen_cookies(context, export_cookie_path)
                    # 登录后保窗宽限（淘宝同款修复——2026-10-08 验收实证）：
                    # 保窗 30s 供人工完成登录后安全验证；用户手动关窗立即提前
                    # 结束（登录态已入 profile，仍算成功）
                    grace_deadline = time.monotonic() + LOGIN_POST_GRACE_S
                    while time.monotonic() < grace_deadline:
                        try:
                            await context.cookies()
                        except Exception:
                            logger.info("[douyin-login] post-login window closed "
                                        "during grace——验证宽限提前结束")
                            break
                        if page.is_closed():
                            break
                        await asyncio.sleep(2)
                    logger.info(f"[douyin-login] login grace ended "
                                f"({LOGIN_POST_GRACE_S:.0f}s)——窗口关闭")
                    break
                if page.is_closed():
                    break
                await asyncio.sleep(2)

            if not logged_in:
                notify("login_timeout_or_closed")
                return {"status": "timeout_or_closed", "profile_dir": profile_dir,
                        "export": "skipped" if not export_cookie_path else "failed",
                        "reused_existing": reused_existing}
            export_status = "skipped"
            if export_cookie_path:
                export_status = first_export or "failed"
                if not page.is_closed():
                    # 补导（grace 期新令牌/首次失败重试）——失败保留落地即导结果
                    second = await _export_listen_cookies(context, export_cookie_path)
                    if second == "ok":
                        export_status = "ok"
            notify("login_ok")
            logger.info(f"[douyin-login] profile login OK → {profile_dir} "
                        f"(export={export_status}, reused_existing={reused_existing})")
            return {"status": "ok", "profile_dir": profile_dir, "export": export_status,
                    "reused_existing": reused_existing}
        finally:
            try:
                await context.close()
            except Exception:  # noqa: BLE001
                pass


# ---- CLI 入口（T3 死代码补齐——runbook 所述命令对齐；ENG-6 撞锁=SingletonLock 天然语义） ----

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="抖音 profile 扫码登录（douyin_profile 指纹绑定；发送会话/协议登录共用）")
    parser.add_argument("room_id", nargs="?", default="",
                        help="直播间 ID（可选——登录后打开该直播间页）")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT,
                        help=f"扫码等待超时（秒，默认 {DEFAULT_TIMEOUT}）")
    args = parser.parse_args()

    async def _cli_login() -> None:
        def _status(status: str) -> None:
            print(f"[status] {status}")

        result = await run_profile_login_flow(
            room_id=args.room_id, headless=False, timeout=args.timeout,
            on_status=_status)
        print(f"[result] {json.dumps(result, ensure_ascii=False)}")
        if result.get("status") != "ok":
            raise SystemExit(1)

    asyncio.run(_cli_login())
