# -*- coding: utf-8 -*-
"""
EDTalk realtime_serve 客户端

照 AUDIO_PLAYER 模式（utils/audio_handle/audio_player.py）：HTTP 调用 + 配置驱动 +
try/except + logger。所有失败均降级处理，不阻塞直播主流程。

关键语义（EDTalk功能集成计划 §4.3 + Eng 义务）：
- 推送通道：POST /audio/push_full（16kHz int16，EMBEDDING v0.4 唯一有效通道）
- 采样率：推送前校验 wav 头，非 16kHz 自动重采样（native E3：edge-tts 默认 24kHz）
- 单飞行句 + 完成判定启发式（native E1）：最短飞行时间 = duration×1.2 + buffer_depth
  回落双条件；宁可延迟不截断（过早推送会被 render_loop 的 _flush_buffer 截断上一句）
- 背压：推送协程内阻塞等待，上游播放循环天然被背压（native A2）
- 错误透传：EDTalk 错误体 {problem, cause, fix} 三段式原样记录（DX 义务②）
- 失败计数：供 webui 经 main.py API 轮询（推送健康度运营可见，native design F4）
"""
import asyncio
import base64
import io
import time
import traceback
import wave

import aiohttp

from utils.my_log import logger

# 16kHz int16 单声道 = 每样本 2 字节
TARGET_RATE = 16000
# 单飞行句完成判定：最短飞行时间系数（duration × 1.2）
FLIGHT_MARGIN = 1.2


def _resample_pcm(pcm_bytes: bytes, width: int, channels: int, rate: int) -> bytes:
    """任意采样率 int16 PCM → 16kHz（线性插值重采样，numpy 实现）。

    Args:
        pcm_bytes: 原始 PCM 字节（int16）
        width: 样本宽度（字节），仅支持 2（int16）
        channels: 声道数，>1 时取第一声道
        rate: 原始采样率

    Returns:
        16kHz int16 单声道 PCM 字节
    """
    import numpy as np

    samples = np.frombuffer(pcm_bytes, dtype=np.int16)
    if channels > 1:
        samples = samples[::channels]
    if rate == TARGET_RATE:
        return samples.tobytes()
    # 线性插值重采样
    duration = len(samples) / float(rate)
    target_len = int(duration * TARGET_RATE)
    if target_len <= 0 or len(samples) == 0:
        return b""
    x_old = np.linspace(0.0, len(samples) - 1, num=len(samples))
    x_new = np.linspace(0.0, len(samples) - 1, num=target_len)
    resampled = np.interp(x_new, x_old, samples.astype(np.float64))
    return resampled.astype(np.int16).tobytes()


def load_wav_as_16k_pcm(pcm_path: str):
    """读取 wav 文件并转换为 16kHz int16 单声道 PCM。

    Returns:
        (pcm_bytes, actual_rate)；actual_rate 为源采样率（用于日志）。
    Raises:
        ValueError: 非 wav 文件 / 非整型 PCM 编码等不可转换情形。
    """
    with wave.open(pcm_path, "rb") as wf:
        rate = wf.getframerate()
        channels = wf.getnchannels()
        width = wf.getsampwidth()
        if width != 2:
            raise ValueError(
                f"音频样本宽度 {width} 字节（需 2 字节 int16）。"
                f"problem=音频编码不受支持；cause=TTS 引擎输出了非 PCM 编码；"
                f"fix=更换 TTS 输出格式为 wav(PCM) 或检查 TTS 配置"
            )
        pcm = wf.readframes(wf.getnframes())
    if rate != TARGET_RATE or channels > 1:
        pcm = _resample_pcm(pcm, width, channels, rate)
    return pcm, rate


