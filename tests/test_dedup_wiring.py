# -*- coding: utf-8 -*-
"""限定时间段去重接线回归测试（2026-10-24 审查批次）。

历史缺陷：is_data_repeat_in_limited_time 仅有定义、全链零调用——live_data 只被
定时清空从不写入，limited_time_deduplication 配置形同虚设（刷屏不丢弃）。
本测试锁定：process_data 入口统一接线 + 类型化去重键 + 未配置类型跳过。
"""
import threading
import time
import unittest

from utils.handlers.timer_handler import TimerHandler
from utils.handlers.utils_handler import UtilsHandler


class _FakeConfig:
    def __init__(self, d):
        self.d = d

    def get(self, *keys):
        r = self.d
        for k in keys:
            if not isinstance(r, dict):
                return None
            r = r.get(k)
        return r


class _FakeHandle:
    """最小 my_handle 替身：config/data_lock/timers/live_data + 处理记录。"""

    def __init__(self, filter_cfg):
        self.config = _FakeConfig({"filter": filter_cfg})
        self.data_lock = threading.Lock()
        self.timers = {}
        self.live_data = {"comment": [], "gift": [], "entrance": [],
                          "follow": [], "like": [], "room_stats": []}
        self.handled = []
        self.utils_handler = UtilsHandler(self)
        self.timer_handler = TimerHandler(self)

    def is_data_repeat_in_limited_time(self, data, type):
        return self.utils_handler.is_data_repeat_in_limited_time(data, type)

    def comment_handle(self, data):
        self.handled.append(("comment", data))

    def blacklist_handle(self, data):
        # 2026-10-31 黑名单统一拦截接线：去重测试桩默认无黑名单用户
        return False

    def talk_handle(self, data):
        self.handled.append(("talk", data))

    def schedule_handle(self, data):
        self.handled.append(("schedule", data))

    def idle_time_task_handle(self, data):
        self.handled.append(("idle_time_task", data))


