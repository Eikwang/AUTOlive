"""淘宝 mtop page-eval sender（AutoDanmu T2——T1 探针判定定型形态）

T1 判定（cards/taobao-mtop-{capture,replay,pageeval}-20261007-*.json）：
- 发送端点 = mtop.taobao.iliad.comment.publish v1.0（appKey 34675810，data={topic,content}）
- 纯 HTTP 重放 0/3 FAIL（RGV587——query 的 bx-ua/bx_et 行为签名必须页面 JS 现生成，
  make_sign 重建 sign 正确仍被拒）→ **页面上下文调用 window.lib.mtop.request 2/2 SUCCESS**
- 形态定型：sender 经瞬态页 evaluate 调页面 mtop 库（页面 JS 现生成 bx-ua/签名/token）
  ——ENG-2/15 的 token 单实例/single-flight 语义由页面 mtop 库天然管理，无需自建

E5 义务：持监听引擎引用，借引擎 _profile_lock 开瞬态页（persistent context 单实例
串行，taobao.py:165 实证禁止另开无锁 context）；S4-1 锁超时回执 busy。

ret 五路径映射（CEO-F4/DX-D5，已知码样本来自 T1）：
  1. SUCCESS → SENT
  2. RGV587/FAIL_SYS_USER_VALIDATE/x5sec（风控）→ FAIL PLATFORM_REJECTED（勿误导重扫码）
  3. SESSION_EXPIRED/NEED_LOGIN/未登录类 → FAIL NEEDS_LOGIN 语义
  4. FAIL_SYS_PARAM*/ILLEGAL（参数错误）→ FAIL 参数细分
  5. 其它未知 ret → FAIL 保守透传原始 ret 到 detail

T2 修复（2026-10-08 验收战役 CEO-1/3，根因卡 m0-send-probe-cards.md:39）：
- 全部 await 显式超时：x5sec 风控触发 noCaptcha 时页面 promise 永不 settle（网络层
  实证 _____tmd_____/report?x5secdata + nocaptcha initialize）——evaluate 裸 await 即
  永久悬挂。EVAL_TIMEOUT_S 硬超时兜底；JS mtop timeout 12s 先行落地错误路径保留 ret。
  边界说明（评审 maint#1/redteam#6）：launch_persistent_context/new_page 依赖
  Playwright 默认 30s 超时，close 无超时参数——此三者的残余挂起面由管线级
  wait_for(70s) 兜底（pipeline.py），超预算即 UNKNOWN 回执，锁不泄漏。
- x5sec 信号检测：eval 窗口内网络层捕获风险信号 → FAILED「风控验证待人工」（勿盲目重试）。
- sender 级 catch（E5 隔离，与 deprecated DOM 钩子/EngineHookSender 同语义）。
- 阶段预算收敛：goto 20 + topic 12 + lib 8 + eval 15 = 55s（CEO-1：前端 60s 中止内闭环）。
"""
from __future__ import annotations

import asyncio
import json
import math
import random
import re
import time
from typing import Any, Optional
from urllib.parse import unquote

from loguru import logger
from playwright.async_api import async_playwright  # 模块级：测试注入点（monkeypatch mod.async_playwright）

from danmaku_listener.contract.models import SendRejectReason, SendStatus
from danmaku_listener.senders.base import BaseSender, SendResult

PUBLISH_API = "mtop.taobao.iliad.comment.publish"
PUBLISH_VERSION = "1.0"
PUBLISH_APPKEY = "34675810"
LOCK_TIMEOUT_S = 3.0          # S4-1：监听重登长动作不饿死发送（锁等待，不计入四阶段预算）
PAGE_TIMEOUT_MS = 20_000      # goto 预算（T2 收敛 30→20s——CEO-1：四阶段合计≤55s）
TOPIC_WAIT_S = 12.0           # 页面请求锚定 topic 预算（T2 收敛 20→12s）
MTOP_LIB_WAIT_S = 8.0         # 页面 mtop 库就绪预算（T2 收敛 20→8s）
EVAL_TIMEOUT_S = 15.0         # mtop page-eval 硬超时（T2 新增——x5sec promise 永挂实证，消灭无超时挂点）
# 四阶段预算和 = 20 + 12 + 8 + 15 = 55s ≤ 前端 60s 中止（CEO-1；tripwire 单测锁定）

