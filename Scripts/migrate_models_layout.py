# -*- coding: UTF-8 -*-
"""一次性迁移脚本：models 统一根布局（发行改造阶段 A2）。

迁移动作（幂等，可中断重跑）：
1. 目录平移（同盘 rename，秒级）：
   models/iic     → models/asr    （SenseVoice + VAD）
   models/mdxnet  → models/uvr5   （MDXNet 人声分离）
   tuber/         → models/tuber  （数字人角色包）
   edtalk/ckpts   → models/edtalk （EDTalk 底模权重；原位留 junction 回指，
                                    EDTalk 训练脚本 "ckpts/xxx.pt" 相对路径不受影响）
2. GPT-SoVITS 底模：外部源 D:\\AI\\GPT-SoVITS\\GPT_SoVITS\\pretrained_models
   （apps 副本内 junction 当前指向）复制到 models/gpt_sovits/（3.07GB，robocopy），
   然后将 junction 重指 models/gpt_sovits。
   注：外部遗留安装不做删除（发行包只依赖 models/gpt_sovits，
   junction 重指后外部树不再被任何代码路径引用）。
3. config 同步：config.json / config.example.json paths 节补注册中心覆盖键
   （空值=默认布局）；config/platforms/talk.json sensevoice 两键
   ./models/iic → ./models/asr。
4. 迁移报告：data/migration_models_layout_report.json（清单 + 校验）。

用法：python Scripts/migrate_models_layout.py [--dry-run]
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
JUNCTION_TAG = 0xA0000003  # mount point / junction

DRY_RUN = "--dry-run" in sys.argv

# 需要平移的目录（repo 相对路径；dst 必须在 models/ 根下）
MOVES = [
    ("models/iic", "models/asr"),
    ("models/mdxnet", "models/uvr5"),
    ("tuber", "models/tuber"),
    ("edtalk/ckpts", "models/edtalk"),
]

# GPT-SoVITS 底模：外部当前源（apps 副本 junction 现指向）→ 项目内物理副本
GSV_EXTERNAL_SRC = Path(r"D:\AI\GPT-SoVITS\GPT_SoVITS\pretrained_models")
GSV_JUNCTION = REPO / "apps" / "gpt-sovits" / "GPT_SoVITS" / "pretrained_models"

# 迁移后校验用关键文件（dst 相对路径 → 抽查文件）
VERIFY_FILES = {
    "models/asr": ["SenseVoiceSmall"],
    "models/uvr5": ["UVR-MDX-NET-Voc_FT.onnx", "model_data.json"],
    "models/rvc": ["wy.pth"],
    "models/edtalk": ["Audio2Lip.pt", "EDTalk.pt", "s3fd"],
    "models/tuber": ["wy"],
    "models/gpt_sovits": ["s1v3.ckpt", "chinese-hubert-base"],
}

# config paths 节新增覆盖键（空值=使用默认布局）
NEW_PATH_KEYS = [
    "models_root", "asr_models", "uvr5_models", "rvc_models",
    "gpt_sovits_models", "edtalk_ckpts", "tuber_root", "song_root",
]


def is_junction(p: Path) -> bool:
    try:
        return (os.lstat(p).st_reparse_tag or 0) == JUNCTION_TAG
    except OSError:
        return False


def junction_target(p: Path) -> str:
    out = subprocess.run(
        ["cmd", "/c", "fsutil", "reparsepoint", "query", str(p)],
        capture_output=True, text=True).stdout
    for line in out.splitlines():
        if "Substitute Name:" in line:
            name = line.split("Substitute Name:", 1)[1].strip()
            # \??\D:\... → D:\...
            return name.replace("\\??\\", "").strip()
    return ""


def run(cmd) -> int:
    print("  $ " + " ".join(cmd))
    if DRY_RUN:
        return 0
    return subprocess.run(cmd).returncode


def make_junction(link: Path, target: Path) -> bool:
    """创建目录 junction（_winapi.CreateJunction，无需 cmd）；返回是否成功。"""
    if link.exists() or is_junction(link):
        print(f"  [junction] 已存在，跳过创建: {link}")
        return True
    print(f"  [junction] {link} -> {target}")
    if DRY_RUN:
        return True
    import _winapi
    try:
        # 签名 CreateJunction(src, dst)：在 dst 创建指向 src 的 junction（src 必须已存在）
        _winapi.CreateJunction(str(target), str(link))
        return True
    except OSError as e:
        print(f"  [ERROR] junction 创建失败: {e}")
        return False


def remove_junction(link: Path) -> None:
    """os.rmdir 对 junction 只删链接不动目标（调用前已确认是 junction）。"""
    if DRY_RUN:
        print(f"  [dry-run] rmdir junction: {link}")
        return
    os.rmdir(link)


def move_dir(src: Path, dst: Path) -> str:
    """返回动作：moved / already-done / both-exist / missing。"""
    src_junction = src.exists() and is_junction(src)
    src_real = src.exists() and not src_junction
    dst_exists = dst.exists()
    if src_real and not dst_exists:
        dst.parent.mkdir(parents=True, exist_ok=True)
        print(f"  [move] {src} -> {dst}")
        if not DRY_RUN:
            shutil.move(str(src), str(dst))
        return "moved"
    if not src_real and dst_exists:
        print(f"  [skip] 已迁移: {dst}（源 {src} 缺失或为链接）")
        return "already-done"
    if src_real and dst_exists:
        print(f"  [ERROR] 源目标同时存在，需人工处理: {src} / {dst}")
        return "both-exist"
    print(f"  [skip] 双缺失: {src} / {dst}")
    return "missing"


def copy_gsv_models() -> str:
    dst = REPO / "models" / "gpt_sovits"
    # 完成标记（2026-11-27 审查修复 #9c）：robocopy 成功后落 marker；副本
    # 非空但 marker 缺失=上次复制被中断（rc>7 或中途强杀），重跑复制
    # （robocopy /E 幂等，已复制文件跳过续传）
    marker = dst / ".gsv_copy_done"
    if dst.exists() and any(dst.iterdir()):
        if marker.exists():
            print(f"  [skip] 底模副本已就位（marker 在）: {dst}")
            return "already-done"
        print(f"  [resume] 副本非空但完成标记缺失（上次复制中断），重跑 robocopy 续传")
        if not GSV_EXTERNAL_SRC.exists():
            print(f"  [ERROR] 外部底模源缺失（续传需源核对）: {GSV_EXTERNAL_SRC}")
            return "missing"
    elif not dst.exists() and not GSV_EXTERNAL_SRC.exists():
        print(f"  [ERROR] 外部底模源缺失: {GSV_EXTERNAL_SRC}")
        return "missing"
    dst.parent.mkdir(parents=True, exist_ok=True)
    print(f"  [copy] {GSV_EXTERNAL_SRC} -> {dst} (robocopy /E，约 3GB)")
    if DRY_RUN:
        return "moved"
    rc = subprocess.run(
        ["robocopy", str(GSV_EXTERNAL_SRC), str(dst),
         "/E", "/NFL", "/NDL", "/NJH", "/NJS", "/NP"],
        capture_output=True, text=True).returncode
    # robocopy 返回码 0-7 均为成功；成功后落完成标记
    if rc <= 7:
        marker.write_text("ok\n", encoding="utf-8")
        return "moved"
    print(f"  [ERROR] robocopy 失败 rc={rc}（中断场景：副本非空无标记，重跑本脚本续传）")
    return "error"


def repoint_gsv_junction() -> str:
    want = str(REPO / "models" / "gpt_sovits")
    if is_junction(GSV_JUNCTION):
        cur = junction_target(GSV_JUNCTION)
        if os.path.normcase(cur) == os.path.normcase(want):
            print(f"  [skip] junction 已指向: {cur}")
            return "already-done"
        print(f"  [repoint] {GSV_JUNCTION}: {cur} -> {want}")
        if not DRY_RUN:
            remove_junction(GSV_JUNCTION)
    elif GSV_JUNCTION.exists():
        print(f"  [ERROR] 非链接（真实目录？）: {GSV_JUNCTION}")
        return "error"
    else:
        # 首跑中断场景：链接已被删但未建成
        print(f"  [rebuild] junction 缺失，补建 -> {want}")
    if not DRY_RUN and not make_junction(GSV_JUNCTION, Path(want)):
        return "error"
    return "moved"


def _load_json(path: Path) -> dict:
    """JSON 读取加固（2026-11-27 审查修复 #9b）：裸 loads 遇损坏/半写文件会
    在 config 已被部分迁移的半程态抛栈——捕获后输出回滚指引再中止，
    不静默继续（避免基于残缺 config 误判「键已存在」跳过补键）。"""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        bak = sorted(REPO.glob(f"{path.name}.bak_*"))
        print(f"  [ERROR] {path} 解析失败（{type(e).__name__}: {e}）——迁移中止")
        if bak:
            print(f"  [回滚指引] 从最近备份恢复后再重跑：copy \"{bak[-1]}\" \"{path}\"")
        else:
            print(f"  [回滚指引] 无自动备份；从编辑器历史/版本库恢复 {path} 后重跑")
        raise SystemExit(1)


def update_configs() -> list:
    changed = []

    def save(path: Path, data: dict):
        print(f"  [config] 更新 {path}")
        changed.append(str(path))
        if not DRY_RUN:
            # 写前备份（2026-11-27 审查修复 #9a）：config.json 不入库
            # （gitignore），写坏不可从版本库恢复——落 config.json.bak_* 副本
            if path.name == "config.json":
                import datetime
                bak = path.with_name(
                    f"{path.name}.bak_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}")
                shutil.copyfile(path, bak)
                print(f"  [backup] {bak}")
            path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8")

    # talk.json sensevoice 两键 ./models/iic → ./models/asr
    talk = REPO / "config" / "platforms" / "talk.json"
    data = _load_json(talk)
    sv = data.get("sensevoice") or {}
    hit = False
    for k in ("asr_model_path", "vad_model_path"):
        v = str(sv.get(k) or "")
        if "models/iic" in v:
            sv[k] = v.replace("models/iic", "models/asr")
            hit = True
            print(f"    sensevoice.{k}: {v} -> {sv[k]}")
    if hit:
        save(talk, data)

    # config.json / config.example.json paths 节补键
    for name in ("config.json", "config.example.json"):
        p = REPO / name
        data = _load_json(p)
        paths = data.setdefault("paths", {})
        add = [k for k in NEW_PATH_KEYS if k not in paths]
        if add:
            for k in add:
                paths[k] = ""  # 空=默认布局
                print(f"    {name}: paths.{k} = \"\"")
            save(p, data)
    return changed


def validate() -> list:
    problems = []
    for rel, checks in VERIFY_FILES.items():
        d = REPO / rel
        if not d.is_dir():
            problems.append(f"目录缺失: {rel}")
            continue
        for c in checks:
            if not (d / c).exists():
                problems.append(f"关键文件缺失: {rel}/{c}")
    # junction 校验
    ckpts = REPO / "edtalk" / "ckpts"
    if not ckpts.exists():
        problems.append(f"edtalk/ckpts 缺失（junction 未创建）")
    elif not is_junction(ckpts):
        problems.append(f"edtalk/ckpts 应为 junction: reparse_tag="
                        f"{hex(getattr(os.lstat(ckpts), 'st_reparse_tag', 0))}")
    elif not (ckpts / "Audio2Lip.pt").exists():
        problems.append("edtalk/ckpts junction 不通（Audio2Lip.pt 不可见）")
    if not is_junction(GSV_JUNCTION):
        problems.append("pretrained_models 应为 junction")
    elif not (GSV_JUNCTION / "s1v3.ckpt").exists():
        problems.append("pretrained_models junction 不通（s1v3.ckpt 不可见）")
    for p in problems:
        print(f"  [FAIL] {p}")
    if not problems:
        print("  [OK] 全部校验通过")
    return problems


def main():
    report = {"time": "", "moves": {}, "gsv_copy": "", "gsv_junction": "",
              "configs_changed": [], "validation_problems": [], "dry_run": DRY_RUN}

    import datetime
    report["time"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    print("== 1/5 GPT-SoVITS 底模复制 -> models/gpt_sovits ==")
    report["gsv_copy"] = copy_gsv_models()

    print("== 2/5 pretrained_models junction 重指 ==")
    report["gsv_junction"] = repoint_gsv_junction()

    print("== 3/5 目录平移 ==")
    for src, dst in MOVES:
        report["moves"][src] = move_dir(REPO / src, REPO / dst)

    print("== 4/5 junction 回指（edtalk/ckpts -> models/edtalk）==")
    # 幂等：只要实体已就位且链接缺失就补建（首跑中断场景）
    if (REPO / "models" / "edtalk").exists() and not is_junction(REPO / "edtalk" / "ckpts"):
        if not make_junction(REPO / "edtalk" / "ckpts", REPO / "models" / "edtalk"):
            report["validation_problems"].append("edtalk/ckpts junction 创建失败")

    print("== 5/5 config 同步 ==")
    report["configs_changed"] = update_configs()

    print("== 校验 ==")
    if DRY_RUN:
        print("  [dry-run] 跳过校验")
    else:
        report["validation_problems"] = validate()

    # 报告落盘（2026-11-27 审查修复 #9d）：dry-run 写 *.dry-run.json，不覆盖
    # 真实迁移报告（dry-run 后真实报告仍可追溯）
    out = REPO / "data" / (
        "migration_models_layout_report.dry-run.json" if DRY_RUN
        else "migration_models_layout_report.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n迁移报告: {out}")
    if report["validation_problems"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
