# -*- coding: utf-8 -*-
"""
动作编排模块（/autoplan 2026-10-21 批次）

事件驱动的数字人画面编排：打赏/关键词/点歌 → 片段序列（enter→main→exit）+
情绪分级（flowchartTD.md Compile 节点填实）。见 choreographer.py 模块docstring。
"""
from utils.orchestration.choreographer import (ActionChoreographer,
                                               get_choreographer,
                                               register_choreographer)
from utils.orchestration.config_model import (ChoreographyConfig, RuleCfg,
                                              StepCfg, TierCfg,
                                              load_choreography_config,
                                              validate_rule_segments)

__all__ = ["ActionChoreographer", "get_choreographer", "register_choreographer",
           "ChoreographyConfig", "RuleCfg", "StepCfg", "TierCfg",
           "load_choreography_config", "validate_rule_segments"]
