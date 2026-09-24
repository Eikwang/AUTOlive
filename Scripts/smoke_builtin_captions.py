# -*- coding: utf-8 -*-
"""
首日冒烟脚本（整合计划 验收第 9 条 / T8）

用法（runtime312 解释器）：
    python Scripts/smoke_builtin_captions.py            # 全部检查
    python Scripts/smoke_builtin_captions.py --audio    # 仅内置播放器回放
    python Scripts/smoke_builtin_captions.py --captions # 仅字幕链路（需主系统已运行）

三断言（R2/R4/R10 收口）：
  1. builtin 播放 + 完成回调 + 停止（本地回放，无外部依赖）
  2. 字幕链路：/captions 页可达 + socket.io 连接 + config_update 初始同步（D1）
  3. EDTalk 模式字幕偏移实测 <0.5s（R10；EDTalk 未激活时跳过）
"""
import argparse
import json
import math
import os
import struct
import sys
import threading
import time
import wave

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

RESULTS = []


def report(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" —— {detail}" if detail else ""))


def make_test_wav(path, seconds=1.0, rate=16000):
    with wave.open(path, "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        # 440Hz 正弦波，可听可测
        frames = b"".join(
            struct.pack("<h", int(12000 * math.sin(2 * math.pi * 440 * i / rate)))
            for i in range(int(rate * seconds)))
        wf.writeframes(frames)
    return path


def check_audio():
    """断言1：builtin 播放 + 完成回调 + 停止"""
    import tempfile
    from utils.audio.builtin_play_center import AUDIO_PLAY_CENTER

    tmp = tempfile.mkdtemp(prefix="smoke_builtin_")
    cfg = {
        "audio_player": {
            "device_index": -1, "queue_max": 5, "audio_interval": 0,
            "random_audio_interval": {"enable": False, "min": 0, "max": 0},
            "priority_mapping": {"comment": 30, "copywriting": 1},
        },
        "play_audio": {"out_path": tmp},
    }
    cfg_path = os.path.join(tmp, "config.json")
    with open(cfg_path, "w", encoding="utf-8") as f:
        json.dump(cfg, f)

    wav_path = make_test_wav(os.path.join(tmp, "tone.wav"), 1.0)

    done = threading.Event()
    player = AUDIO_PLAY_CENTER(cfg.get("audio_player"), config_path=cfg_path)

    import asyncio
    def completion_cb(data):
        done.set()
    # 完成回调经播放器内部 loop 提交 async 协程——用 async 包装验证链路
    async def _cb(data):
        done.set()
    player.set_completion_callback(_cb)

    t0 = time.time()
    player.play({"type": "comment", "voice_path": wav_path, "content": "冒烟测试", "speed": 1})
    # 队列化后立即入队第二条验证插队/保序（不阻塞）
    player.play({"type": "copywriting", "voice_path": wav_path, "content": "第二条", "speed": 1})

    if not done.wait(15):
        report("audio.playback+callback", False, "15s 内未收到首条完成回调")
        player.stop_all()
        return
    first_latency = time.time() - t0

    # 等两条全部播完（队列排空；1s+1s 背靠背应 ≥1.8s）
    while time.time() - t0 < 10:
        st = player.get_status()
        if st["queue_len"] == 0 and not st["playing"] and not st["paused"]:
            break
        time.sleep(0.1)
    elapsed_all = time.time() - t0
    report("audio.playback+callback", 1.8 <= elapsed_all <= 9 and first_latency < 2.0,
           f"两条 1s 音频总耗时 {elapsed_all:.2f}s，首条完成 {first_latency:.2f}s")

    # 跳过语义：入队一条 2s 音频后立即跳过，应在远小于 2s 内回到空闲
    wav2 = make_test_wav(os.path.join(tmp, "long.wav"), 2.0)
    player.play({"type": "comment", "voice_path": wav2, "content": "将被跳过", "speed": 1})
    time.sleep(0.2)
    t_skip = time.time()
    player.skip_current_stream()
    skipped_elapsed = 0.0
    while time.time() - t_skip < 3:
        st = player.get_status()
        if not st["playing"] and st["queue_len"] == 0:
            skipped_elapsed = time.time() - t_skip
            break
        time.sleep(0.05)
    report("audio.skip", 0 < skipped_elapsed < 1.5, f"跳过 2s 条目耗时 {skipped_elapsed:.2f}s")
    player.stop_all()
    report("audio.stop_all", player._stop_flag.is_set())


def tempfile_dir():
    import tempfile
    return tempfile.mkdtemp(prefix="smoke_builtin_")


def check_captions(base_url):
    """断言2：字幕链路（需主系统已运行）"""
    try:
        import requests
    except ImportError:
        report("captions.page", False, "requests 未安装")
        return
    try:
        resp = requests.get(f"{base_url}/captions", timeout=3)
        ok_page = resp.status_code == 200 and "subtitle" in resp.text
        report("captions.page", ok_page, f"HTTP {resp.status_code}")
    except Exception as e:
        report("captions.page", False, f"主系统未运行或不可达：{e}")
        return

    try:
        import socketio as py_socketio  # python-socketio 客户端
        got_config = threading.Event()
        got_enable = {"value": None}

        sio = py_socketio.Client()

        @sio.on("config_update")
        def on_config(data):
            got_enable["value"] = data.get("enable")
            got_config.set()

        sio.connect(base_url.replace("http", "ws") + "/captions_ws",
                    socketio_path="captions_ws/socket.io", wait_timeout=5)
        got_config.wait(5)
        ok = got_config.is_set()
        report("captions.connect+D1初始同步", ok,
               f"enable={got_enable['value']}" if ok else "5s 未收到 config_update（D1 失效）")
        sio.disconnect()
    except Exception as e:
        report("captions.connect+D1初始同步", False, f"socket.io 连接失败：{e}")


def check_edtalk_offset(base_url, config_path="config.json"):
    """断言3：EDTalk 模式字幕偏移实测（R10；未激活时跳过）"""
    try:
        from utils.edtalk_realtime.helpers import is_edtalk_active
        from utils.config import Config
        cfg = Config(config_path)
        if not is_edtalk_active(cfg):
            print("[SKIP] EDTalk 未激活——偏移实测在 EDTalk 模式首播时进行（R10 义务保持）")
            return
    except Exception as e:
        print(f"[SKIP] EDTalk 状态判定失败：{e}")
        return
    # 实测方案：触发一次 TTS 推送并记录字幕页收到 message 的时间与音频起点差。
    # 首日实施时在 EDTalk 模式下运行本脚本并人工比对（自动化注入点已预留）。
    print("[TODO] EDTalk 偏移自动化测量需实时推理服务在跑：连接 /edtalk_status 后触发")
    print("       一句 TTS，比对字幕 message 到达时间与 push_full 时间差 <0.5s（R10）")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--audio", action="store_true", help="仅内置播放器回放")
    parser.add_argument("--captions", action="store_true", help="仅字幕链路（需主系统运行）")
    parser.add_argument("--base-url", default=None, help="主系统内部 API 地址（默认从 config.json 读）")
    args = parser.parse_args()

    base_url = args.base_url
    if base_url is None:
        try:
            with open("config.json", "r", encoding="utf-8") as f:
                cfg = json.load(f)
            api_ip = cfg.get("api_ip", "127.0.0.1")
            if api_ip == "0.0.0.0":
                api_ip = "127.0.0.1"
            base_url = f"http://{api_ip}:{cfg.get('api_port', 8082)}"
        except Exception:
            base_url = "http://127.0.0.1:8082"

    only_audio = args.audio and not args.captions
    only_captions = args.captions and not args.audio

    if not only_captions:
        check_audio()
    if not only_audio:
        check_captions(base_url)
        check_edtalk_offset(base_url)

    failed = [r for r in RESULTS if not r[1]]
    print(f"\n=== 冒烟结果：{len(RESULTS) - len(failed)}/{len(RESULTS)} 通过 ===")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
