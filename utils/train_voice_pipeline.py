# -*- coding: UTF-8 -*-
"""
数字人-训练声音编排（P2-3 + GPT-SoVITS 深化整合）：六步训练全链路（v2ProPlus）

步骤清单（深化整合计划 T-1~T-4：五态=待执行/执行中/成功/失败/跳过，
失败四要素 DX2，单步重跑覆盖下游产物 H-2）：
  1. mdx      声音处理：MDXNet 三阶段分离（已是干声时跳过；AICoverGen mdx.py 进程内直调）
  2. slice    音频切分（tools/slice_audio.py，上游 webui 11 参数语义）
  3. asr      ASR 标注双模型路由（zh/yue→达摩 funasr large float32；en/ja/ko/auto→faster-whisper large-v3 float16）
  4. prepare  文本/hubert/语义（1-get-text → 2-get-hubert-wav32k/2-get-sv → 3-get-semantic，env 接口）
  5. s2       SoVITS 训练（s2_train.py -c，v2ProPlus，batch4/epoch10/lr0.4/save5/仅存最新）
  6. s1       GPT 训练（s1_train.py --config_file，batch4/epoch20/DPO/save10/仅存最新，底模 s1v3.ckpt）
  7. export   导出声音档案（voice_profiles 写入 + 仅保留最新权重对 + R2 兜底说明）

- GPU 准入：s2/s1 前经 gpu_policy.is_background_task_allowed（E-2）
- 子进程：runtime312-clone + PYTHONPATH 注入（P1-2 收编）；cwd=apps/gpt-sovits（S-1 唯一副本）
- 资产预检（T-4）：prepare() 时校验实际写入训练配置的路径（非固定文件名），缺失列清单+预取指引
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

# 用户规格默认参数（深化整合计划：SoVITS batch4/epoch10/lr0.4/save5；GPT batch4/epoch20/save10/DPO）
DEFAULTS = {
    "batch_size": 4,
    "epochs_s2": 10,
    "epochs_s1": 20,
    "text_low_lr_rate": 0.4,
    "save_every_s2": 5,
    "save_every_s1": 10,
    "if_dpo": True,
    "asr_lang": "zh",
}

# MDXNet 三阶段模型（T-1）：人声/伴奏→主唱/和声→去混响；干声=反相轨 DeReverb
# （2026-10-14 Eng shared-code 裁决：常量与三阶段实现提取至 train_process_utils，
#  GSV/RVC 共用；此处同名 re-export 保持既有 import 兼容）
from utils.train_process_utils import (  # noqa: F401,E402
    MDXNET_DIR,
    MDX_STAGES,
    cleanup_after_step as _tpu_cleanup,
    gpu_gate as _tpu_gpu_gate,
    release_local_gpu as _tpu_release_local_gpu,
    run_mdx_dry_vocals as _tpu_run_mdx,
    vram_used_gb as _tpu_vram_used_gb,
    wait_vram_release as _tpu_wait_vram_release,
)

# T-4 资产预检清单：相对 gsv_root；covers=训练配置实际引用 + ASR 模型 + MDX
REQUIRED_ASSETS = [
    ("GPT_SoVITS/pretrained_models/s1v3.ckpt", "GPT 底模 s1v3.ckpt"),
    ("GPT_SoVITS/pretrained_models/v2Pro/s2Gv2ProPlus.pth", "SoVITS-G 底模"),
    ("GPT_SoVITS/pretrained_models/v2Pro/s2Dv2ProPlus.pth", "SoVITS-D 底模"),
    ("GPT_SoVITS/pretrained_models/sv/pretrained_eres2netv2w24s4ep4.ckpt", "SV 说话人模型（2-get-sv 与推理共用）"),
    ("GPT_SoVITS/pretrained_models/chinese-roberta-wwm-ext-large", "中文 BERT"),
    ("GPT_SoVITS/pretrained_models/chinese-hubert-base", "HuBERT"),
    ("GPT_SoVITS/text/G2PWModel", "G2PW 音素模型"),
]

# ASR 双模型路由（T-2）：zh|yue→达摩；en|ja|ko|auto→Faster Whisper
ASR_ROUTE = {
    "zh": ("tools/asr/funasr_asr.py", ["-s", "large", "-l", "zh", "-p", "float32"]),
    "yue": ("tools/asr/funasr_asr.py", ["-s", "large", "-l", "yue", "-p", "float32"]),
    "en": ("tools/asr/fasterwhisper_asr.py", ["-s", "large-v3", "-l", "en", "-p", "float16"]),
    "ja": ("tools/asr/fasterwhisper_asr.py", ["-s", "large-v3", "-l", "ja", "-p", "float16"]),
    "ko": ("tools/asr/fasterwhisper_asr.py", ["-s", "large-v3", "-l", "ko", "-p", "float16"]),
    "auto": ("tools/asr/fasterwhisper_asr.py", ["-s", "large-v3", "-l", "auto", "-p", "float16"]),
}


class TrainVoicePipeline:
    """声音训练全链路编排（单实例单实验，防多开由页面层持锁）。"""

    def __init__(self, config, paths: AutolivePaths):
        self.config = config
        self.paths = paths
        self.gsv_root = paths.gpt_sovits_root
        self.python = paths.gpt_sovits_python
        self.autolive_home = paths.autolive_home
        self.proc: Optional[subprocess.Popen] = None
        self.running_stage: Optional[str] = None
        self.stop_requested = False
        self.log_lines: List[str] = []
        self.on_stage_change: Optional[Callable] = None
        self.params: Dict = {}  # set by prepare(): dataset_dir, exp_name, epochs...
        self.asset_issues: List[tuple] = []  # set by prepare()（T-4 预检）

    # ---------- 状态 ----------

    def stage_states(self) -> Dict[str, str]:
        return {sid: st.get("state", PENDING) for sid, st in self._plan().items()}

    def _plan(self) -> Dict[str, dict]:
        """步骤清单（按 params 动态生成；重跑=重置该步及其全部下游，H-2）。"""
        p = self.params
        mdx_needed = not p.get("already_dry", False)
        stages = [
            ("mdx", "声音处理 (MDXNet)", self._st_mdx, None if mdx_needed else SKIPPED),
            ("slice", "音频切分", self._st_slice, None),
            ("asr", "ASR 标注", self._st_asr, None),
            ("prepare", "文本/特征/语义提取", self._st_prepare, None),
            ("s2", "训练 SoVITS (v2ProPlus)", self._st_s2, None),
            ("s1", "训练 GPT (DPO)", self._st_s1, None),
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

    def prepare(self, dataset_dir: str, exp_name: str, total_epoch: int = None,
                batch_size: int = None, already_dry: bool = False, authorized: bool = False,
                asr_lang: str = None, epochs_s2: int = None, epochs_s1: int = None,
                text_low_lr_rate: float = None, save_every_s2: int = None,
                save_every_s1: int = None, if_dpo: bool = None,
                on_stage_change: Callable = None):
        """训练前装配（页面把表单值传进来；authorized=授权勾选 D1/P1-7）。

        参数缺省回落用户规格默认（DEFAULTS）；total_epoch 兼容旧调用=SoVITS epoch。
        预检（T-4）在此同步执行：校验实际写入训练配置的路径，缺失记 asset_issues。
        """
        assert authorized, "未完成声音克隆授权勾选（P1-7 机制）"
        d = dict(DEFAULTS)
        self.params = {
            "dataset_dir": os.path.abspath(dataset_dir),
            "exp_name": exp_name,
            "batch_size": int(batch_size if batch_size is not None else d["batch_size"]),
            "epochs_s2": int(epochs_s2 if epochs_s2 is not None
                             else (total_epoch if total_epoch is not None else d["epochs_s2"])),
            "epochs_s1": int(epochs_s1 if epochs_s1 is not None else d["epochs_s1"]),
            "text_low_lr_rate": float(text_low_lr_rate if text_low_lr_rate is not None else d["text_low_lr_rate"]),
            "save_every_s2": int(save_every_s2 if save_every_s2 is not None else d["save_every_s2"]),
            "save_every_s1": int(save_every_s1 if save_every_s1 is not None else d["save_every_s1"]),
            "if_dpo": bool(d["if_dpo"] if if_dpo is None else if_dpo),
            "asr_lang": str(asr_lang or d["asr_lang"]).lower(),
            "already_dry": already_dry,
        }
        self.on_stage_change = on_stage_change
        self._states = {}
        self.log_lines = []
        self.stop_requested = False
        self.asset_issues = self.check_assets()
        if self.asset_issues:
            self.log_lines.append("[预检] 缺失资产（T-4）：")
            for path, note, hint in self.asset_issues:
                self.log_lines.append(f"  ✗ {note} → {path}")
            self.log_lines.append("  处置：将上述文件放置到位后重试（预取机制见缺模型指引）")
            logger.warning(f"[train-voice] 资产预检失败 {len(self.asset_issues)} 项")

    def check_assets(self) -> List[tuple]:
        """T-4：校验实际将写入训练配置的路径（非固定文件名存在性）。

        返回 [(相对路径, 说明, 处置提示)]；s1 底模校验 pretrained_s1 覆写后的值
        （根治 :276 bernard 死路径类问题——预检与配置写入同源）。
        """
        issues = []
        for rel, note in REQUIRED_ASSETS:
            full = os.path.join(self.gsv_root, rel)
            if not os.path.exists(full):
                hint = "从 GPT-SoVITS 官方发布包放置，或配置 paths.gpt_sovits_root 指向含底模的副本"
                issues.append((rel, note, hint))
        for stage in MDX_STAGES:
            full = os.path.join(self.autolive_home if hasattr(self, "autolive_home") else ".",
                                MDXNET_DIR, stage["model"])
            if not os.path.exists(full):
                issues.append((os.path.join(MDXNET_DIR, stage["model"]),
                               f"MDX 分离模型 {stage['model']}", "从 AICoverGen mdxnet_models 复制或按需下载"))
        if not os.path.exists(os.path.join(self.autolive_home, MDXNET_DIR, "model_data.json")):
            issues.append((os.path.join(MDXNET_DIR, "model_data.json"), "MDX 模型参数表", "同上"))
        route = ASR_ROUTE.get(self.params.get("asr_lang", "zh"))
        if route and "funasr" in route[0]:
            if not os.path.isdir(os.path.join(self.gsv_root, "tools/asr/models")):
                issues.append(("tools/asr/models", "FunASR 本地模型目录", "首用将在线下载（hf-mirror），或预放置"))
        return issues

    def run_all(self):
        """后台线程入口：顺序执行全部步骤。"""

        def _worker():
            if self.asset_issues:
                self._states["mdx"] = FAILED
                self._notify()
                logger.error(f"[train-voice] 资产预检未通过（{len(self.asset_issues)} 项缺失），链路未启动——见日志清单")
                self.running_stage = None
                return
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

    # ---------- 子进程工具（2026-10-14 Eng 裁决：实现提取至 train_process_utils，薄委托） ----------

    def _vram_used_gb(self) -> Optional[float]:
        return _tpu_vram_used_gb()

    def _wait_vram_release(self, used_before_gb: Optional[float], timeout: int = 60) -> None:
        _tpu_wait_vram_release(used_before_gb, timeout=timeout, prefix="[train-voice]")

    def _release_local_gpu(self) -> None:
        _tpu_release_local_gpu()

    def _cleanup_after_step(self, proc: Optional[subprocess.Popen], used_before_gb: Optional[float]):
        """步间资源清理（2026-10-14 用户指令）：进程树终结+显存回落确认（共享实现）。"""
        _tpu_cleanup(proc, used_before_gb, prefix="[train-voice]")

    def _run(self, cmd: list, env_extra: Optional[dict] = None, timeout: int = 7200) -> bool:
        """同步子进程：输出进日志缓冲；GPU 准入由调用方先行判定；
        结束后执行步间清理（进程树终结+显存回落确认）。"""
        env = self.paths.subprocess_env(env_extra or {})
        logger.info(f"[train-voice] 执行: {' '.join(cmd[:6])} ...")
        used_before = self._vram_used_gb()
        proc = None
        try:
            proc = subprocess.Popen(
                cmd, cwd=self.gsv_root, env=env,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace",
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            self.proc = proc
            for line in proc.stdout:
                self.log_lines.append(line.rstrip())
                self.log_lines = self.log_lines[-800:]
            rc = proc.wait(timeout=timeout)
            return rc == 0
        except Exception as e:
            logger.error(f"[train-voice] 子进程异常: {e}")
            return False
        finally:
            self._cleanup_after_step(proc, used_before)
            self.proc = None

    def _gpu_gate(self) -> bool:
        return _tpu_gpu_gate(self.config, self.log_lines, prefix="[train-voice]")

    # ---------- 各步骤 ----------

    def _opt_dir(self) -> str:
        return os.path.join(self.gsv_root, "logs", self.params["exp_name"])

    def _st_mdx(self) -> bool:
        """T-1：MDXNet 三阶段分离（共享实现 run_mdx_dry_vocals，2026-10-14 提取）。
        已是干声时本步 SKIPPED（_plan 控制）。"""
        outputs = _tpu_run_mdx(self.params["dataset_dir"], self.log_lines,
                               self.autolive_home, prefix="[MDX]")
        if outputs is None:
            return False
        self.mdx_outputs = outputs
        return True

    def _st_slice(self) -> bool:
        d = self.params["dataset_dir"]
        out = os.path.join(d, "sliced")
        os.makedirs(out, exist_ok=True)
        # 自有切分脚本（2026-10-14 用户裁决）：上游 slicer2 静音切分+8s 硬上限
        # 一道流程到位——不做"切完再扫再切"的第二道；上游 11 argv+max_sec=8
        argv_tail = ["-34", "4000", "300", "10", "500", "0.9", "0.25", "0", "1", "8"]
        script = os.path.join(self.autolive_home, "utils", "smart_slice_audio.py")
        # MDX 步产出干声时切分对象=mdx_vocals 目录（目录输入语义）
        src_dir = (os.path.join(d, "mdx_vocals")
                   if getattr(self, "mdx_outputs", None) else d)
        ok = self._run([self.python, script, src_dir, out] + argv_tail)
        # 切分脚本对单文件失败会 except 吞错仍 rc=0（E2E 实测）——产物计数+超上限断言兜底
        produced, over = self._slice_stats(out)
        if ok and produced == 0:
            self.log_lines.append("[切分失败] rc=0 但零产物（上游 except 吞错）——"
                                  "cause: ffmpeg/load_audio 失败见上方日志；fix: 检查 PATH 中 ffmpeg 与音频可解码性")
            return False
        if over:
            self.log_lines.append(f"[切分失败] {over} 个片段仍 >8s（max_sec 上限未生效）——"
                                  "cause: smart_slice_audio 细分异常；fix: 查看上方 ->fail-> 日志")
            return False
        self.log_lines.append(f"[切分] 产出 {produced} 个片段（全部 <=8s）")
        return ok

    def _slice_stats(self, sliced_dir: str) -> tuple:
        """产物计数+超上限计数（防 OOM 断言：全部片段须 <=8s）。"""
        import librosa
        produced, over = 0, 0
        for fn in os.listdir(sliced_dir):
            if not fn.endswith(".wav"):
                continue
            produced += 1
            try:
                if librosa.get_duration(path=os.path.join(sliced_dir, fn)) > 8.0:
                    over += 1
            except Exception:
                pass
        return produced, over

    def _st_asr(self) -> bool:
        """T-2：ASR 双模型路由。zh|yue→达摩 funasr large float32（precision 上游未接入=天然行为，
        参数位对齐 webui 语义）；en|ja|ko|auto→Faster Whisper large-v3 float16（HF 镜像经 env 注入）。

        上游 -o 语义=输出目录（脚本内拼 <目录>/<输入目录名>.list，funasr_asr.py:89-92），
        产物文件名=输入目录名（"sliced"）——本步后扫描 *.list 供 prepare 步消费。
        """
        d = self.params["dataset_dir"]
        out_dir = os.path.join(self._opt_dir(), "asr_opt")
        os.makedirs(out_dir, exist_ok=True)
        lang = self.params.get("asr_lang", "zh")
        route = ASR_ROUTE.get(lang)
        if not route:
            self.log_lines.append(f"[ASR 失败] 未知语言 {lang}（可选 {sorted(ASR_ROUTE)}）")
            return False
        script, lang_args = route
        ok = self._run([self.python, script,
                        "-i", os.path.join(d, "sliced"), "-o", out_dir] + lang_args)
        lists = [f for f in os.listdir(out_dir) if f.endswith(".list")] if os.path.isdir(out_dir) else []
        if ok and not lists:
            self.log_lines.append("[ASR 失败] rc=0 但零 .list 产物（上游 except 吞错）——见上方日志")
            return False
        self.asr_list_path = os.path.join(out_dir, sorted(lists)[0])
        lines = sum(1 for _ in open(self.asr_list_path, encoding="utf-8"))
        self.log_lines.append(f"[ASR] 标注完成：{self.asr_list_path}（{lines} 行）")
        return ok

    def _st_prepare(self) -> bool:
        """1-get-text → 2-get-hubert-wav32k + 2-get-sv → 3-get-semantic（env 接口）。"""
        opt_dir = self._opt_dir()
        # asr 产物：优先本步发现的 asr_list_path；断点续跑时扫描 asr_opt/ 目录
        inp_text = getattr(self, "asr_list_path", None)
        if not inp_text:
            asr_dir = os.path.join(opt_dir, "asr_opt")
            lists = [f for f in os.listdir(asr_dir) if f.endswith(".list")] if os.path.isdir(asr_dir) else []
            inp_text = os.path.join(asr_dir, sorted(lists)[0]) if lists else ""
        if not inp_text or not os.path.isfile(inp_text):
            self.log_lines.append(f"[prepare 失败] ASR 标注文件缺失（asr_opt/ 无 .list）——"
                                  "cause: asr 步未成功执行；fix: 从 asr 步重跑")
            return False
        base_env = {
            "inp_text": inp_text,
            "inp_wav_dir": "",  # 空则从 asr list 读 0 列路径
            "exp_name": self.params["exp_name"],
            "i_part": "0", "all_parts": "1",
            "_CUDA_VISIBLE_DEVICES": "0",
            "opt_dir": opt_dir,
            "bert_pretrained_dir": "GPT_SoVITS/pretrained_models/chinese-roberta-wwm-ext-large",
            # env 键名对齐上游 webui open1b：cnhubert_base_dir（非 _path）+ Pro 系 sv_path
            "cnhubert_base_dir": "GPT_SoVITS/pretrained_models/chinese-hubert-base",
            "sv_path": "GPT_SoVITS/pretrained_models/sv/pretrained_eres2netv2w24s4ep4.ckpt",
            # 3-get-semantic 的 env（上游 open1c）：语义提取用底模+对应版本 config
            "pretrained_s2G": "GPT_SoVITS/pretrained_models/v2Pro/s2Gv2ProPlus.pth",
            "s2config_path": "GPT_SoVITS/configs/s2v2ProPlus.json",
            "is_half": "True", "version": "v2ProPlus",
        }
        if not self._run([self.python, "GPT_SoVITS/prepare_datasets/1-get-text.py"], base_env):
            return False
        self._merge_parts(opt_dir, "2-name2text-%s.txt", "2-name2text.txt", header=None)
        if not self._run([self.python, "GPT_SoVITS/prepare_datasets/2-get-hubert-wav32k.py"], base_env):
            return False
        # v2ProPlus 需 2-get-sv（语义特征）
        sv_script = "GPT_SoVITS/prepare_datasets/2-get-sv.py"
        if os.path.isfile(os.path.join(self.gsv_root, sv_script)):
            if not self._run([self.python, sv_script], base_env):
                return False
        ok = self._run([self.python, "GPT_SoVITS/prepare_datasets/3-get-semantic.py"], base_env)
        if ok:
            # semantic 合并含表头（上游 webui.py:1225 opt=["item_name\tsemantic_audio"]）
            self._merge_parts(opt_dir, "6-name2semantic-%s.tsv", "6-name2semantic.tsv",
                              header="item_name\tsemantic_audio")
        return ok

    def _merge_parts(self, opt_dir: str, part_pattern: str, merged_name: str,
                     header: Optional[str] = None) -> None:
        """复刻上游 webui 的分片合并（webui.py:1117-1125/:1225-1232）：分片按行合并
        写正式文件并删除分片。单机单分片（i_part=0）时同样必要——上游脚本只写分片名。"""
        merged_path = os.path.join(opt_dir, merged_name)
        lines = [header] if header else []
        i = 0
        while True:
            part = os.path.join(opt_dir, part_pattern % i)
            if not os.path.exists(part):
                break
            with open(part, "r", encoding="utf8") as f:
                lines += f.read().strip("\n").split("\n")
            os.remove(part)
            i += 1
        with open(merged_path, "w", encoding="utf8") as f:
            f.write("\n".join(ln for ln in lines if ln is not None) + "\n")
        logger.info(f"[train-voice] 分片合并 {merged_name}（{i} 片，{len(lines)} 行）")

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
        data["train"]["epochs"] = self.params["epochs_s2"]
        data["train"]["text_low_lr_rate"] = self.params["text_low_lr_rate"]
        data["train"]["pretrained_s2G"] = self._pretrained("v2Pro/s2Gv2ProPlus.pth")
        data["train"]["pretrained_s2D"] = self._pretrained("v2Pro/s2Dv2ProPlus.pth")
        data["train"]["if_save_latest"] = True   # 固定文件名 G_233333333333.pth 覆盖=仅存最新中间 ckpt
        data["train"]["if_save_every_weights"] = True
        data["train"]["save_every_epoch"] = self.params["save_every_s2"]
        data["train"]["gpu_numbers"] = "0"
        data["model"]["version"] = "v2ProPlus"
        data["data"]["exp_dir"] = data["s2_ckpt_dir"] = opt_dir
        data["save_weight_dir"] = "SoVITS_weights_v2ProPlus"
        data["name"] = self.params["exp_name"]
        data["version"] = "v2ProPlus"
        tmp = os.path.join(opt_dir, "tmp_s2.json")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        # 上游 my_save（中文路径规避补丁）要求 checkpoint 目标目录预先存在——
        # webui 时代由上游保证，编排层显式补建（目录名=logs_s2_{hps.model.version}）
        os.makedirs(os.path.join(opt_dir, "logs_s2_v2ProPlus"), exist_ok=True)
        # savee() fp16 导出目录（my_save2 直写，同样需预建）
        os.makedirs(os.path.join(self.gsv_root, "SoVITS_weights_v2ProPlus"), exist_ok=True)
        return self._run([self.python, "-s", "GPT_SoVITS/s2_train.py", "--config", tmp])

    def _st_s1(self) -> bool:
        if not self._gpu_gate():
            return False
        import yaml
        opt_dir = self._opt_dir()
        template = os.path.join(self.gsv_root, "GPT_SoVITS/configs/s1longer-v2.yaml")
        with open(template, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        data["train"]["epochs"] = self.params["epochs_s1"]
        # T-3 修复：UI 值直写（移除旧 :270 无条件 //2——DPO 由上游 data_module 自动减半，
        # 双重减半会让实际 batch=UI/4）；if_dpo=1 时上游 batch_size//2 生效，日志明示
        data["train"]["batch_size"] = self.params["batch_size"]
        if self.params["if_dpo"]:
            self.log_lines.append(f"[s1] DPO 开启：上游自动减半，实际 batch={max(1, self.params['batch_size'] // 2)}")
        data["train"]["if_dpo"] = 1 if self.params["if_dpo"] else 0
        data["train"]["save_every_n_epoch"] = self.params["save_every_s1"]
        data["train"]["if_save_latest"] = True
        data["train"]["if_save_every_weights"] = True
        # s1longer-v2.yaml 的 data 节为平铺结构（无 train/eval 子键）——路径键全部顶层
        # （对齐上游 webui.py:630-640 与 data_module 的读取约定）
        data["train_semantic_path"] = os.path.join(opt_dir, "6-name2semantic.tsv")
        data["train_phoneme_path"] = os.path.join(opt_dir, "2-name2text.txt")
        data["output_dir"] = os.path.join(opt_dir, "logs_s1_v2ProPlus")
        data["train"]["exp_name"] = self.params["exp_name"]
        data["train"]["half_weights_save_dir"] = "GPT_weights_v2ProPlus"
        # T-3 关键修复：s1v3.ckpt（根治 :276 sv_pretrained_bernard 死路径——文件不存在，
        # t2s_lightning_module torch.load 无 try 包裹，训练启动即 FileNotFoundError）
        data["pretrained_s1"] = self._pretrained("s1v3.ckpt")
        tmp = os.path.join(opt_dir, "tmp_s1.yaml")
        with open(tmp, "w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, allow_unicode=True)
        # s1 的 output_dir=logs_s1_v2ProPlus（Lightning ckpt 目录，同上显式补建）
        os.makedirs(os.path.join(opt_dir, "logs_s1_v2ProPlus"), exist_ok=True)
        # savee() fp16 导出目录（my_save2 直写，同样需预建）
        os.makedirs(os.path.join(self.gsv_root, "GPT_weights_v2ProPlus"), exist_ok=True)
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
        # T-3"仅保存最新"（编排层 fp16 清理）：删除该实验其余历史权重，仅留已选用对
        removed = 0
        for weight_dir, keep in ((gsv_w, gpt_model), (vits_w, sovits_model)):
            if not os.path.isdir(weight_dir):
                continue
            for fn in os.listdir(weight_dir):
                if not fn.startswith(exp):
                    continue
                full = os.path.join(weight_dir, fn)
                if os.path.abspath(full) == os.path.abspath(keep):
                    continue
                try:
                    os.remove(full)
                    removed += 1
                except OSError as e:
                    logger.warning(f"[train-voice] 权重清理失败 {fn}: {e}")
        if removed:
            self.log_lines.append(f"[导出] 已清理历史权重 {removed} 个（仅保存最新，T-3）")
        return True
