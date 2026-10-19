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
import subprocess
import sys

AUTO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APPS_ROOT = os.path.join(AUTO_ROOT, "apps")

# 各项目复制清单：(源相对路径, 目标相对路径, 跳过-大文件标记, 说明, 排除子路径)
# 排除子路径=相对该 item 源目录的精确相对路径（逐段匹配，不用 basename，
# 防止 tools/asr/models 的 "models" 误伤 tools/AP_BWE_main/models——S-1 实测教训）
# 大文件=权重/底模/数据（正式打包需要，--skip-big 跳过）
PROJECTS = {
    "gpt-sovits": {
        "source_root": r"D:\AI\GPT-SoVITS",
        "items": [
            ("api_v2.py", "api_v2.py", False, "TTS API 服务入口（backend=api 兜底模式可用）", []),
            ("GPT_SoVITS", "GPT_SoVITS", True, "核心包全量（skip-big 排除 pretrained_models/G2PWModel）",
             ["pretrained_models", "text/G2PWModel"]),
            ("config.py", "config.py", False, "上游配置", []),
            ("多音字.txt", "多音字.txt", False, "中文多音字词典（g2pw pyd 按 cwd 相对路径读取，缺失=文本前端 Errno 2 风暴+bert 特征空 torch.cat 炸，2026-10-16 训练实测）", []),
            ("念法纠正.txt", "念法纠正.txt", False, "念法纠正词典（文本前端同款 cwd 相对依赖）", []),
            ("tools", "tools", True, "训练/ASR 工具链（slice_audio/asr/filters/AP_BWE_main；skip-big 排除模型资产）",
             ["asr/models", "uvr5/uvr5_weights"]),
        ],
        # S-1 资产链接清单：(源相对路径, 说明)——数 GB 模型资产不复制，
        # 以 NTFS junction 指向源树（上游相对路径语义完整保留，零拷贝）。
        # 源缺失时 warning 跳过（安装包场景由安装器按 P1-5 机制放置）。
        "link_items": [
            ("GPT_SoVITS/pretrained_models", "训练/推理底模（s1v3/s2Gv2ProPlus/s2Dv2ProPlus/SV/bert/hubert）"),
            ("GPT_SoVITS/text/G2PWModel", "中文 G2PW 音素模型"),
            ("tools/asr/models", "FunASR paraformer/vad/punc 本地模型"),
        ],
    },
    "rvc": {
        "source_root": r"D:\AI\RVC20240604Nvidia",
        "items": [
            ("infer", "infer", False, "推理模块（rtrvc 实时推理类/训练脚本/torch_compat 兼容层）", []),
            ("configs", "configs", False, "配置（v1/40k.json 训练模板等）", []),
            ("tools", "tools", False, "工具（rvc_for_realtime 旧版保留=变声回退路径/torchgate）", []),
            (".env", ".env", False, "上游 env 约定（weight_root/index_root/outside_index_root 三键，S-1 计划明确复制）", []),
            ("ffmpeg.exe", "ffmpeg.exe", False, "上游 PATH 前置依赖的 ffmpeg（infer/lib/audio.py 调用）", []),
            ("assets/weights", "assets/weights", True, "用户声音模型（P1-4 已迁 models/rvc/，此处随包可选）", []),
            ("assets/pretrained", "assets/pretrained", True, "v1 底模（12 文件，get_pretrained_models v1 分支）", []),
            ("assets/indices", "assets/indices", True, "检索索引", []),
        ],
        # S-1（/autoplan 2026-10-14）：big 资产 junction——v2 底模/hubert/rmvpe/mute
        # 训练集。注意 v2+40k 实际用 assets/pretrained_v2/f0G40k.pth+f0D40k.pth
        #（现状 items 漏 pretrained_v2 是缺陷，本批修正为链接）；logs/mute 为
        # filelist 的 2 条 mute 行依赖。
        "link_items": [
            ("assets/pretrained_v2", "v2 底模（f0G40k/f0D40k 等训练底模）"),
            ("assets/hubert", "HuBERT 特征提取模型（hubert_base.pt）"),
            ("assets/rmvpe", "RMVPE 音高提取模型（rmvpe.pt）"),
            ("logs/mute", "预置静音训练集（filelist mute 行依赖）"),
        ],
        "skip_big_subpaths": [],
    },
    "aicovergen": {
        "source_root": r"D:\AI\AICoverGen",
        "items": [
            ("src", "src", False, "主流水线（mdx/rvc/infer_pack）", []),
            ("mdxnet_models", "mdxnet_models", True, "MDX 分离模型（按需下载亦可）", []),
            ("rvc_models", "rvc_models", True, "RVC 模型目录（翻唱共享通道落点，C-2 基根）", []),
        ],
        "skip_big_subpaths": [],
    },
}


