# -*- coding: UTF-8 -*-
"""
argos-translate 离线翻译模块（P27 静态部分，任务书 系统审查任务.md 翻译节）

定位（uc2 终门裁决两段式）：argos 为系统内置离线兜底翻译；"自定义LLM 翻译"
为默认首选（栈内能力）。T4 回退分支：argos 不可用（未安装/模型缺失/语言对
不支持）→ 本模块返回 None + 错误标记，调用方（my_translate）降级处理，不阻塞。

设计约束：
- argostranslate 延迟导入（依赖未安装场景静默降级 + 安装指引 log 一次）
- 离线模型缺失 / 语言对不支持 → 返回 None（调用方回退）
- 语言对探测仅覆盖「已安装的离线模型」——argos 需先安装语言包
  （argostranslate.package.install_from_path / update_package_index）
"""
from __future__ import annotations

from typing import List, Optional, Tuple

from utils.my_log import logger

# 安装指引只 log 一次（防刷屏）
_INSTALL_HINT_LOGGED = False


def _load_translate_api():
    """延迟导入 argostranslate.translate；未安装返回 None（不抛出）。"""
    try:
        import argostranslate.translate  # noqa: 延迟导入，依赖可选
        return argostranslate.translate
    except Exception:
        return None


def _log_install_hint_once():
    global _INSTALL_HINT_LOGGED
    if not _INSTALL_HINT_LOGGED:
        _INSTALL_HINT_LOGGED = True
        logger.warning(
            "[translate-argos] argostranslate 未安装，argos 翻译不可用——"
            "fix: pip install argostranslate 后按需安装语言包（离线模型）；"
            "本次降级为调用方回退处理")


def normalize_argos_lang(lang: Optional[str]) -> str:
    """语言码归一化到 argos 主码（zh-CN/zh-Hans→zh；en-US→en；auto 不在此处理）。"""
    code = (lang or "").strip().lower()
    if not code:
        return ""
    return code.split("-")[0]


def detect_lang_hint(text: str) -> str:
    """轻量语言启发式（argos 不支持 auto；系统既有 lang_check 已失联，
    此处为静态实现——CJK 统计 + 假名判定，误判由调用方降级兜底）。"""
    if not text:
        return "zh"
    kana = sum(1 for ch in text if "぀" <= ch <= "ヿ")
    if kana > 0:
        return "ja"
    cjk = sum(1 for ch in text if "一" <= ch <= "鿿")
    if cjk * 2 >= max(len(text), 1):
        return "zh"
    return "en"


def check_language_pair(from_lang: str, to_lang: str) -> Tuple[bool, str]:
    """语言对探测（P27）。

    返回 (supported, mark)：
    - 未安装 argostranslate → (False, "not_installed")
    - 源/目标语言离线模型缺失或语言对不支持 → (False, "pair_unsupported")
    - 支持 → (True, "")
    """
    api = _load_translate_api()
    if api is None:
        _log_install_hint_once()
        return False, "not_installed"
    try:
        installed: List = api.get_installed_languages() or []
    except Exception as e:
        logger.warning(f"[translate-argos] 已安装语言包列表获取失败（按模型缺失处理）: {e}")
        return False, "pair_unsupported"
    src = normalize_argos_lang(from_lang)
    dst = normalize_argos_lang(to_lang)
    lang_from = next((l for l in installed if l.code == src), None)
    lang_to = next((l for l in installed if l.code == dst), None)
    if lang_from is None or lang_to is None:
        return False, "pair_unsupported"
    try:
        if lang_from.get_translation(lang_to) is None:
            return False, "pair_unsupported"
    except Exception as e:
        logger.warning(f"[translate-argos] 语言对翻译器获取失败: {e}")
        return False, "pair_unsupported"
    return True, ""


def translate_argos(text: str, from_lang: str = "auto", to_lang: str = "zh") -> Optional[str]:
    """argos 离线翻译主入口（P27）。

    返回译文；不可用（未安装/模型缺失/语言对不支持/异常）返回 None + 错误标记 log，
    调用方（my_translate）按 T4 降级：回退 LLM 翻译或按翻译失败处理。
    """
    if not text:
        return None
    api = _load_translate_api()
    if api is None:
        _log_install_hint_once()
        return None
    src = normalize_argos_lang(from_lang) if from_lang and from_lang != "auto" else detect_lang_hint(text)
    dst = normalize_argos_lang(to_lang) or "zh"
    supported, mark = check_language_pair(src, dst)
    if not supported:
        logger.warning(
            f"[translate-argos] 语言对不可用（mark={mark}）——problem: {src}->{dst} "
            "离线模型缺失或语言对不支持；fix: 安装对应 argos 语言包，或切换翻译类型为 "
            "自定义LLM翻译（默认首选）；本次返回 None 由调用方降级")
        return None
    try:
        lang_from = next(l for l in api.get_installed_languages() if l.code == src)
        lang_to = next(l for l in api.get_installed_languages() if l.code == dst)
        translation = lang_from.get_translation(lang_to)
        result = translation.translate(text)
        logger.debug(f"[translate-argos] {src}->{dst} 完成: {result}")
        return result
    except Exception as e:
        logger.error(f"[translate-argos] 翻译异常（返回 None 降级）: {e}")
        return None
