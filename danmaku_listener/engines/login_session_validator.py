# -*- coding: UTF-8 -*-
"""登录会话有效性校验器（2026-11-12 根修批次——方案二判定升级）

背景（审查实证）：登录态判定是「凭证存在性检查」（cookie 文件存在且有 cookie），
从不校验会话有效性。10-08 登录的 B站 SESSDATA 到 11-12 已过期（nav API 实测
code=-101 isLogin=False），但文件仍在 → 配置期/运行期全部登录入口被
「已有登录态」阻断 → 用户无法重登 → 发送 -101 死循环（「不能登陆、不能发送」
根因 A）。

设计（Eng 约束两条硬红线）：
1. **绝不阻塞 NiceGUI 事件循环/轮询链**：`status()` 只做内存+本地 IO（缓存读+
   文件 mtime）；真实网络校验（bilibili nav API）在 daemon 后台线程执行，
   结果写缓存，下一轮轮询自然呈现。本项目已有两次「同步阻塞饿死 socket.io
   心跳→WS 判死重载」前科（audio-play/webui-async-endpoint，2026-11-06/11）。
2. **校验失败永不阻断登录流程**：网络失败/解析失败 → "unverified"（等价
   升级前的行为），不产生假"过期"。

状态机（status 返回值，None=未登录——由调用方 check_login 先行判定）：
- "valid"     真校验器确认会话有效（目前仅 bilibili）
- "expired"   真校验器确认会话失效（nav code=-101）
- "stale"     无真校验器/缓存未热——凭证文件 mtime 超过 STALE_DAYS（可能过期）
- "unverified"无校验依据（文件新鲜或网络失败）——等价升级前语义

真校验器覆盖（后续逐平台补）：
- bilibili: GET api.bilibili.com/x/web-interface/nav（项目内已实证接口——
  DanmuInfoFetcher.fetch 同源）；code==0 且 data.isLogin → valid，code==-101 → expired
"""
from __future__ import annotations

import json
import os
import threading
import time
from typing import Callable, Dict, Optional

from loguru import logger

#: 「可能过期」启发式阈值（天）：B站 SESSDATA 有效期约 1 个月，快手/斗鱼更短；
#: 超过此阈值且无真校验器 → "stale"（提示性，不阻断）
STALE_DAYS = 14

#: 校验缓存 TTL（秒）——校验低频（添加房间/徽章呈现），5 分钟足够新鲜
TTL_S = 300.0

#: 各平台登录载体路径（相对 cookie_dir；与 danmaku_login_service._meta 的
#: check 路径 / relogin.PLATFORM_LOGIN_CARRIERS 对齐——呈现层专用快查表）
COOKIE_PATHS: Dict[str, str] = {
    "bilibili": "bilibili_storage_state.json",
    "kuaishou": "kuaishou_storage_state.json",
    "douyin": "douyin_cookies.json",
    "douyu": "douyu_login_cookies.json",
    "huya": "huya_login_cookies.json",
    "taobao": "taobao_profile_login_state.json",
    "1688": "1688_profile_login_state.json",
    "xiaohongshu": "xhs_profile_login_state.json",
    "pdd": "pdd_profile_login_state.json",
    "jd": "jd_profile_login_state.json",
    "wechat_channels": "wxsp_profile_login_state.json",
}

#: 真校验器注册表（平台 → 校验函数：cookie_path → "valid"|"expired"|"unverified"）
#: 校验函数必须：①只读请求 ②内部捕获全部异常返回 "unverified" ③timeout 明确
VALIDATORS: Dict[str, Callable[[str], str]] = {}


