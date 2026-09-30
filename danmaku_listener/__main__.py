"""danmaku-listen / danmaku-serve 命令行入口（契约 v1 阶段 0 DX 交付束 A）

用法（等价于 pip install -e 后的 ``danmaku-listen`` / ``danmaku-serve`` 命令）：

    python -m danmaku_listener listen <platform:room_id>   # 演示：单平台单房间，
                                                           # 标准化 JSON 打印 stdout，
                                                           # TTHW 计时输出 stderr
    python -m danmaku_listener serve [--config cfg.toml]   # 常驻服务（阶段 0 骨架）
    python -m danmaku_listener contract                    # 打印契约版本信息

TTHW 口径：进程启动 → 首条统一消息输出（毫秒），打印到 stderr（不污染 stdout 的
JSON 流）。单平台验收以此解耦 AUTOlive 可用性（DX 回归项：每引擎接入时本命令
必须保持可用）。
"""

import argparse
import asyncio
import json
import os
import sys
import time

from loguru import logger

from danmaku_listener.contract import CONTRACT_VERSION

_T0 = time.perf_counter()

# stdout 只承载标准化 JSON 流；日志一律走 stderr（DX：管线友好）
logger.remove()
logger.add(sys.stderr, level="INFO")


def _tthw_print(first: bool) -> None:
    """TTHW 计时输出（stderr）"""
    if first:
        elapsed_ms = (time.perf_counter() - _T0) * 1000
        print(f"[tthw] first message after {elapsed_ms:.0f} ms", file=sys.stderr)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="danmaku-listener", description="六平台弹幕监听")
    sub = parser.add_subparsers(dest="command", required=True)

    listen = sub.add_parser("listen", help="演示模式：监听单房间，标准化 JSON 打印 stdout")
    listen.add_argument("target", nargs="?", default=None, help="平台:房间ID，如 bilibili:23058（离线回放时省略）")
    listen.add_argument("--replay", default=None, help="离线回放 fixtures JSONL（无需真实直播间）")
    listen.add_argument("--speed", type=float, default=1.0, help="回放速度倍率")
    listen.add_argument("--timeout", type=float, default=60.0, help="等待首条消息的超时秒数")

    serve = sub.add_parser("serve", help="常驻服务：WS 推送通道 + 消息源")
    serve.add_argument("--config", default=None, help="TOML 配置文件路径")
    serve.add_argument("--replay", default=None, help="回放 fixtures JSONL 作为消息源（通道冒烟）")
    serve.add_argument("--web", action="store_true", help="同时挂载测试控制台前端（web_port 8080）")

    sub.add_parser("contract", help="打印契约版本信息")
    return parser


def cmd_contract() -> int:
    print(json.dumps({
        "contract_version": CONTRACT_VERSION,
        "schema": "docs/contract/schema.json",
        "docs": ["docs/contract/schema.md", "docs/contract/mapping.md", "docs/contract/migration.md"],
    }, ensure_ascii=False, indent=2))
    return 0


async def cmd_listen_replay(path: str, speed: float, limit: int = 0) -> int:
    """离线回放：fixtures JSONL → stdout JSON 流（无需真实直播间，magical moment 载体）"""
    from danmaku_listener.fixtures.replayer import replay, validate_events

    stats = validate_events(path)
    if stats["invalid"]:
        print(
            json.dumps({
                "error": "invalid_fixtures",
                "reason": f"{stats['invalid']}/{stats['total']} 条不符合契约线格式",
                "fix": "用 scripts/export_contract.py 导出的 Schema 校验 fixtures 生成流程",
                "docs": "docs/contract/schema.md",
            }, ensure_ascii=False),
            file=sys.stderr,
        )
        return 3

    _tthw_print(True)
    first = False
    count = 0

    async def sink(wire: dict) -> None:
        nonlocal count
        count += 1
        print(json.dumps(wire, ensure_ascii=False), flush=True)

    gen = replay(path, sink, speed=speed)
    async for _ in gen:
        if limit and count >= limit:
            break
    print(f"[replay] {count} messages from {path}", file=sys.stderr)
    return 0


