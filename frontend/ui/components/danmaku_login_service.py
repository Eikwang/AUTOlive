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
        # douyu/huya：上游 v0.5.0 login_gate.ensure_cookie_file_login（cookie 文件形态，
        # TCP 协议无注入路径——登录态供上游对账，当前协议直连游客可达）。
        # 参数与引擎调用点逐一对齐（douyu.py:138 / huya.py:118，login_url 引擎同源）。
        if platform in ("douyu", "huya"):
            login_urls = {"douyu": "https://passport.douyu.com/",
                          "huya": "https://www.huya.com/"}
            cookie_path = os.path.join(cd, f"{platform}_login_cookies.json")
            # 判定名对齐 login_gate.LOGIN_COOKIE_NAMES 权威（douyu 曾用过时 dedeuserid
            # ——gate 取证实录：36 cookie 无 dedeuserid，acf_* 家族才是登录字段）
            names = {"douyu": ("acf_uid", "acf_auth"),
                     "huya": ("yyuid", "hiido_ui", "u_db_uid")}
            meta[platform] = {
                "check": lambda cp=cookie_path, nm=names[platform]:
                    self._check_cookie_file_names(cp, nm),
                "flow": lambda p=platform, lp=login_urls[platform], cp=cookie_path:
                    self._run_gate_flow(p, lp, cp),
            }
        # 受控平台（v0.7.0 全平台覆盖——除美团外）：check=F1 快照文件存在且含 cookie
        # （storage_state 格式，_check_state_file 兼容）；flow=persistent profile 登录。
        for p, (prof, url) in self._PROFILE_PLATFORMS.items():
            meta[p] = {
                "check": lambda sp=os.path.join(cd, f"{prof}_login_state.json"):
                    self._check_state_file(sp),
                "flow": lambda p=p, pn=prof, u=url: self._run_profile_flow(p, pn, u),
            }
        # e3c31330 五平台扩展时回归：必须返回单平台条目（返回整表则 meta["check"] KeyError
        # ——登录态判定异常+登录流程异常双失败，2026-10-12 手测发现）
        return meta.get(platform)

    @staticmethod
    def _check_cookie_file_names(path: str, names: tuple) -> bool:
        """cookie 文件 JSON 的登录态判定（login_gate.has_login_cookie 同口径）。"""
        try:
            with open(path, "r", encoding="utf-8") as f:
                stored = json.load(f)
            from danmaku_listener.engines.login_gate import has_login_cookie
            return has_login_cookie(stored.get("cookies") or [], names)
        except (FileNotFoundError, json.JSONDecodeError, Exception):
            return False

    def _run_gate_flow(self, platform: str, login_url: str, cookie_path: str):
        """douyu/huya 配置期登录（返回 coro——_process 统一 run_until_complete；
        曾在内部自行执行后 return None → _process 对 None 再 run_until_complete 炸）。"""
        from danmaku_listener.engines.login_gate import ensure_cookie_file_login

        async def emit_status(detail: str) -> None:
            self._set_state(platform, "waiting_scan", detail)

        self._set_state(platform, "waiting_scan",
                        f"请在弹出的浏览器中登录 {platform} 账号")
        return ensure_cookie_file_login(
            "config-login", platform, login_url, cookie_path,
            {}, {}, set(), emit_status)

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
            logger.opt(exception=True).warning(f"[danmaku-login-svc] {platform} 登录态判定异常")
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

    def request_relogin(self, platform: str) -> dict:
        """清除平台登录载体后重新发起登录（房间行"登录"按钮——配置期路径，2026-10-13）。

        载体清单与安全边界与运行期单源（danmaku_listener.web.relogin——profile
        目录/快照/cookie 文件）；清除后 request_login 入队弹配置期登录窗。
        meituan 无载体定义不支持。运行期路径走控制面 /danmaku/relogin（登录门
        在 main 进程弹窗），两路径互斥由前端 mode 分流保证。
        """
        try:
            from danmaku_listener.web.relogin import clear_login_state, ReloginUnsupported
        except Exception as e:  # noqa: BLE001
            return {"success": False, "error": f"组件 relogin 模块不可用: {e}"}
        try:
            deleted = clear_login_state(platform, _cookie_dir())
        except ReloginUnsupported:
            return {"success": False,
                    "error": f"平台 {platform} 不支持手动重登（无登录载体定义）"}
        except Exception as e:  # noqa: BLE001
            logger.opt(exception=True).error(f"[danmaku-login-svc] {platform} 载体清除失败")
            return {"success": False, "error": f"登录载体清除失败: {e}"}
        r = self.request_login(platform)
        r["cleared"] = deleted
        return r

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
                    # loguru 无 exc_info 参数（曾静默吞栈——手测排查三小时）；opt(exception=True) 才带栈
                    logger.opt(exception=True).error(f"[danmaku-login-svc] {item['platform']} 登录流程异常")
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
        # 外层 wait_for 兜底防挂死（受控平台 coro 内部 deadline 300s+宽限 30s+goto 45s）
        self._loop.run_until_complete(
            asyncio.wait_for(meta["flow"](), timeout=LOGIN_WAIT_TIMEOUT + 120))
        elapsed = time.time() - started
        if self.check_login(platform):
            self._set_state(platform, "success", f"登录成功（{elapsed:.0f}s，浏览器已关闭）")
        elif elapsed >= LOGIN_WAIT_TIMEOUT - 5:
            self._set_state(platform, "timeout", "登录超时——可重试")
        else:
            self._set_state(platform, "failed", "登录未完成（浏览器已关闭）——可重试")

    def _run_flow(self, module_name: str, **kwargs):
        """组件 run_login_flow 统一调用（返回 coro——_process 统一 run_until_complete）。"""
        import importlib
        mod = importlib.import_module(module_name)
        self._set_state(self._state["platform"], "waiting_scan",
                        "请在弹出的浏览器中完成扫码/登录")
        # 组件函数内部有登录标志检测循环；超时由组件/服务层 wait_for 控制
        return mod.run_login_flow(**kwargs)

    # ---------- 受控平台配置期登录（v0.7.0 全平台覆盖——除美团外） ----------

    #: 受控平台注册表：profile 名与快照路径对齐运行期引擎（controlled_base
    #: _profile_dir/_login_state_path 与 web/relogin.py PLATFORM_LOGIN_CARRIERS 同源）；
    #: meituan 按需求不实现。判定名缺省平台（pdd/wxsp——上游待实测校准）走"关窗确认"。
    _PROFILE_PLATFORMS = {
        "taobao": ("taobao_profile", "https://login.taobao.com/"),
        "1688": ("1688_profile", "https://login.1688.com/"),
        "xiaohongshu": ("xhs_profile", "https://www.xiaohongshu.com/"),
        "pdd": ("pdd_profile", "https://www.pinduoduo.com/"),
        "jd": ("jd_profile", "https://passport.jd.com/new/login.aspx"),
        "wechat_channels": ("wxsp_profile", "https://channels.weixin.qq.com/"),
    }

    def _run_profile_flow(self, platform: str, profile_name: str, login_url: str):
        """受控平台配置期登录（返回 coro）：persistent profile + F1 快照自愈。

        判定名命中（taobao/1688/jd/xhs）→ 快照落盘+30s 保窗宽限；判定名缺省
        （pdd/wxsp——LOGIN_COOKIE_NAMES 无条目，上游待实测）→ 用户关窗即确认，
        全量 storage_state 落盘（快照格式与运行期一致，判定名校准后自动收敛）。
        """
        return self._profile_login_coro(platform, profile_name, login_url)

    async def _profile_login_coro(self, platform: str, profile_name: str,
                                  login_url: str) -> str:
        from pathlib import Path

        from danmaku_listener.engines import login_state_store
        from danmaku_listener.engines.login_gate import LOGIN_COOKIE_NAMES
        from playwright.async_api import async_playwright

        cd = _cookie_dir()
        profile = Path(os.path.join(cd, profile_name))
        profile.mkdir(parents=True, exist_ok=True)
        state_path = os.path.join(cd, f"{profile_name}_login_state.json")
        names = tuple(LOGIN_COOKIE_NAMES.get(platform, ()))
        outcome = "timeout"
        async with async_playwright() as pw:
            context = await pw.chromium.launch_persistent_context(
                str(profile), headless=False,
                user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/131.0.0.0 Safari/537.36"),
                viewport={"width": 1280, "height": 800},
                args=["--disable-blink-features=AutomationControlled",
                      "--hide-crash-restore-bubble"])
            try:
                # F1 快照自愈（profile 丢登录时注入——运行期同语义）
                await login_state_store.restore_login_state(
                    context, names=names, path=state_path, label=f"{platform}-config")
                page = context.pages[0] if context.pages else await context.new_page()
                self._set_state(platform, "waiting_scan",
                                f"请在弹出的浏览器中登录 {platform} 账号"
                                + ("" if names else "（完成后关闭浏览器窗口即可保存）"))
                try:
                    await page.goto(login_url, timeout=45000, wait_until="domcontentloaded")
                except Exception as e:  # noqa: BLE001  网络抖动不阻断（用户可在窗口内手动导航）
                    logger.debug(f"[danmaku-login-svc] {platform} goto warning: {e}")
                deadline = time.monotonic() + LOGIN_WAIT_TIMEOUT
                last_cookies = []
                while time.monotonic() < deadline:
                    if self._stop.is_set():
                        outcome = "stopped"
                        break
                    try:
                        cookies = await context.cookies()
                        last_cookies = cookies
                    except Exception:  # noqa: BLE001  窗口被关
                        cookies = last_cookies
                    hit = bool(names) and bool(
                        {c.get("name") for c in cookies if c.get("value")} & set(names))
                    if hit:
                        # 登录落地即快照 + 30s 保窗宽限（登录后立即关窗会杀安全验证——
                        # 虎牙/淘宝/抖音同款缺陷，send_login.py 范式）
                        await login_state_store.snapshot_login_state(
                            context, names=names, path=state_path, label=f"{platform}-config")
                        self._set_state(platform, "saving", "登录态保存中（30s 保窗宽限）")
                        grace = time.monotonic() + 30.0
                        while time.monotonic() < grace:
                            try:
                                await context.cookies()
                            except Exception:
                                break
                            await asyncio.sleep(2)
                        outcome = "logged_in"
                        break
                    try:
                        if page.is_closed():
                            # 关窗确认制：无判定名平台（pdd/wxsp）由用户关窗表示登录完成；
                            # 有判定名平台关窗时最后已知 cookie 命中同样落盘（gate 同款恢复语义）
                            if last_cookies:
                                import json as _json
                                state = await context.storage_state() if not page.is_closed() else {
                                    "cookies": last_cookies, "origins": []}
                                tmp = Path(state_path + ".tmp")
                                tmp.write_text(_json.dumps(state, ensure_ascii=False),
                                               encoding="utf-8")
                                tmp.replace(state_path)
                                outcome = "logged_in"
                            else:
                                outcome = "window_closed"
                            break
                    except Exception:  # noqa: BLE001  页面已关（cookies 也失效）
                        if last_cookies:
                            import json as _json
                            Path(state_path).write_text(
                                _json.dumps({"cookies": last_cookies, "origins": []},
                                            ensure_ascii=False), encoding="utf-8")
                            outcome = "logged_in"
                        else:
                            outcome = "window_closed"
                        break
                    await asyncio.sleep(2)
            finally:
                try:
                    await context.close()
                except Exception:  # noqa: BLE001
                    pass
        return outcome
        self._set_state(self._state["platform"], "saving", "登录态保存中")


# 模块级单例（Eng F-ENG-8）
_login_service: Optional[DanmakuLoginService] = None


def get_login_service() -> DanmakuLoginService:
    global _login_service
    if _login_service is None:
        _login_service = DanmakuLoginService()
    return _login_service