class TestDedupWiring(unittest.TestCase):
    def _cfg(self):
        return {
            "limited_time_deduplication": {"enable": True, "comment": 10},
            "comment_forget_duration": 0.15,
            "comment_forget_reserve_num": 5,
        }

    def test_duplicate_dropped_within_window(self):
        """同用户同内容在窗口内重复 → 第二条被丢弃（只处理一次）。"""
        fh = _FakeHandle(self._cfg())
        data = {"username": "u1", "content": "111"}
        fh.timer_handler.process_data(data, "comment")
        fh.timer_handler.process_data(dict(data), "comment")
        fh.timer_handler.process_data(dict(data), "comment")
        time.sleep(0.3)  # 等遗忘窗口到期分发
        self.assertEqual(len(fh.handled), 1)

    def test_different_content_not_dropped(self):
        """同用户不同内容 → 均进入处理（去重键含内容）。"""
        fh = _FakeHandle(self._cfg())
        fh.timer_handler.process_data({"username": "u1", "content": "aaa"}, "comment")
        fh.timer_handler.process_data({"username": "u1", "content": "bbb"}, "comment")
        time.sleep(0.3)
        self.assertEqual(len(fh.handled), 2)

    def test_unconfigured_type_skips_dedup(self):
        """系统生成类型（schedule）不参与去重——同内容重发不丢弃。

        2026-10-31 限入审查批次：schedule 已直通分发（非限流类型白名单），
        同内容两条均立即处理，不建遗忘窗口 Timer。"""
        fh = _FakeHandle(self._cfg())
        data = {"username": "定时任务", "content": "欢迎回来"}
        fh.timer_handler.process_data(dict(data), "schedule")
        fh.timer_handler.process_data(dict(data), "schedule")
        # 直通无窗口 Timer；轮询等两条全部异步分发
        assert "schedule" not in fh.timers
        deadline = time.time() + 2
        while time.time() < deadline and len(fh.handled) < 2:
            time.sleep(0.01)
        self.assertEqual(len(fh.handled), 2)

    def test_non_throttled_types_direct_bypass(self):
        """聊天/定时任务/闲时任务直通守卫（2026-10-31 限入审查批次）：
        三类不过去重、不进遗忘窗口，逐条立即分发（语音对话被"遗忘"即丢
        回复；系统任务低频无需限流）。"""
        for flag in ("talk", "schedule", "idle_time_task"):
            fh = _FakeHandle(self._cfg())
            data = {"username": "u1", "content": "同一句"}
            for _ in range(3):
                fh.timer_handler.process_data(dict(data), flag)
            assert flag not in fh.timers, f"{flag} 不应建遗忘窗口"
            deadline = time.time() + 2
            while time.time() < deadline and len(fh.handled) < 3:
                time.sleep(0.01)
            self.assertEqual(len(fh.handled), 3, f"{flag} 三条都应直通分发")

    def test_forget_flags_whitelist_guard(self):
        """限流白名单守卫：六类直播间消息在名单内，系统三类不在。"""
        flags = TimerHandler._FORGET_FLAGS
        assert flags == frozenset({
            "comment", "gift", "entrance", "follow", "like", "room_stats"})
        assert "talk" not in flags and "schedule" not in flags \
            and "idle_time_task" not in flags

    def test_gift_dedup_key_uses_gift_name(self):
        """礼物无 content 键——按 gift_name 去重。"""
        cfg = self._cfg()
        cfg["limited_time_deduplication"]["gift"] = 10
        fh = _FakeHandle(cfg)
        fh.timer_handler.process_data({"username": "u1", "gift_name": "火箭"}, "gift")
        fh.timer_handler.process_data({"username": "u1", "gift_name": "火箭"}, "gift")
        # 2026-10-28 重构：首条立即分发（缓冲空），窗口内第二条被去重裁剪
        self.assertEqual(len(fh.timers["gift"].last_data), 0)

    def test_dedup_disabled_passthrough(self):
        """enable=false → 全部放行。"""
        cfg = self._cfg()
        cfg["limited_time_deduplication"] = {"enable": False, "comment": 10}
        fh = _FakeHandle(cfg)
        fh.timer_handler.process_data({"username": "u1", "content": "111"}, "comment")
        fh.timer_handler.process_data({"username": "u1", "content": "111"}, "comment")
        # 2026-10-28 重构：首条立即分发，第二条进窗口缓冲
        self.assertEqual(len(fh.timers["comment"].last_data), 1)

    # ---------- 2026-10-28 防遗忘窗口重构：立即处理 + 窗口末补发 ----------

    def test_first_message_dispatched_immediately(self):
        """首条消息立即处理，不等遗忘窗口到期（单条延迟=0）。"""
        fh = _FakeHandle(self._cfg())
        fh.timer_handler.process_data({"username": "u1", "content": "666"}, "comment")
        # 立即分发走 worker 线程——轮询等待（窗口 0.15s 尚未到期）
        deadline = time.time() + 2
        while time.time() < deadline and not fh.handled:
            time.sleep(0.01)
        self.assertEqual(len(fh.handled), 1)

    def test_window_end_replays_reserve_tail(self):
        """遗忘间隔 10s+保留数 2 语义：窗口内消息缓冲，窗口末补发最新 reserve_num-1 条。"""
        cfg = self._cfg()
        cfg["comment_forget_reserve_num"] = 3
        fh = _FakeHandle(cfg)
        fh.timer_handler.process_data({"username": "u1", "content": "666"}, "comment")  # 首条立即
        fh.timer_handler.process_data({"username": "u2", "content": "666"}, "comment")  # 窗口内
        fh.timer_handler.process_data({"username": "u3", "content": "666"}, "comment")  # 窗口内
        # 缓冲只保留最新 reserve_num-1=2 条
        self.assertEqual(len(fh.timers["comment"].last_data), 2)
        time.sleep(0.3)  # 等遗忘窗口到期补发
        # 首条立即 1 条 + 窗口末补发 2 条 = 全部处理
        self.assertEqual(len(fh.handled), 3)

    def test_reserve_num_one_no_replay(self):
        """入场语义（保留数 1）：每个窗口期内仅处理首条，窗口末不补发。"""
        cfg = self._cfg()
        cfg["comment_forget_reserve_num"] = 1
        fh = _FakeHandle(cfg)
        for i in range(5):
            fh.timer_handler.process_data({"username": f"u{i}", "content": "来了"}, "comment")
        time.sleep(0.3)
        self.assertEqual(len(fh.handled), 1)


if __name__ == "__main__":
    unittest.main()
