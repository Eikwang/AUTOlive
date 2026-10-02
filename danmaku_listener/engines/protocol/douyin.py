"""抖音 Web WS 协议直连引擎（计划 T2/T3：barrage-fly SDK 同款原生路线）

技术路线（端到端原型实测 2026-09-28，证据链见 docs/plans/douyin-native-websocket-plan.md）：
- roomInit：游客 HTTP（ttwid → 房间页正则 real_room/user_unique_id/status）
- 签名：quickjs 引擎级单例跑 douyin-webmssdk.js 的 get_sign（仅 loop 线程调用）
- WS：wss://webcast5-ws-web-{lq|lf|hl}.douyin.com/webcast/im/push/v2/ + SDK 全参数
- 帧协议：PushFrame → gzip → Response{messagesList} → needAck 回 ACK
- 心跳：PushFrame{payloadType:"hb"}（0x3a 0x02 'h' 'b'），10s 周期首帧 5s

映射（显式清单，与 test_douyin_protocol.py 逐条对齐）：
- 业务六类：ChatMessage→DANMU、GiftMessage→GIFT（count=totalCount 原样透传）、
  MemberMessage→ENTER_ROOM（action in (0,1) 进房；其它丢弃+debug）、
  LikeMessage→LIKE、SocialMessage→SOCIAL（action=1 关注/3 分享）、
  RoomStatsMessage→ROOM_STATS（仅 displayType==0 total 类；其它丢弃+debug）
- 系统类：ControlMessage(status==3)→ROOM_STATUS 下播；签名失败/WS 拒绝→ROUTE_FAILED
- 未映射 cmd：丢弃 + debug 日志，不产生 GAP、不消耗 seq

资产 provenance：douyin-webmssdk.js 提取自 barrage-fly SDK 1.5.8 内嵌 MIT 版
（by hua0512）；douyin.proto 提取自 HaoDong108/DouyinBarrageGrab message.proto。
签名失效 → 自检失败报「签名资产过期」（ROUTE_FAILED），刷新流程=重新提取上游 release。
"""

import asyncio
import gzip
import hashlib
import random
import re
import time
from typing import Any, Dict, Optional

import requests
import websockets
from loguru import logger

from danmaku_listener.contract.models import GapReason
from danmaku_listener.engines.protocol.douyin_gifts import lookup_gift
from danmaku_listener.engines.base import BaseEngine
from danmaku_listener.engines.protocol.douyin_assets import douyin_pb2 as dy_pb2

PROTOCOL_VERSION = "douyin-2"

WEB_SOCKET_URIS = [
    "wss://webcast5-ws-web-lq.douyin.com/webcast/im/push/v2/",
    "wss://webcast5-ws-web-lf.douyin.com/webcast/im/push/v2/",
    "wss://webcast5-ws-web-hl.douyin.com/webcast/im/push/v2/",
]
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
HEARTBEAT_FRAME = bytes([58, 2, 104, 98])  # PushFrame{payloadType:"hb"}
HEARTBEAT_INTERVAL = 10.0
HEARTBEAT_INITIAL_DELAY = 5.0

_JS_PATH = "danmaku_listener/engines/protocol/douyin_assets/douyin-webmssdk.js"

_HTTP_HEADERS = {
    "User-Agent": UA,
    "Referer": "https://live.douyin.com/",
}

#: 启动签名自检样本（Eng：固定样本确定性输出，失败报「签名资产过期」）
_SIGN_SELFTEST_SAMPLE = ("live_id=1,aid=6383,version_code=180800,"
                         "webcast_sdk_version=1.0.14-beta.0,room_id=1,sub_room_id=,"
                         "sub_channel_id=,did_rule=3,user_unique_id=1,"
                         "device_platform=web,device_type=,ac=,identity=audience")


class DouyinRoomInitError(ValueError):
    """roomInit 失败（三段式错误的来源；reason_code 编码在 message 内）"""