# 已知 ret 码分类（DX-D5 样本 + 惯例前缀；顺序敏感——先匹配风控再未登录）
RET_RISK_PATTERNS = ("RGV587", "FAIL_SYS_USER_VALIDATE", "x5sec", "P_UNCHECKED")
RET_LOGIN_PATTERNS = ("SESSION_EXPIRED", "NEED_LOGIN", "FAIL_SYS_SESSION", "登录")

#: x5sec/noCaptcha 风控信号（T1 网络层实证：_____tmd_____/report?x5secdata + nocaptcha initialize）
RISK_SIGNAL_URL_PATTERNS = ("_____tmd_____", "x5secdata", "nocaptcha")

#: 自动滑块 knob 候选（noCaptcha 简单滑块——2026-10-10 用户需求：~12h 周期性
#: 滑块验证，简单拖动无拼图；跨 frame 搜索，baxia 堡垒容器可能包 iframe）
SLIDER_KNOB_SELECTORS = ("#nc_1_n1z", "[id*='n1z']", "[class*='btn_slide']")


def plan_slider_drag(track_width: float, knob_width: float, *,
                     rng: Optional[random.Random] = None) -> list[tuple[float, float, float]]:
    """人味滑块拖动轨迹规划（纯函数，单测可锁）

    返回 [(x_abs, dy, delay_ms), ...]——x 为自起点累计的绝对位移，dy 为纵向
    抖动，delay 为该步后停顿。风控行为分的拦截特征=匀速直线/瞬时完成；
    人类剖面：变速（两端慢中段快）+ 纵向微抖（±1.5px）+ 终点微过冲回正
    （sin 半波）+ 总时长 0.55~0.9s + 1~2 次微停顿（犹豫点）。
    """
    rng = rng or random.Random()
    distance = max(120.0, track_width - knob_width + 2.0)
    steps = rng.randint(28, 38)
    total_ms = rng.uniform(800.0, 1300.0)  # 人类 260px 拖动常见 0.8~1.5s（首版 0.55~0.9 偏快被判机器）
    weights = [0.5 + abs(2.0 * (i + 1) / steps - 1.0) for i in range(steps)]
    wsum = sum(weights)
    delays = [total_ms * w / wsum for w in weights]
    for _ in range(rng.randint(1, 2)):
        delays[rng.randrange(steps)] += rng.uniform(30.0, 70.0)
    overshoot = rng.uniform(2.0, 5.0)
    out: list[tuple[float, float, float]] = []
    dy_walk = 0.0  # 纵向平滑随机游走（30Hz 独立抖动非人手特征——首版教训）
    for i in range(steps):
        t = (i + 1) / steps
        x = distance * (1.0 - (1.0 - min(t / 0.9, 1.0)) ** 2)
        if t > 0.9:
            x = distance + overshoot * math.sin((t - 0.9) / 0.1 * math.pi)
        dy_walk = max(-2.0, min(2.0, dy_walk + rng.uniform(-0.6, 0.6)))
        out.append((round(x, 2), round(dy_walk, 2), round(delays[i], 1)))
    return out


def is_risk_signal_url(url: str) -> bool:
    """网络层风险信号判定（纯函数，单测可锁）"""
    return any(p in (url or "") for p in RISK_SIGNAL_URL_PATTERNS)


def is_publish_flow_risk_url(url: str) -> bool:
    """发布流程风控信号判定（单一权威门——观测窗口内命中即计 risk）

    锚定语义（评审 maint#2/security#1 收敛）：仅 h5api.m.taobao.com 的 publish
    相关响应（_____tmd_____/x5secdata 特征）或阿里 noCaptcha 组件（cf.aliyun.com）
    计入——主机白名单防第三方页面资源以子串伪造信号（把 UNKNOWN 伪装成
    「风控待人工」或反向抑制）。
    """
    u = url or ""
    if "h5api.m.taobao.com" in u and PUBLISH_API in u and is_risk_signal_url(u):
        return True
    if "cf.aliyun.com" in u and "nocaptcha" in u:
        return True
    return False

