# -*- coding: UTF-8 -*-
"""
关键词识别中枢守卫测试（2026-10-25 核心架构调整，flowchartTD Keyword 节点）

架构契约（docs/flowchartTD.md：所有信息源 → 关键词识别过滤 → 分发各模块）：
1. 源路由：comment/talk 全量识别；gift 礼物组+礼物点歌+编排接管；
   系统任务源（schedule/idle_time_task/copywriting）仅编排关键词
2. 消费契约：消费函数 hub_result 非 None=信任中枢结果跳过重复匹配；
   None=自行匹配（直调/既有测试兼容不回归）
3. 匹配语义不回归：签到/查询命令词、复读词表、按键映射 difflib、点歌意图
4. 词库 mtime 缓存（load_data_from_file 不再每条消息重读文件）
5. recognize 全兜底：单项异常降级默认值，消息链不中断
"""

import inspect
import os
import time
from unittest.mock import MagicMock, patch

import pytest

from utils.keyword_hub import KeywordHub, KeywordResult
from utils.handlers.feature_handler import FeatureHandler
from utils.handlers.comment_handler import CommentHandler


class _FakeConfig:
    """模拟 Config.get(*keys) 变长键逐层读取（口径同 test_t4/_config_get）。"""

    def __init__(self, cfg):
        self._cfg = cfg

    def get(self, *keys):
        node = self._cfg
        for k in keys:
            if not isinstance(node, dict) or k not in node:
                return None
            node = node[k]
        return node


_BASE_TEXT_CFG = {
    "integral": {
        "enable": True,
        "sign": {"enable": True, "cmd": ["签到"]},
        "crud": {"query": {"enable": True, "cmd": ["我的积分"]}},
    },
    "reread": {"enable": True, "keywords": ["复读"], "top_priority_keywords": ["高优"]},
    "key_mapping": {
        "enable": True, "type": "弹幕", "start_cmd": "",
        "config": [{"keywords": ["上分"], "similarity": 1,
                    "keys": "F2", "gift": ["小火箭"]}],
    },
    "local_qa": {"enable": True, "format": "text"},
    "choose_song": {
        "enable": True, "start_cmd": ["点歌"], "similarity": 1,
        "gift_song_map": {"小火箭": "孤勇者"},
    },
}


def _make_hub(cfg=None):
    """构造被测中枢：config 用字典桩，feature_handler 用 MagicMock
    （local_qa_match 默认未命中、曲库默认含 孤勇者）。"""
    mh = MagicMock()
    mh.config = _FakeConfig(cfg if cfg is not None else _BASE_TEXT_CFG)
    fh = MagicMock()
    fh.local_qa_match.return_value = None
    fh._song_song_names.return_value = ["孤勇者"]
    mh.feature_handler = fh
    return KeywordHub(mh)


# ==================== 1. 源路由 ====================


