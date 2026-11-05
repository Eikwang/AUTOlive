# -*- coding: UTF-8 -*-
"""
待处理消息队列&待播放音频队列综合审查守卫（2026-10-31 队列审查批次）

审查范围（用户目标三条）：
1. 覆盖度：系统各类入队信息是否都在 filter.priority_mapping 中——
   修复两处断链：动态文案消息 type "copywriting"→"trends_copywriting"
   （原不在映射表，UI「动态文案」优先级不生效，按未识别类型队尾+满则丢弃）；
   本地问答文本 local_qa_text 补键（原未识别类型排队）。
2. 优先级逻辑正确应用队列机制（插入算法/满载四规则/999 单实例/
   priority_override——既有 D-20/A1/O7 规则测试锁定，本文件做接线锚守卫）。
3. 机制正确对接系统流程：消息/音频队列最大保留长度、首次触发播放阈值+E2
   flush、排序同步动作编排（插队音轨+打断撤旧）。

死键说明（不在守卫禁止范围，语义矩阵登记）：priority_mapping.integral /
.key_mapping 当前无独立队列生产者——积分/按键映射回复统一走 comment 通道
（feature_handler _integral_* reply / key_mapping 播报 type=comment），
保留为预留配置位。
"""

import json
import os

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def _src(rel):
    with open(os.path.join(_ROOT, rel), encoding="utf-8") as f:
        return f.read()


def _cfg():
    return json.loads(_src("config.example.json"))


# 系统实际入队生产者 type 全集（data_priority_insert("等待合成消息") 上游）：
# - comment 弹幕回复（comment_handler/feature_handler）
# - read_comment 念弹幕（comment_handler 缓冲）
# - gift/entrance/follow 答谢三件（event_handler._thanks_generate type2 变量构造）
# - schedule/idle_time_task 系统任务（event_handler）
# - talk 聊天语音（chat_handler）
# - song 点歌（feature_handler/task_scheduler 切片/choreographer）
# - reread/reread_top_priority 手动直通复读（comment_handler.reread_handle/
#   conversation_manager）
# - local_qa_audio/local_qa_text 本地问答音频/文本（feature_handler）
# - assistant_anchor_text/audio 助播（assistant_router 常量）
# - trends_copywriting 动态文案（task_scheduler；2026-10-31 修复前为 copywriting）
PRODUCER_TYPES = frozenset({
    "comment", "read_comment", "gift", "entrance", "follow",
    "schedule", "idle_time_task", "talk", "song",
    "reread", "reread_top_priority",
    "local_qa_audio", "local_qa_text",
    "assistant_anchor_text", "assistant_anchor_audio",
    "trends_copywriting",
    "dynamic_text",  # 2026-11-05 编排优化批次 d21：动态文案独立类型（权重 15 与编排面 d10 对齐）
})

# 允许的无生产者预留键（语义矩阵登记，不判死键失败）
RESERVED_TYPES = frozenset({"integral", "key_mapping"})


