# -*- coding: UTF-8 -*-
"""
前端样式与参数渲染守卫（F-1a/F-1b，CEO native F-1/Eng E3 → 2026-10-24 落地）

F-1a 硬编码色值扫描：frontend/ui 全部 .style()/.classes()/.props() 样式串
  禁裸色值（#hex/rgb()/rgba()）——var(--token, #fallback) 回退写法合法
  （CSS 标准，令牌缺失兜底）；Tailwind 调色类（bg-blue-100 等）禁用
  （UI-16 四处暗色不可读根因，hint-callout 令牌类替代）。
  前置事实（2026-10-24 摸底）：裸硬编码 0 处——本守卫锁定该状态。

F-1b 配置参数唯一渲染断言：跨 tab 的 _auto_save 字面量 keys 元组重复检测
  ——同一 config 键多页可写渲染=无意的双开关类 bug 形态（历史三例：字幕
  打印双开关/答谢双最低价/动态模板重复条目）。白名单=有意共享（见
  SHARED_BINDING_WHITELIST）。只读引用（get_nested_value）不属渲染绑定。

历史三修复点的防回退断言在 tests/features/test_ui16_batch.py
（test_web_captions_param_switch_removed / test_thanks_single_lowest_price
 / test_trends_config_layout_and_delete），本文件不重复。
"""
import ast
import io
import os
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
UI_DIR = REPO / "frontend" / "ui"

# Tailwind 调色类前缀（UI-16 根因清单；hint-callout 令牌类为唯一合法形态）
TAILWORLD_PALETTE_PREFIXES = (
    "bg-blue-", "bg-gray-", "bg-red-", "bg-green-", "bg-yellow-",
    "bg-slate-", "bg-indigo-", "bg-purple-", "text-gray-", "text-blue-",
    "text-red-", "text-green-", "border-gray-", "border-blue-",
)

# F-1b 白名单：有意跨 tab 共享的绑定键（新增共享须在此登记理由）
SHARED_BINDING_WHITELIST = {
    # EDTalk 配置跨页共享：visual_body 为主配置页，llm/train_streamer 引用同值
    # （edtalk 整合既有形态；收敛候选——收敛后从白名单移除）
    ("edtalk_realtime", "interpreter_path"),
    ("edtalk_realtime", "port"),
    ("edtalk_realtime", "enable"),
}


def _py_sources():
    for root, dirs, files in os.walk(UI_DIR):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for f in sorted(files):
            if f.endswith(".py"):
                path = Path(root) / f
                yield path, io.open(path, encoding="utf-8").read()


def _style_string_literals(tree: ast.AST):
    """提取 .style()/.classes()/.props() 调用的第一个字符串实参（样式串）。"""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr in ("style", "classes", "props")):
            continue
        for arg in node.args:
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                yield node.lineno, func.attr, arg.value


def _strip_var_fallbacks(style_str: str) -> str:
    """剔除 var(--token, #fallback) 回退段——回退值合法（CSS 标准兜底）。"""
    return re.sub(r"var\([^)]*\)", "", style_str)


class TestF1aNoHardcodedColors:
    """F-1a：样式串裸色值清零（var 回退合法；Tailwind 调色类禁用）。"""

    def test_no_bare_hex_or_rgb_in_style_strings(self):
        offenders = []
        for path, src in _py_sources():
            tree = ast.parse(src, filename=str(path))
            for lineno, attr, style in _style_string_literals(tree):
                cleaned = _strip_var_fallbacks(style)
                if re.search(r"#[0-9a-fA-F]{3,8}\b", cleaned) or \
                   re.search(r"\b(?:rgb|rgba|hsl|hsla)\(", cleaned):
                    offenders.append(f"{path.name}:{lineno} ({attr})")
        assert offenders == [], f"裸色值（var 回退外）：{offenders}"

    def test_no_tailwind_palette_classes(self):
        offenders = []
        for path, src in _py_sources():
            tree = ast.parse(src, filename=str(path))
            for lineno, attr, style in _style_string_literals(tree):
                if attr != "classes":
                    continue
                for token in style.replace("'", " ").split():
                    if token.startswith(TAILWORLD_PALETTE_PREFIXES):
                        offenders.append(f"{path.name}:{lineno} ({token})")
        assert offenders == [], f"Tailwind 调色类：{offenders}"


class TestF1bUniqueBindings:
    """F-1b：跨 tab 渲染绑定键唯一（白名单=有意共享）。"""

    def _collect_bindings(self):
        """ast 提取各 tab 的 _auto_save 字面量 keys 元组 → {keys: {文件名}}。"""
        bindings = {}
        tabs_dir = UI_DIR / "tabs"
        for f in sorted(tabs_dir.glob("*.py")):
            tree = ast.parse(io.open(f, encoding="utf-8").read(), filename=str(f))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                if not (isinstance(node.func, ast.Name) and node.func.id == "_auto_save"):
                    continue
                for arg in node.args:
                    if not isinstance(arg, ast.Tuple):
                        continue
                    if not all(isinstance(e, ast.Constant) and isinstance(e.value, str)
                               for e in arg.elts):
                        continue  # 动态 keys（含 index/变量）无法静态判定，跳过
                    keys = tuple(e.value for e in arg.elts)
                    bindings.setdefault(keys, set()).add(f.name)
        return bindings

    def test_no_cross_tab_duplicate_bindings(self):
        bindings = self._collect_bindings()
        dup = {
            keys: files for keys, files in bindings.items()
            if len(files) > 1 and keys not in SHARED_BINDING_WHITELIST
        }
        assert dup == {}, (
            f"跨 tab 重复渲染绑定（同一 config 键多页可写）：{dup}；"
            f"有意共享请登记 SHARED_BINDING_WHITELIST 并注记理由")

    def test_whitelist_entries_still_shared(self):
        """白名单失效自检：登记项已不再跨文件时提示移除（防白名单腐化）。"""
        bindings = self._collect_bindings()
        stale = [
            keys for keys in SHARED_BINDING_WHITELIST
            if keys in bindings and len(bindings[keys]) <= 1
        ]
        assert stale == [], f"白名单条目已不再跨文件共享，请移除：{stale}"

    def test_historical_fix_points_hold(self):
        """历史三修复点防回退（字幕打印双开关/答谢双最低价/答谢标签）。"""
        wcp = io.open(UI_DIR / "tabs" / "web_captions_printer.py", encoding="utf-8").read()
        assert "ui.switch('启用'" not in wcp  # 字幕打印参数页开关唯一入口=二级菜单
        thanks = io.open(UI_DIR / "tabs" / "thanks.py", encoding="utf-8").read()
        assert thanks.count("ui.input(label='最低答谢礼物价格'") == 1
