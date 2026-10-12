"""京东直播弹幕引擎（受控页面 WS 帧拦截——咚咚 IM 群聊体系）

技术路线（2026-09-30 调研定型，四平台第三站）：
京东直播间页内 WebSocket 下发**明文 JSON**（咚咚 IM 群聊体系，直播间=群）。
wss 直连需 liveauth token（App 端 sign 走 JNI 需 frida）——受控页面路线
让页面自己完成鉴权与订阅，引擎只拦截 framereceived 解析（免签名）。

帧结构（2020 App 端调研数据点，网页端待实测校准）：
- 订阅帧 type=join_live_broadcast（aid=dongdong, appid=jd.mall, groupid=房间号）
- 下行消息 type=chat_group_message，弹幕含 nickName/content 字段
引擎对页内全部 WS 挂 framereceived，宽容解析 + raw_hook 诊断钩子
（京东页面可能有多个 WS——帧形态各异，解析失败自然跳过）。

房间参数：京东直播间链接（页面直达，保留全部风控参数）/ 纯数字。
下播检测：业务帧静默 120s（京东直播弹幕稀疏阈值放宽）。
"""

import asyncio
import json
import re
import time
from typing import Any, Dict, Iterable, List, Optional

from loguru import logger

from danmaku_listener.contract.models import GapReason
from danmaku_listener.engines import login_gate as LOGIN_GATE
from danmaku_listener.engines.protocol.controlled_base import (
    SEND_BG_EXTRA_ARGS,
    ControlledPageEngine,
)

PROTOCOL_VERSION = "jd-1"

SILENCE_TIMEOUT = 120.0  # 业务帧静默阈值（京东直播间弹幕稀疏，阈值放宽）

# 2026-09-30 用户实测发现京东直播独立站（游客可看，页面自建 live-ws4 连接）
LIVE_URL_TEMPLATE = "https://zhibo.jd.com/liveroom?liveId={room_id}"


class JDParseError(ValueError):
    """房间参数无法解析"""


def extract_room_id(room_spec: str) -> str:
    """房间参数归一：zhibo.jd.com/liveroom?liveId= / 各类京东链接 / 纯数字

    2026-09-30 用户实测发现京东直播独立站：zhibo.jd.com/liveroom?liveId=
    ——游客可看、页面自建 wss://live-ws4.jd.com 连接（免 liveauth 签名）。
    链接形态整体直达（保留风控参数），room 标识取 liveId/popId/id。
    """
    spec = room_spec.strip()
    if "jd.com" in spec or "jd.hk" in spec or "3.cn" in spec:
        m = (re.search(r"liveId=(\d+)", spec) or re.search(r"popId=(\d+)", spec)
             or re.search(r"liveid=(\d+)", spec) or re.search(r"[\?&]id=(\d+)", spec))
        return m.group(1) if m else spec[:64]  # 无数字特征：链接截断作标识
    m = re.match(r"^(\d{5,25})$", spec)
    if m:
        return m.group(1)
    raise JDParseError(
        f"无法解析京东直播间参数: {spec[:60]!r}——请使用直播间分享链接或房间数字 ID"
        "（推荐 zhibo.jd.com/liveroom?liveId=xxx）")


JD_MSG_TYPES = ("chat_group_message", "join_live_broadcast", "group_message",
                "live_message")


def _is_jd_business(obj: Dict[str, Any]) -> bool:
    """业务对象特征：type 枚举命中，或弹幕特征组合（nickName+content）"""
    if not isinstance(obj, dict):
        return False
    if obj.get("type") in JD_MSG_TYPES:
        return True
    return bool(obj.get("nickName") and obj.get("content"))


def parse_jd_frame(raw) -> List[Dict[str, Any]]:
    """WS 帧宽容解析 → 消息对象列表（纯函数，供单测）

    咚咚 IM 明文 JSON：弹幕特征在 body 内（nickName/content/type），
    顶层为信封（aid/from/type）。收集顶层 + body + msgs[]，业务特征过滤；
    其他页面 WS 的 JSON 帧自然过滤。实测样本后收敛精确结构。
    """
    if isinstance(raw, bytes):
        try:
            raw = raw.decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            return []
    if not isinstance(raw, str):
        return []
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(data, dict):
        return []

    candidates: List[Dict[str, Any]] = [data]
    body = data.get("body")
    if isinstance(body, dict):
        candidates.append(body)
        # body 内嵌套（咚咚 ext/msg 形态，一层即可）
        candidates.extend(v for v in body.values() if isinstance(v, dict))
    msgs = data.get("msgs")
    if isinstance(msgs, list):
        candidates.extend(m for m in msgs if isinstance(m, dict))
    # 去重保序
    seen_ids = set()
    out = []
    for c in candidates:
        cid = id(c)
        if cid not in seen_ids and _is_jd_business(c):
            seen_ids.add(cid)
            out.append(c)
    return out


