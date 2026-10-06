# -*- coding: utf-8 -*-
"""
DanmakuListener 配置期登录服务（整合第二轮 P3-1，webui 进程内轻量后台）

配置期（main.py 未运行）的登录闭环：
    添加房间 → check_login(platform) → 未登录 → run_login(platform)
    → 可见浏览器扫码（组件 run_login_flow）→ cookie 保存 cookie_dir
    → main.py 运行时引擎读取同目录登录态（跨进程接力）

范围裁决（Eng F-ENG-13）：仅 bilibili/kuaishou/douyin 三平台——组件
`*_login.py` 的 has_login_cookie()/run_login_flow() 可直接 import；
其余平台由运行期 login_gate 全平台登录闭环覆盖（上游已优化）。

平台登录元数据单源（Eng F-ENG-12）：本表为 webui 侧单源（上游
LOGIN_COOKIE_NAMES 无这三平台——不依赖它，否则恒判未登录误弹窗）。
上游演进同步点：danmaku_listener/engines/{bilibili,kuaishou,douyin}_login.py。

状态机（Design M-3）：idle → queued → launching → waiting_scan → saving
→ success / failed(user_closed) / failed(launch_failed) / timeout。
非模态（Design A-1）：后台串行队列，页面状态 chip 呈现，配置操作照常可用。
日志：对齐上游 login.* 词表（DX 发现 9），cookie 值一律掩码（Eng F-ENG-11）。
"""
from __future__ import annotations

import asyncio
import os
import threading
import time
from typing import Any, Callable, Optional

import json

from utils.my_log import logger

# 登录窗口参数（DX 发现 8b：参数化默认沿用上游值；仅作用于本服务——
# 运行期引擎用组件模块常量，作用域差异已在 README 注明）
LOGIN_WAIT_TIMEOUT = 300.0
LOGIN_POLL_INTERVAL = 2.0


def _cookie_dir() -> str:
    """cookie_dir 单源（config.danmaku_listener.cookie_dir，默认仓库根 cookie/）。"""
    try:
        from frontend.config.settings import get_config
        return str(get_config().get("danmaku_listener", "cookie_dir",
                                    default=os.path.join("cookie")))
    except Exception:
        return os.path.join("cookie")


