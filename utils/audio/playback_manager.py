"""
音频播放管理模块 - 管理音频播放、文案播放等功能
"""

import asyncio
import shutil
import threading
import time
import traceback
import os
from typing import Optional
from ..my_log import logger
from ..edtalk_realtime import is_edtalk_active
from .queue_manager import QueueMixin, _split_group_note, normal_queue_len
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

# 前奏借位预算安全余量（2026-11-26 人声对齐根修）：R5c 借位 await 全程阻塞
# 播完才返回，估时（pydub 实测/文本估算兜底）存在读数与出声延迟残差——
# 借位须满足 est ≤ remain−余量 才放行，余量吸收尾部过冲，保证循环跳出时
# remain ≤ 0 即刻推人声（伴奏真实时间轴不被顺延）。值与闪避恢复余量同量级。
_SONG_BORROW_BUDGET_MARGIN_S = 0.5

# 段播完估计的出声延迟（2026-11-11 R4f 实测校准）：帧锁定出声=受理+preroll
# （首块推理就绪 ~0.3s），播完=受理+preroll+段时长——last_end 用加法精确化
# （此前乘法 margin 1.03/1.15 的残差逐段累积：45s 段 1.35s/段，实测相邻无
# 间奏段「人声停顿 1.5s、与伴奏差异越来越大」的根因）。推理慢于实时的极端
# 场景由 e13 失步声明兜底（宁可截断尾巴不破坏对齐）。
_SONG_PREROLL_S = 0.3

# 间奏插播硬性条件（2026-11-10 R4，用户指定）：间奏窗口剩余<此值或窗口本身
# <此值 → 不再起插播音频（避免说一半）。用户口径：「进入间奏开始计时，不足
# 3 秒时无需消费音频，或间奏空间本身就不足 3 秒，也无需消费音频」。
# 2026-11-25 用户裁决#7：本值作为基线，实际阈值经
# _interlude_min_consume_s_locked 按队列积压自适应下调（软兜底——替代已
# 移除的强制起播，预算预检始终保留）。
_INTERLUDE_MIN_CONSUME_S = 3.0

# 间奏借位高优豁免线（2026-11-25 问题2方案A，用户裁决）：≥此值的借位候选
# （点歌应答/答谢等）豁免窗口预算门——实测前奏期间答谢被「应答时长>剩余
# 窗口」预算门挤到间奏、而前奏空窗仍被在飞文案尾巴占据；高优音频改为立即
# 借位播出，人声起点顺延由 EDTalk 帧锁定播放等待自动吸收（时间轴漂移由
# e13 失步声明兜底，与既有「人声照唱」降级语义对称）。
_INTERLUDE_PRIORITY_BYPASS = 80

# 念白单飞门超时（2026-11-16 弹幕↔回复配对批次）：念白消费后其配对回复
# 超此秒数仍未出队（LLM 失败/流式熔断/登记竞态遗漏）→ 强制放行后续念白，
# 防配对等待卡死消费链。LLM+TTS 正常 3-8s，15s 覆盖慢速模型冗余。
_READ_REPLY_GATE_TIMEOUT = 15.0

# 配对门分级续等窗（2026-11-26 缺陷1 根修）：首次 15s 超时且组回复已在两队列
# 可见（已入队、仍在合成/排队——满载洪峰下 LLM 生成+背压门+GPU 并发≤2 排队
# 可能 >15s）→ 续等一轮此值再放弃（总上限 25s）；已续等过仍超时=真失败，
# 放弃并计数。超时率告警口径：obs read_reply_gate_timeout 持续增长=阈值失效
# 信号；read_reply_gate_extend=分级续等触发率（应远低于超时率）。
_READ_REPLY_GATE_EXTEND_S = 10.0

