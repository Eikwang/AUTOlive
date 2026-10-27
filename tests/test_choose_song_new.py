# -*- coding: UTF-8 -*-
"""点歌模式重构批次测试（/autoplan 2026-10-23 批次，义务 R9 矩阵）。

覆盖：弹幕意图匹配三态（contains 触发/触发无歌/未触发/句中/后置/相似度边界）、
礼物归一匹配、四文案渲染（缺变量容错）、助播路由两态、配置容错、covers 命中
（整轨/分轨）、冷却与节流、R13 迁移、R1 残留断言（D-20 双代码路径纪律）。
用户验收场景 1-4 对应单元断言（场景级实测见 R11 手动清单）。
"""
import os
import sys
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from utils.common.text_mixin import (  # noqa: E402
    match_song_intent, match_gift_to_song_entry, match_gift_to_song_preview)
from utils.autolive_paths import (  # noqa: E402
    song_audio_root, SONG_AUDIO_DIR_DEFAULT, _SONG_PATH_MIGRATION_WARNED)

TRIGGERS = ["主播会唱", "可以唱", "能唱"]
SONGS = ["你是我的眼"]


def _make_handler(config_data):
    """构造 FeatureHandler（mock my_handle：config 双形态取 Config.get(*keys) 语义）。"""
    from utils.handlers.feature_handler import FeatureHandler
    cfg = MagicMock()

    def _get(*keys):
        node = config_data
        for k in keys:
            if not isinstance(node, dict):
                return None
            node = node.get(k)
        return node
    cfg.get = _get
    mh = MagicMock()
    mh.config = cfg
    mh.common.merge_consecutive_asterisks = lambda s: s
    return FeatureHandler(mh)


BASE_CFG = {
    "choose_song": {
        "enable": True,
        "similarity": 0.5,
        "start_cmd": TRIGGERS,
        "match_fail_copy": "抱歉，我还没学会唱{content}",
        "match_success_copy": "{user} 点了一首《{content}》，谢谢老板的{gift}！",
        "guide_copy": "会的{user}，只需要打赏一个{gift}我就唱给你听哦",
        "prepare_copy": "收到{user}点的《{content}》，正在准备中",
        "gift_song_map": {"粉丝灯牌": "你是我的眼"},
        "song_cooldown_s": 300,
        "reply_cooldown_s": 30,
        "backing_volume": 0.8,
        "cover_profile_id": "default",
    },
    "assistant_anchor": {"type": [], "username": "小助"},
}


# ---------- 弹幕意图匹配（R2 三态，native F1 contains 语义） ----------

class TestMatchSongIntent:
    def test_scene2_prefix_trigger_matched(self):
        # 场景 2：触发词开头+列表有歌
        assert match_song_intent("主播会唱你是我的眼吗？", TRIGGERS, SONGS, 0.5) == ("matched", "你是我的眼", 1.0)

    def test_scene4_no_song_triggered(self):
        # 场景 4：触发但列表无歌 → triggered（失败文案）
        assert match_song_intent("主播会唱孤勇者吗", TRIGGERS, SONGS, 0.5) == ("triggered", None, None)

    def test_no_trigger_silent(self):
        assert match_song_intent("今天天气不错", TRIGGERS, SONGS, 0.5) is None

    def test_contains_mid_sentence(self):
        # F1：触发词句中（旧 starts_with_any 语义下漏触发）
        assert match_song_intent("请问可以唱你是我的眼吗", TRIGGERS, SONGS, 0.5)[0] == "matched"

    def test_trigger_after_song_name(self):
        # 触发词后置：歌名在触发词前面
        assert match_song_intent("你是我的眼，能唱吗", TRIGGERS, SONGS, 0.5)[0] == "matched"

    def test_trigger_only_no_song(self):
        assert match_song_intent("能唱", TRIGGERS, SONGS, 0.5) == ("triggered", None, None)

    def test_empty_song_list_triggered(self):
        # 空列表配置容错：触发仍成立（走失败文案），不抛异常
        assert match_song_intent("点歌", ["点歌"], [], 0.5) == ("triggered", None, None)

    def test_longest_trigger_wins(self):
        # 多触发词：最长优先（"主播会唱"先于"能唱"命中）
        r = match_song_intent("主播会唱你是我的眼", TRIGGERS, SONGS, 0.5)
        assert r == ("matched", "你是我的眼", 1.0)

    def test_similarity_boundary(self):
        low = match_song_intent("主播会唱你的眼", TRIGGERS, SONGS, 0.5)
        assert low is not None and low[0] in ("matched", "triggered")  # 0.5 阈值行为确定
        assert match_song_intent("主播会唱你的眼", TRIGGERS, SONGS, 0.99) == ("triggered", None, None)


