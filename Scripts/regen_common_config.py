# -*- coding: UTF-8 -*-
"""
从 webui-bak.py 重新生成 common_config.py
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

# 替换 ui.tab_panel 为 pass（因为我们在函数内）
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

# 缩进代码
lines = code.split('\n')
indented = ['    ' + line for line in lines]
body = '\n'.join(indented)

# 写入文件
output_path = os.path.join('frontend', 'ui', 'tabs', 'common_config.py')
with open(output_path, 'w', encoding='utf-8') as f:
    f.write(header + body)

print(f'done: {output_path} regenerated ({len(header + body)} chars)')
