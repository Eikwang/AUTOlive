# -*- coding: utf-8 -*-
"""
DanmakuListener WS 消费者与 serve 托管（DanmakuListener 整合计划 G1/G2/G3/G6）

架构（义务块"整合形态/消费者并发与队列/双通道"）：

    DanmakuListener serve（独立进程，ManagedProcess 托管 + TCP 探活）
      └─ ws://127.0.0.1:<port>/ws（Bearer token，loopback）
           └─ _ConsumerLoop（独立线程 asyncio 事件循环）
                ├─ system 快路径：HEARTBEAT 计时 / GAP / NEEDS_LOGIN / ENGINE_STATUS /
                │   ROOM_STATUS / BACKPRESSURE / RECOVERED / ROUTE_FAILED（不进主队列）
                └─ business → _WORK_QUEUE（有界 500，线程安全）→ _WorkerThread
                     └─ 映射进 my_handle.process_data（对话链复用，不重建）

关键语义（对应义务块条款）：
- token 生命周期：托管层生成（secrets.token_urlsafe≥32B），每次重拉轮换；
  消费者每次重连前读当前 token；401 触发一次刷新重试；轮换即世代信号。
- 世代重置：token 变化 → seq 期待与 my_handle 去重窗口清空（重拉=新 serve 进程）。
- 重拉归口：消费者只上报连接失败，重拉裁决唯一归口 ManagedProcess（T-3 计数器）。
- 优先级表：服务端丢弃序 = 消费端溢出序 = 队列路由分类（LIKE/ENTER_ROOM/ROOM_STATS 走计数快路径）。
- DANMU 聚合限速：窗口合并进对话链，GIFT/SUPER_CHAT FIFO（SUPER_CHAT 可插队）。
"""
from __future__ import annotations

import asyncio
import json
import os
import queue
from collections import deque
import secrets
import socket
import sys
import threading
import time
from typing import Any, Callable, Optional

import websockets
from websockets.asyncio.client import connect as ws_connect  # S-4：显式 asyncio.client 实现路径


_DC_FIRST_LOG = {'done': False}
def dc_debug_first_log() -> bool:
    if _DC_FIRST_LOG['done']:
        return False
    _DC_FIRST_LOG['done'] = True
    return True

from utils.my_log import logger

# ---------- 运行时阈值（义务块：首版命名常量不进配置，调参见 utils/danmaku_consumer.py 本节） ----------
HEARTBEAT_INTERVAL_S = 10        # 组件心跳周期（契约 engine_heartbeat_seconds 同值）
HEARTBEAT_LOST_PERIODS = 3       # 3 周期未收 → 失联告警
ZOMBIE_TIMEOUT_S = 60            # 失联持续 60s → 上报托管判僵死（重拉由 ManagedProcess 裁决）
SEQ_WAIT_TIMEOUT_S = 10          # seq 跳跃等待 GAP 超时（1 个心跳周期）后 approx 降级放行
SEQ_BUFFER_MAX = 1000            # seq 等待缓冲上限（条/房间）
QUEUE_MAX = 500                  # business 主队列上限
RECONNECT_BACKOFF_MIN_S = 1.0    # 重连退避下限
RECONNECT_BACKOFF_MAX_S = 30.0   # 重连退避上限
CONNECT_FAIL_HANDOFF = 3         # 连续 3 次连接失败移交托管层（与防护计数对齐）
DANMU_AGG_WINDOW_S = 5.0         # DANMU 聚合时间窗
DANMU_AGG_MAX = 5                # 聚合条数上限（窗内达到即合并下发）
CONTRACT_VERSION_MAJOR = 1       # 兼容断言：1.x 接受，2.x 拒绝

# 溢出丢弃优先级（数值小先丢；与组件 bus_drop_order 及契约对齐——双端一致性义务）
_DROP_ORDER = {"LIKE": 0, "ENTER_ROOM": 1, "DANMU": 2, "SOCIAL": 3, "ROOM_STATS": 4, "GIFT": 5, "SUPER_CHAT": 6}
# 计数快路径集合：不进主队列、不进对话链（B-2 路由分类）
_FASTPATH_BUSINESS = {"ENTER_ROOM", "ROOM_STATS"}  # LIKE 移出：2026-10-03 用户需求"监听到谁点赞"——点赞走对话链聚合（_LikeAggregator）


# ---------- 全局单例桥（前端组件轮询用；main.py 挂钩创建时注入） ----------
_SERVICE_INSTANCE: Optional["DanmakuListenerService"] = None


