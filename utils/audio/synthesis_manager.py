"""
音频合成管理模块 - 管理TTS合成、变声等功能
"""

import asyncio
import aiohttp
import math
import os
import random
import shutil
import threading
import time
import traceback
from copy import deepcopy
from ..my_log import logger
from ..edtalk_realtime import is_edtalk_active
from .vocal_silences import silences_path_for as _silences_path_for
from .vocal_silences import load_silences as _load_silences
from .vocal_silences import compute_silences as _vs_compute
from .vocal_silences import write_silences_file as _vs_write
from ..edtalk_realtime import is_edtalk_active
from ..audio_handle.tts_params import clamp_speed_factor
from .. import alarm_notifier
from ..config import Config


class SynthesisMixin:
    """音频合成管理Mixin类"""

    # 配置热同步（2026-10-29 实测修复）：webui 与 main 各持内存副本且互不同步
    # （config_watcher 未接线，platform_config.py 同款结论）——用户在 TTS 页改
    # 参考文本/关闭无参考文本模式/换参考音频后，弹幕链永远读进程启动快照，合成
    # 仍用旧参考音频+旧转写（实测：降级重试打出旧参考文本"マスター…"）。
    # mtime 门控惰性重载：文件未变仅 os.stat 零开销；变化才 Config.reload
    # （类缓存刷新，进程内全部 Config 实例可见，与测试链 reload_config 同语义）。
    _disk_config_mtime_seen = 0.0

    def _sync_config_from_disk(self):
        """弹幕链合成入口的配置热同步（mtime 门控，未变化零开销）。"""
        try:
            mtime = os.path.getmtime(self.config_path)
        except OSError:
            return
        if mtime == SynthesisMixin._disk_config_mtime_seen:
            return
        SynthesisMixin._disk_config_mtime_seen = mtime
        if Config.reload(str(self.config_path)):
            logger.info("[config] 检测到 config.json 变化，合成链已热重载")
    
    async def message_queue_thread(self):
        """音频合成消息队列线程"""
        logger.info("创建音频合成消息队列线程")
        while True:  # 无限循环，直到队列为空时退出
            try:
                # 获取线程锁，避免同时操作
                with self.message_queue_lock:
                    while not self.message_queue:
                        # 消费者在消费完一个消息后，如果列表为空，则调用wait()方法阻塞自己，直到有新消息到来
                        self.message_queue_not_empty.wait()  # 阻塞直到列表非空
                    message = self.message_queue.pop(0)

                # 此处的message数据，是等待合成音频的数据，此数据经过了优先级排队在此线程中被取出，即将进行音频合成。
                # 由于有些对接的项目自带音频播放功能，所以为保留相关机制的情况下做对接，此类型的对接源码应写于此处
                #
                # 音频分流（EDTalk功能集成计划 §4.3.2，与启动条件对齐杜绝静音组合；
                # 2026-10-22 批次：metahuman_stream 驱动退役（G2）——本地 TTS 单路径）：
                # - edtalk_realtime 且 enable：走本地 TTS 合成 → 播放环节跳过本地播放、
                #   推送 /audio/push_full 给 EDTalk 帧锁定播放（playback_manager 分流）
                # - edtalk_realtime 但 enable=false：走本地 TTS + 本地正常播放
                #   （有声音、无数字人画面）——UI 侧常驻警告提示服务未启用
                if self.config.get("visual_body") == "edtalk_realtime" and not is_edtalk_active(self.config):
                    logger.warning(
                        "数字人服务未启用（edtalk_realtime.enable=false）："
                        "本次音频将走本地声音播放，无数字人画面与口型"
                    )

                # 合成音频并插入待播放队列
                await self.my_play_voice(message)

                # E2 饥饿路径 flush：突发结束（待合成队列空且无在途合成）时，
                # 若待播放队列积压未达首次播放阈值，立即放行——规则见
                # QueueMixin.flush_starved_playback_queue 注释
                self.flush_starved_playback_queue()

                # message = Audio.message_queue.get(block=True)
                # logger.debug(message)
                # await self.my_play_voice(message)
                # Audio.message_queue.task_done()

                # 加个延时 降低点edge-tts的压力
                # await asyncio.sleep(0.5)
            except Exception as e:
                logger.error(traceback.format_exc())

    # so_vits_svc_api 已随主流程变声退役（/autoplan 2026-10-22 批次 A-6：
    # 变声=独立 rvc-vc 流程，不参与系统主流程；外部 so-vits-svc 服务依赖解除）


    # ---------- ② 点歌切片插播（2026-10-27 架构优化批次） ----------

    _SONG_SEG_DIR_PREFIX = ".segs_"
    _SONG_SEG_STALE_S = 3600  # 泄漏兜底：切片目录超 1h 未清理则清扫

    # T5/F-C 惰性解析（2026-11-05 编排优化批次）：存量翻唱首次点播后台解析
    # vocal 静音段（类级 in-flight 注册表——多实例共享去重；超时=线程僵死放弃）
    _silence_parse_inflight: dict = {}
    _silence_parse_lock = threading.Lock()
    _SILENCE_PARSE_TIMEOUT_S = 120.0

    def _song_segment_seconds(self):
        """点歌切片时长读侧（play_audio.song_segment_seconds，缺省 45s；
        ≤0 = 关闭切片维持整曲单条目）。"""
        try:
            v = float(self.config.get("play_audio", "song_segment_seconds"))
        except (TypeError, ValueError):
            return 45.0
        return v if v > 0 else 0.0

    def _min_interlude_s(self):
        """间奏窗口最短读侧（2026-11-10 失步根修 R1）：检测静音段（-20dB/0.3s
        用户标准）中 ≥ 此值才作间奏（切段+插播窗口）；0.3-1.0s 停顿留在人声段
        内自然播（0.3s 窗口插不了任何音频——答谢 TTS 最短 ~2s；全按 0.3s 切段
        产生 50-100 碎段）。

        config 键 choose_song.min_interlude_s 兜底可调（缺省 1.0，未落盘）；
        钳制 [vocal_silences.DEFAULT_MIN_SILENCE_S(0.3), 10.0]——0.0 会退化成
        每 0.3s 停顿全切（碎段爆炸）、过大使插播永久关闭（Eng F7/D26）。"""
        from utils.audio.vocal_silences import DEFAULT_MIN_SILENCE_S
        try:
            v = float(self.config.get("choose_song", "min_interlude_s"))
        except (TypeError, ValueError):
            v = 1.0
        if not (v > 0):  # 0/负值/None=回缺省
            v = 1.0
        clamped = min(max(v, DEFAULT_MIN_SILENCE_S), 10.0)
        if clamped != v:
            logger.info(
                f"[song] min_interlude_s 钳制：{v} → {clamped}"
                f"（合法区间 [{DEFAULT_MIN_SILENCE_S}, 10.0]s）")
        return clamped

    def _purge_stale_song_segments(self, base_dir):
        """陈旧切片目录清扫（泄漏兜底：跳过/打断导致末段清理未触达时，下次
        切片前按目录 mtime 清扫超时目录）。"""
        try:
            if not os.path.isdir(base_dir):
                return
            now = time.time()
            for name in os.listdir(base_dir):
                if not name.startswith(self._SONG_SEG_DIR_PREFIX):
                    continue
                path = os.path.join(base_dir, name)
                try:
                    if now - os.path.getmtime(path) > self._SONG_SEG_STALE_S:
                        shutil.rmtree(path, ignore_errors=True)
                        logger.info(f"[song] 陈旧切片目录清扫：{path}")
                except OSError:
                    pass
        except Exception:
            logger.error(traceback.format_exc())

    def _split_audio_segments(self, voice_path, seg_seconds):
        """点歌音频切片（② 可打断背景位）：按 N 秒切片分条目入队——回复等
        更高优先级音频可经优先级排序插播到片段之间，打断延迟上限=单片段时长。

        切片文件置于源文件同目录隐藏子目录 .segs_<ns>/（末段播放完 rmtree，
        异常残留由 _purge_stale_song_segments 兜底）。

        Args:
            voice_path (str): 歌曲音频文件路径
            seg_seconds (float): 单片段秒数（>0）

        Returns:
            list[dict] | None: 片段列表 [{voice_path, seq, total, seg_dir}]；
                短音频/失败返回 None（调用方降级整曲单条目，原语义）
        """
        try:
            from pydub import AudioSegment
            seg = AudioSegment.from_file(voice_path)
            total_ms = len(seg)
            if total_ms <= seg_seconds * 1000:
                return None
            self._purge_stale_song_segments(os.path.dirname(voice_path) or ".")
            seg_ms = int(seg_seconds * 1000)
            count = math.ceil(total_ms / seg_ms)
            seg_dir = os.path.join(
                os.path.dirname(voice_path) or ".",
                f"{self._SONG_SEG_DIR_PREFIX}{time.time_ns()}")
            os.makedirs(seg_dir, exist_ok=True)
            out = []
            for i in range(count):
                piece = seg[i * seg_ms:(i + 1) * seg_ms]
                p = os.path.join(seg_dir, f"seg_{i:02d}.mp3")
                piece.export(p, format="mp3")
                # start_s（2026-11-09 失步根修）：固定切片按等长推进——消费侧
                # 段间窗口门（含播完保底）同样生效，消除固定切片段间
                # push 互踩截断（原语义=背靠背连推）。
                out.append({"voice_path": p, "seq": i, "total": count,
                            "seg_dir": seg_dir,
                            "start_s": round(i * seg_seconds, 3)})
            logger.info(f"[song] 点歌切片：{count} 段 × {seg_seconds:g}s"
                        f"（回复音频可按优先级插播）：{voice_path}")
            return out
        except Exception:
            logger.error("点歌切片失败，降级整曲单条目播放\n" + traceback.format_exc())
            return None

    def _semantic_or_fixed_slices(self, voice_path: str, message: dict):
        """T5/d1/d15/d20（2026-11-05 批次）：EDTalk 路径切片策略——

        - silences 数据文件存在（d15 旁挂）→ 语义切片（人声段=条目，间奏=
          插入窗口）；空 silences=合法「无间奏」→ 整曲单段（人工边界不引入）。
        - 无数据文件/损坏（e12 fail-soft）→ 45s 固定切片回退 + F-C 惰性解析
          调度（后台，本播不等待；完成后下次点播走语义切片）。
        - 切片关闭（song_segment_seconds≤0）→ None（整曲单条目）。

        Returns: (segments, silences)——segments 与 _split_audio_segments 同构
        （含 gap_after_s 间奏元数据），None=不可切片。
        """
        full = message.get("full_path") or ""
        sp = _silences_path_for(full) if full and os.path.isfile(full) else None
        silences = None
        if sp:
            silences = _load_silences(sp)
        if silences is None:
            self._schedule_lazy_silence_parse(full, voice_path)
        else:
            segs = self._split_by_silences(voice_path, silences)
            if segs:
                return segs, silences
        _seg_seconds = self._song_segment_seconds()
        if _seg_seconds > 0:
            return self._split_audio_segments(voice_path, _seg_seconds), None
        return None, silences

    def _split_by_silences(self, voice_path: str, silences):
        """d1：人声段=条目（silences 互补区间），间奏=插入窗口。目录/命名/
        陈旧清扫复用 _split_audio_segments 既有机制；条目附 gap_after_s
        （该段之后的间奏秒数——T7 插播窗口对齐与 X1 观测消费）。

        2026-11-10 R1：入口按 _min_interlude_s 过滤（检测标准 -20dB/0.3s 照
        用户指定；窗口粒度解耦——<min_interlude_s 的静音段不触发切片/不产生
        插播窗口，留在人声段内自然播放；gap_after 按过滤后区间计算=一致）。

        Returns: list[dict]（恒非空：silences 空=整曲单段；导出失败=[]）。"""
        try:
            from pydub import AudioSegment
            _interlude_min = self._min_interlude_s()
            _kept = [g for g in (silences or [])
                     if float(g["end_s"]) - float(g["start_s"]) >= _interlude_min]
            if len(_kept) != len(silences or []):
                logger.info(
                    f"[song] 间奏窗口过滤（min_interlude_s={_interlude_min}s）："
                    f"{len(silences or [])} 段检测静音 → {len(_kept)} 段间奏"
                    "（短停顿留人声段内自然播）")
            silences = _kept
            seg = AudioSegment.from_file(voice_path)
            total_s = len(seg) / 1000.0
            # 人声段=silences 互补区间（钳制到音频实际时长内）
            runs = []
            cursor = 0.0
            for gap in silences:
                if gap["start_s"] > cursor:
                    runs.append((cursor, min(gap["start_s"], total_s)))
                cursor = max(cursor, gap["end_s"])
                if cursor >= total_s:
                    break
            if cursor < total_s:
                runs.append((cursor, total_s))
            if not runs:
                return []
            self._purge_stale_song_segments(os.path.dirname(voice_path) or ".")
            seg_dir = os.path.join(
                os.path.dirname(voice_path) or ".",
                f"{self._SONG_SEG_DIR_PREFIX}{time.time_ns()}")
            os.makedirs(seg_dir, exist_ok=True)
            out = []
            total = len(runs)
            for i, (s, e) in enumerate(runs):
                piece = seg[int(s * 1000):int(e * 1000)]
                p = os.path.join(seg_dir, f"seg_{i:02d}.mp3")
                piece.export(p, format="mp3")
                gap_after = 0.0
                if i < total - 1:
                    gap_after = round(runs[i + 1][0] - e, 3)
                # start_s（2026-11-09 失步根修）：段在歌曲时间轴的绝对起点——
                # 消费侧窗口门（_song_gate_wait_s）据此对齐伴奏时钟（人声段
                # 应在其歌曲位置起播，间奏时长不再被跳过）。首段 s>0=vocal
                # 前奏静音（伴奏先起），同样由消费侧前奏等待对齐。
                out.append({"voice_path": p, "seq": i, "total": total,
                            "seg_dir": seg_dir, "start_s": round(s, 3),
                            "gap_after_s": gap_after})
            logger.info(
                f"[song] 语义切片（d1/d15）：{total} 个人声段"
                f"（间奏 {len(silences)} 段）：{voice_path}")
            return out
        except Exception:
            logger.warning("[song] 语义切片失败，回退固定切片/整曲："
                           + traceback.format_exc())
            return []

    def _schedule_lazy_silence_parse(self, full_path: str, vocal_path: str):
        """F-C/d20：存量翻唱首次点播——后台解析 vocal 分轨静音段（本播 45s
        固定切片立即起播不等待），完成后写 .silences.json 供后续点播语义切片。
        守卫：仅 covers（full_path 成品旁存在 <base>_vocal.wav 分轨）——用户
        上传原曲无分轨=无静音数据源（45s 永久回退，d20）。in-flight 去重+
        超时放弃（观测桶 audio_silences_parse_timeout/fail）。"""
        if not full_path or not vocal_path or not os.path.isfile(full_path):
            return
        base = os.path.splitext(full_path)[0]
        vocal = f"{base}_vocal.wav"
        if not os.path.isfile(vocal):
            return
        sp = _silences_path_for(full_path)
        with self._silence_parse_lock:
            started = self._silence_parse_inflight.get(sp)
            if started is not None:
                if time.monotonic() - started <= self._SILENCE_PARSE_TIMEOUT_S:
                    return  # 解析中（去重，不重复调度）
                try:
                    from utils.obs_stats import record_drop
                    record_drop("audio_silences_parse_timeout")
                except Exception:
                    pass
            self._silence_parse_inflight[sp] = time.monotonic()

        def _work():
            try:
                silences = _vs_compute(vocal)
                if _vs_write(sp, silences):
                    logger.info(f"[silences] 惰性解析完成：{sp}"
                                f"（{len(silences)} 段间奏，下次点播语义切片生效）")
            except Exception:
                try:
                    from utils.obs_stats import record_drop
                    record_drop("audio_silences_parse_fail")
                except Exception:
                    pass
                logger.warning(f"[silences] 惰性解析失败（继续 45s 回退）：{sp}",
                               exc_info=True)
            finally:
                with self._silence_parse_lock:
                    self._silence_parse_inflight.pop(sp, None)

        threading.Thread(target=_work, name="silences-lazy-parse",
                         daemon=True).start()
        logger.info(f"[silences] 惰性解析已调度（本播固定切片不等待——F-C）：{sp}")

    def audio_synthesis(self, message):
        """音频合成（edge-tts / vits_fast等）并播放"""
        try:
            # TTS类型为 none 时不合成音频
            if self.config.get("audio_synthesis_type") == "none":
                return

            # 用户名表情/特殊字符过滤（filter.emoji 同开关，2026-10-31 过滤
            # 优化批次）：合成念用户名前的最终关口——弹幕链已过滤，语音对话/
            # 念弹幕回放/答谢等其余来源的 username 在此兜底；先清洁后数字转
            # 中文（共同构成用户名合成前处理链）。清理为空保留原文兜底。
            if self.config.get("filter", "emoji"):
                if message.get("username"):
                    message["username"] = (self.common.clean_username(message["username"])
                                           or message["username"])

            # 将用户名字符串中的数字转换成中文
            if self.config.get("filter", "username_convert_digits_to_chinese"):
                if message["username"] is not None:
                    message["username"] = self.common.convert_digits_to_chinese(message["username"])

            # 判断是否是点歌模式
            if message['type'] == "song":
                voice_path = message['content']
                _backing = message.get("backing_path") or ""
                # O6/O7（2026-10-28 消费批次）：编排实例序号+插队权重随歌曲
                # 直通链透传（整曲与切片两种 data_json 都要带，切片每片同 ID）
                _choreo_keys = {k: message[k] for k in ("choreo_step_id", "priority_override")
                                if message.get(k) is not None}
                # ② 点歌切片插播（2026-10-27 基础上 T5/d1/d15/d20 升级，2026-11-05）：
                # 切片策略三态——
                # a) EDTalk 激活（人声推数字人）：语义切片优先（silences 数据文件
                #    人声段=条目，间奏=插入窗口，d1/d15）；无数据文件/损坏→45s
                #    固定切片回退（d1）+F-C 惰性解析调度（后台，本播不等待）
                # b) 双轨（uc4b 终门裁决：伴奏继续+人声失步）：语义切片/固定切片
                #    逐片入队，伴奏轨仅首片承载（起播门=首片 push_full 受理放行
                #    ——2026-11-04 两阶段门保留），后继片无 backing_path=伴奏线程
                #    不重启（「逐段重启伴奏语义错误」约束在 uc4b 语义下解除：
                #    伴奏连续播放，人声逐片推 EDTalk，失步=插播时长+累积 e13 声明）
                # c) EDTalk 未激活（builtin 本地 full_path 整轨）：维持 45s 固定
                #    切片现行为（F-D：vocal 静音窗对整曲混音无意义；builtin 插播
                #    机制重整=让位泛化，归 T6/e3）
                _edtalk = is_edtalk_active(self.config)
                segments = None
                if _edtalk:
                    segments, _silences = self._semantic_or_fixed_slices(voice_path, message)
                else:
                    _seg_seconds = self._song_segment_seconds()
                    segments = (self._split_audio_segments(voice_path, _seg_seconds)
                                if _seg_seconds > 0 else None)
                if segments:
                    for s in segments:
                        data_json = {
                            "type": "song",
                            "tts_type": "none",
                            "voice_path": s["voice_path"],
                            "content": message.get("content", voice_path),
                            "song_segment": {
                                "seq": s["seq"], "total": s["total"],
                                "seg_dir": s["seg_dir"],
                                # 歌曲时间轴起点（2026-11-09 失步根修：窗口门对齐锚）
                                "start_s": s.get("start_s", 0.0),
                            },
                        }
                        # uc4b 双轨逐片：伴奏仅首片承载（后继片重启伴奏=已判死语义）
                        if s["seq"] == 0 and _backing:
                            data_json["backing_path"] = _backing
                            data_json["backing_volume"] = message.get("backing_volume", 0.8)
                        # 间奏窗口元数据（T7 插播窗口对齐与 X1 观测消费；切片无间
                        # 奏语义时不带键=固定切片无窗口）
                        if s.get("gap_after_s"):
                            data_json["gap_after_s"] = s["gap_after_s"]
                        data_json.update(_choreo_keys)
                        # 是否开启了音频播放
                        if self.config.get("play_audio", "enable"):
                            self.data_priority_insert("等待合成消息", data_json)
                    return
                # 拼接json数据，存入队列（R6 双轨：backing_path 伴奏轨+音量透传，
                # /autoplan 2026-10-23 批次——EDTalk 分支伴奏外放；full_path 完整
                # 混音成品透传（2026-11-04 本地整轨）：EDTalk 未激活时本地分支播
                # 完整歌曲（前端预览同源），不再丢伴奏轨只出清唱）
                data_json = {
                    "type": message['type'],
                    "tts_type": "none",
                    "voice_path": voice_path,
                    "content": message.get("content", voice_path),
                    "backing_path": _backing,
                    "backing_volume": message.get("backing_volume", 0.8),
                    "full_path": message.get("full_path") or "",
                }
                data_json.update(_choreo_keys)

                # 是否开启了音频播放
                if self.config.get("play_audio", "enable"):
                    self.data_priority_insert("等待合成消息", data_json)
                return
            # 是否为本地问答音频
            elif message['type'] == "local_qa_audio":
                # 拼接json数据，存入队列
                data_json = {
                    "type": message['type'],
                    "tts_type": "none",
                    "voice_path": message['file_path'],
                    "content": message["content"]
                }

                # 是否开启了音频播放
                if self.config.get("play_audio", "enable"):
                    self.data_priority_insert("等待合成消息", data_json)
                return
            # 是否为助播-本地问答音频
            elif message['type'] == "assistant_anchor_audio":
                # 拼接json数据，存入队列
                data_json = {
                    "type": message['type'],
                    "tts_type": "none",
                    "voice_path": message['file_path'],
                    "content": message["content"]
                }

                # 是否开启了音频播放
                if self.config.get("play_audio", "enable"):
                    self.data_priority_insert("等待合成消息", data_json)
                return

            # 闲时任务
            elif message['type'] == "idle_time_task":
                # content_type 安全取值（2026-10-30 过滤审查批次）：text 类闲时消息
                # 无 content_type 键，原直接索引 KeyError 中断播报；["comment","reread"]
                # 分支为无生产者的死引用（复读退役）——仅保留 local_audio 分支，
                # 其余（text/TTS 类）照常落入下方合成链路
                if message.get('content_type') == "local_audio":
                    # A11（2026-10-22 审查批次，义务 R2-A11/E4）：本地音频类维持 enable
                    # 门控（原分支漏检直接入队，关闭态仍会播出——审查发现 R1(7)）；
                    # 本地音频不走 EDTalk 豁免（F-7：闲时音效不应推给数字人端）。
                    if not self.config.get("play_audio", "enable"):
                        return
                    # 拼接json数据，存入队列
                    data_json = {
                        "type": message['type'],
                        "tts_type": "none",
                        "voice_path": message['file_path'],
                        "content": message["content"]
                    }
                    
                    self.data_priority_insert("等待合成消息", data_json)

                    return
            # 按键映射 本地音频
            elif message['type'] == "key_mapping" and "file_path" in message:
                # 拼接json数据，存入队列
                data_json = {
                    "type": message['type'],
                    "tts_type": "none",
                    "voice_path": message['file_path'],
                    "content": message["content"]
                }

                # 是否开启了音频播放
                if self.config.get("play_audio", "enable"):
                    self.data_priority_insert("等待合成消息", data_json)
                return

            # 是否语句切分
            if self.config.get("play_audio", "text_split_enable"):
                sentences = self.common.split_sentences(message['content'])
                # 任务 12 根修（TODOS 2026-10-22 音频批次节：UI-16 R7 免责段删除后
                # 失效模式失去用户可见面）：切分段带组绑定标记（split_group），
                # 队满丢段时 queue_manager 据此输出组上下文 warning（第几段/共几段），
                # 丢数据从静默 info 升级为可定位的可见告警；入队前容量预检提前暴露
                # 丢弃风险（log warning，不打断切分——防丢优先于防提示）。
                _group_id = f"{message['type']}_{time.time_ns()}"
                _total = len(sentences)
                try:
                    _mq_max = int(self.config.get("filter", "message_queue_max_len"))
                    _mq_now = len(self.message_queue)
                    if _total > max(0, _mq_max - _mq_now):
                        logger.warning(
                            f"[长回复切分] {message['type']} 共 {_total} 段，消息队列余量 "
                            f"{max(0, _mq_max - _mq_now)}——超容部分将被丢弃（"
                            f"filter.message_queue_max_len={_mq_max}）；"
                            f"fix=调大队列上限或关闭 play_audio.text_split_enable")
                except Exception:
                    pass
                for _seq, s in enumerate(sentences):
                    message_copy = deepcopy(message)  # 创建 message 的副本
                    message_copy["content"] = s  # 修改副本的 content
                    message_copy["split_group"] = {"id": _group_id, "seq": _seq, "total": _total}
                    logger.debug(f"s={s}")
                    if not self.common.is_all_space_and_punct(s):
                        self.data_priority_insert("等待合成消息", message_copy)  # 将副本放入队列中
            else:
                self.data_priority_insert("等待合成消息", message)
            

            # 单独开线程播放
            # threading.Thread(target=self.my_play_voice, args=(type, data, config, content,)).start()
        except Exception as e:
            logger.error(traceback.format_exc())
            return


    # voice_change 已随主流程变声退役（/autoplan 2026-10-22 批次 A-6：变声=独立
    # rvc-vc 流程不参与主流程；so_vits_svc 分支退役后本方法为空壳，一并移除）

    def _resolve_random_synth_speed(self):
        """A2（2026-10-22 审查批次）：随机语速源迁移合成侧（义务 R2-A2/spec C-1）。

        读 audio_random_speed.normal.{enable,speed_min,speed_max}——键位保留、
        语义变更为合成侧随机语速源（旧 pydub 读侧随路径退役）；enable=false 或
        取值非法时返回 None（不变速=系统默认）。
        边界：tts_type=none 的本地音频/点歌在 audio_synthesis 短路直插播放队列、
        不经本函数，天然满足"本地音频与点歌不变速"边界声明（song 条目
        tts_type=none 已带标记，Feasibility 条件核实关闭）。
        """
        try:
            if not self.config.get("audio_random_speed", "normal", "enable"):
                return None
            _warn = lambda m: logger.warning(f"[变速] {m}")
            lo = clamp_speed_factor(self.config.get("audio_random_speed", "normal", "speed_min"), log_bad=_warn)
            hi = clamp_speed_factor(self.config.get("audio_random_speed", "normal", "speed_max"), log_bad=_warn)
            lo, hi = min(lo, hi), max(lo, hi)
            factor = random.uniform(lo, hi)
            logger.info(f"[变速] 随机语速 factor={factor:.3f}（域 [{lo}, {hi}]）")
            return factor
        except Exception:
            logger.error(traceback.format_exc())
            return None

    def _build_play_entry(self, message, voice_tmp_path, synth_speed=None):
        """拼接播放条目 json（put_to_queue/流式分段 sink 共用，2026-10-30
        审查批次提取——分段入队与单文件入队键面同源，防漂移）。"""
        data_json = {
            "type": message['type'],
            "voice_path": voice_tmp_path,
            "content": message["content"]
        }

        # 任务 12 根修：切分段组绑定透传（音频队列丢段可见面，queue_manager 消费）
        if message.get("split_group"):
            data_json["split_group"] = message["split_group"]

        # D-22：流式回复分句标记透传（queue_manager 连续对话待播舍弃豁免判定）
        if message.get("talk_stream"):
            data_json["talk_stream"] = True

        # P26：合成侧已应用语速时随条目透传（playback_manager 据此跳过 pydub 变速）
        if synth_speed is not None:
            data_json["synth_speed"] = synth_speed

        # O6/O7（2026-10-28 消费批次）：编排实例序号+插队权重透传到播放条目——
        # 播放完成回执按 choreo_step_id 精确关联（同内容重复入队不误配），
        # data_priority_insert 按 priority_override 插队（高于常规语音域）
        if message.get("choreo_step_id"):
            data_json["choreo_step_id"] = message["choreo_step_id"]
        if message.get("priority_override") is not None:
            data_json["priority_override"] = message["priority_override"]

        if "insert_index" in message:
            data_json["insert_index"] = message["insert_index"]

        return data_json

    async def tts_handle(self, message):
        """根据本地配置，使用TTS进行音频合成，返回相关数据

        Args:
            message (dict): json数据，含tts配置，tts类型

        Returns:
            dict: json数据，含tts配置，tts类型，合成结果等信息
        """

        try:
            # 配置热同步（2026-10-29）：webui 改键落盘后无需重启即生效（mtime 门控，
            # 未变化零开销）；置于本链所有 config 读取之前（need_lang/params 装配）
            self._sync_config_from_disk()
            # A2：随机语速源合成侧解析（enable=false → None=系统默认；本地音频不经此路径）
            _random_speed = self._resolve_random_synth_speed()
            # 流式分段清单（2026-10-30 审查批次）：仅 gpt_sovits 分支填充
            seg_paths_collector: list = []

            if message["tts_type"] == "gpt_sovits":
                # P25 键面重构（实测修复）：旧 data.language/api_0322/api_0706/v2_api_0821/webtts
                # 键随参数精简退役——inproc 装配走 params 白名单（my_tts._gpt_sovits_inproc），
                # api 兜底路径用 .get({}) 保旧结构不炸。语言：params.text_lang 缺省"自动识别"
                # （动态检测后回写代码值，inproc 装配透传）。
                _gdata = message["data"]
                _params = _gdata.get("params") or {}
                _lang_raw = _params.get("text_lang") or _gdata.get("language") or "自动识别"
                if _lang_raw == "自动识别":
                    # 动态检测语言（ref_free 模式配套）——2026-10-22 批次（P13 潜伏缺陷修复）：
                    # 原调用的 common.lang_check 已弃用不存在（AttributeError 雷），
                    # 改走新建轻量 lang_check（TextMixin，粤/谚/假/拉/汉字符区段判定）
                    language = self.common.lang_check(message["content"])
                    logger.debug(f'language={language}')
                    _lang_code = language if language in ("en", "zh", "ja", "ko") else "zh"
                    _params.setdefault("text_lang", _lang_code)
                else:
                    # 显示名→代码值（inproc 装配 text_lang 需代码值；params.text_lang 优先已回写）
                    _name_to_code = {"英文": "en", "中文": "zh", "日文": "ja", "韩文": "ko",
                                     "所有": "auto", "粤语": "auto_yue"}
                    _lang_code = _name_to_code.get(_lang_raw, _params.get("text_lang", "zh"))
                    language = _lang_code

                # need_lang（回复语言）映射覆盖 params 静态值（G3/spec L1 落地路径，2026-10-22）——
                # 代码值直通 data.language→my_tts._gpt_sovits_inproc merged.text_lang→引擎
                # （{none→auto, zh→zh, en→en, jp→ja, ko→ko, yue→auto_yue}）
                _need_lang = self.config.get("need_lang") or "none"
                _need_to_text_lang = {"none": "auto", "zh": "zh", "en": "en",
                                      "jp": "ja", "ko": "ko", "yue": "auto_yue"}
                if _need_lang in _need_to_text_lang:
                    _lang_code = _need_to_text_lang[_need_lang]
                    language = _lang_code

                # 兜底数据链收敛（TODOS 基础功能节 #10 评估落地）：my_tts.gpt_sovits_api
                # 旧六分支退役为 v2 单通道（params 装配）——本构造同步清理死键
                # （type/gradio_ip_port/api_0322/api_0706/v2_api_0821/webtts，UI 侧
                # 已无参数卡+config 无存量，透传键无消费端）。
                # 流式分段（2026-10-30 审查批次，TTS 页"流式输出"开关）：引擎逐段
                # 产出即独立入队（首段不等全文合成完）——sink 在合成线程内同步执行，
                # data_priority_insert 持锁安全；条目键面与 put_to_queue 同源
                # （_build_play_entry）。段清单经 data 回传（gpt_sovits_api 返回值
                # 保持 audio_path str 契约不变），tts_handle 尾段收进 result。
                _stream_on = bool((self.config.get("gpt_sovits", "stream_reply") or {}).get("enable", False)
                                  if isinstance(self.config.get("gpt_sovits", "stream_reply"), dict)
                                  else self.config.get("gpt_sovits", "stream_reply", False))

                def _on_segment(seg_path, idx, _m=message, _speed=_random_speed,
                                _collector=seg_paths_collector):
                    _collector.append(seg_path)
                    _entry = self._build_play_entry(_m, seg_path, synth_speed=_speed)
                    if (self.config.get("play_audio", "enable")
                            or is_edtalk_active(self.config)):
                        self.data_priority_insert("待播放音频列表", _entry)

                data = {
                    "api_ip_port": _gdata.get("api_ip_port", ""),
                    "params": _params,
                    "ref_audio_path": _gdata.get("ref_audio_path", _params.get("ref_audio_path", "")),
                    "prompt_text": _gdata.get("prompt_text", _params.get("prompt_text", "")),
                    "prompt_language": _gdata.get("prompt_language", _params.get("prompt_lang", "zh")),
                    "language": _lang_code,
                    "cut": _gdata.get("cut", _params.get("text_split_method", "cut3")),
                    # P26/A2：运行时语速透传（合成参数变速；None=系统默认）
                    "speed": _random_speed,
                    "content": message["content"]
                }
                if _stream_on:
                    data["_on_segment"] = _on_segment

                voice_tmp_path = await self.my_tts.gpt_sovits_api(data)
            elif message["tts_type"] == "edge_tts":
                # edge-tts 合成（切片 3：WebTTS 退役改用 EDGE-TTS；P26/A2 语速经 rate 映射+pitch 联动）
                voice_tmp_path = await self.my_tts.edge_tts_api({
                    "content": message["content"],
                    "speed": _random_speed,
                })
            elif message["tts_type"] == "none":
                # Audio.voice_tmp_path_queue.put(message)
                voice_tmp_path = None

            message["result"] = {
                "code": 200,
                "msg": "合成成功",
                "audio_path": voice_tmp_path
            }
            # 流式分段清单回收（2026-10-30 审查批次）：非空=流式模式（audio_synthesis
            # 据此跳过 put_to_queue 防首段双播）
            if seg_paths_collector:
                message["result"]["segment_paths"] = list(seg_paths_collector)
            # P26/A2：合成侧已应用语速时随结果透传（播放侧据此识别；pydub 回退路径已退役）
            if voice_tmp_path and _random_speed is not None:
                try:
                    message["result"]["synth_speed"] = float(_random_speed)
                except (TypeError, ValueError):
                    pass
        except Exception as e:
            logger.error(traceback.format_exc())
            message["result"] = {
                "code": -1,
                "msg": f"合成失败，{e}",
                "audio_path": None
            }

        return message

    # A3（2026-10-22 审查批次）：send_audio_play_info_to_callback 方法随
    # play_audio.info_to_callback 参数退役删除——回路接收端（/callback 端点）本就
    # 仅打日志（死链，wait_play_audio_num 恒 0 从未写入），移除零功能损失；
    # builtin 完成回调注册一并移除（audio_core）。

    async def audio_synthesis_use_local_config(self, content, audio_synthesis_type="gpt_sovits"):
        """使用本地配置进行音频合成，返回音频路径（页面合成测试链 /tts/synthesize）

        【2026-10-22 审查批次自 processing_manager.py 迁入】——原文件随 pydub 变速
        路径（audio_speed_change）与文案合并（merge_audio_files）退役删除，本测试链
        方法为在飞消费点（web_server /tts/synthesize），迁移至 SynthesisMixin 保链。
        D-20 双代码路径纪律：本方法=页面测试链，与弹幕链（tts_handle）同键面但独立
        存在，改键面时两链同改。

        切片 3 键面重写（2026-10-20 实测修复）：与弹幕链同键面——gpt_sovits 参数装配
        收口在 _gpt_sovits_inproc（config.params 为事实来源，C-1），edge_tts 走
        edge_tts_api（voice/rate 自 edge_tts 节）。

        Args:
            content (str): 待合成的文本内容
            audio_synthesis_type (str, optional): 使用的tts类型. Defaults to "gpt_sovits".

        Returns:
            str: 合成的音频的路径（失败返回 None）
        """
        # 重载配置
        self.reload_config(self.config_path)

        voice_tmp_path = None
        if audio_synthesis_type == "gpt_sovits":
            data = {
                # P26：测试链默认语速取 params.speed_factor（与弹幕链 speed 透传同语义；
                # 测试链为静态配置语速，不走弹幕链随机语速源——语义差异登记 D-20）
                "speed": self.config.get("gpt_sovits", "params", "speed_factor"),
                "content": content
            }
            voice_tmp_path = await self.my_tts.gpt_sovits_api(data)
        elif audio_synthesis_type == "edge_tts":
            voice_tmp_path = await self.my_tts.edge_tts_api({"content": content})
        else:
            logger.error(f"不支持的合成类型：{audio_synthesis_type}（可选 gpt_sovits / edge_tts）")

        return voice_tmp_path


    async def my_play_voice(self, message):
        """合成音频并插入待播放队列

        Args:
            message (dict): 待合成内容的json串

        Returns:
            bool: 合成情况
        """
        try:
            # 如果是tts类型为none，暂时这类为直接播放音频，所以就丢给路径队列
            if message["tts_type"] == "none":
                self.data_priority_insert("待播放音频列表", message)
                return
        except Exception as e:
            logger.error(traceback.format_exc())
            return

        try:
            logger.debug(f"合成音频前的原始数据：{message['content']}")
            message["content"] = self.common.remove_extra_words(message["content"], message["config"]["max_len"], message["config"]["max_char_len"])
            # logger.info("裁剪后的合成文本:" + text)

            message["content"] = message["content"].replace('\n', '。')

            # 空数据就散了吧
            if message["content"] == "":
                return
        except Exception as e:
            logger.error(traceback.format_exc())
            return
        

        # 封装数据发到队列（/autoplan 2026-10-22 批次 A-6：变声已退役为独立
        # rvc-vc 流程，主流程音频直通入队——reply/read_comment 不再经变声环节）
        async def put_to_queue(message, voice_tmp_path, synth_speed=None):
            # 拼接json数据，存入队列（键面构造提取 _build_play_entry，流式分段共用）
            data_json = self._build_play_entry(message, voice_tmp_path, synth_speed)

            # A11（2026-10-22 审查批次，义务 R2-A11/E4）：EDTalk 推送链豁免 enable——
            # 数字人服务的音频不受 play_audio.enable 影响（设计开关行为条款）；
            # 豁免仅限经 TTS→push_full 的类型（本函数即 TTS 链收口）。
            _playback_gate = (self.config.get("play_audio", "enable")
                              or is_edtalk_active(self.config))
            # 是否开启了音频播放，如果没开，则不会传文件路径给播放队列
            if _playback_gate:
                self.data_priority_insert("待播放音频列表", data_json)

            return True


        _tts_t0 = time.time()
        resp_json = await self.tts_handle(message)
        if resp_json["result"]["code"] == 200:
            voice_tmp_path = resp_json["result"]["audio_path"]
        else:
            voice_tmp_path = None
        # T1 观测：TTS 合成耗时样本（成败分计，供吞吐/排队分析；异常吞掉不阻塞主链）
        try:
            from utils.obs_stats import record_tts
            record_tts(time.time() - _tts_t0, voice_tmp_path is not None)
        except Exception:
            pass

        if voice_tmp_path is None:
            logger.error(f"{message['tts_type']}合成失败，请排查服务端是否启动、是否正常，配置、网络等问题。如果排查后都没有问题，可能是接口改动导致的兼容性问题，可以前往官方仓库提交issue，传送门：https://github.com/Ikaros-521/AI-Vtuber/issues\n如果是GSV 400错误，请确认参考音频和参考文本是否正确，或替换参考音频进行尝试")
            # 报警重构（2026-10-26）：旧 abnormal_alarm_handle("tts")（错误计数+本地音频）
            # 退役，改走 alarm_notifier（webhook 推送，模块开关门控+60s 冷却）
            alarm_notifier.alarm_error(
                alarm_notifier.MODULE_TTS,
                f"TTS 合成失败（{message['tts_type']}）：{str(message.get('content', ''))[:80]}",
                fix_hint="排查 TTS 服务是否启动、配置与网络；GSV 400 请核对参考音频/文本")

            return False

        # 流式分段（2026-10-30 审查批次）：引擎逐段产出时 my_tts 在 sink 回调里
        # 已对每段（含首段）独立入队（首段即播、保序）——本层跳过 put_to_queue
        # 防首段双播，仅补记完整段集日志；单文件路径（流式关闭）走原路径。
        seg_paths = resp_json["result"].get("segment_paths") or []

        logger.info(f"[{message['tts_type']}]合成成功，合成内容：【{message['content']}】，音频存储在 {voice_tmp_path}")

        if seg_paths:
            logger.info(f"[{message['tts_type']}]流式分段完成：{len(seg_paths)} 段（首段 {os.path.basename(seg_paths[0])} 已随产出逐段入队）")
        else:
            await put_to_queue(
                message, voice_tmp_path,
                synth_speed=resp_json.get("result", {}).get("synth_speed"))

        return True
