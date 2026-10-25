from frontend.ui.components.config_helper import get_nested_value
"""
AUTOlive 主入口文件
模块化的WebUI应用程序
"""
import sys
import os
import signal
import socket
import subprocess
import threading
import time
import traceback
from collections import deque
from pathlib import Path

# 添加项目根目录到Python路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from nicegui import ui, app

# 导入配置模块
from frontend.config.settings import init_config, get_config
from frontend.config.paths import get_base_path, get_config_path, get_log_dir, get_output_dir

# 导入工具模块
from frontend.utils.helpers import SystemCommand
from frontend.utils.common import textarea_data_change

# 导入UI模块
from frontend.ui.components import (
    NavigationTabs,
    ControlButtons,
    LoginForm,
    ThemeManager,
    SearchFilter,
    ExpansionPanel,
    FormField
)

# 导入新的 DrawerLayout
from frontend.ui.layout import DrawerLayout

# 导入标签页模块
from frontend.ui.tabs import (
    common_config,
    llm,
    tts,
    svc,
    visual_body,
    talk,
    integral,
    assistant_anchor,
    translate,
    serial,
    data_analysis,
    audio_play,
    web_captions_printer,
    filter_config,
    filter_forget,
    filter_dedup,
    filter_queue,
    blacklist,
    read_comment,
    thanks,
    schedule,
    idle_time_task,
    trends_copywriting,
    local_qa,
    choose_song,
    key_mapping,
    database_config,
    system_settings,
    platform_config
)

# 导入样式模块
from frontend.styles.themes import init_theme_manager, get_theme_manager

# 导入日志模块
from utils.my_log import logger
from utils.autolive_paths import ensure_config_backup

# D-15（基础功能 2026-10-22 节 #9）：退役配置键面清单（键路径→说明）。
# 首启扫描存量命中→notify+log 一次性告知，防旧配置静默失效无感知；
# 迁移/清理职责归 settings._ensure_defaults 各迁移点（此处只做可见面，
# 不改配置）。visual_body=metahuman_stream 的自动迁移在 _ensure_defaults
# 有独立 log 告知，不在本清单（迁移后键已不存在）。
_RETIRED_CONFIG_KEYS: list = [
    (("metahuman_stream",), "metahuman_stream 配置节已退役（2026-10-22 批次），整节不再读取，可删除"),
    (("game",), "game 配置节已退役（游戏控制实现已移除，chat_type=game 不可用），可删除"),
    (("audio_player", "priority_mapping"), "audio_player.priority_mapping 已收敛至 filter.priority_mapping（运行时自动合并迁移），可删除"),
    (("filter", "lang_filter"), "filter.lang_filter 已退役（语言过滤由顶层 need_lang 键驱动），可删除"),
    (("key_mapping", "copywriting_trigger_type"), "key_mapping 文案触发已移除（2026-10-24 精简批次），可删除"),
    (("key_mapping", "copywriting_single_sentence_trigger_once"), "key_mapping 文案触发已移除（2026-10-24 精简批次），可删除"),
    (("key_mapping", "local_audio_trigger_type"), "key_mapping 本地音频触发已移除（2026-10-24 精简批次），可删除"),
    (("key_mapping", "local_audio_single_sentence_trigger_once"), "key_mapping 本地音频触发已移除（2026-10-24 精简批次），可删除"),
    (("key_mapping", "img_path_trigger_type"), "key_mapping 图片触发已移除（2026-10-24 精简批次），可删除"),
    (("key_mapping", "img_path_single_sentence_trigger_once"), "key_mapping 图片触发已移除（2026-10-24 精简批次），可删除"),
]