def set_service_instance(svc: Optional["DanmakuListenerService"]) -> None:
    global _SERVICE_INSTANCE
    _SERVICE_INSTANCE = svc


def get_service_instance() -> Optional["DanmakuListenerService"]:
    return _SERVICE_INSTANCE



def _tcp_probe(mproc) -> bool:
    """ManagedProcess health_probe：TCP 连通即视为健康（组件 serve 无 HTTP 健康端点）。"""
    host = "127.0.0.1"
    try:
        with socket.create_connection((host, mproc.port), timeout=3):
            return True
    except OSError:
        return False


def _write_serve_toml(path: str, section: dict) -> None:
    """把 AUTOlive config 节映射为组件 serve 的 TOML 配置文件。"""
    lines = [
        f'ws_port = {int(section["ws_port"])}',
        f'ws_bind = "{section["ws_bind"]}"',
    ]
    if section.get("token_injection") == "file" and section.get("token_file_path"):
        lines.append(f'ws_token_file = "{section["token_file_path"]}"')
    if section.get("cookie_dir"):
        # 受控页面引擎 cookie/profile 目录（空则组件默认 ./cookie）——
        # 可指向共享登录态目录，脚本侧扫码后系统通道免登录
        lines.append(f'cookie_dir = "{section["cookie_dir"]}"')
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")


