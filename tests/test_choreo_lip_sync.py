# -*- coding: utf-8 -*-
"""
口型推理联动守卫（2026-10-25 goal：编排与 EDTalk 联动收口）

语义：编排序列执行中，audio_mode=none 的步骤（跳舞/转场纯画面片段）构成
「纯画面窗口」——数字人只播画面不出声不做口型；playback_manager EDTalk
推送分支在该窗口内延迟推送音频（队列背压保序不丢失）。带语音步骤
（tts/file）不构成窗口——其音频本身就是口型驱动源。

覆盖：
- ChoreoEngine.mute_window 状态矩阵（default/未接受/none 步/tts 步/序列耗尽）
- mute_window_remaining_s（deadline 计时锚点=CL-1 接受时刻）
- ActionChoreographer 门面透传 + 异常降级（不阻塞音频链）
- get_state 快照含 mute_window（监控/前端消费）
- playback_manager 源级：push_full 前守卫接线 + 防呆上限常量
"""

import pytest

from tests.test_action_choreography import (AVAILABLE, DottedConfig, EngineHarness,
                                            gift_data, make_rule)
from utils.orchestration.choreographer import ActionChoreographer
from utils.orchestration.config_model import load_choreography_config


class TestMuteWindowMatrix:
    """mute_window 状态矩阵（EngineHarness 显式 now 驱动，无线程无 sleep）。"""

    def test_default_rotation_is_not_mute(self):
        """默认轮播（无序列）：口型正常。"""
        h = EngineHarness()
        assert h.engine.mute_window() is False

    def test_none_step_with_accepted_switch_is_mute(self):
        """序列中 audio_mode=none 步且切换已接受：纯画面窗口。"""
        h = EngineHarness()
        h.engine.decide_trigger("gift", "小心心", gift_data())
        # enter 步（make_rule 默认无 audio → none），模拟 EDTalk 接受切换
        h.engine.on_switch_result(h.engine.seq["steps"][0]["step_id"], True, h.now)
        assert h.engine.mute_window() is True
        assert h.engine.mute_window_remaining_s() == pytest.approx(4.0)  # enter duration_s

    def test_pending_gap_extends_window(self):
        """步间隙延续：触发后切换命令已发出未被接受（enter=none）——窗口已成立，
        避免间隙期音频插入；切换接受后计时开始。"""
        h = EngineHarness()
        h.engine.decide_trigger("gift", "小心心", gift_data())
        assert h.engine.mute_window() is True  # 待接受间隙（enter none）
        assert h.engine.mute_window_remaining_s() is None  # 间隙期无 deadline
        h.engine.on_switch_result(h.engine.seq["steps"][0]["step_id"], True, h.now)
        assert h.engine.mute_window() is True
        assert h.engine.mute_window_remaining_s() == pytest.approx(4.0)  # enter duration_s

    def test_gap_between_none_steps_extends_window(self):
        """enter(none) 播完 → main(none) 切换待接受：窗口不关闭（否则音频插入跳舞间隙）。"""
        h = EngineHarness()
        h.trigger_gift()
        h.engine.on_switch_result(h.engine.seq["steps"][0]["step_id"], True, h.now)
        h.advance(4.02)
        h.engine.on_tick(h.now)  # enter 完成 → main 切换命令已发出，未接受
        assert h.engine.mute_window() is True
        h.engine.on_switch_result(h.engine.seq["steps"][1]["step_id"], True, h.now)
        assert h.engine.mute_window() is True  # main 也是 none 步

    def test_tts_step_is_not_mute(self):
        """带语音步骤（tts）：音频即口型驱动源，不构成窗口。"""
        rule = make_rule(sequence={
            "enter": {"segment": "A_入场", "switch_at": "segment_end", "duration_s": 4},
            "main": {"segment": "A_跳舞", "switch_at": "segment_end", "duration_s": 30,
                     "audio": {"mode": "tts"}},
            "exit": {"segment": "A_离场", "switch_at": "segment_end", "duration_s": 4},
        })
        h = EngineHarness(rules=[rule])
        h.trigger_gift()
        # enter(none) 完成 → main(tts) 启动并被接受
        h.engine.on_switch_result(h.engine.seq["steps"][0]["step_id"], True, h.now)
        h.advance(4.02)
        h.engine.on_tick(h.now)
        h.engine.on_switch_result(h.engine.seq["steps"][1]["step_id"], True, h.now)
        assert h.engine.mute_window() is False
        assert h.engine.mute_window_remaining_s() is None

    def test_after_sequence_done_is_not_mute(self):
        """序列播完回默认：窗口关闭。"""
        h = EngineHarness()
        h.run_full_sequence()
        assert h.engine.state == "default"
        assert h.engine.mute_window() is False


