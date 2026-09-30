"""B站直播弹幕协议直连引擎（阶段 1）

每房间一条 asyncio 任务 + WS 长连接（进程拓扑见 ADR-001）。
协议编解码见 bilibili_codec.py（纯函数，fixtures 回放测试直接覆盖）。
"""

import asyncio
import json
import os
import time
import uuid
from typing import Any, Callable, Dict, List, Optional

import websockets
from loguru import logger

from danmaku_listener.contract.models import GapReason
from danmaku_listener.engines.base import BaseEngine
from danmaku_listener.engines.protocol import bilibili_codec as codec
from danmaku_listener.managers.reconnect_manager import ReconnectManager

HEARTBEAT_INTERVAL = 30.0
PROTOCOL_VERSION = "bilibili-1"  # 协议版本元数据（调试三项）

#: WS 握手头（对齐 barrage-fly BilibiliLiveChatClient.initConnectionHandler）：
#: 裸 Python UA 的连接会被 B站降级推送（事件流稀疏、弹幕被哑）——实测确认
WS_HANDSHAKE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Origin": "https://live.bilibili.com",
    "Pragma": "no-cache",
}

#: websockets>=14 改名 additional_headers（10-13 为 extra_headers）
_WS_HEADER_KWARG = (
    "additional_headers"
    if tuple(int(x) for x in websockets.__version__.split(".")[:2]) >= (14, 0)
    else "extra_headers"
)


