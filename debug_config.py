import json
import sys
import os

# 添加utils目录到Python路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'utils'))

from config import Config

def debug_config_loading():
    """调试配置加载过程"""
    print("调试配置加载过程...")
    
    # 重置Config类
    Config.config = None
    Config._config_path = None
    Config._is_multi_file = False
    Config._config_files = []
    
    # 测试加载config目录
    config = Config("config")
    
    print("加载的配置文件：")
    for file_path in Config.get_config_files():
        print(f"  - {file_path}")
    
    print(f"\n是否为多文件模式: {Config.is_multi_file_mode()}")
    
    print(f"\n配置键：{list(config.config.keys())}")
    
    # 检查平台配置
    print(f"\n平台配置：")
    print(f"  bilibili: {config.get('bilibili')}")
    print(f"  twitch: {config.get('twitch')}")
    print(f"  talk: {config.get('talk')}")
    
    # 检查其他配置
    print(f"\n其他配置：")
    print(f"  platform: {config.get('platform')}")
    print(f"  chat_type: {config.get('chat_type')}")
    print(f"  play_audio: {config.get('play_audio')}")

if __name__ == "__main__":
    debug_config_loading()
