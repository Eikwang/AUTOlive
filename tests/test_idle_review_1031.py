# -*- coding: UTF-8 -*-
"""
闲时任务审查批次守卫测试（2026-10-31）

覆盖：
1. trigger_type「刷新闲时计时的消息类型」双重断链修复——UI 四复选框此前零保存
   （纯展示），消费方 idle_time_auto_clear 此前全库零调用（"无消息"机制下闲时
   计时仅被 is_handleing 瞬时标志重置，活跃直播间照常触发闲时任务）
2. 「等待播放音频数量阈值+闲时计时减小到」动态降时机制退役（用户裁决：冗余）
3. 最小/最大闲时时间对两队列机制生效（原实现阈值满足即 5s 触发，参数零生效）
4. 闲时文案模式变量替换+brackets 随机段接线（模板文案含 {time}/[1|2|3] 原样
   念出）；comment 模式 LLM 提示词语义保持原样
5. 系统自源（origin=system-idle）排除：闲时自发弹幕不污染 {last_username}、
   不刷新闲时计时；轮询异常不杀线程
"""

import json
import threading
from unittest.mock import MagicMock

import pytest

import utils.task_scheduler as task_mod
from utils import my_global
from utils.common_original import Common
from utils.handlers.timer_handler import TimerHandler
from utils.task_scheduler import TaskScheduler


def make_config(idle_overrides=None):
    base = {
        "schedule": [],
        "schedule_groups": [],
        "idle_time_task": {
            "enable": True,
            "type": "直播间无消息更新闲时",
            "idle_time_min": 1,
            "idle_time_max": 2,
            "min_msg_queue_len_to_trigger": 1,
            "min_audio_queue_len_to_trigger": 1,
            "trigger_type": ["comment"],
            "copywriting": {"enable": True, "random": True, "copy": ["闲时文案A"]},
            "comment": {"enable": False, "random": True, "copy": ["你是谁"]},
            "local_audio": {"enable": False, "random": True, "path": ["x.wav"]},
        },
    }
    if idle_overrides:
        base["idle_time_task"].update(idle_overrides)
    cfg = MagicMock()
    cfg._cfg = base
    cfg.get = MagicMock(side_effect=lambda *keys: _get(base, keys))
    return cfg


def _get(node, keys):
    cur = node
    for k in keys:
        if not isinstance(cur, dict) or k not in cur:
            return None
        cur = cur[k]
    return cur


def make_th(config):
    mh = MagicMock()
    mh.config = config
    mh.data_lock = threading.Lock()
    mh.timers = {}
    mh.is_data_repeat_in_limited_time.return_value = False
    mh.blacklist_handle = MagicMock(return_value=False)
    return TimerHandler(mh), mh


def make_ts(idle_overrides=None):
    mh = MagicMock()
    mh.is_handleing = 0
    mh.audio.message_queue = []
    mh.audio.voice_tmp_path_queue = []
    ts = TaskScheduler(config=make_config(idle_overrides), common=Common(),
                       my_handle=mh, platform="douyin")
    return ts, mh


@pytest.fixture(autouse=True)
def clean_globals():
    my_global.last_username_list = []
    my_global.last_liveroom_data = None
    my_global.global_idle_time = 0
    yield
    my_global.last_username_list = []
    my_global.last_liveroom_data = None
    my_global.global_idle_time = 0


# ---------- ① trigger_type 双重断链修复 ----------

class TestTriggerTypeWiring:
    def test_auto_clear_zeroes_when_configured(self):
        cfg = make_config()  # trigger_type=["comment"]
        my_global.global_idle_time = 42
        assert my_global.idle_time_auto_clear(cfg, "comment") is True
        assert my_global.global_idle_time == 0

    def test_auto_clear_noop_when_not_configured(self):
        cfg = make_config()
        my_global.global_idle_time = 42
        assert my_global.idle_time_auto_clear(cfg, "gift") is False
        assert my_global.global_idle_time == 42

    def test_auto_clear_missing_key_safe(self):
        """trigger_type 缺键/None 容错（原实现 `type in None` TypeError）。"""
        cfg = MagicMock()
        cfg.get = MagicMock(return_value=None)
        assert my_global.idle_time_auto_clear(cfg, "comment") is False

    def test_process_data_refreshes_idle_counter(self):
        """勾选类型（comment）消息到达 → 闲时计时归零。"""
        th, mh = make_th(make_config())
        my_global.global_idle_time = 42
        th.process_data({"username": "观众A", "content": "666"}, "comment")
        assert my_global.global_idle_time == 0

    def test_unchecked_type_no_refresh(self):
        """未勾选类型（gift）到达 → 计时不清零。"""
        th, mh = make_th(make_config())
        my_global.global_idle_time = 42
        th.process_data({"username": "观众B"}, "gift")
        assert my_global.global_idle_time == 42

    def test_ui_checkboxes_bound_to_save(self):
        """UI 四复选框已绑定保存（原纯展示零保存）。"""
        src = open("frontend/ui/tabs/idle_time_task.py", encoding="utf-8").read()
        assert "_sync_trigger_types" in src
        assert 'set_config_callback("idle_time_task", "trigger_type"' in src
        assert "on_value_change" in src.split("_sync_trigger_types")[1][:800]


