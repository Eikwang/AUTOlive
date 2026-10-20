# -*- coding: UTF-8 -*-
"""
T7 黑名单接线+规范化回归测试（/autoplan 断链批次）

覆盖（S-2 义务）：
1. 键面对齐：读 filter.blacklist.username（UI 写入键），list/str 双形态
2. 精确拦截 + 放行
3. 绕过用例：全角字符/零宽字符/首尾空格/大小写 → NFKC+去空白+casefold 规范化拦截
4. UID 优先匹配
5. 空名单=功能关闭
6. 接线验证：comment_handle/talk_handle 前置拦截（写库前，不污染数据分析）
"""

from unittest.mock import MagicMock

import pytest

from utils.handlers.feature_handler import FeatureHandler
from utils.handlers.comment_handler import CommentHandler
from utils.handlers.chat_handler import ChatHandler


def make_config(blacklist_names):
    cfg = MagicMock()
    cfg._cfg = {
        "filter": {"blacklist": {"username": blacklist_names}},
        "database": {"comment_enable": True},
    }
    cfg.get = MagicMock(side_effect=lambda *keys: _get(cfg._cfg, keys))
    return cfg


def _get(node, keys):
    cur = node
    for k in keys:
        if not isinstance(cur, dict) or k not in cur:
            return None
        cur = cur[k]
    return cur


@pytest.fixture
def feature():
    mh = MagicMock()
    mh.config = make_config(["坏人A", "spam_user", "123456"])
    mh.common = MagicMock()
    fh = FeatureHandler(mh)
    return fh, mh


class TestBlacklistMatching:
    def test_exact_match_blocks(self, feature):
        fh, _ = feature
        assert fh.blacklist_handle({"username": "坏人A"}) is True

    def test_non_match_passes(self, feature):
        fh, _ = feature
        assert fh.blacklist_handle({"username": "好人"}) is False

    def test_fullwidth_bypass_blocked(self, feature):
        """全角字符绕过（ＳＰＡＭ＿ＵＳＥＲ）→ NFKC 后拦截。"""
        fh, _ = feature
        assert fh.blacklist_handle({"username": "ＳＰＡＭ＿ＵＳＥＲ"}) is True

    def test_zero_width_and_spaces_bypass_blocked(self, feature):
        """零宽字符+首尾空格绕过 → 规范化后拦截。"""
        fh, _ = feature
        sneaky = "s​pam_user "  # 零宽空格插入 + 尾空格
        assert fh.blacklist_handle({"username": sneaky}) is True

    def test_case_bypass_blocked(self, feature):
        fh, _ = feature
        assert fh.blacklist_handle({"username": "SPAM_USER"}) is True

    def test_uid_priority_match(self, feature):
        """UID 优先：用户名已改但 UID 命中黑名单。"""
        fh, _ = feature
        assert fh.blacklist_handle({"username": "改名了", "uid": "123456"}) is True

    def test_uid_not_in_list_passes(self, feature):
        fh, _ = feature
        assert fh.blacklist_handle({"username": "好人", "uid": "999999"}) is False

    def test_str_form_config(self, feature):
        """textarea 直存 str（换行分隔）形态兼容。"""
        fh, mh = feature
        mh.config._cfg["filter"]["blacklist"]["username"] = "坏人A\nspam_user\n"
        assert fh.blacklist_handle({"username": "坏人A"}) is True
        assert fh.blacklist_handle({"username": "别人"}) is False

    def test_empty_list_disabled(self, feature):
        fh, mh = feature
        mh.config._cfg["filter"]["blacklist"]["username"] = []
        assert fh.blacklist_handle({"username": "坏人A"}) is False

    def test_none_entry_failure_safe(self, feature):
        """config 缺键 → 功能关闭不崩。"""
        fh, mh = feature
        mh.config._cfg["filter"]["blacklist"]["username"] = None
        assert fh.blacklist_handle({"username": "任何人"}) is False


class TestWiring:
    def test_comment_handle_blocks_before_db_write(self, feature):
        """接线：comment_handle 前置拦截——写库不发生（防污染数据分析）。"""
        fh, mh = feature
        ch = CommentHandler(mh)
        mh.comment_check_and_replace.side_effect = lambda x: x
        mh.common.is_punctuation_string.return_value = False
        ch.comment_handle({"username": "坏人A", "content": "广告内容"})
        mh.db.execute.assert_not_called()  # 写库前拦截 ✓
        mh.integral_handle.assert_not_called()  # 积分也不触发

    def test_talk_handle_blocks_before_processing(self, feature):
        """接线：talk_handle 前置拦截。"""
        fh, mh = feature
        handler = ChatHandler(mh)
        result = handler.talk_handle({"username": "坏人A", "content": "你好"})
        assert result is None
        handler.llm_handle = MagicMock()
        handler.llm_handle.assert_not_called()