class DanmakuListenerService:
    """DanmakuListener serve 托管 + WS 消费者生命周期（挂载 main.py 启停）。"""

    def __init__(self, config_getter: Callable[[], dict]):
        self._cfg = config_getter
        self._mproc = None
        self._consumer: Optional[_ConsumerLoop] = None
        self._consumer_thread: Optional[threading.Thread] = None
        self._toml_path = os.path.join("config", "danmaku_serve.toml")
        # token 状态：托管层唯一写方；消费者每次重连前读取（A-1 生命周期语义）
        self._token_lock = threading.Lock()
        self._token: str = ""
        self._token_generation = 0
        self._state_cb: Optional[Callable[[str], None]] = None
        # G4：system 事件环（前端 timer 轮询消费；seen 指针防重复通知）
        self.events: deque = deque(maxlen=100)
        self._event_seq = 0
        # G5：NEEDS_LOGIN 队列（多平台逐个呈现）+ 当前呈现态
        self.login_queue: list = []
        self.login_active: Optional[dict] = None

    # ---------- G4/G5 前端数据面 ----------

    def push_event(self, kind: str, data: dict) -> None:
        """system/business 状态事件入环（前端轮询消费，超限自动淘汰最旧）。"""
        self._event_seq += 1
        self.events.append({"seq": self._event_seq, "ts": time.time(), "kind": kind, "data": data})

    def events_since(self, last_seq: int) -> list:
        """返回 seq 大于 last_seq 的事件（前端增量拉取）。"""
        return [e for e in self.events if e["seq"] > last_seq]

    def submit_login_request(self, payload: dict) -> None:
        """NEEDS_LOGIN 入队（多平台队列呈现）；首个立即激活。"""
        item = {
            "platform": str((payload.get("failure") or {}).get("reason_code", "unknown")).split(".")[0],
            "reason_code": (payload.get("failure") or {}).get("reason_code", ""),
            "fix_hint": (payload.get("failure") or {}).get("fix_hint", ""),
            "docs_anchor": (payload.get("failure") or {}).get("docs_anchor", ""),
            "interactive": payload.get("interactive_login") or {},
            "ts": time.time(),
        }
        self.login_queue.append(item)
        if self.login_active is None:
            self.login_active = self.login_queue.pop(0)
        logger.info(f"[danmaku_listener] 登录请求入队（{item['platform']}，队列 {len(self.login_queue)}）")

    def ack_login_done(self) -> None:
        """UI 确认当前登录请求处理完毕（自动恢复或手动关闭）→ 弹出下一个。"""
        self.login_active = None
        if self.login_queue:
            self.login_active = self.login_queue.pop(0)

    # ---------- token / 世代 ----------

    def _rotate_token(self) -> None:
        """生成新 token 并递增世代（托管层重拉时调用；轮换即 serve 世代信号，A-4）。"""
        with self._token_lock:
            self._token = secrets.token_urlsafe(32)  # S-3：≥32 字节熵
            self._token_generation += 1

    def current_token(self) -> str:
        with self._token_lock:
            return self._token

    def generation(self) -> int:
        with self._token_lock:
            return self._token_generation

    # ---------- 配置 ----------

    def _section(self) -> dict:
        cfg = dict(self._cfg())
        return cfg

    def enabled(self) -> bool:
        return bool(self._section().get("enabled", False))

    def _room_id(self) -> str:
        sec = self._section()
        return str(sec.get("room_id") or "")

    # ---------- 生命周期 ----------

    def start(self, state_cb: Optional[Callable[[str], None]] = None) -> bool:
        """拉起 serve 并启动消费者。返回 False 表示被禁用或托管拉起失败。"""
        sec = self._section()
        if not sec.get("enabled"):
            logger.info("[danmaku_listener] enabled=false，跳过托管拉起")
            return False
        self._state_cb = state_cb
        self._rotate_token()
        os.makedirs(os.path.dirname(self._toml_path), exist_ok=True)
        _write_serve_toml(self._toml_path, sec)

        comp_root = str(sec.get("component_root") or "danmaku_listener")
        fixtures = os.path.join(comp_root, "fixtures", "demo.jsonl")
        replay = fixtures if os.path.isfile(fixtures) else None
        if replay is None:
            logger.warning("[danmaku_listener] 未找到 fixtures/demo.jsonl，serve 将空转（无消息源）")
        command = [
            sys.executable, "-m", "danmaku_listener", "serve",
            "--config", self._toml_path,
        ]
        if replay:
            command += ["--replay", replay]
        env = dict(os.environ)
        env["DANMAKU_TOKEN"] = self.current_token()   # token 注入（env 方式）
        env["PYTHONUTF8"] = "1"                        # C-2：子进程 env 契约（组件 pitfall）
        env["PYTHONPATH"] = comp_root + os.pathsep + env.get("PYTHONPATH", "")

        from utils.service_orchestrator import ManagedProcess
        self._mproc = ManagedProcess(
            name="danmaku_listener",
            command=command,
            cwd=".",
            env=env,
            port=int(sec.get("ws_port", 8765)),
            policies={"start_ready_timeout_s": 30, "health_poll_interval_s": 10},
            health_probe=_tcp_probe,
            on_state_change=self._on_serve_state,
        )
        if not self._mproc.start():
            logger.error("[danmaku_listener] serve 托管拉起失败（见 logs/services/danmaku_listener.log）")
            return False

        from utils.service_orchestrator import ServiceRegistry
        ServiceRegistry.instance().register(self._mproc)
        self._consumer = _ConsumerLoop(self)
        self._consumer_thread = threading.Thread(
            target=self._consumer.run, name="danmaku-consumer", daemon=True
        )
        self._consumer_thread.start()
        set_service_instance(self)
        return True

    def stop(self) -> None:
        """关停：消费者先停 → 托管 terminate→kill（义务块正常关停语义）。"""
        if self._consumer:
            self._consumer.shutdown()
        if self._consumer_thread:
            self._consumer_thread.join(timeout=5)
            self._consumer_thread = None
            self._consumer = None
        if self._mproc:
            self._mproc.stop()
            self._mproc = None
        set_service_instance(None)
        logger.info("[danmaku_listener] 已关停")

    def _on_serve_state(self, state: str, mproc) -> None:
        """托管状态回调 → 状态条胶囊订阅（P1-3 复用）。"""
        logger.info(f"[danmaku_listener] 托管状态: {state}")
        if self._state_cb:
            try:
                self._state_cb(state)
            except Exception:
                logger.error("[danmaku_listener] 状态回调异常", exc_info=True)

    def request_restart(self) -> bool:
        """手动重启入口（状态条胶囊 red 态出路，D10）。"""
        if not self._mproc:
            return False
        self._rotate_token()  # 重拉即新世代
        ok = self._mproc.manual_restart()
        return ok