class EDTalkClient:
    """EDTalk realtime_serve HTTP 客户端（单例，挂在 Audio 类上）。"""

    def __init__(self, config_data: dict):
        self.config_data = config_data or {}
        self.api_ip_port = self.config_data.get("api_ip_port", "http://127.0.0.1:8000")
        # 推送健康度（运营可见：webui 经 main.py /edtalk_status 轮询）
        self.failure_count = 0
        self.last_error = ""
        self.last_push_time = 0.0
        self.last_duration_s = 0.0
        # 单飞行句状态：飞行中的最短完成时刻（monotonic 秒）
        self._flight_until = 0.0
        # 推送锁：串行化推送（多消息来源防护）
        self._push_lock = asyncio.Lock()

    # ---------- 内部工具 ----------

    def _auth_headers(self) -> dict:
        token = (self.config_data.get("api_token") or "").strip()
        return {"Authorization": f"Bearer {token}"} if token else {}

    async def _get_status(self, session: aiohttp.ClientSession) -> dict:
        """GET /status；失败返回 {}。"""
        try:
            async with session.get(
                f"{self.api_ip_port}/status", headers=self._auth_headers(), timeout=aiohttp.ClientTimeout(total=3)
            ) as resp:
                if resp.status == 200:
                    return await resp.json()
        except Exception:
            pass
        return {}

    async def _wait_flight_complete(self, session: aiohttp.ClientSession, duration_s: float):
        """完成判定启发式（Eng E1）：最短飞行时间 + buffer_depth 回落双条件。

        取舍：宁可延迟不截断——启发式超时也强制放行（避免死锁），但记录告警。
        """
        min_flight = duration_s * FLIGHT_MARGIN
        remaining = self._flight_until + min_flight - time.monotonic()
        if remaining > 0:
            await asyncio.sleep(remaining)
        # 双条件：buffer_depth 回落（低于 preroll 视为上一句已被消费）
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            status = await self._get_status(session)
            if not status:
                break  # /status 不可用：不阻塞推送（降级为仅时间启发式）
            buffer_depth = status.get("buffer_depth")
            if buffer_depth is None or buffer_depth <= status.get("preroll_frames", 8):
                break
            await asyncio.sleep(0.2)

    # ---------- 推送 ----------

    async def push_full(self, pcm_path: str, content: str = "") -> bool:
        """推送整句音频给 EDTalk（异步；供 playback 播放循环 await）。

        单飞行句：上一句完成判定（启发式）通过前阻塞——阻塞式背压，
        不丢弃台词。失败降级：记录 + 计数，不抛异常。

        Args:
            pcm_path: wav 文件路径（TTS 落盘产物，可能已变速）
            content: 文本内容（仅日志用）

        Returns:
            True = 推送成功；False = 失败（已降级记录）
        """
        async with self._push_lock:
            try:
                pcm, src_rate = load_wav_as_16k_pcm(pcm_path)
                if not pcm:
                    raise ValueError("音频内容为空")
                duration_s = len(pcm) / 2.0 / TARGET_RATE
                payload = base64.b64encode(pcm).decode("ascii")

                timeout = aiohttp.ClientTimeout(total=max(30, duration_s + 30))
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    await self._wait_flight_complete(session, self.last_duration_s)
                    async with session.post(
                        f"{self.api_ip_port}/audio/push_full",
                        json={"pcm_base64": payload},
                        headers=self._auth_headers(),
                    ) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            self.failure_count = 0
                            self.last_error = ""
                            self.last_push_time = time.time()
                            self.last_duration_s = duration_s
                            self._flight_until = time.monotonic()
                            logger.info(
                                f"EDTalk 推送成功：{content[:30]}… 时长 {duration_s:.1f}s "
                                f"(源采样率 {src_rate}Hz, processing={data.get('processing')})"
                            )
                            return True
                        # EDTalk 错误体三段式透传（detail 内 {problem,cause,fix}）
                        try:
                            detail = (await resp.json()).get("detail", {})
                            if isinstance(detail, dict):
                                logger.error(
                                    f"EDTalk 推送失败[{resp.status}]：{detail.get('problem')} | "
                                    f"原因：{detail.get('cause')} | 修法：{detail.get('fix')}"
                                )
                                self.last_error = str(detail)
                            else:
                                raise ValueError("非结构化错误体")
                        except Exception:
                            self.last_error = f"HTTP {resp.status}"
                            logger.error(f"EDTalk 推送失败：HTTP {resp.status}")
                        self.failure_count += 1
                        return False
            except Exception as e:
                self.failure_count += 1
                self.last_error = f"{type(e).__name__}: {e}"
                logger.error(
                    f"EDTalk 推送失败（第 {self.failure_count} 次）：{e}。"
                    f"problem=数字人服务未连接；cause={type(e).__name__}；"
                    f"fix=检查画面设置页开关与 realtime_serve 服务状态（详见 RUNBOOK §端口冲突）"
                )
                if self.failure_count == 1:
                    logger.error("EDTalk 首次推送失败：直播将无数字人口型（音频已跳过本地播放）")
                return False

    # ---------- 控制面（直调 HTTP，实时生效） ----------

    def switch_segment(self, segment: str, at: str = "segment_end") -> dict:
        """切换素材段（at=segment_end 边界无缝；immediate 硬切）。"""
        try:
            import requests
            resp = requests.post(
                f"{self.api_ip_port}/segment/switch",
                json={"segment": segment, "at": at},
                headers=self._auth_headers(),
                timeout=5,
            )
            return resp.json() if resp.status_code == 200 else {"ok": False, "status": resp.status_code}
        except Exception as e:
            logger.error(f"EDTalk 切段失败（降级跳过）：{e}")
            return {"ok": False, "error": str(e)}

    def get_status(self) -> dict:
        """GET /status 同步版（供 webui 控制区轮询直连）。"""
        try:
            import requests
            resp = requests.get(f"{self.api_ip_port}/status", headers=self._auth_headers(), timeout=3)
            return resp.json() if resp.status_code == 200 else {}
        except Exception:
            return {}

    def health_snapshot(self) -> dict:
        """推送健康度快照（供 main.py /edtalk_status 端点返回）。"""
        return {
            "failure_count": self.failure_count,
            "last_error": self.last_error,
            "last_push_time": self.last_push_time,
            "last_duration_s": self.last_duration_s,
        }


# 推送健康度全局快照（main.py 运行时进程内单例；web_server /edtalk_status 读取）
edtalk_client_holder = {"client": None}


def register_client(client: EDTalkClient) -> None:
    """注册客户端实例（Audio 初始化时调用），供健康度端点读取。"""
    edtalk_client_holder["client"] = client


def get_registered_client() -> "EDTalkClient | None":
    return edtalk_client_holder["client"]
