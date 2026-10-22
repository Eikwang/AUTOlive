# -*- coding: UTF-8 -*-
"""
TTS 合成参数工具模块（切片 3 静态部分：任务书 系统审查任务.md 文本转语音节）

零重依赖（不 import gradio/aiohttp 等），供 my_tts 与前端 tts 页共用、
tests/test_tts_argos.py 直接测试。

内容：
- edge-tts 内置常见声音清单 + 关键字过滤（D-18：搜索式下拉）
- edge-tts rate 百分比 ↔ speed_factor 换算（P26 变速迁移映射）
- 多参考音频路径归一化 + inproc 参考装配（P25 静态部分）
- edge-tts 高级参数 JSON 透传解析（escape hatch，默认空=全默认）
"""
from __future__ import annotations

import json
import re
from typing import Dict, List, Optional, Tuple

# 内置常见 edge-tts 声音清单（D-18：默认值写明具体 voice 名，不写"系统默认"；
# 完整清单经 edge_tts.list_voices 异步获取为 P-spike 实测项，静态部分用内置清单+关键字过滤）
EDGE_TTS_VOICES: List[str] = [
    "zh-CN-XiaoxiaoNeural",
    "zh-CN-XiaoyiNeural",
    "zh-CN-YunjianNeural",
    "zh-CN-YunxiNeural",
    "zh-CN-YunxiaNeural",
    "zh-CN-YunyangNeural",
    "zh-CN-liaoning-XiaobeiNeural",
    "zh-CN-shaanxi-XiaoniNeural",
    "zh-HK-HiuMaanNeural",
    "zh-HK-WanLungNeural",
    "zh-TW-HsiaoChenNeural",
    "zh-TW-YunJheNeural",
    "ja-JP-NanamiNeural",
    "ja-JP-KeitaNeural",
    "en-US-AriaNeural",
    "en-US-GuyNeural",
    "en-US-JennyNeural",
    "en-GB-RyanNeural",
    "en-GB-SoniaNeural",
    "ko-KR-SunHiNeural",
]

# edge-tts 默认音色（D-18）
EDGE_TTS_DEFAULT_VOICE = "zh-CN-XiaoxiaoNeural"

# 默认语速（任务书：rate 默认 100%）
EDGE_TTS_DEFAULT_RATE = "+0%"

_RATE_RE = re.compile(r"([+-]?\d+(?:\.\d+)?)\s*%")


def filter_edge_voices(keyword: str, voices: Optional[List[str]] = None) -> List[str]:
    """edge-tts 声音清单关键字过滤（大小写不敏感；空关键字返回全部）。"""
    pool = list(voices) if voices is not None else list(EDGE_TTS_VOICES)
    kw = (keyword or "").strip().lower()
    if not kw:
        return pool
    return [v for v in pool if kw in v.lower()]


def edge_rate_to_speed(rate_str: str) -> float:
    """edge-tts rate 百分比 → speed_factor 倍率（P26 映射）。

    任务书：默认 "--rate 100%"（不变速），加快 1.2 倍为 "--rate=+20%"，
    减慢 0.9 倍为 "--rate=-10%"。即带符号形式 = 1 ± pct/100；
    不带符号的绝对形式（如默认展示 "100%"）视为不变速（1.0）。
    无法解析时返回 1.0（系统默认）。
    """
    if not rate_str:
        return 1.0
    text = str(rate_str).strip()
    m = _RATE_RE.search(text)
    if not m:
        return 1.0
    try:
        pct = float(m.group(1))
    except (TypeError, ValueError):
        return 1.0
    sign = text[m.start()] if text[m.start()] in ("+", "-") else ""
    if not sign:
        # 无符号绝对形式（如 "100%"）= edge-tts 默认展示，不变速
        return 1.0
    delta = pct if sign == "+" else -abs(pct)
    return round(1.0 + delta / 100.0, 6)


def speed_to_edge_rate(speed: float) -> str:
    """speed_factor 倍率 → edge-tts rate 百分比字符串（P26 映射，edge_rate_to_speed 逆变换）。

    1.2 → "+20%"，0.9 → "-10%"，1.0 → "+0%"（edge-tts 合法形式，等同不变速）。
    """
    try:
        s = float(speed)
    except (TypeError, ValueError):
        s = 1.0
    percent = (s - 1.0) * 100.0
    sign = "+" if percent >= 0 else "-"
    return f"{sign}{round(abs(percent), 2):g}%"


