"""DanmakuListener Web 前端服务

基于 aiohttp 的轻量级 Web 服务，内嵌 danmaku_listener 后端。
提供房间管理、弹幕实时展示、代理状态监控、关键词屏蔽、系统代理控制。
"""

import asyncio
import json
import os
from typing import Optional

from aiohttp import web
from aiohttp_cors import setup as cors_setup, ResourceOptions
from loguru import logger

from danmaku_listener.web.bridge import DanmakuBridge


# 全局桥接器实例（单例）
_bridge: Optional[DanmakuBridge] = None


def get_bridge() -> DanmakuBridge:
    """获取全局 DanmakuBridge 单例"""
    global _bridge
    if _bridge is None:
        _bridge = DanmakuBridge()
    return _bridge


async def index(request: web.Request) -> web.FileResponse:
    """返回前端首页"""
    html_path = os.path.join(os.path.dirname(__file__), "static", "index.html")
    resp = web.FileResponse(html_path)
    # 禁用缓存，确保外部浏览器不使用旧版 HTML/脚本
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp


async def api_get_status(request: web.Request) -> web.Response:
    """获取系统整体状态 → AC-001"""
    bridge = request.app.get("bridge") or get_bridge()
    return web.json_response(bridge.get_status())




async def api_get_rooms(request: web.Request) -> web.Response:
    """获取所有房间列表"""
    bridge = request.app.get("bridge") or get_bridge()
    rooms = list(bridge._rooms.values())
    return web.json_response({"rooms": rooms})


async def api_add_room(request: web.Request) -> web.Response:
    """添加房间并启动监听 → AC-002, AC-003"""
    bridge = request.app.get("bridge") or get_bridge()
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"success": False, "error": "Invalid JSON"}, status=400)

    room = data.get("room", "")
    if not room:
        return web.json_response({"success": False, "error": "Missing 'room' field"}, status=400)

    try:
        result = await bridge.add_room(room)
        return web.json_response(result)
    except Exception as e:
        from danmaku_listener.web.bridge import RoomError
        if isinstance(e, RoomError):
            return web.json_response({"success": False, "error": e.message}, status=e.status)
        return web.json_response({"success": False, "error": str(e)}, status=500)


async def api_remove_room(request: web.Request) -> web.Response:
    """停止并移除房间 → AC-006"""
    bridge = request.app.get("bridge") or get_bridge()
    platform = request.match_info["platform"]
    room_id = request.match_info["room_id"]

    try:
        result = await bridge.remove_room(platform, room_id)
        return web.json_response(result)
    except Exception as e:
        from danmaku_listener.web.bridge import RoomError
        if isinstance(e, RoomError):
            return web.json_response({"success": False, "error": e.message}, status=e.status)
        return web.json_response({"success": False, "error": str(e)}, status=500)


async def api_stop_all(request: web.Request) -> web.Response:
    """停止所有房间 → AC-006"""
    bridge = request.app.get("bridge") or get_bridge()
    try:
        result = await bridge.stop_all()
        return web.json_response(result)
    except Exception as e:
        return web.json_response({"success": False, "error": str(e)}, status=500)






async def api_get_keywords(request: web.Request) -> web.Response:
    """获取关键词列表和过滤状态 → AC-007"""
    bridge = request.app.get("bridge") or get_bridge()
    return web.json_response(bridge.get_keywords())


async def api_add_keyword(request: web.Request) -> web.Response:
    """添加屏蔽关键词 → AC-007"""
    bridge = request.app.get("bridge") or get_bridge()
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"success": False, "error": "Invalid JSON"}, status=400)

    keyword = data.get("keyword", "")
    if not keyword:
        return web.json_response({"success": False, "error": "Missing 'keyword' field"}, status=400)

    try:
        result = bridge.add_keyword(keyword)
        return web.json_response(result)
    except ValueError as e:
        return web.json_response({"success": False, "error": str(e)}, status=400)


async def api_remove_keyword(request: web.Request) -> web.Response:
    """删除屏蔽关键词 → AC-018"""
    bridge = request.app.get("bridge") or get_bridge()
    keyword = request.match_info["keyword"]
    result = bridge.remove_keyword(keyword)
    return web.json_response(result)


async def api_toggle_keyword_filter(request: web.Request) -> web.Response:
    """开启/关闭关键词过滤 → AC-018"""
    bridge = request.app.get("bridge") or get_bridge()
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"success": False, "error": "Invalid JSON"}, status=400)

    enabled = data.get("enabled", True)
    result = bridge.toggle_keyword_filter(enabled)
    return web.json_response(result)


async def api_get_config(request: web.Request) -> web.Response:
    """获取白名单配置参数当前值（S2 设置面板）"""
    bridge = request.app.get("bridge") or get_bridge()
    from danmaku_listener.config.config_store import CONFIG_WHITELIST, default_config_path, load_overrides
    from danmaku_listener.config.settings import get_settings

    settings = get_settings()
    values = {f: getattr(settings, f) for f in CONFIG_WHITELIST}
    return web.json_response({
        "config": values,
        "source": "config.local.toml" if os.path.exists(default_config_path()) else "defaults",
        "all_require_restart": True,
    })


