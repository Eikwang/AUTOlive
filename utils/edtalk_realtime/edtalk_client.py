# -*- coding: utf-8 -*-
"""
EDTalk realtime_serve 客户端

照 AUDIO_PLAYER 模式（utils/audio_handle/audio_player.py）：HTTP 调用 + 配置驱动 +
try/except + logger。所有失败均降级处理，不阻塞直播主流程。

关键语义（EDTalk功能集成计划 §4.3 + Eng 义务）：
- 推送通道：POST /audio/push_full（16kHz int16，EMBEDDING v0.4 唯一有效通道）
- 采样率：推送前校验 wav 头，非 16kHz 自动重采样（native E3：edge-tts 默认 24kHz）；
  非 RIFF 音频（edge_tts 产物是 mp3）先 pydub 转临时 wav 再推（2026-10-25 实测修复）
- 单飞行句 + 完成判定启发式（native E1）：最短飞行时间 = duration×1.2 + buffer_depth
  回落双条件；宁可延迟不截断（过早推送会被 render_loop 的 _flush_buffer 截断上一句）
- 背压：推送协程内阻塞等待，上游播放循环天然被背压（native A2）
- 错误透传：EDTalk 错误体 {problem, cause, fix} 三段式原样记录（DX 义务②）
- 失败计数：供 webui 经 main.py API 轮询（推送健康度运营可见，native design F4）
"""
import asyncio
import base64
import io
import os
import threading
import time
import traceback
import wave

import aiohttp

from utils.my_log import logger
from utils import alarm_notifier

# 16kHz int16 单声道 = 每样本 2 字节
TARGET_RATE = 16000
# 单飞行句完成判定：最短飞行时间系数。
# 2026-11-23 pending 自动衔接：降至 0.6——客户端在上段有效语音生成完
# （produced>=total）后，仅需等上段 60% 时长即提前 push 下段为 pending，
# 下段与上段显示并行预推理；服务端严格等上段 displayed>=total 才激活下段
# （绝不提前丢有效语音尾，防截断由 seg 精确判定兜底，不依赖时间余量）。
# 下段在上段播完时已推理就绪 → 激活零推理 → 段间零间隙。旧 1.0 让客户端
# 等满上段时长才 push，此时上段已播完，pending 预推理失效（生产推理慢时
# 下段首帧延迟 = 推理时间，gap 反弹）。margin 仅控制提前量，不影响安全。
FLIGHT_MARGIN = 0.6
# buffer_depth / seg 完成度轮询间隔（2026-11-23 拖音修复：0.2→0.05，
# 让 displayed 达标后尽快放行，压缩段间空档；/status 极轻量，高频无害）。
_POLL_INTERVAL = 0.05
_POLL_DEADLINE = 1.0
# 2026-11-21 截断修复：轮询上限改为 max(固定值, 上一句时长×本系数)。
# 根因：推理慢于实时时帧锁定 pacing（'hold'）拉长真实播放时长，固定 1s
# 轮询超时强制放行 → 下一条 push 触发服务端 flush 替换 → 上一句尾字被
# 截断（用户实测「最后一个字没说完」）。正常路径 buffer 播完即回落、轮询
# 提前 break，不产生额外白等；上限仅封顶病态场景（GPU 卡死/服务异常）。
_POLL_DEADLINE_SCALE = 1.0
# 2026-11-17 热切机制批次：连接基址解析 TTL 缓存（秒）——resolve_api_base 每
# 次调用读运行时文件（磁盘 IO），push_full 属逐句音频路径，不做每请求 open；
# 端口变更（服务重启自动换端口写运行时文件/配置 api_ip_port 修改）最迟 TTL 内生效
_API_BASE_TTL_S = 60.0


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