class DanmakuLoginService:
    """配置期登录服务（模块级单例；串行队列；每状态迁移结构化日志）。"""

    def __init__(self):
        # RLock（可重入）：request_login 持锁后调 _set_state（同线程再取锁）——
        # threading.Lock 不可重入会自死锁，冻结 NiceGUI 事件循环（investigate 实测栈）
        self._lock = threading.RLock()
        self._queue: list[dict] = []          # 待处理登录请求（串行）
        self._state: dict = {"platform": None, "status": "idle", "detail": "",
                             "ts": 0.0}
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    # ---------- 平台元数据单源（Eng F-ENG-12） ----------

    def _meta(self, platform: str) -> Optional[dict]:
        cd = _cookie_dir()
        meta = {
            "bilibili": {
                "check": lambda: self._check_state_file(
                    os.path.join(cd, "bilibili_storage_state.json")),
                "flow": lambda: self._run_flow(
                    "danmaku_listener.engines.bilibili_login",
                    state_path=os.path.join(cd, "bilibili_storage_state.json")),
            },
            "kuaishou": {
                "check": lambda: self._check_state_file(
                    os.path.join(cd, "kuaishou_storage_state.json")),
                "flow": lambda: self._run_flow(
                    "danmaku_listener.engines.kuaishou_login",
                    state_path=os.path.join(cd, "kuaishou_storage_state.json")),
            },
            "douyin": {
                "check": lambda: self._check_cookies_file(
                    os.path.join(cd, "douyin_cookies.json")),
                "flow": lambda: self._run_flow(
                    "danmaku_listener.engines.douyin_login",
                    cookie_path=os.path.join(cd, "douyin_cookies.json")),
            },
        }
        return meta.get(platform)

    @staticmethod
    def _check_state_file(path: str) -> bool:
        """storage_state JSON 存在且含非空 cookie 数组（Eng F-ENG-10 fixture 判定同口径）。"""
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            cookies = data.get("cookies") if isinstance(data, dict) else data
            return bool(cookies)
        except (FileNotFoundError, json.JSONDecodeError, TypeError):
            return False

    @staticmethod
    def _check_cookies_file(path: str) -> bool:
        """douyin cookies.json：存在且任一条目有 sessionid 标志（douyin_login 口径）。"""
        try:
            from danmaku_listener.engines.douyin_login import has_login_cookie
            return has_login_cookie(path)
        except Exception:
            return False

    # ---------- 对外接口 ----------

    def check_login(self, platform: str) -> bool:
        """平台登录态判定（配置期首登引导；运行期引擎事件为唯一真相源——CEO F7 契约）。"""
        meta = self._meta(platform)
        if meta is None:
            return False
        try:
            return bool(meta["check"]())
        except Exception:
            logger.warning(f"[danmaku-login-svc] {platform} 登录态判定异常", exc_info=True)
            return False

    def login_supported(self, platform: str) -> bool:
        return self._meta(platform) is not None

    def request_login(self, platform: str) -> dict:
        """发起登录（串行队列入队；webui 轮询 _state 呈现进度）。"""
        if not self.login_supported(platform):
            return {"success": False, "error": f"平台 {platform} 不支持配置期登录",
                    "fix_hint": "启动系统后首次监听时按引导登录"}
        with self._lock:
            if self._state.get("platform") == platform and \
                    self._state.get("status") in ("queued", "launching", "waiting_scan", "saving"):
                return {"success": True, "status": self._state["status"], "already": True}
            self._queue = [q for q in self._queue if q["platform"] != platform]
            self._queue.append({"platform": platform, "ts": time.time()})
            if self._state.get("status") not in ("queued", "launching", "waiting_scan", "saving"):
                self._set_state(platform, "queued", "")
        self._ensure_thread()
        return {"success": True, "status": self._state["status"]}

    def get_state(self) -> dict:
        with self._lock:
            return dict(self._state)

    def shutdown(self) -> None:
        """webui 退出钩子：停轮询线程（Eng F-ENG-8 退出清理）。"""
        self._stop.set()

    # ---------- 内部：串行队列线程 ----------

    def _set_state(self, platform: Optional[str], status: str, detail: str) -> None:
        with self._lock:
            self._state = {"platform": platform, "status": status,
                           "detail": detail, "ts": time.time()}
        logger.info(f"[danmaku-login-svc] login.{status} platform={platform} {detail}")

    def _ensure_thread(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._queue_loop, name="danmaku-login-svc", daemon=True)
        self._thread.start()

    def _queue_loop(self) -> None:
        """串行消费登录队列（同一时刻仅一个登录流——Design M-3）。"""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            while not self._stop.is_set():
                with self._lock:
                    item = self._queue.pop(0) if self._queue else None
                if item is None:
                    self._stop.wait(0.5)
                    continue
                try:
                    self._process(item["platform"])
                except Exception:
                    logger.error(f"[danmaku-login-svc] {item['platform']} 登录流程异常", exc_info=True)
                    self._set_state(item["platform"], "failed", "登录流程异常")
        finally:
            self._loop.close()

    def _process(self, platform: str) -> None:
        meta = self._meta(platform)
        if meta is None:
            return
        if self.check_login(platform):
            self._set_state(platform, "success", "已有登录态，无需重复登录")
            return
        self._set_state(platform, "launching", "浏览器启动中")
        started = time.time()
        # 组件 run_login_flow：可见浏览器 → 扫码 → cookie 保存（阻塞至完成/超时/关闭）
        self._loop.run_until_complete(meta["flow"]())
        elapsed = time.time() - started
        if self.check_login(platform):
            self._set_state(platform, "success", f"登录成功（{elapsed:.0f}s，浏览器已关闭）")
        elif elapsed >= LOGIN_WAIT_TIMEOUT - 5:
            self._set_state(platform, "timeout", "登录超时——可重试")
        else:
            self._set_state(platform, "failed", "登录未完成（浏览器已关闭）——可重试")

    def _run_flow(self, module_name: str, **kwargs) -> None:
        """组件 run_login_flow 统一调用（login.waiting_scan/saving 迁移日志）。"""
        import importlib
        mod = importlib.import_module(module_name)
        self._set_state(self._state["platform"], "waiting_scan",
                        "请在弹出的浏览器中完成扫码/登录")
        # 组件函数内部轮询登录标志 cookie（30 行注释见 douyin_login）
        coro = mod.run_login_flow(**kwargs)
        # 组件 run_login_flow 内部有登录标志检测循环；超时由组件控制
        self._loop.run_until_complete(asyncio.wait_for(coro, timeout=LOGIN_WAIT_TIMEOUT))
        self._set_state(self._state["platform"], "saving", "登录态保存中")


# 模块级单例（Eng F-ENG-8）
_login_service: Optional[DanmakuLoginService] = None


def get_login_service() -> DanmakuLoginService:
    global _login_service
    if _login_service is None:
        _login_service = DanmakuLoginService()
    return _login_service

