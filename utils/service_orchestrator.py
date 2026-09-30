# -*- coding: UTF-8 -*-
"""
AUTOlive 托管服务编排模块（P1-1）

通用 ManagedProcess 类：GPT-SoVITS api_v2 / RVC 包裹服务 / 翻唱 worker 共用（CEO-S5）。

状态机：
    stopped → starting → healthy ──(子进程死亡或健康检查失败)→ crashed(自动重拉)
        ↑                        │ 连续失败 ≥ crash_loop_max（默认 3）
        └────── 手动重启(清零计数) ──┴──→ red（停止自动重拉，置红告警）

行为契约（全部来自批准计划，义务块可追溯）：
- 拉起前端口占用探测 + 占用者 PID/进程名归因（DX-1/B-4）：本系统残留子进程
  → 自动清理重试；外部进程 → 冲突文案 problem/cause/fix（直连
  gpt_sovits.hosted=false 逃逸口）；归因失败计入 crash-loop 计数。
- 健康检查超时与拉起失败共用同一计数器（T-3），超时时长入 policies 键化。
- 连续 3 次失败停止自动重拉、置红（失控防护，CEO-P1-3）；手动重启成功清零
  回健康态（D10）。
- 崩溃自动重拉由监控线程执行；健康轮询周期/超时均入 policies 键化（DX-3）。
- 子进程 stdout/stderr 落 logs/services/<name>.log，日志尾可查询。
- 托管服务必须显式绑定 127.0.0.1（E-1 硬条款），本模块在启动前校验。
"""
import os
import socket
import subprocess
import threading
import time
from typing import Callable, Optional

import psutil

from utils.my_log import logger

# 默认策略值（可被 config audio_integration.policies 覆盖，DX-3 键化）
DEFAULT_POLICIES = {
    "health_poll_interval_s": 30,   # 健康轮询周期
    "health_timeout_s": 10,         # 单次健康检查超时（与拉起失败共用计数，T-3）
    "start_ready_timeout_s": 120,   # 拉起后就绪判定窗口
    "crash_loop_max": 3,            # 连续失败上限，超过置红停拉
    "port": 9880,                   # 服务端口（占用探测用）
}

# 状态常量
ST_STOPPED = "stopped"
ST_STARTING = "starting"
ST_HEALTHY = "healthy"
ST_CRASHED = "crashed"   # 自动重拉进行中
ST_RED = "red"           # crash-loop 触发，停止自动重拉


class PortConflictError(Exception):
    """端口被占用。problem/cause/fix 三要素齐备，供 UI 直接展示（DX-1）。"""

    def __init__(self, port: int, pid: Optional[int], pname: str, own_child: bool):
        self.port = port
        self.pid = pid
        self.pname = pname
        self.own_child = own_child
        if own_child:
            fix = "系统将自动清理残留进程并重试拉起"
        else:
            fix = (
                f"结束占用进程（PID {pid}），或设 gpt_sovits.hosted=false "
                f"改为挂入外部进程"
            )
        super().__init__(
            f"端口 {port} 已被占用（PID {pid}，进程 {pname}）。"
            f"原因：{'本系统上次运行残留的服务进程' if own_child else '外部程序占用了该端口'}。"
            f"修复：{fix}"
        )