class _ConsumerLoop:
    """WS 消费者主循环（独立线程事件循环）。system 快路径 + business 队列投递。"""

    def __init__(self, service: DanmakuListenerService):
        self._svc = service
        self._stop = threading.Event()
        self._work_queue: "queue.Queue[dict]" = queue.Queue(maxsize=QUEUE_MAX)
        self._worker: Optional[threading.Thread] = None
        self._last_heartbeat = time.time()
        self._generation_seen = service.generation()
        self._seq_expected: dict[str, int] = {}     # room → 下一期待 seq
        self._seq_wait_buf: dict[str, list] = {}    # room → 等待缓冲
        self._seq_wait_since: dict[str, float] = {}
        self._connect_fail_streak = 0
        self._lost_reported = False
        self._login_cb: Optional[Callable[[dict], None]] = None
        self._status_cb: Optional[Callable[[str, dict], None]] = None
        self._unknown_type_count = 0
        self._invalid_msg_count = 0
        self._dropped_count = 0

    # ---------- 外部接线 ----------

    def _emit_status(self, kind: str, data: dict) -> None:
        """系统状态/计数事件上报（事件环 → 前端轮询；回调直连）。"""
        try:
            self._svc.push_event(kind, data)
        except Exception:
            pass
        if self._status_cb:
            try:
                self._status_cb(kind, data)
            except Exception:
                logger.error(f"[danmaku_consumer] 状态回调异常（kind={kind}）", exc_info=True)

    def set_login_callback(self, cb: Callable[[dict], None]) -> None:
        """NEEDS_LOGIN 回调（payload 含 interactive_login QR/URL）→ UI 扫码对话框。"""
        self._login_cb = cb

    def set_status_callback(self, cb: Callable[[str, dict], None]) -> None:
        """系统状态回调（kind: heartbeat_lost/gap/engine/backpressure/recovered/room/connected）。"""
        self._status_cb = cb

    def worker_snapshot(self) -> dict:
        """状态条/调试快照。"""
        return {
            "queue_size": self._work_queue.qsize(),
            "unknown_type_count": self._unknown_type_count,
            "invalid_msg_count": self._invalid_msg_count,
            "dropped_count": self._dropped_count,
            "last_heartbeat_age_s": round(time.time() - self._last_heartbeat, 1),
            "generation": self._generation_seen,
        }

    def shutdown(self) -> None:
        self._stop.set()
        if self._worker:
            self._worker.join(timeout=3)

    # ---------- 主循环 ----------

    def run(self) -> None:
        self._worker = threading.Thread(target=self._worker_loop, name="danmaku-worker", daemon=True)
        self._worker.start()
        sec = self._svc._section()
        port = int(sec.get("ws_port", 8765))
        url = f"ws://127.0.0.1:{port}/ws"
        backoff = RECONNECT_BACKOFF_MIN_S
        while not self._stop.is_set():
            gen = self._svc.generation()
            if gen != self._generation_seen:
                self._on_generation_reset(gen)
            # 每个连接会话独立 asyncio.run（daemon 线程无既有事件循环，asyncio connect 必须在循环内使用）
            session_ok = asyncio.run(self._session(url))
            if self._stop.is_set():
                return
            if session_ok:
                backoff = RECONNECT_BACKOFF_MIN_S
                self._connect_fail_streak = 0
                continue
            self._connect_fail_streak += 1
            if self._connect_fail_streak >= CONNECT_FAIL_HANDOFF:
                # A-3：移交托管层（重拉裁决归口 ManagedProcess），消费者保持退避重连
                logger.error(f"[danmaku_consumer] 连续 {self._connect_fail_streak} 次连接失败，已移交托管层")
                self._emit_status("handoff", {"streak": self._connect_fail_streak})
            sleep_s = min(backoff, RECONNECT_BACKOFF_MAX_S)
            backoff = min(backoff * 2, RECONNECT_BACKOFF_MAX_S)
            self._stop.wait(sleep_s)

    async def _session(self, url: str) -> bool:
        """单次连接会话：连接 → 读循环。正常关停返回 True，异常返回 False。"""
        token = self._svc.current_token()  # A-1：每次重连前读当前 token
        gen = self._svc.generation()
        try:
            headers = {"Authorization": f"Bearer {token}"} if token else {}
            async with ws_connect(url, additional_headers=headers, open_timeout=5) as ws:
                logger.info(f"[danmaku_consumer] 已连接 {url}（generation={gen}）")
                self._connect_fail_streak = 0
                self._lost_reported = False
                self._last_heartbeat = time.time()
                self._emit_status("connected", {})
                async for raw in ws:
                    if self._stop.is_set():
                        return True
                    self._process_frame(raw)
                return True
        except Exception as e:
            if self._stop.is_set():
                return True
            if "401" in str(e) or "invalid" in str(e).lower():
                logger.warning(f"[danmaku_consumer] 鉴权失败（{e}）；下一轮以新 token 重试")
                self._emit_status("auth_error", {"detail": str(e)})
            else:
                logger.warning(f"[danmaku_consumer] 连接异常: {e}")
            return False

    def _on_generation_reset(self, gen: int) -> None:
        """世代重置（A-4）：重拉产生新 serve 进程，seq 期待与去重窗口清空。"""
        logger.info(f"[danmaku_consumer] 世代切换 {self._generation_seen} → {gen}；重置 seq/去重窗口")
        self._generation_seen = gen
        self._seq_expected.clear()
        self._seq_wait_buf.clear()
        self._seq_wait_since.clear()
        try:
            from utils import my_global
            if getattr(my_global, "my_handle", None):
                my_global.my_handle.clear_live_data()
        except Exception:
            logger.error("[danmaku_consumer] 去重窗口清空失败", exc_info=True)

    def _process_frame(self, raw) -> None:
        """单帧处理：心跳判定（快路径内联，不受主队列拥堵影响——Eng A-2）+ 分流。"""
        now = time.time()
        age = now - self._last_heartbeat
        if age >= ZOMBIE_TIMEOUT_S and not self._lost_reported:
            self._lost_reported = True
            self._emit_status("zombie", {"age_s": round(age, 1)})
        elif age >= HEARTBEAT_LOST_PERIODS * HEARTBEAT_INTERVAL_S and not self._lost_reported:
            self._lost_reported = True
            self._emit_status("heartbeat_lost", {"age_s": round(age, 1)})
        try:
            msg = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            self._invalid_msg_count += 1
            logger.warning(f"[danmaku_consumer] 非法 JSON 帧丢弃（累计 {self._invalid_msg_count}）")
            return
        norm = _normalize_wire(msg)
        if norm is None:
            self._invalid_msg_count += 1
            logger.warning(f"[danmaku_consumer] envelope 缺失丢弃（累计 {self._invalid_msg_count}）")
            return
        if not norm["version"].startswith(f"{CONTRACT_VERSION_MAJOR}."):
            logger.error(f"[danmaku_consumer] 契约版本不兼容: {norm['version']}（要求 {CONTRACT_VERSION_MAJOR}.x）——拒绝消费")
            self._emit_status("contract_mismatch", {"version": norm["version"]})
            self._stop.set()
            return
        category, mtype, room, seq = norm["category"], norm["type"], norm["room"], norm["seq"]
        if mtype == "HEARTBEAT":
            self._last_heartbeat = time.time()
            if self._lost_reported:
                self._lost_reported = False
                self._emit_status("recovered", {})
            return
        if category == "system":
            self._handle_system(mtype, norm["payload"], room)
            return
        if category != "business":
            self._unknown_type_count += 1
            return
        # business：计数快路径 vs 对话链队列（B-2 路由分类）
        if mtype in _FASTPATH_BUSINESS:
            self._count_fastpath(mtype)
            return
        self._route_business(mtype, {"envelope": norm["env"], "payload": norm["payload"]}, room, seq)

    # ---------- system 快路径 ----------

    def _handle_system(self, mtype: str, msg: dict, room: str) -> None:
        payload = msg.get("payload") or {}
        if mtype == "GAP":
            logger.warning(f"[danmaku_consumer] GAP {payload.get('window_start')}~{payload.get('window_end')} "
                           f"reason={payload.get('reason')} approx={payload.get('approx')}")
            self._emit_status("gap", payload)
        elif mtype == "NEEDS_LOGIN":
            logger.warning(f"[danmaku_consumer] NEEDS_LOGIN: {payload.get('failure', {}).get('reason_code')}")
            try:
                self._svc.submit_login_request(payload)
            except Exception:
                logger.error("[danmaku_consumer] 登录请求入队异常", exc_info=True)
            if self._login_cb:
                try:
                    self._login_cb(payload)
                except Exception:
                    logger.error("[danmaku_consumer] 登录回调异常", exc_info=True)
        elif mtype == "ENGINE_STATUS":
            self._emit_status("engine", {"engine": payload.get("engine"), "detail": payload.get("detail")})
        elif mtype == "ROUTE_FAILED":
            f = payload.get("failure") or {}
            logger.error(f"[danmaku_consumer] ROUTE_FAILED {f.get('reason_code')}：{f.get('fix_hint')}（{f.get('docs_anchor')}）")
            self._emit_status("route_failed", f)
        elif mtype == "BACKPRESSURE":
            logger.warning(f"[danmaku_consumer] BACKPRESSURE 窗口丢弃: {payload.get('dropped_by_type')}")
            self._emit_status("backpressure", payload)
        elif mtype == "RECOVERED":
            logger.info(f"[danmaku_consumer] 组件内恢复（{payload.get('after_seconds')}s）")
            self._emit_status("component_recovered", payload)
        elif mtype == "ROOM_STATUS":
            self._emit_status("room", {"online": payload.get("online")})
        else:
            self._unknown_type_count += 1

    # ---------- business 路由 ----------

    def _count_fastpath(self, mtype: str) -> None:
        """计数快路径（LIKE/ENTER_ROOM/ROOM_STATS）：进计数不进对话链。"""
        self._emit_status("count", {"type": mtype})

    def _route_business(self, mtype: str, msg: dict, room: str, seq) -> None:
        # seq 单调校验（契约：同房间单调；跳跃等待 GAP，10s 超时 approx 降级）
        if isinstance(seq, int):
            expected = self._seq_expected.get(room)
            if expected is not None and seq > expected:
                # 跳跃：入等待缓冲（上限 SEQ_BUFFER_MAX）
                buf = self._seq_wait_buf.setdefault(room, [])
                if len(buf) < SEQ_BUFFER_MAX:
                    buf.append((mtype, msg))
                    if room not in self._seq_wait_since:
                        self._seq_wait_since[room] = time.time()
                    if time.time() - self._seq_wait_since[room] < SEQ_WAIT_TIMEOUT_S:
                        return  # 等待 GAP 到达
                    # 超时：approx 降级放行（分批注入，B-3）
                    logger.warning(f"[danmaku_consumer] seq 等待超时降级放行 room={room}（{len(buf)} 条缓冲）")
                    self._flush_wait_buf(room, batch=True)
            self._seq_expected[room] = seq + 1
            # GAP/超时后恢复单调：放行等待缓冲
            if self._seq_wait_buf.get(room):
                self._flush_wait_buf(room, batch=True)
        self._enqueue(mtype, msg)

    def _flush_wait_buf(self, room: str, batch: bool) -> None:
        buf = self._seq_wait_buf.pop(room, [])
        self._seq_wait_since.pop(room, None)
        step = 50 if batch else len(buf)
        for i in range(0, len(buf), step):  # 分批注入，避免瞬时打满主队列（B-3）
            chunk = buf[i:i + step]
            for mtype, msg in chunk:
                self._enqueue(mtype, msg)
            if batch and i + step < len(buf):
                time.sleep(0.05)

    def _enqueue(self, mtype: str, msg: dict) -> None:
        try:
            self._work_queue.put_nowait((mtype, msg))
        except queue.Full:
            # 溢出：按背压同序丢弃低优先级（义务块：同序丢弃并计数）
            dropped = self._drop_lowest()
            if dropped:
                self._dropped_count += 1
                try:
                    self._work_queue.put_nowait((mtype, msg))
                    return
                except queue.Full:
                    pass
            self._dropped_count += 1
            logger.warning(f"[danmaku_consumer] 主队列溢出丢弃（累计 {self._dropped_count}）")

    def _drop_lowest(self) -> bool:
        """从队列中移除一个最低优先级项（_DROP_ORDER 数值大者优先保留）。"""
        items = []
        while True:
            try:
                items.append(self._work_queue.get_nowait())
            except queue.Empty:
                break
        if not items:
            return False
        items.sort(key=lambda it: _DROP_ORDER.get(it[0], 99))
        keep = items[1:]  # 丢弃优先级最低（排序最前）的一条
        for it in keep:
            self._work_queue.put_nowait(it)
        return True

    # ---------- worker：队列 → 映射 → my_handle ----------

    def _worker_loop(self) -> None:
        agg = _DanmuAggregator(on_flush=self._dispatch_agg)
        like_agg = _LikeAggregator(on_flush=self._dispatch_like_agg)
        while not self._stop.is_set():
            try:
                mtype, msg = self._work_queue.get(timeout=0.5)
            except queue.Empty:
                agg.maybe_flush()
                like_agg.maybe_flush()
                continue
            if mtype == "DANMU":
                agg.add(msg)   # B-1：窗口聚合限速进对话链
            elif mtype == "LIKE":
                like_agg.add(msg)  # 点赞聚合进对话链（2026-10-03 用户需求）
            else:
                self._dispatch(mtype, msg)
        agg.flush()
        like_agg.flush()

    def _dispatch_agg(self, merged: dict) -> None:
        self._dispatch("DANMU", merged)

    def _dispatch_like_agg(self, merged: dict) -> None:
        """聚合点赞 → 交互链（like_handle 优先；comment 兜底）。"""
        try:
            from utils import my_global
            handle = getattr(my_global, "my_handle", None)
            if handle is None:
                return
            env = merged.get("envelope") or {}
            platform = str(env.get("platform", ""))
            data = {"platform": platform,
                    "username": merged.get("like_user_summary") or "有人",
                    "count": merged.get("like_count", 1)}
            if hasattr(handle, "like_handle"):
                handle.like_handle(data)
            else:
                data["content"] = f"点赞了直播 x{data['count']}"
                handle.process_data(data, "comment")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[danmaku_consumer] like 派发失败: {e}")

    def _dispatch(self, mtype: str, msg: dict) -> None:
        """business 消息映射进 my_handle 交互链（G3：复用不重建）。"""
        payload = msg.get("payload") or {}
        env = msg.get("envelope") or {}
        platform = str(env.get("platform", ""))
        try:
            from utils import my_global
            handle = getattr(my_global, "my_handle", None)
            if handle is None:
                logger.warning("[danmaku_consumer] my_handle 未就绪，消息丢弃")
                return
            if mtype == "DANMU":
                data = {"platform": platform, "username": payload.get("user_name") or "匿名",
                        "content": _sanitize_danmu_text(str(payload.get("content") or ""))}
                handle.process_data(data, "comment")
            elif mtype == "GIFT":
                num = int(payload.get("gift_count") or 1)
                price = payload.get("gift_value") or 0
                data = {"platform": platform, "gift_name": payload.get("gift_name") or "礼物",
                        "username": payload.get("user_name") or "匿名", "num": num,
                        "unit_price": (price / num) if num else 0, "total_price": price}
                handle.process_data(data, "gift")
            elif mtype == "SUPER_CHAT":
                data = {"platform": platform, "username": payload.get("user_name") or "匿名",
                        "content": _sanitize_danmu_text(str(payload.get("content") or "")),
                        "price": payload.get("price") or 0}
                handle.process_data(data, "comment")  # 醒目留言进对话链（price 附加）
            elif mtype == "ENTER_ROOM":
                handle.process_data({"platform": platform, "username": payload.get("user_name") or "匿名"},
                                    "entrance")
            elif mtype == "SOCIAL":
                action = str(payload.get("action") or "")
                if "关注" in action or "follow" in action.lower():
                    handle.process_data({"platform": platform, "username": payload.get("user_name") or "匿名"},
                                        "follow") if hasattr(handle, "event_handler") else None
                    handle.follow_handle({"platform": platform, "username": payload.get("user_name") or "匿名"})
                else:
                    logger.info(f"[danmaku_consumer] SOCIAL {action} 计数")
            elif mtype == "LIVE_STATUS_CHANGE":
                live = bool(payload.get("live"))
                logger.info(f"[danmaku_consumer] LIVE_STATUS_CHANGE live={live}")
                if not live:
                    self._on_stream_offline()
                else:
                    self._on_stream_online()
            elif mtype in ("ROOM_STATS",):
                pass  # 已走计数快路径；此分支防御性忽略
            else:
                self._unknown_type_count += 1
        except Exception:
            logger.error(f"[danmaku_consumer] {mtype} 处理异常", exc_info=True)

    def _on_stream_offline(self) -> None:
        """下播：清空队列低优先级（保 GIFT/SUPER_CHAT），不触发对话链（义务块下播裁剪）。"""
        kept, dropped = [], 0
        while True:
            try:
                mtype, msg = self._work_queue.get_nowait()
            except queue.Empty:
                break
            if mtype in ("GIFT", "SUPER_CHAT"):
                kept.append((mtype, msg))
            else:
                dropped += 1
        for it in kept:
            try:
                self._work_queue.put_nowait(it)
            except queue.Full:
                break
        logger.info(f"[danmaku_consumer] 下播裁剪：清空 {dropped} 条低优先级，保留 {len(kept)} 条礼物类")

    def _on_stream_online(self) -> None:
        """开播：复位状态机（seq 期待清空，恢复互动）。"""
        self._seq_expected.clear()
        self._seq_wait_buf.clear()
        logger.info("[danmaku_consumer] 开播复位")


