# -*- coding: utf-8 -*-
"""S2 切分组完整性保护回归（2026-11-26 log9 实证根修）。

症状：点歌 hold 期间弹幕 LLM 回复切分为 7 段，段 1 经间奏借位播出后，
段 2-5 滞留待播放队列；段 6/7 合成完成入队时播放队列普通容量满（4/4），
「同权重取代组首最老条目」把段 2/3 逐出——组内自相蚕食，回复只播出部分。

根因：满载驱逐候选不识别 split_group 组身份，且聊天源（chatbox）回复无
reply_link，S1 遗弃保护不覆盖——同组新段把自己的老段当独立同权重条目驱逐。

修复（三层）：
1. 同组不互逐：规则2/3/队尾驱逐/F1 顶替候选排除与新条目同 split_group 的条目；
2. 组活跃保护：组任一段出队（合成侧/消费侧）登记活跃（_split_group_active），
   活跃组段满载不丢不驱逐、入队豁免接组尾；点歌 hold 期驱逐候选不保护
   （唱歌期回复组遵循「顶替-保留最新」最新优先语义）；
3. 组尾保序兜底：规则2/3 无候选且队内存在同组段时，新段按 seq 保序接组尾
   （超限豁免，消费后回落），不再触发规则4丢弃/顶替自残。
"""
import threading

import pytest

from utils.audio.queue_manager import QueueMixin


class _FakeConfig:
    def __init__(self, max_len=4, mq_max_len=4, replace_same_priority=True):
        self._data = {
            ("filter", "priority_mapping"): {t: int(t[1:]) for t in
                                             ("w100", "w90", "w60", "w10")},
            ("filter", "voice_tmp_path_queue_min_start_play"): 0,
            ("filter", "voice_tmp_path_queue_max_len"): max_len,
            ("filter", "message_queue_max_len"): mq_max_len,
            ("filter", "message_queue_replace_same_priority"): replace_same_priority,
            ("play_audio", "enable"): True,
            ("play_audio", "synth_gate_budget_s"): 15.0,
        }

    def get(self, *keys):
        return self._data.get(keys)


class _QueueFixture(QueueMixin):
    def __init__(self, config=None):
        self.message_queue = []
        self.message_queue_lock = threading.Lock()
        self.message_queue_not_empty = threading.Condition(lock=self.message_queue_lock)
        self.voice_tmp_path_queue = []
        self.voice_tmp_path_queue_lock = threading.Lock()
        self.voice_tmp_path_queue_not_empty = threading.Condition(lock=self.voice_tmp_path_queue_lock)
        self.voice_tmp_path_queue_not_empty_flag = False
        if config is not None:
            self.config = config


def _seg(gid, seq, total=7, priority_type="w100", label=None):
    """长回复切分段条目（聊天源形态：无 reply_link）"""
    return {
        "type": priority_type,
        "voice_path": f"{label or gid}-seg{seq}.wav",
        "content": f"段{seq}",
        "split_group": {"id": gid, "seq": seq, "total": total},
    }


@pytest.fixture(autouse=True)
def _reset_group_state():
    QueueMixin._reply_gate_pending.clear()
    QueueMixin._reply_gate_abandoned.clear()
    QueueMixin._reply_gate_active.clear()
    QueueMixin._split_group_active.clear()
    yield
    QueueMixin._reply_gate_pending.clear()
    QueueMixin._reply_gate_abandoned.clear()
    QueueMixin._reply_gate_active.clear()
    QueueMixin._split_group_active.clear()


