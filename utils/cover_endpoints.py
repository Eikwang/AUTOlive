# -*- coding: utf-8 -*-
"""点歌翻唱控制面路由注册（T3/2026-11-02 实测批次根修）。

架构根因：/cover/* 原注册于 WebServer.app（主进程 uvicorn，api_port 8082），
而实际工作流=webui 单进程（frontend/main.py，NiceGUI 8086）——主进程不启动时
端点不可达（netstat 实证 8082 无监听）。抽共享注册函数两侧挂载：

- 主进程：web_server.py（双进程调试面，行为不变）
- webui 进程：frontend/main.py _register_api_endpoints（单进程工作流，同源 8086）

CoverQueue 依赖 config.get(*keys) 变长键（Config 对象形态）——webui 侧传
AutoliveApp.config（init_config 同形态，零适配）；my_handle 可选（webui 轻量
进程无播放链，on_done=None=完成仅落盘，插播依赖点歌主链）。
"""
import logging
import threading

from fastapi import HTTPException

logger = logging.getLogger(__name__)

_CONFIG_LOCK = threading.Lock()  # 写者矩阵：cover_worker 唯一写者，端点只读窄面


def register_cover_routes(app, config, get_my_handle=None):
    """在给定 FastAPI/NiceGUI app 上注册 /cover/status 与 /cover/enqueue。

    Args:
        app: FastAPI 或 NiceGUI（FastAPI 子类）实例。
        config: Config 对象（get(*keys) 变长键形态；CoverQueue/音高等按此消费）。
        get_my_handle: 可选 callable → my_handle 或 None。提供且其 cover_queue
            已有 on_done 时沿用（主进程=弹幕点歌插播链）；None=完成仅落盘。
    """
    get_my_handle = get_my_handle or (lambda: None)

    @app.get("/cover/status")
    async def cover_status():
        try:
            mh = get_my_handle()
            cq = getattr(mh, "cover_queue", None) if mh else None
            if cq is None:
                return {"available": False, "reason": "翻唱队列未初始化（未触发过点歌）"}
            with cq.lock:
                queue = [{"song": j["song"], "viewer": j["viewer"],
                          "retries_left": j["retries_left"],
                          "wait_code": j.get("wait_code", ""),
                          "wait_reason": j.get("wait_reason", "")} for j in cq.queue]
                cur = dict(cq.current) if cq.current else None
                if cur and cur.get("started_at"):
                    import time as _time
                    cur = {**cur, "elapsed_s": round(_time.time() - cur["started_at"], 1)}
            return {"available": True, "queue": queue, "current": cur,
                    "policies": cq._policies()}
        except Exception as e:
            logger.error("/cover/status 异常", exc_info=True)
            return {"available": False, "reason": str(e)}

    @app.post("/cover/enqueue")
    async def cover_enqueue(req: dict):
        song = str((req or {}).get("song") or "").strip()
        if not song:
            raise HTTPException(status_code=400, detail="缺少 song 参数")
        try:
            mh = get_my_handle()
            cq = getattr(mh, "cover_queue", None) if mh else None
            if cq is None:
                from utils.cover_worker import CoverQueue
                cq = CoverQueue(config, None)
                if mh is not None:
                    mh.cover_queue = cq
            fh = getattr(mh, "feature_handler", None) if mh else None
            if getattr(cq, "on_done", None) is None and fh is not None:
                cq.on_done = fh._play_cover_done
            profile = config.get("choose_song", "cover_profile_id") or "default"
            r = cq.enqueue("webui-manual", song, profile, manual=True)
            if r.get("action") == "queued":
                cq.ensure_worker()
            return r
        except HTTPException:
            raise
        except Exception as e:
            logger.error("/cover/enqueue 异常", exc_info=True)
            raise HTTPException(status_code=500, detail=str(e))
