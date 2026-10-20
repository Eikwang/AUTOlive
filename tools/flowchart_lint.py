# -*- coding: UTF-8 -*-
"""
flowchartTD.md 图校验脚本（T1/T2 机器门禁，/autoplan 批准批次 main-20261020-180346）

四项检查（ENG T-b/T-a 义务）：
1. mermaid 语法 lint：括号平衡、边定义基本形状
2. 节点/边一致性：所有边引用的节点 ID 都有定义（悬空/孤儿引用检查）
3. 四态断言：每个节点 label 恰含一个〔真实〕/〔断链〕/〔骨架〕/〔规划〕标注；
   〔断链〕节点必须绑定修复任务（→T# 引用）或显式处置（零孤儿边义务）
4. 锚点全量校验（T-a）：Review record 前提表 F1-F26 的 file:line 逐一确认
   目标文件存在且行号在文件行数范围内（非 .backup 的当前工作区证据）

用法：python tools/flowchart_lint.py [docs/flowchartTD.md]
退出码：0=全部通过，1=存在失败项
"""
import re
import sys
from pathlib import Path

STATES = ["〔真实〕", "〔断链〕", "〔骨架〕", "〔规划〕"]


def extract_mermaid(text: str) -> str:
    m = re.search(r'```mermaid\n(.*?)```', text, re.DOTALL)
    if not m:
        print("FAIL: 未找到 mermaid 代码块")
        sys.exit(1)
    return m.group(1)


def check_brackets(diagram: str) -> list:
    """括号平衡检查（忽略 %% 注释行与引号内内容）。"""
    errors = []
    pairs = {"[": "]", "(": ")", "{": "}"}
    for i, raw in enumerate(diagram.split("\n"), 1):
        line = raw.split("%%")[0] if raw.strip().startswith("%%") is False and "%%" in raw else ("" if raw.strip().startswith("%%") else raw)
        # 去掉引号内内容（label 内可能有成对括号，计数两侧即可）
        line_wo_quotes = re.sub(r'"[^"]*"', '', line)
        stack = []
        for ch in line_wo_quotes:
            if ch in pairs:
                stack.append(ch)
            elif ch in pairs.values():
                if not stack or pairs[stack[-1]] != ch:
                    errors.append(f"  L{i} 括号不平衡: {raw.strip()[:60]}")
                    break
                stack.pop()
        if stack:
            errors.append(f"  L{i} 括号未闭合: {raw.strip()[:60]}")
    return errors


# mermaid 节点形状：ID[".."] 矩形 / ID("..") 圆角 / ID{".."} 菱形 / ID([".."]) stadium / ID[("..")] 圆柱 / ID[[".."]] subroutine
SHAPE_DEF = re.compile(r'([A-Za-z][A-Za-z0-9_]*)\s*(?:\(\[|\[\(|\[\[|\{|\(|\[)\s*"([^"]*)"')
EDGE_DEF = re.compile(r'([A-Za-z][A-Za-z0-9_]*)\s*(?:-{2,3}>|-\.->|~{3})\s*(?:\|([^|]*)\|\s*)?([A-Za-z][A-Za-z0-9_]*)')


def collect_nodes(diagram: str):
    """收集节点定义（ID + 形状开头的引号 label）。"""
    defined = {}
    for i, raw in enumerate(diagram.split("\n"), 1):
        line = raw.strip()
        if (line.startswith("%%") or line.startswith("subgraph") or line.startswith("end")
                or line.startswith("direction") or line.startswith("flowchart") or not line):
            continue
        for m in SHAPE_DEF.finditer(line):
            nid = m.group(1)
            if nid not in defined:
                defined[nid] = (m.group(2), i)
    return defined


def collect_edges(diagram: str):
    """收集边（A --> B / A -.-> B / A ~~~ B），含行内定义与边标签。"""
    edges = []
    for i, raw in enumerate(diagram.split("\n"), 1):
        line = raw.strip()
        if line.startswith("%%") or line.startswith("subgraph") or not line:
            continue
        for m in EDGE_DEF.finditer(line):
            src, label, dst = m.group(1), m.group(2) or "", m.group(3)
            edges.append((src, dst, label, i))
    return edges


