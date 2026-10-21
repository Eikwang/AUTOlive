# -*- coding: UTF-8 -*-
"""
仓库卫生门禁（/autoplan main-20261021-153719 清理批次 T5 交付，TODOS P2"仓库卫生门禁"）

防再脏机制：拦截 backup/草稿类后缀文件入库。一次性工作副本/快照不应提交——
本地需要副本时放 .gitignore 覆盖的路径或不建文件；历史包袱见 2026-10-21
清理批次（f0aa2976：utils/*.backup 挂起退役、24 死代码对象已删）。

扩展方式：新发现的脏模式（如临时脚本命名、错放目录）在此追加测试函数，
每个函数一条独立断言，失败信息给出处置指引。
"""

import subprocess

BACKUP_SUFFIXES = (".backup", ".bak", ".orig", ".old", ".tmp")


def _tracked_files():
    """git ls-files 全跟踪清单（仅跟踪文件——本地未跟踪垃圾由 .gitignore 管辖）。"""
    r = subprocess.run(
        ["git", "ls-files"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    assert r.returncode == 0, f"git ls-files 失败：{r.stderr}"
    return r.stdout.splitlines()


def test_no_backup_suffix_files_tracked():
    """跟踪文件中不得含 backup/草稿类后缀（.backup/.bak/.orig/.old/.tmp）。

    背景：utils/*.backup 三件曾长期滞留（TODOS 20261021 第 1 条挂起退役），
    da8f3b34 删除批亦清理过同类。新副本一律走 .gitignore 或不落盘。
    """
    offenders = [
        f for f in _tracked_files()
        if f.lower().endswith(BACKUP_SUFFIXES)
    ]
    assert not offenders, (
        f"backup 类文件入库：{offenders}。"
        "处置：删除（一次性快照）或移入 .gitignore 覆盖路径（确需本地保留的工作副本）。"
    )
