"""
音频播放管理模块 - 管理音频播放、文案播放等功能
"""

import asyncio
import shutil
import threading
import traceback
import os
from ..my_log import logger
from ..edtalk_realtime import is_edtalk_active
from utils.web_captions import get_captions_manager

# 伴奏起播门等待上限（2026-11-04 人声对齐修复）：覆盖编排静默窗口上限
# （_CHOREO_MUTE_MAX_WAIT=300s）+ push_full 受理开销；超时照常起播（宁可有
# 伴奏不悬挂线程，与既有「人声照唱」降级语义对称）。
_BACKING_START_MAX_WAIT = 360.0

# 编排纯画面窗口音频等待上限（防呆兜底）：正常窗口 ≤ 三步 duration_s 之和
# （enter/main/exit，用户配置通常各 10-30s）；看门狗 F1 保证窗口必然推进，
# 超上限按异常降级照推（宁可口型出现不丢语音）。
_CHOREO_MUTE_MAX_WAIT = 300.0

# 助播条目类型（v2.2 流程优化，flowchartTD |助播流程| 边）：助播音频无口型
# 语义，EDTalk 激活时也绕过数字人直接本地外放（queue_manager 优先级映射
# 同名类型，feature_handler R4 助播路由产出）。
ASSISTANT_PLAYBACK_TYPES = frozenset({"assistant_anchor_text", "assistant_anchor_audio"})


def routes_to_edtalk(data_json, config) -> bool:
    """播放路由判定（v2.2 流程优化，flowchartTD 音频播放模块）：EDTalk 激活且
    非助播条目 → 数字人帧锁定播放；助播条目（无口型语义）与其余情形 → 本地
    播放分支（SystemPlay）。

    Args:
        data_json: 待播放队列条目（含 type）
        config: Settings/Config 配置对象

    Returns:
        True = 推送数字人（EDTalk）；False = 本地播放（助播条目或 EDTalk 未激活）
    """
    if data_json.get("type") in ASSISTANT_PLAYBACK_TYPES:
        return False
    return is_edtalk_active(config)


