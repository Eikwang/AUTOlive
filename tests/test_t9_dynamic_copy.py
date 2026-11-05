# -*- coding: UTF-8 -*-
"""
T9 动态文案实装回归测试（/autoplan 断链批次）

覆盖：
1. 文案实装：本地文案文件 → TTS 入队播放（原循环体只记日志）
2. LLM 改写（prompt_change_enable）+ 枚举迁移（chatgpt→custom_llm，A5）
3. LLM 不可用/失败降级播放原文案
4. S-3 注入防护：改写输入仅含本地文案+固定提示词（无弹幕注入点）+ 500 字符截断
5. 空目录跳过（D-8 静默语义）
6. 编码失败不静默（E4）
"""

import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from utils.task_scheduler import TaskScheduler


@pytest.fixture
def scheduler(tmp_path: Path):
    config = MagicMock()
    config._cfg = {
        "trends_copywriting": {
            "enable": True,
            "llm_type": "chatgpt",
            "random_play": False,
            "play_interval": 1,
            # UI-16 批次 R5③：提示词转换页级化（原逐条目键退役，消费点读页级）
            "prompt_change_enable": True,
            "prompt_change_content": "请改写下面的内容，保持含义但换个说法",
            "copywriting": [
                {"folder_path": str(tmp_path / "文案"), "prompt_change_enable": True,
                 "prompt_change_content": "请改写下面的内容，保持含义但换个说法"}
            ],
        },
        "audio_synthesis_type": "none",
        "filter": {},
        "none": {},
    }
    config.get = MagicMock(side_effect=lambda *keys: _get(config._cfg, keys))

    common = MagicMock()
    common.get_all_file_paths = lambda folder: [str(p) for p in Path(folder).glob("*")] if Path(folder).exists() else []

    my_handle = MagicMock()
    my_handle.chat_type_list = ["custom_llm"]

    ts = TaskScheduler(config=config, common=common, my_handle=my_handle)
    ts.is_running = True
    return ts, my_handle, tmp_path


def _get(node, keys):
    cur = node
    for k in keys:
        if not isinstance(cur, dict) or k not in cur:
            return None
        cur = cur[k]
    return cur


def _run_one_cycle(ts):
    """运行一次文案节拍（线程体函数内循环，跑一轮后停）。

    确定性单周期：首个播放调用即置 is_running=False（原实现依赖
    interval≥10s 暗置下限在 2.5s 窗口内只跑一轮；间隔下限修正为 1s 后
    改用确定性停止信号）。保留 2.5s 兜底 stopper 供无播放调用路径
    （空目录 D-8 静默 / 解码失败跳过）使用。
    """
    real_play = ts.my_handle.audio_synthesis_handle

    def _play_then_stop(*args, **kwargs):
        ts.is_running = False
        return real_play(*args, **kwargs)

    ts.my_handle.audio_synthesis_handle = MagicMock(side_effect=_play_then_stop)

    def stopper():
        time.sleep(2.5)
        ts.is_running = False
    t = threading.Thread(target=stopper, daemon=True)
    t.start()
    ts.start_trends_copywriting()
    ts.trends_copywriting_thread.join(timeout=6)
    t.join(timeout=1)


