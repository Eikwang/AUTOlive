# -*- coding: UTF-8 -*-
"""T0 主匹配器热修守卫（/autoplan main-20261026-214845 D8）。

F5 回归背景：feature_handler.find_answer/find_similar_answer 曾调用不存在的
Common.calculate_similarity → AttributeError 被吞 → 主知识库匹配运行时 100% 失败
（完美命中也失败）；tests/test_common.py 的 hasattr 守卫让该缺失在测试里静默跳过。
本文件回归三件事：①方法必须存在（无 hasattr 守卫）②精确短路（归一化全等）
③相似度三态（命中/边界/未命中）+ D8 标定语料分布观测。
"""
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.common import Common
from utils.common.text_mixin import normalize_for_match
from utils.handlers.feature_handler import FeatureHandler


class _StubHandle:
    common = Common()
    config = None


def _make_handler():
    fh = FeatureHandler.__new__(FeatureHandler)
    fh.my_handle = _StubHandle()
    return fh


def _write_kb(lines):
    """写临时一问一答词库（问题|答案 管道行格式）。"""
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8")
    tmp.write("\n".join(lines) + "\n")
    tmp.close()
    return tmp.name


# D8 标定语料（保守临时值，E-H3：T6 落地后同语料重跑标定）：
# 同义改写/部分重叠/无关 三类，用于观测命中分布并钉住三态边界。
CALIBRATION_CORPUS = [
    # (查询, 词库问题, 期望类别)  期望类别: exact / high / miss
    ("怎么点歌", "怎么点歌", "exact"),
    ("怎么点歌？", "怎么点歌", "exact"),          # 全角标点经归一化近似（尾标点差异）
    ("　怎么 点歌　", "怎么点歌", "exact"),        # 全角空格+空白
    ("ＨＯＷ 点歌", "how点歌", "high"),            # 全角→半角+大小写
    ("主播多大了", "主播多大了呀", "high"),
    ("今天星期几", "今天星期几啊", "high"),
    ("怎么听歌", "怎么点歌", "high"),              # 部分重叠（听/点 一字之差）
    ("你叫什么名字", "你叫什么", "high"),
    ("主播会唱歌吗", "主播会唱歌", "high"),
    ("直播间几点开播", "几点开播", "high"),
    ("你好呀", "你好", "high"),
    ("今天几号", "欢迎回来", "miss"),
    ("今天天气怎么样", "主播今天穿什么", "miss"),
    ("这个游戏怎么玩", "这首歌叫什么", "miss"),
    ("哈哈哈笑死我了", "主播笑一个", "miss"),
    ("再见啦", "欢迎下次来", "miss"),
]


@pytest.mark.parametrize("text_a,text_b,expected", [
    ("怎么点歌", "怎么点歌", 1.0),
    ("ＨＯＷ", "how", 1.0),          # 全角→半角
    ("a b　c", "abc", 1.0),          # 空白全去
    ("你好", "再见", 0.0),
    (None, "x", 0.0),
    ("", "x", 0.0),
])
def test_calculate_similarity_contract(text_a, text_b, expected):
    """相似度契约：归一化全等=1.0，空/None=0.0，无关=0.0。"""
    assert Common.calculate_similarity(text_a, text_b) == pytest.approx(expected)


def test_calculate_similarity_must_exist():
    """F5 回归（反 hasattr 守卫）：方法必须存在——缺失即测试红，绝不静默跳过。"""
    assert callable(Common.calculate_similarity)


def test_find_answer_exact_match_hits():
    """F5 实锤场景回归：完全相同问题必须命中（热修前恒 AttributeError→None）。"""
    fh = _make_handler()
    kb = _write_kb(["怎么点歌|发送 点歌 歌名 即可", "主播多大了|这是秘密哦"])
    try:
        assert fh.find_answer("怎么点歌", kb, 0.5) == "发送 点歌 歌名 即可"
        assert fh.find_similar_answer("怎么点歌", kb, 0.5) == "发送 点歌 歌名 即可"
    finally:
        os.unlink(kb)


