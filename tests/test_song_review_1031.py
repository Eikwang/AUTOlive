# -*- coding: UTF-8 -*-
"""
点歌模式审查批次守卫测试（2026-10-31）

任务书落地项：
1. 退役三参数：choose_song.song_cooldown_s（同歌冷却间隔）/
   choose_song.reply_cooldown_s（弹幕回应节流）/ choose_song.prepare_copy
   （准备文案）——UI/后端消费/features.json 模板全链移除，
   _RETIRED_CONFIG_KEYS 登记。
2. 礼物点歌开关（choose_song.gift_song_enable）：开启=关键词点歌命中仅回应
   引导文案（不播放）、礼物命中映射即播报+播放；关闭=关键词直点歌（播报
   匹配成功文案进播放流程）、礼物仅答谢。礼物无映射→答谢照常不静默丢弃。
3. 歌曲列表扩展：映射表歌名 ∪ covers/ 翻唱成品 ∪ song/ 曲源——翻唱产物
   自动进入点歌列表。
4. 一键翻唱：前端上传歌曲+一键翻唱控件；main 侧 /cover/enqueue 端点。
5. 干声/伴奏协同：干声推数字人口型（EDTalk）、伴奏本地外放（backing_volume），
   数字人未激活时本地整轨/混音播放。
"""
import json
import os

import pytest

from utils.handlers.feature_handler import FeatureHandler

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

RETIRED_KEYS = ("song_cooldown_s", "reply_cooldown_s", "prepare_copy")


def _read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


def _make_handler(config_data):
    from unittest.mock import MagicMock

    class _FakeConfig:
        def __init__(self, data):
            self._data = data

        def get(self, *keys):
            node = self._data
            for k in keys:
                if not isinstance(node, dict):
                    return None
                node = node.get(k)
            return node

    mh = MagicMock()
    mh.config = _FakeConfig(config_data)
    mh.common.merge_consecutive_asterisks = lambda s: s
    return FeatureHandler(mh)


BASE_CFG = {
    "choose_song": {
        "enable": True,
        "similarity": 0.5,
        "start_cmd": ["点歌", "主播会唱"],
        "match_fail_copy": "抱歉，我还没学会唱{content}",
        "match_success_copy": "{user} 点了一首《{content}》",
        "guide_copy": "打赏个{gift}我就唱给你听",
        "gift_song_enable": True,
        "gift_song_map": {"小火箭": "晴天"},
        "backing_volume": 0.8,
    },
    "assistant_anchor": {"type": [], "username": "小助"},
}


# ---------- 1. 退役三参数：零消费+登记 ----------

class TestRetiredParamsGone:
    @pytest.mark.parametrize("rel", [
        "utils/handlers/feature_handler.py",
        "utils/keyword_hub.py",
        "frontend/ui/tabs/choose_song.py",
        "utils/cover_worker.py",
        "utils/audio/synthesis_manager.py",
    ])
    def test_no_backend_consumers(self, rel):
        src = _read(rel)
        for key in RETIRED_KEYS:
            assert f'"{key}"' not in src, f"{rel} 残留退役键 {key}"
        # 退役方法一并删除（防复活）
        fh_src = _read("utils/handlers/feature_handler.py")
        assert "_song_cooldown_check" not in fh_src
        assert "_song_reply_throttled" not in fh_src
        assert "_SONG_PLAY_COOLDOWN_DEFAULT_S" not in fh_src
        assert "_SONG_REPLY_DEFAULT_COOLDOWN_S" not in fh_src

    def test_retired_keys_registered_in_main(self):
        src = _read("frontend/main.py")
        for key in RETIRED_KEYS:
            assert f'("choose_song", "{key}")' in src, f"_RETIRED_CONFIG_KEYS 缺 choose_song.{key}"

    def test_features_json_shape(self):
        node = json.loads(_read("config/features/features.json"))["choose_song"]
        for key in RETIRED_KEYS:
            assert key not in node
        for need in ("enable", "similarity", "start_cmd", "match_fail_copy",
                     "match_success_copy", "guide_copy", "gift_song_enable",
                     "gift_song_map", "backing_volume"):
            assert need in node, need


# ---------- 2. 礼物点歌开关（gift_song_enable 分叉） ----------

