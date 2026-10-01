"""美团直播弹幕引擎（HTTP 轮询直连——无需登录/浏览器/签名）

技术路线（2026-09-30 调研定型，四平台从易到难第一站）：
美团直播弹幕走 mapi 网关 HTTP 轮询（参考开源实现 markadc/meituan-live 实测）：
GET i.meituan.com/mapi/dzu/live/livestudiobaseinfo.bin 返回**全量倒序快照**
messageVO.msgs[]，按 commentId 去重后增量 emit。弹幕读取无需登录 cookie
（cookie 仅主播端 startlive/商品管理需要——本引擎只监听不操作）。

参数安全边界（持续有效）：只监听、不发送弹幕、不操作直播间；不绑定客户主账号。

房间参数：live_id（场次级——下播即失效，与 1688 feedId 同性质，
需重新复制直播间分享链接）/ dpurl.cn 分享短链 / 含 liveid= 的美团链接。
下播/场次失效检测：响应异常退避重试，持续失败三段式提示重新复制链接。

消息映射（一期按实测样本最小集）：
- imUserDTO+imMsgDTO → DANMU（userName/userId/content/commentId）
- liveInfoVo.beginTime → 历史场次一次性提示（SYSTEM_STATUS）
- 其余形态（进入/点赞等 msgType）debug 丢弃——实测样本后校准（TODOS）
"""

import asyncio
import random
import re
import time
from typing import Any, Dict, Optional

import requests
from loguru import logger

from danmaku_listener.contract.models import GapReason
from danmaku_listener.engines.base import BaseEngine

PROTOCOL_VERSION = "meituan-1"

POLL_URL = "https://i.meituan.com/mapi/dzu/live/livestudiobaseinfo.bin"
LIVEINFO_URL = "https://mlive.meituan.com/live/centercontrol/liveinfo"

#: 通用分享 key（markadc/meituan-live 实测可用——非账号凭证，观众侧公开参数）
SHAREKEY = "B3053BB78F172E06BEA628B879C4FDC7"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0")

POLL_INTERVAL = 1.5          # 轮询间隔（秒）——1-3s 区间取下限+抖动
POLL_TIMEOUT = 10.0          # 单次 HTTP 超时
FAIL_THRESHOLD = 40          # 连续失败阈值（约 1 分钟）→ 三段式
DEDUP_LIMIT = 500            # commentId 去重有界集
IDLE_NOTICE_TIMEOUT = 600.0  # 轮询正常但零新消息的静默提示阈值（10 分钟）


class MeituanParseError(ValueError):
    """房间参数无法解析"""


class MeituanLiveEnded(Exception):
    """场次已结束/无效（内部信号——触发三段式与长退避）"""


def extract_live_id(room_spec: str) -> str:
    """live_id 归一（纯格式预判，不碰网络）：
    纯数字 live_id / 含 liveid= 的美团链接 / dpurl.cn 短链（运行期 302 解析）
    """
    spec = room_spec.strip()
    if "meituan.com" in spec or "dpurl.cn" in spec:
        m = re.search(r"liveid=(\d+)", spec)
        if m:
            return m.group(1)
        if "dpurl.cn" in spec:
            return spec  # 短链原样返回，_resolve_live_id 运行期解析
        raise MeituanParseError(
            f"无法从美团链接提取 liveid: {spec[:80]!r}——"
            "请使用直播间分享短链（dpurl.cn/...）或含 liveid= 的链接")
    m = re.match(r"^(\d{5,15})$", spec)
    if m:
        return m.group(1)
    raise MeituanParseError(
        f"无法解析美团直播间 live_id: {spec[:60]!r}——"
        "请使用直播间分享短链或 live_id 数字")


