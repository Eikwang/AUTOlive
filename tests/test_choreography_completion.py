# -*- coding: UTF-8 -*-
"""
动作编排补完批次回归测试（/autoplan 2026-10-23 批次，义务块 GAP-1/2/3/4 + F1-F7 + spec C-1/C-3）

覆盖义务块验证条款：
- GAP-1：热启用死路（_ensure_worker 幂等+apply 补启动+失败回滚+ready/worker_started 回填）
- GAP-2：计数时间窗口（reset_after_s 解析校验+超窗清零+窗内累积+0 禁用+锚点含节流拒绝 F5）
- GAP-3：模拟触发（rule_id 形态合成载荷 F3/裸 API below_min_price/reason 枚举 F2/端点 422/403/503/200 诚实失败）
- GAP-4：get_state 观测字段（pending_requeue/step_remaining_s/worker_alive/queue_depth）
- F1：恢复重试竞态守卫（sequence 态放弃重试）
- F6：403 门范围（POST 拦/GET status 只读放行/回环谓词 ::1 与 127.0.0.2）

时间敏感断言全部用显式 now 参数驱动（无 sleep；真线程测试仅 worker 启动断言用短轮询）。
"""

import threading
import time
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import utils.orchestration.api as choreo_api
from utils.orchestration.choreographer import (ActionChoreographer, ChoreoEngine,
                                               RestoreDefault, SwitchSegment)
from utils.orchestration.config_model import load_choreography_config

from tests.test_action_choreography import (DottedConfig, AVAILABLE, gift_data,
                                            make_cfg, make_rule)
from tests.test_choreography_p2 import _cfg_node


def _facade(node=None, worker=False):
    """P2 _facade 同构（worker=False 额外封死 _ensure_worker——模拟旧测试环境，
    新热启用行为由本文件 TestHotEnable 专测）。"""
    client = MagicMock()
    client.switch_segment.return_value = {"ok": True}
    client.schedule.return_value = {"ok": True}
    client.get_status.return_value = {"available_segments": sorted(AVAILABLE)}
    ch = ActionChoreographer(DottedConfig({"choreography": node or _cfg_node()}), client,
                             audio_enqueue=MagicMock(),
                             thanks_generate=MagicMock(return_value=None),
                             clock=lambda: 1000.0)
    if not worker:
        ch._worker = None
        ch._ensure_worker = lambda enable=None: False  # GAP-1 后 apply 会补启动——旧测试环境封死
    return ch, client


def _facade_hot(node):
    """真 worker 测试台（不封死 _ensure_worker）：初始 enable=false → worker 未启动，
    apply(enable=true) 走真实热启用路径（GAP-1 专测）。"""
    client = MagicMock()
    client.switch_segment.return_value = {"ok": True}
    client.schedule.return_value = {"ok": True}
    client.get_status.return_value = {"available_segments": sorted(AVAILABLE)}
    ch = ActionChoreographer(DottedConfig({"choreography": node}), client,
                             audio_enqueue=MagicMock(),
                             thanks_generate=MagicMock(return_value=None),
                             clock=lambda: 1000.0)
    return ch, client


def _engine_harness(rules=None, **over):
    from tests.test_action_choreography import EngineHarness
    return EngineHarness(rules=rules, **over)


def drain(ch):
    """同步排空命令队列（无 worker 测试环境），返回全部命令。"""
    out = []
    while True:
        try:
            out.append(ch._events.get_nowait())
        except Exception:
            break
    return out