def _sanitize_danmu_text(text: str) -> str:
    """S-1 最低防线：长度钳制 + 控制字符/零宽字符剥离（URL 由上层过滤不进 prompt 的规则在对话链侧）。"""
    text = "".join(ch for ch in text if ch.isprintable() and ch not in "​‌‍﻿")
    return text[:200]


def _normalize_wire(msg: dict) -> Optional[dict]:
    """线格式适配：组件 to_wire() 为扁平结构（信封字段顶层 + payload 并列），
    schema.json 文档为嵌套结构（envelope/payload）。两者兼容归一，返回
    {"env":..., "payload":..., "category":..., "type":..., "room":..., "seq":...} 或 None。
    （上游 schema 与实现的漂移已记录，待组件侧对齐后本适配层保持向后兼容。）"""
    if not isinstance(msg, dict):
        return None
    payload = msg.get("payload")
    if isinstance(msg.get("envelope"), dict) and isinstance(payload, dict):
        env = msg["envelope"]                       # 嵌套格式（schema.json 文档形态）
    elif isinstance(payload, dict) and msg.get("category"):
        env = msg                                   # 扁平格式（to_wire 实际线格式）
        payload = dict(payload)
    else:
        return None
    return {
        "env": env,
        "payload": payload,
        "category": env.get("category"),
        "type": env.get("type") or payload.get("type"),
        "room": str(env.get("room_id", "")),
        "seq": env.get("seq"),
        "version": str(env.get("contract_version", "1.0.0")),
    }


