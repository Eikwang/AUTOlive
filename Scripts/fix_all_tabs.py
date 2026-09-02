# -*- coding: UTF-8 -*-
"""
批量修复所有 tab 模块中的 config.get() 调用
"""
import re
import os
import glob

def fix_file(filepath):
    """修复单个文件"""
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    
    original = content
    
    # 替换 config.get("k1", "k2", "k3", "k4", default=xxx) 格式
    content = re.sub(
        r'config\.get\("([^"]+)",\s*"([^"]+)",\s*"([^"]+)",\s*"([^"]+)"\)',
        r'get_nested_value(config, "\1", "\2", "\3", "\4")',
        content
    )
    
    # 替换 config.get("k1", "k2", "k3", default=xxx) 格式
    content = re.sub(
        r'config\.get\("([^"]+)",\s*"([^"]+)",\s*"([^"]+)",\s*default=([^)]+)\)',
        r'get_nested_value(config, "\1", "\2", "\3", default=\4)',
        content
    )
    
    # 替换 config.get("k1", "k2", "k3") 格式
    content = re.sub(
        r'config\.get\("([^"]+)",\s*"([^"]+)",\s*"([^"]+)"\)',
        r'get_nested_value(config, "\1", "\2", "\3")',
        content
    )
    
    # 替换 config.get("k1", "k2", []) 格式
    content = re.sub(
        r'config\.get\("([^"]+)",\s*"([^"]+)",\s*\[\]\)',
        r'get_nested_value(config, "\1", "\2", default=[])',
        content
    )
    
    # 替换 config.get("k1", "k2") 格式
    content = re.sub(
        r'config\.get\("([^"]+)",\s*"([^"]+)"\)',
        r'get_nested_value(config, "\1", "\2")',
        content
    )
    
    # 替换单 key 的 config.get("k") 格式
    content = re.sub(
        r'(?<!\.)config\.get\("([^"]+)"\)',
        r'get_nested_value(config, "\1")',
        content
    )
    
    # 确保有导入
    if 'get_nested_value' in content and 'from frontend.ui.components.config_helper import get_nested_value' not in content:
        content = 'from frontend.ui.components.config_helper import get_nested_value\n' + content
    
    if content != original:
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
        return True
    return False

# 修复所有 tab 模块
tab_files = glob.glob('frontend/ui/tabs/*.py')
fixed = []

for filepath in tab_files:
    if '__init__' in filepath or 'config_helper' in filepath:
        continue
    if fix_file(filepath):
        fixed.append(filepath)
        print(f'fixed: {filepath}')

# 修复 main.py
if fix_file('frontend/main.py'):
    fixed.append('frontend/main.py')
    print('fixed: frontend/main.py')

print(f'\ntotal fixed: {len(fixed)} files')
