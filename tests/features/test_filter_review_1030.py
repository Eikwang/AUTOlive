# -*- coding: UTF-8 -*-
"""
过滤功能审查批次守卫测试（2026-10-30 过滤审查批次）

锁定「过滤」环节（弹幕监听→分类/去重→关键词解析之间的中间过滤层）三部分改
造不被回退：
(a) 六前缀后缀配置退役（弹幕触发前缀/后缀、弹幕过滤前缀/后缀、LLM 触发前缀/
    后缀——后端零消费点，语义由词表/违禁/语言过滤承担）
(b) 保留参数接线语义：最大单词数/最大字符数=超限自动截断不丢弃；用户名中的
    数字转中文（仅 LLM 输入）；弹幕表情过滤；违禁词过滤 badwords 组
    （总开关+组开关双门控；丢弃/替换双模式；拼音变体仅丢弃模式生效）
(c) 复读触发退役（弹幕关键词触发复读移除；聊天页手动直接复读保留为直通通道）

参数语义口径（用户裁决）：截断=只保留设定值前缀（max_len 按空格分词、
max_char_len 按字符），统一走 remove_extra_words（与合成侧幂等）。
"""
import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


def _src(rel: str) -> str:
    return (REPO / rel).read_text(encoding="utf-8")


class _FilterConfigStub:
    """变长键 get 配置桩（utils/config.py Config.get 同构，逐层取值）。"""

    def __init__(self, cfg):
        self._cfg = cfg

    def get(self, *keys):
        node = self._cfg
        for k in keys:
            if not isinstance(node, dict) or k not in node:
                return None
            node = node[k]
        return node


def _make_handler(filter_cfg):
    """CommentHandler + 真实 Common（词表/拼音/截断/表情全走真实实现）。"""
    from utils.common import Common
    from utils.handlers.comment_handler import CommentHandler

    class _MH:
        pass

    mh = _MH()
    mh.config = _FilterConfigStub(filter_cfg)
    mh.common = Common()
    return CommentHandler(mh)


def _word_file(tmp_path, content="违禁词\n"):
    f = tmp_path / "badwords.txt"
    f.write_text(content, encoding="utf-8")
    return str(f)


# ---------------------------------------------------------------------------
# (a) 六前缀后缀退役守卫
# ---------------------------------------------------------------------------

_RETIRED_SIX = (
    "before_must_str", "after_must_str",
    "before_filter_str", "after_filter_str",
    "before_must_str_for_llm", "after_must_str_for_llm",
)


class TestSixPrefixSuffixRetired:
    def test_comment_handler_no_consumption(self):
        """后端零消费守卫：comment_handler 不得再读六键。"""
        src = _src("utils/handlers/comment_handler.py")
        for key in _RETIRED_SIX:
            assert key not in src, f"退役键 {key} 不得回归 comment_handler"

    def test_filter_config_ui_no_widgets(self):
        """UI 守卫：filter_config 页不得再渲染六键控件。"""
        src = _src("frontend/ui/tabs/filter_config.py")
        for key in _RETIRED_SIX:
            assert key not in src, f"退役键 {key} 不得回归过滤页 UI"

    def test_config_template_no_keys(self):
        """模板守卫：config.example.json filter 节不得再含六键。"""
        cfg = json.loads(_src("config.example.json"))
        filter_cfg = cfg.get("filter", {})
        for key in _RETIRED_SIX:
            assert key not in filter_cfg, f"退役键 {key} 不得回归配置模板"

    def test_retired_keys_registered(self):
        """可见面守卫：六键 + reread 节须登记 frontend/main.py 退役清单
        （存量配置一次性告知，可手动删除）。"""
        src = _src("frontend/main.py")
        for key in _RETIRED_SIX:
            assert f'("filter", "{key}")' in src, f"退役键 {key} 未登记退役清单"
        assert '(("reread",)' in src


# ---------------------------------------------------------------------------
# (b1) 截断语义：超限自动截断只保留设定值，不丢弃
# ---------------------------------------------------------------------------


class TestTruncationSemantics:
    CFG = {"filter": {"enable": True, "max_len": 10, "max_char_len": 50}}

    def test_over_char_limit_truncated_not_discarded(self):
        """最大字符数：超限截断保留前 50 字符（旧实现整条丢弃，语义已修正）。"""
        h = _make_handler(self.CFG)
        content = "长" * 120
        out = h.comment_check_and_replace(content)
        assert out is not None, "超限弹幕必须截断而非丢弃"
        assert out == "长" * 50

    def test_over_word_limit_truncated(self):
        """最大单词数：英文空格分词超限截断为前 10 词（多余词丢弃，
        末词带省略号标记——remove_extra_words 既有实现语义）。"""
        h = _make_handler({"filter": {"enable": True, "max_len": 10, "max_char_len": 200}})
        content = " ".join(["word"] * 40)
        out = h.comment_check_and_replace(content)
        assert out is not None
        tokens = out.split()
        assert [t.rstrip(".") for t in tokens] == ["word"] * 10

    def test_within_limit_untouched(self):
        h = _make_handler(self.CFG)
        content = "正常弹幕内容"
        assert h.comment_check_and_replace(content) == content

    def test_master_switch_off_passthrough(self):
        """过滤总开关关闭：长度截断整体失效（原样返回）。"""
        h = _make_handler({"filter": {"enable": False, "max_len": 10, "max_char_len": 50}})
        assert h.comment_check_and_replace("长" * 120) == "长" * 120

    def test_empty_after_emoji_filter_guard(self):
        """纯表情弹幕被过滤成空串后，comment_handle 空守卫终止流程（不进关键词解析）。"""
        src = _src("utils/handlers/comment_handler.py")
        assert "if not content:" in src


