# -*- coding: UTF-8 -*-
"""
动作编排（Action Choreography）P1 回归测试（/autoplan 2026-10-21 批次 T7）

覆盖义务块验证条款：
- 配置模型：结构校验表逐项拒绝 + 合法装载 + keyword 规范化 + FB-2 静默跳段拦截
- 引擎：条件1/2/3 状态机、hold 窗口（F5）、超时看门狗（F1）、计数器语义（D-4）、
  档位选择（CP-4 超档兜底/缺省单档）、段名校验+资产缺口报告（F2）、DEFAULT 初始化（CP-5）、
  恢复重试（S1-1）、开关关闭零行为、切换失败降级
- 接线：gift_handle 接管短路、完成回调关联（CL-2 (type,content)+时间窗）
- 客户端：schedule 方法（mock HTTP）

时间敏感断言全部用显式 now 参数驱动（无 sleep，无 flake）。
"""

import threading
from unittest.mock import MagicMock, patch

import pytest

from utils.orchestration.choreographer import (ActionChoreographer,
                                               ChoreoEngine, EnqueueFile,
                                               LogCmd, RestoreDefault,
                                               SwitchSegment, TtsThanks,
                                               _normalize_text)
from utils.orchestration.config_model import (load_choreography_config,
                                              validate_rule_segments)


# ---------- 工具 ----------

class DottedConfig:
    """点式 get(*keys) 配置桩（沿 tests/test_t7_blacklist.py 模式）。"""

    def __init__(self, cfg):
        self._cfg = cfg

    def get(self, *keys):
        cur = self._cfg
        for k in keys:
            if not isinstance(cur, dict) or k not in cur:
                return None
            cur = cur[k]
        return cur


def make_rule(**over):
    rule = {
        "rule_id": "girl_A",
        "trigger_type": "gift",
        "match": "小心心",
        "min_price": 0,
        "sequence": {
            "enter": {"segment": "A_入场", "switch_at": "segment_end", "duration_s": 4},
            "main": {"segment": "A_跳舞", "switch_at": "segment_end", "duration_s": 30},
            "exit": {"segment": "A_离场", "switch_at": "segment_end", "duration_s": 4},
        },
    }
    rule.update(over)
    return rule


def make_cfg(rules=None, **over):
    node = {"enable": True, "default_mode": "rotate",
            "default_segments": ["1", "55"],
            "rules": rules if rules is not None else [make_rule()]}
    node.update(over)
    return DottedConfig({"choreography": node})


def gift_data(name="小心心", price=1, num=1):
    return {"username": "观众A", "gift_name": name, "gift_num": num,
            "num": num, "total_price": price, "unit_price": price / max(num, 1)}


AVAILABLE = {"A_入场", "A_跳舞", "A_离场", "A_生气入场", "A_崩溃跳舞",
             "1", "55", "B_入场", "B_跳舞", "B_离场"}


def switch_cmd(cmds):
    """从命令列表中取段切换命令（触发命令列表 = [LogCmd, SwitchSegment, ...]）。"""
    return next(c for c in cmds if isinstance(c, SwitchSegment))


class EngineHarness:
    """显式 now 驱动的引擎测试台（无线程无 sleep）。

    时序约定：deadline 到达当刻 on_tick 即完成（now < deadline 为严格小于），
    边界测试用 advance(周期 - 0.01) 后断言不推进，再 advance(0.02) 断言推进。
    """

    def __init__(self, rules=None, ready=True, **over):
        self.cfg = load_choreography_config(make_cfg(rules=rules, **over))
        self.now = 1000.0
        self.engine = ChoreoEngine(self.cfg, clock=lambda: self.now)
        if ready:
            self.engine.on_edtalk_ready(AVAILABLE)

    def trigger_gift(self, name="小心心", price=1, **data_over):
        d = gift_data(name, price, **data_over)
        return self.engine.decide_trigger("gift", name, d)

    def advance(self, dt):
        self.now += dt

    def run_full_sequence(self):
        """推完 enter→main→exit 三步（none 步计时驱动），返回 RestoreDefault 命令列表。"""
        d = self.trigger_gift()
        cmds = self.engine.on_switch_result(switch_cmd(d["commands"]).step_id, True, self.now)
        self.advance(3.99)
        assert self.engine.on_tick(self.now) == []
        self.advance(0.02)
        cmds = self.engine.on_tick(self.now)
        main_cmd = cmds[0]
        cmds = self.engine.on_switch_result(main_cmd.step_id, True, self.now)
        self.advance(29.99)
        assert self.engine.on_tick(self.now) == []
        self.advance(0.02)
        cmds = self.engine.on_tick(self.now)
        exit_cmd = cmds[0]
        cmds = self.engine.on_switch_result(exit_cmd.step_id, True, self.now)
        self.advance(3.99)
        assert self.engine.on_tick(self.now) == []
        self.advance(0.02)
        return self.engine.on_tick(self.now)


