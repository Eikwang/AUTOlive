"""
配置管理模块
负责加载和管理应用程序配置
"""
import json
import os
import shutil
import time
from pathlib import Path
from typing import Any, Dict, Optional


class Settings:
    """配置管理类"""

    def __init__(self, config_path: str):
        """
        初始化配置管理器

        Args:
            config_path: 配置文件路径
        """
        self.config_path = Path(config_path)
        self._config: Dict[str, Any] = {}
        self.load()

    def load(self) -> None:
        """加载配置文件。

        文件不存在 = 首次运行，空配置合法；
        存在但解析失败 = 硬失败（保留损坏副本供人工恢复），
        禁止静默降级为空配置（空配置一旦被 save 落盘会毁掉用户全部配置）。
        """
        if not self.config_path.exists():
            self._config = {}
            return
        try:
            with open(self.config_path, 'r', encoding='utf-8') as f:
                self._config = json.load(f)
        except Exception as e:
            backup = self.config_path.with_name(
                f"{self.config_path.stem}.corrupt-{int(time.time())}{self.config_path.suffix}"
            )
            try:
                shutil.copy2(self.config_path, backup)
            except Exception:
                backup = "（副本保留失败）"
            raise RuntimeError(
                f"配置文件解析失败：{e}。已保留损坏副本：{backup}。"
                f"请修复或恢复 {self.config_path} 后重新启动（ refusing to start with empty config）。"
            ) from e

    def save(self) -> None:
        """保存配置到文件（原子写：tmp + os.replace；异常上抛，不吞）。

        E-F6：写失败时调用方（开关处理器）需要感知以回滚 UI 状态，
        静默吞异常会造成「开关已翻转 + 假成功提示 + 重启回滚」事故。
        """
        if not self._config:
            raise RuntimeError("内存配置为空，拒绝落盘（防止覆盖既有配置）")
        tmp_path = self.config_path.with_suffix(
            self.config_path.suffix + f".tmp{os.getpid()}"
        )
        try:
            with open(tmp_path, 'w', encoding='utf-8') as f:
                json.dump(self._config, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, self.config_path)
        except Exception:
            try:
                if tmp_path.exists():
                    os.remove(tmp_path)
            except Exception:
                pass
            raise
    
    def get(self, *keys: str, default: Any = None) -> Any:
        """
        获取配置值
        
        Args:
            *keys: 配置键路径（支持多级键）
            default: 默认值
            
        Returns:
            配置值，如果不存在则返回默认值
        """
        result = self._config
        for key in keys:
            if isinstance(result, dict):
                result = result.get(key, default)
            else:
                return default
        return result
    
    def set(self, *keys: str, value: Any) -> None:
        """
        设置配置值
        
        Args:
            *keys: 配置键路径（支持多级键）
            value: 要设置的值
        """
        if not keys:
            return
        
        config = self._config
        for key in keys[:-1]:
            if key not in config:
                config[key] = {}
            config = config[key]
        
        config[keys[-1]] = value
    
    def update(self, data: Dict[str, Any]) -> None:
        """
        更新配置
        
        Args:
            data: 要更新的配置数据
        """
        self._deep_update(self._config, data)
    
    def _deep_update(self, base: Dict, update: Dict) -> Dict:
        """深度更新字典"""
        for key, value in update.items():
            if isinstance(value, dict) and key in base and isinstance(base[key], dict):
                self._deep_update(base[key], value)
            else:
                base[key] = value
        return base


# 全局配置实例
_config_instance: Optional[Settings] = None


def get_config() -> Settings:
    """获取全局配置实例"""
    global _config_instance
    if _config_instance is None:
        raise RuntimeError("配置尚未初始化，请先调用 init_config()")
    return _config_instance


def init_config(config_path: str) -> Settings:
    """
    初始化全局配置

    Args:
        config_path: 配置文件路径

    Returns:
        配置实例
    """
    global _config_instance
    _config_instance = Settings(config_path)
    return _config_instance


def get_function_groups() -> list:
    """
    获取功能分组列表

    Returns:
        功能分组列表
    """
    config = get_config()
    return config.get("webui", "function_groups", default=[])