def _validate_bilibili(cookie_path: str) -> str:
    """B站会话校验：storage_state cookie → nav API isLogin（项目实证接口）"""
    try:
        import requests

        if not os.path.exists(cookie_path):
            return "unverified"
        with open(cookie_path, encoding="utf-8") as fh:
            state = json.load(fh)
        cookies = state.get("cookies") if isinstance(state, dict) else []
        session = requests.Session()
        for c in cookies or []:
            if c.get("value"):
                session.cookies.set(c["name"], c["value"],
                                    domain=c.get("domain") or ".bilibili.com")
        resp = session.get(
            "https://api.bilibili.com/x/web-interface/nav", timeout=8,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                                   "Chrome/131.0.0.0 Safari/537.36",
                     "Referer": "https://www.bilibili.com/"})
        data = resp.json()
        if data.get("code") == 0 and (data.get("data") or {}).get("isLogin"):
            return "valid"
        if data.get("code") == -101:
            return "expired"
        return "unverified"  # 风控/未知码——不妄判
    except Exception as e:  # noqa: BLE001  校验失败永不阻断
        logger.debug(f"[login-validator] bilibili 校验失败（unverified）: "
                     f"{type(e).__name__}: {str(e)[:80]}")
        return "unverified"


VALIDATORS["bilibili"] = _validate_bilibili


class LoginSessionValidator:
    """平台会话有效性校验（缓存 + 后台线程刷新；实例由登录服务持有）"""

    def __init__(self) -> None:
        self._cache: Dict[str, dict] = {}  # platform → {status, checked_at}
        self._inflight: set = set()
        self._lock = threading.Lock()

    # ---------- 调用面（非阻塞；内存+本地 IO） ----------

    def status(self, platform: str, cookie_dir: str) -> Optional[str]:
        """登录态会话有效性（check_login=True 的前提下调用；None=无凭证文件）

        非阻塞契约：真实校验 miss 时返回启发式临时值并后台刷新缓存。
        """
        rel = COOKIE_PATHS.get(platform)
        if not rel:
            return None
        path = os.path.join(cookie_dir, rel)
        if not os.path.exists(path):
            return None

        with self._lock:
            cached = self._cache.get(platform)
        if cached and time.time() - cached["checked_at"] < TTL_S:
            return cached["status"]

        temporary = self._heuristic(platform, path)
        self._refresh_async(platform, path)  # 有真校验器的平台后台刷新（幂等去重）
        return temporary

    def validate_now(self, platform: str, cookie_dir: str) -> Optional[str]:
        """同步强制校验（onboarding/测试专用——调用方必须在 to_thread 内）"""
        rel = COOKIE_PATHS.get(platform)
        if not rel:
            return None
        path = os.path.join(cookie_dir, rel)
        if not os.path.exists(path):
            return None
        result = self._run_validator(platform, path)
        with self._lock:
            self._cache[platform] = {"status": result, "checked_at": time.time()}
        return result

    def invalidate(self, platform: str) -> None:
        """登录动作完成后清缓存（下次呈现重新校验）"""
        with self._lock:
            self._cache.pop(platform, None)
            self._inflight.discard(platform)

    # ---------- 内部 ----------

    @staticmethod
    def _heuristic(platform: str, path: str) -> str:
        """无真校验器/缓存未热时的本地启发式：mtime 新鲜度"""
        age_days = (time.time() - os.path.getmtime(path)) / 86400.0
        if age_days > STALE_DAYS:
            return "stale"
        return "unverified"

    def _run_validator(self, platform: str, path: str) -> str:
        fn = VALIDATORS.get(platform)
        if fn is None:
            return self._heuristic(platform, path)
        try:
            return fn(path)
        except Exception as e:  # noqa: BLE001  双层兜底（校验函数已自带，防御注册错）
            logger.debug(f"[login-validator] {platform} 校验异常: {type(e).__name__}")
            return "unverified"

    def _refresh_async(self, platform: str, path: str) -> None:
        """后台线程校验（有真校验器的平台）；inflight 去重防线程堆积"""
        if platform not in VALIDATORS:
            return
        with self._lock:
            if platform in self._inflight:
                return
            self._inflight.add(platform)

        def _job() -> None:
            try:
                result = self._run_validator(platform, path)
                with self._lock:
                    self._cache[platform] = {"status": result,
                                             "checked_at": time.time()}
            finally:
                with self._lock:
                    self._inflight.discard(platform)

        threading.Thread(target=_job, name=f"login-validate-{platform}",
                         daemon=True).start()
