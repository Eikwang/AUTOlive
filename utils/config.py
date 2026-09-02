import json
import os
import logging
from pathlib import Path
from typing import Dict, List, Optional, Any

logger = logging.getLogger(__name__)


class Config:
    """
    配置管理类
    
    支持多文件配置加载和热更新。
    支持两种模式：
    1. 单文件模式：传统单个config.json文件
    2. 多文件模式：config目录下的多个配置文件
    
    所有 Config 实例共享同一份配置数据（类级别缓存）。
    """
    # 类级别配置缓存，所有实例共享
    config = None
    _config_path = None  # 配置文件或目录路径
    _is_multi_file = False  # 是否为多文件模式
    _config_files = []  # 多文件模式下的配置文件列表
    _callbacks = []  # 配置变化回调列表

    def __init__(self, config_path: str):
        """
        初始化配置管理器
        
        Args:
            config_path: 配置文件路径或配置目录路径
        """
        self._config_path = config_path
        
        if Config.config is None:
            Config._config_path = config_path
            self._load_config(config_path)
    
    def _load_config(self, config_path: str):
        """
        加载配置文件
        
        Args:
            config_path: 配置文件路径或配置目录路径
        """
        path = Path(config_path)
        
        if path.is_dir():
            # 多文件模式：加载目录下的所有配置文件
            self._load_multi_file_config(path)
        elif path.is_file():
            # 单文件模式：加载单个配置文件
            self._load_single_file_config(path)
        else:
            logger.error(f"配置路径不存在: {config_path}")
            raise FileNotFoundError(f"配置路径不存在: {config_path}")
    
    def _load_single_file_config(self, file_path: Path):
        """
        加载单个配置文件
        
        Args:
            file_path: 配置文件路径
        """
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                Config.config = json.load(f)
            Config._is_multi_file = False
            Config._config_files = [str(file_path)]
            logger.info(f"单文件配置加载成功: {file_path}")
        except json.JSONDecodeError as e:
            logger.error(f"配置文件JSON格式错误: {e}")
            raise
        except Exception as e:
            logger.error(f"配置文件加载失败: {e}")
            raise
    
    def _load_multi_file_config(self, config_dir: Path):
        """
        加载多文件配置
        
        Args:
            config_dir: 配置目录路径
        """
        try:
            # 收集所有配置文件
            config_files = []
            
            # 加载基础配置
            base_config_path = config_dir / "base.json"
            if base_config_path.exists():
                config_files.append(str(base_config_path))
            
            # 加载平台配置
            platforms_dir = config_dir / "platforms"
            if platforms_dir.exists():
                for file_path in platforms_dir.glob("*.json"):
                    if not file_path.name.endswith('.example.json'):
                        config_files.append(str(file_path))
            
            # 加载LLM配置
            llm_dir = config_dir / "llm"
            if llm_dir.exists():
                for file_path in llm_dir.glob("*.json"):
                    if not file_path.name.endswith('.example.json'):
                        config_files.append(str(file_path))
            
            # 加载TTS配置
            tts_dir = config_dir / "tts"
            if tts_dir.exists():
                for file_path in tts_dir.glob("*.json"):
                    if not file_path.name.endswith('.example.json'):
                        config_files.append(str(file_path))
            
            # 加载features配置
            features_dir = config_dir / "features"
            if features_dir.exists():
                for file_path in features_dir.glob("*.json"):
                    if not file_path.name.endswith('.example.json'):
                        config_files.append(str(file_path))
            
            # 按文件名排序，确保加载顺序一致
            config_files.sort()
            
            # 合并所有配置
            merged_config = {}
            for config_file in config_files:
                try:
                    with open(config_file, 'r', encoding='utf-8') as f:
                        file_config = json.load(f)
                    
                    # 获取文件名（不含扩展名）作为配置键
                    file_name = Path(config_file).stem
                    
                    # 判断配置文件类型
                    if 'platforms' in config_file:
                        # 平台配置：以平台名称为键
                        platform_name = file_name
                        merged_config[platform_name] = file_config
                    elif 'llm' in config_file:
                        # LLM配置：以LLM名称为键
                        llm_name = file_name
                        merged_config[llm_name] = file_config
                    elif 'tts' in config_file:
                        # TTS配置：合并到tts相关配置
                        self._deep_merge(merged_config, file_config)
                    elif 'features' in config_file:
                        # features配置：合并到主配置
                        self._deep_merge(merged_config, file_config)
                    elif file_name == 'base':
                        # 基础配置：合并到主配置
                        self._deep_merge(merged_config, file_config)
                    else:
                        # 其他配置：合并到主配置
                        self._deep_merge(merged_config, file_config)
                    
                    logger.debug(f"加载配置文件: {config_file}")
                except json.JSONDecodeError as e:
                    logger.error(f"配置文件JSON格式错误 {config_file}: {e}")
                    continue
                except Exception as e:
                    logger.error(f"配置文件加载失败 {config_file}: {e}")
                    continue
            
            if not merged_config:
                logger.error("没有找到有效的配置文件")
                raise ValueError("没有找到有效的配置文件")
            
            Config.config = merged_config
            Config._is_multi_file = True
            Config._config_files = config_files
            
            logger.info(f"多文件配置加载成功，共加载 {len(config_files)} 个配置文件")
            
        except Exception as e:
            logger.error(f"多文件配置加载失败: {e}")
            raise
    
    def _deep_merge(self, base: Dict, override: Dict):
        """
        深度合并两个字典
        
        Args:
            base: 基础字典
            override: 覆盖字典
        """
        for key, value in override.items():
            if key in base and isinstance(base[key], dict) and isinstance(value, dict):
                # 递归合并嵌套字典
                self._deep_merge(base[key], value)
            else:
                # 直接覆盖
                base[key] = value
    
    @classmethod
    def reload(cls, config_path: str = None):
        """
        重新加载配置文件（热更新）
        
        Args:
            config_path: 配置文件或目录路径，为None时使用上次的路径
        
        Returns:
            bool: 是否成功重新加载
        """
        path = config_path or cls._config_path
        if not path:
            logger.error("配置路径未设置，无法重新加载")
            return False
        
        if not os.path.exists(path):
            logger.error(f"配置路径不存在: {path}")
            return False
        
        try:
            # 创建临时实例来加载配置
            temp_config = Config.__new__(cls)
            temp_config._load_config(path)
            
            # 更新类级别配置
            cls.config = temp_config.config
            cls._config_path = path
            cls._is_multi_file = temp_config._is_multi_file
            cls._config_files = temp_config._config_files
            
            # 触发回调
            for callback in cls._callbacks:
                try:
                    callback(cls.config)
                except Exception as e:
                    logger.error(f"配置变化回调执行失败: {e}")
            
            logger.info("配置重新加载成功")
            return True
            
        except Exception as e:
            logger.error(f"配置重新加载失败: {e}")
            return False
    
    @classmethod
    def on_config_change(cls, callback):
        """
        注册配置变化回调函数
        
        Args:
            callback: 回调函数，接收新配置字典作为参数
        """
        if callback not in cls._callbacks:
            cls._callbacks.append(callback)
    
    @classmethod
    def remove_callback(cls, callback):
        """移除配置变化回调函数"""
        if callback in cls._callbacks:
            cls._callbacks.remove(callback)
    
    def __getitem__(self, key):
        return self.config.get(key)
    
    def get(self, *keys):
        """
        获取嵌套配置值
        
        Args:
            *keys: 配置键路径
            
        Returns:
            配置值，如果不存在返回None
        """
        result = self.config
        for key in keys:
            if result is None:
                break
            result = result.get(key, None)
        return result
    
    @classmethod
    def get_config_files(cls) -> List[str]:
        """获取当前加载的配置文件列表"""
        return cls._config_files.copy()
    
    @classmethod
    def is_multi_file_mode(cls) -> bool:
        """是否为多文件模式"""
        return cls._is_multi_file