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
        """未配置去重周期的类型（如 schedule）同内容重发不丢弃。"""
        cfg = self._cfg()
        cfg["schedule_forget_duration"] = 0.15
        cfg["schedule_forget_reserve_num"] = 5
        fh = _FakeHandle(cfg)
        data = {"username": "定时任务", "content": "欢迎回来"}
        fh.timer_handler.process_data(dict(data), "schedule")
        fh.timer_handler.process_data(dict(data), "schedule")
        # 同内容两条都进了遗忘窗口（未被去重裁剪）
        self.assertEqual(len(fh.timers["schedule"].last_data), 2)

    def test_gift_dedup_key_uses_gift_name(self):
        """礼物无 content 键——按 gift_name 去重。"""
        cfg = self._cfg()
        cfg["limited_time_deduplication"]["gift"] = 10
        fh = _FakeHandle(cfg)
        fh.timer_handler.process_data({"username": "u1", "gift_name": "火箭"}, "gift")
        fh.timer_handler.process_data({"username": "u1", "gift_name": "火箭"}, "gift")
        # 窗口内第二条未入遗忘窗口（timers['gift'] 的 last_data 只有首条）
        self.assertEqual(len(fh.timers["gift"].last_data), 1)

    def test_dedup_disabled_passthrough(self):
        """enable=false → 全部放行。"""
        cfg = self._cfg()
        cfg["limited_time_deduplication"] = {"enable": False, "comment": 10}
        fh = _FakeHandle(cfg)
        fh.timer_handler.process_data({"username": "u1", "content": "111"}, "comment")
        fh.timer_handler.process_data({"username": "u1", "content": "111"}, "comment")
        self.assertEqual(len(fh.timers["comment"].last_data), 2)


if __name__ == "__main__":
    unittest.main()
