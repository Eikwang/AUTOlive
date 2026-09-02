"""
配置文件热更新模块

使用 watchdog 监听配置文件变化，实现配置自动重新加载。
支持防抖机制，避免短时间内多次触发。
支持多文件配置模式。

使用方法:
    from utils.config_watcher import ConfigWatcher

    def on_config_change(new_config):
        # 处理配置变化
        pass

    # 单文件模式
    watcher = ConfigWatcher("config.json", callback=on_config_change)
    watcher.start()

    # 多文件模式
    watcher = ConfigWatcher("config", callback=on_config_change)
    watcher.start()

    # 程序退出时
    watcher.stop()
"""

import json
import time
import threading
import logging
from pathlib import Path
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

logger = logging.getLogger(__name__)


class ConfigFileHandler(FileSystemEventHandler):
    """配置文件变化处理器"""

    def __init__(self, config_path: str, callback=None, debounce_seconds: float = 1.0):
        """
        初始化处理器

        Args:
            config_path: 配置文件或目录路径
            callback: 配置变化回调函数，接收新配置字典作为参数
            debounce_seconds: 防抖时间（秒），默认1秒
        """
        self.config_path = Path(config_path).resolve()
        self.callback = callback
        self.debounce_seconds = debounce_seconds
        self._last_modified = 0
        self._lock = threading.Lock()
        self._is_dir = self.config_path.is_dir()
        
        # 多文件模式下，监听所有配置文件
        if self._is_dir:
            self._config_files = self._collect_config_files()
        else:
            self._config_files = [self.config_path]

    def _collect_config_files(self):
        """收集目录下的所有配置文件"""
        config_files = []
        
        # 基础配置
        base_config = self.config_path / "base.json"
        if base_config.exists():
            config_files.append(base_config)
        
        # 平台配置
        platforms_dir = self.config_path / "platforms"
        if platforms_dir.exists():
            for file_path in platforms_dir.glob("*.json"):
                if not file_path.name.endswith('.example.json'):
                    config_files.append(file_path)
        
        # LLM配置
        llm_dir = self.config_path / "llm"
        if llm_dir.exists():
            for file_path in llm_dir.glob("*.json"):
                if not file_path.name.endswith('.example.json'):
                    config_files.append(file_path)
        
        # TTS配置
        tts_dir = self.config_path / "tts"
        if tts_dir.exists():
            for file_path in tts_dir.glob("*.json"):
                if not file_path.name.endswith('.example.json'):
                    config_files.append(file_path)
        
        # features配置
        features_dir = self.config_path / "features"
        if features_dir.exists():
            for file_path in features_dir.glob("*.json"):
                if not file_path.name.endswith('.example.json'):
                    config_files.append(file_path)
        
        return config_files

    def on_modified(self, event):
        """文件修改事件处理"""
        # 只处理目标配置文件
        event_path = Path(event.src_path).resolve()
        
        # 检查是否是配置文件
        is_config_file = False
        if self._is_dir:
            # 多文件模式：检查是否在配置目录中
            is_config_file = any(event_path == config_file for config_file in self._config_files)
        else:
            # 单文件模式：检查是否是目标文件
            is_config_file = event_path == self.config_path
        
        if not is_config_file:
            return

        # 防抖：忽略短时间内重复触发
        with self._lock:
            current_time = time.time()
            if current_time - self._last_modified < self.debounce_seconds:
                return
            self._last_modified = current_time

        logger.info(f"检测到配置文件变化: {event.src_path}")

        try:
            # 等待一小段时间，确保文件写入完成
            time.sleep(0.1)

            # 重新加载配置
            from utils.config import Config
            
            if self._is_dir:
                # 多文件模式：重新加载整个目录
                Config.reload(str(self.config_path))
            else:
                # 单文件模式：重新加载单个文件
                Config.reload(str(self.config_path))
            
            if self.callback:
                self.callback(Config.config)

            logger.info("配置热更新成功")

        except json.JSONDecodeError as e:
            logger.error(f"配置文件JSON格式错误: {e}")
        except Exception as e:
            logger.error(f"配置热更新失败: {e}")


class ConfigWatcher:
    """配置文件热更新监视器"""

    def __init__(self, config_path: str, callback=None, debounce_seconds: float = 1.0):
        """
        初始化配置监视器

        Args:
            config_path: 配置文件或目录路径
            callback: 配置变化回调函数，接收新配置字典作为参数
            debounce_seconds: 防抖时间（秒），默认1秒
        """
        self.config_path = config_path
        self.callback = callback
        self.debounce_seconds = debounce_seconds

        self._handler = ConfigFileHandler(
            config_path=config_path,
            callback=callback,
            debounce_seconds=debounce_seconds
        )
        self._observer = Observer()
        
        # 监听配置文件所在目录
        config_path_obj = Path(config_path)
        if config_path_obj.is_dir():
            watch_dir = str(config_path_obj)
        else:
            watch_dir = str(config_path_obj.parent or ".")
        
        self._observer.schedule(self._handler, watch_dir, recursive=True)
        self._running = False

        logger.info(f"配置监视器初始化: {config_path}")

    def start(self):
        """启动配置监视器"""
        if not self._running:
            self._observer.start()
            self._running = True
            logger.info("配置监视器已启动")

    def stop(self):
        """停止配置监视器"""
        if self._running:
            self._observer.stop()
            self._observer.join(timeout=5)
            self._running = False
            logger.info("配置监视器已停止")

    def is_running(self) -> bool:
        """检查监视器是否运行中"""
        return self._running

    def set_callback(self, callback):
        """设置回调函数"""
        self.callback = callback
        self._handler.callback = callback