"""
Task-19: 更新导航栏分组配置
测试验证 config.json 中 function_groups 分组结构

门禁A 2026-10-19 对齐现行 config.json 重写：
- function_groups 现为 7 组（基础功能/AI对话/互动设置/助播/高级功能/数字人/系统设置），
  前端 layout 经 config["webui"]["function_groups"] 动态读取
- tab_key 归属调整：integral/translate→高级功能，visual_body/train_*→数字人，
  assistant_anchor→独立"助播"组；旧 'web' tab_key 已由 web_captions_printer 等取代
- 旧"直播选项/运行状态/色彩模式"分组已消亡，逐个标记退役
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


@pytest.fixture
def function_groups(config):
    """提取 webui.function_groups"""
    return config.get('webui', {}).get('function_groups', [])


def test_function_groups_has_7_groups(function_groups):
    """验证 function_groups 包含 7 个分组（现行结构）"""
    assert len(function_groups) == 7


def test_function_groups_names(function_groups):
    """验证 7 个分组名称正确"""
    group_names = [g['name'] for g in function_groups]
    expected_names = [
        '基础功能', 'AI对话', '互动设置', '助播',
        '高级功能', '数字人', '系统设置'
    ]
    assert group_names == expected_names


def test_each_group_has_icon(function_groups):
    """验证每个分组有对应的图标"""
    for group in function_groups:
        assert 'icon' in group, f"分组 {group['name']} 缺少 icon 字段"
        assert group['icon'], f"分组 {group['name']} 的 icon 为空"


def test_basic_functions_group(function_groups):
    """验证基础功能分组包含正确的功能"""
    basic_group = next((g for g in function_groups if g['name'] == '基础功能'), None)
    assert basic_group is not None
    assert len(basic_group['functions']) >= 2
    tab_keys = [f['tab_key'] for f in basic_group['functions']]
    assert 'common_config' in tab_keys
    # 旧 'web' 已退役，现行页面配置为 platform_config，播放/字幕页为
    # audio_play / web_captions_printer
    assert 'platform_config' in tab_keys
    assert 'audio_play' in tab_keys
    assert 'web_captions_printer' in tab_keys


@pytest.mark.skip(reason="退役：'直播选项'分组已并入现行 7 组结构（播放/字幕等移入基础功能），不再单独成组（门禁A 2026-10-19）")
def test_live_options_group(config):
    """验证直播选项分组包含正确的功能"""
    function_groups = config.get('webui', {}).get('function_groups', [])
    live_group = next((g for g in function_groups if g['name'] == '直播选项'), None)
    assert live_group is not None
    assert len(live_group['functions']) >= 1


def test_interaction_settings_group(function_groups):
    """验证互动设置分组包含正确的功能"""
    interaction_group = next((g for g in function_groups if g['name'] == '互动设置'), None)
    assert interaction_group is not None
    assert len(interaction_group['functions']) >= 3
    tab_keys = [f['tab_key'] for f in interaction_group['functions']]
    # integral/assistant_anchor/translate 已移出（高级功能/助播组），现行以
    # 过滤器/直播间互动功能为主
    assert 'filter_config' in tab_keys
    assert 'blacklist' in tab_keys
    assert 'read_comment' in tab_keys
    assert 'thanks' in tab_keys


def test_ai_dialog_group(function_groups):
    """验证AI对话分组包含正确的功能"""
    ai_group = next((g for g in function_groups if g['name'] == 'AI对话'), None)
    assert ai_group is not None
    assert len(ai_group['functions']) >= 3
    tab_keys = [f['tab_key'] for f in ai_group['functions']]
    assert 'llm' in tab_keys
    assert 'talk' in tab_keys


def test_advanced_features_group(function_groups):
    """验证高级功能分组包含正确的功能"""
    advanced_group = next((g for g in function_groups if g['name'] == '高级功能'), None)
    assert advanced_group is not None
    assert len(advanced_group['functions']) >= 2
    tab_keys = [f['tab_key'] for f in advanced_group['functions']]
    # visual_body 已移至"数字人"组；integral/translate 现归属本组
    assert 'integral' in tab_keys
    assert 'translate' in tab_keys


def test_anchor_group(function_groups):
    """验证助播分组存在且包含助播功能（原互动设置内 assistant_anchor 独立成组）"""
    anchor_group = next((g for g in function_groups if g['name'] == '助播'), None)
    assert anchor_group is not None
    tab_keys = [f['tab_key'] for f in anchor_group['functions']]
    assert 'assistant_anchor' in tab_keys


def test_digital_human_group(function_groups):
    """验证数字人分组包含形象/训练功能（原高级功能内 visual_body 独立成组）"""
    dh_group = next((g for g in function_groups if g['name'] == '数字人'), None)
    assert dh_group is not None
    tab_keys = [f['tab_key'] for f in dh_group['functions']]
    assert 'visual_body' in tab_keys
    assert 'train_streamer' in tab_keys
    assert 'train_voice' in tab_keys


def test_system_settings_group(function_groups):
    """验证系统设置分组存在"""
    system_group = next((g for g in function_groups if g['name'] == '系统设置'), None)
    assert system_group is not None


@pytest.mark.skip(reason="退役：'运行状态'分组已消亡，运行状态现经侧边栏服务状态条/启停控制区呈现，不再是导航分组（门禁A 2026-10-19）")
def test_running_status_group(config):
    """验证运行状态分组存在"""
    function_groups = config.get('webui', {}).get('function_groups', [])
    status_group = next((g for g in function_groups if g['name'] == '运行状态'), None)
    assert status_group is not None


@pytest.mark.skip(reason="退役：'色彩模式'分组已消亡，色彩模式现经侧边栏主题切换按钮（深色/浅色）呈现，不再是导航分组（门禁A 2026-10-19）")
def test_color_mode_group(config):
    """验证色彩模式分组存在"""
    function_groups = config.get('webui', {}).get('function_groups', [])
    color_group = next((g for g in function_groups if g['name'] == '色彩模式'), None)
    assert color_group is not None


def test_each_function_has_required_fields(function_groups):
    """验证每个功能项有必需的字段"""
    for group in function_groups:
        for func in group['functions']:
            assert 'name' in func, f"功能 {func} 缺少 name 字段"
            assert 'tab_key' in func, f"功能 {func} 缺少 tab_key 字段"
            assert 'enabled' in func, f"功能 {func} 缺少 enabled 字段"
