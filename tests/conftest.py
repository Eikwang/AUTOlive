# -*- coding: UTF-8 -*-
"""
pytest configuration file
Provides common test fixtures and utility functions
"""

import pytest
import sys
import os
import json
import tempfile
import shutil
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime, timedelta

# Add project root to Python path
sys.path.insert(0, str(Path(__file__).parent.parent))

# Import project modules
from utils.config import Config
from utils.db import SQLiteDB


@pytest.fixture(scope="session")
def project_root():
    """Project root directory"""
    return Path(__file__).parent.parent


@pytest.fixture(scope="session")
def test_data_dir(project_root):
    """Test data directory"""
    test_data = project_root / "test_data"
    test_data.mkdir(exist_ok=True)
    return test_data


@pytest.fixture
def config_path():
    """Configuration file path"""
    return "config.json"


@pytest.fixture
def sample_config():
    """Sample configuration"""
    return {
        "platform": "talk",
        "chat_type": "custom_llm",
        "room_display_id": "278333",
        "llm": {
            "provider": "openai",
            "model": "gpt-3.5-turbo",
            "api_key": "test-key"
        },
        "tts": {
            "provider": "edge-tts",
            "voice": "zh-CN-XiaoxiaoNeural"
        }
    }


@pytest.fixture
def temp_config_file(sample_config, tmp_path):
    """Temporary configuration file"""
    config_file = tmp_path / "test_config.json"
    with open(config_file, 'w', encoding='utf-8') as f:
        json.dump(sample_config, f, ensure_ascii=False, indent=2)
    return str(config_file)


@pytest.fixture
def config_instance(temp_config_file):
    """Configuration instance"""
    return Config(temp_config_file)


@pytest.fixture
def temp_db_file(tmp_path):
    """Temporary database file"""
    db_file = tmp_path / "test.db"
    return str(db_file)


@pytest.fixture
def db_instance(temp_db_file):
    """Database instance"""
    db = SQLiteDB(temp_db_file)
    yield db
    # Cleanup: close all connections
    while not db.connection_pool.empty():
        try:
            conn = db.connection_pool.get_nowait()
            conn.close()
        except:
            break


@pytest.fixture
def sample_db_data():
    """Sample database data"""
    return {
        "users": [
            {"id": 1, "name": "test_user1", "score": 100},
            {"id": 2, "name": "test_user2", "score": 200},
            {"id": 3, "name": "test_user3", "score": 150}
        ],
        "questions": [
            {"id": 1, "question": "What is AI?", "answer": "Artificial Intelligence"},
            {"id": 2, "question": "What is Python?", "answer": "Programming language"}
        ]
    }


@pytest.fixture
def mock_logger():
    """Mock logger"""
    with patch('utils.logger.logger') as mock:
        yield mock


@pytest.fixture
def sample_text():
    """Sample text data"""
    return {
        "chinese": "Hello, World! This is a test text.",
        "english": "Hello, World! This is a test text.",
        "mixed": "Hello World",
        "empty": "",
        "special_chars": "!@#$%^&*()_+-=[]{}|;':\",./<>?",
        "long": "This is a long text. " * 100
    }


@pytest.fixture
def sample_audio_file(tmp_path):
    """Sample audio file path"""
    audio_file = tmp_path / "test_audio.wav"
    # Create an empty audio file for testing
    audio_file.touch()
    return str(audio_file)


@pytest.fixture
def sample_image_file(tmp_path):
    """Sample image file path"""
    image_file = tmp_path / "test_image.jpg"
    # Create an empty image file for testing
    image_file.touch()
    return str(image_file)


@pytest.fixture
def mock_requests():
    """Mock requests library"""
    with patch('requests.get') as mock_get, \
         patch('requests.post') as mock_post:
        yield {
            'get': mock_get,
            'post': mock_post
        }


@pytest.fixture
def sample_api_response():
    """Sample API response"""
    return {
        "success": {
            "status": 200,
            "data": {"message": "success"},
            "headers": {"Content-Type": "application/json"}
        },
        "error": {
            "status": 500,
            "data": {"error": "Internal Server Error"},
            "headers": {"Content-Type": "application/json"}
        },
        "timeout": {
            "status": 408,
            "data": {"error": "Request Timeout"},
            "headers": {"Content-Type": "application/json"}
        }
    }


@pytest.fixture
def sample_datetime():
    """Sample datetime"""
    return {
        "now": datetime.now(),
        "past": datetime.now() - timedelta(days=7),
        "future": datetime.now() + timedelta(days=7),
        "format": "%Y-%m-%d %H:%M:%S"
    }


@pytest.fixture(autouse=True)
def setup_test_environment():
    """Automatically setup test environment"""
    # Setup before test
    os.environ["TESTING"] = "true"
    os.environ["LOG_LEVEL"] = "DEBUG"
    
    yield
    
    # Cleanup after test
    os.environ.pop("TESTING", None)
    os.environ.pop("LOG_LEVEL", None)


@pytest.fixture
def capture_logs(caplog):
    """Capture log output"""
    import logging
    with caplog.at_level(logging.DEBUG):
        yield caplog


# Mark slow tests
def pytest_configure(config):
    """Configure pytest"""
    config.addinivalue_line(
        "markers", "slow: mark test as slow running"
    )
    config.addinivalue_line(
        "markers", "integration: mark test as integration test"
    )
    config.addinivalue_line(
        "markers", "unit: mark test as unit test"
    )
    config.addinivalue_line(
        "markers", "database: mark test as database test"
    )


# Test report hook
def pytest_runtest_makereport(item, call):
    """Custom test report"""
    if call.when == "call":
        # Record test execution time
        duration = call.duration
        if duration > 1.0:  # Tests taking more than 1 second
            item.user_properties.append(("slow_test", True))