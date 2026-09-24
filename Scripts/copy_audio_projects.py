# -*- coding: utf-8 -*-
"""
音频三项目 → AUTOlive apps/ 复制脚本（P1-5，CEO-F6 装后布局）

将 GPT-SoVITS / RVC / AICoverGen 复制为唯一副本进 AUTOlive/apps/（EDTalk 范式），
供安装包分发与装机布局使用。作者本机开发时 paths.gpt_sovits_root 仍可指向
原位置（config 可配）；apps/ 是装后形态。

用法（AUTOlive 根目录）：
    python Scripts\\copy_audio_projects.py --project gpt-sovits [--skip-big]
    python Scripts\\copy_audio_projects.py --project all

跳过大文件：--skip-big 跳过权重/底模（代码先行验证用）；正式打包不带该参数。
"""
import argparse
import hashlib
import json
import os
import shutil
import sys

AUTO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APPS_ROOT = os.path.join(AUTO_ROOT, "apps")

# 各项目复制清单：(源相对路径, 目标相对路径, 跳过-大文件标记)
# 大文件=权重/底模/数据（正式打包需要，--skip-big 跳过）
PROJECTS = {
    "gpt-sovits": {
        "source_root": r"D:\AI\GPT-SoVITS",
        "items": [
            ("api_v2.py", "api_v2.py", False, "TTS API 服务入口（托管目标）"),
            ("GPT_SoVITS", "GPT_SoVITS", True, "核心包（含 pretrained_models 底模，skip-big 时跳过 pretrained_models）"),
            ("config.py", "config.py", False, "上游配置"),
        ],
        "skip_big_subpaths": ["GPT_SoVITS/pretrained_models"],
    },
    "rvc": {
        "source_root": r"D:\AI\RVC20240604Nvidia",
        "items": [
            ("infer", "infer", False, "推理模块"),
            ("configs", "configs", False, "配置"),
            ("tools", "tools", False, "工具（rvc_for_realtime.py 推理类所在）"),
            ("assets/weights", "assets/weights", True, "用户声音模型（P1-4 已迁 models/rvc/，此处随包可选）"),
            ("assets/pretrained", "assets/pretrained", True, "底模（hubert/rmvpe 等）"),
            ("assets/indices", "assets/indices", True, "检索索引"),
        ],
        "skip_big_subpaths": [],
    },
    "aicovergen": {
        "source_root": r"D:\AI\AICoverGen",
        "items": [
            ("src", "src", False, "主流水线（mdx/rvc/infer_pack）"),
            ("mdxnet_models", "mdxnet_models", True, "MDX 分离模型（按需下载亦可）"),
            ("rvc_models", "rvc_models", True, "RVC 模型目录（翻唱用，P3 指向 models/rvc/）"),
        ],
        "skip_big_subpaths": [],
    },
}


def copy_item(src_root: str, project: str, rel_src: str, rel_dst: str,
              skip_big: bool, big_mark: bool, exclude_subdirs=None):
    """复制单项。skip_big+big_mark=整项跳过；exclude_subdirs=目录内排除的子路径
    （如 GPT_SoVITS 包代码要、pretrained_models 底模不要）。"""
    src = os.path.join(src_root, rel_src)
    dst = os.path.join(APPS_ROOT, project, rel_dst)
    if not os.path.exists(src):
        print(f"  [SKIP 缺失] {rel_src}")
        return False
    if skip_big and big_mark and not exclude_subdirs:
        print(f"  [SKIP big] {rel_src}")
        return False
    if os.path.isfile(src):
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
    else:
        ignores = ["__pycache__", "*.pyc", "*.log", "tmp"]
        if skip_big and exclude_subdirs:
            # ignore_patterns 按 basename 匹配：取排除相对路径的末段
            ignores += [os.path.basename(x.rstrip("/\\")) for x in exclude_subdirs]
        shutil.copytree(
            src, dst,
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns(*ignores),
        )
    print(f"  [OK] {rel_src} → {os.path.relpath(dst, AUTO_ROOT)}")
    return True


def copy_project(name: str, skip_big: bool) -> dict:
    spec = PROJECTS[name]
    src_root = spec["source_root"]
    if not os.path.isdir(src_root):
        print(f"[FAIL] 源目录不存在: {src_root}")
        return {"project": name, "ok": False, "files": []}
    print(f"=== {name} ← {src_root} ===")
    copied = []
    for rel_src, rel_dst, big_mark, note in spec["items"]:
        if copy_item(src_root, name, rel_src, rel_dst, skip_big, big_mark,
                     exclude_subdirs=spec.get("skip_big_subpaths") or None):
            copied.append({"src": rel_src, "dst": f"apps/{rel_dst}", "note": note})
    return {"project": name, "ok": True, "files": copied}


def sha256_of(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def gen_manifest() -> str:
    """S-3：逐项体积+SHA-256+许可引用，写 apps/assets-manifest.json。"""
    entries = []
    for dirpath, _, filenames in os.walk(APPS_ROOT):
        if "assets-manifest.json" in filenames:
            continue
        for fn in filenames:
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, APPS_ROOT).replace("\\", "/")
            entries.append({
                "path": f"apps/{rel}",
                "bytes": os.path.getsize(full),
                "sha256": sha256_of(full),
            })
    manifest = {
        "generated_by": "Scripts/copy_audio_projects.py",
        "policy": "S-3：安装器逐项 SHA-256 校验后放行；许可引用见 docs/许可核查清单.md",
        "assets": sorted(entries, key=lambda e: e["path"]),
    }
    out = os.path.join(APPS_ROOT, "assets-manifest.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)
    print(f"[manifest] {len(entries)} 项 → {out}")
    return out


def main():
    ap = argparse.ArgumentParser(description="音频三项目 apps/ 复制 + manifest 生成")
    ap.add_argument("--project", required=True, choices=list(PROJECTS) + ["all"])
    ap.add_argument("--skip-big", action="store_true", help="跳过权重/底模（代码先行验证）")
    ap.add_argument("--manifest", action="store_true", help="复制后生成 assets-manifest.json（S-3）")
    args = ap.parse_args()

    os.makedirs(APPS_ROOT, exist_ok=True)
    targets = list(PROJECTS) if args.project == "all" else [args.project]
    results = [copy_project(name, args.skip_big) for name in targets]
    if args.manifest:
        gen_manifest()
    ok = all(r["ok"] for r in results)
    print("ALL OK" if ok else "PARTIAL/FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