async def cmd_listen(target: str | None, timeout: float, replay_path: str | None = None, speed: float = 1.0) -> int:
    if replay_path:
        return await cmd_listen_replay(replay_path, speed)
    if not target:
        print(
            json.dumps({
                "error": "missing_target",
                "reason": "listen 需要 平台:房间ID 或 --replay 文件",
                "fix": "示例：python -m danmaku_listener listen bilibili:23058 或 --replay docs/contract/examples/demo.jsonl",
                "docs": "docs/integration/autolive.md",
            }, ensure_ascii=False),
            file=sys.stderr,
        )
        return 2
    if ":" not in target:
        print(
            json.dumps({
                "error": "invalid_target",
                "reason": f"目标格式须为 平台:房间ID，收到 {target!r}",
                "fix": "示例：python -m danmaku_listener listen bilibili:23058",
                "docs": "docs/integration/autolive.md",
            }, ensure_ascii=False),
            file=sys.stderr,
        )
        return 2

    first = True
    got_message = asyncio.Event()

    async def handle(data: dict) -> None:
        nonlocal first
        _tthw_print(first)
        first = False
        got_message.set()
        print(json.dumps(data, ensure_ascii=False), flush=True)

    listener = DanmakuListener()
    listener.on_danmaku(handle)

    try:
        await asyncio.wait_for(listener.start([target]), timeout=timeout)
    except asyncio.TimeoutError:
        print(
            json.dumps({
                "error": "start_timeout",
                "reason": f"引擎启动超过 {timeout}s 未完成",
                "fix": "检查网络与平台房间号；部署前置检查见 docs/ops/compliance-review.md",
                "docs": "docs/ops/compliance-review.md",
            }, ensure_ascii=False),
            file=sys.stderr,
        )
        return 3

    try:
        await asyncio.wait_for(got_message.wait(), timeout=timeout)
    except asyncio.TimeoutError:
        print(
            json.dumps({
                "error": "no_message",
                "reason": "连接建立但超时未收到弹幕（房间可能未开播）",
                "fix": "确认房间正在直播；查看 docs/platforms/ 对应平台手册",
                "docs": "docs/contract/schema.md",
            }, ensure_ascii=False),
            file=sys.stderr,
        )
        return 4

    await asyncio.sleep(1.0)
    await listener.stop()
    return 0


async def cmd_serve(config: str | None, replay: str | None = None, with_web: bool = False) -> int:
    """常驻服务：WS 推送通道 + 消息源（回放演示或引擎注册表，阶段 0 骨架）"""
    import os

    from danmaku_listener.config.settings import get_settings, load_toml_overrides
    from danmaku_listener.push.ws_server import PushServer, load_token

    # S2 默认加载（round3 L-1）：显式 --config 优先，否则 config.local.toml（缺失静默）；
    # 植入模块级覆盖实例确保 web app/bridge/引擎全部拿到新值（get_settings 优先返回）
    from danmaku_listener.config.config_store import load_overrides
    from danmaku_listener.config.settings import set_settings
    config_path = config or os.path.join(os.getcwd(), "config.local.toml")
    overrides = load_overrides(config_path) if os.path.exists(config_path) else {}
    if overrides:
        settings = get_settings()
        set_settings(settings.__class__(**{**{f: getattr(settings, f) for f in settings.__class__.model_fields}, **overrides}))
    settings = get_settings()

    token = load_token(settings.ws_token_file, os.environ.get("DANMAKU_TOKEN"))
    server = PushServer(host=settings.ws_bind, port=settings.ws_port, token=token)
    await server.start()
    config_source = config_path

    if with_web:
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from danmaku_listener.web.app import create_app as _create_web_app
        from aiohttp import web as _web
        webapp = _create_web_app()
        web_runner = _web.AppRunner(webapp)
        await web_runner.setup()
        web_site = _web.TCPSite(web_runner, "localhost", settings.web_port)
        await web_site.start()
        print(json.dumps({"web_console": f"http://localhost:{settings.web_port}"}), flush=True)
    print(
        json.dumps({
            "service": "danmaku-serve",
            "ws": f"ws://{settings.ws_bind}:{settings.ws_port}/ws",
            "auth": "token" if token else "loopback-only (未配置 token)",
            "contract_version": CONTRACT_VERSION,
            "engines_online": [],
        }, ensure_ascii=False),
        flush=True,
    )

    try:
        if replay:
            from danmaku_listener.fixtures.replayer import replay as _replay_src  # 别名避免遮蔽路径参数

            async def sink(wire: dict) -> None:
                await server.broadcast(wire)

            async for _ in _replay_src(replay, sink, speed=5.0, loop=True):
                pass
        else:
            # 引擎注册表随阶段 1-5 接入；阶段 0 服务保持空转（DX 回归项已固定 CLI 面）
            print(
                "serve: no message source yet — engines land with phases 1-5 (use --replay to smoke-test the channel)",
                file=sys.stderr,
            )
            await asyncio.Event().wait()
    finally:
        await server.stop()
    return 0


def main() -> int:
    # exe 名即子命令：danmaku-serve.exe → 自动注入 "serve"（分发可用性，S3）
    argv = sys.argv[1:]
    exe_name = os.path.basename(sys.argv[0] or "").lower()
    if exe_name.startswith("danmaku-serve") and (not argv or argv[0] not in ("listen", "serve", "contract")):
        argv = ["serve"] + argv
    elif exe_name.startswith("danmaku-listen") and (not argv or argv[0] not in ("listen", "serve", "contract")):
        argv = ["listen"] + argv

    args = _build_parser().parse_args(argv)
    if args.command == "contract":
        return cmd_contract()
    if args.command == "listen":
        return asyncio.run(cmd_listen(args.target, args.timeout, args.replay, args.speed))
    if args.command == "serve":
        return asyncio.run(cmd_serve(args.config, getattr(args, "replay", None), getattr(args, "web", False)))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
