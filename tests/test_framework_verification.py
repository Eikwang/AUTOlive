# -*- coding: UTF-8 -*-
"""
Simple test to verify testing framework works
"""

import pytest
import json
import tempfile
from pathlib import Path


class TestSimpleFramework:
    """Simple framework tests"""
    
    def test_basic_assertion(self):
        """Test basic assertion"""
        assert 1 + 1 == 2
        assert "hello" == "hello"
        assert [1, 2, 3] == [1, 2, 3]
    
    def test_temp_file_creation(self, tmp_path):
        """Test temporary file creation"""
        test_file = tmp_path / "test.txt"
        test_file.write_text("Hello, World!", encoding='utf-8')
        
        assert test_file.exists()
        assert test_file.read_text(encoding='utf-8') == "Hello, World!"
    
    def test_json_operations(self, tmp_path):
        """Test JSON operations"""
        test_data = {"key": "value", "number": 42, "list": [1, 2, 3]}
        
        # Write JSON file
        json_file = tmp_path / "test.json"
        with open(json_file, 'w', encoding='utf-8') as f:
            json.dump(test_data, f, ensure_ascii=False, indent=2)
        
        # Read JSON file
        with open(json_file, 'r', encoding='utf-8') as f:
            loaded_data = json.load(f)
        
        assert loaded_data == test_data
    
    def test_fixtures_work(self, sample_config):
        """Test that fixtures work"""
        assert sample_config is not None
        assert isinstance(sample_config, dict)
        assert "platform" in sample_config
    
    def test_markers_work(self):
        """Test that markers work"""
        # This test doesn't use markers, but we can test marker registration
        pass


@pytest.mark.unit
class TestUnitExample:
    """Example unit test class"""
    
    def test_unit_test(self):
        """Example unit test"""
        assert True


@pytest.mark.integration
class TestIntegrationExample:
    """Example integration test class"""
    
    def test_integration_test(self):
        """Example integration test"""
        assert True


@pytest.mark.performance
class TestPerformanceExample:
    """Example performance test class"""
    
    def test_performance_test(self):
        """Example performance test"""
        import time
        start = time.time()
        # Simulate some work
        sum(range(10000))
        end = time.time()
        assert end - start < 1.0  # Should complete in less than 1 second


if __name__ == "__main__":
    pytest.main([__file__, "-v"])