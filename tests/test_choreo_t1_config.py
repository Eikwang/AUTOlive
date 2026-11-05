"""T1 装载校验扩展守卫（2026-11-05 编排优化批次）。

覆盖裁决条款：
- d4：VALID_TRIGGER_TYPES 扩展 dynamic_text（keyword_hub 源直连，match 可空）
- d14：continuous_trigger_window_s（默认 30；≥0 数值；0=禁用；非法回退+告警）
- d10/e11：arbitration_weights 类型键级校验（非法键/值剔除+聚合告警，不整体拒绝）
- d22 基座：arbitration_enable（默认 true；非布尔回退+告警）
- d14/d21：config.example.json 同步（choreography 键 + priority_mapping.dynamic_text=15）

纯装载测试（不经 TestClient 写盘）——无生产配置污染面（conftest 兜底之外零依赖）。
"""
import json
from pathlib import Path
from unittest import mock

import pytest

from utils.orchestration import config_model as cm
from utils.orchestration.config_model import (
    VALID_TRIGGER_TYPES,
    load_choreography_config,
)


@pytest.fixture
def warn_spy():
    """loguru 告警捕获（项目 logger 为 loguru，不经 stdlib——caplog 不可用）。"""
    with mock.patch.object(cm, "logger") as m:
        yield m


def _warn_texts(spy):
    return [" ".join(str(a) for a in c.args) for c in spy.warning.call_args_list]


def _cfg(node):
    """构造 choreography 节并装载（load_choreography_config 接受任意带 .get 的包装）。"""
    return load_choreography_config({"choreography": node})


def _rule(rid="r1", t="gift", match="x"):
    """最小合法规则（legacy 语义：none 音轨步须 duration_s>0）。"""
    return {"rule_id": rid, "trigger_type": t, "match": match,
            "sequence": {"main": {"segment": "44", "switch_at": "immediate",
                                  "duration_s": 3}}}


# ---------------------------------------------------------------- d4 类型扩展

def test_dynamic_text_in_valid_trigger_types():
    assert "dynamic_text" in VALID_TRIGGER_TYPES


def test_dynamic_text_rule_loads_without_match():
    """d4：dynamic_text 源直连——match 缺失/空均不拒绝。"""
    node = {"enable": True, "rules": [_rule(rid="dt1", t="dynamic_text", match="")]}
    cfg = _cfg(node)
    assert len(cfg.rules) == 1
    assert cfg.rules[0].trigger_type == "dynamic_text"
    assert getattr(cfg, "_rejections", []) == []


def test_unknown_trigger_type_still_rejected():
    """类型扩展不得放松既有值域守卫。"""
    node = {"enable": True, "rules": [_rule(rid="bad1", t="bogus")]}
    cfg = _cfg(node)
    assert cfg.rules == []
    assert any("trigger_type" in r.get("reason", "") for r in cfg._rejections)


# ------------------------------------------------- d14 连续触发窗口键

def test_window_default_30_when_missing():
    cfg = _cfg({"enable": True, "rules": []})
    assert cfg.continuous_trigger_window_s == 30.0


@pytest.mark.parametrize("raw,expect", [(45, 45.0), (0, 0.0), (12.5, 12.5), ("90", 90.0)])
def test_window_valid_values_honored(raw, expect):
    """0=禁用语义保留（0 是合法值不是回退值）。"""
    cfg = _cfg({"enable": True, "continuous_trigger_window_s": raw, "rules": []})
    assert cfg.continuous_trigger_window_s == expect


@pytest.mark.parametrize("raw", ["abc", -5, -0.1, float("nan"), float("inf"), [30]])
def test_window_invalid_falls_back_with_warning(raw, warn_spy):
    cfg = _cfg({"enable": True, "continuous_trigger_window_s": raw, "rules": []})
    assert cfg.continuous_trigger_window_s == 30.0
    assert any("continuous_trigger_window_s" in t for t in _warn_texts(warn_spy))


# ------------------------------------------------- d10/e11 仲裁权重覆盖键

def test_weights_valid_keys_honored():
    cfg = _cfg({"enable": True,
                "arbitration_weights": {"gift": 25, "song": 20, "dynamic_text": 15},
                "rules": []})
    assert cfg.arbitration_weights == {"gift": 25.0, "song": 20.0, "dynamic_text": 15.0}


def test_weights_default_empty():
    cfg = _cfg({"enable": True, "rules": []})
    assert cfg.arbitration_weights == {}


def test_weights_invalid_dropped_with_warning(warn_spy):
    """e11：未知类型键/非数/负值剔除，合法键照常生效（不整体拒绝）。"""
    cfg = _cfg({"enable": True,
                "arbitration_weights": {"gift": 25, "bogus": 5, "song": -1,
                                        "keyword": "x", "comment": 20},
                "rules": []})
    assert cfg.arbitration_weights == {"gift": 25.0}
    texts = _warn_texts(warn_spy)
    assert any("arbitration_weights" in t for t in texts)
    assert any("bogus" in t for t in texts)


