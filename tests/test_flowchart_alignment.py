# -*- coding: utf-8 -*-
"""图-码对齐断言（/autoplan main-20261026-153911 T3，终门批准实施）。

防三类回归（义务块：tests/test_flowchart_alignment.py，Eng SC-1/native F2/F3/F4）：
1. 播放分流拓扑歧义（AudioList 双出边 / SystemPlay 双入边 = 双声误读，C1）；
2. 图锚点失效（图内引用的代码符号已不存在 = 识别中枢式重构后图失去追踪力，spec R1 I-6）；
3. 图头元数据丢失（autoplan restore 回滚曾静默丢掉图头纪律行，native Eng F2）。

口径（ceo 义务块统一约定）：符号名为主、行号为辅——本文件只按符号名/文本片段
断言，禁止按行号断言（行号随重构过期）。
"""
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
FLOWCHART = REPO / "docs" / "flowchartTD.md"


def _graph_text() -> str:
    return FLOWCHART.read_text(encoding="utf-8")


def _mermaid_text() -> str:
    """图内文本（```mermaid 代码块）——锚点/结构断言的扫描范围。

    Review record 义务块与审计账里的 file:line 引用属审查记录，不在图锚点校验范围。
    """
    m = re.search(r"```mermaid\n(.*?)```", _graph_text(), re.S)
    assert m, "flowchartTD.md 中找不到 mermaid 代码块"
    return m.group(1)


class TestPlaybackTopology:
    """C1 结构断言：图必须如实表达互斥三分支播放分流（非双路并行）。"""

    def test_audio_list_single_outgoing(self):
        """AudioList 出边恰 1 条（至 AvatarAI）——歧义直连边不得回归。"""
        edges = re.findall(r"^\s*AudioList\s*-->", _mermaid_text(), re.M)
        assert len(edges) == 1, (
            f"AudioList 出边应为 1 条（播放分流互斥：EDTalk/PLAYER_KEYS/pygame 三分支，"
            f"playback_manager.only_play_audio），实际 {len(edges)} 条——歧义直连边疑似回归"
        )

    def test_system_play_single_incoming(self):
        """SystemPlay 入边恰 1 条（来自 AvatarAI 关闭分支，可带边标签）。"""
        edges = re.findall(
            r"^\s*\S+\s*-->\s*(?:\|[^|]*\|)?\s*SystemPlay", _mermaid_text(), re.M
        )
        assert len(edges) == 1, (
            f"SystemPlay 入边应为 1 条（仅 AvatarAI 关闭分支），实际 {len(edges)} 条：{edges}"
        )


class TestGraphAnchors:
    """锚点 lint：图内引用的每个 .py 文件必须在登记表中，且其关键符号在源码存在。

    新增图锚点时同步扩展 ANCHOR_REGISTRY——未登记锚点一律红（防未验证引用混入）。
    """

    # 图内文件名（basename）-> (仓库相对路径, [必须存在的符号])
    ANCHOR_REGISTRY = {
        "playback_manager.py": (
            "utils/audio/playback_manager.py",
            ["is_edtalk_active", "_captions_push", "PLAYER_KEYS",
             "_play_backing_local", "backing_path", "push_full",
             "_CHOREO_MUTE_MAX_WAIT"],
        ),
        "keyword_hub.py": (
            "utils/keyword_hub.py",
            ["on_keyword", "on_gift"],
        ),
        "feature_handler.py": (
            "utils/handlers/feature_handler.py",
            ["on_song"],
        ),
        "flowchart-detail.md": None,  # 文档引用，非代码锚点
    }

    def test_all_py_anchors_registered(self):
        """图内每个 `xxx.py:行号` 引用都必须在登记表中。"""
        refs = set(re.findall(r"([\w\-]+\.py):\d+", _mermaid_text()))
        unknown = refs - set(self.ANCHOR_REGISTRY)
        assert not unknown, (
            f"图内存在未登记的代码锚点 {sorted(unknown)}——"
            f"新增锚点须先经批次审查并扩展 TestGraphAnchors.ANCHOR_REGISTRY"
        )

    def test_anchor_symbols_exist(self):
        """登记表内每个代码锚点的关键符号必须仍在源码中。"""
        for basename, entry in self.ANCHOR_REGISTRY.items():
            if entry is None:
                continue
            relpath, symbols = entry
            src_path = REPO / relpath
            assert src_path.is_file(), f"图锚点文件不存在：{relpath}"
            src = src_path.read_text(encoding="utf-8", errors="replace")
            missing = [s for s in symbols if s not in src]
            assert not missing, (
                f"图锚点失效：{relpath} 中找不到符号 {missing}"
                f"——图注记已无法按符号追踪代码（识别中枢式重构后须同步修图）"
            )


class TestGraphHeader:
    """C7 图头断言：元数据三行在位（restore 回滚曾静默丢失图头，native Eng F2）。"""

    def test_version_line_present(self):
        assert "v2.1" in _graph_text(), "图头版本行（v2.1）缺失——疑 restore 回滚，须随批次重落盘"

    def test_discipline_line_present(self):
        assert "改链必改图" in _graph_text(), "图头纪律行（改链必改图）缺失——疑 restore 回滚，须随批次重落盘"