class TestSourceRouting:
    """recognize 按源路由匹配器：全量/礼物/系统任务/未知源。"""

    def test_comment_full_routing(self):
        hub = _make_hub()
        rec = hub.recognize({"content": "签到", "username": "u"}, "comment")
        assert rec.source == "comment"
        assert rec.integral_sign is True and rec.integral_query is False
        assert rec.reread_type == ""
        assert rec.song_verdict is None

    def test_comment_integral_query(self):
        hub = _make_hub()
        rec = hub.recognize({"content": "我的积分", "username": "u"}, "comment")
        assert rec.integral_query is True and rec.integral_sign is False

    def test_comment_reread_normal_and_top_priority(self):
        hub = _make_hub()
        assert hub.recognize({"content": "来复读一下"}, "comment").reread_type == "reread"
        # top_priority 优先于普通词表
        assert hub.recognize({"content": "高优复读"}, "comment").reread_type == "reread_top_priority"

    def test_comment_keymap_groups(self):
        hub = _make_hub()
        rec = hub.recognize({"content": "上分", "username": "u"}, "comment")
        assert len(rec.keymap_groups) == 1
        group, keyword = rec.keymap_groups[0]
        assert keyword == "上分" and group.get("keys") == "F2"

    def test_comment_song_intent_matched(self):
        hub = _make_hub()
        rec = hub.recognize({"content": "点歌 孤勇者", "username": "u"}, "comment")
        assert rec.song_verdict is not None
        state, song_name, _ratio = rec.song_verdict
        assert state == "matched" and song_name == "孤勇者"

    def test_talk_no_reread_with_choreo(self, monkeypatch):
        """talk 源功能集（2026-10-27 对齐 flowchartTD Capture→Keyword→动作编排）：
        复读保持不开（语音链复读自身无意义）；编排订阅启用——识别到关键词即
        并行触发画面序列。"""
        calls = []
        import utils.orchestration as orch
        monkeypatch.setattr(orch, "get_choreographer", lambda: _FakeChoreo(calls))
        hub = _make_hub()
        rec = hub.recognize({"content": "复读", "username": "u"}, "talk")
        assert rec.reread_type == ""
        assert rec.choreo_accepted is True
        assert calls == [("keyword", "复读")]
        # 积分/按键映射/点歌仍全量识别
        assert rec.integral_sign is False
        assert hub.recognize({"content": "签到", "username": "u"}, "talk").integral_sign is True

    def test_gift_source_routing(self):
        hub = _make_hub()
        rec = hub.recognize({"gift_name": "小火箭", "username": "u"}, "gift")
        assert rec.source == "gift"
        assert len(rec.keymap_gift_groups) == 1
        group, gift = rec.keymap_gift_groups[0]
        assert gift == "小火箭" and group.get("keys") == "F2"
        assert rec.gift_song == "孤勇者"
        # 文本型匹配不参与礼物源
        assert rec.keymap_groups == [] and rec.integral_sign is False

    def test_system_source_choreo_only(self):
        """系统任务三源：仅编排关键词，不触发积分/复读/按键映射/点歌。"""
        for source in ("schedule", "idle_time_task", "copywriting"):
            hub = _make_hub()
            rec = hub.recognize({"content": "签到 复读 上分 点歌 孤勇者"}, source)
            assert rec.source == source
            assert rec.integral_sign is False and rec.integral_query is False
            assert rec.reread_type == "" and rec.keymap_groups == []
            assert rec.song_verdict is None and rec.local_qa_answer == ""

    def test_unknown_source_empty_result(self):
        hub = _make_hub()
        rec = hub.recognize({"content": "签到", "username": "u"}, "like")
        assert rec.source == "like"
        assert rec.integral_sign is False and rec.reread_type == ""

    def test_reread_str_wordlist_normalization(self):
        """词表 str 双形态口径与消费侧一致（G7/reread_handle）：换行拆分；
        单行逗号串按原口径整体为一个关键词（逗号仅为换行拆空的兜底）。"""
        cfg = {**_BASE_TEXT_CFG, "reread": {"enable": True, "keywords": "词A\n词B"}}
        hub = _make_hub(cfg)
        assert hub.recognize({"content": "xx词Bxx"}, "comment").reread_type == "reread"
        cfg2 = {**_BASE_TEXT_CFG, "reread": {"enable": True, "keywords": "词A,词B"}}
        hub2 = _make_hub(cfg2)
        # 单行逗号串=一个整体关键词（原语义，不拆分）
        assert hub2.recognize({"content": "xx词Axx"}, "comment").reread_type == ""
        assert hub2.recognize({"content": "xx词A,词Bxx"}, "comment").reread_type == "reread"


# ==================== 2. 消费契约：信任中枢结果 ====================


