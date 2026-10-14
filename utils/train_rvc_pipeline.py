# -*- coding: utf-8 -*-
"""RVC 训练管线（/autoplan 2026-10-14 批准 T 系列：六步完整化，对齐 GSV 深化模板）。

六步：mdx（复用共享 MDX 分离）→ preprocess（处理数据）→ extract（f0+feature）
→ train（训练模型）→ index（特征索引）→ export（导出声音档案）。

工程要点（勘察 14 条隐式契约 + spike 实测补充，全部 file:line 出处见
specs/integration/RVC深化整合计划.md §2.1）：
- 子进程 cwd=RVC 副本根（相对路径依赖），python=runtime312（EDTalk）
- logs/{exp}/ 目录预建（spike 实测：preprocess.py:26 以 "a+" 打开日志文件）
- config.json 特判：v2+40k 共用 configs/v1/40k.json 蓝本（上游 infer-web.py:575-578）
- filelist：四目录文件名交集 + 路径 \\\\ 转义 + spk_id 行尾 + 2 条 mute 行 + shuffle
- 完成信号 = train.py 退出码 2333333（os._exit 正常完成）
- 导出 = 终态无后缀 {exp}.pth（train.py:636-650；e8/e16 带后缀是中间点，勿选）
- 步间资源清理/GPU 门/MDX 分离 = utils.train_process_utils 共享（Eng 裁决单一修复点）
"""
import json
import os
import random
import re
import shutil
import subprocess
import threading
import time
from typing import Callable, Dict, List, Optional

from utils.autolive_paths import AutolivePaths
from utils.my_log import logger
from utils.train_process_utils import (
    cleanup_after_step,
    gpu_gate,
    run_mdx_dry_vocals,
    vram_used_gb,
)

PENDING = "pending"
RUNNING = "running"
SUCCESS = "success"
FAILED = "failed"
SKIPPED = "skipped"

# 用户规格固化（/autoplan §3 表，TST-1a 断言对象；save_every=8 终门 uc2a 确认）
DEFAULTS = {
    "batch_size": 8,
    "epochs": 20,
    "save_every": 8,
    "sample_rate": "40k",
    "version": "v2",
    "if_f0": True,
    "n_p": 10,
    "f0method": "rmvpe_gpu",
    "spk_id": 0,
    "per": 3.7,
}

REQUIRED_ASSETS = [
    ("assets/hubert/hubert_base.pt", "HuBERT 特征提取模型"),
    ("assets/rmvpe/rmvpe.pt", "RMVPE 音高提取模型"),
    ("assets/pretrained_v2/f0G40k.pth", "v2 40k 生成器底模（缺失=train.py 空串随机初始化陷阱）"),
    ("assets/pretrained_v2/f0D40k.pth", "v2 40k 判别器底模"),
    ("configs/v1/40k.json", "v2+40k 训练超参模板（上游特判共用 v1）"),
    ("logs/mute/0_gt_wavs", "预置静音训练集（filelist mute 行依赖）"),
]
ENV_KEYS = ("weight_root", "index_root", "outside_index_root")  # .env 三键（T-7 预检）

# fork 基准 tripwire（S-1/复核发现 5）：14 条契约涉及的核心 fork 文件哈希清单。
# 基线由 S-1 时点生成（apps/rvc/.autolive_fork_baseline.json），check_assets 比对，
# 不一致告警"复刻面基准已漂移"。
FORK_BASELINE_FILES = [
    "infer/modules/train/preprocess.py",
    "infer/modules/train/extract/extract_f0_rmvpe.py",
    "infer/modules/train/extract_feature_print.py",
    "infer/modules/train/train.py",
    "configs/v1/40k.json",
    "infer/lib/rtrvc.py",
]


