# -*- coding: utf-8 -*-
"""段间让位：动态文案插播&切回 + barge-in 退役（2026-11-16 批次）。

覆盖裁决条款：
- barge-in 打断即弃退役：playback_manager 无 _on_edtalk_barge_in /
  INTERRUPTIBLE_COPY_TYPES；EDTalkClient 无 request_interrupt / 打断事件；
  queue_manager 无 _edtalk_barge_in_hook 钩子链（原 _maybe_barge_in_for 改名
  _maybe_priority_yield_for，仅保留 d12 builtin 让位）
- 段间让位（用户确认设计：播完当前段再插播）：动态文案切分段（权重 10）在队
  时，更高权重音频（comment=50）入队 → 队列排序高权重在前 → 消费循环先消费
  插播音频，剩余文案段自然续播（切回）——无需 999 特权机制
- 触发门（task_scheduler._is_synth_queue_idle，2026-11-21 用户裁决：只看待
  合成消息队列）——message_queue 空即触发；已合成音频播报中不阻止新任务；
  动态文案生成前/入队前两查，忙则跳过本轮

风格对齐：宿主 stub 手工组装（test_audio_playback_review/test_t9_dynamic_copy
同款模式）。
"""
import asyncio
import threading
import time
from types import SimpleNamespace

import pytest

from utils.audio.playback_manager import PlaybackMixin
from utils.audio.queue_manager import QueueMixin
from utils.edtalk_realtime.edtalk_client import EDTalkClient


# ------------------------------------------- barge-in 退役断言

def test_barge_in_mechanism_retired():
    """barge-in 打断即弃整体退役：钩子方法/类型集/打断事件均不存在（防复活）。"""
    assert not hasattr(PlaybackMixin, "_on_edtalk_barge_in")
    assert not hasattr(PlaybackMixin, "_maybe_barge_in_for")
    from utils.audio import playback_manager as _pm
    assert not hasattr(_pm, "INTERRUPTIBLE_COPY_TYPES")
    client = EDTalkClient({})
    assert not hasattr(client, "request_interrupt")
    assert not hasattr(client, "_interrupt_event")
    q = QueueMixin.__new__(QueueMixin)
    assert not hasattr(q, "_edtalk_barge_in_hook")


# ------------------------------------------- 段间让位（队列排序语义）

class _QueueCfg:
    def __init__(self, mapping):
        self._m = {"filter": {"priority_mapping": mapping,
                              "voice_tmp_path_queue_max_len": 100,
                              "voice_tmp_path_queue_min_start_play": 1,
                              "message_queue_max_len": 100}}

    def get(self, *keys):
        cur = self._m
        for k in keys:
            if not isinstance(cur, dict) or k not in cur:
                return None
            cur = cur[k]
        return cur


class _QueueFixture(QueueMixin):
    def __init__(self, mapping):
        self.config = _QueueCfg(mapping)
        self.message_queue = []
        self.message_queue_lock = threading.Lock()
        self.message_queue_not_empty = threading.Condition(lock=self.message_queue_lock)
        self.voice_tmp_path_queue = []
        self.voice_tmp_path_queue_lock = threading.Lock()
        self.voice_tmp_path_queue_not_empty = threading.Condition(lock=self.voice_tmp_path_queue_lock)
        self.voice_tmp_path_queue_not_empty_flag = False
        self.audio_player = None


def _copy_segment(seq, total=3):
    """动态文案切分段条目（split_group 组绑定，权重 10）"""
    return {"type": "trends_copywriting", "voice_path": f"copy_{seq}.mp3",
            "content": f"文案第{seq + 1}段",
            "split_group": {"id": "g1", "seq": seq, "total": total}}


