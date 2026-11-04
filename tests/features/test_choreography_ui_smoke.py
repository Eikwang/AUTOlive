# -*- coding: UTF-8 -*-
"""动作编排页构建冒烟（2026-11-04 erro2 修复守卫）。

真实 NiceGUI 客户端构建动作编排页，捕获构建期失败面。本次缺陷：
档位编辑器 ui.select 哨兵元组键值反写（("（继承基础档）", "")——
NiceGUI dict options 语义为键=值/值=标签），空覆盖档 value="" 不在
options 键中 → ValueError: Invalid value:（erro2.log；点击「加一档」
新增空覆盖档即触发）。config 预置含空覆盖档的 emotion_tiers，构建期
（choreography.py 末行 _render_rules()）即执行该渲染路径。

实现注记：构建必须跑在独立子进程——nicegui.testing.user_simulation
的全局重置不还原进入前快照，套件进程内运行会污染后续所有 UI 构建
测试（2026-11-02 实测 85 例连坐 slot-stack 失败）。故本文件测试仅
负责派发子进程，构建断言在 `python 本文件 --child` 中执行。
"""
import os
import subprocess
import sys

_MARKERS = [
    "动作素材创建（画面资产库）",   # 卡 1（2026-11-04 四卡重排：创建/列表分离）
    "素材列表",                      # 卡 2
    "默认画面（空闲轮播）",          # 卡 3（规则列表独立成卡 4 后余部）
    "编排规则（事件驱动画面序列）",  # 卡 4
    "规则 r1",                       # expansion 标题（折叠态标题可见）
]

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def _run_build_in_child() -> bool:
    """子进程构建断言主体：返回全部通过与否。"""
    import asyncio

    async def _build() -> bool:
        from nicegui import ui
        from nicegui.testing.user_simulation import user_simulation

        async with user_simulation() as user:

            @ui.page("/choreo-smoke")
            def _page():
                from frontend.ui.tabs.choreography import create_choreography_tab

                create_choreography_tab(
                    config={"api_port": 8082,
                            "choreography": {
                                "enable": True,
                                "default_mode": "single",
                                "default_segments": ["pool_1"],
                                "default_single_segment": "pool_1",
                                "rules": [{
                                    "rule_id": "r1", "trigger_type": "gift", "match": "",
                                    "pred_version": "new", "interrupt": "standard",
                                    "sequence": {
                                        "enter": {"segment": "e_seg", "switch_at": "segment_end"},
                                        "main": {"segment": "m_seg", "switch_at": "segment_end"},
                                        "exit": {"segment": "x_seg", "switch_at": "segment_end"},
                                    },
                                    # 档位一=空覆盖（erro2 缺陷路径：value="" 须命中哨兵键）
                                    "emotion_tiers": [
                                        {"min_count": 3, "mood": "happy", "segment_overrides": {}},
                                        {"min_count": 6, "mood": "angry",
                                         "segment_overrides": {"enter": "t_seg"}},
                                    ],
                                }],
                            }},
                    theme_config={"card": "", "switch_internal": ""},
                    set_config_callback=lambda *a, **k: None,
                )

            await user.open("/choreo-smoke")
            for marker in _MARKERS:
                await user.should_see(marker)
        return True

    return asyncio.run(_build())


def test_choreography_tab_build_smoke():
    """构建冒烟：空覆盖档渲染路径（erro2）+ 页面关键标记可见。子进程隔离。"""
    env = os.environ.copy()
    env["PYTHONPATH"] = _REPO_ROOT + os.pathsep + env.get("PYTHONPATH", "")
    r = subprocess.run(
        [sys.executable, os.path.abspath(__file__), "--child"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=120, env=env, cwd=_REPO_ROOT,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    assert r.returncode == 0, (
        f"动作编排页构建冒烟子进程失败（rc={r.returncode}）"
        + str(getattr(r, 'stdout', '')[-1500:]) + str(getattr(r, 'stderr', '')[-1500:]))


if __name__ == "__main__":
    if "--child" in sys.argv:
        ok = _run_build_in_child()
        print("[smoke] choreography build ok" if ok else "[smoke] choreography build FAILED",
              file=sys.stderr)
        sys.exit(0 if ok else 1)
    sys.exit(0)