class TestGiftSongEnableGate:
    def _handler_with_sent(self, cfg):
        h = _make_handler(cfg)
        sent = []
        h.my_handle.audio_synthesis_handle = lambda m: sent.append(m)
        h.my_handle.webui_show_chat_log_callback = lambda *a: sent.append(("log", a))
        return h, sent

    def test_keyword_matched_gate_on_guide_reply(self):
        """开启礼物点歌：关键词命中→仅回应引导文案，不进播放/翻唱。"""
        h, sent = self._handler_with_sent(BASE_CFG)
        assert h.choose_song_handle({"username": "小明", "content": "点歌晴天"}) is True
        assert ("enqueue", ("小明", "晴天")) not in sent
        assert not any(isinstance(m, dict) and m.get("type") == "song" for m in sent)
        assert any(isinstance(m, dict) and "打赏" in m.get("content", "") for m in sent)

    def test_keyword_matched_gate_off_direct_play(self):
        """关闭礼物点歌：关键词命中→播匹配成功文案+入队播放流程（免费点歌）。"""
        cfg = dict(BASE_CFG)
        cfg["choose_song"] = dict(BASE_CFG["choose_song"], gift_song_enable=False)
        h, sent = self._handler_with_sent(cfg)
        h._song_enqueue_cover = lambda *a: sent.append(("enqueue", a))
        assert h.choose_song_handle({"username": "小明", "content": "点歌晴天"}) is True
        texts = [m.get("content", "") for m in sent if isinstance(m, dict)]
        assert any("小明" in t and "晴天" in t for t in texts)  # 匹配成功文案
        assert ("enqueue", ("小明", "晴天")) in sent

    def test_gift_trigger_gate_off(self, tmp_path, monkeypatch):
        """关闭礼物点歌：礼物命中映射也不触发点歌（仅答谢），返回 False。"""
        monkeypatch.setattr("utils.autolive_paths.SONG_AUDIO_DIR_DEFAULT", str(tmp_path))
        cfg = dict(BASE_CFG)
        cfg["choose_song"] = dict(BASE_CFG["choose_song"], gift_song_enable=False)
        h, sent = self._handler_with_sent(cfg)
        assert h.choose_song_gift_trigger(
            {"username": "小明", "gift_name": "小火箭"}) is False
        assert sent == []

    def test_gift_trigger_gate_on_plays(self, tmp_path, monkeypatch):
        """开启礼物点歌：礼物命中映射→播报+入队播放（答谢照旧由调用方保证）。"""
        monkeypatch.setattr("utils.autolive_paths.SONG_AUDIO_DIR_DEFAULT", str(tmp_path))
        h, sent = self._handler_with_sent(BASE_CFG)
        h._song_enqueue_cover = lambda *a: sent.append(("enqueue", a))
        assert h.choose_song_gift_trigger(
            {"username": "小明", "gift_name": "小火箭"}) is True
        texts = [m.get("content", "") for m in sent if isinstance(m, dict)]
        assert any("小明" in t and "晴天" in t for t in texts)  # match_success_copy
        assert ("enqueue", ("小明", "晴天")) in sent

    def test_unmapped_gift_falls_through(self, tmp_path, monkeypatch):
        """礼物无映射歌曲→返回 False，答谢文案照常触发（不静默丢弃点歌语义）。"""
        monkeypatch.setattr("utils.autolive_paths.SONG_AUDIO_DIR_DEFAULT", str(tmp_path))
        h, sent = self._handler_with_sent(BASE_CFG)
        assert h.choose_song_gift_trigger(
            {"username": "小明", "gift_name": "爱心"}) is False

    def test_hub_match_gift_song_gated(self):
        """识别中枢 _match_gift_song 受 gift_song_enable 门控。"""
        from utils.keyword_hub import KeywordHub
        from unittest.mock import MagicMock

        def _hub(cfg):
            mh = MagicMock()
            mh.config = _DictConfig(cfg)
            hub = KeywordHub.__new__(KeywordHub)
            hub.my_handle = mh
            return hub

        on = _hub({"choose_song": {"enable": True, "gift_song_enable": True,
                                   "gift_song_map": {"小火箭": "晴天"}}})
        assert on._match_gift_song("小火箭") == "晴天"
        off = _hub({"choose_song": {"enable": True, "gift_song_enable": False,
                                    "gift_song_map": {"小火箭": "晴天"}}})
        assert off._match_gift_song("小火箭") == ""
        missing = _hub({"choose_song": {"enable": True,
                                        "gift_song_map": {"小火箭": "晴天"}}})
        assert missing._match_gift_song("小火箭") == ""


class _DictConfig:
    def __init__(self, data):
        self._data = data

    def get(self, *keys):
        node = self._data
        for k in keys:
            if not isinstance(node, dict):
                return None
            node = node.get(k)
        return node


# ---------- 3. 歌曲列表：翻唱产物/曲源自动进入 ----------