def _ensure_wav_riff(path: str):
    """RIFF 归一：非 wav 音频（edge_tts 产物是 mp3，无 RIFF 头）→ pydub 转临时 wav。

    wave.open 直读 mp3 必抛 "file does not start with RIFF id"（2026-10-25 实测
    致数字人推送全败）；pydub 解码依赖 ffmpeg（builtin_play_center 同款既有依赖）。

    Returns:
        (wav_path, tmp_created)：RIFF 头原样返回 (path, False)；转码成功返回
        (临时 wav 路径, True)——调用方用完负责删除。
    Raises:
        Exception: 解码/转码失败——由 push_full 兜底记录，不阻塞主流程。
    """
    try:
        with open(path, "rb") as f:
            if f.read(4) == b"RIFF":
                return path, False
    except OSError:
        return path, False  # 打不开交由后续 load 抛标准错误
    from pydub import AudioSegment
    tmp = f"{os.path.splitext(path)[0]}_edtalk.wav"
    try:
        AudioSegment.from_file(path).export(tmp, format="wav")
    except Exception as e:
        raise RuntimeError(
            f"非 RIFF 音频转码失败（{type(e).__name__}: {e}）。"
            f"problem=TTS 产物非 wav 且解码转码失败；"
            f"cause=pydub/ffmpeg 缺失或源音频损坏；"
            f"fix=安装 ffmpeg 并加入 PATH，或把 TTS 输出格式改为 wav"
        ) from e
    return tmp, True