# ---------- ② 动态降时机制退役 ----------

class TestRemovedMechanism:
    def test_ui_controls_removed(self):
        src = open("frontend/ui/tabs/idle_time_task.py", encoding="utf-8").read()
        assert "wait_play_audio_num_threshold" not in src
        assert "idle_time_reduce_to" not in src
        assert "等待播放音频数量阈值" not in src
        assert "闲时计时减小到" not in src

    def test_example_config_keys_removed(self):
        cfg = json.load(open("config.example.json", encoding="utf-8"))
        assert "wait_play_audio_num_threshold" not in cfg["idle_time_task"]
        assert "idle_time_reduce_to" not in cfg["idle_time_task"]

    def test_retired_keys_registered(self):
        from frontend.main import _RETIRED_CONFIG_KEYS
        keys = {k for k, _n in _RETIRED_CONFIG_KEYS}
        assert ("idle_time_task", "wait_play_audio_num_threshold") in keys
        assert ("idle_time_task", "idle_time_reduce_to") in keys

    def test_backend_zero_consumption(self):
        src = open("utils/task_scheduler.py", encoding="utf-8").read()
        assert "wait_play_audio_num_threshold" not in src
        assert "idle_time_reduce_to" not in src


# ---------- ③ 最小/最大闲时时间对队列机制生效 ----------

class TestQueueMechanismMinMax:
    def _run_idle_thread(self, monkeypatch, ts, rounds):
        """驱动闲时线程：sleep 打桩推进轮次（每轮=5s），rounds 后停止。"""
        state = {"n": 0}

        def fake_sleep(_s):
            state["n"] += 1
            if state["n"] >= rounds:
                ts.is_running = False

        monkeypatch.setattr(task_mod.time, "sleep", fake_sleep)
        ts.is_running = True  # __init__ 默认 False，线程体 while 依赖此开关
        ts.start_idle_time_task_thread()
        ts.idle_time_task_thread.join(timeout=5)
        assert not ts.idle_time_task_thread.is_alive(), "闲时线程应在轮次耗尽后退出"

    def test_queue_idle_wait_respected(self, monkeypatch):
        """合成队列持续空闲（[]≤阈值1）+ min/max=30：30s 累计后才触发，
        不再 5s 一条刷屏。"""
        ts, mh = make_ts({"type": "待合成消息队列更新闲时",
                          "idle_time_min": 30, "idle_time_max": 30})
        ts.do_task = MagicMock(return_value=(0, [], [], []))
        self._run_idle_thread(monkeypatch, ts, rounds=12)  # 12×5s=60s
        assert 1 <= ts.do_task.call_count <= 2, \
            f"60s 空闲、min/max=30 → 触发 1-2 次（实际 {ts.do_task.call_count}）"

    def test_queue_busy_no_trigger(self, monkeypatch):
        """队列长度 > 阈值 → 空闲条件不满足，永不触发。"""
        ts, mh = make_ts({"type": "待合成消息队列更新闲时",
                          "idle_time_min": 1, "idle_time_max": 1})
        mh.audio.message_queue = ["m"] * 5
        ts.do_task = MagicMock(return_value=(0, [], [], []))
        self._run_idle_thread(monkeypatch, ts, rounds=6)
        assert ts.do_task.call_count == 0

    def test_idle_wait_resets_when_busy(self, monkeypatch):
        """空闲确认被打断（队列填充）→ 等待累计清零重计。"""
        ts, mh = make_ts({"type": "待合成消息队列更新闲时",
                          "idle_time_min": 30, "idle_time_max": 30})
        ts.do_task = MagicMock(return_value=(0, [], [], []))
        queue_states = [["m"] * 5] * 3 + [[]] * 9  # 前 3 轮忙、后 9 轮闲（45s）
        state = {"n": 0}

        def fake_sleep(_s):
            mh.audio.message_queue = queue_states[min(state["n"], len(queue_states) - 1)]
            state["n"] += 1
            if state["n"] >= 12:
                ts.is_running = False

        monkeypatch.setattr(task_mod.time, "sleep", fake_sleep)
        ts.is_running = True  # __init__ 默认 False，线程体 while 依赖此开关
        ts.start_idle_time_task_thread()
        ts.idle_time_task_thread.join(timeout=5)
        # 忙 15s 不计，闲累计 5..45 → 第 9 轮（30s）触发 1 次，60s 处第 2 次
        assert 1 <= ts.do_task.call_count <= 2

    def test_poll_exception_survives(self, monkeypatch):
        """单轮异常（do_task 抛错）不杀线程，下轮恢复。"""
        ts, mh = make_ts({"type": "待合成消息队列更新闲时",
                          "idle_time_min": 1, "idle_time_max": 1})
        ts.do_task = MagicMock(side_effect=[RuntimeError("boom"), (0, [], [], [])])
        self._run_idle_thread(monkeypatch, ts, rounds=2)  # 第1轮抛错、第2轮恢复
        assert ts.do_task.call_count == 2, "异常后下轮应恢复触发"

    def test_audio_queue_mechanism_wired(self, monkeypatch):
        """待播放音频队列机制：voice_tmp_path_queue 阈值判定+min/max 等待。"""
        ts, mh = make_ts({"type": "待播放音频队列更新闲时",
                          "idle_time_min": 1, "idle_time_max": 1})
        mh.audio.voice_tmp_path_queue = []
        ts.do_task = MagicMock(return_value=(0, [], [], []))
        self._run_idle_thread(monkeypatch, ts, rounds=2)
        assert ts.do_task.call_count >= 1


