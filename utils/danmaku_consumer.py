# -*- coding: utf-8 -*-
"""
DanmakuListener 进程内托管层（整合计划第二阶段：进程内化与全面替换）

架构（specs/integration/DanmakuListener系统模块整合计划.md 第 3 节，已批准）：

    AUTOlive main.py 进程
     ├─ 托管层 utils/danmaku_consumer.py（本模块）
     │   ├─ 专用 asyncio daemon 线程（_loop_main）
     │   │    └─ AutoliveBridge(DanmakuBridge) 子类实例
     │   │         ├─ 12 平台引擎 + 登录门 + 房间注册表（组件库，副本零改动）
     │   │         └─ 消息 hook（重写 _broadcast）→ _BoundedDropQueue（线程安全）
     │   ├─ 消费线程：队列 → 契约线格式解析 → 活跃路由 → _dispatch → my_handle
     │   └─ 控制面：FastAPI /danmaku/*（utils/web_server.py 通道）→ 同步方法
     │        → asyncio.run_coroutine_threadsafe → Bridge 协程（Eng A1）
     └─ 前端（webui 进程）→ fetch /danmaku/*（跨进程边界唯一通道）

关键语义（对应计划义务块 autoplan-accepted:ceo/dx/eng）：
- 线程亲和（Eng A1 高危）：Bridge 控制方法均为协程，UI/FastAPI 线程直调只会得到
  未执行的 coroutine 对象——所有控制面调用统一经 _submit()（future+超时+异常转换）。
- 丢弃结构（Eng E1）：queue.Queue 无删除 API，排空-回灌与消费 get() 并发交错会
  乱序竞态——自实现 deque+Condition 有界结构，按可丢类型扫描淘汰，下播裁剪同构。
- 停机顺序（Eng T4）：先提交 stop_all 并等待 future 完成，再停事件循环，再 join。
- 数据落点纪律：cookie_dir/房间注册表/关键词文件全部显式外置仓库根
  persistence_data/ 与 cookie/——danmaku_listener/ 会被 copy_danmaku.py 重跑 rmtree。
- 副本纯净：对组件的全部集成走子类覆写与组合调用，零组件源码改动；
  升级流程含 hook 存在性断言（_broadcast/_rooms_file/_load_blocked_keywords 等）。
- 抖音 GIFT 增量换算：组件 TODOS"GIFT.count 是 totalCount 累计值透传"——按
  (room_id, user_id, gift_id) 维护上次值，首见视为 1，LRU 5000 键。
"""
from __future__ import annotations

import asyncio
import json
import os
import socket
import threading
import time
from collections import OrderedDict, deque
from typing import Any, Callable, Optional

from utils.my_log import logger

# ---------- 运行时阈值（默认值；queue_maxsize/web_console_port/stall_threshold_seconds 可经 config 覆盖——DX 5.1） ----------
QUEUE_MAX_DEFAULT = 500            # 业务队列上限（可覆盖：danmaku_listener.queue_maxsize）
WEB_CONSOLE_PORT_DEFAULT = 8080    # web_console 调试通道端口（可覆盖）
STALL_THRESHOLD_S_DEFAULT = 60     # 收流停滞判定阈值（可覆盖）
DANMU_AGG_WINDOW_S = 5.0           # DANMU 聚合时间窗
DANMU_AGG_MAX = 5                  # 聚合条数上限
CONTRACT_VERSION_MAJOR = 1         # 兼容断言：1.x 接受，2.x 拒绝
CONTROL_TIMEOUT_S = 30             # 控制面协程等待超时（登录门启动等合法耗时最长）
REBUILD_GUARD_MAX = 3              # 守护重建失控防护（连续 3 次停止重建、置红）
GIFT_LRU_MAX = 5000                # 抖音 GIFT 换算映射上限（Section 7 闭环）
SELF_SEND_LRU_MAX = 5000           # R39 自联回环去重键上限（对齐 GIFT_LRU_MAX 先例）
SELF_SEND_WINDOW_S = 10            # R39：收到时刻与 sent_at 差 ≤10s 才判回环（sent_at 秒级、payload 无 room 维度——窗口收紧防批量同秒误杀）

# 溢出丢弃优先级（数值小先丢；与组件 bus_drop_order 及契约对齐——双端一致性）
_DROP_ORDER = {"LIKE": 0, "ENTER_ROOM": 1, "DANMU": 2, "SOCIAL": 3, "ROOM_STATS": 4, "GIFT": 5, "SUPER_CHAT": 6}
_FASTPATH_BUSINESS = {"ENTER_ROOM", "ROOM_STATS"}  # 计数快路径（LIKE 走对话链聚合——用户需求）

_SERVICE_INSTANCE: Optional["DanmakuListenerService"] = None
_CONFIG_LOCK = threading.Lock()    # config.json danmaku_listener 节 RMW 锁（main.py 进程内）


def set_service_instance(svc: Optional["DanmakuListenerService"]) -> None:
    global _SERVICE_INSTANCE
    _SERVICE_INSTANCE = svc


def get_service_instance() -> Optional["DanmakuListenerService"]:
    return _SERVICE_INSTANCE


# ---------- 有界丢弃队列（Eng E1：deque+Condition 原子结构） ----------

