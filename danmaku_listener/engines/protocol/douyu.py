"""斗鱼直播弹幕协议直连引擎（阶段 3a，独立门禁）

连接通道：**TCP 明文直连**（2026-09-28 实测切换）——
- wss 入口（danmuproxy:9501）连接被重置；wsproxy:6671 被 TLS 指纹拦截
  （Python/OpenSSL ClientHello 被 WAF 拒绝，SSLV3_ALERT_HANDSHAKE_FAILURE）
- TCP 明文端口（danmuproxy:12601/12602/7501/8601 + gateway API 动态列表）实测全通
- 帧格式与 WS 版一致（12B 小端头 + key@=value/ 文本体），仅传输层不同

弹幕服务器地址优先从 gateway API 动态获取（barrage-fly DouyuApis.getServerInfo
同款：POST /lapi/live/gateway/web/{roomId}?isH5=1），失败退 danmuproxy 经典端口。
"""

import asyncio
import json
import struct
import time
from typing import Any, Dict, List, Tuple

import websockets
from loguru import logger

from danmaku_listener.contract.models import GapReason
from danmaku_listener.engines.base import BaseEngine
from danmaku_listener.engines.protocol import douyu_codec as codec

PROTOCOL_VERSION = "douyu-1"

#: gateway API（动态弹幕服务器列表；barrage-fly 同款）
GATEWAY_URL = "https://www.douyu.com/lapi/live/gateway/web/{room_id}?isH5=1"
_HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Origin": "https://www.douyu.com",
    "Referer": "https://www.douyu.com/",
}

#: TCP 明文兜底端口（danmuproxy 经典弹幕代理端口组）
TCP_FALLBACK_HOST = "danmuproxy.douyu.com"
TCP_FALLBACK_PORTS = (12601, 12602, 7501, 7601, 8601, 8602)


