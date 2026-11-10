# -*- coding: utf-8 -*-
"""训练管线共享子进程/GPU 工具（/autoplan 2026-10-14 Eng shared-code 裁决提取）。

自 utils/train_voice_pipeline.py:248-333 提取，GSV（train_voice_pipeline）与
RVC（train_rvc_pipeline）两管线共用——GPU 残留显存教训（WDDM 换页慢 10-20 倍）
的防护逻辑单一修复点，禁止在两管线各留拷贝。

内容：
- vram_used_gb / wait_vram_release / release_local_gpu：显存基线轮询三件
- cleanup_after_step：步间 taskkill /F /T 进程树终结 + 显存回落确认
- gpu_gate：后台任务准入（gpu_policy 单一模块委托）
- run_mdx_dry_vocals：MDXNet 三阶段干声分离（T-1，进程内直调
  apps/aicovergen/src/mdx.py + models/mdxnet 资产，GSV/RVC 训练共用）
"""
import json
import os
import subprocess
import time
from typing import Callable, List, Optional

from utils.my_log import logger

MDXNET_DIR = os.path.join("models", "mdxnet")

# 三阶段链（pick 语义对齐 AICoverGen main.py 解包：stage2/3 主唱在反相轨）
# kwargs 以 train_voice_pipeline.py E2E 实跑验证版为准（2026-10-14 提取时核对）
MDX_STAGES = [
    {"model": "UVR-MDX-NET-Voc_FT.onnx", "pick": "main",
     "kwargs": {"denoise": True}},
    {"model": "UVR_MDXNET_KARA_2.onnx", "pick": "invert",
     "kwargs": {"suffix": "Backup", "invert_suffix": "Main", "denoise": True}},
    {"model": "Reverb_HQ_By_FoxJoy.onnx", "pick": "invert",
     "kwargs": {"invert_suffix": "DeReverb", "exclude_main": True, "denoise": True}},
]


def _noflag():
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def vram_used_gb() -> Optional[float]:
    """当前显存占用（GB）；查询失败返回 None（跳过等待）。"""
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=8,
            creationflags=_noflag(),
        ).stdout.strip().splitlines()
        return float(out[0]) / 1024.0 if out else None
    except Exception:
        return None


def wait_vram_release(used_before_gb: Optional[float], timeout: int = 60,
                      prefix: str = "[train]") -> None:
    """轮询等待显存回落到基线（+0.5GB 容差）；超时放行+警告。"""
    if used_before_gb is None:
        return
    deadline = time.time() + timeout
    while time.time() < deadline:
        now = vram_used_gb()
        if now is None or now <= used_before_gb + 0.5:
            return
        time.sleep(3)
    try:
        logger.warning(
            f"{prefix} 显存回落超时（当前 {vram_used_gb():.1f}GB > 基线 {used_before_gb:.1f}GB）"
            "——可能存在外部进程占用，继续下一步")
    except Exception:
        pass


def release_local_gpu() -> None:
    """进程内 GPU 资源显式回收（MDX 步等直调场景）：GC+torch 缓存清空。"""
    try:
        import gc as _gc
        _gc.collect()
        import torch as _torch
        if _torch.cuda.is_available():
            _torch.cuda.empty_cache()
    except Exception:
        pass


def _tree_pids(root_pid: int) -> set:
    """进程树 PID 集（root+psutil children recursive；psutil 缺失仅 root）。"""
    pids = {root_pid}
    try:
        import psutil
        for c in psutil.Process(root_pid).children(recursive=True):
            pids.add(c.pid)
    except Exception:
        pass
    return pids


def cleanup_after_step(proc: Optional[subprocess.Popen], used_before_gb: Optional[float],
                       prefix: str = "[train]"):
    """步间资源清理（2026-10-14 用户指令）：步骤子进程退出后——
    ①taskkill /T 终结整棵进程树（mp.spawn 孙进程/dataloader worker 孤儿必灭）；
    ②显存清零确认——2026-11-09 用户实报根修：判定从"总量回落基线"改为
    "**训练树 PID 归属**"（PDH 探针 gpu_probe.pdh_memory_pids，任务管理器
    同源）。taskkill 后 WDDM 驱动层显存回收延迟（实测 >120s、无进程残留、
    最终自行回落）与外部进程显存波动（安卓虚拟机/浏览器）都会污染总量判定，
    误判"残留"阻断编排。现语义：训练树 PID 从 PDH 消失=清零放行（总量高为
    外部占用，日志说明不阻断）；PDH 不可用回退总量判定（原行为）。"""
    from utils.gpu_probe import pdh_memory_pids

    tree_pids: set = set()
    if proc is not None and proc.poll() is None:
        tree_pids = _tree_pids(proc.pid)
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True, timeout=15,
                creationflags=_noflag())
        except Exception:
            logger.warning(f"{prefix} taskkill 清理异常", exc_info=True)

    if not tree_pids:
        # 无可归属树（proc 已退出/None）——保留总量回落确认（原行为）
        wait_vram_release(used_before_gb, prefix=prefix)
        return

    def _tree_alive() -> Optional[bool]:
        rows = pdh_memory_pids()
        if rows is None:
            return None
        return bool(tree_pids & {p for p, _ in rows})

    deadline = time.time() + 120
    while time.time() < deadline:
        alive = _tree_alive()
        if alive is None:
            # PDH 不可用——回退总量判定（原行为）
            wait_vram_release(used_before_gb, prefix=prefix, timeout=max(
                5, int(deadline - time.time())))
            return
        if not alive:
            total_now = vram_used_gb()
            if used_before_gb is not None and total_now is not None \
                    and total_now > used_before_gb + 0.5:
                logger.info(
                    f"{prefix} 训练树 {len(tree_pids)} PID 显存已清零（总量 "
                    f"{total_now:.1f}GB 高于基线 {used_before_gb:.1f}GB 为外部进程占用，不阻断）")
            return
        time.sleep(5)
    logger.warning(
        f"{prefix} 训练树 PID {sorted(tree_pids)} 显存 120s 未清零（真残留）"
        "——检查进程是否存活；总量基线信息：当前 "
        f"{vram_used_gb()}GB / 基线 {used_before_gb}GB")


