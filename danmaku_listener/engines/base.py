"""监听引擎抽象基类

定义所有监听引擎必须实现的接口，确保代理模式和浏览器模式的统一行为。

契约 v1 阶段 0 扩展（保持 start/stop/restart(room_id) 公开契约不变）：
- 每房间状态：单调 seq、live 在线状态、last-received 时间戳（崩溃恢复持久化钩子）
- GAP 补发与下播裁剪：停机/断线窗口按 LIVE_STATUS_CHANGE 裁剪，下播期间缺失不计 GAP
- SYSTEM_STATUS 工厂：心跳/GAP/NEEDS_LOGIN/路线失效/恢复统一经 SystemStatusFactory 构造
- restart(room_id) 语义：仅重启该房间的 asyncio 任务；引擎级重启另有显式方法
"""

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Coroutine, Dict, List, Optional

from loguru import logger

from danmaku_listener.bus.system_status import SystemStatusFactory
from danmaku_listener.contract.models import GapReason, UnifiedMessage
from danmaku_listener.managers.reconnect_manager import ReconnectManager
from danmaku_listener.persistence.room_state_store import RoomStateStore


class EngineStatus(str, Enum):
    """引擎状态枚举"""

    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    ERROR = "error"
    RECONNECTING = "reconnecting"


# 回调函数类型
MessageCallback = Callable[[Dict[str, Any]], Coroutine[Any, Any, None]]
ErrorCallback = Callable[[Exception], Coroutine[Any, Any, None]]


@dataclass
class RoomState:
    """每房间运行状态（契约 v1：seq 单调、live 状态、last-received 持久化）"""

    seq: int = 0
    live: Optional[bool] = None            # None=未知；True=开播 False=下播
    live_changed_at: Optional[int] = None  # 最近一次 live 状态转换时间戳
    last_received_ts: Optional[int] = None
    gap_pending_start: Optional[int] = None  # 断线窗口起始（收到首条消息时闭合）
    extra: Dict[str, Any] = field(default_factory=dict)


