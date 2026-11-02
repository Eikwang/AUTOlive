# -*- coding: UTF-8 -*-
"""
定时任务审查批次守卫测试（2026-10-31）

覆盖：
1. {last_username} 数据源接线（process_data 互动记录——此前
   add_username_to_last_username_list 全库零调用，变量恒"未知用户"）
2. {user_num} 数据源修复（OnlineUserCount→viewer_count 全平台生效——原键
   全库无写入点，恒"N"）
3. 变量替换健壮性（str.format→str.replace：孤立花括号/未知占位符不炸不丢）
4. schedule_groups 任务组消费接线（D-16/H2：组接管语义、存在即启用、
   非法组跳过）——此前 UI 任务组管理后端零消费
5. 调度线程健壮性（周期归一、执行异常不中断重排、run_pending 异常不杀线程）
6. 存量单任务兼容（schedule_task 直调/越界/空文案）
"""

import threading
from unittest.mock import MagicMock

import pytest

import utils.task_scheduler as task_mod
from utils import my_global
from utils.common_original import Common
from utils.handlers.timer_handler import TimerHandler
from utils.task_scheduler import TaskScheduler


def make_config(overrides=None):
    base = {
        "schedule": [
            {"enable": False, "time_min": 3600.0, "time_max": 7200.0,
             "copy": ["现在是{time}", "当前时间为{time}"]},
        ],
        "schedule_groups": [],
    }
    if overrides:
        base.update(overrides)
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


def make_scheduler(platform="bilibili"):
    mh = MagicMock()
    mh.config = make_config()
    mh.common = Common()
    ts = TaskScheduler(config=mh.config, common=mh.common, my_handle=mh, platform=platform)
    return ts, mh


@pytest.fixture(autouse=True)
def clean_globals():
    """全局环形记录隔离（防测试间污染）。"""
    my_global.last_username_list = []
    my_global.last_liveroom_data = None
    yield
    my_global.last_username_list = []
    my_global.last_liveroom_data = None


# ---------- ① {last_username} 数据源接线 ----------

class TestLastUsernameWiring:
    def _make_th(self):
        ts_unused, mh = make_scheduler()
        mh.data_lock = threading.Lock()
        mh.timers = {}
        mh.is_data_repeat_in_limited_time.return_value = False
        mh.blacklist_handle = MagicMock(return_value=False)
        return TimerHandler(mh), mh

    def test_initial_list_not_none(self):
        """初始化为列表（原 None：add 若被调用将 None.append 崩溃）。"""
        assert isinstance(my_global.last_username_list, list)

    def test_add_records_and_keeps_last_three(self):
        for name in ("A", "B", "C", "D"):
            my_global.add_username_to_last_username_list(name)
        assert my_global.last_username_list == ["B", "C", "D"]

    def test_process_data_records_interaction(self):
        """互动消息到达 process_data 即记录（{last_username} 数据源）。"""
        th, mh = self._make_th()
        th.process_data({"username": "观众A", "content": "666"}, "comment")
        assert "观众A" in my_global.last_username_list

    def test_blacklisted_not_recorded(self):
        """黑名单用户不记录（T7 语义：不进行任何处理）。"""
        th, mh = self._make_th()
        mh.blacklist_handle = MagicMock(return_value=True)
        th.process_data({"username": "坏人A", "content": ""}, "gift")
        assert "坏人A" not in my_global.last_username_list

    def test_room_stats_not_recorded(self):
        """room_stats 为系统快照（username=房间信息），不记录。"""
        th, mh = self._make_th()
        th.process_data({"username": "房间信息", "stats": {"viewer_count": 1}}, "room_stats")
        assert my_global.last_username_list == []

    def test_direct_types_not_recorded(self):
        """系统消息（talk/schedule 直通分支）不记录。"""
        th, mh = self._make_th()
        th.process_data({"username": "某人", "content": "x"}, "talk")
        assert "某人" not in my_global.last_username_list


# ---------- ② {user_num} 数据源修复 ----------

class TestUserNumWiring:
    def test_viewer_count_used(self):
        """读监听端 ROOM_STATS 统一契约键 viewer_count（原 OnlineUserCount
        全库无写入点恒"N"）。"""
        my_global.last_liveroom_data = {"viewer_count": 42}
        ts, mh = make_scheduler()
        ts._emit_copy_task(["当前{user_num}人"])
        sent = mh.process_data.call_args[0][0]
        assert sent["content"] == "当前42人"

    def test_missing_data_falls_back(self):
        """last_liveroom_data 未就绪（None/非 dict）→ "N" 不炸。"""
        ts, mh = make_scheduler()
        my_global.last_liveroom_data = None
        ts._emit_copy_task(["当前{user_num}人"])
        assert mh.process_data.call_args[0][0]["content"] == "当前N人"

    def test_all_platforms(self):
        """原 dy/tiktok 平台白名单删除——各平台 room_stats 均产出 viewer_count。"""
        my_global.last_liveroom_data = {"viewer_count": 7}
        for platform in ("douyin", "bilibili", "kuaishou"):
            ts, mh = make_scheduler(platform=platform)
            ts._emit_copy_task(["{user_num}"])
            assert mh.process_data.call_args[0][0]["content"] == "7"


