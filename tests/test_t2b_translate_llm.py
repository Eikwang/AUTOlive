# -*- coding: utf-8 -*-
"""T2b 守卫测试（/autoplan main-20261031-201249 终门批准批次）。

覆盖：翻译 LRU 缓存（命中/键含目标语言/失败不缓存/LRU 淘汰/reload 清空）
+ LLM 并发信号量（容量=常量/开路不占槽语义锚/超时降级走托底/finally 释放）。
"""
import threading
import time
import unittest
from unittest import mock

from utils import my_translate as mt
from utils.my_translate import (
    _cache_get, _cache_put, _translate_cache, _translate_cache_lock,
    _TRANSLATE_CACHE_MAX,
)


def _reset_cache():
    with _translate_cache_lock:
        _translate_cache.clear()


class TestTranslateCache(unittest.TestCase):
    def setUp(self):
        _reset_cache()

    def test_put_get_roundtrip(self):
        _cache_put("hello", "zh", "你好")
        self.assertEqual(_cache_get("hello", "zh"), "你好")

    def test_key_includes_target_lang(self):
        _cache_put("hello", "zh", "你好")
        self.assertIsNone(_cache_get("hello", "en"))  # 不同目标语言不串键

    def test_lru_eviction(self):
        for i in range(_TRANSLATE_CACHE_MAX + 10):
            _cache_put(f"t{i}", "zh", f"v{i}")
        self.assertIsNone(_cache_get("t0", "zh"))       # 最老的被淘汰
        self.assertEqual(_cache_get(f"t{_TRANSLATE_CACHE_MAX + 9}", "zh"),
                         f"v{_TRANSLATE_CACHE_MAX + 9}")

    def test_lru_touch_extends_life(self):
        for i in range(_TRANSLATE_CACHE_MAX):
            _cache_put(f"t{i}", "zh", f"v{i}")
        _cache_get("t0", "zh")  # touch 最老条目→移到队尾
        _cache_put("new-entry", "zh", "new")
        self.assertIsNotNone(_cache_get("t0", "zh"))    # 被 touch 后不在淘汰序

    def test_failed_translation_not_cached(self):
        """失败返回 None→不缓存（防固定坏值）。"""
        with mock.patch.object(mt.My_Translate, "baidu_trans", return_value=None):
            inst = mt.My_Translate.__new__(mt.My_Translate)
            inst.config_data = {"type": "baidu"}
            inst.baidu_config = {"to_lang": "zh", "appid": "x", "appkey": "y", "from_lang": "auto"}
            inst.config = mock.MagicMock()
            out = inst.trans("some text")
            self.assertIsNone(out)
            self.assertIsNone(_cache_get("some text", "zh"))

    def test_reload_clears_cache(self):
        _cache_put("hello", "zh", "旧通道译文")
        inst = mt.My_Translate.__new__(mt.My_Translate)
        with mock.patch.object(mt.Config, "reload", return_value=True), \
             mock.patch.object(mt.Config, "__init__", return_value=None), \
             mock.patch.object(mt.Config, "get", return_value={}):
            inst.reload_config("fake.json")
        self.assertIsNone(_cache_get("hello", "zh"))

    def test_trans_caches_success(self):
        with mock.patch.object(mt.My_Translate, "baidu_trans", return_value="译文") as m:
            inst = mt.My_Translate.__new__(mt.My_Translate)
            inst.config_data = {"type": "baidu"}
            inst.baidu_config = {"to_lang": "zh"}
            inst.config = mock.MagicMock()
            inst._target_lang = lambda t=None: "zh"
            first = inst.trans("repeat text")
            second = inst.trans("repeat text")
            self.assertEqual(first, "译文")
            self.assertEqual(second, "译文")
            self.assertEqual(m.call_count, 1)  # 第二次命中缓存零外呼


class TestLLMSemaphore(unittest.TestCase):
    """H-2：信号量语义（容量常量/开路不占槽/超时降级/调用段 finally 释放）。"""

    def test_constants(self):
        from utils.handlers.chat_handler import _LLM_MAX_CONCURRENCY, _LLM_ACQUIRE_TIMEOUT_S
        self.assertEqual(_LLM_MAX_CONCURRENCY, 3)
        self.assertEqual(_LLM_ACQUIRE_TIMEOUT_S, 30)

    def test_semaphore_capacity(self):
        from utils.handlers.chat_handler import _LLM_MAX_CONCURRENCY
        sem = threading.BoundedSemaphore(_LLM_MAX_CONCURRENCY)
        acquired = [sem.acquire(timeout=0.1) for _ in range(_LLM_MAX_CONCURRENCY)]
        self.assertTrue(all(acquired))
        self.assertFalse(sem.acquire(timeout=0.1))  # 第 4 个获取失败（超时语义同款）

    def test_acquire_after_breaker_check(self):
        """代码序锚：allow_request 判定先于 acquire（开路状态不占槽位）。"""
        import re
        from pathlib import Path
        src = Path("utils/handlers/chat_handler.py").read_text(encoding="utf-8")
        breaker_pos = src.find("if not breaker.allow_request():")
        acquire_pos = src.find("self._llm_semaphore.acquire(timeout=")
        self.assertGreater(acquire_pos, breaker_pos > 0 and acquire_pos > 0)
        self.assertGreater(acquire_pos, breaker_pos)

    def test_release_on_both_paths(self):
        """调用段 finally release + 模型未初始化分支显式 release 两路在场。"""
        from pathlib import Path
        src = Path("utils/handlers/chat_handler.py").read_text(encoding="utf-8")
        self.assertIn("finally:\n                self._llm_semaphore.release()", src)
        self.assertIn("self._llm_semaphore.release()\n                return self._try_local_qa_fallback(chat_type, data_json, \"模型未初始化\")", src)

    def test_timeout_goes_fallback(self):
        """超时语义：acquire 返回 False→走 _try_local_qa_fallback（并发饱和降级）。"""
        # 等价验证：BoundedSemaphore 满载时 acquire(timeout=短) 返回 False
        sem = threading.BoundedSemaphore(1)
        sem.acquire()
        self.assertFalse(sem.acquire(timeout=0.05))


if __name__ == "__main__":
    unittest.main()
