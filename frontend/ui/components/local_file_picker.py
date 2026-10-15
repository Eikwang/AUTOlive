# -*- coding: utf-8 -*-
"""
本地文件选择对话框组件

webui 跑在本机（localhost），服务端可直接访问文件系统——素材选择无需经
浏览器上传（GB 级视频走浏览器上传有内存与耗时问题），只需记录路径、由
业务侧在启动时复制。本组件提供"浏览本地目录 + 多选文件"对话框：

- 点击文件夹行进入；顶部按钮：此电脑（盘符列表）/ 上一级
- 点击文件行切换选中（高亮反馈），可跨目录累计；"全选本目录"批量选择
- "确定"提交选中文件的 Path 列表；"取消"返回 None

注意：NiceGUI 3.x 的 ui.table 无 on_row_click（官方旧 file-picker 的行
点击交互在新版不可用），故行交互全部走 ui.list 的 Python 事件链。

用法：
    picker = LocalFilePicker(start_folder=None, multiple=True)
    result = await picker   # list[Path] 或 None（取消）
"""
import os
import string
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from nicegui import ui

VIDEO_EXTS = (".mp4", ".avi", ".mov", ".mkv", ".webm")
_MAX_ROWS = 2000  # 大目录保护：超过则截断并提示（避免一次性渲染数千行 DOM）

_ITEM_STYLE = "width:100%;cursor:pointer;{bg}"
_ITEM_SELECTED_BG = "background:var(--bg-selected);"


def list_drives() -> List[str]:
    """Windows 盘符探测（A-Z 中真实存在的盘）。"""
    return [f"{c}:\\" for c in string.ascii_uppercase if os.path.exists(f"{c}:\\")]


def format_size(n: float) -> str:
    """字节数 → 人类可读（B/KB/MB/GB/TB）。"""
    if n < 1024:
        return f"{n:.0f} B"
    for unit in ("KB", "MB", "GB"):
        n /= 1024
        if n < 1024:
            return f"{n:.1f} {unit}"
    return f"{n / 1024:.1f} TB"


def scan_rows(
    folder: Optional[Path],
    exts: Tuple[str, ...] = VIDEO_EXTS,
    *,
    show_hidden: bool = False,
    drives: Optional[List[str]] = None,
    max_rows: int = _MAX_ROWS,
) -> Tuple[List[Dict], bool]:
    """扫描目录构造行数据（纯函数，可独立测试）。

    folder=None 表示"此电脑"页（列出盘符）。仅显示目录与扩展名命中
    exts 的文件。返回 (rows, truncated)：目录优先、名称不区分大小写
    升序；超过 max_rows 截断并置 truncated=True。
    """
    if folder is None:
        drives = drives if drives is not None else list_drives()
        rows = [
            {"name": d, "path": d, "icon": "storage", "is_dir": True, "size_text": "本地磁盘"}
            for d in drives
        ]
        return rows, False
    rows: List[Dict] = []
    try:
        entries = sorted(folder.iterdir(), key=lambda p: p.name.lower())
    except OSError:
        return [], False
    for f in entries:
        if not show_hidden and f.name.startswith("."):
            continue
        try:
            is_dir = f.is_dir()
        except OSError:
            continue
        if not is_dir and f.suffix.lower() not in exts:
            continue
        size_text = ""
        if not is_dir:
            try:
                size_text = format_size(f.stat().st_size)
            except OSError:
                size_text = "?"
        rows.append({
            "name": f.name,
            "path": str(f),
            "icon": "folder" if is_dir else "movie",
            "is_dir": is_dir,
            "size_text": size_text,
        })
    rows.sort(key=lambda r: (not r["is_dir"], r["name"].lower()))
    truncated = len(rows) > max_rows
    return rows[:max_rows], truncated


