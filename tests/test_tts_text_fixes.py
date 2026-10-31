# -*- coding: utf-8 -*-
"""念法纠正/多音字（TTS 前文本前端）全链测试。

覆盖（2026-10-30 审查批次）：
1. parse_fix_pairs 解析全形态（半角/全角等号、中文冒号、分号单行、dict、坏行、最长优先）
2. apply_text_fixes 应用语义（纠正先于多音字、空配置透传、回显往返）
3. my_tts._gpt_sovits_inproc 接线源级守卫（params_cfg 现读 + apply 在合成调用前 + 流式共用 text）
4. 前端保存链守卫（blur 失焦保存 + 显式按钮兜底 + 解析 0 命中负反馈——修复 on_change
   每键 WebSocket 竞态导致刷新后丢失）
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.audio_handle.tts_params import (
    parse_fix_pairs, apply_text_fixes,
    parse_polyphone_lines, build_polyphone_lines, POLYPHONE_FILENAME)

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(rel):
    with open(os.path.join(_REPO, rel), encoding="utf-8") as f:
        return f.read()


class TestParseFixPairs:
    """解析层全形态。"""

    def test_halfwidth_equals(self):
        assert parse_fix_pairs("AI=人工智能") == [("AI", "人工智能")]

    def test_fullwidth_equals(self):
        assert parse_fix_pairs("AI＝人工智能") == [("AI", "人工智能")]

    def test_chinese_colon(self):
        assert parse_fix_pairs("AI：人工智能") == [("AI", "人工智能")]

    def test_semicolon_single_line(self):
        pairs = parse_fix_pairs("AI=人工智能；TTS=文本转语音")
        assert ("AI", "人工智能") in pairs and ("TTS", "文本转语音") in pairs

    def test_space_separated_legacy_format(self):
        """空格兜底（apps/gpt-sovits/念法纠正.txt 旧词库形态：`AI 人工智能`）。"""
        pairs = parse_fix_pairs("80后 八零后\nmm 毫米\n3.0 三点零")
        assert ("80后", "八零后") in pairs
        assert ("mm", "毫米") in pairs
        assert ("3.0", "三点零") in pairs

    def test_arrow_separators(self):
        """箭头兜底（→、->、=>）。"""
        assert parse_fix_pairs("AI→人工智能") == [("AI", "人工智能")]
        assert parse_fix_pairs("AI->人工智能") == [("AI", "人工智能")]
        assert parse_fix_pairs("AI=>人工智能") == [("AI", "人工智能")]

    def test_dict_input_roundtrip(self):
        assert parse_fix_pairs({"说服": "shuifu", "重庆": "chongqing"}) == [
            ("说服", "shuifu"), ("重庆", "chongqing")]

    def test_bad_lines_skipped(self):
        """无分隔符/空键/自等行跳过，不抛异常不报错。"""
        assert parse_fix_pairs("自由文本\n=空键\nAI=AI\n好=的") == [("好", "的")]

    def test_longest_first_sorting(self):
        """按原文长度降序——"重庆"须先于"重"命中。"""
        pairs = parse_fix_pairs("重=zhong\n重庆=chongqing")
        assert pairs[0] == ("重庆", "chongqing")

    def test_empty_inputs(self):
        assert parse_fix_pairs(None) == []
        assert parse_fix_pairs("") == []
        assert parse_fix_pairs({}) == []


class TestApplyTextFixes:
    """应用语义。"""

    def test_pronunciation_fix_rewrites_whole_word(self):
        assert apply_text_fixes("AI很聪明", {"AI": "人工智能"}, {}) == "人工智能很聪明"

    def test_polyphone_spelling_annotation(self):
        assert apply_text_fixes("重庆的长江很长", {}, {"长": "chang"}) == "重庆的chang江很chang"

    def test_fix_takes_priority_over_polyphone(self):
        """念法纠正先应用——多音字同字条目不再命中已改写文本。"""
        assert apply_text_fixes("AI长文", {"AI": "人工智能"}, {"长": "chang"}) == "人工智能chang文"

    def test_empty_configs_passthrough(self):
        text = "原文不动"
        assert apply_text_fixes(text, {}, {}) == text

    def test_roundtrip_echo_stable(self):
        """UI 回显形态（k=v 多行）再解析结果与 dict 一致（防往返漂移）。"""
        mapping = {"说服": "shuifu", "重庆": "chongqing"}
        echoed = "\n".join(f"{k}={v}" for k, v in parse_fix_pairs(mapping))
        assert parse_fix_pairs(echoed) == [("说服", "shuifu"), ("重庆", "chongqing")]


class TestPolyphoneEngineDict:
    """GPT-SoVITS 引擎词典（多音字.txt）直读直写（2026-10-30 用户裁决）。

    多音字统一走引擎 g2p 词典（改发音不改正文，发音精确）——UI 拼写改写
    （polyphone_map）退役。"""

    def test_parse_engine_native_line(self):
        entries, bad = parse_polyphone_lines('一行 ["yi4","hang2"]')
        assert entries == [("一行", ["yi4", "hang2"])]
        assert bad == []

    def test_parse_multi_with_comment_and_blank(self):
        text = '# 注释\n\n一行 ["yi4","hang2"]\n说服 ["shui4","fu2"]\n'
        entries, bad = parse_polyphone_lines(text)
        assert entries == [("一行", ["yi4", "hang2"]), ("说服", ["shui4", "fu2"])]
        assert bad == []

    def test_bad_lines_collected(self):
        """坏行（缺数组/拼音非串/空数组/自由文本）收集不抛异常。"""
        text = '自由文本\n一行\n重 [123]\n空 []\n好 ["yi4"]\n'
        entries, bad = parse_polyphone_lines(text)
        assert entries == [("好", ["yi4"])]
        assert len(bad) == 4

    def test_build_parse_roundtrip(self):
        """构建→解析往返稳定（引擎原生格式）。"""
        entries = [("一行", ["yi4", "hang2"]), ("说服", ["shui4", "fu2"])]
        assert parse_polyphone_lines(build_polyphone_lines(entries))[0] == entries

    def test_filename_anchored_to_repo_root(self):
        """引擎按相对 cwd 打开——文件名锚定仓库根（TODOS L112 语义）。"""
        assert POLYPHONE_FILENAME == "多音字.txt"

    def test_frontend_reads_writes_engine_file(self):
        """前端源级守卫：多音字 textarea 直读直写引擎词典文件，polyphone_map
        配置面退役。"""
        src = _read("frontend/ui/tabs/tts.py")
        assert '_polyphone_path = os.path.join(_repo_root, "多音字.txt")' in src
        assert "_read_polyphone_file()" in src
        assert "_save_polyphone_file" in src
        seg = src[src.find("_poly_box = ui.textarea"):src.find("def _save_both_fixes")]
        assert "polyphone_map" not in seg, "前端不再读写 polyphone_map"

    def test_my_tts_polyphone_rewrite_retired(self):
        """合成前端不再做多音字拼写改写（防拼写串污染 g2p 输入）。"""
        src = _read("utils/audio_handle/my_tts.py")
        proc = src[src.find("async def _gpt_sovits_inproc"):src.find("async def edge_tts_api")]
        assert 'params_cfg.get("polyphone_map")' not in proc


class TestGptSovitsWiring:
    """my_tts._gpt_sovits_inproc 接线源级守卫。"""

    def test_apply_injected_before_synthesis(self):
        src = _read("utils/audio_handle/my_tts.py")
        proc = src[src.find("async def _gpt_sovits_inproc"):src.find("async def edge_tts_api")]
        assert "apply_text_fixes(" in proc, "念法纠正/多音字未接入 inproc 合成前端"
        # params_cfg 现读（TTS 页即时配置生效，不依赖缓存）
        assert 'params_cfg = ' in proc
        assert proc.index("params_cfg = ") < proc.index("apply_text_fixes(")
        # 应用点在引擎合成调用之前（流式/普通两路共用 text）
        assert proc.index("apply_text_fixes(") < proc.index("engine.synthesize")

    def test_streaming_branch_shares_fixed_text(self):
        """流式分段分支与普通分支共用纠正后的 text（同一变量）。"""
        src = _read("utils/audio_handle/my_tts.py")
        proc = src[src.find("async def _gpt_sovits_inproc"):src.find("async def edge_tts_api")]
        assert "text = apply_text_fixes(" in proc
        assert proc.index("text = apply_text_fixes(") < proc.index("_seg_writer")


class TestFrontendSaveChain:
    """前端保存链守卫（修复刷新丢失回归）。"""

    def _tts_src(self):
        return _read("frontend/ui/tabs/tts.py")

    def test_blur_save_bound(self):
        """失焦保存绑定（on('blur')）——on_change 每键 WebSocket 竞态退役。"""
        src = self._tts_src()
        assert "_fix_box.on('blur'" in src
        assert "_poly_box.on('blur'" in src

    def test_explicit_save_button(self):
        """显式保存按钮兜底（防刷新竞态）。"""
        src = self._srd = self._tts_src()
        assert "_save_both_fixes" in src
        assert "保存念法纠正/多音字" in src

    def test_zero_hit_warning(self):
        """解析 0 命中负反馈（格式问题可见，不再静默丢）。"""
        src = self._tts_src()
        assert "未识别到有效条目" in src

    def test_per_key_onchange_retired(self):
        """两框不再用 on_change 每键绑定（竞态窗口来源；注释提及不算）。"""
        src = self._tts_src()
        seg = src[src.find("_fix_box = ui.textarea"):src.find("def _save_both_fixes")]
        assert "on_change=" not in seg
