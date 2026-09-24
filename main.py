"""
主程序入口
模块化重构后的主程序，调用各个功能模块
"""

import os
import sys
import time
import threading
import traceback
from typing import Optional

# 添加项目根目录到Python路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from utils.my_log import logger
from utils.config import Config
from utils.common import Common
from utils.my_handle import My_handle
import utils.my_global as my_global

# 导入各个模块
from utils.web_server import WebServer, get_web_server
from utils.audio_processor import AudioProcessor, get_audio_processor
from utils.conversation_manager import ConversationManager, get_conversation_manager
from utils.input_handler import InputHandler, get_input_handler
from utils.task_scheduler import TaskScheduler, get_task_scheduler


class MainApplication:
    """主应用程序类"""
    
    def __init__(self, config_path: str = "config.json"):
        """
        初始化主应用程序
        
        Args:
            config_path: 配置文件路径
        """
        self.config_path = config_path
        self.config = None
        self.common = None
        self.my_handle = None
        
        # 各个模块实例
        self.web_server = None
        self.audio_processor = None
        self.conversation_manager = None
        self.input_handler = None
        self.task_scheduler = None
        
        # 初始化状态
        self.is_initialized = False
        self.is_running = False
        
        logger.info("主应用程序初始化开始")
    
    def initialize(self):
        """初始化应用程序"""
        try:
            # 初始化配置
            self.config = Config(self.config_path)
            logger.info(f"配置文件加载完成: {self.config_path}")
            
            # 初始化通用工具
            self.common = Common(self.config)
            logger.info("通用工具初始化完成")
            
            # 初始化处理器
            self.my_handle = My_handle(self.config, self.common)
            logger.info("处理器初始化完成")
            
            # 初始化各个模块
            self._init_modules()
            
            self.is_initialized = True
            logger.info("应用程序初始化完成")
            
        except Exception as e:
            logger.error(f"应用程序初始化失败: {traceback.format_exc()}")
            raise
    
    def _init_modules(self):
        """初始化各个功能模块"""
        # 获取平台标识
        platform = self.config.get("platform", default="")
        
        # 初始化Web服务器
        self.web_server = get_web_server(self.config, self.my_handle)
        logger.info("Web服务器模块初始化完成")
        
        # 初始化音频处理器
        self.audio_processor = get_audio_processor(self.config, self.my_handle)
        logger.info("音频处理器模块初始化完成")
        
        # 初始化对话管理器
        self.conversation_manager = get_conversation_manager(self.config, self.common, self.my_handle)
        logger.info("对话管理器模块初始化完成")
        
        # 初始化输入处理器
        self.input_handler = get_input_handler(
            self.config,
            on_start_recording=self._on_start_recording,
            on_stop_recording=self._on_stop_recording
        )
        logger.info("输入处理器模块初始化完成")
        
        # 初始化任务调度器
        self.task_scheduler = get_task_scheduler(self.config, self.common, self.my_handle, platform)
        logger.info("任务调度器模块初始化完成")

        # 托管服务（P1-1）：gpt_sovits.hosted=true 时随系统拉起 api_v2，
        # 状态条（P1-3）经 ServiceRegistry 自动展示；失败不阻断主程序（可手动重启）
        try:
            from utils.autolive_paths import AutolivePaths
            from utils.gpt_sovits_service import build_gpt_sovits_service
            self.autolive_paths = AutolivePaths(self.config)
            gpt_svc = build_gpt_sovits_service(self.config, self.autolive_paths)
            if gpt_svc is not None:
                threading.Thread(target=gpt_svc.start, name="svc-start-gpt-sovits", daemon=True).start()
                logger.info("GPT-SoVITS 托管服务拉起已后台启动")
        except Exception:
            logger.error(f"托管服务初始化失败（不阻断主程序）: {traceback.format_exc()}")
    
    def _on_start_recording(self):
        """开始录音的回调函数"""
        logger.info("开始录音回调触发")
        if self.audio_processor:
            self.audio_processor.start_recording_thread()
    
    def _on_stop_recording(self):
        """停止录音的回调函数"""
        logger.info("停止录音回调触发")
        if self.audio_processor:
            self.audio_processor.stop_recording()
        if self.conversation_manager:
            self.conversation_manager.stop_do_listen_and_comment_thread_event.set()
    
    def start(self):
        """启动应用程序"""
        if not self.is_initialized:
            logger.error("应用程序未初始化，无法启动")
            return
        
        try:
            self.is_running = True
            
            # 启动Web服务器
            api_port = self.config.get("api_port", default=8000)
            web_port = self.config.get("web_port", default=8080)
            self.web_server.start_all(api_port, web_port)
            logger.info(f"Web服务器已启动，API端口: {api_port}, Web端口: {web_port}")
            
            # 启动任务调度器
            self.task_scheduler.start_all()
            logger.info("任务调度器已启动")
            
            # 启动输入监听
            if self.config.get("talk", "key_listener_enable"):
                self.input_handler.start_listening()
                logger.info("输入监听已启动")
            
            # 是否启用直接运行对话
            if self.config.get("talk", "direct_run_talk"):
                logger.info("直接运行对话模式已启用")
                self.conversation_manager.direct_run_talk()
            
            logger.info("应用程序启动完成")
            
            # 主线程保持运行
            self._main_loop()
            
        except Exception as e:
            logger.error(f"应用程序启动失败: {traceback.format_exc()}")
            self.stop()
    
    def _main_loop(self):
        """主循环"""
        try:
            while self.is_running:
                time.sleep(1)
                
                # 这里可以添加主循环的逻辑
                # 比如监控各个模块的状态
                
        except KeyboardInterrupt:
            logger.info("收到中断信号，正在停止...")
        except Exception as e:
            logger.error(f"主循环异常: {traceback.format_exc()}")
        finally:
            self.stop()
    
    def stop(self):
        """停止应用程序"""
        logger.info("正在停止应用程序...")
        
        self.is_running = False
        
        # 停止各个模块
        if self.task_scheduler:
            self.task_scheduler.stop_all()
        
        if self.input_handler:
            self.input_handler.stop_listening()
        
        if self.conversation_manager:
            self.conversation_manager.stop_all()
        
        if self.web_server:
            self.web_server.stop_all()

        # 停止托管服务（P1-1）：优雅 terminate→kill，状态条随 ServiceRegistry 清空
        try:
            from utils.service_orchestrator import ServiceRegistry
            for svc in ServiceRegistry.instance().all():
                svc.stop()
        except Exception:
            logger.error(f"托管服务停止异常: {traceback.format_exc()}")

        logger.info("应用程序已停止")
    
    def get_status(self) -> dict:
        """
        获取应用程序状态
        
        Returns:
            dict: 应用程序状态信息
        """
        status = {
            "is_initialized": self.is_initialized,
            "is_running": self.is_running,
            "config_path": self.config_path,
        }
        
        if self.web_server:
            status["web_server"] = "running" if self.web_server else "stopped"
        
        if self.audio_processor:
            status["audio_processor"] = self.audio_processor.get_audio_devices()
        
        if self.conversation_manager:
            status["conversation_manager"] = self.conversation_manager.get_talk_status()
        
        if self.input_handler:
            status["input_handler"] = self.input_handler.get_key_config()
        
        if self.task_scheduler:
            status["task_scheduler"] = self.task_scheduler.get_task_status()
        
        return status


def main():
    """主函数"""
    # 配置文件路径
    config_path = "config.json"
    
    # 检查配置文件是否存在
    if not os.path.exists(config_path):
        logger.error(f"配置文件不存在: {config_path}")
        return
    
    # 创建主应用程序
    app = MainApplication(config_path)
    
    try:
        # 初始化应用程序
        app.initialize()
        
        # 启动应用程序
        app.start()
        
    except Exception as e:
        logger.error(f"应用程序运行失败: {traceback.format_exc()}")
        app.stop()
        sys.exit(1)


if __name__ == "__main__":
    # 打印启动信息
    print("""
    ___ _                       
    |_ _| | ____ _ _ __ ___  ___ 
     | || |/ / _` | '__/ _ \/ __|
     | ||   < (_| | | | (_) \__ \
    |___|_|\_\\__,_|_|  \___/|___/
    
    模块化重构版本
    """)
    
    main()
