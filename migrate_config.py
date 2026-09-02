#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
配置文件迁移脚本

将单个config.json文件拆分为多个配置文件。

使用方法:
    python migrate_config.py

功能:
    1. 读取config.json文件
    2. 按功能拆分为多个配置文件
    3. 创建config目录结构
    4. 生成配置文件
    5. 验证配置加载
"""

import json
import os
import sys
from pathlib import Path

def load_config(config_file: str) -> dict:
    """
    加载配置文件
    
    Args:
        config_file: 配置文件路径
        
    Returns:
        配置字典
    """
    try:
        with open(config_file, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        print(f"[ERROR] 配置文件不存在: {config_file}")
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"[ERROR] 配置文件JSON格式错误: {e}")
        sys.exit(1)

def create_directory_structure():
    """创建配置目录结构"""
    directories = [
        "config",
        "config/platforms",
        "config/llm",
        "config/tts",
        "config/features"
    ]
    
    for directory in directories:
        os.makedirs(directory, exist_ok=True)
        print(f"[OK] 创建目录: {directory}")

def split_config(config: dict):
    """
    拆分配置文件
    
    Args:
        config: 原始配置字典
    """
    # 创建基础配置
    base_config = {
        'platform': config.get('platform', 'talk'),
        'room_display_id': config.get('room_display_id', ''),
        'chat_type': config.get('chat_type', 'custom_llm'),
        'need_lang': config.get('need_lang', 'none'),
        'before_prompt': config.get('before_prompt', '请简要回复:'),
        'after_prompt': config.get('after_prompt', ''),
        'api_ip': config.get('api_ip', '127.0.0.1'),
        'api_port': config.get('api_port', 8082)
    }
    
    # 创建平台配置
    platforms = {
        'bilibili': config.get('bilibili', {}),
        'twitch': config.get('twitch', {}),
        'talk': config.get('talk', {})
    }
    
    # 创建LLM配置
    llm_config = {
        'custom_llm': config.get('custom_llm', {}),
        'gemini': config.get('gemini', {}),
        'search_online': config.get('search_online', {})
    }
    
    # 创建TTS配置
    tts_config = {
        'play_audio': config.get('play_audio', {}),
        'audio_player': config.get('audio_player', {}),
        'audio_synthesis_type': config.get('audio_synthesis_type', ''),
        'audio_random_speed': config.get('audio_random_speed', False),
        'so_vits_svc': config.get('so_vits_svc', {}),
        'gpt_sovits': config.get('gpt_sovits', {})
    }
    
    # 创建features配置
    features_config = {
        'read_comment': config.get('read_comment', {}),
        'filter': config.get('filter', {}),
        'thanks': config.get('thanks', {}),
        'local_qa': config.get('local_qa', {}),
        'choose_song': config.get('choose_song', {}),
        'sd': config.get('sd', {}),
        'copywriting': config.get('copywriting', {}),
        'header': config.get('header', {}),
        'image_recognition': config.get('image_recognition', {}),
        'captions': config.get('captions', {}),
        'schedule': config.get('schedule', {}),
        'idle_time_task': config.get('idle_time_task', {}),
        'database': config.get('database', {}),
        'game': config.get('game', {}),
        'trends_copywriting': config.get('trends_copywriting', {}),
        'web_captions_printer': config.get('web_captions_printer', {}),
        'integral': config.get('integral', {}),
        'key_mapping': config.get('key_mapping', {}),
        'custom_cmd': config.get('custom_cmd', {}),
        'translate': config.get('translate', {}),
        'abnormal_alarm': config.get('abnormal_alarm', {}),
        'trends_config': config.get('trends_config', {}),
        'coordination_program': config.get('coordination_program', {}),
        'assistant_anchor': config.get('assistant_anchor', {}),
        'serial': config.get('serial', {}),
        'data_analysis': config.get('data_analysis', {}),
        'webui': config.get('webui', {}),
        'login': config.get('login', {}),
        'comment_template': config.get('comment_template', {}),
        'reply_template': config.get('reply_template', {}),
        'comment_log_type': config.get('comment_log_type', '回答'),
        'visual_body': config.get('visual_body', 'metahuman_stream'),
        'luoxi_project': config.get('luoxi_project', {}),
        'metahuman_stream': config.get('metahuman_stream', {}),
        'ordinaryroad_barrage_fly': config.get('ordinaryroad_barrage_fly', {})
    }
    
    # 写入配置文件
    write_config_file("config/base.json", base_config)
    
    for platform_name, platform_config in platforms.items():
        write_config_file(f"config/platforms/{platform_name}.json", platform_config)
    
    for llm_name, llm_config_item in llm_config.items():
        write_config_file(f"config/llm/{llm_name}.json", llm_config_item)
    
    write_config_file("config/tts/tts.json", tts_config)
    write_config_file("config/features/features.json", features_config)

def write_config_file(file_path: str, config: dict):
    """
    写入配置文件
    
    Args:
        file_path: 文件路径
        config: 配置字典
    """
    try:
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
        print(f"[OK] 写入配置文件: {file_path}")
    except Exception as e:
        print(f"[ERROR] 写入配置文件失败 {file_path}: {e}")

def verify_config():
    """验证配置加载"""
    print("\n验证配置加载...")
    
    try:
        # 添加utils目录到Python路径
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'utils'))
        
        from config import Config
        
        # 测试多文件配置加载
        config = Config("config")
        
        # 验证基础配置
        assert config.get('platform') == 'talk', "平台配置错误"
        assert config.get('chat_type') == 'custom_llm', "聊天类型配置错误"
        
        # 验证平台配置
        assert config.get('bilibili') is not None, "Bilibili配置加载失败"
        assert config.get('twitch') is not None, "Twitch配置加载失败"
        assert config.get('talk') is not None, "Talk配置加载失败"
        
        # 验证LLM配置
        assert config.get('custom_llm') is not None, "自定义LLM配置加载失败"
        assert config.get('gemini') is not None, "Gemini配置加载失败"
        
        # 验证TTS配置
        assert config.get('play_audio') is not None, "播放音频配置加载失败"
        
        # 验证features配置
        assert config.get('read_comment') is not None, "读取评论配置加载失败"
        
        print("[OK] 配置验证通过！")
        
        # 显示配置文件列表
        config_files = Config.get_config_files()
        print(f"\n加载的配置文件数量: {len(config_files)}")
        for file_path in config_files:
            print(f"  - {file_path}")
        
        return True
        
    except Exception as e:
        print(f"[ERROR] 配置验证失败: {e}")
        import traceback
        traceback.print_exc()
        return False

def main():
    """主函数"""
    print("Luna AI 配置文件迁移工具")
    print("=" * 50)
    
    # 检查config.json是否存在
    if not os.path.exists("config.json"):
        print("[ERROR] config.json文件不存在")
        print("请确保当前目录下有config.json文件")
        sys.exit(1)
    
    # 加载原始配置
    print("\n加载原始配置...")
    config = load_config("config.json")
    print(f"[OK] 加载配置成功，包含 {len(config)} 个顶级配置项")
    
    # 创建目录结构
    print("\n创建目录结构...")
    create_directory_structure()
    
    # 拆分配置文件
    print("\n拆分配置文件...")
    split_config(config)
    
    # 验证配置
    print("\n验证配置...")
    if verify_config():
        print("\n" + "=" * 50)
        print("[SUCCESS] 配置迁移完成！")
        print("\n新的配置文件已创建在config目录下。")
        print("您可以：")
        print("1. 检查生成的配置文件")
        print("2. 测试配置加载功能")
        print("3. 更新代码使用新的配置加载方式")
        print("\n向后兼容：原始config.json文件保持不变，仍可使用单文件模式。")
    else:
        print("\n[ERROR] 配置迁移失败，请检查配置文件")
        sys.exit(1)

if __name__ == "__main__":
    main()