class TestFacadePassthrough:
    """ActionChoreographer 门面透传与异常降级。"""

    def test_facade_passthrough(self):
        ch = _facade_ready()
        ch.on_gift(gift_data())
        step_id = ch.engine.seq["steps"][0]["step_id"]
        ch.engine.on_switch_result(step_id, True, ch._clock())
        assert ch.in_mute_window() is True
        assert ch.mute_window_remaining_s() == pytest.approx(4.0)

    def test_facade_degrades_on_error(self):
        """引擎判定抛异常 → 门面降级 False（不阻塞音频链推送）。"""
        ch = _facade_ready()
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(ch.engine, "mute_window", lambda: (_ for _ in ()).throw(RuntimeError("x")))
            assert ch.in_mute_window() is False
            assert ch.mute_window_remaining_s() is None


class TestStateSnapshot:
    def test_get_state_includes_mute_window(self):
        ch = _facade_ready()
        assert ch.get_state()["mute_window"] is False  # DEFAULT 态
        ch.on_gift(gift_data())
        ch.engine.on_switch_result(ch.engine.seq["steps"][0]["step_id"], True, ch._clock())
        assert ch.get_state()["mute_window"] is True


class TestPlaybackGuardWiring:
    """playback_manager EDTalk 分支守卫接线（源级：窗口判定在 push_full 之前）。"""

    def test_guard_before_push(self):
        import inspect
        from utils.audio import playback_manager
        src = inspect.getsource(playback_manager)
        push_pos = src.index("await self.edtalk_client.push_full(")
        guard_pos = src.index("in_mute_window()")
        assert guard_pos < push_pos  # 守卫判定先于推送
        assert "_CHOREO_MUTE_MAX_WAIT" in src  # 防呆上限常量在位
        # 等待循环消费 in_mute_window（窗口结束才放行推送）
        assert "while _choreo_guard.in_mute_window()" in src

    def test_mute_wait_limit_defined(self):
        from utils.audio.playback_manager import _CHOREO_MUTE_MAX_WAIT
        assert _CHOREO_MUTE_MAX_WAIT >= 60.0  # 覆盖三步 duration 上限


def _facade_ready():
    """同步驱动门面（worker 停掉；EDTalk 就绪后可触发序列）。沿 p2 _facade 模式。"""
    from unittest.mock import MagicMock
    client = MagicMock()
    client.switch_segment.return_value = {"ok": True}
    client.schedule.return_value = {"ok": True}
    client.get_status.return_value = {"available_segments": sorted(AVAILABLE)}
    ch = ActionChoreographer(
        DottedConfig({"choreography": {
            "enable": True, "default_mode": "rotate", "default_segments": ["1", "55"],
            "min_retrigger_s": 2, "min_sequence_hold_s": 5, "step_timeout_s": 60,
            "rules": [make_rule()]}}),
        client, audio_enqueue=MagicMock(), thanks_generate=MagicMock(return_value=None),
        clock=lambda: 1000.0)
    ch._worker = None
    ch._ensure_worker = lambda enable=None: True
    ch.engine.on_edtalk_ready(AVAILABLE)
    return ch
