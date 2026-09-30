"""DanmakuBridge — 前后端桥接层

持有 DanmakuListener 实例，桥接 EventBus 事件到 WebSocket 客户端。
"""

import asyncio
import json
import time
from typing import Any, Dict, List, Optional, Set

from aiohttp import web
from loguru import logger

from danmaku_listener.contract import Category, SystemType
from danmaku_listener.contract.legacy import legacy_to_unified
from danmaku_listener.contract.models import Envelope, FailureInfo, RouteFailedPayload, UnifiedMessage


class RoomError(Exception):
    """房间操作错误（带 HTTP 状态码）"""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


class DanmakuBridge:
    """前后端桥接层

    职责：
    - 持有 DanmakuListener 实例
    - 管理 WebSocket 客户端连接
    - 广播弹幕消息到前端
    - 关键词过滤
    - 系统代理控制
    """

    def __init__(self) -> None:
        """初始化桥接器"""
        # DanmakuListener 实例（延迟初始化，避免 import 循环）
        self._listener: Any = None

        # WebSocket 客户端集合
        self._ws_clients: Set[web.WebSocketResponse] = set()

        # 房间状态跟踪
        self._rooms: Dict[str, Dict[str, Any]] = {}

        # 关键词屏蔽
        self._blocked_keywords: List[str] = []
        self._keyword_filter_enabled: bool = True
        self._load_blocked_keywords()

        # 契约 v1（阶段 W1）：广播单源——PushServer 实例由 app 注入；
        # 客户端管理与广播全部委托 push（web /ws 与 PushServer 共用实现）
        self._push: Optional[Any] = None

        # 每房间契约 seq 计数器与消息计数（/api/status 快照数据源）
        self._room_seq: Dict[str, int] = {}
        self._message_count = 0

        # registry 引擎实例缓存（S1：协议每平台一实例；视频号每房间一实例）
        self._engine_instances: Dict[str, Any] = {}

        # 设置回调
        self._setup_callbacks()

    def set_push_server(self, push: Any) -> None:
        """注入 PushServer（广播单源；客户端管理委托给它）"""
        self._push = push

    async def _bilibili_login_then_start(self, room_key: str, room_id: str, engine: Any, state_path: str) -> None:
        """bilibili 受控登录闭环：弹窗口→用户登录→cookie 保存→自动开始监听

        进度经 SYSTEM_STATUS 推送（前端可见）；登录超时/关闭则降级游客模式。
        """
        from danmaku_listener.engines.bilibili_login import run_login_flow

        async def status_cb(status: str) -> None:
            await self._broadcast_system_status(room_key, f"bilibili 登录流程: {status}")

        await self._broadcast_system_status(room_key, "登录窗口已打开，请在浏览器中登录 B站账号")
        result = await run_login_flow(
            state_path=state_path, headless=False,
            on_status=lambda s: status_cb(s),
        )
        # 重建 fetcher（带新登录 cookie）
        engine._danmu_info = type(engine._danmu_info)(cookie_file=state_path)

        if result.get("status") != "ok":
            await self._broadcast_system_status(room_key, "登录未完成——已降级游客模式（弹幕受限）")
            return

        await self._broadcast_system_status(room_key, "登录成功——开始监听")
        try:
            await engine.start(room_id)
            self._rooms[room_key]["status"] = "running"
        except Exception as e:
            logger.error(f"[bilibili] start after login failed: {e}")
            await self._broadcast_system_status(room_key, f"登录成功但启动失败: {e}")

    async def _kuaishou_login_then_start(self, room_key: str, room_id: str, engine: Any, state_path: str) -> None:
        """kuaishou 受控登录闭环：弹窗口→用户扫码→cookie 保存→自动开始监听

        快手 web 直播间已强制游客登录（2026-09 实测）；登录态浏览器承担
        token 获取（engine._fetch_context_via_browser），协议连接保持直连。
        """
        from danmaku_listener.engines.kuaishou_login import run_login_flow

        async def status_cb(status: str) -> None:
            await self._broadcast_system_status(room_key, f"kuaishou 登录流程: {status}")

        await self._broadcast_system_status(room_key, "登录窗口已打开，请在浏览器中登录快手账号")
        result = await run_login_flow(
            room_id=room_id, state_path=state_path, headless=False,
            on_status=lambda s: status_cb(s),
        )
        # 引擎 cookie_file 指向新登录态（token 获取走登录态浏览器）
        engine._cookie_file = state_path

        if result.get("status") != "ok":
            await self._broadcast_system_status(room_key, "登录未完成——快手监听需要登录态，请重试")
            self._rooms[room_key]["status"] = "login_failed"
            return

        await self._broadcast_system_status(room_key, "登录成功——开始监听")
        try:
            await engine.start(room_id)
            self._rooms[room_key]["status"] = "running"
        except Exception as e:
            logger.error(f"[kuaishou] start after login failed: {e}")
            await self._broadcast_system_status(room_key, f"登录成功但启动失败: {e}")

    async def _broadcast_system_status(self, room_key: str, detail: str) -> None:
        """ENGINE_STATUS 快捷广播（平台/房间从 room_key 解析）"""
        import time as _time

        from danmaku_listener.contract.models import Envelope, UnifiedMessage

        platform, _, room_id = room_key.partition(":")
        seq = self._next_seq(room_key)
        msg = UnifiedMessage(
            envelope=Envelope(
                category=Category.SYSTEM, type=SystemType.ENGINE_STATUS.value,
                platform=platform, room_id=room_id,
                seq=seq, timestamp=int(_time.time()), engine="bridge",
            ),
            payload={"type": "ENGINE_STATUS", "engine": "bridge", "detail": detail},
        )
        await self._broadcast(msg.to_wire())

    async def _on_engine_message(self, wire: dict) -> None:
        """registry 引擎消息回调（直通归一：仅 to_wire() 形状直通，S1.4）

        - 关键词过滤作用于 DANMU 载荷（行为保持，AC-007/BR-005）
        - 不含完整信封形状的 ad-hoc dict 包装为 ENGINE_STATUS（防畸形污染）
        """
        is_contract = (
            isinstance(wire, dict)
            and "category" in wire and "type" in wire and "payload" in wire
            and "platform" in wire and "room_id" in wire and "seq" in wire
        )
        if is_contract:
            if (
                self._keyword_filter_enabled
                and wire.get("type") == "DANMU"
                and any(kw in (wire["payload"].get("content") or "") for kw in self._blocked_keywords)
            ):
                logger.debug(f"Danmaku filtered by keyword: {wire['payload'].get('content', '')[:30]}")
                return
            self._message_count += 1
            await self._broadcast(wire)
            return

        # ad-hoc/畸形 dict → ENGINE_STATUS 包装（round3 L-2：免检直通不存在）
        room_key = next(iter(self._rooms), "unknown:0")
        platform = room_key.split(":")[0]
        msg = UnifiedMessage(
            envelope=Envelope(
                category=Category.SYSTEM, type=SystemType.ENGINE_STATUS.value,
                platform=platform, room_id=room_key.split(":")[-1],
                seq=self._next_seq(room_key), timestamp=int(time.time()), engine="bridge",
            ),
            payload={"type": "ENGINE_STATUS", "engine": "bridge",
                     "detail": json.dumps(wire, ensure_ascii=False, default=str)[:300]},
        )
        await self._broadcast(msg.to_wire())

    def _next_seq(self, room_key: str) -> int:
        self._room_seq[room_key] = self._room_seq.get(room_key, 0) + 1
        return self._room_seq[room_key]

    def _setup_callbacks(self) -> None:
        """注册 DanmakuListener 事件回调

        回调在 listener.start() 时才真正订阅到 EventBus。
        这里通过 listener.on_danmaku() 等方法注册回调函数。
        """
        try:
            self.listener.on_danmaku(self._on_danmaku_handler)
            self.listener.on_error(self._on_error_handler)
            self.listener.on_reconnect(self._on_reconnect_handler)
            self.listener.on_status_change(self._on_status_change_handler)
        except Exception as e:
            logger.warning(f"Failed to register callbacks (listener not ready): {e}")

    @property
    def listener(self) -> Any:
        """获取 DanmakuListener 实例（延迟初始化）"""
        if self._listener is None:
            from danmaku_listener import DanmakuListener
            self._listener = DanmakuListener()
        return self._listener

    async def add_ws_client(self, ws: web.WebSocketResponse) -> None:
        """添加 WebSocket 客户端"""
        self._ws_clients.add(ws)
        logger.debug(f"WS client added, total: {len(self._ws_clients)}")

    async def remove_ws_client(self, ws: web.WebSocketResponse) -> None:
        """移除 WebSocket 客户端"""
        self._ws_clients.discard(ws)
        logger.debug(f"WS client removed, total: {len(self._ws_clients)}")

    async def _broadcast(self, message: dict) -> None:
        """广播消息到所有 WebSocket 客户端（单源：委托 PushServer.broadcast）

        message 必须已是契约 v1 线格式（各 handler 负责构造）。
        无 push 注入时退回本地客户端集合（兼容旧测试）。
        """
        if self._push is not None:
            await self._push.broadcast(message)
            return

        msg_str = json.dumps(message, ensure_ascii=False)
        dead_clients: Set[web.WebSocketResponse] = set()
        for client in self._ws_clients:
            try:
                await client.send_str(msg_str)
            except Exception:
                dead_clients.add(client)
        if dead_clients:
            self._ws_clients -= dead_clients
            logger.debug(f"Removed {len(dead_clients)} dead WS clients")

    def _load_blocked_keywords(self) -> None:
        """从文件加载屏蔽关键词"""
        import os
        kw_file = os.path.join(os.path.dirname(__file__), "blocked_keywords.json")
        try:
            with open(kw_file, "r", encoding="utf-8") as f:
                self._blocked_keywords = json.load(f)
            logger.info(f"Loaded {len(self._blocked_keywords)} blocked keywords")
        except (FileNotFoundError, json.JSONDecodeError):
            self._blocked_keywords = []
            logger.info("No blocked keywords loaded (file not found or empty)")

    def _save_blocked_keywords(self) -> None:
        """保存屏蔽关键词到文件"""
        import os
        kw_file = os.path.join(os.path.dirname(__file__), "blocked_keywords.json")
        with open(kw_file, "w", encoding="utf-8") as f:
            json.dump(self._blocked_keywords, f, indent=2, ensure_ascii=False)

    def get_keywords(self) -> dict:
        """获取关键词列表和过滤状态 → AC-007"""
        return {
            "keywords": list(self._blocked_keywords),
            "enabled": self._keyword_filter_enabled,
        }

    def add_keyword(self, keyword: str) -> dict:
        """添加屏蔽关键词 → AC-007

        Args:
            keyword: 关键词文本

        Returns:
            操作结果字典

        Raises:
            ValueError: 关键词长度超过 50 字符
        """
        if len(keyword) > 50:
            raise ValueError("Keyword length exceeds 50 characters")

        if keyword not in self._blocked_keywords:
            self._blocked_keywords.append(keyword)
            self._save_blocked_keywords()
            logger.info(f"Keyword added: {keyword}")

        return {"success": True, "keywords": list(self._blocked_keywords)}

    def remove_keyword(self, keyword: str) -> dict:
        """删除屏蔽关键词 → AC-018

        Args:
            keyword: 要删除的关键词

        Returns:
            操作结果字典
        """
        if keyword in self._blocked_keywords:
            self._blocked_keywords.remove(keyword)
            self._save_blocked_keywords()
            logger.info(f"Keyword removed: {keyword}")

        return {"success": True, "keywords": list(self._blocked_keywords)}

    def toggle_keyword_filter(self, enabled: bool) -> dict:
        """开启/关闭关键词过滤 → AC-018

        Args:
            enabled: 是否启用过滤

        Returns:
            操作结果字典
        """
        self._keyword_filter_enabled = enabled
        logger.info(f"Keyword filter {'enabled' if enabled else 'disabled'}")
        return {"success": True, "enabled": self._keyword_filter_enabled}

    def get_status(self) -> dict:
        """获取系统整体状态（含引擎快照——数据源为 bridge 实际实例）"""
        return {
            "backend_connected": True,
            "rooms_count": len(self._rooms),
            "rooms": dict(self._rooms),
            "room_seq": dict(self._room_seq),
            "message_count": self._message_count,
            "push_clients": len(self._push._clients) if self._push is not None else len(self._ws_clients),
            "keyword_filter": {
                "enabled": self._keyword_filter_enabled,
                "count": len(self._blocked_keywords),
            },
        }

    async def add_room(self, room_spec: str) -> dict:
        """添加房间并启动监听（契约 v1：registry 引擎路由全切换）

        Returns:
            操作结果字典，包含 room 信息与引擎可用性 warnings

        Raises:
            RoomError: 格式错误(400)、平台不支持(400)、重复(409)、
                       douyin 单进程 501、启动失败(500)
        """
        from danmaku_listener.utils.platform_parser import parse_room_spec
        from danmaku_listener.engines.registry import build_engine, PLATFORM_WARNINGS

        # 1. 校验格式
        try:
            spec = parse_room_spec(room_spec)
        except ValueError as e:
            raise RoomError(str(e), status=400)

        # 2. 检查重复 → AC-013
        room_key = f"{spec.platform}:{spec.room_id}"
        if room_key in self._rooms:
            raise RoomError(f"Room already exists: {room_key}", status=409)

        # 3. douyin：原生 Web WS 直连（2026-09-29 registry 切换）——无特判，
        #    前置条件（无外部程序）经 PLATFORM_WARNINGS 透出

        # 4. 构造/复用 registry 引擎实例（粒度：协议每平台一实例；视频号每房间一实例）
        engine = self._engine_instances.get(spec.platform)
        if engine is None or spec.platform == "wechat_channels":
            engine = build_engine(spec.platform)
            engine.on_message(self._on_engine_message)
            self._engine_instances[spec.platform] = engine

        # 4.5 平台自动登录闭环（用户裁定：cookie 获取自动化）
        # bilibili：游客被限流，完整弹幕流需登录态
        if spec.platform == "bilibili":
            from danmaku_listener.config.settings import get_settings as _gs
            from danmaku_listener.engines.bilibili_login import has_login_cookie
            state_path = _gs().bilibili_cookie_file.replace(
                "bilibili_cookies.txt", "bilibili_storage_state.json")
            if not has_login_cookie(state_path):
                self._rooms[room_key] = {
                    "platform": spec.platform,
                    "room_id": spec.room_id,
                    "status": "login_required",
                    "engine_type": "protocol:bilibili",
                }
                asyncio.create_task(self._bilibili_login_then_start(
                    room_key, spec.room_id, engine, state_path))
                return {
                    "success": True,
                    "status": "login_required",
                    "room": self._rooms[room_key],
                    "message": "需要登录 B站账号（完整弹幕流）：登录窗口已打开，"
                               "请在弹出的浏览器中扫码/登录；登录后自动开始监听",
                }

        # kuaishou：游客已被强制登录（2026-09 实测），登录态浏览器承担 token 获取
        if spec.platform == "kuaishou":
            from danmaku_listener.engines.kuaishou_login import has_login_cookie
            state_path = getattr(engine, "_cookie_file", None) or \
                "cookie/kuaishou_storage_state.json"
            if not has_login_cookie(state_path):
                self._rooms[room_key] = {
                    "platform": spec.platform,
                    "room_id": spec.room_id,
                    "status": "login_required",
                    "engine_type": "protocol:kuaishou",
                }
                asyncio.create_task(self._kuaishou_login_then_start(
                    room_key, spec.room_id, engine, state_path))
                return {
                    "success": True,
                    "status": "login_required",
                    "room": self._rooms[room_key],
                    "message": "需要登录快手账号（web 直播间已强制登录）：登录窗口已打开，"
                               "请在弹出的浏览器中扫码/登录；登录后自动开始监听",
                }

        # 4.8 房间参数预校验（引擎可解析性——错误前缀立即 400 而非静默循环）
        if hasattr(engine, "validate_room_id"):
            try:
                engine.validate_room_id(spec.room_id)
            except ValueError as e:
                raise RoomError(str(e), status=400)

        # 5. 启动该房间
        try:
            await engine.start(spec.room_id)
        except RoomError:
            raise
        except Exception as e:
            raise RoomError(str(e), status=500)

        # 6. 记录房间状态
        self._rooms[room_key] = {
            "platform": spec.platform,
            "room_id": spec.room_id,
            "status": "running",
            "engine_type": engine.engine_id,
        }

        logger.info(f"*Room added: {room_key} ({engine.engine_id})")

        result = {"success": True, "room": self._rooms[room_key]}
        if spec.platform in PLATFORM_WARNINGS:
            result["warnings"] = [PLATFORM_WARNINGS[spec.platform]]
        return result

    async def remove_room(self, platform: str, room_id: str) -> dict:
        """停止并移除房间

        Args:
            platform: 平台标识
            room_id: 房间 ID

        Returns:
            操作结果字典

        Raises:
            RoomError: 房间不存在(404)
        """
        room_key = f"{platform}:{room_id}"

        if room_key not in self._rooms:
            raise RoomError(f"Room not found: {room_key}", status=404)

        # 从状态中移除
        self._rooms.pop(room_key)

        # 停止对应 registry 引擎实例的该房间（视频号随房间销毁实例）
        engine = self._engine_instances.get(platform)
        try:
            if engine is not None:
                await engine.stop(room_id)
            if platform == "wechat_channels":
                self._engine_instances.pop(platform, None)
        except Exception as e:
            logger.error(f"Error stopping engine for {room_key}: {e}")

        logger.info(f"Room removed: {room_key}")

        return {
            "success": True,
            "message": f"Room {room_key} stopped and removed",
        }

    async def stop_all(self) -> dict:
        """停止所有房间监听并恢复系统代理 → AC-006, BR-006

        Returns:
            操作结果字典
        """
        if not self._rooms:
            return {"success": True, "message": "No rooms to stop"}

        room_count = len(self._rooms)
        rooms_snapshot = dict(self._rooms)
        self._rooms.clear()

        # 停止全部 registry 引擎实例的对应房间（视频号随房间销毁实例）
        for room_key in rooms_snapshot:
            platform, _, room_id = room_key.partition(":")
            engine = self._engine_instances.get(platform)
            try:
                if engine is not None:
                    await engine.stop(room_id)
                if platform == "wechat_channels":
                    self._engine_instances.pop(platform, None)
            except Exception as e:
                logger.error(f"Error stopping {room_key}: {e}")

        logger.info(f"All rooms stopped ({room_count})")

        return {
            "success": True,
            "message": f"All rooms stopped ({room_count}), proxy restored",
        }

    async def _on_danmaku_handler(self, msg: Any) -> None:
        """弹幕事件回调（契约 v1：旧 DanmakuMessage → UnifiedMessage 线格式）

        关键词过滤（仅普通弹幕）→ BR-005 保持。
        """
        if self._keyword_filter_enabled and msg.message_type == "normal":
            if any(kw in msg.content for kw in self._blocked_keywords):
                logger.debug(f"Danmaku filtered by keyword: {msg.content[:30]}")
                return

        room_key = f"{msg.platform}:{msg.room_id}"
        unified = legacy_to_unified(msg, seq=self._next_seq(room_key), engine=f"legacy:{msg.platform}")
        self._message_count += 1
        await self._broadcast(unified.to_wire())

    async def _on_error_handler(self, error: Exception) -> None:
        """错误事件（契约 v1：ROUTE_FAILED 三段式）"""
        room_key = next(iter(self._rooms), "unknown:0")
        platform, room_id = room_key.split(":", 1)
        msg = UnifiedMessage(
            envelope=Envelope(
                category=Category.SYSTEM, type=SystemType.ROUTE_FAILED.value,
                platform=platform, room_id=room_id,
                seq=self._next_seq(room_key), timestamp=int(time.time()), engine="bridge",
            ),
            payload=RouteFailedPayload(failure=FailureInfo(
                reason_code="internal.error",
                fix_hint=(str(error)[:200] or "查看服务日志"),
                docs_anchor="docs/ops/compliance-review.md",
            )),
        )
        await self._broadcast(msg.to_wire())

    async def _on_reconnect_handler(self, room_id: str) -> None:
        """重连事件（契约 v1：ENGINE_STATUS/detail=reconnecting）→ AC-012"""
        room_key = next((k for k in self._rooms if k.endswith(f":{room_id}")), f"unknown:{room_id}")
        platform = room_key.split(":")[0]
        msg = UnifiedMessage(
            envelope=Envelope(
                category=Category.SYSTEM, type=SystemType.ENGINE_STATUS.value,
                platform=platform, room_id=room_id,
                seq=self._next_seq(room_key), timestamp=int(time.time()), engine="bridge",
            ),
            payload={"type": "ENGINE_STATUS", "engine": "bridge", "detail": "reconnecting"},
        )
        await self._broadcast(msg.to_wire())

    async def _on_status_change_handler(self, room_id: str, status: str) -> None:
        """状态变化（契约 v1：ENGINE_STATUS/detail 房间状态）"""
        room_key = next((k for k in self._rooms if k.endswith(f":{room_id}")), f"douyin:{room_id}")
        platform = room_key.split(":")[0]
        if room_key in self._rooms:
            self._rooms[room_key]["status"] = status
        msg = UnifiedMessage(
            envelope=Envelope(
                category=Category.SYSTEM, type=SystemType.ENGINE_STATUS.value,
                platform=platform, room_id=room_id,
                seq=self._next_seq(room_key), timestamp=int(time.time()), engine="bridge",
            ),
            payload={"type": "ENGINE_STATUS", "engine": "bridge", "detail": f"room {room_id}: {status}"},
        )
        await self._broadcast(msg.to_wire())

    async def shutdown(self) -> None:
        """关闭桥接器，释放资源"""
        if self._listener is not None:
            try:
                await self._listener.stop()
            except Exception as e:
                logger.error(f"Error stopping listener: {e}")
            self._listener = None

        self._push = None
        # 关闭所有 WebSocket 客户端
        for client in list(self._ws_clients):
            try:
                await client.close()
            except Exception:
                pass
        self._ws_clients.clear()