def test_segment_yield_high_priority_first():
    """段间让位核心：文案 3 段已在队 → comment（50）后入队 → 队列排序
    comment 在前。消费循环逐条 pop 队首：先插播 comment，播完后剩余文案段
    继续消费（切回续播），不弃段不重推。"""
    q = _QueueFixture({"trends_copywriting": 10, "comment": 50})
    for seq in range(3):
        assert q.data_priority_insert(
            "song", _copy_segment(seq))["code"] == 200
    assert q.data_priority_insert(
        "song", {"type": "comment", "voice_path": "reply.mp3",
                 "content": "弹幕回复"})["code"] == 200
    types = [e["type"] for e in q.voice_tmp_path_queue]
    assert types == ["comment", "trends_copywriting", "trends_copywriting",
                     "trends_copywriting"], \
        f"插播音频应排在全部文案段之前：{types}"


def test_segment_group_order_stable_among_copy():
    """同权重文案段保持 FIFO 稳定（切回时按切分顺序续播，不乱序）。"""
    q = _QueueFixture({"trends_copywriting": 10, "comment": 50})
    for seq in range(3):
        q.data_priority_insert("song", _copy_segment(seq))
    contents = [e["content"] for e in q.voice_tmp_path_queue]
    assert contents == ["文案第1段", "文案第2段", "文案第3段"]


def test_priority_yield_builtin_kept_after_rename():
    """d12 builtin 让位保留（改名后语义不变）：在播权重 40 + 新音频 50 →
    request_yield 触发；新音频路由 EDTalk（同声道前提）→ 不让位。"""
    q = _QueueFixture({"comment": 50, "key_mapping": 40})
    yielded = []
    q.audio_player = SimpleNamespace(
        request_yield=lambda: yielded.append(1),
        get_playing_weight=lambda: 40)
    q._maybe_priority_yield_for({"type": "comment", "voice_path": "x.mp3"})
    assert len(yielded) == 1


def test_priority_yield_no_player_fail_soft():
    """无播放器/无 request_yield 能力 → 安全跳过（fail-soft，不抛）。"""
    q = _QueueFixture({"comment": 50})
    q.audio_player = None
    q._maybe_priority_yield_for({"type": "comment", "voice_path": "x.mp3"})  # 不抛
    q.audio_player = SimpleNamespace()  # 无 request_yield
    q._maybe_priority_yield_for({"type": "comment", "voice_path": "x.mp3"})  # 不抛


# ------------------------------------------- 闲时门（task_scheduler，保留回归）

def _idle_gate_scheduler(mh_audio):
    """TaskScheduler 最小宿主（test_t9_dynamic_copy 同款构造）。"""
    from utils.task_scheduler import TaskScheduler

    config = SimpleNamespace()
    config.get = lambda *keys: {
        ("trends_copywriting", "play_interval"): 1,
    }.get(keys)

    my_handle = SimpleNamespace(audio=mh_audio)
    ts = TaskScheduler.__new__(TaskScheduler)
    ts.config = config
    ts.my_handle = my_handle
    ts.is_running = True
    return ts


def test_synth_queue_idle_matrix():
    """触发门判定矩阵（2026-11-21 用户裁决：只看待合成消息队列）：
    message_queue 空=空闲（待播放队列/在飞登记不阻止触发）；非空=忙；
    宿主缺音频组件=忙（保守跳过）。"""
    from utils.task_scheduler import TaskScheduler

    # 待播放队列非空 + 在飞登记非空：不阻止触发（旧三条件门已退役）
    ts = _idle_gate_scheduler(SimpleNamespace(
        message_queue=[], voice_tmp_path_queue=["待播放"],
        _edtalk_current_entry={"type": "trends_copywriting"}))
    assert ts._is_synth_queue_idle() is True

    busy_cases = [
        SimpleNamespace(message_queue=["待合成"], voice_tmp_path_queue=[],
                        _edtalk_current_entry=None),
    ]
    for audio in busy_cases:
        ts = _idle_gate_scheduler(audio)
        assert ts._is_synth_queue_idle() is False

    ts = _idle_gate_scheduler(None)  # 宿主音频组件未就绪=忙（保守跳过）
    assert ts._is_synth_queue_idle() is False


