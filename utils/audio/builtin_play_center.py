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

import ctypes
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


# ---------- MCI 原生播放后端（2026-11-13 助播撕裂卡顿根修） ----------
# 根因实证（本机对照实验）：PortAudio 回调/阻塞写都必须由 Python 线程供数，
# 主进程 GIL 争用（TTS/LLM/队列处理等纯 Python 工作）会让供数线程超过缓冲
# 时长拿不到执行机会——20 个同进程忙线程下回调 underflow 4-10 次、播放被
# 拉长 2-6 倍（=实播撕裂卡顿）；跨进程满载 0 欠载（排除 CPU 因素）。
# 修法：本地播放改用 winmm MCI——解码出的 wav 交给 Windows 音频栈原生播放，
# Python 只下发 play/pause/seek/close 指令，播放时序不再依赖 GIL（实测满载
# 下 3s 音频实测 3.07-3.14s 实时完成）。让位暂停&回退机制语义不变：暂停=
# mci pause、回退=seek 到 (暂停点-1s)、续播=resume（999 机制 2026-11-15 已退役，
# 该机制由 d12 泛化让位继续使用）。
_MCI_AVAILABLE = False
try:
    if os.name == "nt":
        _winmm = ctypes.WinDLL("winmm")
        _winmm.mciSendStringW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p,
                                          ctypes.c_uint, ctypes.c_void_p]
        _winmm.mciSendStringW.restype = ctypes.c_uint
        _MCI_AVAILABLE = True
except Exception:  # 非 Windows / winmm 不可用 → 回退 PortAudio 流
    _winmm = None

_mci_lock = threading.Lock()  # mciSendString 非线程安全（UI 暂停与播放线程并发）


def _mci_send(cmd):
    """下发 MCI 命令，返回 (rc, 返回串)。"""
    buf = ctypes.create_unicode_buffer(512)
    with _mci_lock:
        rc = _winmm.mciSendStringW(cmd, buf, 511, None)
    return rc, buf.value


