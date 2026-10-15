# -*- coding: utf-8 -*-
"""
训练主播一键全链路编排（EDTalk功能集成计划 §5.2）

步骤模型（4 大阶段，Eng 修正：预处理以 run_preprocess.py 为唯一事实来源）：
  1. 预处理编排 —— 调用 EDTalk 自带 run_preprocess.py（PIPELINE_STEPS=11 步，
     含 prepare_gaze_data；继承其幂等/失败即停/--steps 步骤选择）
  2. 底模微调 —— train_fine_tune.py（--only_fine_tune_dec，默认 30000 iter）
  3. 评估 —— tools/eval_finetune.py（解析 eval_results.jsonl 机器可读输出；
     不过门槛默认停止，UI 提供"仍要继续"显式确认）
  4. 口型微调 —— train/train_audio2mouth.py（Audio2Mouth 定稿配方参数）

设计要点：
- 后台线程执行（webui 进程内），子进程 stdout 逐行回显 + 落盘
- 每完成一步写进度文件 train_progress/<角色>.json（损坏按无进度处理）
- base_dir 锁文件（PID+时间戳）跨会话互斥；启动前磁盘余量预检（native E4）
- webui 重启边界如实声明：当前步骤子进程独立存活跑完，编排线程死亡，
  后续步骤需人工从断点重跑（进度文件给出断点）
- 任一步失败即停；停止按钮终止当前子进程
"""
import json
import os
import shutil
import subprocess
import threading
import time
import traceback

from utils.my_log import logger

# 模块级训练状态（frontend/main.py start_programs 检测训练中标志用）
training_running = False
training_role: str | None = None  # 最近训练角色（D2/D20：启动拦截弹窗读进度用）


def get_edtalk_dir() -> str:
    """EDTalk 运行副本目录（AUTOlive/edtalk）。"""
    return os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "edtalk")


def get_interpreter(config_data: dict) -> str:
    return (config_data.get("edtalk_realtime", {}) or {}).get(
        "interpreter_path", "D:\\AI\\EDTalk\\runtime312\\python.exe"
    )


def pid_alive(pid: int) -> bool:
    """跨平台进程存活探测（只查询、不触碰目标进程）。

    Windows 下禁用 os.kill(pid, 0)：其非 CTRL 信号实现是
    OpenProcess(PROCESS_ALL_ACCESS)+TerminateProcess——"探测"即杀死
    同用户进程、对高权限进程（如 explorer）权限拒绝误报不存在。
    2026-10-15 修复：psutil 优先，缺失时 ctypes 仅申请查询权限。"""
    try:
        import psutil
        return psutil.pid_exists(int(pid))
    except ImportError:
        pass
    if os.name == "nt":
        import ctypes
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if not h:
            return False  # 打不开=不存在；无权场景按不存在放行（宁可漏拦不误杀）
        try:
            code = ctypes.c_ulong()
            return bool(k32.GetExitCodeProcess(h, ctypes.byref(code))) and code.value == STILL_ACTIVE
        finally:
            k32.CloseHandle(h)
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


# 评估门槛（EDTalk RUNBOOK §单角色微调，底模校准值）
EVAL_THRESHOLDS = {
    "pose_mae": 11.1,       # 姿势 MAE ≤ 11.1px
    "paste_proxy": 3.6,     # 贴回代理 ≤ 3.6px
    "bg_ssim_drop": 0.02,   # 背景 SSIM 降幅 ≤ 0.02
    "mouth_ratio": (0.8, 1.25),  # 开合比 0.8-1.25
}

# 口型微调默认配方（终门 uc2b：epoch 默认 2；义务块"默认值单一事实源表"——
# 配置键/默认/范围/生效时机：train_streamer.fine_tune_iter 默认 50000 范围 1000-1000000；
# epoch 默认 2 范围 1-10；batch_size 默认 4 范围 1-32；lr_schedule 默认三段值；amp 默认开）
AUDIO2MOUTH_DEFAULTS = {
    "preload_features": True,
    "epoch": 2,
    "batch_size": 4,
    "lr_schedule": "0.001,0.001,0.0001",
    "lip_dim_weight": 5.0,
    "mouth_vgg_weight": 0.2,
    "vgg_weight": 0.2,
    "vgg_sparse_freq": 50,
    "smooth_weight": 0.2,
    "audio_encoder_unfreeze_from": 9,
    "audio_encoder_lr_mult": 0.1,
    "amp": True,
}

# 底模微调默认（R3：实践推荐 50000；训练高级参数暴露义务）
FINE_TUNE_DEFAULTS = {
    "iter": 50000,
}

# 高级参数范围校验（Eng F-E8：非法值拒绝，不进子进程命令行）
PARAM_RANGES = {
    "iter": (1000, 1000000, int),
    "epoch": (1, 10, int),
    "batch_size": (1, 32, int),
}

# 角色名校验（design D4：角色名直接成为目录名，非法输入会炸落盘）
import re as _re

