# -*- coding: UTF-8 -*-
"""
内置音频播放器（builtin 播放后端）

移植自 D:\\AI\\audio_player\\utils\\audio_play_center.py（AUDIO_PLAY_CENTER），
按《audio_player与captions_printer整合计划》§4 完成以下改造：
- 接口适配：play/pause_stream/resume_stream/skip_current_stream/get_list/clear
  与 utils/audio_handle/audio_player.py 的 AUDIO_PLAYER HTTP 客户端同名同参
  （鸭子类型，playback_manager 调用点零改动）；
- 缺陷修复：自旋等待改 Event+有界轮询（原 while...pass 烧 CPU）、tmp wav
  句柄关闭次序（wf→stream→删文件，Windows PermissionError）、单条失败跳过
  不清全队列、device_index 播放时现读（修复"写了不读"）；
- 新增：get_status() UI 专用摘要（get_list 保持鸭子契约原样）、queue_max
  满载阻塞背压、完成回调经专用 loop 线程提交（对齐 play_audio.info_to_callback
  语义）、stop_all() 供系统停止链路调用。

音频来源为本系统 TTS/变速产物（本地 wav/mp3），不移植 URL 下载、cache
复用与播放器级变速（变速由 playback_manager 层在产物上完成）。
"""

import json
import os
import random
import threading
import time
import traceback

import pyaudio
import wave

from pydub import AudioSegment

from utils.my_log import logger


def _load_config_file(config_path):
    """现场读取 config.json（修复 device_index"写了不读"缺陷的取值来源）"""
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        logger.error("builtin播放器读取配置文件失败\n" + traceback.format_exc())
        return None


class _CallbackLoop:
    """常驻事件循环线程：供播放器线程把协程安全提交到 loop 执行"""

    def __init__(self):
        self.loop = None
        self._ready = threading.Event()
        threading.Thread(target=self._run, daemon=True, name="builtin-play-cb-loop").start()
        self._ready.wait(5)

    def _run(self):
        import asyncio
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self._ready.set()
        self.loop.run_forever()

    def submit(self, coro):
        """提交协程；失败返回 False（调用方计入错误可见性，不得静默）"""
        import asyncio
        try:
            if self.loop is None or self.loop.is_closed():
                return False
            asyncio.run_coroutine_threadsafe(coro, self.loop)
            return True
        except Exception:
            logger.error("builtin播放器回调提交失败\n" + traceback.format_exc())
            return False