def build_danmaku_listener_service(config) -> Optional[DanmakuListenerService]:
    """main.py 托管挂钩工厂：返回 None 表示未启用（不拉起）。"""
    def cfg_getter() -> dict:
        try:
            sec = config.get("danmaku_listener")
            return dict(sec) if isinstance(sec, dict) else {}
        except Exception:
            return {}
    sec = cfg_getter()
    if not sec.get("enabled"):
        logger.info("[danmaku_listener] enabled=false，托管服务不构建")
        return None
    return DanmakuListenerService(cfg_getter)


class _LikeAggregator:
    """LIKE 窗口聚合：窗内点赞合并为"张三、李四 等 N 人点赞"摘要进对话链
    （2026-10-03 用户需求：监听到谁点赞——原计数快路径不进对话链致点赞不可见；
    聚合防高频刷屏，模式同 _DanmuAggregator）。"""

    def __init__(self, on_flush: Callable[[dict], None]):
        self._on_flush = on_flush
        self._buf: list[dict] = []
        self._window_start = time.time()

    def add(self, msg: dict) -> None:
        payload = msg.get("payload") or {}
        self._buf.append(payload)
        if (len(self._buf) >= DANMU_AGG_MAX
                or time.time() - self._window_start >= DANMU_AGG_WINDOW_S):
            self.flush()

    def maybe_flush(self) -> None:
        if self._buf and time.time() - self._window_start >= DANMU_AGG_WINDOW_S:
            self.flush()

    def flush(self) -> None:
        if not self._buf:
            return
        names = [str(p.get("user_name") or "") for p in self._buf]
        named = [n for n in names if n]
        shown = "、".join(named[:3])
        if len(named) > 3:
            shown += f" 等{len(named)}人"
        elif not named:
            shown = "有人"
        head, rest = self._buf[0], self._buf[1:]
        merged = dict(head)
        merged["like_user_summary"] = shown
        merged["like_count"] = len(self._buf)
        self._on_flush(merged)
        self._buf = []
        self._window_start = time.time()