def map_jd_message(frame: Dict[str, Any], seq: int, ts: int) -> Optional[Dict[str, Any]]:
    """咚咚 IM 顶层帧 → 契约消息映射（纯函数）；未识别返回 None

    2026-09-30 zhibo.jd.com 实测（58 帧）：顶层 type=chat_group_message/
    get_statistics_result，业务形态在 body.type：
    - join_live_broadcast_summary → ENTER_ROOM（聚合形态"xx等16人来了"）
    - thumbs_up → LIKE（body.thumbs_up_num）
    - get_statistics_result → ROOM_STATS（current_viewer/thumbs_up_num）
    - viewer_buy_product_summary/user_places_order 等运营形态不映射
    - 弹幕：body.type=viewer_send_message（nickName+content，实测命中；游客会话即可收他人弹幕，无需登录）
    """
    top_type = frame.get("type")
    body = frame.get("body") or {}
    body_type = body.get("type")
    user_name = str(body.get("nickName") or body.get("nickname") or "")
    content = str(body.get("content") or "").strip()

    if top_type == "get_statistics_result":
        viewer = body.get("current_viewer")
        if isinstance(viewer, int):
            return {"category": "business", "type": "ROOM_STATS", "seq": seq,
                    "timestamp": ts,
                    "payload": {"type": "ROOM_STATS", "viewer_count": viewer}}
        return None

    if top_type != "chat_group_message":
        return None
    if body_type == "join_live_broadcast_summary" and user_name:
        return {"category": "business", "type": "ENTER_ROOM", "seq": seq,
                "timestamp": ts,
                "payload": {"type": "ENTER_ROOM", "user_name": user_name}}
    if body_type == "viewer_send_message" and content and user_name:
        # 2026-09-30 实测：弹幕=body.type=viewer_send_message（带 body.type，
        # 宽容路径覆盖不到——游客会话即可收其他观众弹幕，无需登录）
        return {"category": "business", "type": "DANMU", "seq": seq,
                "timestamp": ts,
                "payload": {"type": "DANMU", "user_name": user_name,
                            "content": content,
                            "user_id": str(frame.get("from", {}).get("pinmd5", ""))}}
    if body_type == "thumbs_up":
        count = body.get("thumbs_up_num", 1)
        if not isinstance(count, int) or count < 1:
            count = 1
        return {"category": "business", "type": "LIKE", "seq": seq,
                "timestamp": ts,
                "payload": {"type": "LIKE", "count": count}}
    # 弹幕：body.nickName+content（text 类 body.type 待样本，宽容兼容）
    if not body_type and content and user_name:
        return {"category": "business", "type": "DANMU", "seq": seq,
                "timestamp": ts,
                "payload": {"type": "DANMU", "user_name": user_name,
                            "content": content}}
    return None


def jd_ws_echo_frame(frames: Iterable[Any], content: str) -> Optional[Dict[str, Any]]:
    """WS 帧集合中提取本人弹幕的服务端回环（F3——回显判定唯一真源）

    咚咚 IM 帧：body.type=viewer_send_message + content 匹配即本人消息
    （服务端广播回环；DOM 乐观渲染不可信——2026-10-10 xhs/huya 三例实证）。
    frames 为 framereceived payload 原始集合（str/bytes/None 混合）。
    命中返回信封对象（供 F4 自发声回环注入消息流），未命中 None。纯函数供单测。
    """
    for raw in frames:
        if raw is None:
            continue
        for obj in parse_jd_frame(raw):
            body = obj.get("body") or {}
            if (body.get("type") == "viewer_send_message"
                    and str(body.get("content") or "").strip() == content):
                return obj
    return None


