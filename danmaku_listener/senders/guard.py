"""SendGuard——风控六件套（AutoDanmu T4；开关/限速/过滤/dry-run/审计/熔断）

状态矩阵（P2/DX-F1/F4 裁定）：
- 开关关      → SENDER_DISABLED（拒绝+审计一行）
- 开关开+dry-run 开 → 只记录不实发（status=dry_run 非成功语义）
- 开关开+dry-run 关 → 实发
守卫命中=拒绝不排队（DX-F1）：限速/去重/超长/关键词/审计 fail-closed 各自回执原因码。
熔断（R10/F3）：连续 N 次失败 → CIRCUIT_OPEN（与人工关闭 SENDER_DISABLED 分离）；
恢复=人工 enable() 重开。unknown 不计失败但计告警（R36）。
"""
from __future__ import annotations

import hashlib
import time
from typing import Dict, Optional, Tuple

import json

from loguru import logger

from danmaku_listener.config.settings import Settings
from danmaku_listener.contract.models import SendRejectReason

#: per-platform 限速覆写内置默认（T6 裁定：虎牙 35s——10-14s 实测失败、35s 补发全过，
#: 默认 30s 无裕量；代码内置=出厂即安全，INI 同键值合并覆写优先——DX-D3/ENG-3）
DEFAULT_MIN_INTERVAL_OVERRIDES = {"huya": 35.0}


def load_min_interval_overrides(raw: str) -> dict:
    """解析 INI overrides JSON 并按键合并进内置默认（未覆写平台保留默认值）"""
    merged = dict(DEFAULT_MIN_INTERVAL_OVERRIDES)
    raw = (raw or "").strip()
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                merged.update({k: float(v) for k, v in parsed.items()})
        except (json.JSONDecodeError, ValueError, TypeError) as e:
            logger.warning(f"[send-guard] send_min_interval_overrides 解析失败（忽略）: {e}")
    return merged


class SendGuard:
    """发送前置守卫：全部拒绝类判定集中此处（管线唯一调用方，R32）"""

    def __init__(self, settings: Settings, blocked_keywords: Optional[list[str]] = None):
        self._settings = settings
        self._keywords = {k.strip() for k in (blocked_keywords or []) if k.strip()}
        self._enabled = {p.strip() for p in settings.send_enabled_platforms.split(",") if p.strip()}
        # 限速键 → 上次发送时刻（F4：键口径=platform 或 platform_room）
        self._last_send: Dict[str, float] = {}
        # 同内容去重窗口（R14）：content_hash → 上次发送时刻
        self._dedup: Dict[str, float] = {}
        # 熔断状态（F3/R10）：platform → 连续失败计数 + 是否熔断
        self._fail_streak: Dict[str, int] = {}
        self._tripped: Dict[str, bool] = {}
        # per-platform 限速覆写（T6/X1：按键合并，内置默认兜底——DX-D3/ENG-3）
        self._interval_overrides = load_min_interval_overrides(
            getattr(settings, "send_min_interval_overrides", "") or "")

    # ---- 开关与状态 ----

    def is_enabled(self, platform: str) -> bool:
        return platform in self._enabled

    def is_tripped(self, platform: str) -> bool:
        return bool(self._tripped.get(platform))

    def enable(self, platform: str) -> None:
        """人工启用/熔断重开（恢复默认=人工重开，不自动恢复——R10）"""
        self._enabled.add(platform)
        self._tripped[platform] = False
        self._fail_streak[platform] = 0
        logger.info(f"[send-guard] platform {platform} enabled (manual)")

    def disable(self, platform: str) -> None:
        self._enabled.discard(platform)
        self._tripped[platform] = False
        self._fail_streak[platform] = 0

    def snapshot(self) -> dict:
        """day-1 状态行数据源（S8-1）：平台/开关/dry-run/熔断三态"""
        platforms = sorted(self._enabled | set(self._tripped))
        return {
            "dry_run": self._settings.send_dry_run,
            "platforms": {
                p: {
                    "enabled": self.is_enabled(p),
                    "circuit_open": self.is_tripped(p),
                }
                for p in platforms
            },
        }

    # ---- 守卫检查（管线唯一入口）----

    def check(self, platform: str, room_id: str, content: str) -> Tuple[bool, Optional[str], Optional[str]]:
        """返回 (ok, reason_code, sanitized_content)；不 ok 时 reason_code 即回执码"""
        if not content or not content.strip():
            return False, SendRejectReason.TOO_LONG.value, None
        if self.is_tripped(platform):
            return False, SendRejectReason.CIRCUIT_OPEN.value, None
        if not self.is_enabled(platform):
            return False, SendRejectReason.SENDER_DISABLED.value, None

        # 长度上限（清洗前检查原始长度——控制符不计入放宽）
        if len(content) > self._settings.send_max_length:
            return False, SendRejectReason.TOO_LONG.value, None

        # 控制符/换行清洗（S3-1：playwright fill 为值注入，仍需清洗防 UI 异常）
        sanitized = "".join(ch for ch in content if ch == " " or ch.isprintable()).strip()
        if not sanitized:
            return False, SendRejectReason.TOO_LONG.value, None

        # 关键词过滤（blocked_keywords 复用；F13 加严方向在 Eng 定稿清单）
        for kw in self._keywords:
            if kw and kw in sanitized:
                return False, SendRejectReason.KEYWORD_BLOCKED.value, None

        # 同内容去重窗口（R14）
        now = time.monotonic()
        window = self._settings.send_dedup_window_seconds
        digest = hashlib.sha1(sanitized.encode("utf-8")).hexdigest()[:16]
        last = self._dedup.get(digest)
        if last is not None and now - last < window:
            return False, SendRejectReason.DUPLICATE.value, None

        # 限速（F4：键口径 per-platform / per-platform_room；T6：per-platform 间隔覆写）
        key = self._rate_key(platform, room_id)
        last = self._last_send.get(key)
        min_interval = self._interval_overrides.get(platform, self._settings.send_min_interval_seconds)
        if last is not None and now - last < min_interval:
            return False, SendRejectReason.RATE_LIMITED.value, None

        return True, None, sanitized

    def _rate_key(self, platform: str, room_id: str) -> str:
        if self._settings.send_rate_key == "platform_room":
            return f"{platform}:{room_id}"
        return platform

    # ---- 发送后记账 ----

    def record_attempt(self, platform: str, room_id: str, content: str) -> None:
        """发送发起即占窗口（限速/去重键位记账——防连发穿透）"""
        now = time.monotonic()
        self._last_send[self._rate_key(platform, room_id)] = now
        digest = hashlib.sha1(content.encode("utf-8")).hexdigest()[:16]
        self._dedup[digest] = now

    def record_result(self, platform: str, status: str, detail: str = "") -> None:
        """熔断计数（R36：failed 计入；unknown 只告警；success/dry_run 清零）"""
        if status == "failed":
            streak = self._fail_streak.get(platform, 0) + 1
            self._fail_streak[platform] = streak
            if streak >= self._settings.send_circuit_threshold and not self.is_tripped(platform):
                self._tripped[platform] = True
                logger.warning(f"[send-guard] CIRCUIT_OPEN {platform}: {streak} 连续失败（{detail}）——人工重开")
        elif status == "unknown":
            logger.warning(f"[send-guard] unknown result {platform}: {detail}（不计熔断，计告警——R36）")
        else:
            self._fail_streak[platform] = 0