class _DanmuAggregator:
    """DANMU 窗口聚合（B-1）：窗内合并为一条摘要消息，降低对话链压力。"""

    def __init__(self, on_flush: Callable[[dict], None]):
        self._on_flush = on_flush
        self._buf: list[dict] = []
        self._window_start = 0.0

    def add(self, msg: dict) -> None:
        now = time.time()
        if not self._buf:
            self._window_start = now
        self._buf.append(msg)
        if len(self._buf) >= DANMU_AGG_MAX or now - self._window_start >= DANMU_AGG_WINDOW_S:
            self.flush()

    def maybe_flush(self) -> None:
        if self._buf and time.time() - self._window_start >= DANMU_AGG_WINDOW_S:
            self.flush()

    def flush(self) -> None:
        if not self._buf:
            return
        if len(self._buf) == 1:
            merged = self._buf.pop()
            self._on_flush(merged)
            return
        items = self._buf[:]
        self._buf = []
        names, contents = [], []
        for m in items:
            p = m.get("payload") or {}
            names.append(str(p.get("user_name") or "匿名"))
            contents.append(_sanitize_danmu_text(str(p.get("content") or "")))
        merged = {
            "envelope": dict(items[-1].get("envelope") or {}),
            "payload": {
                "type": "DANMU",
                "user_name": "、".join(dict.fromkeys(names))[:120],
                "content": "；".join(f"[{n}]{c}" for n, c in zip(names, contents))[:600],
                "aggregated": len(items),
            },
        }
        self._on_flush(merged)
