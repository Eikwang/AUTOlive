# -*- coding: UTF-8 -*-
"""助播改判权重保留守卫测试（2026-11-19 助播权重保留批次）

用户裁决语义：filter.priority_mapping 的 assistant_anchor_text/audio 两键只指
助播知识库（KB）匹配成功产生的消息（AI对话/聊天勾选后经 assistant_match_chain
命中的答复）；其余产线（复读/念弹幕/LLM 回复/答谢 gift·entrance·follow/定时
任务/闲时任务/积分/点歌答复）改判助播后保留原产线权重——仅换出口不改排序身份
（assistant_copy_message 经 origin_type 查原产线映射写 priority_override，
queue_manager entry_priority 链 override 优先于类型映射）。

权重取值口径：本文件映射镜像用户实际 config.json（gift "70"/schedule "90"/
read_comment "60"/comment 50/song "80"/reread 30/entrance "30"/follow 20/
idle_time_task 10 等，含字符串数字形态——config.example.json 值不同，批次
语义以上述裁决值为准）；测试内置固定映射保持封闭可复现。

覆盖：
1. 各产线改判后 priority_override == 原产线映射值
2. KB 命中产线（kb_chain / main_kb_hit / KB 音频）无 priority_override
3. 字符串数字归一与非法值不设 override
4. entry_priority 解析链 + queue_manager 入队排序（gift 改判条目先于 comment）
5. 全部接线点源级守卫（含编排 extra priority_override 500 优先不回退）
"""

import inspect
import os
import sys
import threading

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.handlers import assistant_router as ar
from utils.handlers.comment_handler import CommentHandler
from utils.handlers.event_handler import EventHandler
from utils.handlers.feature_handler import FeatureHandler
from utils.handlers.chat_handler import ChatHandler
from utils.audio.queue_manager import QueueMixin
from utils.audio.audio_priority import entry_priority

BATCH = "2026-11-19 助播权重保留批次"

# 镜像用户实际 config.json filter.priority_mapping（含字符串数字形态）
PRIORITY_MAPPING = {
    "abnormal_alarm": 1,
    "trends_copywriting": 10,
    "schedule": "90",
    "idle_time_task": 10,
    "local_qa_audio": "50",
    "entrance": "30",
    "comment": 50,
    "song": "80",
    "read_comment": "60",
    "gift": "70",
    "follow": 20,
    "key_mapping": "40",
    "integral": "30",
    "dynamic_text": 15,
    "assistant_anchor_text": 40,
    "assistant_anchor_audio": 40,
    "talk": 30,
    "reread": 30,
    "reread_top_priority": 500,
    "local_qa_text": "50",
}


class FakeConfig:
    """双形态 config 桩（get(*keys) 变长键，模拟主进程 Config 读法）。"""

    def __init__(self, mapping=None):
        self.d = {
            "assistant_anchor": {
                "enable": True,
                "username": "小助",
                "audio_synthesis_type": "edge_tts",
                "type": ["ai_chat", "chat", "read_comment", "gift", "entrance",
                         "follow", "schedule", "idle_time_task", "integral",
                         "song", "reread"],
                "local_qa": {
                    "text": {"enable": True, "format": "text",
                             "file_path": "", "similarity": 0.6},
                    "audio": {"enable": True, "type": "相似度匹配",
                              "file_path": "", "similarity": 0.5},
                },
            },
            "audio_synthesis_type": "gpt_sovits",
            "edge_tts": {"voice": "test"},
            "gpt_sovits": {"params": {}},
            "filter": {
                "priority_mapping": dict(PRIORITY_MAPPING) if mapping is None
                else mapping,
            },
        }

    def get(self, *keys):
        cur = self.d
        for k in keys:
            if isinstance(cur, dict) and k in cur:
                cur = cur[k]
            else:
                return None
        return cur


# ---------- ① 各产线改判后保留原产线映射权重 ----------

@pytest.mark.parametrize("origin_type,expected", [
    ("gift", 70), ("schedule", 90), ("read_comment", 60), ("comment", 50),
    ("song", 80), ("reread", 30), ("entrance", 30), ("follow", 20),
    ("idle_time_task", 10),
])
def test_origin_type_preserves_line_weight(origin_type, expected):
    """非 KB 产线改判：priority_override=原产线映射值（字符串数字归一为 int）。"""
    m = ar.assistant_copy_message(FakeConfig(), "内容", producer_type=origin_type,
                                  origin_type=origin_type)
    assert m["type"] == "assistant_anchor_text"  # 出口换助播
    assert m["priority_override"] == expected    # 排序身份保留原产线
    assert isinstance(m["priority_override"], int)


