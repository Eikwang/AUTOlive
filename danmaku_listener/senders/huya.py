"""虎牙常驻会话 sender（AutoDanmu T6/X1——ResidentSendSession 第二实例）

形态：huya_login_profile persistent + headless_new 完全后台（2026-10-10 用户
验收通过后裁定终态；STEALTH_JS 7 信号伪装经 init_scripts 接线；诊断期前台
形态完成使命回退——当日实证链：卡片 huya-dom-*（输入面 DOM 取证）/
huya-bg-*（后台形态）+ 用户可见窗口 a/b/c 流程）。ENG-8 flag 族由会话
管理器统一注入。冷却约束（普通账号 ~30s，10-14s 实测失败、35s 补发全过）
由 guard 的 per-platform 覆写内置默认 {"huya": 35} 兜底（senders/guard.py）
——sender 内不再自设限速。

配方（2026-10-10 DOM 取证终版，卡片 huya-dom-*）：输入面 `#pub_msg_input`
（textarea，placeholder"发条弹幕呗~"）+ `#msg_send_bt`；发现逻辑=逐匹配
扫描+可见且可编辑过滤（用户 devtools 路径实为包裹层 div 的教训固化）。
登录态：F1 快照/恢复（login_state_store 共享实现）+ 登录失效时可见窗转正
（login_takeover——登录后快照落盘自动转回后台会话形态）。
"""
from __future__ import annotations

import asyncio
import time

from loguru import logger

from danmaku_listener.contract.models import SendRejectReason, SendStatus
# 常量单源（2026-10-14 F12/F11）：HUYA_COOKIE_NAMES/HUYA_TAKEOVER_PARAMS 与
# STEALTH_JS 均收敛单一来源；send_login 模块级仅依赖 stdlib+loguru，无环
from danmaku_listener.engines.send_login import HUYA_COOKIE_NAMES, HUYA_TAKEOVER_PARAMS
from danmaku_listener.senders.base import BaseSender, SendResult
from danmaku_listener.senders._stealth import STEALTH_JS  # 2026-10-14 D5 裁定单源化（原双副本之一）
from danmaku_listener.senders.resident_session import (
    MODE_HEADLESS_NEW,
    LoginTakeoverError,
    ResidentSendSession,
)

PROFILE_DIR = "cookie/huya_login_profile"

INPUT_SELECTORS = ("#pub_msg_input",  # 2026-10-10 DOM 取证实测（真输入面=textarea
                   # placeholder"发条弹幕呗~"，卡片 huya-dom-20261010-*；用户
                   # devtools 路径 #J_RoomChatSpeaker > div > div 实为包裹层 div
                   # ——不可编辑，打字全空，已移除）
                   "#J_RoomChatSpeaker textarea",  # 结构兜底（speaker 容器内 textarea）
                   "textarea", "div[contenteditable=true]",
                   "input[placeholder*=弹幕]")
BUTTON_SELECTORS = ("#msg_send_bt",  # 2026-10-10 验收用户 devtools 实测（与 10-09 一致）
                    "[class*=send]", 'button:has-text("发送")', ".send-btn")
ECHO_WAIT_S = 12.0

#: 进程级会话单例（虎牙 profile 独立于抖音——第二实例参数化复用同一管理器类）
_session: ResidentSendSession | None = None


def _get_session(idle_timeout_s: int, window_mode: str) -> ResidentSendSession:
    global _session
    if _session is None or _session._window_mode != window_mode:
        _session = ResidentSendSession(
            "huya-send", PROFILE_DIR,
            # 2026-10-10 纯后台终态：headless=new 完整 Blink 指纹 + STEALTH_JS
            # 7 信号伪装（10-09 备而未接线——当日回退的对抗实验缺此变量）+
            # --mute-audio；INI send_window_mode 可覆写
            window_mode=window_mode, idle_timeout_s=idle_timeout_s,
            init_scripts=[f"({STEALTH_JS})()"],
            login_cookie_names=("yyuid", "hicl_imid", "huya_uid"))
    return _session


async def export_huya_archive(context) -> bool:
    """huya 域登录 cookie 写监听存档（login_gate 同格式 {"cookies":[...]}）

    一次登录两态齐（2026-10-13 用户验收裁定）：takeover 快照入发送 profile
    后同步写存档文件——引擎存档门见存档即过，不再二次弹窗。
    """
    import json as _json
    import os as _os
    from pathlib import Path as _Path
    from danmaku_listener.config.settings import get_settings

    names = set(HUYA_COOKIE_NAMES)  # F12：单源（engines/send_login.py）
    try:
        # huya 域段匹配（F7：原子串匹配会误命中伪后缀域——notdouyin.com 同类）
        cookies = []
        for c in await context.cookies():
            domain = str(c.get("domain", ""))
            if c.get("value") and (domain == "huya.com" or domain.endswith(".huya.com")):
                cookies.append(c)
        if not ({c["name"] for c in cookies} & names):
            logger.warning("[huya] 存档导出：context 无 huya 登录 cookie（未登录？）")
            return False
        path = _Path(get_settings().cookie_dir) / "huya_login_cookies.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = str(path) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(_json.dumps({"cookies": cookies}, ensure_ascii=False, indent=2))
        _os.replace(tmp, path)
        logger.info(f"[huya] 监听存档已同步（{len(cookies)} 条）→ {path}")
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[huya] 监听存档导出失败: {type(e).__name__}: {str(e)[:80]}")
        return False