class AutoliveApp:
    """AUTOlive 应用程序类"""

    # script mode 运行态单例（启动链假启动修复 2026-10-12）：每个浏览器连接都会
    # 重跑 __init__（root page 重入，__init__:106 重入判定），实例字段的运行态会被
    # 重置——running_flag 刷新即"假停止"、my_subprocesses 丢失成孤儿进程。
    # 运行态挂类级 property 共享；dark_mode 等每用户状态不受影响。
    _shared_running = False
    _shared_subprocesses = {}

    # 最近异常环形缓冲（D1 状态条数据源）：类级 deque（script mode 重入共享），
    # 监控（_monitor_subprocesses）与启动编排（run_external_program / D-14）都写入，
    # 消息入缓冲前经 redact_exception 脱敏（DX 义务）。
    _shared_exception_buffer = deque(maxlen=10)

    # UI-16 同期（TODOS 2026-09-23 节竞态条）：UI executor 线程与 /sys_cmd
    # async endpoint 并发启停的 TOCTOU 窗口收口——类级锁串行化运行态写入。
    # 读侧（状态条轮询）不加锁（bool 原子读；GIL 下无撕裂）。
    _running_flag_lock = threading.Lock()

    @property
    def running_flag(self) -> bool:
        return AutoliveApp._shared_running

    @running_flag.setter
    def running_flag(self, value) -> None:
        with AutoliveApp._running_flag_lock:
            AutoliveApp._shared_running = bool(value)

    @property
    def my_subprocesses(self) -> dict:
        return AutoliveApp._shared_subprocesses

    @my_subprocesses.setter
    def my_subprocesses(self, value: dict) -> None:
        # 测试与重置路径用赋值换新 dict；类级引用替换保持跨重入共享
        AutoliveApp._shared_subprocesses = value if isinstance(value, dict) else {}

    # ============ D1 运行状态条数据源（类级运行态的读取/写入便捷层） ============

    @classmethod
    def push_exception(cls, module: str, message: str, *, fix_hint: str = "") -> dict:
        """异常入环形缓冲（D1）：脱敏后追加，供状态条展示。

        Args:
            module: 模块名（main / edtalk_realtime / fastrag …）
            message: 异常消息（入缓冲前经 redact_exception 脱敏）
            fix_hint: 修复指引（D-14 三类：重登录/改端口/查路径）

        Returns:
            入缓冲的条目 dict（ts/module/message/fix_hint）
        """
        from frontend.utils.redact import redact_exception
        entry = {
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
            "module": module,
            "message": redact_exception(message),
            "fix_hint": str(fix_hint or ""),
        }
        cls._shared_exception_buffer.append(entry)
        return entry

    @classmethod
    def recent_exceptions(cls) -> list:
        """最近异常列表（时间正序，最新在末尾；上限 10 条）。"""
        return list(cls._shared_exception_buffer)

    def subprocess_health(self) -> list:
        """子进程健康快照（D1 状态条数据源）：[(name, alive)]。

        alive: True=存活 / False=已退出 / None=探测失败未知。
        """
        snap = []
        for name, proc in list(self.my_subprocesses.items()):
            try:
                snap.append((name, proc.poll() is None))
            except Exception:
                snap.append((name, None))
        return snap

    def __init__(self):
        """初始化应用程序

        NiceGUI 3.x script mode（整合修复 2026-10-06）：顶层构建 UI 的主脚本被注册为
        root page——每个浏览器连接都会 runpy 重跑本模块与 main()（__name__='__main__'）。
        重入（server 已启动）时跳过 Audio/数据库/目录等重活，仅保留 config 与状态字段
        ——重活每次执行就是"前端不断重启"现象的根因（Audio init 约 12s）。
        """
        # 初始化基础路径
        self.base_path = get_base_path()

        # 初始化配置（必须在 _init_directories 之前；webui 进程内单例，重入幂等）
        config_path = get_config_path()
        self.config = init_config(str(config_path))

        # NiceGUI 3.x 重入判定：server 已启动 = 本次为 root page 重跑（非首次启动）
        from nicegui import core as _nicegui_core
        self._reentry = bool(getattr(_nicegui_core.app, "is_started", False))
        if not self._reentry:
            # 恢复出厂基线自维护（/autoplan 批准批次 C6 项，TODOS #8）：bak 缺失时
            # 从当前 config.json 固化一份，factory（:614）不再必炸。幂等，仅首次固化。
            _bak = os.path.join(str(self.base_path), "config.json.bak")
            ensure_config_backup(str(get_config_path()), _bak)
        if self._reentry:
            logger.debug("AutoliveApp 重入（script mode root page）——跳过重活初始化")

        # 初始化目录（重活，仅首次）
        if not self._reentry:
            self._init_directories()
        
        # 初始化主题管理器
        self.theme_manager = init_theme_manager(self.config._config)
        
        # 初始化UI组件
        self.navigation_tabs = NavigationTabs(self.config._config)
        self.control_buttons = ControlButtons(self.config._config)
        self.login_form = LoginForm(self.config._config)
        self.theme_manager_ui = ThemeManager(self.config._config)

        # 状态变量（运行态挂类级 property——见类头注释；此处不重置）
        self.user_info = None

        # 暗夜模式（任务7/DG-B8：单实例收敛 + 按配置初始化——先于任何 UI 构建
        # 与 check_login，登录页不闪白；缺省 light）
        self.dark_mode = ui.dark_mode()
        self.dark_mode.value = bool(
            get_nested_value(self.config._config, "webui", "dark_mode", default=False)
        )

        # 初始化Audio类（与原文件 webui-bak.py L141 一致；重活，重入跳过）
        if self._reentry:
            self.audio = None
        else:
            try:
                from utils.audio import Audio
                self.audio = Audio(str(get_config_path()), type=2)
                logger.info("Audio初始化完成")
            except Exception as e:
                logger.warning(f"Audio初始化失败: {e}")
                self.audio = None

        # 初始化数据分析模块（与原文件 webui-bak.py L4939-4941 一致；重活，重入跳过）
        if self._reentry:
            self.data_analysis = None
        else:
            try:
                from utils.data_analysis import Data_Analysis
                self.data_analysis = Data_Analysis(str(get_config_path()))
                logger.info("数据分析模块初始化完成")
            except Exception as e:
                logger.warning(f"数据分析模块初始化失败: {e}")
                self.data_analysis = None

        logger.info("AUTOlive 应用程序初始化完成")
    
    def _init_directories(self):
        """初始化必要的目录"""
        # 日志目录
        log_dir = get_log_dir()
        logger.debug(f"日志目录: {log_dir}")
        
        # 输出目录
        output_dir = get_output_dir()
        logger.debug(f"输出目录: {output_dir}")
        
        logger.debug("项目相关文件夹初始化完成")
    
    def set_config(self, *keys, value):
        """
        设置配置值
        
        Args:
            *keys: 配置键路径
            value: 配置值
        """
        self.config.set(*keys, value=value)
        self.config.save()
    
    def save_config(self):
        """保存配置到文件（与原文件 webui-bak.py L2710-2759 一致）"""
        try:
            # 从UI组件读取配置值
            from frontend.ui.tabs.common_config import save_common_config
            config_data = save_common_config(self.config._config)

            # 更新配置
            self.config._config.update(config_data)

            # 保存到文件
            self.config.save()
            ui.notify(position="top", type="positive", message="配置保存成功")
            logger.info("配置保存成功")
        except Exception as e:
            ui.notify(position="top", type="negative", message=f"配置保存失败: {e}")
            logger.error(f"配置保存失败: {e}")
    
    # 外围模块显示名（D-14 部分启动 notify 文案用）
    _MODULE_LABELS = {
        "main": "主系统",
        "edtalk_realtime": "EDTalk 推理",
        "fastrag": "知识库服务",
    }

    def start_programs(self) -> list:
        """启动外围服务（EDTalk 实时推理 + fastrag 知识库），返回启动失败列表。

        coordination_program 配置驱动拉起已随模块退役移除（/autoplan 批准批次
        删除批，任务书"联动程序此功能已弃用，直接移除"）；EDTalk/fastrag 的
        拉起均显式 shell=False + list 传参（native H1）。

        Returns:
            [(module, reason, fix_hint), ...]——D-14 外围失败分级：调用方
            （run_external_program）降级继续（拉起主系统），仅告警不中断。
        """
        config_data = self.config._config
        failures = []

        # EDTalk 实时推理服务器（计划 §4.2：开关双检 + 端口幂等 + 身份验证）
        ok, reason, fix_hint = self._start_edtalk_realtime(config_data)
        if not ok:
            failures.append(("edtalk_realtime", reason, fix_hint))

        # fastrag 知识库服务（计划 §6.2）
        ok, reason, fix_hint = self._start_fastrag(config_data)
        if not ok:
            failures.append(("fastrag", reason, fix_hint))

        return failures

    def _start_main_system(self):
        """拉起主系统 main.py 子进程（编排层，启动链假启动修复 2026-10-12）。

        start_programs 只负责配置驱动的外围程序（coordination/edtalk/fastrag，
        tests/test_startup_wiring.py 锁定其行为）；main.py 主系统的拉起属启动
        编排。shell=False + list 传参（native H1 规范）。

        Returns:
            (ok, reason, fix_hint)——D-14：关键模块失败 → 调用方复位 running_flag
            并弹 negative 通知（主系统失败=整体失败，不做降级）。
        """
        main_py = os.path.join(str(getattr(self, "base_path", os.getcwd())), "main.py")
        if not os.path.exists(main_py):
            logger.error(f"主系统入口不存在: {main_py}（problem=入口缺失；fix=检查仓库完整性）")
            return False, f"主系统入口不存在: {main_py}", "请检查安装完整性（main.py 缺失）"
        logger.info(f"运行程序: main 主系统 位于: {os.path.dirname(main_py)}")
        try:
            process = subprocess.Popen(
                [sys.executable, main_py],
                cwd=str(os.path.dirname(main_py)), shell=False)
            self.my_subprocesses["main"] = process
            return True, "", ""
        except Exception as e:
            logger.error(f"启动主系统 main 失败: {e}")
            return False, f"启动主系统进程失败: {e}", "请查看完整日志；若提示端口占用请修改端口配置"

    def _start_edtalk_realtime(self, config_data: dict):
        """拉起 EDTalk realtime_serve 服务（计划 §4.2 + Eng 义务⑨⑬）。

        - 开关双检：visual_body == "edtalk_realtime" 且 edtalk_realtime.enable
        - 解释器存在性校验（缺失报错附修复动作）
        - 端口幂等探测 + GET /status 身份验证（占用者未必是 realtime_serve）
        - 训练进行中检测（双向显存防护）
        - 显式 shell=False + list 传参（native H1）

        Returns:
            (ok, reason, fix_hint)——D-14：ok=False 时由调用方（start_programs
            → run_external_program）降级继续并汇总告警；开关未启用/训练中/
            已在运行等"主动跳过"不算失败。
        """
        try:
            ed_cfg = config_data.get("edtalk_realtime", {})
        except Exception:
            ed_cfg = {}
        if config_data.get("visual_body") != "edtalk_realtime" or not ed_cfg.get("enable"):
            return True, "", ""

        interpreter = ed_cfg.get("interpreter_path") or "D:\\AI\\EDTalk\\runtime312\\python.exe"
        if not os.path.exists(interpreter):
            logger.error(
                f"EDTalk 解释器不存在：{interpreter}。problem=解释器缺失；"
                f"cause=runtime312 未安装或路径变更；"
                f"fix=修改画面设置页『解释器路径』配置项指向可用解释器"
            )
            return False, f"EDTalk 解释器不存在：{interpreter}", "请在画面设置页修改『解释器路径』配置项"

        # 训练进行中检测（双向显存防护，16GB 卡训练与推理不可并存）
        try:
            from utils.train_streamer import pipeline as _train_pipeline
            if getattr(_train_pipeline, "training_running", False):
                logger.warning(
                    "训练正在进行中，启动实时推理服务器将导致 GPU 显存冲突（16GB 卡）——已跳过拉起，建议错开"
                )
                ui.notify(position="top", type="warning",
                          message="训练进行中，实时推理服务未启动（显存冲突风险），建议错开")
                return True, "", ""
        except ImportError:
            pass

        edtalk_dir = os.path.join(str(get_base_path()), "edtalk")
        if not os.path.isdir(edtalk_dir):
            logger.error(
                f"EDTalk 运行副本目录不存在：{edtalk_dir}。problem=副本缺失；"
                f"cause=尚未执行复制；fix=运行 scripts/copy_edtalk.py 一键复制脚本"
            )
            return False, f"EDTalk 运行副本目录不存在：{edtalk_dir}", "请运行 scripts/copy_edtalk.py 一键复制脚本"

        port = int(ed_cfg.get("port", 8000))
        host = ed_cfg.get("host", "127.0.0.1")

        # 端口幂等探测 + 身份验证（native E5）：占用者未必是 realtime_serve
        already_running = False
        try:
            import requests as _requests
            probe = _requests.get(f"http://127.0.0.1:{port}/status", timeout=2)
            if probe.status_code == 200 and "available_segments" in probe.text:
                already_running = True
                logger.warning(f"EDTalk realtime_serve 已在运行（端口 {port}），跳过拉起")
            elif probe.status_code == 200:
                logger.error(
                    f"端口 {port} 被其他 HTTP 服务占用（响应非 realtime_serve）。"
                    f"problem=端口冲突；cause=端口被未知服务占用；"
                    f"fix=修改画面设置页『端口』配置项或结束占用进程"
                )
                return False, f"端口 {port} 被非 EDTalk 服务占用", "请在画面设置页修改『端口』配置或结束占用进程"
        except Exception:
            pass  # 连接失败 = 端口空闲，正常走拉起

        if already_running:
            ui.notify(position="top", type="warning",
                      message=f"EDTalk 服务已在运行（端口 {port}），跳过重复拉起")
            return True, "", ""

        # 参数映射（config.edtalk_realtime → RUNBOOK 启动参数表）
        cmd = [interpreter, "-m", "realtime_serve.main"]
        cmd += ["--character-dir", str(ed_cfg.get("character_dir", "wy"))]
        cmd += ["--target-fps", str(ed_cfg.get("target_fps", 25))]
        cmd += ["--buffer-frames", str(ed_cfg.get("buffer_frames", 16))]
        cmd += ["--preroll-frames", str(ed_cfg.get("preroll_frames", 8))]
        face_repair = ed_cfg.get("face_repair", "adaptive")
        if face_repair and face_repair != "off":
            cmd += ["--face-repair", face_repair]
        gaze = ed_cfg.get("gaze_correction", "adaptive")
        if gaze and gaze != "off":
            cmd += ["--gaze-correction", gaze]
        cmd += ["--gaze-convergence", str(ed_cfg.get("gaze_convergence", 0.05))]
        segments = ed_cfg.get("segments", "auto")
        if segments and segments != "auto":
            cmd += ["--segments"] + str(segments).split()
        elif segments == "auto":
            cmd += ["--segments", "auto"]
        cmd += ["--host", host]
        cmd += ["--port", str(port)]
        api_token = (ed_cfg.get("api_token") or "").strip()
        if api_token:
            cmd += ["--api-token", api_token]

        try:
            # 显式 shell=False + list 传参（native H1：路径含空格/中文不会被 cmd.exe 打碎）
            # stdout/stderr 落盘（2026-10-16 排障缺口：realtime_serve 全 print 输出
            # 随 webui 控制台丢失，出画失败无从取证）
            log_dir = str(get_log_dir())
            os.makedirs(log_dir, exist_ok=True)
            log_path = os.path.join(log_dir, "edtalk_realtime.log")
            log_f = open(log_path, "ab", buffering=0)
            stamp = time.strftime("%Y-%m-%d %H:%M:%S")
            log_f.write(f"\n===== 启动 {stamp} 端口 {port} =====\n".encode("utf-8"))
            process = subprocess.Popen(cmd, cwd=edtalk_dir, shell=False,
                                       stdout=log_f, stderr=subprocess.STDOUT)
            self.my_subprocesses["edtalk_realtime"] = process
            self._edtalk_log_f = log_f  # 保引用防 GC 关闭句柄
            logger.info(f"运行程序: edtalk_realtime（PID {process.pid}，端口 {port}，日志 {log_path}）")
            return True, "", ""
        except Exception as e:
            logger.error(f"启动 EDTalk realtime_serve 失败: {e}")
            return False, f"启动 EDTalk realtime_serve 失败: {e}", "请查看完整日志（log/edtalk_realtime.log）定位原因"

    def _start_fastrag(self, config_data: dict):
        """拉起 fastrag server（计划 §6.2 + Eng 义务⑬：端口环境变量注入）。

        Returns:
            (ok, reason, fix_hint)——D-14：ok=False 由调用方降级继续并汇总告警；
            未启用不算失败。端口预检用 connect_ex 试探（任务书 D-14：端口占用
            =启动失败，fix=修改端口配置）。
        """
        fastrag_cfg = config_data.get("fastrag", {})
        if not fastrag_cfg.get("enable"):
            return True, "", ""

        base = str(get_base_path())
        fastrag_dir = os.path.join(base, "edtalk", "fastrag")
        server_exe = os.path.join(fastrag_dir, "server.exe")
        if not os.path.exists(server_exe):
            logger.error(
                f"fastrag server.exe 不存在：{server_exe}。problem=可执行文件缺失；"
                f"cause=fastrag 未复制；fix=运行 scripts/copy_edtalk.py 一键复制脚本"
            )
            return False, f"fastrag server.exe 不存在：{server_exe}", "请运行 scripts/copy_edtalk.py 一键复制脚本"

        port = int(fastrag_cfg.get("port", 11420))

        # 端口预检（D-14：connect_ex 连接试探，被监听即占用）
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as _probe:
                _probe.settimeout(0.5)
                if _probe.connect_ex(("127.0.0.1", port)) == 0:
                    logger.error(
                        f"fastrag 端口 {port} 已被占用。problem=端口冲突；"
                        f"cause=端口被其他进程监听；fix=修改系统设置页 fastrag『端口』配置"
                    )
                    return False, f"fastrag 端口 {port} 已被占用", "请修改 fastrag『端口』配置项"
        except Exception:
            pass  # 探测失败不阻断拉起（保持宽容，Popen 层兜底）

        env = os.environ.copy()
        # 端口经环境变量注入（tomllib 只读，零 Config.toml 手术——native H2）
        env["FASTRAG__SERVER__PORT"] = str(port)

        try:
            process = subprocess.Popen(
                [server_exe], cwd=fastrag_dir, shell=False, env=env
            )
            self.my_subprocesses["fastrag"] = process
            logger.info(f"运行程序: fastrag（PID {process.pid}，端口 {port}）")
            return True, "", ""
        except Exception as e:
            logger.error(f"启动 fastrag 失败: {e}")
            return False, f"启动 fastrag 失败: {e}", "请查看完整日志定位原因"

        # 注意（2026-10-15 EDTalk 整合义务修复）：此处曾残留 webui-bak 移植的
        # main.py 拉起代码——fastrag.enable=true 时 main.py 双拉起、首个进程成孤儿
        # （my_subprocesses["main"] 被覆盖，stop_programs 只杀得到第二个）。
        # main.py 的拉起由 run_external_program → _start_main_system 唯一负责，此处不再拉起。

    def stop_program(self, name):
        """停止一个正在运行的程序及其所有子进程（与原文件 webui-bak.py L246-269 一致）

        Args:
            name: 要停止的程序的名称
        """
        if name in self.my_subprocesses:
            pid = self.my_subprocesses[name].pid
            logger.info(f"停止程序和它所有的子进程: {name} with PID {pid}")

            try:
                if os.name == 'nt':  # Windows
                    command = ["taskkill", "/F", "/T", "/PID", str(pid)]
                    subprocess.run(command, check=True)
                else:  # POSIX系统，如Linux和macOS
                    os.killpg(os.getpgid(pid), signal.SIGKILL)

                logger.info(f"程序 {name} 和 它所有的子进程都被终止.")
            except Exception as e:
                logger.error(f"终止程序 {name} 失败: {e}")

            del self.my_subprocesses[name]
        else:
            logger.warning(f"程序 {name} 没有在运行.")

    def stop_programs(self):
        """停止所有已启动程序（coordination 循环随模块退役移除）。"""
        # 停止主程序
        self.stop_program("main")

    def run_external_program(self, type="webui"):
        """运行外部程序（与原文件 webui-bak.py L357-382 一致）"""
        if self.running_flag:
            if type == "webui":
                ui.notify(position="top", type="warning", message="运行中，请勿重复运行")
            return

        # 训练中启动主系统拦截（义务块"双向显存互斥拦截"升级：阻止整个启动而非
        # 仅跳过 EDTalk 拉起——design D2/D20：弹窗展示训练阶段+ETA+"前往训练页"跳转）
        try:
            from utils.train_streamer import pipeline as _train_pipeline
            if getattr(_train_pipeline, "training_running", False):
                prog = _train_pipeline.TrainPipeline.read_progress(
                    getattr(_train_pipeline, "training_role", "") or "")
                stage = (prog or {}).get("current_stage")
                stage_txt = f"阶段 {stage}/4" if stage else "进行中"
                eta_txt = ""
                if prog and prog.get("eta_s"):
                    eta_txt = f"，预计剩余约 {prog['eta_s'] // 60} 分钟"
                logger.warning("训练进行中，用户尝试启动主系统——已阻止（显存冲突防护）")
                ui.notify(
                    position="top", type="warning",
                    message=f"训练进行中（{stage_txt}{eta_txt}），主系统启动已阻止。"
                            f"请先停止训练或等其完成；可先用现有角色体验实时推理。",
                    actions=[{"label": "前往训练页", "type": "route",
                              "handler": lambda: ui.navigate.to("/train_streamer", new_tab=True)}],
                )
                return
        except ImportError:
            pass

        try:
            self.running_flag = True

            # 外围程序先行（D-14 分级：外围失败降级继续，不阻断主系统拉起）
            outer_failures = self.start_programs()

            # 启动主系统进程（启动链假启动修复 2026-10-12）：代码拆分后主系统
            # 独立为 main.py（控制面 API 8082 + 主循环），webui 仅前端——但启动链
            # 沿用 webui-bak 内嵌时代的逻辑，从未拉起 main.py（stop_programs 却
            # 一直有 "main" 停止项）→「启动系统」实际只拉外围程序，假启动。
            # sys.executable=webui 进程解释器（runtime312），与手动
            # `python main.py` 等价；stop_programs("main") 原样兼容。
            #
            # D-14 启动失败分支（/autoplan 批准批次切片 2）：主系统=关键模块，
            # 失败 → 复位 running_flag + negative 通知（附修复指引）+ 异常缓冲
            # 记录；外围模块失败 → log warning + 异常缓冲 + 降级继续（notify
            # warning 呈现"部分启动"显式状态）。
            ok, reason, fix_hint = self._start_main_system()
            if not ok:
                self.running_flag = False
                self.push_exception("main", reason, fix_hint=fix_hint)
                if type == "webui":
                    ui.notify(position="top", type="negative",
                              message=f"主系统启动失败：{reason}。修复指引：{fix_hint}")
                return {"code": -1, "msg": f"主系统启动失败！{reason}"}

            if outer_failures:
                labels = "、".join(
                    self._MODULE_LABELS.get(m, m) for m, _, _ in outer_failures)
                for m, r, h in outer_failures:
                    logger.warning(f"外围程序 {m} 启动失败（降级继续）: {r}")
                    self.push_exception(m, r, fix_hint=h)
                if type == "webui":
                    ui.notify(position="top", type="warning",
                              message=f"部分启动：{labels} 未启动（其余正常），详见侧边栏运行状态条")

            # 启动后一次性存活检查（E2）：3 秒后 poll 各子进程。部分失败场景下
            # start_programs 内部只 logger.error（沿用移植自 webui-bak 的行为），
            # 且 Windows shell=True 时命令不存在 Popen 也可能"假成功"——这里把
            # 秒退程序变成用户可见的 negative notify。不复位 running_flag：
            # 持续监控/自动恢复属独立议题（TODOS「main.py 子进程退出监控」条目）。
            # 通道分派（slot 修复 2026-10-12）：ui.timer 需要 NiceGUI slot 上下文，
            # 在 /sys_cmd 等 HTTP 请求上下文调用即抛 slot empty 异常——曾把 api
            # 通道的 run 连带 except 复位 running_flag（子进程成孤儿）。webui 通道
            # （按钮点击，有 slot）走 ui.timer+notify；api 通道走线程定时器+logger。
            def check_processes_alive(use_ui: bool = False):
                for name, proc in list(self.my_subprocesses.items()):
                    try:
                        if proc.poll() is not None:
                            logger.error(f"启动后检查: 程序 {name} 已退出 (code={proc.returncode})")
                            self.push_exception(name, f"程序启动后已退出 (code={proc.returncode})")
                            if use_ui:
                                ui.notify(position="top", type="negative",
                                          message=f"程序 {name} 启动后已退出，请查看日志")
                    except Exception as e:
                        logger.error(f"启动后检查 {name} 失败: {e}")

            if type == "webui":
                ui.timer(3.0, lambda: check_processes_alive(use_ui=True), once=True)
            else:
                threading.Timer(3.0, check_processes_alive).start()

            if type == "webui":
                ui.notify(position="top", type="positive", message="程序开始运行")
            logger.info("程序开始运行")

            return {"code": 200, "msg": "程序开始运行"}
        except Exception as e:
            if type == "webui":
                ui.notify(position="top", type="negative", message=f"错误：{e}")
            logger.error(traceback.format_exc())
            self.running_flag = False

            return {"code": -1, "msg": f"运行失败！{e}"}

    def stop_external_program(self, type="webui"):
        """停止外部程序（与原文件 webui-bak.py L386-403 一致）"""
        if self.running_flag:
            try:
                # 停止协同程序和主程序
                self.stop_programs()

                self.running_flag = False
                if type == "webui":
                    ui.notify(position="top", type="positive", message="程序已停止")
                logger.info("程序已停止")

                return {"code": 200, "msg": "程序已停止"}
            except Exception as e:
                if type == "webui":
                    ui.notify(position="top", type="negative", message=f"停止错误：{e}")
                logger.error(f"停止错误：{e}")

                return {"code": -1, "msg": f"停止失败！{e}"}
        else:
            if type == "webui":
                ui.notify(position="top", type="warning", message="程序未在运行")

            return {"code": 0, "msg": "程序未在运行"}
    
    def change_light_status(self):
        """改变暗夜模式状态"""
        self.dark_mode.value = not self.dark_mode.value
        if self.dark_mode.value:
            ui.notify(position="top", type="info", message="已切换到暗夜模式")
        else:
            ui.notify(position="top", type="info", message="已切换到日间模式")
    
    def restart_application(self, type="webui"):
        """重启应用程序（与原文件 webui-bak.py L415-427 一致）"""
        try:
            # 先停止运行
            self.stop_external_program(type)

            logger.info(f"重启webui")
            if type == "webui":
                ui.notify(position="top", type="ongoing", message=f"重启中...")
            python = sys.executable
            os.execl(python, python, *sys.argv)  # Start a new instance of the application
        except Exception as e:
            logger.error(f"重启失败: {e}")
            return {"code": -1, "msg": f"重启失败！{e}"}

    def factory(self, src_path='config.json.bak', dst_path='config.json', type="webui"):
        """恢复出厂配置（与原文件 webui-bak.py L430-451 一致）"""
        try:
            base_path = str(get_base_path())
            src_full = os.path.join(base_path, src_path)
            dst_full = os.path.join(base_path, dst_path)

            with open(src_full, 'r', encoding="utf-8") as source:
                with open(dst_full, 'w', encoding="utf-8") as destination:
                    destination.write(source.read())
            logger.info("恢复出厂配置成功！")
            if type == "webui":
                ui.notify(position="top", type="positive", message=f"恢复出厂配置成功！")

            # 重启
            self.restart_application()

            return {"code": 200, "msg": "恢复出厂配置成功！"}
        except Exception as e:
            logger.error(f"恢复出厂配置失败！\n{e}")
            if type == "webui":
                ui.notify(position="top", type="negative", message=f"恢复出厂配置失败！\n{e}")

            return {"code": -1, "msg": f"恢复出厂配置失败！\n{e}"}
    
    def scroll_to_top(self):
        """滚动到页面顶部"""
        ui.run_javascript('window.scrollTo({top: 0, behavior: "smooth"})')
    
    def goto_func_page(self):
        """跳转到功能页"""
        # 清除登录页面
        if hasattr(self, 'login_column') and self.login_column:
            self.login_column.clear()
        
        # 创建主界面
        self.create_main_ui()
    
    def check_login(self):
        """检查登录状态"""
        if not get_nested_value(self.config._config, "login", "enable"):
            # 不需要登录，直接进入功能页
            self.goto_func_page()
            return
        
        # 显示登录页面
        self.login_column = self.login_form.create_login_form(
            on_login=self.handle_login,
            on_forget_password=self.handle_forget_password
        )
    
    def handle_login(self):
        """处理登录（与原文件 webui-bak.py L5132-5184 一致）"""
        if not self.login_form.validate_input():
            return

        username = self.login_form.get_username()
        password = self.login_form.get_password()

        try:
            import requests
            from urllib.parse import urljoin

            config_data = self.config._config
            ums_api = config_data.get("login", {}).get("ums_api", "")

            if not ums_api:
                # 没有配置UMS API，直接登录成功
                self.user_info = {"username": username}
                ui.notify(position="top", type="positive", message="登录成功")
                logger.info(f"用户 {username} 登录成功")
                self.goto_func_page()
                return

            API_URL = urljoin(ums_api, '/auth/login')

            resp_json = requests.post(API_URL, json={
                "username": username,
                "password": password
            }).json()

            if resp_json is None:
                ui.notify(position="top", type="negative", message="登录失败")
                return

            if "data" not in resp_json or "success" not in resp_json:
                ui.notify(position="top", type="negative", message="用户名或密码不正确")
                return

            if not resp_json["success"]:
                ui.notify(position="top", type="warning",
                          message=f'账号过期时间：{resp_json["data"].get("expiration_ts", "未知")}，已到期，请联系管理员续费')
                return

            self.user_info = resp_json["data"]
            expiration_ts = resp_json["data"].get("expiration_ts", "")

            ui.notify(position="top", type="info",
                      message=f'登录成功，账号到期时间：{expiration_ts}')
            logger.info(f"用户 {username} 登录成功")

            # 启动过期检查定时器
            ui.timer(600.0, lambda: self._check_expiration())

            # 进入功能页
            self.goto_func_page()

        except Exception as e:
            logger.error(f"登录失败: {e}")
            ui.notify(position="top", type="negative", message=f"登录失败: {e}")
    
    def handle_forget_password(self):
        """处理忘记密码"""
        ui.notify(position="top", type="info", message="请联系管理员重置密码")
    
    def create_main_ui(self):
        """创建主界面 - 使用新的 drawer 布局"""
        # 使用新的 DrawerLayout，传入保存回调函数与 app 引用（E1：启停按钮经此调用
        # AutoliveApp 函数链；app 为 Optional，None 时按钮降级为禁用态，兼容测试构造）
        self.drawer_layout = DrawerLayout(
            self.config._config,
            save_callback=self.config.save,
            app=self,
            dark_mode=self.dark_mode
        )

        # 创建左侧导航栏
        self.drawer_layout.create_drawer()

        # 创建导航栏功能分组
        self.drawer_layout.create_navigation_groups()

        # 创建内容区
        self.drawer_layout.create_content_area()

        # 默认显示第一个分组的功能列表
        function_groups = self.config._config.get("webui", {}).get("function_groups", [])
        if function_groups:
            first_group = function_groups[0]["name"]
            self.drawer_layout.select_group(first_group)
            self.drawer_layout.show_function_list(first_group)

        # 是否启用自动运行功能（E7：先于控制按钮构建执行——按钮初始态直接反映
        # running_flag，避免 auto_run 场景下按钮先构建出错误初始态、再等 timer 纠正）
        if get_nested_value(self.config._config, "webui", "auto_run"):
            logger.info("自动运行 已启用")
            self.run_external_program(type="api")

        # 创建底部控制按钮
        self._create_control_buttons()

        # 创建回到顶部按钮
        self._create_scroll_top_button()

        # D-15 首启退役键面告知（延迟至客户端连接后 notify 可达；once=True）
        ui.timer(2.5, self._notify_retired_config_keys, once=True)

    def _notify_retired_config_keys(self):
        """D-15 首启退役键面告知（基础功能 2026-10-22 节 #9）：扫描存量退役
        键→notify+log 一次性告知（标志 retired_keys_notice_shown 防重复）。
        只做可见面不改配置；notify 失败（无客户端连接）不设标志，下轮启动再试。
        """
        try:
            if get_nested_value(self.config._config, "webui", "retired_keys_notice_shown"):
                return
            hits = []
            for keys, note in _RETIRED_CONFIG_KEYS:
                node = self.config._config
                for k in keys:
                    if not isinstance(node, dict) or k not in node:
                        node = None
                        break
                    node = node[k]
                if node is not None:
                    hits.append(f"{'.'.join(keys)}：{note}")
            if not hits:
                return
            msg = "检测到已退役配置键面（不影响运行，可删除）：\n" + "\n".join(hits)
            logger.warning(msg)
            ui.notify(position="top", type="warning", message=msg, timeout=10)
            # value 必须关键字传参（Settings.set(*keys, value) 签名，2026-10-24
            # 修复：原位置传参 TypeError 被吞→标志永不落盘→每次启动重复告知）
            self.config.set("webui", "retired_keys_notice_shown", value=True)
            self.config.save()
        except Exception as e:
            logger.debug(f"退役键面告知跳过（下轮启动再试）：{e}")

    def _monitor_subprocesses(self):
        """子进程存活监控：main 死亡→running_flag 复位+告警（auto_run 时自动重拉）。

        仅在 running_flag=True（运行中）时动作——主动停止流程已复位则静默跳过。
        main 重拉仅拉主系统（coordination/edtalk/fastrag 为独立进程各自存活，
        不经 run_external_program 重拉以免双实例）；重拉前查训练显存互斥
        （与 run_external_program 同判据），训练中跳过并保持告警等人工处理。

        D1（2026-10-19）：退出事件写入异常环形缓冲（状态条呈现）；告警 notify
        受 abnormal_alarm.notify_enable 通道开关控制（默认开，缺省向后兼容）。
        """
        if not self.running_flag:
            return
        notify_enable = bool(get_nested_value(
            self.config._config, "abnormal_alarm", "notify_enable", default=True))
        for name, proc in list(self.my_subprocesses.items()):
            try:
                code = proc.poll()
            except Exception:
                continue
            if code is None:
                continue  # 存活
            logger.error(f"子进程退出监控：{name} 已退出（exit={code}）")
            self.my_subprocesses.pop(name, None)
            if name == "main":
                self.running_flag = False
                self.push_exception("main", f"主系统进程退出（exit={code}）",
                                    fix_hint="请查看完整日志定位原因后重新启动")
                if notify_enable:
                    ui.notify(position="top", type="negative",
                              message=f"主系统进程已退出（exit={code}）——请查看日志；启动按钮已恢复")
                if get_nested_value(self.config._config, "webui", "auto_run"):
                    try:
                        from utils.train_streamer import pipeline as _train_pipeline
                        if getattr(_train_pipeline, "training_running", False):
                            logger.warning("auto_run 自动重拉跳过：训练进行中（显存互斥），请训练完成后手动启动")
                            continue
                    except Exception:
                        pass
                    logger.warning("auto_run 已启用：自动重拉主系统 main")
                    ok, reason, fix_hint = self._start_main_system()
                    if ok and "main" in self.my_subprocesses:
                        self.running_flag = True
                    else:
                        self.push_exception("main", f"auto_run 自动重拉失败：{reason}",
                                            fix_hint=fix_hint)
            else:
                self.push_exception(name, f"外围程序退出（exit={code}）")
                if notify_enable:
                    ui.notify(position="top", type="warning",
                              message=f"外围程序 {name} 已退出（exit={code}）——请查看日志")
    
    def _create_control_buttons(self):
        """创建底部控制按钮 - 已移除重启和关灯按钮"""
        # 根据 CR-002 要求，重启和关灯按钮已移除
        # 一键运行/停止运行功能已合并到运行状态分组中
        # 保存配置按钮已移到三级参数设置页面
        pass
    
    def _create_scroll_top_button(self):
        """创建回到顶部按钮"""
        with ui.row().style("position:fixed; bottom: 20px; right: 20px;"):
            self.control_buttons.create_scroll_top_button(on_click=self.scroll_to_top)
    
    def _register_api_endpoints(self):
        """注册API端点（与原文件 webui-bak.py L602-925 一致）"""
        from starlette.requests import Request

        @app.post('/set_config')
        async def api_set_config(request: Request):
            """远程设置配置"""
            try:
                data_json = await request.json()
                logger.info(f'set_config接口 收到数据：{data_json}')

                config_path = data_json.get("config_path", "")
                config_data = data_json.get("data", {})

                if not config_path:
                    return {"code": -1, "message": "config_path不能为空"}

                # 读取现有配置
                import json
                try:
                    with open(config_path, 'r', encoding="utf-8") as config_file:
                        existing_data = json.load(config_file)
                except Exception as e:
                    logger.error(f"无法读取配置文件！\n{e}")
                    return {"code": -1, "message": f"无法读取配置文件！{e}"}

                # 合并字典
                existing_data.update(config_data)

                # 写入配置到配置文件
                try:
                    with open(config_path, 'w', encoding="utf-8") as config_file:
                        json.dump(existing_data, config_file, indent=2, ensure_ascii=False)
                        config_file.flush()

                    logger.info("配置数据已成功写入文件！")
                    return {"code": 200, "message": "配置数据已成功写入文件！"}
                except Exception as e:
                    logger.error(f"无法写入配置文件！\n{str(e)}")
                    return {"code": -1, "message": f"无法写入配置文件！{e}"}
            except Exception as e:
                logger.error(f"set_config处理失败: {e}")
                return {"code": -1, "message": f"set_config执行失败！{e}"}

        @app.post('/sys_cmd')
        async def api_sys_cmd(request: Request):
            """系统命令（run/stop/restart/factory）"""
            try:
                data_json = await request.json()
                logger.info(f'sys_cmd接口 收到数据：{data_json}')
                cmd_type = data_json.get('type', '')
                logger.info(f"开始执行 {cmd_type}命令...")

                if cmd_type == 'run':
                    resp_json = self.run_external_program(type="api")
                elif cmd_type == 'stop':
                    resp_json = self.stop_external_program(type="api")
                elif cmd_type == 'restart':
                    # 语义注记（E3 批次 /sys_cmd 语义标注）：此处 restart = 重启 webui
                    # 控制台自身（os.execl，见 restart_application），与 WebUI 导航栏
                    # 「停止&重启」按钮的系统级重启（stop+run，控制台不重启）不同。
                    # 语义统一/废弃待后续裁决（TODOS「/sys_cmd 重启语义统一」条目）。
                    api_type = data_json.get('api_type', 'api')
                    resp_json = self.restart_application(type=api_type)
                elif cmd_type == 'factory':
                    src_path = data_json.get('data', {}).get('src_path', 'config.json.bak')
                    dst_path = data_json.get('data', {}).get('dst_path', 'config.json')
                    resp_json = self.factory(src_path, dst_path, type="api")
                else:
                    resp_json = {"code": -1, "message": f"未知命令类型: {cmd_type}"}

                return resp_json
            except Exception as e:
                logger.error(f"sys_cmd处理失败: {e}")
                return {"code": -1, "message": f"sys_cmd执行失败！{e}"}

        @app.post('/send')
        async def api_send(request: Request):
            """发送数据到主程序"""
            try:
                data_json = await request.json()
                logger.info(f'WEBUI API send接口收到数据：{data_json}')

                import json
                import urllib.request
                config_data = self.config._config
                main_api_ip = "127.0.0.1" if config_data.get("api_ip") == "0.0.0.0" else config_data.get("api_ip", "127.0.0.1")
                main_api_port = config_data.get("api_port", 8000)
                url = f'http://{main_api_ip}:{main_api_port}/send'

                req = urllib.request.Request(
                    url,
                    data=json.dumps(data_json).encode('utf-8'),
                    headers={'Content-Type': 'application/json'}
                )
                with urllib.request.urlopen(req, timeout=60) as response:
                    resp_data = json.loads(response.read().decode('utf-8'))
                    return resp_data
            except Exception as e:
                logger.error(f"send处理失败: {e}")
                return {"code": -1, "message": f"发送数据失败！{e}"}

        @app.post('/callback')
        async def api_callback(request: Request):
            """数据回调（聊天记录）——UI-16 二轮验收任务 14：TODO 空实现修复，
            core 侧 webui_show_chat_log_callback 的聊天数据入共享缓冲，talk 页签
            ui.timer 轮询渲染（frontend/utils/chat_log_buffer.py）。"""
            try:
                data_json = await request.json()
                logger.info(f'WEBUI API callback接口收到数据：{data_json}')
                payload = (data_json or {}).get("data") or {}
                # UI-16 二轮验收（任务 14 语义修正）：白名单——聊天记录只收聊天
                # 功能互动（voice=语音对话/chatbox=聊天框发送），弹幕链等其余
                # 来源（无标记/点歌/积分/本地问答的弹幕触发路径）在此丢弃
                if payload and payload.get("chat_source") in ("voice", "chatbox"):
                    from frontend.utils.chat_log_buffer import append_chat
                    append_chat(payload)
                return {"code": 200, "message": "成功"}
            except Exception as e:
                logger.error(f"callback处理失败: {e}")
                return {"code": -1, "message": f"失败！{e}"}

        @app.post('/tts')
        async def api_tts(request: Request):
            """TTS合成接口"""
            try:
                data_json = await request.json()
                logger.info(f'WEBUI API tts接口收到数据：{data_json}')
                # TODO: 实现TTS合成
                return {"code": 200, "message": "成功", "data": {}}
            except Exception as e:
                logger.error(f"tts处理失败: {e}")
                return {"code": -1, "message": f"失败！{e}"}

        @app.post('/llm')
        async def api_llm(request: Request):
            """LLM推理接口"""
            try:
                data_json = await request.json()
                logger.info(f'WEBUI API llm接口 收到数据：{data_json}')

                import json
                import urllib.request
                config_data = self.config._config
                main_api_ip = "127.0.0.1" if config_data.get("api_ip") == "0.0.0.0" else config_data.get("api_ip", "127.0.0.1")
                main_api_port = config_data.get("api_port", 8000)
                url = f'http://{main_api_ip}:{main_api_port}/llm'

                req = urllib.request.Request(
                    url,
                    data=json.dumps(data_json).encode('utf-8'),
                    headers={'Content-Type': 'application/json'}
                )
                with urllib.request.urlopen(req, timeout=60) as response:
                    resp_data = json.loads(response.read().decode('utf-8'))
                    return resp_data
            except Exception as e:
                logger.error(f"llm处理失败: {e}")
                return {"code": -1, "message": f"失败！{e}"}

        @app.get('/get_sys_info')
        async def api_get_sys_info():
            """获取系统信息"""
            try:
                import json
                import urllib.request
                config_data = self.config._config
                main_api_ip = "127.0.0.1" if config_data.get("api_ip") == "0.0.0.0" else config_data.get("api_ip", "127.0.0.1")
                main_api_port = config_data.get("api_port", 8000)
                url = f'http://{main_api_ip}:{main_api_port}/get_sys_info'

                req = urllib.request.Request(url, method='GET')
                with urllib.request.urlopen(req, timeout=60) as response:
                    resp_data = json.loads(response.read().decode('utf-8'))
                    return resp_data
            except Exception as e:
                logger.error(f"get_sys_info处理失败: {e}")
                return {"code": -1, "message": f"get_sys_info处理失败！{e}"}

    def _check_expiration(self):
        """检查账号过期（与原文件 webui-bak.py L284-333 一致）"""
        try:
            import requests
            from urllib.parse import urljoin

            config_data = self.config._config
            ums_api = config_data.get("login", {}).get("ums_api", "")

            if not ums_api:
                return True

            API_URL = urljoin(ums_api, '/auth/check_expiration')

            if self.user_info is None:
                ui.notify(position="top", type="negative", message=f"账号登录信息失效，请重新登录")
                self.stop_programs()
                return False

            if "accessToken" not in self.user_info:
                ui.notify(position="top", type="negative", message=f"账号登录信息失效，请重新登录")
                self.stop_programs()
                return False

            headers = {
                "Authorization": "Bearer " + self.user_info["accessToken"]
            }

            response = requests.post(API_URL, headers=headers)

            if response.status_code == 200:
                resp_json = response.json()
                if resp_json.get("code") == 0 and resp_json.get("success"):
                    logger.info(f'账号可用，过期时间：{resp_json["data"]["expiration_ts"]}')
                    return True
                else:
                    ui.notify(position="top", type="negative",
                              message=f'账号已过期，请联系管理员续费')
                    self.stop_programs()
                    return False
            else:
                logger.error(f"自检异常！")
                return False
        except Exception as e:
            logger.error(f"检查过期失败: {e}")
            return False

    def run(self):
        """运行应用程序"""
        # 注册API端点
        self._register_api_endpoints()

        # 获取WebUI配置
        # 暴露面收敛（TODOS #6 / autoplan C6 批）：默认绑定改 127.0.0.1——旧默认 0.0.0.0
        # 会把无鉴权控制端（含 /sys_cmd run/stop/restart/factory）暴露给局域网。
        # 用户显式配置 0.0.0.0 时启动日志告警（页面常驻横幅由状态条任务 D-1 承载）。
        webui_ip = get_nested_value(self.config._config, "webui", "ip", default="127.0.0.1")
        if str(webui_ip) == "0.0.0.0":
            logger.warning(
                "webui 绑定为 0.0.0.0：控制端（含 /sys_cmd 启停/恢复出厂）将对局域网无鉴权暴露。"
                "确认需要局域网访问请保留；否则请在 webui 配置页把 IP 改回 127.0.0.1。"
                "（token 鉴权已登记 TODOS）"
            )
        webui_port = get_nested_value(self.config._config, "webui", "port", default=8086)
        webui_title = get_nested_value(self.config._config, "webui", "title", default="AUTOlive")

        # 设置页面标题
        ui.page_title(webui_title)

        # 检查登录状态
        self.check_login()

        # 发送心跳包（与原文件 webui-bak.py L5127 一致）
        # 注意：心跳包功能需要common模块支持，暂时用日志替代
        ui.timer(9 * 60, lambda: logger.debug("发送心跳包"))

        # NiceGUI 3.x script mode 重入防护：server 已启动时（root page 重跑），
        # UI 构建已完成（本方法前段），绝不能再 ui.run()——否则端口/事件循环冲突。
        from nicegui import core as _nicegui_core
        if getattr(_nicegui_core.app, "is_started", False):
            logger.debug("AutoliveApp.run() 重入——跳过 ui.run（server 已运行）")
            return

        # NiceGUI 3.16 socket.io 消息超限断联（TODOS 既有问题）：AsyncServer 在
        # nicegui/nicegui.py:52 硬编码初始化，无 buffer 配置通道（默认 1MB，3.x script
        # mode 全量 UI 下发易超限致"连接丢失"）——启动前猴补调大至 10MB。
        # 注意 python-engineio 参数为 snake_case max_http_buffer_size（camelCase 是 JS 版）。
        # 升级 NiceGUI 若官方开放 socket.io 配置透传，移除本补丁改用官方参数。
        import socketio as _socketio
        if getattr(_socketio.AsyncServer.__init__, "_autolive_patched", None) is not True:
            _orig_server_init = _socketio.AsyncServer.__init__

            def _patched_server_init(self, *args, **kwargs):
                kwargs.setdefault("max_http_buffer_size", 10 * 1024 * 1024)
                _orig_server_init(self, *args, **kwargs)

            _patched_server_init._autolive_patched = True
            _socketio.AsyncServer.__init__ = _patched_server_init

        # 启动应用
        ui.run(
            host=webui_ip,
            port=webui_port,
            title=webui_title,
            favicon="./ui/favicon-64.ico",
            language="zh-CN",
            dark=False,
            reload=False
        )


def main():
    """主函数"""
    try:
        app = AutoliveApp()
        app.run()
    except Exception as e:
        logger.error(f"应用程序启动失败: {e}")
        import traceback
        logger.error(traceback.format_exc())
        sys.exit(1)


if __name__ == "__main__":
    main()