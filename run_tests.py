#!/usr/bin/env python3
# -*- coding: UTF-8 -*-
"""
测试运行脚本
用于运行所有测试并生成报告
"""

import subprocess
import sys
import os
from pathlib import Path


def run_tests():
    """运行所有测试"""
    print("=" * 60)
    print("Luna AI 测试套件")
    print("=" * 60)
    
    # 检查测试依赖
    try:
        import pytest
        print(f"✓ pytest 版本: {pytest.__version__}")
    except ImportError:
        print("✗ 错误: pytest 未安装")
        print("请运行: pip install -r requirements-test.txt")
        return 1
    
    # 检查覆盖率工具
    try:
        import coverage
        print(f"✓ coverage 版本: {coverage.__version__}")
    except ImportError:
        print("⚠ 警告: coverage 未安装，将跳过覆盖率报告")
    
    # 运行测试
    print("\n" + "-" * 60)
    print("开始运行测试...")
    print("-" * 60)
    
    # 构建 pytest 命令
    cmd = [
        sys.executable, "-m", "pytest",
        "tests/",
        "-v",
        "--tb=short",
        "--strict-markers",
        "--cov=utils",
        "--cov-report=term-missing",
        "--cov-report=html:htmlcov",
        "--cov-report=xml:coverage.xml",
        "--durations=10",
        "--durations-min=0.1"
    ]
    
    # 添加额外参数
    if len(sys.argv) > 1:
        cmd.extend(sys.argv[1:])
    
    # 运行测试
    result = subprocess.run(cmd, capture_output=False)
    
    print("\n" + "=" * 60)
    print("测试完成!")
    print("=" * 60)
    
    # 检查覆盖率报告
    if Path("htmlcov").exists():
        print(f"\n✓ HTML 覆盖率报告已生成: {Path('htmlcov').absolute()}")
    
    if Path("coverage.xml").exists():
        print(f"✓ XML 覆盖率报告已生成: {Path('coverage.xml').absolute()}")
    
    return result.returncode


def run_specific_tests(test_file=None):
    """运行特定测试文件"""
    if test_file:
        print(f"\n运行特定测试: {test_file}")
        cmd = [sys.executable, "-m", "pytest", test_file, "-v"]
        result = subprocess.run(cmd)
        return result.returncode
    else:
        print("请指定测试文件，例如: python run_tests.py tests/test_config.py")
        return 1


def run_unit_tests():
    """只运行单元测试"""
    print("\n运行单元测试...")
    cmd = [
        sys.executable, "-m", "pytest",
        "tests/",
        "-v",
        "--tb=short",
        "-m", "unit"
    ]
    result = subprocess.run(cmd)
    return result.returncode


def run_integration_tests():
    """只运行集成测试"""
    print("\n运行集成测试...")
    cmd = [
        sys.executable, "-m", "pytest",
        "tests/",
        "-v",
        "--tb=short",
        "-m", "integration"
    ]
    result = subprocess.run(cmd)
    return result.returncode


def run_performance_tests():
    """只运行性能测试"""
    print("\n运行性能测试...")
    cmd = [
        sys.executable, "-m", "pytest",
        "tests/",
        "-v",
        "--tb=short",
        "-m", "performance"
    ]
    result = subprocess.run(cmd)
    return result.returncode


def main():
    """主函数"""
    if len(sys.argv) > 1:
        command = sys.argv[1]
        
        if command == "all":
            return run_tests()
        elif command == "unit":
            return run_unit_tests()
        elif command == "integration":
            return run_integration_tests()
        elif command == "performance":
            return run_performance_tests()
        elif command == "file":
            if len(sys.argv) > 2:
                return run_specific_tests(sys.argv[2])
            else:
                print("请指定测试文件")
                return 1
        else:
            print(f"未知命令: {command}")
            print("可用命令: all, unit, integration, performance, file <filename>")
            return 1
    else:
        # 默认运行所有测试
        return run_tests()


if __name__ == "__main__":
    sys.exit(main())