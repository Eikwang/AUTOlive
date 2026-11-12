# -*- coding: UTF-8 -*-
"""守卫：登录入口放行 + 会话有效性校验（2026-11-12 根修批次——方案二）

背景（审查实证，B站 nav API code=-101 isLogin=False）：
1. 登录态判定是凭证存在性检查，会话过期后三处入口全部被「已有登录态」阻断：
   - _process 269-271（原）→「已有登录态，无需重复登录」直接 return
   - _onboard_login（原）→「登录态正常」return，登录窗口永不弹出
   - 平台行登录按钮 `if not logged` 独占渲染 → 过期后无按钮
   → 发送 -101「重新登录」死循环（「不能登陆、不能发送」根因 A）。
2. 受控平台关窗确认制漏 hit 检查（原）——匿名会话（xhs web_session 游客态）
   也落盘记 logged_in（2026-11-12 11:29 xhs 快照假"登录成功"实证）。

守卫（源码断言 + 行为断言，同 test_relogin_failure_visibility 风格）：
- _process 显式登录请求不被已有登录态阻断（行为级）；
- _profile_login_coro 关窗分支必须按判定名命中落盘（源码级）；
- 平台行登录按钮常渲染 + 徽章含过期分级（源码级）；
- onboarding 过期照样弹窗（源码级）；
- 校验器 bilibili nav 判定 / stale 启发式 / 后台刷新缓存（行为级，零真实网络）。
"""
import asyncio
import os
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


def _src(rel: str) -> str:
    return (REPO / rel).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. _process 放行（行为级——原阻断删除后显式登录请求必须到达 flow）
# ---------------------------------------------------------------------------

class TestProcessExplicitLoginNotBlocked:
    def _make_service(self):
        from frontend.ui.components.danmaku_login_service import DanmakuLoginService
        return DanmakuLoginService()

    def test_check_login_true_still_runs_flow(self):
        svc = self._make_service()
        svc._loop = asyncio.new_event_loop()
        try:
            ran = {"flow": False}

            async def _flow():
                ran["flow"] = True
                await asyncio.sleep(0)

            svc._meta = lambda platform: {"check": lambda: True,  # 已有登录态（过期凭证同形态）
                                          "flow": _flow}
            svc._process("bilibili")
            assert ran["flow"], "check_login=True 时 flow 必须照常执行——" \
                                "显式登录请求不得被「已有登录态」阻断（根因 A 回归即红）"
            assert svc.get_state()["status"] == "success"
        finally:
            svc._loop.close()
            svc._stop.set()

    def test_check_login_true_flow_failure_reports_failed_not_success(self):
        svc = self._make_service()
        svc._loop = asyncio.new_event_loop()
        try:
            async def _flow():
                raise RuntimeError("launch failed")

            svc._meta = lambda platform: {"check": lambda: True, "flow": _flow}
            with pytest.raises(RuntimeError):
                svc._process("kuaishou")
        finally:
            svc._loop.close()
            svc._stop.set()


# ---------------------------------------------------------------------------
# 2. 关窗确认制 hit 检查（源码级——xhs 假"登录成功"根因 B 回归即红）
# ---------------------------------------------------------------------------

class TestProfileCloseRequiresMarkerHit:
    def test_close_branch_guards_by_marker_names(self):
        src = _src("frontend/ui/components/danmaku_login_service.py")
        # is_closed 分支：无判定名关窗确认 + 有判定名必须 hit 才落盘
        assert "if not names and last_cookies:" in src
        assert "elif names and hit:" in src
        # except 分支（页面已关）同样不得无条件落盘
        assert "if (not names and last_cookies) or (names and hit):" in src
        # 原无条件落盘形态不得回归：coro 段内不得再出现裸 `if last_cookies:` 落盘条件
        seg = src.split("def _profile_login_coro")[1].split("return outcome")[0]
        assert "if last_cookies:\n" not in seg

    def test_window_closed_outcome_present(self):
        """未命中关窗 → outcome="window_closed"（登录未完成，不假成功）"""
        src = _src("frontend/ui/components/danmaku_login_service.py")
        seg = src.split("def _profile_login_coro")[1].split("return outcome")[0]
        assert seg.count('outcome = "window_closed"') >= 2  # is_closed/except 双分支兜底齐全


