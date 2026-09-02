"""
配置自动保存模块
提供自动保存配置的功能
"""
from typing import Any, Callable, Dict, Optional, Tuple
from nicegui import ui


def auto_bind(component, config: Dict[str, Any], keys: Tuple[str, ...], set_config_callback: Callable = None, tab_key: str = ""):
    """
    为任意UI组件添加自动保存配置的功能

    Args:
        component: NiceGUI UI组件（ui.input, ui.switch, ui.select, ui.textarea 等）
        config: 配置字典
        keys: 配置键路径，例如 ("talk", "type")
        set_config_callback: 配置设置回调函数（可选）
        tab_key: 所属标签页的tab_key，用于全局搜索

    Returns:
        原始组件
    """
    # 自动注册到配置项注册表（用于全局搜索）
    if tab_key:
        try:
            from frontend.utils.config_registry import get_config_registry
            registry = get_config_registry()
            # 从组件中提取 label、tooltip、placeholder
            label = ""
            tooltip = ""
            placeholder = ""
            if hasattr(component, 'label'):
                label = component.label or ""
            if hasattr(component, '_tooltip'):
                tooltip = component._tooltip or ""
            if hasattr(component, 'placeholder'):
                placeholder = component.placeholder or ""
            if label or tooltip or placeholder:
                registry.register(
                    tab_key=tab_key,
                    label=str(label),
                    tooltip=str(tooltip),
                    placeholder=str(placeholder),
                    config_keys=keys
                )
        except Exception:
            pass

    if not hasattr(component, 'on_value_change'):
        return component

    def on_change(e):
        # 更新配置字典
        cfg = config
        for key in keys[:-1]:
            if key not in cfg:
                cfg[key] = {}
            cfg = cfg[key]
        cfg[keys[-1]] = e.value

        # 调用回调函数保存配置
        if set_config_callback:
            try:
                set_config_callback(*keys, value=e.value)
            except Exception as ex:
                print(f"保存配置失败: {ex}")

    component.on_value_change(on_change)
    return component


def create_auto_save(config: Dict[str, Any], set_config_callback: Callable = None, tab_key: str = ""):
    """
    创建一个绑定了配置和tab_key的auto_save函数

    Args:
        config: 配置字典
        set_config_callback: 配置设置回调函数
        tab_key: 所属标签页的tab_key

    Returns:
        auto_save函数，可以用来包装UI组件
    """
    def _auto_save(component, keys):
        return auto_bind(component, config, keys, set_config_callback, tab_key=tab_key)
    return _auto_save