"""发送管线装配（AutoDanmu T5/T6——进程级单例，ws/REST 双通道共用）

装配内容：
- SenderRegistry：E5 引擎中介 sender（受控页面五平台）+ E4 直连 sender（bilibili）
- SendGuard：settings [send] 六件套配置 + bridge blocked_keywords 复用
- SendAuditLog：审计文件+幂等索引（R6/F9）
- F2 监听房间判定：bridge._rooms 快照
- F6 鉴权：token 未配置=拒绝服务（AUTH_UNCONFIGURED）
"""
from __future__ import annotations

import os
from typing import Any, Optional

from loguru import logger

from danmaku_listener.senders.audit import SendAuditLog
from danmaku_listener.senders.bilibili import BilibiliSender
from danmaku_listener.senders.guard import SendGuard
from danmaku_listener.senders.pipeline import DanmuCommandPipeline
from danmaku_listener.senders.registry import EngineHookSender, SenderRegistry

_pipeline: Optional[DanmuCommandPipeline] = None
_broadcaster: Optional[Any] = None


def load_auth_token() -> Optional[str]:
    """与 push.ws_server.load_token 同源（env 优先，其次 token 文件）"""
    from danmaku_listener.push.ws_server import load_token
    from danmaku_listener.config.settings import get_settings

    settings = get_settings()
    return load_token(settings.ws_token_file, os.environ.get("DANMAKU_TOKEN"))


def _pick_bilibili_cookie_file(settings) -> Optional[str]:
    """B站 cookie 文件选择（E4 三格式解析器兼容任一）"""
    candidates = [settings.bilibili_cookie_file, "./cookie/bilibili_storage_state.json"]
    for c in candidates:
        if c and os.path.exists(c):
            return c
    return candidates[-1]  # 缺省路径（未登录时 sender 回执 PLATFORM_REJECTED）


def get_send_pipeline() -> DanmuCommandPipeline:
    """进程级管线单例（懒构造；web bridge 与 push 通道共用，F1）"""
    global _pipeline
    if _pipeline is not None:
        return _pipeline
    from danmaku_listener.config.settings import get_settings
    from danmaku_listener.web.app import get_bridge

    settings = get_settings()
    bridge = get_bridge()

    def room_listened(platform: str, room_id: str) -> bool:
        """F2 监听房间快照判定（bridge._rooms；拒绝即可）"""
        info = bridge._rooms.get(f"{platform}:{room_id}")
        return bool(info and info.get("status") == "running")

    registry = SenderRegistry(room_checker=room_listened)
    # E5：受控页面平台——sender=引擎实例 send 钩子适配器（禁止另开同 profile context）
    # taobao 移出 E5 循环（T2 裁定）：TaobaoMtopSender（mtop page-eval）替换 DOM 钩子——
    # T1 判定（cards/taobao-mtop-pageeval-20261007）：纯 HTTP 重放被 RGV587 拒，
    # 页面 mtop 库调用是唯一可行 API 形态；taobao.py DOM 钩子 deprecated 保留作 T4 参照
    for plat in ("1688", "xiaohongshu", "jd", "wechat_channels"):
        try:
            engine = bridge._get_or_build_engine(plat)
            registry.register(EngineHookSender(plat, engine,
                                               lambda rid, _p=plat: room_listened(_p, rid)))
        except Exception as e:  # noqa: BLE001  引擎构造失败不阻塞其余 sender
            logger.warning(f"[send-wiring] engine build failed for {plat}: {e}")
    # 淘宝 mtop page-eval sender（T2——E5 借引擎 _profile_lock；页面 JS 现生成 bx-ua）
    try:
        taobao_engine = bridge._get_or_build_engine("taobao")
        from danmaku_listener.senders.taobao_mtop import TaobaoMtopSender
        registry.register(TaobaoMtopSender(taobao_engine))
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[send-wiring] taobao mtop sender register failed: {e}")
    # 三平台接线（T6/X1）：快手 storage_state 注入/斗鱼 cookie 注入（瞬态基类）+
    # 虎牙 ResidentSendSession 第二实例（headed minimized；35s 覆写由 guard 内置默认兜底）
    try:
        from danmaku_listener.senders.huya import HuyaResidentSender
        from danmaku_listener.senders.kuaishou_douyu import (DouyuCookieSender,
                                                             KuaishouStateSender)
        registry.register(KuaishouStateSender())
        registry.register(DouyuCookieSender())
        registry.register(HuyaResidentSender(settings=settings))
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[send-wiring] 3-platform senders register failed: {e}")
    # E4：bilibili 直连 sender（复用引擎 cookie 解析器）
    registry.register(BilibiliSender(cookie_file=_pick_bilibili_cookie_file(settings)))
    # 抖音常驻会话 sender（T3——headless_new 形态；DX-D9 三件套配置；旧瞬态路径 deprecated 保留）
    try:
        from danmaku_listener.senders.douyin import DouyinProfileSender
        registry.register(DouyinProfileSender(settings=settings))
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[send-wiring] douyin sender register failed: {e}")

    guard = SendGuard(settings, blocked_keywords=list(bridge._blocked_keywords))
    audit = SendAuditLog(settings.send_audit_file, settings.send_idempotency_index)
    token = load_auth_token()
    _pipeline = DanmuCommandPipeline(
        registry=registry, guard=guard, audit=audit,
        auth_configured=token is not None,  # F6
    )
    if _broadcaster is not None:
        _pipeline.set_broadcaster(_broadcaster)
    logger.info(f"[send-wiring] pipeline ready: platforms={registry.platforms()} "
                f"enabled={settings.send_enabled_platforms or '(none)'} dry_run={settings.send_dry_run} "
                f"auth={'token' if token else 'UNCONFIGURED（命令拒绝服务——F6）'}")
    return _pipeline


def set_result_broadcaster(fn) -> None:
    """注入回执广播器（AUTOlive 8765 通道；serve 启动时调用）"""
    global _broadcaster
    _broadcaster = fn
    if _pipeline is not None:
        _pipeline.set_broadcaster(fn)