class AUDIO_PLAY_CENTER:
    """内置播放器（进程内队列播放：插队/暂停/续播/跳过/清队）"""

    def __init__(self, config_data, config_path="config.json"):
        # config_data = config.get("audio_player") 快照（priority_mapping 等静态语义）；
        # device_index / queue_max 播放时经 config_path 现场读取（eng E 系"写了不读"修复）。
        self.config_data = config_data or {}
        self.config_path = config_path

        self.audio = pyaudio.PyAudio()
        self.stream = None

        self.pause_event = threading.Event()      # 暂停标志（流级暂停点恢复）
        self.audio_data_event = threading.Event()  # 空队列唤醒
        self._done_event = threading.Event()       # 当前条目播放完毕/被跳过
        self._skip_event = threading.Event()       # 跳过当前条目
        self._stop_flag = threading.Event()        # 系统停止（stop_all）

        self.audio_json_list = []
        self.list_lock = threading.Lock()

        self._current = None          # 正在播放条目摘要（get_status 用）
        self.last_error = None        # 最近一次播放器错误（控制区错误条，D9）
        self._error_seq = 0

        self._cb_loop = _CallbackLoop()
        self._completion_cb = None    # audio_core 注册的 async 完成回调

        # 输出目录：沿用系统 play_audio.out_path（合成产物同级）
        try:
            cfg_all = _load_config_file(self.config_path) or {}
            out_path = (cfg_all.get("play_audio") or {}).get("out_path", "out")
        except Exception:
            out_path = "out"
        self.audio_out_path = out_path
        self._startup_tmp_cleanup()

        self.play_thread = threading.Thread(target=self.play_audio, daemon=True, name="builtin-play")
        self.play_thread.start()
        logger.info("内置音频播放器已启动（builtin）")
        register_builtin_player(self)

    # ---------- 配置现读 ----------
    def _hot_value(self, key, default):
        """播放时现场读 audio_player.<key>（config.json 单文件模式）"""
        cfg_all = _load_config_file(self.config_path)
        if cfg_all is None:
            return (self.config_data or {}).get(key, default)
        section = cfg_all.get("audio_player") or {}
        value = section.get(key, (self.config_data or {}).get(key, default))
        return value if value is not None else default

    def _startup_tmp_cleanup(self):
        """启动时清理上次运行遗留的 tmp wav（缺陷2 修复的启动侧）"""
        try:
            if not os.path.isdir(self.audio_out_path):
                return
            for name in os.listdir(self.audio_out_path):
                if name.startswith("tmp_") and name.endswith(".wav"):
                    try:
                        os.remove(os.path.join(self.audio_out_path, name))
                    except OSError:
                        pass
        except Exception:
            logger.error("builtin播放器启动清理 tmp 失败\n" + traceback.format_exc())

    # ---------- 完成回调 ----------
    def set_completion_callback(self, async_cb):
        """注册播放完成回调（async(data_dict)）；在播放器线程经专用 loop 提交"""
        self._completion_cb = async_cb

    def _fire_completion(self):
        try:
            if self._completion_cb is None:
                return
            data = {
                "type": "audio_playback_completed",
                "data": {"wait_play_audio_num": len(self.audio_json_list)},
            }
            if not self._cb_loop.submit(self._completion_cb(data)):
                self._record_error("播放完成回调提交失败")
        except Exception:
            logger.error("builtin播放器完成回调异常\n" + traceback.format_exc())

    def _record_error(self, msg):
        self._error_seq += 1
        self.last_error = {"seq": self._error_seq, "message": str(msg), "ts": time.time()}
        logger.error(f"builtin播放器错误：{msg}")

    # ---------- 主循环 ----------
    def play_audio(self):
        while not self._stop_flag.is_set():
            try:
                if len(self.audio_json_list) <= 0:
                    self.audio_data_event.clear()
                    self.audio_data_event.wait(1.0)
                    continue

                with self.list_lock:
                    data_json = self.audio_json_list.pop(0)

                self._play_item(data_json)
            except Exception:
                # 单条失败跳过（缺陷3 修复：不清空整个队列）
                logger.error("builtin播放器播放循环异常，跳过当前条\n" + traceback.format_exc())
                self._cleanup_stream()
                time.sleep(0.2)

    def _play_item(self, data_json):
        voice_path = data_json.get("voice_path")
        if not voice_path or not os.path.isfile(voice_path):
            self._record_error(f"音频文件不存在，跳过：{voice_path}")
            return
        if data_json.get("mode") == "url":
            # 内置模式仅消费本地产物（URL 场景属外部服务模式）
            self._record_error(f"builtin 不支持 url 模式，跳过：{voice_path}")
            return

        # 解码统一转 wav（mp3 等格式依赖 ffmpeg，既有系统依赖）
        audio = AudioSegment.from_file(voice_path)
        tmp_audio_path = os.path.join(
            self.audio_out_path, "tmp_" + str(int(time.time() * 1000)) + ".wav")
        os.makedirs(self.audio_out_path, exist_ok=True)
        audio.export(tmp_audio_path, format="wav")

        wf = wave.open(tmp_audio_path, "rb")
        self._skip_event.clear()
        completed_flag = {"fired": False}
        self._current = {
            "type": data_json.get("type", ""),
            "content": str(data_json.get("content", ""))[:20],
        }

        def callback(in_data, frame_count, time_info, status):
            data = wf.readframes(frame_count)
            if len(data) == 0:
                # EOF：让 PortAudio 排空缓冲后自然转 inactive（实证：喂完最后一帧
                # 后 PortAudio 不再调用回调，EOF 不能在此处作为完成信号）
                return (b"", pyaudio.paComplete)
            return (data, pyaudio.paContinue)

        try:
            # device_index 播放时现读（缺陷4 修复）；-1 = 系统默认（pyaudio 需 None）
            device_index = self._hot_value("device_index", -1)
            try:
                device_index = int(device_index)
            except (TypeError, ValueError):
                device_index = -1

            rate = int(wf.getframerate() * float(data_json.get("speed", 1) or 1))

            self.stream = self.audio.open(
                format=self.audio.get_format_from_width(wf.getsampwidth()),
                channels=wf.getnchannels(),
                rate=rate,
                output=True,
                output_device_index=None if device_index < 0 else device_index,
                stream_callback=callback,
            )
            self.stream.start_stream()
            logger.info(f"builtin播放开始：type={data_json.get('type')} path={voice_path}")

            # 完成判定 = 流转 inactive（pyaudio 实证：EOF 无独立回调信号）。
            # 0.1s 有界轮询——已修复的原缺陷是"零休眠 pass 自旋烧 CPU"，非轮询本身。
            while self.stream is not None and self.stream.is_active():
                if self._skip_event.is_set() or self._stop_flag.is_set():
                    break
                time.sleep(0.1)

            skipped = self._skip_event.is_set() or self._stop_flag.is_set()
            logger.info(f"builtin播放结束：{'跳过' if skipped else '完成'} type={data_json.get('type')}")

            if not skipped:
                self._fire_completion()

            # 播完后间隔（默认 0：间隔由 playback_manager 层负责，避免双重间隔）
            interval = float(self._hot_value("audio_interval", 0) or 0)
            random_interval = self._hot_value("random_audio_interval", None) or {}
            if random_interval.get("enable"):
                time.sleep(random.uniform(float(random_interval.get("min", 0)), float(random_interval.get("max", 0))))
            elif interval > 0:
                time.sleep(interval)
        except Exception:
            self._record_error(f"播放失败（设备/解码）：{voice_path}\n" + traceback.format_exc())
        finally:
            # 句柄关闭次序（eng E-8）：先 wf 后 stream 再删文件，防 Windows PermissionError
            try:
                wf.close()
            except Exception:
                pass
            self._cleanup_stream()
            try:
                os.remove(tmp_audio_path)
            except OSError:
                pass
            self._current = None

    def _cleanup_stream(self):
        if self.stream is not None:
            try:
                self.stream.stop_stream()
                self.stream.close()
            except Exception:
                pass
            self.stream = None

    # ---------- 接口适配（与 AUDIO_PLAYER 客户端同名同参；get_status 为 UI 专用新增） ----------
    def play(self, data):
        """入队（映射源 add_audio_json）；队列满载阻塞背压（S7，queue_max 可配）"""
        queue_max = int(self._hot_value("queue_max", 50) or 50)
        while True:
            with self.list_lock:
                if len(self.audio_json_list) < max(1, queue_max):
                    break
            # 满载：阻塞等待消费（对齐 EDTalk 计划"阻塞式背压"义务）
            if self._stop_flag.wait(0.2):
                return False
        with self.list_lock:
            if "insert_index" in data:
                if data["insert_index"] == -1:
                    self.audio_json_list.append(data)
                else:
                    self.audio_json_list.insert(data["insert_index"], data)
            else:
                self._data_priority_insert(data)
        self.audio_data_event.set()
        return True

    def _data_priority_insert(self, audio_json):
        """按 priority_mapping 插队（源 data_priority_insert 语义保真：未识别 type 落队尾）"""
        priority_mapping = (self.config_data or {}).get("priority_mapping") or {}

        def get_priority(item):
            return priority_mapping.get(item.get("type"))

        new_priority = get_priority(audio_json)
        if new_priority is None:
            insert_position = len(self.audio_json_list)
        else:
            insert_position = 0
            for i in range(len(self.audio_json_list) - 1, -1, -1):
                item_priority = get_priority(self.audio_json_list[i])
                if item_priority is not None and item_priority >= new_priority:
                    insert_position = i + 1
                    break
        self.audio_json_list.insert(insert_position, audio_json)

    def pause_stream(self):
        """流级暂停（暂停点恢复；push 侧经 get_status().paused 同步挂起字幕，eng E-4）"""
        self.pause_event.set()
        if self.stream:
            try:
                self.stream.stop_stream()
            except Exception:
                pass
        logger.info("builtin播放已暂停")

    def resume_stream(self):
        if self.pause_event.is_set():
            self.pause_event.clear()
            if self.stream:
                try:
                    self.stream.start_stream()
                except Exception:
                    pass
            logger.info("builtin播放已恢复")

    def skip_current_stream(self):
        """跳过当前条（builtin 停止语义调用目标，native F6）"""
        with self.list_lock:
            if self.stream is not None:
                try:
                    self.stream.stop_stream()
                    self.stream.close()
                except Exception:
                    pass
                self.stream = None
            self.pause_event.clear()
            self._skip_event.set()
            self._done_event.set()  # 唤醒等待循环
            if len(self.audio_json_list) > 0:
                self.audio_data_event.set()
        logger.info("builtin跳过当前播放")

    def clear(self):
        with self.list_lock:
            self.audio_json_list.clear()
        logger.info("builtin清空播放队列")

    def get_list(self):
        """保持与 AUDIO_PLAYER 客户端一致的鸭子契约（原始列表 JSON）"""
        try:
            return json.dumps({"list": self.audio_json_list}, ensure_ascii=False)
        except Exception:
            return json.dumps({"list": []})

    def get_status(self):
        """UI 专用摘要（dx 发现3：与 get_list 契约分离）"""
        with self.list_lock:
            return {
                "queue_len": len(self.audio_json_list),
                "playing": self.stream is not None and not self.pause_event.is_set(),
                "paused": self.pause_event.is_set(),
                "current": dict(self._current) if self._current else None,
                "last_error": dict(self.last_error) if self.last_error else None,
            }

    def get_all_audio_device_info(self):
        """枚举有输出通道的声卡（设置页设备下拉）"""
        device_infos = []
        try:
            for device_index in range(self.audio.get_device_count()):
                device_info = self.audio.get_device_info_by_index(device_index)
                if device_info["maxOutputChannels"] > 0:
                    device_infos.append({"device_index": device_index, "device_info": device_info["name"]})
        except Exception:
            logger.error("builtin枚举声卡失败\n" + traceback.format_exc())
        return device_infos

    def stop_all(self):
        """系统停止链路调用（eng E-11）：清队列+停流+唤醒线程退出"""
        self._stop_flag.set()
        with self.list_lock:
            self.audio_json_list.clear()
        self.pause_event.clear()
        self._skip_event.set()
        self._done_event.set()
        self.audio_data_event.set()
        self._cleanup_stream()
        logger.info("builtin播放器已停止（stop_all）")


# ---------- 全局单例（web_server 跨端点访问；照 edtalk register_client 先例） ----------
_builtin_player = None


def register_builtin_player(player):
    global _builtin_player
    _builtin_player = player


def get_builtin_player():
    return _builtin_player
