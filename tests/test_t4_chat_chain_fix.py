# -*- coding: UTF-8 -*-
"""
T4 聊天链三 bug 修复回归测试（/autoplan 批准批次 main-20261020-180346）

覆盖：
1. chat_handler datetime import 缺失（NameError 曾被外层 try 静默吞掉、中断回复链）
2. DB 写窄 try 隔离（ENG H-1：写库失败不中断回复链）
3. chat_type=game 分支防御（GameControl 已移除，不再 None.parse_keys AttributeError）
4. conversation_manager 契约（ENG H-2：传 dict 走 talk 通道，与弹幕路径同构）
"""

import pytest
from unittest.mock import MagicMock, patch

from utils.handlers.chat_handler import ChatHandler
from utils import conversation_manager as cm_module


def _make_my_handle():
    """构造最小 My_handle 替身：只需 talk_handle 链路触达的属性。"""
    mh = MagicMock()
    mh.config.get = MagicMock(side_effect=lambda *keys: _config_get(mh.config, keys))
    mh.common.get_bj_time.return_value = "2026-10-20 12:00:00"
    mh.integral_handle.return_value = False
    mh.blacklist_handle.return_value = False  # T7 接线后 talk/comment 入口有黑名单拦截
    mh.prohibitions_handle.side_effect = lambda x: x
    mh.comment_check_and_replace.side_effect = lambda x: x
    mh.common.is_punctuation_string.return_value = False
    mh.local_qa_handle.return_value = False
    mh.choose_song_handle.return_value = False
    mh.chat_type_list = ["custom_llm"]
    return mh


def _config_get(config, keys):
    """模拟 Config.get(*keys) 变长键读取。"""
    node = config._cfg
    for k in keys:
        if not isinstance(node, dict) or k not in node:
            return None
        node = node[k]
    return node


@pytest.fixture
def my_handle():
    cfg = {
        "database": {"comment_enable": True},
        "webui": {"ip": "127.0.0.1", "port": 8080},
        "talk": {"show_chat_log": False},
        "key_mapping": {"type": "none"},
        "chat_type": "custom_llm",
        "audio_synthesis_type": "none",
        "translate": {"enable": False},
        "game": {"enable": True},
        "custom_llm": {},
    }
    mh = _make_my_handle()
    mh.config = MagicMock()
    mh.config._cfg = cfg
    # 绑定必须在 config 对象就位之后（side_effect 闭包读取 mh.config._cfg）
    mh.config.get = MagicMock(side_effect=lambda *keys: _config_get(mh.config, keys))
    # 2026-10-22 批次：回复模板接线后 talk_handle 多一步 apply_reply_template——
    # MagicMock 属性会污染 result["content"]，此处透传原文（模板行为由
    # test_common_config_wiring.py::TestApplyReplyTemplate 专项覆盖）
    mh.apply_reply_template = MagicMock(side_effect=lambda resp, username: resp)
    return mh


class TestDatetimeImport:
    """bug 1：chat_handler 使用 datetime.now() 但未导入 datetime。"""

    def test_chat_handler_module_exposes_datetime(self):
        # 修复前：模块内无 datetime 符号，talk_handle 开库路径 NameError
        import utils.handlers.chat_handler as ch
        assert hasattr(ch, "datetime"), "chat_handler.py 缺少 datetime 导入（T4 bug 1 未修复）"

    def test_talk_handle_survives_db_logging(self, my_handle):
        """开库（comment_enable=true）时 talk_handle 走过写库点而不抛 NameError。"""
        handler = ChatHandler(my_handle)
        my_handle.db.execute.return_value = None
        # talk_handle 调用 ChatHandler 自身的 llm_handle——mock 到 handler 实例
        handler.llm_handle = MagicMock(return_value=None)
        handler.talk_handle({"username": "tester", "content": "你好"})
        # 到达 LLM 调用即证明写库点已通过（NameError 曾在此前中断整链）
        handler.llm_handle.assert_called_once()


class TestDbWriteIsolation:
    """ENG H-1：DB 写失败仅丢日志，不中断回复链。"""

    def test_db_failure_does_not_break_reply_chain(self, my_handle):
        handler = ChatHandler(my_handle)
        my_handle.db.execute.side_effect = Exception("database is locked")
        handler.llm_handle = MagicMock(return_value="回复内容")
        result = handler.talk_handle({"username": "tester", "content": "你好"})
        # DB 写炸了，回复链仍然走完：LLM 被调用、音频合成收到消息
        handler.llm_handle.assert_called_once()
        my_handle.audio_synthesis_handle.assert_called_once()
        assert result is not None and result["content"] == "回复内容"


class TestGameBranchDefense:
    """bug 3：chat_type=game 时 my_handle.game 为 None，GameControl 已移除。"""

    def test_game_branch_no_attribute_error(self, my_handle):
        handler = ChatHandler(my_handle)
        # my_handle.game 为 None（GPT_MODEL.get("game") 返回 None 的真实行为）
        my_handle.game = None
        my_handle.config._cfg["chat_type"] = "game"  # 命中 game 分支
        my_handle.chat_type_list = []
        # 修复前：None.parse_keys... → AttributeError；修复后：警告日志 + 正常 return
        result = handler.talk_handle({"username": "tester", "content": "左 右 上"})
        assert result is None  # game 分支直接 return，无异常即通过

    def test_comment_handler_game_branch_no_attribute_error(self):
        """comment_handler.py:323-326 同款分支同步修复。"""
        import inspect
        from utils.handlers import comment_handler
        src = inspect.getsource(comment_handler)
        assert "self.my_handle.game.parse_keys" not in src, (
            "comment_handler 仍直接调用 my_handle.game.parse_keys（None 崩溃风险）"
        )


class TestConversationManagerContract:
    """ENG H-2：聊天输入传 dict 走 talk 通道（原为 (str, "comment") 双重错误）。"""

    def test_talk_handle_passes_dict_on_talk_channel(self):
        manager = cm_module.ConversationManager(config=MagicMock(), common=MagicMock())
        manager.my_handle = MagicMock()
        manager.config.get.return_value = "local_user"
        manager.check_talk_awake = MagicMock(return_value={"ret": 0, "is_talk_awake": True, "first": False, "trigger_word": None})

        result = manager.talk_handle("你好")

        assert result["success"] is True
        manager.my_handle.process_data.assert_called_once()
        args, kwargs = manager.my_handle.process_data.call_args
        data, flag = args[0], args[1]
        assert isinstance(data, dict) and data["content"] == "你好" and "username" in data
        assert flag == "talk", "聊天输入应走 talk 通道（chat_handler.talk_handle），而非 comment（弹幕链）"
