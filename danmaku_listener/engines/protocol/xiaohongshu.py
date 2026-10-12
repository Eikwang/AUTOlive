"""小红书直播弹幕引擎（受控页面 WS 帧拦截——1688 同构，拦截对象为 WebSocket）

技术路线（2026-09-30 调研定型，四平台第二站）：
小红书直播间页（www.xiaohongshu.com/livestream/{room_id}）通过页内 WebSocket
下发业务帧，观众侧无需登录（设备 cookie a1 访问时自动种下）。引擎常驻
Playwright 页面，拦截 WS `framereceived` 帧解析（参考 qdlx2000/xhs-recorder；
修正其 framereceived payload 传参 bug）。

帧结构（调研+开源实证）：
JSON 字符串帧 t==4 → data.b.d.b[] 数组 → 每项 .d 字段 base64 → JSON
→ customData（JSON 字符串）→ 二次 parse → 业务对象：
- type=text：弹幕（desc=内容、profile.nickname/user_id）
- type=audience_join(_v2)：进入；type=like：点赞；type=gift：礼物
  （giftName/count）；type=follow_emcee：关注
- type=refresh/letter_refresh：链路活跃信号（用于存活判定，不 emit）

下播检测：t==4 业务帧静默 90s（弹幕/refresh 均停 = 下播/未开播/风控）。
房间参数：room_id 数字 / xiaohongshu.com/livestream/ 链接。
"""

import asyncio
import base64
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
from danmaku_listener.engines.protocol.xhs_gift_prices import lookup_price

PROTOCOL_VERSION = "xiaohongshu-1"

LIVE_URL_TEMPLATE = "https://www.xiaohongshu.com/livestream/{room_id}"
SILENCE_TIMEOUT = 90.0  # 业务帧静默阈值（refresh 类帧持续流动时不会触发）


class XiaohongshuParseError(ValueError):
    """房间参数无法解析"""


def extract_room_id(room_spec: str) -> str:
    """room_id 归一：数字 / xiaohongshu 直播间链接（livestream/{id}）"""
    spec = room_spec.strip()
    if "xiaohongshu.com" in spec or "xhslink.com" in spec:
        # 2026-10-09：新分享链接形态 livestream/<dynpath>/<id>（中间可有路径段）——
        # 旧正则 livestream/(\d+) 要求紧邻，新形态解析失败（验收用户实测）
        m = re.search(r"livestream/(?:[^/\s]*/)?(\d+)", spec)
        if m:
            return m.group(1)
        raise XiaohongshuParseError(
            f"无法从小红书链接提取直播间 room_id: {spec[:80]!r}——"
            "请使用直播间页链接（.../livestream/<数字id>）")
    m = re.match(r"^(\d{5,25})$", spec)
    if m:
        return m.group(1)
    raise XiaohongshuParseError(
        f"无法解析小红书直播间 room_id: {spec[:60]!r}——"
        "请使用直播间链接或 room_id 数字")


