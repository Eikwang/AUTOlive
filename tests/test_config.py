# -*- coding: UTF-8 -*-
"""
Configuration module tests
Tests for Config class in utils/config.py
"""

import pytest
import json
import os
from pathlib import Path
from unittest.mock import patch, mock_open

from utils.config import Config


class TestConfig:
    """Config class tests"""
    
    def setup_method(self):
        """Reset Config class cache before each test"""
        Config.config = None
        Config._config_path = None
        Config._is_multi_file = False
        Config._config_files = []
        Config._callbacks = []
    
    def test_init_with_valid_config(self, temp_config_file, sample_config):
        """Test initialization with valid config file"""
        config = Config(temp_config_file)
        assert config.config is not None
        assert config.config == sample_config

    def test_constructor_noop_then_reload_reads_disk(self, tmp_path):
        """2026-10-24 实测修复回归：构造函数类级缓存 no-op 语义锁定 +
        Config.reload 真读盘。

        缺陷现场：UI 改键落盘后主系统进程 Config(path) 构造永不重读——
        页面测试链 edge_tts.rate 恒为启动快照（语速设置无效恒 +0% 根因）。
        """
        cfg_file = tmp_path / "config.json"
        cfg_file.write_text(json.dumps({"edge_tts": {"rate": "+10%"}}), encoding="utf-8")
        try:
            first = Config(str(cfg_file))
            assert first.get("edge_tts", "rate") == "+10%"
            # 磁盘变更后：构造 no-op（类级缓存现状锁定），reload 真读盘
            cfg_file.write_text(json.dumps({"edge_tts": {"rate": "+30%"}}), encoding="utf-8")
            second = Config(str(cfg_file))
            assert second.get("edge_tts", "rate") == "+10%"
            assert Config.reload(str(cfg_file)) is True
            # 类缓存刷新后所有实例（含旧实例）可见新值
            assert first.get("edge_tts", "rate") == "+30%"
            assert second.get("edge_tts", "rate") == "+30%"
        finally:
            # 类级缓存进程全局——复位防污染后续测试（同 setup_method 口径）
            Config.config = None
            Config._config_path = None
            Config._is_multi_file = False
            Config._config_files = []
    
    def test_init_with_invalid_file(self, tmp_path):
        """Test initialization with invalid file"""
        invalid_file = tmp_path / "nonexistent.json"
        with pytest.raises(FileNotFoundError):
            Config(str(invalid_file))
    
    def test_init_with_invalid_json(self, tmp_path):
        """Test initialization with invalid JSON file"""
        invalid_json = tmp_path / "invalid.json"
        # Write invalid JSON with proper encoding
        invalid_json.write_text("This is not valid JSON", encoding='utf-8')
        with pytest.raises(json.JSONDecodeError):
            Config(str(invalid_json))
    
    def test_getitem_existing_key(self, temp_config_file):
        """Test getting existing key"""
        config = Config(temp_config_file)
        assert config["platform"] == "talk"
        assert config["chat_type"] == "custom_llm"
    
    def test_getitem_nonexistent_key(self, temp_config_file):
        """Test getting non-existent key"""
        config = Config(temp_config_file)
        assert config["nonexistent"] is None
    
    def test_get_single_key(self, temp_config_file):
        """Test getting single key"""
        config = Config(temp_config_file)
        assert config.get("platform") == "talk"
    
    def test_get_nested_keys(self, temp_config_file):
        """Test getting nested keys"""
        config = Config(temp_config_file)
        assert config.get("llm", "provider") == "openai"
        assert config.get("llm", "model") == "gpt-3.5-turbo"
    
    def test_get_nonexistent_nested_keys(self, temp_config_file):
        """Test getting non-existent nested keys"""
        config = Config(temp_config_file)
        assert config.get("nonexistent", "key") is None
        assert config.get("llm", "nonexistent") is None
    
    def test_get_with_default_value(self, temp_config_file):
        """Test getting key with default value"""
        # Note: Config class get method doesn't support default value parameter
        # This tests returning None case
        config = Config(temp_config_file)
        assert config.get("nonexistent") is None
    
    def test_config_data_integrity(self, temp_config_file, sample_config):
        """Test config data integrity"""
        config = Config(temp_config_file)
        # Ensure config data hasn't been modified
        assert config.config == sample_config
        
        # Test nested structure
        assert isinstance(config["llm"], dict)
        assert isinstance(config["tts"], dict)
    
    def test_config_singleton_behavior(self, temp_config_file):
        """Test config singleton behavior"""
        # Reset Config cache
        Config.config = None
        
        config1 = Config(temp_config_file)
        config2 = Config(temp_config_file)
        
        # Since Config has class-level cache, both instances should share the same config
        assert config1.config is config2.config
        assert config1.config == config2.config
    
    def test_config_encoding(self, tmp_path, sample_config):
        """Test config file encoding handling"""
        # Reset Config cache
        Config.config = None
        
        # Create config file with Chinese characters
        config_with_chinese = sample_config.copy()
        config_with_chinese["description"] = "This is a test configuration"
        
        config_file = tmp_path / "chinese_config.json"
        with open(config_file, 'w', encoding='utf-8') as f:
            json.dump(config_with_chinese, f, ensure_ascii=False, indent=2)
        
        config = Config(str(config_file))
        assert config["description"] == "This is a test configuration"
    
    def test_config_large_file(self, tmp_path):
        """Test large config file handling"""
        # Reset Config cache
        Config.config = None
        
        # Create a large config file
        large_config = {
            "data": {f"key_{i}": f"value_{i}" for i in range(1000)}
        }
        
        config_file = tmp_path / "large_config.json"
        with open(config_file, 'w', encoding='utf-8') as f:
            json.dump(large_config, f)
        
        config = Config(str(config_file))
        assert len(config["data"]) == 1000
        assert config.get("data", "key_999") == "value_999"


