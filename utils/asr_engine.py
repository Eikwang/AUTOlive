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
