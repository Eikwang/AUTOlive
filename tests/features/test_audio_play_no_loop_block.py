# -*- coding: UTF-8 -*-
"""音频播放页事件循环零阻塞守卫（2026-11-06 断联根修回归）。

根修机制：NiceGUI 3.16 默认 reconnect_timeout=3.0 → socket.io ping_timeout=2.0s；
audio_play 页原为事件循环上的同步 requests（进入 0.5s 设备枚举 timeout=2+5、
1s 状态轮询 timeout=1.5）——主进程响应 ≥1.5s 时背靠背阻塞 >2s → WS 判死 →
断联重载循环（实测：3s 延迟下进入页面 ~10-20s 断联；修后 50s 稳定）。

守卫断言（子进程内执行，沿 test_choose_song_cover_ui.py 子进程隔离约定）：
1. 结构面：页面注册的全部 Timer 回调必须是协程函数（sync 回调=回归）。
2. 行为面：把 requests.get 打成 3s 慢响应，手动触发轮询/枚举回调——
   事件循环必须在其飞行中仍可调度（0.1s 探针）；旧同步实现下探针
   永不执行，本测试必红。
"""
import os
import subprocess
import sys

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def _run_child() -> bool:
    import asyncio
    import inspect
    import time

    async def _verify() -> bool:
        from nicegui import ui
        from nicegui.elements.timer import Timer
        from nicegui.testing.user_simulation import user_simulation

        class _FakeResp:
            def __init__(self, payload):
                self._payload = payload

            def json(self):
                return self._payload

        _STATUS_PAYLOAD = {
            "builtin": True,
            "status": {"playing": False, "paused": False, "queue_len": 0,
                       "current": None, "last_error": None},
            "captions_failures": 0,
        }
        _DEVICES_PAYLOAD = {"code": 200, "devices": [
            {"device_index": 0, "device_info": "TestDevice"}]}

        import requests as _requests

        def _fake_get(url=None, **kwargs):
            time.sleep(3)  # 慢响应——复现条件的替身
            return _FakeResp(_STATUS_PAYLOAD)

        def _fake_post(url=None, **kwargs):
            return _FakeResp(_DEVICES_PAYLOAD)

        _requests.get = _fake_get
        _requests.post = _fake_post

        timers = []

        async with user_simulation() as user:

            @ui.page("/ap-no-block")
            def _page():
                from frontend.ui.tabs.audio_play import create_audio_play_tab

                create_audio_play_tab(
                    config={"play_audio": {"text_split_enable": True, "enable": True},
                            "audio_random_speed": {"normal": {"enable": True,
                                                              "speed_min": 0.9, "speed_max": 1.1}},
                            "audio_player": {"device_index": -1, "queue_max": 50},
                            "filter": {"priority_mapping": {"comment": 20}},
                            "api_ip": "127.0.0.1", "api_port": 8082},
                    theme_config={"card": "", "switch_internal": ""},
                    set_config_callback=lambda *a, **k: None,
                )

            await user.open("/ap-no-block")
            await user.should_see("音频播放")
            await user.should_see("文本切分")

            # 收集本页注册的 Timer（页面函数只建本 tab → 恰 2 个：0.5s 枚举+1s 轮询）
            # 注意：断言与探针必须在 user_simulation 上下文内完成——退出时 client
            # 清理会取消 Timer（callback 置空），块外断言会误报非协程。
            from nicegui.client import Client
            for client in list(Client.instances.values()):
                if client.page.path != "/ap-no-block":
                    continue

                def _walk(elem):
                    for child in elem:
                        if isinstance(child, Timer):
                            timers.append(child)
                        _walk(child)

                _walk(client.layout)

            if len(timers) < 2:
                print(f"[guard] 期望 ≥2 个 Timer，实际 {len(timers)}", file=sys.stderr)
                return False

            # 断言1（结构面）：全部 Timer 回调必须是协程函数
            for t in timers:
                if not inspect.iscoroutinefunction(t.callback):
                    print(f"[guard] Timer 回调非协程（interval={t.interval}s）——"
                          f"同步回调会在主进程迟滞时阻塞事件循环 >ping_timeout(2.0s)→断联",
                          file=sys.stderr)
                    return False

            # 断言2（行为面）：慢请求飞行中事件循环必须可调度
            for t in timers:
                done = []

                async def _probe(_done=done):
                    await asyncio.sleep(0.1)
                    _done.append(time.monotonic())

                t0 = time.monotonic()
                task = asyncio.create_task(t.callback())
                await _probe()
                if not done:
                    task.cancel()
                    print(f"[guard] 事件循环被 Timer 回调阻塞（0.1s 探针未执行，"
                          f"interval={t.interval}s）——同步 HTTP 回到事件循环上了", file=sys.stderr)
                    return False
                await asyncio.wait_for(task, timeout=10)
                elapsed = time.monotonic() - t0
                if elapsed < 2.5:
                    print(f"[guard] 慢 fetch 未真实执行（elapsed={elapsed:.2f}s）", file=sys.stderr)
                    return False
        return True

    return asyncio.run(_verify())


def test_audio_play_page_never_blocks_event_loop():
    """慢响应（3s）下轮询/枚举回调不得阻塞事件循环——断联根修守卫。"""
    env = os.environ.copy()
    env["PYTHONPATH"] = _REPO_ROOT + os.pathsep + env.get("PYTHONPATH", "")
    r = subprocess.run(
        [sys.executable, os.path.abspath(__file__), "--child"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=180, env=env, cwd=_REPO_ROOT,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    assert r.returncode == 0, (
        f"音频播放页事件循环阻塞守卫子进程失败（rc={r.returncode}）"
        + str(getattr(r, "stdout", "")[-1500:]) + str(getattr(r, "stderr", "")[-1500:]))


if __name__ == "__main__":
    if "--child" in sys.argv:
        ok = _run_child()
        sys.exit(0 if ok else 1)
    # 直接运行也执行子进程路径，便于手动排查
    test_audio_play_page_never_blocks_event_loop()
    print("GUARD OK")
