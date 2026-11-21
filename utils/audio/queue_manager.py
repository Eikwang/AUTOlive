"""
队列管理模块 - 管理消息队列和音频路径队列
"""

import threading
import time
import traceback
from ..my_log import logger

# G2（2026-10-28 消费回传批次）：消费回执环形窗容量
_CONSUMED_RECEIPT_MAX = 50


def _split_group_note(data_json):
    """切分段组上下文附注（任务 12 根修，TODOS 2026-10-22 音频批次节）：长回复
    切分段带 split_group 组绑定（synthesis_manager 切分循环注入、put_to_queue
    透传）——队列取代/丢弃点的 log 附组上下文（第几段/共几段），失效模式恢复
    可定位可见面；非切分段返回空串。"""
    sg = data_json.get("split_group") if isinstance(data_json, dict) else None
    if not isinstance(sg, dict):
        return ""
    seq = sg.get("seq")
    return (f"〔长回复切分段：第 {seq + 1 if isinstance(seq, int) else '?'}/"
            f"{sg.get('total', '?')} 段〕")


def _is_song_slice_entry(e):
    """song 切片段判定（满载豁免/分账/驱逐候选排除共用单源）。"""
    return (isinstance(e, dict) and e.get("type") == "song"
            and bool(e.get("song_segment")))


def _is_bg_slice_entry(e):
    """背景流切片判定（2026-11-21 动态文案审查批次语义收口单源）：song 切片段
    /动态文案分段——编组序列不可丢，满载豁免+容量分账共用。"""
    return (isinstance(e, dict)
            and ((e.get("type") == "song" and e.get("song_segment"))
                 or (e.get("type") == "trends_copywriting"
                     and isinstance(e.get("split_group"), dict))))


def normal_queue_len(q):
    """容量分账口径单源（2026-11-21 队列优化批次 E3）：song 切片段=背景流
    不占普通音频队列容量、动态文案分段同入分账编组——两队列满载判定与长回复
    切分预检告警统一走本口径，消除告警虚报（点歌/文案在场时总队列长度≠普通
    容量占用量）。"""
    return sum(1 for e in q if not _is_bg_slice_entry(e))