# ---------- 配置模型 ----------

class TestConfigModel:
    def test_legal_load(self):
        cfg = load_choreography_config(make_cfg())
        assert cfg.enable is True
        assert len(cfg.rules) == 1
        r = cfg.rules[0]
        assert (r.enter.segment, r.main.segment, r.exit.segment) == ("A_入场", "A_跳舞", "A_离场")
        assert r.enter.duration_s == 4

    def test_missing_section_disabled(self):
        cfg = load_choreography_config(DottedConfig({}))
        assert cfg.enable is False and cfg.rules == []

    def test_reject_trigger_type_entrance(self):
        cfg = load_choreography_config(make_cfg(rules=[make_rule(trigger_type="entrance")]))
        assert cfg.rules == []  # 预留枚举位拒绝并提示 P1 不订阅

    def test_reject_unknown_switch_at(self):
        rules = [make_rule(sequence={"enter": {"segment": "A", "switch_at": "fade", "duration_s": 1},
                                     "main": {"segment": "B", "duration_s": 5},
                                     "exit": {"segment": "C", "duration_s": 1}})]
        assert load_choreography_config(make_cfg(rules=rules)).rules == []

    def test_reject_bad_audio_mode(self):
        rules = [make_rule(sequence={"enter": {"segment": "A", "duration_s": 1},
                                     "main": {"segment": "B", "duration_s": 5,
                                              "audio": {"mode": "mp3", "path": "x.mp3"}},
                                     "exit": {"segment": "C", "duration_s": 1}})]
        assert load_choreography_config(make_cfg(rules=rules)).rules == []

    def test_reject_none_without_duration_fb2(self):
        # FB-2：无 audio 步骤必须 duration_s>0（拦截单槽 pending 静默跳段）
        rules = [make_rule(sequence={"enter": {"segment": "A"},
                                     "main": {"segment": "B", "duration_s": 5},
                                     "exit": {"segment": "C", "duration_s": 1}})]
        assert load_choreography_config(make_cfg(rules=rules)).rules == []

    def test_file_requires_path(self):
        rules = [make_rule(sequence={"enter": {"segment": "A", "duration_s": 1},
                                     "main": {"segment": "B", "duration_s": 5,
                                              "audio": {"mode": "file"}},
                                     "exit": {"segment": "C", "duration_s": 1}})]
        assert load_choreography_config(make_cfg(rules=rules)).rules == []

    def test_reject_interrupt_unknown(self):
        assert load_choreography_config(make_cfg(rules=[make_rule(interrupt="aggressive")])).rules == []

    def test_reject_tiers_not_ascending(self):
        tiers = [{"max_count": 6, "mood": "angry"}, {"max_count": 3, "mood": "happy"}]
        assert load_choreography_config(make_cfg(rules=[make_rule(emotion_tiers=tiers)])).rules == []

    def test_min_retrigger_invalid_falls_back(self):
        cfg = load_choreography_config(make_cfg(min_retrigger_s=-1))
        assert cfg.min_retrigger_s == 2.0

    def test_song_match_optional(self):
        rules = [make_rule(rule_id="any_song", trigger_type="song", match="")]
        cfg = load_choreography_config(make_cfg(rules=rules))
        assert len(cfg.rules) == 1 and cfg.rules[0].match == ""

    def test_keyword_normalization_semantics(self):
        # 规范化（NFKC+去空白+casefold）：全角/空白/大小写绕过失效（CL-3）
        assert _normalize_text("ＢＥＳＴ 歌") == _normalize_text("best歌")
        assert _normalize_text("介绍一下") in _normalize_text("ＩＮＴＲＯ 介绍一下吧")


