# -*- coding: UTF-8 -*-
"""
主题管理模块
负责管理UI主题和样式
只支持浅色模式和深色模式

设计令牌统一由 frontend/styles/design_tokens.py 定义并通过 CSS 变量注入。
浅色/深色主题使用同一套样式字符串 —— 颜色等值通过 CSS 变量（--color-*、--bg-*）
引用，切换主题时由 body--dark class 自动切换变量值，无需返回不同字符串。
"""
from typing import Dict, Any, Optional

# 统一主题样式配置（浅色/深色共用，颜色经 CSS 变量自动适配）
# 键名保持向后兼容，38 个 tab 文件通过 theme_config.get("card") 等方式引用
UNIFIED_THEME = {
    "tab_panel": "padding: var(--spacing-md); background: var(--bg-page);",
    "card": (
        "padding: var(--spacing-md); "
        "background: var(--bg-card); "
        "border-radius: var(--radius-lg); "
        "box-shadow: var(--shadow-sm);"
    ),
    "button_bottom": "margin: var(--spacing-sm);",
    "button_bottom_color": "primary",
    "button_internal": "margin: var(--spacing-xs);",
    "button_internal_color": "primary",
    "switch_internal": "margin: var(--spacing-sm);",
    "echart": "height: 300px;",
}


class ThemeManager:
    """主题管理器 - 只支持浅色和深色模式"""

    # 浅色主题配置（引用 CSS 变量，与深色共用同一套样式）
    LIGHT_THEME = dict(UNIFIED_THEME)

    # 深色主题配置（引用 CSS 变量，与浅色共用同一套样式）
    DARK_THEME = dict(UNIFIED_THEME)

    def __init__(self, config: Dict[str, Any]):
        """
        初始化主题管理器

        Args:
            config: 配置字典
        """
        self.config = config
        self._is_dark = False

    def get_current_theme(self) -> str:
        """
        获取当前主题名称

        Returns:
            主题名称 ("light" 或 "dark")
        """
        return "dark" if self._is_dark else "light"

    def get_theme_config(self, theme_name: Optional[str] = None) -> Dict[str, str]:
        """
        获取主题配置

        Args:
            theme_name: 主题名称，如果为None则使用当前主题

        Returns:
            主题配置字典
        """
        if theme_name is None:
            theme_name = self.get_current_theme()

        if theme_name == "dark":
            return self.DARK_THEME
        return self.LIGHT_THEME

    def get_css(self, element: str, theme_name: Optional[str] = None) -> str:
        """
        获取CSS样式

        Args:
            element: 元素名称
            theme_name: 主题名称

        Returns:
            CSS样式字符串
        """
        theme_config = self.get_theme_config(theme_name)
        return theme_config.get(element, "")

    def set_theme(self, theme_name: str) -> bool:
        """
        设置当前主题

        Args:
            theme_name: 主题名称 ("light" 或 "dark")

        Returns:
            是否设置成功
        """
        if theme_name in ("light", "dark"):
            self._is_dark = (theme_name == "dark")
            return True
        return False

    def toggle_theme(self) -> str:
        """
        切换主题

        Returns:
            切换后的主题名称
        """
        self._is_dark = not self._is_dark
        return self.get_current_theme()

    def is_dark(self) -> bool:
        """
        是否为深色模式

        Returns:
            是否为深色模式
        """
        return self._is_dark


# 全局主题管理器实例
_theme_manager: Optional[ThemeManager] = None


def get_theme_manager() -> ThemeManager:
    """获取全局主题管理器实例"""
    global _theme_manager
    if _theme_manager is None:
        raise RuntimeError("主题管理器尚未初始化")
    return _theme_manager


def init_theme_manager(config: Dict[str, Any]) -> ThemeManager:
    """
    初始化全局主题管理器

    Args:
        config: 配置字典

    Returns:
        主题管理器实例
    """
    global _theme_manager
    _theme_manager = ThemeManager(config)
    return _theme_manager


def toggle_theme():
    """切换深色/浅色主题"""
    from nicegui import ui
    dark_mode = ui.dark_mode()
    # NiceGUI 的 dark_mode 是一个布尔值
    dark_mode.value = not dark_mode.value


def init_theme():
    """初始化主题"""
    from nicegui import ui
    dark_mode = ui.dark_mode()
    # 默认使用浅色主题
    dark_mode.value = False