async def takeover_login_and_archive(room_id: str) -> bool:
    """发送侧登录闭环（引擎一次登录门委托）：takeover 可见窗一次扫码→
    快照入发送 profile →huya 域 cookie 写监听存档。False=用户关窗/超时/异常。
    """
    from danmaku_listener.config.settings import get_settings
    st = get_settings()
    mode = getattr(st, "send_window_mode", MODE_HEADLESS_NEW) or MODE_HEADLESS_NEW
    idle = int(getattr(st, "send_session_idle_timeout_seconds", 1800) or 1800)
    session = _get_session(idle, mode)
    try:
        page = await session.login_takeover(room_id, *HUYA_TAKEOVER_PARAMS)
        return await export_huya_archive(page.context)
    except asyncio.CancelledError:
        # ship 对抗F2：stop_room→引擎取消路径会穿透此处（CancelledError 非
        # Exception 子类）——可见登录窗+playwright 进程必须就地回收，否则
        # profile 目录被占用，后续发送/重登连锁撞 SingletonLock。
        try:
            await session.close()
        except Exception:  # noqa: BLE001  close 失败由 DX-D7 stale 自愈兜底
            pass
        raise
    except LoginTakeoverError as e:
        logger.warning(f"[huya] takeover 登录未完成: {e}")
        return False
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[huya] takeover 异常: {type(e).__name__}: {str(e)[:80]}")
        return False


