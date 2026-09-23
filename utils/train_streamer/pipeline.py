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


def get_edtalk_dir() -> str:
    """EDTalk 运行副本目录（AUTOlive/edtalk）。"""
    return os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "edtalk")


def get_interpreter(config_data: dict) -> str:
    return (config_data.get("edtalk_realtime", {}) or {}).get(
        "interpreter_path", "D:\\AI\\EDTalk\\runtime312\\python.exe"
    )


# 评估门槛（EDTalk RUNBOOK §单角色微调，底模校准值）
EVAL_THRESHOLDS = {
    "pose_mae": 11.1,       # 姿势 MAE ≤ 11.1px
    "paste_proxy": 3.6,     # 贴回代理 ≤ 3.6px
    "bg_ssim_drop": 0.02,   # 背景 SSIM 降幅 ≤ 0.02
    "mouth_ratio": (0.8, 1.25),  # 开合比 0.8-1.25
}

# 口型微调定稿配方（EDTalk 记忆：Audio2Mouth 优化最终记录，wy 发声版 2026-09-19）
AUDIO2MOUTH_ARGS = [
    "--preload_features",
    "--epoch", "2",
    "--batch_size", "4",
    "--lr_schedule", "0.001,0.001",
    "--lip_dim_weight", "5.0",
    "--mouth_vgg_weight", "0.2",
    "--vgg_weight", "0.2",
    "--vgg_sparse_freq", "50",
    "--smooth_weight", "0.2",
    "--audio_encoder_unfreeze_from", "9",
    "--audio_encoder_lr_mult", "0.1",
    "--amp",
]


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
        self.iter_count = int((config_data.get("train_streamer", {}) or {}).get("fine_tune_iter", 30000))

        self._process: subprocess.Popen | None = None
        self._stop_requested = False
        self.confirm_event = threading.Event()
        self.confirm_decision = None  # None/"continue"/"stop"
        self.current_stage = 0
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
        """写进度文件（native F6：断点步号来源；损坏按无进度处理）。"""
        try:
            os.makedirs(os.path.dirname(self.progress_path), exist_ok=True)
            data = {
                "role": self.role_name,
                "video_dir": self.video_dir,
                "completed_stage": stage - 1 if status == "done" else stage - 1,
                "current_stage": stage,
                "status": status,
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
            with open(self.progress_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=1)
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
        """启动前校验。返回错误消息（None=通过）。"""
        global training_running
        if training_running:
            return "已有训练任务在进行中（防重入）"
        if not self.video_dir or not os.path.isdir(self.video_dir):
            return f"视频目录不存在：{self.video_dir}"
        if not os.path.isdir(os.path.join(self.video_dir, "original_videos")):
            return (
                f"目录中未找到 original_videos 子目录。problem=目录结构不符；"
                f"cause=原始视频需放入 <目录>\\original_videos\\ 下；"
                f"fix=按 EDTalk 数据集处理流程组织目录后重试"
            )
        if not os.path.exists(self.interpreter):
            return (
                f"解释器不存在：{self.interpreter}。problem=解释器缺失；"
                f"cause=runtime312 未安装或路径变更；"
                f"fix=修改画面设置页 interpreter_path 配置项"
            )
        if not os.path.isfile(os.path.join(self.edtalk_dir, "data_preprocess", "data_preprocess_for_train", "run_preprocess.py")):
            return (
                f"EDTalk 运行副本缺失：{self.edtalk_dir}。problem=副本未复制；"
                f"cause=尚未执行复制；fix=运行 scripts/copy_edtalk.py 一键复制脚本"
            )
        # 跨会话互斥锁（native E4：PID+时间戳）
        if os.path.exists(self.lock_path):
            try:
                with open(self.lock_path, encoding="utf-8") as f:
                    lock = json.load(f)
                pid = lock.get("pid")
                alive = False
                if pid:
                    try:
                        os.kill(pid, 0)
                        alive = True
                    except OSError:
                        alive = False
                if alive:
                    return f"该角色已有训练进程运行（PID {pid}，开始于 {lock.get('time')}）。如确认无进程可手动删除 {self.lock_path}"
                logger.warning("发现孤儿锁文件（进程已不存在），自动清除")
                os.remove(self.lock_path)
            except Exception as e:
                return f"锁文件读取失败：{e}（可手动删除 {self.lock_path}）"
        # 磁盘余量预检（native E4：拆帧产物巨大，建议 ≥50GB）
        try:
            free_gb = shutil.disk_usage(self.video_dir).free / (1024 ** 3)
            if free_gb < 50:
                return (
                    f"磁盘剩余空间不足（{free_gb:.0f}GB < 50GB）。problem=磁盘空间不足；"
                    f"cause=预处理拆帧产物巨大（wy 基准 63055 clips）；fix=清理磁盘后重试"
                )
        except Exception as e:
            logger.warning(f"磁盘余量检测失败（不阻塞）：{e}")
        return None

    # ---------- 子进程执行 ----------

    def _run_step(self, cmd: list, stage: int, cwd: str | None = None) -> bool:
        """执行一个子进程步骤：逐行回显、失败即停。返回 True=成功。"""
        if self._stop_requested:
            return False
        self._log(f"[步骤 {stage}/4] {' '.join(cmd[:6])}…", stage)
        self._log(f"[步骤 {stage}/4] 开始：{self.STAGE_NAMES[stage - 1]}", stage)
        try:
            self._process = subprocess.Popen(
                cmd, cwd=cwd or self.edtalk_dir, shell=False,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                encoding="utf-8", errors="replace", bufsize=1,
            )
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

    # ---------- 主流程（后台线程入口） ----------

    def run(self):
        """执行全链路训练。阻塞调用——请放后台线程。"""
        global training_running
        training_running = True
        self.confirm_decision = None
        self.confirm_event.clear()
        success = False
        message = ""
        try:
            err = self.preflight()
            if err:
                self._log(f"[前置校验] {err}")
                self.on_complete(False, err)
                return

            # 获取锁
            os.makedirs(os.path.dirname(self.lock_path), exist_ok=True)
            with open(self.lock_path, "w", encoding="utf-8") as f:
                json.dump({"pid": os.getpid(), "time": time.strftime("%Y-%m-%d %H:%M:%S")}, f)

            self._log(f"=== 训练开始：角色 {self.role_name}，目录 {self.video_dir}，从阶段 {self.start_stage} 开始 ===")
            t0 = time.time()

            # ---- 阶段 1：预处理编排 ----
            if self.start_stage <= 1:
                self.current_stage = 1
                self._write_progress(1, "running")
                cmd = [self.interpreter, "data_preprocess/data_preprocess_for_train/run_preprocess.py",
                       "--base_dir", self.video_dir]
                if not self._run_step(cmd, 1):
                    message = "预处理失败，详见日志"
                    self._write_progress(1, "failed")
                    return
                self._write_progress(1, "done")

            # ---- 阶段 2：底模微调 ----
            if self.start_stage <= 2:
                self.current_stage = 2
                self._write_progress(2, "running")
                cmd = [self.interpreter, "train_fine_tune.py",
                       "--datapath", self.role_name,
                       "--exp_name", self.role_name,
                       "--only_fine_tune_dec",
                       "--iter", str(self.iter_count)]
                if not self._run_step(cmd, 2):
                    message = "底模微调失败，详见日志"
                    self._write_progress(2, "failed")
                    return
                self._write_progress(2, "done")

            # ---- 阶段 3：评估 ----
            if self.start_stage <= 3:
                self.current_stage = 3
                self._write_progress(3, "running")
                eval_out = "tools/eval_results"
                cmd = [self.interpreter, "tools/eval_finetune.py",
                       "--cand-ckpt", f"ckpt_models/{self.role_name}/checkpoint/{self.iter_count:06d}.pt",
                       "--datapath", self.role_name,
                       "--out-dir", eval_out]
                if not self._run_step(cmd, 3):
                    message = "评估执行失败，详见日志"
                    self._write_progress(3, "failed")
                    return
                metrics = self._parse_eval_results(os.path.join(self.edtalk_dir, eval_out))
                verdict = self._judge_eval(metrics)
                if verdict is not None:
                    # 门槛不过：默认停止，UI 提供"仍要继续"显式确认
                    self._log(f"[评估] 未过门槛：{verdict}。等待用户确认（5 分钟内未确认将自动停止）")
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
                cmd = [self.interpreter, "train/train_audio2mouth.py",
                       "--data_path", self.role_name,
                       "--resume_ckpt", f"ckpt_models/{self.role_name}/checkpoint/{self.iter_count:06d}.pt",
                       "--audio2lip_ckpt", "ckpts/Audio2Lip.pt",
                       "--exp_name", self.role_name] + AUDIO2MOUTH_ARGS
                if not self._run_step(cmd, 4):
                    message = "口型微调失败，详见日志"
                    self._write_progress(4, "failed")
                    return
                self._write_progress(4, "done")

            elapsed = time.time() - t0
            self._log(f"=== 训练完成：产物 ckpt_models/{self.role_name}/checkpoint/（耗时 {elapsed/3600:.1f}h）===")
            message = f"训练完成（耗时 {elapsed/3600:.1f} 小时），产物：ckpt_models/{self.role_name}/checkpoint/"
            self._completed_ok = True
            success = True
            self.on_complete(True, message)
        except Exception as e:
            logger.error(traceback.format_exc())
            message = f"训练编排异常：{e}"
            self.on_complete(False, message)
        finally:
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