class TestConsumerTrustsHubResult:
    """hub_result 非 None=中枢已判定，消费侧跳过重复匹配直接执行。"""

    def test_integral_sign_trusts_hub(self):
        """内容不含签到词，中枢判定命中仍计分（hub_result 通道）；None 时自匹配不命中。"""
        mh = MagicMock()
        mh.config = _FakeConfig({"integral": {"enable": True}})
        fh = FeatureHandler(mh)
        data = {"platform": "douyin", "username": "tester", "uid": "1",
                "content": "今天天气不错"}  # 内容不含签到命令词
        rec = KeywordResult(source="comment")
        rec.integral_sign = True
        with patch.object(FeatureHandler, "_integral_fetch_row", return_value=None), \
             patch.object(FeatureHandler, "_integral_db_write", return_value=True) as m_write, \
             patch.object(FeatureHandler, "_integral_tier_reply"):
            assert fh._integral_sign_handle(data, hub_result=rec) is True
            m_write.assert_called_once()
        # 兼容路径：hub_result=None 时同内容自匹配 → 不命中不写库
        with patch.object(FeatureHandler, "_integral_fetch_row", return_value=None), \
             patch.object(FeatureHandler, "_integral_db_write", return_value=True) as m_write2:
            assert fh._integral_sign_handle(data, hub_result=None) is False
            m_write2.assert_not_called()

    def test_key_mapping_trusts_hub_groups(self):
        """中枢命中组直接分派（内容无关仍触发）；空命中组不触发。"""
        mh = MagicMock()
        mh.config = _FakeConfig({
            "key_mapping": {"enable": True, "key_trigger_type": "关键词",
                            "key_single_sentence_trigger_once": False}})
        fh = FeatureHandler(mh)
        group = {"keys": "F2", "keywords": ["上分"], "similarity": 1}
        rec = KeywordResult(source="comment")
        rec.keymap_groups = [(group, "上分")]
        data = {"username": "u", "content": "完全无关的内容"}
        with patch.object(FeatureHandler, "_trigger_key_press", return_value=True) as m_press:
            assert fh.key_mapping_handle("弹幕", data, hub_result=rec) is True
            m_press.assert_called_once()
        rec_empty = KeywordResult(source="comment")
        with patch.object(FeatureHandler, "_trigger_key_press", return_value=True) as m_press2:
            assert fh.key_mapping_handle("弹幕", data, hub_result=rec_empty) is False
            m_press2.assert_not_called()

    def test_key_mapping_gift_trusts_hub_groups(self):
        """礼物分支中枢通道（gift_handle 生产链接线）。"""
        mh = MagicMock()
        mh.config = _FakeConfig({
            "key_mapping": {"enable": True, "key_trigger_type": "礼物",
                            "key_single_sentence_trigger_once": False}})
        fh = FeatureHandler(mh)
        group = {"keys": "F5", "gift": ["小火箭"]}
        rec = KeywordResult(source="gift")
        rec.keymap_gift_groups = [(group, "小火箭")]
        data = {"username": "u", "gift_name": "小火箭"}
        with patch.object(FeatureHandler, "_trigger_key_press", return_value=True) as m_press:
            assert fh.key_mapping_handle("礼物", data, hub_result=rec) is True
            m_press.assert_called_once()

    def test_reread_trusts_hub_type(self):
        """复读：中枢判定命中类型直接合成（内容无需再过词表）；None 自匹配兼容。"""
        mh = MagicMock()
        mh.config = _FakeConfig({"reread": {"enable": True},
                                 "audio_synthesis_type": "none"})
        ch = CommentHandler(mh)
        rec = KeywordResult(source="comment")
        rec.reread_type = "reread_top_priority"
        data = {"username": "u", "content": "与词表无关"}
        assert ch.reread_handle(data, type=rec.reread_type, hub_result=rec) is True
        msg = mh.audio_synthesis_handle.call_args[0][0]
        assert msg["type"] == "reread_top_priority" and msg["content"] == "与词表无关"

    def test_reread_none_self_match_compat(self):
        """hub_result=None 直调兼容：词表命中→True，未命中→False（原语义）。"""
        mh = MagicMock()
        mh.config = _FakeConfig({"reread": {"enable": True, "keywords": ["复读"]},
                                 "audio_synthesis_type": "none"})
        ch = CommentHandler(mh)
        assert ch.reread_handle({"username": "u", "content": "来复读一下"},
                                type="reread") is True
        assert ch.reread_handle({"username": "u", "content": "无关"},
                                type="reread") is False

    def test_choose_song_trusts_hub_verdict(self):
        """点歌：中枢判定意图直接走回应分支（内容无需再过触发词/曲库）。"""
        mh = MagicMock()
        mh.config = _FakeConfig({
            "choose_song": {"enable": True, "guide_copy": "已收到点歌 {content}",
                            "match_fail_copy": "没有 {content}"},
            "audio_synthesis_type": "none"})
        mh.common.merge_consecutive_asterisks.side_effect = lambda s: s
        mh.webui_show_chat_log_callback = MagicMock()
        fh = FeatureHandler(mh)
        rec = KeywordResult(source="comment")
        rec.song_verdict = ("matched", "孤勇者", 1.0)
        data = {"username": "u", "content": "与点歌无关"}
        assert fh.choose_song_handle(data, hub_result=rec) is True
        msg = mh.audio_synthesis_handle.call_args[0][0]
        assert "孤勇者" in msg["content"]
        # verdict=None → 未触发静默
        rec_none = KeywordResult(source="comment")
        mh.audio_synthesis_handle.reset_mock()
        assert fh.choose_song_handle(data, hub_result=rec_none) is False
        mh.audio_synthesis_handle.assert_not_called()

    def test_choose_song_gift_trusts_hub(self):
        """礼物点歌：中枢 gift_song 直接播报播放；未命中（空串）不触发。"""
        mh = MagicMock()
        fh = FeatureHandler(mh)
        rec = KeywordResult(source="gift")
        rec.gift_song = "孤勇者"
        data = {"username": "u", "gift_name": "小火箭"}
        with patch.object(FeatureHandler, "_song_cooldown_check", return_value=True), \
             patch.object(FeatureHandler, "_song_announce_and_play", return_value=True) as m_play:
            assert fh.choose_song_gift_trigger(data, hub_result=rec) is True
            m_play.assert_called_once_with("u", "孤勇者", "小火箭")
        rec_miss = KeywordResult(source="gift")
        with patch.object(FeatureHandler, "_song_cooldown_check", return_value=True), \
             patch.object(FeatureHandler, "_song_announce_and_play", return_value=True) as m_play2:
            assert fh.choose_song_gift_trigger(data, hub_result=rec_miss) is False
            m_play2.assert_not_called()

    def test_local_qa_trusts_hub_answer(self):
        """本地问答：中枢命中答案直接合成；空答案（未命中）静默。"""
        mh = MagicMock()
        mh.config = _FakeConfig({"chat_type": "local_qa",
                                 "local_qa": {"enable": True},
                                 "audio_synthesis_type": "none"})
        fh = FeatureHandler(mh)
        rec = KeywordResult(source="comment")
        rec.local_qa_answer = "渲染后的答案"
        data = {"username": "u", "content": "无关问题"}
        assert fh.local_qa_handle(data, hub_result=rec) is True
        msg = mh.audio_synthesis_handle.call_args[0][0]
        assert msg["content"] == "渲染后的答案"
        rec_empty = KeywordResult(source="comment")
        mh.audio_synthesis_handle.reset_mock()
        assert fh.local_qa_handle(data, hub_result=rec_empty) is False
        mh.audio_synthesis_handle.assert_not_called()