async def api_put_config(request: web.Request) -> web.Response:
    """更新白名单配置参数（S2 设置面板；round2 C3：写入跟随 --config 或默认 config.local.toml）"""
    bridge = request.app.get("bridge") or get_bridge()
    from danmaku_listener.config.config_store import (
        CONFIG_WHITELIST, default_config_path, load_overrides, save_fields)
    from danmaku_listener.config.settings import Settings, get_settings, set_settings

    try:
        data = await request.json()
    except Exception:
        return web.json_response({"success": False, "error": "Invalid JSON"}, status=400)

    updates = data.get("config") or {}
    unknown = [k for k in updates if k not in CONFIG_WHITELIST]
    if unknown:
        return web.json_response({
            "success": False,
            "error": f"未知/不可配置参数: {unknown}",
            "allowed": CONFIG_WHITELIST,
        }, status=400)

    # 值校验（round3 L-3）：Settings(**merged) 试构造，model_validator 错误转译 400
    current = get_settings()
    merged = {f: getattr(current, f) for f in CONFIG_WHITELIST}
    merged.update(updates)
    try:
        validated = Settings(**merged)
    except Exception as e:
        return web.json_response({
            "success": False,
            "error": f"参数校验失败: {e}",
        }, status=400)

    # 写盘（tomlkit 保留结构；失败告警不崩溃）
    target = request.app.get("config_path") or default_config_path()
    try:
        save_fields(target, updates)
    except Exception as e:
        return web.json_response({"success": False, "error": f"写入配置失败: {e}"}, status=500)

    # 植入新值（round3 L-1：模块级覆盖实例，立即对 get_settings 生效；
    # 引擎构造参数等仍需重启生效——统一声明见响应）
    set_settings(validated)

    return web.json_response({
        "success": True,
        "config": {f: getattr(validated, f) for f in CONFIG_WHITELIST},
        "all_require_restart": True,
    })


async def websocket_handler(request: web.Request) -> web.WebSocketResponse:
    """WebSocket 连接处理器

    前端通过此端点接收实时弹幕推送。
    连接时发送初始状态，断开时从广播列表移除。
    """
    ws = web.WebSocketResponse(heartbeat=30, timeout=120, max_msg_size=16 * 1024 * 1024)
    await ws.prepare(request)

    bridge = request.app.get("bridge") or get_bridge()
    await bridge.add_ws_client(ws)

    # 发送初始状态
    try:
        status_msg = json.dumps(
            {"type": "status", "data": bridge.get_status()},
            ensure_ascii=False,
        )
        await ws.send_str(status_msg)
    except Exception as e:
        logger.warning(f"Failed to send initial status: {e}")

    # 处理客户端消息
    try:
        async for msg in ws:
            if msg.type == web.WSMsgType.TEXT:
                # 前端发来的消息（目前不处理，后续任务扩展）
                pass
            elif msg.type == web.WSMsgType.ERROR:
                logger.error(f"WS error: {ws.exception()}")
                break
            elif msg.type == web.WSMsgType.CLOSE:
                break
    except Exception as e:
        logger.warning(f"WS handler error: {e}")
    finally:
        await bridge.remove_ws_client(ws)
        logger.debug(f"WS client disconnected")

    return ws


def create_app() -> web.Application:
    """创建 aiohttp Application

    配置 CORS、路由、静态文件服务。
    """
    app = web.Application()

    # 配置 CORS
    cors = cors_setup(app, defaults={
        "*": ResourceOptions(
            allow_credentials=True,
            expose_headers="*",
            allow_headers="*",
            allow_methods="*",
        )
    })

    # 静态文件路由
    static_dir = os.path.join(os.path.dirname(__file__), "static")

    # 页面路由
    app.router.add_get("/", index)

    # 系统状态 API
    cors.add(app.router.add_get("/api/status", api_get_status))

    # 系统代理控制 API

    # WebSocket 弹幕推送
    app.router.add_get("/ws", websocket_handler)

    # 房间管理 API
    cors.add(app.router.add_get("/api/rooms", api_get_rooms))
    cors.add(app.router.add_post("/api/rooms", api_add_room))
    cors.add(app.router.add_delete("/api/rooms/{platform}/{room_id}", api_remove_room))
    cors.add(app.router.add_post("/api/rooms/stop-all", api_stop_all))

    # 配置参数 API（S2 设置面板）
    cors.add(app.router.add_get("/api/config", api_get_config))
    cors.add(app.router.add_put("/api/config", api_put_config))

    # 关键词屏蔽 API
    cors.add(app.router.add_get("/api/keywords", api_get_keywords))
    cors.add(app.router.add_post("/api/keywords", api_add_keyword))
    cors.add(app.router.add_delete("/api/keywords/{keyword}", api_remove_keyword))
    cors.add(app.router.add_put("/api/keywords/toggle", api_toggle_keyword_filter))

    # 静态文件服务
    app.router.add_static("/static", static_dir)

    return app


async def main() -> None:
    """主函数：启动 Web 服务（测试控制台；端口 8080 与 PushServer 8765 隔离）"""
    from danmaku_listener.config.settings import get_settings
    from danmaku_listener.push.ws_server import PushServer

    settings = get_settings()
    host = "localhost"
    port = settings.web_port

    app = create_app()

    # 广播单源（CEO 义务块）：/ws 客户端管理委托 PushServer 实例（非 8765 那个）
    from danmaku_listener.push.ws_server import PushServer as _PS
    push = _PS(host="127.0.0.1", port=0, token=None)  # port=0：仅复用 handler/broadcast，不监听
    app.router.add_get("/ws", push.ws_handler)
    bridge = get_bridge()
    bridge.set_push_server(push)

    runner = web.AppRunner(app, keepalive_timeout=30, client_timeout=60)
    await runner.setup()

    site = web.TCPSite(runner, host, port, reuse_address=True)
    await site.start()

    print(f"DanmakuListener Web 服务已启动: http://{host}:{port}")
    print(f"WebSocket 端点: ws://{host}:{port}/ws")
    print("按 Ctrl+C 停止服务")

    # 保持服务运行
    try:
        await asyncio.Future()
    except asyncio.CancelledError:
        print("服务停止中...")
        bridge = get_bridge()
        await bridge.shutdown()
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