def test_trends_copywriting_skips_round_when_busy(tmp_path):
    """触发门端到端（2026-11-21 用户裁决）：待合成队列非空 → run_trends_
    copywriting 一轮整体跳过（文案未读取、LLM 未调、未入队），不告警不重试。"""
    from utils.task_scheduler import TaskScheduler

    ts = _idle_gate_scheduler(SimpleNamespace(
        message_queue=["busy-entry"], voice_tmp_path_queue=[],
        _edtalk_current_entry=None))

    # 金丝雀：文案读取面/LLM 面被触碰即失败（忙=本轮应整体跳过）
    ts.common = SimpleNamespace(
        get_all_file_paths=lambda folder: (_ for _ in ()).throw(
            AssertionError("系统忙时不应读取文案目录")))

    ts.config.get = lambda *keys: {
        ("trends_copywriting", "enable"): True,
        ("trends_copywriting", "play_interval"): 1,
    }.get(keys)

    synthesis_calls = []
    ts.my_handle.audio_synthesis_handle = lambda msg: synthesis_calls.append(msg)

    # 确定性停止：忙跳过一轮（含 play_interval=1 分片睡眠）后停线程
    def stopper():
        time.sleep(1.6)
        ts.is_running = False
    threading.Thread(target=stopper, daemon=True).start()

    ts.start_trends_copywriting()
    ts.trends_copywriting_thread.join(timeout=6)

    assert synthesis_calls == [], "待合成队列忙时动态文案不应入队播放"


def test_trends_copywriting_triggers_despite_voice_queue_busy(tmp_path):
    """触发门端到端（2026-11-21 用户裁决）：待播放队列非空不阻止触发——
    待合成队列为空即触发并正常入链（旧三条件门会误判忙）。"""
    from utils.task_scheduler import TaskScheduler

    ts = _idle_gate_scheduler(SimpleNamespace(
        message_queue=[], voice_tmp_path_queue=["busy-entry"],
        _edtalk_current_entry=None))

    ts.config.get = lambda *keys: {
        ("trends_copywriting", "enable"): True,
        ("trends_copywriting", "play_interval"): 1,
        ("trends_copywriting", "copy"): ["测试词|今天的商品，不要999"],
        ("trends_copywriting", "random_play"): False,
        ("trends_copywriting", "prompt_change_enable"): False,
    }.get(keys)

    synthesis_calls = []
    ts.my_handle.audio_synthesis_handle = lambda msg: synthesis_calls.append(msg)

    def stopper():
        time.sleep(1.6)
        ts.is_running = False
    threading.Thread(target=stopper, daemon=True).start()

    ts.start_trends_copywriting()
    ts.trends_copywriting_thread.join(timeout=6)

    assert len(synthesis_calls) >= 1, "待播放队列忙不应阻止动态文案触发入链"
    assert synthesis_calls[0]["type"] == "trends_copywriting"
    assert synthesis_calls[0]["content"] == "今天的商品，不要999"


class _MsgHost(QueueMixin):
    """data_priority_insert 等待合成消息分支最小宿主。"""

    class _Cfg:
        def get(self, *keys):
            return {
                ("filter", "priority_mapping"): {
                    "trends_copywriting": 10, "entrance": 30,
                    "comment": 50, "song": 300,
                },
                ("filter", "message_queue_max_len"): 200,
                ("filter", "message_queue_replace_same_priority"): False,
            }.get(tuple(keys))

    def __init__(self):
        self.message_queue = []
        self.message_queue_lock = threading.Lock()
        self.message_queue_not_empty = threading.Condition(
            lock=self.message_queue_lock)
        self.config = _MsgHost._Cfg()


def _msg_copy(text):
    """待合成动态文案消息（生产者批量入队形态，权重 10）。"""
    return {"type": "trends_copywriting", "content": text}


def _msg_song_slice(seq, total=2):
    """song 切片合成消息（song_segment 绑定，R5b 追加语义）。"""
    return {"type": "song", "content": "vocal.wav",
            "song_segment": {"seq": seq, "total": total, "start_s": seq * 45.0}}


