# -*- coding: utf-8 -*-
"""
阈值定标器（2026-11-26 二期 B3，队列审查缺陷5）。

职责：从运行时观测分位（obs_stats）推导关键阈值的「建议值」，与当前生效值
（缺省常量/配置覆盖）对比输出定标报告日志——**只建议不自动改行为**（安全
阈值自动漂移有风险，人工确认后经 config 覆盖键生效）。

定标依据：
- play_audio.reply_gate_timeout_s（配对门超时，缺省 15s）
    = LLM P95 + TTS P95 × 2（回复通常 1-3 段，取 2 段代表值）+ 5s 排队余量
- play_audio.reply_gate_extend_s（分级续等窗，缺省 10s）
    = 建议超时的 50%（单轮续等覆盖半个阈值窗口的长尾）
- filter.reply_group_active_ttl_s（S1/S2 组活跃 TTL，缺省 300s）
    = TTS P95 × 1.2 × 切分段数峰值（组最长消费周期=逐段合成间隔上界×段数）

数据不足（样本 <10）时跳过对应建议并说明——冷启动期报告只有当前值。

周期执行：task_scheduler.run_schedule 主循环每秒 tick 处以 600s 节流调用
maybe_run_calibration()；启动后首个报告延迟 300s（等首批观测样本积累）。
"""
import threading
import time

from utils.my_log import logger

_REPORT_INTERVAL_S = 600.0   # 报告节流（调度循环 tick 调用）
_FIRST_DELAY_S = 300.0       # 启动后首报延迟（等观测样本积累）
_MIN_SAMPLES = 10            # 分位数最小样本数

_lock = threading.Lock()
_last_report_mono = 0.0
_started_mono = time.monotonic()


def _pctl_or_none(info, key):
    """obs 分位安全读取（键缺失/None/样本不足 → None）。"""
    try:
        blk = info.get(key) or {}
        if blk.get("n", 0) < _MIN_SAMPLES:
            return None
        return blk.get("p95_s")
    except Exception:
        return None


def recommended_reply_gate_timeout_s(info):
    """配对门超时建议值（秒）：LLM P95 + TTS P95×2 + 5s 余量；数据不足返回 None。"""
    llm = _pctl_or_none(info, "llm_synth")
    tts = _pctl_or_none(info, "tts_synth")
    if llm is None or tts is None:
        return None
    return round(llm + tts * 2.0 + 5.0, 1)


def recommended_reply_gate_extend_s(info, timeout_rec):
    """续等窗建议值（秒）= 建议超时的 50%；无建议超时返回 None。"""
    if timeout_rec is None or timeout_rec <= 0:
        return None
    return round(timeout_rec * 0.5, 1)


def recommended_group_active_ttl_s(info):
    """S1/S2 组活跃 TTL 建议值（秒）= TTS P95 × 1.2 × 切分段数峰值；数据不足
    （TTS 无样本或段数峰值为 0）返回 None。"""
    tts = _pctl_or_none(info, "tts_synth")
    total_max = info.get("split_total_max") or 0
    if tts is None or total_max <= 0:
        return None
    return round(tts * 1.2 * total_max, 1)


def _cfg_float(config, section, key, default):
    """配置覆盖读侧（字符串落盘容错；非法/≤0 → default）。"""
    try:
        v = float(config.get(section, key))
        return v if v > 0 else default
    except Exception:
        return default


def run_calibration(config):
    """输出一次定标报告（异常全兜底——定标不得影响调度主链）。"""
    try:
        from utils.obs_stats import snapshot as _obs_snapshot
        info = _obs_snapshot() or {}
    except Exception:
        return
    tts_p95 = _pctl_or_none(info, "tts_synth")
    llm_p95 = _pctl_or_none(info, "llm_synth")

    t_rec = recommended_reply_gate_timeout_s(info)
    x_rec = recommended_reply_gate_extend_s(info, t_rec)
    ttl_rec = recommended_group_active_ttl_s(info)

    t_cur = x_cur = ttl_cur = None
    if config is not None:
        t_cur = _cfg_float(config, "play_audio", "reply_gate_timeout_s", 15.0)
        x_cur = _cfg_float(config, "play_audio", "reply_gate_extend_s", 10.0)
        ttl_cur = _cfg_float(config, "filter", "reply_group_active_ttl_s", 300.0)

    drops = info.get("drops") or {}
    gate_timeout = drops.get("read_reply_gate_timeout", 0)
    gate_extend = drops.get("read_reply_gate_extend", 0)
    depth = info.get("queue_depth") or {}

    logger.info(
        "[阈值定标] "
        f"样本：LLM P95={llm_p95}（{'有' if llm_p95 is not None else '不足'}）/"
        f"TTS P95={tts_p95}（{'有' if tts_p95 is not None else '不足'}）/"
        f"段数峰值={info.get('split_total_max', 0)}；"
        f"配对门超时建议={t_rec}（当前 {t_cur}）/续等窗建议={x_rec}（当前 {x_cur}）/"
        f"组TTL建议={ttl_rec}（当前 {ttl_cur}）；"
        f"越界计数：配对门超时={gate_timeout}/续等={gate_extend}/"
        f"TTL失效={drops.get('group_active_ttl_lapsed', 0)}；"
        f"队列深度峰值：{depth}；"
        f"覆盖键=play_audio.reply_gate_timeout_s/reply_gate_extend_s、"
        f"filter.reply_group_active_ttl_s（>0 生效，缺省走内置值）"
    )


def maybe_run_calibration(config):
    """调度循环 tick 钩子（600s 节流 + 启动 300s 延迟）。异常全吞。"""
    global _last_report_mono
    try:
        now = time.monotonic()
        if now - _started_mono < _FIRST_DELAY_S:
            return
        with _lock:
            if now - _last_report_mono < _REPORT_INTERVAL_S:
                return
            _last_report_mono = now
        run_calibration(config)
    except Exception:
        pass
