"""
音频播放管理模块 - 管理音频播放、文案播放等功能
"""

import asyncio
import shutil
import threading
import time
import traceback
import os
from ..my_log import logger
from ..edtalk_realtime import is_edtalk_active
from utils.web_captions import get_captions_manager

# 伴奏起播门等待上限（2026-11-04 人声对齐修复）：覆盖编排静默窗口上限
# （_CHOREO_MUTE_MAX_WAIT=300s）+ push_full 受理开销；超时照常起播（宁可有
# 伴奏不悬挂线程，与既有「人声照唱」降级语义对称）。
_BACKING_START_MAX_WAIT = 360.0

# 伴奏真实起播回执等待上限（2026-11-09 前奏对齐）：前奏静音场景伴奏线程
# 准备阶段（加载/增益/导出/开流）的等待上限；超时 fail-open 以当前时刻为
# 歌曲时间轴零点（宁可贵一点的对齐偏差不悬挂播放循环）。
_BACKING_PRELUDE_MAX_WAIT = 10.0

# 间奏窗口门推送预补偿（2026-11-09 失步根修）：due 提前量=pop→push HTTP
# 受理开销≈0.3s，使「受理+preroll 出声」落在歌曲时间轴应到时刻附近。
_SONG_PUSH_LEAD = 0.3

# 段播完估计的出声延迟（2026-11-11 R4f 实测校准）：帧锁定出声=受理+preroll
# （首块推理就绪 ~0.3s），播完=受理+preroll+段时长——last_end 用加法精确化
# （此前乘法 margin 1.03/1.15 的残差逐段累积：45s 段 1.35s/段，实测相邻无
# 间奏段「人声停顿 1.5s、与伴奏差异越来越大」的根因）。推理慢于实时的极端
# 场景由 e13 失步声明兜底（宁可截断尾巴不破坏对齐）。
_SONG_PREROLL_S = 0.3

# 答谢连续跳过上限（2026-11-10 R3/F4/D14）：song 序列内连续跳过（到期优先
# pop(i)）计数达此值 → 下一到期段放行队首条目一条（答谢时效保障——人声落后
# 时间轴时每段皆 due，无上限=答谢饿死至歌尾；放行人声延后有界 ≈ 一条答谢
# TTS 2-5s，e13 失步声明方向内）。R4 起为兜底（预算预检在前端控量）。
_OVERDUE_SKIP_LIMIT = 2

# 间奏插播硬性条件（2026-11-10 R4，用户指定）：间奏窗口剩余<此值或窗口本身
# <此值 → 不再起插播音频（避免说一半）。用户口径：「进入间奏开始计时，不足
# 3 秒时无需消费音频，或间奏空间本身就不足 3 秒，也无需消费音频」。
_INTERLUDE_MIN_CONSUME_S = 3.0

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