# ---------- 引擎：触发与状态机 ----------

class TestTriggerAndState:
    def test_trigger_starts_sequence(self):
        h = EngineHarness()
        d = h.trigger_gift()
        assert d["accepted"] is True
        assert d["takeover"] is False  # 无 tts 音轨 → 不接管答谢（原生答谢照常）
        assert h.engine.state == "sequence"
        cmd = switch_cmd(d["commands"])
        assert cmd.segment == "A_入场"
        assert h.engine.counters["girl_A"] == 1

    def test_takeover_only_with_tts_audio(self):
        rules = [make_rule(sequence={"enter": {"segment": "A_入场", "duration_s": 4},
                                     "main": {"segment": "A_跳舞", "duration_s": 30,
                                              "audio": {"mode": "tts"}},
                                     "exit": {"segment": "A_离场", "duration_s": 4}})]
        assert EngineHarness(rules=rules).trigger_gift()["takeover"] is True
        assert EngineHarness().trigger_gift()["takeover"] is False

    def test_condition2_main_playing_ignored_not_counted(self):
        h = EngineHarness()
        h.trigger_gift()
        d = h.trigger_gift()  # main 未完成：忽略且不计数（WP-6）
        assert d["accepted"] is False
        assert h.engine.counters["girl_A"] == 1
        assert h.engine.state == "sequence"

    def test_condition2_main_done_requeues_full_three(self):
        h = EngineHarness()
        d = h.trigger_gift()
        cmds = h.engine.on_switch_result(switch_cmd(d["commands"]).step_id, True, h.now)
        h.advance(4.01)
        cmds = h.engine.on_tick(self_now(h))  # enter 完成 → main
        assert cmds[0].segment == "A_跳舞"
        cmds = h.engine.on_switch_result(cmds[0].step_id, True, h.now)
        h.advance(30.01)
        cmds = h.engine.on_tick(self_now(h))  # main 完成 → exit
        assert cmds[0].segment == "A_离场"
        assert h.engine.seq["main_done"] is True
        # 同键再触发（处 exit 步）→ 重排完整三步（WP-9）
        d = h.trigger_gift()
        assert d["accepted"] is True and h.engine.counters["girl_A"] == 2
        assert [s["name"] for s in h.engine.seq["pending_requeue"]] == ["enter", "main", "exit"]
        # 当前 exit 播完 → 重排 enter（不重复 exit）
        cmds = h.engine.on_switch_result(h.engine.seq["steps"][h.engine.seq["idx"]]["step_id"], True, h.now)
        h.advance(4.01)
        cmds = h.engine.on_tick(self_now(h))
        assert switch_cmd(cmds).segment == "A_入场"

    def test_condition3_interrupt_replaces_queue_and_resets_counter(self):
        rules = [make_rule(),
                 make_rule(rule_id="girl_B", match="玫瑰",
                           sequence={"enter": {"segment": "B_入场", "duration_s": 4},
                                     "main": {"segment": "B_跳舞", "duration_s": 30},
                                     "exit": {"segment": "B_离场", "duration_s": 4}})]
        h = EngineHarness(rules=rules)
        h.trigger_gift()
        h.engine.counters["girl_A"] = 5  # 旧键已有计数
        h.advance(6)  # 出 hold 窗口
        d = h.engine.decide_trigger("gift", "玫瑰", gift_data("玫瑰"))
        assert d["accepted"] is True
        assert h.engine.counters["girl_A"] == 0     # 他键触发清零（D-4）
        assert h.engine.counters["girl_B"] == 1
        # 队列 = [旧 exit, 新 enter, 新 main, 新 exit]（用户示例3：A 离场→C 入场）
        names = [s["name"] for s in h.engine.seq["steps"]]
        assert names == ["exit", "enter", "main", "exit"]
        assert h.engine.seq["steps"][0]["segment"] == "A_离场"

    def test_condition3_exit_displayed_not_reinserted(self):
        rules = [make_rule(),
                 make_rule(rule_id="girl_B", match="玫瑰",
                           sequence={"enter": {"segment": "B_入场", "duration_s": 4},
                                     "main": {"segment": "B_跳舞", "duration_s": 30},
                                     "exit": {"segment": "B_离场", "duration_s": 4}})]
        h = EngineHarness(rules=rules)
        h.trigger_gift()
        cmds = h.engine.on_switch_result(h.engine.seq["steps"][0]["step_id"], True, h.now)
        h.advance(4.01)
        cmds = h.engine.on_tick(self_now(h))
        cmds = h.engine.on_switch_result(cmds[0].step_id, True, h.now)
        h.advance(30.01)
        cmds = h.engine.on_tick(self_now(h))
        assert cmds[0].segment == "A_离场" and h.engine.seq["exit_started"] is True
        h.advance(6)
        h.engine.decide_trigger("gift", "玫瑰", gift_data("玫瑰"))
        names = [s["name"] for s in h.engine.seq["steps"]]
        assert names == ["enter", "main", "exit"]  # 旧 exit 已显示：不重插（CP-3）

    def test_hold_window_ignores_other_key(self):
        rules = [make_rule(),
                 make_rule(rule_id="girl_B", match="玫瑰",
                           sequence={"enter": {"segment": "B_入场", "duration_s": 4},
                                     "main": {"segment": "B_跳舞", "duration_s": 30},
                                     "exit": {"segment": "B_离场", "duration_s": 4}})]
        h = EngineHarness(rules=rules, min_sequence_hold_s=5)
        h.trigger_gift()
        d = h.engine.decide_trigger("gift", "玫瑰", gift_data("玫瑰"))  # hold 内（F5）
        assert d["accepted"] is False
        assert h.engine.counters.get("girl_B", 0) == 0
        assert h.engine.seq["rule_id"] == "girl_A"  # 旧序列未终止

    def test_throttle_ignored_not_counted(self):
        h = EngineHarness(min_retrigger_s=2)
        h.trigger_gift()
        h.engine.state = "default"
        h.engine.seq = None
        d = h.trigger_gift()  # 2s 内再触发：节流忽略
        assert d["accepted"] is False
        assert h.engine.counters["girl_A"] == 1

    def test_min_price_gate(self):
        assert EngineHarness().trigger_gift(price=0)["accepted"] is True
        h2 = EngineHarness(rules=[make_rule(min_price=10)])
        assert h2.trigger_gift(price=1)["accepted"] is False

    def test_keyword_rule_normalized_match(self):
        rules = [make_rule(rule_id="kw_rule", trigger_type="keyword", match="介绍一下")]
        h = EngineHarness(rules=rules)
        d = h.engine.decide_trigger("keyword", "ＩＮＴＲＯ 介绍一下吧", {})
        assert d["accepted"] is True  # 全角+大小写规范化后子串命中（CL-3）

    def test_song_rule_empty_match_hits_any(self):
        rules = [make_rule(rule_id="song_rule", trigger_type="song", match="")]
        h = EngineHarness(rules=rules)
        assert h.engine.decide_trigger("song", "任意歌名", {})["accepted"] is True


