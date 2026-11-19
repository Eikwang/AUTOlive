# -*- coding: utf-8 -*-
"""消息分段组原子消费守卫（2026-11-17 组原子消费批次）。

覆盖裁决条款：
- 组原子核心：同一消息切分段（split_group 组绑定）视为一个组，开始消费后
  连续消费完成——念弹幕 2 段+高权重插入 → 两段连续消费，高权重最后消费；
  复读（reread=500）/编排插队（priority_override=500）不插入非文案组中间
- 唯一例外动态文案（trends_copywriting）：分段组维持 2026-11-16 段间让位
  批次的刻意设计——段边界允许高权重插队/让位（现状保持）
- 边界与防死锁：组内段队满丢弃/合成失败跳过 → 扫描落空视为已消费（组推进
  不卡死）；组完成恢复正常权重 pop；单段组天然原子
- 让位门控：在播非文案组段 → request_yield 不触发（新条目仅入队等待组完）；
  在播文案组段/非组条目 → 触发（d12 保留）；无 get_playing_entry 能力 →
  fail-open 退既有让位语义
- builtin 移交层组原子地板：内部 pending 队列含非文案组段时，新条目不插队
  到组段之前（play() 提前移交后消费循环组原子门管不到移交层）；文案组段
  维持可插队现状；insert_index 显式定位路径不越权改写
- song 切片不受影响：song_segment 字段非 split_group 组，不入组上下文；
  song 锚存在（点歌时间轴）时组原子门 fail-open 不生效（间奏插播语义
  不回归），锚清除后组原子恢复

风格对齐：宿主 stub 手工组装（test_copy_segment_yield /
test_song_interlude_sync 同款模式）。
"""
import threading
import time
from types import SimpleNamespace

import pytest

from utils.audio.playback_manager import PlaybackMixin
from utils.audio.queue_manager import QueueMixin


# ------------------------------------------- 宿主 stub

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


_MAPPING = {"read_comment": 60, "comment": 20, "reread": 500,
            "song": 10, "trends_copywriting": 10}


def _make_pb(entries=None, mapping=None):
    """带队列/锁/组上下文的消费循环宿主（_pop_next_entry_locked 单元）。"""
    import threading as _th

    class _Impl(PlaybackMixin, QueueMixin):
        pass

    pb = _Impl()
    pb.config = _QueueCfg(mapping or dict(_MAPPING))
    pb.voice_tmp_path_queue = list(entries or [])
    pb.voice_tmp_path_queue_lock = _th.RLock()
    pb.voice_tmp_path_queue_not_empty = _th.Condition(lock=pb.voice_tmp_path_queue_lock)
    pb.voice_tmp_path_queue_not_empty_flag = False
    pb.message_queue = []
    pb.message_queue_lock = _th.Lock()
    pb.message_queue_not_empty = _th.Condition(lock=pb.message_queue_lock)
    pb.audio_player = None
    pb._song_anchor = None
    return pb


def _pop(pb):
    with pb.voice_tmp_path_queue_lock:
        return pb._pop_next_entry_locked()


def _seg(gid, seq, total=2, type_="read_comment"):
    return {"type": type_, "voice_path": f"{gid}_{seq}.mp3",
            "content": f"{gid}第{seq + 1}段",
            "split_group": {"id": gid, "seq": seq, "total": total}}


# ------------------------------------------- 组原子核心

def test_read_comment_group_atomic_high_priority_last():
    """组原子核心（用户例：念弹幕 2 段）：念弹幕组开始消费后插入复读（500）
    → 两段连续消费，复读最后消费（不插入组中间）。"""
    pb = _make_pb()
    for seq in range(2):
        assert pb.data_priority_insert("song", _seg("g1", seq))["code"] == 200
    d1, gw, _b = _pop(pb)
    assert gw == 0 and d1["split_group"]["seq"] == 0  # 组开始消费
    # 复读（映射 500 > 60）权重序插到剩余段之前
    assert pb.data_priority_insert(
        "song", {"type": "reread", "voice_path": "rr.mp3", "content": "复读"})["code"] == 200
    assert pb.voice_tmp_path_queue[0]["type"] == "reread"
    d2, _, _ = _pop(pb)
    assert d2["split_group"]["seq"] == 1, "组原子：无视插队条目强制续消费同组下一段"
    d3, _, _ = _pop(pb)
    assert d3["type"] == "reread", "组完成后高权重条目消费"


