# -*- coding: UTF-8 -*-
"""
运行时观测统计单点（T1 观测面补齐包，/autoplan main-20261031-201249 终门批准批次）。

职责：跨 handler 的轻量线程安全计数器注册表——队列丢弃分桶、消费延迟样本、
TTS 合成吞吐、编排静默窗口等待。全部写入点各自兜底（观测不得影响主链），
读取统一 snapshot()（供 get_audio_info 叠加与后续 D-1 常驻状态条消费）。

丢弃分桶口径（P-1，docs/架构审查报告-2026-10-31.md 四、精准维）：
- message_queue_full    消息队列满载丢弃（低优/未识别类型插入位在末尾）
- message_queue_evict   消息队列满载插队驱逐队尾（最低优组 FIFO 尾）
- unknown_type_dropped  音频队列未识别类型满载丢弃
- audio_999_rejected    999 单实例约束舍弃新 999
- audio_rule4_dropped   音频队列四规则之规则4（满且新权重更低）舍弃
- audio_same_tier_evict 音频队列同权重组首取代/高优插入驱逐最低优组首
- talk_discard          连续对话待播舍弃（只播最新一条）
- dedup_dropped         限定时间去重丢弃（入口即弃）
- throttle_merged       限入窗口合并暂缓（**非丢弃**：窗口末补发最新 reserve_num-1 条，
                        计数语义=未即时分发的消息量，与丢弃分桶并列透出）
"""
import threading
import time
import traceback
from collections import deque

from utils.my_log import logger

_LATENCY_WINDOW_MAX = 200      # 消费延迟环形窗（条）
_TTS_WINDOW_MAX = 200          # 合成耗时环形窗（条）

_lock = threading.Lock()
_drop_counts = {}              # bucket -> int
_drop_total = 0
_latency_samples = deque(maxlen=_LATENCY_WINDOW_MAX)   # 秒（float）
_tts_samples = deque(maxlen=_TTS_WINDOW_MAX)           # {"s": 耗时秒, "ok": bool}
_choreo_mute_events = deque(maxlen=50)                 # {"waited_s": ...}（超 60s 打点事件）
_started_at = time.time()


def record_drop(bucket: str, n: int = 1):
    """丢弃/合并事件计数（分桶见模块 docstring）。观测失败不外抛。"""
    global _drop_total
    try:
        with _lock:
            _drop_counts[bucket] = _drop_counts.get(bucket, 0) + int(n)
            _drop_total += int(n)
    except Exception:
        pass  # 观测写入永不影响主链（对齐 report_consumed 兜底语义）


def record_latency(seconds: float):
    """消费延迟样本（入队→播放完成）。"""
    try:
        with _lock:
            _latency_samples.append(round(float(seconds), 3))
    except Exception:
        pass


def record_tts(seconds: float, ok: bool):
    """TTS 合成耗时样本（成败分计，吞吐=样本率推演）。"""
    try:
        with _lock:
            _tts_samples.append({"s": round(float(seconds), 3), "ok": bool(ok)})
    except Exception:
        pass


def record_choreo_mute(waited_s: float):
    """编排静默窗口超阈值等待事件（>60s 打点，F6a 量化观测）。"""
    try:
        with _lock:
            _choreo_mute_events.append({"waited_s": round(float(waited_s), 1),
                                        "ts": round(time.time(), 3)})
    except Exception:
        pass


def _pctl(samples, q: float):
    """环形窗样本分位数（样本不足返回 None）。"""
    if not samples:
        return None
    data = sorted(samples)
    idx = min(len(data) - 1, max(0, int(len(data) * q)))
    return data[idx]


def snapshot(include_gpu: bool = False) -> dict:
    """观测快照（obs 全量一次读齐；include_gpu 时现查 GPU——查询有子进程成本，
    仅供低频面板/诊断调用，不入热路径）。"""
    try:
        with _lock:
            drops = dict(_drop_counts)
            lat = list(_latency_samples)
            tts = list(_tts_samples)
            mute = list(_choreo_mute_events)
        info = {
            "drops": drops,
            "drop_total": sum(drops.values()),
            "consumed_latency_s": {
                "n": len(lat),
                "p50": _pctl(lat, 0.50),
                "p95": _pctl(lat, 0.95),
                "max": (max(lat) if lat else None),
            },
            "tts_synth": {
                "n": len(tts),
                "ok_n": sum(1 for x in tts if x.get("ok")),
                "p50_s": _pctl([x.get("s", 0) for x in tts], 0.50),
                "p95_s": _pctl([x.get("s", 0) for x in tts], 0.95),
            },
            "choreo_mute_over_60s": len(mute),
            "choreo_mute_last": (mute[-1] if mute else None),
            "uptime_s": round(time.time() - _started_at, 1),
        }
        if include_gpu:
            try:
                from utils.gpu_policy import gpu_snapshot
                info["gpu"] = gpu_snapshot()
            except Exception as e:
                info["gpu"] = {"error": str(e)}
        return info
    except Exception:
        logger.debug(f"[obs] 观测快照异常（忽略）：{traceback.format_exc()}")
        return {"error": "snapshot_failed"}