class TestPlayQueueGroupIntegrity:
    """待播放队列：同组不互逐 + 组尾保序兜底（log9 场景逐段复现）"""

    def _fixture(self, max_len=4):
        return _QueueFixture(config=_FakeConfig(max_len=max_len))

    def _paths(self, fx):
        return [d["voice_path"] for d in fx.voice_tmp_path_queue]

    def test_same_group_segments_never_evict_each_other(self):
        """核心回归：队满（4/4）全为本组段时，后续同组段接组尾而非取代组首
        ——7 段回复零丢失（修复前段 6/7 经同权重取代逐出段 2/3）"""
        fx = self._fixture(max_len=4)
        for seq in range(2, 6):  # 段 2-5 在队（普通容量 4/4 满）
            fx.data_priority_insert("待播放音频列表", _seg("rc-g1", seq))
        assert len(fx.voice_tmp_path_queue) == 4
        # 段 6/7 入队：同组段被规则2候选排除 → 规则4 兜底同组保序接组尾
        assert fx.data_priority_insert(
            "待播放音频列表", _seg("rc-g1", 6))["code"] == 200
        assert fx.data_priority_insert(
            "待播放音频列表", _seg("rc-g1", 7))["code"] == 200
        # 全部 6 段存活且 seq 有序
        assert self._paths(fx) == [f"rc-g1-seg{s}.wav" for s in range(2, 8)]

    def test_active_group_member_exempt_append_when_full(self):
        """组活跃（任一段已消费出队）→ 后续段满载豁免接组尾（编组切片同款）"""
        fx = self._fixture(max_len=4)
        for seq in range(0, 4):
            fx.data_priority_insert("待播放音频列表", _seg("rc-g2", seq))
        # 段 0 消费出队 → 组活跃登记
        fx.reply_gate_track_popped(_seg("rc-g2", 0))
        r = fx.data_priority_insert("待播放音频列表", _seg("rc-g2", 4))
        assert r["code"] == 200
        assert len(fx.voice_tmp_path_queue) == 5  # 超限豁免，消费后回落
        assert self._paths(fx)[-1] == "rc-g2-seg4.wav"
        # 队内段 0-3 无一被逐
        for seq in range(0, 4):
            assert f"rc-g2-seg{seq}.wav" in self._paths(fx)

    def test_active_group_protected_from_other_group_eviction(self):
        """非点歌态：活跃组段不被其它同权重组段驱逐（无候选→新组段按规则4舍弃）"""
        fx = self._fixture(max_len=4)
        for seq in range(0, 4):
            fx.data_priority_insert("待播放音频列表", _seg("rc-old", seq))
        fx.reply_gate_track_popped(_seg("rc-old", 0))  # 旧组消费中
        r = fx.data_priority_insert("待播放音频列表", _seg("rc-new", 0))
        # 活跃旧组受保护：新组段无同权重候选、无更低优候选 → 规则4丢弃
        assert r["code"] == 1
        assert self._paths(fx) == [f"rc-old-seg{s}.wav" for s in range(0, 4)]

    def test_song_hold_old_group_yields_to_new_group(self):
        """点歌 hold 期（歌切片在队）：活跃旧组让位新组（顶替-保留最新语义）"""
        fx = self._fixture(max_len=3)  # 歌切片为背景流不占普通容量
        # 歌切片先入队（未识别类型满载会被丢弃）=点歌 hold 态
        fx.data_priority_insert("待播放音频列表", {
            "type": "song", "song_segment": True, "voice_path": "song-seg0.wav"})
        for seq in range(0, 3):
            fx.data_priority_insert("待播放音频列表", _seg("rc-old", seq))
        fx.reply_gate_track_popped(_seg("rc-old", 0))  # 旧组活跃
        r = fx.data_priority_insert("待播放音频列表", _seg("rc-new", 0))
        assert r["code"] == 200
        # 旧组组首被逐（最新优先），歌切片保留
        assert "rc-old-seg0.wav" not in self._paths(fx)
        assert "song-seg0.wav" in self._paths(fx)
        assert "rc-new-seg0.wav" in self._paths(fx)

    def test_ungrouped_same_priority_still_replaces_oldest(self):
        """无组条目语义不变：满载同权重仍取代组首最老条目"""
        fx = self._fixture(max_len=4)
        for i in range(4):
            fx.data_priority_insert("待播放音频列表", {
                "type": "w100", "voice_path": f"plain-old{i}.wav"})
        fx.data_priority_insert("待播放音频列表", {
            "type": "w100", "voice_path": "plain-new.wav"})
        assert "plain-old0.wav" not in self._paths(fx)
        assert "plain-new.wav" in self._paths(fx)

    def test_track_popped_registers_group_without_reply_link(self):
        """聊天源切分段出队（无 reply_link）同样登记组活跃（S1 盲区修复）"""
        fx = _QueueFixture(config=_FakeConfig())
        fx.reply_gate_track_popped(_seg("rc-g3", 0))
        assert "rc-g3" in QueueMixin._split_group_active


class TestMessageQueueGroupIntegrity:
    """待合成消息队列：F1 顶替同组排除 + 组尾保序兜底"""

    def _msg(self, gid, seq, total=7):
        return {
            "type": "w100",
            "content": f"段{seq}",
            "split_group": {"id": gid, "seq": seq, "total": total},
        }

    def test_same_group_tail_append_when_full(self):
        fx = _QueueFixture(config=_FakeConfig(mq_max_len=4))
        for seq in range(0, 4):
            assert fx.data_priority_insert("等待合成消息", self._msg("rc-m1", seq))["code"] == 200
        # 队满：同组新段不被顶替/丢弃 → 保序接组尾
        r = fx.data_priority_insert("等待合成消息", self._msg("rc-m1", 4))
        assert r["code"] == 200
        contents = [d["content"] for d in fx.message_queue]
        assert contents == [f"段{s}" for s in range(0, 5)]

    def test_ungrouped_still_replaced_when_full(self):
        fx = _QueueFixture(config=_FakeConfig(mq_max_len=4))
        for i in range(4):
            fx.data_priority_insert("等待合成消息", {"type": "w100", "content": f"old{i}"})
        fx.data_priority_insert("等待合成消息", {"type": "w100", "content": "new"})
        contents = [d["content"] for d in fx.message_queue]
        assert "old0" not in contents
        assert "new" in contents
        assert len(contents) == 4
