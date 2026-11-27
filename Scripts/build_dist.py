# -*- coding: UTF-8 -*-
"""发行目录组装脚本（发行改造阶段 E，2026-11-22）。

把 Nuitka 产物（build_nuitka/webui.dist）与运行时资产组装为绿色目录发行包：

    dist/AUTOlive-1.0.0-portable/
    ├── AUTOlive.exe + Nuitka 依赖（webui.dist 平铺到根）
    ├── ui/  config.example.json  config_templates/
    ├── main.py  utils/  frontend/  danmaku_listener/  webui.py   # 主系统源码
    ├── models/            # 统一模型根（A2 新布局直拷，无 junction）
    ├── runtime312/        # 全量解释器（排除 __pycache__）
    ├── apps/              # gpt-sovits + rvc + aicovergen（/XJ 排除 junction）
    └── edtalk/            # 运行时子集（排除 test_data 1TB / 残留）

关键设计：**发行包不含任何 junction**（zip 无法携带）——三个 junction
（edtalk/ckpts、apps/gpt-sovits/pretrained_models、apps/gpt-sovits/runtime）
由 webui.py frozen 引导块首启幂等重建；注册中心绝对路径解析为第二保障。

用法（仓库根）：
    runtime312\\python.exe Scripts\\build_dist.py              # 构建+组装
    runtime312\\python.exe Scripts\\build_dist.py --skip-build  # 复用已有 webui.dist
    runtime312\\python.exe Scripts\\build_dist.py --zip        # 组装后打 zip（store 不压缩）
"""
import json
import os
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

VERSION = "1.0.0"
DIST_ROOT = REPO / "dist" / f"AUTOlive-{VERSION}-portable"
NUITKA_DIST = REPO / "build_nuitka" / "webui.dist"

SKIP_BUILD = "--skip-build" in sys.argv
MAKE_ZIP = "--zip" in sys.argv

# robocopy 复制清单：(源, 目标, 排除目录, 排除文件)
COPY_ITEMS = [
    ("runtime312", "runtime312", ["__pycache__"], []),
    ("apps", "apps", ["__pycache__"], []),
    # edtalk 运行时子集：test_data 1TB / 训练残留 / junction(ckpts, /XJ) 排除
    ("edtalk", "edtalk",
     ["test_data", "__pycache__", "logs", "train_progress", "ckpts"], []),
    ("models", "models", ["__pycache__"], []),
    ("frontend", "frontend", ["__pycache__"], []),
    ("utils", "utils", ["__pycache__"], []),
    ("danmaku_listener", "danmaku_listener", ["__pycache__"], []),
]
# 根级平铺文件（主系统源码 + 回退保障：runtime312\python.exe webui.py 仍可用）
ROOT_FILES = ["main.py", "webui.py", "config.example.json",
              "Scripts/autolive_entry.py"]  # NiceGUI script mode 重执行入口
ROOT_DIRS = ["config_templates"]


def robocopy(src: Path, dst: Path, exclude_dirs=None) -> int:
    """robocopy /E /XJ 复制；返回码 0-7 视为成功（1=有新文件）。"""
    cmd = ["robocopy", str(src), str(dst), "/E", "/XJ", "/MT:16",
           "/NFL", "/NDL", "/NJH", "/NJS", "/NP", "/R:2", "/W:5"]
    for d in (exclude_dirs or []):
        cmd += ["/XD", str(src / d)]
    print(f"[copy] {src.name} -> {dst}")
    return subprocess.run(cmd).returncode


def assemble() -> None:
    if DIST_ROOT.exists():
        print(f"[assemble] 目标已存在，清空重建: {DIST_ROOT}")
        shutil.rmtree(DIST_ROOT)
    DIST_ROOT.mkdir(parents=True)

    # 1) Nuitka 产物平铺
    if not (NUITKA_DIST / "AUTOlive.exe").exists():
        print(f"[assemble][FAIL] Nuitka 产物缺失: {NUITKA_DIST / 'AUTOlive.exe'}\n"
              f"先执行 Scripts\\build_frontend.py，或加 --skip-build 前先构建")
        sys.exit(1)
    rc = robocopy(NUITKA_DIST, DIST_ROOT, ["__pycache__"])
    if rc > 7:
        print(f"[assemble][FAIL] webui.dist 平铺失败 rc={rc}")
        sys.exit(rc)

    # 2) 大资产 + 源码目录
    for src, dst, excl, _ in COPY_ITEMS:
        rc = robocopy(REPO / src, DIST_ROOT / dst, excl)
        if rc > 7:
            print(f"[assemble][FAIL] {src} 复制失败 rc={rc}")
            sys.exit(rc)

    # 3) 根级文件 / 目录（源路径含子目录时按 basename 平铺到 dist 根）
    for f in ROOT_FILES:
        src = REPO / f
        if src.exists():
            shutil.copy2(src, DIST_ROOT / Path(f).name)
    for d in ROOT_DIRS:
        src = REPO / d
        if src.is_dir():
            shutil.copytree(src, DIST_ROOT / d, ignore=shutil.ignore_patterns("__pycache__"))

    # 4) 校验 + MANIFEST
    problems = validate()
    write_manifest()
    if problems:
        print("[assemble][FAIL] 校验未通过：")
        for p in problems:
            print("  - " + p)
        sys.exit(1)
    print(f"[assemble][OK] 发行目录就绪: {DIST_ROOT}")