class _BoundedDropQueue:
    """有界消息队列：满载按背压同序扫描淘汰，支持按类型批量裁剪。

    替代 queue.Queue 的排空-回灌（racy）模式；put/get 全程持锁，
    消费侧 get(timeout) 用 Condition 等待，任何路径不 busy-wait。
    """

    def __init__(self, maxsize: int):
        self._max = max(1, int(maxsize))
        self._items: deque = deque()
        self._cond = threading.Condition()
        self.dropped_count = 0

    def put(self, item) -> None:
        with self._cond:
            if len(self._items) >= self._max:
                if not self._drop_one_locked():
                    # 可丢类型穷尽 → 丢最新（本条）并入计数；不阻塞（Eng E1/R3-2）
                    self.dropped_count += 1
                    return
            self._items.append(item)
            self._cond.notify()

    _DROPPABLE = {"LIKE", "ENTER_ROOM", "DANMU", "SOCIAL", "ROOM_STATS"}
    """可淘汰类型集合：GIFT/SUPER_CHAT 为保类型（背压语义——满载且仅剩保类型时丢最新）。"""

    def _drop_one_locked(self) -> bool:
        if not self._items:
            return False
        worst_idx, worst_pri = None, -1
        for i, (mtype, _msg) in enumerate(self._items):
            if mtype not in self._DROPPABLE:
                continue
            pri = _DROP_ORDER.get(mtype, 99)
            if worst_idx is None or pri < worst_pri:
                worst_idx, worst_pri = i, pri
        if worst_idx is None:
            return False
        del self._items[worst_idx]
        self.dropped_count += 1
        return True

    def get(self, timeout: float = 0.5):
        with self._cond:
            if not self._items:
                self._cond.wait(timeout)
            if self._items:
                return self._items.popleft()
        return None

    def prune_keep(self, keep_types: set) -> int:
        """下播裁剪：仅保留 keep_types 中的类型（Eng E1：同构原子操作）。"""
        with self._cond:
            before = len(self._items)
            kept = [it for it in self._items if it[0] in keep_types]
            dropped = before - len(kept)
            self._items.clear()
            self._items.extend(kept)
            return dropped

    def qsize(self) -> int:
        with self._cond:
            return len(self._items)

    def drain(self) -> int:
        with self._cond:
            n = len(self._items)
            self._items.clear()
            return n


# ---------- 配置 RMW（main.py 进程内原子写回；前端写盘通道独立——低频短窗口） ----------

def _config_path() -> str:
    return "config.json"


