"""
配置自动保存模块
提供自动保存配置的功能
"""
from typing import Any, Callable, Dict, Optional, Tuple
from nicegui import ui


def auto_bind(component, config: Dict[str, Any], keys: Tuple[str, ...], set_config_callback: Callable = None, tab_key: str = "", split_lines: bool = False):
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
        value = e.value
        # UI-16 二轮验收（任务 10）：文案列表类 textarea 归一化——保存按行 split
        # 成 list 落盘（config 统一 list 形态；存量 str 值由消费点双形态兼容）。
        # 根因：textarea e.value 是字符串，直存 config 后后端 random.choice/
        # for 遍历按字符迭代（每个字符一条「文案」），前端 textarea_data_change
        # 同样按字符换行——用户实测「每个字符都换行」。
        if split_lines and isinstance(value, str):
            value = [line for line in value.split("\n") if line.strip()]
        cfg = config
        for key in keys[:-1]:
            if key not in cfg:
                cfg[key] = {}
            cfg = cfg[key]
        cfg[keys[-1]] = value

        # 调用回调函数保存配置
        if set_config_callback:
            try:
                # 三轮验收修复：落盘传归一化后的 value（原 bug 传 e.value 原始
                # 字符串——内存 dict 归一化为 list 但 config.json 落盘仍是 str，
                # 用户实测「显示正确了但配置文件保存的是文本，系统无法解析」）
                set_config_callback(*keys, value=value)
            except Exception as ex:
                print(f"保存配置失败: {ex}")

    component.on_value_change(on_change)

    # blur 兜底（"填完没自动保存"修复 2026-10-20）：NiceGUI input 的值上报默认
    # 在失焦时触发——填完直接关页/断连（直播场景常见）时 blur 未发、值丢失。
    # 失焦时把组件当前值再存一次（幂等：值未变时 set_config 快速返回）。
    def _on_blur(_e):
        try:
            current = component.value
        except Exception:
            return
        if current is None:
            return
        cfg = config
        try:
            for key in keys[:-1]:
                if key not in cfg:
                    cfg[key] = {}
                cfg = cfg[key]
            if cfg.get(keys[-1]) != current:
                on_change(type("V", (), {"value": current})())
        except Exception:
            pass

    # mock 组件（测试替身）可能无 on 方法——防御性注册
    on_registrar = getattr(component, "on", None)
    if callable(on_registrar):
        on_registrar("blur", _on_blur)
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
    def _auto_save(component, keys, split_lines: bool = False):
        return auto_bind(component, config, keys, set_config_callback, tab_key=tab_key, split_lines=split_lines)
    return _auto_save