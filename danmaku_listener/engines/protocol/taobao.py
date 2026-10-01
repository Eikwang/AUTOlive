"""淘宝直播弹幕 Web 协议引擎（mtop 双通道，TaobaoLiveWebFetcher 参考实现对照）

协议（2026-09-29 校准，参考实现 537 行逐段对照）：
- 凭证：Playwright 打开 tbzb.taobao.com/live?liveId={id}（游客可用）→
  拦截 iliad 请求拿 topic → 取 _m_h5_tk/_m_h5_tk_enc cookies
- 主通道：mtop.taobao.powermsg.h5.msg.pullnativemsg（appKey 12574478，10s 轮询）
  响应 data.timestampList[].data = base64(protobuf+JSON 混合) → 扫描提取 JSON 对象，
  按字段特征分发：viewCountFormat=统计 / nick+flowSourceText=进场 / value.dig=点赞 /
  subType==10001 聊天 / subType==10002 礼物 / status==3 下播
- 评论通道：mtop.taobao.iliad.comment.query.latest（appKey 34675810，服务端 delay 轮询）
  data.comments[]（publisherNick/publisherId/content）
- 30s 无消息 → 凭证过期/风控 → 整轮重建（重取凭证，契约 O）

映射：ChatMessage→DANMU、Member→ENTER_ROOM、Gift→GIFT、Like→LIKE、
统计→ROOM_STATS、Control(status==3)→LIVE_STATUS_CHANGE(live=false)。
"""

import asyncio
import json
import re
import time

import requests
from typing import Any, Dict, Optional

import websockets  # noqa: F401  保留与其它协议引擎一致的导入形态
from loguru import logger

from danmaku_listener.contract.models import GapReason
from danmaku_listener.engines.base import BaseEngine
from danmaku_listener.engines.protocol.mtop import (
    MtopClient,
    MtopError,
    MtopCredential,
    extract_live_id,
    parse_base64_mixed_message,
)

PROTOCOL_VERSION = "taobao-1"

LIVE_URL = "https://tbzb.taobao.com/live?liveId={live_id}"
POWERMSG_API = "mtop.taobao.powermsg.h5.msg.pullnativemsg"
POWERMSG_VERSION = "1.0"
POWERMSG_APP_KEY = "12574478"
ILIAD_API = "mtop.taobao.iliad.comment.query.latest"
ILIAD_VERSION = "1.0"
ILIAD_APP_KEY = "34675810"

POLL_INTERVAL_POWERMSG = 10.0
NO_MESSAGE_TIMEOUT = 30.0

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36")


