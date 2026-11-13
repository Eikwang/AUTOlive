# -*- coding: utf-8 -*-
"""GSV 空段崩双面守卫（2026-11-13 torch.cat 空列表崩批）。

根因链（全程实测复现）：答谢/弹幕文本装饰符号（･･ U+FF65 半角片假名中点，
用户名装饰高频）→ split_lang 判独立 ja 段 → text_lang=zh 混合分支强制按 zh
清洗 → 白名单外字符全剥 → norm_text=""/word2ph=[] → get_bert_feature 内
torch.cat([]) 崩。

历史教训：2026-10-29 曾以运行时 monkeypatch
（gpt_sovits_inproc._patch_text_preprocessor）修同款崩点（全角方括号［］），
但 patch 目标模块（GPT_SoVITS.TTS_infer_pack.TextPreprocessor，repo 前缀导入）
与运行时实际类（TTS_infer_pack.TextPreprocessor，TTS.py:40 内层目录前缀导入）
是同一磁盘文件的两个独立模块对象——补丁只命中幽灵类，防护从未生效，本次
崩穿实证。守卫已源码化进 fork（_autolive_empty_seg_guard 类旗标），运行时
补丁退役。本文件守卫三层：
1. 两条导入路径的类旗标同时在场（防 import 遮蔽脱靶复发——本次崩穿核心教训）
2. fork 守卫行为（空输入零特征 / 空段跳过 / 生产崩文本全链不崩）
3. 源头治理（答谢用户名白名单过滤 + monkeypatch 不复活）
"""
import os
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

pytestmark = pytest.mark.usefixtures()

_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_GSV_INNER = os.path.join(_REPO, "apps", "gpt-sovits", "GPT_SoVITS")
_GSV_REPO_ROOT = os.path.join(_REPO, "apps", "gpt-sovits")


@pytest.fixture(scope="module")
def gsv_tp():
    """fork 文本前端 TextPreprocessor（运行时导入路径 = TTS.py:40 同款）。

    import 链 chinese/chinese2 模块级加载 G2PW（onnx+roberta），bert 路径
    相对 cwd——复刻 inproc _patch_shadows 的 bert_path env 注入语义；sys.path
    复刻 inproc gpt_sovits_pythonpath_entries 六条 + eres2net 手动补丁。
    """
    bert_abs = os.path.join(_GSV_INNER, "pretrained_models",
                            "chinese-roberta-wwm-ext-large")
    assert os.path.isdir(bert_abs), f"G2PW 依赖 roberta 不存在: {bert_abs}"
    os.environ.setdefault("bert_path", bert_abs)
    _entries = [_GSV_REPO_ROOT, _GSV_INNER,
                os.path.join(_GSV_INNER, "BigVGAN"),
                os.path.join(_GSV_INNER, "eres2net"),
                os.path.join(_GSV_REPO_ROOT, "tools"),
                os.path.join(_GSV_REPO_ROOT, "tools", "asr"),
                os.path.join(_GSV_REPO_ROOT, "tools", "uvr5"),
                _REPO]
    for _p in _entries:
        if os.path.isdir(_p) and _p not in sys.path:
            sys.path.insert(0, _p)
    import torch  # noqa: E402
    from TTS_infer_pack.TextPreprocessor import TextPreprocessor  # noqa: E402
    return TextPreprocessor(MagicMock(), MagicMock(), torch.device("cpu"))


class _CfgStub:
    """config.get(*keys) 桩：缺省全 None（need_lang/username_max_len 走兜底分支）。"""

    def get(self, *keys):
        return None


class _HandleStub:
    def __init__(self):
        from utils.common import Common  # noqa: E402
        self.common = Common()
        self.config = _CfgStub()


# ---------- 第 1 层：两条导入路径旗标在场（防遮蔽脱靶复发） ----------

def test_empty_seg_guard_flag_on_both_import_paths():
    """同一磁盘文件经两条前缀导入得到的类都必须带源码守卫旗标。

    崩穿教训：运行时实例类（TTS_infer_pack.*）与 monkeypatch 目标类
    （GPT_SoVITS.TTS_infer_pack.*）互不相同，补丁脱靶后守卫形同虚设——
    本断言锚定守卫在源码层（fork 文件）而非任何单一模块实例。
    """
    for _p in (_GSV_REPO_ROOT, _GSV_INNER,
               os.path.join(_GSV_INNER, "BigVGAN"),
               os.path.join(_GSV_INNER, "eres2net"),
               os.path.join(_GSV_REPO_ROOT, "tools"),
               os.path.join(_GSV_REPO_ROOT, "tools", "asr"),
               os.path.join(_GSV_REPO_ROOT, "tools", "uvr5"),
               _REPO):
        if os.path.isdir(_p) and _p not in sys.path:
            sys.path.insert(0, _p)
    import TTS_infer_pack.TextPreprocessor as _tp_runtime  # noqa: E402
    import GPT_SoVITS.TTS_infer_pack.TextPreprocessor as _tp_repo  # noqa: E402
    assert getattr(_tp_runtime.TextPreprocessor, "_autolive_empty_seg_guard", False), \
        "运行时导入路径（TTS_infer_pack.*，TTS.py:40 同款）缺空段守卫旗标"
    assert getattr(_tp_repo.TextPreprocessor, "_autolive_empty_seg_guard", False), \
        "repo 前缀导入路径（GPT_SoVITS.TTS_infer_pack.*）缺空段守卫旗标"


