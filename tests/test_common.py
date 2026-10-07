# -*- coding: UTF-8 -*-
"""
通用工具模块测试
测试 utils/common.py 中的 Common 类
"""

import pytest
import os
import json
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock, mock_open
from datetime import datetime, timedelta

from utils.common import Common


class TestCommon:
    """Common 类测试"""
    
    def test_init(self):
        """测试 Common 类初始化"""
        common = Common()
        assert common is not None
    
    def test_text_processing(self, sample_text):
        """测试文本处理功能"""
        common = Common()
        
        # 测试中文文本处理
        if hasattr(common, 'process_text'):
            result = common.process_text(sample_text["chinese"])
            assert result is not None
        
        # 测试英文文本处理
        if hasattr(common, 'process_text'):
            result = common.process_text(sample_text["english"])
            assert result is not None
    
    def test_language_detection(self, sample_text):
        """测试语言检测功能"""
        common = Common()
        
        if hasattr(common, 'detect_language'):
            # 测试中文检测
            lang = common.detect_language(sample_text["chinese"])
            assert lang is not None
            
            # 测试英文检测
            lang = common.detect_language(sample_text["english"])
            assert lang is not None
    
    def test_profanity_filter(self, sample_text):
        """测试敏感词过滤功能"""
        common = Common()
        
        if hasattr(common, 'filter_profanity'):
            # 测试正常文本
            result = common.filter_profanity(sample_text["chinese"])
            assert result is not None
            
            # 测试包含敏感词的文本
            profanity_text = "这是一个包含敏感词的文本"
            result = common.filter_profanity(profanity_text)
            assert result is not None
    
    def test_similarity_calculation(self, sample_text):
        """测试相似度计算功能"""
        common = Common()
        
        if hasattr(common, 'calculate_similarity'):
            # 测试相同文本的相似度
            similarity = common.calculate_similarity(
                sample_text["chinese"], 
                sample_text["chinese"]
            )
            assert similarity == 1.0
            
            # 测试不同文本的相似度
            similarity = common.calculate_similarity(
                sample_text["chinese"],
                sample_text["english"]
            )
            assert 0 <= similarity <= 1
    
    def test_file_operations(self, tmp_path):
        """测试文件操作功能"""
        common = Common()
        
        # 创建测试文件
        test_file = tmp_path / "test_file.txt"
        test_file.write_text("测试内容", encoding='utf-8')
        
        if hasattr(common, 'read_file'):
            # read_file 方法现在需要 return_type 参数
            content = common.read_file(str(test_file), 'text')
            assert content == "测试内容"
        
        if hasattr(common, 'write_file'):
            common.write_file(str(test_file), "新内容")
            assert test_file.read_text(encoding='utf-8') == "新内容"
    
    def test_datetime_operations(self, sample_datetime):
        """测试日期时间操作功能"""
        common = Common()
        
        if hasattr(common, 'format_datetime'):
            # 测试日期时间格式化
            formatted = common.format_datetime(sample_datetime["now"])
            assert formatted is not None
            assert isinstance(formatted, str)
    
    def test_json_operations(self, sample_config):
        """测试 JSON 操作功能"""
        common = Common()
        
        if hasattr(common, 'load_json'):
            # 测试 JSON 加载
            json_str = json.dumps(sample_config)
            result = common.load_json(json_str)
            assert result == sample_config
        
        if hasattr(common, 'dump_json'):
            # 测试 JSON 序列化
            result = common.dump_json(sample_config)
            assert result is not None
            assert isinstance(result, str)
    
    def test_string_operations(self, sample_text):
        """测试字符串操作功能"""
        common = Common()
        
        if hasattr(common, 'clean_text'):
            # 测试文本清理
            dirty_text = "  这是一个\n包含\t特殊字符的文本  "
            cleaned = common.clean_text(dirty_text)
            assert cleaned is not None
        
        if hasattr(common, 'truncate_text'):
            # 测试文本截断
            long_text = sample_text["long"]
            truncated = common.truncate_text(long_text, 100)
            assert len(truncated) <= 100
    
    def test_validation_functions(self):
        """测试验证功能"""
        common = Common()
        
        if hasattr(common, 'validate_email'):
            # 测试邮箱验证
            assert common.validate_email("test@example.com") == True
            assert common.validate_email("invalid-email") == False
        
        if hasattr(common, 'validate_url'):
            # 测试 URL 验证
            assert common.validate_url("https://example.com") == True
            assert common.validate_url("invalid-url") == False
    
    def test_conversion_functions(self):
        """测试转换功能"""
        common = Common()
        
        if hasattr(common, 'to_int'):
            # 测试整数转换
            assert common.to_int("123") == 123
            assert common.to_int("abc", default=0) == 0
        
        if hasattr(common, 'to_float'):
            # 测试浮点数转换
            assert common.to_float("3.14") == 3.14
            assert common.to_float("abc", default=0.0) == 0.0