class _MciStream:
    """MCI 原生播放（模拟 pyaudio Stream 的方法子集，供 _wait_loop/_cleanup 复用）。

    start_stream：首次=play，其后=resume（对应 stop_stream 的 pause）
    stop_stream：pause（保留播放位置，续播从暂停点继续）
    is_active：playing/paused 期间为真；播完 MCI 自动转 stopped → False=完成
    """

    _seq = 0

    def __init__(self, wav_path, alias=None):
        if alias is None:
            _MciStream._seq += 1
            alias = f"bpc_{os.getpid()}_{_MciStream._seq}"
        self._alias = alias
        self._started = False
        self._closed = False
        rc, _ = _mci_send(f'open "{wav_path}" type waveaudio alias {self._alias}')
        if rc != 0:
            self._closed = True
            raise RuntimeError(f"MCI open 失败 rc={rc}")

    def _cmd(self, cmd):
        try:
            return _mci_send(cmd)
        except Exception:
            return (-1, "")

    def start_stream(self):
        if self._closed:
            return
        if not self._started:
            self._started = True
            rc, _ = self._cmd(f"play {self._alias}")
            if rc != 0:
                raise RuntimeError(f"MCI play 失败 rc={rc}")
        else:
            # resume 重试（2026-11-20 卡住审查批次）：原静默吞错——resume 失败
            # 后 mode 停 paused，配合旧 is_active(paused=active) 即永久卡住。
            # MCI 偶发设备忙（waveOut 竞争）重试可恢复。
            for attempt in range(3):
                rc, _ = self._cmd(f"resume {self._alias}")
                if rc == 0:
                    return
                time.sleep(0.05)
            logger.warning(f"MCI resume 连续 3 次失败 rc={rc}（alias={self._alias}）"
                           "——续播可能失效，条目将被判完成（丢尾段不卡死）")

    def stop_stream(self):
        if self._closed:
            return
        self._cmd(f"pause {self._alias}")

    def close(self):
        if self._closed:
            return
        self._closed = True
        self._cmd(f"stop {self._alias}")
        self._cmd(f"close {self._alias}")

    def is_active(self):
        """仅 "playing" 视为活跃（2026-11-20 卡住审查批次）。

        旧实现 paused 也算 active：resume 静默失败（_cmd 吞错）后 mode 永久
        停在 paused → _wait_loop 把死态当在播 → 永久卡住（需手动跳过）。
        paused 语义由 _wait_loop 的 pause_event 分支管理（手动暂停/让位挂起
        均先置位 pause_event，不依赖 MCI paused 状态），故此处排除 paused——
        resume 失败时 mode 停 paused → is_active False → 条目判完成（丢尾段
        但不卡死；配合 start_stream 重试降低概率）。"""
        if self._closed:
            return False
        _, mode = self._cmd(f"status {self._alias} mode")
        return mode == "playing"

    def position_ms(self):
        """当前播放位置（毫秒；让位挂起回退基准）。"""
        if self._closed:
            return 0
        try:
            _, v = self._cmd(f"status {self._alias} position")
            return int(v or 0)
        except (TypeError, ValueError):
            return 0

    def seek_ms(self, ms):
        if self._closed:
            return
        self._cmd(f"seek {self._alias} to {max(0, int(ms))}")


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

    def __init__(self, config_data, config_path="config.json", config_obj=None):
        # config_data = config.get("audio_player") 快照（priority_mapping 等静态语义）；
        # device_index / queue_max 播放时经 config_path 现场读取（eng E 系"写了不读"修复）。
        # 2026-11-17 热切机制批次：config_obj=共享 Config 实例（audio_core 注入）——
        # _hot_value 升级为内存现读优先（零每事件磁盘 IO，与合成链 mtime 门控
        # Config.reload 同源同步）；config.json 现读保留为回退（config_obj 缺失/节缺键）。
        self.config_data = config_data or {}
        self.config_path = config_path
        self._config_obj = config_obj

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
        self._priority_provider = None  # A5：权威优先级表读取器（filter 单源内存视图）

        # 挂起-续播状态（A7 单一暂停机制，F-2/F-3/F-5；2026-11-15 批次：999
        # 机制退役，本状态由 d12 泛化让位继续使用——任何在播条目被更高优
        # 新音频让位时挂起，插播者播毕续播。字段名保留 _999 后缀=历史命名）
        self._suspended_999 = None     # 挂起的条目（播放器持有，不重新入队——R2-A7a）
        self._suspended_999_at = None  # 挂起登记时刻（monotonic；d22/uc5b 看门狗锚点）
        self._suspended_999_alarm_at = None  # 本挂起期看门狗已告警标记（去重）
        self._suspended_play = None    # 挂起现场 {"wf","tmp_audio_path"}（流=self.stream）
        self._playing_weight = None    # 当前播放条目有效权重（e3/d12 泛化让位判定；None=空闲）
        self._playing_entry = None     # 当前播放条目原始数据（2026-11-17 组原子消费批次：组原子让位门判定源）
        self._current_output_rate = 0  # 输出采样率（F-5：回退 1 秒帧数折算基准）
        self._underrun_count = 0       # 输出欠载计数（2026-11-13 抗 underrun 观测）
        self._use_mci = False          # 当前条目是否走 MCI 原生播放后端
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
        """播放时现读 audio_player.<key>。

        2026-11-17 热切机制批次：优先共享 Config 内存现读（零磁盘 IO；合成链
        mtime 门控 Config.reload 后类缓存刷新，本视图即时同新），节存在但键缺
        失/Config 不可用时回退 config.json 现场读取（原语义），再回退启动快照。
        device_index 变更=下一条目播放时生效（流打开时读取）；已开播条目不受
        影响（设备切换类属"重建生效"，不做运行中迁移）。"""
        if self._config_obj is not None:
            try:
                section = self._config_obj.get("audio_player")
                if isinstance(section, dict) and section.get(key) is not None:
                    return section.get(key)
            except Exception:
                pass
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
        不复用 set_completion_callback/_completion_cb：其载荷固定且被挂起续播
        复用，复用将使 audio_core 完成判定误消费开播消息、破坏播放背压语义）"""
        self._started_cb = async_cb

    def _fire_started(self, data_json):
        """开播回调（F3）。异常隔离沿 _fire_completion；载荷=播放条目
        content/type/voice_path 三键（Eng T1 义务：keep_time 取实际播放文件时长）。
        挂起续播恢复不重推（挂起前已在开播时推送）。"""
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

    def _fire_completion(self, data_json=None):
        try:
            if self._completion_cb is None:
                return
            # G1（2026-10-28 消费回传补齐）：载荷扩展 item_type/item_content——
            # audio_core 注册的编排转发回调据 (type,content) 匹配编排 inflight
            # （CL-2 语义，沿 playback_manager EDTalk 路径同款）；旧键保留兼容。
            data = {
                "type": "audio_playback_completed",
                "data": {
                    "wait_play_audio_num": len(self.audio_json_list),
                    "item_type": (data_json or {}).get("type", ""),
                    "item_content": (data_json or {}).get("content", ""),
                    # O6（2026-10-28 消费批次）：编排实例序号（未透传条目为空串，
                    # audio_core 转 None→编排侧退回 (type,content) 匹配）
                    "item_step_id": (data_json or {}).get("choreo_step_id", ""),
                },
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
    def _watchdog_999_suspension(self):
        """d22/uc5b 看门狗（2026-11-05 编排优化批次）：挂起超时告警，不强制回落。

        挂起=插播者播放期间持有（正常秒级，R2-A7b 播毕即续播）；超阈值=
        打断者链路卡死（builtin 播放挂死/EDTalk 背压不返回）——alarm_notifier
        告警每挂起期一次。uc5b 用户裁决（严格字面语义）：不强制回落——长挂起
        属合法态（低流量时段插播者可长于阈值），强制回落会吃掉挂起条目播报。
        song 伴唱挂起/resident 挂起看门狗随 T4 挂起态同批落地（编排侧）。
        999 机制 2026-11-15 已退役——本看门狗服务 d12 泛化让位的挂起态；
        配置键 suspend_999_watchdog_s 保留（历史命名，避免存量配置失读）。"""
        if self._suspended_999 is None or self._suspended_999_at is None:
            return
        if self._suspended_999_alarm_at is not None:
            return  # 本挂起期已告警（去重）
        try:
            threshold = float(self._hot_value("suspend_999_watchdog_s", 300) or 300)
        except Exception:
            threshold = 300.0
        elapsed = time.monotonic() - self._suspended_999_at
        if elapsed <= threshold:
            return
        self._suspended_999_alarm_at = elapsed
        logger.warning(
            f"[watchdog] 挂起超时：{elapsed:.0f}s > {threshold:g}s"
            "（告警不强制回落，uc5b；插播者链路疑似卡死）")
        try:
            from utils.alarm_notifier import alarm_error, MODULE_AUDIO_PLAYBACK
            alarm_error(MODULE_AUDIO_PLAYBACK,
                        f"让位挂起超时（{elapsed:.0f}s，未强制回落）",
                        fix_hint="检查打断者音频链路（builtin 播放/EDTalk 推送背压）是否卡死；"
                                 "必要时跳过当前条（skip）触发续播")
        except Exception:
            pass  # 告警失败不影响播放主循环

    def play_audio(self):
        while not self._stop_flag.is_set():
            try:
                self._watchdog_999_suspension()
                if len(self.audio_json_list) <= 0:
                    if self._suspended_999 is not None:
                        # 队列空且仍有挂起条目（插播者已被消费/清空）→ 恢复
                        self._resume_suspended_999()
                        continue
                    self.audio_data_event.clear()
                    self.audio_data_event.wait(1.0)
                    continue

                with self.list_lock:
                    data_json = self.audio_json_list.pop(0)

                status = self._play_item(data_json)
                if status == "suspended":
                    # 让位挂起：下一步从队列播插播条目（不得立即续播）
                    continue
                # 插播条目播毕 → 立即续播挂起条目（R2-A7b：不等队列排空）
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
        """条目权重（A5 单源）：见 _priority_mapping；未识别 → None。
        T2/e2（2026-11-05）：经 audio_priority 单一源——priority_override 优先
        于类型映射（编排插队 500 在播放侧判定同样生效；2026-11-15 批次 999
        机制退役，reread_top_priority 映射 500 保持最高顺位）。"""
        try:
            from utils.audio.audio_priority import entry_priority
            return entry_priority(data_json, self._priority_mapping())
        except Exception:
            return None

    def set_priority_provider(self, provider):
        """A5：注入权威优先级表读取器（audio_core 传入 Config 内存视图——合并迁移
        即时可见，不依赖 config.json 落盘）。"""
        self._priority_provider = provider

    def request_yield(self):
        """让位请求（e3 泛化，2026-11-05 批次；999 机制 2026-11-15 已退役）：
        正在播条目暂停挂起、让新条目先播（播毕自动续播）。触发语义由调用方
        裁决（queue_manager._maybe_barge_in_for）：在播条目 + 新音频有效权重
        严格更高（d12——song 让位给答谢，整曲暂停=伴奏人声同停近似）。
        已挂起则忽略（R2-A7c）。"""
        if getattr(self, "_playing_weight", None) is not None:
            # 有条目在播且未挂起 → 受理让位
            if self._suspended_999 is None:
                self._interrupt_999_event.set()

    def get_playing_weight(self):
        """当前播放条目有效权重（d12 b 触发判定用；None=空闲/未知）。"""
        return self._playing_weight

    def get_playing_entry(self):
        """当前播放条目原始数据（2026-11-17 组原子消费批次：queue_manager
        组原子让位门判定源——在播条目 split_group 组型决定是否让位；None=
        空闲/未知/挂起窗口。与 _current（字幕摘要）分离：摘要截断不含
        split_group。挂起恢复路径不回填（fail-open 退既有让位语义，可接受
        ——组原子门已阻断非文案组被挂起，恢复态条目必为文案组/非组条目）。"""
        return self._playing_entry

    def _wait_loop(self):
        """播放等待循环（A7 重构，F-2 修复；2026-11-15 批次：999 机制退役，
        原 is_999 参数随 999 专属分支删除——泛化让位分支对任意在播条目生效）。

        旧循环缺陷：pause_stream 调 stop_stream 后 is_active 翻 False → 循环即出
        → 误报完成 + finally 关流删 tmp → resume_stream 对已关流失效（=变相跳过）。
        新循环：pause_event 置位期间保持等待（流停、wf 读位冻结），恢复后续播；
        让位中断（design 情况1）；跳过/停止；EOF 自然完成。

        Returns:
            str: "completed" | "skipped" | "suspended"
        """
        while True:
            # e3 泛化（2026-11-05）：让位分支对任意在播条目生效——触发语义由
            # queue_manager._maybe_barge_in_for 裁决（新音频权重严格更高才触发）
            if self._suspended_999 is None and self._interrupt_999_event.is_set():
                # 让位：暂停挂起（流停、wf 读位冻结；现场由调用方登记保留）。
                # 同置 pause_event：get_status().paused 翻真 → E-4 字幕挂起同步
                # （字幕批次 Eng E1 交界缝隙：否则让位窗口字幕继续走 keep_time，
                # 续播回退 1s 后偏移=挂起时长）。
                self._interrupt_999_event.clear()
                self.pause_event.set()
                try:
                    self.stream.stop_stream()
                except Exception:
                    pass
                logger.info("在播条目让位挂起（新音频插播，design 情况1；字幕 E-4 同步）")
                return "suspended"
            if self._skip_event.is_set() or self._stop_flag.is_set():
                return "skipped"
            if self.pause_event.is_set():
                # F-2：手动暂停保持等待（不退出循环、不误报完成），恢复后从暂停点继续
                # （2026-11-20 卡住审查批次：skip 检查提到 pause 前——暂停态下
                # 「跳过当前」按钮此前不可达，暂停+跳过组合=永久卡死）
                if self._skip_event.is_set() or self._stop_flag.is_set():
                    return "skipped"
                time.sleep(0.1)
                continue
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
        self._playing_weight = None
        self._playing_entry = None  # 2026-11-17 组原子消费批次：在播条目原始数据同清
        self._use_mci = False

    def _resume_suspended_999(self):
        """A7 续播：回退 1 秒后从暂停点继续（打断者播毕即续播，不等队列排空——R2-A7b）。

        回退口径（E2/F-5）：1 秒 = 输出采样率帧数（speed≠1 时按输出流速率折算，
        即听众时间轴回退 1 秒）。999 机制 2026-11-15 已退役——本方法服务
        d12 泛化让位的挂起续播（方法名保留=历史命名）。
        """
        item = self._suspended_999
        held = self._suspended_play
        self._suspended_999 = None
        self._suspended_999_at = None
        self._suspended_999_alarm_at = None
        self._suspended_play = None
        self._interruptor_pending = False
        try:
            wf = held["wf"]
            rewind = int(self._current_output_rate * 1.0) if self._current_output_rate else 0
            if held.get("mci"):
                # MCI 后端续播：重新打开设备并 seek 回退 1 秒后 play（语义同
                # 内存游标回退；MCI 位置单位为毫秒）
                self.stream = _MciStream(held["tmp_audio_path"])
                self._use_mci = True
                target_ms = max(0, int(held.get("pos_ms") or 0) - 1000)
                self.stream.seek_ms(target_ms)
                logger.info(f"让位条目续播回退 1 秒（MCI：暂停点 {held.get('pos_ms')}ms "
                            f"→ {target_ms}ms）")
                self.pause_event.clear()
                self.stream.start_stream()
            else:
                if rewind > 0:
                    try:
                        # 内存游标回退（2026-11-13 抗 underrun 根修：回调已改内存
                        # PCM 切片，原 wf.tell/setpos 对内存游标无效——语义等价：
                        # 回退 1 秒 = 输出采样率帧数 × 帧宽字节；挂起现场无游标键
                        # 时回退 wf 文件游标（防御旧格式，正常路径不会命中）
                        _pos = held.get("pos")
                        _fw = held.get("frame_width") or 2
                        if _pos is not None:
                            pos_frames = _pos[0] // _fw
                            new_frames = max(0, pos_frames - rewind)
                            _pos[0] = new_frames * _fw
                            logger.info(f"让位条目续播回退 1 秒（{rewind} 帧，暂停点 {pos_frames}）")
                        else:
                            pos = wf.tell()
                            wf.setpos(max(0, pos - rewind))
                            logger.info(f"让位条目续播回退 1 秒（{rewind} 帧，暂停点 {pos}）")
                    except Exception:
                        logger.warning("续播回退设置失败（继续暂停点位置）")
                self.pause_event.clear()  # E-4：字幕挂起同步解除
                if self.stream is not None:
                    try:
                        self.stream.start_stream()
                    except Exception:
                        pass
            logger.info(f"让位条目续播：{item.get('voice_path')}")

            self._skip_event.clear()
            status = self._wait_loop()
            if status == "suspended":
                # e3 泛化（2026-11-05）：续播期间再次让位——重新登记挂起，
                # 条目不丢（新打断者播毕后经 _interruptor_pending 再续播）
                self._suspended_play = held
                self._suspended_999 = item
                self._suspended_999_at = time.monotonic()
                self._suspended_999_alarm_at = None
                self._interruptor_pending = True
                logger.info(f"续播期再次让位（重新挂起）：{item.get('voice_path')}")
                return
            if status == "completed":
                self._fire_completion(item)
            self._finish_cleanup(wf, held.get("tmp_audio_path"))
            self._post_play_interval()
        except Exception:
            logger.error("挂起续播异常\n" + traceback.format_exc())
            self._finish_cleanup(held.get("wf") if held else None,
                                 held.get("tmp_audio_path") if held else None)

    def _discard_suspended(self, reason):
        """F-3：清理路径（skip/clear/stop_all）放弃挂起的条目，日志留痕不静默。"""
        if self._suspended_999 is None:
            return
        held = self._suspended_play
        path = self._suspended_999.get("voice_path")
        self._suspended_999 = None
        self._suspended_999_at = None
        self._suspended_999_alarm_at = None
        self._suspended_play = None
        self._interruptor_pending = False
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
        logger.info(f"{reason}：放弃挂起条目（F-3）：{path}")

    def _post_play_interval(self):
        """播完后间隔（默认 0：间隔由 playback_manager 层负责，避免双重间隔）"""
        interval = float(self._hot_value("audio_interval", 0) or 0)
        random_interval = self._hot_value("random_audio_interval", None) or {}
        if random_interval.get("enable"):
            time.sleep(random.uniform(float(random_interval.get("min", 0)), float(random_interval.get("max", 0))))
        elif interval > 0:
            time.sleep(interval)

    def _play_item(self, data_json):
        self._playing_entry = None
        voice_path = data_json.get("voice_path")
        if not voice_path or not os.path.isfile(voice_path):
            self._record_error(f"音频文件不存在，跳过：{voice_path}")
            return "completed"
        if data_json.get("mode") == "url":
            # 内置模式仅消费本地产物（URL 场景属外部服务模式）
            self._record_error(f"builtin 不支持 url 模式，跳过：{voice_path}")
            return "completed"

        self._playing_weight = self._item_priority(data_json)  # e3/d12：泛化让位判定源
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
            self._playing_weight = None
            return "completed"

        wf = wave.open(tmp_audio_path, "rb")
        self._skip_event.clear()
        # 2026-11-17 组原子消费批次：在播条目原始数据（组原子让位门判定源；
        # 与 _current 摘要分离——摘要截断不含 split_group）
        self._playing_entry = data_json
        self._current = {
            "type": data_json.get("type", ""),
            "content": str(data_json.get("content", ""))[:20],
        }

        # PortAudio 回退路径的抗 underrun 处理（2026-11-13 根修，仅回退路径使用）：
        # PCM 一次性预读入内存，回调只做内存切片——避免实时线程做 Python 文件 IO。
        # MCI 后端不消费 PCM，故懒加载（长歌曲整轨可达数十 MB，不做无谓占用）。
        _frame_width = wf.getsampwidth() * wf.getnchannels()
        _pcm_state = {"pcm": b"", "pos": [0]}
        self._underrun_count = 0

        def callback(in_data, frame_count, time_info, status):
            if status and (status & pyaudio.paOutputUnderflow):
                self._underrun_count += 1  # 实时线程只计数不 IO
            _pcm = _pcm_state["pcm"]
            _pos = _pcm_state["pos"]
            start = _pos[0]
            if start >= len(_pcm):
                # EOF：让 PortAudio 排空缓冲后自然转 inactive（实证：喂完最后一帧
                # 后 PortAudio 不再调用回调，EOF 不能在此处作为完成信号）
                return (b"", pyaudio.paComplete)
            end = start + frame_count * _frame_width
            _pos[0] = min(end, len(_pcm))
            return (_pcm[start:end], pyaudio.paContinue)

        suspended = False
        try:
            # device_index 播放时现读（缺陷4 修复）；-1 = 系统默认（pyaudio 需 None）
            device_index = self._hot_value("device_index", -1)
            try:
                device_index = int(device_index)
            except (TypeError, ValueError):
                device_index = -1

            # A2 语义（字幕节#4 退役）：播放链不变速——条目 speed 恒 1（playback_manager
            # 重建硬编码），语速在合成侧生效（gpt_sovits speed_factor/edge-tts rate）。
            # speed 乘算退役：防未来误透传时真实播放时长偏离文件时长（字幕 keep_time 漂移）。
            rate = int(wf.getframerate())
            self._current_output_rate = rate  # F-5：让位续播回退 1 秒的折算基准

            # 播放后端选择（2026-11-13 助播撕裂卡顿根修）：MCI 原生播放不依赖
            # Python 供数 → 免疫主进程 GIL 争用；仅当用户显式指定输出设备
            # （device_index>=0，MCI 只能走系统默认设备）或 MCI 不可用时回退
            # PortAudio 回调流。
            self._use_mci = _MCI_AVAILABLE and device_index < 0
            if self._use_mci:
                try:
                    self.stream = _MciStream(tmp_audio_path)
                    self.stream.start_stream()
                except Exception:
                    logger.warning("MCI 播放启动失败，回退 PortAudio\n"
                                   + traceback.format_exc())
                    self._cleanup_stream()
                    self._use_mci = False
            if not self._use_mci:
                # PCM 一次性预读（仅回退路径）：回调只做内存切片，不在实时线程做 IO
                _pcm_state["pcm"] = wf.readframes(wf.getnframes())
                self.stream = self.audio.open(
                    format=self.audio.get_format_from_width(wf.getsampwidth()),
                    channels=wf.getnchannels(),
                    rate=rate,
                    output=True,
                    output_device_index=None if device_index < 0 else device_index,
                    frames_per_buffer=max(2048, int(rate * 0.1)),  # ~100ms 抗 underrun
                    stream_callback=callback,
                )
                self.stream.start_stream()
            logger.info(f"builtin播放开始：type={data_json.get('type')} path={voice_path}"
                        f" backend={'mci' if self._use_mci else 'portaudio'}")
            self._fire_started(data_json)  # F3：字幕推送时点=实际开播时

            status = self._wait_loop()

            if status == "suspended":
                # A7：现场登记后返回（流/wf/tmp 由挂起状态持有，续播或弃置时处理）；
                # 内存游标/帧宽随现场传递（抗 underrun 根修：续播回退改操作内存游标）；
                # MCI 后端登记暂停点毫秒（续播 seek 到 暂停点-1s，语义同回退 1 秒）
                suspended = True
                self._suspended_play = {
                    "wf": wf, "tmp_audio_path": tmp_audio_path,
                    "pos": _pcm_state["pos"], "frame_width": _frame_width,
                    "mci": self._use_mci,
                    "pos_ms": self.stream.position_ms() if self._use_mci else None,
                }
                # MCI 挂起：关闭设备释放文件句柄（续播时按回退位置重新打开）
                if self._use_mci:
                    self._cleanup_stream()
                self._suspended_999 = data_json
                self._suspended_999_at = time.monotonic()
                self._suspended_999_alarm_at = None  # 新挂起期重置看门狗去重
                self._interruptor_pending = True
                return "suspended"

            skipped = (status == "skipped")
            logger.info(f"builtin播放结束：{'跳过' if skipped else '完成'} type={data_json.get('type')}")
            if self._underrun_count:
                # underrun 观测（抗 underrun 根修）：输出欠载次数>0=曾发生供数
                # 断裂（听感撕裂/卡顿的物理证据），实播回归直接看本行
                logger.warning(f"builtin 输出 underrun ×{self._underrun_count}："
                               f"type={data_json.get('type')} path={voice_path}")

            if not skipped:
                self._fire_completion(data_json)
            self._finish_cleanup(wf, tmp_audio_path)
            self._post_play_interval()
            return status
        except Exception:
            self._record_error(f"播放失败（设备/解码）：{voice_path}\n" + traceback.format_exc())
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
    def internal_queue_len(self) -> int:
        """内部队列深度快照（TODOS 2026-10-28 消费逻辑审查 #4）：play() 提前移交
        的条目脱离 voice_tmp_path_queue，get_audio_info 叠加本值防观测面低估积压。"""
        with self.list_lock:
            return len(self.audio_json_list)

    def internal_normal_entries(self) -> list:
        """内部队列普通条目快照（2026-11-21 队列优化批次 E2/S3）：排除 song
        切片段（背景流不占合成背压门深度/秒数预算——间奏插播内容依赖预合成
        不被门滞留，与 synth_gate_wait_s 待播放队列口径一致）。本地路径 play()
        提前移交后播放器内部队列才是真实积压点——门控须叠加本队列。"""
        with self.list_lock:
            return [dict(e) if isinstance(e, dict) else e
                    for e in self.audio_json_list
                    if not (isinstance(e, dict) and e.get("type") == "song"
                            and e.get("song_segment"))]

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
        A5 单源：消费 filter.priority_mapping（经 _priority_mapping 三级回退）。
        T2/e2（2026-11-05）：条目有效权重经 audio_priority 单一源——
        priority_override 优先于类型映射（编排插队 500 在 builtin 队列同样
        生效；2026-11-15 批次 999 机制退役，reread_top_priority 映射 500 保持
        最高顺位；与 queue_manager._entry_priority 同源防漂移）。

        组原子地板（2026-11-17 组原子消费批次）：pending 区存在非动态文案
        切分段组（split_group）时，新条目不得插到其 pending 段之前——组段经
        play() 提前移交后已脱离消费循环队列，消费循环的组原子门管不到移交层，
        不设地板则后到高权重条目按权重插队会把移交中的组段拆散=组中间插播
        （违背组原子语义）。地板=最后一个非文案组 pending 段位置+1；插队只
        能落在组段之后（地板下移不触达 insert_index 显式定位路径——编排
        显式定位机制不越权改写）。"""
        from utils.audio.audio_priority import entry_priority
        priority_mapping = self._priority_mapping()

        # 组原子地板扫描（O(n)，队列上限 queue_max 有界）
        group_floor = 0
        for i, item in enumerate(self.audio_json_list):
            _sg = item.get("split_group") if isinstance(item, dict) else None
            if (isinstance(_sg, dict) and _sg.get("id")
                    and item.get("type") != "trends_copywriting"):
                group_floor = i + 1

        new_priority = entry_priority(audio_json, priority_mapping)
        if new_priority is None:
            insert_position = len(self.audio_json_list)
        else:
            insert_position = 0
            for i in range(len(self.audio_json_list) - 1, -1, -1):
                item_priority = entry_priority(self.audio_json_list[i], priority_mapping)
                if item_priority is not None and item_priority >= new_priority:
                    insert_position = i + 1
                    break
            if insert_position < group_floor:
                insert_position = group_floor  # 组原子地板：不让插到组 pending 段前
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
            # A7：挂起期手动续播不生效——插播条目播毕后自动续播（防挂起流
            # 被手动拉起与插播者双出声；E-4 同步由挂起状态维持）
            logger.info("让位挂起中，手动续播不生效（插播完成后自动续播）")
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
        F-3：挂起中的条目一并放弃（进下一条，日志留痕）"""
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
