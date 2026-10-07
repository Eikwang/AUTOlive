# -*- coding: utf-8 -*-
"""
EDTalk → AUTOlive 一键复制脚本（EDTalk功能集成计划 §3 + DX 义务①）

单命令执行：复制代码与模型 → 生成 MANIFEST → pip freeze 快照 →
fastrag 防火墙阻断 → 步骤脚本存在性校验 → .gitignore 更新。

用法（AUTOlive 根目录）：
    python scripts\\copy_edtalk.py            # 全量复制
    python scripts\\copy_edtalk.py --skip-big  # 跳过大文件（代码先行验证用）

冒烟说明：复制完成后须按 edtalk/README.md 执行三入口冒烟与迷你训练冒烟，
通过后一键训练按钮才解锁（按钮本身由迷你冒烟通过的 dev 键控制）。
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time

SOURCE_ROOT = r"D:\AI\EDTalk"
TARGET_ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "edtalk")

# 复制清单（源相对路径, 目标相对路径, git 处置, 说明）
COPY_ITEMS = [
    ("realtime_serve", "realtime_serve", "ignore-weights", "实时推理服务代码"),
    ("train_fine_tune.py", "train_fine_tune.py", "track", "底模微调入口"),
    ("demo_EDTalk_A.py", "demo_EDTalk_A.py", "track", "get_mel 依赖（冒烟发现的仓库根依赖）"),
    ("runtime312/ffmpeg/bin", "runtime312/ffmpeg/bin", "ignore", "ffmpeg 拆帧依赖（exe+DLL，crop_head_video 路径假设）"),
    ("fine_tune", "fine_tune", "track", "底模微调模块"),
    ("data_preprocess", "data_preprocess", "track", "11 步预处理链"),
    ("datasets", "datasets", "track", "训练数据集模块"),
    ("networks", "networks", "track", "网络模块"),
    ("hparams.py", "hparams.py", "track", "超参数"),
    ("audio.py", "audio.py", "track", "音频工具"),
    ("face_detection", "face_detection", "track", "人脸检测库"),
    ("face_sr", "face_sr", "track", "面部修复库"),
    ("pytorch_gaze_redirection", "pytorch_gaze_redirection", "track", "视线纠正库"),
    ("tools", "tools", "track", "评估工具（eval_finetune/mouth_metrics）"),
    ("ckpts", "ckpts", "ignore", "底模权重（大文件）"),
    ("ckpt_models/wy_runC", "ckpt_models/wy_runC", "ignore", "RunC resume 产物（Eng F7）"),
    ("fastrag", "fastrag", "ignore-exe", "fastrag 服务（exe+模型）"),
]

# .gitignore 追加段（大文件不入库）
GITIGNORE_BLOCK = """
# === EDTalk 运行副本大文件（EDTalk功能集成计划 §3） ===
edtalk/ckpts/
edtalk/ckpt_models/
edtalk/realtime_serve/weights/
edtalk/realtime_serve/*.log
edtalk/fastrag/*.exe
edtalk/fastrag/target/
edtalk/fastrag/chekpoint/
edtalk/fastrag/kbindex/
edtalk/fastrag/*.log
edtalk/train_progress/.lock-*
edtalk/data_preprocess/data_preprocess_for_train/__pycache__/
"""


def run(cmd, **kw):
    print(f"  $ {' '.join(cmd) if isinstance(cmd, list) else cmd}")
    return subprocess.run(cmd, **kw)


def copy_tree(src, dst, skip_big=False):
    """目录复制（robocopy 优先——Windows 大文件效率高）。"""
    if skip_big and _dir_size_gb(src) > 1.0:
        print(f"  ⏭ 跳过大目录（--skip-big，{_dir_size_gb(src):.1f}GB）：{src}")
        return False
    if os.name == "nt":
        # robocopy 退出码：0-7 均为成功
        result = run(["robocopy", src, dst, "/E", "/NFL", "/NDL", "/NJH", "/NJS", "/NP"],
                     capture_output=True)
        if result.returncode > 7:
            print(f"  ✗ robocopy 失败（exit {result.returncode}）")
            return False
    else:
        shutil.copytree(src, dst, dirs_exist_ok=True)
    return True


def _dir_size_gb(path):
    total = 0
    for root, _, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total / (1024 ** 3)


def step1_copy(skip_big=False):
    """① 复制代码与模型。返回 [(源, 目标, GB)] 清单供 MANIFEST 用。"""
    print("\n[1/5] 复制代码与模型")
    manifest = []
    for src_rel, dst_rel, git, desc in COPY_ITEMS:
        src = os.path.join(SOURCE_ROOT, src_rel.replace("/", os.sep))
        dst = os.path.join(TARGET_ROOT, dst_rel.replace("/", os.sep))
        if not os.path.exists(src):
            print(f"  ⚠ 源不存在，跳过：{src}")
            continue
        size_gb = _dir_size_gb(src) if os.path.isdir(src) else os.path.getsize(src) / (1024 ** 3)
        t0 = time.time()
        if os.path.isdir(src):
            ok = copy_tree(src, dst, skip_big)
        else:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)
            ok = True
        if ok:
            print(f"  ✓ {src_rel} → edtalk/{dst_rel}（{size_gb:.2f}GB，{time.time()-t0:.0f}s）[{desc}]")
            manifest.append((src_rel, dst_rel, git, desc, size_gb))
    return manifest


def step2_gitignore():
    """② 更新 .gitignore（大文件防入库）。"""
    print("\n[2/5] 更新 .gitignore")
    repo_root = os.path.dirname(TARGET_ROOT)
    gi_path = os.path.join(repo_root, ".gitignore")
    existing = open(gi_path, encoding="utf-8").read() if os.path.exists(gi_path) else ""
    if "edtalk/ckpts/" in existing:
        print("  ✓ 已包含 EDTalk 段，跳过")
        return
    with open(gi_path, "a", encoding="utf-8") as f:
        f.write(GITIGNORE_BLOCK)
    print("  ✓ 已追加 EDTalk 大文件忽略规则")


def step3_manifest(manifest):
    """③ 生成 MANIFEST.md（来源/日期/git 处置/大小——Eng F7 + DX 义务⑥）。"""
    print("\n[3/5] 生成 MANIFEST.md")
    lines = [
        "# EDTalk 运行副本 MANIFEST",
        "",
        f"- 复制日期：{time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 来源：{SOURCE_ROOT}",
        f"- 唯一运行副本声明：本目录（AUTOlive/edtalk）为唯一运行副本，"
        f"EDTalk 原目录降为开发存档",
        "",
        "| 源（D:\\AI\\EDTalk） | 目标（edtalk/） | git 处置 | 说明 | 大小(GB) |",
        "|---|---|---|---|---|",
    ]
    for src, dst, git, desc, gb in manifest:
        lines.append(f"| {src} | {dst} | {git} | {desc} | {gb:.2f} |")
    with open(os.path.join(TARGET_ROOT, "MANIFEST.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"  ✓ MANIFEST.md（{len(manifest)} 项）")


def step4_freeze():
    """④ pip freeze + Python 版本快照（native F11：防环境升级静默断裂）。"""
    print("\n[4/5] 依赖快照")
    py = os.path.join(SOURCE_ROOT, "runtime312", "python.exe")
    if not os.path.exists(py):
        print(f"  ⚠ 解释器不存在：{py}（快照跳过）")
        return
    freeze = run([py, "-m", "pip", "freeze"], capture_output=True, text=True)
    docs_dir = os.path.join(os.path.dirname(TARGET_ROOT), "docs")
    os.makedirs(docs_dir, exist_ok=True)
    with open(os.path.join(docs_dir, "edtalk-runtime-freeze.txt"), "w", encoding="utf-8") as f:
        f.write(f"# EDTalk runtime312 pip freeze 快照（{time.strftime('%Y-%m-%d %H:%M:%S')}）\n")
        f.write(freeze.stdout or "")
    version = run([py, "--version"], capture_output=True, text=True)
    with open(os.path.join(docs_dir, "edtalk-runtime-freeze.txt"), "a", encoding="utf-8") as f:
        f.write(f"# {version.stdout.strip()}\n")
    print("  ✓ docs/edtalk-runtime-freeze.txt")


def step5_security_and_verify():
    """⑤ fastrag 防火墙阻断 + 步骤脚本存在性校验 + .pth 暗门断言。"""
    print("\n[5/5] 安全与校验")
    # S1：fastrag server.exe 硬编码 0.0.0.0 无鉴权——入站阻断（本机回环不受影响）
    if os.name == "nt":
        result = run(
            ["netsh", "advfirewall", "firewall", "add", "rule",
             "name=Block fastrag 11420 inbound (AUTOlive local-only)",
             "dir=in", "action=block", "protocol=TCP", "localport=11420"],
            capture_output=True, text=True,
        )
        ok = result.returncode == 0
        print(f"  {'✓' if ok else '⚠'} fastrag 11420 入站阻断规则"
              f"{'已添加' if ok else '添加失败（可能已有规则或需管理员权限，请手动添加）'}")

    # E2：run_preprocess 全部 11 个步骤脚本存在性校验
    pp = os.path.join(TARGET_ROOT, "data_preprocess", "data_preprocess_for_train", "run_preprocess.py")
    if os.path.exists(pp):
        src = open(pp, encoding="utf-8").read()
        scripts = re.findall(r'"([A-Za-z_0-9]+\.py)"', src)
        missing = [s for s in scripts if s != "run_preprocess.py" and
                   not os.path.exists(os.path.join(TARGET_ROOT, "data_preprocess", "data_preprocess_for_train", s))
                   and not os.path.exists(os.path.join(TARGET_ROOT, s))]
        if missing:
            print(f"  ✗ 预处理步骤脚本缺失：{missing}")
        else:
            print(f"  ✓ 预处理 {len(scripts)-1} 个步骤脚本全部存在")
    else:
        print("  ✗ run_preprocess.py 缺失——复制不完整")

    # H3：.pth 可编辑安装暗门——import 路径断言
    py = os.path.join(SOURCE_ROOT, "runtime312", "python.exe")
    if os.path.exists(py):
        probe = run(
            [py, "-c",
             "import sys; sys.path.insert(0, r'" + TARGET_ROOT + "');"
             "import datasets; print('datasets@', datasets.__file__)"],
            capture_output=True, text=True,
        )
        out = (probe.stdout or "") + (probe.stderr or "")
        if "EDTalk" in out and "AUTOlive" not in out:
            print(f"  ⚠ datasets 解析回 EDTalk 旧树（.pth 可编辑安装暗门）：{out.strip()[:200]}")
        else:
            print("  ✓ 模块解析检查通过（或不可判定，冒烟时复核）")


def main():
    parser = argparse.ArgumentParser(description="EDTalk → AUTOlive 一键复制脚本")
    parser.add_argument("--skip-big", action="store_true", help="跳过 >1GB 大目录（代码先行验证）")
    args = parser.parse_args()

    print(f"=== EDTalk 一键复制：{SOURCE_ROOT} → {TARGET_ROOT} ===")
    if not os.path.isdir(SOURCE_ROOT):
        print(f"✗ 源目录不存在：{SOURCE_ROOT}")
        sys.exit(1)
    t0 = time.time()
    manifest = step1_copy(args.skip_big)
    step2_gitignore()
    step3_manifest(manifest)
    step4_freeze()
    step5_security_and_verify()
    print(f"\n=== 复制完成（{time.time()-t0:.0f}s）。下一步：")
    print("  1. 三入口冒烟（见 edtalk/README.md）：")
    print("     runtime312\\python.exe -m realtime_serve.main --no-unitycapture")
    print("     runtime312\\python.exe train_fine_tune.py --help")
    print("     edtalk\\fastrag\\server.exe（/health 探活后关闭）")
    print("  2. 迷你训练冒烟（通过后一键训练按钮解锁）")
    print("  3. 画面设置页开启开关 → 运行系统")


if __name__ == "__main__":
    main()
