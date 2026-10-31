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
    2026-10-24 实测修复：百分比整数化——edge-tts 7.3.1 校验 ^[+-]\\d+%$，
    随机语速因子的小数（1.0788 → "+7.88%"）被拒 "Invalid rate"（实证：
    +7.88% REJECT，+8% OK），弹幕链合成全灭。
    """
    try:
        s = float(speed)
    except (TypeError, ValueError):
        s = 1.0
    percent = (s - 1.0) * 100.0
    value = round(abs(percent))
    sign = "+" if (percent >= 0 or value == 0) else "-"
    return f"{sign}{value}%"


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
    2026-10-24 实测修复：Hz 整数化（同 speed_to_edge_rate——edge-tts 7.3.1
    拒收小数，实证 "+7.88Hz" REJECT）；原 "-37.5Hz" 类输出在 pitch 联动
    随机语速下即崩点形态。
    """
    s = clamp_speed_factor(speed)
    hz = (s - 1.0) * 50.0
    hz = max(-50.0, min(50.0, hz))
    value = round(abs(hz))
    sign = "+" if (hz >= 0 or value == 0) else "-"
    return f"{sign}{value}Hz"


def normalize_edge_rate(rate) -> Optional[str]:
    """用户 rate 配置规范化（P26，2026-10-20 实测修复）。

    edge-tts 7.x 要求带符号增量（"+0%"/"+20%"/"-10%"），裸 "100%" 会抛
    Invalid rate。UI 语义是"语速百分比"（100%=基准），与 edge-tts 的
    "相对增量"语义差 100：裸值 X% 先换算 speed=X/100 再映射增量
    （"120%"→"+20%"，"90%"→"-10%"）。
    2026-10-24 实测修复：带符号值不再原样透传——解析后整数化重出
    （"+7.88%"→"+8%"，edge-tts 7.3.1 只收整数百分比）；无法解析（含无
    % 号裸值如 "+20"）返回 None（调用方落默认值）。
    """
    if rate is None:
        return None
    r = str(rate).strip()
    if not r:
        return None
    if r[0] in "+-":
        m = re.fullmatch(r"([+-])(\d+(?:\.\d+)?)\s*%", r)
        if not m:
            return None
        return f"{m.group(1)}{round(float(m.group(2)))}%"
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