def test_integral_reply_preserves_comment_weight():
    """积分回复（feature_handler._integral_reply 产线）改判：原产线产出
    type=comment → origin_type="comment"（priority_mapping 遗留键 integral
    无产线消费，本批不删除不消费）。"""
    m = ar.assistant_copy_message(FakeConfig(), "积分回复", producer_type="integral",
                                  origin_type="comment")
    assert m["priority_override"] == 50
    assert "integral" not in m


def test_kb_hit_lines_keep_anchor_key_semantics():
    """KB 命中产线（kb_chain / main_kb_hit 直答）不传 origin_type → 无
    priority_override，assistant_anchor_text 映射（40）即其排序身份。"""
    kb = ar.assistant_copy_message(FakeConfig(), "KB答复", producer_type="kb_chain",
                                   hit_layer="assistant_text")
    assert "priority_override" not in kb
    main_kb = ar.assistant_copy_message(FakeConfig(), "KB直答", producer_type="main_kb_hit",
                                        hit_layer="main_kb")
    assert "priority_override" not in main_kb
    # KB 音频命中同理（comment_handler 直接构建 assistant_anchor_audio 字面量）
    assert "priority_override" not in {
        "type": "assistant_anchor_audio", "tts_type": "none"}


def test_kb_message_resolves_to_anchor_mapping_value():
    """无 override 的 KB 消息按类型映射解析=assistant_anchor_text 值（40），
    非 KB 改判消息按 override 解析=原产线值——单一源 entry_priority 对称。"""
    cfg = FakeConfig()
    kb = ar.assistant_copy_message(cfg, "KB答复", producer_type="kb_chain")
    gift = ar.assistant_copy_message(cfg, "答谢", producer_type="gift",
                                     origin_type="gift")
    pm = cfg.get("filter", "priority_mapping")
    assert entry_priority(kb, pm) == 40
    assert entry_priority(gift, pm) == 70


# ---------- ② 归一与容错 ----------

@pytest.mark.parametrize("mapping,origin,expect_override", [
    ({"gift": "70"}, "gift", 70),          # 字符串数字归一
    ({"gift": 70.5}, "gift", 70),          # 浮点值整型化（int(70.5)=70）
    ({"gift": "abc"}, "gift", None),       # 非法字符串不设 override
    ({"gift": None}, "gift", None),        # 映射值 None 不设
    ({}, "gift", None),                    # 键缺失不设
    ({"schedule": "90.5"}, "schedule", 90.5),  # 浮点串归 float（再经 int 链=90）
])
def test_override_normalization_and_tolerance(mapping, origin, expect_override):
    cfg = FakeConfig(mapping=mapping)
    m = ar.assistant_copy_message(cfg, "内容", producer_type=origin,
                                  origin_type=origin)
    if expect_override is None:
        assert "priority_override" not in m
    else:
        assert m["priority_override"] == expect_override


def test_mapping_section_missing_no_override():
    """filter.priority_mapping 节缺失 → 不设 override（不抛异常）。"""
    cfg = FakeConfig()
    cfg.d["filter"].pop("priority_mapping")
    m = ar.assistant_copy_message(cfg, "内容", producer_type="gift",
                                  origin_type="gift")
    assert "priority_override" not in m


def test_extra_override_wins_over_origin():
    """extra 含 priority_override（编排插队 500）→ 后合入优先，编排语义不变。"""
    m = ar.assistant_copy_message(FakeConfig(), "内容", producer_type="gift",
                                  origin_type="gift",
                                  extra={"priority_override": 500})
    assert m["priority_override"] == 500


def test_default_calls_unchanged():
    """存量调用（不传 origin_type）行为零变化：无 override、元数据照常。"""
    m = ar.assistant_copy_message(FakeConfig(), "内容", producer_type="gift")
    assert "priority_override" not in m
    assert m["_anchor_meta"]["producer_type"] == "gift"


# ---------- ③ entry_priority 解析链 + 队列排序 ----------

class _QueueHarness(QueueMixin):
    """最小队列桩（镜像 audio_core 类级队列形态，data_priority_insert 消息分支）。"""

    def __init__(self):
        self.config = FakeConfig()
        self.config.d["filter"]["message_queue_max_len"] = 50
        self.message_queue = []
        self.message_queue_lock = threading.Lock()
        self.message_queue_not_empty = threading.Condition(lock=self.message_queue_lock)
        self.voice_tmp_path_queue = []
        self.voice_tmp_path_queue_lock = threading.Lock()
        self.voice_tmp_path_queue_not_empty = threading.Condition(
            lock=self.voice_tmp_path_queue_lock)
        self.voice_tmp_path_queue_not_empty_flag = False


def _msg(t, content):
    return {"type": t, "content": content}


