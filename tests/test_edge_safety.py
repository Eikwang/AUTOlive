# -*- coding: UTF-8 -*-
"""
边界与安全批测试（/autoplan 批准批次切片 2）

- E2 播放阈值饥饿路径：首次触发阈值=N 时突发仅产出 N-1 条，flush 后可播
- E4 违禁词文件编码：GBK 解码失败行内报错不静默空表；UTF-8 BOM 正常
- S4 回复侧违禁词过滤：既有 prohibitions_handle 行为锚（回复进 TTS 前丢弃式过滤）
"""

import logging
import threading
import time

import pytest

from utils.audio.queue_manager import QueueMixin
from utils.common import Common
from utils.handlers.comment_handler import CommentHandler


class _FakeConfig:
    """data_priority_insert 依赖的 filter 配置桩"""

    def __init__(self, priority_mapping=None, min_start_play=3, max_len=100):
        self._data = {
            ("filter", "priority_mapping"): priority_mapping if priority_mapping is not None else {},
            ("filter", "voice_tmp_path_queue_min_start_play"): min_start_play,
            ("filter", "voice_tmp_path_queue_max_len"): max_len,
        }

    def get(self, *keys):
        return self._data[keys]


class _QueueFixture(QueueMixin):
    """手工组装 QueueMixin 依赖的队列属性（不拉起 Audio 全量依赖）"""

    def __init__(self, config=None):
        self.message_queue = []
        self.message_queue_lock = threading.Lock()
        self.message_queue_not_empty = threading.Condition(lock=self.message_queue_lock)
        self.voice_tmp_path_queue = []
        self.voice_tmp_path_queue_lock = threading.Lock()
        self.voice_tmp_path_queue_not_empty = threading.Condition(lock=self.voice_tmp_path_queue_lock)
        self.voice_tmp_path_queue_not_empty_flag = False
        if config is not None:
            self.config = config


@pytest.fixture
def queue_fx():
    return _QueueFixture(config=_FakeConfig())


def _audio_item(i):
    return {"type": "comment", "voice_path": f"{i}.wav"}


# ---------------------------------------------------------------------------
# E2 饥饿路径 flush
# ---------------------------------------------------------------------------

class TestE2StarvationFlush:
    def test_flush_starved_queue_wakes_consumer(self, queue_fx):
        """E2 核心用例：突发仅产出 N-1 条（未达阈值，生产者永不 notify）——
        flush 后消费者取到全部积压音频"""
        threshold = 3
        consumed = []
        stop = threading.Event()

        def consumer():
            # 复刻 only_play_audio 的等待-消费骨架
            while not stop.is_set():
                with queue_fx.voice_tmp_path_queue_lock:
                    while not queue_fx.voice_tmp_path_queue:
                        if not queue_fx.voice_tmp_path_queue_not_empty.wait(timeout=5):
                            if stop.is_set():
                                return
                            continue  # 超时重查，防漏 notify 挂死
                    consumed.append(queue_fx.voice_tmp_path_queue.pop(0))

        t = threading.Thread(target=consumer, daemon=True)
        t.start()

        # 模拟生产者：flag=False 且队列未达阈值时入队 2 条不 notify（现网行为）
        with queue_fx.voice_tmp_path_queue_lock:
            for i in range(threshold - 1):
                queue_fx.voice_tmp_path_queue.append(_audio_item(i))

        # 合成线程突发结束点调用 flush
        assert queue_fx.flush_starved_playback_queue() is True

        deadline = time.time() + 5
        while len(consumed) < threshold - 1 and time.time() < deadline:
            time.sleep(0.01)
        stop.set()
        t.join(timeout=5)

        assert len(consumed) == threshold - 1
        assert queue_fx.voice_tmp_path_queue_not_empty_flag is True
        assert queue_fx.voice_tmp_path_queue == []

    def test_flush_noop_when_message_queue_busy(self, queue_fx):
        """待合成队列非空（突发未结束）时不放行"""
        queue_fx.voice_tmp_path_queue.append(_audio_item(0))
        queue_fx.message_queue.append({"type": "comment", "content": "待合成"})
        assert queue_fx.flush_starved_playback_queue() is False
        assert queue_fx.voice_tmp_path_queue_not_empty_flag is False

    def test_flush_noop_when_play_queue_empty(self, queue_fx):
        """待播放队列为空时无操作（metahuman 等不产音频路径安全）"""
        assert queue_fx.flush_starved_playback_queue() is False
        assert queue_fx.voice_tmp_path_queue_not_empty_flag is False

    def test_flush_noop_when_flag_already_set(self, queue_fx):
        """阈值门已开（flag 置位）时不重复置位"""
        queue_fx.voice_tmp_path_queue.append(_audio_item(0))
        queue_fx.voice_tmp_path_queue_not_empty_flag = True
        assert queue_fx.flush_starved_playback_queue() is False

    def test_threshold_notify_semantics_unchanged(self, queue_fx):
        """既有语义不回归：达阈值 N 时生产者置位标志并唤醒消费者；
        未达阈值时保持不 notify"""
        for i in range(2):
            result = queue_fx.data_priority_insert("待播放音频列表", _audio_item(i))
            assert result["code"] == 200
        assert queue_fx.voice_tmp_path_queue_not_empty_flag is False

        result = queue_fx.data_priority_insert("待播放音频列表", _audio_item(2))
        assert result["code"] == 200
        assert queue_fx.voice_tmp_path_queue_not_empty_flag is True


