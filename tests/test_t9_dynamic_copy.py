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
    """运行一次文案节拍（线程体函数内循环，跑一轮后停）。"""
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
        """本地文案文件内容进入播放链（T9 核心修复：原来只记日志）。"""
        ts, mh, tmp = scheduler
        ts.config._cfg["trends_copywriting"]["prompt_change_enable"] = False  # 页级改写关（R5③）
        ts.config._cfg["trends_copywriting"]["copywriting"][0]["prompt_change_enable"] = False
        d = tmp / "文案"
        d.mkdir()
        (d / "c1.txt").write_text("大家好，欢迎来到直播间！", encoding="utf-8")

        _run_one_cycle(ts)

        mh.audio_synthesis_handle.assert_called()
        sent = mh.audio_synthesis_handle.call_args[0][0]
        assert sent["type"] == "copywriting"
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
