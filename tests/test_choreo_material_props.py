# -*- coding: utf-8 -*-
"""
new 谓词规则守卫（2026-10-27 批次 R-ceo4/R-eng3/R-eng5/R-eng6——画面输出重构）。

语义：pred_version=new 的规则——时长=素材时长（apply 二段从 sidecar 物化）、
mute 谓词=素材口型标志（off=纯画面窗口）、接管判定=song 触发才接管、
song 主线段 hold_for_song（歌曲起播锚点+剩余时长覆盖保险丝）。
存量规则（legacy）维持旧谓词直至重存（零静默行为变化）。

覆盖（R-eng3 最小清单）：
- build_steps 谓词版本+lip_flag 物化
- mute_window 双分支（new off=窗口/new on=非窗口/legacy 旧谓词原样）
- 时长接替：new 步 duration=素材时长；未知→step_timeout_s 兜底+降级计数
- hold_for_song：保险丝起点→on_song_started 覆盖延长→离场衔接
- 接管终态：song new=接管 / gift new=不接管 / legacy=has_tts_audio 旧判定
- mute_window_remaining_s 窗口长度=素材时长（看门狗 F1 契约保持）
"""

import pytest

from tests.test_action_choreography import (DottedConfig, EngineHarness,
                                            gift_data, make_cfg, make_rule)
from utils.orchestration.choreographer import ActionChoreographer
from utils.orchestration.config_model import load_choreography_config


def _new_rule(**over):
    """new 谓词规则（无 duration_s/audio——时长与口型由 apply 二段物化）。"""
    rule = make_rule(**over)
    rule["pred_version"] = "new"
    return rule


def _stamp_material_props(cfg, props_by_seg):
    """模拟 apply 二段物化（真实实现读 sidecar；测试注入）。"""
    for rule in cfg.rules:
        if rule.pred_version != "new":
            continue
        for step in (rule.enter, rule.main, rule.exit):
            props = props_by_seg.get(step.segment) or {}
            step.lip_flag = bool(props.get("lip_flag", True))
            step.duration_s = props.get("duration_s")
            if rule.trigger_type == "song" and step.name == "main":
                step.hold_for_song = True
    return cfg


class TestNewPredicateMatrix:
    """mute_window 双分支（R-ceo4：new=素材口型标志；legacy=旧谓词零变化）。"""

    def _harness_new(self, lip_flags, trigger="gift"):
        """new 规则 harness：enter/main/exit 口型标志依次给定；时长 4/8/2。"""
        rules = [_new_rule(trigger_type=trigger, match="" if trigger == "song" else "小心心")]
        cfg = load_choreography_config(make_cfg(rules=rules))
        segs = ("E", "M", "X")
        for step, lip in zip((cfg.rules[0].enter, cfg.rules[0].main, cfg.rules[0].exit),
                             lip_flags):
            step.segment = segs[(step.name == "enter") * 0 + (step.name == "main") * 1
                                + (step.name == "exit") * 2]
            step.lip_flag = lip
            step.duration_s = {"enter": 4.0, "main": 8.0, "exit": 2.0}[step.name]
        if trigger == "song":
            cfg.rules[0].main.hold_for_song = True
        h = EngineHarness(rules=[])  # 空 rules：手动换装 cfg
        h.engine.cfg = cfg
        h.engine.enabled_rules = cfg.rules
        return h, cfg

    def test_new_off_step_is_mute(self):
        """new 谓词：素材口型标志 off=纯画面窗口（窗口长度=素材时长）。"""
        h, cfg = self._harness_new([True, False, True])
        h.engine.decide_trigger("gift", "小心心", gift_data())
        h.engine.on_switch_result(h.engine.seq["steps"][0]["step_id"], True, h.now)
        assert h.engine.mute_window() is False  # enter lip on=非窗口
        h.advance(4.02)
        h.engine.on_tick(h.now)
        h.engine.on_switch_result(h.engine.seq["steps"][1]["step_id"], True, h.now)
        assert h.engine.mute_window() is True  # main lip off=窗口
        assert h.engine.mute_window_remaining_s() == pytest.approx(8.0)

    def test_new_on_step_is_not_mute(self):
        """new 谓词：素材口型标志 on=非窗口（口型驱动照常）。"""
        h, cfg = self._harness_new([True, True, True])
        h.engine.decide_trigger("gift", "小心心", gift_data())
        h.engine.on_switch_result(h.engine.seq["steps"][0]["step_id"], True, h.now)
        assert h.engine.mute_window() is False

    def test_legacy_rule_predicate_unchanged(self):
        """存量规则旧谓词零静默变化：audio_mode=none 步=窗口（与批前一致）。"""
        h = EngineHarness()  # make_rule 默认 legacy（无 pred_version）
        h.engine.decide_trigger("gift", "小心心", gift_data())
        h.engine.on_switch_result(h.engine.seq["steps"][0]["step_id"], True, h.now)
        assert h.engine.mute_window() is True  # 旧谓词：none 步=窗口
        assert h.engine.mute_window_remaining_s() == pytest.approx(4.0)

    def test_legacy_tts_step_not_mute_after_new_semantics_land(self):
        """存量规则 tts 步=非窗口（旧谓词完整保留，混跑零静默）。"""
        rules = [make_rule(sequence={
            "enter": {"segment": "A_入场", "duration_s": 4},
            "main": {"segment": "A_跳舞", "duration_s": 30,
                     "audio": {"mode": "tts"}},
            "exit": {"segment": "A_离场", "duration_s": 4}})]
        h = EngineHarness(rules=rules)
        h.engine.decide_trigger("gift", "小心心", gift_data())
        h.engine.on_switch_result(h.engine.seq["steps"][0]["step_id"], True, h.now)
        assert h.engine.mute_window() is True  # enter(none)=窗口（旧谓词）
        h.advance(4.02)
        h.engine.on_tick(h.now)
        h.engine.on_switch_result(h.engine.seq["steps"][1]["step_id"], True, h.now)
        assert h.engine.mute_window() is False  # main(tts)=非窗口


