# -*- coding: UTF-8 -*-
"""
ASR 引擎适配层（T10 语音对话链接通，2026-10-24；四轮用户澄清后重写）

语音对话链的「音频→文字」段。**对齐项目既有 ASR 实现**
（apps/gpt-sovits/tools/asr/{funasr_asr,fasterwhisper_asr}.py，训练标注链
同款）：本地模型优先（apps/gpt-sovits/tools/asr/models/，零下载）、
HF_ENDPOINT 镜像、need_lang 语言路由。

语言路由（2026-10-25 重构：按「基础功能-通用配置-回复语言」(need_lang) 一次定模型，
不再"whisper 先转写→检测 zh→转发 FunASR"的冗余双模型链——白耗一次推理延时）：
- need_lang='zh'/'yue' → 达摩 FunASR 直转（paraformer-large / UniASR 粤语）
- 'en'/'jp'/'ko' → faster_whisper 指定语言直转；'none'（所有）→ faster_whisper
  自动检测（talk.faster_whisper.language 配置仍作为 whisper 语言提示）

模型资产（apps/gpt-sovits/tools/asr/models/，2026-10-24 盘点）：
- faster-whisper-large-v3-turbo-ct2（本地 ct2 模型——talk.model_size 配置
  的 'small' 被本地探测胜出，零下载）
- speech_paraformer-large（中文）/speech_fsmn_vad/punc_ct-transformer
"""
import os
import threading
from typing import Optional

import numpy as np

from utils.my_log import logger

# HF 镜像（fasterwhisper_asr.py:5 同款——国内下载可达性）
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

_APP_ASR_ROOT = os.path.join("apps", "gpt-sovits", "tools", "asr")
_APP_ASR_MODELS = os.path.join(_APP_ASR_ROOT, "models")

_ENGINE_SINGLETON = {}
_ENGINE_LOCK = threading.Lock()


def _local_fw_model_dir() -> Optional[str]:
    """探测本地 faster-whisper 模型目录（apps/.../models/faster-whisper-*）。"""
    if not os.path.isdir(_APP_ASR_MODELS):
        return None
    for name in sorted(os.listdir(_APP_ASR_MODELS)):
        if name.startswith("faster-whisper-"):
            path = os.path.join(_APP_ASR_MODELS, name)
            if os.path.isdir(path):
                return path
    return None


def _get_faster_whisper(config):
    """faster_whisper 模型懒加载单例——本地模型优先（零下载），缺失时按配置下载。"""
    key = "faster_whisper"
    with _ENGINE_LOCK:
        if key in _ENGINE_SINGLETON:
            return _ENGINE_SINGLETON[key]
        from faster_whisper import WhisperModel
        local_dir = _local_fw_model_dir()
        if local_dir:
            model_path = local_dir
            logger.info(f"[asr] 使用本地 faster_whisper 模型：{local_dir}")
        else:
            model_size = str(config.get("talk", "faster_whisper", "model_size") or "small")
            model_path = model_size  # HF 下载（镜像已设）
            logger.info(f"[asr] 本地模型缺失，从镜像下载 faster_whisper：{model_size}")
        device = str(config.get("talk", "faster_whisper", "device") or "cuda")
        compute_type = str(config.get("talk", "faster_whisper", "compute_type") or "float16")
        model = WhisperModel(model_path, device=device, compute_type=compute_type)
        _ENGINE_SINGLETON[key] = model
        return model


def _funasr_model_paths(language: str) -> dict:
    """FunASR 模型路径解析（funasr_asr.py create_model 同款选择逻辑，绝对路径优先）。

    Returns:
        {"model":…, "vad":…, "punc":…}（绝对路径或 HF id；vad/punc 可为 None——粤语无 VAD）。
    """
    def _prefer_local(hf_id: str) -> str:
        name = hf_id.split("/")[-1]
        local = os.path.join(_APP_ASR_MODELS, name)
        return local if os.path.isdir(local) else hf_id

    if language == "zh":
        return {
            "model": _prefer_local(
                "iic/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-pytorch"),
            "vad": _prefer_local("iic/speech_fsmn_vad_zh-cn-16k-common-pytorch"),
            "punc": _prefer_local("iic/punc_ct-transformer_zh-cn-common-vocab272727-pytorch"),
        }
    if language == "yue":
        # UniASR 粤语（funasr_asr.py：无 VAD——带 VAD 会 shape 报错）
        return {
            "model": _prefer_local(
                "iic/speech_UniASR_asr_2pass-cantonese-CHS-16k-common-vocab1468-tensorflow1-online"),
            "vad": None, "punc": None,
        }
    return {}


def _get_funasr(language: str):
    """FunASR 模型懒加载单例（按语言缓存——paraformer/UniASR 双模型不互斥）。"""
    key = f"funasr_{language}"
    with _ENGINE_LOCK:
        if key in _ENGINE_SINGLETON:
            return _ENGINE_SINGLETON[key]
        from funasr import AutoModel
        paths = _funasr_model_paths(language)
        logger.info(f"[asr] 加载 FunASR 模型：{language} ← {paths['model']}")
        model = AutoModel(
            model=paths["model"],
            vad_model=paths["vad"],
            punc_model=paths["punc"],
            disable_update=True,
        )
        _ENGINE_SINGLETON[key] = model
        return model