class DanmuInfoFetcher:
    """获取弹幕服务器 token（getDanmuInfo + wbi 签名）

    B站 2023+ 风控：裸请求返回 code=-352。修复流程：
    1. Session 预热访问直播间页面（cookie 种子）
    2. nav 接口拿 wbi img_key/sub_key（游客可访问）
    3. wbi 签名（w_rid+wts）后请求 getDanmuInfo
    注入替代实现便于测试。

    **弹幕限流**（实测确认）：游客连接只收到零星弹幕样本——完整弹幕流
    需要登录 cookie（SESSDATA 等，专用小号）。cookie 来源：
    - cookie_file: 浏览器导出的 cookie 字符串文件（"SESSDATA=xxx; buvid3=xxx; ..."）
    - 写入 cookie/bilibili_cookies.txt 即自动加载
    """

    def __init__(self, cookie_file: Optional[str] = None, cookie_str: Optional[str] = None):
        self._cookie_file = cookie_file
        self._cookie_str = cookie_str
        self._session = None  # requests.Session 惰性创建（cookie 复用）

    def _apply_login_cookies(self, session) -> None:
        """把登录 cookie 合并进 session（专用小号 → 完整弹幕流）

        支持三种来源格式：
        - Playwright storage_state（{"cookies": [{name, value, domain}...]}）
        - 浏览器复制字符串（"SESSDATA=xxx; buvid3=xxx; ..."）
        - 简单 JSON dict（{"SESSDATA": "xxx"}）
        """
        raw: Optional[str] = self._cookie_str
        if not raw and self._cookie_file and os.path.exists(self._cookie_file):
            raw = open(self._cookie_file, encoding="utf-8").read().strip()
        if not raw:
            return
        stripped = raw.strip()
        if stripped.startswith("{"):
            try:
                data = json.loads(stripped)
                if isinstance(data.get("cookies"), list):
                    # Playwright storage_state 格式
                    for c in data["cookies"]:
                        if c.get("value"):
                            session.cookies.set(
                                c["name"], c["value"],
                                domain=c.get("domain") or ".bilibili.com")
                    return
                for k, v in data.items():
                    session.cookies.set(k, str(v), domain=".bilibili.com")
                return
            except json.JSONDecodeError:
                pass
        for pair in stripped.split(";"):
            pair = pair.strip()
            if "=" in pair:
                k, _, v = pair.partition("=")
                session.cookies.set(k.strip(), v.strip(), domain=".bilibili.com")

    def _get_session(self):
        import requests

        if self._session is None:
            self._session = requests.Session()
            self._session.headers.update({
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                              "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
                "Referer": "https://live.bilibili.com/",
                "Origin": "https://live.bilibili.com",
            })
            self._apply_login_cookies(self._session)
        return self._session

    async def fetch_wbi_keys(self) -> str:
        """nav 接口获取 wbi mixin key"""
        import requests

        def _sync() -> str:
            s = self._get_session()
            resp = s.get(codec.NAV_URL, timeout=10)
            resp.raise_for_status()
            img_key, sub_key = codec.extract_wbi_keys(resp.json())
            return codec.get_mixin_key(img_key + sub_key)

        return await asyncio.get_running_loop().run_in_executor(None, _sync)

    async def fetch(self, room_id: int) -> Dict[str, Any]:
        """带 wbi 签名获取弹幕 token（每次调用实时签名——wts 时效性）"""

        def _sync() -> Dict[str, Any]:
            import requests

            s = self._get_session()
            # 预热：直播间页面（cookie 种子；已有 cookie 时为轻量请求）
            try:
                s.get(f"https://live.bilibili.com/{room_id}", timeout=10)
            except Exception:
                pass  # 预热失败不阻断（wbi 签名是主防线）

            mixin_key = s.get("https://api.bilibili.com/x/web-interface/nav", timeout=10)
            nav_data = mixin_key.json()
            img_key, sub_key = codec.extract_wbi_keys(nav_data)
            mixin = codec.get_mixin_key(img_key + sub_key)

            params = codec.wbi_sign_params({"id": room_id, "type": 0}, mixin)
            base_url = codec.DANMU_INFO_URL.split("?")[0]  # 去掉旧查询模板，签名参数由 params 传
            resp = s.get(base_url, params=params, timeout=10)
            resp.raise_for_status()
            body = resp.json()
            code = body.get("code")
            if code != 0:
                raise ValueError(
                    f"getDanmuInfo code={code} message={body.get('message', '')!r}（B站风控/风控升级）"
                )
            return body

        return await asyncio.get_running_loop().run_in_executor(None, _sync)

    # ---- 连接上下文（对齐 barrage-fly SDK roomInit，2026-09-28 限流修复）----

    #: finger/spi：游客生成 buvid3/buvid4（barrage-fly BilibiliApis.roomInit 同款）
    FINGER_SPI_URL = "https://api.bilibili.com/x/frontend/finger/spi"
    ROOM_PLAY_INFO_URL = (
        "https://api.live.bilibili.com/xlive/web-room/v2/index/getRoomPlayInfo"
    )

    @staticmethod
    def _cookie_value(session, name: str) -> Optional[str]:
        """从 session cookiejar 提取 cookie 值（跨 domain 变体取第一个非空）"""
        for c in session.cookies:
            if c.name == name and c.value:
                return c.value
        return None

    def _build_connect_context(self, room_id: int) -> Dict[str, Any]:
        """同步：roomInit 完整预备——cookie 上下文 + 真实房间号 + token

        对齐 barrage-fly BilibiliApis.roomInit（登录/游客双路径）：
        1. 预热直播间页面
        2. 登录态：uid=DedeUserID、buvid=cookie 里的 buvid3（缺一被服务端踢/限流）
           游客：finger/spi 生成 buvid3/buvid4 并带入后续请求
        3. getRoomPlayInfo 拿真实房间号（短号/活动号归一）
        4. nav wbi 签名 → getDanmuInfo（Referer 用 blanc 页面格式）
        """
        s = self._get_session()

        # 1. 预热（cookie 种子）
        try:
            s.get(f"https://live.bilibili.com/{room_id}", timeout=10)
        except Exception:
            pass

        # 2. cookie 上下文：登录 uid / 游客 buvid
        uid = 0
        buvid3 = self._cookie_value(s, "buvid3")
        if self._cookie_value(s, "SESSDATA"):
            uid = int(self._cookie_value(s, "DedeUserID") or 0)
        else:
            # 游客：生成 buvid（B站按 buvid 判设备可信度——游客限流根因之一）
            try:
                resp = s.get(self.FINGER_SPI_URL, timeout=10)
                data = (resp.json() or {}).get("data") or {}
                b3, b4 = data.get("b_3"), data.get("b_4")
                if b3:
                    s.cookies.set("buvid3", b3, domain=".bilibili.com")
                    buvid3 = b3
                if b4:
                    s.cookies.set("buvid4", b4, domain=".bilibili.com")
            except Exception:
                pass  # 生成失败退回现有 cookie（后续 wbi 签名仍是主防线）

        # 3. 真实房间号（auth 包 roomid 必须真实号，SDK 注释明确）
        real_room_id = room_id
        try:
            resp = s.get(
                self.ROOM_PLAY_INFO_URL,
                params={"room_id": room_id, "no_playurl": 1},
                timeout=10,
            )
            data_room = (resp.json() or {}).get("data") or {}
            real_room_id = int(data_room.get("room_id") or room_id)
        except Exception:
            pass  # 失败退回输入号（多数场景输入号即真实号）

        # 4. wbi 签名 → getDanmuInfo
        nav_data = s.get("https://api.bilibili.com/x/web-interface/nav", timeout=10).json()
        img_key, sub_key = codec.extract_wbi_keys(nav_data)
        mixin = codec.get_mixin_key(img_key + sub_key)
        params = codec.wbi_sign_params({"id": real_room_id, "type": 0}, mixin)
        resp = s.get(
            codec.DANMU_INFO_URL.split("?")[0],
            params=params,
            timeout=10,
            headers={
                "Referer": f"https://live.bilibili.com/blanc/{room_id}"
                           "?liteVersion=true&live_from=62001",
            },
        )
        resp.raise_for_status()
        body = resp.json()
        code = body.get("code")
        if code != 0:
            raise ValueError(
                f"getDanmuInfo code={code} message={body.get('message', '')!r}（B站风控/风控升级）"
            )
        data = body.get("data") or {}
        return {
            "token": str(data.get("token", "")),
            "host_list": data.get("host_list") or [],
            "uid": uid,
            "buvid": buvid3 or "",
            "real_room_id": real_room_id,
        }

    async def connect_context(self, room_id: int) -> Dict[str, Any]:
        """WS 连接全上下文：token/host_list/uid/buvid/real_room_id

        auth 包必需（buvid 2023-08 起必须字段；登录 uid 必须与 token 配对）。
        """
        return await asyncio.get_running_loop().run_in_executor(
            None, self._build_connect_context, room_id
        )


