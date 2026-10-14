# -*- coding: utf-8 -*-
"""T-0 spike：runtime312 下 RVC 训练链最小实跑（/autoplan 2026-10-14 批准项）。

目的：实证 py3.12/torch2.11 下 preprocess→f0→feature→train 六步前提可用，
失败则整批重估（计划 D2 回退预案）。spike 记录时长/显存作 T-4 估算校准。

用法：D:/AI/EDTalk/runtime312/python.exe tests/run_rvc_spike.py
cwd 必须是 AUTOlive 根（脚本内部切到 RVC fork 根跑子进程）。
"""
import os
import re
import shutil
import subprocess
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

FORK = r"D:\AI\RVC20240604Nvidia"
PY = r"D:\AI\EDTalk\runtime312\python.exe"
DATASET_SRC = os.path.join("tests", "sound", "sliced")
SPIKE_DATASET = os.path.join(FORK, "spike_dataset")
EXP_DIR = os.path.join(FORK, "logs", "spike0")
N_SAMPLE = 12          # 小样本：spike 验证兼容性，不训质量
EPOCHS = 1
BATCH = 2

STEPS = []


def run(step, cmd, timeout):
    t0 = time.time()
    print(f"[{step}] $ {' '.join(os.path.basename(str(c)) if i == 0 else str(c) for i, c in enumerate(cmd))[:160]}", flush=True)
    p = subprocess.run(cmd, cwd=FORK, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=timeout)
    dt = time.time() - t0
    STEPS.append((step, dt, p.returncode))
    tail = (p.stdout or "")[-400:].strip().replace("\n", " | ")
    err = (p.stderr or "")[-300:].strip().replace("\n", " | ")
    print(f"[{step}] rc={p.returncode} {dt:.1f}s  out…{tail[:200]}", flush=True)
    if err:
        print(f"[{step}] err…{err[:200]}", flush=True)
    return p.returncode


def count_dir(d, exts):
    return len([f for f in os.listdir(d) if f.endswith(exts)]) if os.path.isdir(d) else 0