# 伴奏闪避（2026-11-12 伴奏闪避批次，用户需求）：前奏/间奏窗口借位消费其它
# 音频（播报/答谢等非歌曲音频）时伴奏压低系数——保证播报可辨；歌曲人声不
# 触发（严格 borrowed 口径）。固定 0.5（用户决定不加配置面）。恢复=播报
# 播完≈受理+时长+余量（与 last_end=受理+preroll+段时长 同源估计口径）。
_BACKING_DUCK_FACTOR = 0.5
_BACKING_DUCK_RELEASE_MARGIN_S = 0.5

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
                        started_event=None, started_box=None, duck_box=None):
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
    为歌曲时间轴零点（t0）等待前奏后再推，对齐伴奏时钟。

    2026-11-12 伴奏闪避批次：duck_box 非 None 时写流循环每 chunk 读取系数
    （dict["v"]，1.0=原音量）并缩放——与子进程 duck_value 同型（降级线程
    无 mp 原语，dict 载体；GIL 下主进程单写者安全）。"""
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
        # 伴奏闪避（2026-11-12）：每 chunk 读系数缩放——与子进程同型（降级
        # 线程 dict 载体）；读缩放原语延迟导入（backing_player 薄入口），
        # factor<1 才缩放（正常路径零开销）
        from utils.audio.backing_player import _read_duck as _duck_read, \
            _scale_chunk as _duck_scale
        while data:
            _factor = _duck_read(duck_box)
            if _factor < 0.999:
                data = _duck_scale(data, wf.getsampwidth(), _factor)
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
        due = anchor["t0"] + float(seg.get("start_s") or 0.0) - self._song_timing("song_push_lead_s")
        last_end = anchor.get("last_end") or 0.0
        if last_end > due:
            due = last_end
        remaining = due - time.monotonic()
        return remaining if remaining > 0 else 0.0

    def _pop_next_entry_locked(self):
        """出队选择总入口（2026-11-17 组原子消费批次）：先过组原子门
        （_group_atomic_pop_locked——同消息切分段组开始消费后连续消费完成，
        其他优先级信息不得插入组中间；唯一例外动态文案 trends_copywriting
        维持 2026-11-16 段间让位批次的段边界插队现状），未命中再走既有
        出队选择逻辑 _pop_select_locked（2026-11-10 R4 窗口预算模型）。

        B2 第二批出队因果门（2026-11-27，C3 用户裁决「音频随画面取消」）：
        编排条目（choreo_step_id 非空）出队时校验 step 活跃性——非活跃=孤儿
        音轨（序列已被打断/覆盖/收尾，画面永不到来）静默丢弃并计数（P1-7
        「有声音无画面」错配的最后防线；上游 CancelAudio/is_active_step
        入队门覆盖不了的残留窗口在此收口）。查询异常 fail-open（音频照播）。"""
        dropped = 0
        while True:
            forced = self._group_atomic_pop_locked()
            if forced is not None:
                data_json, gate_wait, borrowed = forced
            else:
                data_json, gate_wait, borrowed = self._pop_select_locked()
            orphan_reason = None
            if isinstance(data_json, dict):
                sid = data_json.get("choreo_step_id")
                tok = data_json.get("choreo_preempt_token")
                if sid and self._choreo_step_orphan(str(sid)):
                    orphan_reason = f"step={sid} 非活跃（序列已替换/收尾）"
                elif tok and not self._choreo_token_alive(str(tok)):
                    orphan_reason = f"token={tok} 已死亡（请求被覆盖/执行/失效）"
            if orphan_reason:
                dropped += 1
                logger.warning(
                    f"[choreo-gate] 孤儿编排音轨出队丢弃（{orphan_reason}，"
                    f"累计 {getattr(self, '_choreo_orphan_dropped', 0) + dropped}）："
                    "problem=编排序列已被替换但音轨滞留队列；"
                    "cause=打断/覆盖/收尾时该音轨已越过撤销窗口；"
                    "fix=音频随画面取消（C3 裁决 2026-11-27），静默丢弃不错配")
                continue
            if dropped:
                self._choreo_orphan_dropped = getattr(self, "_choreo_orphan_dropped", 0) + dropped
            # 组上下文登记（2026-11-17 组原子消费批次）：切分段出队即登记组绑定
            # （id+type）；非切分段不清 ctx——组完成判定由组原子门扫描驱动
            # （队列无同组剩余段=组完成，门内清除），song 锚/借位等旁路出队不
            # 丢组上下文（歌毕恢复组原子续消费）。
            sg = data_json.get("split_group") if isinstance(data_json, dict) else None
            if isinstance(sg, dict) and sg.get("id"):
                self._atomic_group_ctx = {"id": sg["id"], "type": data_json.get("type", "")}
            return data_json, gate_wait, borrowed

    def _choreo_step_orphan(self, step_id: str) -> bool:
        """B2 第二批因果门查询：编排 step 是否已非活跃（孤儿）。编排器未注册
        /查询异常一律 fail-open（False=照播，不吞音频）。"""
        try:
            from utils.orchestration.choreographer import get_choreographer
            ch = get_choreographer()
        except Exception:
            return False
        if ch is None:
            return False
        try:
            return not ch.is_active_step(step_id)
        except Exception:
            return False

    def _choreo_token_alive(self, token: str) -> bool:
        """C3 因果门查询：preempt 令牌是否仍活跃。fail-open=True（照播）。"""
        try:
            from utils.orchestration.choreographer import get_choreographer
            ch = get_choreographer()
        except Exception:
            return True
        if ch is None:
            return True
        try:
            return bool(ch.choreo_token_alive(token))
        except Exception:
            return True

    def _group_atomic_pop_locked(self):
        """组原子门（2026-11-17 组原子消费批次，须在 voice_tmp_path_queue_lock
        持有内调用）。

        语义（用户口径）：同一消息切分的片段（split_group 组绑定）视为一个
        组，开始消费后连续消费完成——念弹幕 2 段+弹幕回复 4 段场景：念弹幕组
        播完前弹幕回复不得插入；弹幕回复组播完前其他信息不得插入。唯一例外
        动态文案（trends_copywriting）：其分段组保持 2026-11-16 段间让位批次
        的刻意设计（段边界允许高优插队/让位）。

        判定：队列中还存在在播/刚播组（_atomic_group_ctx=上一个出队切分段）
        的下一段 → 组未完成 → 强制续消费同组下一段（无视队列中更高权重条目）；
        无同组剩余段 → 组完成（ctx 清除）→ 恢复正常权重 pop。

        边界与防死锁：组内段被队满丢弃/合成失败跳过=该段永不入队，扫描自然
        落空视为已消费（组推进不卡死）；单段组出队后扫描即落空=天然原子。
        song 切片走 song_segment 字段（非 split_group），不受本门影响。

        fail-open（点歌时间轴优先，沿念白单飞门先例）：song 锚存在（t0 有值）
        时不生效——组原子不破坏「间奏结束立刻切回人声」与编排插队音轨
        （priority_override=500）对歌曲间奏的插播（插播发生在间奏窗口边界
        而非同组段间）；锚清除（垃圾时间结束/收尾）后组原子恢复。

        Returns: None=未命中（调用方走 _pop_select_locked）；
                 (data_json, 0.0, False)=强制续消费的同组下一段。"""
        ctx = getattr(self, "_atomic_group_ctx", None)
        if not isinstance(ctx, dict) or not ctx.get("id"):
            return None
        anchor = getattr(self, "_song_anchor", None)
        if anchor and anchor.get("t0") is not None:
            return None  # 点歌时间轴优先（fail-open，不破坏间奏插播语义）
        if ctx.get("type") == "trends_copywriting":
            return None  # 动态文案组：维持段边界插队现状（2026-11-16 批次设计）
        queue = self.voice_tmp_path_queue
        gid = ctx.get("id")
        # 2026-11-26 双 worker 乱序根修（7b）：组内按最小 seq 定位下一段——
        # 双 worker 合成完成序≠seq 序，段可能乱序入库；此前按「第一个同组段」
        # 推进会把后入库的小 seq 段排到前面（倒序播出）。缺 seq 防御：不参与
        # 定位比较的段按队序兜底（无 seq 段仅在全部缺失时取第一个）。
        _best_idx = None
        _best_seq = None
        _first_idx = None
        for i, e in enumerate(queue):
            if not (isinstance(e, dict)
                    and isinstance(e.get("split_group"), dict)
                    and e["split_group"].get("id") == gid):
                continue
            if _first_idx is None:
                _first_idx = i
            _sq = e["split_group"].get("seq")
            if (isinstance(_sq, int)
                    and (_best_seq is None or _sq < _best_seq)):
                _best_idx, _best_seq = i, _sq
        idx = _best_idx if _best_idx is not None else _first_idx
        if idx is None:
            self._atomic_group_ctx = None  # 组完成（无同组剩余段）→ 正常权重 pop
            return None
        entry = queue.pop(idx)
        logger.info(f"组原子续消费：无视插队条目续播同组下一段"
                    f"{_split_group_note(entry)}")
        return entry, 0.0, False

    def _pop_select_locked(self):
        """出队选择（2026-11-10 R4 窗口预算模型——用户实测反馈重构，须在
        voice_tmp_path_queue_lock 持有内调用；2026-11-17 组原子消费批次
        原方法体原样提取为 _pop_select_locked，组原子门前置见
        _pop_next_entry_locked）。

        用户设计预期（2026-11-10 实测反馈）：伴奏响=唱歌时间；其它消费音频
        只能在间奏期间消费；间奏剩余不足 3s（或间奏本身不足 3s）不再起音频
        （避免说一半）；间奏结束立刻切回人声（无论插播条目是否播完）；人声
        分段全部对齐伴奏播完；人声播完而伴奏未完=垃圾时间（伴奏继续+自由
        消费，新点歌可停止当前伴奏起新唱歌时间）。

        ① 垃圾时间（anchor["wrapping"]）：伴奏进程死（is_alive poll，fail-soft）
        → 清锚回 IDLE；否则**全直通 pop(0)**（自由消费，无窗口约束）。
        ② 唱歌时间（锚存在）：a) 到期 song 段（due≤now，含 last_end 保底）
        → pop(i) 跳过其前插播条目（延后留队不丢）=
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
                    # 垃圾时间=伴奏自由消费期（用户 2026-11-25 确认为刻意
                    # 设计，与 ②b 收尾间隙分支语义统一）。2026-11-25 问题②
                    # 口径补齐：其它消息优先于文案——队列存在非文案条目时
                    # 先消费其它消息（段边界让位语义同无锚态），仅剩文案
                    # 时 pop(0) 续播文案
                    _pop_i = 0
                    for _i, _e in enumerate(queue):
                        if (isinstance(_e, dict)
                                and not (_e.get("type") == "trends_copywriting"
                                         and isinstance(
                                             _e.get("split_group"), dict))):
                            _pop_i = _i
                            break
                    return queue.pop(_pop_i), 0.0, False
                anchor["wrapping"] = False  # 新歌到来：回到唱歌时间机制
                logger.info("[song] 垃圾时间结束（新点歌触发），进入新唱歌时间")
            now = time.monotonic()
            last_end = anchor.get("last_end") or 0.0
            # ②a 到期 song 段：间奏结束立刻切回人声（延后插播条目）。
            # 2026-11-25 用户裁决：连续跳过强制起播机制（原 _OVERDUE_SKIP_LIMIT
            # 放行队首）移除——歌曲常含多个 <3s 间奏/停顿，窗口不足时强制插播
            # 会造成音频消费错乱；插播仅由 ②b/②c 窗口预算预检裁决，窗口不足时
            # 条目留队至下一合规窗口或歌毕锚清除后消费（饿死至歌尾为接受取舍）。
            for i, entry in enumerate(queue):
                if not isinstance(entry, dict) or entry.get("type") != "song":
                    continue
                seg = entry.get("song_segment") or {}
                if not seg or int(seg.get("seq", 0)) == 0:
                    continue
                due = anchor["t0"] + float(seg.get("start_s") or 0.0) - self._song_timing("song_push_lead_s")
                if last_end > due:
                    due = last_end  # 播完保底地板（F8：防插播后下段抢跑）
                if now < due:
                    continue
                return queue.pop(i), 0.0, False
            # ②b 队首=插播条目 → 窗口预检（预算语义+3s 硬性条件）
            # 2026-11-24 用户裁决：唱歌时间排除动态文案插播——文案分段在锚存续
            # 期间一律留队（含无后续 song 段的收尾间隙），歌毕锚清后按段边界
            # 让位续消费；其它消息（答谢/回复等）照旧按窗口预算插播。队首为
            # 文案分段时向后扫描首个非歌段非文案条目做预检（文案让位语义同
            # 无锚态——其它消息不被文案挡住）。
            head = queue[0]
            _head_is_copy = (isinstance(head, dict)
                             and head.get("type") == "trends_copywriting"
                             and isinstance(head.get("split_group"), dict))
            _head_is_song = (isinstance(head, dict)
                             and head.get("type") == "song")
            if not _head_is_copy and not _head_is_song:
                window_end = self._next_song_due_locked(anchor)
                if window_end is None:
                    # 无后续 song 段（末段已消费完的收尾间隙）→ 自由消费
                    return queue.pop(0), 0.0, False
                remaining = window_end - now
                window_total = window_end - (last_end if last_end > 0 else now)
                min_consume = self._interlude_min_consume_s_locked()
                # 方案A（2026-11-25 问题2）：≥80 高优条目（点歌应答/答谢）借位
                # 豁免窗口预算门——宁顺延人声起点（EDTalk 帧锁定等待+e13 兜底）
                # 不把高优答谢积压到间奏
                _p = self._entry_priority_of(head)
                if not (_p is not None and _p >= _INTERLUDE_PRIORITY_BYPASS):
                    if remaining < min_consume or window_total < min_consume:
                        # 硬性条件：剩余/窗口不足最小余量 → 不起音频（避免说
                        # 一半）——等到期推人声；队满则遗弃未插播普通条目
                        # （2026-11-25 裁决#3，不积累到歌毕）
                        self._abandon_song_overflow_locked()
                        return None, max(remaining, 0.0) or self._song_timing("song_push_lead_s"), False
                    if self._entry_duration_s(head) > remaining:
                        # 预算语义：该音频播不完窗口 → 不起下一条（消费完剩余
                        # 不足最小余量时已由上面拦住）；队满遗弃同上
                        self._abandon_song_overflow_locked()
                        return None, max(remaining, 0.0) or self._song_timing("song_push_lead_s"), False
                else:
                    logger.info(
                        f"[song] 高优条目（权重{_p}）豁免间奏预算门提前借位："
                        f"{head.get('voice_path', '')}")
                return queue.pop(0), 0.0, True
            if _head_is_copy:
                # 队首=文案分段：向后扫描首个非歌段非文案条目按窗口插播
                window_end = self._next_song_due_locked(anchor)
                if window_end is None:
                    remaining = self._song_timing("song_push_lead_s")  # 收尾间隙：无门，自由消费
                    _gated = False
                else:
                    remaining = window_end - now
                    _gated = True
                    window_total = window_end - (
                        last_end if last_end > 0 else now)
                _min_consume = self._interlude_min_consume_s_locked()
                for i, entry in enumerate(queue):
                    if not isinstance(entry, dict):
                        continue
                    if (entry.get("type") == "song"
                            and entry.get("song_segment")):
                        continue  # 歌段留队（②a 到期/窗口门管）
                    if (entry.get("type") == "trends_copywriting"
                            and isinstance(entry.get("split_group"), dict)):
                        continue  # 唱歌时间不插播文案（用户裁决）
                    # 方案A（2026-11-25 问题2）：≥80 高优音频豁免窗口预算门
                    # （前奏期间答谢不再被「时长>剩余」挤到间奏；人声顺延由
                    # EDTalk 帧锁定等待+e13 失步声明兜底）
                    _p = self._entry_priority_of(entry)
                    _bypass = (_gated and _p is not None
                               and _p >= _INTERLUDE_PRIORITY_BYPASS)
                    if _gated and not _bypass:
                        if (remaining < _min_consume
                                or window_end - (
                                    last_end if last_end > 0 else now)
                                < _min_consume):
                            continue  # 窗口不足最小余量 → 不起音频（避免说一半）
                        if self._entry_duration_s(entry) > remaining:
                            continue  # 预算：播不完窗口 → 下一条
                    return queue.pop(i), 0.0, True
                # 仅文案在队（或全部超窗）→ 等到期推人声，文案留队；
                # 队满则遗弃未插播普通条目（2026-11-25 裁决#3）
                self._abandon_song_overflow_locked()
                wait = ((max(remaining, 0.0) if _gated else 0.0)
                        or self._song_timing("song_push_lead_s"))
                return None, wait, False
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
                if (entry.get("type") == "trends_copywriting"
                        and isinstance(entry.get("split_group"), dict)):
                    continue  # 唱歌时间不插播文案（2026-11-24 用户裁决：
                    # 锚存续期间文案一律留队，歌毕锚清后续消费）
                dur = self._entry_duration_s(entry)
                min_consume = self._interlude_min_consume_s_locked()
                # 方案A（2026-11-25 问题2）：≥80 高优借位豁免最小余量门
                _p = self._entry_priority_of(entry)
                if not (_p is not None and _p >= _INTERLUDE_PRIORITY_BYPASS):
                    if remaining - dur < min_consume:
                        continue  # 超窗/不足最小余量 → 看下一条
                return queue.pop(i), 0.0, True  # R5b：借位走 EDTalk 口型
            # 全队列不可借 → 等窗口；队满则遗弃未插播普通条目（2026-11-25
            # 裁决#3，不积累到歌毕）
            self._abandon_song_overflow_locked()
            return None, gate_wait, False
        # 念白单飞门（2026-11-16 弹幕↔回复配对批次）：song 窗口判定之后、
        # 常规 pop 之前——无 pending 时零开销直通；有 pending（更早念白的
        # 回复未出队）时放行首个非念白条目（等待中的回复/插播音频），念白
        # 一律留队。song 窗口分支已提前 return（点歌时间轴优先，fail-open
        # 不参与配对门）。（2026-11-24 契约统一：门返回三元组
        # (放行条目|None, 等待秒, borrowed)，此前放行分支返回元组而调用方
        # 按浮点比较——tuple>int TypeError 静默丢条目，实际为坏分支。）
        rc_entry, rc_wait, rc_borrowed = self._read_reply_gate_locked(queue)
        if rc_entry is not None:
            return rc_entry, rc_wait, rc_borrowed
        if rc_wait > 0:
            return None, rc_wait, False
        # 动态文案段边界让位（2026-11-24 用户口径：任何其它权重消息都能插队
        # 消费文案）：队首为文案分段且队列存在其它待播条目 → 第一个非文案
        # 条目插队出队，文案段延后留队（seq 保序，间奏窗口/空闲续消费）。
        # 此前 song 切片与文案分段同为队尾追加，song seq0 被剩余文案段完全
        # 阻塞（实测：文案全部消费完歌曲才起播，点歌画面已超时回默认）。
        head = queue[0]
        if (isinstance(head, dict) and head.get("type") == "trends_copywriting"
                and isinstance(head.get("split_group"), dict)):
            for i in range(1, len(queue)):
                e = queue[i]
                if (isinstance(e, dict) and e.get("type") == "trends_copywriting"
                        and isinstance(e.get("split_group"), dict)):
                    continue  # 跳过后续文案段（文案间 FIFO，各组 seq 保序）
                return queue.pop(i), 0.0, False
        return queue.pop(0), 0.0, False

    def _read_reply_gate_locked(self, queue):
        """念白单飞门（2026-11-16 弹幕↔回复配对批次，须在
        voice_tmp_path_queue_lock 持有内调用）。

        语义（用户中压实测口径）：念弹幕消费后、对应 AI 回复消费前，不消费
        下一条念白——根治权重队列下念白（60）持续压制回复（50）导致的
        「念了多条弹幕才回答第一条」。机制：念白出队时经 reply_gate_track_popped
        登记 pending[link]；回复出队解除。门控期非念白条目正常放行（回复自己/
        插播音频），仅念白留队。

        fail-open：pending 空（常态）零开销直通；条目无 reply_link（未打标
        旧条目/聊天源）不参与；超时 _READ_REPLY_GATE_TIMEOUT 强制放行；
        生产端 abandon_reply_link（无回复路径）直接豁免。

        Returns: (entry|None, wait_s, borrowed) 三元组（2026-11-24 契约统一）
          entry 非 None=门已弹出的放行条目（调用方直接返回，勿再 pop）；
          entry None 且 wait_s>0=门控等待秒数（0.5=轮询节奏，调用方锁外
          sleep 后重查）；entry None 且 wait_s=0=无 pending（调用方常规 pop）。"""
        now = time.monotonic()
        try:
            from utils.obs_stats import record_drop
        except Exception:
            record_drop = None
        with QueueMixin._reply_gate_lock:
            pending = QueueMixin._reply_gate_pending
            # pending 超时兜底（2026-11-26 分级超时根修，缺陷1）：此前直接放弃
            # =念白/回复错位（内容错配级，比丢消息更隐蔽）——满载洪峰下回复
            # 合成排队（LLM 生成+背压门+GPU 并发≤2）可能 >15s。改两级判定：
            # ① 首次超时且组回复已在两队列可见（已入队、仍在合成/排队）→
            #   续等一轮 EXTEND_S（pending 时刻回拨使新超时点=now+EXTEND_S）；
            # ② 已续等过 或 不可见（大概率 LLM 失败/生产端放弃路径漏登记）→
            #   放弃 + 超时计数（阈值失效告警口径）。
            for lk in [lk for lk, ts in pending.items()
                       if now - ts >= self._reply_gate_timeout_s()]:
                _visible = self._reply_group_visible_locked(lk, queue)
                if (_visible
                        and lk not in QueueMixin._reply_gate_extended):
                    QueueMixin._reply_gate_extended[lk] = now
                    pending[lk] = (now - self._reply_gate_timeout_s()
                                   + self._reply_gate_extend_s())
                    if record_drop:
                        record_drop("read_reply_gate_extend")
                    continue
                del pending[lk]
                QueueMixin._reply_gate_abandoned[lk] = now
                QueueMixin._reply_gate_extended.pop(lk, None)
                if record_drop:
                    record_drop("read_reply_gate_timeout")
                    # B4 越界告警（阈值失效信号，60s 冷却）
                    self._alarm_reply_gate_timeout()
            if not pending:
                return None, 0.0, False
        # 有在途等待：允许弹出第一个非念白条目（等待中的回复或插播音频），
        # 念白条目（含无 link 旧条目，保守一律留队）全部跳过；动态文案分段
        # 同步让位（2026-11-24 用户口径：文案让位任何消息——门控期插队条目
        # 不得被文案段挡住）。
        for i, entry in enumerate(queue):
            if not isinstance(entry, dict):
                return queue.pop(i), 0.0, False
            if (entry.get("type") == "read_comment"
                    or entry.get("producer_type") == "read_comment"):
                continue  # 念白留队（配对语义）
            if (entry.get("type") == "trends_copywriting"
                    and isinstance(entry.get("split_group"), dict)):
                continue  # 文案段让位（延后留队，段边界语义同调用方）
            return queue.pop(i), 0.0, False
        # 队列全是念白/文案段 → 等待回复合成入队（调用方按 wait_s 轮询重查）
        return None, 0.5, False

    def _reply_gate_timeout_s(self):
        """配对门超时读侧（B1 阈值配置化 2026-11-26）：config 键
        play_audio.reply_gate_timeout_s，缺省/非法=15.0（原常量值）。
        定标依据：LLM P95 + TTS P95×回复段数 + 排队余量，见
        utils/threshold_calibration.py。"""
        try:
            v = float(self.config.get("play_audio", "reply_gate_timeout_s"))
        except (TypeError, ValueError, AttributeError):
            return _READ_REPLY_GATE_TIMEOUT
        return v if v > 0 else _READ_REPLY_GATE_TIMEOUT

    def _reply_gate_extend_s(self):
        """配对门分级续等窗读侧（B1）：config 键 play_audio.reply_gate_extend_s，
        缺省/非法=10.0。"""
        try:
            v = float(self.config.get("play_audio", "reply_gate_extend_s"))
        except (TypeError, ValueError, AttributeError):
            return _READ_REPLY_GATE_EXTEND_S
        return v if v > 0 else _READ_REPLY_GATE_EXTEND_S

    # C4 硬件相关常量定标表（2026-11-27）：名称 → (默认值, 下界, 上界)。
    # 定标依据：推送预补偿=pop→push HTTP 受理开销（0.3s 实测）；preroll=首块
    # 推理出声延迟（0.3s 实测 R4f）；借位余量/闪避恢复=闪避收敛量级（0.5s）；
    # 闪避系数=用户裁决 0.5；间奏基线=用户裁决 3s。配置面=play_audio.<名称>，
    # 现场按硬件差异（GPU 推理延迟/声卡延迟/HTTP 开销）调定，不改源码。
    _SONG_TIMING_SPECS = {
        "song_push_lead_s": (_SONG_PUSH_LEAD, 0.0, 2.0),
        "song_preroll_s": (_SONG_PREROLL_S, 0.0, 2.0),
        "song_borrow_budget_margin_s": (_SONG_BORROW_BUDGET_MARGIN_S, 0.0, 3.0),
        "backing_duck_factor": (_BACKING_DUCK_FACTOR, 0.05, 1.0),
        "backing_duck_release_margin_s": (_BACKING_DUCK_RELEASE_MARGIN_S, 0.0, 3.0),
        "interlude_min_consume_s": (_INTERLUDE_MIN_CONSUME_S, 1.0, 15.0),
    }

    _song_timing_alarm_last: dict = {}

    def _song_timing(self, name: str) -> float:
        """C4 定标读侧（2026-11-27）：config 键 play_audio.<name>，缺省/非法
        类型=模块常量默认（静默）；数值越界=告警（60s 冷却）+回退默认——
        越界值会系统性破坏对齐模型（推人声过晚/借位溢出），宁可回退不采信。"""
        spec = self._SONG_TIMING_SPECS.get(name)
        if not spec:
            return 0.0
        default, lo, hi = spec
        try:
            v = float(self.config.get("play_audio", name))
        except (TypeError, ValueError, AttributeError):
            return default
        if v < lo or v > hi:
            now = time.monotonic()
            if now - PlaybackMixin._song_timing_alarm_last.get(name, 0.0) > 60.0:
                PlaybackMixin._song_timing_alarm_last[name] = now
                logger.warning(
                    f"[song-timing] 配置 play_audio.{name}={v} 超出定标范围 "
                    f"[{lo}, {hi}]（硬件对齐模型参数），回退默认 {default}："
                    "problem=越界值将系统性破坏人声对齐/借位预算；"
                    "fix=改回范围内或删去该键用默认")
            return default
        return v

    _gate_timeout_alarm_last = 0.0

    def _alarm_reply_gate_timeout(self):
        """B4 越界告警（2026-11-26 二期）：配对门超时放弃=基础窗+续等窗仍装
        不下回复链路延迟（LLM+背压排队+TTS）——阈值失效信号。60s 冷却防逐句
        刷屏，附观测比率（extend/timeout 计数）。"""
        try:
            now = time.monotonic()
            if now - PlaybackMixin._gate_timeout_alarm_last < 60.0:
                return
            PlaybackMixin._gate_timeout_alarm_last = now
            drops = {}
            try:
                from utils.obs_stats import snapshot as _obs_snapshot
                drops = (_obs_snapshot() or {}).get("drops", {})
            except Exception:
                pass
            t = drops.get("read_reply_gate_timeout", 0)
            x = drops.get("read_reply_gate_extend", 0)
            logger.warning(
                f"problem=念白配对门超时放弃（基础窗 {self._reply_gate_timeout_s():.0f}s"
                f"+续等窗 {self._reply_gate_extend_s():.0f}s 内回复未出队，念白/回复"
                f"可能错位）；cause=LLM+合成排队延迟超过阈值；"
                f"fix=按 utils/threshold_calibration.py 定标建议调大 "
                f"play_audio.reply_gate_timeout_s（累计超时 {t} 次/续等 {x} 次）")
        except Exception:
            pass

    def _reply_group_visible_locked(self, link, voice_queue):
        """配对组两队列可见性（分级超时判定用，2026-11-26 缺陷1 根修；须持
        voice_tmp_path_queue_lock）：同 reply_link 组的任一条目已在待播放队列
        （锁内扫描）或 message_queue（锁外快照——[:] 原地替换保对象身份惯例，
        锁外读者安全）。误读漏读只影响是否多等一轮（保守判定），不破坏放弃
        语义——回复完全不可见时按原 15s 口径及时放行，不因误判饿死消费链。"""
        link = str(link)
        for e in voice_queue:
            if isinstance(e, dict) and str(e.get("reply_link") or "") == link:
                return True
        try:
            for e in list(self.message_queue):
                if isinstance(e, dict) and str(e.get("reply_link") or "") == link:
                    return True
        except Exception:
            pass
        return False

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
            due = t0 + float(seg.get("start_s") or 0.0) - self._song_timing("song_push_lead_s")
            return max(due, last_end)
        return None

    def _entry_priority_of(self, item):
        """条目有效权重数值化（O7 单源：priority_override 优先于类型映射表；
        与 queue_manager._entry_priority 同经 audio_priority——其实现为闭包
        绑映射不可复用，此处直连单一源）。异常保守 None=未识别。"""
        try:
            from utils.audio.audio_priority import entry_priority
            p = entry_priority(item, self.config.get("filter", "priority_mapping"))
            return int(p) if p is not None else None
        except Exception:
            return None

    def _interlude_min_consume_s_locked(self):
        """间奏插播最小余量自适应（2026-11-25 用户裁决#7，须锁内调用）：
        队列普通条目（非 song 段/文案分段，normal_queue_len 口径）积压越多
        → 阈值越低——答谢积压场景下固定 3s 硬性条件会让插播条目饿死整首歌
        （打赏用户等 3-5 分钟才被答谢）。作为有预算约束的软兜底随积压放宽
        （>2 条起每 +1 条 −0.5s，下限 1s）；预算预检（时长>剩余不放行）
        不受影响，仍保证「避免说一半」。"""
        n = normal_queue_len(self.voice_tmp_path_queue)
        if n <= 2:
            return self._song_timing("interlude_min_consume_s")
        return max(1.0, self._song_timing("interlude_min_consume_s") - (n - 2) * 0.5)

    def _abandon_song_overflow_locked(self):
        """唱歌期间队满遗弃（2026-11-25 用户裁决#3，须锁内调用）：插播窗口
        预检失败且待播放队列普通容量已满时，从队首向后找第一条可遗弃普通
        条目直接遗弃（不积累到歌毕）——长歌+高频短停顿场景下积压可跨越整首
        歌时长，歌毕一次性倾泻的是几分钟前的陈旧答谢；唱歌期间即遗弃。
        跳过歌段（序列不可丢）、文案分段（锚存续期间留队，2026-11-24 裁决）、
        受保护回复组段（S1 遗弃保护）；念白候选遗弃同步 abandon_reply_link
        解除配对门。wrapping 垃圾时间为伴奏自由消费期（无窗口约束，不触发）。

        Returns: True=已遗弃至少一条。"""
        anchor = getattr(self, "_song_anchor", None)
        if not anchor or anchor.get("t0") is None or anchor.get("wrapping"):
            return False
        max_len = int(self.config.get("filter", "voice_tmp_path_queue_max_len"))
        queue = self.voice_tmp_path_queue
        if normal_queue_len(queue) < max_len:
            return False
        for i, entry in enumerate(queue):
            if not isinstance(entry, dict):
                continue
            if entry.get("type") == "song" and entry.get("song_segment"):
                continue  # 歌段序列不可丢
            if (entry.get("type") == "trends_copywriting"
                    and isinstance(entry.get("split_group"), dict)):
                continue  # 锚存续期间文案一律留队（2026-11-24 裁决）
            if self._candidate_reply_protected(entry):
                continue  # S1 遗弃保护：消费中回复组不丢（快照化：读条目冻结快照）
            queue.pop(i)
            link = entry.get("reply_link")
            if link:
                self.abandon_reply_link(link)
            try:
                from utils.obs_stats import record_drop
                record_drop("song_overflow_abandon")
            except Exception:
                pass
            logger.warning(
                f"[song] 唱歌期间队满遗弃未插播条目（不积累到歌毕）："
                f"{entry.get('voice_path', '')}"
                f"【{str(entry.get('content', '') or '')[:30]}】")
            return True
        return False

    def _entry_duration_s(self, entry):
        """插播条目音频时长（pydub 读 voice_path，fail-soft）。2026-11-25
        用户裁决：读取失败不再返回 0 放行——一条读不到时长的长音频会吃掉整个
        间奏窗口并压到下一段人声；改用文本长度估算（中文≈4 字/秒，下限 1s）
        参与窗口预检。锁内调用豁免说明：本地小音频 pydub 加载 <100ms（非重
        IO/网络），持锁窗口可容忍。"""
        path = entry.get("voice_path") or ""
        try:
            if path and os.path.isfile(path):
                from pydub import AudioSegment
                return len(AudioSegment.from_file(path)) / 1000.0
        except Exception:
            pass  # 时长读取失败 → 文本估算兜底（不放行长音频）
        return max(1.0, len(str(entry.get("content", "") or "")) / 4.0)

    def _pick_intro_borrow_locked(self, remain: float):
        """前奏窗口借位候选选择（2026-11-11 R5c 提取；须持
        voice_tmp_path_queue_lock——遍历中按索引 pop）。

        借位预算校验（2026-11-26 人声对齐根修）：借位 await 全程阻塞播完才
        返回，R5c 循环只在两次借位之间检查 due——超预算借位的尾部会越过 due，
        人声顺延（实测 ≤3s）与伴奏真实时间轴失步（用户裁决：绝对对齐）。
        估时 ≤ remain−_SONG_BORROW_BUDGET_MARGIN_S 才放行；超限条目**留队**
        （不 pop 不改序），待间奏窗口/歌毕消费；t0 永不回退。
        候选条件（2026-11-25 问题②修复口径）：非 song 切片段、非动态文案
        分段（trends_copywriting+split_group）。估时与间奏窗口预检/闪避恢复
        同源（_entry_duration_s——锁内容忍本地小音频 pydub 加载）。

        Args:
            remain: 前奏窗口剩余秒（=t0+start_s−推送预补偿−now）

        Returns:
            dict | None: 借位条目（已从队列弹出）；无候选/首候选超预算返回 None
            （首候选语义=不改排出队顺序，R5c 修正口径）。
        """
        for _i, _e in enumerate(self.voice_tmp_path_queue):
            if (isinstance(_e, dict)
                    and not (_e.get("type") == "song"
                             and _e.get("song_segment"))
                    and not (_e.get("type") == "trends_copywriting"
                             and isinstance(_e.get("split_group"), dict))):
                _est = self._entry_duration_s(_e)
                if _est > remain - self._song_timing("song_borrow_budget_margin_s"):
                    logger.debug(
                        f"[song] 前奏插播预算不足，留队待间奏窗口："
                        f"估时 {_est:.1f}s > 剩余 {remain:.1f}s："
                        f"{_e.get('content', '')[:20]}")
                    return None
                return self.voice_tmp_path_queue.pop(_i)
        return None

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
        # ⓪ 闪避残留清理（2026-11-12 伴奏闪避批次）：取消未触发恢复定时器；
        # 原语下方新建（新歌=全量起播，不残留上一首闪避态——AC-007）
        self._backing_duck_cancel_restore()
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
            duck_val = _mp.Value('d', 1.0)  # 闪避原语（1.0=原音量起播）
            proc = _mp.Process(
                target=_bp._backing_process_main,
                args=(backing_path, volume, gate, started_ev, value, stop_ev,
                      duck_val),
                daemon=True, name="song-backing")
            # spawn 的 Process.start() 是阻塞调用（100-300ms）——线程化启动，
            # 门机制吸收启动延迟（F9/D24 to_thread 纪律的线程等价形态）
            threading.Thread(target=proc.start, name="song-backing-spawn",
                             daemon=True).start()
            self._backing_proc = proc
            self._backing_stop_ev = stop_ev
            self._backing_duck = duck_val

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
            duck_box = {"v": 1.0}  # 降级线程闪避载体（dict 形态，同型语义）
            threading.Thread(
                target=_play_backing_local,
                args=(backing_path, volume, gate),
                kwargs={"started_event": started_ev, "started_box": box,
                        "duck_box": duck_box},
                name="song-backing", daemon=True).start()
            self._backing_duck = duck_box

            def _read_degraded():
                try:
                    return float(box.get("t", 0.0))
                except Exception:
                    return 0.0

            return gate, started_ev, _read_degraded, stop_ev, None, True

    def _stop_backing_player(self):
        """伴奏停止链（F5/D15）：协作停止原语（子进程每 chunk 检查）→ 短等
        → terminate 挂死兜底 → exitcode 观测。前端「停止」路径与切歌共用。
        伴奏闪避清理（2026-11-12 伴奏闪避批次）：取消恢复定时器+原语置空——
        此后 hold/release 全 no-op（AC-007：新歌经 _start_backing_player
        重建原语=全量起播）。"""
        self._backing_duck_cancel_restore()
        self._backing_duck = None
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

    # -------------------- 伴奏闪避生命周期（2026-11-12 伴奏闪避批次）--------

    def _set_backing_duck(self, factor: float):
        """闪避底层写（双形态原语）：mp.Value('d')/.value 或 dict["v"]——与
        回执 started_value 同型双形态（F3/D21 先例）。原语缺失（无伴奏轨/
        整轨模式/停止清理后）no-op；写失败 fail-soft（闪避降级为不压，不
        影响播放）。仅状态翻转时打日志（防 per-pop 噪音）。"""
        duck = getattr(self, "_backing_duck", None)
        if duck is None:
            return
        try:
            factor = max(0.0, min(1.0, float(factor)))
            if isinstance(duck, dict):
                prev = float(duck.get("v", 1.0))
                duck["v"] = factor
            else:
                prev = float(duck.value)
                duck.value = factor
            if prev != factor:
                logger.info(f"[song] 伴奏闪避 {'ON' if factor < 1.0 else 'OFF'}"
                            f"（×{factor:.2f}）")
        except Exception:
            logger.warning("[song] 伴奏闪避设置失败（fail-soft，不影响播放）\n"
                           + traceback.format_exc())

    def _backing_duck_cancel_restore(self):
        """取消未触发的闪避恢复定时器（hold/立即恢复/停止路径共用）。句柄
        置换竞态窗口=µs 级且触发方向恒为恢复（fail-safe），不设锁；无条件
        归一化属性（未调度过也可安全读取 None）。"""
        timer = getattr(self, "_backing_duck_restore_timer", None)
        if timer is not None:
            try:
                timer.cancel()
            except Exception:
                pass
        self._backing_duck_restore_timer = None

    def _backing_duck_hold(self):
        """闪避 ON：取消未触发恢复 + 压至 _BACKING_DUCK_FACTOR（借位条目起播
        前）。连续借位=重复 hold——定时器被取消即全程保持压低（AC-003），
        写 0.5 幂等。"""
        self._backing_duck_cancel_restore()
        self._set_backing_duck(self._song_timing("backing_duck_factor"))

    def _backing_duck_release_later(self, entry):
        """闪避恢复调度（播报播完≈受理+时长+余量）：push_full 返回=播报起播
        （受理），EDTalk 帧锁定 1× 播放 → 受理+pydub 时长=播完物理估计（与
        last_end=受理+preroll+段时长 同源口径）。时长未知（fail-soft=0，如
        pydub 读失败）不定时——等下一次非借位 pop（人声段 release_now）或
        停止路径恢复（宁可持续压低不提前放开盖住播报的降级语义）。"""
        self._backing_duck_cancel_restore()
        try:
            dur = float(self._entry_duration_s(entry) or 0.0)
        except Exception:
            dur = 0.0
        if dur <= 0:
            return
        timer = threading.Timer(
            dur + _BACKING_DUCK_RELEASE_MARGIN_S, self._backing_duck_release_now)
        timer.daemon = True
        self._backing_duck_restore_timer = timer
        timer.start()

    def _backing_duck_release_now(self):
        """闪避 OFF（幂等）：取消定时器 + 回全量。歌曲人声段出队/异常路径/
        停止路径/定时器回调共用出口（AC-002/006/007）。"""
        self._backing_duck_cancel_restore()
        self._set_backing_duck(1.0)

    def song_hold_alive(self) -> Optional[dict]:
        """音频侧歌曲活跃快照（A 收尾防线，2026-11-26 画面来回跳根修）：
        编排序列播完（条件1）时询问「歌是否还在唱/还有切片待播」——活跃=
        保险丝或挂起锚先于歌终到期的证据，编排据此直切 song hold 而非回默认
        （log1.txt 16:55:01 接管→16:56:07 回默认→16:56:32 切回实测）。

        活跃判定（与画面 hold 语义对齐）：
          ① 队列仍有 song 切片待播（未起播/间奏后段/新歌排队）→ 活跃；
          ② 锚活跃（t0 已建立且非 wrapping=前奏/人声/间奏进行中）→ 活跃；
        wrapping（垃圾时间，R4 口径=非唱歌时间）且无待播切片 → 不活跃。
        remaining 口径：同歌末片 start_s+真实时长 − 当前歌曲时间位置（+2s
        出声余量，与 _song_choreo_remaining_s 同源）；无锚=未起播（位置 0）。
        Returns: {"song_name": str, "remaining_s": float} 或 None。"""
        # 锁序守卫：本方法会在编排 facade _lock 内被调用（_audio_song_alive），
        # 禁止再取 voice_tmp_path_queue_lock（否则与出队门持 voice 锁→取 facade
        # 锁构成 ABBA 环）。list() 单次 C 级调用对元素引用是 GIL 原子的，
        # 快照足够（歌切片条目只追加/移除，不原地修改）。
        _segs = [e for e in list(self.voice_tmp_path_queue)
                 if isinstance(e, dict) and e.get("type") == "song"
                 and isinstance(e.get("song_segment"), dict)]
        anchor = getattr(self, "_song_anchor", None)
        _singing = bool(anchor) and anchor.get("t0") is not None \
            and not anchor.get("wrapping")
        if not _segs and not _singing:
            return None
        _cur_dir = ""
        _name = ""
        _pos = 0.0
        if _singing:
            # 唱歌中=锚所属歌（目录圈定同歌——连点新歌切片由其自身起播接管）
            _cur_dir = str(anchor.get("dir") or "")
            _name = str(anchor.get("name") or "")
            _pos = max(0.0, time.monotonic() - float(anchor["t0"]))
        if (not _singing or not _cur_dir or not _name) and _segs:
            # 锚信息缺失（旧格式锚/防御路径）→ 回退队首待播切片归属
            if not _cur_dir:
                _cur_dir = str((_segs[0].get("song_segment") or {}).get("seg_dir") or "")
            if not _name:
                _name = str(_segs[0].get("content") or "")
            if not _singing:
                _pos = 0.0
        if not _name:
            return None
        # 同歌末片定总长（目录圈定同歌——_song_choreo_remaining_s 同口径）
        _total = None
        _same = [e for e in _segs
                 if not (_cur_dir
                         and (e.get("song_segment") or {}).get("seg_dir")
                         and e["song_segment"].get("seg_dir") != _cur_dir)]
        if _same:
            _last = max(_same, key=lambda e: float(
                e["song_segment"].get("start_s") or 0.0))
            try:
                import soundfile as _sf
                _total = (float(_last["song_segment"].get("start_s") or 0.0)
                          + float(_sf.info(_last.get("voice_path")).duration))
            except Exception:
                _total = None
        if _total is None and _singing:
            _t0 = float(anchor.get("t0") or 0.0)
            _le = float(anchor.get("last_end") or 0.0)
            if _le > _t0:
                _total = _le - _t0
        if _total is None:
            return None
        _rem = _total - _pos
        if _rem <= 0:
            return None  # 歌曲时间轴已走完（防御——正常由 wrapping/清锚覆盖）
        return {"song_name": _name, "remaining_s": _rem + 2.0}

    def _song_choreo_remaining_s(self, data_json, voice_tmp_path):
        """歌曲起播剩余时长（2026-11-24 画面漂移根修，供编排 hold deadline）。

        旧口径 (total−seq)×seg_seconds 等长近似，语义切片（不等长人声段+间奏
        段）下系统性低估：实测 seg0 起播估 90s/真实 ~147s——间奏中 song hold
        与挂起文案 deadline 同时到期 → 画面回默认，下一人声段起播才切回
        （用户实测 log3.txt 17:12:39→51；用户口径：唱歌时间内消费任何消息
        都保持点歌画面）。

        口径（start_s 为歌曲时间轴坐标，天然含前序间奏）：
        song_total = 队内同歌末片 start_s + 末片真实时长；
        remaining  = song_total − 当前片 start_s（+2s 出声余量）；
        无后续片在队（当前即末片）= 当前片真实时长；
        无切片元数据（整轨直通）= 整文件时长；不可测返回 None（调用方幂等忽略）。
        """
        _seg = (data_json or {}).get("song_segment") or {}
        try:
            import soundfile as _sf
            if not _seg or not _seg.get("total"):
                # 整轨直通（无切片元数据）：剩余=整文件时长
                return float(_sf.info(voice_tmp_path).duration)
            _cur_start = float(_seg.get("start_s") or 0.0)
            _dir = _seg.get("seg_dir")
            _last = None  # (start_s, voice_path) 队内最晚切片
            try:
                with self.voice_tmp_path_queue_lock:
                    for _e in self.voice_tmp_path_queue:
                        if not (isinstance(_e, dict) and _e.get("type") == "song"
                                and isinstance(_e.get("song_segment"), dict)):
                            continue
                        _es = _e["song_segment"]
                        if _dir and _es.get("seg_dir") and _es.get("seg_dir") != _dir:
                            continue  # 异歌切片（seg_dir 圈定同歌）
                        _es_start = float(_es.get("start_s") or 0.0)
                        if _last is None or _es_start > _last[0]:
                            _last = (_es_start, _e.get("voice_path"))
            except Exception:
                _last = None
            _song_total = None
            if _last is not None and _last[0] > _cur_start and _last[1]:
                try:
                    _song_total = _last[0] + float(_sf.info(_last[1]).duration)
                except Exception:
                    _song_total = None
            if _song_total is None:
                # 无更晚切片在队（当前即末片）：song_total=当前片起点+真实时长
                try:
                    _song_total = _cur_start + float(
                        _sf.info(voice_tmp_path).duration)
                except Exception:
                    return None
            return max(0.0, _song_total - _cur_start) + 2.0
        except Exception:
            return None

    async def _consume_edtalk_entry(self, data_json, voice_tmp_path):
        """EDTalk 路由条目消费（T2-批 2026-11-06 自 only_play_audio 原样提取）：
        双轨伴奏起播门/编排静默窗口等待/song 起播锚/在飞条目登记/push_full/
        字幕与编排完成回执/消费回执/切片清理。异常交由调用方
        （W2 根修 finally 兜底伴奏闪避清理）。
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
        if _choreo_guard is not None:
            # A 收尾防线（2026-11-26 画面来回跳根修）：注册音频侧歌曲活跃查询
            # provider——编排序列播完时询问「歌是否还在唱」，活跃=直切 song
            # hold 不回默认。幂等覆盖（choreographer 重建随下一次消费自愈）。
            try:
                _choreo_guard.set_song_alive_provider(self.song_hold_alive)
            except Exception:
                pass
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
                        _remain = _t0 + _start_s - self._song_timing("song_push_lead_s") - time.monotonic()
                        if _remain <= 0:
                            break
                        _interlude_entry = None
                        with self.voice_tmp_path_queue_lock:
                            # 2026-11-25 问题②修复：文案分段不进前奏借位（用户
                            # 口径：唱歌时间文案一律留队，人声播完后的垃圾时间
                            # 才续播）——此前仅 R5c 歌段排除，前奏空窗被在飞文案
                            # 分段占据（log7 17:01:16~22 实测连播 4 段，人声
                            # 延迟与伴奏失步）
                            # 2026-11-26 借位预算校验内聚 _pick_intro_borrow_locked
                            _interlude_entry = self._pick_intro_borrow_locked(_remain)
                        if _interlude_entry is not None:
                            logger.info(
                                f"[song] 前奏窗口插播（推数字人口型）："
                                f"{_interlude_entry.get('content', '')[:30]}")
                            # 伴奏闪避（2026-11-12 伴奏闪避批次）：前奏窗口借位
                            # 与消费循环同口径——**受理（出声）**时压伴奏，受理+
                            # 时长估计恢复；异常=未出声不压（AC-001/002/006）。
                            # 压低不放出队点（song6.log 修正：飞行等待期不抢
                            # 当前音频——前奏场景当条=点歌应答，同理）。
                            _borrow_consumed = False
                            try:
                                await self._consume_edtalk_entry(
                                    _interlude_entry,
                                    _interlude_entry.get("voice_path", ""))
                                _borrow_consumed = True
                            finally:
                                if _borrow_consumed:
                                    self._backing_duck_hold()
                                    self._backing_duck_release_later(
                                        _interlude_entry)
                                else:
                                    self._backing_duck_release_now()
                            # 字幕/编排回执/消费回执由 _consume_edtalk_entry
                            # 内置承担（勿重复调用——T3 单一源守卫）
                        else:
                            await asyncio.sleep(min(_remain, 0.5))
                self._song_anchor = {"t0": _t0, "last_end": 0.0,
                                     "name": str(data_json.get("content") or ""),
                                     "dir": str(_seg_meta.get("seg_dir") or "")}
            else:
                self._song_anchor = {"t0": None, "last_end": 0.0,
                                     "name": str(data_json.get("content") or ""),
                                     "dir": str(_seg_meta.get("seg_dir") or "")}
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
                # 2026-11-24 画面漂移根修：剩余时长按真实切片文件计算（含间奏，
                # _song_choreo_remaining_s 单源）——旧 (total-seq)×seg_seconds
                # 等长近似低估，间奏中 hold 到期画面回默认（log3.txt 实测）。
                _remaining = self._song_choreo_remaining_s(
                    data_json, voice_tmp_path)
                if _remaining and _remaining > 0:
                    _song_choreo.on_song_started(
                        str(data_json.get("content", ""))[:40], _remaining)
        # 在飞条目登记（2026-11-16 段间让位批次）：push 受理前登记当前消费
        # 条目——EDTalk 消费时序锚（组原子门/窗口机制依赖）；push 返回后由
        # finally 统一清理。（2026-11-21 动态文案触发门改为只看待合成消息
        # 队列——原 task_scheduler._is_playback_idle 三条件门退役，在飞登记
        # 不再作为触发门判定依据。）
        self._edtalk_current_entry = data_json
        # T7/d3（2026-11-05）：song 切片跳过飞行等待——切片时序
        # 锚定伴奏（uc4b），人声起即停答谢=push 即 flush 替换
        # （e5 口径：替换生效时刻=本次 push 受理时刻，非推送发起）
        _wait_flight = not (data_json.get("type") == "song"
                            and data_json.get("song_segment"))
        try:
            _push_ok = await self.edtalk_client.push_full(
                voice_tmp_path, content=data_json.get("content", ""),
                wait_flight=_wait_flight,
            )
        finally:
            self._edtalk_current_entry = None
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
                self._song_anchor = {"t0": _pushed_at - _start, "last_end": 0.0,
                                     "name": str(data_json.get("content") or ""),
                                     "dir": str(_seg_meta.get("seg_dir") or "")}
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
                        _pushed_at + self._song_timing("song_preroll_s") + _seg_dur)
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
        # 2026-11-25 问题①修复：文案分段**末段**回执延迟至服务端显示完成
        # ——push_full 返回=受理（pending 提前放行时可早于本段开播一段），
        # 受理点即发回执使 copy_done 提前落位，exit 立切截掉末句尾字
        # （实测 1-2 字缺失）。仅延迟末段（copy_done 依赖尾段回执；早段
        # 回执只作保险丝续期，受理口径无副作用），推送失败不等待。
        if (data_json.get("type") == "trends_copywriting"
                and isinstance(data_json.get("split_group"), dict)
                and int((data_json["split_group"] or {}).get("seq", 0))
                == int((data_json["split_group"] or {}).get("total", 1)) - 1
                and _push_ok):
            await self.edtalk_client.wait_last_push_displayed()
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

    async def only_play_audio(self):
        """只进行普通音频播放"""
        try:
            while True:
                try:
                    # 出队选择（2026-11-10 R3 到期优先+上批窗口门，见
                    # _pop_next_entry_locked）：①song 序列内到期段优先——
                    # 锁内扫描找第一个「seq>0∧锚存在∧due 已到」切片段，其前
                    # 插播条目延后留队不丢（2026-11-25 起无强制放行上限）；
                    # ②无到
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
                        # 等待观测（2026-11-16 分桶）：song 窗口门与念白单飞门
                        # 共用 gate_wait 通道，按锚存在性分桶避免观测混淆
                        try:
                            from utils.obs_stats import record_drop
                            if getattr(self, "_song_anchor", None):
                                record_drop("song_gap_wait", round(_gate_wait, 1))
                            else:
                                record_drop("read_reply_gate_wait",
                                            round(_gate_wait, 1))
                        except Exception:
                            pass
                        await asyncio.sleep(min(_gate_wait, 0.5))

                    # 念白配对出队回执（2026-11-16 弹幕↔回复配对批次）：念白
                    # 出队登记 pending、回复出队解除——门控时序的唯一登记点
                    # （覆盖常规 pop/song 借位/门控放行所有返回路径）
                    self.reply_gate_track_popped(data_json)

                    # 合成背压门消费回执（2026-11-16 消费回传背压批次）：出队
                    # =普通条目深度下降，唤醒门控中的合成线程（同一回执点覆盖
                    # 常规 pop/song 借位/念白门放行所有返回路径）
                    self.notify_synth_gate()

                    # 精简日志（2026-10-28）：整包 dict 倾倒退役——只留 type+content
                    # 摘要（完整字段可按需在合成/路由链路排查时临时加回）
                    logger.debug(f"待播放队列出队 type={data_json.get('type')} "
                                 f"content={str(data_json.get('content', ''))[:50]}")

                    # 伴奏闪避挂点（2026-11-12 伴奏闪避批次；同日 song6.log
                    # 实测修正：压低点=**受理（播报出声）**而非出队——②b 窗口
                    # 预检可在人声未唱完时出队借位条目（push 飞行等待把实际
                    # 出声推迟到真实间奏），出队即压低会把正在唱的人声伴奏压
                    # 掉 60s+。非借位出队（人声段/自由消费）幂等恢复（AC-004
                    # +残留清理）。压低点见分支 finally（受理后）。
                    if not _borrowed:
                        self._backing_duck_release_now()

                    voice_tmp_path = data_json["voice_path"]

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
                        # T2-批（2026-11-06 协作机制流程审查，W2 根修）：分支内
                        # 任何异常逃逸（含 CancelledError 穿透）由 finally 兜底
                        # 清理伴奏闪避现场（原释放点仅在正常完成路径，逃逸即
                        # 悬挂）。分支体提取为 _consume_edtalk_entry 逐字保持。
                        _consumed_ok = False
                        try:
                            await self._consume_edtalk_entry(data_json, voice_tmp_path)
                            _consumed_ok = True
                        finally:
                            # 伴奏闪避（2026-11-12 伴奏闪避批次）：**受理=
                            # 播报出声**（push_full 返回）→ 此刻压低，受理+
                            # 时长估计定时恢复（AC-001/002）；异常=播报未出声
                            # 不压不悬挂（AC-006）。压低不放出队点（song6.log
                            # 修正：飞行等待期间人声在唱，出队即压=压人声伴奏）
                            if _borrowed:
                                if _consumed_ok:
                                    self._backing_duck_hold()
                                    self._backing_duck_release_later(data_json)
                                else:
                                    self._backing_duck_release_now()
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
                        # 伴奏闪避（2026-11-12 伴奏闪避批次）：builtin 移交≈
                        # 助播借位出声点 → 压低+受理口径定时恢复（同 EDTalk
                        # 面）；移交异常由外层 except 兜底（AC-006）
                        if _borrowed:
                            self._backing_duck_hold()
                            self._backing_duck_release_later(data_json)
                        # ② 点歌切片末段清理：builtin play()=异步入队非播放完成判定，
                        # 末段清理依赖合成侧 _purge_stale_song_segments 清扫兜底。

                except Exception as e:
                    logger.error(traceback.format_exc())
                    # 伴奏闪避兜底（2026-11-12 伴奏闪避批次，AC-006）：受理点
                    # 语义下异常=未压低，此处幂等清残留（定时器/状态归零）。
                    self._backing_duck_release_now()
        except Exception as e:
            logger.error(traceback.format_exc())


    def _notify_choreo_complete(self, data_json):
        """编排播完回调（M3 提取，2026-10-28 消费批次：EDTalk/builtin 两路共用）。
        O6：choreo_step_id 随条目透传——按实例序号精确匹配（同内容重复入队不误配），
        未透传路径编排侧退回 (type,content) 匹配。异常隔离（失败退编排 F1 超时
        兜底，不影响播放循环）。

        2026-11-25 画面同步修复：split_group 条目附 group_pending（该组在两队列
        的剩余段数）——编排侧文案完成判定须「结尾锚定+组消费完成」双条件，早段
        回执不再误登记 copy_done。两锁顺序取用（message→voice），与
        cancel_choreo_audio 同序，不嵌套持有。

        2026-11-27 B1 结构化终结：附 copy_groups_pending（两队列中**其它**文案
        组的组数，按回执组 id 排除自身）——编排侧完成判定主条件升级为结构化
        组事件（本组空+全局空），异组残留段回执不再依赖尾串启发式兜底。"""
        try:
            from utils.orchestration import get_choreographer
            _choreo = get_choreographer()
            if _choreo is not None:
                _group_pending = 0
                _other_group_ids = set()
                sg = data_json.get("split_group")
                if isinstance(sg, dict) and sg.get("id"):
                    gid = sg.get("id")
                    with self.message_queue_lock:
                        _group_pending += sum(
                            1 for d in self.message_queue
                            if isinstance(d, dict)
                            and isinstance(d.get("split_group"), dict)
                            and d["split_group"].get("id") == gid)
                        _other_group_ids |= {
                            d["split_group"].get("id") for d in self.message_queue
                            if isinstance(d, dict)
                            and d.get("type") == "trends_copywriting"
                            and isinstance(d.get("split_group"), dict)
                            and d["split_group"].get("id")
                            and d["split_group"].get("id") != gid}
                    with self.voice_tmp_path_queue_lock:
                        _group_pending += sum(
                            1 for d in self.voice_tmp_path_queue
                            if isinstance(d, dict)
                            and isinstance(d.get("split_group"), dict)
                            and d["split_group"].get("id") == gid)
                        _other_group_ids |= {
                            d["split_group"].get("id") for d in self.voice_tmp_path_queue
                            if isinstance(d, dict)
                            and d.get("type") == "trends_copywriting"
                            and isinstance(d.get("split_group"), dict)
                            and d["split_group"].get("id")
                            and d["split_group"].get("id") != gid}
                _choreo.on_audio_complete(
                    data_json.get("type", ""), data_json.get("content", ""),
                    data_json.get("choreo_step_id") or None,
                    group_pending=_group_pending,
                    copy_groups_pending=len(_other_group_ids))
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

        EDTalk 模式（2026-11-16 聊天审查批次·打断闭嘴）：音频进入口型推理的
        方式=整句 push_full——服务端只要还有音频在消费就持续推理口型，打断时
        推送静音触发服务端 flush 替换（d3 替换语义）→ 数字人立即闭嘴。
        （此前语义：EDTalk 端无 flush-audio 契约端点、静默跳过——已由
        EDTalkClient.flush_silence 以 push 静音方式补齐，不再依赖新契约端点。）
        """
        if is_edtalk_active(self.config):
            # 2026-11-10 F5/D15：伴奏子进程纳入停止链（协作 stop_event→短等
            # →terminate 兜底+exitcode 观测；daemon 线程时代不纳入治理属既有
            # 缺口，子进程化后缺口可见化）
            self._stop_backing_player()
            client = getattr(self, "edtalk_client", None)
            if client is not None:
                client.flush_silence()
            return
        self.audio_player.skip_current_stream()