# ---------- ③ 变量替换健壮性 ----------

class TestVariableReplacement:
    def test_time_replaced(self):
        ts, mh = make_scheduler()
        ts._emit_copy_task(["现在是{time}"])
        content = mh.process_data.call_args[0][0]["content"]
        assert "点" in content and "分" in content
        assert "{time}" not in content

    def test_last_username_replaced(self):
        my_global.last_username_list = ["互动王"]
        ts, mh = make_scheduler()
        ts._emit_copy_task(["感谢{last_username}的支持"])
        assert mh.process_data.call_args[0][0]["content"] == "感谢互动王的支持"

    def test_last_username_empty_fallback(self):
        ts, mh = make_scheduler()
        ts._emit_copy_task(["感谢{last_username}的支持"])
        assert mh.process_data.call_args[0][0]["content"] == "感谢未知用户的支持"

    def test_unknown_placeholder_preserved(self):
        """未知占位符原样保留（原 str.format 抛 KeyError）。"""
        ts, mh = make_scheduler()
        ts._emit_copy_task(["{unknown}保持原样"])
        assert mh.process_data.call_args[0][0]["content"] == "{unknown}保持原样"

    def test_lone_braces_not_fatal(self):
        """孤立花括号不炸（原 str.format 抛 ValueError/IndexError 且可打死
        调度线程）。"""
        ts, mh = make_scheduler()
        content = ts._emit_copy_task(["花括号}{测试{time}结束"])
        assert content is not None
        assert "}{" in content and "点" in content

    def test_brackets_randomize_after_replace(self):
        """[|] 随机段在变量替换后仍生效。"""
        ts, mh = make_scheduler()
        content = ts._emit_copy_task(["你好[啊|呀]{last_username}"])
        assert content in ("你好啊未知用户", "你好呀未知用户")

    def test_empty_copy_skipped(self):
        """空文案列表跳过（warning）不入队。"""
        ts, mh = make_scheduler()
        assert ts._emit_copy_task([]) is None
        mh.process_data.assert_not_called()


# ---------- ④ schedule_groups 任务组消费接线 ----------

class TestGroupWiring:
    def test_group_takeover_registered_and_executed(self, monkeypatch):
        """组非空 → 组语义接管：注册 grp0 tag + 组文案入队 + 存量任务不消费。"""
        ts, mh = make_scheduler()
        ts.config._cfg["schedule"][0]["enable"] = True  # 存量任务启用——应被接管忽略
        ts.config._cfg["schedule_groups"] = [
            {"name": "报时组", "min_interval": 60, "max_interval": 120,
             "copywriting_list": ["组文案{time}"]},
        ]
        fake_sched = MagicMock()
        monkeypatch.setattr(task_mod, "schedule", fake_sched)
        monkeypatch.setattr(task_mod.time, "sleep", lambda s: None)
        fake_sched.run_pending.side_effect = lambda: setattr(ts, "is_running", False)
        ts.run_schedule()

        fake_sched.clear.assert_any_call("grp0")
        tag_arg = fake_sched.every.return_value.seconds.do.return_value.tag.call_args[0][0]
        assert tag_arg == "grp0"
        # 组文案执行（job 立即首跑）+ 存量任务被接管忽略
        assert mh.process_data.call_count == 1
        assert "组文案" in mh.process_data.call_args[0][0]["content"]
        assert mh.process_data.call_args[0][1] == "schedule"

    def test_legacy_runs_when_no_groups(self, monkeypatch):
        """无任务组 → 存量单任务照常消费（兼容）。"""
        ts, mh = make_scheduler()
        ts.config._cfg["schedule"][0]["enable"] = True
        fake_sched = MagicMock()
        monkeypatch.setattr(task_mod, "schedule", fake_sched)
        monkeypatch.setattr(task_mod.time, "sleep", lambda s: None)
        fake_sched.run_pending.side_effect = lambda: setattr(ts, "is_running", False)
        ts.run_schedule()

        fake_sched.clear.assert_any_call(0)
        assert mh.process_data.call_count == 1
        assert "点" in mh.process_data.call_args[0][0]["content"]

    def test_invalid_group_skipped(self, monkeypatch):
        """缺组名/周期非法的组跳过注册（不炸其余组）。"""
        ts, mh = make_scheduler()
        ts.config._cfg["schedule_groups"] = [
            {"name": "", "min_interval": 60, "max_interval": 120, "copywriting_list": ["x"]},
            {"name": "坏周期组", "min_interval": "abc", "max_interval": None, "copywriting_list": ["y"]},
            {"name": "好组", "min_interval": 60, "max_interval": 120, "copywriting_list": ["好文案"]},
        ]
        fake_sched = MagicMock()
        monkeypatch.setattr(task_mod, "schedule", fake_sched)
        monkeypatch.setattr(task_mod.time, "sleep", lambda s: None)
        fake_sched.run_pending.side_effect = lambda: setattr(ts, "is_running", False)
        ts.run_schedule()

        # 仅好组注册执行（grp2）；坏组无注册痕迹
        assert mh.process_data.call_count == 1
        assert mh.process_data.call_args[0][0]["content"] == "好文案"

    def test_start_condition_groups(self, monkeypatch):
        """组非空即启动调度线程（存量任务全关也启动）。"""
        ts, mh = make_scheduler()
        ts.config._cfg["schedule_groups"] = [
            {"name": "g", "min_interval": 60, "max_interval": 120, "copywriting_list": ["x"]},
        ]
        fake_thread_cls = MagicMock()
        monkeypatch.setattr(task_mod.threading, "Thread", fake_thread_cls)
        ts.start_schedule_thread()
        assert fake_thread_cls.called
        assert fake_thread_cls.return_value.start.called

    def test_start_condition_disabled_no_thread(self, monkeypatch):
        """存量全关+无组+非 dy → 不启动。"""
        ts, mh = make_scheduler()
        fake_thread_cls = MagicMock()
        monkeypatch.setattr(task_mod.threading, "Thread", fake_thread_cls)
        ts.start_schedule_thread()
        fake_thread_cls.assert_not_called()