def test_override_500_not_inserted_mid_group():
    """编排插队音轨（priority_override=500）不插入非文案组中间。"""
    pb = _make_pb()
    for seq in range(3):
        pb.data_priority_insert("song", _seg("g2", seq, total=3, type_="comment"))
    d1, _, _ = _pop(pb)
    assert d1["split_group"]["seq"] == 0
    assert pb.data_priority_insert(
        "song", {"type": "comment", "voice_path": "choreo.mp3", "content": "编排插队",
                 "priority_override": 500})["code"] == 200
    assert pb.voice_tmp_path_queue[0].get("priority_override") == 500
    for seq in (1, 2):
        d, _, _ = _pop(pb)
        assert d["split_group"]["seq"] == seq, "500 插队条目不得插入组中间"
    d, _, _ = _pop(pb)
    assert d.get("priority_override") == 500, "组完成后插队条目消费"


def test_trends_copywriting_group_interleave_kept():
    """唯一例外动态文案：分段组维持段边界插队现状（2026-11-16 段间让位批次
    设计）——组开始消费后高权重条目仍可在段边界插到剩余段之前消费。"""
    pb = _make_pb()
    for seq in range(3):
        pb.data_priority_insert("song", _seg("gc", seq, total=3,
                                             type_="trends_copywriting"))
    assert pb.data_priority_insert(
        "song", {"type": "reread", "voice_path": "rr.mp3", "content": "复读"})["code"] == 200
    order = []
    while pb.voice_tmp_path_queue:
        d, _, _ = _pop(pb)
        order.append(d["voice_path"])
    assert order == ["rr.mp3", "gc_0.mp3", "gc_1.mp3", "gc_2.mp3"], \
        f"文案组段边界插队现状保持：{order}"


def test_trends_copywriting_ctx_not_force_pop():
    """文案组段出队登记组上下文后，组原子门仍不强制续消费（下一段前插入的
    高权重条目先消费）。"""
    pb = _make_pb()
    pb.data_priority_insert("song", _seg("gc", 0, total=2, type_="trends_copywriting"))
    pb.data_priority_insert("song", _seg("gc", 1, total=2, type_="trends_copywriting"))
    d1, _, _ = _pop(pb)
    assert d1["type"] == "trends_copywriting"
    assert pb.data_priority_insert(
        "song", {"type": "reread", "voice_path": "rr.mp3", "content": "复读"})["code"] == 200
    d2, _, _ = _pop(pb)
    assert d2["type"] == "reread", "文案组不触发组原子强制续消费"


# ------------------------------------------- 边界与防死锁

def test_group_advances_when_segment_missing():
    """组内段合成失败/队满丢弃=永不入队 → 扫描跳过缺失段推进（seq1 缺失，
    seq0 消费后 seq2 强制续消费，组不卡死）。"""
    pb = _make_pb()
    pb.data_priority_insert("song", _seg("g3", 0, total=3, type_="comment"))
    pb.data_priority_insert("song", _seg("g3", 2, total=3, type_="comment"))  # seq1 缺失
    d1, _, _ = _pop(pb)
    assert d1["split_group"]["seq"] == 0
    d2, _, _ = _pop(pb)
    assert d2["split_group"]["seq"] == 2, "缺失段视为已消费，组推进不卡死"