def gpu_gate(config, log_sink: List[str], prefix: str = "[train]") -> bool:
    """后台任务 GPU 准入（E-2/R-6 单一模块委托）；拒绝原因写入 log_sink。"""
    from utils.gpu_policy import is_background_task_allowed
    reserve = 4.0
    try:
        v = config.get("audio_integration", "policies", "vram_reserve_gb")
        if v:
            reserve = float(v)
    except Exception:
        pass
    ok, reason = is_background_task_allowed(reserve)
    if not ok:
        log_sink.append(f"[GPU 拒绝] {reason}——完成后可重试本步骤")
    return ok


def run_mdx_dry_vocals(dataset_dir: str, log_sink: List[str],
                       autolive_home: str, prefix: str = "[MDX]") -> Optional[List[str]]:
    """MDXNet 三阶段分离（T-1，GSV/RVC 共用）。

    输入→44100Hz 立体声归一化（mono→ffmpeg -ac 2）→三阶段链（denoise=True），
    最终干声 = 反相轨，收集到 dataset_dir/mdx_vocals/。失败返回 None（log_sink
    有原因），成功返回干声绝对路径列表。
    """
    import sys as _sys
    used_before = vram_used_gb()
    work = os.path.join(dataset_dir, "mdx_work")
    os.makedirs(work, exist_ok=True)
    mdxnet_dir = os.path.join(autolive_home, MDXNET_DIR)
    with open(os.path.join(mdxnet_dir, "model_data.json"), encoding="utf-8") as f:
        model_params = json.load(f)
    sys_path = os.path.join(autolive_home, "apps", "aicovergen", "src")
    if sys_path not in _sys.path:
        _sys.path.insert(0, sys_path)
    from mdx import run_mdx  # noqa: E402  进程内直调

    # 阶段 0：输入归一化——**无条件**转 44100Hz 立体声副本进 mdx_work
    try:
        import librosa
    except ImportError as e:
        log_sink.append(f"{prefix} 失败] librosa 不可用: {e}")
        return None
    inputs = []
    for fn in sorted(os.listdir(dataset_dir)):
        if not fn.lower().endswith((".wav", ".mp3", ".flac", ".m4a", ".ogg")):
            continue
        src = os.path.join(dataset_dir, fn)
        stem = os.path.splitext(fn)[0]
        stereo = os.path.join(work, f"{stem}_stereo.wav")
        if os.path.exists(stereo):
            inputs.append(stereo)
            continue
        try:
            librosa.load(src, mono=False, sr=44100)  # 仅探测可解码性
        except Exception as e:
            log_sink.append(f"{prefix} 失败] 无法解码 {fn}: {e}")
            return None
        rc = subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", src,
             "-ar", "44100", "-ac", "2", stereo],
            capture_output=True,
            creationflags=_noflag())
        if rc.returncode != 0:
            log_sink.append(f"{prefix} 失败] 立体声转换失败 {fn}: ffmpeg rc={rc.returncode}")
            return None
        inputs.append(stereo)
    if not inputs:
        log_sink.append(f"{prefix} 失败] 数据集目录无音频文件（支持 wav/mp3/flac/m4a/ogg）")
        return None

    current = inputs
    for stage in MDX_STAGES:
        model_path = os.path.join(mdxnet_dir, stage["model"])
        nxt = []
        for inp in current:
            out_dir = os.path.dirname(inp)
            try:
                main_path, invert_path = run_mdx(model_params, out_dir, model_path,
                                                 inp, keep_orig=True, **stage["kwargs"])
            except Exception as e:
                log_sink.append(f"{prefix} 失败] {stage['model']} 处理 {os.path.basename(inp)}: {e}")
                logger.error(f"{prefix} 分离异常", exc_info=True)
                return None
            got = invert_path if stage["pick"] == "invert" else main_path
            if not got or not os.path.exists(got):
                log_sink.append(f"{prefix} 失败] {stage['model']} 未产出预期文件（{os.path.basename(inp)}）")
                return None
            nxt.append(got)
        current = nxt
    # 干声落位：收集到 dataset_dir/mdx_vocals/
    vocals_dir = os.path.join(dataset_dir, "mdx_vocals")
    os.makedirs(vocals_dir, exist_ok=True)
    outputs = []
    for wav in current:
        target = os.path.join(vocals_dir, os.path.basename(wav))
        if os.path.abspath(wav) != os.path.abspath(target):
            os.replace(wav, target)
        outputs.append(target)
    # 步间清理：进程内直调——显式回收显存并等待回落基线
    release_local_gpu()
    wait_vram_release(used_before, prefix=prefix)
    names = ", ".join(os.path.basename(x) for x in outputs[:3])
    log_sink.append(f"{prefix}] 三阶段完成，{len(outputs)} 个干声入 {vocals_dir}（如 {names}）")
    return outputs