def _play_backing_local(backing_path: str, volume: float, start_event=None,
                        started_event=None, started_box=None):
    """R6 双轨伴奏外放（/autoplan 2026-10-23 批次，EDTalk 激活场景）：点歌伴奏轨
    独立 pyaudio 流本地播放（人声从数字人端出、伴奏本地出——双设备分工），随播完
    自退。失败仅告警不抛（R6 降级语义=人声照唱，伴奏缺失不黑场；F-S5 可观测）。

    2026-11-04 人声对齐修复：start_event 非 None 时两阶段起播——①准备阶段
    （AudioSegment 加载/增益/导出/开流，耗时部分）立即执行，吸收人声侧上一句
    飞行等待；②门放行后 start_stream 起播（playback_manager 在 push_full(vocal)
    受理返回后置位）。原实现线程启动即写流：伴奏立刻响，而人声被 EDTalk 单飞行句
    背压（_wait_flight_complete）挡在点歌应答播完之后（实测滞后 5~10s）——R6
    <0.5s 同步义务被击穿。start_event=None 保持旧语义（open 即起播，兼容直调）。

    2026-11-09 前奏对齐：started_event/started_box 非 None 时，start_stream 后
    回执真实起播时刻（started_box["t"]=monotonic）——前奏静音场景人声首段以此
    为歌曲时间轴零点（t0）等待前奏后再推，对齐伴奏时钟。"""
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
        if started_event is not None:
            # 真实起播回执（2026-11-09 前奏对齐）：start_stream 后立即回执，
            # 人声侧以该时刻为歌曲时间轴零点计算前奏/间奏窗口
            if started_box is not None:
                started_box["t"] = time.monotonic()
            started_event.set()
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

    def _song_gate_wait_s(self, data_json) -> float:
        """间奏窗口门（2026-11-09 失步根修）：队首 song 切片段未到「歌曲时间轴
        应到时刻」时返回剩余等待秒（>0=消费循环锁外短睡重查）；其余恒 0。

        due = t0 + start_s（该段在歌曲时间轴的起点；t0=首段建立的歌曲时间轴
        零点≈伴奏起播时刻）− 推送预补偿（_SONG_PUSH_LEAD），再与上一段播完
        保底（last_end=受理+段时长×margin）取大——服务端渐进推理慢于实时时
        宁可人声延后（e13 失步声明方向）也不 flush 截断上一段。

        fail-open：非 song/首段（受理即零点，前奏等待在消费函数内）/无锚
        （异常态）恒 0 不阻塞——窗口门只在正常 song 序列中段生效。"""
        if not isinstance(data_json, dict) or data_json.get("type") != "song":
            return 0.0
        seg = data_json.get("song_segment") or {}
        if not seg or int(seg.get("seq", 0)) == 0:
            return 0.0
        anchor = getattr(self, "_song_anchor", None)
        if not anchor or anchor.get("t0") is None:
            return 0.0
        due = anchor["t0"] + float(seg.get("start_s") or 0.0) - _SONG_PUSH_LEAD
        last_end = anchor.get("last_end") or 0.0
        if last_end > due:
            due = last_end
        remaining = due - time.monotonic()
        return remaining if remaining > 0 else 0.0

    def _pop_next_entry_locked(self):
        """出队选择（2026-11-10 R4 窗口预算模型——用户实测反馈重构，须在
        voice_tmp_path_queue_lock 持有内调用）。

        用户设计预期（2026-11-10 实测反馈）：伴奏响=唱歌时间；其它消费音频
        只能在间奏期间消费；间奏剩余不足 3s（或间奏本身不足 3s）不再起音频
        （避免说一半）；间奏结束立刻切回人声（无论插播条目是否播完）；人声
        分段全部对齐伴奏播完；人声播完而伴奏未完=垃圾时间（伴奏继续+自由
        消费，新点歌可停止当前伴奏起新唱歌时间）。

        ① 垃圾时间（anchor["wrapping"]）：伴奏进程死（is_alive poll，fail-soft）
        → 清锚回 IDLE；否则**全直通 pop(0)**（自由消费，无窗口约束）。
        ② 唱歌时间（锚存在）：a) 到期 song 段（due≤now，含 last_end 保底）
        → pop(i) 跳过其前插播条目（延后留队不丢；跳过区间含 999 豁免）=
        间奏结束立刻切回人声；b) 队首=插播条目 → **窗口预检**——间奏窗口
        =[last_end, 下一 song 段 due]，剩余=窗口结束−now：剩余<3s 或窗口
        总长<3s 不起音频；插播音频时长（pydub 读 voice_path，fail-soft 放行）
        >剩余 → 不起（预算语义=消费完剩 2s 不够下一条则不消费）；通过 → pop。
        c) 队首=song 段未到期 → 窗口门等待（复用 _song_gate_wait_s）。
        ③ 无锚 → 全直通（fail-open）。

        Returns: (data_json|None, gate_wait_s, borrowed)
          data_json 非 None=已出队待消费；gate_wait_s>0=队首 song 段窗口未到
          （调用方锁外 sleep 后重入）；borrowed=True=窗口借位条目（R5：本地
          外放消费，不推数字人——人声流不中断）。"""
        queue = self.voice_tmp_path_queue
        if not queue:
            return None, 0.0, False
        anchor = getattr(self, "_song_anchor", None)
        if anchor and anchor.get("t0") is not None:
            # ① 垃圾时间（末段受理后，人声播完伴奏未完）：伴奏播完感知=
            # 子进程 is_alive poll（降级线程无 proc 引用→保守保持 wrapping
            # 直到新歌重建锚）。队列出现 song 切片段=新点歌序列 → 退出
            # 垃圾时间走正常机制（seq0 消费重建锚+新伴奏起新唱歌时间）。
            if anchor.get("wrapping"):
                _has_new_song = any(
                    isinstance(e, dict) and e.get("type") == "song"
                    and e.get("song_segment") for e in queue)
                proc = getattr(self, "_backing_proc", None)
                backing_alive = True
                if proc is not None:
                    try:
                        backing_alive = proc.is_alive()
                    except Exception:
                        pass
                if not _has_new_song and not backing_alive:
                    # 伴奏播完且无新歌=垃圾时间结束 → 清锚回 IDLE
                    self._song_anchor = None
                    return queue.pop(0), 0.0, False
                if not _has_new_song:
                    return queue.pop(0), 0.0, False  # 垃圾时间自由消费
                anchor["wrapping"] = False  # 新歌到来：回到唱歌时间机制
                logger.info("[song] 垃圾时间结束（新点歌触发），进入新唱歌时间")
            now = time.monotonic()
            last_end = anchor.get("last_end") or 0.0
            skip_count = int(anchor.get("skip_count") or 0)
            # ②a 到期 song 段：间奏结束立刻切回人声（延后插播条目）
            for i, entry in enumerate(queue):
                if not isinstance(entry, dict) or entry.get("type") != "song":
                    continue
                seg = entry.get("song_segment") or {}
                if not seg or int(seg.get("seq", 0)) == 0:
                    continue
                due = anchor["t0"] + float(seg.get("start_s") or 0.0) - _SONG_PUSH_LEAD
                if last_end > due:
                    due = last_end  # 播完保底地板（F8：防插播后下段抢跑）
                if now < due:
                    continue
                if any(self._is_999_entry(e) for e in queue[:i]):
                    return queue.pop(0), 0.0, False  # 999 豁免（最高优先级不延后）
                anchor["skip_count"] = 0 if i == 0 else min(
                    skip_count + 1, _OVERDUE_SKIP_LIMIT)
                return queue.pop(i), 0.0, False
            # ②b 队首=插播条目 → 窗口预检（预算语义+3s 硬性条件）
            head = queue[0]
            if not (isinstance(head, dict) and head.get("type") == "song"):
                window_end = self._next_song_due_locked(anchor)
                if window_end is None:
                    # 无后续 song 段（末段已消费完的收尾间隙）→ 自由消费
                    return queue.pop(0), 0.0, False
                remaining = window_end - now
                window_total = window_end - (last_end if last_end > 0 else now)
                if remaining < _INTERLUDE_MIN_CONSUME_S \
                        or window_total < _INTERLUDE_MIN_CONSUME_S:
                    # 硬性条件：剩余<3s 或窗口本身<3s → 不起音频（避免说
                    # 一半）——等到期推人声
                    return None, max(remaining, 0.0) or _SONG_PUSH_LEAD, False
                if self._entry_duration_s(head) > remaining:
                    # 预算语义：该音频播不完窗口 → 不起下一条（消费完剩余
                    # 不足 3s 时已由上面拦住）
                    return None, max(remaining, 0.0) or _SONG_PUSH_LEAD, False
                return queue.pop(0), 0.0, True
        # ②c 无锚（fail-open）或队首=song 段：窗口门判定
        gate_wait = self._song_gate_wait_s(queue[0])
        if gate_wait > 0:
            # 借位消费（R4f，2026-11-11 实测根修）：窗口未到期时，队首歌段
            # 之后的待播音频（入场/答谢等——低优先级被插队规则压在歌段后，
            # 窗口门只看队首导致前奏/间奏零消费）先出队消费——**借位 pop(i)
            # 不动歌段**（歌段留队等待期）。预算预检：借位条目时长已知时，
            # 播完须留 ≥3s 余量（用户硬性条件「不足 3 秒避免说一半」）；时长
            # 未知（fail-soft=0）放行。遇下一个歌段即止（其后条目属后续窗口）。
            window_end = self._next_song_due_locked(anchor)
            remaining = ((window_end - time.monotonic())
                         if window_end is not None else 0.0)
            for i, entry in enumerate(queue):
                if (isinstance(entry, dict) and entry.get("type") == "song"
                        and entry.get("song_segment")):
                    continue  # 跳过歌段（R5c：答谢压队尾跨歌段——不 break，
                    # 全队列扫描借位；歌段留队不动）
                if not isinstance(entry, dict):
                    continue
                dur = self._entry_duration_s(entry)
                if dur > 0 and remaining - dur < _INTERLUDE_MIN_CONSUME_S:
                    continue  # 超窗/不足 3s 余量 → 看下一条
                return queue.pop(i), 0.0, True  # R5b：借位走 EDTalk 口型
            return None, gate_wait, False
        return queue.pop(0), 0.0, False

    def _next_song_due_locked(self, anchor):
        """队列中下一个 song 切片段的窗口结束时刻（due=t0+start−预补偿，含
        last_end 保底）；无 → None（自由消费）。须锁内调用。"""
        t0 = anchor.get("t0")
        last_end = anchor.get("last_end") or 0.0
        for entry in self.voice_tmp_path_queue:
            if not isinstance(entry, dict) or entry.get("type") != "song":
                continue
            seg = entry.get("song_segment") or {}
            if not seg or int(seg.get("seq", 0)) == 0:
                continue
            due = t0 + float(seg.get("start_s") or 0.0) - _SONG_PUSH_LEAD
            return max(due, last_end)
        return None

    def _entry_duration_s(self, entry):
        """插播条目音频时长（pydub 读 voice_path，fail-soft：读取失败→0=
        放行不阻塞——时长不可知时退化为不预检）。锁内调用豁免说明：本地小
        音频 pydub 加载 <100ms（非重 IO/网络），持锁窗口可容忍；时长透传
        优化列 TODOS。"""
        try:
            from pydub import AudioSegment
            path = entry.get("voice_path") or ""
            if not path or not os.path.isfile(path):
                return 0.0
            return len(AudioSegment.from_file(path)) / 1000.0
        except Exception:
            return 0.0

    def _start_backing_player(self, backing_path: str, volume: float):
        """伴奏播放启动编排（2026-11-10 R2，须在主进程调用）。

        单实例：terminate 存活旧伴奏进程（连点/切歌防双伴奏），顺带 poll
        exitcode≠0 告警（N2 观测挂点）。子进程路径=mp 原语（Event/Value('d')
        哨兵 0.0/协作停止）+Process 启动线程化（spawn 阻塞 100-300ms，F9/
        D24——门机制天然吸收启动延迟）；降级=mp 导入/启动失败回退线程方案
        （CL1/D21：既有语义无 terminate，连播可能重叠——日志提示）。

        Returns: (gate, started_event, read_started, stop_event, proc|None,
                  degraded: bool)——gate/.set() 与 started.wait() 形态无关；
                  read_started()=起播时刻 float（哨兵 0.0=无回执）。"""
        # ① 单实例：旧进程 terminate+exitcode 观测
        old = getattr(self, "_backing_proc", None)
        if old is not None:
            try:
                if old.is_alive():
                    old.terminate()
                old.join(timeout=2)
                if old.exitcode not in (0, None):
                    self._backing_exitcode_alarm(old.exitcode)
            except Exception:
                pass
            self._backing_proc = None
        # ② 子进程路径
        try:
            import multiprocessing as _mp
            from utils.audio import backing_player as _bp
            gate = _mp.Event()
            started_ev = _mp.Event()
            value = _mp.Value('d', 0.0)
            stop_ev = _mp.Event()
            proc = _mp.Process(
                target=_bp._backing_process_main,
                args=(backing_path, volume, gate, started_ev, value, stop_ev),
                daemon=True, name="song-backing")
            # spawn 的 Process.start() 是阻塞调用（100-300ms）——线程化启动，
            # 门机制吸收启动延迟（F9/D24 to_thread 纪律的线程等价形态）
            threading.Thread(target=proc.start, name="song-backing-spawn",
                             daemon=True).start()
            self._backing_proc = proc
            self._backing_stop_ev = stop_ev

            def _read():
                try:
                    return float(value.value)
                except Exception:
                    return 0.0

            logger.info(f"[song] 伴奏子进程启动：{backing_path}")
            return gate, started_ev, _read, stop_ev, proc, False
        except Exception:
            # ③ 降级线程（既有 _play_backing_local 全语义）
            logger.warning(
                "[song] 伴奏子进程不可用，降级线程方案（既有语义：无 terminate，"
                "连播伴奏可能重叠——非新缺陷）")
            gate = threading.Event()
            started_ev = threading.Event()
            box = {"t": 0.0}
            stop_ev = threading.Event()
            threading.Thread(
                target=_play_backing_local,
                args=(backing_path, volume, gate),
                kwargs={"started_event": started_ev, "started_box": box},
                name="song-backing", daemon=True).start()

            def _read_degraded():
                try:
                    return float(box.get("t", 0.0))
                except Exception:
                    return 0.0

            return gate, started_ev, _read_degraded, stop_ev, None, True

    def _stop_backing_player(self):
        """伴奏停止链（F5/D15）：协作停止原语（子进程每 chunk 检查）→ 短等
        → terminate 挂死兜底 → exitcode 观测。前端「停止」路径与切歌共用。"""
        proc = getattr(self, "_backing_proc", None)
        stop_ev = getattr(self, "_backing_stop_ev", None)
        if stop_ev is not None:
            try:
                stop_ev.set()
            except Exception:
                pass
        if proc is None:
            return
        try:
            proc.join(timeout=3)  # 协作停止窗口
            if proc.is_alive():
                proc.terminate()  # 挂死兜底
                proc.join(timeout=2)
            if proc.exitcode not in (0, None):
                self._backing_exitcode_alarm(proc.exitcode)
        except Exception:
            pass
        self._backing_proc = None

    def _backing_exitcode_alarm(self, exitcode):
        """子进程异常退出告警（MODULE_AUDIO_PLAYBACK 门控+60s 冷却既有）。"""
        try:
            from utils.alarm_notifier import alarm_error, MODULE_AUDIO_PLAYBACK
            alarm_error(MODULE_AUDIO_PLAYBACK,
                        f"伴奏播放子进程异常退出（exitcode={exitcode}，"
                        "人声照唱）",
                        fix_hint="检查音频设备占用与伴奏文件可读性；反复出现"
                                 "可临时关闭双轨")
        except Exception:
            pass

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
        # 2026-11-10 R2 伴奏卡顿根修：播放迁移独立子进程（backing_player 薄
        # 入口，独立 GIL 隔离主进程消费路径争抢）；单实例 terminate 旧进程
        # （连点/切歌防双伴奏）+协作停止+停止链治理接入；mp 不可用降级线程
        # （既有语义：无 terminate/连播可能重叠——日志已提示）。
        _backing_path = data_json.get("backing_path") or ""
        _backing_gate = None
        _backing_started = None
        _read_started = None
        _backing_stop = None
        _backing_proc = None
        if _backing_path and os.path.isfile(_backing_path):
            (_backing_gate, _backing_started, _read_started, _backing_stop,
             _backing_proc, _backing_degraded) = self._start_backing_player(
                _backing_path,
                float(data_json.get("backing_volume", 0.8) or 0.8))
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
        # 前奏对齐+序列锚（2026-11-09 失步根修）：song 切片首段推送前——
        # start_s>0（vocal 前奏静音=伴奏先起）时伴奏门提前放行（伴奏起播即
        # 歌曲时间轴零点 t0），人声等待前奏静音段后再推（修复「人声开场即
        # 超前伴奏」）；start_s=0 保持门随 push 受理放行（原语义），t0=受理
        # 时刻回填。锚 _song_anchor 供段间奏窗口门（_song_gate_wait_s）计算
        # 后续段应到时刻；无伴奏轨时 t0 取当前时刻（时间轴语义仍供插播窗口）。
        _seg_meta = data_json.get("song_segment") or {}
        if data_json.get("type") == "song" and _seg_meta \
                and int(_seg_meta.get("seq", 0)) == 0:
            _start_s = float(_seg_meta.get("start_s") or 0.0)
            if _start_s > 0:
                if _backing_gate is not None:
                    _backing_gate.set()  # 伴奏先起播（前奏期间伴奏响、人声静默）
                    if not _backing_started.wait(timeout=_BACKING_PRELUDE_MAX_WAIT):
                        logger.warning(
                            f"[song] 伴奏起播回执超时（{_BACKING_PRELUDE_MAX_WAIT:.0f}s，"
                            "fail-open：以当前时刻为时间轴零点）")
                    # 起播时刻读方形态封装（子进程 mp.Value('d')/线程 dict 统一
                    # read_started()——F3/D21；哨兵 0.0=无回执→fail-open 取当前）
                    _t0 = (_read_started() if _read_started else 0.0) or time.monotonic()
                else:
                    _t0 = time.monotonic()
                _remain = _t0 + _start_s - time.monotonic()
                if _remain > 0:
                    logger.info(f"[song] 前奏等待 {_remain:.1f}s（人声对齐伴奏时间轴）")
                    try:
                        from utils.obs_stats import record_drop
                        record_drop("song_gap_wait", round(_remain, 1))
                    except Exception:
                        pass
                    # 前奏窗口调度循环（R5c，2026-11-11 用户模型）：等待期间
                    # **借位消费**队列其它音频（推数字人口型——静音期插播），
                    # 歌段留队等窗口结束。此前 await sleep 整段阻塞=前奏 31s
                    # 零消费（消费循环被 seq0 消费占用，出队侧借位跑不了）。
                    while True:
                        _remain = _t0 + _start_s - _SONG_PUSH_LEAD - time.monotonic()
                        if _remain <= 0:
                            break
                        _interlude_entry = None
                        with self.voice_tmp_path_queue_lock:
                            for _i, _e in enumerate(self.voice_tmp_path_queue):
                                if (isinstance(_e, dict)
                                        and not (_e.get("type") == "song"
                                                 and _e.get("song_segment"))):
                                    # 按遍历索引弹（R5c 修正：此前 pop(0) 与
                                    # 扫描条件错位——找到队尾答谢却弹了队首
                                    # 歌段，5 段被当插播连推互相 flush）
                                    _interlude_entry = (
                                        self.voice_tmp_path_queue.pop(_i))
                                    break
                        if _interlude_entry is not None:
                            logger.info(
                                f"[song] 前奏窗口插播（推数字人口型）："
                                f"{_interlude_entry.get('content', '')[:30]}")
                            # 字幕/编排回执/消费回执由 _consume_edtalk_entry
                            # 内置承担（勿重复调用——T3 单一源守卫）
                            await self._consume_edtalk_entry(
                                _interlude_entry,
                                _interlude_entry.get("voice_path", ""))
                        else:
                            await asyncio.sleep(min(_remain, 0.5))
                self._song_anchor = {"t0": _t0, "last_end": 0.0}
            else:
                self._song_anchor = {"t0": None, "last_end": 0.0}
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
        # 序列锚更新（2026-11-09 失步根修）：受理时刻=段起播锚（帧锁定出声
        # preroll ~0.3s）；last_end=受理+段时长×margin（播完保底——服务端
        # 渐进推理慢于实时时窗口门取 max(due, last_end)，宁可人声延后不
        # flush 自截断）。R4 垃圾时间（2026-11-10 用户预期）：末段受理后
        # **不清锚**——置 wrapping=True（人声播完而伴奏未完=垃圾时间：伴奏
        # 继续+自由消费音频，不属于唱歌时间）；退出=伴奏进程播完感知（出队
        # poll is_alive）或新点歌 seq0 重建锚（新歌停止当前伴奏起新唱歌时间）。
        if data_json.get("type") == "song" and _seg_meta:
            _pushed_at = time.monotonic()
            _anchor = getattr(self, "_song_anchor", None)
            if _anchor is None and int(_seg_meta.get("seq", 0)) > 0:
                # R4g 防御（song3.log 实测）：seq0 被队列满载挤掉（异常态）→
                # 锚从未建立 → 窗口机制全瘫+人声从段位置直唱。反推时间轴零点
                # t0=受理−start_s 保住对齐骨架（伴奏已随 seq0 丢失，另行豁免）。
                _start = float(_seg_meta.get("start_s") or 0.0)
                self._song_anchor = {"t0": _pushed_at - _start, "last_end": 0.0}
                _anchor = self._song_anchor
                logger.warning(
                    f"[song] 首段缺失防御：seq={_seg_meta.get('seq')} 受理时无锚，"
                    f"反推时间轴零点 t0=受理−start_s（伴奏轨可能随 seq0 丢失）")
            if _anchor is not None:
                if _anchor.get("t0") is None:
                    _anchor["t0"] = _pushed_at  # start_s=0 首段：受理即零点
                _seg_dur = float(getattr(self.edtalk_client,
                                         "last_duration_s", 0.0) or 0.0)
                if _seg_dur > 0:
                    # R4f 加法化：受理+preroll+段时长=帧锁定播完的物理估计
                    # （乘法 margin 的残差逐段累积已退役）
                    _anchor["last_end"] = max(
                        _anchor.get("last_end") or 0.0,
                        _pushed_at + _SONG_PREROLL_S + _seg_dur)
                if int(_seg_meta.get("seq", 0)) == int(_seg_meta.get("total", 0)) - 1:
                    _anchor["wrapping"] = True  # 垃圾时间（R4：替代清锚）
            # 段推送诊断（2026-11-10 R4b 实测排障）：每段受理即打点——
            # seq/段在歌曲时间轴位置/时长，服务端无受理时对齐主进程侧
            logger.info(
                f"[song] 段推送受理 seq={_seg_meta.get('seq')}/"
                f"{_seg_meta.get('total')} start_s={_seg_meta.get('start_s')} "
                f"时长={float(getattr(self.edtalk_client, 'last_duration_s', 0.0) or 0.0):.1f}s")
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
                    # 出队选择（2026-11-10 R3 到期优先+上批窗口门，见
                    # _pop_next_entry_locked）：①song 序列内到期段优先——
                    # 锁内扫描找第一个「seq>0∧锚存在∧due 已到」切片段，其前
                    # 插播条目延后留队不丢（跳过区间含 999 豁免；连续跳过达
                    # _OVERDUE_SKIP_LIMIT 放行队首一条=答谢时效保障）；②无到
                    # 期段→队首窗口门（due 未到锁外短睡重查，答谢可随时插队
                    # 队首被优先消费=间奏插播 uc4b）；③窗口到期推送即 flush
                    # 残余答谢（d3 人声起即停）。非 song/首段/无锚 fail-open。
                    while True:
                        with self.voice_tmp_path_queue_lock:
                            while not self.voice_tmp_path_queue:
                                # 消费者在消费完一个消息后，如果列表为空，则调用wait()方法阻塞自己，直到有新消息到来
                                self.voice_tmp_path_queue_not_empty.wait()  # 阻塞直到列表非空
                            data_json, _gate_wait, _borrowed = (
                                self._pop_next_entry_locked())
                        if _gate_wait <= 0:
                            if data_json is None:
                                continue  # 锁释放窗口内队列被清空（停止/flush）→ 回外层
                            break
                        try:
                            from utils.obs_stats import record_drop
                            record_drop("song_gap_wait", round(_gate_wait, 1))
                        except Exception:
                            pass
                        await asyncio.sleep(min(_gate_wait, 0.5))

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
                    # R5b（2026-11-11 用户纠错）：借位条目**走正常 EDTalk 路由**
                    # （推数字人、口型推理——数字人对插播音频做口型），此前
                    # 「本地外放」理解错误已回滚。插播只发生在人声静音窗口
                    # （预算预检+时间戳调度），人声有声段不被打断。
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
                                "insert_index": data_json["insert_index"],
                                # Q-1（T3-批 2026-11-06 协作审查顺带）：choreo_step_id
                                # 透传——重建原丢弃扩展键，编排实例精确匹配（O6）退化
                                # (type,content) 容差；builtin 完成回调按此回传 step_id。
                                "choreo_step_id": data_json.get("choreo_step_id", ""),
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
                                "speed": 1,
                                "choreo_step_id": data_json.get("choreo_step_id", ""),
                            }
                        # 字幕由 F3 开播回调在真实起播时刻推送（play()=入队非同步
                        # 播放，此处推会随队列积压超前）——builtin 播放器专属接线。
                        self.audio_player.play(data_json)
                        # W3（T3-批 2026-11-06 协作审查，uc1b 裁决落地）：本地路径
                        # 观测回执收敛至 builtin 真实完成点单点（audio_core
                        # _choreo_completion_cb 完成点②）——移交点不再发观测回执，
                        # 根修 builtin 路径 total_consumed/by_type 双计。M3 原双报
                        # 语义（移交点+真实完成点各一）退役；编排实例匹配
                        # （_notify_choreo_complete，下方）保留在移交点——编排序列
                        # 推进语义不变（inflight 重置对完成点二次回调幂等）。
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
            # 2026-11-10 F5/D15：伴奏子进程纳入停止链（协作 stop_event→短等
            # →terminate 兜底+exitcode 观测；daemon 线程时代不纳入治理属既有
            # 缺口，子进程化后缺口可见化）
            self._stop_backing_player()
            return
        self.audio_player.skip_current_stream()
