"""
控制按钮组件
提供底部控制按钮组（保存、运行、停止等）
"""
from nicegui import ui
from typing import Dict, Any, Optional, Callable, List
from .config_helper import get_nested_value


class ControlButtons:
    """控制按钮组件"""

    def __init__(self, config: Dict[str, Any]):
        """
        初始化控制按钮组件

        Args:
            config: 配置字典
        """
        self.config = config
        self.buttons = {}

        # 获取主题配置 - 使用简化的浅色/深色模式
        from frontend.styles.themes import get_theme_manager
        theme_manager = get_theme_manager()
        theme_config = theme_manager.get_theme_config()
        self.button_bottom_css = theme_config.get("button_bottom", "")
        self.button_bottom_color = theme_config.get("button_bottom_color", "")
    
    def create_button(
        self,
        name: str,
        text: str,
        on_click: Optional[Callable] = None,
        tooltip: Optional[str] = None,
        color: Optional[str] = None,
        css: Optional[str] = None
    ) -> ui.button:
        """
        创建控制按钮
        
        Args:
            name: 按钮名称
            text: 按钮文本
            on_click: 点击事件处理函数
            tooltip: 提示信息
            color: 颜色
            css: CSS样式
            
        Returns:
            按钮组件
        """
        button_color = color or self.button_bottom_color
        button_css = css or self.button_bottom_css
        
        button = ui.button(
            text,
            on_click=on_click,
            color=button_color
        ).style(button_css)
        
        if tooltip:
            button.tooltip(tooltip)
        
        self.buttons[name] = button
        return button
    
    def create_save_button(self, on_click: Optional[Callable] = None) -> ui.button:
        """
        创建保存配置按钮
        
        Args:
            on_click: 点击事件处理函数
            
        Returns:
            保存按钮
        """
        return self.create_button(
            'save',
            '保存配置',
            on_click=on_click,
            tooltip="保存webui的配置到本地文件，有些配置保存后需要重启生效"
        )
    
    def create_run_button(self, on_click: Optional[Callable] = None) -> ui.button:
        """
        创建运行按钮
        
        Args:
            on_click: 点击事件处理函数
            
        Returns:
            运行按钮
        """
        return self.create_button(
            'run',
            '一键运行',
            on_click=on_click,
            tooltip="运行main.py"
        )
    
    def create_stop_button(self, on_click: Optional[Callable] = None) -> ui.button:
        """
        创建停止按钮
        
        Args:
            on_click: 点击事件处理函数
            
        Returns:
            停止按钮
        """
        return self.create_button(
            'stop',
            '停止运行',
            on_click=on_click,
            tooltip="停止运行main.py"
        )
    
    def create_light_button(self, on_click: Optional[Callable] = None) -> ui.button:
        """
        创建关灯按钮
        
        Args:
            on_click: 点击事件处理函数
            
        Returns:
            关灯按钮
        """
        return self.create_button(
            'light',
            '关灯',
            on_click=on_click
        )
    
    def create_restart_button(self, on_click: Optional[Callable] = None) -> ui.button:
        """
        创建重启按钮
        
        Args:
            on_click: 点击事件处理函数
            
        Returns:
            重启按钮
        """
        return self.create_button(
            'restart',
            '重启',
            on_click=on_click,
            tooltip="停止运行main.py并重启webui"
        )
    
    def create_scroll_top_button(self, on_click: Optional[Callable] = None) -> ui.button:
        """
        创建回到顶部按钮
        
        Args:
            on_click: 点击事件处理函数
            
        Returns:
            回到顶部按钮
        """
        return self.create_button(
            'scroll_top',
            '⇧',
            on_click=on_click,
            css=self.button_bottom_css
        )
