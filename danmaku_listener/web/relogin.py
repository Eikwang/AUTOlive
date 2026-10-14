"""手动重新登录流程 v2——会话刷新编排（2026-10-13 /autoplan 批准计划 S1/S3/S4）

语义升级：v1 只做"清监听载体+触发监听登录门"（发送侧零接触——重登后发送静默
失败的根因 A）。v2 把重登升级为**会话刷新编排**：按平台路由登录闭环，监听侧与
发送侧一并刷新，异步两段式回执（同步段即时事实 + GET /api/relogin/status 状态
机轮询），AUTOlive 与 webui 前端均可编程消费。

载体分组（PLATFORM_LOGIN_CARRIERS 全清单保留——ReloginUnsupported 判定与
向后兼容；实际清理走 RELOGIN_CLEAN_SUBSETS 预定子集）：
- profile 组（taobao/1688/xiaohongshu/jd/wechat_channels/pdd）：监听发送同
  profile（EngineHookSender/锁互斥）→ 全清单清理，重登即全刷
- 文件组（bilibili/kuaishou/douyu）：sender 注入同一 cookie 文件 → 全清单清理
- 双载体组（douyin/huya）：发送侧 profile **不预清**（登录流程原地刷新；登录
  放弃时发送侧保留旧态不劣化），仅清监听侧文件：
  - huya: 清理集=空（监听匿名可听，huya_login_cookies.json 仅存档不再清）
  - douyin: 清理集=[douyin_cookies.json]（监听礼物事件登录态由单窗双喂重写）

执行模型（Eng 发现1/2，计划 §3.1-5）：
- Playwright 登录流（async API）在主事件循环 await（子进程驱动，非线程阻塞）
- 清理类大 IO（rmtree）用 asyncio.to_thread 包裹；导出类小落盘（10-100KB）
  同步写（登录低频路径，量级判定见 2026-10-14 ship perf 审查）
- **每平台串行**：per-platform asyncio.Lock——同一平台禁止两个登录窗并发
  （persistent profile 目录锁/可见窗互斥）；跨平台编排可并发
- supersede 取消 = task.cancel() + 带超时 join 等待实际退出（旧任务 finally
  走既有 close() 幂等清理——resident_session.py finally/ENG-4 同语义；裸
  cancel 不 join 会新旧可见窗争抢同一 profile 目录）
- watcher 在事件循环轮询注册表（纯内存读）；注册表读写限定单一事件循环（免锁）

回执契约（AUTOlive 消费，计划 §3.1-6）：
- 同步段（POST /api/relogin 立即返回，只含即时事实）：
  {relogin_id, platform, rooms_stopped[], carriers_cleared[], send_refresh:
   "pending"|"not_applicable"|"close_ok"|"close_failed_stale_marked",
   login_flows: [{side, kind, triggered, reused_existing}]}
- 异步段（GET /api/relogin/status?id= 轮询）：
  {state: recovering|completed|login_required|failed|superseded, rooms:
   [{room_id, status: running|stopped|failed, recovery_error?}], send_refresh:
   ...+refresh_ok|refresh_failed, login_flows, interruption_window, error?}
- send_refresh 终值规则：not_applicable=单载体终值；close_*=双载体中间值（登录
  流程出结果后必被 refresh_* 覆盖；导出失败=refresh_failed，来源含导出失败）
- 404=记录不可得（重启即失/淘汰），AUTOlive 契约：停止轮询按未知终局处理
  （重 POST 有停房+弹窗副作用——指南注明）
"""
from __future__ import annotations

import asyncio
import copy
import os
import shutil
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from loguru import logger

# 跨层常量单源（2026-10-14 D5/F21：登录窗超时与 huya takeover 参数组——
# douyin_login/send_login 均只依赖 stdlib+loguru，web→engines 方向无环）
from danmaku_listener.engines.douyin_login import DEFAULT_TIMEOUT
from danmaku_listener.engines.send_login import HUYA_TAKEOVER_PARAMS

#: 平台登录载体全清单（相对 cookie/ 目录；values 顺序=清理顺序）——
#: 语义不变：ReloginUnsupported 判定 + 兼容既有调用方（clear_login_state 缺省）
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

#: 预定清理子集（S1：双载体平台发送侧不预清，原地刷新）
#: - 单载体平台=全清单；huya=空（监听匿名可听，存档文件不再清）；
#:   douyin=仅监听 cookie 文件（监听礼物事件登录态由单窗双喂重写）
RELOGIN_CLEAN_SUBSETS: dict[str, list[str]] = {
    "huya": [],
    "douyin": ["douyin_cookies.json"],
}

