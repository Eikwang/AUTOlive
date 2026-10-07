# -*- coding: UTF-8 -*-
"""
测试工具模块
提供测试辅助函数和工具
"""

import os
import json
import tempfile
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
from unittest.mock import MagicMock, patch
from datetime import datetime, timedelta


class TestDataGenerator:
    """测试数据生成器"""
    
    @staticmethod
    def generate_config_data(platform: str = "talk") -> Dict[str, Any]:
        """生成配置数据"""
        return {
            "platform": platform,
            "chat_type": "custom_llm",
            "room_display_id": "278333",
            "llm": {
                "provider": "openai",
                "model": "gpt-3.5-turbo",
                "api_key": "test-key-12345"
            },
            "tts": {
                "provider": "edge-tts",
                "voice": "zh-CN-XiaoxiaoNeural"
            },
            "database": {
                "type": "sqlite",
                "path": "data/test.db"
            }
        }
    
    @staticmethod
    def generate_user_data(count: int = 5) -> List[Dict[str, Any]]:
        """生成用户数据"""
        users = []
        for i in range(count):
            users.append({
                "id": i + 1,
                "username": f"user_{i+1}",
                "email": f"user_{i+1}@example.com",
                "score": (i + 1) * 100,
                "created_at": datetime.now().isoformat()
            })
        return users
    
    @staticmethod
    def generate_question_data(count: int = 10) -> List[Dict[str, Any]]:
        """生成问题数据"""
        questions = []
        for i in range(count):
            questions.append({
                "id": i + 1,
                "question": f"测试问题 {i+1}",
                "answer": f"测试答案 {i+1}",
                "category": "test",
                "difficulty": (i % 3) + 1
            })
        return questions
    
    @staticmethod
    def generate_audio_metadata() -> Dict[str, Any]:
        """生成音频元数据"""
        return {
            "filename": "test_audio.wav",
            "duration": 10.5,
            "sample_rate": 44100,
            "channels": 2,
            "format": "wav",
            "size_bytes": 1024 * 1024  # 1MB
        }
    
    @staticmethod
    def generate_chat_message(message_type: str = "text") -> Dict[str, Any]:
        """生成聊天消息"""
        base_message = {
            "id": "msg_12345",
            "user_id": "user_1",
            "username": "test_user",
            "timestamp": datetime.now().isoformat(),
            "room_id": "room_123"
        }
        
        if message_type == "text":
            base_message.update({
                "type": "text",
                "content": "这是一条测试消息",
                "extra": {}
            })
        elif message_type == "gift":
            base_message.update({
                "type": "gift",
                "gift_name": "测试礼物",
                "gift_id": 1001,
                "gift_count": 1,
                "price": 100
            })
        elif message_type == "follow":
            base_message.update({
                "type": "follow",
                "action": "follow"
            })
        
        return base_message


