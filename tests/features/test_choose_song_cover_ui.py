# -*- coding: UTF-8 -*-
"""歌曲翻唱页构建冒烟（2026-11-02 一键翻唱重构守卫）。

真实 NiceGUI 客户端构建点歌页，捕获构建期失败面（定义顺序错误/组件
value 值域漂移等静态字符串断言不可见的错误——本次重构曾出现
on_click 引用后置定义函数的 NameError 类问题，构建冒烟可拦）。

实现注记：构建必须跑在独立子进程——nicegui.testing.user_simulation
的全局重置不还原进入前快照，套件进程内运行会污染后续所有 UI 构建
测试（2026-11-02 实测 85 例连坐 slot-stack 失败）。故本文件测试仅
负责派发子进程，构建断言在 `python 本文件 --child` 中执行。
"""
import os
import subprocess
import sys

_MARKERS = [
    "上传歌曲", "一键翻唱",       # 五件套按钮（标准 outline 按钮，非原生 uploader）
    "歌曲列表", "翻唱音色档案",     # 参数与列表区
]

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def _run_build_in_child() -> bool:
    """子进程构建断言主体：返回全部通过与否。"""
    import asyncio

    async def _build() -> bool:
        from nicegui import ui
        from nicegui.testing.user_simulation import user_simulation

        async with user_simulation() as user:

            @ui.page("/cover-smoke")
            def _page():
                from frontend.ui.tabs.choose_song import create_choose_song_tab

                create_choose_song_tab(
                    config={"choose_song": {}, "api_port": 8000},
                    theme_config={"card": "", "switch_internal": ""},
                    set_config_callback=lambda *a, **k: None,
                )

            await user.open("/cover-smoke")
            for marker in _MARKERS:
                await user.should_see(marker)
            # 「选择歌曲」下拉已移除（一键翻唱直接使用本次上传的歌曲）
            try:
                await user.should_see("选择歌曲")
            except AssertionError:
                pass
            else:
                print(f"[smoke] legacy「选择歌曲」仍可见", file=sys.stderr)
                return False
        return True

    return asyncio.run(_build())


def test_choose_song_cover_ui_build_smoke():
    """子进程内无头构建点歌页并断言五件套元素（隔离全局状态污染）。"""
    env = os.environ.copy()
    env["PYTHONPATH"] = _REPO_ROOT + os.pathsep + env.get("PYTHONPATH", "")
    r = subprocess.run(
        [sys.executable, os.path.abspath(__file__), "--child"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=120, env=env, cwd=_REPO_ROOT,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    assert r.returncode == 0, (
        f"构建冒烟子进程失败（rc={r.returncode}）\n{r.stdout[-1500:]}\n{r.stderr[-1500:]}")


if __name__ == "__main__":
    if "--child" in sys.argv:
        sys.exit(0 if _run_build_in_child() else 1)