#: 双载体平台（发送侧走独立登录闭环；回执 send_refresh 用 refresh_* 终值）
DUAL_CARRIER_PLATFORMS = ("douyin", "huya")

#: 监听引擎登录超时常量锚点（watcher 预算下限=登录窗自身超时+缓冲，Eng C6/R9）
#: douyin=DEFAULT_TIMEOUT 引用（engines/douyin_login.py，F21 单源）；huya 240.0
#: 与 resident_session.login_takeover 的 timeout_s 缺省对齐（该处无常量——字面量锚定）
LOGIN_WINDOW_TIMEOUT_S = {"huya": 240.0, "douyin": DEFAULT_TIMEOUT}
#: 显式 hard-coded 常量（DX F8：内部单一消费方、无常量调优实证，暂不开配置面）
WATCHER_POLL_S = 2.0
RETRY_AFTER_S = 30
REGISTRY_KEEP_MAX = 10
REGISTRY_KEEP_S = 3600.0
#: 房间恢复预算（E6：按房间数放大——多房串行 start_room 不得挤爆 30s 缓冲）
RECOVERY_BUDGET_PER_ROOM_S = 30.0
#: supersede join 超时（旧任务 finally 清理上限；超时则记录告警后放行）
SUPERSEDE_JOIN_TIMEOUT_S = 15.0
#: 发送会话 close join 超时（close 超时→DX-D7 stale 补偿路径接管）
CLOSE_JOIN_TIMEOUT_S = 20.0

#: 平台重登提示（v2 语义：如实描述各平台行为——修 RELOGIN_NOTES 失真，S4-C）
RELOGIN_NOTES = {
    "huya": "虎牙：弹发送登录窗口（监听匿名可听自动恢复）；重登=主动刷新，"
            "两次重登间会话自然过期仍走发送时懒弹登录",
    "douyin": "抖音：弹 profile 登录窗口，一次扫码同时刷新发送与监听登录态",
}


class ReloginUnsupported(Exception):
    """平台无登录载体定义（不支持手动重登）"""


def clear_login_state(platform: str, cookie_dir: str = "./cookie",
                      carriers: Optional[list[str]] = None) -> list[str]:
    """清平台登录载体；返回已删除的相对路径列表

    carriers 覆写参数（S1 分平台精确化）：None=全清单（向后兼容既有调用）；
    编排层传 RELOGIN_CLEAN_SUBSETS 子集。
    安全边界：仅允许 cookie/ 目录内的相对路径（防路径逃逸——载体清单是
    代码常量，此处仍按目录逃逸防护兜底）。
    """
    names = PLATFORM_LOGIN_CARRIERS.get(platform) if carriers is None else carriers
    if not names and carriers is None:
        raise ReloginUnsupported(f"平台 {platform} 不支持手动重登（无登录载体定义）")
    base = Path(cookie_dir).resolve()
    deleted: list[str] = []
    for name in names:
        target = (base / name).resolve()
        if target != base and base not in target.parents:
            logger.warning(f"[relogin] {platform} 载体路径逃逸，跳过: {name}")
            continue
        if target.is_dir():
            shutil.rmtree(target, ignore_errors=True)
            # ship 对抗F4：rmtree(ignore_errors=True) 会静默吞部分删除（Windows
            # 句柄延迟释放常态）——以实际消失为准，未消失的进 missing 差集告警
            if not target.exists():
                deleted.append(name)
        elif target.is_file():
            try:
                target.unlink(missing_ok=True)
                deleted.append(name)
            except OSError as e:  # 文件占用（PermissionError 等）不炸同步段
                logger.warning(f"[relogin] {platform} 载体删除失败: {name}: {e}")
    return deleted


# ---------------------------------------------------------------------------
# 发送会话接线（S4-B：复用既有 ResidentSendSession.close()，存在才关——
# _get_session 懒创建会为从未发送的平台凭空拉起浏览器只为关掉它；
# import 边界：只 import senders.<p> 模块级单例，禁止 import senders.wiring）
# ---------------------------------------------------------------------------

def _peek_send_session(platform: str):
    """读模块级会话单例（不触发懒创建）；None=无活跃发送会话"""
    if platform == "huya":
        from danmaku_listener.senders import huya as mod
        return mod._session
    if platform == "douyin":
        from danmaku_listener.senders import douyin as mod
        return mod._session
    return None