class JDProtocolEngine(ControlledPageEngine):
    """京东直播独立站受控页面引擎（发送=方案 B 移植 2026-10-10——游客发送区
    无输入框（"请先登录再发弹幕互动"DOM 取证 cards/jd-dom-*），cookie 判登录+
    未登录可见窗等待）"""

    SEND_INPUT_SELECTORS = ['[class*="LiveMessage_sendWrapper"] > input',
                            # ↑ 2026-10-10 验收用户 devtools 实测（登录态输入面）；
                            # CSS-module 哈希类名用稳定前缀匹配（__Reken 后缀随构建变）
                            "textarea", "div[contenteditable=true]",
                            "input[placeholder*=说]", "input[placeholder*=弹幕]"]
    SEND_BUTTON_SELECTORS = ['[class*="LiveMessage_sendWrapper"] > div',
                             # ↑ 用户 devtools 实测：发送按钮是 wrapper 直接 div 子元素
                             # （非 button——button:has-text 点不到它）
                             'button:has-text("发送")', 'text=发送']
    LOGIN_WAIT_TIMEOUT_S = 240.0   # 页内登录等待（用户扫码/验证期间发送挂起）

    async def send_danmu(self, room_id: str, content: str):
        """京东发送（方案 B 移植 2026-10-10）：瞬态后台会话 + cookie 判登录
        （游客发送区无输入框——DOM 取证"请先登录再发弹幕互动"）+ 未登录可见窗
        等待 + WS 帧服务端回环判定 + F4 自发声回环。"""
        from danmaku_listener.contract.models import SendRejectReason, SendStatus
        from danmaku_listener.senders.base import SendResult

        try:
            await asyncio.wait_for(self._profile_lock.acquire(),
                                   timeout=3.0)  # S4-1/R14
        except asyncio.TimeoutError:
            return SendResult(SendStatus.FAILED, SendRejectReason.PLATFORM_REJECTED.value,
                              fix_hint="profile 锁被监听操作占用——稍后重试（busy；"
                                       "首次使用请先完成监听侧登录窗口）")
        self._send_active = True
        try:
            from playwright.async_api import async_playwright

            # 房间 URL：链接形态原样携带；纯数字拼独立站模板
            url = (room_id.strip() if room_id.strip().startswith("http")
                   else self._send_room_url(room_id))
            async with async_playwright() as pw:
                await self._close_listen_ctx()  # F2：监听让位（会话侧 _send_active 门）
                # 无感模式：headless=new 后台发送；登录续期唯一弹窗场景（游客
                # 发送区无输入框），登录后快照落盘即回到无感
                context = await self._launch(pw, headless=False,
                                             extra_args=SEND_BG_EXTRA_ARGS)
                try:
                    logged = await self._ctx_has_login(context)
                    if not logged:
                        logger.info("[jd] 后台会话无登录态——改弹可见窗口等待登录"
                                    "（游客发送区无输入框）")
                        try:
                            await context.close()
                        except Exception:  # noqa: BLE001
                            pass
                        context = await self._launch(pw, headless=False)  # headed 可见（登录窗）
                    page = (context.pages[0] if context.pages
                            else await context.new_page())

                    def _wire_ws(p) -> List[Any]:
                        """F3：WS 收集必须先于 goto（已有连接不触发 websocket 事件）"""
                        frames: List[Any] = []
                        p.on("websocket", lambda ws: ws.on(
                            "framereceived",
                            lambda q: frames.append(
                                q.get("payload") if isinstance(q, dict) else q)))
                        return frames

                    ws_frames = _wire_ws(page)
                    try:
                        await page.goto(url, timeout=45000,
                                        wait_until="domcontentloaded")
                    except Exception as e:  # noqa: BLE001
                        return SendResult(SendStatus.UNKNOWN,
                                          SendRejectReason.SEND_TIMEOUT.value,
                                          detail=f"goto: {type(e).__name__}")
                    await asyncio.sleep(3)
                    if not logged:
                        logger.info(f"[jd] room {room_id} 等待用户在可见窗口完成登录"
                                    f"（最长 {self.LOGIN_WAIT_TIMEOUT_S:.0f}s，"
                                    "完成后自动继续发送）")
                        logged = await self._wait_login_interactive(
                            context, self.LOGIN_WAIT_TIMEOUT_S)
                        if not logged:
                            # 关窗竞态兜底：登录 cookie 随用户手动关窗（干净关闭）
                            # 已落盘 profile——重开一次会话复检，不要求重扫
                            try:
                                await context.close()
                            except Exception:  # noqa: BLE001
                                pass
                            try:
                                context = await self._launch(pw, headless=False)
                                logged = await self._ctx_has_login(context)
                                if logged:
                                    await self._snapshot_login_state(context)
                                    await asyncio.sleep(10)
                                    logger.info("[jd] 登录窗关闭后 profile 复检登录成功"
                                                "——继续发送")
                            except Exception as e:  # noqa: BLE001
                                logger.debug(f"[jd] post-login relaunch failed: {e}")
                        if not logged:
                            return SendResult(
                                SendStatus.FAILED,
                                SendRejectReason.PLATFORM_REJECTED.value,
                                detail="登录未完成（等待超时/关窗）",
                                fix_hint="京东游客发送区无输入框（页面提示'请先登录"
                                         "再发弹幕互动'）——重试发送将再次弹出登录窗口")
                        if page in context.pages:
                            # 同窗登录（用户在房间页完成登录）——重载渲染发送面
                            # （page 级 WS 收集器跨导航存活，frames 继续累积）
                            try:
                                await page.reload(timeout=45000,
                                                  wait_until="domcontentloaded")
                                await asyncio.sleep(3)
                            except Exception as e:  # noqa: BLE001
                                logger.debug(f"[jd] post-login reload warning: {e}")
                        else:
                            # 关窗竞态复检窗——新 page 重挂 WS 收集器并导航
                            page = (context.pages[0] if context.pages
                                    else await context.new_page())
                            ws_frames = _wire_ws(page)
                            try:
                                await page.goto(url, timeout=45000,
                                                wait_until="domcontentloaded")
                                await asyncio.sleep(3)
                            except Exception as e:  # noqa: BLE001
                                logger.debug(f"[jd] post-login goto warning: {e}")
                    # 输入框发现：逐匹配扫描 + 可见且可编辑过滤（huya 包裹层教训）
                    inp = None
                    inp_sel = None
                    for _ in range(5):
                        for sel in self.SEND_INPUT_SELECTORS:
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
                        return SendResult(SendStatus.FAILED,
                                          SendRejectReason.PLATFORM_REJECTED.value,
                                          fix_hint="登录后仍未发现发送输入框（页面结构"
                                                   "变更）——重跑 DOM 探针核对选择器")
                    await inp.click()
                    await inp.press_sequentially(content, delay=50)
                    btn = None
                    for sel in self.SEND_BUTTON_SELECTORS:
                        try:
                            loc = page.locator(sel).first
                            if await loc.count() > 0 and await loc.is_visible():
                                btn = loc
                                break
                        except Exception:  # noqa: BLE001
                            continue
                    try:
                        if btn is not None:
                            await btn.click(timeout=3_000)
                        else:
                            await inp.press("Enter")
                    except Exception:  # noqa: BLE001
                        await inp.press("Enter")
                    # F3 回显判定：WS viewer_send_message 服务端回环 = SENT 唯一真源
                    echo_deadline = time.monotonic() + 15
                    while time.monotonic() < echo_deadline:
                        hit = jd_ws_echo_frame(ws_frames, content)
                        if hit is not None:
                            await self._emit_self_echo(room_id, hit)
                            return SendResult(SendStatus.SENT,
                                              sent_at=int(time.time()))
                        await asyncio.sleep(1)
                    # 次级诊断信号：DOM 列表命中（乐观渲染不可信——仅区分
                    # "提交未触发"与"提交了但 WS 不回环自己"两种失败形态）
                    dom_hit = False
                    try:
                        body_text = await page.evaluate("() => document.body.innerText")
                        dom_hit = content in (body_text or "")
                    except Exception:  # noqa: BLE001
                        pass
                    return SendResult(SendStatus.UNKNOWN,
                                      SendRejectReason.SEND_TIMEOUT.value,
                                      detail="无服务端 WS 回环；DOM 列表"
                                             + ("命中（乐观渲染不可信）" if dom_hit
                                                else "未命中")
                                             + "——按未知处理",
                                      fix_hint="查看弹幕流回环确认；连续 UNKNOWN 请核对"
                                               "账号是否被禁言/风控")
                finally:
                    try:
                        await context.close()  # 干净关闭——cookie 提交落盘（F2 归还 profile）
                    except Exception:  # noqa: BLE001
                        pass
        except Exception as e:  # noqa: BLE001  E5 隔离：异常不出引擎边界
            logger.warning(f"[jd] send_danmu error: {type(e).__name__}: {str(e)[:100]}")
            return SendResult(SendStatus.FAILED, SendRejectReason.PLATFORM_REJECTED.value,
                              detail=f"{type(e).__name__}: {str(e)[:100]}")
        finally:
            self._send_active = False
            self._profile_lock.release()

    async def _emit_self_echo(self, room_id: str, frame: Dict[str, Any]) -> None:
        """F4 自发声回环：把发送侧拿到的服务端确认帧注入消息流（背景见
        xiaohongshu 同名方法——F2 让位窗口内监听离线，自弹幕广播落在窗口里）"""
        try:
            mapped = map_jd_message(frame, self.next_seq(room_id), int(time.time()))
            if not mapped or mapped["type"] != "DANMU":
                return
            self._self_echo_mark = (mapped["payload"].get("content", ""),
                                    time.monotonic())
            await self._emit_message(self._envelope(room_id, mapped))
            logger.info(f"[jd] 自发声回环注入: room={room_id}")
        except Exception as e:  # noqa: BLE001  回环失败不影响发送回执
            logger.debug(f"[jd] self echo emit failed: {type(e).__name__}: {e}")

    def _send_room_url(self, room_id: str) -> str:
        return LIVE_URL_TEMPLATE.format(room_id=room_id)

    """京东直播弹幕引擎（受控页面 WS 帧拦截，独立平台）"""

    platform = "jd"
    profile_name = "jd_profile"
    protocol_version = "jd-1"

    def __init__(self, state_store=None, cookie_dir: str = "./cookie",
                 raw_hook=None):
        super().__init__(state_store=state_store, cookie_dir=cookie_dir)
        self._raw_hook = raw_hook  # 诊断钩子：全部 JSON 帧（含未映射）回调
        # F4 自发声回环去重标记：(content, monotonic)——发送侧注入后 10s 内
        # 监听侧同内容 DANMU 跳过（danmu-send 计划 R39 义务落地）
        self._self_echo_mark: Optional[tuple] = None

    def validate_room_id(self, room_id: str) -> None:
        try:
            extract_room_id(room_id)
        except JDParseError as e:
            raise ValueError(str(e)) from e

    # ---- 房间任务主循环 ----

    async def _run_room(self, room_id: str) -> None:
        room_key = extract_room_id(room_id)
        # 链接形态整体直达（保留风控参数）；纯数字 liveId 拼独立站模板
        goto_url = (room_id.strip() if room_id.strip().startswith("http")
                    else LIVE_URL_TEMPLATE.format(room_id=room_key))
        logger.info(f"[jd] room {room_id} connecting (key={room_key})")
        backoff = 15.0
        while not self._stopped(room_id):
            try:
                await self._run_session(room_id, room_key, goto_url)
                backoff = 15.0
            except self.NeedLoginVisible as sig:
                # 登录闭环（2026-10-06 平台登录计划，用户裁定 B）：撕裂会话弹可见
                # 窗口（live1688 NeedLoginVisible 同款时序）；预算在检测点扣减
                logger.info(f"[jd] room {room_id} login gate triggered")
                await self._emit_system_status(
                    room_id,
                    f"{LOGIN_GATE.LOGIN_EVENT_FIRST_LOGIN}: 京东直播间登录增强——"
                    "已弹出浏览器，请登录京东账号（可跳过，游客可收弹幕）")
                outcome = await self._wait_login_visible(
                    room_id, sig.goto_url, LOGIN_GATE.LOGIN_COOKIE_NAMES["jd"])
                if outcome == "logged_in":
                    self._login_budgets[room_id] = LOGIN_GATE.LOGIN_BUDGET  # 成功清零
                    self._budget_warned.discard(room_id)
                    await self._emit_system_status(room_id, "登录成功——恢复监听")
                backoff = 15.0
                continue  # 重开会话（登录态已入 profile / 或降级游客继续）
            except asyncio.CancelledError:
                raise
            except JDParseError as e:
                logger.warning(f"[jd] room {room_id} {e}")
                await self._emit_route_failed(room_id, "jd.page.parse_failed",
                                              str(e), "docs/platforms/jd/runbook.md")
                return  # 参数问题：重试无意义，等用户改参数
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[jd] room {room_id} error: {type(e).__name__}: {str(e)[:90]}")
                if self._is_stopping(room_id):
                    return  # stop-in-progress close is expected (sent 1000) - no GAP/ERROR

                self._set_status(self.status.__class__.ERROR)
                self.mark_gap_start(room_id)
                gap = self.build_gap_message(room_id, GapReason.NETWORK)
                if gap:
                    await self._emit_system(gap)
                if self._stopped(room_id):
                    return
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 900.0)

    async def _run_session(self, room_id: str, room_key: str,
                           goto_url: str) -> None:
        """单次有界会话：常驻页面 + WS 帧拦截 + 业务帧静默检测"""
        from playwright.async_api import async_playwright

        session_start = int(time.time())
        deadline = time.monotonic() + 14400.0
        self._touch_frame(room_id)  # 静默计时起点

        # F2：发送占用 profile 时让位（同 profile 双开 TargetClosedError 实证——
        # 发送前会关停本 context；等待发送完成归还后再重建）
        while self._send_active and not self._stopped(room_id):
            await asyncio.sleep(2)
        async with async_playwright() as pw:
            context = await self._launch(pw, headless=True)
            self._listen_ctx = context
            try:
                page = context.pages[0] if context.pages else await context.new_page()
                self._wire_ws_intercept(room_id, page)

                try:
                    await page.goto(goto_url,
                                    timeout=30000, wait_until="domcontentloaded")
                except Exception as e:  # noqa: BLE001
                    logger.debug(f"[jd] room {room_id} goto warning: {e}")

                logger.info(f"[jd] room {room_id} live page ready")

                # 登录门槛（2026-10-06 用户裁定 B：pt_key/pt_pin 判定→弹窗；
                # 预算在弹出前扣减——taobao 同款语义；预算未尽才撕裂会话）
                cookies = await context.cookies()
                if not LOGIN_GATE.has_login_cookie(
                        cookies, LOGIN_GATE.LOGIN_COOKIE_NAMES["jd"]):
                    if self._consume_login_budget(room_id):
                        raise self.NeedLoginVisible(goto_url)
                    if room_id not in self._budget_warned:
                        self._budget_warned.add(room_id)
                        await self._emit_system_status(
                            room_id,
                            f"{LOGIN_GATE.LOGIN_EVENT_BUDGET_EXHAUSTED}: 登录未完成——"
                            "已按游客模式继续（游客可收弹幕）；停止该房间后重新添加"
                            "可再次触发登录窗口")

                self._set_status(self.status.__class__.RUNNING)

                while time.monotonic() < deadline and not self._stopped(room_id):
                    await asyncio.sleep(2)
                    if self._listen_ctx is not context:
                        logger.info(f"[jd] room {room_id} 监听 context 已让位给"
                                    "发送（F2）——发送完成后自动重建")
                        return
                    if self._frame_silent(room_id, SILENCE_TIMEOUT):
                        raise JDParseError(
                            "jd.session.silent: 120s 无业务帧"
                            "（可能未开播/已下播/风控——确认直播中）")
            finally:
                if self._listen_ctx is context:
                    self._listen_ctx = None
                try:
                    await context.close()
                except Exception:  # noqa: BLE001  context 已被发送侧关停（F2）
                    pass
        elapsed = int(time.time()) - session_start
        if elapsed >= 14400.0:
            logger.info(f"[jd] room {room_id} session rebuilt after {elapsed}s")

    async def _on_ws_frame(self, room_id: str, payload: Any) -> None:
        """framereceived 事件 → 解析 emit（payload 为 {"payload": str|bytes}）"""
        raw = payload.get("payload") if isinstance(payload, dict) else payload
        if raw is None:
            return
        for obj in parse_jd_frame(raw):
            self._touch_frame(room_id)
            self.mark_received(room_id, int(time.time()))
            if self._raw_hook is not None:
                try:
                    self._raw_hook(obj)
                except Exception:  # noqa: BLE001
                    pass
            mapped = map_jd_message(obj, self.next_seq(room_id), int(time.time()))
            if mapped:
                # F4 自发声回环去重（R39）：发送侧已注入的弹幕，监听侧 10s 内
                # 同内容 DANMU 跳过（时序竞态双份防护——正常让位窗口内监听离线）
                if mapped["type"] == "DANMU" and self._self_echo_mark is not None:
                    echo_content, echo_t = self._self_echo_mark
                    if (mapped["payload"].get("content") == echo_content
                            and time.monotonic() - echo_t < 10):
                        self._self_echo_mark = None
                        logger.debug(f"[jd] room {room_id} 自发声回环去重"
                                     "（监听侧同帧竞态）")
                        continue
                await self._emit_message(self._envelope(room_id, mapped))