def test_msg_song_slices_jump_copy_messages():
    """核心场景（log2.txt 铁证复现·第三层根修）：文案生产者一次性批量入队
    31 条文案消息后触发点歌 → song 切片合成消息必须越过全部文案消息
    （插入 position=0），否则合成线程逐条消费文案 ~2 分钟后才轮到切片，
    伴奏/人声迟迟不产。"""
    host = _MsgHost()
    for i in range(31):
        host.data_priority_insert(data_json=_msg_copy(f"文案第{i + 1}句"))
    assert len(host.message_queue) == 31
    host.data_priority_insert(data_json=_msg_song_slice(0))
    assert host.message_queue[0]["song_segment"]["seq"] == 0
    assert len(host.message_queue) == 32
    host.data_priority_insert(data_json=_msg_song_slice(1))
    # seq 保序：seq0 在 seq1 前，均在文案之前
    assert host.message_queue[0]["song_segment"]["seq"] == 0
    assert host.message_queue[1]["song_segment"]["seq"] == 1
    assert host.message_queue[2]["type"] == "trends_copywriting"


def test_msg_song_slice_stays_after_non_copy():
    """song 切片保持背景流语义：不越过非文案消息（答谢/回复先于歌播），
    只越过文案消息。"""
    host = _MsgHost()
    host.data_priority_insert(data_json=_msg_copy("文案A"))
    host.data_priority_insert(data_json={"type": "entrance", "content": "入场词"})  # 30 > 10 → 插到文案前
    assert host.message_queue[0]["type"] == "entrance"
    host.data_priority_insert(data_json=_msg_song_slice(0))
    assert host.message_queue[1]["song_segment"]["seq"] == 0
    assert host.message_queue[2]["type"] == "trends_copywriting"


def test_msg_song_slice_empty_queue_appends():
    """空队列常规追加（无让位语义介入）。"""
    host = _MsgHost()
    host.data_priority_insert(data_json=_msg_song_slice(0))
    assert len(host.message_queue) == 1
    assert host.message_queue[0]["song_segment"]["seq"] == 0


def test_msg_copy_batch_unaffected():
    """文案消息彼此仍按权重/队尾 FIFO（10 同权不互相插队），生产顺序保持。"""
    host = _MsgHost()
    for i in range(5):
        host.data_priority_insert(data_json=_msg_copy(f"文案第{i + 1}句"))
    assert [m["content"] for m in host.message_queue] == \
        [f"文案第{i + 1}句" for i in range(5)]


# --------------------------------- 出队侧段边界让位（2026-11-24 用户口径）
#
# 用户实测（点歌期间文案阻塞起播）：文案分段与 song 切片同为队尾追加，出队
# 选择按 FIFO 把剩余文案段全部消费完才轮到 song seq0——伴奏/人声迟迟不起，
# 点歌画面 hold 超时回默认。用户口径：动态文案在队列中的底层逻辑是任何其它
# 权重消息都能插队消费。守卫面：无锚态下队首文案段让位第一个非文案条目。

class _PopHost(QueueMixin, PlaybackMixin):
    """_pop_select_locked 最小宿主（无锚态；_entry_duration_s 走 fail-soft 0）。"""

    def __init__(self):
        self.voice_tmp_path_queue = []
        self.voice_tmp_path_queue_lock = threading.Lock()
        self.voice_tmp_path_queue_not_empty = threading.Condition(
            lock=self.voice_tmp_path_queue_lock)
        self.voice_tmp_path_queue_not_empty_flag = False
        self.audio_player = None


def _song_slice(seq, total=2):
    """song 切片段条目（song_segment 组绑定，队尾追加语义）"""
    return {"type": "song", "voice_path": f"seg_{seq}.mp3",
            "content": "牧马城市",
            "song_segment": {"seq": seq, "total": total,
                             "start_s": seq * 45.0, "seg_dir": "d1"}}


# ----------------------- 唱歌时间排除文案插播（2026-11-24 用户裁决） -----------------------