def extract_mtop_ret(body: str) -> Optional[list]:
    """mtop 响应体 → ret 列表（jsonp 包裹/纯 JSON 双形态；垃圾体 None）"""
    if not body:
        return None
    try:
        payload = body
        if "(" in body and body.rstrip().endswith(")"):
            payload = body[body.find("(") + 1: body.rfind(")")]
        data = json.loads(payload)
        ret = data.get("ret") if isinstance(data, dict) else None
        return ret if isinstance(ret, list) else None
    except Exception:  # noqa: BLE001
        return None


EVAL_SEND_JS = """
async (args) => {
  const mtop = (window.lib && window.lib.mtop) || window.mtop;
  if (!mtop || typeof mtop.request !== 'function') {
    return {error: 'mtop lib not found'};
  }
  try {
    const res = await mtop.request({
      api: args.api, v: args.v, appKey: args.appKey,
      data: args.data, type: 'GET', dataType: 'jsonp', timeout: 12000,
    });
    return {ret: res && res.ret};
  } catch (e) {
    return {rejected: true, ret: (e && e.ret) || null, detail: String(e && (e.message || e)).slice(0, 160)};
  }
}
"""

#: 无头滑块未通过的哨兵 detail（send() 据此触发有头窗口自动重试——验证环境分）
SLIDER_HEADLESS_RETRY_DETAIL = "自动滑块未通过（无头环境）——有头窗口自动重试"

EVAL_LIB_PROBE_JS = "!!((window.lib && window.lib.mtop) || window.mtop) && typeof ((window.lib && window.lib.mtop) || window.mtop).request === 'function'"


