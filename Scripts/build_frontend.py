# -*- coding: UTF-8 -*-
"""Nuitka 前端构建脚本（发行改造阶段 D，2026-11-22）。

职责：把前端 WebUI（入口 webui.py）编译为 standalone AUTOlive.exe。
- 只编译前端：主系统 main.py 与全部 AI 子服务继续由发行包内
  runtime312\\python.exe 运行（阶段 C 已改造解释器链，frozen 下零改动）
- 防泄漏兜底：构建后解析 compilation-report.xml + 扫描 .dist，
  断言无 torch/openblas 等 GB 级依赖混入，否则构建失败

用法（在仓库根）：
    runtime312\\python.exe Scripts\\build_frontend.py            # 全量构建
    runtime312\\python.exe Scripts\\build_frontend.py --report-only  # 仅校验产物
产物：build_nuitka\\webui.dist\\（AUTOlive.exe + 依赖平铺）
"""
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BUILD_DIR = REPO / "build_nuitka"
DIST_DIR = BUILD_DIR / "webui.dist"
REPORT = BUILD_DIR / "compilation-report.xml"
OUTPUT_NAME = "AUTOlive.exe"

# 轻依赖闭包：动态 import / 数据文件不被静态分析捕获的包，显式收编
# （soundfile/jieba 数据、quickjs 抖音协议、playwright 登录链、audio 链 pyaudio/pydub）
# 2026-11-22 T1 冒烟增量：nicegui.elements 为运行期 importlib 懒加载（ui.tabs 报
# ModuleNotFoundError）；frontend.ui.tabs 33 个 tab 由预热 import_module 动态加载；
# danmaku_listener 惰性导出 + engines.*_login 动态 import；requests 仅 __import__ 动态引用
INCLUDE_PACKAGES = [
    "nicegui",  # 整包收编：elements/functions/ui_run 均为运行期 importlib 懒加载
    "frontend.ui.tabs",
    "danmaku_listener",
    "requests",
    "filelock",  # 懒加载 __getattr__（huggingface_hub 链）
    "quickjs", "pyaudio", "pydub", "websockets", "pypinyin",
    "ahocorasick",  # pip 名 pyahocorasick，模块名 ahocorasick
    "profanity", "schedule", "keyboard",
    "pyautogui", "pygetwindow", "serial", "pygtrans",
    "_soundfile_data",  # libsndfile DLL（soundfile 本体是单模块，数据在顶级包）
]
# 数据文件：nicegui 静态资源（缺则白页）、playwright driver（登录扫码）、
# jieba 词库、_soundfile_data libsndfile DLL
# 2026-11-22 T1 冒烟增量：pypinyin pinyin_dict.json（open(__file__) 相对读）、
# certifi CA 证书（requests 链）、tzdata/zoneinfo（Windows 无系统时区库）
INCLUDE_PACKAGE_DATA = ["nicegui", "playwright", "jieba", "_soundfile_data",
                        "pypinyin", "certifi", "tzdata", "zoneinfo"]

# 重依赖排除：torch/GPT-SoVITS/funasr/faster_whisper/faiss/cv2/librosa/
# argostranslate/transformers/scipy/numba/onnx 仅闭包内函数级引用且 webui
# 运行态不执行 → nofollow 安全（frontend 顶层导入无这些重依赖）
NOFOLLOW = ",".join([
    "torch", "GPT_SoVITS", "funasr", "faster_whisper", "faiss", "cv2",
    "librosa", "argostranslate", "transformers", "scipy", "numba", "onnx",
])

# 防泄漏断言：dist 内不得出现的模块前缀 / DLL 关键字
LEAK_MODULES = ("torch", "gpt_sovits", "funasr", "faster_whisper", "faiss",
                "cv2", "librosa", "transformers", "scipy", "numba", "onnx")
LEAK_DLL_KEYWORDS = ("torch", "openblas", "cud", "mklnn", "fbgemm")

