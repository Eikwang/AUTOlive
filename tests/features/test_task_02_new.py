# -*- coding: UTF-8 -*-
"""
Task-02: 创建功能分组数据结构 测试

门禁A 2026-10-19 对齐现行 config.json（7 组结构）重写：
- function_groups 现为 7 组：基础功能/AI对话/互动设置/助播/高级功能/数字人/系统设置
- 旧"页面配置"(web) 已由平台配置(platform_config)等取代；积分/翻译/串口/助播/
  虚拟身体按现行归属分别改在高级功能/系统设置/助播/数字人组中断言
- 旧"直播选项/运行状态/色彩模式"分组已消亡，逐个标记退役
"""

import pytest
import json
from pathlib import Path


class TestFunctionGroupDataStructure:
    """测试功能分组数据结构"""

    def test_config_has_function_groups(self):
        """测试配置文件包含功能分组"""
        # Given: 配置文件
        # When: 读取配置
        # Then: 包含功能分组配置
        with open("config.json", "r", encoding="utf-8") as f:
            config = json.load(f)

        assert "webui" in config
        assert "function_groups" in config["webui"]

    def test_function_groups_has_seven_groups(self):
        """测试功能分组有 7 个分组（现行结构）"""
        # Given: 配置文件
        # When: 读取功能分组
        # Then: 有 7 个分组
        with open("config.json", "r", encoding="utf-8") as f:
            config = json.load(f)

        groups = config["webui"]["function_groups"]
        assert len(groups) == 7

    def test_function_groups_names(self):
        """测试功能分组名称正确"""
        # Given: 配置文件
        # When: 读取功能分组名称
        # Then: 包含所有分组名称
        with open("config.json", "r", encoding="utf-8") as f:
            config = json.load(f)

        groups = config["webui"]["function_groups"]
        group_names = [g["name"] for g in groups]

        assert "基础功能" in group_names
        assert "AI对话" in group_names
        assert "互动设置" in group_names
        assert "助播" in group_names
        assert "高级功能" in group_names
        assert "数字人" in group_names
        assert "系统设置" in group_names

    def test_each_group_has_functions(self):
        """测试每个分组有功能列表"""
        # Given: 配置文件
        # When: 读取功能分组
        # Then: 每个分组有 functions 列表
        with open("config.json", "r", encoding="utf-8") as f:
            config = json.load(f)

        groups = config["webui"]["function_groups"]
        for group in groups:
            assert "functions" in group
            assert isinstance(group["functions"], list)
            assert len(group["functions"]) > 0

    def test_each_function_has_required_fields(self):
        """测试每个功能有必需字段"""
        # Given: 配置文件
        # When: 读取功能项
        # Then: 每个功能有 name 和 enabled 字段
        with open("config.json", "r", encoding="utf-8") as f:
            config = json.load(f)

        groups = config["webui"]["function_groups"]
        for group in groups:
            for func in group["functions"]:
                assert "name" in func
                assert "enabled" in func
                assert isinstance(func["enabled"], bool)


