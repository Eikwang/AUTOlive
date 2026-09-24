# -*- coding: UTF-8 -*-
"""
数字人-训练声音编排（P2-3）：GPT-SoVITS 声音训练全链路（v2ProPlus）

步骤清单（批准计划 P2-3/D5：五态=待执行/执行中/成功/失败/跳过，失败三要素，
单步重跑覆盖下游产物 H-2）：
  1. uvr5      可选 UVR5 人声分离（已是干声时跳过，D1 录音指引前置）
  2. slice     音频切分（tools/slice_audio.py）
  3. asr       ASR 标注（tools/asr/funasr_asr.py）
  4. prepare   文本/hubert/语义（1-get-text → 2-get-hubert-wav32k/2-get-sv → 3-get-semantic，env 接口）
  5. s2        SoVITS 训练（s2_train.py -c 生成 json，v2ProPlus）
  6. s1        GPT 训练（s1_train.py --config_file 生成 yaml）
  7. export    导出声音档案（voice_profiles 写入 + R2 兜底说明）

- GPU 准入：s2/s1 前经 gpu_policy.is_background_task_allowed（E-2）
- 子进程：runtime312-clone（Step 0 已验证可用）+ PYTHONPATH 注入（P1-2 收编）
- 结构复用 train_streamer 范式（步骤队列 + 后台线程 + 状态回调查询）
"""
import json
import os
import subprocess
import threading
import time
from typing import Callable, Dict, List, Optional

from utils.autolive_paths import AutolivePaths
from utils.gpu_policy import is_background_task_allowed
from utils.my_log import logger

# 待执行 / 执行中 / 成功 / 失败 / 跳过（D5 五态）
PENDING, RUNNING, SUCCESS, FAILED, SKIPPED = "pending", "running", "success", "failed", "skipped"


