# -*- coding: UTF-8 -*-
"""
P2 管理页后端回归（/choreography/* 路由 + 热换装 apply_config + 写者矩阵）

覆盖：
- apply_config：DEFAULT 态换装（计数保留同名键/剔失效键）、序列执行中拒绝 409、
  EDTalk 不可达 503、结构拒绝清单、段名缺口禁用、DEFAULT 重下发
- _write_choreography_section：原子写+读回一致（tmp 隔离，不污染真实 config）
- 路由：GET /choreography/status（装配/未装配）、POST /choreography/rules
  （applied+persisted / 409 / 503 / 422）
- get_state mood 字段（P2 状态条情绪档显示）
"""

import json
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import utils.orchestration.api as choreo_api
from utils.orchestration.choreographer import (ActionChoreographer, ChoreoEngine,
                                               RestoreDefault)
from utils.orchestration.config_model import load_choreography_config

from tests.test_action_choreography import (DottedConfig, AVAILABLE, gift_data,
                                            make_cfg, make_rule)


def _cfg_node(**over):
    node = {"enable": True, "default_mode": "rotate", "default_segments": ["1", "55"],
            "min_retrigger_s": 2, "min_sequence_hold_s": 5, "step_timeout_s": 60,
            "rules": [make_rule()]}
    node.update(over)
    return node


def _facade(node=None, worker=False):
    client = MagicMock()
    client.switch_segment.return_value = {"ok": True}
    client.schedule.return_value = {"ok": True}
    client.get_status.return_value = {"available_segments": sorted(AVAILABLE)}
    ch = ActionChoreographer(DottedConfig({"choreography": node or _cfg_node()}), client,
                             audio_enqueue=MagicMock(),
                             thanks_generate=MagicMock(return_value=None),
                             clock=lambda: 1000.0)
    if not worker:
        ch._worker = None  # 停掉真线程，同步驱动
        # GAP-1（2026-10-23 补完）：apply_config 现在会补启动 worker——本文件场景
        # 模拟"worker 已在运行"（幂等成功语义，与初始真线程行为一致），不触发
        # 回滚分支；热启用真路径由 tests/test_choreography_completion.py::TestHotEnable 专测。
        ch._ensure_worker = lambda enable=None: True
    return ch, client


class TestApplyConfig:
    def test_default_state_swap_keeps_same_id_counters(self):
        ch, client = _facade()
        ch.engine.on_edtalk_ready(AVAILABLE)
        ch.on_gift(gift_data())          # 计数 girl_A=1，序列执行中
        # 推完序列回 DEFAULT（直接置态模拟）
        ch.engine.state = "default"
        ch.engine.seq = None
        result = ch.apply_config(_cfg_node())  # 同 ID 规则换装
        assert result["applied"] is True and result["status"] == 200
        assert result["enabled_rules"] == ["girl_A"]
        assert ch.engine.counters.get("girl_A") == 1  # 同名键保留
        # DEFAULT 重新下发（worker=None：手动排空命令队列驱动执行）
        while True:
            try:
                item = ch._events.get_nowait()
            except Exception:
                break
            if isinstance(item, RestoreDefault):
                ch._execute([item])
        client.schedule.assert_called()

    def test_swap_drops_stale_counters(self):
        ch, _ = _facade()
        ch.engine.on_edtalk_ready(AVAILABLE)
        ch.engine.counters["ghost_rule"] = 5  # 失效键
        ch.engine.state = "default"
        ch.engine.seq = None
        ch.apply_config(_cfg_node())
        assert "ghost_rule" not in ch.engine.counters

    def test_sequence_active_rejected_409(self):
        ch, _ = _facade()
        ch.engine.on_edtalk_ready(AVAILABLE)
        ch.on_gift(gift_data())  # state=sequence
        result = ch.apply_config(_cfg_node())
        assert result["applied"] is False and result["status"] == 409
        assert "执行中" in result["message"]

    def test_edtalk_unreachable_503(self):
        ch, client = _facade()
        client.get_status.return_value = {}  # 无可用段
        result = ch.apply_config(_cfg_node())
        assert result["applied"] is False and result["status"] == 503
        assert "EDTalk 未连接" in result["message"]

    def test_structural_rejections_reported(self):
        ch, _ = _facade()
        ch.engine.state = "default"
        node = _cfg_node(rules=[make_rule(),
                                make_rule(rule_id="bad", trigger_type="entrance")])
        result = ch.apply_config(node)
        assert result["applied"] is True  # 好规则照常生效
        assert result["rejected"] and result["rejected"][0]["rule_id"] == "bad"

    def test_segment_gap_disables_rule(self):
        ch, client = _facade()
        client.get_status.return_value = {"available_segments": ["44", "55"]}  # A 段缺失
        ch.engine.state = "default"
        result = ch.apply_config(_cfg_node())
        assert result["applied"] is True
        assert result["enabled_rules"] == []  # girl_A 段（A_入场 等）不在 → 禁用
        assert ch.engine.enabled_rules == []

    def test_mood_in_state(self):
        # P2 状态条 mood 显示（义务：状态条=当前模式/活动序列/计数/mood/降级）
        tiers = [{"max_count": 6, "mood": "angry"}]
        node = _cfg_node(rules=[make_rule(emotion_tiers=tiers)])
        ch, _ = _facade(node=node)
        ch.engine.on_edtalk_ready(AVAILABLE)
        ch.on_gift(gift_data())
        st = ch.get_state()
        assert st["mood"] == "angry"
        assert "mood" in st  # 默认无档时为空串而非缺失