# ---------------------------------------------------------------------------
# (b2) 表情过滤：平台标签 + Unicode emoji
# ---------------------------------------------------------------------------


class TestEmojiFilter:
    CFG = {"filter": {"enable": True, "emoji": True}}

    def test_platform_tags_removed(self):
        """「主播太美了[爱心][色]」→「主播太美了」（参数语义锚）。"""
        h = _make_handler(self.CFG)
        assert h.comment_check_and_replace("主播太美了[爱心][色]") == "主播太美了"

    def test_unicode_emoji_removed(self):
        h = _make_handler(self.CFG)
        assert h.comment_check_and_replace("你好😊🇨🇳✨") == "你好"

    def test_disabled_keeps_text(self):
        h = _make_handler({"filter": {"enable": True, "emoji": False}})
        assert h.comment_check_and_replace("主播太美了[爱心][色]") == "主播太美了[爱心][色]"


# ---------------------------------------------------------------------------
# (b3) 用户名数字转中文：仅 LLM 输入局部重绑
# ---------------------------------------------------------------------------


class TestUsernameDigitsToChinese:
    def test_convert_behavior(self):
        from utils.common import Common
        assert Common().convert_digits_to_chinese("用户9527") == "用户九五二七"

    def test_comment_handle_wiring(self):
        """接线守卫：comment_handle LLM 块读 filter.username_convert_digits_to_chinese
        并调用 convert_digits_to_chinese（仅局部重绑，写库不受影响）。"""
        src = _src("utils/handlers/comment_handler.py")
        assert 'config.get("filter", "username_convert_digits_to_chinese")' in src
        assert "convert_digits_to_chinese(username)" in src


class TestUsernameClean:
    """用户名表情/特殊字符过滤（2026-10-31 过滤优化批次）：filter.emoji
    开关同时约束弹幕内容与用户名——用户名含表情/特殊字符时语音合成念用户名
    产生大量错误语音；合成侧 audio_synthesis 为最终关口兜底（先清洁后数字
    转中文，构成用户名合成前处理链）。"""

    def test_clean_username_behavior(self):
        from utils.common import Common
        c = Common()
        # 平台标签 + emoji 标签混排
        assert c.clean_username("🌸小仙女[爱心]") == "小仙女"
        # 装饰符号（★♪ 属 emoji 杂项区，◆ 属几何图形噪音区）
        assert c.clean_username("◆★大王♪") == "大王"
        # 零宽字符 / 私用区图标 / 替换符
        assert c.clean_username("用\u200b户") == "用户"
        assert c.clean_username("名\uE000字\ufffd") == "名字"
        # 纯表情用户名 → 空串（调用方 or username 兜底保留原文）
        assert c.clean_username("[爱心]") == ""
        # 正常文字/数字/空格不受影响（数字转中文由独立开关负责）
        assert c.clean_username("用户9527") == "用户9527"
        assert c.clean_username("ABC abc 123") == "ABC abc 123"

    def test_comment_handle_wiring(self):
        """弹幕链接线：username 违禁处理后读 filter.emoji 调 clean_username。"""
        src = _src("utils/handlers/comment_handler.py")
        assert 'config.get("filter", "emoji")' in src
        assert "clean_username(username) or username" in src

    def test_synthesis_final_gate_order(self):
        """合成侧最终关口接线：clean_username 先于 convert_digits_to_chinese
        （语音对话/念弹幕回放/答谢等来源的 username 统一在此兜底）。"""
        src = _src("utils/audio/synthesis_manager.py")
        assert 'clean_username(message["username"])' in src
        assert src.index("clean_username(message") < src.index(
            'convert_digits_to_chinese(message["username"])')


# ---------------------------------------------------------------------------
# (b4) 违禁词过滤 badwords 组接线（丢弃/替换/拼音/双门控）
# ---------------------------------------------------------------------------


