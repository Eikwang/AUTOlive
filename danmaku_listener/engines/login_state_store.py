"""登录态快照存取（F1——2026-10-10 xhs/huya profile 重测共享实现）

背景（cards/xhs-persist-*、cards/huya-bg-*）：平台登录 cookie 为持久型
（xhs id_token/web_session 1 年期、huya yyuid 至 2026-10-24）——"会话级
cookie"旧结论（xiaohongshu 方案 A 注释 / resident_session.login_takeover
注释）均系误判。历史登录全丢真因：登录 cookie 只在存活浏览器的内存提交
窗口——进程被硬杀（看门狗/控制台关闭，本环境常态）未提交即丢，且发送侧
profile 登录从未真正落盘（xhs profile 诞生晚于登录、huya profile 目录从未
存在）。

机制：登录 cookie 出现瞬间快照 storage_state 原子落盘（tmp→replace）；
启动时 profile 丢登录则从快照注入（add_cookies）——硬杀/覆写/清档全免疫。
恢复已被服务端作废的会话：页面按游客降级，误恢复无害（cookie 判定仅作
首登检测——login_gate 判定边界）。

消费方：ControlledPageEngine（五受控页面平台）与 ResidentSendSession
（douyin/huya 常驻会话）——两级同语义，避免副本漂移。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from loguru import logger

from danmaku_listener.engines.login_gate import has_login_cookie


async def snapshot_login_state(context, *, names: Iterable[str], path: str,
                               label: str) -> bool:
    """登录 cookie 出现瞬间快照 storage_state 落盘（F1：硬杀免疫）"""
    try:
        cookies = await context.cookies()
        if not has_login_cookie(cookies, names):
            return False
        state = await context.storage_state()
    except Exception as e:  # noqa: BLE001  快照失败不阻断调用方流程
        logger.debug(f"[{label}] login snapshot failed: {type(e).__name__}")
        return False
    p = Path(path)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    tmp.replace(p)
    logger.info(f"[{label}] 登录态快照落盘: {p.name} "
                f"({len(state.get('cookies', []))} cookies)")
    return True


async def restore_login_state(context, *, names: Iterable[str], path: str,
                              label: str) -> bool:
    """profile 丢登录（硬杀未提交/游客覆写/清档/从未落盘）→ 从快照恢复"""
    try:
        cookies = await context.cookies()
        if has_login_cookie(cookies, names):
            return False  # profile 自身登录健在——无需恢复
    except Exception:  # noqa: BLE001
        return False
    try:
        state = json.loads(Path(path).read_text(encoding="utf-8"))
        snap = state.get("cookies") or []
    except Exception:  # noqa: BLE001  快照缺失/损坏按无登录处理
        return False
    if not has_login_cookie(snap, names):
        return False
    try:
        await context.add_cookies(snap)
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[{label}] login restore failed: {type(e).__name__}")
        return False
    logger.info(f"[{label}] 登录态快照恢复: {len(snap)} cookies"
                "（profile 丢登录——硬杀/覆写/未落盘后自愈）")
    return True