async def _close_send_session(platform: str) -> str:
    """关闭该平台发送会话；返回 send_refresh 同步段值

    close 超时由 ResidentSendSession.close() 内部转 stale 标记（DX-D7：
    resident_session.py finally——下次启动前清理），编排层如实回执。
    """
    session = _peek_send_session(platform)
    if session is None:
        logger.info(f"[relogin] {platform} 无活跃发送会话（存在才关）——close_ok")
        return "close_ok"
    try:
        await asyncio.wait_for(session.close(), timeout=CLOSE_JOIN_TIMEOUT_S)
        logger.info(f"[relogin] {platform} 发送会话已关闭（close_ok）")
        return "close_ok"
    except asyncio.TimeoutError:
        logger.warning(f"[relogin] {platform} 发送会话 close 超时——DX-D7 stale 补偿路径接管")
        return "close_failed_stale_marked"
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[relogin] {platform} 发送会话 close 异常: {type(e).__name__}: {str(e)[:80]}")
        return "close_failed_stale_marked"


# ---------------------------------------------------------------------------
# 注册表（进程内存态；重启即失；终态保留 10 条/1h 先到为准；单事件循环访问）
# ---------------------------------------------------------------------------

class ReloginRegistry:
    """relogin 会话注册表（Eng 发现2：单一事件循环内访问，免锁）"""

    def __init__(self) -> None:
        self._sessions: dict[str, dict] = {}
        self._active_by_platform: dict[str, str] = {}
        #: 平台级恢复集合（supersede 转移——Eng 发现4：恢复判定=平台全部因
        #: relogin 而 stopped 的房间，而非仅本次停房集合）
        self._recovery_by_platform: dict[str, dict[str, dict]] = {}

    def create(self, platform: str, room_id: str, rooms_stopped: list[str],
               carriers_cleared: list[str], carriers_expected: list[str],
               send_refresh: str, login_flows: list[dict]) -> dict:
        rec = {
            "relogin_id": str(uuid.uuid4()),
            "platform": platform,
            "room_id": room_id,
            "state": "recovering",
            "rooms_stopped": list(rooms_stopped),
            "carriers_expected": list(carriers_expected),
            "carriers_cleared": list(carriers_cleared),
            "send_refresh": send_refresh,
            "login_flows": list(login_flows),
            "rooms": {rid: {"room_id": rid, "status": "stopped"}
                      for rid in rooms_stopped},
            "interruption_window": {"started_at": int(time.time()), "ended_at": None},
            "error": None,
            "created_at": time.time(),
            "finished_at": None,
            "superseded_by": None,
        }
        self._sessions[rec["relogin_id"]] = rec
        self._recovery_by_platform.setdefault(platform, {})
        for rid in rooms_stopped:
            self._recovery_by_platform[platform][rid] = rec["rooms"][rid]
        self._gc()
        return rec

    def get(self, relogin_id: str) -> Optional[dict]:
        return self._sessions.get(relogin_id)

    def active_id(self, platform: str) -> Optional[str]:
        return self._active_by_platform.get(platform)

    def bind_active(self, platform: str, relogin_id: str) -> None:
        self._active_by_platform[platform] = relogin_id

    def recovery_rooms(self, platform: str) -> dict[str, dict]:
        return self._recovery_by_platform.get(platform, {})

    def transfer_recovery(self, old_platform_record: dict, new_record: dict) -> None:
        """supersede：旧记录未完成恢复的停房集合转移给新记录（Eng 发现4）"""
        platform = old_platform_record["platform"]
        pool = self._recovery_by_platform.get(platform, {})
        for rid, info in list(pool.items()):
            if info.get("status") != "running":
                new_record["rooms"].setdefault(rid, dict(info))
        new_record["rooms_stopped"] = sorted(
            {rid for rid, info in new_record["rooms"].items()
             if info.get("status") != "running"} | set(new_record["rooms_stopped"]))

    def mark_superseded(self, relogin_id: str, by_id: str) -> None:
        rec = self._sessions.get(relogin_id)
        if rec and rec["state"] in ("recovering",):
            rec["state"] = "superseded"
            rec["superseded_by"] = by_id
            rec["finished_at"] = time.time()
            if rec["interruption_window"]["ended_at"] is None:
                rec["interruption_window"]["ended_at"] = int(time.time())

    def set_room(self, platform: str, room_id: str, status: str,
                 recovery_error: Optional[str] = None) -> None:
        """更新平台恢复集合中的房间状态（跨记录共享——watcher 判定用）"""
        info = self._recovery_by_platform.get(platform, {}).get(room_id)
        if info is None:
            return
        info["status"] = status
        if recovery_error is not None:
            info["recovery_error"] = recovery_error
        # 同步回各记录视图（进行中的记录的 rooms 是同一 dict 引用或副本）
        for rec in self._sessions.values():
            if rec["platform"] == platform and room_id in rec["rooms"]:
                rec["rooms"][room_id] = dict(info)

    def finish(self, relogin_id: str, state: str, error: Optional[str] = None) -> None:
        rec = self._sessions.get(relogin_id)
        if not rec or rec["finished_at"] is not None:
            return
        rec["state"] = state
        if error is not None:  # 编排流程已设的 error（如导出失败）不被终局覆写
            rec["error"] = error
        rec["finished_at"] = time.time()
        if rec["interruption_window"]["ended_at"] is None:
            rec["interruption_window"]["ended_at"] = int(time.time())
        # 恢复集合出清：全部 running 则移除（平台无因 relogin 停房）
        pool = self._recovery_by_platform.get(rec["platform"], {})
        if pool and all(info.get("status") == "running" for info in pool.values()):
            self._recovery_by_platform.pop(rec["platform"], None)
        if self._active_by_platform.get(rec["platform"]) == relogin_id:
            self._active_by_platform.pop(rec["platform"], None)

    def _gc(self) -> None:
        """终态保留 10 条/1h 先到为准（淘汰=404 语义）"""
        done = sorted((r for r in self._sessions.values() if r["finished_at"]),
                      key=lambda r: r["finished_at"])
        now = time.time()
        drop = {r["relogin_id"] for i, r in enumerate(done)
                if i < len(done) - REGISTRY_KEEP_MAX or now - r["finished_at"] > REGISTRY_KEEP_S}
        for rid in drop:
            self._sessions.pop(rid, None)