class QueueMixin:
    """队列管理Mixin类"""
    
    def clear_queue(self, type: str="message_queue"):
        """清空 待合成消息队列|待播放音频队列

        Args:
            type (str, optional): 队列类型. Defaults to "message_queue".

        Returns:
            bool: 清空结果
        """
        try:
            if type == "voice_tmp_path_queue":
                if len(self.voice_tmp_path_queue) == 0:
                    return True
                with self.voice_tmp_path_queue_lock:
                    self.voice_tmp_path_queue.clear()
                return True
            elif type == "message_queue":
                if len(self.message_queue) == 0:
                    return True
                with self.message_queue_lock:
                    self.message_queue.clear()
                return True
        except Exception as e:
            logger.error(traceback.format_exc())
            logger.error(f"清空{type}队列失败：{e}")
            return False

    def cancel_choreo_audio(self, step_id: str):
        """O8（2026-10-28 消费批次）：编排打断撤旧音轨——从两队列移除匹配
        choreo_step_id 的未消费条目（[:] 原地替换保对象身份，与锁外读者兼容）。

        已被消费者取出（合成中/播放中）的条目无法撤销：其完成回调因编排
        inflight 已重置被静默忽略（无错配副作用）。

        Returns:
            int: 撤销条数（两队列合计）
        """
        if not step_id:
            return 0
        removed = 0
        try:
            with self.message_queue_lock:
                keep = [d for d in self.message_queue
                        if not (isinstance(d, dict) and d.get("choreo_step_id") == step_id)]
                removed += len(self.message_queue) - len(keep)
                self.message_queue[:] = keep
            with self.voice_tmp_path_queue_lock:
                keep = [d for d in self.voice_tmp_path_queue
                        if not (isinstance(d, dict) and d.get("choreo_step_id") == step_id)]
                removed += len(self.voice_tmp_path_queue) - len(keep)
                self.voice_tmp_path_queue[:] = keep
            if removed:
                logger.info(f"编排打断撤销未消费音轨 {removed} 条（step_id={step_id}）")
        except Exception:
            logger.error(f"撤销编排音轨异常（忽略）：{traceback.format_exc()}")
        # F-ENG-3（T6-批）：撤销登记——无论扫描是否命中都登记（竞态窗口面正是
        # 「扫描时条目尚未入队，合成完成后才到」的迟到孤儿）；锁顺序与
        # data_priority_insert 拦截点一致（队列锁→登记锁，无反向嵌套）。
        self._register_cancelled_step(step_id)
        return removed

    def _register_cancelled_step(self, step_id: str):
        """登记已撤销编排 step_id（懒清理过期项；异常吞掉——登记失败不阻塞撤旧）"""
        try:
            now = time.monotonic()
            with QueueMixin._cancelled_choreo_lock:
                steps = QueueMixin._cancelled_choreo_steps
                for k in [k for k, ts in steps.items()
                          if now - ts > QueueMixin._CANCELLED_STEP_TTL_S]:
                    del steps[k]
                steps[str(step_id)] = now
        except Exception:
            logger.debug(f"撤销登记异常（忽略）：{traceback.format_exc()}")

    def _is_step_cancelled(self, step_id) -> bool:
        """step_id 是否处于撤销登记 TTL 内（异常保守 False——拦截失败退既有扫描语义）"""
        try:
            now = time.monotonic()
            with QueueMixin._cancelled_choreo_lock:
                ts = QueueMixin._cancelled_choreo_steps.get(str(step_id))
                return ts is not None and (now - ts) <= QueueMixin._CANCELLED_STEP_TTL_S
        except Exception:
            return False

    def is_queue_less_or_greater_than(self, type: str="message_queue", less: int=None, greater: int=None):
        """判断 等待合成消息队列|待播放音频队列 数是否小于或大于某个值，就返回True"""
        if less:
            if type == "voice_tmp_path_queue":
                if len(self.voice_tmp_path_queue) < less:
                    return True
                return False
            elif type == "message_queue":
                if len(self.message_queue) < less:
                    return True
                return False
        
        if greater:
            if type == "voice_tmp_path_queue":
                if len(self.voice_tmp_path_queue) > greater:
                    return True
                return False
            elif type == "message_queue":
                if len(self.message_queue) > greater:
                    return True
                return False
        
        return False
    
    def get_audio_info(self):
        """获取音频队列信息

        TODOS 2026-10-28 消费逻辑审查 #4：builtin 播放器的 play() 是提前移交
        （满载背压上限 queue_max 条进 audio_json_list），已移交条目脱离
        voice_tmp_path_queue——wait_play_audio_num 叠加其内部队列深度，防观测
        面低估积压（仅统计语义，不影响 is_queue_less_or_greater_than 等门控）。
        """
        info = {
            "wait_play_audio_num": len(self.voice_tmp_path_queue),
            "wait_synthesis_msg_num": len(self.message_queue),
        }
        player = getattr(self, "audio_player", None)
        if player is not None and hasattr(player, "internal_queue_len"):
            try:
                info["wait_play_audio_num"] += player.internal_queue_len()
            except Exception:
                pass  # 观测叠加失败不阻塞统计（快照尽力而为）
        # T1 观测面补齐包：丢弃分桶/消费延迟/合成吞吐摘要（观测面数据源单点；
        # D-1 常驻状态条 UI 消费留后续批次）
        try:
            from utils.obs_stats import snapshot as _obs_snapshot
            info["obs"] = _obs_snapshot()
        except Exception:
            pass
        return info

    # ------------------------------------------------------------------
    # G2（2026-10-28 消费回传批次）：音频条目消费完成回执（Sort 侧显式 API——
    # 图 v2.6 Frame→lineout→|回传|Sort 边的 Sort 侧落点）。类属性沿
    # message_queue 模式（Mixin 无 __init__，宿主继承共享）。
    # ------------------------------------------------------------------
    _consumed_receipts = []       # 环形窗（最近 _CONSUMED_RECEIPT_MAX 条回执）
    _consumed_total = 0           # 累计消费回执数
    _consumed_by_type = {}        # 累计分类型计数
    _consumed_lock = threading.Lock()

    # F-ENG-3（T6-批 2026-11-06 协作机制流程审查）：编排撤销登记表——
    # CancelAudio 撤旧扫描（打断时刻）与 inflight 音轨合成完成入队（异步）
    # 之间存在竞态窗口：扫描后入队的迟到条目成孤儿音轨照常播出（画面/音频
    # 错配）。登记语义根治：cancel 时登记 step_id，此后 TTL 内该 step_id 的
    # 入队（两队列分支）一律按已撤销拦截。类属性沿 _consumed_* 模式。
    _cancelled_choreo_steps = {}  # step_id -> monotonic 撤销时刻
    _cancelled_choreo_lock = threading.Lock()
    _CANCELLED_STEP_TTL_S = 300.0  # 覆盖最长音轨生命周期（编排答谢为短音轨，300s 冗余）

    # 弹幕↔回复配对门状态（2026-11-16 中压实测批次：念白单飞）。生产端
    # comment_handler 给念白（read_comment 及助播同源条目）与 LLM 回复条目
    # 打同一 reply_link；消费出口（playback_manager 念白门）保证「念白 A →
    # 回复 A 消费后」才放行念白 B——根治弹幕快速进入时念白持续消费、回复
    # 堆积饿死（权重一致无法根治：回复经 LLM+TTS 延迟入队，生产时序天然
    # 乱序）。类属性沿 _cancelled_choreo 模式（Mixin 无 __init__）。
    _reply_gate_pending = {}    # reply_link -> monotonic（念白已消费、回复未出队）
    _reply_gate_abandoned = {}  # reply_link -> monotonic（无回复路径显式放弃/超时兜底）
    # S1 遗弃保护（2026-11-21 队列优化批次）：reply_link -> monotonic（回复组
    # 消费中——念白出队即登记、回复分段出队续期，TTL 兜底清理）
    _reply_gate_active = {}
    _reply_gate_lock = threading.Lock()
    _REPLY_GATE_TTL_S = 120.0   # abandoned/pending 惰性清理上限
    _REPLY_GATE_ACTIVE_TTL_S = 300.0  # active 惰性清理上限（覆盖最长组消费周期）

    def abandon_reply_link(self, link):
        """生产端放弃配对：本条弹幕走无 LLM 回复路径（积分/按键映射/助播KB/
        本地问答/点歌/LLM 空回复等 return 分支）——对应念白不参与配对等待，
        消费门直接放行。异常吞掉（登记失败退超时兜底语义）。"""
        if not link:
            return
        try:
            now = time.monotonic()
            with QueueMixin._reply_gate_lock:
                gates = QueueMixin._reply_gate_abandoned
                for k in [k for k, ts in gates.items()
                          if now - ts > QueueMixin._REPLY_GATE_TTL_S]:
                    del gates[k]
                gates[str(link)] = now
                # 念白若已被消费（pending 在途），放弃=立即解除等待
                QueueMixin._reply_gate_pending.pop(str(link), None)
                # S1：无回复路径同时解除组活跃保护（组已不存在遗弃面）
                QueueMixin._reply_gate_active.pop(str(link), None)
        except Exception:
            logger.debug(f"放弃回复配对登记异常（忽略）：{traceback.format_exc()}")

    def reply_gate_track_popped(self, data_json):
        """消费出口出队回执（锁外调用，播放器移交前）：念白出队→登记 pending；
        回复出队→解除 pending（回复开始消费，下一条念白放行）。异常吞掉。"""
        try:
            if not isinstance(data_json, dict):
                return
            link = data_json.get("reply_link")
            if not link:
                return
            link = str(link)
            is_rc = (data_json.get("type") == "read_comment"
                     or data_json.get("producer_type") == "read_comment")
            now = time.monotonic()
            with QueueMixin._reply_gate_lock:
                if link in QueueMixin._reply_gate_abandoned:
                    return  # 已放弃路径：不登记不解除
                if is_rc:
                    QueueMixin._reply_gate_pending[link] = now
                else:
                    QueueMixin._reply_gate_pending.pop(link, None)
                    # S1（2026-11-21 队列优化批次）：回复分段出队=组进入连续消费
                    # ——登记/续期组活跃（TTL 兜底），组剩余段在两队列满载时
                    # 不丢不驱逐（完整处理并消费闭环）
                    QueueMixin._reply_gate_active[link] = now
        except Exception:
            logger.debug(f"念白配对出队回执异常（忽略）：{traceback.format_exc()}")

    def mark_reply_group_active(self, link):
        """S1 遗弃保护登记（生产侧最早信号）：念白条目自待合成消息队列出队
        （合成开始）即登记组活跃——LLM 回复分段稍后异步入队时即刻受满载豁免
        保护（早于播放出口 pending 登记的窗口）。异常吞掉。"""
        if not link:
            return
        try:
            now = time.monotonic()
            with QueueMixin._reply_gate_lock:
                gates = QueueMixin._reply_gate_active
                for k in [k for k, ts in gates.items()
                          if now - ts > QueueMixin._REPLY_GATE_ACTIVE_TTL_S]:
                    del gates[k]
                gates[str(link)] = now
        except Exception:
            logger.debug(f"回复组活跃登记异常（忽略）：{traceback.format_exc()}")

    def _reply_group_protected(self, data_json) -> bool:
        """弹幕问答编组遗弃保护判定（S1，2026-11-21 队列优化批次）：reply_link
        命中且组已进入消费流程（念白已出队 pending / 组活跃 TTL 内）→ 组剩余段
        在两队列满载时不丢不驱逐——兑现「处理到之后必须完整处理并消费」。
        未进入消费流程的组维持可插队/可同权重顶替语义（设计预期前半段）。"""
        if not isinstance(data_json, dict):
            return False
        link = data_json.get("reply_link")
        if not link:
            return False
        link = str(link)
        now = time.monotonic()
        try:
            with QueueMixin._reply_gate_lock:
                if link in QueueMixin._reply_gate_abandoned:
                    return False
                if link in QueueMixin._reply_gate_pending:
                    return True
                ts = QueueMixin._reply_gate_active.get(link)
                return ts is not None and (now - ts) <= QueueMixin._REPLY_GATE_ACTIVE_TTL_S
        except Exception:
            return False

    # ---------- P1 点歌期弹幕回复「顶替-保留最新」（2026-11-21 用户实测批次） ----------

    @staticmethod
    def _is_reply_entry(e) -> bool:
        """弹幕问答回复组条目判定：带 reply_link 且非念白（念白 read_comment
        本身属配对源不属回复组，唱歌期间念白照常间奏消费）。"""
        return (isinstance(e, dict) and e.get("reply_link")
                and not (e.get("type") == "read_comment"
                         or e.get("producer_type") == "read_comment"))

    def _song_state_active(self) -> bool:
        """点歌窗口状态判定（无锁启发式——调用方可能已持队锁，本方法不再取
        锁防自锁）：song 锚在位（唱歌/垃圾时间）或待播放队列存在 song 切片段
        （点歌触发→前奏等待窗口，锚尚未建立）。"""
        try:
            anchor = getattr(self, "_song_anchor", None)
            if anchor and anchor.get("t0") is not None:
                return True
            return any(_is_song_slice_entry(e) for e in self.voice_tmp_path_queue)
        except Exception:
            return False

    def _discard_stale_replies_for_song(self, data_json):
        """点歌窗口期弹幕回复「顶替-保留最新」（P1，2026-11-21 用户实测批次，
        待播放队列层；内部自取 voice_tmp_path_queue_lock——锁外调用）。

        用户设计预期：点歌触发后，前奏/间奏人声静音期只插播最新的高权重音频，
        人声结束后也从最新的开始播报；唱歌期间产生的旧回复直接舍弃——不将
        这期间所有 LLM 回复依次全量播报。实现：新的回复组段入队成功后，扫描
        待播放队列舍弃其它更旧的回复组段（S1 受保护组豁免——消费中组原子性
        优先）；被舍弃组经 abandon_reply_link 解除念白单飞门/活跃保护，防止
        配对门空等 15s 超时阻塞后续念白。"""
        if not self._is_reply_entry(data_json):
            return
        if not self._song_state_active():
            return
        new_link = str(data_json.get("reply_link"))
        dropped_links = []
        with self.voice_tmp_path_queue_lock:
            kept = []
            for e in self.voice_tmp_path_queue:
                if (self._is_reply_entry(e)
                        and str(e.get("reply_link")) != new_link
                        and not self._reply_group_protected(e)):
                    dropped_links.append(str(e.get("reply_link")))
                    continue
                kept.append(e)
            if not dropped_links:
                return
            self.voice_tmp_path_queue[:] = kept
        for lk in dict.fromkeys(dropped_links):
            self.abandon_reply_link(lk)
        try:
            from utils.obs_stats import record_drop
            record_drop("song_reply_replace", len(dropped_links))
        except Exception:
            pass
        logger.info(
            f"[song] 唱歌期回复顶替：舍弃旧回复组段 {len(dropped_links)} 条"
            f"（links={sorted(set(dropped_links))}），保留最新"
            f"【{str(data_json.get('content', ''))[:30]}】")

    # ---------- 合成背压门（2026-11-16 消费回传背压批次，用户高压实测） ----------
    # 问题：合成线程无背压持续产音频，待播放队列不断增长（高压下打满 max_len
    # 触发满载驱逐——驱逐发生在 TTS 之后=成本白付）。门控：待播放队列深度
    # ≥门限时合成线程暂停 pop，消费出队回执（notify_synth_gate）后放行——
    # 供需速率匹配，溢出从"合成后驱逐"前移到待合成队列（drop-only，合成前）。
    _synth_gate_cond = threading.Condition()  # 消费出队回执唤醒（超时轮询兜底）
    _SYNTH_GATE_BYPASS_PRIORITY = 500  # 高优豁免线（复读/编排 override=500）
    _SYNTH_GATE_POLL_S = 1.0  # 门控等待上限=轮询周期（覆盖漏通知路径）

    def _synth_gate_depth_limit(self):
        """门限读侧（play_audio.synth_gate_depth，缺省 2；≤0=门禁用）。与首播
        阈值互锁防御：min_start_play>门限时取大——保证阈值可达（否则高压下
        待合成队列永不清空→E2 flush 不触发→永不开播）。"""
        try:
            limit = int(self.config.get("play_audio", "synth_gate_depth"))
        except (TypeError, ValueError):
            limit = 2
        if limit <= 0:
            return 0
        try:
            min_start = int(self.config.get("filter", "voice_tmp_path_queue_min_start_play") or 0)
        except (TypeError, ValueError):
            min_start = 0
        return max(limit, min_start) if min_start > 0 else limit

    def synth_gate_exempt(self, message) -> bool:
        """豁免判定（与既有机制联动，缺一不可）：
        - song 切片段：时间轴领先依赖预合成，门控会放大失步（e13 方向）；
        - 权重 ≥500（复读 reread_top_priority=500/编排 override=500）：保真
          "最高顺位"插队语义，不被门滞留 2 条播放时长；
        - reply_link 命中在途 pending（念白配对门）：配对回复被门滞留时念白
          单飞门 15s 超时兜底会被迫接管——配对机制退化为超时驱动，必须豁免；
        - 非 dict 条目防御放行。"""
        if not isinstance(message, dict):
            return True
        if message.get("type") == "song" and message.get("song_segment"):
            return True
        try:
            from utils.audio.audio_priority import entry_priority
            p = entry_priority(message, self.config.get("filter", "priority_mapping") or {})
        except Exception:
            p = None
        if p is not None and int(p) >= QueueMixin._SYNTH_GATE_BYPASS_PRIORITY:
            return True
        link = message.get("reply_link")
        return bool(link and str(link) in QueueMixin._reply_gate_pending)

    def _synth_gate_budget_s(self):
        """S3 门限读侧（play_audio.synth_gate_budget_s，秒数预算口径；缺省/≤0=
        回退条数门限模式）。预算>0 时优先于条数门限：待播放普通条目预估播放
        秒数总和 ≥ 预算才门控——对不同长度消息更精准（2026-11-21 队列优化批次）。"""
        try:
            v = float(self.config.get("play_audio", "synth_gate_budget_s"))
        except (TypeError, ValueError):
            return 0.0
        return v if v > 0 else 0.0

    def _gate_normal_entries(self):
        """门控口径条目快照（2026-11-21 队列优化批次 E2）：待播放队列普通条目
        + builtin 播放器内部队列普通条目——本地路径 play() 提前移交，播放器
        内部队列才是真实积压点（旧口径只数待播放队列=门控盲区）。song 切片段
        两处均排除：背景流不占门控深度，间奏插播内容依赖预合成不被门滞留。"""
        entries = [e for e in self.voice_tmp_path_queue
                   if not (isinstance(e, dict) and e.get("type") == "song"
                           and e.get("song_segment"))]
        player = getattr(self, "audio_player", None)
        getter = (player.internal_normal_entries
                  if player is not None and hasattr(player, "internal_normal_entries")
                  else None)
        if callable(getter):
            try:
                entries.extend(getter() or [])
            except Exception:
                pass  # 快照失败退待播放队列口径
        return entries

    def _entry_play_seconds(self, entry) -> float:
        """单条目预估播放秒数（S3）：voice_path 实际时长优先（soundfile 读头，
        与 web_captions keep_time 同源思路）；读失败降级文本长度估算（中文
        TTS ≈4 字/秒，下限 1s）。异常兜底 1.0。"""
        if not isinstance(entry, dict):
            return 1.0
        path = entry.get("voice_path")
        if path:
            try:
                import os as _os
                if _os.path.isfile(path):
                    import soundfile as _sf
                    d = float(_sf.info(path).duration)
                    if d > 0:
                        return d
            except Exception:
                pass  # 时长读取失败降级文本估算
        try:
            return max(1.0, len(str(entry.get("content", "") or "")) / 4.0)
        except Exception:
            return 1.0

    def synth_gate_wait_s(self, message) -> float:
        """合成背压门判定（须在 message_queue_lock 之外调用）。条目口径=待播放
        队列普通条目+播放器内部队列普通条目（E2 本地路径盲区修复；song 段=
        背景流不占容量，与满载分账同口径）。S3：synth_gate_budget_s>0 时改按
        预估播放秒数预算判定（积压 ≥ 预算秒数才门控），0=条数门限模式。
        0=放行；>0=门控等待秒数（调用方等消费出队回执唤醒，超时轮询重查——
        队首可能已变为豁免条目，每轮重 peek 重判）。"""
        if self.synth_gate_exempt(message):
            return 0.0
        entries = self._gate_normal_entries()
        budget = self._synth_gate_budget_s()
        if budget > 0:
            backlog_s = sum(self._entry_play_seconds(e) for e in entries)
            return 0.0 if backlog_s < budget else QueueMixin._SYNTH_GATE_POLL_S
        limit = self._synth_gate_depth_limit()
        if limit <= 0:
            return 0.0
        return 0.0 if len(entries) < limit else QueueMixin._SYNTH_GATE_POLL_S

    def synth_gate_sleep(self, wait_s):
        """门控等待：消费出队回执（notify_synth_gate）提前唤醒；超时轮询兜底
        （覆盖 cancel_choreo_audio 等非常规出队路径的漏通知）。"""
        with QueueMixin._synth_gate_cond:
            QueueMixin._synth_gate_cond.wait(timeout=wait_s)

    def notify_synth_gate(self):
        """消费出队回执（消费循环每次成功出队后调用）：唤醒门控中的合成线程。
        异常吞掉——回执不得影响消费链。"""
        try:
            with QueueMixin._synth_gate_cond:
                QueueMixin._synth_gate_cond.notify_all()
        except Exception:
            pass

    def report_consumed(self, qtype: str = "", content: str = ""):
        """音频条目真实消费完成回执。

        接入点：playback_manager EDTalk push_full 返回处 + audio_core builtin
        完成回调转发处（两路消费互斥不双报，G1）。维护环形回执窗+累计计数，
        供排序层/观测面查询（get_consumption_stats）；当前无排序决策依赖，
        仅观测透出（TODOS 2026-10-27 批 #1）。异常吞掉——回执不得影响消费链。"""
        try:
            receipt = {"ts": round(time.time(), 3), "type": str(qtype or ""),
                       "content": str(content or "")[:80]}
            with QueueMixin._consumed_lock:
                QueueMixin._consumed_total += 1
                t = receipt["type"]
                QueueMixin._consumed_by_type[t] = QueueMixin._consumed_by_type.get(t, 0) + 1
                QueueMixin._consumed_receipts.append(receipt)
                if len(QueueMixin._consumed_receipts) > _CONSUMED_RECEIPT_MAX:
                    QueueMixin._consumed_receipts.pop(0)
        except Exception:
            logger.debug(f"消费回执记录异常（忽略）：{traceback.format_exc()}")

    def get_consumption_stats(self):
        """消费回执统计快照（累计计数+分类型计数+最近 10 条回执）。"""
        with QueueMixin._consumed_lock:
            return {
                "total_consumed": QueueMixin._consumed_total,
                "by_type": dict(QueueMixin._consumed_by_type),
                "recent": list(QueueMixin._consumed_receipts[-10:]),
            }


    def flush_starved_playback_queue(self):
        """E2 饥饿路径 flush：首次播放阈值门的保底放行

        规则（D-20 矩阵"音频播放队列首次触发阈值"行的 flush 规则）：
        待合成消息队列已空（本次突发全部合成完毕；待播放队列的唯一生产者
        就是合成线程自身，故此时亦无在途合成）且待播放队列仍有音频、
        首次播放触发标志 voice_tmp_path_queue_not_empty_flag 尚未置位
        （本次突发总量未达阈值 filter.voice_tmp_path_queue_min_start_play=N）
        时，立即置位标志并唤醒消费者放行积压音频。

        否则首次突发仅产出 N-1 条时，生产者永不 notify（生产者只在队列
        达到 N 或标志已置位时通知），消费者无限期 wait——积压的 N-1 条
        音频将永远不播，直到下一次突发把队列重新顶过阈值。

        只应在合成线程消费完一条消息（含合成失败）后调用；调用点在
        SynthesisMixin.message_queue_thread 循环尾。

        Returns:
            bool: 是否执行了放行（置位标志+notify）
        """
        # 快路径无锁初筛：待合成队列非空（突发未结束）/待播放队列空/
        # 标志已置位（阈值门已开，生产者自行 notify）均无需放行
        if self.message_queue or not self.voice_tmp_path_queue or self.voice_tmp_path_queue_not_empty_flag:
            return False
        with self.voice_tmp_path_queue_lock:
            # 锁内复核：标志与队列的写入方（data_priority_insert/本方法）
            # 均持同一把锁，避免与生产者置位竞争
            if self.voice_tmp_path_queue_not_empty_flag or not self.voice_tmp_path_queue:
                return False
            self.voice_tmp_path_queue_not_empty_flag = True
            # 生产者通过notify()通知消费者列表中有新的消息
            self.voice_tmp_path_queue_not_empty.notify()
        logger.info(f"E2 flush：待合成队列已空，放行未达首次播放阈值的积压音频 {len(self.voice_tmp_path_queue)} 条")
        return True

    def is_audio_queue_empty(self):
        """判断等待合成和已经合成的队列是否为空

        A3（2026-10-22 审查批次）：mixer_copywriting 位退役（文案模块已弃用）。
        2026-10-30 审查批次：「音频播放器」配置退役——mixer_normal 位4随之移除
        （pygame.mixer 播放态探测不再存在，本地播放统一 builtin 播放器），位编码
        收缩为 4 态：0 都不空 | 1 message_queue 空 | 2 voice_tmp_path_queue 空 |
        3 双队列空（全空闲）；"全空"判定值 7→3（audio_processor 语音对话链调用点
        已同步）。

        Returns:
            int: 0 都不为空 | 1 message_queue 为空 | 2 voice_tmp_path_queue 为空 |
                 3 双队列空（全空闲）
        """

        flag = 0

        # 判断队列是否为空
        if len(self.message_queue) == 0:
            flag += 1

        if len(self.voice_tmp_path_queue) == 0:
            flag += 2

        return flag

    def purge_talk_inputs(self):
        """清除待合成队列中 talk 类型条目（连续对话最新优先，D-22 输入侧）。

        连续对话开启时新输入到来前调用——舍弃未回复/未合成的旧对话消息，
        处理最新的信息；只清 talk，不影响点歌/助播等其它类型。

        Returns:
            int: 清除条数
        """
        try:
            with self.message_queue_lock:
                keep = [m for m in self.message_queue
                        if not (isinstance(m, dict) and m.get("type") == "talk")]
                removed = len(self.message_queue) - len(keep)
                if removed:
                    self.message_queue[:] = keep
            return removed
        except Exception as e:
            logger.warning(f"purge_talk_inputs 异常（忽略）：{e}")
            return 0

    def _continuous_talk_discard_notify(self, count: int):
        """D-10：待播音频被舍弃的聊天区灰字反馈（webui /callback system 源，
        经后台队列投递——沿 utils_handler 聊天记录回调同款通道）。"""
        try:
            from ..common import Common
            from ..background_tasks import submit
            webui_ip = self.config.get("webui", "ip")
            webui_ip = "127.0.0.1" if webui_ip == "0.0.0.0" else webui_ip
            payload = {"type": "llm", "data": {
                "type": "系统", "chat_source": "system", "username": "系统",
                "content_type": "answer",
                "content": f"已舍弃 {count} 条待播音频（连续对话，仅播放最新一条）",
                "timestamp": Common().get_bj_time(0)}}
            submit(Common().send_request,
                   f'http://{webui_ip}:{self.config.get("webui", "port")}/callback',
                   "POST", payload, timeout=10)
        except Exception as e:
            logger.debug(f"舍弃反馈发送失败（忽略）：{e}")

    def _maybe_discard_pending_for_talk(self, data_json):
        """D-22（连续对话最新优先，2026-10-30 聊天审查批次）：talk 整段音频
        入待播放队列时若待播放 ≥2（=2 与 >2 同规则，消除边界缺口），舍弃
        等待播放音频只播最新一条，判断条件为待播放列表数量。

        豁免：切分段（split_group）与流式回复分句（talk_stream）——同一次
        回复的分段/分句必须完整排队，不受新输入淘汰影响。
        """
        try:
            if not (isinstance(data_json, dict) and data_json.get("type") == "talk"):
                return
            if data_json.get("split_group") or data_json.get("talk_stream"):
                return
            if not bool(self.config.get("talk", "continuous_talk")):
                return
            with self.voice_tmp_path_queue_lock:
                pending = len(self.voice_tmp_path_queue)
                if pending >= 2:
                    self.voice_tmp_path_queue.clear()
            if pending >= 2:
                try:
                    from utils.obs_stats import record_drop
                    record_drop("talk_discard")
                except Exception:
                    pass
                logger.info(f"[连续对话] 待播放 {pending} 条 ≥2，舍弃等待播放音频只播最新一条")
                self._continuous_talk_discard_notify(pending)
        except Exception as e:
            logger.warning(f"连续对话待播舍弃判断异常（忽略）：{e}")

    def _maybe_priority_yield_for(self, data_json):
        """新音频入队成功后的让位裁决（2026-11-16 段间让位批次：barge-in 打断
        即弃退役，原 _maybe_barge_in_for 改名重写）。

        插播语义（2026-11-16 用户确认）收敛为：动态文案切分段（split_group，
        权重低）在播时，更高权重音频入队 → 本方法仅处理 builtin 本地声道让位
        （d12）；EDTalk 路径无需打断——正在播的文案段播完后，消费循环按权重
        重新 pop，高权重音频先消费，剩余文案段自然续播（切回）。

        d12 通用高优让位（2026-11-05 批次，保留）：builtin 在播条目 + 新音频
        有效权重严格高于在播条目 → request_yield（song 让位给答谢——builtin
        整曲暂停=伴奏人声同停近似声明，e3；同权重/更低=排队不打断）。
        **同声道前提**（2026-11-13 助播并行根修）：插播者路由 EDTalk（推
        数字人出声）时不让位——其不占本地声道，让位只会使在播条目陷入
        「暂停→插播者推数字人→队列空续播回退 1s」循环：语句中段切断
        （撕裂）+静默间隙（卡顿）+尾段逐次重放（重复），且违背助播并行
        消费语义（2026-10-27 批次）；d12 原始场景=EDTalk 未激活同声道，
        保持生效。
        **组原子让位门**（2026-11-17 组原子消费批次）：在播条目属于非动态
        文案切分段组 → 不让位（组原子语义：组播完前其他信息不得插入组中间，
        新条目仅入队等待组完）；动态文案组维持本方法既有让位现状。
        已挂起时播放器侧自行忽略（R2-A7c 排队不打断）。仅 builtin 具备暂停-
        续播机制。
        """
        try:
            player = getattr(self, "audio_player", None)
            if player is None or not hasattr(player, "request_yield"):
                return
            playing = (player.get_playing_weight()
                       if hasattr(player, "get_playing_weight") else None)
            new_p = None
            try:
                from utils.audio.audio_priority import entry_priority
                new_p = entry_priority(
                    data_json, self.config.get("filter", "priority_mapping") or {})
            except Exception:
                new_p = None
            if new_p is not None and playing is not None and new_p > playing:
                # 助播豁免（2026-11-20 助播插队审查批次，用户裁决）：助播条目
                # （assistant_anchor_*，权重 40）不触发让位——助播是并行锚点
                # 声道（playback_manager ASSISTANT_PLAYBACK_TYPES：无口型语义、
                # 永远本地外放），设计预期「除动态文案切分段外无插队机制」；
                # 助播分句连续入队经 d12 反复打断 talk(30)/integral(30)/
                # entrance(30) 等本地在播条目=实测「助播不停插队+在播音频
                # 播一小段即挂起」根因。助播高权重仅保留排序语义（在播条目
                # 播完后助播先消费），不打断。
                if isinstance(data_json, dict):
                    from utils.audio.playback_manager import ASSISTANT_PLAYBACK_TYPES
                    if data_json.get("type") in ASSISTANT_PLAYBACK_TYPES:
                        return
                # 同声道前提（2026-11-13 助播并行根修）：插播者路由 EDTalk 时
                # 不让位（routes_to_edtalk 单源判定，防条件漂移）
                from utils.audio.playback_manager import routes_to_edtalk
                if routes_to_edtalk(data_json, self.config):
                    return
                # 组原子让位门（2026-11-17 组原子消费批次）：在播条目属于非
                # 动态文案切分段组 → 不让位——组播完前其他信息不得插入组中间
                # （新条目仅入队等待组完；消费循环组原子门保证组连续消费）。
                # 动态文案组/非切分条目维持 d12 让位现状。fail-open：在播条目
                # 不可得（无 get_playing_entry 能力）退既有让位语义。
                playing_entry = (player.get_playing_entry()
                                 if hasattr(player, "get_playing_entry") else None)
                _psg = (playing_entry.get("split_group")
                        if isinstance(playing_entry, dict) else None)
                if (isinstance(_psg, dict) and _psg.get("id")
                        and playing_entry.get("type") != "trends_copywriting"):
                    return
                # ② d12：严格更高权重新音频 → 在播条目让位
                player.request_yield()
                try:
                    from utils.obs_stats import record_drop
                    record_drop("builtin_yield_priority")
                except Exception:
                    pass
        except Exception:
            pass  # 打断/让位触发失败不阻塞入队主流程（在播条目播完自然轮到插队音频）

    def message_queue_normal_len(self) -> int:
        """消息队列分账口径长度（E3 告警口径对齐单源，2026-11-21 队列优化批次）：
        不含背景流切片（song 切片段/动态文案分段）——与满载判定同源，长回复
        切分预检告警防虚报。"""
        return normal_queue_len(self.message_queue)

    def _mq_replace_same_priority(self) -> bool:
        """F1 开关读侧（filter.message_queue_replace_same_priority，缺省 True=
        开启顶替）：待合成消息队列队满同权重堆积时，新条目顶替最旧同权重
        条目（对齐待播放队列规则2与设计预期）；false 回退既有 drop-only 语义。
        字符串值按字面解析（config_auto_save 对布尔不做类型转换，本仓库配置
        存在字符串落盘惯例——"false"/"0" 不得被 bool() 误判为 True）。"""
        try:
            v = self.config.get("filter", "message_queue_replace_same_priority")
            if v is None:
                return True
            if isinstance(v, str):
                return v.strip().lower() not in ("false", "0", "off", "no", "")
            return bool(v)
        except Exception:
            return True

    def data_priority_insert(self, type:str="等待合成消息", data_json:dict=None):
        # T1 观测面补齐包：入队时间戳（消费延迟=播放完成-_enqueued_at；_ 前缀=内部观测键，
        # 消费链无读取点、随条目透传不影响既有键面）
        try:
            if isinstance(data_json, dict) and "_enqueued_at" not in data_json:
                import time as _t
                data_json["_enqueued_at"] = _t.time()
        except Exception:
            pass  # 观测埋点不阻塞入队
        """
        数据根据优先级排队插入待合成音频队列

        type目前有
            reread_top_priority 最高优先级-复读
            talk 聊天（语音输入）
            comment 弹幕
            local_qa_audio 本地问答音频
            song 歌曲
            reread 复读
            key_mapping 按键映射
            integral 积分
            read_comment 念弹幕
            gift 礼物
            entrance 用户入场
            follow 用户关注
            schedule 定时任务
            idle_time_task 闲时任务
            trends_copywriting 动态文案
            assistant_anchor_text 助播-文本
            assistant_anchor_audio 助播-音频
        """
        # 定义 type 到优先级的映射，相同优先级的 type 映射到相同的值，值越大优先级越高
        priority_mapping = self.config.get("filter", "priority_mapping")

        def _priority_of(item):
            """条目有效权重数值化（O7 单源；None=未识别类型）"""
            p = _entry_priority(item)
            return int(p) if p is not None else None

        def get_priority_level(data_json):
            """根据 data_json 的 'type' 键返回优先级，未定义的 type 或缺失 'type'
            键将返回 None。2026-11-10 实测修复：映射表值可能为字符串（前端/
            手动编辑 config 写入 "80"）——490 行 int>=str 比较直接 TypeError
            中断入队（点歌整链失败）。经 audio_priority 单一源数值化：数值
            字符串→int；非数值映射→None（队尾语义），与 item_priority 侧
            int() 对称。"""
            audio_type = data_json.get("type")
            from utils.audio.audio_priority import entry_priority
            return entry_priority({"type": audio_type}, priority_mapping)

        def _entry_priority(item):
            """条目有效权重（O7，2026-10-28 消费批次）：条目自带
            priority_override（编排插队 500）优先于类型映射表——
            既有条目权重比较、同权重组定位、最低优驱逐都必须按插队权重判定，
            否则 500 插队条目会被误当映射权重参与排序/驱逐。判定经
            audio_priority 单一源（与 playback/builtin 同源防漂移）。"""
            from utils.audio.audio_priority import entry_priority
            return entry_priority(item, priority_mapping)

        # 查找插入位置
        new_data_priority = get_priority_level(data_json)

        # O7：新条目显式插队权重优先于类型映射表。通道值由注入方决定：
        # 500=编排插队音轨（高于常规语音域 10~30）与最高顺位复读
        # （2026-11-15 批次：999 特殊机制退役，reread_top_priority 改映射 500）。
        _override = data_json.get("priority_override") if isinstance(data_json, dict) else None
        if _override is not None:
            try:
                new_data_priority = int(_override)
            except (TypeError, ValueError):
                pass

        if type == "等待合成消息":
            logger.info(f"{type} 优先级: {new_data_priority} 内容：【{data_json['content']}】")

            # M1/M2（2026-10-28 消费批次）：扫描/满载判定/插入全部锁内完成——
            # 消除"锁外算位、锁内插入"的多生产者竞态超长窗口；满载时高优先级
            # 插入改为"插队+驱逐队尾"（净长度不变，队尾=最低优组 FIFO 尾），
            # 根治满载高优插入无界超长
            with self.message_queue_lock:
                # F-ENG-3（T6-批）：编排撤销登记拦截（消息面同音频面——撤旧扫描后
                # 迟到入队的已撤销 step_id 条目按已撤销语义丢弃）
                _sid_msg = data_json.get("choreo_step_id") if isinstance(data_json, dict) else None
                if _sid_msg and self._is_step_cancelled(_sid_msg):
                    logger.info(f"编排已撤销消息迟到入队拦截（step_id={_sid_msg}）：【{data_json.get('content', '')}】")
                    try:
                        from utils.obs_stats import record_drop
                        record_drop("choreo_cancelled_late")
                    except Exception:
                        pass
                    return {"code": 1, "msg": f"编排已撤销消息，丢弃迟到条目（step_id={_sid_msg}）"}

                # 如果新数据没有 'type' 键或其类型不在 priority_mapping 中，直接插入到末尾
                # R5b（2026-11-11 用户模型）：song 切片段不插队——追加队尾
                # （切片组=「背景流」：答谢/入场等其它类型按优先级正常插到歌
                # 段之前，点歌应答先于歌播、间奏期新答谢正常插到歌段前被
                # 口型推理消费；song 段自身 seq 有序=队尾追加天然保序）
                if new_data_priority is None:
                    insert_position = len(self.message_queue)
                elif (isinstance(data_json, dict) and data_json.get("type") == "song"
                      and data_json.get("song_segment")):
                    insert_position = len(self.message_queue)
                    logger.debug(f"song 切片段追加队尾（不插队）position={insert_position}")
                elif (isinstance(data_json, dict)
                      and data_json.get("type") == "trends_copywriting"
                      and isinstance(data_json.get("split_group"), dict)):
                    # 分账编组（2026-11-21 动态文案审查批次）：动态文案分段
                    # 不插队——队尾追加 seq 保序；其它消息按权重正常插到文案
                    # 段之前（插队消费），段间让位由播放侧组原子门放行
                    insert_position = len(self.message_queue)
                    logger.debug(f"动态文案分段追加队尾（不插队）position={insert_position}")
                else:
                    insert_position = 0  # 默认插入到列表开头
                    # 从列表的最后一个元素开始，向前遍历列表，直到第一个元素
                    for i in range(len(self.message_queue) - 1, -1, -1):
                        priority_level = _entry_priority(self.message_queue[i])
                        if priority_level is not None:
                            item_priority = int(priority_level)
                            # 确保比较时排除未定义类型的元素
                            if item_priority is not None and item_priority >= new_data_priority:
                                # 如果找到一个元素，其优先级小于或等于新数据，则将新数据插入到此元素之后
                                insert_position = i + 1
                                break

                logger.debug(f"insert_position={insert_position}")
                max_len = int(self.config.get("filter", "message_queue_max_len"))

                # R4f（2026-11-11 实测）：song 切片段豁免满载丢弃——切片序列
                # 不可丢（丢 1 段=歌不完整，实测 5 段切片第 5 段被满载丢弃后
                # 「四段后停」）；满载仍插入（队列超限消费后回落）。
                # F1（2026-11-21 队列优化批次）：普通条目队满同权重堆积改顶替
                # 最旧条目（开关 filter.message_queue_replace_same_priority，
                # 对齐设计预期），其余低优场景维持 drop-only 语义。
                # 2026-11-21 动态文案审查批次：动态文案分段同享豁免（编组
                # 序列不可丢——长文案百段不允许因普通消息挤占被丢段）。
                _is_song_slice = (isinstance(data_json, dict)
                                  and data_json.get("type") == "song"
                                  and data_json.get("song_segment"))
                _is_trends_slice = (isinstance(data_json, dict)
                                    and data_json.get("type") == "trends_copywriting"
                                    and isinstance(data_json.get("split_group"), dict))
                # S1 遗弃保护（2026-11-21 队列优化批次）：弹幕问答编组切分段
                # （reply_link 命中且组已进入消费流程）满载不丢不驱逐——
                # 「处理到之后必须完整处理并消费」闭环
                _reply_protected = self._reply_group_protected(data_json)

                _mq_full = normal_queue_len(self.message_queue) >= max_len

                # 数据队列数据量超长判断：队满且插入位次在末尾（低优/同权重
                # 堆积/未识别类型）→ F1 同权重顶替（开关开启且存在同权重组时）
                # 或丢弃
                if (_mq_full and insert_position >= len(self.message_queue)
                        and not _is_song_slice and not _is_trends_slice
                        and not _reply_protected):
                    # F1（2026-11-21 队列优化批次）：同权重顶替——对齐设计预期
                    # 「新入队的消息顶替最旧的同权重消息」与待播放队列规则2：
                    # 顶替同权重组组首（最老）条目、新条目接组尾（净长度不变，
                    # 组内 FIFO 保序）。候选排除背景流切片与受保护回复组段。
                    if (self._mq_replace_same_priority()
                            and new_data_priority is not None):
                        same_idx_list = [i for i, it in enumerate(self.message_queue)
                                         if _priority_of(it) == int(new_data_priority)
                                         and not _is_bg_slice_entry(it)
                                         and not self._reply_group_protected(it)]
                        if same_idx_list:
                            front_idx = same_idx_list[0]
                            last_idx = same_idx_list[-1]
                            _replaced = self.message_queue.pop(front_idx)
                            self.message_queue.insert(last_idx, data_json)
                            self.message_queue_not_empty.notify()
                            try:
                                from utils.obs_stats import record_drop
                                record_drop("message_queue_same_tier_replace")
                            except Exception:
                                pass
                            logger.warning(
                                f"message_queue 已满，同权重顶替组首最老条目（原位置 {front_idx}）："
                                f"被顶替【{_replaced.get('content', '')}】{_split_group_note(_replaced)}，"
                                f"新数据【{data_json['content']}】{_split_group_note(data_json)}")
                            return {"code": 200, "msg": f"同权重顶替组首条目（原位置 {front_idx}）"}
                    # 任务 12 根修：切分段丢弃升级 warning+组上下文（_split_group_note）
                    logger.warning(f"message_queue 已满，数据丢弃：【{data_json['content']}】{_split_group_note(data_json)}")
                    try:
                        from utils.obs_stats import record_drop
                        record_drop("message_queue_full")
                    except Exception:
                        pass
                    return {"code": 1, "msg": f"message_queue 已满，数据丢弃：【{data_json['content']}】"}

                # 队满且插队位次有效（新数据优先级高于队内既有条目）→ 插队并驱逐队尾
                evicted = None
                if _mq_full and insert_position < len(self.message_queue):
                    # 分账编组保护（2026-11-21 动态文案审查批次）+ S1 遗弃保护：
                    # 队尾驱逐跳过背景流切片与受保护回复组段（序列不可丢）
                    _evict_idx = next((i for i in range(len(self.message_queue) - 1, -1, -1)
                                       if not _is_bg_slice_entry(self.message_queue[i])
                                       and not self._reply_group_protected(self.message_queue[i])), None)
                    if _evict_idx is not None:
                        evicted = self.message_queue.pop(_evict_idx)
                        if insert_position > _evict_idx:
                            insert_position -= 1  # 驱逐点在插入位之前 → 位次同步校正（对齐规则3 做法）
                    elif not (_reply_protected or _is_song_slice or _is_trends_slice):
                        # 无驱逐候选（普通条目全为受保护编组段）且新条目不受
                        # 保护 → 丢弃新条目（受保护段不可驱逐）
                        logger.warning(f"message_queue 已满且无可驱逐候选（受保护编组段占满），数据丢弃：【{data_json['content']}】")
                        try:
                            from utils.obs_stats import record_drop
                            record_drop("message_queue_full")
                        except Exception:
                            pass
                        return {"code": 1, "msg": f"message_queue 已满，数据丢弃：【{data_json['content']}】"}
                    # 受保护新条目无候选 → 满载豁免直插（队列暂时超限，消费回落）

                # 在计算出的位置插入新数据
                self.message_queue.insert(insert_position, data_json)
                # 生产者通过notify()通知消费者列表中有新的消息
                self.message_queue_not_empty.notify()

                # P1（2026-11-21 用户实测批次）：点歌窗口期回复组「顶替-保留
                # 最新」待合成层——新回复入队即舍弃更旧回复（合成成本前移节省，
                # 唱歌结束后残留回复不再依次全量播报）；被舍弃组解除念白配对门
                # （abandon_reply_link，gate 锁序与本锁内 _reply_group_protected
                # 既有先例一致）。S1 受保护组豁免（消费中组原子性优先）。
                if self._is_reply_entry(data_json) and self._song_state_active():
                    _new_link = str(data_json.get("reply_link"))
                    _song_dropped_links = []
                    _kept = []
                    for _e in self.message_queue:
                        if (self._is_reply_entry(_e)
                                and str(_e.get("reply_link")) != _new_link
                                and not self._reply_group_protected(_e)):
                            _song_dropped_links.append(str(_e.get("reply_link")))
                            continue
                        _kept.append(_e)
                    if _song_dropped_links:
                        self.message_queue[:] = _kept
                        for _lk in dict.fromkeys(_song_dropped_links):
                            self.abandon_reply_link(_lk)
                        try:
                            from utils.obs_stats import record_drop
                            record_drop("song_reply_replace_mq", len(_song_dropped_links))
                        except Exception:
                            pass
                        logger.info(
                            f"[song] 唱歌期回复顶替（待合成层）：舍弃旧回复 "
                            f"{len(_song_dropped_links)} 条，保留最新"
                            f"【{str(data_json.get('content', ''))[:30]}】")

            if evicted is not None:
                try:
                    from utils.obs_stats import record_drop
                    record_drop("message_queue_evict")
                except Exception:
                    pass
                logger.warning(
                    f"message_queue 已满，高优数据插队并驱逐队尾（最低优）："
                    f"被驱逐【{evicted.get('content', '')}】{_split_group_note(evicted)}，"
                    f"新数据【{data_json['content']}】{_split_group_note(data_json)}")
            return {"code": 200, "msg": f"数据已插入到位置 {insert_position}"}
        else:
            logger.info(f"{type} 优先级: {new_data_priority} 音频={data_json['voice_path']}")

            # A1（2026-10-22 审查批次，义务 E1）：溢出语义对齐设计示例——实现前经四示例+主
            # 叙述逐行复推精化的权威规则（比义务块字面"取代最靠后同权重"更强，后者在示例4
            # 双 150 连发下残留旧条目无法复现设计终态）：
            #   规则1 未满 → 按权重序插入（同权重接组尾，组内 FIFO）
            #   规则2 满 且同权重组存在 → 取代该组组首（最老）条目，新条目接组尾（净长度不变）
            #   规则3 满 且无同权重 且新权重 > 最低优组 → 插入后驱逐最低优组组首（最老）条目
            #   规则4 其余（满且新权重更低）→ 舍弃新音频
            # 注意：等待合成消息分支原为 drop-only（2026-11-21 队列优化批次
            # F1 起在同权重堆积场景对齐本规则2，见 filter.
            # message_queue_replace_same_priority 开关）。
            #
            # T1-批（2026-11-06 协作机制流程审查，W1 根修）：音频队列 M1 同款锁内化——
            # 满载判定/规则 1-4 扫描与插入/同权重组与最低优组定位全部在
            # voice_tmp_path_queue_lock 内完成，消除「锁外算位、锁内插入」多生产者竞态
            # （规则 2/3 组首驱逐索引漂移 / 满载判定与插入间超限）。
            # 对齐 message_queue 分支 M1/M2（2026-10-28）。锁内零外部 IO：
            # _maybe_discard_pending_for_talk 内部自取本锁（锁外调用——触发集合不变，
            # 仅成功插入的 talk 条目触发，实际舍弃在消费侧 purge_talk_inputs）；
            # _maybe_priority_yield_for 涉及播放器 yield 事件（锁外调用，
            # 锁内禁外部 IO）。
            queue = self.voice_tmp_path_queue
            max_len = int(self.config.get("filter", "voice_tmp_path_queue_max_len"))

            def _insert_and_notify(pos, item):
                """锁内插入并执行首次播放阈值/唤醒逻辑（原语义保真；调用方须持锁）"""
                self.voice_tmp_path_queue.insert(pos, item)
                # 待播放音频数量大于首次播放阈值 且 处于首次播放情况下：
                if len(self.voice_tmp_path_queue) >= int(self.config.get("filter", "voice_tmp_path_queue_min_start_play")) and self.voice_tmp_path_queue_not_empty_flag is False:
                    self.voice_tmp_path_queue_not_empty_flag = True
                    # 生产者通过notify()通知消费者列表中有新的消息
                    self.voice_tmp_path_queue_not_empty.notify()
                # 非首次触发情况下，有数据就触发消费者播放
                elif self.voice_tmp_path_queue_not_empty_flag:
                    # 生产者通过notify()通知消费者列表中有新的消息
                    self.voice_tmp_path_queue_not_empty.notify()

            inserted = False
            insert_msg = None
            with self.voice_tmp_path_queue_lock:
                # F-ENG-3（T6-批）：编排撤销登记拦截——合成完成晚于撤旧扫描的
                # 迟到音轨（竞态窗口孤儿）按已撤销语义丢弃（先于全部插入规则）
                _sid = data_json.get("choreo_step_id") if isinstance(data_json, dict) else None
                if _sid and self._is_step_cancelled(_sid):
                    logger.info(f"编排已撤销音轨迟到入队拦截（step_id={_sid}）：【{data_json.get('voice_path')}】")
                    try:
                        from utils.obs_stats import record_drop
                        record_drop("choreo_cancelled_late")
                    except Exception:
                        pass
                    return {"code": 1, "msg": f"编排已撤销音轨，丢弃迟到条目（step_id={_sid}）"}

                # 未识别类型：维持既有语义——插队尾，满则丢弃
                if new_data_priority is None:
                    if normal_queue_len(queue) >= max_len:
                        logger.warning(f"voice_tmp_path_queue 已满，音频丢弃（未识别类型）：【{data_json['voice_path']}】{_split_group_note(data_json)}")
                        try:
                            from utils.obs_stats import record_drop
                            record_drop("unknown_type_dropped")
                        except Exception:
                            pass
                        return {"code": 1, "msg": f"voice_tmp_path_queue 已满，音频丢弃：【{data_json['voice_path']}】"}
                    _insert_and_notify(len(queue), data_json)
                    inserted = True
                    insert_msg = {"code": 200, "msg": f"音频已插入到位置 {len(queue) - 1}"}
                elif normal_queue_len(queue) < max_len:
                    # R5b：song 切片段不插队——追加队尾（背景流语义，其它类型
                    # 按优先级正常插到歌段前）；容量分账（R4h）：普通音频容量
                    # 不被 song 段挤压
                    if (isinstance(data_json, dict) and data_json.get("type") == "song"
                            and data_json.get("song_segment")):
                        insert_position = len(queue)
                        _insert_and_notify(insert_position, data_json)
                        logger.debug(f"song 切片段追加队尾（不插队）position={insert_position}")
                        inserted = True
                        insert_msg = {"code": 200, "msg": "song 切片段追加队尾（不插队）"}
                    elif (isinstance(data_json, dict)
                          and data_json.get("type") == "trends_copywriting"
                          and isinstance(data_json.get("split_group"), dict)):
                        # 分账编组（2026-11-21 动态文案审查批次）：动态文案分段
                        # 队尾追加 seq 保序，其它消息按权重插到文案段之前
                        insert_position = len(queue)
                        _insert_and_notify(insert_position, data_json)
                        logger.debug(f"动态文案分段追加队尾（不插队）position={insert_position}")
                        inserted = True
                        insert_msg = {"code": 200, "msg": "动态文案分段追加队尾（不插队）"}
                    else:
                        # 规则1：未满 → 权重序插入（从尾向头找第一个 ≥ 新权重的条目，插其后=接组尾）
                        insert_position = 0
                        for i in range(len(queue) - 1, -1, -1):
                            p = _priority_of(queue[i])
                            if p is not None and p >= int(new_data_priority):
                                insert_position = i + 1
                                break
                        _insert_and_notify(insert_position, data_json)
                    logger.debug(f"insert_position={insert_position}")
                    inserted = True
                    insert_msg = {"code": 200, "msg": f"音频已插入到位置 {insert_position}"}
                else:
                    # 队满路径（规则2/3/4）——组定位与驱逐全部锁内
                    # R4g（2026-11-11 song3.log 实测）：song 切片段豁免满载
                    # 取代/驱逐——切片序列不可丢（实测 seg_00 被「同权取代组首」
                    # 挤掉连锁：伴奏不启动（backing_path 仅在 seq0）+锚不建立
                    # （窗口机制全瘫）+人声从歌曲中间开始唱）。满载仍插组尾
                    # （队列超限消费后回落）；普通条目维持 A1 权威语义。
                    # 2026-11-21 动态文案审查批次：动态文案分段同享豁免。
                    _is_song_slice = (isinstance(data_json, dict)
                                      and data_json.get("type") == "song"
                                      and data_json.get("song_segment"))
                    _is_trends_slice = (isinstance(data_json, dict)
                                        and data_json.get("type") == "trends_copywriting"
                                        and isinstance(data_json.get("split_group"), dict))
                    # S1 遗弃保护（2026-11-21 队列优化批次）：弹幕问答编组切分段
                    # （reply_link 命中且组已进入消费流程）满载不丢不驱逐
                    _reply_protected = self._reply_group_protected(data_json)
                    if _is_song_slice or _is_trends_slice or _reply_protected:
                        insert_position = len(queue)
                        _insert_and_notify(insert_position, data_json)
                        inserted = True
                        insert_msg = {"code": 200, "msg": "编组切片满载豁免（接组尾，序列保真）"}
                        logger.info(f"voice_tmp_path_queue 已满，编组切片豁免接组尾（序列保真）：【{data_json['voice_path']}】{_split_group_note(data_json)}")
                    else:
                        # 分账编组保护（2026-11-21 动态文案审查批次）+ S1 遗弃
                        # 保护：规则2/3 的同权重/最低优候选排除背景流切片与受
                        # 保护回复组段（序列不可丢，驱逐只作用于普通条目）
                        same_idx_list = [i for i, item in enumerate(queue)
                                         if _priority_of(item) == int(new_data_priority)
                                         and not _is_bg_slice_entry(item)
                                         and not self._reply_group_protected(item)]
                        if same_idx_list:
                            # 规则2：取代同权重组组首（最老）条目，新条目接组尾（净长度不变，组内 FIFO）
                            front_idx = same_idx_list[0]
                            last_idx = same_idx_list[-1]
                            self.voice_tmp_path_queue.pop(front_idx)
                            self.voice_tmp_path_queue.insert(last_idx, data_json)  # pop 后组尾左移1，插回原组尾位
                            if self.voice_tmp_path_queue_not_empty_flag:
                                self.voice_tmp_path_queue_not_empty.notify()
                            try:
                                from utils.obs_stats import record_drop
                                record_drop("audio_same_tier_evict")
                            except Exception:
                                pass
                            logger.info(f"voice_tmp_path_queue 已满，同权重取代组首最老条目（原位置 {front_idx}）：【{data_json['voice_path']}】{_split_group_note(data_json)}")
                            inserted = True
                            insert_msg = {"code": 200, "msg": f"同权重取代组首条目（原位置 {front_idx}）"}
                        else:
                            non_none_priorities = [_priority_of(it) for it in queue
                                                   if _priority_of(it) is not None
                                                   and not _is_bg_slice_entry(it)
                                                   and not self._reply_group_protected(it)]
                            min_priority = min(non_none_priorities) if non_none_priorities else None
                            if min_priority is not None and int(new_data_priority) > min_priority:
                                # 规则3：插入后驱逐最低优组组首（最老）条目——主叙述 10old/10new 双存要求组首驱逐
                                evict_idx = next(i for i, it in enumerate(queue)
                                                 if _priority_of(it) == min_priority
                                                 and not _is_bg_slice_entry(it)
                                                 and not self._reply_group_protected(it))
                                insert_position = 0
                                for i in range(len(queue) - 1, -1, -1):
                                    p = _priority_of(queue[i])
                                    if p is not None and p >= int(new_data_priority):
                                        insert_position = i + 1
                                        break
                                self.voice_tmp_path_queue.pop(evict_idx)
                                if insert_position > evict_idx:
                                    insert_position -= 1
                                _insert_and_notify(insert_position, data_json)
                                try:
                                    from utils.obs_stats import record_drop
                                    record_drop("audio_same_tier_evict")
                                except Exception:
                                    pass
                                logger.info(f"voice_tmp_path_queue 已满，新权重 {new_data_priority} 插入并驱逐最低优组最老条目（原位置 {evict_idx}）：【{data_json['voice_path']}】")
                                inserted = True
                                insert_msg = {"code": 200, "msg": f"插入位置 {insert_position}，驱逐最低优组条目（原位置 {evict_idx}）"}
                            else:
                                # 规则4：满且新权重更低 → 舍弃
                                logger.warning(f"voice_tmp_path_queue 已满，音频丢弃：【{data_json['voice_path']}】{_split_group_note(data_json)}")
                                try:
                                    from utils.obs_stats import record_drop
                                    record_drop("audio_rule4_dropped")
                                except Exception:
                                    pass
                                insert_msg = {"code": 1, "msg": f"voice_tmp_path_queue 已满，音频丢弃：【{data_json['voice_path']}】"}

            # 锁外收尾：D-22 待播舍弃通知与 barge-in/让位裁决（均不得持锁调用——前者内部
            # 自取本锁，后者触达播放器 yield/EDTalk 打断事件）。原实现 discard 在插入前
            # 评估、interrupt 在各插入点后调用；现统一为「插入成功才触发」，触发集合
            # 不变（未插入路径原实现同样不触发 interrupt；talk 条目被满载丢弃时原
            # 实现会多发一次舍弃通知——该通知语义本就指向实际舍弃，丢弃场景不发更准）。
            if inserted:
                self._maybe_discard_pending_for_talk(data_json)
                self._maybe_priority_yield_for(data_json)
                # P1（2026-11-21 用户实测批次）：点歌窗口期回复组「顶替-保留
                # 最新」——成功插入回复段后舍弃更旧回复组段（锁外自取锁）
                self._discard_stale_replies_for_song(data_json)
            return insert_msg