class MeituanPollEngine(BaseEngine):
    """美团直播弹幕引擎（mapi HTTP 轮询，独立平台）"""

    platform = "meituan"

    def __init__(self, state_store=None, raw_hook=None):
        super().__init__(state_store=state_store)
        self._raw_hook = raw_hook  # 诊断钩子：轮询原始快照（分析用）
        self._room_tasks: Dict[str, asyncio.Task] = {}
        self._stop_flags: Dict[str, bool] = {}
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": UA})
        # per-room 状态：commentId 有界去重 + ROOM_STATS 上次值（变化才 emit）
        self._seen: Dict[str, set] = {}
        self._seen_list: Dict[str, list] = {}
        self._last_stats: Dict[str, tuple] = {}

    @property
    def engine_id(self) -> str:
        return "poll:meituan"

    def validate_room_id(self, room_id: str) -> None:
        """add_room 预校验：live_id 可解析（纯格式，不碰网络）"""
        try:
            extract_live_id(room_id)
        except MeituanParseError as e:
            raise ValueError(str(e)) from e

    # ---- 公开契约 ----

    async def start(self, room_id: str) -> None:
        if room_id in self._room_tasks and not self._room_tasks[room_id].done():
            return
        self._stop_flags[room_id] = False
        self._room_tasks[room_id] = asyncio.create_task(
            self._run_room(room_id), name=f"meituan-room-{room_id}")

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
        await self.stop(room_id)
        await self.start(room_id)

    # ---- 房间任务主循环 ----

    async def _run_room(self, room_id: str) -> None:
        spec = extract_live_id(room_id)
        logger.info(f"[meituan] room {room_id} connecting (spec={spec[:40]})")
        try:
            live_id = await self._resolve_live_id(spec)
        except MeituanParseError as e:
            logger.warning(f"[meituan] room {room_id} {e}")
            await self._emit_route_failed(room_id, "meituan.poll.parse_failed",
                                          str(e), "docs/platforms/meituan/runbook.md")
            return
        logger.info(f"[meituan] room {room_id} live_id={live_id}")

        backoff = 2.0
        fail_count = 0
        idle_since: Optional[float] = None  # 零新消息起始（静默提示用）
        idle_notified = False
        while not self._stop_flags.get(room_id):
            try:
                jsdata = await self._poll_once(live_id)
                fail_count = 0
                backoff = 2.0
                self.mark_received(room_id, int(time.time()))
                new_count = await self._handle_snapshot(room_id, jsdata)
            except asyncio.CancelledError:
                raise
            except MeituanLiveEnded:
                await self._emit_route_failed(
                    room_id, "meituan.poll.live_ended",
                    "美团场次已结束或 live_id 无效——live_id 场次级，"
                    "开播后重新复制直播间分享链接",
                    "docs/platforms/meituan/runbook.md")
                await asyncio.sleep(120.0)
                continue
            except Exception as e:  # noqa: BLE001
                fail_count += 1
                logger.warning(f"[meituan] room {room_id} poll error: "
                               f"{type(e).__name__}: {str(e)[:80]}")
                if fail_count == FAIL_THRESHOLD:
                    self._set_status(self.status.__class__.ERROR)
                    self.mark_gap_start(room_id)
                    gap = self.build_gap_message(room_id, GapReason.NETWORK)
                    if gap:
                        await self._emit_system(gap)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60.0)
                continue
            # 静默提示（一次性）：轮询正常但长时间零新消息——
            # 美团接口无可靠在播判据（实测 endTime/liveStatus 均不可用），
            # 只提示不判死，冷直播间与下播由用户区分
            now = time.monotonic()
            if new_count > 0:
                idle_since, idle_notified = None, False
            else:
                idle_since = idle_since or now
                if (not idle_notified and now - idle_since > IDLE_NOTICE_TIMEOUT):
                    idle_notified = True
                    await self._emit_system_status(
                        room_id, "美团轮询正常但 10 分钟零新弹幕——"
                                 "确认直播仍在进行；live_id 场次级，"
                                 "若已下播请重新复制分享链接")
            await asyncio.sleep(POLL_INTERVAL + random.uniform(0, 0.5))

    # ---- 轮询与解析 ----

    async def _poll_once(self, live_id: str) -> Dict[str, Any]:
        """单次快照拉取（线程池执行同步 requests）"""
        params = {
            "platform": "2", "appid": "10", "inapp": "false",
            "liveid": live_id, "anchorId": "0", "sharekey": SHAREKEY,
            "access_source": "", "invokeApp": "",
            "yodaReady": "h5", "csecplatform": "4", "csecversion": "2.4.0",
        }
        loop = asyncio.get_running_loop()

        def _get():
            r = self._session.get(POLL_URL, params=params, timeout=POLL_TIMEOUT)
            r.raise_for_status()
            return r.json()

        try:
            return await loop.run_in_executor(None, _get)
        except ValueError as e:  # json 解码失败——网关异常页
            raise RuntimeError(f"响应非 JSON: {str(e)[:50]}") from e

    async def _resolve_live_id(self, spec: str) -> str:
        """短链 302 解析（dpurl.cn）；其余形态已由 extract_live_id 归一"""
        if "dpurl.cn" not in spec:
            return spec
        loop = asyncio.get_running_loop()

        def _follow():
            r = self._session.get(spec, allow_redirects=False, timeout=POLL_TIMEOUT)
            loc = r.headers.get("Location", "")
            m = re.search(r"liveid=(\d+)", loc)
            if not m:
                raise MeituanParseError(
                    f"短链跳转未含 liveid: {loc[:80]!r}——确认分享链接有效")
            return m.group(1)

        return await loop.run_in_executor(None, _follow)

    async def _handle_snapshot(self, room_id: str, jsdata: Dict[str, Any]) -> int:
        """快照 → 契约消息（倒序数组正序遍历；commentId 有界去重）

        返回本轮新 emit 的消息数（静默提示用）。
        2026-09-30 用户实测教训：liveInfoVo.endTime/liveStatus **不可用作
        在播判据**（近期场次 endTime 同样有值、liveStatus=None）——已移除
        该判定；场次状态只能靠弹幕流动观察（MeituanLiveEnded 仅保留给
        网关明确报错 code!=0）。
        """
        if not isinstance(jsdata, dict):
            return 0
        if self._raw_hook is not None:
            try:
                self._raw_hook(jsdata)
            except Exception:  # noqa: BLE001
                pass
        await self._maybe_emit_stats(room_id, jsdata.get("liveInfoVo") or {})

        msg_vo = jsdata.get("messageVO") or {}
        msgs = msg_vo.get("msgs")
        if not isinstance(msgs, list):
            if jsdata.get("code") not in (None, 0):
                raise MeituanLiveEnded(f"code={jsdata.get('code')}")
            return 0
        ts = int(time.time())
        new_count = 0
        for one in reversed(msgs):  # 响应倒序 → 正序 emit
            if not isinstance(one, dict):
                continue
            msg_type = one.get("msgType")
            if msg_type != 2:
                # 实测校准：msgType=2=聊天；进入/点赞等类型等在播样本后扩展
                logger.debug(f"[meituan] room {room_id} unmapped msgType="
                             f"{msg_type!r} keys={sorted(one.keys())[:6]}")
                continue
            user = one.get("imUserDTO") or {}
            msg = one.get("imMsgDTO") or {}
            comment_id = msg.get("commentId")
            content = (msg.get("content") or "").strip()
            if not content or comment_id is None:
                continue
            if comment_id in self._seen.setdefault(room_id, set()):
                continue
            self._seen[room_id].add(comment_id)
            self._seen_list.setdefault(room_id, []).append(comment_id)
            if len(self._seen_list[room_id]) > DEDUP_LIMIT:
                self._seen[room_id].discard(self._seen_list[room_id].pop(0))
            new_count += 1
            mapped = {
                "category": "business", "type": "DANMU",
                "seq": self.next_seq(room_id), "timestamp": ts,
                "payload": {"type": "DANMU",
                            "user_name": str(user.get("userName", "")),
                            "content": content,
                            "user_id": str(user.get("userId", ""))},
            }
            await self._emit_message(self._envelope(room_id, mapped))
        return new_count

    async def _maybe_emit_stats(self, room_id: str, live_vo: Dict[str, Any]) -> None:
        """liveLikeCount/liveHeat → ROOM_STATS（值变化才 emit，避免 1.5s 轰炸）"""
        like_count = live_vo.get("liveLikeCount")
        heat = live_vo.get("liveHeat")
        sig = (like_count, heat)
        if not like_count and not heat:
            return
        if self._last_stats.get(room_id) == sig:
            return
        self._last_stats[room_id] = sig
        mapped = {
            "category": "business", "type": "ROOM_STATS",
            "seq": self.next_seq(room_id), "timestamp": int(time.time()),
            "payload": {"type": "ROOM_STATS",
                        "like_count": like_count if isinstance(like_count, int) else 0,
                        "heat": heat if isinstance(heat, str) else str(heat or "")},
        }
        await self._emit_message(self._envelope(room_id, mapped))

    # ---- 契约信封与系统消息 ----

    def _envelope(self, room_id: str, mapped: dict) -> dict:
        """契约信封组装（全键；platform=meituan 如实标注）"""
        return {
            "contract_version": "1.0.0",
            "category": mapped["category"],
            "type": mapped["type"],
            "platform": self.platform,
            "room_id": room_id,
            "seq": mapped["seq"],
            "timestamp": mapped["timestamp"],
            "engine": self.engine_id,
            "protocol_version": PROTOCOL_VERSION,
            "payload": mapped["payload"],
        }

    async def _emit_system_status(self, room_id: str, detail: str) -> None:
        from danmaku_listener.contract import Category, SystemType
        from danmaku_listener.contract.models import Envelope, UnifiedMessage

        msg = UnifiedMessage(
            envelope=Envelope(
                category=Category.SYSTEM, type=SystemType.ENGINE_STATUS.value,
                platform=self.platform, room_id=room_id, seq=self.next_seq(room_id),
                timestamp=int(time.time()), engine=self.engine_id,
            ),
            payload={"type": "ENGINE_STATUS", "engine": self.engine_id, "detail": detail},
        )
        await self._emit_message(msg.to_wire())

    async def _emit_route_failed(self, room_id: str, reason_code: str,
                                 fix_hint: str, docs_anchor: str) -> None:
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
                platform=self.platform, room_id=room_id, seq=self.next_seq(room_id),
                timestamp=int(time.time()), engine=self.engine_id,
            ),
            payload=RouteFailedPayload(
                failure=FailureInfo(reason_code=reason_code, fix_hint=fix_hint,
                                    docs_anchor=docs_anchor)),
        )
        await self._emit_message(msg.to_wire())