#: 进程级注册表（webui 单进程；pipeline fence 与 status 端点共用——
#: senders→web 反向引用先例 wiring.py:51，本方向 web 内部无环）
REGISTRY = ReloginRegistry()


def get_active_relogin(platform: str) -> Optional[dict]:
    """pipeline fence 查表入口（一次查表 O(1)——计划 §3.1-6/S4 附带）"""
    rid = REGISTRY.active_id(platform)
    return REGISTRY.get(rid) if rid else None


# ---------------------------------------------------------------------------
# 平台路由登录闭环（后台任务体）
# ---------------------------------------------------------------------------

async def _recover_rooms(rec: dict, bridge, platform: str, *,
                         guard_terminal: bool = False) -> None:
    """恢复循环：逐房 start_room，失败计入 recovery_error；guard_terminal=True 时终态早退（幂等守卫）

    2026-10-14 D5：douyin/huya/orchestrate 单载体 三处同构循环收口（running 跳过
    判定统一为 rec["rooms"].get(rid, {}).get("status")——原 douyin 版
    `rid in rec["rooms"] and ...` 语义等价）；文案统一「已触发恢复」。
    """
    for rid in list(REGISTRY.recovery_rooms(platform).keys()):
        if guard_terminal and rec["state"] != "recovering":
            return
        if rec["rooms"].get(rid, {}).get("status") == "running":
            continue
        try:
            await bridge.start_room(platform, rid)
            logger.info(f"[relogin][{platform}] 房间 {rid} 已触发恢复")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[relogin][{platform}] 房间 {rid} start_room 失败: {e}")
            REGISTRY.set_room(platform, rid, "failed", recovery_error=str(e)[:120])