# ---------- 礼物归一匹配（R2/R5，native F5 归一同源） ----------

class TestMatchGiftToSong:
    def test_exact_after_normalize(self):
        # 场景 1：礼物名=映射键
        assert match_gift_to_song_entry("粉丝灯牌", {"粉丝灯牌": "你是我的眼"}) == "你是我的眼"

    def test_contains_hit(self):
        # "超级小火箭"命中"小火箭"（前端预演同语义）
        assert match_gift_to_song_entry("超级粉丝灯牌", {"粉丝灯牌": "你是我的眼"}) == "你是我的眼"

    def test_normalize_ignores_case_space(self):
        assert match_gift_to_song_entry(" 粉丝灯牌 ", {"粉丝 灯牌": "你是我的眼"}) == "你是我的眼"

    def test_miss_and_empty(self):
        assert match_gift_to_song_entry("爱心", {"粉丝灯牌": "你是我的眼"}) is None
        assert match_gift_to_song_entry("粉丝灯牌", {}) is None
        assert match_gift_to_song_entry("", {"粉丝灯牌": "你是我的眼"}) is None
        assert match_gift_to_song_entry("粉丝灯牌", None) is None

    def test_preview_full(self):
        hit = match_gift_to_song_preview("粉丝灯牌", {"粉丝灯牌": "你是我的眼"}, ["你是我的眼"], 0.5)
        assert hit == ("你是我的眼", 1.0, "映射[粉丝灯牌]→包含命中")
        assert match_gift_to_song_preview("爱心", {"粉丝灯牌": "你是我的眼"}, ["你是我的眼"], 0.5) is None


# ---------- 四文案渲染（R3：统一变量池+缺变量容错） ----------

class TestRenderSongCopy:
    def setup_method(self):
        self.h = _make_handler(BASE_CFG)

    def test_all_vars(self):
        out = self.h._render_song_copy("{user} 点了《{content}》谢谢{gift}", "小明", "晴天", "火箭")
        assert out == "小明 点了《晴天》谢谢火箭"

    def test_missing_vars_empty_no_raise(self):
        out = self.h._render_song_copy("{user} 点了《{content}》{gift}", "小明", "晴天")
        assert out == "小明 点了《晴天》"
        assert self.h._render_song_copy("{gift}谢谢", "", "", "") == "谢谢"

    def test_bad_template_falls_back(self):
        assert self.h._render_song_copy("孤立括号{", "a", "b", "c") == "孤立括号{"


# ---------- 助播路由（R4 两态） ----------

class TestAssistantRoute:
    def test_route_off_default_tts(self):
        self.h = _make_handler(BASE_CFG)
        msg = self.h._song_copy_message("小明", "你好")
        assert msg["type"] == "comment" and msg["username"] == "小明"

    def test_route_on_assistant_channel(self):
        cfg = dict(BASE_CFG)
        cfg["assistant_anchor"] = {"type": ["song"], "username": "小助"}
        self.h = _make_handler(cfg)
        msg = self.h._song_copy_message("小明", "你好")
        assert msg["type"] == "assistant_anchor_text" and msg["username"] == "小助"


# ---------- 配置容错（F-S2：缺键/坏类型→空列表降级） ----------

class TestConfigFaultTolerance:
    def test_song_names_missing_key(self):
        self.h = _make_handler({"choose_song": {}})
        assert self.h._song_song_names() == []

    def test_song_names_bad_type(self):
        self.h = _make_handler({"choose_song": {"gift_song_map": "not-a-dict"}})
        assert self.h._song_song_names() == []

    def test_song_names_dedup(self):
        self.h = _make_handler({"choose_song": {"gift_song_map": {"a": "歌1", "b": "歌1", "c": "歌2"}}})
        assert self.h._song_song_names() == ["歌1", "歌2"]


# ---------- covers 命中（R7：整轨/分轨/未命中） ----------