class DouyinSigner:
    """quickjs 签名器（引擎级单例；线程模型：仅事件循环线程同步调用）

    quickjs 绑定为线程绑定语义——同一 Context 只能从创建线程调用；
    本引擎 asyncio 单 loop 模型下所有房间任务共享 loop 线程，同步调用天然安全。
    任何 run_in_executor/to_thread 包裹 get_sign 均为违规（Eng H1）。
    """

    def __init__(self, user_agent: str = UA):
        import quickjs

        with open(_JS_PATH, encoding="utf-8") as fh:
            js = fh.read()
        env = (' document = {};\nwindow = {};\nnavigator = {\n'
               f'userAgent: "{user_agent}"\n}};\n')
        t0 = time.perf_counter()
        self._ctx = quickjs.Context()
        self._ctx.eval(env + js)
        self._get_sign = self._ctx.get("get_sign")
        logger.info(f"[douyin] webmssdk eval {time.perf_counter() - t0:.3f}s")
        # 启动签名自检：固定样本确定性非空输出（失败=资产过期）
        sample = self._get_sign(hashlib.md5(_SIGN_SELFTEST_SAMPLE.encode()).hexdigest())
        if not sample or not isinstance(sample, str):
            raise RuntimeError(
                "签名资产过期（douyin.signature.selftest_failed）——webmssdk.js 需从 "
                "barrage-fly 上游 release 重新提取（provenance 见引擎 docstring）")

    def sign(self, room_id: str, user_unique_id: str) -> str:
        param = ("live_id=1,aid=6383,version_code=180800,"
                 "webcast_sdk_version=1.0.14-beta.0,"
                 f"room_id={room_id},sub_room_id=,sub_channel_id=,did_rule=3,"
                 f"user_unique_id={user_unique_id},device_platform=web,device_type=,"
                 "ac=,identity=audience")
        return self._get_sign(hashlib.md5(param.encode()).hexdigest())


class DouyinRoomInit:
    """roomInit（游客 HTTP；T2）"""

    def __init__(self, user_agent: str = UA):
        self._session = requests.Session()
        self._session.headers.update(_HTTP_HEADERS)

    def fetch(self, rid: str) -> Dict[str, Any]:
        """返回 {ttwid, ms_token, real_room, uid}；失败抛 DouyinRoomInitError"""
        try:
            self._session.get("https://live.douyin.com/", timeout=10)
        except Exception as e:  # noqa: BLE001
            raise DouyinRoomInitError(
                f"douyin.room_init.ttwid_failed: {type(e).__name__}: {str(e)[:80]}") from e
        ttwid = self._session.cookies.get("ttwid", "")
        if not ttwid:
            raise DouyinRoomInitError("douyin.room_init.ttwid_failed: 响应无 ttwid（风控升级特征）")
        ms_token = "".join(random.choices("abcdefghijklmnopqrstuvwxyz0123456789=_", k=116))
        try:
            r = self._session.get(
                f"https://live.douyin.com/{rid}", timeout=10,
                cookies={"ttwid": ttwid, "msToken": ms_token})
            body = r.text
        except Exception as e:  # noqa: BLE001
            raise DouyinRoomInitError(
                f"douyin.room_init.page_failed: {type(e).__name__}: {str(e)[:80]}") from e
        m_uid = re.search(r'\\"user_unique_id\\":\\"(\d+)\\"', body)
        m_room = re.search(r'\\"roomId\\":\\"(\d+)\\"', body)
        m_status = re.search(r'\\"status_str\\":\\"(\d+)\\"', body)
        if not m_room or not m_uid:
            raise DouyinRoomInitError(
                f"douyin.room_init.parse_failed: 房间页缺 roomId/user_unique_id（web_rid={rid}）")
        status = m_status.group(1) if m_status else "0"
        if status != "2":
            raise DouyinRoomInitError(
                f"douyin.room_init.not_live: 房间未开播（status={status}）")
        return {
            "ttwid": ttwid,
            "ms_token": ms_token,
            "real_room": m_room.group(1),
            "uid": m_uid.group(1),
        }