class TestConfigEdgeCases:
    """Config edge case tests"""
    
    def setup_method(self):
        """Reset Config class cache before each test"""
        Config.config = None
        Config._config_path = None
        Config._is_multi_file = False
        Config._config_files = []
        Config._callbacks = []
    
    def test_empty_config_file(self, tmp_path):
        """Test empty config file"""
        # Reset Config cache
        Config.config = None
        
        empty_file = tmp_path / "empty.json"
        empty_file.write_text("{}", encoding='utf-8')
        
        config = Config(str(empty_file))
        assert config.config == {}
    
    def test_config_with_null_values(self, tmp_path):
        """Test config with null values"""
        # Reset Config cache
        Config.config = None
        
        config_with_nulls = {
            "key1": None,
            "key2": "value",
            "nested": {"null_key": None}
        }
        
        config_file = tmp_path / "null_config.json"
        with open(config_file, 'w', encoding='utf-8') as f:
            json.dump(config_with_nulls, f)
        
        config = Config(str(config_file))
        assert config["key1"] is None
        assert config["key2"] == "value"
        assert config.get("nested", "null_key") is None
    
    def test_config_with_special_characters(self, tmp_path):
        """Test config with special characters"""
        # Reset Config cache
        Config.config = None
        
        config_with_special = {
            "path": "C:\\Users\\test\\file.txt",
            "url": "https://example.com/path?param=value&other=123",
            "regex": "^[a-zA-Z0-9]+$",
            "unicode": "Test special characters: !@#$%^&*()"
        }
        
        config_file = tmp_path / "special_config.json"
        with open(config_file, 'w', encoding='utf-8') as f:
            json.dump(config_with_special, f, ensure_ascii=False)
        
        config = Config(str(config_file))
        assert config["path"] == "C:\\Users\\test\\file.txt"
        assert config["url"] == "https://example.com/path?param=value&other=123"
        assert config["regex"] == "^[a-zA-Z0-9]+$"
        assert config["unicode"] == "Test special characters: !@#$%^&*()"


@pytest.mark.unit
class TestConfigPerformance:
    """Config performance tests"""
    
    def setup_method(self):
        """Reset Config class cache before each test"""
        Config.config = None
        Config._config_path = None
        Config._is_multi_file = False
        Config._config_files = []
        Config._callbacks = []
    
    def test_config_access_performance(self, temp_config_file):
        """Test config access performance"""
        import time
        
        config = Config(temp_config_file)
        
        # Test performance of multiple accesses
        start_time = time.time()
        for _ in range(10000):
            _ = config["platform"]
            _ = config.get("llm", "provider")
        end_time = time.time()
        
        # Ensure access time is within reasonable range (less than 1 second)
        assert end_time - start_time < 1.0
    
    def test_config_memory_usage(self, temp_config_file):
        """Test config memory usage"""
        import sys
        
        # Reset Config cache
        Config.config = None
        
        # Create config instance
        config = Config(temp_config_file)
        
        # Get object size
        config_size = sys.getsizeof(config.config)
        
        # Ensure memory usage is within reasonable range (less than 1MB)
        assert config_size < 1024 * 1024


@pytest.mark.integration
class TestConfigIntegration:
    """Config integration tests"""
    
    def setup_method(self):
        """Reset Config class cache before each test"""
        Config.config = None
        Config._config_path = None
        Config._is_multi_file = False
        Config._config_files = []
        Config._callbacks = []
    
    def test_config_with_real_file(self, project_root):
        """Test config integration with real file"""
        # Reset Config cache
        Config.config = None
        
        config_file = project_root / "config.json"
        if config_file.exists():
            config = Config(str(config_file))
            assert config.config is not None
            assert isinstance(config.config, dict)
    
    def test_config_file_permissions(self, tmp_path, sample_config):
        """Test config file permissions handling"""
        # Reset Config cache
        Config.config = None
        
        config_file = tmp_path / "readonly_config.json"
        with open(config_file, 'w', encoding='utf-8') as f:
            json.dump(sample_config, f)
        
        # Set to read-only (may require admin privileges on Windows)
        try:
            os.chmod(str(config_file), 0o444)
            config = Config(str(config_file))
            assert config.config is not None
        except (OSError, PermissionError):
            # May not have permission to modify file attributes on some systems
            pytest.skip("Cannot set file permissions")
        finally:
            # Restore permissions for cleanup
            try:
                os.chmod(str(config_file), 0o666)
            except:
                pass


# Test helper functions
def test_config_helper_functions():
    """Test config-related helper functions"""
    # Can test config validation, conversion, etc. helper functions here
    pass


# Performance benchmark test
@pytest.mark.slow
def test_config_load_benchmark():
    """Config loading performance benchmark test"""
    import time
    import tempfile
    import json
    
    # Reset Config cache
    Config.config = None
    
    # Create large config file
    large_config = {
        "section_{}".format(i): {
            "key_{}".format(j): "value_{}".format(j)
            for j in range(100)
        }
        for i in range(100)
    }
    
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False, encoding='utf-8') as f:
        json.dump(large_config, f)
        temp_file = f.name
    
    try:
        # Test loading time
        start_time = time.time()
        for _ in range(100):
            # Reset cache before each load
            Config.config = None
            config = Config(temp_file)
        end_time = time.time()
        
        avg_time = (end_time - start_time) / 100
        print(f"Average config loading time: {avg_time:.4f} seconds")
        
        # Ensure average loading time is within reasonable range
        assert avg_time < 0.1  # Less than 100 milliseconds
    finally:
        os.unlink(temp_file)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])