# DLL 依赖补全（2026-11-22 T1 冒烟复盘）：Nuitka 依赖扫描漏收 conda
# Library\\bin 风格 DLL（ffi-8.dll / libbz2.dll / libcrypto-3-x64.dll 等）。
# 本机 PATH 恰有 anaconda3 base → loader 落到 PATH 加载外来 ffi-8.dll →
# 版本不符 → ucrtbase fastfail abort（AUTOlive.exe 启动即崩）；
# 用户机（干净 PATH）则直接 ImportError。构建后扫描 dist 内 PE 导入表，
# 缺失 DLL 从 runtime312 树解析复制进 dist 根。
_DLL_SEARCH_DIRS = ["Library\\bin", "DLLs", "Scripts", ""]
DLL_FIX_ROUNDS = 4


def _pe_dll_imports(path: str) -> list:
    """粗读 PE 文件内的 *.dll 引用名（导入表名以 ASCII 出现于二进制）。"""
    import re
    raw = open(path, "rb").read()
    names = set()
    for m in re.finditer(rb"[A-Za-z0-9_\-\.]+\.dll", raw):
        n = m.group(0).decode(errors="ignore")
        if 5 <= len(n) <= 64:
            names.add(n)
    return sorted(names)


def _runtime_dll_index() -> dict:
    """runtime312 树内全部 DLL：lower 名 → 源路径（同名取首个）。"""
    rt = REPO / "runtime312"
    index = {}
    for root, _, files in os.walk(rt):
        for f in files:
            if f.lower().endswith(".dll") and f.lower() not in index:
                index[f.lower()] = Path(root) / f
    return index


def complete_dll_deps(dist_dir: Path = None) -> list:
    """扫描 dist 全部 pyd/dll 的导入表，把缺失 DLL 从 runtime312 复制进 dist 根。

    返回本次补入的 DLL 名列表。"""
    import shutil
    dist_dir = Path(dist_dir) if dist_dir else DIST_DIR
    if not dist_dir.is_dir():
        return []
    index = _runtime_dll_index()
    copied = []
    for _ in range(DLL_FIX_ROUNDS):  # 新复制进来的 DLL 自身依赖也要补
        dist_files = {f.name.lower() for f in dist_dir.rglob("*") if f.is_file()}
        missing = set()
        for f in list(dist_dir.rglob("*.pyd")) + list(dist_dir.rglob("*.dll")):
            for name in _pe_dll_imports(str(f)):
                low = name.lower()
                if low not in dist_files and low in index:
                    missing.add(low)
        if not missing:
            break
        for low in sorted(missing):
            src = index[low]
            shutil.copy2(src, dist_dir / src.name)
            copied.append(src.name)
            print(f"[dll-fix] 补入 {src.name} <- {src.relative_to(REPO)}")
    if not copied:
        print("[dll-fix] 无缺失，DLL 依赖完整")
    return copied


def verify_dll_closure(dist_dir: Path = None) -> bool:
    """断言 dist 内 pyd/dll 的导入表全部可满足（dist 内或 runtime312 均无者视为系统 DLL）。"""
    dist_dir = Path(dist_dir) if dist_dir else DIST_DIR
    dist_files = {f.name.lower() for f in dist_dir.rglob("*") if f.is_file()}
    index = _runtime_dll_index()
    ok = True
    seen_missing = set()
    for f in list(dist_dir.rglob("*.pyd")) + list(dist_dir.rglob("*.dll")):
        for name in _pe_dll_imports(str(f)):
            low = name.lower()
            if low not in dist_files and low not in index and low not in seen_missing:
                seen_missing.add(low)
                print(f"[dll-verify][WARN] {f.name} 引用 {name}：dist/runtime312 均无（视为系统 DLL）")
    return ok


def nuitka_args(jobs: str = None) -> list:
    args = [
        "--standalone",
        f"--output-dir={BUILD_DIR.name}",
        f"--output-filename={OUTPUT_NAME}",
        "--assume-yes-for-downloads",      # 依赖缺失自动下载（dependency walker 等）
        "--windows-console-mode=force",    # 首版保留控制台（日志可见）
        f"--report={REPORT}",
        f"--nofollow-import-to={NOFOLLOW}",
        "--include-data-dir=ui=ui",        # ui/ 静态数据
        # NiceGUI script mode 重执行入口（frozen 下 sys.argv[0] 重定向目标，
        # 见 webui.py bootstrap 第 8 步）；放 dist 根，与 exe 平级
        "--include-data-file=Scripts/autolive_entry.py=autolive_entry.py",
        # 限流防卡死（2026-11-22 首次构建致整机卡死复盘）：--jobs/--lto 仅在
        # 显式传参时生效；默认满核并行（用户裁决恢复最初高性能方案）。
        *([f"--jobs={jobs}"] if jobs else []),  # 未传则满核并行（Nuitka 默认）
    ]
    args += [f"--include-package={p}" for p in INCLUDE_PACKAGES]
    args += [f"--include-package-data={p}" for p in INCLUDE_PACKAGE_DATA]
    args.append(str(REPO / "webui.py"))
    return args


