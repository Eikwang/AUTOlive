# -*- coding: UTF-8 -*-
"""
2026-11-19 启动就绪门控批次测试

覆盖：
- 就绪注册表语义：mark/is_ready/wait/幂等/超时兜底/多键 AND/诊断快照
- required_keys 按 config 动态确定（GSV/EDTalk 启用组合、dict 与 Config 形态、
  共享 Config 类级缓存回退、RVC 不设键）
- 快模块入口 wait 调用源级守卫（task_scheduler 三线程 + danmaku_consumer
  连接动作先于 _inject_rooms）
- mark_ready 接线点守卫（GSV 引擎就绪/预热完成、EDTalk 连接成功探测、
  外部 api_v2 健康探测）
"""
import os
import sys
import threading
import time

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from utils import readiness  # noqa: E402
from utils.readiness import (  # noqa: E402
    STARTUP_READY_TIMEOUT_S,
    is_ready,
    mark_ready,
    required_keys,
    wait_ready,
)

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


@pytest.fixture(autouse=True)
def _clean_registry():
    """每用例前后清空就绪注册表（测试隔离，防跨用例键残留）。"""
    with readiness._lock:
        readiness._events.clear()
    yield
    with readiness._lock:
        readiness._events.clear()


class FakeConfig:
    """Config 实例替身（.get(*keys) 嵌套语义同 utils.config.Config）。"""

    def __init__(self, data):
        self._data = data

    def get(self, *keys):
        node = self._data
        for k in keys:
            if not isinstance(node, dict):
                return None
            node = node.get(k)
        return node


class TestRegistrySemantics:
    """注册表语义：mark/wait/is_ready/超时兜底/多键 AND。"""

    def test_mark_then_is_ready_and_wait(self):
        assert not is_ready("gsv")
        mark_ready("gsv")
        assert is_ready("gsv")
        assert wait_ready(["gsv"], timeout_s=0.5) is True

    def test_mark_idempotent(self):
        mark_ready("gsv")
        mark_ready("gsv")
        assert is_ready("gsv")

    def test_wait_blocks_until_mark(self):
        def _late_mark():
            time.sleep(0.2)
            mark_ready("gsv")

        threading.Thread(target=_late_mark, daemon=True).start()
        t0 = time.monotonic()
        assert wait_ready(["gsv"], timeout_s=3) is True
        assert time.monotonic() - t0 >= 0.15

    def test_wait_timeout_fallback(self):
        """超时兜底：键永不就绪 → 返回 False 强制放行（不永久阻塞）。"""
        t0 = time.monotonic()
        assert wait_ready(["never-registered"], timeout_s=0.3) is False
        assert time.monotonic() - t0 < 2.0

    def test_multi_key_and_semantics(self):
        mark_ready("gsv")
        # 单键就绪、另一键未就绪 → 仍等待（AND 语义）
        assert wait_ready(["gsv", "edtalk"], timeout_s=0.3) is False
        mark_ready("edtalk")
        assert wait_ready(["gsv", "edtalk"], timeout_s=1) is True

    def test_empty_keys_pass_through(self):
        assert wait_ready([], timeout_s=0.5) is True
        assert wait_ready(None, timeout_s=0.5) is True

    def test_readiness_state_diagnostic(self):
        mark_ready("gsv")
        assert readiness.readiness_state().get("gsv") is True

    def test_default_timeout_constant(self):
        assert STARTUP_READY_TIMEOUT_S == 180


class TestWaitReadyIntegration:
    """多线程行为：wait 阻塞由其它线程 mark 释放。"""

    def test_concurrent_wait_released_by_marks(self):
        results = {}

        def _waiter():
            results["ok"] = wait_ready(["gsv", "edtalk"], timeout_s=5)

        th = threading.Thread(target=_waiter, daemon=True)
        th.start()
        time.sleep(0.15)
        mark_ready("gsv")
        time.sleep(0.1)
        assert th.is_alive()  # edtalk 未就绪仍等待（AND）
        mark_ready("edtalk")
        th.join(timeout=3)
        assert results.get("ok") is True


class TestRequiredKeysFromConfig:
    """键集合按 config 启用状态动态确定（避免未启用服务卡住启动）。"""

    CFG_BOTH = {
        "audio_synthesis_type": "gpt_sovits",
        "visual_body": "edtalk_realtime",
        "edtalk_realtime": {"enable": True},
    }

    def test_both_enabled_config_instance(self):
        assert required_keys(FakeConfig(self.CFG_BOTH)) == ("gsv", "edtalk")

    def test_both_enabled_dict_form(self):
        assert required_keys(self.CFG_BOTH) == ("gsv", "edtalk")

    def test_gsv_only(self):
        cfg = {"audio_synthesis_type": "gpt_sovits", "visual_body": "video",
               "edtalk_realtime": {"enable": True}}
        assert required_keys(cfg) == ("gsv",)

    def test_edtalk_only(self):
        cfg = {"audio_synthesis_type": "edge_tts", "visual_body": "edtalk_realtime",
               "edtalk_realtime": {"enable": True}}
        assert required_keys(cfg) == ("edtalk",)

    def test_none_enabled(self):
        cfg = {"audio_synthesis_type": "edge_tts", "visual_body": "video",
               "edtalk_realtime": {"enable": False}}
        assert required_keys(cfg) == ()

    def test_edtalk_enable_false_keeps_gsv(self):
        cfg = {"audio_synthesis_type": "gpt_sovits", "visual_body": "edtalk_realtime",
               "edtalk_realtime": {"enable": False}}
        assert required_keys(cfg) == ("gsv",)

    def test_edtalk_missing_enable_key_keeps_gsv(self):
        cfg = {"audio_synthesis_type": "gpt_sovits", "visual_body": "edtalk_realtime"}
        assert required_keys(cfg) == ("gsv",)

    def test_empty_config(self):
        assert required_keys({}) == ()

    def test_none_falls_back_to_shared_config_cache(self, monkeypatch):
        from utils.config import Config
        monkeypatch.setattr(Config, "config", self.CFG_BOTH, raising=False)
        assert required_keys(None) == ("gsv", "edtalk")

    def test_rvc_never_sets_key(self):
        """RVC 变声无独立就绪判定（音频流后处理），不设键。"""
        cfg = dict(self.CFG_BOTH, rvc_vc={"enabled": True})
        assert "rvc" not in required_keys(cfg)