class TaobaoWebProtocolEngine(BaseEngine):
    """淘宝直播 mtop 双通道引擎（每平台一实例、每房间独立任务）"""

    platform = "taobao"
    PROTOCOL_VERSION = PROTOCOL_VERSION  # 类属性（子类覆写自己的版本号）

    #: mtop 网关域名与直播间 URL 模板（1688 子类覆写——同协议异域名）
    MTOP_DOMAIN = "taobao.com"
    LIVE_URL_TEMPLATE = "https://tbzb.taobao.com/live?liveId={live_id}"
    #: powermsg 轮询参数（1688 页面实测：首拉 offset=0、pagesize 20、sdk h5_3.3.3）
    POWERMSG_SDK_VERSION = "h5_3.4.2"
    POWERMSG_PAGESIZE = 10
    POWERMSG_INIT_OFFSET_ZERO = False  # 淘宝=当前时间戳起拉增量；1688=0 起拉历史
    #: 页面来源（mtop 网关校验 Referer/Origin 域——1688 引擎覆写）
    PAGE_ORIGIN = "https://tbzb.taobao.com"
    #: 订阅开关与 H5 appKey（1688 页面实测：pull 前须 subscribe，否则空返回）
    POWERMSG_SUBSCRIBE = False
    POWERMSG_H5_APPKEY = "H5_1WhTdTy0y67M01"
    #: 凭证锚点：页面请求中携带 topic 的接口（子类可加 1688 subscribe）
    TOPIC_ANCHORS = (ILIAD_API, "powermsg")

    def __init__(self, state_store=None, domain: Optional[str] = None,
                 cookie_dir: str = "./cookie"):
        super().__init__(state_store=state_store)
        self._domain = domain or self.MTOP_DOMAIN
        self._cookie_dir = cookie_dir  # profile 持久化目录（wxlivespy 同款 userDataDir）
        self._room_tasks: Dict[str, asyncio.Task] = {}
        self._stop_flags: Dict[str, bool] = {}
        # 未映射消息聚合计数（防 debug 刷屏——60s 汇总一次）
        self._unmapped_stats: Dict[str, dict] = {}
        self._unmapped_last_flush = time.monotonic()

    @property
    def engine_id(self) -> str:
        return "webws:taobao"

    # ---- 公开契约 ----

    async def start(self, room_id: str) -> None:
        if room_id in self._room_tasks and not self._room_tasks[room_id].done():
            return
        self._stop_flags[room_id] = False
        self._room_tasks[room_id] = asyncio.create_task(
            self._run_room(room_id), name=f"taobao-room-{room_id}")

    async def stop(self, room_id: str) -> None:
        self._stop_flags[room_id] = True
        task = self._room_tasks.pop(room_id, None)
        if task:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    async def restart(self, room_id: str) -> None:
        """仅重启该房间任务（契约 I）"""
        await self.stop(room_id)
        await self.start(room_id)

    # ---- 凭证阶段（Playwright）----

    def _profile_dir(self) -> str:
        """浏览器 profile 持久化目录（wxlivespy 同款 userDataDir 模式）——
        滑块验证通过一次后设备指纹/cookie 持久保存，后续启动免验证"""
        import os

        d = f"{self._cookie_dir}/taobao_profile"
        os.makedirs(d, exist_ok=True)
        return d

    async def _fetch_credentials(self, room_id: str) -> tuple:
        """打开直播间页 → topic + cookies

        风控处理（2026-09-29 实测校准）：淘宝 noCaptcha 检测自动化指纹
        （navigator.webdriver 等）——自动化浏览器里**人工拖滑块也会失败**
        （error rTVSNv）。对策：launch_persistent_context（profile 持久化）
        + 反检测注入 + AutomationControlled 禁用；首次验证通过后 profile
        保存，后续启动免验证。等待事件驱动（headless 45s / 可见 240s）。
        """
        from playwright.async_api import async_playwright

        live_id = self._extract_live_id(room_id)
        live_url = self.LIVE_URL_TEMPLATE.format(live_id=live_id)

        async def attempt(wait_limit: float) -> Optional[Dict[str, Any]]:
            """单轮 headless 凭证提取：page 级请求捕获（context 级实测拿不到）"""
            state: Dict[str, Any] = {"topic": None, "cookies": {}}
            async with async_playwright() as pw:
                context = await pw.chromium.launch_persistent_context(
                    self._profile_dir(),
                    headless=True,
                    user_agent=UA,
                    viewport={"width": 1280, "height": 800},
                    args=["--disable-blink-features=AutomationControlled",
                          "--disable-setuid-sandbox",
                          "--hide-crash-restore-bubble"],
                )
                try:
                    await context.add_init_script(
                        "Object.defineProperty(navigator, 'webdriver', "
                        "{get: () => undefined});")
                    page = context.pages[0] if context.pages else await context.new_page()

                    def on_request(request) -> None:
                        if state["topic"]:
                            return
                        u = request.url
                        if any(a in u for a in self.TOPIC_ANCHORS):
                            m = re.search(r"[?&]data=([^&]+)", u)
                            raw = m.group(1) if m else request.post_data
                            if raw:
                                try:
                                    from urllib.parse import unquote
                                    data = json.loads(unquote(raw))
                                    if data.get("topic"):
                                        state["topic"] = data["topic"]
                                except json.JSONDecodeError:
                                    pass

                    page.on("request", on_request)
                    try:
                        await page.goto(live_url, timeout=30000)
                    except Exception as e:  # noqa: BLE001
                        logger.debug(f"[taobao] room {room_id} page goto warning: {e}")

                    deadline = time.monotonic() + wait_limit
                    while state["topic"] is None and time.monotonic() < deadline:
                        await asyncio.sleep(1)
                    # 全量 cookie（1688 网关要求 isg 等安全 cookie 齐全；
                    # 淘宝网关仅 _m_h5_tk 必需——全量兼容两者）
                    state["cookies"] = {c["name"]: c["value"]
                                        for c in await context.cookies()
                                        if c.get("value")}
                finally:
                    await context.close()
            return state

        state = await attempt(wait_limit=45.0)
        if not state["topic"]:
            # 二次尝试（页面偶发加载慢/轮询冷启动）
            state = await attempt(wait_limit=45.0)

        if not state["topic"]:
            raise RuntimeError(
                "taobao.credential.topic_failed: 未能获取 topic（未开播/风控/页面加载慢——"
                "持续失败可稍后重试）")
        if not state["cookies"].get("_m_h5_tk"):
            raise RuntimeError("taobao.credential.token_failed: 未获取 _m_h5_tk（风控升级特征）")
        logger.info(f"[taobao] room {room_id} credentials ready (topic={state['topic'][:24]}...)")
        return state["topic"], MtopCredential(state["cookies"])

    @staticmethod
    async def _try_auto_slide(page) -> bool:
        """自动尝试拖动风控滑块（拟人缓动轨迹；noCaptcha 常在 iframe 内）"""
        try:
            slider = None
            for frame in page.frames:
                slider = await frame.query_selector(
                    '.nc_iconfont.btn_slide, .btn_slide, [data-role="slider"], '
                    '#nc_1_n1z, .nc-lang-cnt ~ * .btn_slide')
                if slider:
                    target_page = page
                    break
            if not slider:
                logger.debug("[taobao] slider element not found")
                return False
            box = await slider.bounding_box()
            if not box:
                return False
            import random as _rand

            start_x = box["x"] + box["width"] / 2
            start_y = box["y"] + box["height"] / 2
            track = max(box["width"] * 4.5, 260)
            await page.mouse.move(start_x, start_y)
            await page.mouse.down()
            steps = 45
            for i in range(steps):
                t = i / steps
                dx = track * (1 - (1 - t) ** 2) + _rand.uniform(-1.5, 1.5)
                await page.mouse.move(start_x + dx, start_y + _rand.uniform(-1.2, 1.2))
                await asyncio.sleep(_rand.uniform(0.008, 0.028))
            await page.mouse.up()
            logger.info("[taobao] auto slide attempted")
            return True
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[taobao] auto slide error: {e}")
            return False

    async def _emit_system_status(self, room_id: str, detail: str) -> None:
        import time as _time

        from danmaku_listener.contract import Category, SystemType
        from danmaku_listener.contract.models import Envelope, UnifiedMessage

        msg = UnifiedMessage(
            envelope=Envelope(
                category=Category.SYSTEM, type=SystemType.ENGINE_STATUS.value,
                platform=self.platform, room_id=room_id, seq=self.next_seq(room_id),
                timestamp=int(_time.time()), engine=self.engine_id,
            ),
            payload={"type": "ENGINE_STATUS", "engine": self.engine_id, "detail": detail},
        )
        await self._emit_message(msg.to_wire())

    # ---- 房间任务主循环 ----

    async def _run_room(self, room_id: str) -> None:
        live_id = self._extract_live_id(room_id)
        logger.info(f"[taobao] room {room_id} connecting (live_id={live_id})")
        backoff = 15.0
        while not self._stop_flags.get(room_id):
            try:
                topic, cred = await self._fetch_credentials(room_id)

                def _make_client() -> MtopClient:
                    # 每通道独立 Session（两轮询并发跨线程共享 Session 会竞争失败）
                    s = requests.Session()
                    s.headers.update({"User-Agent": UA})
                    for k, v in cred.cookies.items():
                        s.cookies.set(k, v)
                    return MtopClient(self._domain, UA, s)

                last_msg_box = {"t": time.monotonic()}
                poll_task = asyncio.create_task(
                    self._poll_powermsg(room_id, _make_client(), cred, topic, live_id,
                                        last_msg_box))
                comment_task = asyncio.create_task(
                    self._poll_comments(room_id, _make_client(), cred, topic))
                self._set_status(self.status.__class__.RUNNING)
                try:
                    while not self._stop_flags.get(room_id):
                        await asyncio.sleep(2)
                        # 30s 无消息 → 凭证过期/风控 → 整轮重建（Eng：主通道断流检测）
                        if time.monotonic() - last_msg_box["t"] > NO_MESSAGE_TIMEOUT:
                            raise RuntimeError(
                                "taobao.no_message_timeout: 30s 无消息（凭证过期/风控）")
                finally:
                    poll_task.cancel()
                    comment_task.cancel()
                    for tk in (poll_task, comment_task):
                        try:
                            await tk
                        except (asyncio.CancelledError, Exception):
                            pass
                backoff = 15.0
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(f"[taobao] room {room_id} error: {type(e).__name__}: {str(e)[:90]}")
                self._set_status(self.status.__class__.ERROR)
                self.mark_gap_start(room_id)
                gap = self.build_gap_message(room_id, GapReason.NETWORK)
                if gap:
                    await self._emit_system(gap)
                if self._stop_flags.get(room_id):
                    return
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 900.0)

    # ---- 主通道：powermsg 长轮询 ----

    async def _poll_powermsg(self, room_id: str, client: MtopClient, cred: MtopCredential,
                             topic: str, live_id: str, last_msg_box: dict) -> None:
        offset = "0" if self.POWERMSG_INIT_OFFSET_ZERO else str(int(time.time() * 1000))
        loop = asyncio.get_running_loop()
        headers = {
            "x-biz-type": "powermsg",
            "x-biz-info": "namespace=1",
            "referer": self.PAGE_ORIGIN + "/",
            "origin": self.PAGE_ORIGIN,
        }
        # 订阅前置（1688 页面实测：不 subscribe 则 pull 的 timestampList 恒空）
        if self.POWERMSG_SUBSCRIBE:
            now = int(time.time() * 1000)
            sub_data = {
                "namespace": 1, "topic": topic, "role": 3,
                "sdkVersion": self.POWERMSG_SDK_VERSION, "tag": "",
                "timestamp": now, "ext": now,
                "appKey": self.POWERMSG_H5_APPKEY,
                "utdId": f"{random.randint(10**9, 10**10)}_{random.randint(100, 999)}",
                "token": "",
            }
            try:
                sub_result = await loop.run_in_executor(
                    None, client.get, "mtop.taobao.powermsg.h5.msg.subscribe",
                    "1.0", POWERMSG_APP_KEY, sub_data, cred.m_h5_tk, headers)
                logger.info(f"[taobao] room {room_id} powermsg subscribe: "
                            f"{str(sub_result.get('ret'))[:60]}")
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[taobao] room {room_id} subscribe error: {e}")
        while not self._stop_flags.get(room_id):
            try:
                data = {
                    "topic": topic, "offset": offset,
                    "pagesize": self.POWERMSG_PAGESIZE,
                    "tag": "", "bizcode": 1,
                    "sdkversion": self.POWERMSG_SDK_VERSION, "role": 3,
                }
                result = await loop.run_in_executor(
                    None, client.get, POWERMSG_API, POWERMSG_VERSION,
                    POWERMSG_APP_KEY, data, cred.m_h5_tk, headers)
                timestamps = (result.get("data") or {}).get("timestampList") or []
                if timestamps:
                    offset = timestamps[-1].get("offset", offset)
                    for td in timestamps:
                        await self._parse_powermsg_item(room_id, td, ts=int(time.time()))
                    last_msg_box["t"] = time.monotonic()
                await asyncio.sleep(POLL_INTERVAL_POWERMSG)
            except MtopError as e:
                logger.warning(f"[taobao] room {room_id} powermsg mtop error: {e}")
                await asyncio.sleep(5)
            except Exception as e:  # noqa: BLE001  task 不得静默死亡
                logger.warning(f"[taobao] room {room_id} powermsg error: "
                               f"{type(e).__name__}: {str(e)[:80]}")
                await asyncio.sleep(5)
            except asyncio.CancelledError:
                raise

    async def _parse_powermsg_item(self, room_id: str, td: Dict[str, Any], ts: int) -> None:
        data_b64 = td.get("data", "")
        if not data_b64:
            return
        try:
            json_objects, _raw = parse_base64_mixed_message(data_b64)
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[taobao] room {room_id} b64 parse error: {e}")
            return
        for obj in json_objects:
            if not isinstance(obj, dict):
                continue
            mapped = self._map_powermsg(room_id, obj, self.next_seq(room_id), ts)
            if mapped:
                await self._emit_message(self._envelope(room_id, mapped))
            else:
                # 未映射消息聚合计数（2026-10-01 用户实测：运营类消息高频，
                # 逐条 debug 刷屏——60s 汇总一次）
                keys_sig = tuple(sorted(obj.keys())[:6])
                stat = self._unmapped_stats.setdefault(room_id, {})
                stat[keys_sig] = stat.get(keys_sig, 0) + 1
                now = time.monotonic()
                if now - self._unmapped_last_flush > 60.0:
                    self._unmapped_last_flush = now
                    for rid, kv in self._unmapped_stats.items():
                        total = sum(kv.values())
                        if total:
                            top = sorted(kv.items(), key=lambda x: -x[1])[:3]
                            logger.debug(
                                f"[taobao] room {rid} unmapped {total} msgs "
                                f"in 60s, top keys: {[list(k) for k, _ in top]}")
                    self._unmapped_stats = {rid: {} for rid in self._unmapped_stats}

    # ---- 评论通道：iliad 轮询 ----

    async def _poll_comments(self, room_id: str, client: MtopClient, cred: MtopCredential,
                             topic: str) -> None:
        pagination_ctx = None
        loop = asyncio.get_running_loop()
        while not self._stop_flags.get(room_id):
            try:
                payload: Dict[str, Any] = {"topic": topic, "limit": 20, "tab": 2,
                                           "order": "asc"}
                if pagination_ctx:
                    payload["paginationContext"] = pagination_ctx
                result = await loop.run_in_executor(
                    None, client.get, ILIAD_API, ILIAD_VERSION,
                    ILIAD_APP_KEY, payload, cred.m_h5_tk, None)
                data = result.get("data") or {}
                pagination_ctx = data.get("paginationContext") or pagination_ctx
                delay_ms = data.get("delay", 6000)
                for c in data.get("comments") or []:
                    mapped = self._map_comment(room_id, c, self.next_seq(room_id),
                                               int(time.time()))
                    if mapped:
                        await self._emit_message(self._envelope(room_id, mapped))
                await asyncio.sleep(max(int(delay_ms), 2000) / 1000.0)
            except MtopError as e:
                logger.warning(f"[taobao] room {room_id} iliad mtop error: {e}")
                await asyncio.sleep(5)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001  网络异常等——task 不得静默死亡
                logger.warning(f"[taobao] room {room_id} iliad error: "
                               f"{type(e).__name__}: {str(e)[:80]}")
                await asyncio.sleep(5)

    @staticmethod
    def _extract_live_id(room_spec: str) -> str:
        return extract_live_id(room_spec)

    def validate_room_id(self, room_id: str) -> None:
        """add_room 预校验：淘宝引擎无法解析 1688 链接（提示用对前缀）"""
        try:
            self._extract_live_id(room_id)
        except ValueError as e:
            raise ValueError(
                f"{e}——若为 1688 直播间请使用 alibaba1688: 前缀添加") from e

    def _envelope(self, room_id: str, mapped: dict) -> dict:
        """契约信封组装（全键——缺键会被 bridge 兜底包装为 ENGINE_STATUS）

        platform/engine 用 self（1688 子类继承后如实标注——用户实测教训：
        静态方法硬编码导致 1688 消息被标成 taobao）
        """
        return {
            "contract_version": "1.0.0",
            "category": mapped["category"],
            "type": mapped["type"],
            "platform": self.platform,
            "room_id": room_id,
            "seq": mapped["seq"],
            "timestamp": mapped["timestamp"],
            "engine": self.engine_id,
            "protocol_version": self.PROTOCOL_VERSION,
            "payload": mapped["payload"],
        }

    # ---- 上游消息 → 契约 v1（显式清单，与 test_taobao_protocol.py 对齐）----

    @staticmethod
    def _map_powermsg(room_id: str, obj: Dict[str, Any], seq: int, ts: int) -> Optional[Dict[str, Any]]:
        # 分发顺序修正（参考实现的字段特征重叠 bug）：subType 聊天/礼物优先判定，
        # 否则 subType==10001 聊天（含 nick）会被进场分支误吞
        sub_type = obj.get("subType")
        if sub_type == 10001:
            content = obj.get("content", obj.get("text", ""))
            if not content:
                return None
            return {"category": "business", "type": "DANMU", "seq": seq, "timestamp": ts,
                    "payload": {"type": "DANMU", "user_name": obj.get("nick", ""),
                                "content": content,
                                "user_id": str(obj.get("userid", obj.get("userId", "")))}}
        if sub_type == 10002:
            gift_name = obj.get("giftName", obj.get("itemName", ""))
            if not gift_name:
                return None
            return {"category": "business", "type": "GIFT", "seq": seq, "timestamp": ts,
                    "payload": {"type": "GIFT", "user_name": obj.get("nick", ""),
                                "gift_name": gift_name,
                                "gift_count": obj.get("count", obj.get("num", 1))}}
        # 统计：viewCountFormat/pageViewCount/totalCount 单键（1688 形态）
        if "viewCountFormat" in obj or "pageViewCount" in obj or "totalCount" in obj:
            current = obj.get("onlineCount", obj.get("current_viewers", 0))
            total = obj.get("totalCount", obj.get("total_viewers", 0))
            return {"category": "business", "type": "ROOM_STATS", "seq": seq,
                    "timestamp": ts,
                    "payload": {"type": "ROOM_STATS", "viewer_count": current,
                                "total_view_count": total}}
        # 进场：nick + flowSourceText（subType 已在上方处理）
        if "nick" in obj and "flowSourceText" in obj:
            identify = obj.get("identify") or {}
            fan_level = identify.get("fanLevel", 0)
            if isinstance(fan_level, str) and fan_level.isdigit():
                fan_level = int(fan_level)
            elif not isinstance(fan_level, int):
                fan_level = 0
            return {"category": "business", "type": "ENTER_ROOM", "seq": seq,
                    "timestamp": ts,
                    "payload": {"type": "ENTER_ROOM",
                                "user_name": obj.get("nick", ""),
                                "user_id": str(obj.get("userid", obj.get("userId", ""))),
                                "fan_level": fan_level}}
        # 点赞：value.dig
        if "value" in obj and isinstance(obj.get("value"), dict) and "dig" in obj["value"]:
            return {"category": "business", "type": "LIKE", "seq": seq, "timestamp": ts,
                    "payload": {"type": "LIKE", "count": obj["value"].get("dig", 1)}}
        return None

    @staticmethod
    def _map_comment(room_id: str, c: Dict[str, Any], seq: int, ts: int) -> Optional[Dict[str, Any]]:
        content = c.get("content", "")
        if not content:
            return None
        nick = c.get("publisherNick", "")
        user_id = str(c.get("publisherId", ""))
        return {"category": "business", "type": "DANMU", "seq": seq, "timestamp": ts,
                "payload": {"type": "DANMU", "user_name": nick, "content": content,
                            "user_id": user_id}}
