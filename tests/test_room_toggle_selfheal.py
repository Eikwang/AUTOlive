"""弹幕监听房间开关切换自愈回归（2026-11-21 开关切换修复）

根因：运行态切换房间开关 → bridge.start_room → 注册表 `_rooms` 无该房间
（disabled 房间从不注入 + rooms.json 持久化文件丢失/损坏回退空表）→
RoomError 404 → 前端「运行面切换失败｜服务仍在线——请重试」假失败。

修复：config.rooms 为权威源——ensure_room_registered 按需补登记（stopped，
不连接）；set_room_enabled 开启分支先补登记再 start；_inject_rooms 对
disabled 房间注册不启动。
"""

import unittest
from unittest.mock import patch

from danmaku_listener.web.bridge import DanmakuBridge, RoomError
from utils import danmaku_consumer as dc


class _StubBridge:
    """最小桥桩：复刻真实 start_room 的「注册表缺失→404」契约。"""

    def __init__(self):
        self._rooms = {}
        self.calls = []

    async def ensure_room_registered(self, platform, room_id):
        return await DanmakuBridge.ensure_room_registered(self, platform, room_id)

    def _save_rooms_registry(self):
        self.calls.append("save")

    async def start_room(self, platform, room_id):
        room_key = f"{platform}:{room_id}"
        if room_key not in self._rooms:
            raise RoomError(f"Room not found: {room_key}", status=404)
        self.calls.append(f"start:{room_key}")
        self._rooms[room_key]["status"] = "running"
        return {"success": True, "room": self._rooms[room_key]}

    async def stop_room(self, platform, room_id):
        room_key = f"{platform}:{room_id}"
        if room_key not in self._rooms:
            raise RoomError(f"Room not found: {room_key}", status=404)
        self.calls.append(f"stop:{room_key}")
        self._rooms[room_key]["status"] = "stopped"
        return {"success": True, "room": self._rooms[room_key]}


class _StubService:
    """最小服务桩：真实 set_room_enabled 绑定 fake_self。"""

    def __init__(self, bridge):
        self._bridge = bridge
        import asyncio
        self._loop = asyncio.new_event_loop()  # 供 is_closed() 探活

    def _submit_room_op(self, coro_factory, timeout=None):
        import asyncio
        return asyncio.run(coro_factory())


class TestEnsureRoomRegistered(unittest.TestCase):
    """ensure_room_registered：缺失补登记（stopped+持久化）、已存在幂等。"""

    def test_registers_missing_room_as_stopped(self):
        bridge = _StubBridge()
        import asyncio
        res = asyncio.run(bridge.ensure_room_registered("bilibili", "123"))
        self.assertTrue(res["success"])
        self.assertEqual(bridge._rooms["bilibili:123"]["status"], "stopped")
        self.assertIn("save", bridge.calls)  # 持久化落盘

    def test_idempotent_when_present(self):
        bridge = _StubBridge()
        bridge._rooms["bilibili:123"] = {"platform": "bilibili",
                                         "room_id": "123", "status": "running",
                                         "engine_type": "x"}
        import asyncio
        asyncio.run(bridge.ensure_room_registered("bilibili", "123"))
        self.assertEqual(bridge._rooms["bilibili:123"]["status"], "running")
        self.assertNotIn("save", bridge.calls)  # 已存在不覆写不落盘


class TestSetRoomEnabledToggle(unittest.TestCase):
    """开关开启路径：注册表缺口先自愈补登记，start_room 不再 404。"""

    def _config_rooms(self):
        return [{"platform": "bilibili", "room_id": "123", "enabled": False}]

    def _patch_write(self):
        """_write_config_section 桩：真实执行 mutator（found 置位语义）。"""
        def _fake_write(mutator):
            mutator({"rooms": self._config_rooms()})
            return True
        return patch.object(dc, "_write_config_section", side_effect=_fake_write)

    def test_toggle_on_selfheals_missing_registry(self):
        bridge = _StubBridge()  # 注册表空（rooms.json 丢失场景）
        svc = _StubService(bridge)
        with self._patch_write():
            res = dc.DanmakuListenerService.set_room_enabled(
                svc, "bilibili", "123", True)
        self.assertTrue(res["success"], f"开关开启应成功: {res}")
        self.assertTrue(res["listening"])
        self.assertIn("start:bilibili:123", bridge.calls)

    def test_toggle_off_still_succeeds(self):
        bridge = _StubBridge()
        bridge._rooms["bilibili:123"] = {"platform": "bilibili",
                                         "room_id": "123", "status": "running"}
        svc = _StubService(bridge)
        with self._patch_write():
            res = dc.DanmakuListenerService.set_room_enabled(
                svc, "bilibili", "123", False)
        self.assertTrue(res["success"])
        self.assertFalse(res["listening"])


if __name__ == "__main__":
    unittest.main()
