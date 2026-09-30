# -*- coding: utf-8 -*-
"""
DanmakuListener → AUTOlive 复制脚本（DanmakuListener 整合计划 G8，类比 copy_edtalk.py）

单命令执行：复制组件核心包与契约 → 生成 MANIFEST（版本戳，Eng 升级三步流程第 1 步）。
升级流程：重跑本脚本（版本戳变化可见）→ python scripts/export_contract.py --check → 冒烟脚本。

用法（AUTOlive 根目录）：
    python Scripts\\copy_danmaku.py
"""
import hashlib
import json
import os
import shutil
import sys
from datetime import datetime

SOURCE_ROOT = r"D:\AI\DanmakuListener"
TARGET_ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "danmaku_listener")
CONTRACT_TARGET = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "danmaku_contract")

# 复制清单（源相对路径, 目标相对路径, 说明）
COPY_ITEMS = [
    ("danmaku_listener", "danmaku_listener", "组件核心包（engines/adapters/bus/push/contract/config/fixtures/managers/persistence/utils）"),
    ("docs/contract", os.path.join("docs", "danmaku_contract"), "契约文档与机读 Schema（export_contract 漂移检查数据源）"),
]
# 复制时排除的目录名/文件名
EXCLUDE_DIRS = {"__pycache__", ".pytest_cache", "cookie", "persistence_data"}
EXCLUDE_FILES = {".gitignore"}


def copy_tree(src: str, dst: str) -> tuple:
    """递归复制目录，返回 (文件数, 总字节)。"""
    n, total = 0, 0
    os.makedirs(dst, exist_ok=True)
    for root, dirs, files in os.walk(src):
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
        rel = os.path.relpath(root, src)
        dst_root = os.path.join(dst, rel) if rel != "." else dst
        os.makedirs(dst_root, exist_ok=True)
        for f in files:
            if f in EXCLUDE_FILES or f.endswith(".pyc"):
                continue
            s, d = os.path.join(root, f), os.path.join(dst_root, f)
            shutil.copy2(s, d)
            n += 1
            total += os.path.getsize(d)
    return n, total


def main() -> int:
    manifest_files = {}
    grand_n = grand_bytes = 0
    for src_rel, dst_rel, note in COPY_ITEMS:
        src = os.path.join(SOURCE_ROOT, src_rel)
        dst = os.path.join(os.path.dirname(TARGET_ROOT), dst_rel) if "/" in dst_rel else os.path.join(TARGET_ROOT, dst_rel) if not dst_rel.startswith("docs") else os.path.join(os.path.dirname(TARGET_ROOT), dst_rel)
        if dst_rel.startswith("docs"):
            dst = os.path.join(os.path.dirname(TARGET_ROOT), dst_rel)
        if not os.path.isdir(src):
            print(f"[FAIL] 源不存在: {src}")
            return 1
        # 组件核心包复制到 TARGET_ROOT 本体；契约文档复制到 docs/danmaku_contract
        if src_rel == "danmaku_listener":
            if os.path.isdir(TARGET_ROOT):
                shutil.rmtree(TARGET_ROOT)
            n, total = copy_tree(src, TARGET_ROOT)
            base = "danmaku_listener"
        else:
            if os.path.isdir(CONTRACT_TARGET):
                shutil.rmtree(CONTRACT_TARGET)
            n, total = copy_tree(src, CONTRACT_TARGET)
            base = dst_rel
        manifest_files[base] = {"files": n, "bytes": total, "note": note}
        grand_n += n
        grand_bytes += total
        print(f"[OK] {src_rel} → {base}：{n} 文件，{total} 字节")

    # 版本戳 MANIFEST（升级可见性：hash 变化即上游有更新）
    hasher = hashlib.sha256()
    for root, dirs, files in os.walk(TARGET_ROOT):
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
        for f in sorted(files):
            if f.endswith(".pyc"):
                continue
            hasher.update(open(os.path.join(root, f), "rb").read())
    for root, dirs, files in os.walk(CONTRACT_TARGET):
        for f in sorted(files):
            hasher.update(open(os.path.join(root, f), "rb").read())

    manifest = {
        "source": SOURCE_ROOT,
        "copied_at": datetime.now().isoformat(timespec="seconds"),
        "content_sha256": hasher.hexdigest(),
        "total_files": grand_n,
        "total_bytes": grand_bytes,
        "items": manifest_files,
        "upgrade_flow": "重跑本脚本 → python -m danmaku_listener contract（版本核对）→ 冒烟脚本",
    }
    out = os.path.join(TARGET_ROOT, "COPY_MANIFEST.json")
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    print(f"[OK] MANIFEST: {out}")
    print(f"[DONE] 共 {grand_n} 文件 / {grand_bytes} 字节，内容戳 {manifest['content_sha256'][:12]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