class _SourceGuard:
    """源级守卫基类：读源码文本断言接线点存在（防回归删除）。"""

    @staticmethod
    def _read(relpath: str) -> str:
        with open(os.path.join(_REPO_ROOT, relpath), "r", encoding="utf-8") as f:
            return f.read()


class TestFastModuleGateSourceGuard(_SourceGuard):
    """快模块入口 wait 调用源级守卫。"""

    def test_task_scheduler_three_thread_gates(self):
        src = self._read("utils/task_scheduler.py")
        # 三个快模块线程入口（定时任务/闲时任务/动态文案）均接门控
        for tag in ("定时任务", "闲时任务", "动态文案"):
            assert f'self._gate_startup_ready("{tag}")' in src, f"缺 {tag} 门控"
        # 门控助手调用 wait_ready + required_keys + 超时兜底常量
        assert "wait_ready(required_keys(self.config), STARTUP_READY_TIMEOUT_S)" in src

    def test_gate_first_in_run_schedule(self):
        """定时任务：门控先于 is_running 置位/注册循环——run_now=True 立即
        首跑不得早于慢模块就绪。"""
        src = self._read("utils/task_scheduler.py")
        run_schedule_pos = src.find("def run_schedule(self):")
        gate_pos = src.find('self._gate_startup_ready("定时任务")')
        assert 0 < run_schedule_pos < gate_pos
        is_running_pos = src.find("self.is_running = True", gate_pos)
        assert 0 < is_running_pos

    def test_gate_inside_idle_and_trends_thread_funcs(self):
        src = self._read("utils/task_scheduler.py")
        idle_func_pos = src.find("def run_idle_time_task():")
        idle_gate_pos = src.find('self._gate_startup_ready("闲时任务")')
        trends_func_pos = src.find("def run_trends_copywriting():")
        trends_gate_pos = src.find('self._gate_startup_ready("动态文案")')
        assert 0 < idle_func_pos < idle_gate_pos
        assert 0 < trends_func_pos < trends_gate_pos

    def test_danmaku_gate_before_inject_rooms(self):
        """弹幕监听延迟连接：连接动作（_inject_rooms）前先 wait_ready；
        监听服务本体（bridge/web_console 拉起）不门控。"""
        src = self._read("utils/danmaku_consumer.py")
        gate_pos = src.find("from utils.readiness import wait_ready")
        inject_pos = src.find("await self._inject_rooms()")
        assert 0 < gate_pos < inject_pos
        assert "await asyncio.to_thread(wait_ready" in src


class TestMarkReadyWiringSourceGuard(_SourceGuard):
    """mark_ready 接线点源级守卫。"""

    def test_gsv_inproc_marks_on_load_ready_and_warmup(self):
        src = self._read("utils/gpt_sovits_inproc.py")
        load_ready_pos = src.find("self._set_state(ST_READY)")
        first_mark_pos = src.find('mark_ready("gsv")')
        assert 0 < load_ready_pos < first_mark_pos  # _load 置 READY 后置位
        assert src.count('mark_ready("gsv")') >= 2  # warmup 完成处幂等兜底
        warmup_done_pos = src.find("[gsv-inproc] 预热完成")
        assert 0 < warmup_done_pos < src.rfind('mark_ready("gsv")')

    def test_main_edtalk_probe_marks_on_connect_success(self):
        src = self._read("main.py")
        probe_pos = src.find("def _probe_edtalk_ready")
        get_status_pos = src.find("client.get_status()", probe_pos)
        # mark 从探测代码之后找（前文批注注释里也含该字面量）
        mark_pos = src.find('mark_ready("edtalk")', get_status_pos)
        thread_pos = src.find("edtalk-ready-probe")
        assert 0 < probe_pos < get_status_pos < mark_pos  # 探测成功→置位
        assert 0 < thread_pos  # 探测线程已拉起

    def test_main_gsv_api_probe_marks_on_health(self):
        src = self._read("main.py")
        probe_pos = src.find("def _probe_gsv_api_ready")
        assert probe_pos > 0
        probe_body = src[probe_pos:src.find("gsv-api-ready-probe")]
        assert "/docs" in probe_body  # 健康探测端点
        assert 'mark_ready("gsv")' in probe_body  # 首次探测成功即置位