def _read_config_file() -> dict:
    try:
        with open(_config_path(), "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except Exception:
        logger.error("[danmaku_listener] config.json 读取失败", exc_info=True)
        return {}


def _write_config_section(mutator: Callable[[dict], None]) -> bool:
    """原子改写 config.json 的 danmaku_listener 节（tmp+os.replace，模块级锁）。

    mutator(section) 在锁内修改节字典（原地）；写失败返回 False。
    """
    with _CONFIG_LOCK:
        try:
            cfg = _read_config_file()
            sec = cfg.setdefault("danmaku_listener", {})
            mutator(sec)
            tmp = _config_path() + ".tmp"
            with open(tmp, "w", encoding="utf-8", newline="\n") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
            os.replace(tmp, _config_path())
            _refresh_memory_section(sec)  # Eng F-ENG-1：内存节同步刷新（写者分配矩阵）
            return True
        except Exception:
            logger.error("[danmaku_listener] config.json 写回失败", exc_info=True)
            return False


def _refresh_memory_section(new_sec: dict) -> None:
    """写者分配矩阵（Eng F-ENG-1）：窄端点写盘后同步刷新 main.py 内存节。"""
    try:
        from utils.config import Config
        if isinstance(Config.config, dict):
            Config.config["danmaku_listener"] = dict(new_sec)
    except Exception:
        logger.error("[danmaku_listener] 内存节刷新失败", exc_info=True)


# ---------- AutoliveBridge：组件 Bridge 的 AUTOlive 子类（副本纯净——仅覆写） ----------

class AutoliveBridge:
    """DanmakuBridge 子类：消息 hook + 持久化外置。

    采用延迟导入包装而非直接继承，避免在模块顶层触发组件重依赖
    （playwright/quickjs）的 import（组件自身 pitfall：eager-import-heavy-deps）。
    实际类在 _build_bridge_class() 内构造（继承 DanmakuBridge）。
    """
    pass


def _build_bridge_class():
    """构造 AutoliveBridge 类（进程内首次调用时执行；继承组件 DanmakuBridge）。"""
    from danmaku_listener.web.bridge import DanmakuBridge

    class AutoliveBridge(DanmakuBridge):
        """AUTOlive 进程内桥：消息 → 托管队列；持久化外置副本。"""

        def __init__(self, svc: "DanmakuListenerService"):
            self._autolive_svc = svc
            super().__init__()
            # Eng A2：基类构造无参且 __init__ 内无条件赋值 _rooms_file——
            # 构造后重定向到仓库根 persistence_data/ 并重载（消除 cwd 漂移 + rmtree 区外）
            import os
            self._rooms_file = os.path.join("persistence_data", "rooms.json")
            self._rooms.clear()
            self._load_rooms_registry()

        # ---- 消息 hook（数据面唯一出口；Eng 义务：不阻塞 asyncio 事件循环） ----

        async def _broadcast(self, message: dict) -> None:
            """契约线格式 → AUTOlive 有界队列（同步非阻塞）+ 保留组件广播。"""
            try:
                self._autolive_svc._on_wire(message)
            except Exception:
                logger.error("[danmaku_listener] 队列投递异常", exc_info=True)
            await super()._broadcast(message)

        # ---- 持久化外置（Eng A3：blocked_keywords.json 在副本 web/ 内——升级清数据+版本戳漂移） ----

        def _kw_file(self) -> str:
            import os
            return os.path.join("persistence_data", "blocked_keywords.json")

        def _load_blocked_keywords(self) -> None:
            import os
            try:
                with open(self._kw_file(), "r", encoding="utf-8") as f:
                    self._blocked_keywords = json.load(f)
                logger.info(f"[danmaku_listener] 关键词已加载 {len(self._blocked_keywords)} 条（外置文件）")
            except (FileNotFoundError, json.JSONDecodeError):
                self._blocked_keywords = []

        def _save_blocked_keywords(self) -> None:
            import os
            try:
                os.makedirs(os.path.dirname(self._kw_file()) or ".", exist_ok=True)
                with open(self._kw_file(), "w", encoding="utf-8") as f:
                    json.dump(self._blocked_keywords, f, indent=2, ensure_ascii=False)
            except OSError:
                logger.error("[danmaku_listener] 关键词保存失败", exc_info=True)

    return AutoliveBridge


# ---------- 托管服务 ----------

class DanmakuListenerService:
    """DanmakuListener 进程内托管（挂载 main.py 启停；控制面经 FastAPI 暴露）。"""

    def __init__(self, config_getter: Callable[[], dict]):
        self._cfg = config_getter
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._loop_thread: Optional[threading.Thread] = None
        self._bridge: Any = None
        self._web_runner: Any = None
        self._stop_evt = threading.Event()
        self._state_cb: Optional[Callable[[str], None]] = None
        self._service_on = False          # set_service_enabled 运行时态
        self._rebuild_count = 0           # 守护重建计数（3 次停止、置红）
        # 事件环（G4 保留：前端经 /danmaku/events 轮询）
        self.events: deque = deque(maxlen=200)
        self._event_seq = 0
        self._evt_lock = threading.Lock()
        # 登录队列（G5 保留）
        self.login_queue: list = []
        self.login_active: Optional[dict] = None
        # 消息面
        self._queue: Optional[_BoundedDropQueue] = None
        self._worker: Optional[threading.Thread] = None
        self._last_heartbeat = time.time()
        self._unknown_type_count = 0
        self._invalid_msg_count = 0
        self._self_send: "OrderedDict[str, int]" = OrderedDict()  # R39：content → sent_at（发送回执登记）
        self._self_echo_dropped = 0       # R39 回环丢弃计数
        self._seen_event_keys: OrderedDict = OrderedDict()  # 事件去重（60s 窗口——Eng E3）
        self._gift_lru: OrderedDict = OrderedDict()         # 抖音 GIFT 换算（LRU 5000）
        self._last_msg_ts = 0.0                             # 生命信号（P2-0 ②）
        self._msg_today_count = 0
        self._no_active_warned = False

    # ---------- 配置 ----------

    def _section(self) -> dict:
        """danmaku_listener 节快照（约定：_cfg 返回节本身——build 工厂与冒烟均已包装）。"""
        try:
            sec = self._cfg()
            return dict(sec) if isinstance(sec, dict) else {}
        except Exception:
            return {}

    def _queue_max(self) -> int:
        return int(self._section().get("queue_maxsize") or QUEUE_MAX_DEFAULT)

    def _stall_threshold(self) -> float:
        return float(self._section().get("stall_threshold_seconds") or STALL_THRESHOLD_S_DEFAULT)

    def _cookie_dir(self) -> str:
        sec = self._section()
        return str(sec.get("cookie_dir") or os.path.join("cookie"))

    # ---------- 事件环 / 登录队列（保留自上轮，前端经 /danmaku/* 轮询） ----------

    def push_event(self, kind: str, data: dict, dedup_key: str = "") -> None:
        with self._evt_lock:
            if dedup_key:
                now = time.time()
                for k, ts in list(self._seen_event_keys.items()):
                    if now - ts > 60:
                        self._seen_event_keys.pop(k, None)
                if dedup_key in self._seen_event_keys:
                    return
                self._seen_event_keys[dedup_key] = now
            self._event_seq += 1
            self.events.append({"seq": self._event_seq, "ts": time.time(),
                                "kind": kind, "data": data})

    def events_since(self, last_seq: int) -> list:
        with self._evt_lock:
            return [e for e in self.events if e["seq"] > last_seq]

    def submit_login_request(self, payload: dict) -> None:
        failure = payload.get("failure") or {}
        item = {
            "platform": str(failure.get("reason_code", "unknown")).split(".")[0],
            "reason_code": failure.get("reason_code", ""),
            "fix_hint": failure.get("fix_hint", ""),
            "docs_anchor": failure.get("docs_anchor", ""),
            "interactive": payload.get("interactive_login") or {},
            "ts": time.time(),
        }
        self.login_queue.append(item)
        if self.login_active is None:
            self.login_active = self.login_queue.pop(0)
        self.push_event("login_request", item,
                        dedup_key=f"login:{item['platform']}:{item['reason_code']}")
        logger.info(f"[danmaku_listener] 登录请求入队（{item['platform']}，队列 {len(self.login_queue)}）")

    def ack_login_done(self) -> None:
        self.login_active = None
        if self.login_queue:
            self.login_active = self.login_queue.pop(0)

    # ---------- 数据面入口（AutoliveBridge._broadcast 调用；asyncio 线程内） ----------

    def _on_wire(self, message: dict) -> None:
        """契约线格式单条处理（组件引擎线程内调用；全路径非阻塞）。

        system → 事件环/登录队列；business → 活跃路由判定 → 有界队列。
        """
        if not isinstance(message, dict):
            return
        norm = _normalize_wire(message)
        if norm is None:
            self._invalid_msg_count += 1
            return
        if not str(norm["version"]).startswith(f"{CONTRACT_VERSION_MAJOR}."):
            logger.error(f"[danmaku_listener] 契约版本不兼容: {norm['version']}")
            self.push_event("contract_mismatch", {"version": norm["version"]})
            return
        mtype, room = norm["type"], norm["room"]
        platform = str(norm["env"].get("platform", ""))
        if mtype == "HEARTBEAT":
            self._last_heartbeat = time.time()
            return
        if norm["category"] == "system":
            self._handle_system(mtype, norm["payload"])
            return
        if norm["category"] == "command":
            # R39：发送回执尽力推送经此登记 sent_at+content（进程内接线见 _start_web_console）
            if mtype == "DANMU_SEND_RESULT" and str(norm["payload"].get("status", "")) == "sent":
                self._record_self_send(norm["payload"])
            return
        if norm["category"] != "business":
            self._unknown_type_count += 1
            return
        if mtype in _FASTPATH_BUSINESS:
            self.push_event("count", {"type": mtype, "room": room})
            return
        if mtype == "DANMU" and self._is_self_echo(str(norm["payload"].get("content", ""))):
            # R39：命中已登记发送且 ≤10s 窗口 → 自联回环，不派发（窗口外/异常均放行）
            self._self_echo_dropped += 1
            logger.info(f"[danmaku_listener] R39 自联回环丢弃: {str(norm['payload'].get('content', ''))[:30]}")
            return
        self._msg_today_count += 1
        self._last_msg_ts = time.time()
        self._queue.put((mtype, {"envelope": norm["env"], "payload": norm["payload"]}))

    def _handle_system(self, mtype: str, payload: dict) -> None:
        if mtype == "GAP":
            self.push_event("gap", payload,
                            dedup_key=f"gap:{payload.get('window_start')}:{payload.get('reason')}")
        elif mtype == "NEEDS_LOGIN":
            self.submit_login_request(payload)
        elif mtype == "ENGINE_STATUS":
            self.push_event("engine", {"engine": payload.get("engine"), "detail": payload.get("detail")})
        elif mtype == "ROUTE_FAILED":
            f = payload.get("failure") or {}
            self.push_event("route_failed", f,
                            dedup_key=f"route:{f.get('reason_code')}:{payload.get('room_id', '')}")
        elif mtype == "BACKPRESSURE":
            self.push_event("backpressure", payload)
        elif mtype == "RECOVERED":
            self.push_event("component_recovered", payload)
        elif mtype == "ROOM_STATUS":
            self.push_event("room", {"online": payload.get("online")})
        else:
            self._unknown_type_count += 1

    # ---------- R39 自联回环去重（ENG-M4/M5：sent_at+content，≤10s 窗口收紧） ----------

    def _record_self_send(self, payload: dict) -> None:
        """登记发送回执的 sent_at+content（LRU 有界；异常降级为不登记）。"""
        try:
            sent_at = payload.get("sent_at")
            content = payload.get("content")
            if not sent_at or not content:
                return
            content = str(content)
            if content in self._self_send:
                self._self_send.move_to_end(content)
            self._self_send[content] = int(sent_at)
            while len(self._self_send) > SELF_SEND_LRU_MAX:
                self._self_send.popitem(last=False)
        except Exception:
            logger.warning("[danmaku_listener] R39 自发声登记异常（降级不登记）", exc_info=True)

    def _is_self_echo(self, content: str) -> bool:
        """R39 判定：content 命中已登记发送且 |now-sent_at| ≤10s → 自联回环。

        窗口外放行（防同内容合法弹幕误杀）；去重自身异常放行（监听主链不断）。
        """
        try:
            sent_at = self._self_send.get(content)
            if sent_at is None:
                return False
            return abs(time.time() - sent_at) <= SELF_SEND_WINDOW_S
        except Exception:
            return False

    # ---------- 活跃房间裁定（P1-3：显式 active 字段） ----------

    # ---------- 消费线程（队列 → 聚合 → _dispatch；用户裁定多房间全互动） ----------

    # ---------- 消费线程（队列 → 活跃路由 → 聚合 → _dispatch） ----------

    def _worker_loop(self) -> None:
        agg = _DanmuAggregator(on_flush=lambda m: self._dispatch("DANMU", m))
        like_agg = _LikeAggregator(on_flush=self._dispatch_like_agg)
        while not self._stop_evt.is_set():
            item = self._queue.get(timeout=0.5)
            if item is None:
                agg.maybe_flush()
                like_agg.maybe_flush()
                continue
            mtype, msg = item
            if mtype == "DANMU":
                agg.add(msg)
            elif mtype == "LIKE":
                like_agg.add(msg)
            else:
                self._dispatch(mtype, msg)
        agg.flush()
        like_agg.flush()

    def _dispatch_like_agg(self, merged: dict) -> None:
        try:
            from utils import my_global
            handle = getattr(my_global, "my_handle", None)
            if handle is None:
                return
            env = merged.get("envelope") or {}
            data = {"platform": str(env.get("platform", "")),
                    "username": merged.get("like_user_summary") or "有人",
                    "count": merged.get("like_count", 1)}
            if hasattr(handle, "like_handle"):
                handle.like_handle(data)
            else:
                data["content"] = f"点赞了直播 x{data['count']}"
                handle.process_data(data, "comment")
        except Exception:
            logger.error("[danmaku_listener] like 派发失败", exc_info=True)

    def _dispatch(self, mtype: str, msg: dict) -> None:
        """business 消息映射进 my_handle 交互链（复用不重建）+ 活跃路由 + GIFT 增量换算。"""
        payload = msg.get("payload") or {}
        env = msg.get("envelope") or {}
        platform = str(env.get("platform", ""))
        room = str(env.get("room_id", ""))
        try:
            from utils import my_global
            handle = getattr(my_global, "my_handle", None)
            if handle is None:
                logger.warning("[danmaku_listener] my_handle 未就绪，消息丢弃")
                return
            if mtype == "DANMU":
                data = {"platform": platform, "username": payload.get("user_name") or "匿名",
                        "content": _sanitize_danmu_text(str(payload.get("content") or ""))}
                handle.process_data(data, "comment")
            elif mtype == "GIFT":
                num = _gift_increment(room, payload, self._gift_lru)
                if num <= 0:
                    return
                price = float(payload.get("gift_value") or 0)
                per = price / _gift_base_count(payload) if _gift_base_count(payload) else 0
                data = {"platform": platform, "gift_name": payload.get("gift_name") or "礼物",
                        "username": payload.get("user_name") or "匿名", "num": num,
                        "unit_price": per, "total_price": per * num}
                handle.process_data(data, "gift")
            elif mtype == "SUPER_CHAT":
                data = {"platform": platform, "username": payload.get("user_name") or "匿名",
                        "content": _sanitize_danmu_text(str(payload.get("content") or "")),
                        "price": payload.get("price") or 0}
                handle.process_data(data, "comment")
            elif mtype == "ENTER_ROOM":
                handle.process_data({"platform": platform, "username": payload.get("user_name") or "匿名"},
                                    "entrance")
            elif mtype == "SOCIAL":
                action = str(payload.get("action") or "")
                if "关注" in action or "follow" in action.lower():
                    handle.follow_handle({"platform": platform, "username": payload.get("user_name") or "匿名"})
                else:
                    self.push_event("count", {"type": "SOCIAL", "action": action})
            elif mtype == "LIVE_STATUS_CHANGE":
                live = bool(payload.get("live"))
                if not live:
                    dropped = self._queue.prune_keep({"GIFT", "SUPER_CHAT"})
                    logger.info(f"[danmaku_listener] 下播裁剪：清空 {dropped} 条低优先级")
                else:
                    logger.info("[danmaku_listener] 开播复位")
            else:
                self._unknown_type_count += 1
        except Exception:
            logger.error(f"[danmaku_listener] {mtype} 处理异常", exc_info=True)

    # ---------- 控制面（跨线程封装——Eng A1 高危：全部经 _submit） ----------

    def _submit(self, coro, timeout: float = CONTROL_TIMEOUT_S):
        """提交协程到组件事件循环并同步等待（UI/FastAPI 线程安全）。

        返回协程结果；RoomError 转 dict（success=False + 三段式）；
        超时/未启动抛 RuntimeError。
        """
        if self._loop is None or self._loop.is_closed():
            raise RuntimeError("DanmakuListener 服务未运行")
        fut = asyncio.run_coroutine_threadsafe(coro, self._loop)
        try:
            return fut.result(timeout=timeout)
        except RuntimeError as e:
            if e.__class__.__name__ == "RoomError" or hasattr(e, "status"):
                return {"success": False, "error": str(e),
                        "reason_code": getattr(e, "reason_code", "") or "room_error",
                        "fix_hint": getattr(e, "fix_hint", ""), "docs_anchor": getattr(e, "docs_anchor", "")}
            raise

    def _submit_room_op(self, coro_factory) -> dict:
        """房间操作统一出口：RoomError → {success:False,...} 三段式（组件异常类）。"""
        if self._loop is None or self._loop.is_closed():
            return {"success": False, "error": "服务未运行",
                    "reason_code": "service.not_running",
                    "fix_hint": "开启 danmaku_listener.enabled 后重启系统",
                    "docs_anchor": "docs/danmaku_module.md"}
        fut = asyncio.run_coroutine_threadsafe(coro_factory(), self._loop)
        try:
            return fut.result(timeout=CONTROL_TIMEOUT_S)
        except Exception as e:
            et = e.__class__.__name__
            if et == "RoomError" or hasattr(e, "status"):
                return {"success": False, "error": str(e), "status": getattr(e, "status", 400),
                        "reason_code": f"room.{et.lower()}", "fix_hint": "", "docs_anchor": ""}
            logger.error("[danmaku_listener] 控制面操作异常", exc_info=True)
            return {"success": False, "error": str(e)[:200], "reason_code": "room.internal",
                    "fix_hint": "查看 logs/ 日志", "docs_anchor": ""}

    # ---- 房间 ----

    def set_room_enabled(self, platform: str, room_id: str, enabled: bool) -> dict:
        """窄端点（F1 config-first）：磁盘 RMW 写 rooms[].enabled + 联动该房间启停
        + 内存节刷新（Eng F-ENG-1）；幂等（重复同值返回当前态）。"""
        key = f"{platform}:{room_id}"
        found = {"found": False}

        def mut(sec):
            rooms = sec.setdefault("rooms", [])
            for r in rooms:
                if isinstance(r, dict) and r.get("platform") == platform and                         str(r.get("room_id")) == room_id:
                    r["enabled"] = bool(enabled)
                    found["found"] = True
                    break
        if not _write_config_section(mut) or not found["found"]:
            return {"success": False, "error": f"房间不存在：{key}", "status": 404,
                    "reason_code": "room.not_found",
                    "fix_hint": "刷新平台配置页后重试", "docs_anchor": "docs/danmaku_module.md"}
        if self._bridge is not None and self._loop and not self._loop.is_closed():
            if enabled:
                result = self._submit_room_op(lambda: self._bridge.start_room(platform, room_id))
                if result.get("status") == "login_required":
                    return {"success": True, "listening": False,
                            "status": "login_required", "room_id": room_id}
                return {"success": result.get("success", False),
                        "listening": result.get("success", False),
                        "room": result.get("room"), "error": result.get("error")}
            self._submit_room_op(lambda: self._bridge.stop_room(platform, room_id))
            return {"success": True, "listening": False}
        return {"success": True, "listening": False}  # Bridge 未起——配置已写，启动时注入

    def reload_keywords(self) -> dict:
        """窄端点（M-1 ③）：磁盘 config.keywords 热载 Bridge 内存态（单向流契约
        Eng F-ENG-4：禁止 body 传词表——一律从磁盘读；外置文件以 config 覆写）。"""
        sec = self._section()
        kws = [str(k) for k in (sec.get("keywords") or [])]
        kw_on = bool(sec.get("keyword_filter_enabled", True))
        if self._bridge is None:
            return {"success": False, "error": "服务未运行", "reason_code": "service.not_running"}
        self._bridge._blocked_keywords = kws
        self._bridge._keyword_filter_enabled = kw_on
        self._bridge._save_blocked_keywords()  # 外置文件缓存覆写（约定固化）
        return {"success": True, "count": len(kws), "enabled": kw_on}

    def remove_room(self, platform: str, room_id: str) -> dict:
        result = self._submit_room_op(lambda: self._bridge.remove_room(platform, room_id))
        if result.get("success"):
            def mut(sec):
                sec["rooms"] = [r for r in (sec.get("rooms") or [])
                                if not (isinstance(r, dict) and r.get("platform") == platform
                                        and str(r.get("room_id")) == room_id)]
            _write_config_section(mut)
        return result

    def start_room(self, platform: str, room_id: str) -> dict:
        return self._submit_room_op(lambda: self._bridge.start_room(platform, room_id))

    def stop_room(self, platform: str, room_id: str) -> dict:
        return self._submit_room_op(lambda: self._bridge.stop_room(platform, room_id))

    def get_rooms(self) -> dict:
        if self._bridge is None:
            return {"rooms": self._section().get("rooms") or [], "running": False}
        status = self._bridge.get_status()
        rooms = [dict(r) for r in (status.get("rooms") or {}).values()]
        return {"rooms": rooms, "running": True, "message_count": status.get("message_count", 0)}

    def get_status(self) -> dict:
        sec = self._section()
        alive = self._loop is not None and not self._loop.is_closed()
        return {
            "enabled": bool(sec.get("enabled")),
            "running": alive and self._service_on,
            "red": self._rebuild_count >= REBUILD_GUARD_MAX,
            "rebuild_count": self._rebuild_count,
            "queue_size": self._queue.qsize() if self._queue else 0,
            "dropped_count": self._queue.dropped_count if self._queue else 0,
            "unknown_type_count": self._unknown_type_count,
            "invalid_msg_count": self._invalid_msg_count,
            "self_echo_dropped": self._self_echo_dropped,
            "last_msg_age_s": round(time.time() - self._last_msg_ts, 1) if self._last_msg_ts else None,
            "stall_threshold_s": self._stall_threshold(),
        }

    # ---- 关键词 ----

    # ---- 服务/平台开关 ----

    def set_service_enabled(self, enabled: bool) -> dict:
        """服务总开关（持久写回 enabled；false=stop_all+停消费，true=按 config 恢复）。"""
        _write_config_section(lambda sec: sec.__setitem__("enabled", bool(enabled)))
        if enabled:
            ok = self._resume_service()
            return {"success": ok, "enabled": True}
        if self._bridge is not None and self._loop and not self._loop.is_closed():
            self._submit_room_op(lambda: self._bridge.stop_all())
        self._service_on = False
        self.push_event("service_state", {"running": False})
        return {"success": True, "enabled": False}

    def request_restart(self) -> bool:
        """手动重启（守护置红出路）：清防护计数并重建。"""
        self._rebuild_count = 0
        return self._start_loop_service()

    # ---------- 生命周期 ----------

    def start(self, state_cb: Optional[Callable[[str], None]] = None) -> bool:
        """拉起进程内托管（enabled=true 才构建——build_danmaku_listener_service 已过滤）。"""
        sec = self._section()
        if not sec.get("enabled"):
            logger.info("[danmaku_listener] enabled=false，跳过启动")
            return False
        self._state_cb = state_cb
        self._queue = _BoundedDropQueue(self._queue_max())
        self._stop_evt.clear()
        self._loop_thread = threading.Thread(target=self._loop_main, name="danmaku-bridge-loop", daemon=True)
        self._loop_thread.start()
        self._worker = threading.Thread(target=self._worker_loop, name="danmaku-worker", daemon=True)
        self._worker.start()
        set_service_instance(self)
        return True

    def _loop_main(self) -> None:
        """专用 asyncio daemon 线程主函数（Eng A1/T4：顶层防护 + 停机顺序）。

        守护语义（P1-6）：顶层异常 → 事件上报 → 重建 Bridge；连续 3 次停止重建、置红。
        """
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._async_start())
            self._loop.run_forever()
        except Exception:
            logger.error("[danmaku_listener] 事件循环致命异常", exc_info=True)
            self.push_event("loop_crashed", {
                "reason_code": "service.loop_crashed",
                "fix_hint": "守护将自动重建；连续失败请查看 logs/ 并手动重启" ,
                "docs_anchor": "docs/danmaku_module.md#排障"},
                dedup_key=f"loop_crash:{time.time()}")
            self._rebuild_count += 1
            if self._rebuild_count < REBUILD_GUARD_MAX:
                logger.warning(f"[danmaku_listener] 守护重建（第 {self._rebuild_count} 次）")
                self._stop_evt.clear()
                self._loop_main()  # 重建（递归；计数上限防护）
            else:
                logger.error("[danmaku_listener] 连续 3 次失败——停止重建，状态置红")
                self.push_event("service_red", {"reason_code": "service.guard_tripped",
                                                "fix_hint": "手动重启：服务状态条胶囊 → 重启",
                                                "docs_anchor": "docs/danmaku_module.md#排障"})
                if self._state_cb:
                    try:
                        self._state_cb("red")
                    except Exception:
                        pass
        finally:
            try:
                self._loop.close()
            except Exception:
                pass

    async def _async_start(self) -> None:
        """事件循环内启动：settings 覆写 → Bridge 构造 → rooms/keywords 注入 → web_console。"""
        # Eng E5：组件 settings 的 cookie 路径构造期覆写（serve TOML 通道删除后的替代）
        try:
            from danmaku_listener.config.settings import get_settings, set_settings
            s = get_settings()
            cookie_dir = os.path.abspath(self._cookie_dir())
            if hasattr(s, "cookie_dir"):
                set_settings(s.model_copy(update={"cookie_dir": cookie_dir}))
                logger.info(f"[danmaku_listener] 组件 cookie_dir 已覆写为 {cookie_dir}")
            # 启动断言：cookie/profile 路径在副本外
            if os.path.abspath("danmaku_listener") in cookie_dir:
                logger.warning("[danmaku_listener] cookie_dir 落在组件副本内——copy_danmaku.py 重跑会清除登录态！")
        except Exception:
            logger.error("[danmaku_listener] 组件 settings 覆写失败", exc_info=True)

        bridge_cls = _build_bridge_class()
        self._bridge = bridge_cls(self)

        # rooms 注入 + ghost 对账（Eng A5）：config 为权威源
        await self._inject_rooms()

        # keywords 注入（整体替换语义——Eng A3）
        sec = self._section()
        kws = [str(k) for k in (sec.get("keywords") or [])]
        self._bridge._blocked_keywords = kws
        self._bridge._keyword_filter_enabled = bool(sec.get("keyword_filter_enabled", True))
        self._bridge._save_blocked_keywords()

        # web_console 调试通道（默认关；8080 占用预检——P1-4）
        if sec.get("web_console"):
            await self._start_web_console(int(sec.get("web_console_port") or WEB_CONSOLE_PORT_DEFAULT))

        self._service_on = True
        self.push_event("service_state", {"running": True})
        restored = len(self._bridge._rooms)
        if restored:
            self.push_event("restored_summary",
                            {"message": f"已恢复 {restored} 个房间配置（启用中自动启动）"})
        logger.info(f"[danmaku_listener] 进程内托管已启动（rooms={restored}）")

    async def _inject_rooms(self) -> None:
        """config.rooms 权威源注入：enabled 且平台启用 → add（自动 start）；ghost 对账（Eng A5）。"""
        sec = self._section()
        config_keys = set()
        for r in (sec.get("rooms") or []):
            if not isinstance(r, dict):
                continue
            p, rid = str(r.get("platform", "")), str(r.get("room_id", ""))
            config_keys.add(f"{p}:{rid}")
            if not p or not rid:
                continue
            if not r.get("enabled"):
                continue
            try:
                res = await self._bridge.add_room(rid, p)
                logger.info(f"[danmaku_listener] 注入房间 {p}:{rid} → {res.get('status', 'ok')}")
            except Exception as e:
                logger.error(f"[danmaku_listener] 注入房间 {p}:{rid} 失败: {e}")
        # ghost 对账：组件注册表中不在 config 的条目清除
        for key in list(self._bridge._rooms.keys()):
            if key not in config_keys:
                p, _, rid = key.partition(":")
                try:
                    await self._bridge.remove_room(p, rid)
                    logger.info(f"[danmaku_listener] ghost 房间已对账清除: {key}")
                except Exception:
                    pass

    async def _start_web_console(self, port: int) -> None:
        """web_console=true：组件 web 层绑 127.0.0.1（app['bridge'] 注入 AutoliveBridge——零组件改动）。"""
        # 占用预检（P1-4）
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                self.push_event("web_console_error", {
                    "reason_code": "webconsole.eaddrinuse",
                    "fix_hint": f"释放端口 {port} 或改 web_console_port",
                    "docs_anchor": "docs/danmaku_module.md#排障"})
                return
        except OSError:
            pass
        try:
            from aiohttp import web as aioweb
            from danmaku_listener.web.app import create_app
            app = create_app()
            app["bridge"] = self._bridge
            # R39：发送回执广播接入进程内 Bridge（独立进程形态在组件 __main__.py 接线；
            # web_console 与 Bridge 同环——pipeline await broadcaster 即同环协程调用）
            try:
                from danmaku_listener.senders import wiring as _send_wiring
                _send_wiring.set_result_broadcaster(self._bridge._broadcast)
            except Exception:
                logger.warning("[danmaku_listener] 发送回执广播接线失败（R39 去重降级为不登记）", exc_info=True)
            self._web_runner = aioweb.AppRunner(app)
            await self._web_runner.setup()
            site = aioweb.TCPSite(self._web_runner, "127.0.0.1", port)
            await site.start()
            logger.info(f"[danmaku_listener] web_console 已启动 http://127.0.0.1:{port}（无鉴权，仅本机调试）")
        except Exception:
            logger.error("[danmaku_listener] web_console 启动失败", exc_info=True)

    def _resume_service(self) -> bool:
        """set_service_enabled(true)：守护置红后的恢复路径（重建循环）。"""
        if self._loop is None or self._loop.is_closed():
            self._rebuild_count = 0
            self._loop_thread = threading.Thread(target=self._loop_main, name="danmaku-bridge-loop", daemon=True)
            self._loop_thread.start()
        return True

    def _start_loop_service(self) -> bool:
        """request_restart 出路：重建事件循环线程。"""
        self._resume_service()
        return True

    def stop(self) -> None:
        """关停（Eng T4 顺序写死）：stop_all future 等待 → 停循环 → join。"""
        self._service_on = False
        if self._bridge is not None and self._loop and not self._loop.is_closed():
            try:
                fut = asyncio.run_coroutine_threadsafe(self._bridge.stop_all(), self._loop)
                fut.result(timeout=10)
            except Exception:
                logger.error("[danmaku_listener] stop_all 等待失败（继续停机）", exc_info=True)
        self._stop_evt.set()
        if self._loop and not self._loop.is_closed():
            if self._web_runner is not None:
                async def _cleanup():
                    await self._web_runner.cleanup()
                try:
                    asyncio.run_coroutine_threadsafe(_cleanup(), self._loop).result(timeout=5)
                except Exception:
                    pass
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._loop_thread:
            self._loop_thread.join(timeout=5)
            self._loop_thread = None
        if self._worker:
            self._worker.join(timeout=3)
            self._worker = None
        self._bridge = None
        set_service_instance(None)
        logger.info("[danmaku_listener] 已关停（stop_all 完成/循环停止/线程 join 干净）")

    # ---------- 状态快照（/danmaku/status 数据源） ----------

    def worker_snapshot(self) -> dict:
        return self.get_status()