def clamp_speed_factor(speed, log_bad=None) -> float:
    """随机语速倍率 clamp（A2/S1，2026-10-22 审查批次）。

    合法域 [0.25, 4.0]（与 GPT-SoVITS speed_factor 滑条范围对齐）。
    非法（None/非数值/越界/0/负值）→ 回落 1.0（不变速）；提供 log_bad 回调时
    输出告警（义务 E6：非法回落 1.0+告警，防打爆 TTS API）。
    """
    try:
        s = float(speed)
    except (TypeError, ValueError):
        if log_bad:
            log_bad(f"语速倍率非法（{speed!r}），回落 1.0")
        return 1.0
    if not (0.25 <= s <= 4.0):
        if log_bad:
            log_bad(f"语速倍率越界（{s}，合法域 [0.25, 4.0]），回落 1.0")
        return 1.0
    return s


def speed_to_edge_pitch(speed: float) -> str:
    """speed_factor 倍率 → edge-tts pitch 字符串（A2 pitch 联动，2026-10-22 批次）。

    设计公式：pitch_hz = (factor - 1) * 50，即 +1.2 倍语速 → "+10Hz"、
    0.9 倍 → "-5Hz"；可调范围 ±50Hz，越界截断（义务 R2-A2）。
    1.0（不变速）→ "+0Hz"（edge-tts 合法形式，等效不调）。
    """
    s = clamp_speed_factor(speed)
    hz = (s - 1.0) * 50.0
    hz = max(-50.0, min(50.0, hz))
    sign = "+" if hz >= 0 else "-"
    return f"{sign}{round(abs(hz), 2):g}Hz"


def normalize_edge_rate(rate) -> Optional[str]:
    """用户 rate 配置规范化（P26，2026-10-20 实测修复）。

    edge-tts 7.x 要求带符号增量（"+0%"/"+20%"/"-10%"），裸 "100%" 会抛
    Invalid rate。UI 语义是"语速百分比"（100%=基准），与 edge-tts 的
    "相对增量"语义差 100：裸值 X% 先换算 speed=X/100 再映射增量
    （"120%"→"+20%"，"90%"→"-10%"）；带符号值原样返回（已是原生语义）。
    无效输入返回 None（调用方落默认值）。
    """
    if rate is None:
        return None
    r = str(rate).strip()
    if not r:
        return None
    if r[0] in "+-":
        return r
    try:
        s = float(r.rstrip("%")) / 100.0
    except ValueError:
        return None
    return speed_to_edge_rate(s)


def normalize_ref_paths(paths_input) -> List[str]:
    """参考音频路径归一化（P25 多参考）。

    接受 list / 多行字符串（换行、逗号、分号分隔），返回非空去重列表（保序）。
    """
    if paths_input is None:
        return []
    if isinstance(paths_input, str):
        raw = re.split(r"[\n;,，；]+", paths_input)
    elif isinstance(paths_input, (list, tuple)):
        raw = []
        for item in paths_input:
            raw.extend(normalize_ref_paths(item))
    else:
        raw = [str(paths_input)]
    seen = set()
    result = []
    for p in raw:
        p = (p or "").strip()
        if p and p not in seen:
            seen.add(p)
            result.append(p)
    return result


def build_inproc_refs(ref_paths: List[str], ref_free: bool,
                      prompt_text: str) -> Dict:
    """inproc 参考装配（P25 静态部分）。

    上游 API 文档形态（GPT-SoVITS TTS_infer_pack/TTS.py run inputs 清单）：
    - aux_ref_audio_paths：list，辅助参考音频（多音色融合），上游原生支持
    - 无参考文本模式 = 不填参考文本（prompt_text 空串）——上游无 ref_free 键，
      ref_free 标志在装配层消化，不透传给引擎
    - P25 spike：prompt_text 空 + aux 列表在 inproc 通道的生效性需 GPU 环境实测；
      失败时由 my_tts._gpt_sovits_inproc 降级单参考（回退预案）

    返回片段 dict（ref_audio_path / aux_ref_audio_paths / prompt_text），
    空参考列表时返回 {}（调用方回退 params.ref_audio_path 单值）。
    """
    fragments: Dict = {}
    if ref_paths:
        fragments["ref_audio_path"] = ref_paths[0]
        if len(ref_paths) > 1:
            fragments["aux_ref_audio_paths"] = list(ref_paths[1:])
    if ref_free:
        # 无参考文本模式：显式空串（不使用参考文本）
        fragments["prompt_text"] = ""
    elif prompt_text:
        fragments["prompt_text"] = prompt_text
    return fragments


def parse_edge_extra_args(json_str: Optional[str]) -> Tuple[Dict, Optional[str]]:
    """edge-tts 高级参数 JSON 透传解析（escape hatch，默认空=全默认）。

    返回 (args_dict, error)。空/空白 → ({}, None)；非法 JSON 或非对象 → ({}, 错误标记)。
    """
    text = (json_str or "").strip()
    if not text:
        return {}, None
    try:
        data = json.loads(text)
    except (ValueError, TypeError) as e:
        return {}, f"JSON 解析失败: {e}"
    if not isinstance(data, dict):
        return {}, "JSON 顶层必须是对象（{...}）"
    return data, None
