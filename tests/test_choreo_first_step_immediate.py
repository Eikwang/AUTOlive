# -*- coding: UTF-8 -*-
"""编排序列第一步 immediate 守卫（2026-11-05 用户实测「触发后画面不切换」根修）。

根因：序列启动的第一步（DEFAULT/打断→enter）沿用规则 switch_at=segment_end
——服务端语义=「当前段自然播完的边界衔接」，而触发时刻当前段是默认池长素材
（实测 22 段=15007 帧=600s），挂起切换要等 5-10 分钟才到边界；引擎按 sidecar
时长正常推进状态机（日志「触发→序列播完」全绿），双时钟漂移静默——画面
永不切换。修复=第一步强制 immediate（打断语义），后续步保持 switch_at
（短段自然边界=引擎时长边界，无缝衔接）。

回归场景映射：启动触发（_start_sequence）、他键打断（_interrupt_sequence）、
序列内推进（enter→main→exit 保持规则值）、规则本配 immediate 不回退。
"""
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from tests.test_action_choreography import (AVAILABLE, EngineHarness,
                                            gift_data, make_rule, switch_cmd)


def _default_trigger():
    """DEFAULT 态触发一台：返回引擎台与命令流。"""
    h = EngineHarness()
    h.engine.on_edtalk_ready(AVAILABLE)
    result = h.engine.decide_trigger("gift", "小心心", gift_data())
    assert result["accepted"] is True
    return h, result["commands"]


class TestFirstStepImmediate:
    def test_start_sequence_first_step_immediate(self):
        """DEFAULT 态触发：第一步 enter 必须 at='immediate'（打断默认轮播）。"""
        h, commands = _default_trigger()
        cmd = switch_cmd(commands)
        assert cmd.at == "immediate", (
            f"序列第一步 at={cmd.at}——segment_end 会挂在默认池长素材"
            f"（数百秒）的自然边界上，画面永不切换")
        assert cmd.segment == "A_入场"  # make_rule enter 段

    def test_advance_step_keeps_rule_switch_at(self):
        """序列内推进：enter→main 步保持规则 switch_at（segment_end 自然衔接）。"""
        h, commands = _default_trigger()
        first = switch_cmd(commands)
        h.engine.on_switch_result(first.step_id, True, h.now + 0.1)
        # enter 时长 4s（make_rule）——推进到 main 步 deadline
        cmds2 = h.engine.on_tick(h.now + 4.1)
        cmd2 = switch_cmd(cmds2)
        assert cmd2.at == "segment_end", (
            f"序列内后续步 at={cmd2.at}——短段自然边界=引擎时长边界，"
            f"必须保持规则 switch_at（强改 immediate 会打断无缝衔接）")

    def test_interrupt_sequence_first_step_immediate(self):
        """他键打断：新序列第一步同样 immediate。"""
        h, commands = _default_trigger()
        first = switch_cmd(commands)
        h.engine.on_switch_result(first.step_id, True, h.now + 0.1)
        # 序列执行中他键触发（权重够，standard 打断）
        result = h.engine.decide_trigger("keyword", "跳舞", {})
        if not result["accepted"]:
            # 权重门让位时跳过（D-4/O3 语义另测）——本守卫只锁打断路径
            return
        cmd = switch_cmd(result["commands"])
        assert cmd.at == "immediate"

    def test_rule_switch_at_immediate_still_immediate(self):
        """规则本就配 immediate：行为不变（force_immediate 只升不降）。"""
        rule = make_rule()
        rule["sequence"]["enter"]["switch_at"] = "immediate"
        h = EngineHarness(rules=[rule])
        h.engine.on_edtalk_ready(AVAILABLE)
        result = h.engine.decide_trigger("gift", "小心心", gift_data())
        assert result["accepted"] is True
        assert switch_cmd(result["commands"]).at == "immediate"
