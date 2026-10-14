# -*- coding: utf-8 -*-
"""
DanmakuListener 控制面 FastAPI 路由（前端 webui → main.py 进程唯一通道）。

前端（webui/NiceGUI 进程）与本服务（main.py 进程）跨进程边界——
全部控制操作经本路由组直调进程内 DanmakuListenerService（Eng A1 线程亲和封装在服务层）。
由 utils/web_server.py 的 _register_routes() 末尾挂接（零侵入一行调用）。
"""
from __future__ import annotations

import json
import threading
from typing import Optional

from utils.my_log import logger


def register_danmaku_routes(app, config) -> None:
    """把 /danmaku/* 控制面路由挂到 main.py 进程的 FastAPI 应用。

    由 WebServer._init_fastapi_app/_register_routes 调用；
    服务实例经 get_service_instance() 延迟解析（main.py 启动顺序无关）。
    """
    from fastapi import HTTPException

    from utils.danmaku_consumer import get_service_instance, _write_config_section

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

    def _gone(old_endpoint: str, replacement: str):
        """RFC 9457 problem+json（DX 发现 7）：410 防复活 + 修复指引。"""
        from fastapi import Response
        return Response(
            content=json.dumps({
                "type": "https://AUTOlive.local/errors/endpoint-retired",
                "title": "Endpoint Retired", "status": 410,
                "detail": old_endpoint + " 已随配置面/运行面分离退役",
                "cause": "配置期操作由 webui 直写 config.json；运行期操作走窄端点",
                "fix": replacement, "instance": old_endpoint,
            }, ensure_ascii=False),
            status_code=410, media_type="application/problem+json")

    @app.post("/danmaku/rooms")
    def danmaku_add_room_gone(body: dict):
        return _gone("POST /danmaku/rooms", "webui 平台配置页直写 config.json")

    @app.post("/danmaku/rooms/{platform}/{room_id}/start")
    def danmaku_start_room(platform: str, room_id: str):
        return _unwrap(_svc().start_room(platform, room_id))

    @app.post("/danmaku/rooms/{platform}/{room_id}/stop")
    def danmaku_stop_room(platform: str, room_id: str):
        return _unwrap(_svc().stop_room(platform, room_id))

    @app.delete("/danmaku/rooms/{platform}/{room_id}")
    def danmaku_remove_room(platform: str, room_id: str):
        return _unwrap(_svc().remove_room(platform, room_id))

    @app.delete("/danmaku/rooms/{platform}/{room_id}")
    def danmaku_remove_room_gone(platform: str, room_id: str):
        return _gone("DELETE /danmaku/rooms/{platform}/{room_id}",
                     "webui 平台配置页删除（配置期直写）；运行期先 enabled=false 停监听")

    @app.post("/danmaku/rooms/{platform}/{room_id}/enabled")
    def danmaku_room_enabled(platform: str, room_id: str, body: dict):
        """窄端点（CEO F1 config-first）：写 config + 联动该房间启停（Eng F-ENG-1 内存节刷新）。"""
        return _unwrap(_svc().set_room_enabled(platform, room_id, bool(body.get("enabled", True))))

    @app.post("/danmaku/keywords/reload")
    def danmaku_keywords_reload():
        """窄端点（M-1 ③）：磁盘 config.keywords 热载 Bridge（禁 body 传词表）。"""
        return _unwrap(_svc().reload_keywords())

    @app.get("/danmaku/keywords")
    def danmaku_keywords():
        svc = _svc()
        bridge = getattr(svc, "_bridge", None)
        if bridge is None:
            return {"keywords": [], "enabled": False}
        return {"keywords": list(bridge._blocked_keywords),
                "enabled": bool(bridge._keyword_filter_enabled)}

    @app.post("/danmaku/keywords")
    @app.delete("/danmaku/keywords/{keyword}")
    @app.post("/danmaku/keywords/toggle")
    def danmaku_keywords_write_retired(keyword: str = "", body: dict = None):
        return _gone("关键词写端点", "webui 平台配置页直写 config.json；运行期热生效自动触发 reload")

    @app.post("/danmaku/platform/{platform}/toggle")
    def danmaku_platform_toggle(platform: str, body: dict):
        return _gone("/danmaku/platform/{platform}/toggle",
                     "platforms 节=可添加平台白名单（Eng F-ENG-3）；监听启停走房间级 enabled 窄端点")

    @app.post("/danmaku/service/toggle")
    def danmaku_service_toggle(body: dict):
        """F-ENG-5 503 死路修复：enabled=true 且服务未构建时在线 build+start。

        跨进程状态纪律（假启动批次修复 2026-10-12）：main.py 与 webui 各持 config
        内存副本（config_watcher 未接线），本端点是运行面 enabled 的唯一写手——
        落盘（_write_config_section）+ 内存构建双写，前端轮询以 /danmaku/status
        的运行面值为准。
        """
        target = bool(body.get("enabled", True))
        svc = get_service_instance()
        if svc is None:
            if not target:
                # 服务本就未构建：幂等返回（此前会 AttributeError——svc.set_service_enabled）
                return {"success": True, "enabled": False, "already_off": True}
            try:
                from utils.danmaku_consumer import DanmakuListenerService, _write_config_section
                if not _write_config_section(lambda sec: sec.__setitem__("enabled", True)):
                    return {"success": False, "error": "config 落盘失败",
                            "reason_code": "config.write_failed",
                            "fix_hint": "检查 config.json 是否被占用", "docs_anchor": "docs/danmaku_module.md"}
                from utils.config import Config
                cfg = Config.config or {}
                sec = cfg.get("danmaku_listener") or {}
                sec["enabled"] = True
                cfg["danmaku_listener"] = sec
                svc = DanmakuListenerService(lambda: dict(sec))
                threading.Thread(target=svc.start, name="svc-start-danmaku-api", daemon=True).start()
                return {"success": True, "enabled": True, "started": True}
            except Exception:
                logger.error("[danmaku_api] 服务在线构建失败", exc_info=True)
                return {"success": False, "error": "服务在线构建失败",
                        "reason_code": "service.build_failed",
                        "fix_hint": "重启系统后重试", "docs_anchor": "docs/danmaku_module.md"}
        return _unwrap(svc.set_service_enabled(target))

    @app.post("/danmaku/send-config")
    def danmaku_send_config(body: dict):
        """发送配置窄端点（写者分配矩阵 F-ENG-2 对齐，2026-10-12）。

        main.py 可达时 Settings.save 会把磁盘 danmaku_listener 节回读覆盖内存
        （防 webui 旧快照覆盖运行期写入）——webui 对该节的 set_config 直写在
        运行期被静默丢弃（toast 假成功）。发送白名单/实发模式的运行期写入必须
        经本端点落盘（对齐 F1 房间开关 config-first 模式）；生效时机=监听重建
        （_async_start 重灌组件 settings）。
        """
        platforms = body.get("platforms", None)
        dry_run = body.get("dry_run", None)

        def _mut(sec):
            # 键缺省不写（2026-10-13 修正：此前缺省写空串会抹掉白名单）
            if platforms is not None:
                sec["send_enabled_platforms"] = str(platforms)
            if dry_run is not None:
                sec["send_dry_run"] = bool(dry_run)

        if not _write_config_section(_mut):
            return {"success": False, "error": "config 落盘失败",
                    "reason_code": "config.write_failed",
                    "fix_hint": "检查 config.json 是否被占用"}
        return {"success": True, "platforms": platforms, "dry_run": dry_run}

    @app.post("/danmaku/keywords-config")
    def danmaku_keywords_config(body: dict):
        """关键词配置窄端点（写者分配矩阵 F-ENG-2 对齐，同 /danmaku/send-config）。

        旧关键词写端点退役时假设 webui 直写有效，但 main 可达时 Settings.save
        会磁盘回读覆盖 danmaku_listener 节——运行期关键词保存假成功且 reload
        热载旧值。本端点落盘后调用 reload_keywords 热载 Bridge（M-1 ③ 闭环）。
        """
        keywords = [str(k) for k in (body.get("keywords") or [])]
        enabled = bool(body.get("enabled", True))

        def _mut(sec):
            sec["keywords"] = keywords
            sec["keyword_filter_enabled"] = enabled

        if not _write_config_section(_mut):
            return {"success": False, "error": "config 落盘失败",
                    "reason_code": "config.write_failed",
                    "fix_hint": "检查 config.json 是否被占用"}
        svc = get_service_instance()
        reloaded = None
        if svc is not None:
            try:
                reloaded = bool(svc.reload_keywords())
            except Exception:
                logger.error("[danmaku_api] 关键词热载失败", exc_info=True)
        return {"success": True, "keywords": keywords,
                "enabled": enabled, "reloaded": reloaded}

    @app.post("/danmaku/rooms-config")
    def danmaku_rooms_config(body: dict):
        """房间列表窄端点（写者分配矩阵 F-ENG-2 对齐，2026-10-13）。

        main 可达时 webui 对 danmaku_listener.rooms 的 set_config 直写被
        Settings.save 磁盘回读静默覆盖（添加/删除房间 toast 假成功、重启回滚）。
        本端点整节落盘 + 服务端 _inject_rooms 权威对账（Eng A5：config 为源——
        新启用房间注入启动，registry 中不在 config 的 ghost 清除）。
        """
        rooms = [r for r in (body.get("rooms") or []) if isinstance(r, dict)]
        if not _write_config_section(lambda sec: sec.__setitem__("rooms", rooms)):
            return {"success": False, "error": "config 落盘失败",
                    "reason_code": "config.write_failed",
                    "fix_hint": "检查 config.json 是否被占用"}
        svc = get_service_instance()
        injected = None
        if svc is not None:
            injected = svc._submit_room_op(svc._inject_rooms)
        return {"success": True, "rooms": rooms, "injected": injected}

    @app.post("/danmaku/relogin")
    def danmaku_relogin(body: dict):
        """手动重登（组件 run_relogin_flow，2026-10-13 用户需求）。

        清平台登录载体（profile 目录/快照/cookie 文件）→ 停该平台在听房间 →
        start_room 既有登录门接管（弹可见窗→登录→保存→自动恢复监听）。
        载体清单与安全边界（目录逃逸防护）在组件侧单源。
        """
        platform = str(body.get("platform") or "")
        room_id = str(body.get("room_id") or "")
        if not platform:
            raise HTTPException(status_code=400, detail="platform 必填")
        svc = _svc()
        from danmaku_listener.web.relogin import run_relogin_flow
        cookie_dir = str((svc._section() or {}).get("cookie_dir") or "./cookie")
        return _unwrap(svc._submit_room_op(
            lambda: run_relogin_flow(svc._bridge, platform, room_id, cookie_dir)))

    @app.post("/danmaku/send-login")
    def danmaku_send_login(body: dict):
        """发送侧登录（双载体平台监听/发送登录态分离——2026-10-13 用户实测闭环补全）。

        douyin/huya 的发送凭证是 persistent profile（指纹绑定，与监听 cookie
        载体独立）、douyu 是独立 cookie 文件——房间行"登录"按钮清理全部载体后
        监听登录门自动收口，发送载体需本端点弹可见窗补登（组件 run_profile_login
        _flow/_huya_login/_douyu_login 已实装）。后台线程执行（扫码等待最长 300s，
        不占控制面 worker）；登录完成直接重试发送即可。
        """
        platform = str(body.get("platform") or "")
        room_id = str(body.get("room_id") or "")
        if platform not in ("douyin", "huya", "douyu"):
            return {"success": False,
                    "error": f"平台 {platform} 无独立发送登录流程",
                    "fix_hint": "该平台发送共用监听凭证或发送时自动引导登录"}
        threading.Thread(target=_send_login_worker, args=(platform, room_id),
                         name=f"send-login-{platform}", daemon=True).start()
        return {"success": True,
                "msg": "发送登录窗口已弹出——完成扫码/登录后重新点『发送』"}

    def _send_login_worker(platform: str, room_id: str) -> None:
        import asyncio as _aio
        loop = _aio.new_event_loop()
        try:
            if platform == "douyin":
                from danmaku_listener.engines.douyin_login import run_profile_login_flow
                res = loop.run_until_complete(run_profile_login_flow(room_id=room_id))
            elif platform == "huya":
                from danmaku_listener.engines.send_login import _huya_login
                res = loop.run_until_complete(_huya_login(room_id))
            else:
                from danmaku_listener.engines.send_login import _douyu_login
                res = loop.run_until_complete(_douyu_login(room_id))
            logger.info(f"[danmaku_api] send-login {platform} 结果: {res.get('status')}")
        except Exception:
            logger.error(f"[danmaku_api] send-login {platform} 异常", exc_info=True)
        finally:
            loop.close()

    @app.post("/danmaku/send")
    def danmaku_send(body: dict):
        """单条手动发送（O2 第一阶段：主 webui 发送入口；鉴权语义同组件 Bearer——
        503 AUTH_UNCONFIGURED / 401 unauthorized 以 body.status_code 内嵌返回，
        对齐控制面 _unwrap 惯例 HTTP 200 + 语义字段）。"""
        platform = str(body.get("platform") or "")
        room_id = str(body.get("room_id") or "")
        content = str(body.get("content") or "")
        if not platform or not room_id or not content.strip():
            raise HTTPException(status_code=400, detail="platform/room_id/content 必填")
        import uuid as _uuid
        request_id = str(body.get("request_id") or f"webui-{_uuid.uuid4().hex[:12]}")
        return _svc().send_danmu(platform, room_id, content, request_id,
                                 token=str(body.get("token") or ""))

    @app.post("/danmaku/send-results")
    def danmaku_send_results(body: dict):
        """发送对账（POST+body token——控制面内部约定；组件侧为 GET+Bearer header）。"""
        request_id = str(body.get("request_id") or "")
        if not request_id:
            raise HTTPException(status_code=400, detail="request_id 必填")
        return _svc().send_results(request_id, token=str(body.get("token") or ""))

    @app.post("/danmaku/send-status")
    def danmaku_send_status(body: dict):
        """发送状态快照（开关/熔断/限速——发送前预检）。"""
        return _svc().send_status_snapshot(token=str(body.get("token") or ""))

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