class TestCopywritingPlayback:
    def test_copy_file_reaches_playback(self, scheduler):
        """本地文案文件内容进入播放链（T9 核心修复：原来只记日志）。
        type=trends_copywriting（2026-10-31 队列审查批次：对齐 priority_mapping
        键——原 "copywriting" 不在映射表，UI 动态文案优先级不生效）。"""
        ts, mh, tmp = scheduler
        ts.config._cfg["trends_copywriting"]["prompt_change_enable"] = False  # 页级改写关（R5③）
        ts.config._cfg["trends_copywriting"]["copywriting"][0]["prompt_change_enable"] = False
        d = tmp / "文案"
        d.mkdir()
        (d / "c1.txt").write_text("大家好，欢迎来到直播间！", encoding="utf-8")

        _run_one_cycle(ts)

        mh.audio_synthesis_handle.assert_called()
        sent = mh.audio_synthesis_handle.call_args[0][0]
        assert sent["type"] == "trends_copywriting"
        assert "欢迎来到直播间" in sent["content"]

    def test_llm_rewrite_applied(self, scheduler):
        """prompt_change_enable=true：LLM 改写输出替代原文案，枚举 chatgpt→custom_llm。"""
        ts, mh, tmp = scheduler
        d = tmp / "文案"
        d.mkdir()
        (d / "c1.txt").write_text("原始文案内容", encoding="utf-8")
        mh.chat_handler.llm_handle = MagicMock(return_value="改写后的文案")

        _run_one_cycle(ts)

        mh.chat_handler.llm_handle.assert_called_once()
        args = mh.chat_handler.llm_handle.call_args
        assert args[0][0] == "custom_llm"  # A5 枚举迁移
        assert "原始文案内容" in args[0][1]["content"]  # S-3：输入=固定提示词+本地文案
        sent = mh.audio_synthesis_handle.call_args[0][0]
        assert sent["content"] == "改写后的文案"

    def test_llm_failure_falls_back_to_source(self, scheduler):
        """LLM 失败/无返回 → 降级播放原文案。"""
        ts, mh, tmp = scheduler
        d = tmp / "文案"
        d.mkdir()
        (d / "c1.txt").write_text("原文案兜底", encoding="utf-8")
        mh.chat_handler.llm_handle = MagicMock(return_value=None)

        _run_one_cycle(ts)

        sent = mh.audio_synthesis_handle.call_args[0][0]
        assert sent["content"] == "原文案兜底"

    def test_rewrite_input_truncated_to_500(self, scheduler):
        """S-3：超长原文案截断 500 字符（防爆 LLM/TTS）。"""
        ts, mh, tmp = scheduler
        d = tmp / "文案"
        d.mkdir()
        (d / "c1.txt").write_text("长" * 800, encoding="utf-8")
        mh.chat_handler.llm_handle = MagicMock(return_value="改写")

        _run_one_cycle(ts)

        prompt = mh.chat_handler.llm_handle.call_args[0][1]["content"]
        assert "长" * 500 in prompt and "长" * 501 not in prompt


class TestEdgeCases:
    def test_empty_folder_skips_silently(self, scheduler):
        """D-8：文案目录空 → 跳过本轮不崩溃不播报。"""
        ts, mh, tmp = scheduler
        (tmp / "文案").mkdir()
        _run_one_cycle(ts)
        mh.audio_synthesis_handle.assert_not_called()

    def test_gbk_file_readable(self, scheduler):
        """E4：GBK 编码文案文件可读（UTF-8 失败回退），不静默跳过。"""
        ts, mh, tmp = scheduler
        ts.config._cfg["trends_copywriting"]["prompt_change_enable"] = False  # 页级改写关（R5③）
        ts.config._cfg["trends_copywriting"]["copywriting"][0]["prompt_change_enable"] = False
        d = tmp / "文案"
        d.mkdir()
        (d / "c1.txt").write_bytes("GBK编码的文案".encode("gbk"))

        _run_one_cycle(ts)

        mh.audio_synthesis_handle.assert_called()
        assert "GBK编码的文案" in mh.audio_synthesis_handle.call_args[0][0]["content"]

    def test_llm_type_not_in_list_falls_back_source(self, scheduler):
        """LLM 类型不在 chat_type_list → 降级原文案（不崩）。"""
        ts, mh, tmp = scheduler
        mh.chat_type_list = []  # custom_llm 不可用
        d = tmp / "文案"
        d.mkdir()
        (d / "c1.txt").write_text("无LLM兜底", encoding="utf-8")

        _run_one_cycle(ts)

        mh.chat_handler.llm_handle.assert_not_called()
        sent = mh.audio_synthesis_handle.call_args[0][0]
        assert sent["content"] == "无LLM兜底"


# ==================== T3「关键词|文案」拆分联动（/autoplan main-20261101-213220） ====================

import unicodedata
from unittest.mock import patch as _mock_patch


@pytest.fixture
def config_chain_scheduler(scheduler):
    """config 文案列表链 fixture：copy 非空即走新链（T3 拆分作用面）。"""
    ts, mh, tmp = scheduler
    ts.config._cfg["trends_copywriting"]["copy"] = []
    # 拆分组测试关闭 LLM 改写(正交隔离;LLM 专用测试在用例内自开)
    ts.config._cfg["trends_copywriting"]["prompt_change_enable"] = False
    return ts, mh, tmp


def _set_copy(ts, entries):
    ts.config._cfg["trends_copywriting"]["copy"] = list(entries)


def _fake_choreo(accepted=False):
    ch = MagicMock()
    ch.on_keyword = MagicMock(return_value=accepted)
    return ch