# ---------- 抖音 GIFT 增量换算（P1-2 义务；组件 TODOS"勿按增量消费"） ----------

def _gift_key(payload: dict, room: str) -> tuple:
    return (room, str(payload.get("user_id") or payload.get("user_name") or ""),
            str(payload.get("gift_id") or payload.get("gift_name") or ""))


def _gift_base_count(payload: dict) -> int:
    try:
        return max(1, int(payload.get("gift_count") or 1))
    except (TypeError, ValueError):
        return 1


def _gift_increment(room: str, payload: dict, lru: OrderedDict) -> int:
    """totalCount → 本次增量。首见视为 1（差额丢弃计数——答谢宁可少报）；
    差值 ≤0 丢弃；LRU 5000 键淘汰最旧（淘汰后首见语义重新触发，安全侧）。"""
    key = _gift_key(payload, room)
    total = _gift_base_count(payload)
    prev = lru.get(key)
    lru[key] = total
    if len(lru) > GIFT_LRU_MAX:
        lru.popitem(last=False)
    if prev is None:
        return 1
    delta = total - prev
    return delta if delta > 0 else 0


# ---------- 线格式归一（保留自上轮：扁平/嵌套双形态兼容） ----------

def _normalize_wire(msg: dict) -> Optional[dict]:
    if not isinstance(msg, dict):
        return None
    payload = msg.get("payload")
    if isinstance(msg.get("envelope"), dict) and isinstance(payload, dict):
        env = msg["envelope"]
    elif isinstance(payload, dict) and msg.get("category"):
        env = msg
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


def _sanitize_danmu_text(text: str) -> str:
    """S-1 最低防线（保留）：长度钳制 + 控制字符/零宽字符剥离。"""
    text = "".join(ch for ch in text if ch.isprintable() and ch not in "​‌‍﻿")
    return text[:200]


def build_danmaku_listener_service(config) -> Optional[DanmakuListenerService]:
    """main.py 托管挂钩工厂（保留原签名）：返回 None 表示未启用。"""
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


# ---------- 聚合器（保留自上轮：B-1 窗口聚合 + LIKE 聚合） ----------

class _LikeAggregator:
    """LIKE 窗口聚合（保留：用户需求"监听到谁点赞"）。"""

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
    """DANMU 窗口聚合（保留：B-1 对话链限速）。"""

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
