# -*- coding: UTF-8 -*-
"""
过滤语言清洁批次守卫测试（2026-11-12 过滤优化批次）

锁定两项过滤机制改造不被回退：
(a) 「用户名数字转文字」（filter.username_convert_digits_to_chinese，原
    "用户名中的数字转中文"）：用户名处理链=清洁（表情+特殊字符+回复语言外
    文字，保留集按 need_lang，中文恒保留；数字与空格恒保留）→ 数字转文字；
    清洁为空念播/合成侧以"观众"替代（原"保留原文"保底退役）
(b) 「弹幕表情过滤」（filter.emoji）：弹幕内容字符白名单清洁
    （clean_danmaku_text——表情标签+emoji+特殊字符+非中英日韩文字全部遗弃）

用户规格锚（2026-11-12 指令）：
- 用户名"爱国*122" → "爱国一二二"（先过滤特殊字符 `*`，后数字转文字）
- 特殊字符/表情/生僻语言（藏文、泰文等）全部遗弃
- 回复语言=所有→保留中英日韩；中文/粤语→仅中文；英文/日文/韩文→中文+所选语言
- 用户名全为特殊字符和表情，过滤后为空白 → 默认"观众"替代
"""
from pathlib import Path

from tests.features.test_filter_review_1030 import _FilterConfigStub, _src


# ---------------------------------------------------------------------------
# (a1) 字符级白名单过滤行为（经 clean_username / clean_danmaku_text 公共接口）
# ---------------------------------------------------------------------------


class TestLangCharWhitelist:
    def _c(self):
        from utils.common import Common
        return Common()

    def test_username_example_pipeline(self):
        """用户规格锚：用户名"爱国*122" → 清洁"爱国122" → 数字转文字
        "爱国一二二"（先过滤后转换）。"""
        c = self._c()
        cleaned = c.clean_username("爱国*122")
        assert cleaned == "爱国122"
        assert c.convert_digits_to_chinese(cleaned) == "爱国一二二"

    def test_special_chars_and_punct_discarded(self):
        """特殊字符/标点全部遗弃（含中英文标点、装饰符号）。"""
        c = self._c()
        assert c.clean_username("小明_123") == "小明123"
        assert c.clean_username("love·你") == "love你"
        assert c.clean_username("小~明！@#￥%……") == "小明"

    def test_rare_scripts_discarded(self):
        """生僻语言字符全部遗弃（藏文/泰文）。"""
        c = self._c()
        assert c.clean_username("你好སུ", "none") == "你好"   # 藏文
        assert c.clean_danmaku_text("你好ทด") == "你好"        # 泰文

    def test_digits_and_space_kept(self):
        """数字与空格与语言无关恒保留。"""
        c = self._c()
        assert c.clean_username("用户9527") == "用户9527"
        assert c.clean_username("John Smith 66") == "John Smith 66"

    def test_reply_language_keep_sets(self):
        """回复语言→保留集（中文恒保留）：所有→中英日韩；中文/粤语→仅中文；
        英文/日文/韩文→中文+所选语言。"""
        c = self._c()
        text = "小明abc カナ 안녕"
        assert c.clean_username(text, "none") == text          # 所有：全保留
        assert c.clean_username(text, "zh") == "小明"           # 中文：仅中文
        assert c.clean_username(text, "en") == "小明abc"        # 英文：中文+英文
        assert c.clean_username(text, "jp") == "小明 カナ"      # 日文：中文+日文
        assert c.clean_username(text, "ko") == "小明 안녕"      # 韩文：中文+韩文
        # 粤语→仅中文（粤字∈CJK 区段）
        assert c.clean_username("唔该hello", "yue") == "唔该"
        assert c.clean_username("唔该hello", "zh") == "唔该"

    def test_unknown_need_lang_lenient(self):
        """未知 need_lang 值宽松放行中英日韩（仅表情/特殊字符过滤生效，
        防配置异常清空用户名）。"""
        c = self._c()
        assert c.clean_username("小明abc", "fr") == "小明abc"
        assert c.clean_username("小明abc", None) == "小明abc"

    def test_danmaku_clean_behavior(self):
        """弹幕内容清洁：标点/表情/生僻语言遗弃，中英日韩与数字保留，
        恒中英日韩（不随 need_lang 变化——语种级丢弃归 lang_filter_pass）。"""
        c = self._c()
        assert c.clean_danmaku_text("主播太美了[爱心]！！！") == "主播太美了"
        assert c.clean_danmaku_text("你好😊🇨🇳✨") == "你好"
        assert c.clean_danmaku_text("hello 你好 こんにちは 안녕") == "hello 你好 こんにちは 안녕"
        assert c.clean_danmaku_text("666！") == "666"
        assert c.clean_danmaku_text("你好สวัสดี") == "你好"
        assert c.clean_danmaku_text("😊😊") == ""               # 纯表情→空串

    def test_danmaku_clean_constant_keep_set(self):
        """弹幕清洁保留集恒中英日韩：need_lang=zh 时日文韩文字符仍保留
        （按用户规格，弹幕不做回复语言级字符过滤）。"""
        c = self._c()
        assert c.clean_danmaku_text("こんにちは안녕") == "こんにちは안녕"


# ---------------------------------------------------------------------------
# (a2) 弹幕链集成：comment_check_and_replace 走 clean_danmaku_text
# ---------------------------------------------------------------------------


