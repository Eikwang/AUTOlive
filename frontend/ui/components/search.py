"""
搜索过滤组件
提供搜索、过滤和筛选功能
"""
from nicegui import ui
from typing import Dict, Any, Optional, Callable, List
from .config_helper import get_nested_value


class SearchFilter:
    """搜索过滤组件"""

    def __init__(self, config: Dict[str, Any]):
        """
        初始化搜索过滤组件

        Args:
            config: 配置字典
        """
        self.config = config
        self.search_input = None
        self.filter_switches = {}
        self.filter_selects = {}

        # 获取主题配置 - 使用简化的浅色/深色模式
        from frontend.styles.themes import get_theme_manager
        theme_manager = get_theme_manager()
        theme_config = theme_manager.get_theme_config()
        self.switch_internal_css = theme_config.get("switch_internal", "")
    
    def create_search_input(
        self,
        label: str = "搜索",
        placeholder: str = "输入关键词搜索...",
        on_change: Optional[Callable] = None,
        width: str = "300px"
    ) -> ui.input:
        """
        创建搜索输入框
        
        Args:
            label: 标签文本
            placeholder: 占位符
            on_change: 值变化事件处理函数
            width: 宽度
            
        Returns:
            搜索输入框组件
        """
        self.search_input = ui.input(
            label=label,
            placeholder=placeholder,
            on_change=on_change
        ).style(f"width:{width};")
        
        return self.search_input
    
    def create_filter_switch(
        self,
        name: str,
        label: str,
        value: bool = False,
        on_change: Optional[Callable] = None
    ) -> ui.switch:
        """
        创建过滤开关
        
        Args:
            name: 过滤器名称
            label: 标签文本
            value: 初始值
            on_change: 值变化事件处理函数
            
        Returns:
            过滤开关组件
        """
        switch = ui.switch(
            label,
            value=value,
            on_change=on_change
        ).style(self.switch_internal_css)
        
        self.filter_switches[name] = switch
        return switch
    
    def create_filter_select(
        self,
        name: str,
        label: str,
        options: Dict[str, str],
        value: str = "",
        on_change: Optional[Callable] = None,
        width: str = "200px"
    ) -> ui.select:
        """
        创建过滤下拉框
        
        Args:
            name: 过滤器名称
            label: 标签文本
            options: 选项字典
            value: 初始值
            on_change: 值变化事件处理函数
            width: 宽度
            
        Returns:
            过滤下拉框组件
        """
        select = ui.select(
            label=label,
            options=options,
            value=value,
            on_change=on_change
        ).style(f"width:{width};")
        
        self.filter_selects[name] = select
        return select
    
    def get_search_value(self) -> str:
        """
        获取搜索值
        
        Returns:
            搜索关键词
        """
        if self.search_input:
            return self.search_input.value or ""
        return ""
    
    def get_filter_values(self) -> Dict[str, Any]:
        """
        获取所有过滤器的值
        
        Returns:
            过滤器值字典
        """
        values = {}
        
        # 获取开关值
        for name, switch in self.filter_switches.items():
            values[name] = switch.value
        
        # 获取下拉框值
        for name, select in self.filter_selects.items():
            values[name] = select.value
        
        return values
