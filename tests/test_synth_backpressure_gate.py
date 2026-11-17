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


def test_gate_counts_normal_entries_only():
    """深度按普通条目计：song 段=背景流不占容量（与满载分账同口径）。"""
    q = _GateFixture(_Cfg(gate_depth=2))
    q.voice_tmp_path_queue = [
        {"type": "song", "song_segment": {"seq": 0}},
        {"type": "song", "song_segment": {"seq": 1}},
        {"type": "comment"},
    ]
    assert q.synth_gate_wait_s({"type": "comment"}) == 0.0  # 普通条目仅 1 条


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
