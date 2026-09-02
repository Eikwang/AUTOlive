"""
导航组件
提供标签页导航和页面切换功能
"""
from nicegui import ui
from typing import Dict, Any, Optional, Callable
from .config_helper import get_nested_value


class NavigationTabs:
    """导航标签页组件"""

    def __init__(self, config: Dict[str, Any]):
        """
        初始化导航标签页

        Args:
            config: 配置字典
        """
        self.config = config
        self.tabs = {}
        self.tab_panels = {}
        self.current_tab = None

        # 获取主题配置 - 使用简化的浅色/深色模式
        from frontend.styles.themes import get_theme_manager
        theme_manager = get_theme_manager()
        self.theme_config = theme_manager.get_theme_config()
        self.tab_panel_css = self.theme_config.get("tab_panel", "")
    
    def create_tabs(self):
        """
        创建标签页导航
        
        Returns:
            ui.tabs 对象
        """
        tabs = ui.tabs().classes('w-full')
        with tabs:
            self.tabs = {
                'common_config': ui.tab('通用配置'),
                'llm': ui.tab('大语言模型'),
                'tts': ui.tab('文本转语音'),
                'svc': ui.tab('变声'),
                'visual_body': ui.tab('虚拟身体'),
                'copywriting': ui.tab('文案'),
                'talk': ui.tab('聊天'),
                'image_recognition': ui.tab('图像识别'),
                'integral': ui.tab('积分'),
                'assistant_anchor': ui.tab('助播'),
                'translate': ui.tab('翻译'),
                'serial': ui.tab('串口'),
                'data_analysis': ui.tab('数据分析'),
                'web': ui.tab('页面配置'),
            }
        return tabs
    
    def create_tab_panels(self) -> Dict[str, ui.column]:
        """
        创建标签页面板
        
        Returns:
            标签页面板字典
        """
        with ui.tab_panels(self.tabs, value=self.tabs['common_config']).classes('w-full'):
            for key, tab in self.tabs.items():
                with ui.tab_panel(tab).style(self.tab_panel_css):
                    self.tab_panels[key] = ui.column()
        
        return self.tab_panels
    
    def get_tab(self, name: str) -> Optional[ui.tab]:
        """
        获取标签页
        
        Args:
            name: 标签页名称
            
        Returns:
            标签页对象
        """
        return self.tabs.get(name)
    
    def get_tab_panel(self, name: str) -> Optional[ui.column]:
        """
        获取标签页面板
        
        Args:
            name: 标签页名称
            
        Returns:
            标签页面板对象
        """
        return self.tab_panels.get(name)