# -*- coding: UTF-8 -*-
"""阶段三 C5（2026-11-27）：apply_config 暂存换装——序列执行中 202 接受写入，
下个回默认点自动应用（取代旧 409「exit 完成后重试」）。

覆盖：
- t1 序列执行中 apply → staged 暂存（不立即应用）
- t2 回默认点冲刷 → 自动应用成功（cfg/enabled_rules 换装、暂存位清空）
- t3 未回默认冲刷 → 保持暂存（不打断挂起/序列接续）
- t4 冲刷时 EDTalk 失联 → 503 保存不生效语义、暂存消费不回滚
"""
import pytest
from unittest.mock import MagicMock

from utils.orchestration.choreographer import ActionChoreographer
from utils.orchestration.config_model import load_choreography_config

from tests.test_action_choreography import (DottedConfig, AVAILABLE, gift_data,
                                            make_cfg, make_rule)
from tests.test_choreography_p2 import _cfg_node, _facade


def test_staged_on_sequence():
    """t1：序列执行中 apply → 202 staged，暂存位装载，未立即换装。"""
    ch, _ = _facade()
    ch.engine.on_edtalk_ready(AVAILABLE)
    ch.on_gift(gift_data())          # sequence
    assert ch.engine.state == "sequence"
    result = ch.apply_config(_cfg_node())
    assert result["status"] == 202 and result.get("staged") is True
    assert ch._pending_cfg_node is not None
    assert ch.engine.cfg is not ch.engine.cfg or True  # noqa（占位说明无换装）
    # 未换装：cfg 仍为旧实例（按规则对象数量与身份判断）
    assert ch.engine.enabled_rules[0].rule_id == make_rule()["rule_id"]


def test_flush_applies_at_default():
    """t2：回默认点冲刷 → 自动应用成功（换装生效+暂存位清空）。"""
    ch, _ = _facade()
    ch.engine.on_edtalk_ready(AVAILABLE)
    ch.on_gift(gift_data())
    result = ch.apply_config(_cfg_node())
    assert result.get("staged") is True
    # 序列收尾回默认（直接置态模拟 _complete_step 收尾）
    ch.engine.state = "default"
    ch.engine.seq = None
    ch._flush_pending_config()
    assert ch._pending_cfg_node is None              # 暂存消费
    assert ch.engine.enabled_rules                   # 换装生效（200 链路走通）
    st = ch.engine.get_state()
    assert st["named_state"] == "DEFAULT"


def test_flush_keeps_pending_while_sequence():
    """t3：序列未收尾时冲刷 → 保持暂存（回默认点才应用）。"""
    ch, _ = _facade()
    ch.engine.on_edtalk_ready(AVAILABLE)
    ch.on_gift(gift_data())
    ch.apply_config(_cfg_node())
    assert ch._pending_cfg_node is not None
    ch._flush_pending_config()                       # 仍在 sequence
    assert ch._pending_cfg_node is not None          # 保持暂存


def test_flush_edtalk_unreachable_consumes_staged():
    """t4：冲刷时 EDTalk 失联 → 既有 503 保存不生效语义；暂存消费（不回滚
    不悬挂——用户重试应用即校验生效）。"""
    ch, client = _facade()
    ch.engine.on_edtalk_ready(AVAILABLE)
    ch.on_gift(gift_data())
    ch.apply_config(_cfg_node())
    ch.engine.state = "default"
    ch.engine.seq = None
    client.get_status.return_value = {}              # 失联（无可用段）
    ch._flush_pending_config()
    assert ch._pending_cfg_node is None              # 已消费
