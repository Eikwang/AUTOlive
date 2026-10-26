"""
Web服务器模块
提供HTTP API服务器和静态文件服务器功能
"""

import asyncio
import http.server
import os
import socketserver
import threading
import traceback
from typing import Optional

from utils.my_log import logger
from utils.my_handle import My_handle
from utils.config import Config

# 尝试导入FastAPI相关模块
try:
    import uvicorn
    from fastapi import FastAPI, HTTPException
    from fastapi.middleware.cors import CORSMiddleware
    from utils.models import (
        SendMessage,
        LLMMessage,
        CallbackMessage,
        CommonResult,
    )
    HAS_FASTAPI = True
except ImportError:
    HAS_FASTAPI = False
    logger.warning("FastAPI未安装，HTTP API功能将不可用")


class WebServer:
    """Web服务器管理类"""
    
    def __init__(self, config: Config, my_handle: Optional[My_handle] = None):
        """
        初始化Web服务器

        Args:
            config: 配置对象
            my_handle: 处理器对象
        """
        self.config = config
        self.my_handle = my_handle
        self.app = None
        self.http_server = None
        self.api_thread = None
        self.web_thread = None
        self._captions_desktop_proc = None  # 桌面字幕窗口子进程句柄（存活则不重复开）
        
        if HAS_FASTAPI:
            self._init_fastapi_app()
    
    def _init_fastapi_app(self):
        """初始化FastAPI应用"""
        self.app = FastAPI(title="内部HTTP API", version="1.0.0")

        # 允许跨域
        self.app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

        # 注册路由
        self._register_routes()

        # 内置字幕打印机：socket.io 挂载 + /captions 页（整合计划 §5.2，eng E-7：与
        # 既有 /send 同暴露面，无鉴权为已接受风险并文档注明）
        self._setup_web_captions()

        logger.info("FastAPI应用初始化完成")

    def _captions_desktop_window_enabled(self):
        """桌面字幕窗口自动打开门控：enable=true 且 desktop_window≠False（缺省开启）"""
        try:
            if not self.config.get("web_captions_printer", "enable"):
                return False
            return self.config.get("web_captions_printer", "desktop_window") is not False
        except Exception:
            return False

    def _spawn_desktop_captions_window(self):
        """打开桌面字幕窗口（子进程；已有存活实例不重复开，UI 手动打开同入口）"""
        proc = self._captions_desktop_proc
        if proc is not None and proc.poll() is None:
            return {"code": 200, "message": "桌面字幕窗口已打开"}
        script = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "utils", "web_captions", "desktop_caption_window.py")
        if not os.path.isfile(script):
            return {"code": -1, "message": "桌面字幕窗口脚本不存在"}
        try:
            import subprocess
            import sys
            kwargs = {"cwd": os.path.dirname(os.path.dirname(os.path.dirname(script))),
                      "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
            if os.name == "nt":
                kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
            self._captions_desktop_proc = subprocess.Popen([sys.executable, script], **kwargs)
            logger.info(f"桌面字幕窗口已启动（pid={self._captions_desktop_proc.pid}）")
            return {"code": 200, "message": "桌面字幕窗口启动成功"}
        except Exception as e:
            logger.error(f"启动桌面字幕窗口失败：{e}\n" + traceback.format_exc())
            return {"code": -1, "message": f"启动桌面窗口失败: {e}"}

    def _setup_web_captions(self):
        """挂载字幕页路由、socket.io 与 FastAPI startup 的 loop 捕获（D1/E-5/E-6）"""
        try:
            import socketio
            import asyncio
            from fastapi import Request
            from fastapi.responses import FileResponse

            captions_root = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "frontend", "web_captions")

            class _MountTolerantSocketIOASGI(socketio.ASGIApp):
                """兼容新旧 Starlette Mount 语义的 socket.io ASGI 子应用。

                runtime312 实测（starlette 1.3.1 + engineio 4.13.5）：挂载后子应用
                收到的 scope['path'] 是全路径 /captions_ws/socket.io/（root_path=
                /captions_ws），而 engineio 判定 startswith('/socket.io/') 恒不成立
                → 握手 404，网页 OBS 源与桌面窗口全部连接失败（字幕打印从未生效的
                服务端根因，2026-10-25 诊断）。
                新语义：engineio_path 直接按全路径判定；
                旧语义（子应用收到剥离前缀的 /socket.io/）：入口回写全路径再交原判定。
                """
                def __init__(self, sio, mount_prefix="/captions_ws"):
                    super().__init__(sio, socketio_path=f"{mount_prefix}/socket.io")
                    self._mount = mount_prefix
                    self._stripped = "/socket.io"
                    self._full = f"{mount_prefix}/socket.io"

                def _normalize_scope_path(self, scope):
                    """旧 Starlette（剥离前缀）→ 回写全路径；新 Starlette 原样放行"""
                    if scope.get("type") in ("http", "websocket"):
                        path = scope.get("path") or ""
                        if path.startswith(self._stripped) and not path.startswith(self._full):
                            return dict(scope, path=f"{self._mount}{path}")
                    return scope

                async def __call__(self, scope, receive, send):
                    await super().__call__(self._normalize_scope_path(scope),
                                           receive, send)

            # 独立 socket.io ASGI 挂载（path /captions_ws/socket.io）；
            # 不复用框架内部 sio，防版本升级耦合
            sio = socketio.AsyncServer(async_mode="asgi", cors_allowed_origins="*")
            self.app.mount("/captions_ws", _MountTolerantSocketIOASGI(sio, "/captions_ws"))

            @self.app.get("/captions")
            async def captions_page():
                """字幕显示页（OBS 浏览器源粘贴此地址）"""
                return FileResponse(os.path.join(captions_root, "index.html"))

            @self.app.get("/captions/{fname:path}")
            async def captions_static(fname: str):
                """字幕页静态资源（js/css/socket.io 客户端；路径穿越防护）"""
                safe = os.path.normpath(fname).lstrip("\\/").replace("..", "")
                target = os.path.join(captions_root, safe)
                if not os.path.isfile(target):
                    raise HTTPException(status_code=404, detail="not found")
                return FileResponse(target)

            @self.app.get("/builtin_status")
            async def builtin_status():
                """内置播放器状态+字幕健康（控制区 1s 轮询数据源，dx D5/D6/D9）"""
                from utils.audio.builtin_play_center import get_builtin_player
                from utils.web_captions import get_captions_manager
                player = get_builtin_player()
                manager = get_captions_manager()
                return {
                    "builtin": player is not None,
                    "status": player.get_status() if player is not None else None,
                    "captions_failures": manager.submitter.consecutive_failures if manager is not None else 0,
                }

            @self.app.post("/builtin_control")
            async def builtin_control(request: Request):
                """控制区四按钮 + 设备枚举（dx D5；跨进程经内部 API 调用）"""
                from utils.audio.builtin_play_center import get_builtin_player
                player = get_builtin_player()
                if player is None:
                    return {"code": -1, "message": "内置播放器未启用"}
                try:
                    body = await request.json()
                    action = body.get("action", "")
                    if action == "pause":
                        player.pause_stream()
                    elif action == "resume":
                        player.resume_stream()
                    elif action == "skip":
                        player.skip_current_stream()
                    elif action == "clear":
                        player.clear()
                    elif action == "list_devices":
                        return {"code": 200, "devices": player.get_all_audio_device_info()}
                    else:
                        return {"code": -1, "message": f"未知操作：{action}"}
                    return {"code": 200, "message": "成功"}
                except Exception as e:
                    logger.error(f"builtin_control 处理失败: {e}")
                    return {"code": -1, "message": f"控制失败：{e}"}

            @self.app.post("/captions_desktop_window/open")
            async def captions_desktop_window_open():
                """打开桌面字幕窗口（字幕打印页按钮入口；幂等——已开不重复）"""
                return self._spawn_desktop_captions_window()

            # FastAPI startup：捕获 uvicorn event loop（eng E-5 提交器用）并桥接字幕管理器
            @self.app.on_event("startup")
            async def _captions_startup():
                from utils.web_captions import register_loop, register_sio, get_captions_manager
                register_loop(asyncio.get_running_loop())
                register_sio(sio)
                manager = get_captions_manager()
                if manager is not None:
                    manager.attach(sio)
                    manager.set_loop(asyncio.get_running_loop())
                logger.info("字幕 socket.io 挂载完成（/captions_ws + startup loop 捕获）")
                # 桌面字幕窗口自动打开（enable && desktop_window；听障用户开箱即用）
                if self._captions_desktop_window_enabled():
                    result = self._spawn_desktop_captions_window()
                    logger.info(f"桌面字幕窗口自动打开：{result.get('message')}")
        except Exception:
            logger.error("字幕模块挂载失败（不影响其余功能）\n" + traceback.format_exc())

    
    def _register_routes(self):
        """注册API路由"""
        if not self.app:
            return

        # DanmakuListener 控制面（整合计划 P1-3：前端 webui → 本进程唯一通道；
        # 挂接失败不阻断主 API——danmaku 模块异常隔离）
        try:
            from utils.danmaku_api import register_danmaku_routes
            register_danmaku_routes(self.app, self.config)
        except Exception:
            logger.error("DanmakuListener 控制面路由挂载失败（不影响其他 API）", exc_info=True)

        # GPT-SoVITS 进程内引擎控制面（深化整合 F-2/R-5：试听+状态+重载；
        # 前端进程无引擎，全部经本通道）
        try:
            from utils.gpt_sovits_inproc import register_tts_routes
            register_tts_routes(self.app, self.config)
        except Exception:
            logger.error("TTS 控制面路由挂载失败（不影响其他 API）", exc_info=True)

        # 动作编排控制面（/autoplan 20261021 批次 P2：管理页后端；
        # /choreography/status 状态条轮询 + /choreography/rules 热应用+持久化）
        try:
            from utils.orchestration.api import register_choreography_routes
            register_choreography_routes(self.app, self.config)
        except Exception:
            logger.error("choreography 控制面路由挂载失败（管理页显示未装配）", exc_info=True)

        # 数字人素材管理控制面（动作编排二级页：素材增删+预处理+热上线）
        try:
            from utils.edtalk_realtime.materials import register_material_routes
            register_material_routes(self.app, self.config)
        except Exception:
            logger.error("素材管理路由挂载失败（动作编排页素材区不可用）", exc_info=True)

        # 翻唱队列控制面（/autoplan 2026-10-14 C-3：点歌页队列状态卡数据源；
        # 写者矩阵=cover_worker（main 进程）唯一写者，webui 只读窄端点）
        @self.app.get("/cover/status")
        async def cover_status():
            try:
                cq = getattr(self.my_handle, "cover_queue", None) if self.my_handle else None
                if cq is None:
                    return {"available": False, "reason": "翻唱队列未初始化（未触发过点歌）"}
                with cq.lock:
                    queue = [{"song": j["song"], "viewer": j["viewer"],
                              "retries_left": j["retries_left"]} for j in cq.queue]
                    cur = dict(cq.current) if cq.current else None
                    if cur and cur.get("started_at"):
                        cur = {**cur, "elapsed_s": round(__import__("time").time() - cur["started_at"], 1)}
                return {"available": True, "queue": queue, "current": cur,
                        "policies": cq._policies()}
            except Exception as e:
                logger.error("/cover/status 异常", exc_info=True)
                return {"available": False, "reason": str(e)}
            
        @self.app.post("/send")
        async def send(msg: SendMessage):
            """处理发送消息请求"""
            try:
                tmp_json = msg.dict()
                logger.info(f"内部HTTP API send接口收到数据：{tmp_json}")
                data_json = tmp_json["data"]
                if "type" not in data_json:
                    data_json["type"] = tmp_json["type"]
                
                if self.my_handle is None:
                    raise HTTPException(status_code=500, detail="处理器未初始化")
                
                if data_json["type"] in ["reread", "reread_top_priority"]:
                    self.my_handle.reread_handle(data_json, type=data_json["type"])
                elif data_json["type"] == "comment":
                    # UI-16 二轮验收（任务 14 语义修正）：聊天框发送来源标记——
                    # 聊天记录只显示聊天功能互动（chat_source=voice/chatbox），
                    # 弹幕监听链无此标记不进聊天记录
                    data_json["chat_source"] = "chatbox"
                    self.my_handle.process_data(data_json, "comment")
                elif data_json["type"] == "tuning":
                    self.my_handle.tuning_handle(data_json)
                elif data_json["type"] == "gift":
                    self.my_handle.gift_handle(data_json)
                elif data_json["type"] == "entrance":
                    self.my_handle.entrance_handle(data_json)
                
                return CommonResult(code=200, message="成功")
            except Exception as e:
                logger.error(f"发送数据失败！{e}")
                return CommonResult(code=-1, message=f"发送数据失败！{e}")
        
        @self.app.post("/llm")
        async def llm(msg: LLMMessage):
            """处理LLM请求"""
            try:
                logger.info(f"内部HTTP API llm接口收到数据：{msg.dict()}")
                # 这里可以添加LLM处理逻辑
                return CommonResult(code=200, message="成功")
            except Exception as e:
                logger.error(f"LLM请求失败！{e}")
                return CommonResult(code=-1, message=f"LLM请求失败！{e}")
        
        @self.app.post("/callback")
        async def callback(msg: CallbackMessage):
            """处理回调请求"""
            try:
                logger.info(f"内部HTTP API callback接口收到数据：{msg.dict()}")
                # 这里可以添加回调处理逻辑
                return CommonResult(code=200, message="成功")
            except Exception as e:
                logger.error(f"回调请求失败！{e}")
                return CommonResult(code=-1, message=f"回调请求失败！{e}")

        @self.app.post("/tts/synthesize")
        async def tts_synthesize(msg: dict = None):
            """合成测试端点（切片3：webui 轻量进程无合成能力，经此跨进程调用
            main 进程的 audio_processor 完成合成，返回音频路径）。

            body: {"content": "...", "audio_synthesis_type": "gpt_sovits|edge_tts"}
            """
            try:
                body = msg or {}
                content = body.get("content", "")
                stype = body.get("audio_synthesis_type", "gpt_sovits")
                if not content:
                    return {"code": -1, "message": "content 为空"}
                audio = getattr(self.my_handle, "audio_processor", None) or getattr(self.my_handle, "audio", None)
                if audio is None:
                    return {"code": -1, "message": "音频处理实例未初始化"}
                file_path = await audio.audio_synthesis_use_local_config(content, stype)
                if not file_path:
                    return {"code": -1, "message": "合成失败，请查看 main 日志"}
                return {"code": 200, "file_path": file_path}
            except Exception as e:
                logger.error(f"/tts/synthesize 失败: {e}")
                return {"code": -1, "message": f"合成失败！{e}"}

        @self.app.get("/edtalk_status")
        async def edtalk_status():
            """EDTalk 推送健康度快照（EDTalk功能集成计划 §4.3.1）。

            webui 画面设置页轮询此端点渲染推送健康度可见面
            （正常 / 未启动 / 最近 N 次失败）。推送发生在本进程
            （main.py 运行时），webui 经既有代理链路读取。
            """
            try:
                from utils.edtalk_realtime.edtalk_client import get_registered_client
                client = get_registered_client()
                if client is None:
                    return {"code": 200, "data": {"available": False, "reason": "音频模块未初始化"}}
                return {"code": 200, "data": {"available": True, **client.health_snapshot()}}
            except Exception as e:
                return {"code": -1, "message": f"获取 EDTalk 状态失败：{e}"}

        logger.info("API路由注册完成")
    
    def start_api_server(self, host: str = "0.0.0.0", port: int = 8000):
        """
        启动HTTP API服务器
        
        Args:
            host: 监听地址
            port: 监听端口
        """
        if not HAS_FASTAPI:
            logger.error("FastAPI未安装，无法启动API服务器")
            return
        
        def run_server():
            try:
                logger.info(f"HTTP API服务器启动中... 地址: {host}:{port}")
                uvicorn.run(self.app, host=host, port=port, log_level="info")
            except Exception as e:
                logger.error(f"HTTP API服务器启动失败: {e}")
        
        self.api_thread = threading.Thread(target=run_server, daemon=True)
        self.api_thread.start()
        logger.info(f"HTTP API服务器线程已启动，端口: {port}")
    
    def start_web_server(self, port: int):
        """
        启动静态文件Web服务器
        
        Args:
            port: 监听端口
        """
        def run_server():
            try:
                Handler = http.server.SimpleHTTPRequestHandler
                with socketserver.TCPServer(("", port), Handler) as httpd:
                    logger.info(f"Web运行在端口：{port}")
                    logger.info(
                        f"可以直接访问Live2D页， http://127.0.0.1:{port}/Live2D/"
                    )
                    httpd.serve_forever()
            except Exception as e:
                logger.error(f"Web服务器启动失败: {e}")
        
        self.web_thread = threading.Thread(target=run_server, daemon=True)
        self.web_thread.start()
        logger.info(f"Web服务器线程已启动，端口: {port}")
    
    def start_all(self, api_port: int = 8000, web_port: int = 8080):
        """
        启动所有服务器
        
        Args:
            api_port: API服务器端口
            web_port: Web服务器端口
        """
        self.start_api_server(port=api_port)
        self.start_web_server(port=web_port)
    
    def stop_all(self):
        """停止所有服务器"""
        # 注意：由于使用了daemon线程，主线程结束时会自动停止
        logger.info("Web服务器停止中...")
        # 这里可以添加清理代码
        logger.info("Web服务器已停止")


# 创建全局实例（可选）
_web_server_instance = None


def get_web_server(config: Config = None, my_handle: My_handle = None) -> WebServer:
    """
    获取Web服务器单例
    
    Args:
        config: 配置对象（首次调用时需要）
        my_handle: 处理器对象（首次调用时需要）
    
    Returns:
        WebServer实例
    """
    global _web_server_instance
    if _web_server_instance is None:
        if config is None:
            raise ValueError("首次调用需要提供config参数")
        _web_server_instance = WebServer(config, my_handle)
    return _web_server_instance


def start_web_servers(config: Config, my_handle: My_handle = None, 
                     api_port: int = 8000, web_port: int = 8080):
    """
    启动Web服务器（便捷函数）
    
    Args:
        config: 配置对象
        my_handle: 处理器对象
        api_port: API服务器端口
        web_port: Web服务器端口
    """
    web_server = get_web_server(config, my_handle)
    web_server.start_all(api_port, web_port)
    return web_server
