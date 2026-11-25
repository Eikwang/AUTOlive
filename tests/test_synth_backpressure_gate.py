# -*- coding: utf-8 -*-
"""合成背压门（消费回传背压）守卫（2026-11-16 用户高压实测）。

症状：合成线程无背压持续产音频，待播放队列不断增长——高压下打满 max_len
触发满载驱逐，驱逐发生在 TTS 之后=合成成本白付。

机制：合成线程 pop 前判定待播放队列深度（普通条目口径），≥门限（缺省 2）
则暂停，消费出队回执 notify_synth_gate 唤醒——供需速率匹配，溢出前移到
待合成队列（drop-only，合成前丢弃）。

豁免面（与既有机制联动，缺一不可）：
- song 切片段（时间轴领先依赖预合成）
- 权重 ≥500（复读/编排"最高顺位"插队语义保真）
- reply_link 命中在途 pending（配对回复不被门滞留，否则念白单飞门 15s
  超时兜底被迫接管=配对机制退化）
- 非 dict 条目防御放行；synth_gate_depth ≤0 门禁用；
  min_start_play>门限取大（首播阈值可达性防御）
"""
import threading
import time

import pytest

from utils.audio.queue_manager import QueueMixin


class _Cfg:
    """Config 同构 fake（get(*keys) 键路行走）。"""

    def __init__(self, gate_depth=None, min_start=0, mapping=None):
        self._m = {
            "filter": {
                "voice_tmp_path_queue_min_start_play": min_start,
                "priority_mapping": mapping or {"comment": 50, "read_comment": 60,
                                                "reread_top_priority": 500},
            }
        }
        if gate_depth is not None:
            self._m["play_audio"] = {"synth_gate_depth": gate_depth}

    def get(self, *keys):
        cur = self._m
        for k in keys:
            if not isinstance(cur, dict) or k not in cur:
                return None
            cur = cur[k]
        return cur


class _GateFixture(QueueMixin):
    def __init__(self, cfg):
        self.config = cfg
        self.voice_tmp_path_queue = []
        self.voice_tmp_path_queue_lock = threading.Lock()
        self.message_queue = []


@pytest.fixture(autouse=True)
def _reset_state():
    QueueMixin._reply_gate_pending.clear()
    yield
    QueueMixin._reply_gate_pending.clear()


def _normal(n=1):
    return [{"type": "comment", "content": f"c{i}"} for i in range(n)]


# ------------------------------------------------- 豁免面

def test_exempt_song_segment():
    """song 切片段豁免：时间轴领先依赖预合成，不受门控。"""
    q = _GateFixture(_Cfg())
    q.voice_tmp_path_queue = _normal(5)  # 深度超限
    msg = {"type": "song", "song_segment": {"seq": 1}}
    assert q.synth_gate_wait_s(msg) == 0.0


def test_exempt_priority_override_500():
    """编排音轨 override=500 豁免：最高顺位插队语义保真。"""
    q = _GateFixture(_Cfg())
    q.voice_tmp_path_queue = _normal(5)
    msg = {"type": "comment", "priority_override": 500}
    assert q.synth_gate_wait_s(msg) == 0.0


def test_exempt_reread_top_priority_mapping():
    """复读 reread_top_priority 映射 500 豁免（映射值可能为字符串）。"""
    q = _GateFixture(_Cfg(mapping={"reread_top_priority": "500"}))
    q.voice_tmp_path_queue = _normal(5)
    assert q.synth_gate_wait_s({"type": "reread_top_priority"}) == 0.0


def test_exempt_reply_link_pending():
    """配对回复豁免：念白已出队（pending 在途）时其回复不被门滞留——否则
    念白单飞门 15s 超时兜底被迫接管，配对机制退化为超时驱动。"""
    q = _GateFixture(_Cfg())
    q.voice_tmp_path_queue = _normal(5)
    QueueMixin._reply_gate_pending["rc-A"] = time.monotonic()
    msg = {"type": "comment", "reply_link": "rc-A"}
    assert q.synth_gate_wait_s(msg) == 0.0