# ---------- ⑤ 调度线程健壮性 ----------

class TestResilience:
    def test_period_min_max_swapped(self, monkeypatch):
        """min>max 归一（原 randint(min,max) 抛 ValueError 打死线程）。"""
        ts, mh = make_scheduler()
        fake_sched = MagicMock()
        monkeypatch.setattr(task_mod, "schedule", fake_sched)
        ran = []
        ts._register_random_job("t", 200, 100, lambda: ran.append(1))
        assert ran == [1]
        assert fake_sched.every.called

    def test_zero_period_clamped(self, monkeypatch):
        """0 周期钳制下限 1（防 every(0) 忙转）。"""
        fake_sched = MagicMock()
        monkeypatch.setattr(task_mod, "schedule", fake_sched)
        TaskScheduler._register_random_job("t", 0, 0, lambda: None)
        assert fake_sched.every.called

    def test_invalid_period_skipped(self, monkeypatch):
        """周期非法（str/None）跳过注册，不炸不注册。"""
        fake_sched = MagicMock()
        monkeypatch.setattr(task_mod, "schedule", fake_sched)
        ran = []
        TaskScheduler._register_random_job("t", "abc", None, lambda: ran.append(1))
        assert ran == []
        fake_sched.every.assert_not_called()

    def test_task_fn_exception_keeps_reordering(self, monkeypatch):
        """任务执行异常不中断重排（原实现异常沿 run_pending 传播杀死调度
        线程——全部定时任务静默停摆）。"""
        fake_sched = MagicMock()
        monkeypatch.setattr(task_mod, "schedule", fake_sched)

        def raising():
            raise RuntimeError("文案异常")

        TaskScheduler._register_random_job("t", 10, 20, raising)
        assert fake_sched.every.called  # 重排仍注册

    def test_run_pending_exception_survives(self, monkeypatch):
        """调度循环 run_pending 异常不杀线程（捕获后继续）。"""
        ts, mh = make_scheduler()
        fake_sched = MagicMock()
        monkeypatch.setattr(task_mod, "schedule", fake_sched)
        monkeypatch.setattr(task_mod.time, "sleep", lambda s: None)
        state = {"n": 0}

        def run_pending():
            state["n"] += 1
            if state["n"] == 1:
                raise RuntimeError("job 异常模拟")
            ts.is_running = False

        fake_sched.run_pending.side_effect = run_pending
        ts.run_schedule()  # 不应抛出
        assert state["n"] == 2


# ---------- ⑥ 存量单任务兼容 ----------

class TestLegacyScheduleTask:
    def test_direct_call_runs(self):
        ts, mh = make_scheduler()
        content = ts.schedule_task(0)
        assert content is not None and "点" in content
        assert mh.process_data.call_args[0][1] == "schedule"

    def test_index_out_of_range(self):
        ts, mh = make_scheduler()
        assert ts.schedule_task(5) is None
        mh.process_data.assert_not_called()

    def test_config_not_list_safe(self):
        """config.schedule 非 list（手改损坏）容错。"""
        ts, mh = make_scheduler()
        ts.config._cfg["schedule"] = None
        assert ts.schedule_task(0) is None
        mh.process_data.assert_not_called()