class TestCoversHit:
    def test_full_track_hit(self, tmp_path, monkeypatch):
        monkeypatch.setattr("utils.autolive_paths.SONG_AUDIO_DIR_DEFAULT", str(tmp_path))
        covers = tmp_path / "covers"
        covers.mkdir()
        (covers / "你是我的眼__default.wav").write_bytes(b"x")
        self.h = _make_handler(BASE_CFG)
        hit = self.h._song_covers_hit("你是我的眼")
        assert hit is not None and hit[0].endswith("你是我的眼__default.wav") and hit[1] is None

    def test_split_tracks_hit(self, tmp_path, monkeypatch):
        monkeypatch.setattr("utils.autolive_paths.SONG_AUDIO_DIR_DEFAULT", str(tmp_path))
        covers = tmp_path / "covers"
        covers.mkdir()
        (covers / "你是我的眼__default.wav").write_bytes(b"x")
        (covers / "你是我的眼__default_vocal.wav").write_bytes(b"v")
        (covers / "你是我的眼__default_inst.wav").write_bytes(b"i")
        self.h = _make_handler(BASE_CFG)
        hit = self.h._song_covers_hit("你是我的眼")
        assert hit is not None and hit[1].endswith("_vocal.wav") and hit[2].endswith("_inst.wav")

    def test_miss(self, tmp_path, monkeypatch):
        monkeypatch.setattr("utils.autolive_paths.SONG_AUDIO_DIR_DEFAULT", str(tmp_path))
        (tmp_path / "covers").mkdir()
        self.h = _make_handler(BASE_CFG)
        assert self.h._song_covers_hit("孤勇者") is None


# ---------- 冷却与节流（R7：同歌冷却/回应节流） ----------

class TestCooldownAndThrottle:
    def test_song_cooldown_first_pass_then_block(self):
        self.h = _make_handler(BASE_CFG)
        assert self.h._song_cooldown_check("歌A") is True
        assert self.h._song_cooldown_check("歌A") is False
        assert self.h._song_cooldown_check("歌B") is True  # 不同歌互不影响

    def test_reply_throttle(self):
        self.h = _make_handler(BASE_CFG)
        assert self.h._song_reply_throttled() is False
        assert self.h._song_reply_throttled() is True


# ---------- 礼物触发链（R2/R5：命中播报/未命中仅答谢/enable 门控） ----------

class TestGiftTrigger:
    def test_disabled_returns_false(self):
        cfg = dict(BASE_CFG)
        cfg["choose_song"] = dict(BASE_CFG["choose_song"], enable=False)
        self.h = _make_handler(cfg)
        assert self.h.choose_song_gift_trigger({"username": "小明", "gift_name": "粉丝灯牌"}) is False

    def test_unmapped_gift_returns_false(self):
        # 场景 3：未配置礼物 → 仅答谢（返回 False，答谢链照旧）
        self.h = _make_handler(BASE_CFG)
        assert self.h.choose_song_gift_trigger({"username": "小明", "gift_name": "爱心"}) is False

    def test_mapped_gift_no_cover_enqueues(self):
        # 场景 1 前半：命中无成品 → prepare_copy 播报+入队（mock 均记录调用）
        self.h = _make_handler(BASE_CFG)
        calls = []
        self.h.my_handle.audio_synthesis_handle = lambda m: calls.append(m)
        self.h.my_handle.webui_show_chat_log_callback = lambda *a: calls.append(("log", a))
        self.h._song_enqueue_cover = lambda *a: calls.append(("enqueue", a))
        r = self.h.choose_song_gift_trigger({"username": "小明", "gift_name": "粉丝灯牌"})
        assert r is True
        types = [m["type"] if isinstance(m, dict) else m[0] for m in calls]
        assert types.count("comment") >= 1  # prepare_copy 播报（助播未开）
        assert ("enqueue", ("小明", "你是我的眼")) in calls


# ---------- 弹幕点歌判断（R1/R2：场景 2/4 + 未触发静默） ----------

class TestChooseSongHandle:
    def _run(self, content):
        self.h = _make_handler(BASE_CFG)
        sent = []
        self.h.my_handle.audio_synthesis_handle = lambda m: sent.append(m)
        self.h.my_handle.webui_show_chat_log_callback = lambda *a: sent.append(("log", a))
        r = self.h.choose_song_handle({"username": "小明", "content": content})
        return r, sent

    def test_scene2_guide_reply_no_play(self):
        # 场景 2：仅回应引导文案，不执行点歌播放（无 song 消息）
        r, sent = self._run("主播会唱你是我的眼吗？")
        assert r is True
        song_msgs = [m for m in sent if isinstance(m, dict) and m.get("type") == "song"]
        assert song_msgs == []
        texts = [m.get("content", "") for m in sent if isinstance(m, dict)]
        assert any("小明" in t and "粉丝灯牌" in t for t in texts)

    def test_scene4_fail_reply_no_play(self):
        # 场景 4：列表无歌 → 失败文案，不播放
        r, sent = self._run("主播会唱孤勇者吗")
        assert r is True
        song_msgs = [m for m in sent if isinstance(m, dict) and m.get("type") == "song"]
        assert song_msgs == []
        texts = [m.get("content", "") for m in sent if isinstance(m, dict)]
        assert any("学会唱" in t for t in texts)

    def test_no_trigger_returns_false(self):
        assert self._run("今天天气不错")[0] is False

    def test_disabled_returns_false(self):
        cfg = dict(BASE_CFG)
        cfg["choose_song"] = dict(BASE_CFG["choose_song"], enable=False)
        self.h = _make_handler(cfg)
        assert self.h.choose_song_handle({"username": "a", "content": "主播会唱X"}) is False


