"""虎牙直播弹幕协议直连引擎（阶段 3b，2026-09 完成协议栈实现）

命令流（barrage-fly SDK 对照 + 浏览器真实帧验证）：
1. roomInit：GET www.huya.com/{room} → tid（lChannelId/TT_ROOM_DATA）
2. WS 连接 wss://cdnws.api.huya.com:443
3. doLaunch（op=3 WupReq liveui/doLaunch）
4. registerGroup（op=16，vGroupId=["live:{tid}","chat:{tid}"]）
5. updateUserInfo（op=33）
6. 心跳（op=20 WupReq onlineui/OnUserHeartBeat，25s 周期/首帧 15s）
7. 下推（op=7）→ WSPushMessage.lUri 分发（1400 弹幕/6501 礼物/6110 进场）
"""

import asyncio
import time
from typing import Dict

import websockets
from loguru import logger

from danmaku_listener.contract.models import GapReason
from danmaku_listener.engines import login_gate as LOGIN_GATE
from danmaku_listener.engines.base import BaseEngine
from danmaku_listener.engines.protocol import huya_codec as codec
from danmaku_listener.engines.protocol.huya_tars import TarsError
from danmaku_listener.engines.send_login import HUYA_COOKIE_NAMES  # ship 对抗F8：存档判定与 takeover 同名单源

PROTOCOL_VERSION = codec.PROTOCOL_VERSION
HEARTBEAT_INITIAL_DELAY = 15.0

HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Referer": "https://www.huya.com/",
}


class RoomNotLiveError(ValueError):
    """房间未开播（区分未开播与房间不存在/页面结构变更）。

    2026-10-14 用户实测回归：未开播此前落入通用 except 记 NETWORK GAP，
    前端显示「缺口 network」误导排查方向（房间根本没连上、无缺失语义）。
    专用类型供 _run_room 分流：上报 mark_live_change(False)（下播裁剪，
    base 承诺「下播不计 GAP」）+ 诚实状态文案，不再走缺口/ERROR 路径。
    """