def _transcribe_funasr(wav_path: str, language: str) -> str:
    model = _get_funasr(language)
    # 修复（2026-10-25 实测批次）：不传 cache——funasr 1.4.12 的 AutoModel.generate
    # 对 cache 参数做 len() 检查，传 cache=False 抛 "object of type 'bool' has no
    # len()" 致中文识别必败；参考实现 funasr_asr.py 即 generate(input=...) 直调
    res = model.generate(input=wav_path)
    if not res or not isinstance(res, list):
        return ""
    return str((res[0] or {}).get("text", "") or "").strip()


def _transcribe_faster_whisper(wav_path: str, config, language: Optional[str]):
    model = _get_faster_whisper(config)
    beam_size = int(config.get("talk", "faster_whisper", "beam_size") or 5)
    segments, info = model.transcribe(
        wav_path, beam_size=beam_size, language=language,
        vad_filter=True, vad_parameters=dict(min_silence_duration_ms=700))
    text = ""
    for seg in segments:
        text += seg.text
    return text.strip()


def transcribe(wav_path: str, config) -> str:
    """录音 wav → 语言路由 ASR → 文本（空串=无文字/引擎不可用）。

    语言路由（2026-10-25 重构）：按「基础功能-通用配置-回复语言」(need_lang)
    一次定模型——中文/粤语 → 达摩 FunASR；其它 → faster_whisper。
    不再"whisper 先转写→检测语言→转发 FunASR"（冗余双模型，延时翻倍）。

    Args:
        wav_path: 录音文件路径（audio_processor 录音产物）
        config: 主进程 Config（变长键 get）

    Returns:
        识别文本（已 strip）；引擎未接入/失败返回 ""——调用方静默跳过。
    """
    if not wav_path or not os.path.exists(wav_path):
        logger.warning(f"[asr] 录音文件不存在：{wav_path}")
        return ""
    asr_type = str(config.get("talk", "type") or "faster_whisper")
    if asr_type in ("google", "baidu"):
        logger.warning(
            f"[asr] talk.type={asr_type} 为云端方案，本地离线链未接入"
            f"（需密钥与外网依赖）——本次语音忽略；可改选 faster_whisper")
        return ""
    need_lang = str(config.get("need_lang") or "none").strip().lower()
    try:
        if need_lang in ("zh", "yue"):
            # 中文/粤语 → 达摩 FunASR（paraformer-large / UniASR 粤语）
            return _transcribe_funasr(wav_path, need_lang)
        # en/jp/ko 显式语言 → whisper 指定直转；none（所有）→ 沿用 whisper 页
        # 语言配置（自动识别→None=whisper 自动检测）
        whisper_lang = {"en": "en", "jp": "ja", "ko": "ko"}.get(need_lang)
        if whisper_lang is None:
            lang_cfg = str(config.get("talk", "faster_whisper", "language") or "自动识别")
            whisper_lang = None if (not lang_cfg or lang_cfg == "自动识别") else lang_cfg
        return _transcribe_faster_whisper(wav_path, config, whisper_lang)
    except Exception as e:
        logger.error(f"[asr] 语音识别失败（need_lang={need_lang}）：{e}")
        return ""


# ==================== 流式 ASR（2026-10-25 方案A：仅中文 FunASR online）====================
#
# 目标：把「说完才开始整段推理」的 ASR 延时（0.4~1s+ GPU）与说话时间重叠——
# 录音循环边说边喂增量 chunk（600ms 窗），说完 is_final=True 尾字 ≈0.2~0.4s，
# 说完→发大模型 从 ≈0.7~1.5s 压到 ≈0.3~0.6s。
#
# 与 _transcribe_funasr 的「不传 cache」不冲突：1.4.12 对 cache 参数做 len()
# 检查，传 bool 才崩；流式的 cache 是 dict（流式状态载体，每会话独立），合法。

_STREAM_MODEL_ID = "iic/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-online"
_PUNC_MODEL_ID = "iic/punc_ct-transformer_zh-cn-common-vocab272727-pytorch"
_STREAM_CHUNK_SIZE = [0, 10, 5]     # 600ms 增量窗（0 左上下文 / 10 当前 / 5 回看）
_STREAM_ENCODER_LOOK_BACK = 4       # funasr 官方流式示例参数
_STREAM_DECODER_LOOK_BACK = 1
_STREAM_FEED_SAMPLES = 9600         # 0.6s @ 16k——与 chunk_size 窗口对齐

_STREAM_LOCK = threading.Lock()  # 串行化 generate（多会话/多线程共享模型实例）


