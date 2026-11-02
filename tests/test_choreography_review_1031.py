# -*- coding: UTF-8 -*-
"""
动作编排审查批次守卫测试（2026-10-31，任务书：动作管理/编排规则参数与系统流程对接）

审查发现与修复：
- F1 回默认 lip/repair/gaze 联动复位缺失：序列 lip-off 步把三开关置 off 后，
  RestoreDefault 回默认画面无复位路径——泄漏为「默认画面有音频不张嘴」。
  修复=RestoreDefault 执行时 _apply_lip_linkage(True)（去抖缓存仅离开 off 态下发）。
- F2 计数窗口退役：reset_after_s 任务书弃用移除（UI 控件/引擎衰减/写出链/
  退役键登记），触发计次不再随时间清零。
- config.example.json choreography 节三退役键清理。

其余核对通过（无行为改动）：三模式消费链（rotate/random→forward、single→pingpong）、
口型推理预处理分叉（lip_flag→crop_head_video 完整链 / 桩四件套画外音）、
sidecar lip_flag→编排联动→EDTalk /lip/params 消费链、素材删除引用保护。
"""
import json

from unittest.mock import MagicMock

from utils.orchestration.choreographer import ActionChoreographer, RestoreDefault
from utils.orchestration.config_model import load_choreography_config

from tests.test_action_choreography import (DottedConfig, AVAILABLE, make_cfg,
                                            make_rule)

BASE = "d:/AI/AUTOlive"


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


# ---------- 源码断言守卫 ----------

class TestRetiredGuards:
    def test_ui_no_counter_window(self):
        """前端编排页：计数窗口控件移除，reset_after_s 仅剩 apply 清除行。"""
        src = _read(f"{BASE}/frontend/ui/tabs/choreography.py")
        assert "计数窗口(s)" not in src  # 输入控件标签已删
        assert src.count("reset_after_s") == 1
        assert 'choreo_node.pop("reset_after_s"' in src

    def test_config_model_retired(self):
        """配置模型：退役告警+恒 0.0（引擎不消费），GAP-2 解析块移除。"""
        src = _read(f"{BASE}/utils/orchestration/config_model.py")
        assert "reset_after_s 已退役" in src
        assert "reset_after_s: float = 0.0" in src
        assert "reset_after_s=0.0" in src
        assert "raw_reset" not in src  # 旧解析块已删

    def test_engine_no_reset_after_consumption(self):
        """引擎：计数衰减消费块移除（cfg.reset_after_s 零读取）。"""
        src = _read(f"{BASE}/utils/orchestration/choreographer.py")
        assert "self.cfg.reset_after_s" not in src

    def test_retired_key_registered(self):
        """前端退役键登记：choreography.reset_after_s。"""
        src = _read(f"{BASE}/frontend/main.py")
        assert '("choreography", "reset_after_s")' in src

    def test_example_config_cleaned(self):
        """示例配置：choreography 节三个退役键不再出现。"""
        src = _read(f"{BASE}/config.example.json")
        for key in ("reset_after_s", "min_retrigger_s", "min_sequence_hold_s"):
            assert key not in src
        node = json.loads(src)["choreography"]
        assert "reset_after_s" not in node


# ---------- 行为断言：F1 回默认联动复位 ----------

def _facade():
    client = MagicMock()
    client.switch_segment.return_value = {"ok": True}
    client.schedule.return_value = {"ok": True}
    client.set_lip_params.return_value = {"ok": True}
    client.set_repair_params.return_value = {"ok": True}
    client.set_gaze_params.return_value = {"ok": True}
    client.get_status.return_value = {"available_segments": sorted(AVAILABLE),
                                      "lip_mode": "on"}
    ch = ActionChoreographer(DottedConfig({"choreography": make_cfg()}), client,
                             audio_enqueue=MagicMock(),
                             thanks_generate=MagicMock(return_value=None),
                             clock=lambda: 1000.0)
    ch._worker = None
    ch._ensure_worker = lambda enable=None: False
    return ch, client


class TestRestoreLipReset:
    def test_restore_resets_lip_off_to_on(self):
        """序列 lip-off 步把 lip 置 off 后，RestoreDefault 执行复位 on。"""
        ch, client = _facade()
        ch._lip_mode_cache = "off"  # 模拟 lip-off 步联动后的残留态
        ch._repair_mode_cache = "off"
        ch._gaze_mode_cache = "off"
        ch._execute([RestoreDefault(["1", "55"], loop="forward")])
        client.set_lip_params.assert_called_with("on")
        client.schedule.assert_called_once()

    def test_restore_reset_debounced(self):
        """去抖：lip 已 on 时回默认不再重复下发。"""
        ch, client = _facade()
        ch._lip_mode_cache = "on"
        ch._execute([RestoreDefault(["1", "55"], loop="forward")])
        client.set_lip_params.assert_not_called()

    def test_restore_resets_aux_flags_to_config_initial(self):
        """repair/gaze 复位=webui 配置初值（edtalk_realtime.face_repair/gaze_correction）。"""
        ch, client = _facade()
        ch._config = DottedConfig({"edtalk_realtime": {"face_repair": "adaptive",
                                                       "gaze_correction": "on"}})
        ch._lip_mode_cache = "off"
        ch._repair_mode_cache = "off"
        ch._gaze_mode_cache = "off"
        ch._execute([RestoreDefault(["1", "55"], loop="forward")])
        client.set_repair_params.assert_called_with("adaptive")
        client.set_gaze_params.assert_called_with("on")


# ---------- 行为断言：计数窗口退役语义 ----------

class TestCounterWindowRetired:
    def test_config_value_never_consumed(self):
        """退役键任意值→cfg 恒 0.0（引擎不消费，计次不随时间衰减）。"""
        for raw in (300, 0, -5, "abc"):
            assert load_choreography_config(make_cfg(reset_after_s=raw)).reset_after_s == 0.0

    def test_default_rule_unchanged(self):
        """退役不影响规则装载（新语义规则正常解析）。"""
        cfg = load_choreography_config(make_cfg(rules=[make_rule(pred_version="new")]))
        assert cfg.rules[0].rule_id == "girl_A"
        assert cfg.rules[0].pred_version == "new"
