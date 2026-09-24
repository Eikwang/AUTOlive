# -*- coding: UTF-8 -*-
"""
内置 web 字幕打印机（进程内模块）

移植自 D:\\AI\\captions_printer\\app.py 的 MessageQueueManager（队列+动态延时
防叠字+串行节流），按《audio_player与captions_printer整合计划》§5 改造：
- 修复 playback_manager.py:74-75 断链：本模块由 Audio 类持有，播放线程
  push(data_json) 进程内直调（原 send_to_web_captions_printer 方法不存在）；
- E-1：keep_time_align_audio=true 时 start_delay 强制 0（音频开始即显示，
  防内置拓扑下每句字幕系统性晚 2 秒）；防叠逻辑保留给积压场景；
- E-2：变速场景 keep_time 按 speed 折算（读的是变速前文件时长）；
- E-4：push 前检查播放器 paused 状态并阻塞等待（暂停期间字幕同步挂起）；
- E-5：sio emit 经带有效性检查的 loop 提交器，失败计数走错误可见性；
- D1/E-6：sio connect 时向新连接 emit 全量配置+enable 状态（OBS 刷新即恢复）；
- dx2：enable/样式经 config 文件 mtime 检测热读取（保存即生效，无需重启）；
- dx4：节流参数（单字延时）读 config 键 single_char_show_time（单一事实源）；
- S7：队列上限 queue_max（默认 100），满载丢最旧并告警。

显示契约沿用源项目：服务端→客户端事件 message（{content,start_delay,keep_time}）
与 config_update（含 enable 字段）；客户端→服务端无事件。
"""

import json
import os
import queue
import threading
import time
import traceback

from utils.my_log import logger

try:
    import soundfile as sf
except ImportError:  # soundfile 缺失时 keep_time 全量降级
    sf = None

# 源项目常量语义保留（降级计算用）
CHARACTER_DELAY_DEFAULT = 80      # ms/字符
DEFAULT_START_DELAY = 2000        # ms（仅 align=false 时的兜底起始延时）


def _load_config_file(config_path):
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


class _LoopSubmitter:
    """带有效性检查的 loop 提交器（eng E-5：引用缺失/失效/提交失败不得静默）"""

    def __init__(self, on_fail=None):
        self.loop = None
        self._fail_count = 0
        self._on_fail = on_fail  # 连续失败阈值回调（同步，错误条联动）

    def set_loop(self, loop):
        self.loop = loop

    def submit(self, coro):
        import asyncio
        try:
            if self.loop is None or self.loop.is_closed():
                raise RuntimeError("event loop 未就绪或已关闭")
            asyncio.run_coroutine_threadsafe(coro, self.loop)
            self._fail_count = 0
            return True
        except Exception as e:
            self._fail_count += 1
            logger.error(f"字幕 emit 提交失败（连续 {self._fail_count} 次）：{e}")
            if self._fail_count == 3 and self._on_fail is not None:
                try:
                    self._on_fail("字幕推送失败（连续 3 次）——请检查字幕页/OBS 源")
                except Exception:
                    pass
            return False

    @property
    def consecutive_failures(self):
        return self._fail_count


