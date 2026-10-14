"""
Task-19: 更新导航栏分组配置
测试验证 config.json 中 function_groups 包含 8 个分组
"""
import json
import os
import pytest


@pytest.fixture
def config():
    """加载 config.json"""
    config_path = os.path.join(os.path.dirname(__file__), '..', '..', 'config.json')
    with open(config_path, 'r', encoding='utf-8') as f:
        return json.load(f)


def test_function_groups_has_8_groups(config):
    """验证 function_groups 包含 8 个分组"""
    function_groups = config.get('webui', {}).get('function_groups', [])
    assert len(function_groups) == 8


def test_function_groups_names(config):
    """验证 8 个分组名称正确"""
    function_groups = config.get('webui', {}).get('function_groups', [])
    group_names = [g['name'] for g in function_groups]
    expected_names = [
        '基础功能', '直播选项', '互动设置', 'AI对话',
        '高级功能', '系统设置', '运行状态', '色彩模式'
    ]
    assert group_names == expected_names


def test_each_group_has_icon(config):
    """验证每个分组有对应的图标"""
    function_groups = config.get('webui', {}).get('function_groups', [])
    for group in function_groups:
        assert 'icon' in group, f"分组 {group['name']} 缺少 icon 字段"
        assert group['icon'], f"分组 {group['name']} 的 icon 为空"


def test_basic_functions_group(config):
    """验证基础功能分组包含正确的功能"""
    function_groups = config.get('webui', {}).get('function_groups', [])
    basic_group = next((g for g in function_groups if g['name'] == '基础功能'), None)
    assert basic_group is not None
    assert len(basic_group['functions']) >= 2
    tab_keys = [f['tab_key'] for f in basic_group['functions']]
    assert 'common_config' in tab_keys
    assert 'web' in tab_keys


def test_live_options_group(config):
    """验证直播选项分组包含正确的功能"""
    function_groups = config.get('webui', {}).get('function_groups', [])
    live_group = next((g for g in function_groups if g['name'] == '直播选项'), None)
    assert live_group is not None
    assert len(live_group['functions']) >= 1


def test_interaction_settings_group(config):
    """验证互动设置分组包含正确的功能"""
    function_groups = config.get('webui', {}).get('function_groups', [])
    interaction_group = next((g for g in function_groups if g['name'] == '互动设置'), None)
    assert interaction_group is not None
    assert len(interaction_group['functions']) >= 3
    tab_keys = [f['tab_key'] for f in interaction_group['functions']]
    assert 'integral' in tab_keys
    assert 'assistant_anchor' in tab_keys
    assert 'translate' in tab_keys


def test_ai_dialog_group(config):
    """验证AI对话分组包含正确的功能"""
    function_groups = config.get('webui', {}).get('function_groups', [])
    ai_group = next((g for g in function_groups if g['name'] == 'AI对话'), None)
    assert ai_group is not None
    assert len(ai_group['functions']) >= 3
    tab_keys = [f['tab_key'] for f in ai_group['functions']]
    assert 'llm' in tab_keys
    assert 'talk' in tab_keys
    assert 'image_recognition' in tab_keys


def test_advanced_features_group(config):
    """验证高级功能分组包含正确的功能"""
    function_groups = config.get('webui', {}).get('function_groups', [])
    advanced_group = next((g for g in function_groups if g['name'] == '高级功能'), None)
    assert advanced_group is not None
    assert len(advanced_group['functions']) >= 2
    tab_keys = [f['tab_key'] for f in advanced_group['functions']]
    assert 'visual_body' in tab_keys
    assert 'copywriting' in tab_keys


def test_system_settings_group(config):
    """验证系统设置分组存在"""
    function_groups = config.get('webui', {}).get('function_groups', [])
    system_group = next((g for g in function_groups if g['name'] == '系统设置'), None)
    assert system_group is not None


def test_running_status_group(config):
    """验证运行状态分组存在"""
    function_groups = config.get('webui', {}).get('function_groups', [])
    status_group = next((g for g in function_groups if g['name'] == '运行状态'), None)
    assert status_group is not None


def test_color_mode_group(config):
    """验证色彩模式分组存在"""
    function_groups = config.get('webui', {}).get('function_groups', [])
    color_group = next((g for g in function_groups if g['name'] == '色彩模式'), None)
    assert color_group is not None


def test_each_function_has_required_fields(config):
    """验证每个功能项有必需的字段"""
    function_groups = config.get('webui', {}).get('function_groups', [])
    for group in function_groups:
        for func in group['functions']:
            assert 'name' in func, f"功能 {func} 缺少 name 字段"
            assert 'tab_key' in func, f"功能 {func} 缺少 tab_key 字段"
            assert 'enabled' in func, f"功能 {func} 缺少 enabled 字段"