async def _douyin_profile_login_and_export(rec: dict, bridge, room_id: str,
                                           cookie_dir: str) -> None:
    """抖音单窗双喂（S2）：profile 登录→域白名单导出→start_room 恢复

    复用 douyin_login.run_profile_login_flow（插导出步骤：export_cookie_path
    参数——登录落地后、finally 关 context 前执行）；导出失败=refresh_failed
    不阻断关窗 finally（Eng 发现12）；登录放弃/超时→login_required。

    降级两窗形态（D4 测试开关 DANMU_RELOGIN_FORCE_TWO_WINDOW=1）：先触发监听
    侧登录门（start_room 写 douyin_cookies.json——原闭环）再跑 profile 登录，
    两窗串行；单窗固化后降级路径仍可回归验证（计划 §5.6）。

    独立 Playwright 实例（run_profile_login_flow 内部 finally 已关 context）
    ——编排层无需会话级清理（2026-10-14 F17：原空 finally 注释上移至此）。
    """
    from danmaku_listener.engines.douyin_login import run_profile_login_flow

    if os.environ.get("DANMU_RELOGIN_FORCE_TWO_WINDOW") == "1":
        logger.info("[relogin][douyin] 降级两窗形态（测试开关）——先监听登录门后 profile 登录")
        rec["login_flows"] = [
            {"side": "listen", "kind": "login_gate", "triggered": True, "reused_existing": False},
            {"side": "send", "kind": "profile_login", "triggered": True, "reused_existing": False},
        ]
        try:
            await bridge.start_room("douyin", room_id)  # 监听登录门（写 cookie 文件）
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[relogin][douyin] 监听登录门触发失败: {e}")

    cookie_path = str(Path(cookie_dir) / "douyin_cookies.json")
    try:
        result = await run_profile_login_flow(
            room_id=room_id, headless=False,
            export_cookie_path=cookie_path,
            on_status=lambda s: logger.info(f"[relogin][douyin] login status: {s}"))
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001  编排层兜底（导出异常/意外失败）
        logger.warning(f"[relogin][douyin] profile login 异常: {type(e).__name__}: {str(e)[:100]}")
        rec["send_refresh"] = "refresh_failed"
        rec["error"] = (f"抖音登录流程异常: {type(e).__name__}——重跑重登即可，"
                        "旧态不劣化（docs/integration/autolive-merge-v0.7.0.md §4.4）")
        return
    if result.get("status") != "ok":
        rec["send_refresh"] = "refresh_failed"
        REGISTRY.finish(rec["relogin_id"], "login_required",
                        error="抖音登录未完成（超时/关窗）——发送侧旧态保留，重跑重登即可"
                              "（docs/integration/autolive-merge-v0.7.0.md §4.4）")
        return
    export = result.get("export", "failed")
    rec["send_refresh"] = "refresh_ok" if export == "ok" else "refresh_failed"
    if result.get("reused_existing"):
        profile_flow = next((f for f in rec["login_flows"]
                             if f["kind"] == "profile_login"), None)
        if profile_flow is not None:
            # 按 kind 定位（F23：防降级两窗路径把 listen 项错标 reused_existing）
            profile_flow["reused_existing"] = True  # S2：复用存活旧态（非新扫码）
    if export != "ok":
        rec["error"] = ("douyin_cookies.json 导出失败（登录已成功，发送侧新态在 profile）"
                        "——重跑重登即可（docs/integration/autolive-merge-v0.7.0.md §4.4）")
    # 登录落地→恢复监听房间（携新 cookie 直启；失败计入房间恢复失败）
    await _recover_rooms(rec, bridge, "douyin")


async def _huya_takeover_and_recover(rec: dict, bridge, room_id: str) -> None:
    """虎牙重登直指发送侧（S3）：close 后 takeover 刷新 profile，恢复不依赖其结果

    takeover 在主事件循环 await（Playwright 子进程驱动，非线程阻塞）；
    被取消时 finally 关可见窗（下方 _orchestrate 统一兜底 close）。
    """
    session = _peek_send_session("huya")
    if session is None:
        # 存在才关探测为 None——takeover 经 _get_session 懒创建（此路径创建无害：
        # 本来就要弹窗登录）。wiring 侧 sender 已注册时 _session 必已存在。
        from danmaku_listener.config.settings import get_settings
        from danmaku_listener.senders.huya import _get_session
        st = get_settings()
        mode = getattr(st, "send_window_mode", "headless_new") or "headless_new"
        idle = int(getattr(st, "send_session_idle_timeout_seconds", 1800) or 1800)
        session = _get_session(idle, mode)
    try:
        await session.login_takeover(room_id, *HUYA_TAKEOVER_PARAMS)
        rec["send_refresh"] = "refresh_ok"
        logger.info("[relogin][huya] 发送侧登录刷新完成（takeover→快照→转后台）")
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001  LoginTakeoverError/超时/滑块未过
        logger.warning(f"[relogin][huya] takeover 失败: {type(e).__name__}: {str(e)[:100]}")
        rec["send_refresh"] = "refresh_failed"
        rec["error"] = (f"虎牙发送登录未完成: {type(e).__name__}——监听不受影响，"
                        "稍后发送将懒弹登录窗自愈（docs/integration/autolive-merge-v0.7.0.md §4.4）")
    # takeover 触发后即直启恢复监听（匿名可听，不依赖发送侧结果——S3）
    await _recover_rooms(rec, bridge, "huya")


