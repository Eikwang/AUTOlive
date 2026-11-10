# -*- coding: utf-8 -*-
"""点歌翻唱控制面路由注册（T3/2026-11-02 实测批次根修；C1 直调批次扩展）。

架构根因：/cover/* 原注册于 WebServer.app（主进程 uvicorn，api_port 8082），
而实际工作流=webui 单进程（frontend/main.py，NiceGUI 8086）——主进程不启动时
端点不可达（netstat 实证 8082 无监听）。抽共享注册函数两侧挂载：

- 主进程：web_server.py（双进程调试面，行为不变）
- webui 进程：frontend/main.py _register_api_endpoints（单进程工作流，同源 8086）

C1（2026-11-03 终门批准批次）：删除/入队操作抽共享函数（D-1 DRY）——
UI（choose_song.py）进程内直调与端点同源调用，移除 HTTP 自调用绕路
（R-7/R-1 修了自锁形态，本批消除「删除正确性耦合 HTTP 响应及时性」故障类）。
/cover/* 端点自本批起为 debug-only HTTP 面（DX-5：UI 直调后不再依赖，
RFC 9457 项按面记 N/A——debug 面，非对外 API）。

CoverQueue 依赖 config.get(*keys) 变长键（Config 对象形态）——webui 侧传
AutoliveApp.config（init_config 同形态，零适配）；my_handle 可选（webui 轻量
进程无播放链，on_done=None=完成仅落盘，插播依赖点歌主链）。
"""
import asyncio
import logging
import os
import threading
from collections import namedtuple

from fastapi import HTTPException

from utils.autolive_paths import as_keyed_config, config_get

logger = logging.getLogger(__name__)

_CONFIG_LOCK = threading.Lock()  # 写者矩阵：cover_worker 唯一写者，端点只读窄面

# C1/DX-4 语义常量：code→HTTP 映射在端点内单点进行，UI 消费语义常量不消费魔数
DELETE_INVALID = 400    # 非法文件名（白名单拒绝）
DELETE_NOT_FOUND = 404  # 成品不存在（三件全无的聚合语义；FileNotFoundError 仅竞态可及）
DELETE_OSERROR = 500    # 文件占用/权限（Windows 播放中删除高频）

CoverDeleteResult = namedtuple("CoverDeleteResult", "ok code detail deleted")


def delete_cover_artifact(config, fn) -> CoverDeleteResult:
    """删除翻唱成品共享函数（C1/D-1：UI 直调与 /cover/delete 端点同源）。

    契约（spec L-1+DX-4+ENG-11）：返回 CoverDeleteResult(ok, code, detail, deleted)，
    内部捕获不跨边界抛异常；端点将 code 映射为 HTTP 状态码（复刻基准=本文件
    现行 400/404/500 三态映射），UI 将结果映射为 positive/negative notify。

    三件删除部分失败语义（spec CP-1）：逐个 remove，任一 OSError 立即停止
    返回失败（已删的不回滚，对齐原端点现行行为；重试安全——isfile 前置跳过，
    重试会清掉残留件，DX-2 代码验证）。"""
    fn = str(fn or "").strip()
    if not fn or "/" in fn or "\\" in fn or ".." in fn or not fn.endswith(".wav"):
        return CoverDeleteResult(False, DELETE_INVALID, f"非法文件名: {fn}", [])
    from utils.autolive_paths import song_audio_root
    covers_dir = os.path.join(song_audio_root(config), "covers")
    deleted = []
    base = fn[:-4]  # 去 .wav
    for cand in (fn, base + "_vocal.wav", base + "_inst.wav"):
        full = os.path.join(covers_dir, cand)
        if os.path.isfile(full):
            try:
                os.remove(full)
                deleted.append(cand)
            except OSError as e:
                return CoverDeleteResult(False, DELETE_OSERROR,
                                         f"删除失败 {cand}: {e}", deleted)
    if not deleted:
        return CoverDeleteResult(False, DELETE_NOT_FOUND, f"成品不存在: {fn}", [])
    return CoverDeleteResult(True, None, "", deleted)


def enqueue_cover_manual(config, get_my_handle=None, song="") -> dict:
    """一键翻唱入队共享函数（ENG-3：端点与 UI 直调同源，D-1 DRY）。

    返回 CoverQueue.enqueue 原样 dict（action/position/...）；缺 song 抛
    ValueError 由调用方映射。取数链与原端点逐行等价（cover_queue 惰性构造/
    on_done 挂接/profile 读取/ensure_worker）。

    config 双形态（erro6.log 实测修复 2026-11-03）：端点侧传 Config 对象、
    UI 直调侧传页面 dict——profile 读取必须走 config_get 双形态适配
    （dict.get 两参=default 语义会把整个 choose_song 节当值返回→
    get_profile(dict) TypeError unhashable）。先例：autolive_paths.config_get
    （2026-10-16 同坑修复）。"""
    song = str(song or "").strip()
    if not song:
        raise ValueError("缺少 song 参数")
    mh = get_my_handle() if get_my_handle else None
    cq = getattr(mh, "cover_queue", None) if mh else None
    if cq is None:
        from utils.cover_worker import CoverQueue
        cq = CoverQueue(as_keyed_config(config), None)
        if mh is not None:
            mh.cover_queue = cq
    fh = getattr(mh, "feature_handler", None) if mh else None
    if getattr(cq, "on_done", None) is None and fh is not None:
        cq.on_done = fh._play_cover_done
    profile = config_get(config, "choose_song", "cover_profile_id") or "default"
    r = cq.enqueue("webui-manual", song, profile, manual=True)
    if r.get("action") == "queued":
        cq.ensure_worker()
    return r


def register_cover_routes(app, config, get_my_handle=None):
    """在给定 FastAPI/NiceGUI app 上注册 /cover/status、/cover/delete 与
    /cover/enqueue（debug-only HTTP 面，DX-5：UI 直调后仅供调试/外部探查）。

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

    @app.post("/cover/delete")
    async def cover_delete(req: dict):
        """删除翻唱成品（debug-only；T7 用户诉求按钮现已进程内直调）。

        删除 covers/ 下同名三件（成品+分轨 vocal/inst）；song_source 原曲不删。
        文件名白名单校验（防路径穿越）。code→HTTP 映射单点在本端点。
        2026-11-11 断联根修：文件删除同步体移 to_thread（大文件删除秒级，
        原 async handler 内联执行阻塞 webui 事件循环 → WS 判死页面重载）。"""
        r = await asyncio.to_thread(
            delete_cover_artifact, config, (req or {}).get("file"))
        if r.ok:
            return {"success": True, "deleted": r.deleted}
        raise HTTPException(status_code=r.code, detail=r.detail)

    @app.post("/cover/enqueue")
    async def cover_enqueue(req: dict):
        song = str((req or {}).get("song") or "")
        try:
            return await asyncio.to_thread(
                enqueue_cover_manual, config, get_my_handle, song)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except HTTPException:
            raise
        except Exception as e:
            logger.error("/cover/enqueue 异常", exc_info=True)
            raise HTTPException(status_code=500, detail=str(e))