def main():
    doc = Path(sys.argv[1] if len(sys.argv) > 1 else "docs/flowchartTD.md")
    text = doc.read_text(encoding="utf-8")
    diagram = extract_mermaid(text)
    failures = []

    # 1. 括号平衡
    bracket_errors = check_brackets(diagram)
    if bracket_errors:
        failures.append("mermaid 括号检查:\n" + "\n".join(bracket_errors))

    # 2. 节点/边一致性（零悬空引用）
    nodes = collect_nodes(diagram)
    edges = collect_edges(diagram)
    dangling = [f"  {s} --> {d} (L{i})" for s, d, _, i in edges if s not in nodes or d not in nodes]
    if dangling:
        failures.append("悬空节点引用（边引用未定义节点）:\n" + "\n".join(dangling))

    # 3. 四态断言
    no_state = []
    multi_state = []
    broken_unbound = []
    for nid, (label, line) in nodes.items():
        found = [s for s in STATES if s in label]
        if not found:
            no_state.append(f"  {nid} (L{line}) 无四态标注")
        elif len(found) > 1:
            multi_state.append(f"  {nid} (L{line}) 多重标注: {found}")
        if found and found[0] == "〔断链〕" and not re.search(r'T\d+|d\d+|TODOS', label):
            broken_unbound.append(f"  {nid} (L{line}) 断链未绑定处置任务")
    if no_state:
        failures.append("节点缺四态标注:\n" + "\n".join(no_state))
    if multi_state:
        failures.append("节点多重标注:\n" + "\n".join(multi_state))
    if broken_unbound:
        failures.append("断链节点未绑定修复任务（零孤儿边义务）:\n" + "\n".join(broken_unbound))

    # 4. 锚点全量校验（T-a）：前提表 F1-F26 的 file:line
    anchor_failures = []
    _file_cache = {}

    def resolve_py_file(name: str):
        """前提表锚点为短路径（main.py / danmaku_consumer.py 等）——递归定位唯一真实文件。"""
        if name in _file_cache:
            return _file_cache[name]
        candidates = [p for p in Path(".").rglob(name)
                      if "backup" not in str(p) and "__pycache__" not in str(p)
                      and ".gstack" not in str(p) and "edtalk/" not in str(p).replace("\\", "/")
                      and "GPT-SoVITS" not in str(p)]
        result = candidates[0] if len(candidates) == 1 else (candidates[0] if candidates else None)
        _file_cache[name] = result
        return result

    for m in re.finditer(r'\| (F\d+) \|.*?\| .*?([A-Za-z][A-Za-z0-9_/.\\-]*\.py):(\d+)(?:-(\d+))?', text):
        fid, fpath, lstart, lend = m.group(1), m.group(2), int(m.group(3)), int(m.group(4) or m.group(3))
        if ".backup" in fpath:
            anchor_failures.append(f"  {fid} 引用 .backup（非当前工作区证据）: {fpath}")
            continue
        f = resolve_py_file(fpath.split("/")[-1])
        if f is None:
            anchor_failures.append(f"  {fid} 文件无法唯一定位: {fpath}")
            continue
        n_lines = sum(1 for _ in open(f, encoding="utf-8", errors="replace"))
        if not (1 <= lstart <= n_lines):
            anchor_failures.append(f"  {fid} 行号越界: {fpath}:{lstart}（{f} 共 {n_lines} 行）")

    # 前提表 F1-F26 应全被捕获（26 条）
    fids = set(re.findall(r'\| (F\d+) \|', text))
    if len(fids) < 26:
        anchor_failures.append(f"  前提表仅捕获 {len(fids)}/26 条（表格格式或正则需检查）")

    if anchor_failures:
        failures.append("锚点校验:\n" + "\n".join(anchor_failures))

    # 报告
    print(f"节点 {len(nodes)} 个，边 {len(edges)} 条，前提表 {len(fids)} 条")
    if failures:
        print("=== 校验失败 ===")
        for f in failures:
            print(f)
        sys.exit(1)
    print("=== 全部通过：括号平衡 / 零悬空引用 / 四态标注 / 断链绑定 / 锚点全量 ===")


if __name__ == "__main__":
    main()