# ---------------------------------------------------------------------------
# 3. 平台行按钮常渲染 + onboarding 过期弹窗（源码级）
# ---------------------------------------------------------------------------

class TestPlatformRowEntryAlwaysRendered:
    def test_relogin_button_unconditional(self):
        src = _src("frontend/ui/tabs/platform_config.py")
        assert '"重新登录" if logged else "登录"' in src
        # 原独占渲染形态不得回归
        assert "if not logged:\n                            ui.button" not in src

    def test_badge_has_expired_tier(self):
        src = _src("frontend/ui/tabs/platform_config.py")
        assert '"expired": ("登录态已过期", "negative")' in src
        assert '"stale": ("登录态可能过期", "warning")' in src
        assert 'sess in ("expired", "stale")' in src

    def test_onboard_validates_session_before_blocking(self):
        src = _src("frontend/ui/tabs/platform_config.py")
        assert "validate_session_now" in src
        assert 'if sess != "expired":' in src
        # 网络校验必须 to_thread（事件循环阻塞红线——audio-play/webui-async 前科）
        assert "asyncio.to_thread(login_svc.validate_session_now" in src

    def test_signature_includes_session_state(self):
        src = _src("frontend/ui/tabs/platform_config.py")
        assert "login_svc.session_status(p)" in src


# ---------------------------------------------------------------------------
# 4. 校验器（行为级——零真实网络）
# ---------------------------------------------------------------------------