async def _orchestrate(rec: dict, bridge, platform: str, room_id: str,
                       cookie_dir: str) -> None:
    """后台编排任务体：路由登录闭环 → watcher（恢复判定/终局）——计划 §3.1-3/4

    幂等守卫（Eng 发现6）：每次 start_room 前检查记录是否已终态，已终态跳过恢复
    （单载体分支经 _recover_rooms(guard_terminal=True)）。被取消（supersede）时
    走既有 close() 幂等清理（虎牙可见窗/发送会话；抖音 profile 登录窗是独立
    Playwright 实例——run_profile_login_flow 内部 finally 已关 context，编排层
    无需会话级清理，2026-10-14 F17 空 finally 已删）。
    """
    platform_lock = _platform_locks.setdefault(platform, asyncio.Lock())
    try:
        async with platform_lock:
            if platform == "douyin":
                await _douyin_profile_login_and_export(rec, bridge, room_id, cookie_dir)
            elif platform == "huya":
                await _huya_takeover_and_recover(rec, bridge, room_id)
            else:
                # 单载体组：维持现有闭环——start_room 登录门弹窗（登录完成自动恢复）；
                # guard_terminal=True：幂等守卫（已终态跳过恢复）
                await _recover_rooms(rec, bridge, platform, guard_terminal=True)
            await _watch_recovery(rec, bridge, platform)
    except asyncio.CancelledError:
        logger.info(f"[relogin][{platform}] 编排任务被取消（supersede）——幂等清理")
        if platform == "huya":
            # F5：此处重新 peek 现值——编排开头 peek 的旧引用关不到懒创建的
            # huya 会话；_close_send_session 内部已有 wait_for close+stale 映射语义
            await _close_send_session("huya")
        raise
    except Exception as e:  # noqa: BLE001  ship 对抗F3：编排意外死亡必须终局化
        # 否则 rec 永久 recovering → fence 对该平台发送永久 RELOGIN_IN_PROGRESS、
        # status 端点永不终局（AUTOlive 契约只有 404 才停轮询）。
        logger.error(f"[relogin][{platform}] 编排任务异常退出: {type(e).__name__}: {str(e)[:120]}")
        if rec["state"] == "recovering":  # 幂等：watcher 可能已终局
            REGISTRY.finish(rec["relogin_id"], "failed",
                            error=f"编排内部异常: {type(e).__name__}——重跑重登即可"
                                  "（docs/integration/autolive-merge-v0.7.0.md §4.4）")


