# -*- coding: UTF-8 -*-
"""
念弹幕功能审查守卫（2026-10-31 念弹幕审查批次）

审查结论落实：
1. 周期性触发退役（用户裁决：系统已有限入/去重/优先级队列，不需要定时回放）
   ——task_data["read_comment"] 缓冲、timer_handler 周期分支、UI 周期区、
   config periodic_trigger 子节、settings 迁移逻辑全链删除；
   voice_change 死键（变声退役遗留，后端从未消费）一并删除。
2. 即时复读接线（功能语义"处理弹幕消息时复读，位于优先级排序之后"）：
   过滤链完成的弹幕直接构造 type=read_comment 消息入 audio_synthesis_handle
   （待合成队列按 filter.priority_mapping.read_comment=20 排序）；
   助播分流判定从 timer 回放点前移至即时入队点（D5 语义不变）。
3. 念用户名三参数接线（此前 UI 有配置后端零消费）：
   - read_username_enable：开=念用户名文案（随机一条 {username} 占位替换）
     +弹幕内容；关=仅念内容
   - username_max_len：超长截断加省略号（对齐 reply_template G6 口径）
   - read_username_copywriting：随机文案，空表/非法行过滤，兜底 "用户名说:"
"""

import json
import os
from unittest.mock import MagicMock

import pytest

from utils.handlers.comment_handler import CommentHandler

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def _src(rel):
    with open(os.path.join(_ROOT, rel), encoding="utf-8") as f:
        return f.read()


class _FakeConfig:
    def __init__(self, cfg):
        self._cfg = cfg

    def get(self, *keys):
        node = self._cfg
        for k in keys:
            if not isinstance(node, dict) or k not in node:
                return None
            node = node[k]
        return node


def _cfg(**rc_overrides):
    rc = {
        "enable": True,
        "read_username_enable": True,
        "username_max_len": 10,
        "read_username_copywriting": ["{username}说:", "{username}说道:"],
    }
    rc.update(rc_overrides)
    return {"read_comment": rc, "audio_synthesis_type": "none", "filter": {}}


def _make_handler(cfg=None):
    mh = MagicMock()
    mh.config = _FakeConfig(cfg if cfg is not None else _cfg())
    return CommentHandler(mh), mh


def _replay_via(handler, mh, content="主播太美了", username="用户9527", src="comment"):
    handler._read_comment_replay(src, username, content)


class TestImmediateReplayWiring:
    """即时复读接线（周期触发退役后唯一链路）。"""

    def test_enabled_comment_enqueues_immediately(self):
        """开启+弹幕源 → 立即入队 type=read_comment（不再进缓冲定时回放）。"""
        handler, mh = _make_handler()
        _replay_via(handler, mh)
        mh.audio_synthesis_handle.assert_called_once()
        msg = mh.audio_synthesis_handle.call_args[0][0]
        assert msg["type"] == "read_comment"
        assert msg["content"] == "用户9527说:主播太美了" or "主播太美了" in msg["content"]

    def test_disabled_no_enqueue(self):
        """关闭 → 不入队（直接 LLM 回复流程）。"""
        handler, mh = _make_handler(_cfg(enable=False))
        _replay_via(handler, mh)
        mh.audio_synthesis_handle.assert_not_called()

    def test_talk_source_not_replayed(self):
        """仅弹幕源复读（聊天页发送消息 src!=comment 不入）。"""
        handler, mh = _make_handler()
        _replay_via(handler, mh, src="talk")
        mh.audio_synthesis_handle.assert_not_called()

    def test_no_task_data_buffer_dependency(self):
        """缓冲机制退役：my_handle 初始化无该缓冲条目；入队消息含原文。"""
        src = _src("utils/my_handle.py")
        assert '"read_comment"' not in src
        handler, mh = _make_handler()
        _replay_via(handler, mh)
        msg = mh.audio_synthesis_handle.call_args[0][0]
        assert "主播太美了" in msg["content"]