class TestPriorityMappingCoverage:
    """审查目标 1：各类信息都在优先级配置列表中，无遗漏。"""

    def test_producer_types_covered_by_mapping(self):
        """模板映射表覆盖全部生产者 type（遗漏=消息降级未识别排队）。"""
        mapping = _cfg()["filter"]["priority_mapping"]
        missing = PRODUCER_TYPES - set(mapping)
        assert not missing, f"priority_mapping 缺失生产者类型: {missing}"

    def test_mapping_no_unknown_keys(self):
        """映射表无未知键（配置→队列反向漂移；预留键白名单除外）。"""
        mapping = _cfg()["filter"]["priority_mapping"]
        unknown = set(mapping) - PRODUCER_TYPES - RESERVED_TYPES
        assert not unknown, f"priority_mapping 存在无生产者的未知键: {unknown}"

    def test_trends_copywriting_type_aligned(self):
        """动态文案 type 断链修复（2026-10-31）：消息 type 对齐映射表键；
        识别中枢源参数保留 "copywriting"（keyword_hub.SYSTEM_SOURCES 命名）。"""
        src = _src("utils/task_scheduler.py")
        assert '"type": "copywriting"' not in src
        assert '"type": "trends_copywriting"' in src
        # 2026-11-01 动作编排批次 T3：调用包 try 保护（异常不杀文案播放链），
        # 缩进随内层 try 加深——断言同步对齐
        assert 'keyword_recognize(\n                                    {"content": resp_content}, "copywriting")' in src

    def test_local_qa_text_wired(self):
        """本地问答文本补键（2026-10-31）：模板键 + UI 行 + 三预设。"""
        assert "local_qa_text" in _cfg()["filter"]["priority_mapping"]
        from frontend.ui.tabs.filter_queue import PRIORITY_GROUPS, PRIORITY_PRESETS
        group_keys = {k for _, items in PRIORITY_GROUPS for k, _ in items}
        assert "local_qa_text" in group_keys
        for name, vals in PRIORITY_PRESETS.items():
            assert "local_qa_text" in vals, f"预设「{name}」缺 local_qa_text"

    def test_ui_groups_match_mapping(self):
        """UI 常量表与映射表键集同步（防未来再漂移）。"""
        from frontend.ui.tabs.filter_queue import PRIORITY_GROUPS
        group_keys = {k for _, items in PRIORITY_GROUPS for k, _ in items}
        mapping = set(_cfg()["filter"]["priority_mapping"])
        assert group_keys == mapping, \
            f"UI 缺行: {mapping - group_keys}；UI 多行: {group_keys - mapping}"

    def test_thanks_variable_type_not_shadowed(self):
        """答谢三件 type=type2 变量构造（gift/entrance/follow）不被改散——
        _thanks_generate 单点构造，改名需同步映射表+本文件 PRODUCER_TYPES。"""
        src = _src("utils/handlers/event_handler.py")
        assert '"type": type2,' in src


class TestQueueMechanismWiring:
    """审查目标 2/3：优先级逻辑与各机制在系统流程中的接线锚。"""

    def test_max_len_consumed(self):
        """两个最大保留长度键在 data_priority_insert 消费（超长丢弃/插队驱逐）。"""
        src = _src("utils/audio/queue_manager.py")
        assert 'self.config.get("filter", "message_queue_max_len")' in src
        assert 'self.config.get("filter", "voice_tmp_path_queue_max_len")' in src
        # 消息队列：满+插队位末尾→丢弃；满+插队位有效→插队驱逐队尾
        assert "高优数据插队并驱逐队尾" in src

    def test_min_start_play_threshold_and_e2_flush(self):
        """首次触发播放阈值：入队侧阈值门 + E2 饥饿 flush 放行 + 合成循环尾调用。"""
        qm = _src("utils/audio/queue_manager.py")
        assert 'self.config.get("filter", "voice_tmp_path_queue_min_start_play")' in qm
        assert "def flush_starved_playback_queue" in qm
        sm = _src("utils/audio/synthesis_manager.py")
        assert "self.flush_starved_playback_queue()" in sm

    def test_choreo_sync_wired(self):
        """排序同步动作编排：插队音轨（priority_override 固定权重，条目权重
        判定优先于类型映射）+ 打断撤旧音轨（两队列清除）。
        T2/e2（2026-11-05）：有效权重判定收敛 utils/audio/audio_priority.py
        单一源（queue 调用 entry_priority；override 抽取逻辑在该模块内）。"""
        qm = _src("utils/audio/queue_manager.py")
        assert "def _entry_priority" in qm
        assert "entry_priority(item, priority_mapping)" in qm
        ap = _src("utils/audio/audio_priority.py")
        assert 'item.get("priority_override")' in ap
        assert "def entry_is_999" in ap
        assert "def cancel_choreo_audio" in qm
        ch = _src("utils/orchestration/choreographer.py")
        assert '"priority_override": CHOREO_AUDIO_PRIORITY' in ch

    def test_priority_mapping_sole_source(self):
        """优先级单源：data_priority_insert 只读 filter.priority_mapping
        （audio_player.priority_mapping 配置节已退役，2026-10-30）。"""
        qm = _src("utils/audio/queue_manager.py")
        assert 'priority_mapping = self.config.get("filter", "priority_mapping")' in qm
        assert qm.count("priority_mapping") >= 2  # 读取+docstring 之外至少读取一处使用