def normalize_prompt_texts(texts_input) -> List[str]:
    """参考文本归一化（2026-10-29 批次：与 ref_audio_paths 逐行对应）。

    接受 list / 多行字符串，返回逐行文本列表：
    - 仅按换行切分（转写文本天然含逗号/分号，不可复用 normalize_ref_paths 的分隔符集）
    - 保留中间空行（对齐语义：第 i 行文本=第 i 行参考音频），仅裁掉尾部空行
    """
    if texts_input is None:
        return []
    if isinstance(texts_input, str):
        lines = texts_input.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    elif isinstance(texts_input, (list, tuple)):
        lines = []
        for item in texts_input:
            lines.extend(str(item or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"))
    else:
        lines = [str(texts_input)]
    while lines and not lines[-1].strip():
        lines.pop()
    return [ln.strip() for ln in lines]


def build_inproc_refs(ref_paths: List[str], ref_free: bool,
                      prompt_text: str, prompt_texts=None) -> Dict:
    """inproc 参考装配（P25 静态部分；2026-10-29 扩展逐音频参考文本）。

    上游 API 文档形态（GPT-SoVITS TTS_infer_pack/TTS.py run inputs 清单）：
    - aux_ref_audio_paths：list，辅助参考音频（多音色融合），上游原生支持
    - 无参考文本模式 = 不填参考文本（prompt_text 空串）——上游无 ref_free 键，
      ref_free 标志在装配层消化，不透传给引擎
    - prompt_texts：与 ref_audio_paths 逐行对应的参考文本列表（2026-10-29）；
      GPT-SoVITS 语义仅主参考（第一行）文本参与合成，其余行存档匹配——
      主参考更换/重排后转写跟随对应音频，不再串档
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
    else:
        # 主参考文本：prompt_texts 第一行 > 旧版单值 prompt_text 兼容回退
        main_text = ""
        if prompt_texts:
            pt_lines = normalize_prompt_texts(prompt_texts)
            if pt_lines and pt_lines[0]:
                main_text = pt_lines[0]
        main_text = main_text or (prompt_text or "")
        if main_text:
            fragments["prompt_text"] = main_text
    return fragments


def parse_fix_pairs(raw) -> List[Tuple[str, str]]:
    """念法纠正映射解析（2026-10-30 审查批次，TTS 页即时配置）。

    输入 dict（config 直接存储形态，逐对键值）或多行字符串（每行 `原文=读音`，
    支持中文冒号/全角等号/箭头（→、->、=>）/空格分隔——空格兜底兼容
    apps/gpt-sovits/念法纠正.txt 旧词库直贴形态；分号可单行多对）；坏行
    （无任何分隔符或空键）跳过不报错。
    返回 [(原文, 读音), ...]，按原文长度降序排序（最长匹配优先——
    "重庆"须先于"重"命中）。

    注：多音字（polyphone_map）拼写改写已退役（2026-10-30 用户裁决）——多音字
    统一走 GPT-SoVITS 引擎词典 多音字.txt（g2p 层改发音不改正文，见
    parse_polyphone_lines）；本函数仅服务念法纠正（整词改写）。"""
    pairs: List[Tuple[str, str]] = []
    if not raw:
        return pairs
    if isinstance(raw, dict):
        items = list(raw.items())
    else:
        items = []
        for line in re.split(r"[\n;；]+", str(raw)):
            line = line.strip()
            if not line:
                continue
            # 分隔符优先级：多字符箭头（->/=>）> =/全角等号/冒号/→ > 空格兜底
            m = re.split(r"->|=>", line, maxsplit=1)
            if len(m) == 2:
                k, v = m
            elif "=" in line:
                k, _, v = line.partition("=")
            else:
                m = re.split(r"[:：＝]|→", line, maxsplit=1)
                if len(m) == 2:
                    k, v = m
                else:
                    # 空格兜底（旧词库形态：`AI 人工智能`）——首个空白切二
                    sp = re.split(r"\s+", line, maxsplit=1)
                    if len(sp) == 2:
                        k, v = sp
                    else:
                        continue
            items.append((k, v))
    for k, v in items:
        k = str(k or "").strip()
        v = str(v or "").strip()
        if k and v and k != v:
            pairs.append((k, v))
    pairs.sort(key=lambda kv: len(kv[0]), reverse=True)
    return pairs


def apply_text_fixes(text: str, pronunciation_fix=None, polyphone_map=None) -> str:
    """TTS 前文本纠正（2026-10-30 审查批次）：念法纠正（pronunciation_fix，
    整词改写如 缩写→SOGOU 拼读）先应用；多音字替换（polyphone_map，同字异音
    标注如 长→chang）后应用。两级均可选（空=原样返回）。

    说明：GPT-SoVITS 逐字读音由其 g2p 决定，本层为纯文本前端——polyphone_map
    以"多音字+注音"形态改写文本（如 长江→chang江）交由模型按拼写朗读；
    edge-tts 通道同样受益（SSML 层前文本已修正）。"""
    if not text:
        return text
    for src, dst in parse_fix_pairs(pronunciation_fix):
        if src in text:
            text = text.replace(src, dst)
    for src, dst in parse_fix_pairs(polyphone_map):
        if src in text:
            text = text.replace(src, dst)
    return text


# ==================== GPT-SoVITS 引擎多音字词典（多音字.txt 直读直写） ====================
# 2026-10-30 用户裁决：多音字统一走引擎词典（g2p 层改发音不改正文，发音精确），
# 前端「多音字」textarea 直读直写仓库根 多音字.txt——UI 拼写改写（polyphone_map）退役。

POLYPHONE_FILENAME = "多音字.txt"

_POLYPHONE_LINE_RE = re.compile(r'^(?P<word>\S+)\s+(?P<phones>\[.*\])\s*$')


def parse_polyphone_lines(text: str) -> Tuple[List[Tuple[str, List[str]]], List[str]]:
    """引擎词典行解析：`一行 ["yi4","hang2"]` → (词, [拼音...])。

    返回 (合法条目列表, 坏行列表)——坏行原样返回供 UI 警告（词缺读音数组/
    拼音非字符串/空词）；注释行（# 开头）与空行忽略。"""
    entries: List[Tuple[str, List[str]]] = []
    bad: List[str] = []
    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        m = _POLYPHONE_LINE_RE.match(line)
        if not m:
            bad.append(raw_line.rstrip())
            continue
        word = m.group("word").strip()
        try:
            phones = json.loads(m.group("phones"))
        except (ValueError, TypeError):
            bad.append(raw_line.rstrip())
            continue
        if not word or not isinstance(phones, list) or not phones or \
                not all(isinstance(p, str) and p.strip() for p in phones):
            bad.append(raw_line.rstrip())
            continue
        entries.append((word, [p.strip() for p in phones]))
    return entries, bad


def build_polyphone_lines(entries) -> str:
    """引擎词典行构建（引擎原生格式：`词 ["p1","p2"]`）。"""
    return "\n".join(
        f"{word} {json.dumps(phones, ensure_ascii=False)}"
        for word, phones in entries)