# ==================== 3. 健壮性与缓存 ====================


class TestRobustnessAndCache:
    """recognize 全兜底 + 词库 mtime 缓存。"""

    def test_recognize_never_raises(self):
        """配置读取炸裂 → 降级空结果（消息链继续），不抛异常。"""
        mh = MagicMock()
        mh.config = MagicMock()
        mh.config.get.side_effect = RuntimeError("config boom")
        hub = KeywordHub(mh)
        rec = hub.recognize({"content": "签到", "username": "u"}, "comment")
        assert isinstance(rec, KeywordResult)
        assert rec.source == "comment"
        assert rec.integral_sign is False and rec.reread_type == ""
        assert rec.keymap_groups == [] and rec.song_verdict is None

    def test_matcher_isolation_partial_degrade(self):
        """单项匹配器炸裂只损失该项，其它匹配结果保留（_safe 隔离）。"""
        hub = _make_hub()
        with patch.object(KeywordHub, "_match_reread", side_effect=RuntimeError("boom")):
            rec = hub.recognize({"content": "签到", "username": "u"}, "comment")
        assert rec.integral_sign is True  # 积分匹配不受复读匹配器异常影响
        assert rec.reread_type == ""

    def test_load_data_from_file_mtime_cache(self, tmp_path):
        """词库缓存：未变复用同对象；mtime 变化即失效重读（热改词库无需刷新）。"""
        p = tmp_path / "qa.txt"
        p.write_text("问题|答案\n", encoding="utf-8")
        fh = FeatureHandler(MagicMock())
        d1 = fh.load_data_from_file(str(p))
        # 2026-10-27 主 local_qa 收敛：加载器委托 load_qa_library，条目为
        # (question, answer, tokens) 三元组（词元随 mtime 缓存）——[0]/[1] 形状不变
        assert [(q, a) for q, a, _t in d1] == [("问题", "答案")]
        d2 = fh.load_data_from_file(str(p))
        assert d1 is d2  # 缓存命中（同一 list 对象）
        p.write_text("问题2|答案2\n", encoding="utf-8")
        st = os.stat(str(p))
        os.utime(str(p), (st.st_atime, st.st_mtime + 5))  # 确保 mtime 变化
        d3 = fh.load_data_from_file(str(p))
        assert [(q, a) for q, a, _t in d3] == [("问题2", "答案2")]