class TestFunctionGroupMapping:
    """测试功能分组映射"""

    def test_basic_functions_group(self):
        """测试基础功能分组"""
        # Given: 配置文件
        # When: 查找基础功能分组
        # Then: 包含通用配置和平台配置（旧"页面配置"web 已退役）
        with open("config.json", "r", encoding="utf-8") as f:
            config = json.load(f)

        groups = config["webui"]["function_groups"]
        basic_group = next(g for g in groups if g["name"] == "基础功能")
        func_names = [f["name"] for f in basic_group["functions"]]

        assert "通用配置" in func_names
        assert "平台配置" in func_names

    def test_interaction_settings_group(self):
        """测试互动设置分组"""
        # Given: 配置文件
        # When: 查找互动设置分组
        # Then: 包含过滤器与直播间互动功能（积分/助播/翻译/串口已按现行归属
        #       分别移至高级功能/助播/系统设置组，在对应用例中验证）
        with open("config.json", "r", encoding="utf-8") as f:
            config = json.load(f)

        groups = config["webui"]["function_groups"]
        interaction_group = next(g for g in groups if g["name"] == "互动设置")
        func_names = [f["name"] for f in interaction_group["functions"]]

        assert "过滤" in func_names
        assert "弹幕黑名单" in func_names
        assert "念弹幕" in func_names
        assert "答谢" in func_names

    def test_ai_dialog_group(self):
        """测试 AI对话 分组"""
        # Given: 配置文件
        # When: 查找 AI对话 分组
        # Then: 包含大语言模型、聊天、图像识别
        with open("config.json", "r", encoding="utf-8") as f:
            config = json.load(f)

        groups = config["webui"]["function_groups"]
        ai_group = next(g for g in groups if g["name"] == "AI对话")
        func_names = [f["name"] for f in ai_group["functions"]]

        assert "大语言模型" in func_names
        assert "聊天" in func_names

    def test_advanced_features_group(self):
        """测试高级功能分组"""
        # Given: 配置文件
        # When: 查找高级功能分组
        # Then: 包含积分、翻译、文案、数据分析
        #       （虚拟身体已移至"数字人"组）
        with open("config.json", "r", encoding="utf-8") as f:
            config = json.load(f)

        groups = config["webui"]["function_groups"]
        advanced_group = next(g for g in groups if g["name"] == "高级功能")
        func_names = [f["name"] for f in advanced_group["functions"]]

        assert "积分" in func_names
        assert "翻译" in func_names
        assert "数据分析" in func_names

    def test_anchor_group(self):
        """测试助播分组（原互动设置内"助播"独立成组）"""
        # Given: 配置文件
        # When: 查找助播分组
        # Then: 包含助播功能
        with open("config.json", "r", encoding="utf-8") as f:
            config = json.load(f)

        groups = config["webui"]["function_groups"]
        anchor_group = next(g for g in groups if g["name"] == "助播")
        func_names = [f["name"] for f in anchor_group["functions"]]

        assert "助播" in func_names

    def test_digital_human_group(self):
        """测试数字人分组（原高级功能内"虚拟身体"独立成组）"""
        # Given: 配置文件
        # When: 查找数字人分组
        # Then: 包含画面设置、训练主播、训练声音
        with open("config.json", "r", encoding="utf-8") as f:
            config = json.load(f)

        groups = config["webui"]["function_groups"]
        dh_group = next(g for g in groups if g["name"] == "数字人")
        func_names = [f["name"] for f in dh_group["functions"]]

        assert "画面设置" in func_names
        assert "训练主播" in func_names
        assert "训练声音" in func_names

    def test_system_settings_group(self):
        """测试系统设置分组"""
        # Given: 配置文件
        # When: 查找系统设置分组
        # Then: 分组存在，且串口归属本组（原互动设置内）
        with open("config.json", "r", encoding="utf-8") as f:
            config = json.load(f)

        groups = config["webui"]["function_groups"]
        system_group = next(g for g in groups if g["name"] == "系统设置")
        assert system_group is not None

        func_names = [f["name"] for f in system_group["functions"]]
        assert "串口" in func_names

    @pytest.mark.skip(reason="退役：'运行状态'分组已消亡，运行状态现经侧边栏服务状态条/启停控制区呈现，不再是导航分组（门禁A 2026-10-19）")
    def test_running_status_group(self):
        """测试运行状态分组"""
        # Given: 配置文件
        # When: 查找运行状态分组
        # Then: 分组存在
        with open("config.json", "r", encoding="utf-8") as f:
            config = json.load(f)

        groups = config["webui"]["function_groups"]
        status_group = next(g for g in groups if g["name"] == "运行状态")
        assert status_group is not None

    @pytest.mark.skip(reason="退役：'色彩模式'分组已消亡，色彩模式现经侧边栏主题切换按钮（深色/浅色）呈现，不再是导航分组（门禁A 2026-10-19）")
    def test_color_mode_group(self):
        """测试色彩模式分组"""
        # Given: 配置文件
        # When: 查找色彩模式分组
        # Then: 分组存在
        with open("config.json", "r", encoding="utf-8") as f:
            config = json.load(f)

        groups = config["webui"]["function_groups"]
        color_group = next(g for g in groups if g["name"] == "色彩模式")
        assert color_group is not None


class TestConfigSettingsModule:
    """测试配置设置模块"""

    def test_settings_module_has_get_function_groups(self):
        """测试 settings 模块有 get_function_groups 函数"""
        # Given: frontend.config.settings 模块
        # When: 检查函数
        # Then: get_function_groups 函数存在
        from frontend.config.settings import get_function_groups
        assert get_function_groups is not None

    def test_get_function_groups_returns_list(self):
        """测试 get_function_groups 返回列表"""
        # Given: 配置实例
        # When: 调用 get_function_groups
        # Then: 返回列表
        from frontend.config.settings import init_config, get_function_groups
        config = init_config("config.json")
        groups = get_function_groups()
        assert isinstance(groups, list)

    def test_get_function_groups_returns_seven_groups(self):
        """测试 get_function_groups 返回 7 个分组（现行结构）"""
        # Given: 配置实例
        # When: 调用 get_function_groups
        # Then: 返回 7 个分组
        from frontend.config.settings import init_config, get_function_groups
        config = init_config("config.json")
        groups = get_function_groups()
        assert len(groups) == 7
