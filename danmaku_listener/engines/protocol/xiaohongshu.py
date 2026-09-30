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
from typing import Any, Dict, List, Optional

from loguru import logger

from danmaku_listener.contract.models import GapReason
from danmaku_listener.engines.protocol.controlled_base import ControlledPageEngine

PROTOCOL_VERSION = "xiaohongshu-1"

LIVE_URL_TEMPLATE = "https://www.xiaohongshu.com/livestream/{room_id}"
SILENCE_TIMEOUT = 90.0  # 业务帧静默阈值（refresh 类帧持续流动时不会触发）


class XiaohongshuParseError(ValueError):
    """房间参数无法解析"""


def extract_room_id(room_spec: str) -> str:
    """room_id 归一：数字 / xiaohongshu 直播间链接（livestream/{id}）"""
    spec = room_spec.strip()
    if "xiaohongshu.com" in spec or "xhslink.com" in spec:
        m = re.search(r"livestream/(\d+)", spec)
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


def map_custom_data(cd: Dict[str, Any], seq: int, ts: int) -> Optional[Dict[str, Any]]:
    """customData → 契约消息映射（纯函数）；未识别类型返回 None

    2026-09-30 在播房间实测校准（xhs_raw.jsonl 696 条采样）：
    - 点赞 type=praise（调研推断的 "like" 实测不存在），count 在
      praise_info.count（本次点赞事件聚合数，非累计）；praise 的 profile
      无 nickname → user_name 置空
    - 礼物 type=gift_dock_and_effect（"gift" 不存在）：send_user_info.nick_name
      （下划线命名）/ base_gift_info.name / gift_action_info.count（本次）；
      gift_comment/gift_settle 为同一次送礼的重复视图（时序实证）——
      跳过防重复计数
    - share → SOCIAL(action=share)；light 为进场来源路径（语义待定，不映射）
    """
    cd_type = cd.get("type", "")
    profile = cd.get("profile") or {}
    user_name = str(profile.get("nickname", ""))
    user_id = str(profile.get("user_id", ""))
    base = {"category": "business", "seq": seq, "timestamp": ts}

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
        return {**base, "type": "LIKE",
                "payload": {"type": "LIKE", "user_name": "", "count": count}}
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
        return {**base, "type": "GIFT",
                "payload": {"type": "GIFT",
                            "user_name": str(send.get("nick_name", "")),
                            "user_id": str(send.get("id", "")),
                            "gift_name": gift_name, "gift_count": count}}
    if cd_type == "follow_emcee":
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


class XiaohongshuEngine(ControlledPageEngine):
    """小红书直播弹幕引擎（受控页面 WS 帧拦截，独立平台）"""

    platform = "xiaohongshu"
    profile_name = "xhs_profile"
    protocol_version = "xiaohongshu-1"

    def __init__(self, state_store=None, cookie_dir: str = "./cookie",
                 raw_hook=None):
        super().__init__(state_store=state_store, cookie_dir=cookie_dir)
        self._raw_hook = raw_hook  # 诊断钩子：每个解析出的 customData（含未映射）回调

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

        async with async_playwright() as pw:
            context = await self._launch(pw, headless=True)
            try:
                page = context.pages[0] if context.pages else await context.new_page()
                self._wire_ws_intercept(room_id, page)

                try:
                    await page.goto(goto_url,
                                    timeout=30000, wait_until="domcontentloaded")
                except Exception as e:  # noqa: BLE001
                    logger.debug(f"[xhs] room {room_id} goto warning: {e}")

                logger.info(f"[xhs] room {room_id} live page ready")
                self._set_status(self.status.__class__.RUNNING)

                # 有界会话 + 业务帧静默检测（t==4 帧含 refresh 心跳，正常持续流动）
                while time.monotonic() < deadline and not self._stopped(room_id):
                    await asyncio.sleep(2)
                    if self._frame_silent(room_id, SILENCE_TIMEOUT):
                        raise XiaohongshuParseError(
                            "xiaohongshu.session.silent: 90s 无业务帧"
                            "（可能未开播/已下播/风控——确认直播中）")
            finally:
                await context.close()
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
            mapped = map_custom_data(cd, self.next_seq(room_id), int(time.time()))
            if mapped:
                await self._emit_message(self._envelope(room_id, mapped))