class CaptionsManager:
    """字幕队列节流管理器（进程内直调 + socket.io 广播）"""

    def __init__(self, config, config_path="config.json"):
        self.config = config            # Config 对象（类级共享快照；热值走文件现读）
        self.config_path = config_path
        self._config_mtime = 0.0
        self._captions_cfg = {}         # web_captions_printer 节快照（mtime 变化时刷新）

        self.message_queue = queue.Queue()
        self.lock = threading.Lock()
        self.last_message_time = None   # 上一条信息入队时间（防叠字，align=false 路径）

        self._sio = None                # web_server attach 时注入
        self.submitter = _LoopSubmitter()
        self._paused_getter = None      # 返回播放器 paused 布尔（eng E-4）
        self._error_notifier = None     # 错误条联动（dx 发现1）

        self._sync_config_if_changed(force=True)
        threading.Thread(target=self._process_message_queue, daemon=True,
                         name="captions-throttle").start()
        logger.info("内置字幕打印机管理器已创建（CaptionsManager）")

    # ---------- 注册接口（web_server / audio_core 调用） ----------
    def attach(self, sio):
        """注入 socket.io AsyncServer 并注册 connect 初始同步（D1/E-6）"""
        self._sio = sio

        async def _on_connect(sid, environ):
            try:
                cfg = dict(self._captions_cfg)
                await sio.emit("config_update", cfg, to=sid)
                logger.info(f"字幕页初始配置已同步（sid={sid}）")
            except Exception:
                logger.error("字幕页初始配置同步失败\n" + traceback.format_exc())

        sio.on("connect", _on_connect)
        logger.info("字幕 socket.io 已挂接（connect 初始同步已注册）")

    def set_loop(self, loop):
        """FastAPI startup 时捕获主系统 event loop（uvicorn 同 loop）"""
        self.submitter.set_loop(loop)

    def set_paused_getter(self, getter):
        """注入播放器 paused 状态读取（eng E-4）"""
        self._paused_getter = getter

    def set_error_notifier(self, notifier):
        """注入错误条联动回调（dx 发现1；web_server 桥接，同步调用）"""
        self._error_notifier = notifier

    def broadcast_config(self):
        """样式/enable 变更时向所有客户端广播 config_update（含 enable 字段）"""
        if self._sio is None:
            return
        cfg = dict(self._captions_cfg)
        ok = self.submitter.submit(self._sio.emit("config_update", cfg))
        if not ok:
            logger.error("config_update 广播失败（loop 未就绪）")

    # ---------- 配置热读取（dx2：mtime 检测，保存即生效） ----------
    def _sync_config_if_changed(self, force=False):
        try:
            mtime = os.path.getmtime(self.config_path) if os.path.exists(self.config_path) else 0.0
            if not force and mtime == self._config_mtime:
                return
            self._config_mtime = mtime
            cfg_all = _load_config_file(self.config_path) or {}
            self._captions_cfg = cfg_all.get("web_captions_printer") or {}
        except Exception:
            logger.error("字幕配置热读取失败\n" + traceback.format_exc())

    def _cfg(self, key, default):
        return (self._captions_cfg or {}).get(key, default)

    @property
    def enabled(self):
        return bool(self._cfg("enable", False))

    # ---------- 推送入口（修复断链：playback_manager 进程内直调） ----------
    async def push(self, data_json):
        """播放线程调用；全捕获（S4：字幕错误不得传播进播放循环）"""
        try:
            self._sync_config_if_changed()
            if not self.enabled:
                return
            content = str((data_json or {}).get("content", "") or "")
            if content == "":
                return  # 不推空字幕

            keep_time_ms, start_delay_ms = self._compute_timing(data_json or {}, content)
            self._enqueue(content, start_delay_ms, keep_time_ms)
        except Exception:
            logger.error("字幕 push 异常（已隔离，不影响播放）\n" + traceback.format_exc())

    def _compute_timing(self, data_json, content):
        """keep_time 与 start_delay 计算（E-1/E-2 修订）"""
        align = bool(self._cfg("keep_time_align_audio", True))
        single_char_ms = float(self._cfg("single_char_show_time", CHARACTER_DELAY_DEFAULT) or CHARACTER_DELAY_DEFAULT)
        auto_ms = len(content) * single_char_ms + DEFAULT_START_DELAY

        # E-2：音频时长按变速折算（push 点在变速前，voice_path 为变速前文件）
        keep_time_ms = auto_ms
        voice_path = data_json.get("voice_path")
        if align and sf is not None and voice_path and os.path.isfile(voice_path):
            try:
                info = sf.info(voice_path)
                audio_ms = float(info.duration) * 1000.0
                speed = float(data_json.get("speed", 1) or 1)
                if speed <= 0:
                    speed = 1.0
                keep_time_ms = max(audio_ms / speed, auto_ms)  # 保底显示时长
            except Exception:
                logger.error(f"音频时长读取失败，keep_time 降级自动计算：{voice_path}")
                keep_time_ms = auto_ms

        # E-1：对齐模式 start_delay=0（音频开始即显示）；非对齐走源防叠逻辑
        if align:
            start_delay_ms = 0
        else:
            start_delay_ms = 0
            with self.lock:
                now = int(time.time() * 1000)
                if self.last_message_time is not None:
                    # 防叠：上一条剩余显示时间内到达则顺延
                    start_delay_ms = max(0, int(auto_ms - (now - (self.last_message_time or now))))
                self.last_message_time = now
        return keep_time_ms, start_delay_ms

    def _enqueue(self, content, start_delay_ms, keep_time_ms):
        queue_max = int(self._cfg("queue_max", 100) or 100)
        try:
            self.message_queue.put_nowait((content, start_delay_ms, keep_time_ms))
        except queue.Full:
            # 满载丢最旧（S7：字幕可丢，台词不可丢）
            try:
                dropped = self.message_queue.get_nowait()
                logger.warning(f"字幕队列满载（>{queue_max}），丢弃最旧：{dropped[0][:20]}")
            except queue.Empty:
                pass
            self.message_queue.put_nowait((content, start_delay_ms, keep_time_ms))

    # ---------- 节流线程（串行消费：start_delay → emit → keep_time） ----------
    def _process_message_queue(self):
        while True:
            try:
                message_data = self.message_queue.get()
                if message_data is None:
                    break
                content, start_delay_ms, keep_time_ms = message_data

                # eng E-4：播放器暂停期间字幕同步挂起
                while True:
                    try:
                        if self._paused_getter is None or not self._paused_getter():
                            break
                    except Exception:
                        break  # 状态读取失败不阻塞字幕
                    time.sleep(0.2)
                    self._sync_config_if_changed()
                    if not self.enabled:
                        break  # 推送中关闭开关 → 放弃本条

                if start_delay_ms > 0:
                    time.sleep(start_delay_ms / 1000)

                self._emit("message", {
                    "content": content,
                    "start_delay": start_delay_ms,
                    "keep_time": keep_time_ms,
                })
                logger.info(f"字幕发送：{content[:20]}… keep={int(keep_time_ms)}ms delay={int(start_delay_ms)}ms")

                time.sleep(keep_time_ms / 1000)
            except Exception:
                logger.error("字幕节流线程异常\n" + traceback.format_exc())
                time.sleep(0.5)

    def _emit(self, event, payload):
        if self._sio is None:
            logger.warning("字幕 sio 未挂载，丢弃推送（页面/服务未启动）")
            return
        self.submitter.submit(self._sio.emit(event, payload))
