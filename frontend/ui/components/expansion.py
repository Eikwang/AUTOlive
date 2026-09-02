"""
折叠面板组件
提供可折叠的内容区域
"""
from nicegui import ui
from typing import Dict, Any, Optional, Callable
from .config_helper import get_nested_value


class ExpansionPanel:
    """折叠面板组件"""

    def __init__(self, config: Dict[str, Any]):
        """
        初始化折叠面板

        Args:
            config: 配置字典
        """
        self.config = config
        self.panels = {}

        # 获取主题配置 - 使用简化的浅色/深色模式
        from frontend.styles.themes import get_theme_manager
        theme_manager = get_theme_manager()
        theme_config = theme_manager.get_theme_config()
        self.card_css = theme_config.get("card", "")
    
    def create(
        self,
        name: str,
        title: str,
        icon: str = "settings",
        value: bool = True,
        content_func: Optional[Callable] = None
    ) -> ui.expansion:
        """
        创建折叠面板
        
        Args:
            name: 面板名称
            title: 面板标题
            icon: 图标
            value: 初始展开状态
            content_func: 内容创建函数
            
        Returns:
            折叠面板组件
        """
        with ui.expansion(title, icon=icon, value=value).classes('w-full') as expansion:
            if content_func:
                content_func()
        
        self.panels[name] = expansion
        return expansion
    
    def create_with_card(
        self,
        name: str,
        title: str,
        icon: str = "settings",
        value: bool = True,
        content_func: Optional[Callable] = None
    ) -> ui.expansion:
        """
        创建带卡片的折叠面板
        
        Args:
            name: 面板名称
            title: 面板标题
            icon: 图标
            value: 初始展开状态
            content_func: 内容创建函数
            
        Returns:
            折叠面板组件
        """
        with ui.expansion(title, icon=icon, value=value).classes('w-full') as expansion:
            with ui.card().style(self.card_css):
                if content_func:
                    content_func()
        
        self.panels[name] = expansion
        return expansion
    
    def get_panel(self, name: str) -> Optional[ui.expansion]:
        """
        获取折叠面板
        
        Args:
            name: 面板名称
            
        Returns:
            折叠面板组件
        """
        return self.panels.get(name)
    
    def toggle_panel(self, name: str) -> bool:
        """
        切换折叠面板状态
        
        Args:
            name: 面板名称
            
        Returns:
            切换后的状态
        """
        panel = self.panels.get(name)
        if panel:
            panel.value = not panel.value
            return panel.value
        return False
    
    def expand_all(self):
        """
        展开所有面板
        """
        for panel in self.panels.values():
            panel.value = True
    
    def collapse_all(self):
        """
        折叠所有面板
        """
        for panel in self.panels.values():
            panel.value = False