class BilibiliProtocolEngine(BaseEngine):
    """B站协议直连引擎

    start/stop/restart(room_id) 公开契约不变（restart 仅重启该房间任务）。
    """

    platform = "bilibili"

    def __init__(
        self,
        state_store=None,
        reconnect_manager: Optional[ReconnectManager] = None,
        danmu_info_fetcher: Optional[Callable[[int], Dict[str, Any]]] = None,
        cookie_file: Optional[str] = None,
        login_flow: Optional[Any] = None,
    ):
        super().__init__(state_store=state_store)
        if reconnect_manager:
            self.set_reconnect_manager(reconnect_manager)
        self._cookie_file = cookie_file
        self._login_flow = login_flow  # 登录流程注入（测试用）；默认 bilibili_login.run_login_flow
        self._danmu_info = danmu_info_fetcher or DanmuInfoFetcher(cookie_file=cookie_file)
        self._room_tasks: Dict[str, asyncio.Task] = {}
        self._room_ws: Dict[str, Any] = {}
        self._heartbeats: Dict[str, asyncio.Task] = {}

    @property
    def engine_id(self) -> str:
        return "protocol:bilibili"

    # ---- 公开契约 ----

    async def start(self, room_id: str) -> None:
        if room_id in self._room_tasks and not self._room_tasks[room_id].done():
            return
        self._set_status(self.status if self.status != self.status.__class__.STOPPED else self.status.__class__.STARTING)
        task = asyncio.create_task(self._run_room(room_id), name=f"bilibili-room-{room_id}")
        self._room_tasks[room_id] = task

    async def stop(self, room_id: str) -> None:
        task = self._room_tasks.pop(room_id, None)
        ws = self._room_ws.pop(room_id, None)
        hb = self._heartbeats.pop(room_id, None)
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
        """语义：仅重启该房间的 asyncio 任务（契约 I 项）"""
        await self.stop(room_id)
        await self.start(room_id)

    # ---- 房间任务主循环 ----

    async def _run_room(self, room_id: str) -> None:
        room_id_int = int(room_id)
        logger.info(f"[bilibili] room {room_id} connecting")
        while True:
            try:
                # 连接上下文：token + 登录 uid/buvid + 真实房间号（barrage-fly 对齐）；
                # fetcher 注入（测试）无 connect_context 时回退游客语义 fetch()
                connect_ctx = getattr(self._danmu_info, "connect_context", None)
                if connect_ctx is not None:
                    ctx = await connect_ctx(room_id_int)
                    token = ctx["token"]
                    if not token:
                        raise ValueError("getDanmuInfo 未返回 token（房间号错误或风控）")
                    host_list = ctx["host_list"]
                else:
                    info = await self._danmu_info.fetch(room_id_int)
                    data = info.get("data") or {}
                    token = str(data.get("token", ""))
                    if not token:
                        raise ValueError(
                            f"getDanmuInfo 未返回 token（code={info.get('code')} "
                            f"message={info.get('message', '')!r}——房间号错误、风控或需登录）"
                        )
                    host_list = data.get("host_list") or []
                    ctx = {
                        "token": token, "host_list": host_list,
                        "uid": 0, "buvid": "", "real_room_id": room_id_int,
                    }

                # 弹幕服务器地址从 host_list 动态取（wss 端口优先）；
                # 硬编码域名已过期（连接被重置）。broadcastlv 为经典兜底。
                ws_urls = [
                    f"wss://{h['host']}:{h['wss_port']}/sub"
                    for h in host_list
                    if h.get("host") and h.get("wss_port")
                ] or [codec.WS_URL]
                logger.debug(f"[bilibili] room {room_id} hosts: {ws_urls}")

                last_err: Optional[Exception] = None
                connected = False
                for ws_url in ws_urls:
                    try:
                        await self._serve_room(room_id, ws_url, ctx)
                        connected = True
                        break
                    except asyncio.CancelledError:
                        raise
                    except Exception as e:
                        last_err = e
                        logger.warning(
                            f"[bilibili] room {room_id} ws {ws_url} failed: "
                            f"{type(e).__name__}: {e}"
                        )
                if not connected:
                    raise last_err or RuntimeError("全部弹幕服务器连接失败")

            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(
                    f"[bilibili] room {room_id} error: {type(e).__name__}: {e}"
                )
                self._set_status(self.status.__class__.ERROR)
                self.mark_gap_start(room_id)
                handled = await self._handle_error_with_reconnect(room_id, e)
                if not handled:
                    # 无重连管理器：按契约 O 走慢速重试（无限，退避封顶）
                    await asyncio.sleep(15)
                else:
                    # 重连成功：补发 GAP（断线窗口）
                    gap = self.build_gap_message(room_id, GapReason.NETWORK)
                    if gap:
                        await self._emit_system(gap)
                    self._set_status(self.status.__class__.RUNNING)

    async def _serve_room(self, room_id: str, ws_url: str, ctx: Dict[str, Any]) -> None:
        """单地址连接 + 认证 + 心跳 + 读循环（host_list 逐地址调用）

        连接失败/读循环异常向上抛出，由 _run_room 尝试下一地址。
        auth 包字段对齐 barrage-fly：真实房间号 + 登录 uid + buvid（必须字段）。
        """
        async with websockets.connect(
                ws_url, ping_interval=None, ping_timeout=None,
                open_timeout=15, close_timeout=5,
                **{_WS_HEADER_KWARG: WS_HANDSHAKE_HEADERS},
            ) as ws:
            self._room_ws[room_id] = ws
            auth_body = codec.build_auth_body(
                int(ctx.get("real_room_id") or room_id),
                ctx["token"],
                uid=int(ctx.get("uid") or 0),
                buvid=str(ctx.get("buvid") or ""),
                queue_uuid=uuid.uuid4().hex[:8],
            )
            await ws.send(codec.encode_packet(codec.OP_AUTH, auth_body))
            logger.info(f"[bilibili] room {room_id} auth sent to {ws_url} "
                        f"(uid={ctx.get('uid', 0)} buvid={'yes' if ctx.get('buvid') else 'no'})")

            hb_task = asyncio.create_task(self._heartbeat(room_id, ws))
            self._heartbeats[room_id] = hb_task
            self._set_status(self.status.__class__.RUNNING)
            try:
                await self._read_loop(room_id, ws)
            finally:
                hb_task.cancel()
                self._room_ws.pop(room_id, None)
                self._heartbeats.pop(room_id, None)

    async def _heartbeat(self, room_id: str, ws) -> None:
        """平台连接心跳（30s，与引擎存活心跳独立）"""
        try:
            while True:
                await ws.send(codec.encode_packet(codec.OP_HEARTBEAT))
                await asyncio.sleep(HEARTBEAT_INTERVAL)
        except asyncio.CancelledError:
            pass

    async def _read_loop(self, room_id: str, ws) -> None:
        """读循环：解帧 → 解压 → op 分发 → 映射发射"""
        async for raw in ws:
            data = raw if isinstance(raw, bytes) else raw.encode("utf-8")
            try:
                packets = codec.decode_packets(data)
            except codec.BilibiliFrameError as e:
                logger.warning(f"[bilibili] room {room_id} frame error: {e}")
                continue
            for proto, op, body in packets:
                if op == codec.OP_HEARTBEAT_REPLY:
                    # 心跳人气值已混淆（直读常为 1），不再映射 ROOM_STATS——
                    # 观看数以 ONLINE_RANK_COUNT 为准（真实在线数）
                    continue
                if op == 8:  # AUTH_REPLY：认证成功应答（body 含 code=0）
                    logger.info(f"[bilibili] room {room_id} auth accepted")
                    continue
                if op != codec.OP_SEND_MSG_REPLY:
                    continue
                # proto 2/3 为压缩嵌套包；0/1 为裸 JSON
                # decompress 统一返回 [(op, body)] 二元组（round3 消费端对齐）
                if proto in (codec.PROTOCOL_ZLIB, codec.PROTOCOL_BROTLI):
                    try:
                        inner_packets = codec.decompress(proto, body)
                    except codec.BilibiliFrameError as e:
                        logger.warning(f"[bilibili] room {room_id} decompress error: {e}")
                        continue
                else:
                    inner_packets = [(op, body)]
                for _iop, inner_body in inner_packets:
                    if _iop != codec.OP_SEND_MSG_REPLY:
                        continue
                    try:
                        text = inner_body.decode("utf-8")
                    except UnicodeDecodeError:
                        continue
                    for packet_text in self._iter_json_packets(text):
                        for mapped in self._map_and_envelope(packet_text, room_id):
                            await self._emit_message(mapped)

    def _iter_json_packets(self, text: str) -> List[str]:
        """op=5 的 body 可能含多条 JSON 串接（无分隔）——逐个解码"""
        decoder = json.JSONDecoder()
        results: List[str] = []
        idx = 0
        while idx < len(text):
            while idx < len(text) and text[idx].isspace():
                idx += 1
            if idx >= len(text):
                break
            try:
                obj, end = decoder.raw_decode(text, idx)
                results.append(json.dumps(obj, ensure_ascii=False))
                idx = end
            except json.JSONDecodeError:
                break
        return results

    def _map_and_envelope(self, text: str, room_id: str) -> List[Dict[str, Any]]:
        """上游 JSON → 线格式 dict（协议版本元数据入信封）"""
        try:
            doc = json.loads(text)
        except json.JSONDecodeError:
            return []
        out: List[Dict[str, Any]] = []
        seq = self.next_seq(room_id)
        ts = int(time.time())
        if isinstance(doc, dict) and doc.get("cmd") == "DANMU_MSG" and isinstance(doc.get("info"), list):
            mapped = codec.map_upstream_message("DANMU_MSG", doc["info"], seq, ts)
        elif isinstance(doc, dict):
            mapped = codec.map_upstream_message(str(doc.get("cmd", "")), doc.get("data"), seq, ts)
        else:
            mapped = None
        if mapped:
            out.append({
                "contract_version": "1.0.0",
                "category": mapped["category"],
                "type": mapped["type"],
                "platform": "bilibili",
                "room_id": room_id,
                "seq": mapped["seq"],
                "timestamp": mapped["timestamp"],
                "engine": self.engine_id,
                "protocol_version": PROTOCOL_VERSION,
                "payload": mapped["payload"],
            })
        self.mark_received(room_id, ts)
        return out

    def _emit_stats(self, room_id: str, popularity: int) -> None:
        """人气值 → ROOM_STATS（fire-and-forget 经回调）"""
        mapped = codec.map_upstream_message(
            "ONLINE_RANK_COUNT", {"count": popularity}, self.next_seq(room_id), int(time.time())
        )
        if mapped:
            import asyncio

            wire = {
                "contract_version": "1.0.0",
                "category": mapped["category"],
                "type": mapped["type"],
                "platform": "bilibili",
                "room_id": room_id,
                "seq": mapped["seq"],
                "timestamp": mapped["timestamp"],
                "engine": self.engine_id,
                "protocol_version": PROTOCOL_VERSION,
                "payload": mapped["payload"],
            }
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(self._emit_message(wire))
            except RuntimeError:
                pass