def self_now(h):
    """on_tick 的显式 now（测试可读性别名）。"""
    return h.now


# ---------- 引擎：完成判定与看门狗 ----------

class TestCompletionPredicate:
    def test_none_step_deadline_from_switch_accepted(self):
        # CL-1：计时起点 = 切换被 EDTalk 接受时刻
        h = EngineHarness()
        d = h.trigger_gift()
        cmds = h.engine.on_switch_result(switch_cmd(d["commands"]).step_id, True, h.now)
        assert cmds == []
        h.advance(3.99)
        assert h.engine.on_tick(h.now) == []  # 界前不推进
        h.advance(0.02)
        cmds = h.engine.on_tick(h.now)
        assert isinstance(cmds[0], SwitchSegment) and cmds[0].segment == "A_跳舞"

    def test_timeout_watchdog_audio_step_degrades(self):
        # F1：audio 步完成回调丢失 → duration_s 上限强制推进 + 降级计数
        rules = [make_rule(sequence={"enter": {"segment": "A_入场", "duration_s": 4},
                                     "main": {"segment": "A_跳舞", "duration_s": 10,
                                              "audio": {"mode": "file", "path": "music/a.mp3"}},
                                     "exit": {"segment": "A_离场", "duration_s": 4}})]
        h = EngineHarness(rules=rules)
        d = h.trigger_gift()
        h.engine.on_switch_result(switch_cmd(d["commands"]).step_id, True, h.now)
        h.advance(4.01)
        main_cmd = h.engine.on_tick(h.now)[0]
        h.engine.on_switch_result(main_cmd.step_id, True, h.now)
        h.engine.on_audio_enqueued(main_cmd.step_id, "song", "music/a.mp3", True, h.now)
        h.advance(10.01)
        cmds = h.engine.on_tick(h.now)  # 超时兜底（回调未到）
        assert isinstance(cmds[0], SwitchSegment) and cmds[0].segment == "A_离场"
        assert h.engine.degrade_total == 1  # 降级计数（F1）

    def test_audio_complete_matching_correlates(self):
        # CL-2：(type,content) 关联 + 晚于入队时间；他链路同 content 不误触
        rules = [make_rule(sequence={"enter": {"segment": "A_入场", "duration_s": 4},
                                     "main": {"segment": "A_跳舞", "duration_s": 60,
                                              "audio": {"mode": "file", "path": "music/a.mp3"}},
                                     "exit": {"segment": "A_离场", "duration_s": 4}})]
        h = EngineHarness(rules=rules)
        d = h.trigger_gift()
        h.engine.on_switch_result(switch_cmd(d["commands"]).step_id, True, h.now)
        h.advance(4.01)
        main_cmd = h.engine.on_tick(h.now)[0]
        h.engine.on_switch_result(main_cmd.step_id, True, h.now)
        h.engine.on_audio_enqueued(main_cmd.step_id, "song", "music/a.mp3", True, h.now)
        # 早于入队时间的回调 → 忽略（时间窗）
        assert h.engine.on_audio_complete("song", "music/a.mp3", h.now - 1) == []
        # 他 content → 忽略
        assert h.engine.on_audio_complete("song", "music/other.mp3", h.now + 5) == []
        # 正常完成回调 → 步完成（不降级）
        cmds = h.engine.on_audio_complete("song", "music/a.mp3", h.now + 1)
        assert isinstance(cmds[0], SwitchSegment) and cmds[0].segment == "A_离场"
        assert h.engine.degrade_total == 0

    def test_switch_failure_degrades_and_advances(self):
        h = EngineHarness()
        d = h.trigger_gift()
        cmds = h.engine.on_switch_result(switch_cmd(d["commands"]).step_id, False, h.now)
        assert isinstance(cmds[0], SwitchSegment) and cmds[0].segment == "A_跳舞"
        assert h.engine.degrade_total == 1

    def test_condition1_restore_default_and_counter_retained(self):
        h = EngineHarness()
        restore = h.run_full_sequence()
        restores = [c for c in restore if isinstance(c, RestoreDefault)]
        assert restores and restores[0].sequence == ["1", "55"]  # rotate 原序
        assert h.engine.state == "default"
        assert h.engine.counters["girl_A"] == 1  # 切回默认不清零（D-4）

    def test_random_mode_pool_valid(self):
        h = EngineHarness(default_mode="random")
        p1, p2 = h.engine._default_pool(), h.engine._default_pool()
        assert sorted(p1) == ["1", "55"] and sorted(p2) == ["1", "55"]

    def test_restore_retry_then_exhaust(self):
        h = EngineHarness()
        cmds = h.engine.on_restore_result(False, h.now)
        assert len(cmds) == 1 and isinstance(cmds[0], RestoreDefault)  # 重试 1
        assert h.engine.on_restore_result(False, h.now)  # 重试 2
        assert h.engine.on_restore_result(False, h.now)  # 重试 3
        assert h.engine.on_restore_result(False, h.now) == []  # 耗尽：CRITICAL 日志
        assert h.engine._restore_attempts == 0

    def test_tier_selection_and_overflow(self):
        tiers = [{"max_count": 3, "mood": "happy"},
                 {"max_count": 6, "mood": "angry", "segment_overrides": {"enter": "A_生气入场"}},
                 {"max_count": 9, "mood": "breakdown", "segment_overrides": {"main": "A_崩溃跳舞"}}]
        h = EngineHarness(rules=[make_rule(emotion_tiers=tiers)])
        h.trigger_gift()
        assert h.engine.seq["steps"][0]["segment"] == "A_入场"  # 第1次：基础素材（happy 无覆盖）
        # 跨档触发：置计数 5 → 本次第 6 次 → angry 档
        # （key_last 置 now-1：绕过节流且不触发 GAP-2 衰减——该测试测档位选择，
        #   窗口衰减行为由 tests/test_choreography_completion.py::TestCounterDecay 专测）
        h.engine.state = "default"
        h.engine.seq = None
        h.engine.key_last["girl_A"] = h.now - 10  # >min_retrigger_s 绕过节流，<300 不触发衰减
        h.engine.counters["girl_A"] = 5
        h.engine.decide_trigger("gift", "小心心", gift_data())
        assert h.engine.seq["steps"][0]["segment"] == "A_生气入场"
        # 超档（count=99 → 第 100 次）：末档兜底（CP-4）
        h.engine.state = "default"
        h.engine.seq = None
        h.engine.key_last["girl_A"] = h.now - 10
        h.engine.counters["girl_A"] = 99
        h.engine.decide_trigger("gift", "小心心", gift_data())
        assert h.engine.seq["steps"][1]["segment"] == "A_崩溃跳舞"
        assert h.engine.seq["tier_mood"] == "breakdown"

    def test_overrides_cover_main(self):
        tiers = [{"max_count": 9, "mood": "breakdown", "segment_overrides": {"main": "A_崩溃跳舞"}}]
        h = EngineHarness(rules=[make_rule(emotion_tiers=tiers)])
        h.trigger_gift()
        assert h.engine.seq["steps"][1]["segment"] == "A_崩溃跳舞"
        assert h.engine.seq["steps"][0]["segment"] == "A_入场"  # 未覆盖步不变

    def test_no_tiers_default_single_tier(self):
        h = EngineHarness()  # emotion_tiers 缺省=单档恒用基础素材（WP-10）
        h.trigger_gift()
        assert h.engine.seq["tier_mood"] == ""


