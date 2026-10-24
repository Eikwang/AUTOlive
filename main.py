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
            
            # 初始化通用工具（Common 为无参 mixin 工具类——config 由 My_handle 等调用方自持；2026-10-12 修复签名漂移）
            self.common = Common()
            logger.info("通用工具初始化完成")
            
            # 初始化处理器（My_handle.__init__(config_path) 单参——内部自建 Config/Common 单例；2026-10-12 修复签名漂移）
            self.my_handle = My_handle(self.config_path)
            # uc1 根因修复（/autoplan 批准批次切片 2）：danmaku_consumer._dispatch 经
            # utils.my_global.my_handle 取处理器引用——该模块属性从未被赋值，导致
            # 每条弹幕/礼物/入场消息"my_handle 未就绪，信息丢弃"（弹幕不进对话链）。
            from utils import my_global
            my_global.my_handle = self.my_handle
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
        # 获取平台标识（整合计划 P2-4：活跃房间平台优先，平滑替代旧架构单平台选择；
        # TaskScheduler 以此作为定时任务/闲时任务消息的 platform 语境）
        try:
            _dl_rooms = (self.config.get("danmaku_listener") or {}).get("rooms", []) or []
        except Exception:
            _dl_rooms = []
        platform = next((str(r.get("platform")) for r in _dl_rooms
                         if isinstance(r, dict) and r.get("active")), "")
        if not platform:
            platform = "talk"  # 旧 config.platform 键已退役（P3-3）——定时任务语境缺省聊天模式
        
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

        # 深化整合 R-5：GPT-SoVITS 推理进程内化（终门 uc1a）
        # backend=inproc（默认）→ 引擎在 main.py 核心进程内初始化（严禁前端进程调用，显存双份+事件循环阻塞）
        # backend=api → 用户自备外部 api_v2（F11：不拉起不托管，UI 文案明示）；api_v2 托管退役
        # RVC/DanmakuListener 仍走 ManagedProcess 托管（不受本次退役影响）
        try:
            from utils.autolive_paths import AutolivePaths
            self.autolive_paths = AutolivePaths(self.config)
            # R-6：直播运行信号接线（运行面近似=弹幕监听托管健康态；远优于既有恒 False）
            from utils import gpu_policy
            gpu_policy.set_live_flag_source(
                lambda: getattr(self.danmaku_svc, "state", "") == "healthy"
                if getattr(self, "danmaku_svc", None) else False)
            backend = (self.config.get("gpt_sovits", "backend") or "inproc")
            if backend == "inproc":
                # TextPreprocessor 全局副作用落点（Eng P2-3）：cwd=AUTOlive 根
                os.makedirs("音频输出", exist_ok=True)
                from utils.gpt_sovits_inproc import init_engine
                self.gsv_engine = init_engine(self.config, self.autolive_paths)
                self.gsv_engine.start()  # 异步加载（胶囊 loading 态，不阻塞启动）
                # R-5：引擎鸭子接入胶囊区（ManagedProcess 接口，audio_player builtin 先例同款）
                from utils.service_orchestrator import ServiceRegistry
                ServiceRegistry.instance().register(self.gsv_engine)
                logger.info("GPT-SoVITS 进程内引擎初始化已启动（异步加载，backend=inproc）")
                threading.Thread(target=self.gsv_engine.warmup, name="gsv-warmup", daemon=True).start()
            else:
                logger.info("gpt_sovits.backend=api：使用用户自备外部 api_v2（"
                            f"{self.config.get('gpt_sovits', 'api_ip_port')}），AUTOlive 不拉起不托管")
            # RVC 变声服务（P2-1）：控制面常驻，音频流按需经前端启停
            from utils.rvc_service import build_rvc_vc_service
            rvc_svc = build_rvc_vc_service(self.config, self.autolive_paths)
            if rvc_svc is not None:
                threading.Thread(target=rvc_svc.start, name="svc-start-rvc-vc", daemon=True).start()
                logger.info("RVC 变声服务拉起已后台启动")
            # DanmakuListener 弹幕监听（整合计划第二阶段·进程内化）：enabled=true 时进程内启动
            from utils.danmaku_consumer import build_danmaku_listener_service
            self.danmaku_svc = build_danmaku_listener_service(self.config)
            if self.danmaku_svc is not None:
                threading.Thread(target=self.danmaku_svc.start, name="svc-start-danmaku", daemon=True).start()
                logger.info("DanmakuListener 托管服务拉起已后台启动")
        except Exception:
            logger.error(f"托管服务初始化失败（不阻断主程序）: {traceback.format_exc()}")
    
    def send_chat_message(self, msg_type: str, content: str) -> bool:
        """聊天页按钮统一发送（T10 同期：复读生产端接线——前端按钮此前无 on_click）。

        POST core /send（webui /send 同构 payload）——reread/reread_top_priority
        走 reread_handle（999 链路），comment 走弹幕链（chat_source=chatbox）。
        Returns: 是否成功（失败 notify 由调用方承接）。
        """
        import json as _json
        import urllib.request as _urllib
        cfg = self.config._config
        main_api_ip = "127.0.0.1" if cfg.get("api_ip") == "0.0.0.0" else cfg.get("api_ip", "127.0.0.1")
        main_api_port = cfg.get("api_port", 8000)
        url = f'http://{main_api_ip}:{main_api_port}/send'
        payload = {"type": msg_type, "data": {
            "username": cfg.get("talk", {}).get("username", "主人"),
            "content": content, "chat_source": "chatbox"}}
        try:
            req = _urllib.Request(url, data=_json.dumps(payload).encode('utf-8'),
                                  headers={'Content-Type': 'application/json'})
            with _urllib.urlopen(req, timeout=10) as resp:
                _ = _json.loads(resp.read().decode('utf-8'))
            return True
        except Exception as e:
            logger.error(f"[t10] 发送失败（{msg_type}）：{e}")
            return False

    def _on_start_recording(self):
        """开始录音的回调函数"""
        logger.info("开始录音回调触发")
        if self.audio_processor:
            self.audio_processor.start_recording_thread()
    
    def _on_stop_recording(self):
        """停止录音的回调函数——T10 语音对话链接通（2026-10-24）：停止后串接
        ASR→对话链（录音 wav → 文字 → conversation_manager.talk_handle →
        LLM/TTS/聊天记录 voice 标记全链复用）。"""
        logger.info("停止录音回调触发")
        if self.audio_processor:
            self.audio_processor.stop_recording()
        wav_path = getattr(self.audio_processor, "last_recording_path", "")
        if self.conversation_manager and wav_path:
            from utils.asr_engine import transcribe
            text = transcribe(wav_path, self.config)
            if text:
                logger.info(f"[t10] 语音识别结果：{text}")
                self.conversation_manager.talk_handle(text)
            else:
                logger.info("[t10] 语音识别无文字输出，本次忽略")
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
            api_port = self.config.get("api_port") or 8000
            web_port = self.config.get("web_port") or 8080
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

        # DanmakuListener 进程内托管关停（整合计划 P1-1：stop_all future 等待→停循环→join）
        danmaku_svc = getattr(self, "danmaku_svc", None)
        if danmaku_svc is not None:
            try:
                danmaku_svc.stop()
            except Exception:
                logger.error("DanmakuListener 关停异常", exc_info=True)

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
    欢迎使用 AUTO-live 直播助手
    系统正在启动中...
    """)
    
    main()
