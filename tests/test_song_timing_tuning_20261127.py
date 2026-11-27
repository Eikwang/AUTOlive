"""阶段三 C4（2026-11-27）：硬件相关常量定标化——play_audio 配置面+越界告警。

背景（docs/动作编排功能报告总结.md 病根②）：歌曲时序常量（推送预补偿/
preroll/借位余量/闪避系数/间奏基线）硬编码=实测校准值，硬件差异（GPU 推理
延迟/声卡延迟/HTTP 开销）需要现场调定时只能改源码。C4 开 play_audio.<键>
配置面（与既有配对门 reply_gate_* 同节），越界（超定标范围）告警+回退默认
——越界值会系统性破坏人声对齐模型，宁可回退不采信。

覆盖：默认回退 / 合法覆盖生效 / 越界告警回退 / 非法类型静默回退 /
config.example.json 定标键齐备。
"""
import json
import os

import pytest

from utils.audio import playback_manager as pm
from utils.audio.playback_manager import PlaybackMixin


class _Host(PlaybackMixin):
    """最小宿主：仅 _song_timing 读侧（config 注入）。"""

    def __init__(self, cfg):
        self.config = cfg


def _cfg(play_audio):
    cfg = type("C", (), {})()
    cfg.get = lambda *keys: (_nested(_root, keys))
    _root = {"play_audio": play_audio} if play_audio is not None else {}
    return cfg, _root


def _nested(root, keys):
    cur = root
    for k in keys:
        if not isinstance(cur, dict) or k not in cur:
            return None
        cur = cur[k]
    return cur


def test_defaults_without_config():
    """t1：无配置/缺键 → 模块常量默认（零行为变化）。"""
    cfg, _ = _cfg(None)
    host = _Host(cfg)
    for name, (default, _, _) in PlaybackMixin._SONG_TIMING_SPECS.items():
        assert host._song_timing(name) == pytest.approx(default)
    assert host._song_timing("song_push_lead_s") == pytest.approx(pm._SONG_PUSH_LEAD)
    assert host._song_timing("song_preroll_s") == pytest.approx(pm._SONG_PREROLL_S)


def test_valid_override_applies():
    """t2：合法范围内覆盖 → 生效（现场硬件调定入口）。"""
    cfg, _ = _cfg({"song_push_lead_s": 0.6, "backing_duck_factor": 0.35})
    host = _Host(cfg)
    assert host._song_timing("song_push_lead_s") == pytest.approx(0.6)
    assert host._song_timing("backing_duck_factor") == pytest.approx(0.35)
    assert host._song_timing("song_preroll_s") == pytest.approx(pm._SONG_PREROLL_S)


def test_out_of_range_warns_and_falls_back(monkeypatch):
    """t3：越界值 → 告警（问题/修复锚点）+ 回退默认（不采信越界值）。"""
    cfg, _ = _cfg({"song_push_lead_s": 10.0, "song_preroll_s": -1.0})
    host = _Host(cfg)
    PlaybackMixin._song_timing_alarm_last.clear()
    warnings = []
    monkeypatch.setattr(pm.logger, "warning",
                        lambda msg, *a, **k: warnings.append(str(msg)))
    assert host._song_timing("song_push_lead_s") == pytest.approx(pm._SONG_PUSH_LEAD)
    assert host._song_timing("song_preroll_s") == pytest.approx(pm._SONG_PREROLL_S)
    assert any("超出定标范围" in w for w in warnings)
    assert any("problem=" in w and "fix=" in w for w in warnings)


def test_illegal_value_silent_fallback():
    """t4：非法类型（非数值）→ 静默回退默认（无告警噪音）。"""
    cfg, _ = _cfg({"song_push_lead_s": "abc"})
    host = _Host(cfg)
    assert host._song_timing("song_push_lead_s") == pytest.approx(pm._SONG_PUSH_LEAD)


def test_example_config_keys_complete():
    """t5：config.example.json 定标键齐备且均在定标范围内（配置面可发现性）。"""
    path = os.path.join(os.path.dirname(__file__), "..", "config.example.json")
    with open(path, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    pa = cfg["play_audio"]
    for name, (default, lo, hi) in PlaybackMixin._SONG_TIMING_SPECS.items():
        assert name in pa, f"config.example.json 缺 play_audio.{name}"
        v = float(pa[name])
        assert lo <= v <= hi, f"play_audio.{name}={v} 超出定标范围 [{lo},{hi}]"
