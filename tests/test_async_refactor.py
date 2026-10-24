"""
核心模块异步化改造测试
"""

import unittest
import ast
import inspect
import os


CUSTOM_LLM_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "utils", "gpt_model", "custom_llm.py")
MY_TTS_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "utils", "audio_handle", "my_tts.py")


def read_file(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def parse_ast(path):
    return ast.parse(read_file(path))


class TestCustomLLMAsync(unittest.TestCase):
    """测试 Custom_LLM 异步化（基于 AST 分析）"""

    def setUp(self):
        self.source = read_file(CUSTOM_LLM_PATH)
        self.tree = parse_ast(CUSTOM_LLM_PATH)

    def test_no_requests_import(self):
        """测试不再使用 requests 库"""
        self.assertNotIn("import requests", self.source)
        self.assertNotIn("from requests", self.source)

    def test_has_aiohttp_import(self):
        """测试使用 aiohttp 库"""
        self.assertIn("import aiohttp", self.source)

    def test_has_asyncio_import(self):
        """测试导入 asyncio"""
        self.assertIn("import asyncio", self.source)

    def test_get_resp_is_async(self):
        """测试 get_resp 是异步方法"""
        for node in ast.walk(self.tree):
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "get_resp":
                return
        self.fail("get_resp should be an async function")

    def test_send_request_is_async(self):
        """测试 send_request 是异步方法"""
        for node in ast.walk(self.tree):
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "send_request":
                return
        self.fail("send_request should be an async function")

    def test_has_sync_wrapper(self):
        """测试有同步包装器 get_resp_sync"""
        self.assertIn("def get_resp_sync", self.source)
        # 同步包装器不应是 async
        for node in ast.walk(self.tree):
            if isinstance(node, ast.FunctionDef) and node.name == "get_resp_sync":
                return
        self.fail("get_resp_sync should be a regular function (not async)")

    def test_no_requests_request_call(self):
        """测试不使用 requests.request()"""
        self.assertNotIn("requests.request", self.source)
        self.assertNotIn("requests.get", self.source)
        self.assertNotIn("requests.post", self.source)

    def test_uses_aiohttp_session(self):
        """测试使用 aiohttp.ClientSession"""
        self.assertIn("aiohttp.ClientSession", self.source)

    def test_uses_client_timeout(self):
        """测试使用 aiohttp.ClientTimeout"""
        self.assertIn("aiohttp.ClientTimeout", self.source)

    def test_parse_headers_preserved(self):
        """测试 parse_headers 方法保留"""
        self.assertIn("def parse_headers", self.source)

    def test_replace_variables_preserved(self):
        """测试 replace_variables 方法保留"""
        self.assertIn("def replace_variables", self.source)


class TestMYTTSAsync(unittest.TestCase):
    """测试 MY_TTS 异步化"""

    def setUp(self):
        self.source = read_file(MY_TTS_PATH)

    def test_download_audio_is_async(self):
        """测试 download_audio 是异步方法"""
        self.assertIn("async def download_audio", self.source)

    def test_gpt_sovits_api_is_async(self):
        """测试 gpt_sovits_api 是异步方法"""
        self.assertIn("async def gpt_sovits_api", self.source)

    def test_blocking_call_wrapped(self):
        """测试阻塞调用被隔离出事件循环（gradio 分支已退役——兜底单通道化，
        2026-10-24；inproc 合成以 to_thread 承接同语义）"""
        self.assertNotIn("run_in_executor", self.source)  # gradio 同步调用面已删
        self.assertIn("asyncio.to_thread", self.source)

    def test_no_blocking_sync_http(self):
        """测试没有阻塞的同步 HTTP 调用"""
        # 不应该有 requests.get/post 调用
        self.assertNotIn("requests.get", self.source)
        self.assertNotIn("requests.post", self.source)

    def test_uses_aiohttp(self):
        """测试使用 aiohttp 进行 HTTP 请求"""
        self.assertIn("import aiohttp", self.source)
        self.assertIn("aiohttp.ClientSession", self.source)


class TestBackwardCompatibility(unittest.TestCase):
    """测试向后兼容性"""

    def test_custom_llm_has_sync_method(self):
        """测试 Custom_LLM 有同步方法"""
        source = read_file(CUSTOM_LLM_PATH)
        self.assertIn("def get_resp_sync", source)

    def test_my_handle_uses_sync_wrapper(self):
        """测试 chat_handler.py 使用同步包装器（模块拆分后 LLM 调用移到了 chat_handler）"""
        # 模块拆分后，LLM 调用移到了 handlers/chat_handler.py
        chat_handler_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "utils", "handlers", "chat_handler.py")
        if os.path.exists(chat_handler_path):
            source = read_file(chat_handler_path)
            # 应该使用 get_resp_sync 而不是直接调用 chat
            self.assertIn("get_resp_sync", source)
        else:
            # 如果 chat_handler.py 不存在，检查 my_handle.py（向后兼容）
            handle_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "utils", "my_handle.py")
            source = read_file(handle_path)
            self.assertIn("get_resp_sync", source)


if __name__ == '__main__':
    unittest.main()