class TrainRvcPipeline:
    """RVC 训练全链路编排（单实例单实验，防多开由页面层持锁——GSV 同款）。"""

    def __init__(self, config, paths: AutolivePaths):
        self.config = config
        self.paths = paths
        self.rvc_root = getattr(paths, "rvc_root", None) or os.path.join(
            getattr(paths, "autolive_home", "") or os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "apps", "rvc")
        self.python = getattr(paths, "rvc_python", None) or r"D:\AI\EDTalk\runtime312\python.exe"
        self.autolive_home = getattr(paths, "autolive_home", "") or os.path.dirname(
            os.path.dirname(os.path.abspath(__file__)))
        self.proc: Optional[subprocess.Popen] = None
        self.running_stage: Optional[str] = None
        self.stop_requested = False
        self.log_lines: List[str] = []
        self.on_stage_change: Optional[Callable] = None
        self.params: Dict = {}
        self.asset_issues: List[tuple] = []
        self._states: Dict[str, str] = {}

    # ---------- 状态（GSV 同款契约） ----------

    def stage_states(self) -> Dict[str, str]:
        return {sid: st.get("state", PENDING) for sid, st in self._plan().items()}

    def _plan(self) -> Dict[str, dict]:
        p = self.params
        mdx_needed = not p.get("already_dry", False)
        stages = [
            ("mdx", "声音处理 (MDXNet)", self._st_mdx, None if mdx_needed else SKIPPED),
            ("preprocess", "处理数据", self._st_preprocess, None),
            ("extract", "特征提取 (f0+HuBERT)", self._st_extract, None),
            ("train", "训练模型", self._st_train, None),
            ("index", "训练特征索引", self._st_index, None),
            ("export", "导出声音档案", self._st_export, None),
        ]
        return {sid: {"name": name, "fn": fn, "state": state or self._states.get(sid, PENDING)}
                for sid, name, fn, state in stages}

    def reset_from(self, stage_id: str):
        ids = list(self._plan().keys())
        for sid in ids[ids.index(stage_id):]:
            self._states[sid] = PENDING

    # ---------- 启动/停止 ----------

    def prepare(self, dataset_dir: str, exp_name: str, batch_size: int = None,
                total_epoch: int = None, save_every: int = None, already_dry: bool = False,
                authorized: bool = False, on_stage_change: Callable = None):
        assert authorized, "未完成声音克隆授权勾选（P1-7 机制）"
        d = dict(DEFAULTS)
        # 全量合并 DEFAULTS 再覆盖表单值——n_p/per/spk_id 等规格键步骤内直读
        #（E2E 实测修复：缺键 KeyError，TST-1a 盲区由 TST-2a 抓到）
        self.params = {
            **d,
            "dataset_dir": os.path.abspath(dataset_dir),
            "exp_name": exp_name,
            "batch_size": int(batch_size if batch_size is not None else d["batch_size"]),
            "epochs": int(total_epoch if total_epoch is not None else d["epochs"]),
            "save_every": int(save_every if save_every is not None else d["save_every"]),
            "already_dry": already_dry,
        }
        self.on_stage_change = on_stage_change
        self._states = {}
        self.log_lines = []
        self.stop_requested = False
        self.asset_issues = self.check_assets()
        if self.asset_issues:
            self.log_lines.append("[预检] 缺失资产（T-7）：")
            for path, note, hint in self.asset_issues:
                self.log_lines.append(f"  ✗ {note} → {path}")
            self.log_lines.append("  处置：将上述文件放置到位后重试")
            logger.warning(f"[train-rvc] 资产预检失败 {len(self.asset_issues)} 项")

    def check_assets(self) -> List[tuple]:
        """T-7 资产预检：底模/模板/mute/.env 三键/rvc_python 存在性 + torch 指纹
        + fork 哈希 tripwire（复核发现 5）。返回 [(路径, 说明, 处置提示)]。"""
        issues = []
        for rel, note in REQUIRED_ASSETS:
            if not os.path.exists(os.path.join(self.rvc_root, rel)):
                issues.append((rel, note, "从 RVC fork 放置或检查 paths.rvc_root 指向"))
        env_path = os.path.join(self.rvc_root, ".env")
        try:
            env_body = open(env_path, encoding="utf-8").read() if os.path.isfile(env_path) else ""
        except Exception:
            env_body = ""
        for key in ENV_KEYS:
            if f"{key}" not in env_body:
                issues.append((f".env:{key}", f".env 缺 {key} 键（上游编排依赖）",
                               "从 RVC fork 根复制 .env"))
        if not (self.python and os.path.isfile(self.python)):
            issues.append((self.python, "rvc_python 解释器不存在", "检查 paths.rvc_python"))
        self._check_fingerprint(issues)
        return issues

    def _check_fingerprint(self, issues: List[tuple]):
        """轻量指纹（native 发现 2.3）：torch 版本 + fork 契约文件哈希比对。"""
        try:
            out = subprocess.run(
                [self.python, "-c", "import torch;print(torch.__version__)"],
                capture_output=True, text=True, timeout=30,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            ver = (out.stdout or "").strip()
            if ver:
                self.log_lines.append(f"[指纹] rvc_python torch={ver}")
        except Exception:
            pass  # 指纹探测失败不阻塞（存在性已预检）
        baseline_path = os.path.join(self.rvc_root, ".autolive_fork_baseline.json")
        try:
            baseline = json.load(open(baseline_path, encoding="utf-8"))
        except Exception:
            return  # 无基线（首次）——export 步落盘基线
        for rel, old_sha in baseline.get("files", {}).items():
            full = os.path.join(self.rvc_root, rel)
            if not os.path.isfile(full):
                continue
            import hashlib
            new_sha = hashlib.sha256(open(full, "rb").read()).hexdigest()
            if new_sha != old_sha:
                issues.append((rel, "复刻面基准已漂移（fork 文件变更）",
                               "核对 14 条隐式契约是否失效（计划 §2.1），必要时重估"))

    # ---------- 运行控制（GSV 同款） ----------

    def run_all(self):
        def _worker():
            if self.asset_issues:
                self._states["mdx"] = FAILED
                self._notify()
                return
            for sid, st in self._plan().items():
                if self.stop_requested:
                    return
                state = st["state"]
                if state == SKIPPED or self._states.get(sid) == SUCCESS:
                    continue
                self._states[sid] = RUNNING
                self.running_stage = sid
                self._notify()
                self.log_lines.append(f"▶ {st['name']}")
                try:
                    ok = st["fn"]()
                except Exception:
                    logger.error(f"[train-rvc] 步骤 {sid} 异常", exc_info=True)
                    ok = False
                self._states[sid] = SUCCESS if ok else FAILED
                self.running_stage = None
                self.log_lines.append(f"{'✔' if ok else '✖'} {st['name']}")
                self._notify()
                if not ok:
                    return
            # 全链完成 notify（delight 1：新声音已就绪）
            self.log_lines.append("[完成] 新声音已就绪，可在变声页切换（AI对话-变声）")

        threading.Thread(target=_worker, name="train-rvc", daemon=True).start()

    def stop(self):
        self.stop_requested = True
        proc = self.proc
        if proc is not None and proc.poll() is None:
            try:
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                               capture_output=True, timeout=15,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            except Exception:
                pass

    def _notify(self):
        if self.on_stage_change:
            try:
                self.on_stage_change(self.stage_states())
            except Exception:
                logger.error("[train-rvc] 状态回调异常", exc_info=True)

    # ---------- 子进程（RVC 版 _run：完成码集合+步间清理共享） ----------

    def _run(self, cmd: list, timeout: int = 7200, rc_ok=(0,)) -> bool:
        """同步子进程：cwd=RVC 副本根（相对路径契约 9）、python=rvc_python；
        结束后步间清理（taskkill /T+显存回落，共享实现）。"""
        env = os.environ.copy()
        env.setdefault("OPENBLAS_NUM_THREADS", "1")  # 上游 .env 约定
        env["PYTHONUTF8"] = "1"
        logger.info(f"[train-rvc] 执行: {' '.join(str(c) for c in cmd[:6])} ...")
        used_before = vram_used_gb()
        proc = None
        try:
            proc = subprocess.Popen(
                cmd, cwd=self.rvc_root, env=env,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace",
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            self.proc = proc
            for line in proc.stdout:
                self.log_lines.append(line.rstrip())
                self.log_lines = self.log_lines[-800:]
            rc = proc.wait(timeout=timeout)
            return rc in rc_ok
        except Exception as e:
            logger.error(f"[train-rvc] 子进程异常: {e}")
            return False
        finally:
            cleanup_after_step(proc, used_before, prefix="[train-rvc]")
            self.proc = None

    def _exp_dir(self) -> str:
        return os.path.join(self.rvc_root, "logs", self.params["exp_name"])

    # ---------- 六步骤 ----------

    def _st_mdx(self) -> bool:
        """T-1：MDXNet 三阶段分离（共享实现；已干声时 _plan SKIPPED）。"""
        outputs = run_mdx_dry_vocals(self.params["dataset_dir"], self.log_lines,
                                     self.autolive_home, prefix="[MDX]")
        if outputs is None:
            return False
        self.mdx_outputs = outputs
        return True

    def _st_preprocess(self) -> bool:
        """T-2：处理数据（上游 argv 契约 infer-web.py:243-252；n_p=10 规格固化；
        产物计数断言兜底上游 except 吞错——勘察事实）。"""
        # 编排职责（spike 实测）：exp_dir 必须预建（preprocess.py:26 打开日志文件）
        os.makedirs(self._exp_dir(), exist_ok=True)
        log_path = os.path.join(self._exp_dir(), "preprocess.log")
        open(log_path, "w").close()  # 编排层先 truncate（上游约定 13）
        cmd = [self.python, "infer/modules/train/preprocess.py",
               self.params["dataset_dir"], "40000", str(self.params["n_p"]),
               self._exp_dir(), "False", f"{self.params['per']:.1f}"]
        if not self._run(cmd, timeout=3600):
            return False
        gt = self._count(os.path.join(self._exp_dir(), "0_gt_wavs"), ".wav")
        k16 = self._count(os.path.join(self._exp_dir(), "1_16k_wavs"), ".wav")
        self.log_lines.append(f"[preprocess] 0_gt_wavs={gt} 1_16k_wavs={k16}")
        return gt > 0 and k16 > 0

    def _st_extract(self) -> bool:
        """T-3：先 f0 后 feature 串行（上游同入口顺序，事实 14）。
        f0 大写契约（extract_f0_rmvpe.py:18-23）+feature 小写契约；gpus_rmvpe
        "0-0"→2 分片逐个派发；rmvpe_gpu 不消费 n_p（规格 10 仅作用 preprocess）。"""
        exp = self._exp_dir()
        n_part = 2  # gpus_rmvpe="0-0" 段数派生（单卡 2 进程）
        for i_part in range(n_part):
            cmd = [self.python, "infer/modules/train/extract/extract_f0_rmvpe.py",
                   str(n_part), str(i_part), "0", exp, "True"]
            if not self._run(cmd, timeout=7200):
                self.log_lines.append(f"[extract] f0 分片 {i_part} 失败（上游不阻断 feature，继续）")
        f0n = self._count(os.path.join(exp, "2a_f0"), ".npy")
        cmd = [self.python, "infer/modules/train/extract_feature_print.py",
               "cuda", str(n_part), "0", "0", exp, self.params["version"], "false"]
        if not self._run(cmd, timeout=7200):
            return False
        fean = self._count(os.path.join(exp, "3_feature768"), ".npy")
        self.log_lines.append(f"[extract] 2a_f0={f0n} 3_feature768={fean}")
        return f0n > 0 and fean > 0

    def _st_train(self) -> bool:
        """T-4：config.json 模板覆写（v2+40k→v1/40k.json 特判）+ filelist 生成
        （四目录交集+转义+spk_id+mute×2+shuffle）+ train.py（rc=2333333 完成信号）。"""
        exp = self._exp_dir()
        os.makedirs(exp, exist_ok=True)
        # ① config.json 特判复刻（infer-web.py:575-589：v2+40k 共用 v1/40k.json）
        cfg_dst = os.path.join(exp, "config.json")
        if not os.path.isfile(cfg_dst):  # 已存在不覆盖（上游约定）
            shutil.copy2(os.path.join(self.rvc_root, "configs", "v1", "40k.json"), cfg_dst)
        # ② filelist 生成（infer-web.py:501-567 精确复刻）
        gt_dir = os.path.join(exp, "0_gt_wavs")
        feat_dir = os.path.join(exp, "3_feature768")
        f0_dir = os.path.join(exp, "2a_f0")
        fnsf_dir = os.path.join(exp, "2b-f0nsf")
        names = ({f.split(".")[0] for f in os.listdir(gt_dir)}
                 & {f.split(".")[0] for f in os.listdir(feat_dir)}
                 & {f.split(".")[0] for f in os.listdir(f0_dir)}
                 & {f.split(".")[0] for f in os.listdir(fnsf_dir)})
        if not names:
            self.log_lines.append("[train] filelist 交集为空（产物缺失）")
            return False
        gt_n = len(os.listdir(gt_dir))
        if len(names) < gt_n - 2:
            self.log_lines.append(f"[train] 警告：交集 {len(names)} < gt {gt_n}（缺失样本被静默丢弃的防护阈值）")
        spk = self.params["spk_id"]
        opt = ["%s/%s.wav|%s/%s.npy|%s/%s.wav.npy|%s/%s.wav.npy|%s" % (
            gt_dir.replace("\\", "\\\\"), n, feat_dir.replace("\\", "\\\\"), n,
            f0_dir.replace("\\", "\\\\"), n, fnsf_dir.replace("\\", "\\\\"), n, spk)
            for n in sorted(names)]
        now = self.rvc_root.replace("\\", "/")
        for _ in range(2):
            opt.append(f"{now}/logs/mute/0_gt_wavs/mute40k.wav|{now}/logs/mute/3_feature768/mute.npy|"
                       f"{now}/logs/mute/2a_f0/mute.wav.npy|{now}/logs/mute/2b-f0nsf/mute.wav.npy|{spk}")
        random.shuffle(opt)
        with open(os.path.join(exp, "filelist.txt"), "w") as f:
            f.write("\n".join(opt))
        self.log_lines.append(f"[train] filelist {len(opt)} 行（交集 {len(names)}+2 mute）")
        # ③ train.py（上游 argv 契约 infer-web.py:592-608；-c 0 维持现状 outside 发现 8）
        cmd = [self.python, "infer/modules/train/train.py",
               "-e", self.params["exp_name"], "-sr", "40k",
               "-f0", "1" if self.params["if_f0"] else "0",
               "-bs", str(self.params["batch_size"]), "-g", "0",
               "-te", str(self.params["epochs"]), "-se", str(self.params["save_every"]),
               "-pg", os.path.join(self.rvc_root, "assets", "pretrained_v2", "f0G40k.pth"),
               "-pd", os.path.join(self.rvc_root, "assets", "pretrained_v2", "f0D40k.pth"),
               "-l", "1", "-c", "0", "-sw", "1", "-v", self.params["version"]]
        if not self._run(cmd, timeout=14400, rc_ok=(0, 2333333)):
            self.log_lines.append("[train] train.py 未正常完成（rc≠0/2333333）")
            return False
        return True

    def _st_index(self) -> bool:
        """T-5：特征索引（进程内复刻 infer-web.py train_index:636-731）——
        3_feature768 合并→>2e5 行 KMeans 降采样（sklearn 缺则截断，诚实降级）→
        n_ivf=min(16√N, N//39)→faiss IVF,Flat→added_* 命名→os.link assets/indices
        （推理端只认 added_ 前缀，事实 5）。"""
        try:
            import faiss
            import numpy as np
        except ImportError as e:
            self.log_lines.append(f"[index] faiss/numpy 不可用: {e}")
            return False
        exp = self._exp_dir()
        feat_dir = os.path.join(exp, "3_feature768")
        big = []
        for fn in sorted(os.listdir(feat_dir)):
            if fn.endswith(".npy"):
                arr = np.load(os.path.join(feat_dir, fn))
                big.append(arr.reshape(-1, arr.shape[-1]))
        if not big:
            self.log_lines.append("[index] 3_feature768 为空")
            return False
        big_npy = np.concatenate(big, axis=0)
        n = big_npy.shape[0]
        self.log_lines.append(f"[index] 特征 {n} 行 × {big_npy.shape[1]} 维")
        if n > 2e5:
            try:
                from sklearn.cluster import MiniBatchKMeans
                km = MiniBatchKMeans(n_clusters=10000, batch_size=256 * 8, n_init=3)
                big_npy = km.cluster_centers_
                self.log_lines.append("[index] KMeans 降采样至 10000 中心")
            except ImportError:
                big_npy = big_npy[:200000]
                self.log_lines.append("[index] sklearn 缺失，截断降级至 200000 行（诚实降级）")
        np.save(os.path.join(exp, "total_fea.npy"), big_npy)
        n_ivf = min(int(16 * pow(n, 0.5)), n // 39)
        if n_ivf < 1:
            n_ivf = 1
        index = faiss.index_factory(big_npy.shape[1], f"IVF{n_ivf},Flat")
        index.train(big_npy)
        ver = self.params["version"]
        exp_name = self.params["exp_name"]
        trained_name = f"trained_IVF{n_ivf}_Flat_nprobe_1_{exp_name}_{ver}.index"
        added_name = f"added_IVF{n_ivf}_Flat_nprobe_1_{exp_name}_{ver}.index"
        index.add(big_npy)
        faiss.write_index(index, os.path.join(exp, added_name))
        # os.link 硬链到 assets/indices（上游 outside_index_root；失败回退复制）
        out_root = os.path.join(self.rvc_root, "assets", "indices")
        os.makedirs(out_root, exist_ok=True)
        dst = os.path.join(out_root, added_name)
        if not os.path.exists(dst):
            try:
                os.link(os.path.join(exp, added_name), dst)
            except OSError:
                shutil.copy2(os.path.join(exp, added_name), dst)
        self.log_lines.append(f"[index] {added_name}（n_ivf={n_ivf}）→ assets/indices")
        return True

    def _st_export(self) -> bool:
        """T-6：导出——终态模型=assets/weights/{exp}.pth（train.py:636-650 savee
        落点=weights 平铺，勘察事实 8；E2E 实测修正：logs/{exp}/ 只有 G_2333333
        大 ckpt，终态小模型在 weights/；复核发现 4：e8/e16 带后缀是中间点勿选，
        实跑产物 rvc_e2e.pth 与 e16_s400 并存即此语义）+added index 配对迁移
        models/rvc/+voice_profiles 单写者合并更新+py39 翻唱 runtime 双冒烟+fork
        基线落盘。"""
        exp_name = self.params["exp_name"]
        exp = self._exp_dir()
        final_pth = os.path.join(self.rvc_root, "assets", "weights", f"{exp_name}.pth")
        if not os.path.isfile(final_pth):
            self.log_lines.append(f"[export] 终态模型缺失: {final_pth}（train -sw 1 应产出）")
            return False
        idx_src = ""
        for fn in sorted(os.listdir(exp)):
            if fn.startswith("added_") and fn.endswith(".index"):
                idx_src = os.path.join(exp, fn)
                break
        if not idx_src:
            self.log_lines.append("[export] added_*.index 缺失（index 步产物）")
            return False
        # 迁移 models/rvc/（配对名统一 {exp}.pth / {exp}.index）
        dst_dir = os.path.join(self.autolive_home, "models", "rvc")
        os.makedirs(dst_dir, exist_ok=True)
        dst_pth = os.path.join(dst_dir, f"{exp_name}.pth")
        dst_idx = os.path.join(dst_dir, f"{exp_name}.index")
        shutil.copy2(final_pth, dst_pth)
        shutil.copy2(idx_src, dst_idx)
        # py39 翻唱 runtime 双冒烟（P-11+outside 发现 5：.pth load+faiss.read_index）
        smoke_py = (self.config.get("rvc_vc", "runtime_python")
                    or r"D:\AI\RVC20240604Nvidia\runtime\python.exe")
        code = ("import sys, torch, faiss\n"
                f"torch.load(r'{dst_pth}', map_location='cpu')\n"
                f"faiss.read_index(r'{dst_idx}')\n"
                "print('SMOKE_OK')\n")
        try:
            r = subprocess.run([smoke_py, "-c", code], capture_output=True,
                               text=True, timeout=120, encoding="utf-8", errors="replace",
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            ok = r.returncode == 0 and "SMOKE_OK" in (r.stdout or "")
            self.log_lines.append(f"[export] py39 双冒烟 {'通过' if ok else '失败: ' + (r.stderr or '')[-200:]}")
            if not ok:
                return False
        except Exception as e:
            self.log_lines.append(f"[export] py39 双冒烟异常: {e}")
            return False
        # voice_profiles 单写者合并更新（保 GSV 字段不被 clean 清空）
        from utils.voice_profiles import get_profile, upsert_profile
        existing = get_profile(exp_name) or {}
        merged = dict(existing)
        merged.update({"id": exp_name, "name": merged.get("name") or exp_name,
                       "rvc_model": dst_pth, "rvc_index": dst_idx})
        upsert_profile(merged)
        self.log_lines.append(f"[export] 档案 {exp_name} 已写入 rvc_model/rvc_index")
        # fork 基线落盘（tripwire 基线，S-1/复核发现 5）
        self._write_fork_baseline()
        return True

    def _write_fork_baseline(self):
        import hashlib
        files = {}
        for rel in FORK_BASELINE_FILES:
            full = os.path.join(self.rvc_root, rel)
            if os.path.isfile(full):
                files[rel] = hashlib.sha256(open(full, "rb").read()).hexdigest()
        out = os.path.join(self.rvc_root, ".autolive_fork_baseline.json")
        json.dump({"generated_by": "train_rvc_pipeline.export", "files": files},
                  open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        self.log_lines.append(f"[export] fork 基线 {len(files)} 文件哈希已落盘（tripwire）")

    @staticmethod
    def _count(d: str, ext: str) -> int:
        return len([f for f in os.listdir(d) if f.endswith(ext)]) if os.path.isdir(d) else 0
