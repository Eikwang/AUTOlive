"""
配置自动保存模块
提供自动保存配置的功能
"""
from typing import Any, Callable, Dict, Optional, Tuple
from nicegui import ui


def descend_container(node, keys):
    """按 keys 逐层下钻，返回末级容器。

    支持 dict 字符串键与 list 数字下标键（"0"/"1"/...，重构 2026-10-24：
    key_mapping/serial 配置组为 list 结构，字段级自动保存需按下标写入）。
    dict 路径缺失时自动补 {}（与原 auto_bind 行为一致）。
    """
    for key in keys:
        if isinstance(node, list):
            node = node[int(key)]
        else:
            if key not in node:
                node[key] = {}
            node = node[key]
    return node


def _container_write(container, key, value):
    """向末级容器写入键值（list 走下标，dict 走键）。"""
    if isinstance(container, list):
        container[int(key)] = value
    else:
        container[key] = value


def _container_get(container, key):
    """从末级容器读取键值（list 走下标越界返回 None，dict 走 get）。"""
    if isinstance(container, list):
        idx = int(key)
        return container[idx] if 0 <= idx < len(container) else None
    return container.get(key)


def _coerce_like(old_value, new_value):
    """数字保型归一化（2026-10-24 实测修复）：ui.input 的 e.value 恒为字符串，
    直存 config 会把数字键污染成 str（实测 play_audio.play_interval_max 落盘
    '0.8'，后端读侧 float/str 比较抛 TypeError，出队待播音频被异常吞掉丢弃
    ——合成成功但数字人无推送、本地无声）。旧值为 int/float（bool 除外）时
    把新值转回同型；转换失败保留原值（消费侧归一化兜底）。旧值缺失或为
    str/list 等非数字形态时不猜类型，原样落盘。
    """
    if (
        isinstance(new_value, str)
        and isinstance(old_value, (int, float))
        and not isinstance(old_value, bool)
    ):
        try:
            return type(old_value)(new_value)
        except (TypeError, ValueError):
            return new_value
    return new_value


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
        # 数字保型归一化（2026-10-24 实测修复，见 _coerce_like）：以旧值类型为
        # schema 代理，把文本框字符串新值转回数字同型；旧值缺失/路径异常时
        # 原样落盘（消费侧归一化兜底），不阻塞保存。
        try:
            _node = descend_container(config, keys[:-1])
            value = _coerce_like(_container_get(_node, keys[-1]), value)
            # 审查修复（发布批次）：write 复用同一次 descend 且并入同一 try——
            # 原实现 try 外重复 descend_container，组删除后滞留的 change 事件
            # 会触发未捕获越界异常，或把编辑写入错位的组并落盘
            _container_write(_node, keys[-1], value)
        except (KeyError, TypeError, AttributeError, IndexError, ValueError):
            # 路径失效（组已删除等）：跳过本次本地写；下方 set_config_callback
            # 自行校验同一路径，失效时其内部异常已被捕获打印，不会落脏数据
            pass

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
        try:
            cfg = descend_container(config, keys[:-1])
            if _container_get(cfg, keys[-1]) != current:
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