class DouyuProtocolEngine(BaseEngine):
    """斗鱼协议直连引擎（复用阶段 1 骨架模式；独立门禁不与虎牙绑驾）"""

    platform = "douyu"

    def __init__(self, state_store=None):
        super().__init__(state_store=state_store)
        self._room_tasks: Dict[str, asyncio.Task] = {}
        self._room_writers: Dict[str, asyncio.StreamWriter] = {}
        self._heartbeats: Dict[str, asyncio.Task] = {}
        self._stop_flags: Dict[str, bool] = {}

    @property
    def engine_id(self) -> str:
        return "protocol:douyu"

    async def start(self, room_id: str) -> None:
        if room_id in self._room_tasks and not self._room_tasks[room_id].done():
            return
        self._stop_flags[room_id] = False
        self._room_tasks[room_id] = asyncio.create_task(self._run_room(room_id), name=f"douyu-room-{room_id}")

    async def stop(self, room_id: str) -> None:
        self._stop_flags[room_id] = True
        task = self._room_tasks.pop(room_id, None)
        hb = self._heartbeats.pop(room_id, None)
        writer = self._room_writers.pop(room_id, None)
        if hb:
            hb.cancel()
        if writer:
            writer.close()
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

    # ---- 服务器地址解析 ----

    @staticmethod
    def _fetch_gateway_list(room_id: int) -> List[Tuple[str, int]]:
        """gateway API 动态列表（同步；SDK DouyuApis.getServerInfo 同款）"""
        import requests

        resp = requests.post(
            GATEWAY_URL.format(room_id=room_id), headers=_HTTP_HEADERS, timeout=10
        )
        resp.raise_for_status()
        data = (resp.json() or {}).get("data") or {}
        endpoints = [
            (str(g["ip"]), int(g["port"]))
            for g in (data.get("gateway") or [])
            if g.get("ip") and g.get("port")
        ]
        return endpoints

    async def _resolve_endpoints(self, room_id: int) -> List[Tuple[str, int]]:
        """弹幕服务器候选列表

        实测（2026-09-28）：danmuproxy 经典代理 TCP 端口对标准 loginreq 兼容；
        gateway API 返回的直连服务器（175.25.x.x:801x）会接受连接但拒绝
        标准 loginreq（立即断开）——故经典代理优先，gateway 仅兜底。
        """
        endpoints: List[Tuple[str, int]] = [
            (TCP_FALLBACK_HOST, p) for p in TCP_FALLBACK_PORTS
        ]
        try:
            dynamic = await asyncio.get_running_loop().run_in_executor(
                None, self._fetch_gateway_list, room_id
            )
            endpoints.extend(dynamic)
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[douyu] room {room_id} gateway list failed: {e}")
        return endpoints[:6]  # 最多尝试 6 个，避免拖长重连周期

    # ---- 房间任务主循环 ----

    async def _run_room(self, room_id: str) -> None:
        logger.info(f"[douyu] room {room_id} connecting (tcp)")
        while not self._stop_flags.get(room_id):
            try:
                endpoints = await self._resolve_endpoints(int(room_id))
                last_err: Exception = RuntimeError("无可用弹幕服务器")
                connected = False
                for host, port in endpoints:
                    try:
                        await self._serve_room(room_id, host, port)
                        connected = True
                        break
                    except asyncio.CancelledError:
                        raise
                    except Exception as e:  # noqa: BLE001
                        last_err = e
                        logger.warning(
                            f"[douyu] room {room_id} tcp {host}:{port} failed: "
                            f"{type(e).__name__}: {str(e)[:80]}"
                        )
                if not connected:
                    raise last_err
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(f"[douyu] room {room_id} error: {e}")
                self._set_status(self.status.__class__.ERROR)
                self.mark_gap_start(room_id)
                gap = self.build_gap_message(room_id, GapReason.NETWORK)
                if gap:
                    await self._emit_system(gap)
                if self._stop_flags.get(room_id):
                    return
                # 慢速重试（契约 O：指数退避封顶 15 分钟）
                await asyncio.sleep(15)

    async def _serve_room(self, room_id: str, host: str, port: int) -> None:
        """单地址 TCP 直连 + login→loginres→join + 心跳 + 读循环（逐地址调用）

        login 与 join 之间必须等 loginres：danmuproxy 逐包读处理，
        背靠背连发时 join_group 会被丢弃（实测 2026-09-28——连接稳定但零消息）。
        """
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=15
        )
        self._room_writers[room_id] = writer
        try:
            writer.write(codec.build_login(int(room_id)))
            await writer.drain()
            await self._wait_login_res(room_id, reader)
            writer.write(codec.build_join_group(int(room_id)))
            await writer.drain()
            logger.info(f"[douyu] room {room_id} login+join sent (tcp {host}:{port})")
            self._set_status(self.status.__class__.RUNNING)
            hb_task = asyncio.create_task(self._heartbeat(room_id, writer))
            self._heartbeats[room_id] = hb_task
            try:
                await self._read_loop(room_id, reader, writer)
            finally:
                hb_task.cancel()
                self._heartbeats.pop(room_id, None)
        finally:
            self._room_writers.pop(room_id, None)
            writer.close()

    @staticmethod
    async def _wait_login_res(room_id: str, reader: asyncio.StreamReader) -> None:
        """读流直到出现 loginres（期间 pingreq 等过程帧丢弃）"""
        buf = b""
        while True:
            data = await asyncio.wait_for(reader.read(65536), timeout=10)
            if not data:
                raise ConnectionResetError("douyu closed before loginres")
            buf += data
            while len(buf) >= 4:
                msg_len = struct.unpack_from("<I", buf, 0)[0]
                frame_total = 4 + msg_len
                if msg_len < 8 or len(buf) < frame_total:
                    break
                frame, buf = buf[:frame_total], buf[frame_total:]
                for fields in codec.decode_packets(frame):
                    if fields.get("type") == "loginres":
                        return

    async def _heartbeat(self, room_id: str, writer: asyncio.StreamWriter) -> None:
        """平台连接心跳（45s；首帧延迟 15s——barrage-fly SDK 同款初始延迟，

        join 后立即心跳会干扰服务器状态机，实测零消息）
        """
        try:
            await asyncio.sleep(15)
            while True:
                writer.write(codec.build_heartbeat())
                await writer.drain()
                await asyncio.sleep(codec.HEARTBEAT_INTERVAL)
        except asyncio.CancelledError:
            pass

    async def _read_loop(self, room_id: str, reader: asyncio.StreamReader,
                         writer: asyncio.StreamWriter) -> None:
        """TCP 流拆帧 → 映射发射（帧格式与 WS 版一致）"""
        buf = b""
        while True:
            data = await reader.read(65536)
            if not data:
                raise ConnectionResetError("douyu tcp closed by peer")
            buf += data
            while len(buf) >= 4:
                msg_len = struct.unpack_from("<I", buf, 0)[0]
                frame_total = 4 + msg_len
                if msg_len < 8 or len(buf) < frame_total:
                    break
                try:
                    packets = codec.decode_packets(buf[:frame_total])
                except codec.DouyuFrameError as e:
                    logger.warning(f"[douyu] room {room_id} frame error: {e}")
                    packets = []
                buf = buf[frame_total:]
                ts = int(time.time())
                for fields in packets:
                    await self._dispatch(room_id, fields, ts, writer)
                if not packets:
                    continue

    async def _dispatch(self, room_id: str, fields: Dict[str, Any], ts: int,
                        writer: asyncio.StreamWriter) -> None:
        """单包分发：标记接收 → 契约映射 → pingreq 应答 → 风控告警"""
        self.mark_received(room_id, ts)
        mapped = codec.map_upstream(fields, self.next_seq(room_id), ts)
        if mapped:
            await self._emit_message({
                "contract_version": "1.0.0",
                "category": mapped["category"],
                "type": mapped["type"],
                "platform": "douyu",
                "room_id": room_id,
                "seq": mapped["seq"],
                "timestamp": mapped["timestamp"],
                "engine": self.engine_id,
                "protocol_version": PROTOCOL_VERSION,
                "payload": mapped["payload"],
            })
        if fields.get("type") == "pingreq":
            # 服务器心跳请求：响应业务心跳
            writer.write(codec.encode_packet(codec.encode_body({"type": "heartbeat"})))
            await writer.drain()
        if fields.get("type") == "killuser" or fields.get("type") == "sysalert":
            logger.warning(f"[douyu] room {room_id} server alert: {fields}")
            await self._emit_system(self._system_factory.route_failed(
                room_id=room_id,
                seq=self.next_seq(room_id),
                ts=ts,
                reason_code=f"douyu.{fields.get('type', 'alert')}",
                fix_hint="平台下发风控/系统消息，查看斗鱼风控手册",
                docs_anchor="docs/platforms/douyu/runbook.md",
            ))