def _play_backing_local(backing_path: str, volume: float, start_event=None):
    """R6 双轨伴奏外放（/autoplan 2026-10-23 批次，EDTalk 激活场景）：点歌伴奏轨
    独立 pyaudio 流本地播放（人声从数字人端出、伴奏本地出——双设备分工），随播完
    自退。失败仅告警不抛（R6 降级语义=人声照唱，伴奏缺失不黑场；F-S5 可观测）。

    2026-11-04 人声对齐修复：start_event 非 None 时两阶段起播——①准备阶段
    （AudioSegment 加载/增益/导出/开流，耗时部分）立即执行，吸收人声侧上一句
    飞行等待；②门放行后 start_stream 起播（playback_manager 在 push_full(vocal)
    受理返回后置位）。原实现线程启动即写流：伴奏立刻响，而人声被 EDTalk 单飞行句
    背压（_wait_flight_complete）挡在点歌应答播完之后（实测滞后 5~10s）——R6
    <0.5s 同步义务被击穿。start_event=None 保持旧语义（open 即起播，兼容直调）。"""
    tmp_path = None
    pa = stream = wf = None
    try:
        import math
        import wave as _wave
        import pyaudio as _pa
        from pydub import AudioSegment
        seg = AudioSegment.from_file(backing_path)
        vol = max(0.0, min(1.0, float(volume or 0.8)))
        if vol <= 0 or not seg:
            return
        seg = seg.apply_gain(20.0 * math.log10(vol))
        tmp_path = backing_path + ".backing.tmp.wav"
        seg.export(tmp_path, format="wav")
        wf = _wave.open(tmp_path, "rb")
        pa = _pa.PyAudio()
        stream = pa.open(
            format=pa.get_format_from_width(wf.getsampwidth()),
            channels=wf.getnchannels(), rate=wf.getframerate(), output=True,
            start=(start_event is None))
        if start_event is not None:
            # 起播门：等待人声受理锚点（push_full 返回）——超时兜底防悬挂
            if not start_event.wait(timeout=_BACKING_START_MAX_WAIT):
                logger.warning(
                    f"[song] 伴奏起播门超时（{_BACKING_START_MAX_WAIT:.0f}s），照常起播：{backing_path}")
            stream.start_stream()
        chunk = 1024 * wf.getnchannels() * wf.getsampwidth()
        data = wf.readframes(1024)
        while data:
            stream.write(data)
            data = wf.readframes(1024)
        logger.info(f"[song] 伴奏外放完成：{backing_path}")
    except Exception as e:
        logger.warning(f"[song] 伴奏外放失败（人声照唱，伴奏缺失不黑场）：{backing_path} → {e}")
        # T1 观测面补齐包（F6b 盲区接入）：伴奏失败不再仅日志静默——模块开关门控+60s 冷却
        try:
            from utils.alarm_notifier import alarm_error, MODULE_AUDIO_PLAYBACK
            alarm_error(MODULE_AUDIO_PLAYBACK,
                        f"伴奏外放失败（人声照唱，直播中伴奏无声）：{backing_path}",
                        fix_hint="检查伴奏文件可读性与音频设备占用；频繁失败可临时调低 backing_volume 或关闭双轨")
        except Exception:
            pass  # 报警失败不改变既有降级语义（人声照唱）
    finally:
        for closer in (lambda: stream.stop_stream(), lambda: stream.close(),
                       lambda: wf.close(), lambda: pa.terminate()):
            try:
                closer()
            except Exception:
                pass
        if tmp_path and os.path.isfile(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass


class PlaybackMixin:
    """音频播放管理Mixin类"""

    def _cleanup_song_segment_if_last(self, data_json):
        """② 点歌切片末段清理（2026-10-27，尽力而为）：末段播放完 rmtree 切片
        目录；跳过/打断未触达的残留由合成侧 _purge_stale_song_segments（下次
        切片前按 mtime 超 1h 清扫）兜底。builtin 播放器 play()=异步入队非播放
        完成判定——其清理同样依赖清扫兜底（登记：EDTalk/pygame 路径即时清）。"""
        seg = (data_json or {}).get("song_segment") or {}
        if not seg or seg.get("seq") != seg.get("total", 0) - 1:
            return
        seg_dir = seg.get("seg_dir")
        if seg_dir and os.path.isdir(seg_dir):
            shutil.rmtree(seg_dir, ignore_errors=True)
            logger.info(f"[song] 切片目录已清理：{seg_dir}")

    def _is_999_entry(self, data_json) -> bool:
        """999 判定单一源（2026-11-05 编排优化批次 T2/e2）：经 audio_priority
        统一判定——type 映射==999 或 priority_override==999（d18 规则级 999
        载体）均触发 999 机制（active 标记/让位/单实例）。"""
        try:
            from utils.audio.audio_priority import entry_is_999
            return entry_is_999(data_json, self.config.get("filter", "priority_mapping") or {})
        except Exception:
            return False

    def _on_edtalk_yield(self) -> bool:
        """d19/uc2b（T7，2026-11-05 批次）：EDTalk 路径 999 让位编排——

        pause（服务端消费冻结）→读游标（output_frames 差=已播帧）→resume
        （答谢 push 前解冻，否则答谢帧不被消费）→答谢照常 push（flush 冻结段）
        →答谢消费完成后剩余段从冻结游标重推（_repush_edtalk_999_remainder）。

        Returns: True=已受理（999 在 EDTalk 飞行中——queue 终止让位链）；
        False=非本路径（builtin 让位由 queue_manager 原逻辑承担）。pause 失败
        =d17 fallback 降级（维持排序：999 播完答谢自然轮到），仍返回 True
        （让位链已消费——bucket edtalk_yield_degraded）。"""
        flight = getattr(self, "_edtalk_999_flight", None)
        if not (isinstance(flight, dict) and flight.get("is_999")):
            return False
        client = getattr(self, "edtalk_client", None)
        if client is None or not hasattr(client, "pause_output"):
            return False
        try:
            paused = client.pause_output()
            if not paused.get("ok", False):
                try:
                    from utils.obs_stats import record_drop
                    record_drop("edtalk_yield_degraded")
                except Exception:
                    pass
                logger.warning("[EDTalk] 999 让位降级维持排序（pause 失败——"
                               "d17 fallback；999 播完答谢自然轮到）")
                return True
            cursor = client.get_output_frames()
            start = flight.get("start_frames")
            if cursor is not None and start is not None:
                consumed = max(0, int(cursor) - int(start))
                # 帧锁定音频切片：640 样本/帧（16000Hz / 25fps，render_loop 常量）
                flight["resume_offset_samples"] = consumed * 640
            client.resume_output()  # 答谢 push 前解冻
            self._edtalk_resume_pending = flight
            logger.info(f"[EDTalk] 999 让位受理（uc2b）：游标"
                        f"={cursor if cursor is not None else '?'}, "
                        f"答谢播毕重推剩余段（d19）")
            return True
        except Exception as e:
            logger.warning(f"[EDTalk] 999 让位编排异常（降级维持排序）：{e}")
            return True

    async def _repush_edtalk_999_remainder(self, flight: dict):
        """d19/uc2b：999 剩余 PCM 重推（从冻结游标切片→临时 wav→push_full）。
        fail-soft：重推失败=999 尾段丢失（bucket+告警），不阻塞播放循环。"""
        try:
            import wave as _wave
            path = flight.get("voice_path")
            offset = int(flight.get("resume_offset_samples") or 0)
            if not path or not os.path.isfile(path) or offset <= 0:
                return  # 游标缺失/已播完（答谢期间自然播完）=无剩余
            with _wave.open(path, "rb") as w:
                total = w.getnframes()
                if offset >= total:
                    return
                w.setpos(offset)
                rest = w.readframes(total - offset)
                rate, ch, sw = w.getframerate(), w.getnchannels(), w.getsampwidth()
            tmp = path + ".resume999.wav"
            with _wave.open(tmp, "wb") as out:
                out.setnchannels(ch)
                out.setsampwidth(sw)
                out.setframerate(rate)
                out.writeframes(rest)
            logger.info(f"[EDTalk] 999 剩余重推（uc2b 任意点恢复）："
                        f"{offset}/{total} 帧（{rate}Hz）")
            await self.edtalk_client.push_full(
                tmp, content="999-remainder", wait_flight=True)
            try:
                os.remove(tmp)
            except OSError:
                pass
            # 重推条目的消费回执（d19 口径：剩余段=原 999 的延续，补一次回执）
            self.report_consumed("reread_top_priority", "999-remainder")
        except Exception:
            try:
                from utils.obs_stats import record_drop
                record_drop("edtalk_999_repush_fail")
            except Exception:
                pass
            logger.error("[EDTalk] 999 剩余重推失败（尾段丢失）\n"
                         + traceback.format_exc())

    async def _consume_edtalk_entry(self, data_json, voice_tmp_path):
        """EDTalk 路由条目消费（T2-批 2026-11-06 自 only_play_audio 原样提取）：
        双轨伴奏起播门/编排静默窗口等待/song 起播锚/999 飞行登记/push_full/
        字幕与编排完成回执/消费回执/999 剩余重推/切片清理。异常交由调用方
        try/finally 释放 999 active 标记（W2 根修）。
        """
        # R6 双轨（/autoplan 2026-10-23 批次）：song 消息带 backing_path
        # 伴奏轨时——人声轨推 EDTalk 驱动对口型（数字人端出声），伴奏轨
        # 本地外放并行起播（粗同步，偏差判据 <0.5s=R6 义务）；伴奏线程
        # 随文件播完自退，push_full 返回=人声飞行完成。R12 时序：伴奏与
        # 人声同起，无三路并发（文案播报已在队列前序完成）。
        # 2026-11-04 人声对齐修复：两阶段起播——伴奏线程启动即进准备阶段
        # （加载/增益/导出/开流），起播门在 push_full(vocal) 受理返回后
        # 放行。原实现线程启动即写流（伴奏立刻响），而人声被 EDTalk 单
        # 飞行句背压挡在「点歌应答文案」播完之后（时长×1.05+buffer 回落，
        # 实测滞后 5~10s）——双轨脱节。现：应答播完 → vocal 受理即出声 →
        # 伴奏放行 → 双轨同步起播（偏差≈人声 preroll ~0.3s，<0.5s 恢复）。
        _backing_path = data_json.get("backing_path") or ""
        _backing_gate = None
        if _backing_path and os.path.isfile(_backing_path):
            _backing_gate = threading.Event()
            threading.Thread(
                target=_play_backing_local,
                args=(_backing_path, float(data_json.get("backing_volume", 0.8) or 0.8),
                      _backing_gate),
                name="song-backing", daemon=True).start()
        elif data_json.get("backing_path"):
            logger.warning(f"[song] 伴奏轨缺失，人声照唱：{_backing_path}")
            # T1 观测面补齐包（F6b 同型盲区）：伴奏轨文件缺失可见化
            try:
                from utils.alarm_notifier import alarm_error, MODULE_AUDIO_PLAYBACK
                alarm_error(MODULE_AUDIO_PLAYBACK,
                            f"伴奏轨文件缺失（人声照唱）：{_backing_path}",
                            fix_hint="检查点歌歌曲的伴奏文件是否被移动/删除")
            except Exception:
                pass
        # 口型推理联动（2026-10-25 goal）：编排纯画面窗口
        # （audio_mode=none 的跳舞/转场片段）内延迟推送——数字人
        # 只播画面不出声不做口型；音频在此等待（队列背压天然保序
        # 不丢失），窗口由编排看门狗（F1 duration 上限）保证必然
        # 结束。带语音步骤（tts/file）不构成窗口，正常驱动口型。
        _choreo_guard = None
        try:
            from utils.orchestration import get_choreographer
            _choreo_guard = get_choreographer()
        except Exception:
            _choreo_guard = None
        if _choreo_guard is not None and _choreo_guard.in_mute_window():
            _mute_remaining = _choreo_guard.mute_window_remaining_s()
            logger.info(
                f"[EDTalk] 编排纯画面窗口内，音频延迟推送"
                f"（剩余约 {_mute_remaining if _mute_remaining is not None else '?'}s）："
                f"{data_json.get('content', '')[:20]}")
            _waited = 0.0
            _mute_punch_done = False
            while _choreo_guard.in_mute_window() and _waited < _CHOREO_MUTE_MAX_WAIT:
                await asyncio.sleep(0.2)
                _waited += 0.2
                # T1 观测面补齐包（F6a）：等待 >60s 打点一次+计数（量化观测，
                # 不报警——300s 上限降级已在位，此处补中间态可见性）
                if _waited >= 60.0 and not _mute_punch_done:
                    _mute_punch_done = True
                    try:
                        from utils.obs_stats import record_choreo_mute
                        record_choreo_mute(_waited)
                    except Exception:
                        pass
                    logger.warning(
                        f"[EDTalk] 编排静默窗口等待已超 60s（当前 {_waited:.0f}s，"
                        f"上限 {_CHOREO_MUTE_MAX_WAIT:.0f}s）——后续音频级联延迟，"
                        f"排查编排窗口素材时长或看门狗状态")
            if _mute_punch_done:
                try:
                    from utils.obs_stats import record_choreo_mute
                    record_choreo_mute(_waited)
                except Exception:
                    pass
            if _choreo_guard.in_mute_window():
                # 防呆上限兜底：仍处窗口也照推（宁可口型出现不丢语音）
                logger.warning(
                    f"[EDTalk] 纯画面窗口等待超 {_CHOREO_MUTE_MAX_WAIT}s，降级照推"
                    f"（problem=窗口未按时结束；cause=编排看门狗异常；fix=检查编排状态）")
        # 歌曲起播锚点（R-eng1/F-M4，2026-10-27 批次）：song 条目
        # 在 push_full 受理时刻通知编排「剩余时长」——hold_for_song
        # 主线段 deadline=锚点+剩余，杜绝「歌没播完序列走完」。
        # 切片条目逐段刷新（自校正）；remaining≤0 或无 hold 段=幂等忽略。
        if (data_json.get("type") == "song"):
            try:
                from utils.orchestration import get_choreographer
                _song_choreo = get_choreographer()
            except Exception:
                _song_choreo = None
            if _song_choreo is not None:
                _remaining = None
                _seg = data_json.get("song_segment") or {}
                if _seg.get("total"):
                    try:
                        _seg_seconds = float(self.config.get(
                            "play_audio", "song_segment_seconds"))
                    except (TypeError, ValueError):
                        _seg_seconds = 0.0
                    if _seg_seconds <= 0:
                        _seg_seconds = 45.0
                    # 剩余=当前片+后续片（各片等长近似，逐段推送自校正）
                    _remaining = (int(_seg.get("total", 1))
                                  - int(_seg.get("seq", 0))) * _seg_seconds
                else:
                    try:
                        import soundfile as _sf
                        _remaining = float(
                            _sf.info(voice_tmp_path).duration)
                    except Exception:
                        _remaining = None
                if _remaining and _remaining > 0:
                    _song_choreo.on_song_started(
                        str(data_json.get("content", ""))[:40], _remaining)
        # T7/d19（uc2b 让位游标基座）：999 飞行登记——push 受理前
        # 读 output_frames 起点（consumed=pause 读数-起点=已播帧）
        if self._is_999_entry(data_json):
            try:
                _start_frames = self.edtalk_client.get_output_frames()
            except Exception:
                _start_frames = None
            self._edtalk_999_flight = {
                "voice_path": voice_tmp_path,
                "start_frames": _start_frames,
                "is_999": True,
            }
        # T7/d3（2026-11-05）：song 切片跳过飞行等待——切片时序
        # 锚定伴奏（uc4b），人声起即停答谢=push 即 flush 替换
        # （e5 口径：替换生效时刻=本次 push 受理时刻，非推送发起）
        _wait_flight = not (data_json.get("type") == "song"
                            and data_json.get("song_segment"))
        try:
            await self.edtalk_client.push_full(
                voice_tmp_path, content=data_json.get("content", ""),
                wait_flight=_wait_flight,
            )
        finally:
            if _backing_gate is not None:
                # 伴奏起播门放行（2026-11-04 人声对齐）：push_full 返回=
                # 人声受理（preroll ~0.3s 后出声）→ 伴奏此刻起播，双轨同步。
                # 失败也放行（对齐既有降级对称面「人声缺失伴奏照播」+ 防
                # 伴奏线程悬挂至超时）。push_full 内部全捕获不抛，finally
                # 兜底 CancelledError 等 BaseException 穿透场景。
                _backing_gate.set()
        # 字幕时序对齐（2026-10-28 修复）：推送点=push_full 返回后
        # ——此刻音频已被数字人受理、即将出声（preroll 缓冲 ~0.3s）。
        # 原推送点在 push_full 之前，未计入其内部的上一句飞行等待
        # （时长×1.2 + buffer_depth 回落，edtalk_client
        # ._wait_flight_complete），字幕系统性超前一句（字幕N 显示时
        # 数字人正在播 N-1）。
        await self._captions_push(data_json)
        # 动作编排完成回调（/autoplan 2026-10-21 批次，义务 CL-2/D-2）：
        # EDTalk push 路径每条目完成处（push_full 返回=单飞行句完成判定）。
        # G1（2026-10-28）：builtin 播放器的完成回传已由 audio_core 注册
        # 完成回调补齐（_fire_completion 携带条目 type/content）——两路
        # 互斥不双报（EDTalk 激活走本路径，未激活走 builtin 播放回调）。
        self._notify_choreo_complete(data_json)
        # G2（2026-10-28 消费回传批次）：Sort 侧显式消费回执
        # （消费完成点①——EDTalk push 路径；report_consumed 内部吞异常）
        self.report_consumed(
            data_json.get("type", ""), data_json.get("content", ""))
        # T7/d3/e5：答谢被替换观测——song 切片 push 受理=上一非
        # song 条目被服务端 flush 替换（d3「人声起立即停止答谢」；
        # 替换视为消费完成——回执已在其 push 返回处上报，此处补
        # 替换事件分桶）。首条目（prev=None）不算替换。
        _prev_type = getattr(self, "_edtalk_prev_type", None)
        if (data_json.get("type") == "song"
                and _prev_type is not None and _prev_type != "song"):
            try:
                from utils.obs_stats import record_drop
                record_drop("edtalk_answer_replaced")
            except Exception:
                pass
            logger.info("[EDTalk] 答谢被切片替换（d3 人声起即停，"
                        "视为消费完成）")
        self._edtalk_prev_type = data_json.get("type")
        # T8/X1/e7（2026-11-05 批次）：间奏插播观测——
        # a) 插入事件：song 在播中非 song 条目推送（插播发生）
        # b) 积压条数：song 切片 push 时待播放队列中非 song 条目
        #    （e7 积压分摊观测维度；X1 等待时长=既有 record_latency
        #    消费延迟样本承担）
        if _prev_type == "song" and data_json.get("type") != "song":
            try:
                from utils.obs_stats import record_drop
                record_drop("song_interlude_insert")
            except Exception:
                pass
        if data_json.get("type") == "song":
            try:
                _backlog = sum(
                    1 for e in getattr(self, "voice_tmp_path_queue", [])
                    if isinstance(e, dict) and e.get("type") != "song")
                if _backlog:
                    from utils.obs_stats import record_drop
                    record_drop("song_interlude_backlog", _backlog)
            except Exception:
                pass
        # T7/d19/uc2b：999 挂起剩余重推（答谢消费完→任意点恢复；
        # flush 已清冻结段，剩余从冻结游标重推）
        _pend = getattr(self, "_edtalk_resume_pending", None)
        if _pend is not None:
            self._edtalk_resume_pending = None
            await self._repush_edtalk_999_remainder(_pend)
        # T1 观测：消费延迟样本（入队→完成；_enqueued_at 缺失=旧条目跳过）
        try:
            from utils.obs_stats import record_latency
            _enq = data_json.get("_enqueued_at")
            if _enq:
                import time as _t
                record_latency(_t.time() - float(_enq))
        except Exception:
            pass
        # ② 点歌切片末段清理（EDTalk 路径即时清）
        self._cleanup_song_segment_if_last(data_json)
        # A7/F-5 短路声明：EDTalk 激活时 999 暂停-续播链路显式不可达——
        # 本分支不进 builtin 播放器（push_full 帧锁定播放），999 按队列
        # 优先级自然排序推送；999 单实例约束仍由 queue_manager 层保证。

    async def only_play_audio(self):
        """只进行普通音频播放"""
        try:
            while True:
                try:
                    # 获取线程锁，避免同时操作
                    with self.voice_tmp_path_queue_lock:
                        while not self.voice_tmp_path_queue:
                            # 消费者在消费完一个消息后，如果列表为空，则调用wait()方法阻塞自己，直到有新消息到来
                            self.voice_tmp_path_queue_not_empty.wait()  # 阻塞直到列表非空
                        data_json = self.voice_tmp_path_queue.pop(0)

                    # 精简日志（2026-10-28）：整包 dict 倾倒退役——只留 type+content
                    # 摘要（完整字段可按需在合成/路由链路排查时临时加回）
                    logger.debug(f"待播放队列出队 type={data_json.get('type')} "
                                 f"content={str(data_json.get('content', ''))[:50]}")

                    voice_tmp_path = data_json["voice_path"]

                    # A7/T1（2026-10-22 审查批次）：999 单实例 active 标记——条目离开
                    # 待播放队列即视为进入播放系统；完成/跳过/清空时释放（EDTalk 在
                    # 本函数释放，builtin 经 release hook 在播放器线程释放）。
                    # T2/e2：判定经 _is_999_entry 单一源（override==999 的规则级
                    # 999 条目同样置位——让位门 active_reread_top_999 对其生效）。
                    _was_999 = self._is_999_entry(data_json)
                    if _was_999:
                        self.active_reread_top_999 = True

                    # 字幕推送时序对齐（2026-10-25 用户实测批次）：出队公共段 push 已
                    # 退役——推送点=开播精确位：EDTalk→push_full 返回后（_captions_push）；
                    # builtin 由 F3 开播回调在真实起播时刻推送（audio_core 接线，最精确）。

                    # 消费回传机制（2026-10-30 审查批次）：播放完成回执驱动下一条
                    # 取用（builtin 完成回调/EDTalk push_full 返回=消费完成点），无需
                    # 冗余的间隔等待——「播放间隔min/max」配置随本批次退役移除
                    # （原 A4 改名键 play_interval_min/max 与旧键 normal_interval_min/max
                    # 一并清理；A2 pydub 随机变速路径已于 2026-10-22 退役删除，变速
                    # 在合成侧经 TTS 引擎参数生效，见 synthesis_manager）。

                    # 根据接入的虚拟身体类型执行不同逻辑（2026-10-22：metahuman_stream 驱动退役，EDTalk 分流为唯一特殊路径）
                    # EDTalk 实时推理模式（EDTalk功能集成计划 §4.3.3）：跳过本地播放——
                    # EDTalk 端 sounddevice 帧锁定播放（本地播放会双声），TTS 落盘
                    # （含变速）后仅推送 /audio/push_full。推送协程内阻塞等待完成
                    # 判定（背压），失败降级记录不阻塞直播主流程。
                    # v2.2 流程优化（flowchartTD |助播流程| 边）：助播条目绕过数字人。
                    if routes_to_edtalk(data_json, self.config):
                        # T2-批（2026-11-06 协作机制流程审查，W2 根修）：999 active
                        # 标记 finally 化——分支内任何异常逃逸（含 CancelledError 穿透）
                        # 都释放标记，防 999 单实例门卡死复读专线（原释放点仅在正常
                        # 完成路径，逃逸即悬挂）。分支体提取为 _consume_edtalk_entry
                        # 逐字保持，仅移除正常路径内的冗余释放点——finally 统一承担。
                        try:
                            await self._consume_edtalk_entry(data_json, voice_tmp_path)
                        finally:
                            if _was_999:
                                self.active_reread_top_999 = False
                    else:
                        # 本地播放统一 builtin 进程内播放器（2026-10-30 审查批次）：
                        # 「音频播放器」选择配置退役移除——外部 AUDIO_PLAYER HTTP 客户端
                        # 与 pygame 直播分支随配置一并删除（消费回传/999 暂停-续播/
                        # F3 开播字幕均由 builtin 承接）。
                        # 2026-11-04 本地整轨（用户优化）：EDTalk 未激活时 song 条目播
                        # 完整混音成品（full_path，一键翻唱三产物之一，前端预览同源）——
                        # 原实现仅播人声轨（清唱，伴奏轨在本地路径无消费方）。整轨降级/
                        # 翻唱自动插播消息本身即整轨（full_path 空→不替换），行为不变；
                        # full 文件缺失按防呆回退 voice_path。
                        _local_voice = voice_tmp_path
                        if data_json.get("type") == "song":
                            _full = data_json.get("full_path") or ""
                            if _full and os.path.isfile(_full):
                                _local_voice = _full
                                logger.info(f"[song] EDTalk 未激活，整轨播放（前端预览同源）：{_full}")
                        if "insert_index" in data_json:
                            data_json = {
                                "type": data_json["type"],
                                "voice_path": _local_voice,
                                "content": data_json["content"],
                                "random_speed": {
                                    "enable": False,
                                    "max": 1.3,
                                    "min": 0.8
                                },
                                "speed": 1,
                                "insert_index": data_json["insert_index"]
                            }
                        else:
                            data_json = {
                                "type": data_json["type"],
                                "voice_path": _local_voice,
                                "content": data_json["content"],
                                "random_speed": {
                                    "enable": False,
                                    "max": 1.3,
                                    "min": 0.8
                                },
                                "speed": 1
                            }
                        # 字幕由 F3 开播回调在真实起播时刻推送（play()=入队非同步
                        # 播放，此处推会随队列积压超前）——builtin 播放器专属接线。
                        self.audio_player.play(data_json)
                        # M3（2026-10-28 消费批次）：本地播放器路径消费回传——以移交点
                        # （play() 返回=条目脱离本队列）为近似消费完成点：Sort 侧回执 +
                        # 编排实例匹配（保证编排序列可推进，对齐推进语义优先于精确播放
                        # 时点）；builtin 的真实完成点经完成回调二次回执（G1，互斥不双报
                        # 由回调注册点保证——playback_manager 侧仅移交回执）。
                        self.report_consumed(data_json.get("type", ""), data_json.get("content", ""))
                        try:
                            from utils.obs_stats import record_latency
                            _enq = data_json.get("_enqueued_at")
                            if _enq:
                                import time as _t
                                record_latency(_t.time() - float(_enq))
                        except Exception:
                            pass
                        self._notify_choreo_complete(data_json)
                        # ② 点歌切片末段清理：builtin play()=异步入队非播放完成判定，
                        # 末段清理依赖合成侧 _purge_stale_song_segments 清扫兜底。

                except Exception as e:
                    logger.error(traceback.format_exc())
        except Exception as e:
            logger.error(traceback.format_exc())


    def _notify_choreo_complete(self, data_json):
        """编排播完回调（M3 提取，2026-10-28 消费批次：EDTalk/builtin 两路共用）。
        O6：choreo_step_id 随条目透传——按实例序号精确匹配（同内容重复入队不误配），
        未透传路径编排侧退回 (type,content) 匹配。异常隔离（失败退编排 F1 超时
        兜底，不影响播放循环）。"""
        try:
            from utils.orchestration import get_choreographer
            _choreo = get_choreographer()
            if _choreo is not None:
                _choreo.on_audio_complete(
                    data_json.get("type", ""), data_json.get("content", ""),
                    data_json.get("choreo_step_id") or None)
        except Exception as e:
            logger.debug(f"动作编排完成回调异常（依赖 F1 超时兜底）：{e}")

    async def _captions_push(self, data_json):
        """字幕推送（时序对齐版，2026-10-25 用户实测批次）：调用点=音频进
        数字人服务器/系统开播前一刻。原出队公共段推送随队列积压与编排静默
        窗口系统性超前（EDTalk 场景可达分钟级），已退役。全捕获（S4：字幕
        错误不得传播进播放循环）。"""
        try:
            manager = get_captions_manager()
            if manager is not None:
                await manager.push(data_json)
        except Exception:
            logger.error("字幕推送异常（已隔离，不影响播放）\n" + traceback.format_exc())

    def stop_current_audio(self):
        """停止当前播放的音频

        注意（EDTalk 模式语义变化，native E6）：edtalk_realtime 激活时本地播放
        已被跳过，弹幕打断只作用于本地 mixer/audio_player；EDTalk 端无
        flush-audio 契约端点，打断不会停止其音频播放。已列入 edtalk/README
        语义变化清单。
        """
        if is_edtalk_active(self.config):
            # EDTalk 契约无音频 flush 端点——静默跳过（保持既有调用方兼容）
            return
        self.audio_player.skip_current_stream()