# ---------- ④ 闲时文案变量替换+brackets ----------

class TestIdleCopyVariables:
    def test_copy_variables_and_brackets(self):
        """模板文案 {time} 报时替换 + [甲|乙] 随机段生效（原样念占位符断链修复）。"""
        ts, mh = make_ts({"copywriting": {"enable": True, "random": True,
                                          "copy": ["现在是{time}[甲|乙]"]}})
        ts.do_task(0, ["现在是{time}[甲|乙]"], [], [])
        assert mh.process_data.call_args[0][1] == "idle_time_task"
        content = mh.process_data.call_args[0][0]["content"]
        assert "点" in content and "分" in content
        assert "{time}" not in content
        assert content.endswith(("甲", "乙"))

    def test_comment_mode_content_untouched(self):
        """comment 模式 LLM 提示词语义保持原样（无变量替换/brackets）。"""
        ts, mh = make_ts({"copywriting": {"enable": False},
                          "comment": {"enable": True, "random": True,
                                      "copy": ["请介绍{time}[甲|乙]"]}})
        ts.do_task(0, [], ["请介绍{time}[甲|乙]"], [])
        mh.send_live_danmu.assert_called_once_with("请介绍{time}[甲|乙]")
        assert mh.process_data.call_args[0][0]["content"] == "请介绍{time}[甲|乙]"


# ---------- ⑤ 系统自源排除 ----------

class TestSystemSourceExclusion:
    def test_idle_danmu_not_recorded_not_refresh(self):
        """闲时自发弹幕（origin=system-idle）不进 {last_username}、不刷新闲时计时。"""
        th, mh = make_th(make_config())
        my_global.global_idle_time = 42
        th.process_data({"username": "闲时任务", "content": "你好呀",
                         "origin": "system-idle"}, "comment")
        assert "闲时任务" not in my_global.last_username_list
        assert my_global.global_idle_time == 42

    def test_normal_comment_recorded_and_refresh(self):
        """正常观众弹幕：记录用户名+刷新计时（对照）。"""
        th, mh = make_th(make_config())
        my_global.global_idle_time = 42
        th.process_data({"username": "观众C", "content": "嗨"}, "comment")
        assert "观众C" in my_global.last_username_list
        assert my_global.global_idle_time == 0