# ==================== 4. 编排订阅与链路接线（源级守卫） ====================


class _FakeChoreo:
    def __init__(self, calls):
        self._calls = calls

    def on_keyword(self, content):
        self._calls.append(("keyword", content))
        return True

    def on_gift(self, data):
        self._calls.append(("gift", data.get("gift_name")))
        return True


class TestChoreoRouting:
    """编排订阅经中枢单点触发（判定即触发，无重复调用）。"""

    def test_choreo_keyword_and_gift(self, monkeypatch):
        calls = []
        import utils.orchestration as orch
        monkeypatch.setattr(orch, "get_choreographer", lambda: _FakeChoreo(calls))
        hub = _make_hub()
        rec = hub.recognize({"content": "你好", "username": "u"}, "comment")
        assert rec.choreo_accepted is True
        rec_g = hub.recognize({"gift_name": "小火箭", "username": "u"}, "gift")
        assert rec_g.choreo_takeover is True
        assert calls == [("keyword", "你好"), ("gift", "小火箭")]

    def test_system_source_choreo_fired(self, monkeypatch):
        calls = []
        import utils.orchestration as orch
        monkeypatch.setattr(orch, "get_choreographer", lambda: _FakeChoreo(calls))
        hub = _make_hub()
        rec = hub.recognize({"content": "你好"}, "schedule")
        assert rec.choreo_accepted is True
        assert calls == [("keyword", "你好")]

    def test_idle_origin_choreo_skipped(self, monkeypatch):
        """③ 闲时自源守卫（2026-10-27）：origin=system-idle 的 comment 源跳过编排
        触发——闲时文案/LLM 触发链内容可能含编排关键词，系统自引发画面切换属非
        预期行为；LLM 对话触发语义保持（process_data 照常入链）。"""
        calls = []
        import utils.orchestration as orch
        monkeypatch.setattr(orch, "get_choreographer", lambda: _FakeChoreo(calls))
        hub = _make_hub()
        rec = hub.recognize(
            {"content": "你好", "username": "闲时任务", "origin": "system-idle"}, "comment")
        assert rec.choreo_accepted is False
        assert calls == []
        # 同内容普通弹幕照常触发（守卫只对闲时源生效）
        rec2 = hub.recognize({"content": "你好", "username": "u"}, "comment")
        assert rec2.choreo_accepted is True
        assert calls == [("keyword", "你好")]


