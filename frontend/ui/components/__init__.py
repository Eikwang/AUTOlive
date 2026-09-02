"""
UI组件模块
包含可复用的UI组件
"""
from nicegui import ui
from typing import Any, Callable, Dict, List, Optional

# 导入配置辅助工具
from .config_helper import get_nested_value, safe_get_theme_config

# 导入所有组件
from .navigation import NavigationTabs
from .theme import ThemeManager
from .search import SearchFilter
from .control_buttons import ControlButtons
from .login import LoginForm
from .expansion import ExpansionPanel


class FormField:
    """表单字段组件 - 统一输入框宽度规范：sm=150px / md=250px / lg=400px"""

    # 输入框宽度三档（与 design_tokens.INPUT_WIDTH_* 保持一致）
    WIDTH_SM = "150px"
    WIDTH_MD = "250px"
    WIDTH_LG = "400px"

    @staticmethod
    def _extract_width(style: Optional[str], default: str = "250px") -> str:
        """
        从 style 字符串中提取宽度值。

        使用负向后顾断言排除 min-width/max-width；
        仅匹配数字+单位（px/%），不匹配 var() 等函数值。

        Args:
            style: 样式字符串
            default: 未匹配时的默认宽度

        Returns:
            宽度值字符串
        """
        if style and "width" in style:
            import re
            w = re.search(r'(?<![-\w])width:\s*([0-9]+(?:\.[0-9]+)?(?:px\b|%))', style)
            if w:
                return w.group(1)
        return default

    @staticmethod
    def create_input(
        label: str,
        value: str = "",
        placeholder: str = "",
        on_change: Optional[Callable] = None,
        width: str = "250px",
        tooltip: Optional[str] = None,
        style: Optional[str] = None,
        **kwargs
    ):
        """
        创建输入框

        Args:
            label: 标签文本
            value: 初始值
            placeholder: 占位符
            on_change: 值变化事件处理函数
            width: 宽度
            tooltip: 提示信息
        """
        # 从 style 参数中提取宽度
        width = FormField._extract_width(style, width)
        input_field = ui.input(
            label=label,
            value=value,
            placeholder=placeholder,
            on_change=on_change
        ).style(f"width:{width};")

        if tooltip:
            input_field.tooltip(tooltip)

        return input_field

    @staticmethod
    def create_select(
        label: str,
        options: Dict[str, str],
        value: str = "",
        on_change: Optional[Callable] = None,
        width: str = "250px",
        tooltip: Optional[str] = None,
        style: Optional[str] = None,
        **kwargs
    ):
        """
        创建下拉选择框

        Args:
            label: 标签文本
            options: 选项字典
            value: 初始值
            on_change: 值变化事件处理函数
            width: 宽度
            tooltip: 提示信息
        """
        # 从 style 参数中提取宽度
        width = FormField._extract_width(style, width)

        # 处理空 options 或 value 不在 options 中的情况
        if not options:
            options = {}
        if value is not None and str(value) not in options:
            # 将 value 添加到 options 中
            options[str(value)] = str(value)

        select_field = ui.select(
            label=label,
            options=options,
            value=value,
            on_change=on_change
        ).style(f"width:{width};")

        if tooltip:
            select_field.tooltip(tooltip)

        return select_field
    

    @staticmethod
    def create_switch(
        label: str,
        value: bool = False,
        on_change: Optional[Callable] = None,
        **kwargs
    ):
        """
        创建开关

        Args:
            label: 标签文本
            value: 初始值
            on_change: 值变化事件处理函数
        """
        switch_field = ui.switch(
            text=label,
            value=value,
            on_change=on_change
        )
        return switch_field

    @staticmethod
    def create_textarea(
        label: str,
        value: str = "",
        placeholder: str = "",
        on_change: Optional[Callable] = None,
        rows: int = 3,
        width: str = "100%",
        style: Optional[str] = None,
        tooltip: Optional[str] = None,
        **kwargs
    ):
        """
        创建文本区域
        
        Args:
            label: 标签文本
            value: 初始值
            placeholder: 占位符
            on_change: 值变化事件处理函数
            rows: 行数
        """
        return ui.textarea(
            label=label,
            value=value,
            placeholder=placeholder,
            on_change=on_change
        ).props(f'rows={rows}')
    
    @staticmethod
    def create_number(
        label: str,
        value: float = 0,
        min_value: Optional[float] = None,
        max_value: Optional[float] = None,
        step: float = 1,
        on_change: Optional[Callable] = None,
        width: str = "150px"
    ):
        """
        创建数字输入框
        
        Args:
            label: 标签文本
            value: 初始值
            min_value: 最小值
            max_value: 最大值
            step: 步长
            on_change: 值变化事件处理函数
            width: 宽度
        """
        props = {}
        if min_value is not None:
            props['min'] = min_value
        if max_value is not None:
            props['max'] = max_value
        
        number_field = ui.number(
            label=label,
            value=value,
            step=step,
            on_change=on_change,
            format='%d' if step >= 1 else '%.2f'
        ).style(f"width:{width};")
        
        if props:
            number_field.props(' '.join(f'{k}={v}' for k, v in props.items()))
        
        return number_field


class CardContainer:
    """卡片容器组件"""

    @staticmethod
    def create(
        title: str,
        content_func: Callable,
        css: str = ""
    ):
        """
        创建卡片容器

        Args:
            title: 卡片标题
            content_func: 内容创建函数
            css: CSS样式
        """
        from frontend.styles.design_tokens import title_style
        with ui.card().style(css):
            ui.label(title).style(title_style("card"))
            content_func()


class ButtonGroup:
    """按钮组组件"""
    
    @staticmethod
    def create_horizontal(
        buttons: List[Dict[str, Any]],
        css: str = ""
    ):
        """
        创建水平按钮组
        
        Args:
            buttons: 按钮配置列表
            css: CSS样式
        """
        with ui.row().style(css):
            for btn_config in buttons:
                ui.button(
                    btn_config.get('text', ''),
                    on_click=btn_config.get('on_click')
                ).props(btn_config.get('props', ''))


class NotificationHelper:
    """通知助手"""
    
    @staticmethod
    def success(message: str, position: str = "top"):
        """成功通知"""
        ui.notify(position=position, type="positive", message=message)
    
    @staticmethod
    def error(message: str, position: str = "top"):
        """错误通知"""
        ui.notify(position=position, type="negative", message=message)
    
    @staticmethod
    def warning(message: str, position: str = "top"):
        """警告通知"""
        ui.notify(position=position, type="warning", message=message)
    
    @staticmethod
    def info(message: str, position: str = "top"):
        """信息通知"""
        ui.notify(position=position, type="info", message=message)


# 导出所有组件
__all__ = [
    'FormField',
    'CardContainer',
    'ButtonGroup',
    'NotificationHelper',
    'NavigationTabs',
    'ThemeManager',
    'SearchFilter',
    'ControlButtons',
    'LoginForm',
    'ExpansionPanel',
    'get_nested_value',
    'safe_get_theme_config',
]