def test_no_exempt_reply_link_without_pending():
    """reply_link 不在 pending（念白未出队）→ 正常参与门控。"""
    q = _GateFixture(_Cfg())
    q.voice_tmp_path_queue = _normal(5)
    assert q.synth_gate_wait_s({"type": "comment", "reply_link": "rc-A"}) > 0


def test_exempt_non_dict_defensive():
    q = _GateFixture(_Cfg())
    q.voice_tmp_path_queue = _normal(5)
    assert q.synth_gate_wait_s("legacy") == 0.0


# ------------------------------------------------- 门控判定

def test_gate_closed_at_depth_limit():
    """深度达门限（2）→ 门控等待（等待消费出队回执）。"""
    q = _GateFixture(_Cfg(gate_depth=2))
    q.voice_tmp_path_queue = _normal(2)
    assert q.synth_gate_wait_s({"type": "comment"}) > 0


def test_gate_open_below_limit():
    q = _GateFixture(_Cfg(gate_depth=2))
    q.voice_tmp_path_queue = _normal(1)
    assert q.synth_gate_wait_s({"type": "comment"}) == 0.0


def test_budget_mode_blocks_at_budget():
    """S3（2026-11-21 队列优化批次）：秒数预算模式——积压预估秒数 ≥ 预算
    → 门控（budget>0 优先于条数门限）。文本无 voice_path 降级 4 字/秒估算。"""
    q = _GateFixture(_CfgBudget(budget=10.0))
    q.voice_tmp_path_queue = _normal(3)  # 每条 content 2 字 → 下限 1s ×3 = 3s
    q.voice_tmp_path_queue[0]["content"] = "这是一条四十个字的测试内容用于估算时长假条目哦"  # 24 字 → 6s
    q.voice_tmp_path_queue[1]["content"] = "这是一条四十个字的测试内容用于估算时长假条目哦"  # 6s
    assert q.synth_gate_wait_s({"type": "comment"}) > 0  # 6+6+1 = 13s ≥ 10s


def test_budget_mode_open_below_budget():
    q = _GateFixture(_CfgBudget(budget=10.0))
    q.voice_tmp_path_queue = _normal(2)  # 1+1 = 2s < 10s
    assert q.synth_gate_wait_s({"type": "comment"}) == 0.0


def test_budget_mode_excludes_song_segments():
    """预算口径排除 song 切片段：背景流不占预算（间奏插播依赖预合成）。"""
    q = _GateFixture(_CfgBudget(budget=2.0))
    seg = {"type": "song", "song_segment": {"seq": 1}, "content": "x" * 400}
    q.voice_tmp_path_queue = [seg, {"type": "comment", "content": "c"}]
    assert q.synth_gate_wait_s({"type": "comment"}) == 0.0  # 仅 1s 普通 < 2s


def test_budget_zero_falls_back_to_depth_mode():
    """budget=0/缺省 → 回退条数门限模式（既有行为兼容）。"""
    q = _GateFixture(_CfgBudget(budget=0))
    q.voice_tmp_path_queue = _normal(3)  # 深度 3 ≥ 缺省门限 2
    assert q.synth_gate_wait_s({"type": "comment"}) > 0


class _CfgBudget(_Cfg):
    """S3 预算模式 fake：play_audio 段带 synth_gate_budget_s（无 depth 键）。"""

    def __init__(self, budget):
        super().__init__(gate_depth=None)
        self._m["play_audio"] = {"synth_gate_budget_s": budget}


def test_gate_counts_normal_entries_only():
    """深度按普通条目计：song 段=背景流不占容量（与满载分账同口径）。"""
    q = _GateFixture(_Cfg(gate_depth=2))
    q.voice_tmp_path_queue = [
        {"type": "song", "song_segment": {"seq": 0}},
        {"type": "song", "song_segment": {"seq": 1}},
        {"type": "comment"},
    ]
    assert q.synth_gate_wait_s({"type": "comment"}) == 0.0  # 普通条目仅 1 条


def _copy_seg(seq, total=6):
    """动态文案分段条目（split_group 组绑定，队尾追加语义）。"""
    return {"type": "trends_copywriting", "content": f"文案第{seq + 1}段",
            "voice_path": f"copy_{seq}.mp3",
            "split_group": {"id": "g1", "seq": seq, "total": total}}


