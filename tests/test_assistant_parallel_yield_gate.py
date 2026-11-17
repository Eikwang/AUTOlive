# -*- coding: utf-8 -*-
"""助播并行消费让位门守卫（2026-11-13 用户实报根修；2026-11-16 段间让位批次：
999 与 barge-in 机制均已退役，本文件守卫 d12 builtin 让位的同声道前提）。

症状：助播音频播放中与数字人音频并行消费时撕裂卡顿并多次重复。

根因（b 分支，已修并保留）：queue_manager._maybe_priority_yield_for（原
_maybe_barge_in_for，e3 泛化 2026-11-05）按「新音频权重严格高于在播条目」
触发 builtin 让位，但未区分
插播者声道——EDTalk 激活期插播者（comment 50/gift 70 等全部高于助播 40）
推数字人出声、不占本地声道，让位只会使在播助播条目陷入：
    request_yield 挂起（语句中段切断=撕裂）
    → 插播者走 EDTalk（builtin 队列无插播者）
    → builtin 队列空 _resume_suspended_999 续播回退 1s（尾段重放=重复）
    → 下一条更高权重数字人音频入队再次让位……
循环。修复=让位加同声道前提（routes_to_edtalk 单源判定）：插播者路由
EDTalk 时不让位（助播并行消费语义，2026-10-27 批次设计恢复）；EDTalk 未
激活（d12 原始场景，song 让位答谢=同声道争用）保持生效。

退役说明：原 a) 999 active 分支随 999 特殊机制退役（2026-11-15 批次）移除；
barge-in 打断即弃（2026-11-15 批次）亦于 2026-11-16 段间让位批次退役——插播
语义收敛为「切分段 + 权重排序段间让位」，判定面见 test_copy_segment_yield.py。

守卫面：
- EDTalk 激活：助播在播+任意更高权重数字人条目入队 → 零让位（b 根修）
- EDTalk 未激活：同声道让位保持（d12/e3 既有语义回归锚）
"""
import threading

from utils.audio.queue_manager import QueueMixin


class _Cfg:
    """Config 同构 fake（get(*keys) 键路行走；visual_body/edtalk_realtime
    键驱动 is_edtalk_active 判定）。"""

    def __init__(self, mapping, edtalk_active=False):
        self._m = {"filter": {"priority_mapping": mapping}}
        if edtalk_active:
            self._m["visual_body"] = "edtalk_realtime"
            self._m["edtalk_realtime"] = {"enable": True}

    def get(self, *keys):
        cur = self._m
        for k in keys:
            if not isinstance(cur, dict) or k not in cur:
                return None
            cur = cur[k]
        return cur


class _YieldPlayer:
    def __init__(self, playing_weight=None):
        self.playing_weight = playing_weight
        self.yields = 0

    def request_yield(self):
        self.yields += 1

    def get_playing_weight(self):
        return self.playing_weight


class _QueueFixture(QueueMixin):
    def __init__(self, mapping, player, edtalk_active=False):
        self.config = _Cfg(mapping, edtalk_active=edtalk_active)
        self.audio_player = player
        self.message_queue = []
        self.message_queue_lock = threading.Lock()
        self.message_queue_not_empty = threading.Condition(lock=self.message_queue_lock)
        self.voice_tmp_path_queue = []
        self.voice_tmp_path_queue_lock = threading.Lock()
        self.voice_tmp_path_queue_not_empty = threading.Condition(lock=self.voice_tmp_path_queue_lock)
        self.voice_tmp_path_queue_not_empty_flag = False


PM = {"comment": 50, "gift": 70, "read_comment": 60, "song": 80,
      "assistant_anchor_text": 40, "assistant_anchor_audio": 40,
      "reread_top_priority": 500}


# ------------------------------------------------- EDTalk 激活（跨声道）零让位

def test_edtalk_active_assistant_playing_comment_no_yield():
    """核心根修：助播(40)在播+comment(50)入队 → 不让位（插播者推数字人，
    本地声道无争用；旧行为=让位挂起→续播回退 1s 循环=撕裂卡顿重复）。"""
    q = _QueueFixture(PM, _YieldPlayer(playing_weight=40), edtalk_active=True)
    q._maybe_priority_yield_for({"type": "comment", "voice_path": "x.mp3"})
    assert q.audio_player.yields == 0


def test_edtalk_active_assistant_playing_gift_no_yield():
    q = _QueueFixture(PM, _YieldPlayer(playing_weight=40), edtalk_active=True)
    q._maybe_priority_yield_for({"type": "gift", "voice_path": "x.mp3"})  # 70>40
    assert q.audio_player.yields == 0


def test_edtalk_active_song_entry_no_yield():
    """song(80) 入队同样路由 EDTalk（人声推数字人、伴奏独立流）→ 不让位。"""
    q = _QueueFixture(PM, _YieldPlayer(playing_weight=40), edtalk_active=True)
    q._maybe_priority_yield_for({"type": "song", "voice_path": "x.mp3"})
    assert q.audio_player.yields == 0


# ------------------------------------------------- EDTalk 未激活（同声道）保持

def test_edtalk_inactive_song_yields_to_comment():
    """d12 原始场景保持：song(10) 本地整曲在播+comment(20) 本地排队 → 让位。"""
    q = _QueueFixture(PM, _YieldPlayer(playing_weight=10), edtalk_active=False)
    q._maybe_priority_yield_for({"type": "comment", "voice_path": "x.mp3"})
    assert q.audio_player.yields == 1


def test_edtalk_inactive_assistant_yields_to_comment():
    """同声道泛化语义保持：EDTalk 未激活时助播与回复同走 builtin 本地，
    更高权重回复插队让位（e3 既有行为，本修不改）。"""
    q = _QueueFixture(PM, _YieldPlayer(playing_weight=40), edtalk_active=False)
    q._maybe_priority_yield_for({"type": "comment", "voice_path": "x.mp3"})
    assert q.audio_player.yields == 1