# ---------------------------------------------------------------------------
# E4 违禁词文件编码
# ---------------------------------------------------------------------------

class TestE4BannedWordFileEncoding:
    def test_gbk_bytes_raise_not_silent(self, tmp_path, caplog):
        """E4 核心用例：GBK 字节流→UnicodeDecodeError 上抛+error 行内报错，
        不静默回退空表（静默空表=违禁词过滤失效）"""
        gbk_file = tmp_path / "banned_gbk.txt"
        gbk_file.write_bytes("违禁词一\n违禁词二\n".encode("gbk"))

        common = Common()
        with caplog.at_level(logging.ERROR):
            with pytest.raises(UnicodeDecodeError):
                common.check_sensitive_words(str(gbk_file), "包含违禁词一的文本")

        assert any("违禁词过滤失效" in r.message for r in caplog.records)

    def test_utf8_bom_ok(self, tmp_path):
        """E4：UTF-8 带 BOM 文件正常读取，BOM 不混入首词"""
        bom_file = tmp_path / "banned_bom.txt"
        bom_file.write_bytes(b"\xef\xbb\xbf" + "违禁词一\n违禁词二\n".encode("utf-8"))

        common = Common()
        words = common.read_sensitive_word_file(str(bom_file))

        assert words == ["违禁词一", "违禁词二"]
        assert all(not w.startswith("﻿") for w in words)

    def test_utf8_plain_ok(self, tmp_path):
        """回归：纯 UTF-8（无 BOM）词表读取与命中行为不变"""
        f = tmp_path / "banned.txt"
        f.write_text("违禁词一\n", encoding="utf-8")

        common = Common()
        assert common.check_sensitive_words(str(f), "含违禁词一的内容") is True
        assert common.check_sensitive_words(str(f), "正常内容") is False

    def test_pinyin_file_gbk_raise_not_silent(self, tmp_path):
        """违禁拼音词表同读取路径：GBK 编码中文行（ASCII 行的 GBK 即合法
        UTF-8，需中文字节才能触发解码失败）同样上抛不静默"""
        gbk_file = tmp_path / "bad_pinyin_gbk.txt"
        gbk_file.write_bytes("歪进词\n歪进品银\n".encode("gbk"))

        common = Common()
        with pytest.raises(UnicodeDecodeError):
            common.check_sensitive_words3(str(gbk_file), "weijinci")


# ---------------------------------------------------------------------------
# S4 回复侧违禁词过滤（既有行为锚）
# ---------------------------------------------------------------------------

class _ConfigStub:
    def __init__(self, filter_config):
        self._filter = filter_config

    def get(self, *keys):
        assert keys[0] == "filter"
        return self._filter


class _MyHandleStub:
    def __init__(self, filter_config):
        self.config = _ConfigStub(filter_config)


class TestS4ReplySideFilter:
    def test_reply_side_prohibitions_discard(self):
        """S4 行为锚：LLM 回复进 TTS 前经 prohibitions_handle 丢弃式过滤
        （comment_handler 回复路径 / chat_handler.py 回复路径，注释已标注登记点）"""
        handler = CommentHandler(_MyHandleStub({"enable": True, "prohibitions": ["违禁词"]}))
        assert handler.prohibitions_handle("包含违禁词的回复") is None
        assert handler.prohibitions_handle("正常回复内容") == "正常回复内容"

    def test_reply_side_filter_disabled_passthrough(self):
        """过滤总开关关闭时原样透传（既有语义）"""
        handler = CommentHandler(_MyHandleStub({"enable": False, "prohibitions": ["违禁词"]}))
        assert handler.prohibitions_handle("包含违禁词的回复") == "包含违禁词的回复"
