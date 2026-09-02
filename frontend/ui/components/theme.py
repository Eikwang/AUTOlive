"""
主题管理组件
提供主题切换、暗黑模式等功能
只支持浅色和深色模式
"""
from nicegui import ui
from typing import Dict, Any, Optional, Callable
from frontend.styles.themes import get_theme_manager


class ThemeManager:
    """主题管理器 - 只支持浅色和深色模式"""

    def __init__(self, config: Dict[str, Any]):
        """
        初始化主题管理器

        Args:
            config: 配置字典
        """
        self.config = config
        self.dark_mode = None

        # 获取主题配置
        theme_manager = get_theme_manager()
        self.theme_config = theme_manager.get_theme_config()

        # CSS样式
        self.tab_panel_css = self.theme_config.get("tab_panel", "")
        self.card_css = self.theme_config.get("card", "")
        self.button_bottom_css = self.theme_config.get("button_bottom", "")
        self.button_bottom_color = self.theme_config.get("button_bottom_color", "")
        self.button_internal_css = self.theme_config.get("button_internal", "")
        self.button_internal_color = self.theme_config.get("button_internal_color", "")
        self.switch_internal_css = self.theme_config.get("switch_internal", "")
        self.echart_css = self.theme_config.get("echart", "")
        self.login_card_css = self.theme_config.get("login_card", "")

    def init_dark_mode(self) -> ui.dark_mode:
        """
        初始化暗黑模式

        Returns:
            暗黑模式对象
        """
        self.dark_mode = ui.dark_mode()
        return self.dark_mode

    def toggle_dark_mode(self) -> bool:
        """
        切换暗黑模式

        Returns:
            切换后的状态
        """
        if self.dark_mode:
            self.dark_mode.toggle()
            # 更新主题配置
            theme_manager = get_theme_manager()
            theme_manager.toggle_theme()
            self.theme_config = theme_manager.get_theme_config()
            self._update_css_styles()
            return self.dark_mode.value
        return False

    def _update_css_styles(self):
        """更新CSS样式"""
        self.tab_panel_css = self.theme_config.get("tab_panel", "")
        self.card_css = self.theme_config.get("card", "")
        self.button_bottom_css = self.theme_config.get("button_bottom", "")
        self.button_bottom_color = self.theme_config.get("button_bottom_color", "")
        self.button_internal_css = self.theme_config.get("button_internal", "")
        self.button_internal_color = self.theme_config.get("button_internal_color", "")
        self.switch_internal_css = self.theme_config.get("switch_internal", "")
        self.echart_css = self.theme_config.get("echart", "")
        self.login_card_css = self.theme_config.get("login_card", "")

    def set_theme(self, theme_name: str) -> bool:
        """
        设置主题

        Args:
            theme_name: 主题名称 ("light" 或 "dark")

        Returns:
            是否设置成功
        """
        theme_manager = get_theme_manager()
        if theme_manager.set_theme(theme_name):
            self.theme_config = theme_manager.get_theme_config()
            self._update_css_styles()
            return True
        return False

    def get_css(self, element: str) -> str:
        """
        获取元素的CSS样式

        Args:
            element: 元素名称

        Returns:
            CSS样式字符串
        """
        return self.theme_config.get(element, "")