class TestChainWiringSourceGuard:
    """链路接线源级守卫：各入口单次识别 + 消费点全部走 hub_result 通道。"""

    def test_comment_chain_single_recognition(self):
        from utils.handlers import comment_handler as mod
        src = inspect.getsource(mod)
        assert src.count('keyword_recognize(data, "comment")') == 1, \
            "弹幕链必须恰好单次调用识别中枢（零重复匹配守卫）"
        for call in ('integral_handle("comment", data, hub_result=rec)',
                     'integral_handle("crud", data, hub_result=rec)',
                     'key_mapping_handle("弹幕", data, hub_result=rec)',
                     'local_qa_handle(data, hub_result=rec)',
                     'choose_song_handle(data, hub_result=rec)',
                     'reread_handle(data, type=rec.reread_type, hub_result=rec)'):
            assert call in src, f"弹幕链消费点缺中枢通道：{call}"

    def test_comment_chain_no_legacy_inline_matching(self):
        """原弹幕链内联复读词表扫描已上收中枢（不得回归）。"""
        from utils.handlers import comment_handler as mod
        src = inspect.getsource(mod)
        assert "_top_kws" not in src and "_rd_kws" not in src
        # 消费点不得出现不带 hub_result 的弹幕链积分/按键映射调用
        assert 'integral_handle("comment", data)' not in src
        assert 'key_mapping_handle("弹幕", data)' not in src

    def test_talk_chain_single_recognition(self):
        from utils.handlers import chat_handler as mod
        src = inspect.getsource(mod)
        assert src.count('keyword_recognize(data, "talk")') == 1
        for call in ('integral_handle("comment", data, hub_result=rec)',
                     'integral_handle("crud", data, hub_result=rec)',
                     'key_mapping_handle("弹幕", data, hub_result=rec)',
                     'local_qa_handle(data, hub_result=rec)',
                     'choose_song_handle(data, hub_result=rec)'):
            assert call in src, f"语音链消费点缺中枢通道：{call}"

    def test_gift_chain_wiring(self):
        from utils.handlers import event_handler as mod
        src = inspect.getsource(mod)
        assert src.count('keyword_recognize(data, "gift")') == 1
        assert 'key_mapping_handle("礼物", data, hub_result=rec)' in src, \
            "gift 链必须消费中枢礼物组（生产链接线守卫）"
        assert 'choose_song_gift_trigger(data, hub_result=rec)' in src

    def test_system_sources_wiring(self):
        from utils.handlers import event_handler as mod
        src = inspect.getsource(mod)
        assert 'keyword_recognize(data, "schedule")' in src
        assert 'keyword_recognize(data, "idle_time_task")' in src
        from utils import task_scheduler as ts_mod
        ts_src = inspect.getsource(ts_mod)
        assert 'keyword_recognize(' in ts_src and '"copywriting"' in ts_src

    def test_my_handle_delegates_passthrough(self):
        """My_handle 委托方法必须透传 hub_result（签名守卫）。"""
        import utils.my_handle as mod
        src = inspect.getsource(mod)
        for sig in ("def local_qa_handle(self, data, hub_result=None)",
                    "def choose_song_handle(self, data, hub_result=None)",
                    "def choose_song_gift_trigger(self, data, hub_result=None)",
                    "def integral_handle(self, type, data, hub_result=None)",
                    "def key_mapping_handle(self, type, data, hub_result=None)",
                    "def reread_handle(self, data, type=None, hub_result=None)",
                    "def keyword_recognize(self, data, source)"):
            assert sig in src, f"My_handle 委托签名缺失：{sig}"
