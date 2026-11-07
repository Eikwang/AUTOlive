# -*- coding: utf-8 -*-
"""
RVC 变声服务 AUTOlive 侧启动器 + 控制客户端（P2-1；C2/C3 治理批次扩展）

- build_rvc_vc_service(): 构造 ManagedProcess（RVC runtime python 跑薄 adapter，
  注册进 ServiceRegistry 供状态条展示）——C2 终版单例判定内建
- lazy_register_rvc_vc(): C3 懒启动占位登记（进前端不拉进程，幂等）
- ensure_rvc_vc(): C3 懒触发（变声功能入口调用——按 C2 判定语义拉起）
- start_idle_monitor(): C3 闲置退出（无活跃流 N 分钟优雅休眠）
- RvcVcClient(): 控制面 HTTP 客户端（/switch /start /stop /status /devices），
  供前端 svc.py 与训练页调用
"""
import json
import os
import threading
import time
import urllib.request
from typing import Optional

from utils.autolive_paths import (AutolivePaths, as_keyed_config,
                                  write_interpreter_memory)
from utils.config import Config
from utils.my_log import logger
from utils.service_orchestrator import (DEFAULT_POLICIES, ST_RED, ST_STOPPED,
                                        ST_STARTING, ManagedProcess,
                                        ServiceRegistry)

HTTP_PORT_DEFAULT = 9391