class LocalFilePicker(ui.dialog):
    """本地文件选择对话框（用法见模块 docstring）。"""

    def __init__(
        self,
        start_folder: Optional[str] = None,
        *,
        multiple: bool = True,
        file_exts: Tuple[str, ...] = VIDEO_EXTS,
        title: str = "选择文件",
    ) -> None:
        super().__init__()
        self.folder: Optional[Path] = Path(start_folder).resolve() if start_folder else None
        self.multiple = multiple
        self.file_exts = tuple(e.lower() for e in file_exts)
        self.selected: Dict[str, Dict] = {}  # path -> row（跨目录累计）
        self._rows: List[Dict] = []
        self._items: Dict[str, ui.item] = {}  # path -> 当前行元素（局部高亮更新用）
        self.props("full-width")
        with self, ui.card().style("width:100%;max-width:780px"):
            with ui.row().style("width:100%;align-items:center;gap:8px"):
                ui.label(title).style(
                    "font-size: var(--font-size-card-title); font-weight: var(--font-weight-title); "
                    "color: var(--text-title)")
                ui.space()
                ui.button("此电脑", icon="computer", on_click=self._go_computer).props("flat dense").tooltip("回到盘符列表")
                ui.button(icon="arrow_upward", on_click=self._go_up).props("flat dense").tooltip("上一级")
            self.path_label = ui.label("").style(
                "width:100%;word-break:break-all;font-family:var(--font-family-mono, monospace);"
                "font-size: var(--font-size-small);color: var(--text-secondary)")
            self.hint_label = ui.label("").style(
                "width:100%;color: var(--color-warning);font-size: var(--font-size-small);display:none")
            self.list_container = ui.scroll_area().style(
                "height:380px;border:1px solid var(--border-color);border-radius:var(--radius-md)")
            with ui.row().style("width:100%;align-items:center;justify-content:space-between"):
                self.sel_label = ui.label("已选 0 个文件").style(
                    "color: var(--text-secondary); font-size: var(--font-size-small)")
                with ui.row():
                    ui.button("全选本目录", on_click=self._select_dir).props("flat").tooltip("选中当前目录下全部视频文件")
                    ui.button("清空已选", on_click=self._clear_selected).props("flat")
                    ui.button("取消", on_click=lambda: self.submit(None)).props("flat")
                    ui.button("确定", on_click=self._submit)
        self._refresh()

    # ---------- 导航 ----------
    def _go_computer(self):
        self.folder = None
        self._refresh()

    def _go_up(self):
        if self.folder is None:
            return
        parent = self.folder.parent
        # 盘根再上一级 = 此电脑
        self.folder = None if parent == self.folder else parent
        self._refresh()

    def _enter(self, path: str):
        self.folder = Path(path)
        self._refresh()

    # ---------- 渲染 ----------
    def _refresh(self):
        self._rows, truncated = scan_rows(self.folder, self.file_exts)
        self._items = {}
        self.path_label.set_text(str(self.folder) if self.folder else "此电脑")
        self.hint_label.style("display:block" if truncated else "display:none")
        if truncated:
            self.hint_label.set_text(f"目录项过多，仅显示前 {_MAX_ROWS} 项（可逐级进入子目录）")
        self.list_container.clear()
        with self.list_container:
            with ui.list().props("dense separator").style("width:100%"):
                for row in self._rows:
                    self._build_item(row)
        self._update_label()

    def _build_item(self, row: Dict):
        sel = row["path"] in self.selected
        item = ui.item(on_click=lambda _, r=row: self._on_item(r)).style(
            _ITEM_STYLE.format(bg=_ITEM_SELECTED_BG if sel else ""))
        self._items[row["path"]] = item
        with item:
            with ui.item_section().props("avatar"):
                ui.icon(row["icon"]).style(
                    "color: var(--color-primary)" if row["is_dir"] else "color: var(--text-hint)")
            with ui.item_section():
                ui.item_label(row["name"]).style(
                    "word-break:break-all" + (";font-weight:var(--font-weight-emphasis)" if row["is_dir"] else ""))
            if not row["is_dir"]:
                with ui.item_section().props("side"):
                    ui.item_label(row["size_text"]).style(
                        "color: var(--text-secondary); font-size: var(--font-size-small)")

    # ---------- 选择 ----------
    def _on_item(self, row: Dict):
        if row["is_dir"]:
            self._enter(row["path"])
            return
        path = row["path"]
        if path in self.selected:
            del self.selected[path]
            bg = ""
        elif self.multiple:
            self.selected[path] = row
            bg = _ITEM_SELECTED_BG
        else:
            self.selected = {path: row}
            self._restyle_all()
            bg = _ITEM_SELECTED_BG
        el = self._items.get(path)
        if el is not None:
            el.style(_ITEM_STYLE.format(bg=bg))
        self._update_label()

    def _restyle_all(self):
        """按 self.selected 重刷当前所有行的高亮态（单选互斥/清空用）。"""
        for path, el in self._items.items():
            row = self._rows_by_path().get(path)
            sel = path in self.selected and row is not None and not row["is_dir"]
            el.style(_ITEM_STYLE.format(bg=_ITEM_SELECTED_BG if sel else ""))

    def _rows_by_path(self) -> Dict[str, Dict]:
        return {r["path"]: r for r in self._rows if not r["is_dir"]}

    def _select_dir(self):
        for row in self._rows:
            if not row["is_dir"]:
                self.selected[row["path"]] = row
        self._restyle_all()
        self._update_label()

    def _clear_selected(self):
        self.selected.clear()
        self._restyle_all()
        self._update_label()

    def _update_label(self):
        n = len(self.selected)
        self.sel_label.set_text(f"已选 {n} 个文件" if n else "尚未选择文件（点击文件行选中，可跨目录累计）")

    # ---------- 提交 ----------
    def _submit(self):
        if not self.selected:
            ui.notify(position="top", type="warning", message="尚未选择任何文件")
            return
        self.submit([Path(p) for p in self.selected])