class TestKeywordCopySplit:
    """格式拆分组：半角/全角/多分隔符/NFKC 规范化/向下兼容边界。"""

    def test_halfwidth_split_triggers_and_strips_prefix(self, config_chain_scheduler):
        """半角"|"：关键词投编排、文案部分（不含前缀）入播放链。"""
        ts, mh, tmp = config_chain_scheduler
        _set_copy(ts, ["一号链接|给大家介绍我们的洗洁精，分量足好用不伤手"])
        with _mock_patch("utils.orchestration.get_choreographer", return_value=_fake_choreo(True)):
            _run_one_cycle(ts)
        sent = mh.audio_synthesis_handle.call_args[0][0]
        assert sent["content"] == "给大家介绍我们的洗洁精，分量足好用不伤手"

    def test_fullwidth_separator_split(self, config_chain_scheduler):
        """全角"｜"(U+FF5C) 同样拆分（Eng E2）。"""
        ts, mh, tmp = config_chain_scheduler
        _set_copy(ts, ["一号链接｜给大家介绍洗洁精"])
        fake = _fake_choreo(True)
        with _mock_patch("utils.orchestration.get_choreographer", return_value=fake):
            _run_one_cycle(ts)
        fake.on_dynamic_text.assert_called_once_with("给大家介绍洗洁精")  # d4：内容整段直连
        sent = mh.audio_synthesis_handle.call_args[0][0]
        assert sent["content"] == "给大家介绍洗洁精"

    def test_multiple_separators_first_wins(self, config_chain_scheduler):
        """多个分隔符=首个生效，其余归文案部分。"""
        ts, mh, tmp = config_chain_scheduler
        _set_copy(ts, ["关键词|第一段|第二段"])
        fake = _fake_choreo(True)
        with _mock_patch("utils.orchestration.get_choreographer", return_value=fake):
            _run_one_cycle(ts)
        fake.on_dynamic_text.assert_called_once_with("第一段|第二段")  # d4：首分隔符后文案整段
        sent = mh.audio_synthesis_handle.call_args[0][0]
        assert sent["content"] == "第一段|第二段"

    def test_keyword_nfkc_normalized(self, config_chain_scheduler):
        """关键词侧 NFKC 规范化（全角字符归一）。"""
        ts, mh, tmp = config_chain_scheduler
        _set_copy(ts, ["１号链接｜文案内容"])  # 全角"１"
        fake = _fake_choreo(True)
        with _mock_patch("utils.orchestration.get_choreographer", return_value=fake):
            _run_one_cycle(ts)
        fake.on_dynamic_text.assert_called_once_with("文案内容")  # d4：内容整段直连（关键词字段退役出编排链）

    def test_empty_keyword_falls_back_to_full_chain(self, config_chain_scheduler):
        """关键词为空=向下兼容：整行原文走既有全链（hub 识别照旧）。"""
        ts, mh, tmp = config_chain_scheduler
        _set_copy(ts, ["|只有文案没有关键词"])
        fake = _fake_choreo(True)
        with _mock_patch("utils.orchestration.get_choreographer", return_value=fake):
            _run_one_cycle(ts)
        fake.on_keyword.assert_not_called()
        mh.keyword_recognize.assert_called()
        sent = mh.audio_synthesis_handle.call_args[0][0]
        assert sent["content"] == "|只有文案没有关键词"

    def test_empty_copy_falls_back(self, config_chain_scheduler):
        """分隔符后文案为空=向下兼容整行。"""
        ts, mh, tmp = config_chain_scheduler
        _set_copy(ts, ["关键词|"])
        fake = _fake_choreo(True)
        with _mock_patch("utils.orchestration.get_choreographer", return_value=fake):
            _run_one_cycle(ts)
        fake.on_keyword.assert_not_called()
        mh.keyword_recognize.assert_called()