class TestDurationSuccession:
    """时长接替（R-ceo4/X2/R-eng6②）：new 步=素材时长；未知→兜底+降级。"""

    def test_new_step_uses_material_duration(self):
        rules = [_new_rule()]
        cfg = load_choreography_config(make_cfg(rules=rules))
        _stamp_material_props(cfg, {"A_入场": {"duration_s": 7.5, "lip_flag": False}})
        h = EngineHarness(rules=[])
        h.engine.cfg = cfg
        h.engine.enabled_rules = cfg.rules
        h.engine.decide_trigger("gift", "小心心", gift_data())
        h.engine.on_switch_result(h.engine.seq["steps"][0]["step_id"], True, h.now)
        assert h.engine.mute_window() is True
        assert h.engine.mute_window_remaining_s() == pytest.approx(7.5)

    def test_new_step_unknown_duration_falls_back_with_degrade(self):
        """R-eng6② 引擎防御：时长未知→step_timeout_s 兜底+timeout_degrade 降级计数。"""
        rules = [_new_rule()]
        cfg = load_choreography_config(make_cfg(rules=rules))
        _stamp_material_props(cfg, {"A_入场": {"lip_flag": False}})  # 无 duration_s
        h = EngineHarness(rules=[])
        h.engine.cfg = cfg
        h.engine.enabled_rules = cfg.rules
        before = h.engine.degrade_total
        h.engine.decide_trigger("gift", "小心心", gift_data())
        h.engine.on_switch_result(h.engine.seq["steps"][0]["step_id"], True, h.now)
        assert h.engine.degrade_total == before + 1
        assert h.engine.mute_window_remaining_s() == pytest.approx(
            h.engine.cfg.step_timeout_s)

    def test_fb2_guard_still_rejects_legacy_none_without_duration(self):
        """FB-2 守卫对 legacy 规则保持（静默跳段防护不放宽）。"""
        rules = [make_rule(sequence={
            "enter": {"segment": "A_入场"},  # 无 duration 无 audio
            "main": {"segment": "A_跳舞", "duration_s": 30},
            "exit": {"segment": "A_离场", "duration_s": 4}})]
        cfg = load_choreography_config(make_cfg(rules=rules))
        assert cfg.rules == []  # legacy 无时长 none 步仍被拒


