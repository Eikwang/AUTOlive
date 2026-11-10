# -*- coding: utf-8 -*-
"""全 tab 模块加载冒烟守卫（2026-11-10 用户实报：画面设置页"加载配置失败:
name 'os' is not defined"）

背景：页面构建（render_config_page）是同步执行路径——tab 模块的 NameError/
ImportError 等在 socketio handler 内抛出，前端只显示"加载配置失败"兜底页，
全量测试不含 NiceGUI 页面构建（需 client 上下文）抓不住。本守卫在纯 import
层面把全部 tab/布局模块拉一遍（模块顶层执行=import 缺失/语法错/顶层异常
现形），启动期预热线程（frontend/main._prewarm_tab_modules）的同源覆盖。

边界：只验证模块可加载（顶层执行），不构建页面（需 NiceGUI client 上下文）；
页面内函数体的运行时错误仍由功能测试覆盖。
"""
import importlib
import os

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _tab_modules():
    pkg_dir = os.path.join(ROOT, "frontend", "ui", "tabs")
    mods = []
    for fn in sorted(os.listdir(pkg_dir)):
        if fn.endswith(".py") and not fn.startswith("_"):
            mods.append(f"frontend.ui.tabs.{fn[:-3]}")
    return mods


def test_all_tab_modules_importable():
    """全部 tab 模块可加载（页面构建前的最后一道 import 关）。"""
    failures = []
    for mod in _tab_modules():
        try:
            importlib.import_module(mod)
        except Exception as e:
            failures.append(f"{mod}: {type(e).__name__}: {e}")
    assert not failures, "tab 模块加载失败（进页即'加载配置失败'）:\n" + "\n".join(failures)


def test_layout_module_importable():
    """布局模块可加载（页面构建调度方）。"""
    importlib.import_module("frontend.ui.layout")


def test_prewarm_covers_all_tab_modules():
    """启动期预热线程的扫描口径与本守卫一致（frontend.main 扫 tabs 包全部 .py）。"""
    src = open(os.path.join(ROOT, "frontend", "main.py"), encoding="utf-8").read()
    assert "_prewarm_tab_modules" in src
    assert 'os.listdir(pkg_dir)' in src, "预热扫描口径应覆盖 tabs 包全部 .py"
    assert 'fn.endswith(".py") and not fn.startswith("_")' in src
