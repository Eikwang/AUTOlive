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
from danmaku_listener.senders.base import BaseSender, SendResult
from danmaku_listener.senders.resident_session import (
    MODE_HEADLESS_NEW,
    LoginTakeoverError,
    ResidentSendSession,
)

PROFILE_DIR = "cookie/huya_login_profile"
#: headless 反检测指纹包（2026-10-09 对抗实验 1：虎牙风控识别 headless=new
#: 指纹并静默吞弹幕——headed 对照可过。注入 7 信号伪装后再验）
STEALTH_JS = """
() => {
    Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
    Object.defineProperty(window, 'outerWidth', {get: () => 1280});
    Object.defineProperty(window, 'outerHeight', {get: () => 800});
    Object.defineProperty(screen, 'availWidth', {get: () => 1280});
    Object.defineProperty(screen, 'availHeight', {get: () => 760});
    Object.defineProperty(screen, 'colorDepth', {get: () => 24});
    Object.defineProperty(screen, 'pixelDepth', {get: () => 24});
    if (!window.chrome) { window.chrome = {runtime: {}, loadTimes: () => {}, csi: () => {}}; }
    Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
    Object.defineProperty(navigator, 'languages', {get: () => ['zh-CN', 'zh', 'en']});
    const origQuery = window.navigator.permissions.query;
    window.navigator.permissions.query = (p) => p.name === 'notifications'
        ? Promise.resolve({state: Notification.permission}) : origQuery(p);
}
"""

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
                logged = bool({"yyuid", "hicl_imid", "huya_uid"} & cookies) and not mask
            except Exception:  # noqa: BLE001  探测失败不阻断（fake page 兼容）
                logged = True
            if not logged:
                logger.info(f"[huya] room {rid} 未登录（UDB 遮罩/cookie 缺失）——弹可见登录窗转正")
                try:
                    page = await self._session.login_takeover(
                        rid, f"https://www.huya.com/{rid}",
                        ("yyuid", "hicl_imid", "huya_uid"), grace_s=30.0)
                except LoginTakeoverError as e:
                    return SendResult(SendStatus.FAILED, SendRejectReason.PLATFORM_REJECTED.value,
                                      detail=f"登录未完成: {e}",
                                      fix_hint="重试发送将再次弹出登录窗口——请在窗口内完成登录与安全验证")
                except Exception as e:  # noqa: BLE001  会话层异常隔离
                    logger.warning(f"[huya] login takeover error: {type(e).__name__}: {str(e)[:80]}")
                    return SendResult(SendStatus.FAILED, SendRejectReason.PLATFORM_REJECTED.value,
                                      detail=f"登录窗异常: {type(e).__name__}: {str(e)[:80]}",
                                      fix_hint="重试发送将再次弹出登录窗口")
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