def parse_ws_frame(raw) -> List[Dict[str, Any]]:
    """WS 帧解析 → 业务对象列表（纯函数，供单测）

    t==4 业务帧 → b.d.b[] → 每项 .d base64 → JSON → customData 二次 parse。
    非 JSON/非业务帧/解析失败均返回空列表（页内还有其他 WS，帧形态各异）。
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
    if not isinstance(data, dict) or data.get("t") != 4:
        return []
    items = (((data.get("b") or {}).get("d") or {}).get("b")) or []
    out = []
    for item in items:
        if not isinstance(item, dict):
            continue
        encoded = item.get("d", "")
        if not encoded:
            continue
        try:
            wrapper = json.loads(base64.b64decode(encoded).decode("utf-8", errors="replace"))
        except Exception:  # noqa: BLE001
            continue
        custom = wrapper.get("customData")
        if not custom:
            continue
        if isinstance(custom, dict):
            out.append(custom)
            continue
        try:
            cd = json.loads(custom)
            if isinstance(cd, dict):
                out.append(cd)
        except (json.JSONDecodeError, TypeError):
            continue
    return out


def map_custom_data(cd: Dict[str, Any], seq: int, ts: int,
                    nick_cache: Optional[Dict[str, str]] = None) -> Optional[Dict[str, Any]]:
    """customData → 契约消息映射；未识别类型返回 None

    2026-09-30 在播房间实测校准（xhs_raw.jsonl 696 条采样）：
    - 点赞 type=praise（调研推断的 "like" 实测不存在），count 在
      praise_info.count（本次点赞事件聚合数，非累计）；praise 的 profile
      无 nickname → 查 nick_cache 反查（2026-10-03 用户需求"点赞显示
      实际用户名"）
    - 礼物 type=gift_dock_and_effect（"gift" 不存在）：send_user_info.nick_name
      （下划线命名）/ base_gift_info.name / gift_action_info.count（本次）；
      gift_comment/gift_settle 为同一次送礼的重复视图（时序实证）——
      跳过防重复计数
    - share → SOCIAL(action=share)；light 为进场来源路径（语义待定，不映射）

    nick_cache（会话内 user_id→nickname 学习表，引擎持有跨帧复用）：
    - refresh 帧的 room_data.viewers[] 为在线观众全量名单（user_id+nickname）
    - text/audience_join_v2/follow_emcee/share 的 profile 均带双字段
    - gift_dock_and_effect 的 send_user_info（id/nick_name）同样入表
    """
    cache = nick_cache if nick_cache is not None else {}
    cd_type = cd.get("type", "")
    profile = cd.get("profile") or {}
    user_name = str(profile.get("nickname", ""))
    user_id = str(profile.get("user_id", ""))
    base = {"category": "business", "seq": seq, "timestamp": ts}

    if cd_type == "refresh":
        for v in ((cd.get("room_data") or {}).get("viewers") or []):
            vid, vnick = str(v.get("user_id", "")), str(v.get("nickname", ""))
            if vid and vnick:
                cache[vid] = vnick
        # 在线人数（2026-10-06 用户实测缺口补齐）：refresh 的 room_data.viewers
        # 为在线观众全量名单——名单非空时映射 ROOM_STATS（viewer_count=名单长度；
        # 名单为空跳过——空名单可能意味着名单机制未启用而非 0 人，避免"观看 0"
        # 误导，对齐 taobao 2026-10-03 语义校准教训）。引擎层做同值去重。
        viewers = (cd.get("room_data") or {}).get("viewers") or []
        if viewers:
            return {**base, "type": "ROOM_STATS",
                    "payload": {"type": "ROOM_STATS",
                                "viewer_count": len(viewers),
                                "total_view_count": 0}}
        return None
    if user_id and user_name:
        cache[user_id] = user_name
    if cd_type == "text":
        content = (cd.get("desc") or "").strip()
        if not content:
            return None
        return {**base, "type": "DANMU",
                "payload": {"type": "DANMU", "user_name": user_name,
                            "content": content, "user_id": user_id}}
    if cd_type in ("audience_join", "audience_join_v2"):
        return {**base, "type": "ENTER_ROOM",
                "payload": {"type": "ENTER_ROOM", "user_name": user_name,
                            "user_id": user_id}}
    if cd_type == "praise":
        count = (cd.get("praise_info") or {}).get("count", 1)
        if not isinstance(count, int) or count < 1:
            count = 1
        # praise 帧 profile 无 nickname——按 user_id 查会话学习表（观众名单/
        # 弹幕/进场/关注帧学到的昵称），未命中置空（前端回退"有人"）
        return {**base, "type": "LIKE",
                "payload": {"type": "LIKE", "user_name": cache.get(user_id, ""),
                            "user_id": user_id,
                            "count": count}}
    if cd_type == "gift_dock_and_effect":
        send = cd.get("send_user_info") or {}
        gift = cd.get("base_gift_info") or {}
        action = cd.get("gift_action_info") or {}
        gift_name = gift.get("name", "")
        if not gift_name:
            return None
        count = action.get("count", 1)
        if not isinstance(count, int) or count < 1:
            count = 1
        send_id, send_nick = str(send.get("id", "")), str(send.get("nick_name", ""))
        if send_id and send_nick:
            cache[send_id] = send_nick
        # 价格：按名查实测价格表（薯币）；coins（协议自带单价）优先
        unit = gift.get("coins") or lookup_price(gift_name)
        return {**base, "type": "GIFT",
                "payload": {"type": "GIFT",
                            "user_name": send_nick,
                            "user_id": send_id,
                            "gift_name": gift_name, "gift_count": count,
                            "gift_value": unit * count}}
    if cd_type in ("follow_emcee", "follow"):
        # follow_emcee 为调研推断名（未在 2026-09-30 采样中实测命中——采样期间
        # 无关注动作）；2026-10-06 用户实测收不到关注事件，加宽匹配 "follow"
        # 变体容错，未识别 type 由引擎层首见日志校准（复测点一次关注即可确认）
        return {**base, "type": "SOCIAL",
                "payload": {"type": "SOCIAL", "action": "follow",
                            "user_name": user_name, "user_id": user_id}}
    if cd_type == "share":
        return {**base, "type": "SOCIAL",
                "payload": {"type": "SOCIAL", "action": "share",
                            "user_name": user_name, "user_id": user_id}}
    # refresh/letter_refresh：活跃信号；gift_comment/gift_settle：送礼重复视图；
    # light/live_banner_resource/goods_rank_entrance_im：运营位/来源路径——均不 emit
    return None


def ws_echo_frame(frames: Iterable[Any], content: str) -> Optional[Dict[str, Any]]:
    """从 WS 帧集合提取本人弹幕的服务端回环 customData（F3——回显判定唯一真源）

    2026-10-10 探针实证：DOM 列表匹配受本地乐观渲染与虚拟列表影响（10-09
    16:06 两次假 SENT 的来源），WS text 帧是服务端确认。frames 为
    framereceived payload 原始集合（str/bytes/None 混合），用 parse_ws_frame
    解析。命中返回业务对象（供 F4 自发声回环注入消息流），未命中 None。
    纯函数供单测。
    """
    for raw in frames:
        if raw is None:
            continue
        for cd in parse_ws_frame(raw):
            if cd.get("type") == "text" and (cd.get("desc") or "").strip() == content:
                return cd
    return None


def ws_echo_hit(frames: Iterable[Any], content: str) -> bool:
    """bool 包装（ws_echo_frame——既有单测与判定语义保留）"""
    return ws_echo_frame(frames, content) is not None


class XiaohongshuEngine(ControlledPageEngine):
    """小红书受控页面引擎（AutoDanmu send 钩子同 E5 纪律——选择器待 M0 校准）"""

    SEND_INPUT_SELECTORS = ["#input-area",
                            "#input-area div[contenteditable=true]",
                            "#input-area textarea",
                            "textarea", "div[contenteditable=true]"]
    SEND_BUTTON_SELECTORS = ["#input-area button", "#msg_send_bt"]
    LOGIN_WAIT_TIMEOUT_S = 240.0   # 页内登录等待（用户扫码/验证期间发送挂起）

    # ---- 方案 B：瞬态后台发送会话（2026-10-10 profile 重测探针裁定）----
    # 探针实证链（cards/xhs-persist-*）：
    # 1. 登录 cookie 为持久型（id_token/web_session 1 年期）——"会话级"旧结论废除；
    #    历史登录全丢根因=硬杀丢未提交窗口 + 登录从未在 profile 内完成落盘
    # 2. 同 profile 双 persistent context 必然 TargetClosedError（F2——基类公共件）
    # 3. 游客态 #input-area 可见——输入框可见性判登录失真 → 改 cookie（id_token）判定
    # 4. 登录态持久化=F1 快照（登录检测点立即落盘）+ _launch 恢复，不再依赖浏览器存活

    async def send_danmu(self, room_id: str, content: str):
        """小红书发送（方案 B）：瞬态 headed 会话 + cookie 判登录（未登录窗内等待）
        + #input-area 配方（逐键+按钮时序）+ WS 帧服务端回环判定。"""
        from danmaku_listener.contract.models import SendRejectReason, SendStatus
        from danmaku_listener.senders.base import SendResult

        try:
            await asyncio.wait_for(self._profile_lock.acquire(),
                                   timeout=3.0)  # S4-1/R14
        except asyncio.TimeoutError:
            return SendResult(SendStatus.FAILED, SendRejectReason.PLATFORM_REJECTED.value,
                              fix_hint="profile 锁被监听操作占用——稍后重试（busy）")
        self._send_active = True
        try:
            from playwright.async_api import async_playwright

            # 房间 URL：链接形态原样携带（含 xsec_token）；纯数字才拼模板
            url = (room_id.strip() if "xiaohongshu.com" in room_id
                   else self._send_room_url(room_id))
            async with async_playwright() as pw:
                await self._close_listen_ctx()  # F2：监听让位（会话侧 _send_active 门等待归还）
                # 无感模式：headless=new 后台发送（登录态探针实证 SENT）；
                # 登录续期必须可见窗（扫码）——唯一弹窗场景，登录后快照落盘
                # 即回到无感
                context = await self._launch(pw, headless=False,
                                             extra_args=SEND_BG_EXTRA_ARGS)
                try:
                    logged = await self._ctx_has_login(context)
                    if not logged:
                        logger.info("[xhs] 后台会话无登录态——改弹可见窗口等待登录")
                        try:
                            await context.close()
                        except Exception:  # noqa: BLE001
                            pass
                        context = await self._launch(pw, headless=False)  # headed 可见（登录窗）
                    page = (context.pages[0] if context.pages
                            else await context.new_page())
                    # F3：WS 帧收集必须先于 goto（已有连接不触发 websocket 事件——
                    # 2026-10-10 探针实证后挂收集器 0 帧）
                    ws_frames: List[Any] = []

                    def _on_ws(ws) -> None:
                        ws.on("framereceived",
                              lambda p: ws_frames.append(
                                  p.get("payload") if isinstance(p, dict) else p))

                    page.on("websocket", _on_ws)
                    try:
                        await page.goto(url, timeout=45000,
                                        wait_until="domcontentloaded")
                    except Exception as e:  # noqa: BLE001
                        return SendResult(SendStatus.UNKNOWN,
                                          SendRejectReason.SEND_TIMEOUT.value,
                                          detail=f"goto: {type(e).__name__}")
                    await asyncio.sleep(3)
                    # 登录判定已在启动前完成（cookie 基准——_launch 已做快照恢复，
                    # 无登录=快照也无效，已在 headed 窗口等待）；此处仅 headed
                    # 路径进入等待循环（最长 240s），登录即快照（F1）
                    if not logged:
                        logger.info(f"[xhs] room {room_id} 未登录——等待用户在 headed 窗口"
                                    f"完成登录（最长 {self.LOGIN_WAIT_TIMEOUT_S:.0f}s，"
                                    "完成后自动继续发送）")
                        logged = await self._wait_login_interactive(
                            context, self.LOGIN_WAIT_TIMEOUT_S)
                        if not logged:
                            return SendResult(
                                SendStatus.FAILED,
                                SendRejectReason.PLATFORM_REJECTED.value,
                                detail="登录未完成（等待超时）",
                                fix_hint="小红书游客发送会被服务端静默吞——重试发送"
                                         "将再次弹出登录窗口，完成登录后自动继续")
                    # 输入框发现（发现模式：候选序+可见性）
                    inp = None
                    for sel in self.SEND_INPUT_SELECTORS:
                        try:
                            loc = page.locator(sel).first
                            if await loc.count() > 0 and await loc.is_visible():
                                inp = loc
                                break
                        except Exception:  # noqa: BLE001
                            continue
                    if inp is None:
                        return SendResult(SendStatus.FAILED,
                                          SendRejectReason.PLATFORM_REJECTED.value,
                                          fix_hint="未发现发送输入框（页面结构变更）"
                                                   "——重跑 M0 探针核对选择器")
                    await inp.click()
                    await inp.press_sequentially(content, delay=50)
                    # 发送按钮输入文字后才出现（验收用户实测）——先 Enter 兜底再按钮
                    await inp.press("Enter")
                    btn = page.locator("#input-area button").first
                    if await btn.count() > 0 and await btn.is_visible():
                        try:
                            await btn.click(timeout=3_000)
                        except Exception:  # noqa: BLE001
                            pass
                    # F3 回显判定：WS text 帧服务端回环 = SENT 唯一真源；
                    # 命中帧同时注入消息流（F4 自发声回环——F2 让位窗口内监听
                    # 离线，自弹幕广播恰好落在窗口里，发送侧是唯一可靠来源）
                    echo_deadline = time.monotonic() + 15
                    while time.monotonic() < echo_deadline:
                        hit_cd = ws_echo_frame(ws_frames, content)
                        if hit_cd is not None:
                            await self._emit_self_echo(room_id, hit_cd)
                            return SendResult(SendStatus.SENT,
                                              sent_at=int(time.time()))
                        await asyncio.sleep(1)
                    return SendResult(SendStatus.UNKNOWN,
                                      SendRejectReason.SEND_TIMEOUT.value,
                                      detail="无服务端 WS 回环（弹幕可能已进聊天流——"
                                             "乐观渲染不可信，按未知处理）",
                                      fix_hint="查看弹幕流回环确认；连续 UNKNOWN "
                                               "请核对账号是否被禁言/风控")
                finally:
                    try:
                        await context.close()  # 干净关闭——cookie 提交落盘（F2 归还 profile）
                    except Exception:  # noqa: BLE001
                        pass
        except Exception as e:  # noqa: BLE001  E5 隔离：异常不出引擎边界
            logger.warning(f"[xiaohongshu] send_danmu error: {type(e).__name__}: {str(e)[:100]}")
            return SendResult(SendStatus.FAILED, SendRejectReason.PLATFORM_REJECTED.value,
                              detail=f"{type(e).__name__}: {str(e)[:100]}")
        finally:
            self._send_active = False
            self._profile_lock.release()

    async def _emit_self_echo(self, room_id: str, cd: Dict[str, Any]) -> None:
        """F4 自发声回环：把发送侧拿到的服务端确认帧注入消息流

        背景（2026-10-10 用户实测）：F2 让位窗口内监听离线，自弹幕的房间
        广播恰好落在窗口里（WS 广播不补发）——监听永远收不到自己发的弹幕。
        发送流程手里本就握着这条帧（SENT 判定依据），直接按监听同款信封
        注入；监听侧若因时序竞态也收到同帧，由 _self_echo_mark 10s 去重。
        """
        try:
            mapped = map_custom_data(cd, self.next_seq(room_id),
                                     int(time.time()), self._nick_cache)
            if not mapped or mapped["type"] != "DANMU":
                return
            self._self_echo_mark = (mapped["payload"].get("content", ""),
                                    time.monotonic())
            await self._emit_message(self._envelope(room_id, mapped))
            logger.info(f"[xhs] 自发声回环注入: room={room_id} "
                        f"content={mapped['payload'].get('content', '')[:20]!r}")
        except Exception as e:  # noqa: BLE001  回环失败不影响发送回执
            logger.debug(f"[xhs] self echo emit failed: {type(e).__name__}: {e}")

    def _send_room_url(self, room_id: str) -> str:
        return LIVE_URL_TEMPLATE.format(room_id=room_id)

    """小红书直播弹幕引擎（受控页面 WS 帧拦截，独立平台）"""

    platform = "xiaohongshu"
    profile_name = "xhs_profile"
    protocol_version = "xiaohongshu-1"

    def __init__(self, state_store=None, cookie_dir: str = "./cookie",
                 raw_hook=None):
        super().__init__(state_store=state_store, cookie_dir=cookie_dir)
        self._raw_hook = raw_hook  # 诊断钩子：每个解析出的 customData（含未映射）回调
        # 会话内 user_id→nickname 学习表（refresh 观众名单/弹幕/进场/关注/
        # 礼物帧学习；praise 帧无昵称靠它反查——2026-10-03 用户需求）
        self._nick_cache: Dict[str, str] = {}
        # ROOM_STATS 同值去重（refresh 高频——viewer_count 不变时跳过 emit）
        self._last_room_stats: Dict[str, tuple] = {}
        # 未识别 customData 类型首见集合（校准日志——follow 真实 type 名确认用）
        self._unmapped_seen: set = set()
        # F4 自发声回环去重标记：(content, monotonic)——发送侧注入后 10s 内
        # 监听侧同内容 DANMU 跳过（danmu-send 计划 R39 义务落地）
        self._self_echo_mark: Optional[tuple] = None

    def validate_room_id(self, room_id: str) -> None:
        try:
            extract_room_id(room_id)
        except XiaohongshuParseError as e:
            raise ValueError(str(e)) from e

    # ---- 房间任务主循环 ----

    async def _run_room(self, room_id: str) -> None:
        room_key = extract_room_id(room_id)
        # 原始链接直达（2026-09-30 用户实测：直播间链接带 xsec_token 风控参数，
        # 必须原样携带——纯 room_id 拼模板可能被拒；纯数字 room_id 才拼模板）
        goto_url = (room_id.strip() if "xiaohongshu.com" in room_id
                    else LIVE_URL_TEMPLATE.format(room_id=room_key))
        logger.info(f"[xhs] room {room_id} connecting (room_id={room_key})")
        backoff = 15.0
        while not self._stopped(room_id):
            try:
                await self._run_session(room_id, room_key, goto_url)
                backoff = 15.0
            except self.NeedLoginVisible as sig:
                # 登录闭环（2026-10-06 平台登录计划，用户裁定 B）：撕裂会话弹可见
                # 窗口（live1688 NeedLoginVisible 同款时序）；预算在检测点扣减
                logger.info(f"[xhs] room {room_id} login gate triggered")
                await self._emit_system_status(
                    room_id,
                    f"{LOGIN_GATE.LOGIN_EVENT_FIRST_LOGIN}: 小红书直播间登录增强——"
                    "已弹出浏览器，请登录小红书账号（可跳过，游客可收弹幕）")
                outcome = await self._wait_login_visible(
                    room_id, sig.goto_url, LOGIN_GATE.LOGIN_COOKIE_NAMES["xiaohongshu"])
                if outcome == "logged_in":
                    self._login_budgets[room_id] = LOGIN_GATE.LOGIN_BUDGET  # 成功清零
                    self._budget_warned.discard(room_id)
                    await self._emit_system_status(room_id, "登录成功——恢复监听")
                backoff = 15.0
                continue  # 重开会话（登录态已入 profile / 或降级游客继续）
            except asyncio.CancelledError:
                raise
            except XiaohongshuParseError as e:
                logger.warning(f"[xhs] room {room_id} {e}")
                await self._emit_route_failed(room_id, "xiaohongshu.page.parse_failed",
                                              str(e), "docs/platforms/xiaohongshu/runbook.md")
                if self._stopped(room_id):
                    return
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 900.0)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[xhs] room {room_id} error: {type(e).__name__}: {str(e)[:90]}")
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
                    logger.debug(f"[xhs] room {room_id} goto warning: {e}")

                logger.info(f"[xhs] room {room_id} live page ready")

                # 登录门槛（2026-10-06 用户裁定 B：web_session 判定→弹窗；
                # 预算在弹出前扣减——taobao 同款语义；预算未尽才撕裂会话）
                cookies = await context.cookies()
                if not LOGIN_GATE.has_login_cookie(
                        cookies, LOGIN_GATE.LOGIN_COOKIE_NAMES["xiaohongshu"]):
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

                # 有界会话 + 业务帧静默检测（t==4 帧含 refresh 心跳，正常持续流动）
                while time.monotonic() < deadline and not self._stopped(room_id):
                    await asyncio.sleep(2)
                    if self._listen_ctx is not context:
                        logger.info(f"[xhs] room {room_id} 监听 context 已让位给"
                                    "发送（F2）——发送完成后自动重建")
                        return
                    if self._frame_silent(room_id, SILENCE_TIMEOUT):
                        raise XiaohongshuParseError(
                            "xiaohongshu.session.silent: 90s 无业务帧"
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
            logger.info(f"[xhs] room {room_id} session rebuilt after {elapsed}s")

    async def _on_ws_frame(self, room_id: str, payload: Any) -> None:
        """framereceived 事件 → 解析 emit（Playwright payload 为 {"payload": str|bytes}）"""
        raw = payload.get("payload") if isinstance(payload, dict) else payload
        if raw is None:
            return
        for cd in parse_ws_frame(raw):
            self._touch_frame(room_id)  # 业务帧到达 = 链路活跃
            self.mark_received(room_id, int(time.time()))
            if self._raw_hook is not None:
                try:
                    self._raw_hook(cd)
                except Exception:  # noqa: BLE001
                    pass
            mapped = map_custom_data(cd, self.next_seq(room_id), int(time.time()),
                                     self._nick_cache)
            if not mapped:
                # 未识别类型首见日志（2026-10-06 关注事件缺口校准——复测点关注
                # 即可从日志确认真实 type 名，对齐 pdd/taobao 校准模式）
                cd_type = cd.get("type", "")
                if cd_type and cd_type not in self._unmapped_seen:
                    self._unmapped_seen.add(cd_type)
                    logger.info(f"[xhs] room {room_id} unmapped customData type "
                                f"first seen: {cd_type!r} keys={sorted(cd.keys())[:8]}")
                continue
            # F4 自发声回环去重（R39）：发送侧已注入的弹幕，监听侧 10s 内
            # 同内容 DANMU 跳过（时序竞态双份防护——正常让位窗口内监听离线，
            # 此分支极少命中）
            if mapped["type"] == "DANMU" and self._self_echo_mark is not None:
                echo_content, echo_t = self._self_echo_mark
                if (mapped["payload"].get("content") == echo_content
                        and time.monotonic() - echo_t < 10):
                    self._self_echo_mark = None
                    logger.debug(f"[xhs] room {room_id} 自发声回环去重"
                                 "（监听侧同帧竞态）")
                    continue
            # ROOM_STATS 同值去重（refresh 高频，名单数不变时跳过 emit——
            # 对齐 taobao _last_room_stats 模式；seq 已消耗同现状无害）
            if mapped["type"] == "ROOM_STATS":
                sig = (mapped["payload"].get("viewer_count", 0),)
                if sig == self._last_room_stats.get(room_id):
                    continue
                self._last_room_stats[room_id] = sig
            await self._emit_message(self._envelope(room_id, mapped))