def rvc_vc_alive(config: Config, paths: AutolivePaths, timeout: float = 2.0) -> bool:
    """控制面探活（2026-10-30 变声独立化）：GET /status 短超时，在线返回 True。

    用途：变声服务独立于系统主流程——懒触发（C3）与 main.py 核心（启动系统时
    按 rvc_vc.enabled 拉起）拉起前探活防端口冲突/双实例。注意（D-5 defer 项）：
    探 URL 不认进程——端口被无关进程占用时会误判在线（pid 归属核验见
    _verify_port_owner）。"""
    try:
        http_port = int(config.get("rvc_vc", "http_port") or HTTP_PORT_DEFAULT)
        with urllib.request.urlopen(
                f"http://127.0.0.1:{http_port}/status", timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def _verify_port_owner(config: Config) -> str:
    """C2/native F4：在线跳过路径的 pid 归属核验（best-effort）。

    返回占用者描述串（"pid=123 name=python.exe cmd=..." 或 "归属未知"）。
    核验失败不阻断跳过（保守防竞态），仅 warning 提供归因线索——端口冲突的
    权威处置仍归 ManagedProcess.start 既有归因机制。"""
    try:
        import psutil
        http_port = int(config.get("rvc_vc", "http_port") or HTTP_PORT_DEFAULT)
        for conn in psutil.net_connections(kind="tcp"):
            if (conn.status == psutil.CONN_LISTEN and conn.laddr.port == http_port
                    and conn.pid):
                p = psutil.Process(conn.pid)
                return f"pid={p.pid} name={p.name()} cmd={' '.join((p.cmdline() or [])[:3])}"
    except Exception as e:
        return f"归属未知（{type(e).__name__}）"
    return "归属未知（未找到监听者）"


def rvc_runtime_python(config: Config, paths: AutolivePaths) -> str:
    """变声服务解释器（T5 候选链收敛，/autoplan main-20261106-173018）：

    顺序：rvc_vc.runtime_python（rvc_vc 节显式覆盖，指向真实文件才采纳——
    迁移清键对象 X3-T7，实盘迁移前该键指外部、语义不变）→ paths.rvc_python
    （已候选链化：config paths 覆盖 → 记忆 → edtalk/runtime312 仓库副本 →
    EDTalk 兄弟安装兜底，见 autolive_paths.resolve_python_interpreter）。
    全部缺失返回空串（调用方三要素报错；禁止再从 rvc_root 派生——apps/rvc
    副本翻转后派生值不存在）。"""
    config = as_keyed_config(config)  # 双形态归一（dict 直调场景）
    v = config.get("rvc_vc", "runtime_python")
    if v and str(v).strip() and os.path.isfile(str(v).strip()):
        return os.path.abspath(str(v).strip())
    if v and str(v).strip():
        logger.warning(
            f"[rvc-vc] rvc_vc.runtime_python 指向不存在的路径（忽略走候选链）: {v}"
            "——迁移场景请运行 Scripts/migrate_runtime_check.py 清键校验")
    return paths.rvc_python or ""


def build_rvc_vc_service(
    config: Config, paths: AutolivePaths, on_state_change=None,
    *, caller_starts: bool = True,
) -> Optional[ManagedProcess]:
    """构造（或复用在册实例）RVC 变声服务。config rvc_vc.hosted 默认 true。

    C2 终版单例判定（spec 三轮收敛：C-1/CP-2/FE-1；ENG-1 堵窗口）：
    ①注册表在册且 state 非 stopped → 复用跳过（无论 alive 真假——实例自愈由
      ManagedProcess 监控线程承担（_monitor_loop 死进程 auto_restart 在位），
      外部重拉恰是竞态源，不做）；
    ②不在册或 state==stopped → alive 复查：在线 → 跳过拉起仅记日志
      （覆盖外部进程/主系统已拉起场景，pid 归属核验 native F4）；离线 → 构造；
    时序约束（spec FE-1）：register 同步完成后置 ST_STARTING（ENG-1：语义
    真相=「拉起已在途」，堵 register→start 线程的 stopped 判定窗口）——
    调用方 start 线程在其后，register 恒先于 Popen。

    caller_starts（2026-11-06 懒启动断链修复）：
      True（默认，main.py 主系统契约）——调用方自行起 start 线程，build 只构造；
      False（ensure 懒启动路径）——新构造实例由 build 内部后台 boot（start 成功
      接 idle monitor+解释器记忆）。**修复背景**：ensure 对新构造实例（state=
      starting、proc=None）的判定只接 stopped/red——新实例落"在途"分支后无人
      start，前端单独懒拉起自 C3 起即断链（服务卡 starting、端口无监听，必须
      先启动主系统由 main.py 承接 start——与"变声独立进程可单独启动"设计不符，
      用户 2026-11-06 晚实测确认）。复用分支（①/stopped/red）不受本参数影响。

    返回：ManagedProcess（调用方负责 start，除非 caller_starts=False）/ 已在线
    时 None / hosted=false None。
    """
    config = as_keyed_config(config)  # C3 双形态归一（svc 页 dict / main Config）
    hosted = config.get("rvc_vc", "hosted")
    if hosted is None:
        hosted = True
    if not hosted:
        logger.info("[rvc-vc] rvc_vc.hosted=false，跳过托管拉起")
        return None

    registry = ServiceRegistry.instance()
    existing = registry.get("rvc-vc")
    if existing is not None and existing.state != ST_STOPPED:
        # ① 在册非 stopped → 复用跳过（无论 alive——自愈归监控线程，spec C-1）
        logger.debug(f"[rvc-vc] 注册表在册实例（state={existing.state}），复用跳过新建")
        return existing

    # ② 不在册或 stopped → alive 复查
    if rvc_vc_alive(config, paths):
        owner = _verify_port_owner(config)
        if "name=python" in owner or "归属未知" in owner:
            logger.info(f"[rvc-vc] 服务已在线，跳过拉起（{owner}）")
        else:
            logger.warning(f"[rvc-vc] 端口被非 Python 进程占用却响应 /status？"
                           f"{owner}——保守跳过拉起（归因处置交由端口冲突机制）")
        return None

    # 离线 → 构造（stopped 在册实例不重建：stop 后 proc=None，复用实例直接
    # start 即可——见 ensure_rvc_vc 的 stopped 分支；此处仅处理「不在册」）
    if existing is not None and existing.state == ST_STOPPED:
        return existing

    # S-1 根收敛：副本根=paths.rvc_root（apps/rvc），config 可覆盖回外部树
    rvc_root = getattr(paths, "rvc_root", None) or config.get("rvc_vc", "rvc_root") \
        or r"D:\AI\AUTOlive\apps\rvc"
    http_port = int(config.get("rvc_vc", "http_port") or HTTP_PORT_DEFAULT)

    policies = dict(DEFAULT_POLICIES)
    # 控制面探活地址（HTTP 服务常驻，无模型时 /status 即 200）
    custom = config.get("audio_integration", "policies") or {}
    policies.update({k: v for k, v in custom.items() if isinstance(v, (int, float))})

    adapter = os.path.join(paths.autolive_home, "apps", "rvc", "rvc_adapter.py")
    command = [
        rvc_runtime_python(config, paths),
        adapter,
        "--autolive-home", paths.autolive_home,
        "--rvc-root", rvc_root,
        "--http-port", str(http_port),
    ]
    env = paths.subprocess_env({"RVC_ROOT": rvc_root, "RVC_VC_HTTP_PORT": str(http_port)})

    svc = ManagedProcess(
        name="rvc-vc",
        command=command,
        cwd=rvc_root,  # RVC 内部相对路径依赖（assets/hubert 等）
        env=env,
        health_url=f"http://127.0.0.1:{http_port}/status",
        port=http_port,
        policies=policies,
        on_state_change=on_state_change,
    )
    registry.register(svc)
    # ENG-1：register 后立即置 ST_STARTING（语义真相=「拉起已在途」）——
    # 堵 register→start 线程之间 state==stopped 的判定窗口（spec E-1）。
    # 直接赋值跳过 _set_state 日志（start() 会再打一次 starting，避免重复行）。
    svc.state = ST_STARTING
    if not caller_starts:
        # 懒启动断链修复（2026-11-06）：ensure 路径的新构造实例由 build 内部
        # boot——承接 start+idle monitor+解释器记忆（与 ensure stopped 分支同款）。
        # 线程名独立（rvc-vc-boot），main.py 默认路径（caller_starts=True）零改动。
        def _boot():
            if svc.start():
                start_idle_monitor(svc, config)
                _remember_interpreter(svc)
            else:
                logger.error("[rvc-vc] 懒拉起失败——查看 logs/services/rvc-vc.log 定位")
        # 胶囊静默（2026-11-07 整合批次，用户预期 4）：懒实例不常显侧边栏胶囊，
        # svc.py _poll 按流状态翻转 show_in_statusbar（运行中显示/停止消失）
        svc.show_in_statusbar = False
        threading.Thread(target=_boot, name="rvc-vc-boot", daemon=True).start()
        logger.info("[rvc-vc] 新实例后台 boot 已启动（懒启动路径）")
    return svc


def ensure_rvc_vc(config: Config, paths: AutolivePaths,
                  on_state_change=None) -> Optional[ManagedProcess]:
    """C3 懒触发（变声功能入口调用：svc 页开关/试音/start_stream 前）。

    按 C2 终版判定语义拉起：在册非 stopped → 复用（含 starting 在途——已在
    拉起中，直接返回）；stopped 在册 → 复用实例后台 start（不新建）；不在册
    且离线 → build+后台 start；在线 → 返回 None（调用方按 None 走在线直连）。

    缺陷3 根修（2026-11-06 批次 T1，/autoplan main-20261106-173018）：ST_RED
    在册实例原被 build ①分支"非 stopped 复用跳过"静默吞掉——监控线程置红后
    已全部退出，复用=永不重拉，前端所有调用 10061 死循环（用户报障实证）。
    修复：红态且无进程时后台 manual_restart（清零失败计数+同步 start 留在
    后台线程——start 含 120s 就绪窗口同步阻塞，禁入事件循环/调用线程）。

    返回：ManagedProcess（新拉起/复用中/复活中，状态条轮询就绪）或 None（已在线）。

    懒启动断链修复（2026-11-06 晚，用户实测）：build 以 caller_starts=False 调用
    ——新构造实例由 build 内部后台 boot（此前新实例 state=starting 落"在途"
    分支后无人 start，服务卡死 starting、端口无监听，必须先启动主系统才由
    main.py:166 承接 start——与"变声独立进程可单独手动启动"设计不符）。"""
    config = as_keyed_config(config)  # 双形态归一（idle_exit_min/透传 build）
    svc = build_rvc_vc_service(config, paths, on_state_change=on_state_change,
                               caller_starts=False)
    if svc is None:
        return None  # 已在线 / hosted=false
    if svc.proc is None and svc.state == ST_STOPPED:
        # C3：stopped 复用实例（idle 休眠后重拉/懒占位首拉）
        def _start():
            if svc.start():
                start_idle_monitor(svc, config)  # 关键路径先行
                _remember_interpreter(svc)       # T5 记忆写入（best-effort）
        threading.Thread(target=_start, name="rvc-vc-ensure", daemon=True).start()
        logger.info("[rvc-vc] 首次使用/闲置唤醒：后台拉起变声服务（状态条可查进度）")
        return svc
    if svc.proc is None and svc.state == ST_RED:
        # 缺陷3 根修：置红实例复活（唯一出路 manual_restart，D10；后台线程）
        def _revive():
            logger.warning("[rvc-vc] 检测到置红实例——手动重启恢复中（清零失败计数）")
            if svc.manual_restart():
                start_idle_monitor(svc, config)
                _remember_interpreter(svc)
                logger.info("[rvc-vc] 置红实例恢复成功")
            else:
                logger.error("[rvc-vc] 置红实例重启仍失败——查看 logs/services/rvc-vc.log 定位")
        threading.Thread(target=_revive, name="rvc-vc-revive", daemon=True).start()
        return svc
    # 在途（starting/healthy/...）——build 分支已返回在册实例
    return svc


def pick_default_model(config=None, models_dir: str = None):
    """默认 RVC 模型选择（2026-11-07 整合批次：svc 页与 main.py 开关自动开流共用）。

    档案优先（voice_profiles.rvc_model）→ models/rvc 目录扫描序。返回
    {"name","pth","index"} 或 None（无可用模型——调用方按无模型指引处置）。"""
    try:
        from utils.voice_profiles import load_profiles
        for pid, prof in (load_profiles() or {}).items():
            if prof.get("rvc_model"):
                return {"name": pid, "pth": prof["rvc_model"],
                        "index": prof.get("rvc_index")}
    except Exception:
        pass
    if not models_dir:
        return None
    try:
        best = None
        for fn in sorted(os.listdir(models_dir)):
            if not fn.endswith(".pth"):
                continue
            name = fn[:-4]
            base = os.path.join(models_dir, name)
            index = None
            for cand in (f"{base}.index",):
                if os.path.isfile(cand):
                    index = cand
                    break
            if index is None:
                prefix = name + "_"
                for other in sorted(os.listdir(models_dir)):
                    if other.startswith(prefix) and other.endswith(".index"):
                        index = os.path.join(models_dir, other)
                        break
            best = {"name": name, "pth": os.path.join(models_dir, fn),
                    "index": index}
            break  # 目录扫描序取第一个（与原 _scan_models 排序一致）
        return best
    except Exception:
        return None


def auto_start_stream(config, models_dir: str = None) -> None:
    """开关语义执行器（2026-11-07 整合批次，用户预期 3）：预切默认模型+按
    已保存设备开流——"启动即执行变声功能"，不管服务是谁拉的。

    前置=服务在线（调用方保证：main.py 探活在线分支或 start() 成功后）。
    流已在运行 → 幂等跳过。失败抛异常（调用方报警通知）。"""
    client = RvcVcClient(config=config)
    st = client.status()
    if st.get("state") == "running":
        logger.info("[rvc-vc] 流已在运行——跳过重复开流")
        return
    if not (st.get("model") or {}).get("pth"):
        if not models_dir:
            try:
                paths = AutolivePaths(config)
                models_dir = os.path.join(paths.autolive_home, "models", "rvc")
            except Exception:
                models_dir = None
        picked = pick_default_model(config, models_dir)
        if picked:
            client.switch_model(
                picked["pth"], picked["index"],
                float(as_keyed_config(config).get("rvc_vc", "index_rate") or 0.75))
        else:
            logger.warning("[rvc-vc] 无可用 RVC 模型——开流继续（服务端可能拒绝，见日志）")
    client.start_stream(
        input_device=as_keyed_config(config).get("rvc_vc", "input_device") or None,
        output_device=as_keyed_config(config).get("rvc_vc", "output_device") or None)
    logger.info("[rvc-vc] 变声流已开启（开关语义=启动即执行）")


def _remember_interpreter(svc) -> None:
    """T5（F-N2 配套）：拉起成功后记录实际使用的解释器（best-effort）。
    getattr 守卫：测试/mock 实例可能无 command 属性，静默跳过不干扰关键路径。"""
    cmd = getattr(svc, "command", None)
    if not cmd:
        return
    try:
        write_interpreter_memory("rvc_python", cmd[0])
    except Exception:
        pass


def start_idle_monitor(svc: ManagedProcess, config: Config,
                       check_interval_s: float = 60.0) -> None:
    """C3 闲置退出（uc1c 终门裁决）：无活跃变声流（/status state!="running"）
    持续 rvc_vc.idle_exit_min（默认 10）分钟 → svc.stop() 优雅休眠。

    懒触发登记（ensure）不随休眠消失——下次使用 ensure 复用 stopped 实例重拉。
    探测失败（/status 不可达瞬态）不累计闲置（保守）；子进程死亡交回监控线程
    auto_restart，不在此处理。"""
    try:
        minutes = float(config.get("rvc_vc", "idle_exit_min") or 10)
    except (TypeError, ValueError):
        minutes = 10.0

    def _monitor():
        idle_since: Optional[float] = None
        client = RvcVcClient(config=config)
        logger.info(f"[rvc-vc] 闲置监控已启动（{minutes:g} 分钟无活跃流自动休眠）")
        while not svc._monitor_stop.is_set():
            if svc._monitor_stop.wait(timeout=check_interval_s):
                return
            if svc.proc is None or svc.proc.poll() is not None:
                return  # 子进程未起/已死——监控线程或 ensure 负责后续
            try:
                state = (client.status() or {}).get("state")
            except Exception:
                state = "unknown"  # 保守：状态不可知不累计闲置
            if state == "running":
                idle_since = None
                continue
            if state == "idle":
                idle_since = idle_since or time.time()
            # unknown：idle_since 不动
            if idle_since and time.time() - idle_since >= minutes * 60:
                logger.info(f"[rvc-vc] 闲置 {minutes:g} 分钟，自动休眠"
                            f"（GPU/CPU 已释放；下次使用变声功能时自动拉起）")
                svc.stop()
                return

    threading.Thread(target=_monitor, name="rvc-vc-idle", daemon=True).start()


class RvcVcClient:
    """控制面客户端（供前端/其它模块调用）。全部回环 HTTP，异常带三要素。"""

    def __init__(self, http_port: int = None, config: Config = None):
        if http_port is None:
            http_port = int(
                (config or Config(os.path.join(
                    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "config.json"))).get("rvc_vc", "http_port") or HTTP_PORT_DEFAULT
            )
        self.base = f"http://127.0.0.1:{http_port}"

    def _get(self, path: str, timeout: int = 5):
        return json.loads(urllib.request.urlopen(
            f"{self.base}{path}", timeout=timeout).read())

    def _post(self, path: str, body: dict, timeout: int = 120):
        req = urllib.request.Request(
            f"{self.base}{path}", data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"})
        try:
            return json.loads(urllib.request.urlopen(req, timeout=timeout).read())
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"{e} {detail}") from e

    def status(self):
        return self._get("/status")

    def devices(self):
        return self._get("/devices")

    def switch_model(self, pth_path: str, index_path: str = None,
                     index_rate: float = None):
        """事务热切换：失败抛 RuntimeError（含 problem/cause/fix），旧模型继续。"""
        return self._post("/switch", {
            "pth_path": pth_path.replace("\\", "/"),
            "index_path": (index_path or "").replace("\\", "/") or None,
            "index_rate": index_rate,
        })

    def update_params(self, updates: dict):
        """R-2 参数链闭合（/autoplan 2026-10-14）：pitch/formant/index_rate/
        protect/f0method 热更新。失败抛 RuntimeError（三要素），参数不变。"""
        return self._post("/params", updates, timeout=15)

    def start_stream(self, input_device=None, output_device=None):
        return self._post("/start", {"input_device": input_device,
                                     "output_device": output_device})

    def stop_stream(self):
        return self._post("/stop", {})
