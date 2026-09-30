"""本机 WebSocket 推送通道

契约 v1 阶段 0：把统一消息（线格式 JSON）推送给已连接的消费者（AUTOlive）。
- 默认绑定 127.0.0.1（本机边界由 bind 地址保证）
- 预共享 token 鉴权（常量时间比较，Eng S-2）
- 广播语义：无 ack（重连不重放，缺口由 GAP 消息报知——契约投递语义）
"""

import asyncio
import hmac
import json
from pathlib import Path
from typing import Optional, Set

from loguru import logger

try:
    from aiohttp import WSMsgType, web
except ImportError as e:  # pragma: no cover
    raise RuntimeError("ws 推送通道需要 aiohttp；pip install aiohttp") from e


def load_token(token_file: Optional[str], env_token: Optional[str]) -> Optional[str]:
    """加载预共享 token：环境变量优先，其次受限权限文件（DX-C）"""
    if env_token:
        return env_token
    if token_file:
        p = Path(token_file)
        if p.exists():
            return p.read_text(encoding="utf-8").strip()
    return None


def constant_time_equal(a: str, b: str) -> bool:
    """常量时间字符串比较（防时序侧信道，Eng S-2）"""
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


class PushServer:
    """本机 WS 推送服务器（aiohttp）"""

    def __init__(self, host: str = "127.0.0.1", port: int = 8765, token: Optional[str] = None):
        self.host = host
        self.port = port
        self._token = token
        self._clients: Set = set()
        self._app = web.Application()
        self._app.router.add_get("/ws", self._handler)
        self._runner: Optional[web.AppRunner] = None
        self._site: Optional[web.TCPSite] = None

    async def start(self) -> None:
        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, self.host, self.port)
        await self._site.start()
        logger.info(f"PushServer listening on ws://{self.host}:{self.port}/ws")

    async def stop(self) -> None:
        for ws in list(self._clients):
            await ws.close()
        self._clients.clear()
        if self._runner:
            await self._runner.cleanup()

    async def ws_handler(self, request):
        """公开的 WS 连接处理器（供外部 aiohttp app 挂载，单源客户端管理）"""
        return await self._handler(request)

    async def broadcast(self, wire: dict) -> None:
        """向全部已连接客户端广播一条统一消息（线格式）"""
        if not self._clients:
            return
        text = json.dumps(wire, ensure_ascii=False)
        dead = []
        for ws in self._clients:
            try:
                await ws.send_str(text)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self._clients.discard(ws)

    async def _handler(self, request) -> web.WebSocketResponse:
        # token 鉴权：未配置 token 时仅依赖 127.0.0.1 边界并在日志中提示
        if self._token:
            provided = request.headers.get("Authorization", "")
            if provided.startswith("Bearer "):
                provided = provided[len("Bearer "):]
            if not provided or not constant_time_equal(provided, self._token):
                await request.transport.abort() if request.transport else None
                raise web.HTTPUnauthorized(text="invalid or missing token")
        ws = web.WebSocketResponse(heartbeat=30)
        await ws.prepare(request)
        self._clients.add(ws)
        logger.info(f"consumer connected ({len(self._clients)} online)")
        try:
            async for _ in ws:
                # v1：消费端→服务端的消息（如控制命令）未定义；读到即忽略
                pass
        finally:
            self._clients.discard(ws)
            logger.info(f"consumer disconnected ({len(self._clients)} online)")
        return ws


async def _selftest() -> None:  # pragma: no cover
    """手动冒烟：python -m danmaku_listener.push.ws_server"""
    server = PushServer(port=18765, token="demo-token")
    await server.start()

    async def feeder():
        for i in range(3):
            await server.broadcast({"seq": i, "type": "DANMU", "hello": True})
            await asyncio.sleep(0.2)
        await server.stop()

    import websockets

    async def consumer():
        await asyncio.sleep(0.3)
        async with websockets.connect(
            "ws://127.0.0.1:18765/ws", additional_headers={"Authorization": "Bearer demo-token"}
        ) as ws:
            for _ in range(3):
                msg = await ws.recv()
                print(msg)

    await asyncio.gather(feeder(), consumer())


if __name__ == "__main__":
    asyncio.run(_selftest())