async def _watch_recovery(rec: dict, bridge, platform: str) -> None:
    """恢复判定 watcher（事件循环内纯内存读）：完成/超时终局——计划 §3.1-4

    完成条件（Eng 发现5 修正）：平台恢复集合全部 running **且** send_refresh 非
    pending（空集合只豁免房间等待——state 等 send_refresh 落定，防 completed
    吞掉后续 refresh_failed）。终局：completed | login_required | failed(恢复超时/内部错误)。
    """
    login_timeout = LOGIN_WINDOW_TIMEOUT_S.get(platform, 300.0)
    n_rooms = max(1, len(REGISTRY.recovery_rooms(platform)))
    budget = max(login_timeout + 30.0, n_rooms * RECOVERY_BUDGET_PER_ROOM_S)
    deadline = time.monotonic() + budget
    login_done = platform not in DUAL_CARRIER_PLATFORMS  # 单载体：登录完成=房间恢复

    while True:
        pool = REGISTRY.recovery_rooms(platform)
        # 桥接侧状态回读（本地内存；running 即恢复）
        for rid, info in list(pool.items()):
            if info.get("status") == "running":
                continue
            key = f"{platform}:{rid}"
            room = getattr(bridge, "_rooms", {}).get(key)
            if not room:
                continue
            st = room.get("status")
            if st == "running":
                REGISTRY.set_room(platform, rid, "running")
            elif st in ("error", "failed", "login_failed", "login_required"):
                # bridge._rooms 实际值域含登录失败态（bridge.py :129/:166 login_failed、
                # :198/:211/:226 login_required）——F4：不识别会白等满恢复预算
                REGISTRY.set_room(platform, rid, "failed", recovery_error="房间启动失败")
        all_running = (not pool) or all(i.get("status") == "running" for i in pool.values())
        # 早停（Eng 发现6 镜像）：全部恢复房间 failed → 无可等待，立即 failed 终局
        if pool and all(i.get("status") == "failed" for i in pool.values()):
            REGISTRY.finish(rec["relogin_id"], "failed",
                            error="房间恢复失败（start_room 失败）——重跑重登或手动启动"
                                  "（docs/integration/autolive-merge-v0.7.0.md §4.4）")
            return
        # 未决=非 refresh_* 终值且非单载体终值（契约：close_* 中间值必被 refresh_* 覆盖）
        send_pending = rec["send_refresh"] in ("pending", "close_ok", "close_failed_stale_marked")
        if rec["state"] != "recovering":
            return  # 已被 supersede/finish——让位
        if all_running and not send_pending:
            REGISTRY.finish(rec["relogin_id"], "completed")
            logger.info(f"[relogin][{platform}] 完成（relogin_id={rec['relogin_id']}）")
            return
        if platform in DUAL_CARRIER_PLATFORMS and not login_done \
                and rec["send_refresh"] in ("refresh_ok", "refresh_failed"):
            login_done = True  # 登录流程已出结果（含失败——部分成功语义不翻转整体）
        if time.monotonic() >= deadline:
            break
        await asyncio.sleep(WATCHER_POLL_S)

    # 预算超时终局（login_done 判定在超时后再取一次最终值）
    pool = REGISTRY.recovery_rooms(platform)
    all_running = (not pool) or all(i.get("status") == "running" for i in pool.values())
    if rec["send_refresh"] == "pending" and platform in DUAL_CARRIER_PLATFORMS \
            and not login_done:
        # 登录窗自身超时应已判负（预算≥窗口+30s）——此处兜底判 login_required
        # ship 对抗终审F4 留档：当前生产不可达（watcher 严格在登录流程返回后启动，
        # 此时 send_refresh 必为 refresh_ok/refresh_failed；同步段只写 close_*/not_
        # applicable，无 pending）——该分支守护的是「未来重构把 watcher 提前并发」
        # 的假想形态；若删测试参数化 (pending→login_required) 会红。
        REGISTRY.finish(rec["relogin_id"], "login_required",
                        error="登录窗口超时未完成——房间保持停止，重跑重登即可")
        return
    if all_running:
        # 房间齐了但 send_refresh 仍未落定（不应发生——兜底等 send 刷新短窗）
        REGISTRY.finish(rec["relogin_id"], "completed")
        return
    REGISTRY.finish(rec["relogin_id"], "failed",
                    error="恢复超时（非登录原因）——部分房间未回到监听，重跑重登或手动启动"
                          "（docs/integration/autolive-merge-v0.7.0.md §4.4）")


_platform_locks: dict[str, asyncio.Lock] = {}


# ---------------------------------------------------------------------------
# 同步段入口（POST /api/relogin 处理体）——计划 §3.1-1/2
# ---------------------------------------------------------------------------

#: per-platform 同步段互斥（2026-10-14 D3：并发双编排防护）——supersede 检查与
#: bind_active 之间有多个 await，无互斥时并发 POST 同平台会产生双编排。与
#: _platform_locks 分开：orchestrate 的锁覆盖整个登录闭环（可达数分钟），同步段
#: 若复用该锁会被在途编排阻塞；本锁只覆盖秒级同步段，两锁无共享临界区、不成环。
_RELOGIN_SYNC_LOCKS: dict[str, asyncio.Lock] = {}


