# -*- coding: utf-8 -*-
"""
RVC 声音训练流水线（P3-1）：数据集 → 预处理 → f0 → 特征 → 训练 → 迁移 models/rvc/

全部 4 步均为 CLI 子进程（infer-web.py 同款命令构造，版本锁定 v2）：
  1. preprocess.py  <trainset_dir> <sr> <n_p> <logs/exp> <sample_rate> <cpu>
  2. extract_f0_rmvpe.py <logs/exp> <n_p>  （f0 提取）
  3. extract_feature_print.py <device> <n_p> 0 0 <logs/exp> <cpu> feature768
  4. train.py -e <exp> -sr v2 -f0 1 -bs <bs> -g 0 -te <epochs> -se <save_every>
              -pg s2Gv2 -pd s2Dv2 -l 1 -c 0 -sw 1 -v v2

- 与 GPT-SoVITS 训练共用 train_voice.py 页面（P3-1：并入），tab 切换管线
- GPU 准入：每步前 gpu_policy（E-2）
- 产物迁移：logs/<exp>/G_*.pth + added_*.index → models/rvc/（P1-4 共享目录）
- 结构与 TrainVoicePipeline 同款五态（D5）
"""
import glob
import os
import shutil
import subprocess
import threading
import time
from typing import Callable, Dict, List, Optional

from utils.autolive_paths import AutolivePaths
from utils.gpu_policy import is_background_task_allowed
from utils.my_log import logger

PENDING, RUNNING, SUCCESS, FAILED, SKIPPED = "pending", "running", "success", "failed", "skipped"


