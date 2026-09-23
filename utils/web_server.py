"""
Web服务器模块
提供HTTP API服务器和静态文件服务器功能
"""

import asyncio
import http.server
import socketserver
import threading
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
        
        logger.info("FastAPI应用初始化完成")
    
    def _register_routes(self):
        """注册API路由"""
        if not self.app:
            return
            
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
