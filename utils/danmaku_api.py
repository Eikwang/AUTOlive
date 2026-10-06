# -*- coding: utf-8 -*-
"""
DanmakuListener 控制面 FastAPI 路由（前端 webui → main.py 进程唯一通道）。

前端（webui/NiceGUI 进程）与本服务（main.py 进程）跨进程边界——
全部控制操作经本路由组直调进程内 DanmakuListenerService（Eng A1 线程亲和封装在服务层）。
由 utils/web_server.py 的 _register_routes() 末尾挂接（零侵入一行调用）。
"""
from __future__ import annotations

from typing import Optional

from utils.my_log import logger


def register_danmaku_routes(app, config) -> None:
    """把 /danmaku/* 控制面路由挂到 main.py 进程的 FastAPI 应用。

    由 WebServer._init_fastapi_app/_register_routes 调用；
    服务实例经 get_service_instance() 延迟解析（main.py 启动顺序无关）。
    """
    from fastapi import HTTPException

    from utils.danmaku_consumer import get_service_instance

    def _svc():
        svc = get_service_instance()
        if svc is None:
            raise HTTPException(status_code=503, detail="DanmakuListener 服务未启用（config.danmaku_listener.enabled=false）")
        return svc

    def _unwrap(result: dict) -> dict:
        """RoomError 三段式 → HTTP 200 内嵌 success=False（前端按 success 分支呈现）。"""
        return result if isinstance(result, dict) else {"success": False, "error": str(result)}

    @app.get("/danmaku/status")
    def danmaku_status():
        svc = get_service_instance()
        return svc.get_status() if svc else {"enabled": False, "running": False}

    @app.get("/danmaku/rooms")
    def danmaku_rooms():
        try:
            return _svc().get_rooms()
        except HTTPException:
            raise
        except Exception as e:  # noqa: BLE001
            logger.error("[danmaku_api] rooms 查询失败", exc_info=True)
            raise HTTPException(status_code=500, detail=str(e)[:200])

    @app.post("/danmaku/rooms")
    def danmaku_add_room(body: dict):
        return _unwrap(_svc().add_room(
            str(body.get("room_spec") or body.get("room") or ""),
            str(body.get("platform") or ""),
            start=bool(body.get("start", True)),
        ))

    @app.post("/danmaku/rooms/{platform}/{room_id}/start")
    def danmaku_start_room(platform: str, room_id: str):
        return _unwrap(_svc().start_room(platform, room_id))

    @app.post("/danmaku/rooms/{platform}/{room_id}/stop")
    def danmaku_stop_room(platform: str, room_id: str):
        return _unwrap(_svc().stop_room(platform, room_id))

    @app.delete("/danmaku/rooms/{platform}/{room_id}")
    def danmaku_remove_room(platform: str, room_id: str):
        return _unwrap(_svc().remove_room(platform, room_id))

    @app.get("/danmaku/keywords")
    def danmaku_keywords():
        svc = _svc()
        bridge = getattr(svc, "_bridge", None)
        if bridge is None:
            return {"keywords": [], "enabled": False}
        return {"keywords": list(bridge._blocked_keywords),
                "enabled": bool(bridge._keyword_filter_enabled)}

    @app.post("/danmaku/keywords")
    def danmaku_add_keyword(body: dict):
        return _unwrap(_svc().add_keyword(str(body.get("keyword") or "")))

    @app.delete("/danmaku/keywords/{keyword}")
    def danmaku_remove_keyword(keyword: str):
        return _unwrap(_svc().remove_keyword(keyword))

    @app.post("/danmaku/keywords/toggle")
    def danmaku_toggle_keywords(body: dict):
        return _unwrap(_svc().set_keyword_filter(bool(body.get("enabled", True))))

    @app.post("/danmaku/platform/{platform}/toggle")
    def danmaku_platform_toggle(platform: str, body: dict):
        return _unwrap(_svc().set_platform_enabled(platform, bool(body.get("enabled", True))))

    @app.post("/danmaku/service/toggle")
    def danmaku_service_toggle(body: dict):
        return _unwrap(_svc().set_service_enabled(bool(body.get("enabled", True))))

    @app.get("/danmaku/events")
    def danmaku_events(since: int = 0):
        svc = get_service_instance()
        if svc is None:
            return {"events": [], "last_seq": 0}
        evts = svc.events_since(since)
        return {"events": evts, "last_seq": evts[-1]["seq"] if evts else since}

    @app.post("/danmaku/login/ack")
    def danmaku_login_ack():
        svc = _svc()
        svc.ack_login_done()
        return {"success": True}

    @app.get("/danmaku/login_state")
    def danmaku_login_state():
        """登录弹窗数据源（webui 进程 danmaku_login.py 轮询；替代失效的进程内轮询）。"""
        svc = get_service_instance()
        if svc is None:
            return {"active": None, "queue": 0}
        return {"active": svc.login_active, "queue": len(svc.login_queue)}

    logger.info("[danmaku_api] /danmaku/* 控制面路由已挂载")