class TestHoldForSong:
    """song 主线保持（R-eng1/F-M4）：保险丝→on_song_started 覆盖延长。"""

    def _song_harness(self):
        rules = [_new_rule(trigger_type="song", match="")]
        cfg = load_choreography_config(make_cfg(rules=rules))
        _stamp_material_props(cfg, {"A_入场": {"duration_s": 4.0, "lip_flag": False},
                                    "A_跳舞": {"duration_s": 8.0, "lip_flag": False},
                                    "A_离场": {"duration_s": 2.0, "lip_flag": False}})
        # 主线段 hold_for_song（apply 二段对 song 规则主线自动置位；此处直置）
        cfg.rules[0].main.hold_for_song = True
        h = EngineHarness(rules=[])
        h.engine.cfg = cfg
        h.engine.enabled_rules = cfg.rules
        return h

    def test_hold_main_starts_with_fuse_cap(self):
        """song 主线段起步=step_timeout_s 保险丝（歌曲未起播的诚实降级路径）。"""
        h = self._song_harness()
        h.engine.decide_trigger("song", "", {})
        h.engine.on_switch_result(h.engine.seq["steps"][0]["step_id"], True, h.now)
        h.advance(4.02)
        h.engine.on_tick(h.now)  # enter 完成 → main 切换
        h.engine.on_switch_result(h.engine.seq["steps"][1]["step_id"], True, h.now)
        assert h.engine.seq.get("hold_for_song") is True
        assert h.engine.mute_window_remaining_s() == pytest.approx(
            h.engine.cfg.step_timeout_s)

    def test_song_started_extends_deadline(self):
        """on_song_started：deadline=锚点+剩余时长（>保险丝）——歌没播完不走完。"""
        h = self._song_harness()
        h.engine.decide_trigger("song", "", {})
        h.engine.on_switch_result(h.engine.seq["steps"][0]["step_id"], True, h.now)
        h.advance(4.02)
        h.engine.on_tick(h.now)
        h.engine.on_switch_result(h.engine.seq["steps"][1]["step_id"], True, h.now)
        cmds = h.engine.on_song_started("测试歌", 180.0, h.now)
        assert h.engine.mute_window_remaining_s() == pytest.approx(180.0)
        assert any("歌曲起播" in getattr(c, "msg", "") for c in cmds)

    def test_song_ignored_without_hold_step(self):
        """无 hold 段（默认轮播/gift 规则）：on_song_started 幂等忽略。"""
        h = EngineHarness()
        cmds = h.engine.on_song_started("歌", 180.0, h.now)
        assert cmds == []
        assert h.engine.mute_window() is False


class TestTakeoverTerminal:
    """接管判定终态（R-ceo4）：song new=接管；gift/keyword new=不接管；legacy 旧判定。"""

    def test_new_song_rule_takes_over(self):
        cfg = load_choreography_config(make_cfg(
            rules=[_new_rule(trigger_type="song", match="")]))
        assert cfg.rules[0].has_tts_audio() is True

    def test_new_gift_rule_does_not_take_over(self):
        cfg = load_choreography_config(make_cfg(rules=[_new_rule()]))
        assert cfg.rules[0].has_tts_audio() is False

    def test_legacy_rule_keeps_old_judgment(self):
        cfg = load_choreography_config(make_cfg(rules=[make_rule()]))  # gift legacy
        assert cfg.rules[0].has_tts_audio() is False
        cfg2 = load_choreography_config(make_cfg(rules=[make_rule(sequence={
            "enter": {"segment": "A_入场", "duration_s": 4},
            "main": {"segment": "A_跳舞", "duration_s": 30,
                     "audio": {"mode": "tts"}},
            "exit": {"segment": "A_离场", "duration_s": 4}})]))
        assert cfg2.rules[0].has_tts_audio() is True  # legacy tts=旧判定接管


class TestPredVersionStamp:
    """build_steps 谓词版本物化（R-eng3）：步骤携带 pred_version/lip_flag。"""

    def test_build_steps_stamp_new(self):
        cfg = load_choreography_config(make_cfg(rules=[_new_rule()]))
        _stamp_material_props(cfg, {"A_入场": {"duration_s": 4.0, "lip_flag": False}})
        h = EngineHarness(rules=[])
        h.engine.cfg = cfg
        h.engine.enabled_rules = cfg.rules
        h.engine.decide_trigger("gift", "小心心", gift_data())
        steps = h.engine.seq["steps"]
        assert all(s["pred_version"] == "new" for s in steps)
        assert steps[0]["lip_flag"] is False  # 物化的素材口型标志
        assert steps[0]["duration_s"] == 4.0

    def test_build_steps_stamp_legacy(self):
        h = EngineHarness()
        h.engine.decide_trigger("gift", "小心心", gift_data())
        assert all(s["pred_version"] == "legacy" for s in h.engine.seq["steps"])
        assert h.engine.seq["steps"][0].get("lip_flag") is None