def test_find_answer_exact_short_circuit_normalized():
    """精确短路：归一化（去空白+全半角+大小写，D4 口径）全等即命中，且不依赖阈值。"""
    fh = _make_handler()
    kb = _write_kb(["怎么点歌|管道行命中"])
    try:
        # 全角空格差异：归一化后全等 → 阈值拉满 1.0 也命中（短路不受阈值门控）
        assert fh.find_answer("　怎么点歌", kb, 1.0) == "管道行命中"
        # 尾标点 ？ 不在归一化口径内（D4=去空白+全半角+大小写）：
        # 非全等，但字符相似度 ~0.889 ≥ 默认 0.8 → 相似度命中
        assert fh.find_answer("　怎么点歌？", kb, 0.8) == "管道行命中"
        assert fh.find_answer("怎么点歌呀", kb, 0.9) is None  # 低于阈值未命中
    finally:
        os.unlink(kb)


def test_find_answer_three_states():
    """三态回归：高相似命中（≥阈值）/ 低于阈值未命中 / 空词库未命中。"""
    fh = _make_handler()
    kb = _write_kb(["主播多大了呀|18岁", "几点开播|晚上八点"])
    try:
        assert fh.find_answer("主播多大了", kb, 0.8) == "18岁"
        assert fh.find_answer("感谢老板的礼物", kb, 0.8) is None
        # 完全相同问题（精确短路）
        assert fh.find_answer("几点开播", kb, 0.8) == "晚上八点"
    finally:
        os.unlink(kb)

    empty = _write_kb([])
    try:
        assert fh.find_answer("怎么点歌", empty, 0.5) is None
    finally:
        os.unlink(empty)


def test_find_answer_missing_file_returns_none():
    """文件缺失：返回 None（不抛异常），由调用方走 LLM 托底。"""
    fh = _make_handler()
    assert fh.find_answer("怎么点歌", "Z:/不存在的词库-锚定不存在路径.txt", 0.5) is None


def test_d8_calibration_corpus_distribution(caplog):
    """D8 标定语料分布观测（E-H3 保守临时值基线）：
    断言三态极性（exact 全命中 / miss 全未命中）+ 打印 high 类命中分布供 T6 重标定对照。
    """
    fh = _make_handler()
    kb_lines = []
    queries = []
    expectations = []
    for i, (query, kb_q, expected) in enumerate(CALIBRATION_CORPUS):
        kb_lines.append(f"{kb_q}|答案{i}")
        queries.append((query, i, expected))
    kb = _write_kb(kb_lines)
    try:
        high_scores = []
        for query, idx, expected in queries:
            sim = Common.calculate_similarity(query, CALIBRATION_CORPUS[idx][1])
            if expected == "exact":
                # exact 类：精确短路（归一化全等）或高相似，必须命中 0.8 阈值
                assert sim >= 0.8 or normalize_for_match(query) == normalize_for_match(CALIBRATION_CORPUS[idx][1]), \
                    f"exact 样本未命中: {query!r} vs {CALIBRATION_CORPUS[idx][1]!r} sim={sim}"
            elif expected == "miss":
                assert sim < 0.5, f"miss 样本意外高分: {query!r} sim={sim}"
            else:
                high_scores.append((query, round(sim, 3)))
        # 观测值打印（T6 重标定对照基线）
        print("\nD8 标定语料 high 类分布（T0 基线）:")
        for q, s in high_scores:
            print(f"  {q!r} -> {s}")
    finally:
        os.unlink(kb)


def test_normalize_for_match_shared_semantics():
    """归一化口径（D4 两侧共享）：去全部空白+全半角+大小写折叠。"""
    assert normalize_for_match("　Ａｂｃ ｄＥ，！") == "abcde,!"
    assert normalize_for_match(None) == ""
    assert normalize_for_match("Ｈｅｌｌｏ　ＷＯＲＬＤ") == "helloworld"