class EDTalkClient:
    """EDTalk realtime_serve HTTP 客户端（单例，挂在 Audio 类上）。"""

    def __init__(self, config_data: dict = None, config_obj=None):
        """2026-11-17 热切机制批次：连接级参数（api_ip_port/api_token）改运行期现读。

        - config_data：启动快照（edtalk_realtime 节），作 config_obj 缺失时的
          回退基线（旧签名兼容——测试/外部直调仍可只传节 dict）；
        - config_obj：共享 Config 实例（utils/config.py 类级缓存，Config.get
          实时读内存）——audio_core 注入，热重载（webui 落盘→合成链 mtime
          门控 Config.reload）后每请求取到新值，零磁盘 IO。

        连接语义说明：本客户端为 HTTP 通道（每请求独立 session，无长连 ws），
        连接地址/令牌变更即下次请求生效，无"重连"概念；enable 开关关断由
        push_full 入口实时门控（停止新发送）即时生效。
        """
        self.config_data = config_data or {}
        self._config_obj = config_obj
        # 端口解析统一走运行时文件（webui 拉起后写入，支持占用自动切换）；
        # config api_ip_port 仅作存量/测试回退（2026-10-26 配置面收敛）。
        # 构造时解析一次作初始值兼 TTL 缓存预热（运行期经 _api_base() 现读刷新）。
        from utils.edtalk_realtime.runtime import resolve_api_base
        self.api_ip_port = resolve_api_base(self.config_data)
        self._api_base_cache = self.api_ip_port
        self._api_base_at = time.monotonic()
        # 推送健康度（运营可见：webui 经 main.py /edtalk_status 轮询）
        self.failure_count = 0
        self.last_error = ""
        self.last_push_time = 0.0
        self.last_duration_s = 0.0
        # 单飞行句状态：飞行中的最短完成时刻（monotonic 秒）
        self._flight_until = 0.0
        # 推送锁：串行化推送（多消息来源防护）
        self._push_lock = asyncio.Lock()
        # pending 自动衔接（2026-11-23）：seg_id 跟踪串行。_last_seg_id 是最近
        # 一次 /status 观察到的服务端段标识；_expected_seg_id 是「下一次 wait
        # 要等其完成的段」的期望 seg_id（= 上次 push 时观察值 +1）。服务端支持
        # pending 时，wait 在上段 produced>=total 即提前放行 push 下段（pending），
        # 服务端严格等上段 displayed>=total 才激活 → 段间零间隙且不丢尾。
        self._last_seg_id = 0
        self._expected_seg_id = 0

    # ---------- 2026-11-17 热切机制批次：配置现读 ----------
    def _live_section(self) -> dict:
        """edtalk_realtime 节现读：共享 Config 内存视图优先（零 IO、热重载即见），
        回退构造快照（config_obj 缺失时的旧语义）。"""
        if self._config_obj is not None:
            try:
                section = self._config_obj.get("edtalk_realtime")
                if isinstance(section, dict):
                    return section
            except Exception:
                pass
        return self.config_data or {}

    def _api_base(self) -> str:
        """连接基址现读（TTL 缓存防每请求磁盘 IO，见 _API_BASE_TTL_S 注释）。"""
        now = time.monotonic()
        if now - self._api_base_at < _API_BASE_TTL_S:
            return self._api_base_cache
        try:
            # 局部导入（对齐 __init__ 先例）；resolve_api_base 的 legacy 回退按
            # 全量 config 形态取 edtalk_realtime.api_ip_port——包装节 dict 保持
            # 该回退对节内现读值生效
            from utils.edtalk_realtime.runtime import resolve_api_base
            base = resolve_api_base({"edtalk_realtime": self._live_section()})
        except Exception:
            base = self._api_base_cache
        self._api_base_cache = base or self._api_base_cache
        self._api_base_at = now
        return self._api_base_cache

    def _edtalk_enabled(self) -> bool:
        """enable 开关现读（helpers.is_edtalk_active 同源语义：visual_body 双键
        判定）。config_obj 缺失（旧签名/测试替身）时维持构造时行为不门控。"""
        if self._config_obj is None:
            return True
        try:
            if self._config_obj.get("visual_body") != "edtalk_realtime":
                return False
            return bool(self._config_obj.get("edtalk_realtime", "enable"))
        except Exception:
            return True

    def _auth_headers(self) -> dict:
        # 热切机制批次：api_token 现读（原读构造快照引用，热重载后失效）
        token = str((self._live_section().get("api_token") or "")).strip()
        return {"Authorization": f"Bearer {token}"} if token else {}

    async def _get_status(self, session: aiohttp.ClientSession) -> dict:
        """GET /status；失败返回 {}。"""
        try:
            async with session.get(
                f"{self._api_base()}/status", headers=self._auth_headers(), timeout=aiohttp.ClientTimeout(total=3)
            ) as resp:
                if resp.status == 200:
                    return await resp.json()
        except Exception:
            pass
        return {}

    async def _wait_flight_complete(self, session: aiohttp.ClientSession, duration_s: float):
        """完成判定启发式（Eng E1）：最短飞行时间 + buffer_depth 回落双条件。

        取舍：宁可延迟不截断——启发式超时也强制放行（避免死锁），但记录告警。
        2026-10-29 优化批次：等待构成观测日志——区分「上一句真在播」（合理
        串行等待）与「buffer 永不回落导致轮询超时」（系统性白等，需查服务端）。
        2026-11-16 段间让位批次：barge-in 打断即弃退役——飞行等待恢复完整
        语义（当前段播完才放行），插播由队列权重排序在段间完成。
        """
        t0 = time.monotonic()
        min_flight = duration_s * FLIGHT_MARGIN
        remaining = self._flight_until + min_flight - t0
        if remaining > 0:
            await asyncio.sleep(remaining)
        sleep_s = time.monotonic() - t0
        # 双条件：buffer_depth 回落（低于 preroll 视为上一句已被消费）。
        # 轮询上限随上一句时长缩放（截断修复见 _POLL_DEADLINE_SCALE 注释）。
        deadline = time.monotonic() + max(
            _POLL_DEADLINE, duration_s * _POLL_DEADLINE_SCALE)
        buffer_depth = None
        timed_out = False
        seg_done = False
        pending_early = False
        target = self._expected_seg_id
        while True:
            status = await self._get_status(session)
            if not status:
                break  # /status 不可用：不阻塞推送（降级为仅时间启发式）
            seg_id = int(status.get("seg_id", 0) or 0)
            self._last_seg_id = seg_id
            pending_supported = bool(status.get("pending_supported"))
            # 精确完成判定（2026-11-21 撕裂修复）：seg_chunk_total/produced/
            # displayed 三者齐备，产出≥总数、显示≥产出 = 该段全部产出且播放完。
            seg_total = status.get("seg_chunk_total")
            if not seg_total:
                # 无活跃语音段（空闲/首段前）：新服务端直接放行（无上段要等）。
                if pending_supported:
                    seg_done = True
                    break
            else:
                produced = status.get("seg_chunk_produced", 0)
                displayed = status.get("seg_chunk_displayed", 0)
                if seg_id >= target:
                    # 期望段（上次 push 的段）已激活：等它显示完成才放行下一段。
                    # 2026-11-21 跳帧修复：只认精确完成判定，不用深度宽松放行，
                    # 否则上段仍有已产出未显示帧时提前推下段 → flush 丢尾 → 跳帧。
                    if produced >= seg_total and displayed >= produced:
                        seg_done = True
                        break
                    if pending_supported and produced >= seg_total:
                        # 2026-11-23 pending 自动衔接：上一段已激活且有效语音
                        # 全部产出（仍在显示）→ 提前放行 push 下段为 pending。
                        # 服务端严格等上段 displayed>=total 才激活下段（防丢尾），
                        # 下段已预推理就绪 → 段间零间隙。
                        # 必须 seg_id>=target（上一段已激活）才提前：若上一段
                        # 仍是 pending 未激活（seg_id<target），此时 /status 反映
                        # 的是更早段，提前 push 会覆盖 pending 缓冲 → 丢句/乱序。
                        pending_early = True
                        break
                # seg_id < target：上一段尚未激活（仍在 pending 缓冲）→ 继续轮询
                # 等待，绝不提前放行，避免覆盖 pending 导致丢句或乱序。
            if not seg_total and not pending_supported:
                # 旧服务端无精确字段：退化到深度启发式。
                buffer_depth = status.get("buffer_depth")
                if buffer_depth is None or buffer_depth <= status.get("preroll_frames", 8):
                    break
            if time.monotonic() >= deadline:
                timed_out = True  # 轮询超时强制放行（避免死锁）
                break
            await asyncio.sleep(_POLL_INTERVAL)
        poll_s = time.monotonic() - t0 - sleep_s
        # 观测（2026-11-26 队列审查批次缺陷6）：飞行等待总时长分桶——渲染慢于
        # 音频时消费循环在此阻塞（阻塞式反压），代价转嫁为待播放队列积压、
        # 低优内容被满载规则逐出；等待持续偏大（>2×句时长）=服务端推理跟
        # 不上，需告警可见而非仅从「答谢被替换」间接推断。轮询超时=病态白等
        # （GPU 卡死/服务异常）。计数口径沿 song_gap_wait 先例（整秒计数）。
        _flight_total_s = sleep_s + poll_s
        try:
            from utils.obs_stats import record_drop
            record_drop("edtalk_flight_wait", max(1, int(round(_flight_total_s))))
        except Exception:
            pass
        if timed_out or _flight_total_s > max(2.0, duration_s * 2.0):
            logger.warning(
                f"[edtalk] 飞行等待异常：总等待 {_flight_total_s:.2f}s"
                f"（上句时长 {duration_s:.1f}s）"
                + ("，轮询超时强制放行（查服务端推理/GPU）" if timed_out else ""))
        logger.info(
            f"[edtalk-wait] 上句时长 {duration_s:.1f}s 飞行等待 {sleep_s:.2f}s "
            f"轮询 {poll_s:.2f}s buffer_depth={buffer_depth} "
            f"seg_id={self._last_seg_id} target={target}"
            + ("（句段完成判定）" if seg_done else "")
            + ("（pending 提前放行）" if pending_early else "")
            + ("（轮询超时放行）" if timed_out else "")
        )

    # ---------- 推送 ----------

    async def push_full(self, pcm_path: str, content: str = "",
                        wait_flight: bool = True) -> bool:
        """推送整句音频给 EDTalk（异步；供 playback 播放循环 await）。

        单飞行句：上一句完成判定（启发式）通过前阻塞——阻塞式背压，
        不丢弃台词。失败降级：记录 + 计数，不抛异常。

        Args:
            pcm_path: wav 文件路径（TTS 落盘产物，可能已变速）
            content: 文本内容（仅日志用）
            wait_flight: T7/d3（2026-11-05 批次）——song 切片传 False：跳过
                上一句飞行等待（切片时序锚定伴奏，人声起即停答谢=push 即
                flush 替换，d3 替换语义；服务端 _push_lock 串行+flush 兜底
                服务侧安全）。答谢/普通语音保持 True（阻塞背压不丢台词）。

        Returns:
            True = 推送成功；False = 失败（已降级记录）
        """
        async with self._push_lock:
            # 2026-11-17 热切机制批次：enable 关断即时生效——推送入口现读
            # visual_body+edtalk_realtime.enable 双键（is_edtalk_active 同源），
            # 关断后停止一切新发送（HTTP 通道无长连可断，逐句门控即"断开"语义）。
            # 上游分流（synthesis_manager/playback_manager）同样实时判定，此处为
            # 竞态窗口内已入队推送的兜底闸；关断按显式决策记 debug（非故障，
            # 不计失败计数不告警）。
            if not self._edtalk_enabled():
                logger.debug("[EDTalk] edtalk_realtime.enable=false（现读），跳过推送："
                             f"{content[:30]}")
                return False
            wav_path, tmp_created = pcm_path, False
            try:
                # RIFF 归一（2026-10-25 实测修复）：edge_tts 产物是 mp3（无 RIFF 头），
                # wave.open 直读必抛 "file does not start with RIFF id"——先转临时 wav 再加载
                wav_path, tmp_created = _ensure_wav_riff(pcm_path)
                pcm, src_rate = load_wav_as_16k_pcm(wav_path)
                if not pcm:
                    raise ValueError("音频内容为空")
                duration_s = len(pcm) / 2.0 / TARGET_RATE
                payload = base64.b64encode(pcm).decode("ascii")

                timeout = aiohttp.ClientTimeout(total=max(30, duration_s + 30))
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    if wait_flight:
                        await self._wait_flight_complete(session, self.last_duration_s)
                    async with session.post(
                        f"{self._api_base()}/audio/push_full",
                        json={"pcm_base64": payload,
                              "immediate": not wait_flight},
                        headers=self._auth_headers(),
                    ) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            self.failure_count = 0
                            self.last_error = ""
                            self.last_push_time = time.time()
                            self.last_duration_s = duration_s
                            self._flight_until = time.monotonic()
                            # pending 串行锚点：下一次 wait 等「本次 push 的段」完成。
                            # pending 模式下本次 push 可能尚未激活（服务端存缓冲），
                            # seg_id 未变 → 用 push 前观察值 +1 作为期望，下次 wait
                            # 轮询到 seg_id 达标即表示本段已激活。
                            self._expected_seg_id = self._last_seg_id + 1
                            logger.info(
                                f"EDTalk 推送成功：{content[:30]}… 时长 {duration_s:.1f}s "
                                f"(源采样率 {src_rate}Hz, processing={data.get('processing')}) "
                                f"expected_seg={self._expected_seg_id}"
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
                        self._notify_push_failure()
                        return False
            except Exception as e:
                self.failure_count += 1
                self.last_error = f"{type(e).__name__}: {e}"
                logger.error(
                    f"EDTalk 推送失败（第 {self.failure_count} 次）：{e}。"
                    f"problem=数字人服务未连接；cause={type(e).__name__}；"
                    f"fix=检查画面设置页开关与 realtime_serve 服务状态（详见 RUNBOOK §端口冲突）"
                )
                self._notify_push_failure()
                if self.failure_count == 1:
                    logger.error("EDTalk 首次推送失败：直播将无数字人口型（音频已跳过本地播放）")
                return False
            finally:
                if tmp_created:
                    try:
                        os.remove(wav_path)
                    except OSError:
                        pass

    def _notify_push_failure(self):
        """报警重构（2026-10-26）：推送失败→数字人服务报警（webhook 推送，
        60s 模块级冷却防故障期逐句刷屏）。"""
        alarm_notifier.alarm_error(
            alarm_notifier.MODULE_DIGITAL_HUMAN,
            f"EDTalk 音频推送失败（第 {self.failure_count} 次）：{self.last_error}",
            fix_hint="检查 EDTalk 实时推理服务状态（画面设置页开关与 realtime_serve，详见 RUNBOOK）")

    # ---------- 控制面（直调 HTTP，实时生效） ----------

    def flush_silence(self, duration_s: float = 0.2) -> dict:
        """打断闭嘴（2026-11-16 聊天审查批次）：推送静音整句触发服务端 flush
        替换（d3 替换语义——push 即截断上一句缓冲），数字人立即闭嘴。

        背景：音频进入口型推理的方式=整句 push——服务端只要还有音频在消费
        就持续推理口型；因此打断闭嘴的关键是让服务端缓冲被静音替换。
        （/output/pause 只冻结消费与帧位，画面定格不等于闭嘴，不采用。）
        同通道 /audio/push_full（EMBEDDING v0.4 唯一有效），同步直调绕过
        单飞行句背压——打断必须立即生效；并发安全由服务端 _push_lock 串行
        +flush 兜底。成功后同步客户端飞行启发式状态（last_duration_s/
        _flight_until），下一句真实推送不被旧句飞行等待拖延。

        Args:
            duration_s: 静音时长（秒），默认 0.2s——足够 render_loop flush
                并消费完静音闭嘴，又不占用明显服务时间

        Returns:
            dict: {"ok": bool, ...}（失败降级不抛异常，与 pause_output 同款）
        """
        try:
            import requests
            samples = max(1, int(TARGET_RATE * duration_s))
            pcm = b"\x00\x00" * samples  # int16 单声道静音
            resp = requests.post(
                f"{self._api_base()}/audio/push_full",
                json={"pcm_base64": base64.b64encode(pcm).decode("ascii"),
                      "immediate": True},
                headers=self._auth_headers(),
                timeout=5,
            )
            if resp.status_code == 200:
                # 飞行启发式与服务端缓冲同步：服务端当前缓冲=本段静音
                self.last_duration_s = float(duration_s)
                self._flight_until = time.monotonic()
                self.last_push_time = time.time()
                # pending 串行锚点同步：打断替换了服务端当前段（seg_id 前进、
                # 可能覆盖未激活 pending），刷新观察值使下一次 wait 锚定静音段。
                try:
                    st = requests.get(f"{self._api_base()}/status",
                                      headers=self._auth_headers(), timeout=3)
                    if st.status_code == 200:
                        j = st.json()
                        self._last_seg_id = int(j.get("seg_id", 0) or 0)
                        self._expected_seg_id = self._last_seg_id + 1
                except Exception:
                    pass
                logger.info(f"[EDTalk] 静音 flush 已推送（{duration_s:.1f}s，打断闭嘴）"
                            f" expected_seg={self._expected_seg_id}")
                return {"ok": True}
            return {"ok": False, "status": resp.status_code}
        except Exception as e:
            logger.warning(f"[EDTalk] 静音 flush 失败（降级忽略）：{e}")
            return {"ok": False, "error": str(e)}

    def pause_output(self) -> dict:
        """F-A/uc2b（2026-11-05 批次 T6）：任意点暂停输出（消费冻结，精确帧位
        续播）。失败降级 {"ok": False}（999 机制 2026-11-15 已退役，本方法保留
        为服务端控制面通用端点——运行时降级=按队列序播放，不阻塞入队主链）。"""
        try:
            import requests
            resp = requests.post(
                f"{self._api_base()}/output/pause",
                json={},
                headers=self._auth_headers(),
                timeout=5,
            )
            if resp.status_code == 200:
                # 2026-11-14 实测修复：服务端成功体为 {"paused": true}（无 ok 键，
                # 契约见 test_choreo_t6_yield.py）——原样透传会让 _on_edtalk_yield
                # 的 .get("ok", False) 把成功误判为失败 → 走 d17 降级分支且永不
                # resume → 服务端输出永久冻结（画面停住、口型音频不消费）。
                # 成功统一归一 ok=True，与失败 {"ok": False} 契约对称。
                return {"ok": True, **(resp.json() or {})}
            return {"ok": False, "status": resp.status_code}
        except Exception as e:
            logger.warning(f"EDTalk 暂停输出失败（降级维持排序）：{e}")
            return {"ok": False, "error": str(e)}

    def get_output_frames(self):  # -> int | None（失败 None）
        """F-A/uc2b（T7）：读 /status 的 output_frames（累计出帧数——让位冻结
        时刻的续播游标基座：consumed = pause 时读数 - push 受理时读数）。
        失败返回 None（调用方放弃精确续播=降级）。"""
        try:
            import requests
            resp = requests.get(
                f"{self._api_base()}/status",
                headers=self._auth_headers(),
                timeout=3,
            )
            if resp.status_code == 200:
                data = resp.json() or {}
                frames = data.get("output_frames")
                return int(frames) if frames is not None else None
        except Exception as e:
            logger.debug(f"EDTalk output_frames 读取失败（忽略）：{e}")
        return None

    def resume_output(self) -> dict:
        """F-A/uc2b：从冻结帧位恢复输出。失败降级 {"ok": False}（恢复失败=
        输出持续冻结——alarm_notifier 告警由调用方处置，本方法不阻塞）。"""
        try:
            import requests
            resp = requests.post(
                f"{self._api_base()}/output/resume",
                json={},
                headers=self._auth_headers(),
                timeout=5,
            )
            if resp.status_code == 200:
                # 同 pause_output 归一：服务端成功体 {"paused": false} 无 ok 键
                return {"ok": True, **(resp.json() or {})}
            return {"ok": False, "status": resp.status_code}
        except Exception as e:
            logger.error(f"EDTalk 恢复输出失败（输出将保持冻结）：{e}")
            return {"ok": False, "error": str(e)}

    def switch_segment(self, segment: str, at: str = "segment_end",
                       loop_mode: str = "loop") -> dict:
        """切换素材段（at=segment_end 边界无缝；immediate 硬切）。

        loop_mode（2026-11-24 pingpong 批次）：段循环模式——loop=锯齿折返
        （默认）；pingpong=三角波正放↔倒放无缝往返（编排主线 hold 场景，
        口型由音频帧锁定单独驱动不受倒放影响）。旧服务端忽略未知字段时
        仅退默认循环（向后兼容面）。"""
        try:
            import requests
            resp = requests.post(
                f"{self._api_base()}/segment/switch",
                json={"segment": segment, "at": at, "loop_mode": loop_mode},
                headers=self._auth_headers(),
                timeout=5,
            )
            return resp.json() if resp.status_code == 200 else {"ok": False, "status": resp.status_code}
        except Exception as e:
            logger.error(f"EDTalk 切段失败（降级跳过）：{e}")
            return {"ok": False, "error": str(e)}

    def set_lip_params(self, mode: str) -> dict:
        """口型推理门控（R-ceo2/R-eng5：off=画外音直通，下一帧生效）。

        返回 {"ok": bool, "lip_mode": str?}；失败降级 {"ok": False}（联动为
        性能层——R-eng5 失败处置=debug 日志+低频告警，不阻塞切换）。
        """
        try:
            import requests
            resp = requests.post(
                f"{self._api_base()}/lip/params",
                json={"mode": mode},
                headers=self._auth_headers(),
                timeout=3,
            )
            if resp.status_code == 200:
                return {"ok": True, **(resp.json() or {})}
            return {"ok": False, "status": resp.status_code}
        except Exception as e:
            logger.error(f"EDTalk 口型门控失败（降级跳过）：{e}")
            return {"ok": False, "error": str(e)}

    def set_repair_params(self, mode: str) -> dict:
        """面部修复热开关（2026-10-27 动作编排完善批次：编排动态设定）。

        mode ∈ on|adaptive|off；409=启动未构造（repair_capable=false）。
        失败降级 {"ok": False}（联动为性能层，不阻塞切换）。
        """
        try:
            import requests
            resp = requests.post(
                f"{self._api_base()}/repair/params",
                json={"mode": mode},
                headers=self._auth_headers(),
                timeout=3,
            )
            if resp.status_code == 200:
                return {"ok": True, **(resp.json() or {})}
            return {"ok": False, "status": resp.status_code}
        except Exception as e:
            logger.error(f"EDTalk 面部修复门控失败（降级跳过）：{e}")
            return {"ok": False, "error": str(e)}

    def set_gaze_params(self, mode: str) -> dict:
        """眼神修正热开关（2026-10-27 动作编排完善批次：编排动态设定）。

        mode ∈ on|adaptive|off；on→off 恢复原生眼（mask epoch 联动）；
        409=启动未构造（gaze_capable=false）。失败降级 {"ok": False}。
        """
        try:
            import requests
            resp = requests.post(
                f"{self._api_base()}/gaze/params",
                json={"mode": mode},
                headers=self._auth_headers(),
                timeout=3,
            )
            if resp.status_code == 200:
                return {"ok": True, **(resp.json() or {})}
            return {"ok": False, "status": resp.status_code}
        except Exception as e:
            logger.error(f"EDTalk 眼神修正门控失败（降级跳过）：{e}")
            return {"ok": False, "error": str(e)}

    def schedule(self, sequence: list, align: bool = True,
                 loop: str = "forward") -> dict:
        """下发段播放序列（/segment/schedule）。

        align=True 立即对齐 sequence[0]（编排回默认用）；False 仅装表下一边界生效。
        loop（2026-10-27 动作编排完善批次）：forward=顺序循环 |
        pingpong=首尾往返（默认画面单段模式）。
        失败降级返回 {"ok": False}，不抛异常（调用方负责重试）。
        """
        try:
            import requests
            resp = requests.post(
                f"{self._api_base()}/segment/schedule",
                json={"sequence": list(sequence), "align": bool(align),
                      "loop": loop if loop in ("forward", "pingpong") else "forward"},
                headers=self._auth_headers(),
                timeout=5,
            )
            return resp.json() if resp.status_code == 200 else {"ok": False, "status": resp.status_code}
        except Exception as e:
            logger.error(f"EDTalk 序列下发失败（降级跳过）：{e}")
            return {"ok": False, "error": str(e)}

    def get_status(self) -> dict:
        """GET /status 同步版（供 webui 控制区轮询直连）。"""
        try:
            import requests
            resp = requests.get(f"{self._api_base()}/status", headers=self._auth_headers(), timeout=3)
            return resp.json() if resp.status_code == 200 else {}
        except Exception:
            return {}

    async def wait_last_push_displayed(self) -> None:
        """等待最近一次 push 的段在服务端完整显示（2026-11-25 回执精确对齐）。

        背景（问题①根因）：push_full 返回=受理（pending 提前放行时可早于
        本段开播整整一段），完成回执在受理点即发 → 编排据此推进切段/终结
        循环，末段音频尾被截（实测末句 1-2 字缺失）。分段消费回执须以
        「服务端显示完成」为准——复用 _wait_flight_complete 既有精确判定
        （seg_id≥期望段 ∧ produced≥total ∧ displayed≥produced），其 target
        锚正是刚 push 的段。轮询超时按上句时长缩放强制放行（与飞行等待
        同款 fail-open，宁可轻微滞后不悬挂消费循环）。异常全捕获放行。"""
        try:
            timeout = aiohttp.ClientTimeout(total=max(
                30, float(self.last_duration_s or 0.0) + 30))
            async with aiohttp.ClientSession(timeout=timeout) as session:
                await self._wait_flight_complete(session, self.last_duration_s)
        except Exception as e:
            logger.debug(f"[EDTalk] 显示完成等待异常（放行）：{e}")

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
