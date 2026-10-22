# -*- coding: UTF-8 -*-
"""
LLM 通道熔断器（基础功能版块 2026-10-22 批次，native CEO F3 + ENG E-3）

作用：LLM 服务故障时，避免每条弹幕都等待完整超时（60s）——连续失败达阈值后
开路（直接走托底，不发 LLM 请求），开路期满放行一次探测请求，成功后闭合。

并发模型（ENG E-3）：per-channel 计数器与状态机由 threading.Lock 保护
（仓库 data_lock 先例）；half-open 用原子"探测中"标志仅放行一个探测请求，
其余直接走托底，防惊群叠加停顿。
"""
import threading
import time

from utils.base import logger

# 熔断参数（模块顶部常量，可调——DX medium）
BREAKER_FAILURE_THRESHOLD = 3      # 连续失败 N 次后开路
BREAKER_OPEN_SECONDS = 120         # 开路时长（秒），期满进入 half-open 探测


class LLMBreaker:
    """单通道熔断器状态机（per-channel 实例，通道间不互扰）。"""

    def __init__(self, channel: str):
        self.channel = channel
        self._lock = threading.Lock()
        self._consecutive_failures = 0
        self._opened_at = 0.0            # 开路起始时刻（monotonic）；0=闭合态
        self._probing = False            # half-open 原子"探测中"标志

    def allow_request(self) -> bool:
        """是否放行真实 LLM 请求。

        Returns:
            bool: True=放行（闭合态或 half-open 探测位抢到）；False=开路中，直接走托底
        """
        with self._lock:
            if self._opened_at == 0.0:
                return True
            elapsed = time.monotonic() - self._opened_at
            if elapsed < BREAKER_OPEN_SECONDS:
                return False
            # half-open：仅放行一个探测请求（原子标志防惊群）
            if self._probing:
                return False
            self._probing = True
            return True

    def record_success(self):
        """请求成功：复位为闭合态。"""
        with self._lock:
            was_open = self._opened_at != 0.0
            self._consecutive_failures = 0
            self._opened_at = 0.0
            self._probing = False
        if was_open:
            logger.info(
                f"BREAKER_OPEN 闭合：LLM 通道 {self.channel} 探测成功，恢复正常直连")

    def record_failure(self):
        """请求失败：计数累加，达阈值开路。"""
        with self._lock:
            self._consecutive_failures += 1
            if self._opened_at != 0.0:
                # half-open 探测失败：重置开路计时，继续开一个周期（ENG E-3/DX CAP6-7）
                logger.warning(
                    f"BREAKER_OPEN half-open 探测失败：LLM 通道 {self.channel}，"
                    f"重置开路计时 {BREAKER_OPEN_SECONDS}s（继续直走托底）")
                self._opened_at = time.monotonic()
                self._probing = False
                return
            if self._consecutive_failures >= BREAKER_FAILURE_THRESHOLD:
                self._opened_at = time.monotonic()
                self._probing = False
                logger.warning(
                    f"BREAKER_OPEN 开路：LLM 通道 {self.channel} 连续 "
                    f"{self._consecutive_failures} 次失败，{BREAKER_OPEN_SECONDS}s 内直走托底")