def test_queue_insert_order_gift_rerouted_before_comment():
    """入队排序按原权重：gift 改判条目（70）排在 comment（50）之前；
    KB 消息（类型映射 40）排在 comment 之后——改判仅换出口不改排序身份。"""
    harness = _QueueHarness()
    QueueMixin.data_priority_insert(harness, "等待合成消息", _msg("comment", "弹幕"))
    gift_rerouted = ar.assistant_copy_message(
        harness.config, "答谢", producer_type="gift", origin_type="gift")
    QueueMixin.data_priority_insert(harness, "等待合成消息", gift_rerouted)
    kb_msg = ar.assistant_copy_message(harness.config, "KB答复",
                                       producer_type="kb_chain",
                                       hit_layer="assistant_text")
    QueueMixin.data_priority_insert(harness, "等待合成消息", kb_msg)
    order = [e.get("priority_override", entry_priority(e, PRIORITY_MAPPING))
             for e in harness.message_queue]
    assert order == [70, 50, 40]
    assert [e["content"] for e in harness.message_queue] == ["答谢", "弹幕", "KB答复"]


def test_queue_insert_order_schedule_string_mapping_normalized():
    """字符串映射值（schedule "90"）归一后排序生效：改判条目压过 comment。"""
    harness = _QueueHarness()
    QueueMixin.data_priority_insert(harness, "等待合成消息", _msg("comment", "弹幕"))
    sched = ar.assistant_copy_message(harness.config, "定时", producer_type="schedule",
                                      origin_type="schedule")
    QueueMixin.data_priority_insert(harness, "等待合成消息", sched)
    assert harness.message_queue[0]["priority_override"] == 90


def test_queue_insert_order_invalid_mapping_falls_to_type_mapping():
    """origin_type 映射非法（"abc"）→ 不设 override → 按类型映射
    assistant_anchor_text=40 解析（回落助播键，队尾语义由调用方承担）。"""
    cfg = FakeConfig(mapping={**PRIORITY_MAPPING, "gift": "abc"})
    m = ar.assistant_copy_message(cfg, "答谢", producer_type="gift",
                                  origin_type="gift")
    assert "priority_override" not in m
    assert entry_priority(m, cfg.get("filter", "priority_mapping")) == 40


# ---------- ④ 接线点源级守卫 ----------

def _src_of(func):
    return inspect.getsource(func)


class TestWiringPoints:
    """全部 assistant_copy_message 接线点 origin_type 归类守卫（批次语义）。"""

    def test_reread_wired(self):
        assert 'origin_type="reread"' in _src_of(CommentHandler.reread_handle)

    def test_read_comment_wired(self):
        assert 'origin_type="read_comment"' in \
            _src_of(CommentHandler._read_comment_replay)

    def test_comment_handle_llm_reply_wired_twice(self):
        """LLM 回复（流式+全文两处）均归 comment 权重。"""
        src = _src_of(CommentHandler.comment_handle)
        assert src.count('origin_type="comment"') == 2

    def test_comment_handle_kb_sites_no_override(self):
        """KB 命中两站（kb_chain 文本 / assistant_anchor_audio 音频字面量）不写
        priority_override——助播键语义。"""
        src = _src_of(CommentHandler.comment_handle)
        assert '"priority_override"' not in src
        assert 'producer_type="kb_chain"' in src

    def test_thanks_wired(self):
        assert "origin_type=type2" in _src_of(EventHandler._thanks_generate)

    def test_schedule_wired(self):
        assert 'origin_type="schedule"' in _src_of(EventHandler.schedule_handle)

    def test_idle_time_task_wired(self):
        assert 'origin_type="idle_time_task"' in \
            _src_of(EventHandler.idle_time_task_handle)

    def test_main_kb_hit_no_origin(self):
        """主 KB 直答=助播键语义，不传 origin_type（源内仅注释提及不带 =）。"""
        src = _src_of(FeatureHandler.local_qa_handle)
        assert "origin_type=" not in src

    def test_song_wired(self):
        assert 'origin_type="song"' in _src_of(FeatureHandler._song_copy_message)

    def test_integral_wired_to_comment(self):
        src = _src_of(FeatureHandler._integral_reply)
        assert 'origin_type="comment"' in src
        assert 'origin_type="integral"' not in src  # 遗留键不消费

    def test_voice_llm_reply_classified_talk(self):
        """voice 源 llm_reply（ROUTE_TABLE 恒 False 不可达站点）：语义归位
        origin_type="talk"（原产线 type）。"""
        assert 'origin_type="talk"' in _src_of(ChatHandler.talk_handle)

    def test_batch_annotated(self):
        """批次注释统一落点（各接线文件可 grep 到批次短语）。"""
        for rel in ("utils/handlers/assistant_router.py",
                    "utils/handlers/comment_handler.py",
                    "utils/handlers/event_handler.py",
                    "utils/handlers/feature_handler.py",
                    "utils/handlers/chat_handler.py"):
            path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                rel)
            with open(path, encoding="utf-8") as f:
                assert BATCH in f.read(), f"{rel} 缺批次注释：{BATCH}"
