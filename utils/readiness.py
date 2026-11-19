# -*- coding: UTF-8 -*-
"""
启动就绪门控注册表（2026-11-19 启动就绪门控批次）

问题背景：启动快模块（定时任务/闲时任务/动态文案/弹幕监听）在慢模块
（GPT-SoVITS / 数字人 EDTalk）未就绪时已开始执行，产生的任务无法正确处理，
控制台报错。本模块提供轻量就绪注册表：慢模块就绪点 mark_ready()，快模块
线程入口 wait_ready() 阻塞等待——就绪前不产生任务（门控阻断源头，无需
额外报错降级处理）。

键集合按 config 启用状态动态确定（required_keys）——未启用的服务不设键，
不卡启动：
- "gsv"：音频合成 TTS 通道为 gpt_sovits（audio_synthesis_type）。backend=inproc
  进程内引擎在模型加载完成（ST_READY）处放行（gpt_sovits_inproc._load，
  warmup 完成处幂等二次置位）；backend=api 外部通道在健康检查首次探测成功处
  放行（main.py 探测线程）。
- "edtalk"：数字人 EDTalk 启用（visual_body=edtalk_realtime 且
  edtalk_realtime.enable——is_edtalk_active 同源双键判定）。realtime_serve
  /status 首次探测成功处放行（main.py 探测线程；EDTalk 客户端为 HTTP 通道
  无长连，"连接成功"=首次探测成功）。
- RVC 变声不设键：变声为音频播放流后处理（rvc_vc.enabled 控制），无独立
  就绪判定（进程在线≠变声流在跑），且非快模块任务产出的依赖。

超时兜底 STARTUP_READY_TIMEOUT_S：单服务挂死（模型加载失败/外部服务永不
可达）时 wait_ready 超时返回 False 并一次性告警，调用方强制继续执行——
避免任何单服务把快模块永久卡死。
"""
import threading
import time
from typing import Dict, Iterable, Optional, Tuple

from utils.my_log import logger

# 超时兜底（秒）：GSV 模型冷加载分钟级 + EDTalk realtime_serve 拉起等待，
# 180s 覆盖正常启动窗口；超过即视为异常，强制放行（一次性告警）。
STARTUP_READY_TIMEOUT_S = 180

_lock = threading.Lock()
_events: Dict[str, threading.Event] = {}


def _event(key: str) -> threading.Event:
    with _lock:
        ev = _events.get(key)
        if ev is None:
            ev = threading.Event()
            _events[key] = ev
        return ev


def mark_ready(key: str) -> None:
    """标记键就绪（幂等——重复置位无害，首次置位打一条诊断日志）。"""
    ev = _event(key)
    if not ev.is_set():
        logger.info(f"[readiness] 慢模块就绪：{key}")
    ev.set()


def is_ready(key: str) -> bool:
    """键是否已就绪（非阻塞查询）。"""
    return _event(key).is_set()


def readiness_state() -> Dict[str, bool]:
    """诊断快照：全部已登记键的就绪布尔（启动诊断友好）。"""
    with _lock:
        return {k: ev.is_set() for k, ev in _events.items()}


def wait_ready(keys: Iterable[str], timeout_s: Optional[float] = None) -> bool:
    """阻塞等待全部键就绪（AND 语义）——快模块线程入口调用。

    快模块线程均为 daemon 工作线程，阻塞等待不卡主线程；mark_ready 在其它
    线程（GSV 加载/探测、EDTalk 探测）完成置位，无循环等待。

    日志契约（一次性三条）：等待开始 / 全部就绪放行 / 超时兜底。

    Args:
        keys: 就绪键集合（required_keys() 产出；空集合立即放行）。
        timeout_s: 超时兜底秒数；None 用 STARTUP_READY_TIMEOUT_S。

    Returns:
        True = 全部就绪；False = 超时兜底（已一次性告警，调用方强制继续）。
    """
    key_list = [str(k) for k in (keys or []) if str(k)]
    if not key_list:
        return True
    timeout = STARTUP_READY_TIMEOUT_S if timeout_s is None else timeout_s
    logger.info(f"[readiness] 启动门控等待就绪：{key_list}（超时兜底 {timeout}s）")
    deadline = time.monotonic() + timeout
    while True:
        missing = [k for k in key_list if not _event(k).is_set()]
        if not missing:
            logger.info(f"[readiness] 全部就绪，放行：{key_list}")
            return True
        if time.monotonic() >= deadline:
            logger.warning(
                f"[readiness] 等待就绪超时兜底（{timeout}s），强制放行继续执行"
                f"——未就绪键 {missing}（单服务挂死不阻断启动；请检查对应服务状态）")
            return False
        time.sleep(0.25)


def _cfg_get(cfg, *keys):
    """Config 实例（.get(*keys) 多参嵌套）与普通 dict（逐层下钻）双形态读取。"""
    if cfg is None:
        return None
    try:
        if isinstance(cfg, dict):
            node = cfg
            for k in keys:
                if not isinstance(node, dict):
                    return None
                node = node.get(k)
            return node
        return cfg.get(*keys)
    except Exception:
        return None


def _resolve_config(config=None):
    """required_keys 的配置来源：显式传参优先；None=共享 Config 类级缓存
    （utils.config.Config.config 全量 dict，主进程启动后恒已加载）。"""
    if config is not None:
        return config
    try:
        from utils.config import Config
        return Config.config
    except Exception:
        return None


def _edtalk_active(cfg) -> bool:
    """EDTalk 启用判定（utils/edtalk_realtime/helpers.is_edtalk_active 同源
    双键：visual_body=edtalk_realtime 且 edtalk_realtime.enable）。"""
    if _cfg_get(cfg, "visual_body") != "edtalk_realtime":
        return False
    return bool(_cfg_get(cfg, "edtalk_realtime", "enable"))


def required_keys(config=None) -> Tuple[str, ...]:
    """按当前 config 启用状态动态确定就绪键集合（wait 前计算）。

    Args:
        config: Config 实例 / 普通 dict / None（读共享 Config 类级缓存）。

    Returns:
        就绪键元组（可能为空——无慢模块启用时快模块零等待直通）。
    """
    cfg = _resolve_config(config)
    keys = []
    # gsv：音频合成 TTS 通道含 gpt_sovits（inproc / 外部 api 均需引擎/服务就绪）
    if str(_cfg_get(cfg, "audio_synthesis_type") or "").strip() == "gpt_sovits":
        keys.append("gsv")
    # edtalk：数字人 EDTalk 启用（双键同源判定）
    if _edtalk_active(cfg):
        keys.append("edtalk")
    # RVC 变声不设键（模块头说明：音频流后处理，无独立就绪判定且非任务产出依赖）
    return tuple(keys)