class HuyaResidentSender(BaseSender):
    """虎牙常驻会话 sender（headless_new 纯后台；F1 登录快照/恢复）"""

    platform = "huya"

    def __init__(self, settings=None):
        # 2026-10-10 终态（用户验收通过裁定）：纯后台 headless_new；DX-D9 三件套
        # settings.send_window_mode 可覆写（注意全局默认即"headless_new"——INI
        # 未配置时同样后台；前台诊断期绕过 settings 的临时逻辑已移除）
        mode = MODE_HEADLESS_NEW
        idle = 1800
        if settings is not None:
            mode = getattr(settings, "send_window_mode", mode) or mode
            idle = int(getattr(settings, "send_session_idle_timeout_seconds", idle) or idle)
        self._session = _get_session(idle, mode)

    async def send(self, room_id: str, content: str) -> SendResult:
        url = f"https://www.huya.com/{room_id}"
        # 登录转正流程（可见窗+人工登录+30s 验证宽限）可能需要数分钟——
        # action 预算放宽至 360s（默认 90s 会掐断登录窗）
        return await self._session.send(room_id, url, self._page_action(content),
                                        action_timeout_s=360.0)

    def _page_action(self, content: str):
        async def action(page, rid: str) -> SendResult:
            # 登录态检测（cookie 基准 + UDB 遮罩可见性判定——2026-10-10 探针
            # 实证 mask 节点 DOM 常驻：count 判定把已登录页误判为未登录）
            try:
                mask_loc = page.locator("#UDBSdkLgn-mask")
                mask = (await mask_loc.count() > 0
                        and await mask_loc.first.is_visible())
                cookies = {c["name"] for c in await page.context.cookies() if c.get("value")}
                logged = bool(set(HUYA_COOKIE_NAMES) & cookies) and not mask
            except Exception:  # noqa: BLE001  探测失败不阻断（fake page 兼容）
                logged = True
            if not logged:
                logger.info(f"[huya] room {rid} 未登录（UDB 遮罩/cookie 缺失）——弹可见登录窗转正")
                try:
                    page = await self._session.login_takeover(
                        rid, f"https://www.huya.com/{rid}",
                        HUYA_COOKIE_NAMES, grace_s=30.0)
                except LoginTakeoverError as e:
                    return SendResult(SendStatus.FAILED, SendRejectReason.PLATFORM_REJECTED.value,
                                      detail=f"登录未完成: {e}",
                                      fix_hint="重试发送将再次弹出登录窗口——请在窗口内完成登录与安全验证")
                except Exception as e:  # noqa: BLE001  会话层异常隔离
                    logger.warning(f"[huya] login takeover error: {type(e).__name__}: {str(e)[:80]}")
                    return SendResult(SendStatus.FAILED, SendRejectReason.PLATFORM_REJECTED.value,
                                      detail=f"登录窗异常: {type(e).__name__}: {str(e)[:80]}",
                                      fix_hint="重试发送将再次弹出登录窗口")
                else:
                    # 一次登录（2026-10-13）：takeover 成功即同步监听存档——
                    # 引擎存档门不再二次弹窗
                    await export_huya_archive(page.context)
            # 渲染等待：goto 返回后 SPA 输入框延迟挂载（1s 即查会假阴性）。
            # 2026-10-10 发现逻辑升级：逐匹配扫描（.first 抓错元素教训）+
            # 可见且可编辑过滤（包裹层 div 不可编辑——选它打字全空）
            inp = None
            inp_sel = None
            for _ in range(10):
                for sel in INPUT_SELECTORS:
                    try:
                        loc = page.locator(sel)
                        n = await loc.count()
                    except Exception:  # noqa: BLE001
                        continue
                    for i in range(min(n, 5)):
                        cand = loc.nth(i)
                        try:
                            if not await cand.is_visible():
                                continue
                            if not await cand.is_editable():
                                continue
                        except Exception:  # noqa: BLE001
                            continue
                        inp, inp_sel = cand, sel
                        break
                    if inp is not None:
                        break
                if inp is not None:
                    break
                await asyncio.sleep(2)
            if inp is None:
                return SendResult(SendStatus.FAILED, SendRejectReason.PLATFORM_REJECTED.value,
                                  fix_hint="虎牙发送输入框未出现（登录态失效）——重新登录："
                                           "python -m danmaku_listener.engines.send_login huya [room_id]（DX-D1）",
                                  docs_anchor="docs/ops/send-runbook.md")
            await inp.click(timeout=10_000)
            await asyncio.sleep(0.4)
            await inp.press_sequentially(content, delay=40)
            await asyncio.sleep(0.3)
            # 诊断观测（2026-10-10）：提交前回读输入框——contenteditable 用
            # innerText、input/textarea 落 value 双读法；区分"没打进去"与
            # "打进去了但被吞"（用户可见窗口期的人眼判定补充硬证据）
            typed = None
            try:
                typed = await inp.evaluate(
                    "el => ((el.innerText || el.textContent || '') || (el.value ?? '')).trim()")
            except Exception:  # noqa: BLE001  回读失败按 None 记
                typed = None
            typed_ok = (typed == content)
            if not typed_ok and inp is not None:
                # 逐键失败自愈：textarea 直填（fill 触发 input 事件——React 受控
                # 组件通常可感知）；contenteditable 的 fill 会抛错，落 except 跳过
                try:
                    await inp.fill(content)
                    await asyncio.sleep(0.2)
                    typed = await inp.evaluate(
                        "el => ((el.innerText || el.textContent || '') || (el.value ?? '')).trim()")
                    typed_ok = (typed == content)
                    logger.info(f"[huya] room {rid} 逐键失败→fill 重试: typed_ok={typed_ok}")
                except Exception:  # noqa: BLE001
                    pass
            logger.info(f"[huya] room {rid} 提交前输入回读: sel={inp_sel} typed_ok={typed_ok} "
                        f"len={len(typed or '')}/{len(content)}")
            sent_btn = None
            for sel in BUTTON_SELECTORS:
                try:
                    loc = page.locator(sel).first
                    if await loc.count() > 0 and await loc.is_visible():
                        sent_btn = loc
                        break
                except Exception:  # noqa: BLE001
                    continue
            try:
                if sent_btn is not None:
                    await sent_btn.click(timeout=5_000)
                else:
                    await inp.press("Enter")
            except Exception:  # noqa: BLE001
                await inp.press("Enter")
            # 提交后状态观测（前移至 +0.3s——huya 提交成功后会重建聊天区节点，
            # 观测晚了读到的就是重建后的世界，2026-10-09 实测教训）
            input_after = None
            try:
                ta = page.locator(inp_sel).first
                if await ta.count() > 0:
                    input_after = await ta.evaluate(
                        "el => ((el.innerText || el.textContent || '') || (el.value ?? '')).trim()")
            except Exception:  # noqa: BLE001  节点已被页面重建替换——按 None 记
                input_after = None
            # F5：get_by_text 穿透 shadow DOM 回显；兜底=聊天容器 innerText 扫描
            # （虎牙 2026-10-09 实测：提交成功后节点重建致 get_by_text 假阴性——
            #   斗鱼/1688 同款第三例；innerText 扫描按行拼接仍可命中）
            deadline = time.monotonic() + ECHO_WAIT_S
            while time.monotonic() < deadline:
                try:
                    if await page.get_by_text(content).count() > 0:
                        return SendResult(SendStatus.SENT, sent_at=int(time.time()))
                    aside_text = await page.evaluate(
                        "() => { const a = document.querySelector('#js-player-asideMain');"
                        " return a ? a.innerText : ''; }")
                    if content in (aside_text or ""):
                        return SendResult(SendStatus.SENT, sent_at=int(time.time()))
                except Exception:  # noqa: BLE001
                    pass
                await asyncio.sleep(1.5)
            state = ("空(已提交——回显与观测均未命中)" if (typed_ok and input_after == "")
                     else ("有内容(提交未触发)" if input_after else "无法读取(节点已重建)"))
            if not typed_ok:
                state = f"输入未落地(提交前回读={typed!r})"
            return SendResult(SendStatus.UNKNOWN, SendRejectReason.SEND_TIMEOUT.value,
                              detail=f"无回显无显式错误；提交前输入={'正确' if typed_ok else repr(typed)}；"
                                     f"提交后输入框={state}（虎牙冷却/虚拟列表可能）")
        return action