def main():
    assert os.path.isdir(FORK), f"RVC fork 不存在: {FORK}"
    # 0) 小样本数据集（sliced 前 N 个 wav）
    shutil.rmtree(SPIKE_DATASET, ignore_errors=True)
    os.makedirs(SPIKE_DATASET, exist_ok=True)
    srcs = sorted(f for f in os.listdir(DATASET_SRC) if f.endswith(".wav"))[:N_SAMPLE]
    assert len(srcs) == N_SAMPLE, f"素材不足: {len(srcs)}"
    for f in srcs:
        shutil.copy2(os.path.join(DATASET_SRC, f), os.path.join(SPIKE_DATASET, f))
    print(f"[prep] spike 数据集 {len(srcs)} wav（来自 sliced）", flush=True)

    t_all = time.time()
    # 1) 处理数据（上游 argv 契约：trainset sr n_p exp_dir noparallel per）
    # 编排职责（spike 实测发现，14 条清单外补充）：exp_dir 必须预建——
    # preprocess.py:26 以 "a+" 打开 exp_dir/preprocess.log，父目录缺失即 FileNotFoundError
    os.makedirs(EXP_DIR, exist_ok=True)
    rc = run("preprocess", [PY, "infer/modules/train/preprocess.py",
                            SPIKE_DATASET, "40000", "2", EXP_DIR, "False", "3.7"], 600)
    gt = count_dir(os.path.join(EXP_DIR, "0_gt_wavs"), (".wav",))
    k16 = count_dir(os.path.join(EXP_DIR, "1_16k_wavs"), (".wav",))
    print(f"[check] 0_gt_wavs={gt} 1_16k_wavs={k16}", flush=True)
    assert rc == 0 and gt > 0 and k16 > 0, "preprocess 产物为空"

    # 2) f0 rmvpe（大写契约）+ 3) feature（小写契约）
    rc = run("f0", [PY, "infer/modules/train/extract/extract_f0_rmvpe.py",
                    "1", "0", "0", EXP_DIR, "True"], 900)
    f0n = count_dir(os.path.join(EXP_DIR, "2a_f0"), (".npy",))
    print(f"[check] 2a_f0={f0n}", flush=True)
    assert rc == 0 and f0n > 0, "f0 产物为空"
    rc = run("feature", [PY, "infer/modules/train/extract_feature_print.py",
                         "cuda", "1", "0", "0", EXP_DIR, "v2", "false"], 900)
    fean = count_dir(os.path.join(EXP_DIR, "3_feature768"), (".npy",))
    print(f"[check] 3_feature768={fean}", flush=True)
    assert rc == 0 and fean > 0, "feature 产物为空"

    # 4) config.json（v2+40k→v1/40k.json 特判复刻）+ filelist（四目录交集）
    shutil.copy2(os.path.join(FORK, "configs", "v1", "40k.json"),
                 os.path.join(EXP_DIR, "config.json"))
    gt_dir = os.path.join(EXP_DIR, "0_gt_wavs")
    feat_dir = os.path.join(EXP_DIR, "3_feature768")
    f0_dir = os.path.join(EXP_DIR, "2a_f0")
    fnsf_dir = os.path.join(EXP_DIR, "2b-f0nsf")
    names = ({f.split(".")[0] for f in os.listdir(gt_dir)}
             & {f.split(".")[0] for f in os.listdir(feat_dir)}
             & {f.split(".")[0] for f in os.listdir(f0_dir)}
             & {f.split(".")[0] for f in os.listdir(fnsf_dir)})
    print(f"[check] filelist 交集={len(names)}（gt={gt} feat={fean} f0={f0n}）", flush=True)
    assert len(names) >= gt - 2, f"交集丢失样本: {len(names)} < {gt}-2"
    opt = []
    for name in sorted(names):
        opt.append("%s/%s.wav|%s/%s.npy|%s/%s.wav.npy|%s/%s.wav.npy|%s" % (
            gt_dir.replace("\\", "\\\\"), name,
            feat_dir.replace("\\", "\\\\"), name,
            f0_dir.replace("\\", "\\\\"), name,
            fnsf_dir.replace("\\", "\\\\"), name, 0))
    now = FORK.replace("\\", "/")  # mute 行=now_dir+正斜杠拼接（上游混拼形态）
    for _ in range(2):
        opt.append(f"{now}/logs/mute/0_gt_wavs/mute40k.wav|{now}/logs/mute/3_feature768/mute.npy|"
                   f"{now}/logs/mute/2a_f0/mute.wav.npy|{now}/logs/mute/2b-f0nsf/mute.wav.npy|0")
    import random
    random.shuffle(opt)
    with open(os.path.join(EXP_DIR, "filelist.txt"), "w") as f:
        f.write("\n".join(opt))
    print(f"[filelist] {len(opt)} 行（含 2 mute）", flush=True)

    # 5) 训练 1 epoch（-l 0 不固定名/ -sw 0 不存小模型——spike 只验证链路）
    cmd = [PY, "infer/modules/train/train.py", "-e", "spike0", "-sr", "40k", "-f0", "1",
           "-bs", str(BATCH), "-g", "0", "-te", str(EPOCHS), "-se", "1",
           "-pg", os.path.join(FORK, "assets", "pretrained_v2", "f0G40k.pth"),
           "-pd", os.path.join(FORK, "assets", "pretrained_v2", "f0D40k.pth"),
           "-l", "0", "-c", "0", "-sw", "0", "-v", "v2"]
    rc = run("train", cmd, 1800)
    # 上游完成信号=退出码 2333333（train.py os._exit）；-sw 0 时无 savee 但 rc 语义相同
    ckpts = [f for f in os.listdir(EXP_DIR) if re.match(r"[GD]_\d+\.pth", f)] if os.path.isdir(EXP_DIR) else []
    print(f"[check] train rc={rc} ckpts={ckpts}", flush=True)
    ok = rc in (0, 2333333) and ckpts, f"训练未产出 ckpt（rc={rc}）"

    total = time.time() - t_all
    print("\n===== SPIKE RESULT =====", flush=True)
    for step, dt, r in STEPS:
        print(f"  {step:<10} rc={r}  {dt:6.1f}s")
    print(f"  total {total:.1f}s  dataset={len(srcs)} wav  epochs={EPOCHS} bs={BATCH}")
    print("RESULT:", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