class TestKeywordCopyOrthogonal:
    """触发直通组：无条件投编排+hub 跳过+文件链豁免+正交性守卫。"""

    def test_split_entry_skips_hub_and_dispatches_unconditionally(self, config_chain_scheduler):
        """格式化条目：copywriting 源 hub 识别不被调；on_keyword 未命中仍被调
        （无条件直通，spec R2①）；编排拒绝不影响文案播放。"""
        ts, mh, tmp = config_chain_scheduler
        _set_copy(ts, ["未命中词|照常播放的文案"])
        fake = _fake_choreo(False)  # no_match
        with _mock_patch("utils.orchestration.get_choreographer", return_value=fake):
            _run_one_cycle(ts)
        fake.on_dynamic_text.assert_called_once_with("照常播放的文案")  # d4：未命中仍无条件直通（spec R2①）
        mh.keyword_recognize.assert_not_called()  # 文案部分不进 hub
        sent = mh.audio_synthesis_handle.call_args[0][0]
        assert sent["content"] == "照常播放的文案"

    def test_plain_entry_keeps_hub_no_choreo(self, config_chain_scheduler):
        """非格式化条目：hub 识别照旧、on_keyword 不被调。"""
        ts, mh, tmp = config_chain_scheduler
        _set_copy(ts, ["普通文案没有分隔符"])
        fake = _fake_choreo(True)
        with _mock_patch("utils.orchestration.get_choreographer", return_value=fake):
            _run_one_cycle(ts)
        mh.keyword_recognize.assert_called_once()
        fake.on_keyword.assert_not_called()

    def test_file_chain_not_split(self, scheduler):
        """Eng E1：copywriting 文件夹链（散文）含"|"不拆分。"""
        ts, mh, tmp = scheduler
        ts.config._cfg["trends_copywriting"]["copy"] = []  # 走文件链
        ts.config._cfg["trends_copywriting"]["prompt_change_enable"] = False
        d = tmp / "文案"
        d.mkdir(exist_ok=True)
        (d / "s1.txt").write_text("散文中出现|竖线符号的段落", encoding="utf-8")
        fake = _fake_choreo(True)
        with _mock_patch("utils.orchestration.get_choreographer", return_value=fake):
            _run_one_cycle(ts)
        fake.on_keyword.assert_not_called()
        mh.keyword_recognize.assert_called()
        sent = mh.audio_synthesis_handle.call_args[0][0]
        assert sent["content"] == "散文中出现|竖线符号的段落"

    def test_orthogonal_guard_single_recognize_site(self):
        """正交性守卫：keyword_recognize 调用点全库仅 my_handle（入口定义）+
        task_scheduler（copywriting 源唯一调用点）——防二次识别点复活（Eng T-1）。"""
        import re
        from pathlib import Path
        # 各 handler 调 keyword_recognize 传 comment/talk/gift/schedule/idle_time_task;
        # copywriting 源调用点=task_scheduler 唯一(防止二次识别点复活)。
        hits = []
        for f in Path("utils").rglob("*.py"):
            text = f.read_text(encoding="utf-8", errors="replace")
            for m in re.finditer(r"keyword_recognize\([^)]*\)", text, re.S):
                if '"copywriting"' in m.group(0):
                    hits.append(f.as_posix())
        assert hits == ["utils/task_scheduler.py"], hits


class TestKeywordCopyCompat:
    """向下兼容与截断口径组。"""

    def test_truncation_applies_to_copy_part(self, config_chain_scheduler):
        """Eng E6：500 截断作用于文案部分（关键词不计入预算）。"""
        ts, mh, tmp = config_chain_scheduler
        _set_copy(ts, ["关键词|" + "好" * 560])
        fake = _fake_choreo(True)
        with _mock_patch("utils.orchestration.get_choreographer", return_value=fake):
            _run_one_cycle(ts)
        sent = mh.audio_synthesis_handle.call_args[0][0]
        assert sent["content"] == "好" * 500

    def test_plain_entry_truncation_unchanged(self, config_chain_scheduler):
        """非格式化条目截断口径不变（既有语义回归保护）。"""
        ts, mh, tmp = config_chain_scheduler
        _set_copy(ts, ["普" * 560])
        _run_one_cycle(ts)
        sent = mh.audio_synthesis_handle.call_args[0][0]
        assert sent["content"] == "普" * 500


class TestKeywordCopyDegrade:
    """降级组：LLM 失败播文案部分；改写输出含"|"不二次拆分。"""

    def test_llm_failure_plays_copy_part(self, config_chain_scheduler):
        """LLM 失败=降级播文案部分（不含关键词前缀）。"""
        ts, mh, tmp = config_chain_scheduler
        _set_copy(ts, ["关键词|待改写文案"])
        ts.config._cfg["trends_copywriting"]["prompt_change_enable"] = True
        mh.chat_handler.llm_handle = MagicMock(side_effect=Exception("LLM 不可用"))
        fake = _fake_choreo(True)
        with _mock_patch("utils.orchestration.get_choreographer", return_value=fake):
            _run_one_cycle(ts)
        sent = mh.audio_synthesis_handle.call_args[0][0]
        assert sent["content"] == "待改写文案"

    def test_llm_output_with_pipe_not_resplit(self, config_chain_scheduler):
        """LLM 改写输出含"|"=原样播放不二次拆分（Eng E6/T-1）。"""
        ts, mh, tmp = config_chain_scheduler
        _set_copy(ts, ["关键词|待改写文案"])
        ts.config._cfg["trends_copywriting"]["prompt_change_enable"] = True
        mh.chat_handler.llm_handle = MagicMock(return_value="改写后|含竖线|的输出")
        fake = _fake_choreo(True)
        with _mock_patch("utils.orchestration.get_choreographer", return_value=fake):
            _run_one_cycle(ts)
        fake.on_dynamic_text.assert_called_once_with("改写后|含竖线|的输出")  # d4：LLM 改写后整段直连
        sent = mh.audio_synthesis_handle.call_args[0][0]
        assert sent["content"] == "改写后|含竖线|的输出"