class TestWriteSection:
    def test_atomic_write_and_readback(self, tmp_path, monkeypatch):
        cfg_file = tmp_path / "config.json"
        cfg_file.write_text(json.dumps({"other": 1, "choreography": {"enable": False}}),
                            encoding="utf-8")
        monkeypatch.setattr(choreo_api, "_config_path", lambda: str(cfg_file))
        ok = choreo_api._write_choreography_section({"enable": True, "rules": [1]})
        assert ok is True
        data = json.loads(cfg_file.read_text(encoding="utf-8"))
        assert data["other"] == 1 and data["choreography"]["enable"] is True
        assert not list(tmp_path.glob("*.tmp"))  # 无残留临时文件

    def test_write_failure_returns_false(self, tmp_path, monkeypatch):
        monkeypatch.setattr(choreo_api, "_config_path", lambda: str(tmp_path / "no_dir" / "c.json"))
        assert choreo_api._write_choreography_section({"enable": True}) is False


class TestRoutes:
    def _app(self, ch, node):
        app = FastAPI()
        cfg = DottedConfig({"choreography": node})
        choreo_api.register_choreography_routes(app, cfg)
        client = TestClient(app)
        # GAP-3（2026-10-23）：POST 路由现在带回环校验依赖（非回环 403）——本文件
        # 测路由业务逻辑，放行门禁；403 行为由 tests/test_choreography_completion.py::TestEndpoints 专测。
        client.app.dependency_overrides[choreo_api._require_loopback] = lambda: None
        return client

    def test_status_assembled(self):
        ch, _ = _facade()
        ch.engine.on_edtalk_ready(AVAILABLE)
        client = self._app(ch, _cfg_node())
        with patch("utils.orchestration.api._choreographer", return_value=ch):
            r = client.get("/choreography/status")
        assert r.status_code == 200
        body = r.json()
        assert body["enable"] is True and body["configured_rules"] == 1
        assert body["persisted_enable"] is True

    def test_status_unassembled_503(self):
        client = self._app(None, _cfg_node())
        with patch("utils.orchestration.api._choreographer", return_value=None):
            r = client.get("/choreography/status")
        assert r.status_code == 503

    def test_rules_apply_and_persist(self, tmp_path, monkeypatch):
        cfg_file = tmp_path / "config.json"
        cfg_file.write_text("{}", encoding="utf-8")
        monkeypatch.setattr(choreo_api, "_config_path", lambda: str(cfg_file))
        ch, client_mock = _facade()
        client = self._app(ch, _cfg_node())
        with patch("utils.orchestration.api._choreographer", return_value=ch):
            r = client.post("/choreography/rules", json=_cfg_node())
        body = r.json()
        assert r.status_code == 200 and body["success"] is True
        assert body["applied"] is True and body["persisted"] is True
        saved = json.loads(cfg_file.read_text(encoding="utf-8"))
        assert saved["choreography"]["enable"] is True

    def test_rules_409_when_sequence_active(self, tmp_path, monkeypatch):
        monkeypatch.setattr(choreo_api, "_config_path", lambda: str(tmp_path / "c.json"))
        ch, _ = _facade()
        ch.engine.on_edtalk_ready(AVAILABLE)
        ch.on_gift(gift_data())  # sequence
        client = self._app(ch, _cfg_node())
        with patch("utils.orchestration.api._choreographer", return_value=ch):
            r = client.post("/choreography/rules", json=_cfg_node())
        assert r.status_code == 200  # HTTP 层 200，body.status_code=409（页面按 body 分支）
        assert r.json()["applied"] is False and r.json()["status"] == 409

    def test_rules_422_non_object(self):
        client = self._app(None, _cfg_node())
        with patch("utils.orchestration.api._choreographer", return_value=None):
            r = client.post("/choreography/rules", json="not-a-dict")
        assert r.status_code == 422

    def test_rules_503_unassembled(self):
        client = self._app(None, _cfg_node())
        with patch("utils.orchestration.api._choreographer", return_value=None):
            r = client.post("/choreography/rules", json=_cfg_node())
        assert r.status_code == 503