# ---------- 就绪校验与初始化 ----------

class TestReadiness:
    def test_gap_report_disables_rules_with_missing_segments(self):
        # F2：段名校验升格资产清单缺口报告
        rules = [make_rule(),
                 make_rule(rule_id="missing_rule", match="玫瑰",
                           sequence={"enter": {"segment": "不存在_入场", "duration_s": 4},
                                     "main": {"segment": "不存在_跳舞", "duration_s": 5},
                                     "exit": {"segment": "不存在_离场", "duration_s": 4}})]
        h = EngineHarness(rules=rules, ready=False)
        result = h.engine.on_edtalk_ready(AVAILABLE)
        assert result["enabled_rules"] == ["girl_A"]
        assert ("missing_rule", ["不存在_入场", "不存在_离场", "不存在_跳舞"]) in result["disabled_rules"]
        assert [r.rule_id for r in h.engine.enabled_rules] == ["girl_A"]  # 缺失规则 disabled

    def test_default_init_commands(self):
        h = EngineHarness()
        h.engine.on_edtalk_ready(AVAILABLE)
        cmds = h.engine.default_init_commands()
        assert any(isinstance(c, RestoreDefault) and c.sequence == ["1", "55"] for c in cmds)

    def test_default_init_zero_when_disabled(self):
        cfg = load_choreography_config(make_cfg(enable=False))
        eng = ChoreoEngine(cfg)
        eng.on_edtalk_ready(AVAILABLE)
        assert eng.default_init_commands() == []  # enable=false 零动作（权属线 CP-5）

    def test_default_pool_prunes_missing(self):
        h = EngineHarness(default_segments=["1", "幽灵段"])
        h.engine.on_edtalk_ready(AVAILABLE)
        assert h.engine.cfg.default_segments == ["1"]

    def test_validate_rule_segments_direct(self):
        rules = load_choreography_config(make_cfg(rules=[make_rule()])).rules
        result = validate_rule_segments(rules, {"A_入场", "A_跳舞"})  # exit 缺失
        assert result["enabled_rules"] == []
        assert result["disabled_rules"][0][1] == ["A_离场"]