class TrainRvcPipeline:
    """RVC 声音训练流水线（v2，rmvpe f0，单卡）。"""

    def __init__(self, config, paths: AutolivePaths):
        self.config = config
        self.paths = paths
        self.rvc_root = config.get("rvc_vc", "rvc_root") or r"D:\AI\RVC20240604Nvidia"
        self.python = os.path.join(self.rvc_root, "runtime", "python.exe")
        self.proc: Optional[subprocess.Popen] = None
        self.running_stage: Optional[str] = None
        self.stop_requested = False
        self.log_lines: List[str] = []
        self.on_stage_change: Optional[Callable] = None
        self.params: Dict = {}
        self._states: Dict[str, str] = {}

    def stage_states(self) -> Dict[str, str]:
        return {sid: self._states.get(sid, PENDING) for sid, _, _ in self.stages}

    def reset_from(self, stage_id: str):
        ids = [sid for sid, _, _ in self.stages]
        for sid in ids[ids.index(stage_id):]:
            self._states[sid] = PENDING

    @property
    def stages(self) -> List:
        p = self.params
        return [
            ("preprocess", "RVC 预处理", self._st_preprocess),
            ("f0", "F0 提取 (rmvpe)", self._st_f0),
            ("feature", "特征提取 (768)", self._st_feature),
            ("train", "RVC 训练", self._st_train),
            ("migrate", "迁移到 models/rvc/", self._st_migrate),
        ]

    def prepare(self, dataset_dir: str, exp_name: str, total_epoch: int = 20,
                batch_size: int = 8, sample_rate: str = "40k",
                on_stage_change: Callable = None):
        self.params = {
            "dataset_dir": os.path.abspath(dataset_dir),
            "exp_name": exp_name,
            "total_epoch": total_epoch,
            "batch_size": batch_size,
            "sample_rate": sample_rate,
        }
        self.on_stage_change = on_stage_change
        self._states = {}
        self.log_lines = []
        self.stop_requested = False

    def run_all(self):
        def _worker():
            for sid, name, fn in self.stages:
                if self.stop_requested:
                    self._states[sid] = SKIPPED
                    self._notify()
                    continue
                if self._states.get(sid) == SUCCESS:
                    continue
                self._states[sid] = RUNNING
                self.running_stage = sid
                self._notify()
                ok = fn()
                self._states[sid] = SUCCESS if ok else FAILED
                self._notify()
                if not ok:
                    logger.error(f"[rvc-train] 步骤 {sid} 失败，链路停止")
                    break
            self.running_stage = None

        threading.Thread(target=_worker, daemon=True).start()

    def stop(self):
        self.stop_requested = True
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=5)
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass

    def _notify(self):
        if self.on_stage_change:
            try:
                self.on_stage_change(self.stage_states())
            except Exception:
                logger.error("[rvc-train] 状态回调异常", exc_info=True)

    def _log_dir(self) -> str:
        return os.path.join(self.rvc_root, "logs", self.params["exp_name"])

    def _run(self, cmd: list, timeout: int = 14400) -> bool:
        ok, reason = is_background_task_allowed(float(
            self.config.get("audio_integration", "policies", "vram_reserve_gb") or 4.0))
        if not ok:
            self.log_lines.append(f"[GPU 拒绝] {reason}——完成后可重试")
            return False
        env = os.environ.copy()
        env["PYTHONPATH"] = self.rvc_root  # 嵌入式 ._pth：显式注入 RVC 根
        logger.info(f"[rvc-train] 执行: {' '.join(cmd[:4])} ...")
        try:
            self.proc = subprocess.Popen(
                cmd, cwd=self.rvc_root, env=env,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace",
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            for line in self.proc.stdout:
                self.log_lines.append(line.rstrip())
                self.log_lines = self.log_lines[-800:]
            return self.proc.wait(timeout=timeout) == 0
        except Exception as e:
            logger.error(f"[rvc-train] 子进程异常: {e}")
            return False

    def _st_preprocess(self) -> bool:
        log_dir = self._log_dir()
        os.makedirs(log_dir, exist_ok=True)
        # args: <trainset_dir> <sr=40k> <n_p=8线程> <log_dir> <sample_rate=40000> <cpu=8.0>
        return self._run([
            self.python, "infer/modules/train/preprocess.py",
            self.params["dataset_dir"], "40k", "8", log_dir, "40000", "8.0",
        ])

    def _st_f0(self) -> bool:
        # rmvpe f0 提取（-m rmvpe 指定方法；infer-web 同款）
        return self._run([
            self.python, "infer/modules/train/extract_f0_rmvpe.py",
            self._log_dir(), "8",
        ])

    def _st_feature(self) -> bool:
        return self._run([
            self.python, "infer/modules/train/extract_feature_print.py",
            "cuda:0", "8", "0", "0", self._log_dir(), "8.0", "feature768",
        ])

    def _st_train(self) -> bool:
        p = self.params
        pg = os.path.join(self.rvc_root, "assets", "pretrained_v2", "f0G40k.pth")
        pd = os.path.join(self.rvc_root, "assets", "pretrained_v2", "f0D40k.pth")
        return self._run([
            self.python, "infer/modules/train/train.py",
            "-e", p["exp_name"], "-sr", "v2", "-f0", "1",
            "-bs", str(p["batch_size"]), "-g", "0",
            "-te", str(p["total_epoch"]), "-se", "5",
            "-pg", pg, "-pd", pd,
            "-l", "1", "-c", "0", "-sw", "1", "-v", "v2",
        ])

    def _st_migrate(self) -> bool:
        """产物迁移（P3-1 验收）：logs/<exp>/G_*.pth + added_*.index → models/rvc/。"""
        exp = self.params["exp_name"]
        log_dir = self._log_dir()
        dest = os.path.join(
            self.config.get("paths", "autolive_home")
            or os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "models", "rvc")
        os.makedirs(dest, exist_ok=True)
        g_files = sorted(glob.glob(os.path.join(log_dir, f"G_*_{exp}.pth")),
                         key=os.path.getmtime)
        i_files = sorted(glob.glob(os.path.join(log_dir, f"added_*_{exp}.index")),
                         key=os.path.getmtime)
        if not g_files or not i_files:
            self.log_lines.append("[迁移失败] 未找到训练产物（G_*.pth / added_*.index）")
            return False
        shutil.copy2(g_files[-1], os.path.join(dest, f"{exp}.pth"))
        shutil.copy2(i_files[-1], os.path.join(dest, os.path.basename(i_files[-1])))
        # 挂接声音档案（P2-4 三端选用）
        try:
            from utils.voice_profiles import upsert_profile, load_profiles
            profiles = load_profiles()
            pid = exp if exp in profiles else upsert_profile({"name": exp})
            prof = get_profile(pid) if "get_profile" in dir() else None
            # 简化：直接更新档案 RVC 字段
            from utils.voice_profiles import _atomic_update
            def _upd(data):
                vp = data.setdefault("voice_profiles", {})
                prof = vp.setdefault(pid, {"name": exp, "note": "RVC 训练产物",
                                           "gpt_model": "", "sovits_model": "",
                                           "ref_audio": "", "prompt_text": "",
                                           "prompt_lang": "zh"})
                prof["rvc_model"] = os.path.join(dest, f"{exp}.pth")
                prof["rvc_index"] = os.path.join(dest, os.path.basename(i_files[-1]))
            _atomic_update(os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "config.json"), _upd)
        except Exception:
            logger.error("[rvc-train] 档案挂接失败（模型已迁移）", exc_info=True)
        return True


def get_profile(pid):
    from utils.voice_profiles import get_profile as _get
    return _get(pid)