class HuyaProtocolEngine(BaseEngine):
    """虎牙协议直连引擎（Tars 栈完整实现）"""

    platform = "huya"

    def __init__(self, state_store=None):
        super().__init__(state_store=state_store)
        self._room_tasks: Dict[str, asyncio.Task] = {}
        self._stop_flags: Dict[str, bool] = {}
        self._gift_items: dict = {}  # 礼物 ID 表（getPropsList 响应，引擎级共享）
        # 登录闭环状态（2026-10-06 平台登录计划；cookie 文件形态——login_gate）
        self._login_budgets: Dict[str, int] = {}
        self._budget_warned: set = set()

    @property
    def engine_id(self) -> str:
        return "protocol:huya"

    async def start(self, room_id: str) -> None:
        if room_id in self._room_tasks and not self._room_tasks[room_id].done():
            return
        # ship 对抗终审F1：重新添加=新登录机会（对齐 jd/taobao/xiaohongshu 的
        # 「成功清零+budget_warned 释放」先例）——否则预算耗尽文案承诺的
        # 「停止重加可重触发」落空，引擎级缓存单例下永久游客模式
        self._login_budgets.pop(room_id, None)
        self._budget_warned.discard(room_id)
        self._stop_flags[room_id] = False
        self._room_tasks[room_id] = asyncio.create_task(self._run_room(room_id), name=f"huya-room-{room_id}")

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

    # ---- 房间初始化 ----

    @staticmethod
    def _fetch_tid_sync(room_id: str) -> int:
        """直播间页面提取 tid（TT_ROOM_DATA.lChannelId/hyPlayerConfig）"""
        import re

        import requests

        resp = requests.get(
            f"https://www.huya.com/{room_id}", headers=HTTP_HEADERS, timeout=10
        )
        resp.raise_for_status()
        body = resp.text
        # 房间状态判定（2026-10-06 用户实测：未开播房间 tid 字段全 0/缺失——
        # 原"房间不存在或页面结构变更"误导；区分未开播与不存在）
        m_state = re.search(
            r'TT_ROOM_DATA\s*=\s*\{[^}]*"isOn"\s*:\s*(true|false)', body)
        if m_state and m_state.group(1) == "false":
            # 回放细分（2026-10-14 用户实测困惑：虎牙下播后自动播放回放，
            # 观感酷似在播——「未开播」文案让用户误判检测失效。isReplay
            # 与 isOn 同层扁平字段，[^}]* 同一窗口可达）
            m_replay = re.search(
                r'TT_ROOM_DATA\s*=\s*\{[^}]*"isReplay"\s*:\s*(true|false)', body)
            replay = bool(m_replay and m_replay.group(1) == "true")
            suffix = "（正在播放回放，并非实时直播）" if replay else ""
            raise RoomNotLiveError(
                f"虎牙房间 {room_id} 未开播{suffix}——开播后引擎将自动开始监听（持续重试中）")
        m = re.search(r'"tid"\s*:\s*(\d+)', body) or re.search(
            r'"lChannelId"\s*:\s*"?(\d+)', body
        )
        if not m:
            raise ValueError("虎牙房间页未找到 tid（房间不存在或页面结构变更）")
        return int(m.group(1))

    async def _fetch_tid(self, room_id: str) -> int:
        return await asyncio.get_running_loop().run_in_executor(
            None, self._fetch_tid_sync, room_id
        )

    # ---- 房间任务主循环 ----

    async def _run_room(self, room_id: str) -> None:
        # 登录门槛（2026-10-06 平台登录计划，用户裁定 B）：cookie 文件登录态
        # 判定→弹窗→存档。诚实边界：登录态不进当前 Tars 协议连接（协议无注入路径）。
        try:
            from danmaku_listener.config.settings import get_settings
            import json as _json
            import os as _os
            cookie_path = _os.path.join(get_settings().cookie_dir,
                                        f"{self.platform}_login_cookies.json")
            # 一次登录门（2026-10-13 用户验收裁定）：存档缺失→发送侧 takeover
            # 一次扫码（登录态入发送 profile+快照+同步写监听存档）——替代旧
            # 非持久存档窗两段式。takeover 失败回退旧存档窗兜底。
            archived = False
            if _os.path.isfile(cookie_path):
                try:
                    with open(cookie_path, encoding="utf-8") as fh:
                        _cookies = _json.load(fh).get("cookies", [])
                    archived = any(c.get("name") in HUYA_COOKIE_NAMES
                                   and c.get("value") for c in _cookies)
                except Exception:  # noqa: BLE001
                    archived = False
            if not archived:
                # ship 对抗F5：takeover 弹窗同样受登录预算约束（LOGIN_BUDGET=2）
                # ——无人值守下存档持续缺失时不按重连频率反复弹可见窗
                budget = self._login_budgets.get(room_id, LOGIN_GATE.LOGIN_BUDGET)
                if budget <= 0:
                    if room_id not in self._budget_warned:
                        self._budget_warned.add(room_id)
                        await self._emit_system_status(
                            room_id, f"{LOGIN_GATE.LOGIN_EVENT_BUDGET_EXHAUSTED}: "
                            "登录未完成——已按游客模式继续；停止该房间后重新添加可再次触发登录窗口")
                else:
                    self._login_budgets[room_id] = budget - 1
                    from danmaku_listener.senders.huya import takeover_login_and_archive
                    await self._emit_system_status(
                        room_id, f"{LOGIN_GATE.LOGIN_EVENT_FIRST_LOGIN}: 虎牙登录——"
                        "已弹出浏览器，一次扫码同时开通监听存档与弹幕发送")
                    if await takeover_login_and_archive(room_id):
                        await self._emit_system_status(room_id, "登录成功——监听存档与发送登录态已同步")
                        # ship 对抗终审F1：成功清零对齐 jd/taobao 先例（下次失效重新计预算）
                        self._login_budgets.pop(room_id, None)
                        self._budget_warned.discard(room_id)
                    else:
                        outcome = await LOGIN_GATE.ensure_cookie_file_login(
                            room_id, self.platform, "https://www.huya.com/",
                            cookie_path,
                            self._stop_flags, self._login_budgets, self._budget_warned,
                            lambda detail: self._emit_system_status(room_id, detail))
                        if outcome == "stopped":
                            return
            else:
                await self._emit_system_status(room_id, "登录存档有效——跳过登录门")
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001  登录门槛故障不阻塞监听（降级游客）
            logger.warning(f"[huya] room {room_id} login gate error: "
                           f"{type(e).__name__}: {str(e)[:80]}")
        logger.info(f"[huya] room {room_id} connecting")
        while not self._stop_flags.get(room_id):
            try:
                tid = await self._fetch_tid(room_id)
                async with websockets.connect(codec.WS_URL, ping_interval=None) as ws:
                    # 命令流：doLaunch → registerGroup → updateUserInfo → 礼物表拉取
                    await ws.send(codec.build_do_launch())
                    await ws.send(codec.build_register_group(tid))
                    await ws.send(codec.build_update_user_info())
                    await ws.send(codec.build_gift_list_req())
                    logger.info(f"[huya] room {room_id} doLaunch+registerGroup sent (tid={tid})")
                    self._set_status(self.status.__class__.RUNNING)
                    # 上报开播（2026-10-14 接线：base 的 GAP 裁剪依赖 live 状态，
                    # 下播后重连的未决缺口起点需 live_changed_at 裁剪）
                    self.mark_live_change(room_id, True)
                    hb_task = asyncio.create_task(self._heartbeat(room_id, ws, tid))
                    try:
                        await self._read_loop(room_id, ws)
                    finally:
                        hb_task.cancel()
            except asyncio.CancelledError:
                raise
            except RoomNotLiveError as e:
                # 2026-10-14 用户实测回归：未开播此前落入通用 except 记 NETWORK
                # GAP——房间根本没连上、无缺失语义，前端「缺口 network」误导
                # 排查方向。mark_live_change(False) 落实 base「下播不计 GAP」
                # 承诺（未决缺口作废）；状态转换沿只发一次，重试静默轮询。
                logger.info(f"[huya] room {room_id} not live: {e}")
                if self.mark_live_change(room_id, False):
                    await self._emit_system_status(room_id, str(e))
                if self._stop_flags.get(room_id):
                    return
                await asyncio.sleep(15)
            except Exception as e:
                logger.warning(f"[huya] room {room_id} error: {e}")
                if self._is_stopping(room_id):
                    return  # stop-in-progress close is expected (sent 1000) - no GAP/ERROR

                self._set_status(self.status.__class__.ERROR)
                self.mark_gap_start(room_id)
                gap = self.build_gap_message(room_id, GapReason.NETWORK)
                if gap:
                    await self._emit_system(gap)
                if self._stop_flags.get(room_id):
                    return
                await asyncio.sleep(15)

    async def _heartbeat(self, room_id: str, ws, tid: int) -> None:
        """平台心跳（首帧 15s 延迟 + 25s 周期，SDK 默认口径）"""
        try:
            await asyncio.sleep(HEARTBEAT_INITIAL_DELAY)
            while True:
                await ws.send(codec.build_heartbeat(tid))
                await asyncio.sleep(codec.HEARTBEAT_INTERVAL)
        except asyncio.CancelledError:
            pass

    async def _read_loop(self, room_id: str, ws) -> None:
        async for raw in ws:
            data = raw if isinstance(raw, bytes) else raw.encode("utf-8")
            ts = int(time.time())
            try:
                cmd = codec.decode_command(data)
            except (TarsError, Exception) as e:  # noqa: BLE001
                logger.warning(f"[huya] room {room_id} frame error: {e}")
                continue
            op = cmd["operation"]
            v_data = cmd["v_data"]
            if op == codec.OP_WUP_RSP:
                # doLaunch 应答 / 礼物表响应
                self.mark_received(room_id, ts)
                try:
                    rsp = codec.decode_wup_rsp(v_data)
                except Exception as e:  # noqa: BLE001
                    logger.debug(f"[huya] room {room_id} wup rsp decode error: {e}")
                    continue
                if rsp.get("func") == "getPropsList" and not self._gift_items:
                    t_rsp = (rsp.get("uni") or {}).get("tRsp") or b""
                    if t_rsp:
                        try:
                            self._gift_items = codec.decode_gift_list(t_rsp)
                            logger.info(
                                f"[huya] room {room_id} gift list loaded "
                                f"({len(self._gift_items)} items)"
                            )
                        except Exception as e:  # noqa: BLE001
                            logger.warning(f"[huya] room {room_id} gift list parse error: {e}")
                continue
            if op in (codec.OP_REGISTER_GROUP_RSP, codec.OP_UPDATE_USER_INFO_RSP,
                      codec.OP_HEARTBEAT_RSP):
                self.mark_received(room_id, ts)
                continue
            if op == codec.OP_MSG_PUSH:
                self.mark_received(room_id, ts)
                pushes: list = []
                try:
                    single = codec.decode_push_message(v_data)
                    if single:
                        pushes.append(single)
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"[huya] room {room_id} push decode error: {e}")
                for push in pushes:
                    mapped = codec.map_upstream(
                        push["data"], push["uri"], self.next_seq(room_id), ts,
                        gift_items=self._gift_items,
                    )
                    if mapped:
                        await self._emit_message(self._envelope(room_id, mapped))
                continue
            if op == codec.OP_MSG_PUSH_V2:
                # 批量下推（流量主体；每条 WSMsgItem 同 op=7 分发）
                self.mark_received(room_id, ts)
                try:
                    items = codec.decode_push_message_v2(v_data)
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"[huya] room {room_id} push v2 decode error: {e}")
                    continue
                for item in items:
                    mapped = codec.map_upstream(
                        item["data"], item["uri"], self.next_seq(room_id), ts,
                        gift_items=self._gift_items,
                    )
                    if mapped:
                        await self._emit_message(self._envelope(room_id, mapped))
                continue

    @staticmethod
    def _envelope(room_id: str, mapped: dict) -> dict:
        """契约信封包装"""
        return {
            "contract_version": "1.0.0",
            "category": mapped["category"],
            "type": mapped["type"],
            "platform": "huya",
            "room_id": room_id,
            "seq": mapped["seq"],
            "timestamp": mapped["timestamp"],
            "engine": "protocol:huya",
            "protocol_version": PROTOCOL_VERSION,
            "payload": mapped["payload"],
        }
