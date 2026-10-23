# -*- coding: UTF-8 -*-
"""UC-2 写库/过滤顺序测试（/autoplan 2026-10-22 流程图批次终门 uc2a 裁决，
2026-10-23 TODOS 批次实施）：违禁词过滤链移到 danmu 写库 INSERT 之前，
违禁弹幕不入库；写库内容=过滤后弹幕。双代码路径（comment_handler 弹幕链 +
chat_handler 聊天链）同规则断言。"""
import os
import sys
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from utils.handlers.comment_handler import CommentHandler  # noqa: E402
from utils.handlers.chat_handler import ChatHandler  # noqa: E402


_UNSET = object()


def _make_chain(handler_cls, filtered_content=_UNSET, blocked=False):
    """构造弹幕链 handler（mock my_handle：db 记录 INSERT、过滤可配）。

    blocked=True: prohibitions_handle 返回 None（违禁拦截）；
    filtered_content: 模拟 comment_check_and_replace 的改写结果（None=拦截）。
    """
    cfg = MagicMock()
    cfg.get = lambda *keys: {
        ("database", "comment_enable"): True,
        ("talk", "show_chat_log"): False,
        ("key_mapping", "type"): "无",
        ("chat_type",): "custom_llm",
        ("filter",): {"enable": False},
    }.get(keys, None)
    mh = MagicMock()
    mh.config = cfg

    inserts = []
    db = MagicMock()
    db.execute = lambda sql, params: inserts.append(params) if "INSERT INTO danmu" in sql else None
    mh.db = db

    def prohibitions(value):
        if blocked:
            return None
        return value
    mh.prohibitions_handle = prohibitions
    if filtered_content is not _UNSET:
        mh.comment_check_and_replace = lambda c: filtered_content
    mh.lang_filter_pass = lambda c: True
    mh.common.is_punctuation_string = lambda c: False
    mh.blacklist_handle = lambda d: False
    mh.integral_handle = lambda *a: False
    mh.key_mapping_handle = lambda *a: False

    handler = object.__new__(handler_cls)
    handler.my_handle = mh
    # CommentHandler 用类内自有过滤方法（非 my_handle 委托）——实例级 patch 对齐
    handler.prohibitions_handle = prohibitions
    if filtered_content is not _UNSET:
        handler.comment_check_and_replace = lambda c: filtered_content
    return handler, inserts


BENIGN = {"username": "小明", "content": "主播好厉害"}


class TestCommentHandlerWriteOrder:
    def test_benign_inserted_filtered(self):
        h, inserts = _make_chain(CommentHandler, filtered_content="主播好厉害(已替换)")
        h.comment_handle(dict(BENIGN))
        assert len(inserts) == 1
        assert inserts[0][1] == "主播好厉害(已替换)"  # 写库内容=过滤后

    def test_blocked_not_inserted(self):
        h, inserts = _make_chain(CommentHandler, blocked=True)
        h.comment_handle(dict(BENIGN))
        assert inserts == []  # 违禁弹幕不入库（UC-2 核心断言）

    def test_filtered_none_not_inserted(self):
        h, inserts = _make_chain(CommentHandler, filtered_content=None)
        h.comment_handle(dict(BENIGN))
        assert inserts == []

    def test_punctuation_only_not_inserted(self):
        h, inserts = _make_chain(CommentHandler)
        h.my_handle.common.is_punctuation_string = lambda c: True
        h.comment_handle(dict(BENIGN))
        assert inserts == []


class TestChatHandlerWriteOrder:
    def test_benign_inserted_filtered(self):
        h, inserts = _make_chain(ChatHandler, filtered_content="主播好厉害(已替换)")
        h.talk_handle(dict(BENIGN))
        assert len(inserts) == 1
        assert inserts[0][1] == "主播好厉害(已替换)"

    def test_blocked_not_inserted(self):
        h, inserts = _make_chain(ChatHandler, blocked=True)
        h.talk_handle(dict(BENIGN))
        assert inserts == []

    def test_punctuation_only_not_inserted(self):
        h, inserts = _make_chain(ChatHandler)
        h.my_handle.common.is_punctuation_string = lambda c: True
        h.talk_handle(dict(BENIGN))
        assert inserts == []