class BaseEngine(ABC):
    """监听引擎抽象基类

    所有具体的监听引擎（代理模式、浏览器模式）都必须继承此类并实现抽象方法。
    """

    def __init__(self, state_store: Optional[RoomStateStore] = None):
        """初始化引擎

        Args:
            state_store: 每房间状态持久化存储（契约 v1 崩溃恢复）；None=纯内存
        """
        self._status: EngineStatus = EngineStatus.STOPPED
        self._message_callbacks: List[MessageCallback] = []
        self._error_callbacks: List[ErrorCallback] = []
        self._reconnect_manager: Optional[ReconnectManager] = None
        self._rooms: Dict[str, RoomState] = {}
        self._state_store = state_store
        self._system_factory = SystemStatusFactory(
            platform=getattr(self, "platform", "unknown"),
            engine=self.engine_id,
        )

    # ---- 引擎标识（SYSTEM_STATUS 的 engine 字段来源；子类覆盖） ----

    @property
    def engine_id(self) -> str:
        """引擎标识，如 ``protocol:bilibili`` / ``proxy:douyin`` / ``fallback:generic``"""
        return f"engine:{self.__class__.__name__}"

    def validate_room_id(self, room_id: str) -> None:
        """房间参数预校验（add_room 时调用；不可解析抛 ValueError）

        默认不校验；平台引擎覆写以在添加时给出明确错误（而非静默退避循环）。
        """
        return None

    @property
    def status(self) -> EngineStatus:
        """获取当前状态"""
        return self._status

    # ---- 每房间状态与 seq ----

    def _room(self, room_id: str) -> RoomState:
        state = self._rooms.get(room_id)
        if state is None:
            state = RoomState()
            # 崩溃恢复：子类可覆盖 _load_persisted_state 提供上次会话的 seq/live 状态
            persisted = self._load_persisted_state(room_id)
            if persisted:
                state.extra.update(persisted)
                if isinstance(persisted.get("seq"), int) and persisted["seq"] >= 0:
                    state.seq = persisted["seq"]
                if isinstance(persisted.get("live"), (bool, type(None))):
                    state.live = persisted.get("live")
                if isinstance(persisted.get("live_changed_at"), int):
                    state.live_changed_at = persisted["live_changed_at"]
            self._rooms[room_id] = state
        return state

    def next_seq(self, room_id: str) -> int:
        """分配房间内下一个单调序号"""
        state = self._room(room_id)
        state.seq += 1
        return state.seq

    def current_seq(self, room_id: str) -> int:
        return self._room(room_id).seq

    # ---- 持久化钩子（默认内存态；子类覆盖为文件/磁盘实现以支持崩溃恢复） ----

    def _persist_state(self, room_id: str, state: RoomState) -> None:
        """持久化每房间状态（last-received 序号/时间戳、live 状态与转换时间）。

        有 RoomStateStore 时写入 JSON 快照（防抖+fail-open）；
        无存储实例时为纯内存（子类亦可另行覆盖为自定义实现）。
        """
        if self._state_store is not None:
            self._state_store.update(
                room_id,
                seq=state.seq,
                live=state.live,
                live_changed_at=state.live_changed_at,
                last_received_ts=state.last_received_ts,
            )
            self._state_store.flush()

    def _load_persisted_state(self, room_id: str) -> Optional[Dict[str, Any]]:
        """加载上次会话持久化的房间状态；默认无。返回 dict 含
        seq / live / live_changed_at / last_received_ts（均可缺省）。"""
        if self._state_store is None:
            return None
        return {
            k: self._state_store.get(room_id, k)
            for k in ("seq", "live", "live_changed_at", "last_received_ts")
            if self._state_store.get(room_id, k) is not None
        }

    # ---- live 状态与 GAP 裁剪 ----

    def mark_received(self, room_id: str, ts: Optional[int] = None) -> None:
        """记录最近收到消息的时刻（用于断线窗口计算与持久化）"""
        state = self._room(room_id)
        state.last_received_ts = ts if ts is not None else int(time.time())
        self._persist_state(room_id, state)

    def mark_live_change(self, room_id: str, live: bool, ts: Optional[int] = None) -> None:
        """记录直播状态转换（开播/下播）；下播即闭合未决缺口（下播期间缺失不计 GAP）"""
        state = self._room(room_id)
        ts = ts if ts is not None else int(time.time())
        changed = state.live != live
        state.live = live
        state.live_changed_at = ts
        if not live:
            state.gap_pending_start = None  # 下播裁剪：未决缺口作废
        self._persist_state(room_id, state)
        if changed:
            logger.debug(f"Room {room_id} live state -> {live}")

    def mark_gap_start(self, room_id: str, ts: Optional[int] = None) -> None:
        """断线/停机开始：记录未决缺口起点（仅开播中才记录，下播不计 GAP）"""
        state = self._room(room_id)
        if state.live is False:
            return
        if state.gap_pending_start is None:
            state.gap_pending_start = ts if ts is not None else int(time.time())
            self._persist_state(room_id, state)

    def build_gap_message(
        self,
        room_id: str,
        reason: GapReason,
        window_end: Optional[int] = None,
        approx: bool = False,
        dropped_estimate: Optional[int] = None,
    ) -> Optional[UnifiedMessage]:
        """构造 GAP 消息并闭合未决缺口

        依赖 live 状态裁剪窗口起点：若停机窗口横跨下播时段，起点取
        max(gap_start, live_changed_at)（再开播后的段）。live 状态未知
        （崩溃恢复且无法校准）时标记 approx=True。
        """
        state = self._room(room_id)
        if state.gap_pending_start is None and reason not in (GapReason.BACKPRESSURE,):
            return None  # 无未决缺口
        end = window_end if window_end is not None else int(time.time())
        start = state.gap_pending_start or end
        if state.live is False and reason not in (GapReason.BACKPRESSURE,):
            # 下播中：无缺口语义
            state.gap_pending_start = None
            self._persist_state(room_id, state)
            return None
        if state.live is None and state.live_changed_at is not None:
            approx = True
        if state.live is True and state.live_changed_at and state.live_changed_at > start:
            start = state.live_changed_at  # 停机横跨下播：起点裁剪到再开播
        state.gap_pending_start = None
        self._persist_state(room_id, state)
        return self._system_factory.gap(
            room_id=room_id,
            seq=self.next_seq(room_id),
            ts=end,
            window_start=start,
            window_end=end,
            reason=reason,
            approx=approx or state.live is None,
            dropped_estimate=dropped_estimate,
        )

    # ---- 抽象接口（公开契约不变） ----

    @abstractmethod
    async def start(self, room_id: str) -> None:
        """启动对指定房间的监听

        Args:
            room_id: 房间 ID
        """
        pass

    @abstractmethod
    async def stop(self, room_id: str) -> None:
        """停止对指定房间的监听

        Args:
            room_id: 房间 ID
        """
        pass

    @abstractmethod
    async def restart(self, room_id: str) -> None:
        """重启对指定房间的监听

        语义：仅重启该房间的 asyncio 任务；引擎级重启另有显式方法。

        Args:
            room_id: 房间 ID
        """
        pass

    # ---- 回调注册 ----

    def on_message(self, callback: MessageCallback) -> None:
        """注册消息回调

        Args:
            callback: 异步回调函数
        """
        self._message_callbacks.append(callback)

    def on_error(self, callback: ErrorCallback) -> None:
        """注册错误回调

        Args:
            callback: 异步回调函数
        """
        self._error_callbacks.append(callback)

    async def _emit_message(self, data: Dict[str, Any]) -> None:
        """触发消息事件

        Args:
            data: 消息数据字典
        """
        for callback in self._message_callbacks:
            try:
                await callback(data)
            except Exception as e:
                # 不中断其他回调的执行
                pass

    async def _emit_system(self, message: UnifiedMessage) -> None:
        """以线格式发射 SYSTEM_STATUS 消息（type 字段与旧 ad-hoc 事件兼容）"""
        await self._emit_message(message.to_wire())

    async def _emit_error(self, error: Exception) -> None:
        """触发错误事件

        Args:
            error: 异常对象
        """
        for callback in self._error_callbacks:
            try:
                await callback(error)
            except Exception:
                pass

    def _set_status(self, status: EngineStatus) -> None:
        """更新状态

        Args:
            status: 新状态
        """
        self._status = status

    def set_reconnect_manager(self, manager: ReconnectManager) -> None:
        """设置重连管理器

        Args:
            manager: ReconnectManager 实例
        """
        self._reconnect_manager = manager
        logger.debug(f"Set reconnect manager for {self.__class__.__name__}")

    async def _handle_error_with_reconnect(self, room_id: str, error: Exception) -> bool:
        """处理错误并尝试重连

        如果设置了重连管理器，尝试通过 restart 重连。
        重连成功后发布 reconnect_success 消息。
        无重连管理器或重连失败时触发错误回调。

        契约 v1：断线开始时记录未决缺口起点（开播中），供恢复后 GAP 补发。

        Args:
            room_id: 房间 ID
            error: 原始错误

        Returns:
            True 如果重连成功，False 如果重连失败或无重连管理器
        """
        self.mark_gap_start(room_id)
        if self._reconnect_manager is None:
            # 无重连管理器，直接触发错误回调
            await self._emit_error(error)
            return False

        # 尝试重连
        async def reconnect_callback() -> bool:
            try:
                await self.restart(room_id)
                return True
            except Exception as e:
                logger.warning(f"Reconnect restart failed for room {room_id}: {e}")
                return False

        success = await self._reconnect_manager.reconnect(reconnect_callback, room_id)

        if success:
            # 重连成功，发布状态消息
            await self._emit_message({
                "type": "reconnect_success",
                "room_id": room_id,
            })
            return True
        else:
            # 重连失败，触发错误回调
            await self._emit_error(error)
            return False