def _local_or_hf(hf_id: str) -> str:
    """模型本地优先（apps/.../models/<尾名>），缺失返回 HF id（镜像已设）。"""
    local = os.path.join(_APP_ASR_MODELS, hf_id.split("/")[-1])
    return local if os.path.isdir(local) else hf_id


def _get_funasr_streaming(config):
    """FunASR online 流式模型懒加载单例（本地优先，缺失走 HF 镜像下载）。"""
    key = "funasr_streaming_zh"
    with _ENGINE_LOCK:
        if key in _ENGINE_SINGLETON:
            return _ENGINE_SINGLETON[key]
        from funasr import AutoModel
        model_path = _local_or_hf(_STREAM_MODEL_ID)
        logger.info(f"[asr] 加载 FunASR 流式模型：{model_path}")
        model = AutoModel(model=model_path, disable_update=True)
        _ENGINE_SINGLETON[key] = model
        return model


def _get_funasr_punc(config):
    """标点模型单例（复用 zh 离线链同款 ct-transformer——online 流式模型无标点）。"""
    key = "funasr_punc"
    with _ENGINE_LOCK:
        if key in _ENGINE_SINGLETON:
            return _ENGINE_SINGLETON[key]
        from funasr import AutoModel
        model = AutoModel(model=_local_or_hf(_PUNC_MODEL_ID), disable_update=True)
        _ENGINE_SINGLETON[key] = model
        return model


class StreamingAsrSession:
    """单次录音的流式识别会话（zh online paraformer）。

    用法：录音线程循环 feed(chunk_bytes)（int16 LE PCM，16k 单声道——
    audio_processor 采集产物），说完调 final() 取最终文本。内部攒够
    600ms 才调一次 generate（增量窗口对齐），最终文本自动过标点模型。
    """

    def __init__(self, config):
        self._config = config
        self._model = _get_funasr_streaming(config)
        self._cache = {}                                   # 流式状态（每会话独立）
        self._buffer = np.zeros(0, dtype=np.int16)         # 不足 600ms 的尾部暂存
        self._parts: list = []
        self._closed = False
        self._lock = threading.Lock()                      # feed/final 与停止回调竞态互斥

    def feed(self, pcm_bytes: bytes) -> None:
        """喂入一段录音 chunk（int16 LE PCM 16k 单声道）。"""
        if self._closed or not pcm_bytes:
            return
        with self._lock:
            self._buffer = np.concatenate(
                (self._buffer, np.frombuffer(pcm_bytes, dtype=np.int16)))
            while len(self._buffer) >= _STREAM_FEED_SAMPLES:
                self._emit(is_final=False)

    def final(self) -> str:
        """说完冲刷尾部，返回最终文本（含标点回补；异常降级已识别部分）。"""
        with self._lock:
            if not self._closed:
                self._closed = True
                try:
                    self._emit(is_final=True)
                except Exception as e:
                    logger.warning(f"[asr] 流式尾部冲刷失败：{e}")
            text = "".join(self._parts).strip()
        if not text:
            return ""
        return self._apply_punc(text)

    def _emit(self, is_final: bool) -> None:
        """消费 600ms 窗（final 时全量）→ generate 增量识别。"""
        if is_final:
            chunk = self._buffer.astype(np.float32) / 32768.0
            self._buffer = np.zeros(0, dtype=np.int16)
        else:
            chunk = self._buffer[:_STREAM_FEED_SAMPLES].astype(np.float32) / 32768.0
            self._buffer = self._buffer[_STREAM_FEED_SAMPLES:]
        res = self._model.generate(
            input=chunk, cache=self._cache, is_final=is_final,
            chunk_size=_STREAM_CHUNK_SIZE,
            encoder_chunk_look_back=_STREAM_ENCODER_LOOK_BACK,
            decoder_chunk_look_back=_STREAM_DECODER_LOOK_BACK)
        if res and isinstance(res, list):
            part = str((res[0] or {}).get("text", "") or "")
            if part:
                self._parts.append(part)

    def _apply_punc(self, text: str) -> str:
        """online 模型无标点——最终文本过一遍 ct-transformer（失败降级原文本）。"""
        try:
            punc = _get_funasr_punc(self._config)
            res = punc.generate(input=text)
            if res and isinstance(res, list) and (res[0] or {}).get("text"):
                return str(res[0]["text"]).strip()
        except Exception as e:
            logger.warning(f"[asr] 标点回补失败，返回无标点文本：{e}")
        return text


def create_streaming_session(config):
    """按配置创建流式识别会话；不可用返回 None（调用方回退整段转写）。

    门控：talk.asr_streaming 缺省开启（None is not False 语义）；仅
    need_lang=zh 启用（online paraformer 中文流式）——粤语 UniASR 与
    en/jp/ko whisper 维持整段转写。
    """
    if config is None:
        return None
    if config.get("talk", "asr_streaming") is False:
        return None
    if str(config.get("need_lang") or "none").strip().lower() != "zh":
        return None
    try:
        return StreamingAsrSession(config)
    except Exception as e:
        logger.warning(f"[asr] 流式会话不可用（{e}），回退整段转写")
        return None