def test_gate_excludes_copy_segments_depth_mode():
    """2026-11-24 用户实测根修（深度模式）：文案分段积压不占门控深度——
    song 请求消息（权重 300 排队首）不被文案音频积压滞留在合成门。此前
    门控按普通条目计文案段 → song 消息等到文案全部消费完才合成=实测
    「文案播完歌才起」。文案让位任何消息在合成链路对齐出队层让位。"""
    q = _GateFixture(_Cfg(gate_depth=2))
    q.voice_tmp_path_queue = [_copy_seg(0), _copy_seg(1), _copy_seg(2),
                              _copy_seg(3), _copy_seg(4), _copy_seg(5)]
    assert q.synth_gate_wait_s({"type": "song", "content": "牧马城市"}) == 0.0
    assert q.synth_gate_wait_s({"type": "comment"}) == 0.0


def test_gate_excludes_copy_segments_budget_mode():
    """2026-11-24 根修（预算模式）：文案段不占秒数预算——大段文案积压
    不滞留其它消息合成。"""
    q = _GateFixture(_CfgBudget(budget=2.0))
    segs = []
    for i in range(6):
        s = _copy_seg(i)
        s["content"] = "这是一条四十个字的测试内容用于估算时长假条目哦"  # 24 字 → 6s
        segs.append(s)
    q.voice_tmp_path_queue = segs
    assert q.synth_gate_wait_s({"type": "song", "content": "牧马城市"}) == 0.0


def test_gate_still_counts_normal_with_copy_present():
    """文案段排除但普通条目仍计数：门控对普通积压的原语义保持。"""
    q = _GateFixture(_Cfg(gate_depth=2))
    q.voice_tmp_path_queue = [_copy_seg(0), {"type": "comment"},
                              {"type": "comment"}]
    assert q.synth_gate_wait_s({"type": "comment"}) > 0  # 普通条目 2 条达门限


def test_gate_disabled_when_depth_zero():
    """synth_gate_depth ≤0 → 门禁用（直通旧行为）。"""
    q = _GateFixture(_Cfg(gate_depth=0))
    q.voice_tmp_path_queue = _normal(5)
    assert q.synth_gate_wait_s({"type": "comment"}) == 0.0


def test_gate_default_when_config_missing():
    """配置缺省 → 默认门限 2（当前生产 config 显式 2，此处守卫缺省语义）。"""
    q = _GateFixture(_Cfg(gate_depth=None))
    q.voice_tmp_path_queue = _normal(2)
    assert q.synth_gate_wait_s({"type": "comment"}) > 0


def test_min_start_play_interlock():
    """首播阈值可达性防御：min_start_play=4 > 门限 2 → 有效门限取 4（否则
    高压下待合成队列永不清空 → E2 flush 不触发 → 永不开播）。"""
    q = _GateFixture(_Cfg(gate_depth=2, min_start=4))
    q.voice_tmp_path_queue = _normal(3)
    assert q.synth_gate_wait_s({"type": "comment"}) == 0.0  # 3 < 有效门限 4
    q.voice_tmp_path_queue = _normal(4)
    assert q.synth_gate_wait_s({"type": "comment"}) > 0


# ------------------------------------------------- 消费回执唤醒

def test_notify_wakes_gate_waiter():
    """消费出队回执提前唤醒门控等待（不等 1s 轮询超时）。"""
    q = _GateFixture(_Cfg(gate_depth=2))
    done = threading.Event()

    def waiter():
        q.synth_gate_sleep(QueueMixin._SYNTH_GATE_POLL_S)
        done.set()

    t = threading.Thread(target=waiter, daemon=True)
    t.start()
    time.sleep(0.1)
    start = time.monotonic()
    q.notify_synth_gate()
    assert done.wait(timeout=2.0)
    assert time.monotonic() - start < 1.0  # 提前唤醒而非等满超时


def test_notify_no_waiter_noop():
    """无等待者时 notify 不抛不挂（消费链安全）。"""
    q = _GateFixture(_Cfg())
    q.notify_synth_gate()