# ---------- 第 2 层：fork 守卫行为 ----------

def test_get_bert_feature_empty_input_zero_feature(gsv_tp):
    """空输入直接调用返回零宽特征，不进推理、不抛 torch.cat 空列表。"""
    import torch  # noqa: E402
    feat = gsv_tp.get_bert_feature("", [])
    assert tuple(feat.shape) == (1024, 0)
    assert feat.device.type == "cpu"


def test_halfwidth_kana_dot_cleans_to_empty_segment(gsv_tp):
    """空段形态锚定：･（U+FF65）单独成段被 zh 清洗器剥光（根因链第 2 环）。"""
    phones, word2ph, norm_text = gsv_tp.clean_text_inf("･･", "zh", "v2")
    assert phones == []
    assert word2ph == []
    assert norm_text == ""


def test_production_text_full_chain_no_crash(gsv_tp):
    """生产崩文本（答谢文案全串）全链 get_phones_and_bert 不崩、产出可读。

    get_bert_feature 实例桩仅替代 BERT 推理（返回与 word2ph 等宽零特征），
    空段跳过守卫在 get_phones_and_bert 内部——桩不遮蔽它。
    """
    import torch  # noqa: E402
    gsv_tp.get_bert_feature = lambda text, word2ph: torch.zeros(
        (1024, sum(word2ph) if word2ph else 0), dtype=torch.float32)
    phones, bert, norm_text = gsv_tp.get_phones_and_bert(
        "感谢冲冲冲冲冲･･送的1个单价0.1元，总价0.1元的粉丝团灯牌喵", "zh", "v2")
    assert len(phones) > 0
    assert norm_text and "･" not in norm_text and "･･" not in norm_text


def test_pure_symbol_text_full_chain_no_crash(gsv_tp):
    """纯符号文本全链不崩（全空段→bert_list 兜底→final=True "." 兜底重切）。"""
    import torch  # noqa: E402
    gsv_tp.get_bert_feature = lambda text, word2ph: torch.zeros(
        (1024, sum(word2ph) if word2ph else 0), dtype=torch.float32)
    # 不抛任何异常即通过（返回 phones 可能空/句号段，由 preprocess 层空值处置）
    gsv_tp.get_phones_and_bert("･･", "zh", "v2")


# ---------- 第 3 层：源头治理 ----------

def test_thanks_username_whitelist_strips_decoration():
    """答谢用户名白名单过滤：装饰符号剥除（根因链第 1 环注入点收口）。"""
    from utils.handlers.event_handler import EventHandler  # noqa: E402
    handler = EventHandler.__new__(EventHandler)
    handler.my_handle = _HandleStub()
    # 生产实证样本：用户名"冲冲冲冲冲･･"——U+FF65 白名单外剥除，汉字保留
    assert handler._thanks_username("冲冲冲冲冲･･") == "冲冲冲冲冲"
    # emoji + 中点装饰剥除
    assert handler._thanks_username("🍂冲冲冲冲冲··") == "冲冲冲冲冲"
    # 英文恒保留（默认 need_lang=none 保留集含 en）
    assert handler._thanks_username("Alice") == "Alice"
    # 全符号名白名单全剥为空 → 回退原清洗结果（用户名不可为空；
    # 合成不崩由 fork 空段守卫保证）
    assert handler._thanks_username("･･") == "･･"


def test_patch_text_preprocessor_retired():
    """运行时 monkeypatch 不复活：守卫职责已移交 fork 源码（幽灵补丁退役）。"""
    for _p in (_REPO,):
        if _p not in sys.path:
            sys.path.insert(0, _p)
    from utils.gpt_sovits_inproc import GptSovitsInproc  # noqa: E402
    assert not hasattr(GptSovitsInproc, "_patch_text_preprocessor"), \
        "_patch_text_preprocessor 已退役（双层 import 遮蔽脱靶），禁止复活"
