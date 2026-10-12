"""常驻发送会话管理器（AutoDanmu T3——抖音 headless=new 实证形态 + 参数化多平台复用）

CEO-F5 探针判定（cards/douyin-ceo-f5-probe-20261007）：bd_ticket_guard 检测的是旧
headless 特征——`--headless=new` 完整 Chrome 指纹下面板渲染 ✓+实发成功 ✓（f5-diag
截图实证）——**常驻会话可无桌面运行**（CEO-F1/F2 部署约束解除；窗口形态保留
minimized/foreground 作 escape hatch，DX-D9 三件套）。

义务映射：
- E5/ENG-1（high）：per-room page + 同一把 asyncio.Lock 串行；空闲关闭必须持锁+
  锁内二次校验活动时间戳（防'发送协程拿到锁后操作已关闭 page'竞态）；每个页面
  操作显式超时（ENG-1：防渲染卡死致锁队列队头阻塞）
- CEO-F8：发送前健康检查（context/page 存活）——会话丢失=自动拉起重试（重启设界
  1 次，ENG-6）；登录失效判定归 sender 的页面配方层（信号如"需先登录"文本）
- ENG-6：SingletonLock 反向场景（douyin_login 扫码持锁期间发送）——启动失败识别
  为独立错误（不自动重启）
- DX-D7：close 失败自愈——close 超时→标记 stale→下次启动前按命令行匹配清理残留
  Chromium 进程（psutil 既有依赖），错误信息指明已清理
- ENG-12：生命周期结构化日志（启动/关闭/重启/清理/撞锁 各一条）
- ENG-4：aclose() 退出钩子（serve 停止时异步关全部会话）
- CEO-F3：发送前 visibilityState 断言入日志（离屏后台化可观测——headless=new 下
  探针实证 visible；遮挡 flag 族仍保留，ENG-8）
- ENG-8：launch flag 族含 --disable-renderer-backgrounding /
  --disable-background-timer-throttling（防渲染后台化与定时器节流）

线程模型：单 asyncio.Lock 内串行全部页面操作；空闲看门狗为独立 task，关闭动作
同样取锁（与发送互斥，ENG-1）；不引入跨进程锁（douyin_login 互斥=Chromium
SingletonLock 天然语义，ENG-6）。
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Awaitable, Callable, Dict, Optional

from loguru import logger
from playwright.async_api import async_playwright  # 模块级：测试注入点（T2 测试卫生先例）

from danmaku_listener.contract.models import SendRejectReason, SendStatus
from danmaku_listener.engines import login_state_store
from danmaku_listener.senders.base import SendResult

PAGE_OP_TIMEOUT_S = 30.0          # ENG-1：单次页面操作超时（防卡死队头阻塞）
CLOSE_TIMEOUT_S = 10.0            # DX-D7：close 超时（超过标记 stale）
RESTART_BUDGET = 1                # ENG-6：单次发送内会话重启上限
VISIBILITY_CHECK_S = 5.0          # visibilityState 断言超时
IDLE_POLL_S = 60                  # 空闲看门狗轮询间隔（秒；测试注入缩短）

#: 会话窗口形态（DX-D9 send_window_mode；headless_new=T1 探针实证无桌面可用）
MODE_HEADLESS_NEW = "headless_new"
MODE_MINIMIZED = "minimized"
MODE_FOREGROUND = "foreground"


def _base_launch_args(mode: str) -> tuple[dict, list]:
    """按窗口形态生成 launch 参数（ENG-8 flag 族全模式保留）"""
    extra = [
        "--disable-renderer-backgrounding",
        "--disable-background-timer-throttling",
        "--mute-audio",  # 2026-10-08 验收用户反馈：后台常驻页播直播流有声——纯后台形态静音
    ]
    if mode == MODE_HEADLESS_NEW:
        # 新无头：headless=False + flag（Chromium 解析；完整 Blink 指纹，无桌面要求）
        return {"headless": False}, extra + ["--headless=new"]
    if mode == MODE_MINIMIZED:
        return {"headless": False}, extra + ["--window-position=-32000,-32000"]
    return {"headless": False}, extra  # foreground


class LoginTakeoverError(Exception):
    """可见登录窗流程未完成（超时/用户关窗）——调用方转 FAILED 回执。"""


def cleanup_stale_chromium(profile_dir: str) -> int:
    """DX-D7：按命令行匹配清理占用 profile 的残留 Chromium 进程；返回清理数"""
    try:
        import psutil
    except ImportError:  # pragma: no cover
        return 0
    killed = 0
    me = __import__("os").getpid()
    for proc in psutil.process_iter(["pid", "cmdline", "name"]):
        try:
            if proc.info["pid"] == me:
                continue
            cmdline = " ".join(proc.info.get("cmdline") or [])
            if profile_dir in cmdline and "chrome" in (proc.info.get("name") or "").lower():
                proc.kill()
                killed += 1
        except Exception:  # noqa: BLE001 进程已退出/权限不足——跳过
            continue
    return killed


class ResidentSendSession:
    """常驻发送会话（单 profile 单实例；参数化 profile/URL/形态——多平台复用同一类）"""

    def __init__(self, name: str, profile_dir: str, *,
                 window_mode: str = MODE_HEADLESS_NEW,
                 idle_timeout_s: int = 1800,
                 user_agent: Optional[str] = None,
                 viewport: Optional[dict] = None,
                 init_scripts: Optional[list] = None,
                 login_cookie_names: tuple = ()):
        self.name = name
        self._profile_dir = profile_dir
        self._window_mode = window_mode
        self._idle_timeout_s = idle_timeout_s
        self._user_agent = user_agent
        self._viewport = viewport or {"width": 1280, "height": 800}

        self._lock = asyncio.Lock()          # ENG-1：会话内全部操作串行
        self._pw_stack: Optional[Any] = None  # async_playwright 上下文（持有以维持生命周期）
        self._pw = None
        self._context = None
        self._pages: Dict[str, Any] = {}      # per-room page（视频号 _held_pages 同构）
        self._last_activity = time.monotonic()
        self._idle_task: Optional[asyncio.Task] = None
        self._stale = False                   # DX-D7：close 失败→下次启动前清理
        self._closed = False
        self._init_scripts = init_scripts or []  # 平台反检测脚本（context 级，全部页面生效）
        self._login_cookie_names = tuple(login_cookie_names)  # F1：登录判定 cookie 名（空=不启用快照）
        self._login_state_path = f"{profile_dir}_login_state.json"

    # ---- 生命周期 ----

    async def _launch(self) -> None:
        """懒启动 persistent context（ENG-12 启动日志；ENG-6 撞锁识别；DX-D7 stale 清理）"""
        if self._stale:
            killed = cleanup_stale_chromium(self._profile_dir)
            logger.info(f"[{self.name}] 会话清理: 移除残留 Chromium 进程 {killed} 个（DX-D7 自愈）")
            self._stale = False
        launch_kwargs, extra_args = _base_launch_args(self._window_mode)
        args = ["--disable-blink-features=AutomationControlled",
                "--disable-setuid-sandbox", "--hide-crash-restore-bubble"] + extra_args
        self._pw_stack = async_playwright()
        self._pw = await self._pw_stack.__aenter__()
        try:
            self._context = await self._pw.chromium.launch_persistent_context(
                self._profile_dir, user_agent=self._user_agent,
                viewport=self._viewport, args=args, **launch_kwargs)
            for script in self._init_scripts:
                await self._context.add_init_script(script)
            await self._restore_login(self._context)  # F1：profile 丢登录时快照自愈
        except Exception as e:  # noqa: BLE001
            await self._pw_stack.__aexit__(None, None, None)
            self._pw_stack = self._pw = None
            msg = str(e)
            if "user data directory" in msg.lower() or "target" in msg.lower() and "closed" in msg.lower():
                # ENG-6：SingletonLock 撞锁（登录 CLI 持锁/残留进程）——独立错误不自动重启
                logger.warning(f"[{self.name}] 会话撞锁: profile 被其他进程占用（登录进行中？）——ENG-6 独立错误")
                raise SessionLockedError(f"profile 被占用（登录进行中或残留进程）: {msg[:120]}") from e
            raise
        self._last_activity = time.monotonic()
        logger.info(f"[{self.name}] 会话启动: mode={self._window_mode} profile={self._profile_dir}")

    async def login_takeover(self, room_id: str, url: str, cookie_names: tuple,
                             grace_s: float = 30.0, timeout_s: float = 240.0) -> Any:
        """关闭当前（无头）context，弹可见登录窗，登录后快照落盘并转无感后台会话。

        背景（2026-10-10 XHS 修复经验移植）：原设计"登录后窗口不关、转正为
        headed 常驻"基于"huya 登录令牌为会话级 cookie"的误判——磁盘取证推翻
        （yyuid 持久至 2026-10-24；XHS id_token 1 年期，同款结论同日废除）。
        新语义：登录 cookie 检测点立即快照（F1，硬杀免疫）→ 保窗宽限（滑块）
        → 关闭可见窗 → 按会话 window_mode 重启（无感后台）→ 快照恢复注入。

        Returns:
            已登录的 per-room page（已导航到 url；_pages/_context 均已登记）。

        Raises:
            LoginTakeoverError: 登录未完成（超时/用户关窗）或无感转正失败。
        """
        from playwright.async_api import async_playwright

        await self.close()          # 释放 profile（关无头 context；pages 清空）
        self._closed = False        # 转正重启需复位 close 语义
        self._pw_stack = async_playwright()
        self._pw = await self._pw_stack.__aenter__()
        try:
            self._context = await self._pw.chromium.launch_persistent_context(
                self._profile_dir, headless=False,
                user_agent=self._user_agent, viewport=self._viewport,
                args=["--disable-blink-features=AutomationControlled",
                      "--disable-setuid-sandbox", "--hide-crash-restore-bubble",
                      "--mute-audio"])
        except Exception as e:
            await self._pw_stack.__aexit__(None, None, None)
            self._pw_stack = self._pw = self._context = None
            raise
        try:
            page = self._context.pages[0] if self._context.pages else await self._context.new_page()
            try:
                await page.goto(url, timeout=45000, wait_until="domcontentloaded")
            except Exception as e:  # noqa: BLE001
                logger.debug(f"[{self.name}] login takeover goto warning: {e}")
            deadline = time.monotonic() + timeout_s
            logged = False
            # baseline 差分（controlled_base._wait_login_visible 同款）：yyuid 等
            # cookie 可能是登录前就种的设备/追踪 cookie（huya 2026-10-10 实证——
            # 注入监听侧 yyuid 后 TT_PROFILE_INFO 无访客身份）——按"登录名 cookie
            # 新出现/值变化"判定，防 takeover 瞬间假成功
            baseline: Dict[str, str] = {}
            try:
                baseline = {c["name"]: (c.get("value") or "")
                            for c in await self._context.cookies()}
            except Exception:  # noqa: BLE001
                baseline = {}
            while time.monotonic() < deadline:
                try:
                    cookies_list = await self._context.cookies()
                except Exception:  # noqa: BLE001  窗口被用户关闭
                    break
                cookies = {c["name"] for c in cookies_list if c.get("value")}
                new_hit = any(c.get("name") in cookie_names
                              and (c.get("value") or "")
                              and c.get("value") != baseline.get(c.get("name"))
                              for c in cookies_list)
                if new_hit:
                    logged = True
                    await self._snapshot_login()  # F1：登录落地立即快照
                    # 保窗 grace：供安全验证（滑块等）——验证完关窗转无感（快照已保底）
                    g = time.monotonic() + grace_s
                    while time.monotonic() < g:
                        try:
                            await self._context.cookies()
                        except Exception:  # noqa: BLE001
                            break
                        await asyncio.sleep(2)
                    await self._snapshot_login()  # F1：宽限后补快照（令牌可能已刷新）
                    break
                try:
                    if page.is_closed():
                        break
                except Exception:  # noqa: BLE001
                    break
                await asyncio.sleep(2)
            if not logged:
                try:
                    await self._context.close()
                except Exception:  # noqa: BLE001
                    pass
                self._pw_stack = self._pw = self._context = None
                raise LoginTakeoverError("登录未完成（超时/关窗）——重试发送将再次弹出登录窗口")
            # 无感转正：关可见窗 → 按会话形态重启（headless_new 完整指纹）→ 快照恢复
            logger.info(f"[{self.name}] login takeover OK——快照落盘，关闭可见窗转"
                        f"无感后台会话（mode={self._window_mode}）")
            try:
                await self._context.close()
            except Exception:  # noqa: BLE001
                pass
            try:
                await self._pw_stack.__aexit__(None, None, None)
            except Exception:  # noqa: BLE001
                pass
            self._pw_stack = self._pw = self._context = None
            self._pages.clear()
            await self._launch()
            page = (self._context.pages[0] if self._context.pages
                    else await asyncio.wait_for(self._context.new_page(),
                                                timeout=PAGE_OP_TIMEOUT_S))
            try:
                await page.goto(url, timeout=45000, wait_until="domcontentloaded")
            except Exception as e:  # noqa: BLE001
                logger.debug(f"[{self.name}] login takeover room goto warning: {e}")
            self._pages[room_id] = page
            self._last_activity = time.monotonic()
            return page
        except LoginTakeoverError:
            raise
        except Exception as e:  # noqa: BLE001  无感转正失败——明确报错不悬挂
            try:
                await self.close()
            except Exception:  # noqa: BLE001
                pass
            self._closed = False
            raise LoginTakeoverError(f"无感转正失败: {type(e).__name__}: {str(e)[:100]}") from e

    async def _snapshot_login(self) -> bool:
        """F1：登录态快照（login_cookie_names 为空=未启用，返回 False）"""
        if not self._login_cookie_names or self._context is None:
            return False
        return await login_state_store.snapshot_login_state(
            self._context, names=self._login_cookie_names,
            path=self._login_state_path, label=self.name)

    async def _restore_login(self, context) -> bool:
        """F1：profile 丢登录 → 快照恢复（未启用返回 False）"""
        if not self._login_cookie_names:
            return False
        return await login_state_store.restore_login_state(
            context, names=self._login_cookie_names,
            path=self._login_state_path, label=self.name)

    async def close(self) -> None:
        """关闭会话（ENG-12 关闭日志；DX-D7 close 超时→stale 标记）"""
        if self._closed:
            return
        self._closed = True
        try:
            if self._context is not None:
                try:
                    await asyncio.wait_for(self._context.close(), timeout=CLOSE_TIMEOUT_S)
                except asyncio.TimeoutError:
                    self._stale = True  # DX-D7：下次启动前清理残留
                    logger.warning(f"[{self.name}] 会话关闭: close 超时 {CLOSE_TIMEOUT_S}s——标记 stale，下次启动前清理")
                except Exception as e:  # noqa: BLE001
                    logger.debug(f"[{self.name}] 会话关闭异常: {type(e).__name__}")
        finally:
            self._pages.clear()
            if self._pw_stack is not None:
                try:
                    await asyncio.wait_for(self._pw_stack.__aexit__(None, None, None), timeout=CLOSE_TIMEOUT_S)
                except Exception:  # noqa: BLE001
                    self._stale = True
            self._context = self._pw = self._pw_stack = None
            logger.info(f"[{self.name}] 会话关闭: 完成（stale={self._stale}）")

    async def aclose(self) -> None:
        """ENG-4 退出钩子：serve 停止时调用（含空闲看门狗取消）"""
        if self._idle_task is not None:
            self._idle_task.cancel()
            self._idle_task = None
        await self.close()

    # ---- 空闲看门狗（ENG-1：关闭动作取同一把锁+锁内二次校验）----

    def _ensure_idle_watchdog(self) -> None:
        if self._idle_task is None or self._idle_task.done():
            self._idle_task = asyncio.create_task(self._idle_watchdog(),
                                                  name=f"{self.name}-idle")

    async def _idle_watchdog(self) -> None:
        while not self._closed:
            await asyncio.sleep(min(self._idle_timeout_s, IDLE_POLL_S))
            if self._closed or self._context is None:
                continue
            idle_for = time.monotonic() - self._last_activity
            if idle_for < self._idle_timeout_s:
                continue
            async with self._lock:  # ENG-1：与发送互斥
                # 锁内二次校验（ENG-1 核心：拿到锁后发送可能刚恢复活动）
                idle_for = time.monotonic() - self._last_activity
                if idle_for < self._idle_timeout_s or self._closed or self._context is None:
                    continue
                logger.info(f"[{self.name}] 会话空闲关闭: 空闲 {int(idle_for)}s 超阈值")
                await self.close()
                return

    # ---- 发送入口 ----

    async def send(self, room_id: str, room_url: str,
                   send_action: Callable[[Any, str], Awaitable[SendResult]],
                   action_timeout_s: float | None = None) -> SendResult:
        """在常驻会话的 per-room page 上执行 send_action(page, room_id)

        send_action 由平台 sender 提供（DOM 配方/回显判定/登录失效检测）——
        会话管理器只管生命周期（E5/ENG-1/CEO-F8），不掺发送配方。
        """
        async with self._lock:
            if self._closed:
                self._closed = False
            if self._context is None:
                restarts = 0
                while True:
                    try:
                        await self._launch()
                        break
                    except SessionLockedError:
                        return SendResult(SendStatus.FAILED, SendRejectReason.PLATFORM_REJECTED.value,
                                          fix_hint="会话 profile 被占用（抖音登录进行中或残留进程）——"
                                                   "稍后重试或完成登录后再发")
                    except Exception as e:  # noqa: BLE001
                        restarts += 1
                        if restarts > RESTART_BUDGET:  # ENG-6：重启设界
                            return SendResult(SendStatus.FAILED, SendRejectReason.PLATFORM_REJECTED.value,
                                              detail=f"会话启动失败: {type(e).__name__}: {str(e)[:100]}",
                                              fix_hint="会话反复启动失败——检查 profile 目录与登录态")
                        logger.warning(f"[{self.name}] 会话重启: 第 {restarts}/{RESTART_BUDGET} 次"
                                       f"（{type(e).__name__}: {str(e)[:120]}）")

            # CEO-F8：健康检查（context/page 存活）——会话丢失自动拉起（重启设界内）
            page = self._pages.get(room_id)
            healthy = False
            try:
                healthy = (page is not None and not page.is_closed()
                           and self._context is not None and len(self._context.pages) >= 0)
            except Exception:  # noqa: BLE001
                healthy = False
            if not healthy:
                logger.info(f"[{self.name}] 会话健康检查: room={room_id} 页面失效——自动拉起（CEO-F8）")
                self._pages.pop(room_id, None)
                page = None

            # per-room page 懒创建（playwright timeout 参数=毫秒；asyncio.wait_for=秒——双层保护）
            # 挂页失败重试一次（2026-10-09 虎牙实证：用户关闭转正窗口→context 死亡，
            # 首次 new_page 撞死 context 报 TargetClosedError——全量重启浏览器后可自愈；
            # 重启后若会话失效，由 action 层登录检测触发转正登录窗）
            for mount_attempt in range(2):
                try:
                    page = await asyncio.wait_for(self._context.new_page(), timeout=PAGE_OP_TIMEOUT_S)
                    await asyncio.wait_for(page.goto(room_url, timeout=PAGE_OP_TIMEOUT_S * 1000,
                                                     wait_until="domcontentloaded"), timeout=PAGE_OP_TIMEOUT_S + 15)
                    break
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"[{self.name}] 页面挂载失败(第{mount_attempt + 1}次): "
                                   f"{type(e).__name__}: {str(e)[:80]}——全量重启浏览器重试")
                    await self.close()          # 全量关闭（含死亡 context）
                    self._closed = False
                    if mount_attempt == 0:
                        await self._launch()    # 全量重启浏览器（无头形态）
                        continue
                    return SendResult(SendStatus.UNKNOWN, SendRejectReason.SEND_TIMEOUT.value,
                                      detail=f"挂页失败(重试后仍失败): {type(e).__name__}",
                                      fix_hint="页面导航超时/失败——重试将使用全新会话")
            else:
                return SendResult(SendStatus.UNKNOWN, SendRejectReason.SEND_TIMEOUT.value,
                                  detail="挂页失败: 重试耗尽")
            self._pages[room_id] = page
            logger.info(f"[{self.name}] 页面挂载: room={room_id}（per-room 常驻）")

            # CEO-F3：visibilityState 断言（离屏后台化可观测）
            try:
                vis = await asyncio.wait_for(page.evaluate("document.visibilityState"),
                                             timeout=VISIBILITY_CHECK_S)
                if vis != "visible":
                    logger.warning(f"[{self.name}] visibilityState={vis}（room={room_id}）——离屏后台化风险信号（CEO-F3）")
            except Exception as e:  # noqa: BLE001 断言失败不阻断发送
                logger.debug(f"[{self.name}] visibilityState 探测失败: {type(e).__name__}")

            # 页面配方执行（ENG-1：显式超时——超时会话级重置）
            self._last_activity = time.monotonic()
            try:
                result = await asyncio.wait_for(send_action(page, room_id),
                                                timeout=action_timeout_s or PAGE_OP_TIMEOUT_S * 3)
            except asyncio.TimeoutError:
                logger.warning(f"[{self.name}] 会话重置: 发送操作超时 {PAGE_OP_TIMEOUT_S*3}s（ENG-1）——关闭待重建")
                await self.close()
                self._closed = False
                return SendResult(SendStatus.UNKNOWN, SendRejectReason.SEND_TIMEOUT.value,
                                  detail=f"页面操作超时（会话已重置）",
                                  fix_hint="页面渲染卡死触发会话重置——重试将使用全新会话")
            self._last_activity = time.monotonic()
            self._ensure_idle_watchdog()
            return result


class SessionLockedError(RuntimeError):
    """ENG-6：SingletonLock 撞锁（profile 被登录流程/残留进程占用）——独立错误，不自动重启"""