class TestBadwordsWiring:
    def test_discard_mode_whole_message(self, tmp_path):
        """违禁语句丢弃开：整条丢弃返回 None（违禁词路径词表命中）。"""
        h = _make_handler({"filter": {
            "enable": True,
            "badwords": {"enable": True, "discard": True,
                         "path": _word_file(tmp_path), "replace": "*"}}})
        assert h.prohibitions_handle("包含违禁词的内容") is None
        assert h.prohibitions_handle("正常内容") == "正常内容"

    def test_replace_mode_keeps_message(self, tmp_path):
        """违禁语句丢弃关：命中词替换为 badwords.replace，消息保留。"""
        h = _make_handler({"filter": {
            "enable": True,
            "badwords": {"enable": True, "discard": False,
                         "path": _word_file(tmp_path), "replace": "***"}}})
        assert h.prohibitions_handle("说违禁词的人") == "说***的人"

    def test_pinyin_homophone_discard_only(self, tmp_path):
        """拼音变体仅丢弃模式生效：discard 开→None；discard 关→不处理
        （拼音命中无法定位原文词做替换）。"""
        pinyin_file = tmp_path / "bad_pinyin.txt"
        pinyin_file.write_text("沙雕\n", encoding="utf-8")
        word_file = tmp_path / "badwords.txt"
        word_file.write_text("无关词\n", encoding="utf-8")
        cfg_discard = {"filter": {
            "enable": True,
            "badwords": {"enable": True, "discard": True,
                         "path": str(word_file),
                         "bad_pinyin_path": str(pinyin_file), "replace": "*"}}}
        h = _make_handler(cfg_discard)
        # 同音变体「砂凋」：原文精确匹配不命中，拼音命中→丢弃
        assert h.prohibitions_handle("你就是个砂凋") is None

        cfg_replace = {"filter": {
            "enable": True,
            "badwords": {"enable": True, "discard": False,
                         "path": str(word_file),
                         "bad_pinyin_path": str(pinyin_file), "replace": "*"}}}
        h2 = _make_handler(cfg_replace)
        assert h2.prohibitions_handle("你就是个砂凋") == "你就是个砂凋"

    def test_double_gating(self, tmp_path):
        """双门控：filter.enable 总开关关闭 或 badwords.enable 关闭 → 原样透传。"""
        path = _word_file(tmp_path)
        h = _make_handler({"filter": {
            "enable": False,
            "badwords": {"enable": True, "discard": True, "path": path}}})
        assert h.prohibitions_handle("含违禁词的内容") == "含违禁词的内容"
        h = _make_handler({"filter": {
            "enable": True,
            "badwords": {"enable": False, "discard": True, "path": path}}})
        assert h.prohibitions_handle("含违禁词的内容") == "含违禁词的内容"

    def test_missing_file_fail_visible(self, tmp_path):
        """词表路径缺失：过滤失效但原样返回（不崩溃、不静默拦截正常消息）。"""
        h = _make_handler({"filter": {
            "enable": True,
            "badwords": {"enable": True, "discard": True,
                         "path": str(tmp_path / "no_such_file.txt")}}})
        assert h.prohibitions_handle("正常内容") == "正常内容"

    def test_chain_order_wiring(self):
        """链路顺序守卫：弹幕链=用户名/内容违禁处理→格式检查（截断+表情）→
        空守卫；LLM 回复侧违禁处理在 TTS 合成前。"""
        src = _src("utils/handlers/comment_handler.py")
        assert "username = self.prohibitions_handle(username)" in src
        assert "content = self.prohibitions_handle(content)" in src
        assert "content = self.comment_check_and_replace(content)" in src
        assert src.index("prohibitions_handle(username)") < src.index("comment_check_and_replace(content)")
        assert "resp_content = self.prohibitions_handle(resp_content)" in src


# ---------------------------------------------------------------------------
# (c) 复读触发退役（弹幕触发）；手动直通保留
# ---------------------------------------------------------------------------


class TestRereadRetired:
    def test_no_keyword_triggered_reread_anywhere(self):
        """全后端守卫：comment_handle/keyword_hub 不得出现复读触发分发
        （结构级：无匹配方法、无分发调用、无结果字段声明；docstring 退役
        备注中的历史标识符不算回归）。"""
        ch = _src("utils/handlers/comment_handler.py")
        assert "rec.reread_type" not in ch
        hub = _src("utils/keyword_hub.py")
        assert "def _match_reread" not in hub
        assert "_match_reread(" not in hub
        assert "reread_type:" not in hub

    def test_manual_direct_passthrough(self):
        """手动直通语义：reread_handle 无门控原文直通合成（type 归一保持）。"""
        from unittest.mock import MagicMock
        from utils.handlers.comment_handler import CommentHandler

        mh = MagicMock()
        mh.config = _FilterConfigStub({"audio_synthesis_type": "none"})
        ch = CommentHandler(mh)
        assert ch.reread_handle({"username": "u", "content": "任意内容"}) is True
        msg = mh.audio_synthesis_handle.call_args[0][0]
        assert msg["type"] == "reread" and msg["content"] == "任意内容"

    def test_ui_no_reread_card(self):
        """UI 守卫：过滤页不得再有复读触发卡（手动按钮在 talk 页，保留）。"""
        src = _src("frontend/ui/tabs/filter_config.py")
        assert "复读触发" not in src