async def run_relogin_flow(bridge, platform: str, room_id: str,
                           cookie_dir: str = "./cookie") -> dict:
    """会话刷新编排同步段：停房→关发送会话→清载体（子集）→后台触发登录闭环

    立即返回同步段回执（即时事实）；登录与恢复由后台任务推进，AUTOlive 轮询
    GET /api/relogin/status?id= 消费终局。同平台再次重登=supersede（cancel+join）。
    同步段全程持有 _RELOGIN_SYNC_LOCKS[platform]（D3 并发双编排防护）。
    """
    if platform not in PLATFORM_LOGIN_CARRIERS:
        raise ReloginUnsupported(f"平台 {platform} 不支持手动重登（无登录载体定义）")

    async with _RELOGIN_SYNC_LOCKS.setdefault(platform, asyncio.Lock()):
        # supersede：同平台旧活跃编排先取消并 join（防新旧窗争抢 profile 目录锁）
        old_id = REGISTRY.active_id(platform)
        old_task = _active_tasks.get(platform)
        if old_id and old_task and not old_task.done():
            logger.info(f"[relogin][{platform}] supersede 旧编排 {old_id[:8]}…（cancel+join）")
            old_task.cancel()
            # asyncio.wait 不传播被等任务的取消/异常，且当前协程被取消时本 await
            # 处正常抛 CancelledError（2026-10-14 D4：替代 wait_for 的异常混入语义）
            done, _ = await asyncio.wait({old_task}, timeout=SUPERSEDE_JOIN_TIMEOUT_S)
            if not done:
                logger.info(f"[relogin][{platform}] supersede join 超时放行（DX-D7 兜底）")
            elif not old_task.cancelled():
                old_task.exception()  # 取走旧任务异常防 GC "never retrieved" 噪音（不传播）
            # 旧任务 finally 已走 close() 幂等清理

        # 1. 停该平台全部在听房间（profile 单实例独占）
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
            except Exception as e:  # noqa: BLE001  单房间停止失败不阻断
                logger.warning(f"[relogin] {key} stop failed: {e}")

        # 2. 关发送会话（存在才关；双载体平台才有常驻会话）
        if platform in DUAL_CARRIER_PLATFORMS:
            send_refresh = await _close_send_session(platform)
        else:
            send_refresh = "not_applicable"

        # 3. 清载体（预定清理子集；双载体发送侧不预清）——FS 阻塞操作 to_thread
        subset = RELOGIN_CLEAN_SUBSETS.get(platform, PLATFORM_LOGIN_CARRIERS[platform])
        carriers_expected = list(subset)
        deleted = await asyncio.to_thread(clear_login_state, platform, cookie_dir, subset) \
            if subset else []
        # 差集（Eng CEO F-A：基准=预定清理子集∩实际存在路径；rmtree ignore_errors
        # 已尽力——deleted 与期望差集仅记录告警，真失败由登录流程暴露）
        missing = [c for c in carriers_expected if c not in deleted]
        if missing:
            logger.warning(f"[relogin] {platform} 预定清理子集存在但未删除: {missing}"
                           "（可能文件本不存在或句柄延迟释放——Windows 常态）")
        logger.info(f"[relogin] {platform} 载体已清: {deleted}（stopped={stopped}，"
                    f"send_refresh={send_refresh}）")

        # 4. 建记录 + 后台触发路由登录闭环
        kind = {"douyin": "profile_login", "huya": "takeover"}.get(platform, "login_gate")
        login_flows = [{"side": "send" if platform in DUAL_CARRIER_PLATFORMS else "listen",
                        "kind": kind, "triggered": True, "reused_existing": False}]
        rec = REGISTRY.create(platform, room_id, stopped, deleted, carriers_expected,
                              send_refresh, login_flows)
        # supersede 停房集合转移（Eng 发现4）：旧记录未完成恢复的房间并入新恢复判定
        if old_id:
            old_rec = REGISTRY.get(old_id)
            if old_rec:
                REGISTRY.transfer_recovery(old_rec, rec)
        REGISTRY.bind_active(platform, rec["relogin_id"])

        task = asyncio.create_task(_orchestrate(rec, bridge, platform, room_id, cookie_dir),
                                   name=f"relogin-{platform}")
        _active_tasks[platform] = task
        # 把 superseded 旧记录指向新记录
        if old_id:
            REGISTRY.mark_superseded(old_id, by_id=rec["relogin_id"])

    note = RELOGIN_NOTES.get(platform, "")
    # ship 对抗终审F3：回执内层容器 deepcopy——list(...) 只浅拷贝容器，元素 dict
    # 与注册表同对象；F23 原地改 rec["login_flows"] 元素会穿透持有同步回执的调用方
    return {
        "success": True,
        "relogin_id": rec["relogin_id"],
        "platform": platform,
        "rooms_stopped": copy.deepcopy(rec["rooms_stopped"]),
        "carriers_cleared": copy.deepcopy(rec["carriers_cleared"]),
        "send_refresh": rec["send_refresh"],
        "login_flows": copy.deepcopy(rec["login_flows"]),
        "status_url": f"/api/relogin/status?id={rec['relogin_id']}",
        "msg": "会话刷新编排已启动——请完成登录窗口，恢复进度轮询 status 端点"
               + (f"（{note}）" if note else ""),
    }


_active_tasks: dict[str, asyncio.Task] = {}


def get_relogin_status(relogin_id: str) -> Optional[dict]:
    """异步段回执（status 端点处理体）；未知 id → None（路由层 404）

    深拷贝隔离（ship 对抗F9）：嵌套 rooms/login_flows/interruption_window 与
    注册表共享引用会让调用方改动穿透注册表——契约面一律 deepcopy。
    """
    rec = REGISTRY.get(relogin_id)
    if rec is None:
        return None
    return copy.deepcopy(rec)