def ensure_nuitka(python: str) -> None:
    r = subprocess.run([python, "-m", "nuitka", "--version"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        print("[build] nuitka 未安装，正在 pip install nuitka ...")
        subprocess.run([python, "-m", "pip", "install", "nuitka"], check=True)


def run_build(python: str, jobs: str = None) -> int:
    cmd = [python, "-m", "nuitka"] + nuitka_args(jobs)
    print("[build] $ " + " ".join(cmd))
    # 编译时长 20-40 分钟（MSVC 全量），直接透传输出
    return subprocess.run(cmd, cwd=str(REPO)).returncode


def parse_report_modules() -> list:
    """从 compilation-report.xml 提取编译进 dist 的模块名。"""
    import xml.etree.ElementTree as ET
    tree = ET.parse(REPORT)
    names = []
    for el in tree.iter():
        tag = el.tag.rsplit("}", 1)[-1]
        if tag in ("module", "compiled_module", "extension_module"):
            name = el.get("name") or ""
            if name:
                names.append(name)
    return names


def verify_dist() -> bool:
    """构建产物校验：exe 存在、report 可解析、无 torch/openblas GB 级依赖泄漏。"""
    ok = True
    exe = DIST_DIR / OUTPUT_NAME
    if not exe.exists():
        print(f"[verify][FAIL] 产物缺失: {exe}")
        return False

    if REPORT.exists():
        modules = [m.lower() for m in parse_report_modules()]
        leaks = sorted({m.split(".")[0] for m in modules
                        if m.split(".")[0] in LEAK_MODULES})
        if leaks:
            print(f"[verify][FAIL] report 检出泄漏模块: {leaks}")
            ok = False
        else:
            print(f"[verify][OK] report 无泄漏（共 {len(modules)} 模块编译）")
    else:
        print("[verify][WARN] report 缺失，跳过模块级校验")

    # dist 体积兜底：torch/openblas 级 DLL 直接判失败
    for f in DIST_DIR.rglob("*.dll"):
        low = f.name.lower()
        if any(k in low for k in LEAK_DLL_KEYWORDS):
            size_mb = f.stat().st_size / 1e6
            if size_mb > 50:  # 50MB 以上必为重依赖运行时
                print(f"[verify][FAIL] 泄漏 DLL: {f.name} ({size_mb:.0f} MB)")
                ok = False

    total = sum(f.stat().st_size for f in DIST_DIR.rglob("*") if f.is_file())
    print(f"[verify] dist 总体积: {total / 1e9:.2f} GB（预估 0.3-0.6 GB）")
    if total > 2e9:
        print("[verify][FAIL] dist 超 2GB——疑似重依赖泄漏，请人工排查")
        ok = False
    return ok


def main():
    python = sys.executable
    if "--report-only" in sys.argv:
        sys.exit(0 if verify_dist() else 1)
    if "--fix-dlls" in sys.argv:
        complete_dll_deps()
        sys.exit(0)
    jobs = None  # 满核并行（Nuitka 默认）；--jobs=N 显式限流
    for a in sys.argv:
        if a.startswith("--jobs="):
            jobs = a.split("=", 1)[1]
    ensure_nuitka(python)
    rc = run_build(python, jobs)
    if rc != 0:
        print(f"[build][FAIL] nuitka 退出码 {rc}；若 MSVC 环境缺失，"
              f"可加装 Build Tools 或改用 --mingw64 参数重试")
        sys.exit(rc)
    complete_dll_deps()
    sys.exit(0 if verify_dist() else 1)


if __name__ == "__main__":
    main()