class TaobaoMtopSender(BaseSender):
    """淘宝 mtop page-eval 发送器（E5：经由监听引擎实例的锁与 profile 执行）"""

    platform = "taobao"

    def __init__(self, engine: Any):
        self._engine = engine  # TaobaoWebProtocolEngine（_profile_lock/_profile_dir/LIVE_URL_TEMPLATE）

    async def send(self, room_id: str, content: str) -> SendResult:
        try:
            await asyncio.wait_for(self._engine._profile_lock.acquire(), timeout=LOCK_TIMEOUT_S)
        except asyncio.TimeoutError:
            return SendResult(SendStatus.FAILED, SendRejectReason.PLATFORM_REJECTED.value,
                              fix_hint="profile 锁被监听操作占用——稍后重试（busy）")
        try:
            result = await self._send_locked(room_id, content)
            if (result.detail or "").startswith(SLIDER_HEADLESS_RETRY_DETAIL):
                # 无头滑块未过（环境分）——有头窗口自动重试（用户可见窗口闪现）
                logger.info("[taobao-mtop] 无头滑块未过——有头窗口自动重试（验证环境分）")
                result = await self._send_locked(room_id, content, headed=True)
            return result
        except Exception as e:  # noqa: BLE001  E5 隔离（与 deprecated DOM 钩子/EngineHookSender 同语义——异常不出 sender 边界）
            logger.warning(f"[taobao-mtop] send 异常: {type(e).__name__}: {str(e)[:120]}")
            return SendResult(SendStatus.FAILED, SendRejectReason.PLATFORM_REJECTED.value,
                              detail=f"{type(e).__name__}: {str(e)[:100]}")
        finally:
            self._engine._profile_lock.release()

    async def _send_locked(self, room_id: str, content: str,
                           headed: bool = False) -> SendResult:
        t0 = time.monotonic()
        async with async_playwright() as pw:
            context = await pw.chromium.launch_persistent_context(
                self._engine._profile_dir(), headless=not headed,
                user_agent=self._engine_UA(),
                viewport={"width": 1280, "height": 800},
                args=["--disable-blink-features=AutomationControlled",
                      "--disable-setuid-sandbox", "--hide-crash-restore-bubble"]
                + (["--mute-audio"] if headed else []))
            logger.info(f"[taobao-mtop] stage=launch elapsed={time.monotonic()-t0:.1f}s room={room_id}")
            # 登录门槛（2026-10-09 验收实证：profile 删除/会话失效→匿名 mtop 发送被拒
            # PLATFORM_REJECTED。未登录→关无头 context、弹可见登录窗（等 unb+30s 滑块
            # 宽限）→登录态入 profile 后同页继续发送。taobao 登录 cookie 为持久型——
            # 发送完成后关窗不丢登录态）
            try:
                _ck = {c["name"] for c in await context.cookies() if c.get("value")}
            except Exception:  # noqa: BLE001
                _ck = set()
            if "unb" not in _ck:
                logger.info(f"[taobao-mtop] stage=login_gate 未登录——弹可见登录窗"
                            f"（等用户完成登录+安全验证，最长 240s）")
                await context.close()
                context = await pw.chromium.launch_persistent_context(
                    self._engine._profile_dir(), headless=False,
                    user_agent=self._engine_UA(),
                    viewport={"width": 1280, "height": 800},
                    args=["--disable-blink-features=AutomationControlled",
                          "--disable-setuid-sandbox", "--hide-crash-restore-bubble",
                          "--mute-audio"])
                page = context.pages[0] if context.pages else await context.new_page()
                try:
                    await page.goto(self._room_url(room_id), timeout=45000,
                                    wait_until="domcontentloaded")
                except Exception as e:  # noqa: BLE001
                    logger.debug(f"[taobao-mtop] login window goto warning: {e}")
                deadline = time.monotonic() + 240
                logged = False
                while time.monotonic() < deadline:
                    try:
                        ck = {c["name"] for c in await context.cookies() if c.get("value")}
                        if "unb" in ck:
                            logged = True
                            break
                    except Exception:  # noqa: BLE001
                        break
                    if page.is_closed():
                        return SendResult(SendStatus.FAILED, SendRejectReason.PLATFORM_REJECTED.value,
                                          detail="登录窗口被关闭",
                                          fix_hint="重试发送将再次弹出登录窗口")
                    await asyncio.sleep(2)
                if not logged:
                    return SendResult(SendStatus.FAILED, SendRejectReason.PLATFORM_REJECTED.value,
                                      detail="登录未完成（240s 超时）",
                                      fix_hint="重试发送将再次弹出登录窗口")
                # 保窗宽限 30s：登录后滑块/安全验证（验收用户实测必需）
                g = time.monotonic() + 30
                while time.monotonic() < g:
                    await asyncio.sleep(2)
                logger.info(f"[taobao-mtop] stage=login_gate 完成 elapsed={time.monotonic()-t0:.1f}s")
                page = context.pages[0] if context.pages else await context.new_page()
            try:
                page = context.pages[0] if context.pages else await context.new_page()
                # x5sec 风控信号观测（T1 实证 + 评审 redteam#2：注册提前到 page 创建后——
                # 页面加载期（goto/topic/lib）触发的风控挑战同样计入，不再误诊为
                # 「页面改版」；风险命中时各失败分支统一回风控语义）
                risk = {"hit": False}
                publish_responses: list = []  # publish 响应对象（eval 超时形态读原请求结果）

                def _on_response(resp) -> None:
                    u = getattr(resp, "url", "") or ""
                    if "h5api.m.taobao.com" in u and PUBLISH_API in u:
                        publish_responses.append(resp)
                    if is_publish_flow_risk_url(u):
                        risk["hit"] = True

                page.on("response", _on_response)
                try:
                    try:
                        await page.goto(self._room_url(room_id), timeout=PAGE_TIMEOUT_MS,
                                        wait_until="domcontentloaded")
                        logger.info(f"[taobao-mtop] stage=goto elapsed={time.monotonic()-t0:.1f}s room={room_id}")
                    except Exception as e:  # noqa: BLE001
                        logger.warning(f"[taobao-mtop] stage=goto FAILED elapsed={time.monotonic()-t0:.1f}s "
                                       f"room={room_id} err={type(e).__name__}")
                        if risk["hit"]:
                            return SendResult(SendStatus.FAILED, SendRejectReason.PLATFORM_REJECTED.value,
                                              detail="x5sec 风控信号（网络层捕获，页面加载期）——noCaptcha 验证待人工",
                                              fix_hint="风控验证待人工——勿盲目重试；"
                                                       "降频稍后重试或经有头窗口完成验证后恢复")
                        return SendResult(SendStatus.UNKNOWN, SendRejectReason.SEND_TIMEOUT.value,
                                          detail=f"goto: {type(e).__name__}")
                    topic = await self._wait_topic(page)
                    logger.info(f"[taobao-mtop] stage=topic elapsed={time.monotonic()-t0:.1f}s "
                                f"room={room_id} anchored={bool(topic)}")
                    if not topic:
                        if risk["hit"]:
                            return SendResult(SendStatus.FAILED, SendRejectReason.PLATFORM_REJECTED.value,
                                              detail="x5sec 风控信号（网络层捕获）——topic 未锚定疑因风控拦截",
                                              fix_hint="风控验证待人工——勿盲目重试；降频稍后重试或经有头窗口完成验证后恢复")
                        return SendResult(SendStatus.FAILED, SendRejectReason.PLATFORM_REJECTED.value,
                                          fix_hint="未能从页面锚定 topic（未开播/未登录/加载慢）——"
                                                   "确认房间监听状态后重试；持续失败跑 tools/send_probes/taobao_mtop_capture.py capture",
                                          docs_anchor="docs/testing/m0-send-probe-cards.md")
                    if not await self._wait_mtop_lib(page):
                        logger.warning(f"[taobao-mtop] stage=mtop_lib MISSING elapsed={time.monotonic()-t0:.1f}s room={room_id}")
                        if risk["hit"]:
                            return SendResult(SendStatus.FAILED, SendRejectReason.PLATFORM_REJECTED.value,
                                              detail="x5sec 风控信号（网络层捕获）——mtop 库不可达疑因风控拦截",
                                              fix_hint="风控验证待人工——勿盲目重试；降频稍后重试或经有头窗口完成验证后恢复")
                        return SendResult(SendStatus.FAILED, SendRejectReason.ROUTE_UNVERIFIED.value,
                                          fix_hint="页面 mtop 库不可达（window.lib.mtop 缺失）——页面结构变更，重跑 T1 探针",
                                          docs_anchor="docs/testing/m0-send-probe-cards.md")
                    result: Optional[dict[str, Any]] = None
                    eval_timeout = False
                    try:
                        result = await asyncio.wait_for(
                            page.evaluate(
                                EVAL_SEND_JS,
                                {"api": PUBLISH_API, "v": PUBLISH_VERSION, "appKey": PUBLISH_APPKEY,
                                 "data": {"topic": topic, "content": content}}),
                            timeout=EVAL_TIMEOUT_S)
                    except asyncio.TimeoutError:
                        eval_timeout = True  # 挂点兜底（T2）——x5sec noCaptcha 等待验证形态
                    ret_str = ("; ".join(str(r) for r in (result.get("ret") or []))
                               if isinstance(result, dict) else "")
                    risk_ret = any(p in ret_str for p in RET_RISK_PATTERNS)
                    ret_success = result is not None and any(
                        "SUCCESS" in str(r) for r in (result.get("ret") or []))
                    knob_sel: Optional[str] = None
                    if (risk["hit"] or risk_ret) and not ret_success:
                        # 自动滑块（2026-10-10 用户需求：~12h 周期性简单滑块——人味
                        # 轨迹拖动自动通过；无滑块/未通过=回落原人工路径。良性超时
                        # 无风控信号不触发——T2 实证 captcha 形态必带网络层信号）
                        await asyncio.sleep(1.5)  # 验证浮层挂载窗口
                        knob_sel = await self._try_pass_slider(page)
                        if knob_sel is not None:
                            if eval_timeout:
                                # 双发防线（2026-10-10 用户实测双弹幕根因）：验证通过后
                                # 原挂起请求自行完成——读它的响应，不盲重试
                                logger.info(f"[taobao-mtop] stage=slider 验证后等待原请求完成 room={room_id}")
                                outcome = await self._await_publish_response(publish_responses, 15.0)
                                if outcome is not None:
                                    result = outcome
                                else:
                                    logger.info("[taobao-mtop] 原请求 15s 未决——重试（可能双发一次）")
                                    try:
                                        result = await asyncio.wait_for(
                                            page.evaluate(
                                                EVAL_SEND_JS,
                                                {"api": PUBLISH_API, "v": PUBLISH_VERSION, "appKey": PUBLISH_APPKEY,
                                                 "data": {"topic": topic, "content": content}}),
                                            timeout=EVAL_TIMEOUT_S)
                                        eval_timeout = False
                                    except asyncio.TimeoutError:
                                        result = None
                                        eval_timeout = True
                            else:
                                # ret 风控形态——原请求已被服务端拒绝未发布，重试安全
                                logger.info(f"[taobao-mtop] stage=slider 滑块后重试 mtop room={room_id}")
                                try:
                                    result = await asyncio.wait_for(
                                        page.evaluate(
                                            EVAL_SEND_JS,
                                            {"api": PUBLISH_API, "v": PUBLISH_VERSION, "appKey": PUBLISH_APPKEY,
                                             "data": {"topic": topic, "content": content}}),
                                        timeout=EVAL_TIMEOUT_S)
                                    eval_timeout = False
                                except asyncio.TimeoutError:
                                    result = None
                                    eval_timeout = True
                    if eval_timeout:
                        if risk["hit"] or risk_ret:
                            logger.warning(f"[taobao-mtop] stage=eval x5sec_signal elapsed={time.monotonic()-t0:.1f}s room={room_id}")
                            if not headed:
                                return SendResult(SendStatus.FAILED, SendRejectReason.PLATFORM_REJECTED.value,
                                                  detail=SLIDER_HEADLESS_RETRY_DETAIL,
                                                  fix_hint="有头窗口自动重试进行中——验证环境分更优")
                            return SendResult(SendStatus.FAILED, SendRejectReason.PLATFORM_REJECTED.value,
                                              detail="x5sec 风控信号——自动滑块未通过（无头+有头均重试）",
                                              fix_hint="风控验证待人工——勿盲目重试；降频稍后重试或手动完成验证后恢复")
                        logger.warning(f"[taobao-mtop] stage=eval NO_RESPONSE elapsed={time.monotonic()-t0:.1f}s room={room_id}")
                        return SendResult(SendStatus.UNKNOWN, SendRejectReason.SEND_TIMEOUT.value,
                                          detail=f"mtop 调用 {EVAL_TIMEOUT_S:.0f}s 无响应（页面 promise 未决）",
                                          fix_hint="页面 mtop 调用未决——重跑 T1 探针核对形态")
                    if risk_ret and isinstance(result, dict) and knob_sel is not None and not headed:
                        # 无头下滑块拖了但 ret 仍风控——环境分嫌疑，有头重试
                        logger.warning(f"[taobao-mtop] stage=eval risk_after_slider（无头）room={room_id} ret={ret_str[:80]}")
                        return SendResult(SendStatus.FAILED, SendRejectReason.PLATFORM_REJECTED.value,
                                          detail=SLIDER_HEADLESS_RETRY_DETAIL,
                                          fix_hint="有头窗口自动重试进行中——验证环境分更优")
                    if risk_ret and isinstance(result, dict):
                        # 重试后仍风控 ret——不再二次拖动（避免循环），人工路径
                        logger.warning(f"[taobao-mtop] stage=eval risk_after_slider room={room_id} ret={ret_str[:80]}")
                    logger.info(f"[taobao-mtop] stage=eval elapsed={time.monotonic()-t0:.1f}s room={room_id} ret={str(result.get('ret'))[:80]}")
                    return self._map_ret(result)
                finally:
                    page.remove_listener("response", _on_response)
            finally:
                try:
                    await context.close()
                except Exception:  # noqa: BLE001
                    pass

    async def _await_publish_response(self, responses: list,
                                      timeout_s: float) -> Optional[dict[str, Any]]:
        """等待 publish 响应并解析 ret（eval 超时形态——原挂起请求验证通过后
        自行完成，读它即可，重试会双发；2026-10-10 用户实测双弹幕根因）"""
        deadline = time.monotonic() + timeout_s
        seen: set = set()
        while time.monotonic() < deadline:
            for resp in list(responses):
                if id(resp) in seen:
                    continue
                seen.add(id(resp))
                try:
                    body = await asyncio.wait_for(resp.text(), timeout=3.0)
                except Exception:  # noqa: BLE001  响应体未就绪/连接断
                    continue
                ret = extract_mtop_ret(body or "")
                if ret is not None:
                    logger.info(f"[taobao-mtop] stage=slider 原请求响应 ret={str(ret)[:80]}")
                    return {"ret": ret}
            await asyncio.sleep(0.5)
        return None

    async def _try_pass_slider(self, page) -> Optional[str]:
        """自动滑块（简单滑块——人味轨迹拖动，2026-10-10 用户需求）

        跨 frame 搜索 noCaptcha knob → CDP 真实鼠标事件拖动（isTrusted=true）。
        返回命中的 knob 选择器（None=页面无滑块/探测失败——调用方维持原人工
        路径）。成功判定交给调用方的 mtop 重试（ret 为最终裁决）；"验证通过"
        文本仅日志。
        """
        try:
            frames = list(page.frames)
        except Exception:  # noqa: BLE001  结构异常=无滑块，人工路径
            return None
        for frame in frames:
            for sel in SLIDER_KNOB_SELECTORS:
                try:
                    loc = frame.locator(sel).first
                    if await loc.count() == 0 or not await loc.is_visible():
                        continue
                    kb = await loc.bounding_box()
                    if not kb or kb.get("width", 0) <= 0:
                        continue
                    track_w = await loc.evaluate(
                        "el => { const t = document.getElementById('nc_1__scale_text')"
                        " || document.querySelector('[class*=\"scale_text\"], [class*=\"nc-lang-cnt\"]')"
                        " || el.parentElement;"
                        " return t ? t.getBoundingClientRect().width : 0; }")
                    if not track_w or track_w <= kb["width"]:
                        track_w = 320.0  # 兜底轨道宽（noCaptcha 常见 300±）
                    plan = plan_slider_drag(float(track_w), float(kb["width"]))
                    sx, sy = kb["x"] + kb["width"] / 2, kb["y"] + kb["height"] / 2
                    await page.mouse.move(sx, sy)
                    await page.mouse.down()
                    for dx, dy, dms in plan:
                        await page.mouse.move(sx + dx, sy + dy)
                        await asyncio.sleep(dms / 1000.0)
                    await page.mouse.up()
                    logger.info(f"[taobao-mtop] stage=slider 拖动完成 sel={sel} "
                                f"frame={frame.url[:60]} track={track_w:.0f}px 步数={len(plan)}")
                    # 诊断：knob 是否真的位移（区分"事件未注册"与"动了但被拒"）
                    try:
                        kb2 = await loc.bounding_box()
                        moved = (kb2 is not None and kb is not None
                                 and abs(kb2["x"] - kb["x"]) > 5.0)
                        logger.info(f"[taobao-mtop] stage=slider knob位移={moved} "
                                    f"x:{kb['x']:.0f}->{(kb2 or {}).get('x', -1):.0f}")
                    except Exception:  # noqa: BLE001  组件重置/隐藏——按已通过倾向记
                        logger.info("[taobao-mtop] stage=slider knob 位置不可读（组件可能已重置=通过倾向）")
                    # 文本信号（日志级——成功裁决走 mtop 重试 ret）
                    deadline = time.monotonic() + 5
                    ok_text = False
                    fail_text = False
                    while time.monotonic() < deadline and not (ok_text or fail_text):
                        for f in page.frames:
                            try:
                                txt = await f.evaluate(
                                    "() => document.body ? document.body.innerText : ''")
                                if "验证通过" in (txt or ""):
                                    ok_text = True
                                    break
                                if "验证失败" in (txt or "") or "再次验证" in (txt or ""):
                                    fail_text = True
                                    break
                            except Exception:  # noqa: BLE001  about 帧等
                                continue
                        if not (ok_text or fail_text):
                            await asyncio.sleep(0.5)
                    logger.info(f"[taobao-mtop] stage=slider 文本信号 ok={ok_text} fail={fail_text}")
                    return sel
                except Exception as e:  # noqa: BLE001  单候选失败继续搜
                    logger.debug(f"[taobao-mtop] slider 候选 {sel} 探测失败: {type(e).__name__}")
        return None

    def _room_url(self, room_id: str) -> str:
        from danmaku_listener.engines.protocol.mtop import extract_live_id
        live_id = extract_live_id(room_id)
        return self._engine.LIVE_URL_TEMPLATE.format(live_id=live_id)

    @staticmethod
    def _engine_UA() -> str:
        """与 taobao 引擎同源 UA（taobao.py UA 常量——凭证提取/发送一致，R17 纪律）"""
        from danmaku_listener.engines.protocol.taobao import UA
        return UA

    async def _wait_mtop_lib(self, page) -> bool:
        deadline = time.monotonic() + MTOP_LIB_WAIT_S
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                # 单次探针也必须有界（评审 P1：冻结页下裸 evaluate 永挂→锁泄漏——
                # 「全部 await 显式超时」不变量含探针调用）
                if await asyncio.wait_for(page.evaluate(EVAL_LIB_PROBE_JS),
                                          timeout=min(2.0, remaining)):
                    return True
            except asyncio.TimeoutError:
                pass  # 单次探针未决（页面冻结形态）——计入预算继续轮询
            except Exception:  # noqa: BLE001
                pass
            await asyncio.sleep(1)
        return False

    async def _wait_topic(self, page) -> Optional[str]:
        """页面请求锚定 topic（与 taobao.py:409-425 TOPIC_ANCHORS 同构；不引引擎内部闭包）"""
        state = {"topic": None}

        def on_request(request) -> None:
            if state["topic"]:
                return
            u = request.url
            if "iliad" in u or "powermsg" in u:
                m = re.search(r"[?&]data=([^&]+)", u)
                raw = m.group(1) if m else (request.post_data or "")
                if raw:
                    try:
                        data = json.loads(unquote(raw))
                        if data.get("topic"):
                            state["topic"] = data["topic"]
                    except json.JSONDecodeError:
                        pass

        page.on("request", on_request)
        deadline = time.monotonic() + TOPIC_WAIT_S
        while state["topic"] is None and time.monotonic() < deadline:
            await asyncio.sleep(1)
        page.remove_listener("request", on_request)
        return state["topic"]

    @staticmethod
    def _map_ret(result: dict[str, Any]) -> SendResult:
        """ret 五路径映射（CEO-F4/DX-D5——禁止一律落 NEEDS_LOGIN 误导排障）"""
        if result.get("error") == "mtop lib not found":
            return SendResult(SendStatus.FAILED, SendRejectReason.ROUTE_UNVERIFIED.value,
                              fix_hint="页面 mtop 库不可达——页面结构变更，重跑 T1 探针",
                              docs_anchor="docs/testing/m0-send-probe-cards.md")
        ret_list = result.get("ret") or []
        if result.get("rejected") and not ret_list:
            return SendResult(SendStatus.FAILED, SendRejectReason.PLATFORM_REJECTED.value,
                              detail=f"mtop 调用异常: {result.get('detail', '')[:120]}",
                              fix_hint="页面 mtop 调用异常——重跑 T1 探针核对形态")
        ret_str = "; ".join(str(r) for r in ret_list)
        if any("SUCCESS" in r for r in ret_list):
            return SendResult(SendStatus.SENT, sent_at=int(time.time()))
        if any(p in ret_str for p in RET_RISK_PATTERNS):
            return SendResult(SendStatus.FAILED, SendRejectReason.PLATFORM_REJECTED.value,
                              detail=f"ret={ret_str[:160]}",
                              fix_hint="风控拦截（RGV587/x5sec 类）——降低发送频率稍后重试；勿重扫码（登录态未失效）")
        if any(p in ret_str for p in RET_LOGIN_PATTERNS):
            return SendResult(SendStatus.FAILED, SendRejectReason.PLATFORM_REJECTED.value,
                              detail=f"ret={ret_str[:160]}",
                              fix_hint="登录态失效——重跑淘宝登录窗口后重试",
                              docs_anchor="docs/ops/send-runbook.md")
        if any(p in ret_str for p in ("FAIL_SYS_PARAM", "ILLEGAL", "参数")):
            return SendResult(SendStatus.FAILED, SendRejectReason.PLATFORM_REJECTED.value,
                              detail=f"ret={ret_str[:160]}",
                              fix_hint="参数错误——data 结构可能变更，重跑 T1 探针核对")
        # 路径 5：未知 ret 保守 FAIL + 原始码透传（DX-D5）
        return SendResult(SendStatus.FAILED, SendRejectReason.PLATFORM_REJECTED.value,
                          detail=f"未知 ret={ret_str[:160]}",
                          fix_hint="未知返回码——透传详情排障；持续出现重跑 T1 探针")