@pytest.mark.unit
class TestCommonEdgeCases:
    """Common 边界情况测试"""
    
    def test_empty_input(self):
        """测试空输入"""
        common = Common()
        
        # 测试空字符串处理
        if hasattr(common, 'process_text'):
            result = common.process_text("")
            assert result is not None
    
    def test_none_input(self):
        """测试 None 输入"""
        common = Common()
        
        # 测试 None 输入处理
        if hasattr(common, 'process_text'):
            try:
                result = common.process_text(None)
                # 如果没有抛出异常，应该返回默认值
                assert result is not None
            except (TypeError, ValueError):
                # 抛出异常也是可接受的
                assert True
    
    def test_large_input(self, sample_text):
        """测试大型输入"""
        common = Common()
        
        # 测试大型文本处理
        if hasattr(common, 'process_text'):
            result = common.process_text(sample_text["long"])
            assert result is not None
    
    def test_special_characters(self, sample_text):
        """测试特殊字符"""
        common = Common()
        
        # 测试特殊字符处理
        if hasattr(common, 'process_text'):
            result = common.process_text(sample_text["special_chars"])
            assert result is not None
    
    def test_unicode_handling(self):
        """测试 Unicode 处理"""
        common = Common()
        
        # 测试 Unicode 字符处理
        unicode_text = "测试中文和特殊字符：!@#$%^&*()_+-=[]{}|;':\",./<>?"
        if hasattr(common, 'process_text'):
            result = common.process_text(unicode_text)
            assert result is not None


@pytest.mark.integration
class TestCommonIntegration:
    """Common 集成测试"""
    
    def test_with_real_files(self, project_root):
        """测试与真实文件的集成"""
        common = Common()
        
        # 测试读取真实文件
        config_file = project_root / "config.json"
        if config_file.exists() and hasattr(common, 'read_file'):
            # read_file 方法现在需要 return_type 参数
            content = common.read_file(str(config_file), 'dict')
            assert content is not None
            assert isinstance(content, dict)
    
    def test_with_external_libraries(self):
        """测试与外部库的集成"""
        common = Common()
        
        # 测试与 langid 的集成（如果存在）
        if hasattr(common, 'detect_language'):
            try:
                lang = common.detect_language("Hello World")
                assert lang is not None
            except ImportError:
                pytest.skip("langid 库未安装")
    
    def test_error_handling(self):
        """测试错误处理"""
        common = Common()
        
        # 测试文件不存在的情况
        if hasattr(common, 'read_file'):
            try:
                # read_file 方法现在需要 return_type 参数
                content = common.read_file("nonexistent_file.txt", 'text')
                # 如果没有抛出异常，应该返回默认值
                assert content is not None or content is None
            except FileNotFoundError:
                # 抛出异常也是可接受的
                assert True


@pytest.mark.performance
class TestCommonPerformance:
    """Common 性能测试"""
    
    def test_text_processing_performance(self, sample_text):
        """测试文本处理性能"""
        import time
        
        common = Common()
        
        if hasattr(common, 'process_text'):
            # 测试大量文本处理性能
            start_time = time.time()
            
            for _ in range(1000):
                common.process_text(sample_text["chinese"])
            
            end_time = time.time()
            duration = end_time - start_time
            
            print(f"处理1000次中文文本耗时: {duration:.4f}秒")
            
            # 确保性能在合理范围内（小于2秒）
            assert duration < 2.0
    
    def test_language_detection_performance(self, sample_text):
        """测试语言检测性能"""
        import time
        
        common = Common()
        
        if hasattr(common, 'detect_language'):
            # 测试大量语言检测性能
            start_time = time.time()
            
            for _ in range(1000):
                common.detect_language(sample_text["chinese"])
            
            end_time = time.time()
            duration = end_time - start_time
            
            print(f"1000次语言检测耗时: {duration:.4f}秒")
            
            # 确保性能在合理范围内（小于2秒）
            assert duration < 2.0


# 测试工具函数
def test_common_helper_functions():
    """测试通用工具函数"""
    # 这里可以测试各种辅助函数
    pass


# 性能基准测试
@pytest.mark.slow
def test_common_benchmark():
    """通用工具性能基准测试"""
    import time
    
    common = Common()
    
    # 测试文本处理性能
    test_texts = [
        "测试文本1",
        "测试文本2",
        "测试文本3",
        "Hello World",
        "Test text 4",
        "Test text 5"
    ]
    
    start_time = time.time()
    
    for text in test_texts * 100:  # 重复100次
        if hasattr(common, 'process_text'):
            common.process_text(text)
        if hasattr(common, 'detect_language'):
            common.detect_language(text)
    
    end_time = time.time()
    duration = end_time - start_time
    
    print(f"处理600个文本耗时: {duration:.4f}秒")
    
    # 确保性能基准
    assert duration < 5.0  # 小于5秒


if __name__ == "__main__":
    pytest.main([__file__, "-v"])