class TestLoginSessionValidator:
    def _validator(self):
        from danmaku_listener.engines.login_session_validator import LoginSessionValidator
        return LoginSessionValidator()

    def _state_file(self, tmp_path, name="bilibili_storage_state.json"):
        p = tmp_path / name
        p.write_text('{"cookies": [{"name": "SESSDATA", "value": "x", "domain": ".bilibili.com"}]}',
                     encoding="utf-8")
        return str(p)

    def _patch_nav(self, monkeypatch, payload=None, exc=None):
        """patch requests.Session → nav payload（校验函数内 import requests）"""

        class _FakeResp:
            def json(self_inner):
                if exc:
                    raise exc
                return payload or {}

        class _FakeJar:
            def set(self_inner, *a, **k):
                pass

        class _FakeSession:
            def __init__(self_inner):
                self_inner.cookies = _FakeJar()

            def get(self_inner, url, timeout=None, headers=None):
                return _FakeResp()

        import requests
        monkeypatch.setattr(requests, "Session", _FakeSession)

    def test_bilibili_valid(self, tmp_path, monkeypatch):
        self._patch_nav(monkeypatch, payload={"code": 0, "data": {"isLogin": True}})
        from danmaku_listener.engines.login_session_validator import _validate_bilibili
        assert _validate_bilibili(self._state_file(tmp_path)) == "valid"

    def test_bilibili_expired(self, tmp_path, monkeypatch):
        self._patch_nav(monkeypatch, payload={"code": -101, "data": {"isLogin": False}})
        from danmaku_listener.engines.login_session_validator import _validate_bilibili
        assert _validate_bilibili(self._state_file(tmp_path)) == "expired"

    def test_bilibili_network_failure_unverified(self, tmp_path, monkeypatch):
        self._patch_nav(monkeypatch, exc=ConnectionError("offline"))
        from danmaku_listener.engines.login_session_validator import _validate_bilibili
        assert _validate_bilibili(self._state_file(tmp_path)) == "unverified"

    def test_stale_heuristic_by_mtime(self, tmp_path):
        v = self._validator()
        p = tmp_path / "kuaishou_storage_state.json"
        p.write_text('{"cookies": [{"name": "kuaishou.live.bfb1s", "value": "x"}]}',
                     encoding="utf-8")
        old = time.time() - 30 * 86400  # 30 天前
        os.utime(str(p), (old, old))
        assert v.status("kuaishou", str(tmp_path)) == "stale"
        os.utime(str(p), None)  # 恢复新鲜
        assert v.status("kuaishou", str(tmp_path)) == "unverified"

    def test_background_refresh_updates_cache(self, tmp_path, monkeypatch):
        """有真校验器平台：status 返回启发式临时值，后台刷新后缓存呈现真值"""
        v = self._validator()
        self._state_file(tmp_path)  # bilibili 载体文件——缺失则 status 返回 None
        from danmaku_listener.engines import login_session_validator as lsv
        monkeypatch.setitem(lsv.VALIDATORS, "bilibili",
                            lambda path: "expired")  # 模拟 nav -101
        assert v.status("bilibili", str(tmp_path)) in ("stale", "unverified")  # 临时值
        deadline = time.time() + 5.0
        while time.time() < deadline:
            if v.status("bilibili", str(tmp_path)) == "expired":
                break
            time.sleep(0.05)
        assert v.status("bilibili", str(tmp_path)) == "expired"

    def test_validate_now_caches(self, tmp_path, monkeypatch):
        v = self._validator()
        from danmaku_listener.engines import login_session_validator as lsv
        calls = {"n": 0}

        def _stub(path):
            calls["n"] += 1
            return "valid"

        monkeypatch.setitem(lsv.VALIDATORS, "bilibili", _stub)
        self._state_file(tmp_path)  # 载体文件落盘（返回值无需使用）
        assert v.validate_now("bilibili", str(tmp_path)) == "valid"
        assert v.status("bilibili", str(tmp_path)) == "valid"  # 缓存命中
        assert calls["n"] == 1  # 第二次未再执行校验

    def test_invalidate_clears_cache(self, tmp_path, monkeypatch):
        v = self._validator()
        from danmaku_listener.engines import login_session_validator as lsv
        monkeypatch.setitem(lsv.VALIDATORS, "bilibili", lambda path: "valid")
        self._state_file(tmp_path)  # 载体文件落盘（返回值无需使用）
        v.validate_now("bilibili", str(tmp_path))
        v.invalidate("bilibili")
        with v._lock:
            assert "bilibili" not in v._cache

    def test_missing_file_returns_none(self, tmp_path):
        v = self._validator()
        assert v.status("bilibili", str(tmp_path)) is None
        assert v.validate_now("bilibili", str(tmp_path)) is None


# ---------------------------------------------------------------------------
# 5. 服务层接线（session_status/validate_session_now/invalidate 时机）
# ---------------------------------------------------------------------------

class TestServiceValidatorWiring:
    def test_session_status_none_when_not_logged(self):
        from frontend.ui.components.danmaku_login_service import DanmakuLoginService
        svc = DanmakuLoginService()
        svc._meta = lambda platform: None
        assert svc.session_status("meituan") is None

    def test_process_invalidates_cache_after_flow(self):
        from frontend.ui.components.danmaku_login_service import DanmakuLoginService
        svc = DanmakuLoginService()
        svc._loop = asyncio.new_event_loop()
        try:
            invalidated = {"hit": False}

            def _invalidate(platform):
                invalidated["hit"] = True

            async def _flow():
                await asyncio.sleep(0)

            svc._validator.invalidate = _invalidate
            svc._meta = lambda platform: {"check": lambda: True, "flow": _flow}
            svc._process("douyin")
            assert invalidated["hit"], "登录动作完成后必须失效会话校验缓存（徽章按新凭证重判）"
        finally:
            svc._loop.close()
            svc._stop.set()