class TestDanmakuChainIntegration:
    def test_emoji_on_cleans_content(self):
        """弹幕表情过滤开：内容经白名单清洁（标签+emoji+标点+生僻语言遗弃）。"""
        from utils.common import Common
        from utils.handlers.comment_handler import CommentHandler

        class _MH:
            pass

        mh = _MH()
        mh.config = _FilterConfigStub({"filter": {"enable": True, "emoji": True}})
        mh.common = Common()
        h = CommentHandler(mh)
        assert h.comment_check_and_replace("主播太美了[爱心]！！！") == "主播太美了"
        assert h.comment_check_and_replace("你好😊สวัสดี") == "你好"

    def test_emoji_off_passthrough(self):
        """弹幕表情过滤关：内容原样（不清洁）。"""
        from utils.common import Common
        from utils.handlers.comment_handler import CommentHandler

        class _MH:
            pass

        mh = _MH()
        mh.config = _FilterConfigStub({"filter": {"enable": True, "emoji": False}})
        mh.common = Common()
        h = CommentHandler(mh)
        assert h.comment_check_and_replace("主播太美了[爱心]！！！") == "主播太美了[爱心]！！！"


# ---------------------------------------------------------------------------
# (a3) 接线守卫：职责重划（用户名归 username_convert 开关、弹幕内容归 emoji）
# ---------------------------------------------------------------------------


class TestWiring:
    def test_comment_check_and_replace_uses_clean_danmaku_text(self):
        src = _src("utils/handlers/comment_handler.py")
        assert "common.clean_danmaku_text(content)" in src

    def test_comment_handle_username_gate(self):
        """用户名清洁由「用户名数字转文字」开关驱动（need_lang 透传）。"""
        src = _src("utils/handlers/comment_handler.py")
        assert ('config.get("filter", "username_convert_digits_to_chinese")'
                in src)
        assert ('clean_username(\n                    username, '
                'self.my_handle.config.get("need_lang")) or username)' in src)

    def test_llm_block_convert_after_clean(self):
        """LLM 块数字转换保持链第二步（清洁之后）。"""
        src = _src("utils/handlers/comment_handler.py")
        assert "convert_digits_to_chinese(username)" in src

    def test_synthesis_single_switch_three_steps(self):
        """合成侧单开关三步链：清洁 → "观众"保底 → 数字转文字；
        filter.emoji 不再消费用户名。"""
        src = _src("utils/audio/synthesis_manager.py")
        assert ('config.get("filter", "username_convert_digits_to_chinese")'
                in src)
        assert 'self.config.get("need_lang")) or "观众")' in src
        assert src.index("clean_username(") < src.index(
            "convert_digits_to_chinese(_uname)")
        assert 'config.get("filter", "emoji")' not in src

    def test_read_comment_replay_fallback(self):
        """念播前缀兜底：用户名开关下对传入 username 再清洁，空则"观众"。"""
        src = _src("utils/handlers/comment_handler.py")
        assert 'uname = (self.my_handle.common.clean_username(\n                    uname, self.my_handle.config.get("need_lang")) or "观众")' in src

    def test_ui_label_renamed(self):
        """UI 配置名=「用户名数字转文字」（原"用户名中的数字转中文"退役）。"""
        src = _src("frontend/ui/tabs/filter_config.py")
        assert "ui.switch('用户名数字转文字'" in src
        assert "用户名中的数字转中文" not in src

    def test_key_unchanged(self):
        """配置键名保持不变（config.json 存量兼容，零迁移）。"""
        src = _src("frontend/ui/tabs/filter_config.py")
        assert 'keys=("filter", "username_convert_digits_to_chinese")' in src
        assert 'keys=("filter", "emoji")' in src


# ---------------------------------------------------------------------------
# (a4) 保底"观众"：念播前缀行为级
# ---------------------------------------------------------------------------


class TestAudienceFallback:
    def test_read_comment_replay_blank_username(self):
        """纯表情用户名：念播前缀以"观众说:"替代（原文念播会出乱码）。"""
        from unittest.mock import MagicMock

        from utils.common import Common
        from utils.handlers.comment_handler import CommentHandler

        mh = MagicMock()
        mh.config = _FilterConfigStub({
            "read_comment": {"enable": True, "read_username_enable": True,
                             "username_max_len": 10},
            "filter": {"username_convert_digits_to_chinese": True},
        })
        mh.common = Common()
        h = CommentHandler(mh)
        h._read_comment_replay("comment", "🌸🌸🌸", "你好呀")
        msg = mh.audio_synthesis_handle.call_args[0][0]
        assert msg["content"].startswith("观众说:")
        assert "你好呀" in msg["content"]

    def test_read_comment_replay_normal_username(self):
        """正常用户名：念播前缀照常（含数字转文字后的清洁名）。"""
        from unittest.mock import MagicMock

        from utils.common import Common
        from utils.handlers.comment_handler import CommentHandler

        mh = MagicMock()
        mh.config = _FilterConfigStub({
            "read_comment": {"enable": True, "read_username_enable": True,
                             "username_max_len": 10},
            "filter": {"username_convert_digits_to_chinese": True},
        })
        mh.common = Common()
        h = CommentHandler(mh)
        h._read_comment_replay("comment", "爱国*122", "点首歌")
        msg = mh.audio_synthesis_handle.call_args[0][0]
        assert msg["content"].startswith("爱国122说:")