def _exclude_ignore(src_root_of_item: str, exclude_rels):
    """精确相对路径排除（不用 basename——S-1 教训：models 误伤 AP_BWE_main/models）。"""
    excl = {os.path.normpath(x).replace("/", os.sep).lower() for x in exclude_rels or []}

    def _ig(directory, entries):
        rel_base = os.path.relpath(directory, src_root_of_item)
        out = []
        for e in entries:
            rel = os.path.normpath(os.path.join(rel_base, e)).lower()
            if rel in excl:
                out.append(e)
        return out

    return _ig


def copy_item(src_root: str, project: str, rel_src: str, rel_dst: str,
              skip_big: bool, big_mark: bool, note: str = "", exclude_subdirs=None):
    """复制单项。skip_big+big_mark=整项跳过；exclude_subdirs=相对该 item 源的精确排除。"""
    src = os.path.join(src_root, rel_src)
    dst = os.path.join(APPS_ROOT, project, rel_dst)
    if not os.path.exists(src):
        print(f"  [SKIP 缺失] {rel_src}")
        return False
    if skip_big and big_mark and not exclude_subdirs:
        print(f"  [SKIP big] {rel_src}（无排除清单，整项为大文件）")
        return False
    if os.path.isfile(src):
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
    else:
        base = shutil.ignore_patterns("__pycache__", "*.pyc", "*.log", "tmp")
        precise = _exclude_ignore(src, exclude_subdirs) if (skip_big and exclude_subdirs) else None
        ignore = (lambda d, e: set(base(d, e)) | set(precise(d, e))) if precise else base
        shutil.copytree(src, dst, dirs_exist_ok=True, ignore=ignore)
    print(f"  [OK] {rel_src} → {os.path.relpath(dst, AUTO_ROOT)}")
    return True


def link_items(src_root: str, project: str, links) -> None:
    """S-1：为副本创建模型资产 junction（mklink /J），零复制保留上游相对路径语义。"""
    for rel, note in links:
        src = os.path.join(src_root, rel)
        dst = os.path.join(APPS_ROOT, project, rel)
        if os.path.exists(dst):
            print(f"  [LINK 已存在] {rel}")
            continue
        if not os.path.exists(src):
            print(f"  [LINK 缺源跳过] {rel}（{note}）——安装包场景由安装器放置")
            continue
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        try:
            import _winapi
            _winapi.CreateJunction(src, dst)
            print(f"  [LINK OK] {rel} → {src}")
        except OSError as e:
            print(f"  [LINK FAIL] {rel}: {e}")


def copy_project(name: str, skip_big: bool) -> dict:
    spec = PROJECTS[name]
    src_root = spec["source_root"]
    if not os.path.isdir(src_root):
        print(f"[FAIL] 源目录不存在: {src_root}")
        return {"project": name, "ok": False, "files": []}
    print(f"=== {name} ← {src_root} ===")
    copied = []
    for rel_src, rel_dst, big_mark, note, excludes in spec["items"]:
        if copy_item(src_root, name, rel_src, rel_dst, skip_big, big_mark,
                     note=note, exclude_subdirs=excludes):
            copied.append({"src": rel_src, "dst": f"apps/{rel_dst}", "note": note})
    if spec.get("link_items"):
        link_items(src_root, name, spec["link_items"])
        copied.append({"src": "(junction)", "dst": "apps/<link_items>", "note": "模型资产链接"})
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