class TestSongListSources:
    def test_covers_and_song_root_auto_listed(self, tmp_path, monkeypatch):
        """翻唱成品（剥 __profile 后缀）与 song/ 曲源自动进入点歌列表。"""
        monkeypatch.setattr("utils.autolive_paths.SONG_AUDIO_DIR_DEFAULT", str(tmp_path))
        (tmp_path / "covers").mkdir()
        (tmp_path / "covers" / "晴天__p1.wav").write_bytes(b"x")
        (tmp_path / "covers" / "晴天__p1_vocal.wav").write_bytes(b"v")
        (tmp_path / "covers" / "晴天__p1_inst.wav").write_bytes(b"i")
        (tmp_path / "孤勇者.mp3").write_bytes(b"x")
        (tmp_path / "notes.txt").write_text("x", encoding="utf-8")
        h = _make_handler(BASE_CFG)
        names = h._song_song_names()
        assert "晴天" in names and "孤勇者" in names
        assert "晴天__p1" not in names and "晴天__p1_vocal" not in names
        assert "notes" not in names
        # 映射表歌名也在列表（去重合并）
        assert "晴天" in names  # 映射表与 covers 同名合一

    def test_missing_root_falls_back_to_mapping(self, tmp_path, monkeypatch):
        monkeypatch.setattr("utils.autolive_paths.SONG_AUDIO_DIR_DEFAULT",
                            str(tmp_path / "nonexistent"))
        h = _make_handler(BASE_CFG)
        assert h._song_song_names() == ["晴天"]


# ---------- 4. 一键翻唱：前端控件 + main 端点 ----------

class TestOneClickCover:
    def test_frontend_has_upload_and_enqueue_ui(self):
        src = _read("frontend/ui/tabs/choose_song.py")
        assert "ui.upload" in src and "accept=.mp3,.wav,.flac" in src
        assert "一键翻唱" in src
        assert "/cover/enqueue" in src

    def test_web_server_enqueue_endpoint(self):
        src = _read("utils/web_server.py")
        assert '"/cover/enqueue"' in src
        assert "cover_enqueue" in src
        # 入队后 ensure_worker 拉起 worker，on_done 与弹幕点歌同路
        assert "ensure_worker" in src
        assert "on_done" in src


# ---------- 5. 干声/伴奏协同（R6 双轨） ----------

class TestDualTrackPlayback:
    def test_edtalk_route_backing_local_play(self):
        """EDTalk 分支：人声推数字人口型，伴奏本地外放（playback_manager）。"""
        src = _read("utils/audio/playback_manager.py")
        assert "_play_backing_local" in src
        assert "backing_volume" in src
        # song 条目带伴奏时本地外放线程
        assert 'name="song-backing"' in src

    def test_synthesis_manager_backing_passthrough(self):
        src = _read("utils/audio/synthesis_manager.py")
        assert "backing_path" in src and "backing_volume" in src

    def test_announce_then_play_uses_split_tracks(self):
        """命中翻唱成品：分轨齐全→人声轨+伴奏轨（R6 主方案）；缺失→整轨降级。"""
        src = _read("utils/handlers/feature_handler.py")
        assert "(vocal, inst) if vocal else (cover_full, None)" in src

    def test_song_message_carries_backing_volume(self):
        h = _make_handler(BASE_CFG)
        sent = []
        h.my_handle.audio_synthesis_handle = lambda m: sent.append(m)
        h._song_play_message("小明", "vocal.wav", "inst.wav", "晴天")
        msg = sent[0]
        assert msg["type"] == "song"
        assert msg["voice_path"] == "vocal.wav" and msg["backing_path"] == "inst.wav"
        assert msg["backing_volume"] == 0.8

    def test_no_prep_reply_before_play(self, tmp_path, monkeypatch):
        """退役验收：无成品场景=播报匹配成功文案后静默入队，无第二次播报。"""
        monkeypatch.setattr("utils.autolive_paths.SONG_AUDIO_DIR_DEFAULT", str(tmp_path))
        h = _make_handler(BASE_CFG)
        sent = []
        h.my_handle.audio_synthesis_handle = lambda m: sent.append(m)
        h.my_handle.webui_show_chat_log_callback = lambda *a: None
        h._song_enqueue_cover = lambda *a: None
        assert h.choose_song_gift_trigger(
            {"username": "小明", "gift_name": "小火箭"}) is True
        dict_msgs = [m for m in sent if isinstance(m, dict)]
        assert len(dict_msgs) == 1  # 仅匹配成功文案一次播报（准备文案已退役）
