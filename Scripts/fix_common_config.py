# -*- coding: UTF-8 -*-
"""
修复 common_config.py
1. 从原始 webui-bak.py 提取代码
2. 替换 config.get 为 get_nested_value
3. 定义所有需要的变量
4. 修复缩进
"""
import re
import os

# 读取原始文件
with open('webui-bak.py', 'r', encoding='utf-8') as f:
    original = f.read()

# 提取 common_config_page tab_panel 的内容
start = original.find('with ui.tab_panel(common_config_page)')
end = original.find('with ui.tab_panel(llm_page)')

if start == -1 or end == -1:
    print('error: cannot find boundaries')
    exit(1)

code = original[start:end]

# 替换 ui.tab_panel 为 pass
code = code.replace('with ui.tab_panel(common_config_page).style(tab_panel_css):', '')

# 替换 config.get 为 get_nested_value
def replace_config_get(match):
    args = match.group(1)
    parts = [p.strip() for p in args.split(',')]
    if len(parts) == 1:
        return 'get_nested_value(config, ' + parts[0] + ')'
    elif len(parts) == 2:
        return 'get_nested_value(config, ' + parts[0] + ', default=' + parts[1] + ')'
    else:
        return 'get_nested_value(config, ' + ', '.join(parts) + ')'

code = re.sub(r'config\.get\(([^)]+)\)', replace_config_get, code)

# 提取所有需要的变量定义
# 从原始文件中查找 platform_options, chat_type_options 等定义
variables = {}

# platform_options
match = re.search(r'platform_options\s*=\s*\{[^}]+\}', original, re.DOTALL)
if match:
    variables['platform_options'] = match.group(0)

# chat_type_options
match = re.search(r'chat_type_options\s*=\s*\{[^}]+\}', original, re.DOTALL)
if match:
    variables['chat_type_options'] = match.group(0)

# visual_body_options
match = re.search(r'visual_body_options\s*=\s*\{[^}]+\}', original, re.DOTALL)
if match:
    variables['visual_body_options'] = match.group(0)

# audio_synthesis_type_options
match = re.search(r'audio_synthesis_type_options\s*=\s*\{[^}]+\}', original, re.DOTALL)
if match:
    variables['audio_synthesis_type_options'] = match.group(0)

# 生成文件头
header = '''# -*- coding: UTF-8 -*-
"""
通用配置标签页模块
从 webui-bak.py 的 common_config_page 拆分而来
"""
from nicegui import ui
from typing import Dict, Any, Callable, List, Optional

from frontend.ui.components import FormField
from frontend.ui.components.config_helper import get_nested_value


def textarea_data_change(data):
    """字符串数组数据格式转换"""
    if data is None:
        return ""
    tmp_str = ""
    for tmp in data:
        tmp_str = tmp_str + tmp + chr(10)
    return tmp_str


def create_common_config_tab(
    config: Dict[str, Any],
    theme_config: Dict[str, str],
    set_config_callback: Callable
):
    """创建通用配置标签页"""
    tab_panel_css = theme_config.get("tab_panel", "")
    card_css = theme_config.get("card", "")
    switch_internal_css = theme_config.get("switch_internal", "")
    button_internal_color = theme_config.get("button_internal_color", "primary")
    button_internal_css = theme_config.get("button_internal_css", "")

'''

# 添加变量定义
for name, value in variables.items():
    header += '    ' + value + '\n\n'

# 修复缩进
lines = code.split('\n')
new_lines = []
for line in lines:
    if line.strip() == '':
        new_lines.append(line)
        continue
    
    # 计算当前缩进
    indent = len(line) - len(line.lstrip())
    
    # 如果缩进 >= 16，减少 12 个空格
    if indent >= 16:
        new_line = '    ' + line[12:]  # 移除前 12 个空格，添加 4 个空格
        new_lines.append(new_line)
    elif indent >= 4:
        new_lines.append(line)
    else:
        new_lines.append(line)

body = '\n'.join(new_lines)

# 写入文件
output_path = os.path.join('frontend', 'ui', 'tabs', 'common_config.py')
with open(output_path, 'w', encoding='utf-8') as f:
    f.write(header + body)

print(f'done: {output_path} regenerated ({len(header + body)} chars)')