class DouyinWebProtocolEngine(BaseEngine):
    """抖音 Web WS 协议直连引擎（每平台一实例、每房间独立任务）"""

    platform = "douyin"

    def __init__(self, state_store=None, signer: Optional[DouyinSigner] = None,
                 raw_hook=None):
        super().__init__(state_store=state_store)
        self._signer = signer  # 引擎级单例；默认懒创建（仅 loop 线程）
        self._raw_hook = raw_hook  # 诊断钩子：messageList 原始 method/payload
        self._method_stats: Dict[str, dict] = {}  # 未映射 method 聚合（心跳汇总）
        self._method_stats_seen: Dict[str, dict] = {}  # 全 method 首见记录
        self._room_init = DouyinRoomInit()
        self._room_tasks: Dict[str, asyncio.Task] = {}
        self._room_ws: Dict[str, Any] = {}
        self._heartbeats: Dict[str, asyncio.Task] = {}
        self._stop_flags: Dict[str, bool] = {}

    @property
    def engine_id(self) -> str:
        return "webws:douyin"

    def _get_signer(self) -> DouyinSigner:
        if self._signer is None:
            try:
                self._signer = DouyinSigner()
            except Exception as e:  # noqa: BLE001
                logger.error(f"[douyin] 签名自检失败: {e}")
                raise
        return self._signer

    # ---- 公开契约 ----

    async def start(self, room_id: str) -> None:
        if room_id in self._room_tasks and not self._room_tasks[room_id].done():
            return
        self._stop_flags[room_id] = False
        # 多房间启动错峰（CEO 2.4）：随机 1-5s 抖动，避免并发连发游客抓取
        jitter = random.uniform(1.0, 5.0)
        task = asyncio.create_task(self._run_room(room_id, jitter),
                                   name=f"douyin-room-{room_id}")
        self._room_tasks[room_id] = task

    async def stop(self, room_id: str) -> None:
        self._stop_flags[room_id] = True
        task = self._room_tasks.pop(room_id, None)
        hb = self._heartbeats.pop(room_id, None)
        ws = self._room_ws.pop(room_id, None)
        if hb:
            hb.cancel()
        if ws:
            await ws.close()
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

    # ---- 房间任务主循环 ----

    async def _run_room(self, room_id: str, jitter: float = 0.0) -> None:
        logger.info(f"[douyin] room {room_id} connecting (web ws)")
        if jitter:
            await asyncio.sleep(jitter)
        backoff = 15.0
        while not self._stop_flags.get(room_id):
            try:
                ctx = await asyncio.get_running_loop().run_in_executor(
                    None, self._room_init.fetch, room_id)
                # 签名在 loop 线程同步执行（Eng H1 线程模型）
                sig = self._get_signer().sign(ctx["real_room"], ctx["uid"])
                ws_url = self._build_ws_url(ctx, sig)
                await self._serve_room(room_id, ws_url, ctx)
                backoff = 15.0
            except asyncio.CancelledError:
                raise
            except DouyinRoomInitError as e:
                logger.warning(f"[douyin] room {room_id} roomInit failed: {e}")
                await self._emit_route_failed(room_id, str(e).split(":")[0],
                                              "房间未开播/风控升级——检查房间号与网络；"
                                              "持续失败参考登录态备选路线",
                                              "docs/platforms/douyin/runbook.md")
                await self._backoff_sleep(room_id, backoff)
                backoff = min(backoff * 2, 900.0)
            except Exception as e:
                logger.warning(f"[douyin] room {room_id} error: {type(e).__name__}: {str(e)[:80]}")
                self._set_status(self.status.__class__.ERROR)
                self.mark_gap_start(room_id)
                gap = self.build_gap_message(room_id, GapReason.NETWORK)
                if gap:
                    await self._emit_system(gap)
                if self._stop_flags.get(room_id):
                    return
                await self._backoff_sleep(room_id, backoff)
                backoff = min(backoff * 2, 900.0)

    async def _backoff_sleep(self, room_id: str, seconds: float) -> None:
        try:
            await asyncio.sleep(seconds)
        except asyncio.CancelledError:
            raise

    async def _emit_route_failed(self, room_id: str, reason_code: str,
                                 fix_hint: str, docs_anchor: str) -> None:
        import time as _time

        from danmaku_listener.contract import Category, SystemType
        from danmaku_listener.contract.models import (
            Envelope,
            FailureInfo,
            RouteFailedPayload,
            UnifiedMessage,
        )

        msg = UnifiedMessage(
            envelope=Envelope(
                category=Category.SYSTEM, type=SystemType.ROUTE_FAILED.value,
                platform=self.platform, room_id=room_id,
                seq=self.next_seq(room_id), timestamp=int(_time.time()),
                engine=self.engine_id,
            ),
            payload=RouteFailedPayload(
                failure=FailureInfo(reason_code=reason_code, fix_hint=fix_hint,
                                    docs_anchor=docs_anchor)),
        )
        await self._emit_message(msg.to_wire())

    # ---- WS 参数与连接 ----

    @staticmethod
    def _build_ws_url(ctx: Dict[str, Any], sig: str) -> str:
        from urllib.parse import urlencode

        now = int(time.time() * 1000)
        qp = [
            ("app_name", "douyin_web"), ("version_code", "180800"),
            ("webcast_sdk_version", "1.0.14-beta.0"),
            ("update_version_code", "1.0.14-beta.0"),
            ("compress", "gzip"), ("device_platform", "web"),
            ("cookie_enabled", "true"), ("screen_width", "1280"),
            ("screen_height", "800"), ("browser_language", "zh-CN"),
            ("browser_platform", "Win32"), ("browser_name", "Mozilla"),
            ("browser_version", UA.replace("Mozilla/", "")),
            ("browser_online", "true"), ("tz_name", "Asia/Shanghai"),
            ("cursor", f"t-{now}_r-1_d-1_u-1_fh-743192"
                       f"{random.randint(10**12, 10**13 - 1)}"),
            ("internal_ext",
             "internal_src:dim|wss_push_room_id:" + ctx["real_room"] +
             "|wss_push_did:" + ctx["uid"] +
             "|first_req_ms:" + str(now) + "|fetch_time:" + str(now) +
             "|seq:1|wss_info:0-" + str(now) + "-0-0|wrds_v:743192" +
             "".join(random.choices("0123456789", k=13))),
            ("host", "https://live.douyin.com"), ("aid", "6383"),
            ("live_id", "1"), ("did_rule", "3"), ("endpoint", "live_pc"),
            ("support_wrds", "1"), ("user_unique_id", ctx["uid"]),
            ("im_path", "/webcast/im/fetch/"), ("identity", "audience"),
            ("need_persist_msg_count", "15"), ("insert_task_id", ""),
            ("live_reason", ""), ("room_id", ctx["real_room"]),
            ("heartbeatDuration ", "0"), ("signature", sig),
            ("web_rid", ctx.get("web_rid", "")), ("msToken", ctx["ms_token"]),
        ]
        return WEB_SOCKET_URIS[0] + "?" + urlencode(qp)

    async def _serve_room(self, room_id: str, ws_url: str, ctx: Dict[str, Any]) -> None:
        headers = {
            "User-Agent": UA,
            "Cookie": f"ttwid={ctx['ttwid']}; msToken={ctx['ms_token']}",
            "Origin": "https://live.douyin.com",
        }
        try:
            ws_cm = websockets.connect(ws_url, additional_headers=headers,
                                       ping_interval=None)
            ws = await ws_cm.__aenter__()
        except Exception as e:
            raise RuntimeError(f"ws_rejected: {type(e).__name__}: {str(e)[:80]}") from e
        self._room_ws[room_id] = ws
        logger.info(f"[douyin] room {room_id} ws connected (real_room={ctx['real_room']})")
        self._set_status(self.status.__class__.RUNNING)
        hb_task = asyncio.create_task(self._heartbeat(room_id, ws))
        self._heartbeats[room_id] = hb_task
        try:
            await self._read_loop(room_id, ws)
        finally:
            hb_task.cancel()
            self._heartbeats.pop(room_id, None)
            self._room_ws.pop(room_id, None)
            await ws.close()

    async def _heartbeat(self, room_id: str, ws) -> None:
        """PushFrame{payloadType:"hb"}：首帧 5s、周期 10s（SDK 默认）"""
        try:
            await asyncio.sleep(HEARTBEAT_INITIAL_DELAY)
            while True:
                await ws.send(HEARTBEAT_FRAME)
                await asyncio.sleep(HEARTBEAT_INTERVAL)
        except asyncio.CancelledError:
            pass

    # ---- 读循环与分发 ----

    async def _read_loop(self, room_id: str, ws) -> None:
        while True:
            raw = await ws.recv()
            data = raw if isinstance(raw, bytes) else raw.encode("utf-8")
            ts = int(time.time())
            try:
                frame = dy_pb2.PushFrame()
                frame.ParseFromString(data)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[douyin] room {room_id} frame error: {e}")
                continue
            if frame.payloadType == "hb":
                continue  # 心跳应答仅保活
            payload = frame.payload
            for h in frame.headersList:
                if h.key == "compress_type" and h.value == "gzip":
                    try:
                        payload = gzip.decompress(frame.payload)
                    except Exception as e:  # noqa: BLE001
                        logger.warning(f"[douyin] room {room_id} gzip error: {e}")
                        payload = None
                    break
            if not payload:
                continue
            resp = dy_pb2.Response()
            try:
                resp.ParseFromString(payload)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[douyin] room {room_id} response error: {e}")
                continue
            if resp.needAck:
                ack = dy_pb2.PushFrame()
                ack.logId = frame.logId
                ack.payloadType = "ack"
                ack.payload = resp.internalExt.encode() if isinstance(resp.internalExt, str) else resp.internalExt
                await ws.send(ack.SerializeToString())
            for msg in resp.messagesList:
                self.mark_received(room_id, ts)
                await self._dispatch(room_id, msg, ts)

    async def _dispatch(self, room_id: str, msg, ts: int) -> None:
        # 诊断：全部 method 首见记录 + raw_hook（礼物通道缺失排查——
        # 2026-10-01 用户实测 Like/Social 有而 Gift 无，需真实 method 样本）
        method = msg.method
        if method not in self._method_stats_seen.setdefault(room_id, {}):
            self._method_stats_seen[room_id][method] = 1
            logger.info(f"[douyin] room {room_id} first-seen method={method}")
        if self._raw_hook is not None:
            try:
                import base64 as _b64
                self._raw_hook({"method": method,
                                "msg_id": msg.msgId or None,
                                "payload_b64": _b64.b64encode(msg.payload).decode()})
            except Exception:  # noqa: BLE001
                pass
        mapped = self._map_message(room_id, msg, self.next_seq(room_id), ts)
        if mapped is None:
            stat = self._method_stats.setdefault(room_id, {})
            stat[method] = stat.get(method, 0) + 1
            if stat[method] in (1, 50, 200):  # 首次/50/200 次提示
                logger.info(f"[douyin] room {room_id} unmapped method={method} "
                            f"count={stat[method]}")
        if mapped:
            mapped["msg_id"] = msg.msgId or None  # Message 层 msgId（去重键）
            await self._emit_message({
                "contract_version": "1.0.0",
                "category": mapped["category"],
                "type": mapped["type"],
                "platform": "douyin",
                "room_id": room_id,
                "seq": mapped["seq"],
                "timestamp": mapped["timestamp"],
                "engine": self.engine_id,
                "protocol_version": PROTOCOL_VERSION,
                "msg_id": mapped.get("msg_id"),
                "payload": mapped["payload"],
            })

    # ---- 上游消息 → 契约 v1（显式清单，与 test_douyin_protocol.py 对齐）----

    @staticmethod
    def _map_message(room_id: str, msg, seq: int,
                     ts: int) -> Optional[Dict[str, Any]]:
        method = msg.method
        if method == "WebcastChatMessage":
            c = dy_pb2.ChatMessage()
            c.ParseFromString(msg.payload)
            content = c.content or ""
            if not content:
                return None
            return {
                "category": "business", "type": "DANMU", "seq": seq, "timestamp": ts,
                "payload": {"type": "DANMU",
                            "user_name": c.user.nickName or "",
                            "content": content,
                            "user_id": str(c.user.id) if c.user.id else None},
            }
        if method == "WebcastGiftMessage":
            g = dy_pb2.GiftMessage()
            g.ParseFromString(msg.payload)
            # 礼物名/价格：协议 gift.name 优先，空则查静态对照表
            # （2026-10-02 用户实测 102 项，钻石计价）
            gift_name = g.gift.name or lookup_gift(g.giftId)[0]
            if not gift_name:
                return None
            unit_price = lookup_gift(g.giftId)[1]
            return {
                "category": "business", "type": "GIFT", "seq": seq, "timestamp": ts,
                "payload": {"type": "GIFT",
                            "user_name": g.user.nickName or "",
                            "user_id": str(g.user.id) if g.user.id else None,
                            "gift_name": gift_name,
                            "gift_id": g.giftId,
                            # count 取 totalCount 原样透传（累计值语义，见契约文档——
                            # groupCount/repeatCount/comboCount 不参与，连击合并 defer）
                            "gift_count": g.totalCount or 1,
                            "gift_value": unit_price * (g.totalCount or 1)},
            }
        if method == "WebcastMemberMessage":
            m = dy_pb2.MemberMessage()
            m.ParseFromString(msg.payload)
            if m.action not in (0, 1):  # 仅进房；其它变体丢弃+debug
                logger.debug(f"[douyin] room {room_id} member action={m.action} dropped")
                return None
            return {
                "category": "business", "type": "ENTER_ROOM", "seq": seq, "timestamp": ts,
                "payload": {"type": "ENTER_ROOM",
                            "user_name": m.user.nickName or "",
                            "user_id": str(m.user.id) if m.user.id else None,
                            "member_count": m.memberCount},
            }
        if method == "WebcastLikeMessage":
            lk = dy_pb2.LikeMessage()
            lk.ParseFromString(msg.payload)
            return {
                "category": "business", "type": "LIKE", "seq": seq, "timestamp": ts,
                "payload": {"type": "LIKE", "count": lk.count, "total": lk.total,
                            "user_name": lk.user.nickName or "",
                            "user_id": str(lk.user.id) if lk.user.id else None},
            }
        if method == "WebcastSocialMessage":
            so = dy_pb2.SocialMessage()
            so.ParseFromString(msg.payload)
            action = {1: "follow", 3: "share"}.get(so.action)
            if not action:
                logger.debug(f"[douyin] room {room_id} social action={so.action} dropped")
                return None
            return {
                "category": "business", "type": "SOCIAL", "seq": seq, "timestamp": ts,
                "payload": {"type": "SOCIAL", "action": action,
                            "user_name": so.user.nickName or "",
                            "user_id": str(so.user.id) if so.user.id else None},
            }
        if method == "WebcastRoomStatsMessage":
            st = dy_pb2.RoomStatsMessage()
            st.ParseFromString(msg.payload)
            if st.displayType != 0:  # 仅 total 类（displayType 语义未实测前保守过滤）
                logger.debug(f"[douyin] room {room_id} stats displayType={st.displayType} dropped")
                return None
            return {
                "category": "business", "type": "ROOM_STATS", "seq": seq, "timestamp": ts,
                "payload": {"type": "ROOM_STATS", "total": st.total,
                            "display_value": st.displayValue,
                            "display_short": st.displayShort},
            }
        if method == "WebcastControlMessage":
            ct = dy_pb2.ControlMessage()
            ct.ParseFromString(msg.payload)
            if ct.status == 3:  # 下播（proto 注释：status = 3 下播）
                import time as _time

                from danmaku_listener.contract import Category

                return {
                    "category": Category.SYSTEM.value, "type": "LIVE_STATUS_CHANGE",
                    "seq": seq, "timestamp": ts,
                    "payload": {"type": "LIVE_STATUS_CHANGE", "live": False},
                }
            logger.debug(f"[douyin] room {room_id} control status={ct.status} dropped")
            return None
        # 未映射 cmd：丢弃 + debug，不耗 seq、不产 GAP
        logger.debug(f"[douyin] room {room_id} unmapped cmd: {method}")
        return None