def test_group_completed_ctx_cleared_and_weight_pop_resumed():
    """组剩余段清空=组完成：ctx 清除 + 恢复正常权重 pop（高权重先于低权重）。"""
    pb = _make_pb()
    pb.data_priority_insert("song", _seg("g4", 0, total=2, type_="comment"))
    pb.data_priority_insert("song", _seg("g4", 1, total=2, type_="comment"))
    _pop(pb)  # seq0
    _pop(pb)  # seq1（组完成）
    pb.data_priority_insert("song", {"type": "comment", "voice_path": "low.mp3",
                                     "content": "低权重"})
    pb.data_priority_insert("song", {"type": "reread", "voice_path": "high.mp3",
                                     "content": "高权重"})
    d1, _, _ = _pop(pb)
    d2, _, _ = _pop(pb)
    assert (d1["voice_path"], d2["voice_path"]) == ("high.mp3", "low.mp3"), \
        "组完成后恢复正常权重 pop"
    assert getattr(pb, "_atomic_group_ctx", None) is None, "组完成 ctx 清除"


def test_single_segment_group_naturally_atomic():
    """单段组天然原子：出队后扫描即落空（ctx 清除），后续条目正常权重消费。"""
    pb = _make_pb()
    pb.data_priority_insert("song", _seg("g5", 0, total=1, type_="comment"))
    d1, _, _ = _pop(pb)
    assert d1["split_group"]["seq"] == 0
    pb.data_priority_insert("song", {"type": "reread", "voice_path": "rr.mp3",
                                     "content": "复读"})
    d2, _, _ = _pop(pb)
    assert d2["type"] == "reread"


# ------------------------------------------- 让位门控（builtin 挂起续播触发点）

def _yield_player(playing_entry, weight):
    yielded = []
    player = SimpleNamespace(
        request_yield=lambda: yielded.append(1),
        get_playing_weight=lambda: weight,
        get_playing_entry=lambda: playing_entry)
    return player, yielded


def test_yield_gate_blocked_for_non_copy_playing_group():
    """在播条目=非文案组段：更高权重新音频不让位（组播完前不得插入组中间，
    新条目仅入队等待组完）。"""
    pb = _make_pb(mapping={"read_comment": 60, "reread": 500})
    playing = _seg("g1", 0, total=2)
    player, yielded = _yield_player(playing, 60)
    pb.audio_player = player
    pb._maybe_priority_yield_for({"type": "reread", "voice_path": "rr.mp3"})
    assert yielded == [], "非文案组在播时不触发让位挂起"


def test_yield_gate_kept_for_copy_group_and_non_group():
    """在播=文案组段（例外现状）或非组条目（d12 song 让位答谢等）→ 触发。"""
    pb = _make_pb(mapping={"trends_copywriting": 10, "song": 10, "reread": 500})
    # 文案组段在播：让位现状保持
    playing_copy = _seg("gc", 0, total=3, type_="trends_copywriting")
    player, yielded = _yield_player(playing_copy, 10)
    pb.audio_player = player
    pb._maybe_priority_yield_for({"type": "reread", "voice_path": "rr.mp3"})
    assert len(yielded) == 1, "文案组段在播维持让位现状"
    # 非组条目在播（song 让位给答谢 d12）：让位保留
    player2, yielded2 = _yield_player({"type": "song", "voice_path": "s.mp3"}, 10)
    pb.audio_player = player2
    pb._maybe_priority_yield_for({"type": "reread", "voice_path": "rr.mp3"})
    assert len(yielded2) == 1, "非组条目 d12 让位保留"


def test_yield_gate_fail_open_without_playing_entry():
    """无 get_playing_entry 能力（旧桩/外部播放器）→ fail-open 退既有让位
    语义（2026-11-16 段间让位批次守卫不回归）。"""
    pb = _make_pb(mapping={"key_mapping": 40, "comment": 50})
    yielded = []
    pb.audio_player = SimpleNamespace(
        request_yield=lambda: yielded.append(1),
        get_playing_weight=lambda: 40)
    pb._maybe_priority_yield_for({"type": "comment", "voice_path": "x.mp3"})
    assert len(yielded) == 1, "在播条目不可得时退既有让位语义"


# ------------------------------------------- builtin 移交层组原子地板

def _builtin_player(mapping):
    from utils.audio.builtin_play_center import AUDIO_PLAY_CENTER
    p = AUDIO_PLAY_CENTER.__new__(AUDIO_PLAY_CENTER)
    p.audio_json_list = []
    p.list_lock = threading.Lock()
    p._priority_mapping = lambda: dict(mapping)  # 实例级单源桩（免文件 IO）
    p._hot_value = lambda key, default: default  # 队列上限等播放时现读桩
    p.audio_data_event = threading.Event()
    p._stop_flag = threading.Event()
    return p