class _SingHost(_PopHost):
    """锚存续态宿主：t0=now−10s，间奏窗口由队内 song 段 start_s 决定。"""

    def __init__(self):
        super().__init__()
        self._song_anchor = {"t0": time.monotonic() - 10.0, "last_end": 0.0,
                             "skip_count": 0, "wrapping": False}


def _thanks(kind="comment"):
    """非文案插播条目（答谢/回复，时长未知 fail-soft=0 → 预算放行）。"""
    return {"type": kind, "content": "谢谢关心", "voice_path": "th.mp3"}


def test_singing_copy_head_skips_to_thanks():
    """队首=文案分段：文案留队，首个非歌段非文案条目（答谢）按窗口插播。"""
    host = _SingHost()
    host.voice_tmp_path_queue = [_copy_segment(0), _thanks(),
                                 _song_slice(1)]
    entry, wait, borrowed = host._pop_select_locked()
    assert entry["type"] == "comment" and borrowed is True
    assert host.voice_tmp_path_queue[0]["type"] == "trends_copywriting"  # 文案留队
    assert host.voice_tmp_path_queue[-1]["type"] == "song"  # 歌段留队


def test_singing_copy_only_waits_until_song_end():
    """队首=文案且仅剩文案+未到期歌段：不插播，等到期推人声（文案留队）。"""
    host = _SingHost()
    host.voice_tmp_path_queue = [_copy_segment(0), _copy_segment(1),
                                 _song_slice(1)]
    entry, wait, borrowed = host._pop_select_locked()
    assert entry is None and wait > 0 and borrowed is False
    assert sum(1 for e in host.voice_tmp_path_queue
               if e["type"] == "trends_copywriting") == 2


def test_singing_borrow_skips_copy_behind_song():
    """②c 借位：队首=未到期歌段 → 文案分段跳过，答谢借位消费。"""
    host = _SingHost()
    host.voice_tmp_path_queue = [_song_slice(1), _copy_segment(0), _thanks()]
    entry, wait, borrowed = host._pop_select_locked()
    assert entry["type"] == "comment" and borrowed is True
    assert host.voice_tmp_path_queue[0]["type"] == "song"  # 歌段留队
    assert host.voice_tmp_path_queue[1]["type"] == "trends_copywriting"  # 文案留队


def test_singing_tail_gap_copy_still_queued():
    """收尾间隙（队内无 song 段）：文案仍留队等锚清除，答谢自由消费。"""
    host = _SingHost()
    host.voice_tmp_path_queue = [_copy_segment(0), _thanks()]
    entry, _, borrowed = host._pop_select_locked()
    assert entry["type"] == "comment" and borrowed is True
    assert host.voice_tmp_path_queue[0]["type"] == "trends_copywriting"

    host2 = _SingHost()
    host2.voice_tmp_path_queue = [_copy_segment(0)]
    entry2, wait2, _ = host2._pop_select_locked()
    assert entry2 is None and wait2 > 0  # 仅文案 → 短轮询等待，文案留队
    assert host2.voice_tmp_path_queue[0]["type"] == "trends_copywriting"


def test_singing_copy_resumes_after_anchor_clears():
    """歌毕锚清除（垃圾时间/无锚）→ 文案恢复自由消费（段边界让位续播）。"""
    host = _SingHost()
    host.voice_tmp_path_queue = [_copy_segment(0), _copy_segment(1)]
    host._song_anchor = {"t0": time.monotonic() - 10.0, "last_end": 0.0,
                         "skip_count": 0, "wrapping": True}  # 垃圾时间
    entry, wait, borrowed = host._pop_select_locked()
    assert entry is not None and entry["type"] == "trends_copywriting"


def test_pop_copy_yields_to_song_slice():
    """核心场景（用户实测复现）：文案 2 段在队 + 点歌切片 2 段队尾追加 →
    第一次出队直接弹出 song seq0（点歌应答后立刻起播伴奏/人声），文案段
    延后留队待间奏/空闲续消费。"""
    host = _PopHost()
    host.voice_tmp_path_queue = [_copy_segment(0), _copy_segment(1),
                                 _song_slice(0), _song_slice(1)]
    entry, gate, borrowed = host._pop_select_locked()
    assert gate == 0.0 and borrowed is False
    assert entry["type"] == "song" and entry["song_segment"]["seq"] == 0
    assert [e["type"] for e in host.voice_tmp_path_queue] == \
        ["trends_copywriting", "trends_copywriting", "song"]