# ---------- 门面与接线 ----------

class TestChoreographerFacade:
    def _facade(self, rules=None, **over):
        """门面测试台：worker 停在就绪探测（get_status 返回空），事件同步驱动。

        可变时钟：deadline 完成靠显式 tick（tick_to）驱动，无 sleep 无 flake。
        """
        self.clock = [1000.0]
        client = MagicMock()
        client.switch_segment.return_value = {"ok": True}
        client.schedule.return_value = {"ok": True}
        client.get_status.return_value = {}  # 永不就绪 → worker 停在探测循环，不消费队列
        ch = ActionChoreographer(make_cfg(rules=rules, **over), client,
                                 audio_enqueue=MagicMock(),
                                 thanks_generate=MagicMock(return_value=None),
                                 clock=lambda: self.clock[0])
        ch.engine.on_edtalk_ready(AVAILABLE)
        return ch, client

    def tick_to(self, ch, dt):
        """推进可变时钟并驱动 tick + 排空队列（完成判定全链）。"""
        self.clock[0] += dt
        ch._handle_event(("tick",))
        self._drain(ch)

    def _drain(self, ch):
        """同步驱动：排空队列（命令对象→执行，结果事件→引擎；链式直到排空）。"""
        for _ in range(200):
            try:
                item = ch._events.get_nowait()
            except Exception:
                break
            if isinstance(item, (SwitchSegment, TtsThanks, EnqueueFile, RestoreDefault, LogCmd)):
                ch._execute([item])
            else:
                ch._handle_event(item)

    def test_disabled_zero_behavior(self):
        ch, client = self._facade(enable=False)
        assert ch.on_gift(gift_data()) is False
        assert ch.engine.decide_trigger("gift", "小心心", gift_data())["accepted"] is False
        assert client.switch_segment.call_count == 0  # 关闭=全链零行为（总开关义务）

    def test_on_gift_full_loop_switch_called(self):
        ch, client = self._facade()
        assert ch.on_gift(gift_data()) is False  # none 音轨：不接管答谢
        self._drain(ch)
        assert client.switch_segment.call_count == 1
        assert client.switch_segment.call_args[0][0] == "A_入场"

    def test_on_gift_tts_takeover_and_native_reuse(self):
        rules = [make_rule(sequence={"enter": {"segment": "A_入场", "duration_s": 4},
                                     "main": {"segment": "A_跳舞", "duration_s": 30,
                                              "audio": {"mode": "tts"}},
                                     "exit": {"segment": "A_离场", "duration_s": 4}})]
        ch, client = self._facade(rules=rules)
        assert ch.on_gift(gift_data()) is True  # 接管：gift_handle 跳过原生答谢
        self._drain(ch)  # enter 切段
        assert client.switch_segment.call_count == 1
        self.tick_to(ch, 4.01)  # enter 完成 → main 切段 → tts 指令（经注入引用复用原生链，WP-3/WP-4）
        # 生成失败（桩返回 None）→ main 立即完成 → exit 也切：共 3 次
        assert client.switch_segment.call_count == 3
        assert ch._thanks_generate.call_count == 1
        assert ch._thanks_generate.call_args[0][0] == "gift"

    def test_file_enqueue_via_song_channel(self):
        rules = [make_rule(sequence={"enter": {"segment": "A_入场", "duration_s": 4},
                                     "main": {"segment": "A_跳舞", "duration_s": 60,
                                              "audio": {"mode": "file", "path": "music/a.mp3"}},
                                     "exit": {"segment": "A_离场", "duration_s": 4}})]
        ch, client = self._facade(rules=rules)
        ch.on_gift(gift_data())
        self._drain(ch)
        assert client.switch_segment.call_count == 1  # enter 已切
        self.tick_to(ch, 4.01)  # enter 完成 → main 切段 + file 入队
        ch._audio_enqueue.assert_called_once_with({"type": "song", "content": "music/a.mp3"})
        assert client.switch_segment.call_count == 2  # main 等待完成回调，不推进
        # 完成回调（playback 路径）→ main 完成 → exit 切换
        ch.on_audio_complete("song", "music/a.mp3")
        self._drain(ch)
        assert client.switch_segment.call_count == 3

    def test_worker_loop_body_guard_survives_exception(self):
        # S1-1：循环体异常记日志继续（线程死亡=永久静默的防护）
        client = MagicMock()
        client.get_status.return_value = {"available_segments": sorted(AVAILABLE)}
        client.switch_segment.return_value = {"ok": True}
        client.schedule.return_value = {"ok": True}
        ch = ActionChoreographer(make_cfg(), client, audio_enqueue=MagicMock(),
                                 thanks_generate=MagicMock(return_value=None),
                                 clock=lambda: 1000.0)
        assert ch._worker is not None and ch._worker.is_alive()
        ch._events.put(("audio_complete",))  # 缺字段 → 解包异常 → 循环体防护
        ch.shutdown()
        ch._worker.join(timeout=5)
        assert not ch._worker.is_alive()  # 防护未吞掉 stop 标志，正常退出

    def test_concurrent_triggers_serialized(self):
        # 并发触发经 RLock 串行化，状态机无竞态（义务 D-1 并发串行化断言）
        rules = [make_rule(),
                 make_rule(rule_id="girl_B", match="玫瑰",
                           sequence={"enter": {"segment": "B_入场", "duration_s": 4},
                                     "main": {"segment": "B_跳舞", "duration_s": 30},
                                     "exit": {"segment": "B_离场", "duration_s": 4}})]
        ch, client = self._facade(rules=rules)
        threads = [threading.Thread(target=ch.on_gift, args=(gift_data(n),))
                   for n in ("小心心", "玫瑰", "小心心", "玫瑰")]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self._drain(ch)  # 判定线程投递的命令全部执行
        state = ch.engine.get_state()
        assert state["state"] in ("sequence", "default")
        assert client.switch_segment.call_count >= 1
        # 计数器守恒：全部触发会计入某键或被忽略，无负数/无丢失崩溃
        assert all(v >= 0 for v in state["counters"].values())


class TestClientSchedule:
    def test_schedule_method_posts(self):
        from utils.edtalk_realtime.edtalk_client import EDTalkClient
        client = EDTalkClient({"api_ip_port": "http://127.0.0.1:8000"})
        with patch("requests.post") as post:
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = {"ok": True}
            post.return_value = resp
            assert client.schedule(["1", "55"], align=True) == {"ok": True}
            _, kwargs = post.call_args
            assert kwargs["json"] == {"sequence": ["1", "55"], "align": True}

    def test_schedule_degrades_on_error(self):
        from utils.edtalk_realtime.edtalk_client import EDTalkClient
        client = EDTalkClient({"api_ip_port": "http://127.0.0.1:8000"})
        with patch("requests.post", side_effect=ConnectionError("down")):
            assert client.schedule(["1"], align=True).get("ok") is False  # 降级不抛