def test_builtin_internal_queue_group_floor():
    """组原子地板：内部 pending 队列含非文案组段时，后到高权重条目插到组段
    之后（play() 提前移交层不被插队拆散）。"""
    p = _builtin_player(_MAPPING)
    p.audio_json_list = [_seg("g1", 1, total=4), _seg("g1", 2, total=4)]
    p._data_priority_insert({"type": "reread", "voice_path": "rr.mp3"})
    assert [e["voice_path"] for e in p.audio_json_list] == \
        ["g1_1.mp3", "g1_2.mp3", "rr.mp3"], "高权重条目不得插到组 pending 段之前"


def test_builtin_floor_kept_for_copy_group():
    """文案组 pending 段维持可插队现状（2026-11-16 段间让位批次设计）。"""
    p = _builtin_player(_MAPPING)
    p.audio_json_list = [_seg("gc", 0, total=3, type_="trends_copywriting"),
                         _seg("gc", 1, total=3, type_="trends_copywriting")]
    p._data_priority_insert({"type": "reread", "voice_path": "rr.mp3"})
    assert p.audio_json_list[0]["voice_path"] == "rr.mp3", "文案组段前可插队"


def test_builtin_floor_not_touch_insert_index():
    """insert_index 显式定位路径（编排显式定位机制）不越权改写。"""
    p = _builtin_player(_MAPPING)
    p.audio_json_list = [_seg("g1", 1, total=4)]
    p.play({"type": "reread", "voice_path": "rr.mp3", "insert_index": 0})
    assert p.audio_json_list[0]["voice_path"] == "rr.mp3"


# ------------------------------------------- song 面不回归

def test_song_segment_entries_not_grouped():
    """song 切片走 song_segment 字段（非 split_group 组）：不入组上下文，
    pop 序不受组原子门影响。"""
    pb = _make_pb()
    pb.voice_tmp_path_queue = [
        {"type": "song", "voice_path": "s0.mp3", "content": "s",
         "song_segment": {"seq": 0, "total": 2, "seg_dir": "", "start_s": 0.0}},
        {"type": "song", "voice_path": "s1.mp3", "content": "s",
         "song_segment": {"seq": 1, "total": 2, "seg_dir": "", "start_s": 45.0}},
    ]
    d1, _, _ = _pop(pb)
    assert d1["song_segment"]["seq"] == 0
    assert getattr(pb, "_atomic_group_ctx", None) is None, "song_segment 不入组上下文"
    d2, _, _ = _pop(pb)
    assert d2["song_segment"]["seq"] == 1


def test_song_anchor_fail_open_and_resume_after_anchor_cleared():
    """点歌时间轴优先（fail-open）：song 锚存在时组原子门不生效（间奏插播
    语义不回归）；锚清除后组原子恢复续消费（组上下文跨旁路出队保留）。"""
    import time as _t
    pb = _make_pb()
    pb.data_priority_insert("song", _seg("g6", 0, total=2, type_="comment"))
    pb.data_priority_insert("song", _seg("g6", 1, total=2, type_="comment"))
    d1, _, _ = _pop(pb)
    assert d1["split_group"]["seq"] == 0
    # 歌曲时间轴激活：插播条目（间奏窗口自由消费，队列无后续歌段）先于组段
    pb._song_anchor = {"t0": _t.monotonic(), "last_end": 0.0}
    pb.data_priority_insert("song", {"type": "reread", "voice_path": "rr.mp3",
                                     "content": "间奏插播"})
    d2, _, _ = _pop(pb)
    assert d2["type"] == "reread", "锚存在时组原子门 fail-open（时间轴优先）"
    # 锚清除（垃圾时间结束/收尾）：组原子恢复，同组下一段强制续消费
    pb._song_anchor = None
    d3, _, _ = _pop(pb)
    assert d3["split_group"]["seq"] == 1, "锚清除后组原子恢复（ctx 跨旁路出队保留）"