def test_pop_copy_yields_to_first_non_copy():
    """文案段之间夹/后置任意其它条目 → 第一个非文案条目插队（任何消息都能
    插队消费文案，非 song 专属）。"""
    host = _PopHost()
    host.voice_tmp_path_queue = [_copy_segment(0),
                                 {"type": "entrance", "voice_path": "e.mp3"},
                                 _copy_segment(1)]
    entry, _, _ = host._pop_select_locked()
    assert entry["type"] == "entrance"
    assert [e["type"] for e in host.voice_tmp_path_queue] == \
        ["trends_copywriting", "trends_copywriting"]


def test_pop_song_seq_order_preserved_across_copy():
    """song 切片组跨文案段保序：seq0 先出队，seq1 在文案段之后仍先于文案
    出队（song seq 有序不被文案打断）。"""
    host = _PopHost()
    host.voice_tmp_path_queue = [_copy_segment(0), _song_slice(0),
                                 _copy_segment(1), _song_slice(1)]
    first, _, _ = host._pop_select_locked()
    assert first["song_segment"]["seq"] == 0
    second, _, _ = host._pop_select_locked()
    assert second["song_segment"]["seq"] == 1
    assert [e["type"] for e in host.voice_tmp_path_queue] == \
        ["trends_copywriting", "trends_copywriting"]


def test_pop_no_yield_when_head_not_copy():
    """队首非文案条目 → 常规 FIFO pop（文案让位只在队首为文案段时介入，
    不改变正常权重序语义）。"""
    host = _PopHost()
    head = {"type": "comment", "voice_path": "reply.mp3"}
    host.voice_tmp_path_queue = [head, _copy_segment(0), _song_slice(0)]
    entry, _, _ = host._pop_select_locked()
    assert entry is head


def test_pop_copy_only_queue_pops_head():
    """队列只有文案段 → 常规消费队首（让位不空转、文案不被饿死）。"""
    host = _PopHost()
    host.voice_tmp_path_queue = [_copy_segment(0), _copy_segment(1)]
    entry, _, _ = host._pop_select_locked()
    assert entry["content"] == "文案第1段"


def test_pop_copy_yield_via_group_atomic_gate():
    """端到端（_pop_next_entry_locked）：song seq0 直接越过全部文案段出队
    （组原子门对文案组例外放行 → 出队选择层完成插队）；song 切片段不带
    split_group 不登记组上下文，随后文案段恢复常规消费。"""
    host = _PopHost()
    host.voice_tmp_path_queue = [_copy_segment(0), _copy_segment(1),
                                 _song_slice(0)]
    s0, _, _ = host._pop_next_entry_locked()
    assert s0["type"] == "song" and s0["song_segment"]["seq"] == 0
    c0, _, _ = host._pop_next_entry_locked()
    assert c0["type"] == "trends_copywriting"
    assert [e["type"] for e in host.voice_tmp_path_queue] == \
        ["trends_copywriting"]


def test_pop_pending_reply_gate_skips_copy_for_song():
    """念白配对门在途 + 队首文案段 → 门控放行跳过文案段直取 song 切片
    （文案让位任何消息，含门控期）。"""
    host = _PopHost()
    host.reply_gate_track_popped({"type": "read_comment",
                                  "reply_link": "rc-1"})
    host.voice_tmp_path_queue = [_copy_segment(0), _song_slice(0)]
    entry, gate, _ = host._pop_select_locked()
    assert gate == 0.0
    assert entry["type"] == "song" and entry["song_segment"]["seq"] == 0
    assert [e["type"] for e in host.voice_tmp_path_queue] == \
        ["trends_copywriting"]
    QueueMixin._reply_gate_pending.clear()
    QueueMixin._reply_gate_abandoned.clear()
