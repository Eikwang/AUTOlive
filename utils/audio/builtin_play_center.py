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
        self._started_cb = None       # 字幕打印开播回调（F3，独立注册点——Eng T1 通道隔离）
        self._release_999_hook = None  # audio_core 注入：999 离开系统时清除 Audio.active 标记（A7/T1）
        self._priority_provider = None  # A5：权威优先级表读取器（filter 单源内存视图）

        # A7（2026-10-22 审查批次）：999 暂停-续播状态（单一暂停机制，F-2/F-3/F-5）
        self._suspended_999 = None     # 挂起的 999 条目（播放器持有，不重新入队——R2-A7a）
        self._suspended_play = None    # 挂起现场 {"wf","tmp_audio_path"}（流=self.stream）
        self._playing_is_999 = False   # 当前播放条目是否 999（让位触发判定）
        self._current_output_rate = 0  # 输出采样率（F-5：回退 1 秒帧数折算基准）
        self._interrupt_999_event = threading.Event()  # 让位触发（design 情况1）
        self._interruptor_pending = False  # 挂起后待插播的打断者标记（播毕即续播）

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

    def set_started_callback(self, async_cb):
        """注册开播回调（F3：字幕推送时点=实际播放时点；义务 Eng T1——独立注册点，
        不复用 set_completion_callback/_completion_cb：其载荷固定且被 A7 999 续播
        复用，复用将使 audio_core 完成判定误消费开播消息、破坏播放背压语义）"""
        self._started_cb = async_cb

    def _fire_started(self, data_json):
        """开播回调（F3）。异常隔离沿 _fire_completion；载荷=播放条目
        content/type/voice_path 三键（Eng T1 义务：keep_time 取实际播放文件时长）。
        999 续播恢复不重推（挂起前已在开播时推送）。"""
        try:
            if self._started_cb is None:
                return
            payload = {
                "type": data_json.get("type", ""),
                "content": data_json.get("content", ""),
                "voice_path": data_json.get("voice_path", ""),
            }

            async def _safe_started():
                # 回调协程内异常在 _cb_loop 执行时才发生（submit 只提交不等待）——
                # 此处包裹捕获并计入错误可见性（_record_error→错误条 D9）
                try:
                    await self._started_cb(payload)
                except Exception:
                    self._record_error(f"字幕开播回调异常\n{traceback.format_exc()}")

            if not self._cb_loop.submit(_safe_started()):
                self._record_error("字幕开播回调提交失败（_cb_loop）")
        except Exception:
            self._record_error(f"字幕开播回调异常\n{traceback.format_exc()}")

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
                    if self._suspended_999 is not None:
                        # 队列空且仍有挂起 999（插播者已被消费/清空）→ 恢复
                        self._resume_suspended_999()
                        continue
                    self.audio_data_event.clear()
                    self.audio_data_event.wait(1.0)
                    continue

                with self.list_lock:
                    data_json = self.audio_json_list.pop(0)

                status = self._play_item(data_json)
                if status == "suspended":
                    # 999 让位挂起：下一步从队列播插播条目（不得立即续播）
                    continue
                # 插播条目播毕 → 立即续播挂起的 999（R2-A7b：不等队列排空）
                if self._suspended_999 is not None and self._interruptor_pending:
                    self._interruptor_pending = False
                    self._resume_suspended_999()
            except Exception:
                # 单条失败跳过（缺陷3 修复：不清空整个队列）
                logger.error("builtin播放器播放循环异常，跳过当前条\n" + traceback.format_exc())
                self._cleanup_stream()
                time.sleep(0.2)

    def _priority_mapping(self):
        """A5 单源：priority provider（Config 内存视图）→ filter 表（config.json）
        → 存量 audio_player 表兜底（未迁移配置）。"""
        try:
            if self._priority_provider is not None:
                m = self._priority_provider()
                if m:
                    return m
        except Exception:
            pass
        try:
            cfg_all = _load_config_file(self.config_path) or {}
            m = (cfg_all.get("filter") or {}).get("priority_mapping")
            if m:
                return m
        except Exception:
            pass
        return (self.config_data or {}).get("priority_mapping") or {}

    def _item_priority(self, data_json):
        """条目权重（A7/T1/A5 单源）：见 _priority_mapping；未识别 → None。"""
        try:
            p = self._priority_mapping().get(data_json.get("type"))
            return int(p) if p is not None else None
        except Exception:
            return None

    def set_priority_provider(self, provider):
        """A5：注入权威优先级表读取器（audio_core 传入 Config 内存视图——合并迁移
        即时可见，不依赖 config.json 落盘）。"""
        self._priority_provider = provider

    def set_999_release_hook(self, hook):
        """注册 999 离开系统回调（audio_core 注入，清除 Audio.active_reread_top_999）。"""
        self._release_999_hook = hook

    def _release_999(self):
        try:
            if self._release_999_hook is not None:
                self._release_999_hook()
        except Exception:
            logger.error("999 释放回调异常\n" + traceback.format_exc())

    def request_999_interrupt(self):
        """队列插入侧调用（queue_manager._maybe_interrupt_999_for）：正在播 999 时
        请求让位（design 情况1）。已挂起则忽略（情况 c 排队不打断，R2-A7c）。"""
        if self._playing_is_999 and self._suspended_999 is None:
            self._interrupt_999_event.set()

    def _wait_loop(self, is_999=False):
        """播放等待循环（A7 重构，F-2 修复）。

        旧循环缺陷：pause_stream 调 stop_stream 后 is_active 翻 False → 循环即出
        → 误报完成 + finally 关流删 tmp → resume_stream 对已关流失效（=变相跳过）。
        新循环：pause_event 置位期间保持等待（流停、wf 读位冻结），恢复后续播；
        999 让位中断（design 情况1）；跳过/停止；EOF 自然完成。

        Returns:
            str: "completed" | "skipped" | "suspended"
        """
        while True:
            if is_999 and self._suspended_999 is None and self._interrupt_999_event.is_set():
                # 999 让位：暂停挂起（流停、wf 读位冻结；现场由调用方登记保留）。
                # 同置 pause_event：get_status().paused 翻真 → E-4 字幕挂起同步
                # （字幕批次 Eng E1 交界缝隙：否则让位窗口字幕继续走 keep_time，
                # 续播回退 1s 后偏移=挂起时长）。
                self._interrupt_999_event.clear()
                self.pause_event.set()
                try:
                    self.stream.stop_stream()
                except Exception:
                    pass
                logger.info("999 权重音频让位挂起（新音频插播，design 情况1；字幕 E-4 同步）")
                return "suspended"
            if self.pause_event.is_set():
                # F-2：手动暂停保持等待（不退出循环、不误报完成），恢复后从暂停点继续
                time.sleep(0.1)
                continue
            if self._skip_event.is_set() or self._stop_flag.is_set():
                return "skipped"
            if self.stream is None or not self.stream.is_active():
                return "completed"
            time.sleep(0.1)

    def _finish_cleanup(self, wf=None, tmp_audio_path=None):
        """条目终态清理（eng E-8 次序：先 wf 后 stream 再删文件，防 Windows PermissionError）"""
        if wf is not None:
            try:
                wf.close()
            except Exception:
                pass
        self._cleanup_stream()
        if tmp_audio_path:
            try:
                os.remove(tmp_audio_path)
            except OSError:
                pass
        self._current = None
        self._playing_is_999 = False

    def _resume_suspended_999(self):
        """A7 续播：回退 1 秒后从暂停点继续（打断者播毕即续播，不等队列排空——R2-A7b）。

        回退口径（E2/F-5）：1 秒 = 输出采样率帧数（speed≠1 时按输出流速率折算，
        即听众时间轴回退 1 秒）。
        """
        item = self._suspended_999
        held = self._suspended_play
        self._suspended_999 = None
        self._suspended_play = None
        self._interruptor_pending = False
        try:
            wf = held["wf"]
            rewind = int(self._current_output_rate * 1.0) if self._current_output_rate else 0
            if rewind > 0:
                try:
                    pos = wf.tell()
                    wf.setpos(max(0, pos - rewind))
                    logger.info(f"999 续播回退 1 秒（{rewind} 帧，暂停点 {pos}）")
                except Exception:
                    logger.warning("999 续播回退设置失败（继续暂停点位置）")
            self.pause_event.clear()  # E-4：字幕挂起同步解除
            if self.stream is not None:
                try:
                    self.stream.start_stream()
                except Exception:
                    pass
            logger.info(f"999 权重音频续播：{item.get('voice_path')}")

            self._skip_event.clear()
            status = self._wait_loop(is_999=True)
            if status == "completed":
                self._fire_completion()
            self._release_999()
            self._finish_cleanup(wf, held.get("tmp_audio_path"))
            self._post_play_interval()
        except Exception:
            logger.error("999 续播异常\n" + traceback.format_exc())
            self._release_999()
            self._finish_cleanup(held.get("wf") if held else None,
                                 held.get("tmp_audio_path") if held else None)

    def _discard_suspended(self, reason):
        """F-3：清理路径（skip/clear/stop_all）放弃挂起的 999 条目，日志留痕不静默。"""
        if self._suspended_999 is None:
            return
        held = self._suspended_play
        path = self._suspended_999.get("voice_path")
        self._suspended_999 = None
        self._suspended_play = None
        self._interruptor_pending = False
        self._release_999()
        if held:
            try:
                held["wf"].close()
            except Exception:
                pass
            try:
                os.remove(held["tmp_audio_path"])
            except OSError:
                pass
        self._cleanup_stream()
        logger.info(f"{reason}：放弃挂起的 999 条目（F-3）：{path}")

    def _post_play_interval(self):
        """播完后间隔（默认 0：间隔由 playback_manager 层负责，避免双重间隔）"""
        interval = float(self._hot_value("audio_interval", 0) or 0)
        random_interval = self._hot_value("random_audio_interval", None) or {}
        if random_interval.get("enable"):
            time.sleep(random.uniform(float(random_interval.get("min", 0)), float(random_interval.get("max", 0))))
        elif interval > 0:
            time.sleep(interval)

    def _play_item(self, data_json):
        voice_path = data_json.get("voice_path")
        if not voice_path or not os.path.isfile(voice_path):
            self._record_error(f"音频文件不存在，跳过：{voice_path}")
            return "completed"
        if data_json.get("mode") == "url":
            # 内置模式仅消费本地产物（URL 场景属外部服务模式）
            self._record_error(f"builtin 不支持 url 模式，跳过：{voice_path}")
            return "completed"

        is_999 = (self._item_priority(data_json) == 999)
        self._playing_is_999 = is_999
        self._interrupt_999_event.clear()  # 丢弃上个条目周期的陈旧让位事件

        # 解码统一转 wav（mp3 等格式依赖 ffmpeg，既有系统依赖）
        try:
            audio = AudioSegment.from_file(voice_path)
            # R6 双轨（/autoplan 2026-10-23 批次）：song 消息带 backing_path 伴奏轨时
            # 本地播放=pydub overlay 混音单流（单流播，同步天然成立）；音量线性系数
            # 转 dB 增益。伴奏缺失/失败→整轨降级（F-S5 告警可见，不中断播放）。
            _backing_path = data_json.get("backing_path") or ""
            if _backing_path:
                if not os.path.isfile(_backing_path):
                    self._record_error(f"[song] 伴奏轨缺失，整轨降级：{_backing_path}")
                else:
                    try:
                        _backing = AudioSegment.from_file(_backing_path)
                        _vol = max(0.0, min(1.0, float(data_json.get("backing_volume", 0.8) or 0.8)))
                        if _vol > 0:
                            import math as _math
                            _backing = _backing.apply_gain(20.0 * _math.log10(_vol))
                            audio = audio.overlay(_backing[:len(audio)])
                    except Exception:
                        self._record_error(f"[song] 伴奏混音失败，整轨降级：{_backing_path}\n"
                                           + traceback.format_exc())
            tmp_audio_path = os.path.join(
                self.audio_out_path, "tmp_" + str(int(time.time() * 1000)) + ".wav")
            os.makedirs(self.audio_out_path, exist_ok=True)
            audio.export(tmp_audio_path, format="wav")
        except Exception:
            self._record_error(f"解码失败：{voice_path}\n" + traceback.format_exc())
            self._playing_is_999 = False
            return "completed"

        wf = wave.open(tmp_audio_path, "rb")
        self._skip_event.clear()
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

        suspended = False
        try:
            # device_index 播放时现读（缺陷4 修复）；-1 = 系统默认（pyaudio 需 None）
            device_index = self._hot_value("device_index", -1)
            try:
                device_index = int(device_index)
            except (TypeError, ValueError):
                device_index = -1

            rate = int(wf.getframerate() * float(data_json.get("speed", 1) or 1))
            self._current_output_rate = rate  # F-5：999 续播回退 1 秒的折算基准

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
            self._fire_started(data_json)  # F3：字幕推送时点=实际开播时

            status = self._wait_loop(is_999=is_999)

            if status == "suspended":
                # A7：现场登记后返回（流/wf/tmp 由挂起状态持有，续播或弃置时处理）
                suspended = True
                self._suspended_play = {"wf": wf, "tmp_audio_path": tmp_audio_path}
                self._suspended_999 = data_json
                self._interruptor_pending = True
                return "suspended"

            skipped = (status == "skipped")
            logger.info(f"builtin播放结束：{'跳过' if skipped else '完成'} type={data_json.get('type')}")

            if not skipped:
                self._fire_completion()
            if is_999:
                self._release_999()
            self._finish_cleanup(wf, tmp_audio_path)
            self._post_play_interval()
            return status
        except Exception:
            self._record_error(f"播放失败（设备/解码）：{voice_path}\n" + traceback.format_exc())
            if is_999:
                self._release_999()
            self._finish_cleanup(wf, tmp_audio_path)
            return "completed"

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
        """按优先级插队（源 data_priority_insert 语义保真：未识别 type 落队尾）。
        A5 单源：消费 filter.priority_mapping（经 _priority_mapping 三级回退）。"""
        priority_mapping = self._priority_mapping()

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
        if self._suspended_999 is not None:
            # A7：999 挂起期手动续播不生效——插播条目播毕后自动续播（防挂起流
            # 被手动拉起与插播者双出声；E-4 同步由挂起状态维持）
            logger.info("999 挂起中，手动续播不生效（插播完成后自动续播）")
            return
        if self.pause_event.is_set():
            self.pause_event.clear()
            if self.stream:
                try:
                    self.stream.start_stream()
                except Exception:
                    pass
            logger.info("builtin播放已恢复")

    def skip_current_stream(self):
        """跳过当前条（builtin 停止语义调用目标，native F6）；
        F-3：挂起中的 999 一并放弃（进下一条，日志留痕）"""
        self._discard_suspended("跳过")
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
        self._discard_suspended("清空队列")
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
        """系统停止链路调用（eng E-11）：清队列+停流+唤醒线程退出；F-3 弃置挂起条目"""
        self._stop_flag.set()
        with self.list_lock:
            self.audio_json_list.clear()
        self._discard_suspended("系统停止")
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