class TestUsernameReadingParams:
    """念用户名三参数接线（此前后端零消费）。"""

    def test_username_enabled_uses_copywriting(self):
        """念用户名开：文案随机一条 {username} 占位替换 + 内容。"""
        handler, mh = _make_handler()
        _replay_via(handler, mh, username="小美")
        msg = mh.audio_synthesis_handle.call_args[0][0]
        assert msg["content"] in ("小美说:主播太美了", "小美说道:主播太美了")

    def test_username_disabled_content_only(self):
        """念用户名关：仅念内容。"""
        handler, mh = _make_handler(_cfg(read_username_enable=False))
        _replay_via(handler, mh, username="小美")
        msg = mh.audio_synthesis_handle.call_args[0][0]
        assert msg["content"] == "主播太美了"

    def test_username_max_len_truncates_with_ellipsis(self):
        """超长用户名截断加省略号（对齐 reply_template G6 口径）。"""
        handler, mh = _make_handler(_cfg(username_max_len=3))
        _replay_via(handler, mh, username="很长很长的用户名")
        msg = mh.audio_synthesis_handle.call_args[0][0]
        assert msg["content"] in ("很长很...说:主播太美了", "很长很...说道:主播太美了")

    def test_username_max_len_invalid_falls_back_10(self):
        """非法长度值容错缺省 10（不中断念弹幕链）。"""
        handler, mh = _make_handler(_cfg(username_max_len="abc"))
        _replay_via(handler, mh, username="一二三四五六七八九十十一十二")
        msg = mh.audio_synthesis_handle.call_args[0][0]
        assert msg["content"] in ("一二三四五六七八九十...说:主播太美了", "一二三四五六七八九十...说道:主播太美了")

    def test_empty_copywriting_fallback(self):
        """文案空表/非法行 → 兜底 "用户名说:"（不空念）。"""
        handler, mh = _make_handler(_cfg(read_username_copywriting=["", "  "]))
        _replay_via(handler, mh, username="小美")
        msg = mh.audio_synthesis_handle.call_args[0][0]
        assert msg["content"] == "小美说:主播太美了"

    def test_username_placeholder_replaced(self):
        """{username} 占位符替换（无占位的文案直接拼接）。"""
        handler, mh = _make_handler(_cfg(read_username_copywriting=["来自"]))
        _replay_via(handler, mh, username="小美")
        msg = mh.audio_synthesis_handle.call_args[0][0]
        assert msg["content"] == "来自主播太美了"


class TestAssistantRoutingMovedForward:
    """助播分流判定前移至即时入队点（D5 语义不变：勾选 read_comment 助播
    触发 → 助播消息通道；未勾选 → 原路念弹幕）。"""

    def test_routing_call_present_at_enqueue_site(self):
        src = _src("utils/handlers/comment_handler.py")
        assert 'assistant_route(self.my_handle.config, "read_comment")' in src
        assert 'producer_type="read_comment", hit_layer="direct"' in src

    def test_routed_uses_assistant_copy_message(self, monkeypatch):
        """勾选助播触发 → assistant_copy_message 构造助播消息。"""
        import utils.handlers.assistant_router as ar
        handler, mh = _make_handler()
        monkeypatch.setattr(ar, "assistant_route", lambda cfg, p: True)
        _replay_via(handler, mh)
        msg = mh.audio_synthesis_handle.call_args[0][0]
        assert msg["type"] == "assistant_anchor_text"


class TestPeriodicTriggerRetired:
    """周期触发退役全链守卫。"""

    def test_timer_branch_removed(self):
        src = _src("utils/handlers/timer_handler.py")
        assert '"read_comment"' not in src
        # 统一退役注释短语（2026-10-31 本地问答审查批次：local_qa 收官后
        # periodic_trigger_data_handle 整体删除，念弹幕专用注释并入统一注释）
        assert "周期触发已退役" in src

    def test_template_and_ui_and_settings_clean(self):
        example = json.loads(_src("config.example.json"))
        assert "periodic_trigger" not in example["read_comment"]
        assert "voice_change" not in example["read_comment"]
        ui_src = _src("frontend/ui/tabs/read_comment.py")
        assert "periodic_trigger" not in ui_src
        assert "_read_pt" not in _src("frontend/config/settings.py")

    def test_retired_keys_registered(self):
        src = _src("frontend/main.py")
        assert '("read_comment", "periodic_trigger")' in src
        assert '("read_comment", "voice_change")' in src