ROLE_NAME_RE = _re.compile(r"^[A-Za-z0-9_-]{1,32}$")
# Windows 保留名（不区分大小写）
_WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def validate_role_name(role_name: str) -> str | None:
    """校验角色名。返回 None=通过；字符串=拒绝原因（三段式语义）。"""
    name = (role_name or "").strip()
    if not name:
        return "problem=角色名为空；cause=上传表单未填写；fix=输入 1-32 位字母/数字/下划线/短横线"
    if name != role_name:
        return "problem=角色名含首尾空格；cause=输入带空白；fix=去除首尾空格后重试"
    if not ROLE_NAME_RE.match(name):
        return ("problem=角色名含非法字符或超长；cause=仅允许字母/数字/下划线/短横线（1-32 位）；"
                "fix=改用合法字符，中文可转拼音")
    if name.upper() in _WINDOWS_RESERVED:
        return f"problem=角色名是 Windows 保留名；cause={name} 为系统保留；fix=换一个名称"
    return None


def build_audio2mouth_args(form: dict) -> list:
    """dict(DEFAULTS) 全量合并再覆盖表单值（rvc-e2e 学习：漏键=KeyError），
    组装口型微调命令行参数。范围校验失败抛 ValueError（Eng F-E8）。"""
    merged = dict(AUDIO2MOUTH_DEFAULTS)
    for key in ("epoch", "batch_size", "lr_schedule", "amp"):
        if key in form and form[key] is not None:
            merged[key] = form[key]
    for key, (lo, hi, typ) in PARAM_RANGES.items():
        if key in merged:
            try:
                v = typ(merged[key])
            except (TypeError, ValueError):
                raise ValueError(f"problem={key} 参数非法；cause=值 {merged[key]!r} 不是 {typ.__name__}；fix=恢复默认或输入 {lo}-{hi}")
            if not (lo <= v <= hi):
                raise ValueError(f"problem={key} 超出范围；cause={v} 不在 {lo}-{hi}；fix=输入 {lo}-{hi} 内的值")
            merged[key] = v
    args = ["--preload_features"] if merged["preload_features"] else []
    args += ["--epoch", str(merged["epoch"]),
             "--batch_size", str(merged["batch_size"]),
             "--lr_schedule", str(merged["lr_schedule"]),
             "--lip_dim_weight", str(merged["lip_dim_weight"]),
             "--mouth_vgg_weight", str(merged["mouth_vgg_weight"]),
             "--vgg_weight", str(merged["vgg_weight"]),
             "--vgg_sparse_freq", str(merged["vgg_sparse_freq"]),
             "--smooth_weight", str(merged["smooth_weight"]),
             "--audio_encoder_unfreeze_from", str(merged["audio_encoder_unfreeze_from"]),
             "--audio_encoder_lr_mult", str(merged["audio_encoder_lr_mult"])]
    if merged["amp"]:
        args.append("--amp")
    return args