def test_weights_non_dict_ignored_with_warning(warn_spy):
    cfg = _cfg({"enable": True, "arbitration_weights": "oops", "rules": []})
    assert cfg.arbitration_weights == {}
    assert any("arbitration_weights" in t for t in _warn_texts(warn_spy))


# ------------------------------------------------- d22 仲裁总开关基座

def test_arbitration_enable_default_true():
    assert _cfg({"enable": True, "rules": []}).arbitration_enable is True


def test_arbitration_enable_explicit_false_honored():
    assert _cfg({"enable": True, "arbitration_enable": False,
                 "rules": []}).arbitration_enable is False


def test_arbitration_enable_non_bool_falls_back(warn_spy):
    assert _cfg({"enable": True, "arbitration_enable": "yes",
                 "rules": []}).arbitration_enable is True
    assert any("arbitration_enable" in t for t in _warn_texts(warn_spy))


# ------------------------------------------------- d14/d21 example 同步

def test_example_config_carries_new_keys():
    """config.example.json：choreography 连续触发窗口键（d14 落 example）+
    priority_mapping.dynamic_text=15（d21 音频面对齐编排面 d10）。"""
    example = json.loads(Path("config.example.json").read_text(encoding="utf-8"))
    ch = example.get("choreography") or {}
    assert ch.get("continuous_trigger_window_s") == 30
    pm = (example.get("filter") or {}).get("priority_mapping") or {}
    assert pm.get("dynamic_text") == 15


def test_full_node_round_trip():
    """三新键+dynamic_text 规则整节点装载往返（webui「应用」整节提交面）。"""
    node = {"enable": True, "default_mode": "rotate", "default_segments": ["44"],
            "step_timeout_s": 60, "continuous_trigger_window_s": 15,
            "arbitration_weights": {"gift": 30}, "arbitration_enable": False,
            "rules": [_rule(rid="g1"), _rule(rid="dt1", t="dynamic_text", match="")]}
    cfg = _cfg(node)
    assert cfg.continuous_trigger_window_s == 15.0
    assert cfg.arbitration_weights == {"gift": 30.0}
    assert cfg.arbitration_enable is False
    assert {r.rule_id for r in cfg.rules} == {"g1", "dt1"}


# ------------------------------------------------- e11 提交面端到端（POST /rules）

class _Dotted:
    def __init__(self, cfg):
        self._cfg = cfg

    def get(self, *keys):
        cur = self._cfg
        for k in keys:
            if not isinstance(cur, dict) or k not in cur:
                return None
            cur = cur[k]
        return cur


def test_post_rules_persists_new_keys_and_dynamic_text(tmp_path, monkeypatch):
    """整节替换提交面端到端：新键+dynamic_text 规则经 apply_config 热应用并
    原样持久化（写者= _write_choreography_section，写者矩阵不回归）。
    写者路径 monkeypatch 到 tmp_path（会话级 conftest 兜底之外的本测自隔离）。"""
    from unittest.mock import MagicMock

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from utils.orchestration import api as choreo_api
    from utils.orchestration.choreographer import ActionChoreographer

    node = {"enable": True, "default_mode": "rotate", "default_segments": ["44"],
            "step_timeout_s": 60, "continuous_trigger_window_s": 20,
            "arbitration_weights": {"gift": 30, "bogus": 9},
            "arbitration_enable": True,
            "rules": [_rule(rid="dt1", t="dynamic_text", match="")]}

    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps({"choreography": {"enable": False}}), encoding="utf-8")
    monkeypatch.setattr(choreo_api, "_config_path", lambda: str(cfg_file))

    client_mock = MagicMock()
    client_mock.get_status.return_value = {"available_segments": ["44"]}
    ch = ActionChoreographer(_Dotted({"choreography": node}), client_mock,
                             audio_enqueue=MagicMock(),
                             thanks_generate=MagicMock(return_value=None),
                             clock=lambda: 1000.0)
    ch._worker = None
    ch._ensure_worker = lambda enable=None: True
    monkeypatch.setattr(choreo_api, "_choreographer", lambda: ch)

    app = FastAPI()
    choreo_api.register_choreography_routes(app, _Dotted({"choreography": node}))
    test_client = TestClient(app)
    test_client.app.dependency_overrides[choreo_api._require_loopback] = lambda: None

    resp = test_client.post("/choreography/rules", json=node)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body.get("applied") is True, body
    assert body.get("persisted") is True

    persisted = json.loads(cfg_file.read_text(encoding="utf-8"))["choreography"]
    assert persisted["continuous_trigger_window_s"] == 20
    assert persisted["arbitration_weights"] == {"gift": 30, "bogus": 9}  # 原样持久化（装载侧剔除 bogus）
    assert persisted["rules"][0]["trigger_type"] == "dynamic_text"
    # 运行态：重新装载持久化节点，bogus 被剔除、窗口值生效
    reloaded = load_choreography_config(_Dotted({"choreography": persisted}))
    assert reloaded.continuous_trigger_window_s == 20.0
    assert reloaded.arbitration_weights == {"gift": 30.0}
    assert [r.trigger_type for r in reloaded.rules] == ["dynamic_text"]