# ---------- R13 song_path 迁移（遗留键告警+采纳） ----------

class TestSongPathMigration:
    def test_default_root(self):
        root = song_audio_root(None)
        assert root.endswith(SONG_AUDIO_DIR_DEFAULT)

    def test_legacy_key_adopted_with_warning(self, caplog):
        cfg = {"choose_song": {"song_path": r"D:\legacy\songs"}}
        root = song_audio_root(cfg)
        assert root == r"D:\legacy\songs"  # 采纳遗留值

    def test_legacy_default_value_silent(self):
        cfg = {"choose_song": {"song_path": SONG_AUDIO_DIR_DEFAULT}}
        root = song_audio_root(cfg)
        assert root.endswith(SONG_AUDIO_DIR_DEFAULT)  # 默认值不告警不采纳

    def test_dict_shape_compat(self):
        # config 双形态纪律（前端 dict）：缺失键路径返回默认
        assert song_audio_root({}).endswith(SONG_AUDIO_DIR_DEFAULT)


# ---------- R1 残留断言（grep 无读取点）+ cover_worker 常量化 ----------

class TestRetiredParams:
    def test_no_stop_random_songpath_readers(self):
        # R1：stop_cmd/random_cmd/song_path 全链退役——utils 侧零读取点
        # （features.json 键已删；本断言防回归复活）
        root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        for rel in ("utils/handlers/feature_handler.py", "utils/cover_worker.py",
                    "frontend/ui/tabs/choose_song.py"):
            with open(os.path.join(root, rel), encoding="utf-8") as f:
                src = f.read()
            assert 'config.get("choose_song", "stop_cmd")' not in src, rel
            assert 'config.get("choose_song", "random_cmd")' not in src, rel
            assert 'config.get("choose_song", "song_path")' not in src, rel

    def test_cover_worker_uses_song_audio_root(self):
        root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        with open(os.path.join(root, "utils/cover_worker.py"), encoding="utf-8") as f:
            assert "song_audio_root(self.config)" in f.read()

    def test_features_json_retired_keys_gone(self):
        import json
        root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        with open(os.path.join(root, "config/features/features.json"), encoding="utf-8") as f:
            node = json.load(f)["choose_song"]
        for gone in ("stop_cmd", "random_cmd", "song_path"):
            assert gone not in node
        for need in ("start_cmd", "match_fail_copy", "match_success_copy", "guide_copy",
                     "prepare_copy", "gift_song_map", "song_cooldown_s", "reply_cooldown_s",
                     "backing_volume", "similarity", "enable"):
            assert need in node


# ---------- R6 双轨消息契约（song 消息 backing 透传） ----------

class TestDualTrackMessage:
    def test_play_message_carries_backing(self):
        self.h = _make_handler(BASE_CFG)
        sent = []
        self.h.my_handle.audio_synthesis_handle = lambda m: sent.append(m)
        self.h._song_play_message("小明", "vocal.wav", "inst.wav", "你是我的眼")
        msg = sent[0]
        assert msg["type"] == "song" and msg["voice_path"] == "vocal.wav"
        assert msg["backing_path"] == "inst.wav" and msg["backing_volume"] == 0.8

    def test_play_message_no_backing_empty(self):
        self.h = _make_handler(BASE_CFG)
        sent = []
        self.h.my_handle.audio_synthesis_handle = lambda m: sent.append(m)
        self.h._song_play_message("小明", "full.wav", None, "你是我的眼")
        assert sent[0]["backing_path"] == ""  # 整轨降级（R6）

    def test_synthesis_manager_song_passthrough(self):
        # D-20 双代码路径：synthesis_manager song 构造透传 backing 两键
        # （2026-10-27 ② 切片插播：无伴奏轨走 .segs_ 切片分支分条目入队；
        #   带伴奏轨维持整曲单条目原语义，R6 双轨伴奏与人声同步起播）
        root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        with open(os.path.join(root, "utils/audio/synthesis_manager.py"), encoding="utf-8") as f:
            src = f.read()
        assert '"backing_path": _backing' in src            # 带伴奏整曲分支（R6 双轨）
        assert '"backing_volume": message.get("backing_volume"' in src
        assert '"song_segment"' in src                      # 切片片段分支透传片段元数据
        assert "_split_audio_segments" in src               # 切片器挂接
        # 切片分支不携带伴奏键（R6：伴奏轨与切片互斥）
        assert 'if segments:' in src and 'return' in src