class TestFileManager:
    """测试文件管理器"""
    
    def __init__(self, base_dir: Optional[str] = None):
        """初始化测试文件管理器"""
        if base_dir:
            self.base_dir = Path(base_dir)
        else:
            self.base_dir = Path(tempfile.mkdtemp(prefix="luna_test_"))
        
        self.base_dir.mkdir(parents=True, exist_ok=True)
    
    def create_temp_file(self, content: str, suffix: str = ".txt") -> Path:
        """创建临时文件"""
        temp_file = self.base_dir / f"temp_{datetime.now().strftime('%Y%m%d_%H%M%S')}{suffix}"
        temp_file.write_text(content, encoding='utf-8')
        return temp_file
    
    def create_temp_json(self, data: Dict[str, Any], filename: str = "test.json") -> Path:
        """创建临时 JSON 文件"""
        json_file = self.base_dir / filename
        with open(json_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return json_file
    
    def create_temp_directory(self, dir_name: str = "test_dir") -> Path:
        """创建临时目录"""
        temp_dir = self.base_dir / dir_name
        temp_dir.mkdir(parents=True, exist_ok=True)
        return temp_dir
    
    def cleanup(self):
        """清理测试文件"""
        if self.base_dir.exists():
            shutil.rmtree(self.base_dir, ignore_errors=True)
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.cleanup()


class MockFactory:
    """模拟对象工厂"""
    
    @staticmethod
    def create_mock_logger():
        """创建模拟日志记录器"""
        mock_logger = MagicMock()
        mock_logger.info = MagicMock()
        mock_logger.debug = MagicMock()
        mock_logger.warning = MagicMock()
        mock_logger.error = MagicMock()
        mock_logger.critical = MagicMock()
        return mock_logger
    
    @staticmethod
    def create_mock_config(config_data: Optional[Dict] = None):
        """创建模拟配置"""
        if config_data is None:
            config_data = TestDataGenerator.generate_config_data()
        
        mock_config = MagicMock()
        mock_config.config = config_data
        mock_config.__getitem__ = lambda self, key: config_data.get(key)
        mock_config.get = lambda *keys: _nested_get(config_data, keys)
        return mock_config
    
    @staticmethod
    def create_mock_database():
        """创建模拟数据库"""
        mock_db = MagicMock()
        mock_db.execute = MagicMock()
        mock_db.connection_pool = MagicMock()
        mock_db.connection_pool.qsize.return_value = 5
        mock_db.connection_pool.get.return_value = MagicMock()
        mock_db.connection_pool.put = MagicMock()
        return mock_db
    
    @staticmethod
    def create_mock_audio_player():
        """创建模拟音频播放器"""
        mock_player = MagicMock()
        mock_player.play = MagicMock()
        mock_player.stop = MagicMock()
        mock_player.pause = MagicMock()
        mock_player.resume = MagicMock()
        mock_player.set_volume = MagicMock()
        return mock_player


def _nested_get(data: Dict, keys: tuple) -> Any:
    """嵌套获取字典值"""
    result = data
    for key in keys:
        if isinstance(result, dict):
            result = result.get(key)
        else:
            return None
    return result


class AssertionHelper:
    """断言辅助类"""
    
    @staticmethod
    def assert_dict_contains(actual: Dict, expected: Dict, msg: str = ""):
        """断言字典包含预期的键值对"""
        for key, value in expected.items():
            assert key in actual, f"{msg}缺少键: {key}"
            assert actual[key] == value, f"{msg}键 {key} 的值不匹配: {actual[key]} != {value}"
    
    @staticmethod
    def assert_list_contains_all(actual: List, expected: List, msg: str = ""):
        """断言列表包含所有预期的元素"""
        for item in expected:
            assert item in actual, f"{msg}列表缺少元素: {item}"
    
    @staticmethod
    def assert_datetime_recent(dt: datetime, seconds: int = 5, msg: str = ""):
        """断言日期时间在最近 N 秒内"""
        now = datetime.now()
        diff = abs((now - dt).total_seconds())
        assert diff <= seconds, f"{msg}日期时间不在最近 {seconds} 秒内: {diff}秒"
    
    @staticmethod
    def assert_file_exists(file_path: Union[str, Path], msg: str = ""):
        """断言文件存在"""
        path = Path(file_path)
        assert path.exists(), f"{msg}文件不存在: {file_path}"
    
    @staticmethod
    def assert_file_not_empty(file_path: Union[str, Path], msg: str = ""):
        """断言文件不为空"""
        path = Path(file_path)
        assert path.exists(), f"{msg}文件不存在: {file_path}"
        assert path.stat().st_size > 0, f"{msg}文件为空: {file_path}"


class PerformanceTimer:
    """性能计时器"""
    
    def __init__(self, name: str = ""):
        self.name = name
        self.start_time = None
        self.end_time = None
    
    def __enter__(self):
        self.start_time = datetime.now()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.end_time = datetime.now()
        if self.name:
            print(f"{self.name} 耗时: {self.duration:.4f}秒")
    
    @property
    def duration(self) -> float:
        """获取持续时间（秒）"""
        if self.start_time and self.end_time:
            return (self.end_time - self.start_time).total_seconds()
        return 0.0
    
    def assert_duration_less_than(self, seconds: float, msg: str = ""):
        """断言持续时间小于指定秒数"""
        assert self.duration < seconds, f"{msg}耗时超过 {seconds} 秒: {self.duration:.4f}秒"


def create_test_environment():
    """创建测试环境"""
    test_dir = Path(tempfile.mkdtemp(prefix="luna_test_env_"))
    
    # 创建测试目录结构
    dirs = [
        "tests",
        "test_data",
        "logs",
        "data",
        "config"
    ]
    
    for dir_name in dirs:
        (test_dir / dir_name).mkdir(parents=True, exist_ok=True)
    
    # 创建测试配置文件
    config_data = TestDataGenerator.generate_config_data()
    config_file = test_dir / "config.json"
    with open(config_file, 'w', encoding='utf-8') as f:
        json.dump(config_data, f, ensure_ascii=False, indent=2)
    
    return test_dir


def cleanup_test_environment(test_dir: Path):
    """清理测试环境"""
    if test_dir.exists():
        shutil.rmtree(test_dir, ignore_errors=True)


# 测试装饰器
def skip_if_no_module(module_name: str):
    """如果没有指定模块则跳过测试"""
    import importlib
    try:
        importlib.import_module(module_name)
        return lambda func: func
    except ImportError:
        import pytest
        return pytest.mark.skip(reason=f"模块 {module_name} 未安装")


def retry_on_failure(max_retries: int = 3, delay: float = 0.1):
    """失败时重试装饰器"""
    import time
    import functools
    
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            last_exception = None
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    last_exception = e
                    if attempt < max_retries - 1:
                        time.sleep(delay)
            raise last_exception
        return wrapper
    return decorator


# 测试配置
TEST_CONFIG = {
    "timeout": 30,
    "max_retries": 3,
    "test_data_dir": "test_data",
    "log_level": "DEBUG",
    "cleanup_after_test": True
}


# 测试常量
TEST_CONSTANTS = {
    "SAMPLE_TEXT": "这是一个测试文本",
    "SAMPLE_ENGLISH": "This is a test text",
    "SAMPLE_NUMBER": 12345,
    "SAMPLE_FLOAT": 3.14159,
    "SAMPLE_LIST": [1, 2, 3, 4, 5],
    "SAMPLE_DICT": {"key1": "value1", "key2": "value2"}
}


if __name__ == "__main__":
    # 测试工具模块本身
    print("测试数据生成器测试:")
    print(json.dumps(TestDataGenerator.generate_config_data(), indent=2, ensure_ascii=False))
    
    print("\n测试文件管理器测试:")
    with TestFileManager() as file_manager:
        temp_file = file_manager.create_temp_file("测试内容")
        print(f"创建临时文件: {temp_file}")
        
        temp_json = file_manager.create_temp_json({"test": "data"})
        print(f"创建临时JSON: {temp_json}")
    
    print("\n模拟对象工厂测试:")
    mock_logger = MockFactory.create_mock_logger()
    mock_logger.info("测试日志消息")
    
    print("\n断言辅助类测试:")
    AssertionHelper.assert_dict_contains(
        {"key1": "value1", "key2": "value2"},
        {"key1": "value1"},
        "字典包含测试"
    )
    
    print("\n性能计时器测试:")
    with PerformanceTimer("测试操作") as timer:
        import time
        time.sleep(0.1)
    timer.assert_duration_less_than(1.0, "性能测试")