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
    """播放分流结构断言（v2.2 语义：条目级带标签双路——系统流程/助播流程；
    v2.1 的互斥三分支仍由 playback_manager.routes_to_edtalk 行为守卫）。"""

    def test_audio_list_labeled_dual_route(self):
        """AudioList 出边恰 2 条且均带语义标签（|系统流程|/|助播流程|）——
        无标签歧义直连边不得回归。"""
        # 播放分流边=指向播放节点的带标签边（字幕数据边→Subtitle 不属分流）
        route_edges = re.findall(
            r"^\s*AudioList\s*-->\s*\|([^|]+)\|\s*(AvatarAI|SystemPlay)",
            _mermaid_text(), re.M)
        assert len(route_edges) == 2, (
            f"AudioList 播放分流出边应为 2 条（|系统流程|→AvatarAI + |助播流程|→SystemPlay），"
            f"实际 {len(route_edges)} 条：{route_edges}——疑似歧义直连边回归"
        )
        labels = {label.split("<br>")[0].strip() for label, _ in route_edges}
        assert labels == {"系统流程", "助播流程"}, (
            f"分流边标签应为 系统流程/助播流程 两路，实际 {labels}——语义标签缺失或漂移"
        )

    def test_audio_list_no_unlabeled_route_edges(self):
        """AudioList 出边中指向播放节点（AvatarAI/SystemPlay）的边必须带语义标签——
        无标签歧义直连边（v2.1 C1 病灶）不得以任何形态回归。"""
        all_edges = re.findall(
            r"^\s*AudioList\s*-->\s*(?:\|([^|]*)\|)?\s*(\S+)", _mermaid_text(), re.M)
        assert all_edges, "图中找不到 AudioList 出边"
        for label, target in all_edges:
            if target.startswith(("AvatarAI", "SystemPlay")):
                assert label and label.strip(), (
                    f"AudioList→{target} 无语义标签——歧义直连边（无标签双路）回归"
                )

    def test_system_play_incoming_sources(self):
        """SystemPlay 入边恰 2 条：AvatarAI 关闭分支 + |助播流程| 直连。"""
        edges = re.findall(
            r"^\s*\S+\s*-->\s*(?:\|([^|]*)\|)?\s*SystemPlay", _mermaid_text(), re.M
        )
        assert len(edges) == 2, (
            f"SystemPlay 入边应为 2 条（AvatarAI 关闭 + 助播流程直连），实际 {len(edges)} 条：{edges}"
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
        "choreographer.py": (
            "utils/orchestration/choreographer.py",
            ["_audio_enqueue"],
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
        assert "v2.2" in _graph_text(), "图头版本行（v2.2）缺失——疑 restore 回滚，须随批次重落盘"

    def test_discipline_line_present(self):
        assert "改链必改图" in _graph_text(), "图头纪律行（改链必改图）缺失——疑 restore 回滚，须随批次重落盘"
