"""点歌本地整轨播放测试（2026-11-04 用户优化）。

背景：一键翻唱保留三产物——伴奏（_inst）/人声（_vocal）/完整歌曲
（<song>__<profile>.wav 混音成品，前端预览即播此文件）。R6 双轨只覆盖
EDTalk 激活场景（人声推数字人+伴奏本地外放）；EDTalk 未激活时原实现
data_json 重构丢弃伴奏轨、builtin 只播人声轨（清唱）。

修复：song 消息携带 full_path（完整混音成品路径）全链透传
（feature_handler → synthesis_manager → playback_manager），EDTalk 未激活
的本地分支播整轨而非清唱；整轨降级/翻唱自动插播消息本身即整轨
（full_path 空→不替换），行为不变。
"""

import inspect
from pathlib import Path
from unittest.mock import MagicMock

from utils.handlers.feature_handler import FeatureHandler
from utils.audio import playback_manager as pm
from utils.audio import synthesis_manager as sm


def _make_handler(config_data):
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
        "start_cmd": ["点歌"],
        "match_success_copy": "{user} 点了一首《{content}》",
        "backing_volume": 0.8,
    },
    "audio_synthesis_type": "gpt_sovits",
    "gpt_sovits": {},
    "filter": {},
}


# ---------- 1：消息层（feature_handler） ----------

def test_song_message_carries_full_path():
    """分轨齐全：full_path=完整混音成品随消息透传。"""
    h = _make_handler(BASE_CFG)
    sent = []
    h.my_handle.audio_synthesis_handle = lambda m: sent.append(m)
    h._song_play_message("小明", "vocal.wav", "inst.wav", "晴天", full_path="full.wav")
    msg = sent[0]
    assert msg["type"] == "song"
    assert msg["voice_path"] == "vocal.wav" and msg["backing_path"] == "inst.wav"
    assert msg["full_path"] == "full.wav"


def test_song_message_full_path_default_empty():
    """四参旧签名兼容：full_path 缺省为空串（整轨降级/既有调用面不变）。"""
    h = _make_handler(BASE_CFG)
    sent = []
    h.my_handle.audio_synthesis_handle = lambda m: sent.append(m)
    h._song_play_message("小明", "full.wav", None, "你是我的眼")
    assert sent[0]["full_path"] == ""


def test_announce_split_tracks_pass_full_path():
    """分轨命中：voice=vocal、backing=inst、full_path=整轨成品三路径齐发。"""
    h = _make_handler(BASE_CFG)
    sent = []
    h.my_handle.audio_synthesis_handle = lambda m: sent.append(m)
    h.my_handle.webui_show_chat_log_callback = lambda *a, **k: None
    h._song_covers_hit = lambda name: ("full.wav", "vocal.wav", "inst.wav")
    assert h._song_announce_and_play("小明", "晴天", "") is True
    msg = [m for m in sent if isinstance(m, dict) and m.get("type") == "song"][0]
    assert msg["voice_path"] == "vocal.wav"
    assert msg["backing_path"] == "inst.wav"
    assert msg["full_path"] == "full.wav"


def test_announce_full_track_fallback_empty_full_path():
    """整轨降级（无分轨）：audio_path 即整轨，full_path 留空（防 builtin 双声）。"""
    h = _make_handler(BASE_CFG)
    sent = []
    h.my_handle.audio_synthesis_handle = lambda m: sent.append(m)
    h.my_handle.webui_show_chat_log_callback = lambda *a, **k: None
    h._song_covers_hit = lambda name: ("full.wav", None, None)
    assert h._song_announce_and_play("小明", "晴天", "") is True
    msg = [m for m in sent if isinstance(m, dict) and m.get("type") == "song"][0]
    assert msg["voice_path"] == "full.wav"
    assert msg["backing_path"] == ""
    assert msg["full_path"] == ""


# ---------- 2：透传与播放侧（源码断言，项目既有风格） ----------

def test_synthesis_manager_full_path_passthrough():
    """合成侧整曲分支透传 full_path 进待播放队列消息。"""
    src = inspect.getsource(sm.SynthesisMixin.audio_synthesis)
    assert '"full_path": message.get("full_path") or ""' in src


def test_playback_local_branch_swaps_to_full_track():
    """本地分支：song 条目 full_path 存在→voice_path 替换为整轨（防呆 isfile）。"""
    src = Path(pm.__file__).read_text(encoding="utf-8")
    assert '_local_voice = voice_tmp_path' in src
    assert 'data_json.get("type") == "song"' in src
    assert '_full and os.path.isfile(_full)' in src
    assert '"voice_path": _local_voice' in src
    # 锚点：整轨播放留痕日志（实播排查用）
    assert "整轨播放" in src