class ManagedProcess:
    """
    通用托管子进程。

    Args:
        name: 服务名（日志文件与状态条胶囊标识）
        command: 子进程命令行（list）。含绑定地址的元素必须为 127.0.0.1（E-1）。
        cwd: 子进程工作目录
        env: 附加环境变量（如 PYTHONPATH 注入，P1-2）
        health_url: GET 探活地址（如 http://127.0.0.1:9880/docs）
        port: 占用探测端口
        policies: 行为参数（覆盖 DEFAULT_POLICIES，DX-3）
        on_state_change: 状态变化回调（状态条订阅）
        warmup: 就绪后的一次性预热钩子（CEO-S7，如 TTS 预热合成）
        health_probe: 自定义探活回调（无 HTTP 健康端点的服务用 TCP 探测，
            如 DanmakuListener WS 服务；传入时优先于 health_url）
    """

    def __init__(
        self,
        name: str,
        command: list,
        cwd: str,
        env: Optional[dict] = None,
        health_url: str = "",
        port: int = DEFAULT_POLICIES["port"],
        policies: Optional[dict] = None,
        on_state_change: Optional[Callable[[str, "ManagedProcess"], None]] = None,
        warmup: Optional[Callable[["ManagedProcess"], None]] = None,
        health_probe: Optional[Callable[["ManagedProcess"], bool]] = None,
    ):
        self.name = name
        self.command = list(command)
        self.cwd = cwd
        self.env = env
        self.health_url = health_url
        self.port = port
        self.policies = {**DEFAULT_POLICIES, **(policies or {})}
        self.on_state_change = on_state_change
        self.warmup = warmup
        self.health_probe = health_probe

        # E-1 硬条款：命令行不得包含非回环绑定地址
        for arg in self.command:
            if isinstance(arg, str) and arg.replace("http://", "").replace(
                "https://", ""
            ).startswith(("0.0.0.0",)):
                raise ValueError(
                    f"[{self.name}] E-1 违约：托管服务禁止绑定非回环地址（0.0.0.0）"
                )

        self.state = ST_STOPPED
        self.proc: Optional[subprocess.Popen] = None
        self.fail_count = 0  # 拉起失败+健康失败共用计数器（T-3）
        self._monitor_stop = threading.Event()
        self._monitor_thread: Optional[threading.Thread] = None
        self._state_lock = threading.Lock()

        log_dir = os.path.join("logs", "services")
        os.makedirs(log_dir, exist_ok=True)
        self.log_path = os.path.join(log_dir, f"{name}.log")

    # ---------- 状态流转 ----------

    def _set_state(self, new_state: str):
        with self._state_lock:
            if self.state == new_state:
                return
            self.state = new_state
        logger.info(f"[{self.name}] 状态: {self.state} (连续失败 {self.fail_count})")
        if self.on_state_change:
            try:
                self.on_state_change(self.state, self)
            except Exception:
                logger.error(f"[{self.name}] 状态回调异常", exc_info=True)

    # ---------- 端口占用探测与归因（DX-1/B-4）----------

    def _find_port_occupier(self) -> Optional[psutil.Process]:
        """返回占用目标端口的进程；无占用返回 None。"""
        for conn in psutil.net_connections(kind="tcp"):
            if (
                conn.status == psutil.CONN_LISTEN
                and conn.laddr.port == self.port
                and conn.pid
            ):
                try:
                    return psutil.Process(conn.pid)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    return None
        return None

    def _handle_port_conflict(self):
        """端口占用归因处置：本系统残留子进程自动清理；外部进程抛三要素异常。"""
        occ = self._find_port_occupier()
        if occ is None:
            return  # 无占用（探测窗口竞态已自行释放，TOCTOU 容忍）
        own = False
        try:
            if self.proc and occ.pid == self.proc.pid:
                own = True
            elif occ.name().lower().startswith(("python", "api_v2")):
                # 命令行含本模块管理的 api_v2 判定为残留
                cmdline = " ".join(occ.cmdline() or [])
                own = "api_v2.py" in cmdline
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            own = False
        if own:
            logger.warning(f"[{self.name}] 端口 {self.port} 被本系统残留进程占用（PID {occ.pid}），自动清理")
            try:
                occ.terminate()
                occ.wait(timeout=5)
            except (psutil.NoSuchProcess, psutil.TimeoutExpired):
                try:
                    occ.kill()
                except psutil.NoSuchProcess:
                    pass
            time.sleep(1)
            return
        raise PortConflictError(self.port, occ.pid, occ.name(), own_child=False)

    # ---------- 生命周期 ----------

    def start(self) -> bool:
        """拉起服务（同步等待就绪）。成功返回 True；crash-loop 置红或冲突抛/返回 False。"""
        if self.state == ST_RED:
            logger.error(f"[{self.name}] 处于 red 态（连续失败 {self.fail_count} 次），拒绝自动拉起；请手动重启")
            return False
        self._set_state(ST_STARTING)
        try:
            self._handle_port_conflict()
        except PortConflictError as e:
            logger.error(f"[{self.name}] {e}")
            self.fail_count += 1
            self._escalate()
            return False

        log_f = open(self.log_path, "ab")
        try:
            self.proc = subprocess.Popen(
                self.command,
                cwd=self.cwd,
                env=self.env,
                stdout=log_f,
                stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except Exception as e:
            logger.error(f"[{self.name}] 拉起失败: {e}")
            log_f.close()
            self.fail_count += 1
            self._escalate()
            return False

        # 就绪判定窗口（拉起失败/健康超时共用计数器 T-3）
        deadline = time.time() + self.policies["start_ready_timeout_s"]
        while time.time() < deadline:
            if self.proc.poll() is not None:
                logger.error(f"[{self.name}] 子进程在就绪判定窗口内退出，退出码 {self.proc.returncode}")
                log_f.close()
                self.fail_count += 1
                self._escalate()
                return False
            if self._health_once():
                log_f.close()
                self.fail_count = 0
                self._set_state(ST_HEALTHY)
                self._start_monitor()
                if self.warmup:
                    threading.Thread(target=self._safe_warmup, daemon=True).start()
                return True
            time.sleep(2)
        logger.error(f"[{self.name}] 就绪判定窗口超时（{self.policies['start_ready_timeout_s']}s）")
        log_f.close()
        self.fail_count += 1
        self._escalate()
        return False

    def _safe_warmup(self):
        try:
            self.warmup(self)
        except Exception:
            logger.error(f"[{self.name}] 预热钩子异常", exc_info=True)

    def stop(self):
        """优雅停止：terminate → 5s → kill。停止监控线程，状态回 stopped。"""
        self._monitor_stop.set()
        if self._monitor_thread:
            self._monitor_thread.join(timeout=3)
            self._monitor_thread = None
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=5)
            except (subprocess.TimeoutExpired, psutil.NoSuchProcess):
                try:
                    self.proc.kill()
                except psutil.NoSuchProcess:
                    pass
        self.proc = None
        self._set_state(ST_STOPPED)

    def manual_restart(self) -> bool:
        """手动重启（D10）：清零失败计数，red 态唯一出路。"""
        logger.info(f"[{self.name}] 手动重启（清零失败计数）")
        self.stop()
        self.fail_count = 0
        return self.start()

    def tail_log(self, lines: int = 20) -> str:
        """日志尾（状态条胶囊展示用）。"""
        try:
            with open(self.log_path, "rb") as f:
                f.seek(0, os.SEEK_END)
                size = f.tell()
                f.seek(max(0, size - 64 * 1024))
                data = f.read().decode("utf-8", errors="replace")
            return "\n".join(data.splitlines()[-lines:])
        except FileNotFoundError:
            return ""

    # ---------- 监控 ----------

    def _health_once(self) -> bool:
        """单次健康检查。自定义 probe 优先（TCP 探测）；否则 HTTP GET，不引新依赖。"""
        if self.health_probe is not None:
            try:
                return bool(self.health_probe(self))
            except Exception:
                return False
        try:
            import urllib.request
            req = urllib.request.Request(self.health_url, method="GET")
            urllib.request.urlopen(req, timeout=self.policies["health_timeout_s"])
            return True
        except Exception:
            return False

    def _start_monitor(self):
        self._monitor_stop.clear()
        self._monitor_thread = threading.Thread(
            target=self._monitor_loop, name=f"svc-monitor-{self.name}", daemon=True
        )
        self._monitor_thread.start()

    def _monitor_loop(self):
        """健康轮询（30s 周期）：子进程死亡或健康检查失败 → 计数 → 自动重拉 → 置红。"""
        while not self._monitor_stop.is_set():
            if self._monitor_stop.wait(timeout=self.policies["health_poll_interval_s"]):
                return
            if self.proc and self.proc.poll() is not None:
                logger.error(f"[{self.name}] 子进程意外退出（码 {self.proc.returncode}）")
                self.fail_count += 1
                self._escalate()
                if self.state == ST_RED:
                    return
                self._auto_restart()
                continue
            if not self._health_once():
                logger.error(f"[{self.name}] 健康检查失败（超时 {self.policies['health_timeout_s']}s）")
                self.fail_count += 1
                self._escalate()
                if self.state == ST_RED:
                    return
                self._auto_restart()

    def _auto_restart(self):
        self._set_state(ST_CRASHED)
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=5)
            except (subprocess.TimeoutExpired, psutil.NoSuchProcess):
                try:
                    self.proc.kill()
                except psutil.NoSuchProcess:
                    pass
        self.start()

    def _escalate(self):
        """失控防护（CEO-P1-3）：连续失败达上限 → red，停止自动重拉；否则落 crashed。"""
        if self.fail_count >= self.policies["crash_loop_max"]:
            logger.error(
                f"[{self.name}] 连续失败 {self.fail_count} 次（上限 "
                f"{self.policies['crash_loop_max']}），停止自动重拉，置红告警"
            )
            self._set_state(ST_RED)
        else:
            self._set_state(ST_CRASHED)


class ServiceRegistry:
    """托管服务注册表（状态条数据源，线程安全）。"""

    _instance = None
    _lock = threading.Lock()

    def __init__(self):
        self.services = {}

    @classmethod
    def instance(cls) -> "ServiceRegistry":
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def register(self, svc: ManagedProcess):
        self.services[svc.name] = svc
        logger.info(f"[registry] 注册托管服务: {svc.name}")

    def get(self, name: str) -> Optional[ManagedProcess]:
        return self.services.get(name)

    def all(self):
        return list(self.services.values())