class TrainVoicePipeline:
    """声音训练全链路编排（单实例单实验，防多开由页面层持锁）。"""

    def __init__(self, config, paths: AutolivePaths):
        self.config = config
        self.paths = paths
        self.gsv_root = paths.gpt_sovits_root
        self.python = paths.gpt_sovits_python
        self.proc: Optional[subprocess.Popen] = None
        self.running_stage: Optional[str] = None
        self.stop_requested = False
        self.log_lines: List[str] = []
        self.on_stage_change: Optional[Callable] = None
        self.params: Dict = {}  # set by prepare(): dataset_dir, exp_name, epochs...

    # ---------- 状态 ----------

    def stage_states(self) -> Dict[str, str]:
        return {sid: st.get("state", PENDING) for sid, st in self._plan().items()}

    def _plan(self) -> Dict[str, dict]:
        """步骤清单（按 params 动态生成；重跑=重置该步及其全部下游，H-2）。"""
        p = self.params
        uvr5_needed = not p.get("already_dry", False)
        stages = [
            ("uvr5", "UVR5 人声分离", self._st_uvr5, None if uvr5_needed else SKIPPED),
            ("slice", "音频切分", self._st_slice, None),
            ("asr", "ASR 标注", self._st_asr, None),
            ("prepare", "文本/特征/语义提取", self._st_prepare, None),
            ("s2", "训练 SoVITS (v2ProPlus)", self._st_s2, None),
            ("s1", "训练 GPT", self._st_s1, None),
            ("export", "导出声音档案", self._st_export, None),
        ]
        return {sid: {"name": name, "fn": fn, "state": state or self._states.get(sid, PENDING)}
                for sid, name, fn, state in stages}

    _states: Dict[str, str] = {}

    def reset_from(self, stage_id: str):
        """单步重跑（H-2）：重置该步及其全部下游产物状态。"""
        ids = list(self._plan().keys())
        idx = ids.index(stage_id)
        for sid in ids[idx:]:
            self._states[sid] = PENDING

    # ---------- 启动/停止 ----------

    def prepare(self, dataset_dir: str, exp_name: str, total_epoch: int = 12,
                batch_size: int = 8, already_dry: bool = False, authorized: bool = False,
                on_stage_change: Callable = None):
        """训练前装配（页面把表单值传进来；authorized=授权勾选 D1/P1-7）。"""
        assert authorized, "未完成声音克隆授权勾选（P1-7 机制）"
        self.params = {
            "dataset_dir": os.path.abspath(dataset_dir),
            "exp_name": exp_name,
            "total_epoch": total_epoch,
            "batch_size": batch_size,
            "already_dry": already_dry,
        }
        self.on_stage_change = on_stage_change
        self._states = {}
        self.log_lines = []
        self.stop_requested = False

    def run_all(self):
        """后台线程入口：顺序执行全部步骤。"""

        def _worker():
            for sid, stage in self._plan().items():
                if self.stop_requested:
                    self._states[sid] = SKIPPED
                    self._notify()
                    continue
                if stage["state"] == SUCCESS:
                    continue  # 单步重跑后的已完成步骤跳过
                if stage["state"] == SKIPPED:
                    self._notify()
                    continue
                self._states[sid] = RUNNING
                self.running_stage = sid
                self._notify()
                ok = stage["fn"]()
                self._states[sid] = SUCCESS if ok else FAILED
                self._notify()
                if not ok:
                    logger.error(f"[train-voice] 步骤 {sid} 失败，链路停止（失败态三要素见日志/页面）")
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
                logger.error("[train-voice] 状态回调异常", exc_info=True)

    # ---------- 子进程工具 ----------

    def _run(self, cmd: list, env_extra: Optional[dict] = None, timeout: int = 7200) -> bool:
        """同步子进程：输出进日志缓冲；GPU 准入由调用方先行判定。"""
        env = self.paths.subprocess_env(env_extra or {})
        logger.info(f"[train-voice] 执行: {' '.join(cmd[:6])} ...")
        try:
            self.proc = subprocess.Popen(
                cmd, cwd=self.gsv_root, env=env,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace",
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            for line in self.proc.stdout:
                self.log_lines.append(line.rstrip())
                self.log_lines = self.log_lines[-800:]
            rc = self.proc.wait(timeout=timeout)
            return rc == 0
        except Exception as e:
            logger.error(f"[train-voice] 子进程异常: {e}")
            return False

    def _gpu_gate(self) -> bool:
        ok, reason = is_background_task_allowed(
            float(self.config.get("audio_integration", "policies", "vram_reserve_gb") or 4.0))
        if not ok:
            # D5：拒绝文案含占用来源与再试时机
            self.log_lines.append(f"[GPU 拒绝] {reason}——完成后可重试本步骤")
        return ok

    # ---------- 各步骤 ----------

    def _opt_dir(self) -> str:
        return os.path.join(self.gsv_root, "logs", self.params["exp_name"])

    def _st_uvr5(self) -> bool:
        """UVR5 无稳定 CLI：v1 跳过（录音指引卡 D1 要求提供干声），标记 SKIPPED。

        UVR5 集成挂 TODOS（批准计划 Open Questions 演进项）。
        """
        logger.info("[train-voice] UVR5 v1 跳过（录音指引要求提供干声）")
        self._states["uvr5"] = SKIPPED
        return True

    def _st_slice(self) -> bool:
        d = self.params["dataset_dir"]
        out = os.path.join(d, "sliced")
        os.makedirs(out, exist_ok=True)
        return self._run([
            self.python, "tools/slice_audio.py",
            d, out, "-34", "4000", "300", "10", "500",
        ])

    def _st_asr(self) -> bool:
        d = self.params["dataset_dir"]
        out = os.path.join(self._opt_dir(), "asr_opt.list")
        os.makedirs(self._opt_dir(), exist_ok=True)
        return self._run([
            self.python, "tools/asr/funasr_asr.py",
            "-i", os.path.join(d, "sliced"), "-o", out,
            "-f", "large", "-l", "zh", "-p", "float16",
        ])

    def _st_prepare(self) -> bool:
        """1-get-text → 2-get-hubert-wav32k + 2-get-sv → 3-get-semantic（env 接口）。"""
        opt_dir = self._opt_dir()
        inp_text = os.path.join(opt_dir, "asr_opt.list")
        base_env = {
            "inp_text": inp_text,
            "inp_wav_dir": "",  # 空则从 asr list 读 0 列路径
            "exp_name": self.params["exp_name"],
            "i_part": "0", "all_parts": "1",
            "_CUDA_VISIBLE_DEVICES": "0",
            "opt_dir": opt_dir,
            "bert_pretrained_dir": "GPT_SoVITS/pretrained_models/chinese-roberta-wwm-ext-large",
            "cnhubert_base_path": "GPT_SoVITS/pretrained_models/chinese-hubert-base",
            "is_half": "True", "version": "v2ProPlus",
        }
        if not self._run([self.python, "GPT_SoVITS/prepare_datasets/1-get-text.py"], base_env):
            return False
        if not self._run([self.python, "GPT_SoVITS/prepare_datasets/2-get-hubert-wav32k.py"], base_env):
            return False
        # v2ProPlus 需 2-get-sv（语义特征）
        sv_script = "GPT_SoVITS/prepare_datasets/2-get-sv.py"
        if os.path.isfile(os.path.join(self.gsv_root, sv_script)):
            if not self._run([self.python, sv_script], base_env):
                return False
        return self._run([self.python, "GPT_SoVITS/prepare_datasets/3-get-semantic.py"], base_env)

    def _pretrained(self, name: str) -> str:
        return os.path.join("GPT_SoVITS", "pretrained_models", name)

    def _st_s2(self) -> bool:
        if not self._gpu_gate():
            return False
        opt_dir = self._opt_dir()
        template = os.path.join(self.gsv_root, "GPT_SoVITS/configs/s2v2ProPlus.json")
        with open(template, encoding="utf-8") as f:
            data = json.load(f)
        data["train"]["batch_size"] = self.params["batch_size"]
        data["train"]["epochs"] = self.params["total_epoch"]
        data["train"]["pretrained_s2G"] = self._pretrained("s2Gv2ProPlus.pth")
        data["train"]["pretrained_s2D"] = self._pretrained("s2Dv2ProPlus.pth")
        data["train"]["if_save_latest"] = True
        data["train"]["if_save_every_weights"] = True
        data["train"]["save_every_epoch"] = 5
        data["train"]["gpu_numbers"] = "0"
        data["model"]["version"] = "v2ProPlus"
        data["data"]["exp_dir"] = data["s2_ckpt_dir"] = opt_dir
        data["save_weight_dir"] = "SoVITS_weights_v2ProPlus"
        data["name"] = self.params["exp_name"]
        data["version"] = "v2ProPlus"
        tmp = os.path.join(opt_dir, "tmp_s2.json")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        return self._run([self.python, "-s", "GPT_SoVITS/s2_train.py", "--config", tmp])

    def _st_s1(self) -> bool:
        if not self._gpu_gate():
            return False
        import yaml
        opt_dir = self._opt_dir()
        template = os.path.join(self.gsv_root, "GPT_SoVITS/configs/s1longer-v2.yaml")
        with open(template, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        data["train"]["epochs"] = self.params["total_epoch"]
        data["train"]["batch_size"] = max(1, self.params["batch_size"] // 2)
        data["train"]["save_every_n_epoch"] = 5
        data["data"]["train"]["phoneme_path"] = os.path.join(opt_dir, "2-name2text.txt")
        data["data"]["train"]["semantics_path"] = os.path.join(opt_dir, "6-name2semantic.tsv")
        data["data"]["eval"]["phoneme_path"] = data["data"]["train"]["phoneme_path"]
        data["data"]["eval"]["semantics_path"] = data["data"]["train"]["semantics_path"]
        data["pretrained_s1"] = self._pretrained("sv_pretrained_bernard_ckpt_20250213.ckpt")
        tmp = os.path.join(opt_dir, "tmp_s1.yaml")
        with open(tmp, "w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, allow_unicode=True)
        return self._run([self.python, "-s", "GPT_SoVITS/s1_train.py", "--config_file", tmp])

    def _st_export(self) -> bool:
        """导出声音档案：取最新产物模型对写入 voice_profiles（P2-4）。"""
        from utils.voice_profiles import upsert_profile
        exp = self.params["exp_name"]
        gsv_w = os.path.join(self.gsv_root, "GPT_weights_v2ProPlus")
        vits_w = os.path.join(self.gsv_root, "SoVITS_weights_v2ProPlus")
        gpt_model = max(
            (os.path.join(gsv_w, f) for f in os.listdir(gsv_w) if f.startswith(exp)),
            key=os.path.getmtime, default="") if os.path.isdir(gsv_w) else ""
        sovits_model = max(
            (os.path.join(vits_w, f) for f in os.listdir(vits_w) if f.startswith(exp)),
            key=os.path.getmtime, default="") if os.path.isdir(vits_w) else ""
        if not gpt_model or not sovits_model:
            self.log_lines.append("[导出失败] 未找到训练产物（GPT/SoVITS 权重缺失）")
            return False
        ref = ""
        sliced = os.path.join(self.params["dataset_dir"], "sliced")
        if os.path.isdir(sliced):
            wavs = [f for f in os.listdir(sliced) if f.endswith(".wav")]
            if wavs:
                ref = os.path.join(sliced, sorted(wavs)[0])
        upsert_profile({
            "name": exp,
            "note": f"训练于 {time.strftime('%Y-%m-%d %H:%M')}",
            "gpt_model": gpt_model,
            "sovits_model": sovits_model,
            "rvc_model": "", "rvc_index": "",  # RVC 侧由 P3-1 训练流补挂
            "ref_audio": ref, "prompt_text": "", "prompt_lang": "zh",
        })
        return True
