"""手动重新登录流程（2026-10-10 用户需求——发送按钮旁"登录"按钮）

独立逻辑：清平台登录载体（profile 目录/快照/cookie 文件）→ 重新 start_room，
由既有登录门接管（弹可见窗→登录→保存→自动恢复监听）。不复用/不改动登录门
与预算机制——本模块只做"清载体 + 触发既有闭环"，用途=登录状态异常时用户
自行清理重登。

平台载体清单（相对 cookie/ 目录；三态）：
- profile 型（taobao/1688/xhs/jd/wxsp/pdd/huya-send/douyin-send）：清 profile
  目录 + *_login_state.json 快照，登录态随新 profile 落盘
- 文件型（bilibili/kuaishou/douyu）：清 cookie 文件，既有登录门重写同路径
- huya/douyin 双载体：监听载体 + 发送 profile 都清——重登将先后弹两个窗口
"""
from __future__ import annotations

import shutil
from pathlib import Path

from loguru import logger

#: 平台登录载体清单（相对 cookie/ 目录；values 顺序=清理顺序）
PLATFORM_LOGIN_CARRIERS: dict[str, list[str]] = {
    "taobao": ["taobao_profile", "taobao_profile_login_state.json"],
    "1688": ["1688_profile", "1688_profile_login_state.json"],
    "xiaohongshu": ["xhs_profile", "xhs_profile_login_state.json"],
    "jd": ["jd_profile", "jd_profile_login_state.json"],
    "huya": ["huya_login_profile", "huya_login_profile_login_state.json",
             "huya_login_cookies.json"],
    "douyin": ["douyin_profile", "douyin_cookies.json"],
    "bilibili": ["bilibili_storage_state.json", "bilibili_cookies.txt"],
    "kuaishou": ["kuaishou_storage_state.json"],
    "douyu": ["douyu_login_cookies.json"],
    "wechat_channels": ["wxsp_profile", "wxsp_profile_login_state.json"],
    "pdd": ["pdd_profile", "pdd_profile_login_state.json"],
}

#: 平台重登提示（多窗口平台——用户预期管理）
RELOGIN_NOTES = {
    "huya": "虎牙将先后弹出监听登录与发送登录两个窗口",
    "douyin": "抖音将先后弹出监听登录与发送登录两个窗口",
}


class ReloginUnsupported(Exception):
    """平台无登录载体定义（不支持手动重登）"""


def clear_login_state(platform: str, cookie_dir: str = "./cookie") -> list[str]:
    """清平台登录载体；返回已删除的相对路径列表

    安全边界：仅允许 cookie/ 目录内的相对路径（防路径逃逸——载体清单是
    代码常量，此处仍按目录逃逸防护兜底）。
    """
    carriers = PLATFORM_LOGIN_CARRIERS.get(platform)
    if not carriers:
        raise ReloginUnsupported(f"平台 {platform} 不支持手动重登（无登录载体定义）")
    base = Path(cookie_dir).resolve()
    deleted: list[str] = []
    for name in carriers:
        target = (base / name).resolve()
        if target != base and base not in target.parents:
            logger.warning(f"[relogin] {platform} 载体路径逃逸，跳过: {name}")
            continue
        if target.is_dir():
            shutil.rmtree(target, ignore_errors=True)
            deleted.append(name)
        elif target.is_file():
            target.unlink(missing_ok=True)
            deleted.append(name)
    return deleted


async def run_relogin_flow(bridge, platform: str, room_id: str,
                           cookie_dir: str = "./cookie") -> dict:
    """清登录载体 → 重新 start_room（既有登录门自动弹窗收口）

    停该平台全部在听房间（profile 单实例独占——清理/重登期间必须释放）；
    清理后 start_room 触发对应登录门（协议平台=登录-then-启动闭环、受控
    平台=NeedLoginVisible 可见窗），登录完成自动恢复监听——全部复用既有
    验收过的登录逻辑，本模块不新增窗口/轮询代码。
    """
    if platform not in PLATFORM_LOGIN_CARRIERS:
        raise ReloginUnsupported(f"平台 {platform} 不支持手动重登（无登录载体定义）")

    # 停该平台全部在听房间
    stopped: list[str] = []
    for key, info in list(getattr(bridge, "_rooms", {}).items()):
        if not key.startswith(f"{platform}:"):
            continue
        if info.get("status") != "running":
            continue
        rid = info.get("room_id", "")
        try:
            await bridge.stop_room(platform, rid)
            stopped.append(rid)
        except Exception as e:  # noqa: BLE001  单房间停止失败不阻断清理
            logger.warning(f"[relogin] {key} stop failed: {e}")

    deleted = clear_login_state(platform, cookie_dir)
    logger.info(f"[relogin] {platform} 登录载体已清除: {deleted}（stopped={stopped}）")

    # 重新 start：既有登录门接管（弹可见窗→登录→保存→自动恢复监听）
    await bridge.start_room(platform, room_id)
    note = RELOGIN_NOTES.get(platform, "")
    return {"success": True, "cleared": deleted, "stopped": stopped,
            "msg": "登录窗口已弹出——请完成登录，登录后自动恢复监听"
                   + (f"（{note}）" if note else "")}