def validate() -> list:
    """四链 + 结构校验。"""
    problems = []
    d = DIST_ROOT

    def need(rel: str, glob_first: str = None):
        p = d / rel
        if not p.exists():
            problems.append(f"缺失: {rel}")
        elif glob_first and not any(p.glob(glob_first)):
            problems.append(f"空目录: {rel}")

    need("AUTOlive.exe")
    need("config.example.json")
    need("ui", "*")
    need("main.py")
    need("frontend", "*.py")
    need("utils", "*.py")
    # 四链
    need("runtime312", "python.exe")
    need("runtime312", "python312.dll")
    need("apps/gpt-sovits/GPT_SoVITS", "configs")
    need("apps/rvc")
    need("apps/aicovergen")
    need("edtalk/realtime_serve", "*.py")
    need("edtalk/networks", "*.py")
    need("edtalk/fastrag")
    # models 统一根（A2 布局）
    for sub in ("gpt_sovits", "rvc", "asr", "uvr5", "edtalk", "tuber"):
        need(f"models/{sub}", "*")
    # junction 目标物理存在（首启重建依赖）
    need("models/edtalk/Audio2Lip.pt")
    need("models/gpt_sovits/s1v3.ckpt")
    for p in problems:
        print(f"  [FAIL] {p}")
    if not problems:
        print("  [OK] 结构/四链校验全部通过")
    return problems


def write_manifest() -> None:
    """BUILD_MANIFEST.json：版本、来源、体积、文件数、校验时间。"""
    total, files = 0, 0
    for root, _, fns in os.walk(DIST_ROOT):
        for fn in fns:
            files += 1
            try:
                total += os.path.getsize(os.path.join(root, fn))
            except OSError:
                pass
    manifest = {
        "version": VERSION,
        "built_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "source_repo": str(REPO),
        "nuitka_dist": str(NUITKA_DIST),
        "layout": "models/ 统一根 + 首启 junction 重建（zip 无链接）",
        "junctions_first_run": [
            "edtalk/ckpts -> models/edtalk",
            "apps/gpt-sovits/GPT_SoVITS/pretrained_models -> models/gpt_sovits",
            "apps/gpt-sovits/runtime -> runtime312",
        ],
        "total_files": files,
        "total_bytes": total,
        "fallback": "runtime312\\python.exe webui.py",
    }
    out = DIST_ROOT / "BUILD_MANIFEST.json"
    out.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[manifest] {out}（{files} 文件 / {total / 1e9:.2f} GB）")


def make_zip() -> None:
    """store 模式打包（大模型不压缩提速）；junction 不可能存在于 dist。"""
    # 显式拼接（2026-11-27 审查修复 #7）：with_suffix 会把版本号尾巴
    # （"…-1.0.0-portable" 的 ".0-portable"）整体当后缀替换掉。
    zpath = DIST_ROOT.parent / (DIST_ROOT.name + ".zip")
    print(f"[zip] {zpath}（STORE 模式）")
    with zipfile.ZipFile(zpath, "w", compression=zipfile.ZIP_STORED,
                         allowZip64=True) as zf:
        for root, _, fns in os.walk(DIST_ROOT):
            for fn in fns:
                p = Path(root) / fn
                zf.write(p, p.relative_to(DIST_ROOT.parent))
    print(f"[zip][OK] {zpath.stat().st_size / 1e9:.2f} GB")


def main():
    t0 = time.time()
    if not SKIP_BUILD:
        rc = subprocess.run([sys.executable, str(REPO / "Scripts" / "build_frontend.py")]).returncode
        if rc != 0:
            print("[build_dist][FAIL] 前端构建失败")
            sys.exit(rc)
    assemble()
    if MAKE_ZIP:
        make_zip()
    print(f"[build_dist][DONE] 总耗时 {(time.time() - t0) / 60:.1f} 分钟")


if __name__ == "__main__":
    main()
