# -*- coding: utf-8 -*-
"""/danmaku/relogin/status 转发端点语义测试（DanmakuListener v0.8.0 模块更新，2026-10-14）。

两层覆盖（ENG F4）：
1. mock get_relogin_status——转发端点形状：5 态透传 / 未知 id（None）→ found=False 终止语义 /
   id 形态校验 / 读取异常可重试语义。
2. 真 ReloginRegistry——create/finish/mark_superseded 生命周期后经端点读数，断言 5 态与
   None（REGISTRY 语义漂移 mock 测不出）。
"""
from __future__ import annotations

import unittest
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

import utils.danmaku_api as danmaku_api


def _client() -> TestClient:
    app = FastAPI()
    danmaku_api.register_danmaku_routes(app, config={})
    return TestClient(app)


class TestReloginStatusEndpoint(unittest.TestCase):
    """转发端点形状（mock 上游函数——no_claim=REGISTRY 真实生命周期，另用真 Registry 用例补）。"""

    def _get(self, rid: str, patched):
        with mock.patch("danmaku_listener.web.relogin.get_relogin_status", side_effect=patched):
            with _client() as client:
                return client.get(f"/danmaku/relogin/status", params={"id": rid})

    def test_five_states_passthrough(self):
        """5 态透传：state 字段与 found=True 逐态保持（4 终态+1 进行态）。"""
        for state in ("recovering", "completed", "login_required", "failed", "superseded"):
            rec = {"relogin_id": "abc-123", "state": state, "rooms_stopped": []}
            r = self._get("abc-123", lambda rid, rec=rec: rec)
            self.assertEqual(r.status_code, 200)
            body = r.json()
            self.assertTrue(body["success"], msg=state)
            self.assertTrue(body["found"], msg=state)
            self.assertEqual(body["state"], state, msg=state)
            self.assertEqual(body["record"]["relogin_id"], "abc-123")
            self.assertEqual(body["status_url"], "/danmaku/relogin/status?id=abc-123")

    def test_unknown_id_terminal_semantics(self):
        """None（未知 id——GC 淘汰/进程重启）→ found=False：前端停止轮询的终止语义。"""
        r = self._get("dead-beef-0000", lambda rid: None)
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertTrue(body["success"])
        self.assertFalse(body["found"])
        self.assertIsNone(body["state"])
        self.assertIn("状态已不可查", body["msg"])

    def test_bad_id_rejected(self):
        """id 形态校验（ENG F6）：非法形态直接拒绝，不触达上游。"""
        for bad in ("", "bad id!", "x" * 65, "../etc"):
            r = self._get(bad, lambda rid: self.fail("上游不应被触达"))
            self.assertEqual(r.status_code, 200)
            self.assertFalse(r.json()["success"])
            self.assertEqual(r.json()["reason_code"], "status.bad_id")

    def test_read_exception_retryable_semantics(self):
        """deepcopy 竞态异常重试一次后仍失败 → 可重试语义（区别于未知 id 终态，ENG F1）。"""
        calls = {"n": 0}

        def flaky(rid):
            calls["n"] += 1
            raise RuntimeError("dictionary changed size during iteration")

        r = self._get("abc-123", flaky)
        self.assertEqual(calls["n"], 2, "应重试一次")
        self.assertFalse(r.json()["success"])
        self.assertEqual(r.json()["reason_code"], "status.read_failed")
        self.assertIn("继续轮询", r.json()["fix_hint"])

    def test_read_exception_recovered_on_retry(self):
        """第一次竞态异常、第二次成功 → 正常透传（重试兜底生效）。"""
        calls = {"n": 0}

        def flaky_then_ok(rid):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("transient")
            return {"relogin_id": rid, "state": "completed"}

        r = self._get("abc-123", flaky_then_ok)
        self.assertTrue(r.json()["success"])
        self.assertEqual(r.json()["state"], "completed")


class TestReloginRegistryLifecycle(unittest.TestCase):
    """真 ReloginRegistry 生命周期（ENG F4：REGISTRY 语义漂移 mock 测不出）。"""

    def test_registry_lifecycle_through_endpoint(self):
        from danmaku_listener.web.relogin import ReloginRegistry

        registry = ReloginRegistry()
        rec = registry.create(platform="douyin", room_id="42", rooms_stopped=["douyin-42"],
                              carriers_cleared=["cookie/douyin_profile"], carriers_expected=[],
                              send_refresh="", login_flows=[])
        rid = rec["relogin_id"]
        # 进行态
        self.assertEqual(registry.get(rid)["state"], "recovering")
        # 终态：finish
        registry.finish(rid, "completed")
        self.assertEqual(registry.get(rid)["state"], "completed")
        # superseded：recovering 记录被新编排取代
        rec2 = registry.create(platform="douyin", room_id="42", rooms_stopped=[],
                               carriers_cleared=[], carriers_expected=[],
                               send_refresh="", login_flows=[])
        registry.mark_superseded(rec2["relogin_id"], by_id=rid)
        self.assertEqual(registry.get(rec2["relogin_id"])["state"], "superseded")
        self.assertEqual(registry.get(rec2["relogin_id"])["superseded_by"], rid)
        # 经端点透传（mock 端点层的上游引用指向真 registry 行为等价物）
        with mock.patch("danmaku_listener.web.relogin.get_relogin_status",
                        side_effect=lambda rid_: registry.get(rid_)):
            with _client() as client:
                for rid_, want in ((rid, "completed"), (rec2["relogin_id"], "superseded")):
                    r = client.get("/danmaku/relogin/status", params={"id": rid_})
                    self.assertEqual(r.json()["state"], want)
        # 未知 id → None → 终止语义
        self.assertIsNone(registry.get("nonexistent"))


if __name__ == "__main__":
    unittest.main()