class TrainPipeline:
    """一键全链路训练编排器（每角色一个实例）。"""

    STAGE_NAMES = ["预处理编排(11子步)", "底模微调", "评估", "口型微调"]

    def __init__(self, config_data: dict, video_dir: str, role_name: str,
                 on_log=None, on_complete=None, on_confirm_needed=None,
                 start_stage: int = 1):
        """
        Args:
            config_data: config.json 全量字典
            video_dir: 用户输入的原始视频文件夹路径（含 original_videos 子目录）
            role_name: 角色名（默认取文件夹名）
            on_log: 日志行回调 fn(line: str, stage_idx: int|None)
            on_complete: 完成回调 fn(success: bool, message: str)
            on_confirm_needed: 评估确认回调 fn()——UI 弹"仍要继续"确认框后调
                self.confirm_continue() / self.confirm_stop()
            start_stage: 起始阶段（1-4，步骤级重跑）
        """
        self.config_data = config_data
        self.video_dir = os.path.abspath(video_dir)
        self.role_name = role_name
        self.on_log = on_log or (lambda line, stage=None: None)
        self.on_complete = on_complete or (lambda ok, msg: None)
        self.on_confirm_needed = on_confirm_needed or (lambda: None)
        self.start_stage = max(1, min(4, start_stage))

        self.edtalk_dir = get_edtalk_dir()
        self.interpreter = get_interpreter(config_data)
        # 高级参数：dict(DEFAULTS) 全量合并再覆盖表单值（义务块"训练高级参数暴露"；
        # epoch 终门 uc2b 默认 2；iter R3 实践推荐 50000）
        _ts = (config_data.get("train_streamer", {}) or {})
        self.iter_count = int(_ts.get("fine_tune_iter", FINE_TUNE_DEFAULTS["iter"]) or FINE_TUNE_DEFAULTS["iter"])
        self.a2m_form = {
            "epoch": _ts.get("epoch", AUDIO2MOUTH_DEFAULTS["epoch"]),
            "batch_size": _ts.get("batch_size", AUDIO2MOUTH_DEFAULTS["batch_size"]),
            "lr_schedule": _ts.get("lr_schedule", AUDIO2MOUTH_DEFAULTS["lr_schedule"]),
            "amp": bool(_ts.get("amp", AUDIO2MOUTH_DEFAULTS["amp"])),
        }
        # 训练数据根（上传落点义务：config.train_streamer.data_root）
        self.data_root = _ts.get("data_root") or os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "data", "edtalk_train")
        # 训练开始墙钟（ETA/总耗时义务）
        self._t_start: float | None = None
        self._stage_t0: float | None = None
        # 显存清理状态（DX F-5/Eng F-E13/F-E14）
        self._vram_baseline_gb: float | None = None
        self._cleanup_timed_out = False
        self._final_message = ""

        self._process: subprocess.Popen | None = None
        self._last_proc: subprocess.Popen | None = None
        self._stop_requested = False
        self.confirm_event = threading.Event()
        self.confirm_decision = None  # None/"continue"/"stop"
        self.current_stage = 0
        self._finished = False  # 实例级完成标志（UI _poll 判定用；跨模块 training_running 是值绑定不可靠）
        self._log_file = None
        # UI 轮询状态标志（train_streamer 页面消费）
        self._completed_ok = False
        self._confirm_pending = False
        self._confirm_dialog_shown = False

        global training_running

    # ---------- 路径与状态 ----------

    @property
    def progress_path(self) -> str:
        return os.path.join(self.edtalk_dir, "train_progress", f"{self.role_name}.json")

    @property
    def lock_path(self) -> str:
        return os.path.join(self.edtalk_dir, "train_progress", f".lock-{self.role_name}")

    def _log(self, line: str, stage=None):
        """日志回显 + 落盘（logs/train_streamer/<日期>-<角色>.log）。"""
        self.on_log(line, stage if stage is not None else self.current_stage)
        try:
            if self._log_file is None:
                log_dir = os.path.join(os.path.dirname(self.edtalk_dir), "logs", "train_streamer")
                os.makedirs(log_dir, exist_ok=True)
                self._log_file = open(
                    os.path.join(log_dir, f"{time.strftime('%Y%m%d')}-{self.role_name}.log"),
                    "a", encoding="utf-8",
                )
            self._log_file.write(line + "\n")
            self._log_file.flush()
        except Exception:
            pass

    def _write_progress(self, stage: int, status: str):
        """写进度文件（native F6：断点步号来源；损坏按无进度处理）。
        Eng F-E12：写入原子化（临时文件+rename），读者扩展到任意页面+拦截弹窗。"""
        try:
            os.makedirs(os.path.dirname(self.progress_path), exist_ok=True)
            elapsed_s = round(time.time() - self._t_start, 1) if self._t_start else None
            data = {
                "role": self.role_name,
                "video_dir": self.video_dir,
                "completed_stage": stage - 1 if status == "done" else stage - 1,
                "current_stage": stage,
                "status": status,
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                "elapsed_s": elapsed_s,
            }
            tmp = self.progress_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=1)
            os.replace(tmp, self.progress_path)  # 原子替换（Eng F-E12）
        except Exception as e:
            logger.warning(f"进度文件写入失败（不阻塞训练）：{e}")

    @staticmethod
    def read_progress(role_name: str) -> dict | None:
        """读取进度文件（静态方法；损坏返回 None）。"""
        path = os.path.join(get_edtalk_dir(), "train_progress", f"{role_name}.json")
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None

    # ---------- 前置校验 ----------

    def preflight(self) -> str | None:
        """启动前校验。返回错误消息（None=通过）。三段式语义（D7）。"""
        global training_running
        if training_running:
            return "problem=已有训练任务在进行中；cause=防重入保护；fix=等待当前训练完成或先停止它"
        # 角色名校验（design D4：目录名安全性）
        name_err = validate_role_name(self.role_name)
        if name_err:
            return name_err
        if not self.video_dir or not os.path.isdir(self.video_dir):
            return f"problem=视频目录不存在；cause={self.video_dir}；fix=检查路径或重新上传视频"
        if not os.path.isdir(os.path.join(self.video_dir, "original_videos")):
            return (
                f"problem=目录中未找到 original_videos 子目录。"
                f"cause=原始视频需放入 <目录>\\original_videos\\ 下；"
                f"fix=按 EDTalk 数据集处理流程组织目录后重试"
            )
        if not os.path.exists(self.interpreter):
            return (
                f"problem=解释器不存在：{self.interpreter}。"
                f"cause=runtime312 未安装或路径变更（源目录结构债，见资源清单）；"
                f"fix=修改画面设置页『解释器路径』配置项指向可用解释器"
            )
        if not os.path.isfile(os.path.join(self.edtalk_dir, "data_preprocess", "data_preprocess_for_train", "run_preprocess.py")):
            return (
                f"problem=EDTalk 运行副本缺失：{self.edtalk_dir}。"
                f"cause=尚未执行复制；fix=运行 Scripts/copy_edtalk.py 一键复制脚本"
            )
        # 双向互斥组合判据（Eng F-E2：端口活性+子进程注册表+启动标志，任一为真即拦截）
        conflict = self._check_gpu_conflict()
        if conflict:
            return conflict
        # 跨会话互斥锁（native E4：PID+时间戳；Eng F-E3：陈旧锁自动恢复；
        # 存活探测用 pid_alive——Windows 下 os.kill(pid,0) 语义是 TerminateProcess）
        if os.path.exists(self.lock_path):
            try:
                with open(self.lock_path, encoding="utf-8") as f:
                    lock = json.load(f)
                pid = lock.get("pid")
                alive = bool(pid) and pid_alive(pid)
                if alive:
                    return (f"problem=该角色已有训练进程运行（PID {pid}，开始于 {lock.get('time')}）；"
                            f"cause=锁文件指向存活进程；fix=等待完成或停止该训练；确认无进程可手动删除 {self.lock_path}")
                logger.warning(f"[{self.role_name}] 检测到陈旧训练锁（PID {pid} 已不存在），已自动恢复")
                os.remove(self.lock_path)
            except Exception as e:
                return f"problem=锁文件读取失败；cause={e}；fix=可手动删除 {self.lock_path} 后重试"
        # 磁盘余量预检（native E4：拆帧产物巨大，建议 ≥50GB）
        try:
            free_gb = shutil.disk_usage(self.video_dir).free / (1024 ** 3)
            if free_gb < 50:
                return (
                    f"problem=磁盘剩余空间不足（{free_gb:.0f}GB < 50GB）。"
                    f"cause=预处理拆帧产物巨大（wy 基准 63055 clips）；fix=清理磁盘后重试"
                )
        except Exception as e:
            logger.warning(f"磁盘余量检测失败（不阻塞）：{e}")
        return None

    def _check_gpu_conflict(self) -> str | None:
        """双向互斥组合判据（Eng F-E2）：反向（主系统运行中→拦训练）。
        组合判据=EDTalk 端口活性+主系统子进程注册表，任一为真即拦截。

        2026-10-15 修复：首条判据原误用 is_edtalk_active——那是**配置意图**
        判定（用户选择了 EDTalk 驱动且 enable=True），并非运行状态，导致用户
        未启动系统也被拦"实时推理服务正在运行"（实测：8000 无监听、GPU 空闲
        仍被拦）。配置开关≠进程在跑（同 danmaku 监听开关语义教训），改为端口
        活性探测——与训练主播 tab 层 _check_gpu_conflict 同款判据。"""
        # 1) 端口活性（真实运行状态探测）
        try:
            import requests as _requests
            port = (self.config_data.get("edtalk_realtime", {}) or {}).get("port", 8000)
            r = _requests.get(f"http://127.0.0.1:{port}/status", timeout=1)
            if r.status_code == 200:
                return ("problem=实时推理服务正在运行；cause=16GB 显存下训练与推理不可并存"
                        "（GPU 显存冲突）；fix=先在画面设置页停止实时推理，再启动训练")
        except Exception:
            pass
        # 2) 主系统子进程注册表（frontend/main.py my_subprocesses 非空即有子进程；
        #    webui 进程内为模块缓存，注册表真实有效）
        try:
            from frontend.main import my_subprocesses
            alive = [n for n, p in (my_subprocesses or {}).items()
                     if getattr(p, "poll", lambda: None)() is None]
            if alive:
                return (f"problem=主系统子进程运行中（{','.join(alive[:3])}）；"
                        f"cause=显存可能被占用（16GB 卡训练与推理不可并存）；"
                        f"fix=先停止主系统再启动训练")
        except ImportError:
            pass
        return None

    # ---------- 子进程执行 ----------

    def _run_step(self, cmd: list, stage: int, cwd: str | None = None) -> bool:
        """执行一个子进程步骤：逐行回显、失败即停。返回 True=成功。"""
        if self._stop_requested:
            return False
        self._log(f"[步骤 {stage}/4] {' '.join(cmd[:6])}…", stage)
        self._log(f"[步骤 {stage}/4] 开始：{self.STAGE_NAMES[stage - 1]}", stage)
        try:
            # PYTHONIOENCODING：Windows 中文环境子进程 stdout 非 tty 默认 cp936，
            # 父进程按 utf-8 逐行解码必乱码（2026-10-15 用户实测）——统一强制
            # 子进程 UTF-8 输出（子进程内部再起的子进程继承 env）
            _env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
            self._process = subprocess.Popen(
                cmd, cwd=cwd or self.edtalk_dir, shell=False,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                encoding="utf-8", errors="replace", bufsize=1, env=_env,
            )
            self._last_proc = self._process
            for line in self._process.stdout:
                line = line.rstrip("\n")
                self._log(line, stage)
            code = self._process.wait()
            self._process = None
            if self._stop_requested:
                self._log(f"[步骤 {stage}/4] 用户停止", stage)
                return False
            if code != 0:
                self._log(
                    f"[步骤 {stage}/4] 失败（exit {code}）。problem=步骤执行失败；"
                    f"cause=见上方日志；fix=根据错误日志处理后从本步重跑", stage)
                return False
            self._log(f"[步骤 {stage}/4] 完成：{self.STAGE_NAMES[stage - 1]}", stage)
            return True
        except Exception as e:
            self._log(f"[步骤 {stage}/4] 异常：{traceback.format_exc()}", stage)
            self._process = None
            return False

    # ---------- 显存清理（义务块"步骤间显存清理"；DX F-5 超时+Eng F-E13/F-E14 边界） ----------

    # 显存回落轮询超时（秒）——超时转"清理失败"态，不死锁用户
    VRAM_POLL_TIMEOUT_S = 120

    def _cleanup_after_step(self, proc: subprocess.Popen | None, stage: int) -> None:
        """阶段完成后清理子进程树并等待显存回落。

        范式移植自 train_voice_pipeline（义务块）：taskkill /F /T 杀子进程树
        + nvidia-smi 显存回落基线轮询。超时/降级路径（DX F-5/Eng F-E14）：
        轮询 120s 超时→"清理失败"态三段式提示+强制解除出口标志；
        nvidia-smi 不可用→仅 taskkill+日志，不阻塞。"""
        if proc is not None and proc.poll() is None:
            try:
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                               capture_output=True, timeout=15)
                self._log(f"[清理] 子进程树 {proc.pid} 已终止", stage)
            except Exception as e:
                self._log(f"[清理] taskkill 失败（继续轮询）：{e}", stage)
        # 显存基线=训练开始前采样值（Eng F-E14）
        baseline_gb = self._vram_baseline_gb
        t0 = time.time()
        while time.time() - t0 < self.VRAM_POLL_TIMEOUT_S:
            used = self._query_vram_gb()
            if used is None:
                # nvidia-smi 不可用（Eng F-E14 降级路径）
                self._log("[清理] nvidia-smi 不可用，跳过显存轮询（仅进程清理）", stage)
                return
            if baseline_gb is None or used <= baseline_gb + 0.5:
                self._log(f"[清理] 显存已回落基线（{used:.1f}GB）", stage)
                return
            time.sleep(3)
        # 超时（DX F-5：清理失败态+强制解除出口，不死锁用户）
        self._log(
            f"[清理][失败] problem=显存 {self._query_vram_gb() or '?'}GB 超过 {self.VRAM_POLL_TIMEOUT_S}s 未回落基线；"
            f"cause=可能有残留进程占用显存（nvidia-smi 查看）；"
            f"fix=手动 taskkill 残留 python 进程，或点击『强制解除并继续』（警告：显存可能未释放，"
            f"启动主系统可能失败）", stage)
        self._cleanup_timed_out = True

    def force_release_mutex(self) -> None:
        """『强制解除并继续』出口（DX F-5）：用户显式确认后解除清理失败态。"""
        self._cleanup_timed_out = False
        self._log("[清理] 用户已确认强制解除显存互斥（Eng F-E13 警告：显存可能未释放）")

    def _query_vram_gb(self) -> float | None:
        try:
            out = subprocess.run(
                ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                capture_output=True, timeout=10, text=True)
            if out.returncode != 0:
                return None
            return float(out.stdout.strip().splitlines()[0]) / 1024.0
        except Exception:
            return None

    def _sample_vram_baseline(self) -> None:
        """训练开始前采样显存基线（Eng F-E14：基线非 0）。"""
        self._vram_baseline_gb = self._query_vram_gb()
        self._log(f"[基线] 训练前显存占用 {self._vram_baseline_gb if self._vram_baseline_gb is not None else '未知（降级）'}GB")

    def run(self):
        """执行全链路训练。阻塞调用——请放后台线程。"""
        global training_running, training_role
        self.confirm_decision = None
        self.confirm_event.clear()
        success = False
        message = ""
        try:
            err = self.preflight()
            if err:
                self._log(f"[前置校验] {err}")
                # 不在此处调 on_complete——finally 统一调用（否则与 L626 区
                # 重复，UI 收到两条"=== 结束 ==="，2026-10-15 修复）
                message = err
                return
            # 前置校验通过后才置运行标志（2026-10-15 防重入自锁修复：原先
            # 先置 True 再 preflight，而 preflight 首条判据就是 training_running
            # ——每次启动训练必被自己拦住"已有训练任务在进行中"）
            training_running = True
            training_role = self.role_name

            # 获取锁
            os.makedirs(os.path.dirname(self.lock_path), exist_ok=True)
            with open(self.lock_path, "w", encoding="utf-8") as f:
                json.dump({"pid": os.getpid(), "time": time.strftime("%Y-%m-%d %H:%M:%S")}, f)

            self._log(f"=== 训练开始：角色 {self.role_name}，目录 {self.video_dir}，从阶段 {self.start_stage} 开始 ===")
            t0 = time.time()
            self._t_start = t0
            self._sample_vram_baseline()

            # ---- 阶段 1：预处理编排 ----
            if self.start_stage <= 1:
                self.current_stage = 1
                self._write_progress(1, "running")
                # -u：非 tty stdout 默认块缓冲，无 -u 时逐行回显会延迟到缓冲满
                cmd = [self.interpreter, "-u", "data_preprocess/data_preprocess_for_train/run_preprocess.py",
                       "--base_dir", self.video_dir]
                ok = self._run_step(cmd, 1)
                self._cleanup_after_step(self._last_proc, 1)
                if not ok or self._cleanup_timed_out:
                    message = "预处理失败，详见日志" if not ok else "预处理后显存清理失败（可强制解除后重跑下一阶段）"
                    self._write_progress(1, "failed")
                    return
                self._write_progress(1, "done")

            # ---- 阶段 2：底模微调 ----
            if self.start_stage <= 2:
                self.current_stage = 2
                self._write_progress(2, "running")
                # --datapath 语义=预处理产物根（train_fine_tune 在其下找 count/
                # 子目录；facemark 取其同级目录）——原传 role_name 相对名在
                # edtalk cwd 下不存在必 FileNotFoundError（2026-10-15 实测）
                cmd = [self.interpreter, "-u", "train_fine_tune.py",
                       "--datapath", self.video_dir,
                       "--exp_name", self.role_name,
                       "--only_fine_tune_dec",
                       "--iter", str(self.iter_count)]
                ok = self._run_step(cmd, 2)
                self._cleanup_after_step(self._last_proc, 2)
                if not ok or self._cleanup_timed_out:
                    message = "底模微调失败，详见日志" if not ok else "底模微调后显存清理失败（可强制解除后重跑下一阶段）"
                    self._write_progress(2, "failed")
                    return
                self._write_progress(2, "done")

            # ---- 阶段 3：评估 ----
            if self.start_stage <= 3:
                self.current_stage = 3
                self._write_progress(3, "running")
                eval_out = "tools/eval_results"
                # --datapath 同阶段 2 语义=产物根（eval_finetune 同样在其下找 count/）
                cmd = [self.interpreter, "-u", "tools/eval_finetune.py",
                       "--cand-ckpt", f"ckpt_models/{self.role_name}/checkpoint/{self.iter_count:06d}.pt",
                       "--datapath", self.video_dir,
                       "--out-dir", eval_out]
                if not self._run_step(cmd, 3):
                    message = "评估执行失败，详见日志"
                    self._write_progress(3, "failed")
                    return
                metrics = self._parse_eval_results(os.path.join(self.edtalk_dir, eval_out))
                # D12：评估指标摘要（确认框展示具体数字，不盲选）
                self._last_eval_summary = {
                    "candidate": metrics.get("candidate") or {},
                    "base": metrics.get("base") or {},
                    "verdict": None,
                }
                verdict = self._judge_eval(metrics)
                self._last_eval_summary["verdict"] = verdict
                self._cleanup_after_step(self._last_proc, 3)
                if verdict is not None:
                    # 门槛不过：默认停止，UI 提供"仍要继续"显式确认（D12 义务：确认框带指标与两选项后果）
                    self.confirm_decision = None
                    self.confirm_event.clear()
                    try:
                        self.on_confirm_needed()
                    except Exception:
                        pass
                    confirmed = self.confirm_event.wait(timeout=300)
                    if not confirmed or self.confirm_decision != "continue":
                        self._log("[评估] 用户未确认继续——按计划默认停止（不进入口型微调）")
                        message = f"评估未过门槛已停止：{verdict}"
                        self._write_progress(3, "failed")
                        return
                    self._log("[评估] 用户确认继续——进入口型微调")
                self._write_progress(3, "done")

            # ---- 阶段 4：口型微调 ----
            if self.start_stage <= 4:
                self.current_stage = 4
                self._write_progress(4, "running")
                try:
                    a2m_args = build_audio2mouth_args(self.a2m_form)
                except ValueError as ve:
                    message = f"problem=高级参数校验失败；{ve}"
                    self._log(f"[口型微调] {message}", 4)
                    self._write_progress(4, "failed")
                    return
                # --data_path 同语义=产物根（Audio2LipDataset 在其下找
                # lmdb_features/ 与 lmdb/，均为阶段 1 产物）
                cmd = [self.interpreter, "-u", "train/train_audio2mouth.py",
                       "--data_path", self.video_dir,
                       "--resume_ckpt", f"ckpt_models/{self.role_name}/checkpoint/{self.iter_count:06d}.pt",
                       "--audio2lip_ckpt", "ckpts/Audio2Lip.pt",
                       "--exp_name", self.role_name] + a2m_args
                ok = self._run_step(cmd, 4)
                self._cleanup_after_step(self._last_proc, 4)
                if not ok or self._cleanup_timed_out:
                    message = "口型微调失败，详见日志" if not ok else "口型微调后显存清理失败（可强制解除后重试部署）"
                    self._write_progress(4, "failed")
                    return
                self._write_progress(4, "done")

            elapsed = time.time() - t0
            # ---- checkpoint 自动部署（义务块：评估通过/用户确认继续两路径均部署；
            #      DX F-4：失败三段式+产物保留+手动兜底指引） ----
            deploy_ok = False
            try:
                character_dir = (self.config_data.get("edtalk_realtime", {}) or {}).get(
                    "character_dir") or self.role_name
                deploy_msg = self.apply_to_digital_human(self.role_name, character_dir, self.iter_count)
                deploy_ok = True
                self._log(f"[部署] {deploy_msg}", 4)
            except Exception as de:
                self._log(
                    f"[部署][失败] problem=checkpoint 自动部署失败；cause={de}；"
                    f"fix=检查上方日志；手动兜底：将 ckpt_models/{self.role_name}/checkpoint/"
                    f"{self.iter_count:06d}.pt 的 gen 键导出为角色包 checkpoint/new.pt"
                    f"（训练产物已保留，不丢失）", 4)
                deploy_msg = f"自动部署失败：{de}"

            stage_times = ""
            self._log(f"=== 训练完成：产物 ckpt_models/{self.role_name}/checkpoint/（总耗时 {elapsed/3600:.1f}h，"
                      f"部署{'成功' if deploy_ok else '失败（手动兜底可用）'}）===")
            message = (f"训练完成（总耗时 {elapsed/3600:.1f} 小时）。"
                       f"{'已自动部署，重启实时推理后生效。' if deploy_ok else deploy_msg}")
            self._completed_ok = True
            self._final_message = message  # D2/D17：完成通知+状态卡尾部呈现
            success = True
            self.on_complete(True, message)
        except Exception as e:
            logger.error(traceback.format_exc())
            message = f"训练编排异常：{e}"
            # on_complete 统一由 finally 调用（避免双调）
        finally:
            self._finished = True  # UI _poll 据此走"训练结束"分支（置位先于按钮恢复判定）
            self._final_message = message or "训练未完成"
            self.current_stage = 0
            try:
                if os.path.exists(self.lock_path):
                    os.remove(self.lock_path)
            except Exception:
                pass
            if self._log_file:
                try:
                    self._log_file.close()
                except Exception:
                    pass
                self._log_file = None
            training_running = False
            if not success:
                self.on_complete(False, message or "训练未完成")

    # ---------- 评估解析 ----------

    def _parse_eval_results(self, out_dir: str) -> dict:
        """解析 eval_results.jsonl（机器可读输出，Eng 义务②）。

        实际结构（eval_finetune.py 实测）：每行一个 JSON，含 role 字段
        （base=底模对照行 / candidate=候选行），指标嵌套在 metrics 内。
        返回 {"base": {...}, "candidate": {...}} 两个 metrics 字典。
        """
        parsed = {"base": {}, "candidate": {}}
        path = os.path.join(out_dir, "eval_results.jsonl")
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                        role = row.get("role", "candidate")
                        metrics = row.get("metrics") or {}
                        if role in parsed and metrics:
                            parsed[role] = metrics  # 取最后一行（最新）
                    except json.JSONDecodeError:
                        continue
        except Exception as e:
            self._log(f"[评估] 结果文件读取失败：{e}")
        self._log(f"[评估] base 指标：{json.dumps(parsed['base'], ensure_ascii=False)}")
        self._log(f"[评估] candidate 指标：{json.dumps(parsed['candidate'], ensure_ascii=False)}")
        return parsed

    def _judge_eval(self, parsed: dict) -> str | None:
        """按门槛判定评估结果（EDTalk RUNBOOK §单角色微调）。

        实测字段（eval_results.jsonl metrics 嵌套）：
        - pose_keep_mae_px ≤ 11.1（姿势保持）
        - paste_proxy_mae_px ≤ 3.6（贴回代理）
        - 背景 SSIM 降幅：base.bg_ssim - candidate.bg_ssim ≤ 0.02
        - lip_open_ratio_vs_ref ∈ [0.8, 1.25]（开合比）

        返回 None=通过（或无法解析——交人工判断）；字符串=未过门槛原因。
        """
        cand = parsed.get("candidate") or {}
        base = parsed.get("base") or {}
        if not cand:
            self._log("[评估] 未解析到指标——交由用户人工判断（门槛参考：姿势MAE≤11.1px、贴回≤3.6px、背景SSIM降幅≤0.02、开合比0.8-1.25）")
            return "未解析到评估指标"
        reasons = []
        mae = cand.get("pose_keep_mae_px")
        if mae is not None and float(mae) > EVAL_THRESHOLDS["pose_mae"]:
            reasons.append(f"姿势MAE {mae:.2f} > {EVAL_THRESHOLDS['pose_mae']}")
        paste = cand.get("paste_proxy_mae_px")
        if paste is not None and float(paste) > EVAL_THRESHOLDS["paste_proxy"]:
            reasons.append(f"贴回代理 {paste:.2f} > {EVAL_THRESHOLDS['paste_proxy']}")
        cand_ssim = cand.get("bg_ssim")
        base_ssim = base.get("bg_ssim")
        if cand_ssim is not None and base_ssim is not None:
            drop = float(base_ssim) - float(cand_ssim)
            if drop > EVAL_THRESHOLDS["bg_ssim_drop"]:
                reasons.append(f"背景SSIM降幅 {drop:.3f} > {EVAL_THRESHOLDS['bg_ssim_drop']}")
        ratio = cand.get("lip_open_ratio_vs_ref")
        if ratio is not None and not (EVAL_THRESHOLDS["mouth_ratio"][0] <= float(ratio) <= EVAL_THRESHOLDS["mouth_ratio"][1]):
            reasons.append(f"开合比 {ratio:.2f} 超出 {EVAL_THRESHOLDS['mouth_ratio']}")
        return "；".join(reasons) if reasons else None

    # ---------- 用户确认与停止 ----------

    def confirm_continue(self):
        """评估"仍要继续"确认（UI 回调）。"""
        self.confirm_decision = "continue"
        self.confirm_event.set()

    def confirm_stop(self):
        """评估确认框选择停止。"""
        self.confirm_decision = "stop"
        self.confirm_event.set()

    def stop(self):
        """停止训练：终止当前子进程，编排按失败即停收尾。"""
        self._stop_requested = True
        self.confirm_decision = "stop"
        self.confirm_event.set()
        if self._process and self._process.poll() is None:
            try:
                self._process.terminate()
                logger.info(f"训练子进程 {self._process.pid} 已终止（用户停止）")
            except Exception as e:
                logger.error(f"终止训练子进程失败：{e}")

    # ---------- 应用到数字人（训练→开播接线，A1 映射已验证） ----------

    @staticmethod
    def apply_to_digital_human(role_name: str, character_dir: str, iter_count: int = 30000) -> str:
        """把训练产物应用到实时推理（打通"训练→开播"最后一公里）。

        映射（2026-09-24 迷你冒烟 strict 加载验证通过）：
        - ckpt_models/<角色>/checkpoint/<iter:06d>.pt 的 ['gen'] state_dict
          → <character_dir>/checkpoint/new.pt（realtime_serve Generator 权重）
        - 口型微调产物 audio2lip ckpt → <character_dir>/audio2lip.pt

        Args:
            role_name: 训练角色名
            character_dir: realtime_serve 角色素材目录名（config.edtalk_realtime.character_dir）
            iter_count: 底模微调 iter（定位产物文件名）

        Returns:
            成功消息；失败抛异常（调用方 catch 后 notify）。
        """
        import torch

        edtalk_dir = get_edtalk_dir()
        char_root = os.path.join(edtalk_dir, character_dir)
        ckpt_dir = os.path.join(char_root, "checkpoint")
        os.makedirs(ckpt_dir, exist_ok=True)

        # 1) Generator 权重：gen 键 → new.pt（仅自产 ckpt 校验，native S2）
        cand_path = os.path.join(edtalk_dir, "ckpt_models", role_name, "checkpoint", f"{iter_count:06d}.pt")
        if not os.path.exists(cand_path):
            raise FileNotFoundError(f"训练产物不存在：{cand_path}")
        cand = torch.load(cand_path, map_location="cpu", weights_only=False)
        if "gen" not in cand:
            raise ValueError(f"产物缺少 gen 键（非本编排器自产 ckpt？）：{cand_path}")
        new_pt_path = os.path.join(ckpt_dir, "new.pt")
        torch.save({"gen": cand["gen"]}, new_pt_path)

        # 2) 口型权重：口型微调产物 → audio2lip.pt
        a2l_dir = os.path.join(edtalk_dir, "Audio2Lip", role_name)
        a2l_src = None
        if os.path.isdir(a2l_dir):
            # 取该角色最新口型微调产物
            for root, _, files in os.walk(a2l_dir):
                pts = sorted(f for f in files if f.endswith(".pt"))
                if pts:
                    a2l_src = os.path.join(root, pts[-1])
        if a2l_src:
            shutil.copy2(a2l_src, os.path.join(char_root, "audio2lip.pt"))
            a2l_msg = f"口型权重 {a2l_src} → audio2lip.pt"
        else:
            a2l_msg = "未找到口型微调产物（audio2lip.pt 保持不变）"

        return (
            f"已应用：gen → {new_pt_path}；{a2l_msg}。"
            f"重启 realtime_serve 后生效（停止并重新『运行系统』）"
        )