def wait_until(pred, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(0.02)
    return pred()


# ---------- GAP-2：reset_after_s 配置解析 ----------

class TestResetAfterConfig:
    def test_reset_after_default_300(self):
        cfg = load_choreography_config(make_cfg())
        assert cfg.reset_after_s == 300.0

    def test_reset_after_zero_valid(self):
        cfg = load_choreography_config(make_cfg(reset_after_s=0))
        assert cfg.reset_after_s == 0.0  # 0=禁用衰减（合法值，非回退）

    def test_reset_after_negative_falls_back(self):
        cfg = load_choreography_config(make_cfg(reset_after_s=-5))
        assert cfg.reset_after_s == 300.0

    def test_reset_after_non_numeric_falls_back(self):
        cfg = load_choreography_config(make_cfg(reset_after_s="abc"))
        assert cfg.reset_after_s == 300.0


# ---------- GAP-2 + F5：计数衰减（伪时钟） ----------

class TestCounterDecay:
    def test_decay_after_window(self):
        h = _engine_harness()
        d1 = h.trigger_gift()
        assert h.engine.counters["girl_A"] == 1
        h.advance(301)  # > reset_after_s=300
        h.engine.on_restore_result(True, h.now) if False else None
        # 直接清场回 DEFAULT（跳过序列推进——衰减判定只依赖 decide_trigger 路径）
        h.engine.state = "default"
        h.engine.seq = None
        d2 = h.trigger_gift()
        assert h.engine.counters["girl_A"] == 1  # 超窗清零后从 1 重新累计

    def test_no_decay_within_window(self):
        h = _engine_harness()
        h.trigger_gift()
        assert h.engine.counters["girl_A"] == 1
        h.advance(299)  # < 300 窗口
        h.engine.state = "default"
        h.engine.seq = None
        h.trigger_gift()
        assert h.engine.counters["girl_A"] == 2  # 窗口内累积

    def test_reset_after_zero_disables_decay(self):
        h = _engine_harness(reset_after_s=0)
        h.trigger_gift()
        h.advance(100000)
        h.engine.state = "default"
        h.engine.seq = None
        h.trigger_gift()
        assert h.engine.counters["girl_A"] == 2  # 0=禁用：跨超长间隔仍累积

    def test_anchor_includes_throttled_attempts(self):
        """F5：窗口锚点=最后一次尝试（含被节流拒绝的）——拒绝续窗。"""
        h = _engine_harness()
        h.trigger_gift()                    # t=1000 计数=1
        h.advance(250)
        h.trigger_gift()                    # t=1250 被节流（min_retrigger_s=2>0 距离）……250>2 通过节流！
        # 修正场景：需要真正被节流的尝试——紧凑间隔
        h2 = _engine_harness()
        h2.trigger_gift()                   # t0 计数=1，key_last=t0
        h2.advance(1)                       # t0+1 < min_retrigger_s=2 → 节流拒绝（key_last 刷新为 t0+1）
        h2.trigger_gift()
        h2.advance(299)                     # t0+300：距锚点 t0+1 = 299 < 300 → 不清零
        h2.engine.state = "default"
        h2.engine.seq = None
        h2.trigger_gift()
        assert h2.engine.counters["girl_A"] == 2  # 锚点=被拒绝的尝试 t0+1（若锚=t0 则 300>300 清零为 1）


# ---------- F1：恢复重试竞态守卫 ----------

class TestRestoreRetryGuard:
    def test_retry_abandoned_when_sequence_active(self):
        h = _engine_harness()
        # DEFAULT 态恢复失败 → 重试入列（attempts=1）
        cmds = h.engine.on_restore_result(False, h.now)
        assert any(isinstance(c, RestoreDefault) for c in cmds)
        # 此间新序列启动（state=sequence）
        d = h.trigger_gift()
        assert h.engine.state == "sequence"
        # 重试再次失败（sequence 态）→ F1 守卫放弃，不下发 RestoreDefault
        cmds2 = h.engine.on_restore_result(False, h.now)
        assert not any(isinstance(c, RestoreDefault) for c in cmds2)
        assert h.engine._restore_attempts == 0  # 放弃时计数归零（新序列获全新预算）

    def test_retry_continues_in_default(self):
        h = _engine_harness()
        cmds = h.engine.on_restore_result(False, h.now)
        assert any(isinstance(c, RestoreDefault) for c in cmds)  # DEFAULT 态照常重试


# ---------- GAP-1：热启用（_ensure_worker/apply） ----------

class TestHotEnable:
    def test_ensure_worker_idempotent(self):
        ch, client = _facade(worker=True)
        try:
            assert ch.worker_alive()
            w1 = ch._worker
            assert ch._ensure_worker() is True
            assert ch._worker is w1  # 幂等：同一线程对象，不二次 start
        finally:
            ch.shutdown()

    def test_apply_hot_enable_starts_worker(self):
        """GAP-1 核心：初始 enable=false → apply enable=true → worker 启动并消费 RestoreDefault。"""
        node = _cfg_node(enable=False)
        ch, client = _facade_hot(node)
        assert ch._worker is None  # 初始关闭：worker 未启动
        result = ch.apply_config(_cfg_node(enable=True))
        assert result["applied"] is True and result["status"] == 200
        assert result.get("worker_started") is True
        assert result.get("ready") in (True, False)  # ready 字段回填（Eng 钉子）
        try:
            assert wait_until(lambda: ch.worker_alive())  # 热启用真生效
            # worker 就绪探测（mock get_status 即时就绪）→ DEFAULT 初始化 RestoreDefault → schedule 被调
            assert wait_until(lambda: client.schedule.called)
        finally:
            ch.shutdown()

    def test_apply_worker_start_failure_rolls_back(self, monkeypatch):
        node = _cfg_node(enable=False)
        ch, client = _facade_hot(node)
        old_engine = ch.engine
        old_counters = dict(ch.engine.counters)

        def boom(self):
            raise RuntimeError("boom")
        monkeypatch.setattr(threading.Thread, "start", boom)
        result = ch.apply_config(_cfg_node(enable=True))
        monkeypatch.undo()
        assert result["applied"] is False and result["status"] == 503
        assert result.get("worker_started") is False
        assert ch.engine is old_engine  # 整体回滚：engine 保持旧实例
        assert ch.engine.counters == old_counters
        assert ch.cfg.enable is False   # cfg 同样未换装

    def test_apply_enable_false_no_worker_needed(self):
        """enable=false 的 apply=合法关闭：无需 worker，不触发回滚。"""
        ch, client = _facade_hot(_cfg_node(enable=True))
        ch.shutdown()
        ch._worker = None
        result = ch.apply_config(_cfg_node(enable=False))
        assert result["applied"] is True and result["status"] == 200


# ---------- GAP-3/F2/F3：模拟触发（方法层） ----------

class TestSimulateTrigger:
    def test_rule_id_form_synthesizes_min_price(self):
        """F3：rule_id 形态合成 total_price:=rule.min_price——min_price>0 规则必可触发。"""
        node = _cfg_node(rules=[make_rule(min_price=100)])
        ch, client = _facade(node=node)
        ch._stop.set()  # 封死真线程消费（worker 仍在——用 worker_alive 需真线程；改用同步驱动）
        ch._worker = None
        ch._ensure_worker = lambda enable=None: False
        # worker 未启动 → disabled！rule_id 形态测试需要 worker_alive……
        # 修正：模拟 worker 存活（patch worker_alive）走同步判定路径
        ch.worker_alive = lambda: True
        result = ch.simulate_trigger({"rule_id": "girl_A"})
        assert result["accepted"] is True and result["reason"] == "accepted"
        assert result["rule_id"] == "girl_A"
        cmds = drain(ch)
        assert any(isinstance(c, SwitchSegment) for c in cmds)

    def test_raw_form_below_min_price(self):
        """裸 API 形态 total_price 缺省 0：min_price>0 规则不命中=诚实门槛语义。"""
        node = _cfg_node(rules=[make_rule(min_price=100)])
        ch, client = _facade(node=node)
        ch._worker = None
        ch.worker_alive = lambda: True
        result = ch.simulate_trigger({"trigger_type": "gift", "match_value": "小心心",
                                      "gift": {"gift_name": "小心心"}})
        assert result["accepted"] is False and result["reason"] == "below_min_price"

    def test_raw_form_total_price_zero_semantics(self):
        node = _cfg_node(rules=[make_rule(min_price=0)])
        ch, _ = _facade(node=node)
        ch._worker = None
        ch.worker_alive = lambda: True
        result = ch.simulate_trigger({"trigger_type": "gift", "match_value": "小心心",
                                      "gift": {"gift_name": "小心心", "total_price": 0}})
        assert result["accepted"] is True  # min_price=0：0>=0 命中

    def test_unknown_rule_no_match(self):
        ch, _ = _facade()
        ch._worker = None
        ch.worker_alive = lambda: True
        result = ch.simulate_trigger({"rule_id": "no_such"})
        assert result["accepted"] is False and result["reason"] == "no_match"

    def test_disabled_when_worker_dead(self):
        """C-1/DX channel：worker 未启动 → 200 语义 accepted=false+reason=disabled（诚实失败）。"""
        ch, _ = _facade(node=_cfg_node(enable=False))  # enable=false：无真线程无 DEFAULT 初始化入队
        result = ch.simulate_trigger({"rule_id": "girl_A"})
        assert result["accepted"] is False and result["reason"] == "disabled"
        assert drain(ch) == []  # 指令不进队列（无消费者堆积）

    def test_throttled_reason_with_retry_after(self):
        ch, _ = _facade()
        ch._worker = None
        ch.worker_alive = lambda: True
        r1 = ch.simulate_trigger({"trigger_type": "keyword", "match_value": "跳舞"})
        assert r1["accepted"] is False  # 无 keyword 规则 → no_match
        node = _cfg_node(rules=[make_rule(rule_id="kw", trigger_type="keyword", match="跳舞")])
        ch2, _ = _facade(node=node)
        ch2._worker = None
        ch2.worker_alive = lambda: True
        r2 = ch2.simulate_trigger({"trigger_type": "keyword", "match_value": "跳舞"})
        assert r2["accepted"] is True
        r3 = ch2.simulate_trigger({"trigger_type": "keyword", "match_value": "跳舞"})
        assert r3["accepted"] is False and r3["reason"] == "throttled"
        assert r3["retry_after_s"] is not None and r3["retry_after_s"] > 0

    def test_three_types_hit(self):
        node = _cfg_node(rules=[
            make_rule(rule_id="g", trigger_type="gift", match="小心心"),
            make_rule(rule_id="k", trigger_type="keyword", match="跳舞"),
            make_rule(rule_id="s", trigger_type="song", match=""),
        ])
        ch, _ = _facade(node=node)
        ch._worker = None
        ch.worker_alive = lambda: True
        assert ch.simulate_trigger({"rule_id": "g"})["accepted"] is True
        ch.engine.state = "default"; ch.engine.seq = None; ch.engine.counters.clear()
        ch.engine.key_last.clear()
        assert ch.simulate_trigger({"trigger_type": "keyword", "match_value": "一起跳舞"})["accepted"] is True
        ch.engine.state = "default"; ch.engine.seq = None; ch.engine.counters.clear()
        ch.engine.key_last.clear()
        assert ch.simulate_trigger({"trigger_type": "song", "match_value": "任意歌"})["accepted"] is True

    def test_reason_enum_every_value(self):
        """F2：六拒绝值+接受值至少各一条断言（枚举全覆盖）。"""
        reasons = set()
        node = _cfg_node(rules=[make_rule(rule_id="g", min_price=10),
                                make_rule(rule_id="k", trigger_type="keyword", match="跳舞")])
        ch, _ = _facade(node=node)
        ch._worker = None
        ch.worker_alive = lambda: True
        reasons.add(ch.simulate_trigger({"trigger_type": "gift", "match_value": "不匹配"})["reason"])  # no_match
        reasons.add(ch.simulate_trigger({"rule_id": "g"})["reason"])  # accepted（合成 total_price=10）
        ch2, _ = _facade(node=node)
        ch2._worker = None
        ch2.worker_alive = lambda: True
        ch2.simulate_trigger({"rule_id": "g"})
        reasons.add(ch2.simulate_trigger({"rule_id": "g"})["reason"])  # throttled
        reasons.add(ch2.simulate_trigger({"trigger_type": "gift", "match_value": "不匹配"})["reason"])
        ch3, _ = _facade(node=node)
        ch3._worker = None
        ch3.worker_alive = lambda: True
        r = ch3.simulate_trigger({"trigger_type": "gift", "match_value": "小心心",
                                  "gift": {"gift_name": "小心心", "total_price": 1}})
        reasons.add(r["reason"])  # below_min_price
        # disabled / hold / in_progress / requeued / interrupted
        ch4, _ = _facade()
        ch4._worker = None
        reasons.add(ch4.simulate_trigger({"rule_id": "g"})["reason"])  # disabled
        h = _engine_harness(rules=[make_rule(rule_id="g"),
                                   make_rule(rule_id="b", trigger_type="gift", match="火箭")])
        h.trigger_gift()
        h.advance(1)
        d_hold = h.engine.decide_trigger("gift", "火箭",
                                         gift_data(name="火箭"))
        reasons.add(d_hold["reason"])  # hold（他键 within hold 窗口）
        h2 = _engine_harness()
        h2.trigger_gift()
        h2.engine.key_last["girl_A"] = h2.now - 5  # 绕节流（min_retrigger_s=2）
        d_ip = h2.engine.decide_trigger("gift", "小心心", gift_data())
        reasons.add(d_ip["reason"])  # in_progress（main 未完成）
        h2.engine.seq["main_done"] = True
        h2.engine.key_last["girl_A"] = h2.now - 5  # in_progress 的 decide 已刷新节流锚点，再回拨
        d_rq = h2.engine.decide_trigger("gift", "小心心", gift_data())
        reasons.add(d_rq["reason"])  # requeued
        h3 = _engine_harness(rules=[make_rule(rule_id="g"), make_rule(
            rule_id="b", trigger_type="gift", match="火箭")])
        h3.trigger_gift()
        h3.advance(10)
        d_int = h3.engine.decide_trigger("gift", "火箭", gift_data(name="火箭"))
        reasons.add(d_int["reason"])  # interrupted
        assert {"no_match", "below_min_price", "throttled", "hold", "in_progress",
                "disabled", "accepted", "requeued", "interrupted"} <= reasons


# ---------- GAP-4/F4：get_state 观测字段 ----------

class TestGetStateFields:
    def test_default_state_fields(self):
        ch, _ = _facade(node=_cfg_node(enable=False))  # 无真线程无 DEFAULT 初始化入队
        st = ch.get_state()
        assert st["pending_requeue"] is False
        assert st["step_remaining_s"] is None
        assert st["worker_alive"] is False
        assert st["queue_depth"] == 0

    def test_sequence_state_fields(self):
        ch, _ = _facade()
        ch._worker = None
        ch.engine.on_edtalk_ready(AVAILABLE)
        ch.on_gift(gift_data())
        # 计时起点=切换被 EDTalk 接受（CL-1）：none 步在接受后置 deadline
        ch.engine.on_switch_result(ch.engine.seq["steps"][0]["step_id"], True, 1000.0)
        st = ch.get_state()
        assert st["state"] == "sequence"
        assert st["step_remaining_s"] is not None and st["step_remaining_s"] > 0
        assert st["pending_requeue"] is False
        # 触发重排后 pending_requeue 置位（绕节流：key_last 回拨至窗口外时刻）
        ch.engine.seq["main_done"] = True
        ch.engine.key_last["girl_A"] = 998.0
        ch.on_gift(gift_data())
        st2 = ch.get_state()
        assert st2["pending_requeue"] is True

    def test_worker_alive_true_with_thread(self):
        ch, _ = _facade(worker=True)
        try:
            assert ch.get_state()["worker_alive"] is True
        finally:
            ch.shutdown()


# ---------- T3/F6：端点（TestClient；回环依赖可覆盖） ----------

def _app_and_client(ch):
    app = FastAPI()
    choreo_api.register_choreography_routes(app, DottedConfig(
        {"choreography": _cfg_node(), "api_port": 8082}))
    if ch is not None:
        from utils.orchestration.choreographer import register_choreographer
        register_choreographer(ch)
    return TestClient(app, raise_server_exceptions=False)


class TestEndpoints:
    @pytest.fixture(autouse=True)
    def _clean_holder(self):
        """全局 holder 跨测试清理（register_choreographer 是模块级单例）。"""
        from utils.orchestration.choreographer import _choreographer_holder
        _choreographer_holder["instance"] = None
        yield
        _choreographer_holder["instance"] = None

    def test_trigger_hit_via_override(self):
        ch, _ = _facade()
        ch._worker = None
        ch.worker_alive = lambda: True
        client = _app_and_client(ch)
        client.app.dependency_overrides[choreo_api._require_loopback] = lambda: None
        resp = client.post("/choreography/trigger", json={"rule_id": "girl_A"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["accepted"] is True and data["reason"] == "accepted"

    def test_trigger_unknown_type_422(self):
        client = _app_and_client(None)
        client.app.dependency_overrides[choreo_api._require_loopback] = lambda: None
        resp = client.post("/choreography/trigger", json={"trigger_type": "entrance", "match_value": "x"})
        assert resp.status_code == 422

    def test_trigger_match_value_too_long_422(self):
        client = _app_and_client(None)
        client.app.dependency_overrides[choreo_api._require_loopback] = lambda: None
        resp = client.post("/choreography/trigger",
                           json={"trigger_type": "keyword", "match_value": "x" * 501})
        assert resp.status_code == 422

    def test_trigger_non_dict_422(self):
        client = _app_and_client(None)
        client.app.dependency_overrides[choreo_api._require_loopback] = lambda: None
        resp = client.post("/choreography/trigger", content=b"null",
                           headers={"Content-Type": "application/json"})
        assert resp.status_code == 422

    def test_trigger_unassembled_503(self):
        client = _app_and_client(None)
        client.app.dependency_overrides[choreo_api._require_loopback] = lambda: None
        resp = client.post("/choreography/trigger", json={"rule_id": "x"})
        assert resp.status_code == 503

    def test_trigger_non_loopback_403(self):
        """F6/回环谓词：TestClient 默认 host=testclient 非回环 → POST 403（依赖生效）。"""
        client = _app_and_client(None)
        resp = client.post("/choreography/trigger", json={"rule_id": "x"})
        assert resp.status_code == 403
        assert resp.json()["detail"]["code"] == "remote_forbidden"
        resp2 = client.post("/choreography/rules", json={"enable": False})
        assert resp2.status_code == 403  # POST rules 同门

    def test_get_status_non_loopback_allowed(self):
        """F6：GET status 只读放行（非回环 200——局域网 webui 观察者不被误导）。"""
        ch, _ = _facade()
        ch._worker = None
        client = _app_and_client(ch)
        resp = client.get("/choreography/status")
        assert resp.status_code == 200  # 无回环依赖
        assert resp.json()["worker_alive"] is False

    def test_is_loopback_host_pure(self):
        """回环谓词三断言（DX 义务）：::1 与 127.0.0.2 均回环；192.168.1.5/垃圾串非回环。"""
        assert choreo_api.is_loopback_host("127.0.0.1") is True
        assert choreo_api.is_loopback_host("127.0.0.2") is True  # 击穿裸字符串比较
        assert choreo_api.is_loopback_host("::1") is True
        assert choreo_api.is_loopback_host("192.168.1.5") is False
        assert choreo_api.is_loopback_host("not-an-ip") is False
        assert choreo_api.is_loopback_host("") is False

    def test_rules_endpoint_non_loopback_403_with_allow(self):
        """回环依赖覆盖放行后 POST rules 正常（403 仅由来源驱动，非端点本身）。"""
        ch, _ = _facade()
        ch._worker = None
        ch._ensure_worker = lambda enable=None: False
        client = _app_and_client(ch)
        client.app.dependency_overrides[choreo_api._require_loopback] = lambda: None
        resp = client.post("/choreography/rules", json=_cfg_node(enable=False))
        assert resp.status_code == 200 and resp.json()["applied"] is True
