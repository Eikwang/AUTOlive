# -*- coding: UTF-8 -*-
"""
定时任务标签页模块
从 common_config.py 拆分而来

布局规范：
- 设置项用最多 3 列网格排布，组件宽度跟随列宽（width:100%）
- 开关+关联参数 为上下结构单元：开关在上，输入框在下

2026-11-17 前端布局全面调整批次（任务四：定时任务 tab 重组为动态任务组管理）：
- 任务组=config["schedule"] 数组元素（{enable, time_min, time_max, copy[]}），
  每组渲染为独立卡片：启用开关+最小/最大循环周期+文案列表+删除按钮
- 动态数组不走 _auto_save 静态 keys 路径（下标随增删漂移会把修改写进错位组）——
  组内控件直接改写本组 dict（config["schedule"] 内活引用），再整表落盘
  set_config_callback("schedule", value=...)（同 serial.py 动态配置组口径）；
  组内字段手动补注册全局搜索（原 _auto_save 附带能力）
- 「添加新任务组」管理模块卡片固定渲染在所有任务组之后（页面最下方），新组
  默认 {enable:True, time_min:1200, time_max:2400, copy:[""]}，样式与现有组
  完全一致；删除按钮从数组 pop 该组并重建容器，删空后展示占位提示（空数组
  边界：后端注册零任务，不报错）
- D4 schedule_groups 组接管 UI 随本批退役：本页落盘时同步清空遗留接管键
  schedule_groups（后端组非空即接管存量 schedule，不清空则本页配置被旧组
  覆盖失效；旧组数据不再消费）
- 类型写回保证：enable 布尔、time_min/time_max 整数（存量 float 一并归一）、
  copy 字符串数组（textarea 按行 split 滤空行）
"""
from typing import Dict, Any, Callable, List

from nicegui import ui

from frontend.utils.common import textarea_data_change

# 新增任务组默认模板（任务书指定默认值；字段结构与 config["schedule"] 存量项对齐）
_NEW_TASK_TEMPLATE: Dict[str, Any] = {
    "enable": True,
    "time_min": 1200,
    "time_max": 2400,
    "copy": [""],
}


def create_schedule_tab(
    config: Dict[str, Any],
    theme_config: Dict[str, str],
    set_config_callback: Callable
):
    """创建定时任务标签页"""
    card_css = theme_config.get("card", "")
    switch_internal_css = theme_config.get("switch_internal", "")
    button_internal_color = theme_config.get("button_internal_color", "primary")
    button_internal_css = theme_config.get("button_internal_css", "")

    def _get_tasks() -> List[Dict[str, Any]]:
        tasks = config.get("schedule")
        return tasks if isinstance(tasks, list) else []

    def _register_search(label: str, keys: tuple, placeholder: str = "") -> None:
        """组内字段注册全局搜索（原 _auto_save 附带能力，动态组手动补注册）"""
        try:
            from frontend.utils.config_registry import get_config_registry
            get_config_registry().register(
                tab_key="schedule", label=label, placeholder=placeholder,
                config_keys=keys)
        except Exception:
            pass

    def _persist_tasks():
        """任务组整表落盘（各组 dict 即 config["schedule"] 内活引用，改动已就地生效）。

        2026-11-17 批次：D4 遗留接管键 schedule_groups 非空时后端组语义会接管
        存量 schedule（本页改动失效）——落盘前一并清空该键（内存就地改，随本次
        保存落盘），任务组语义统一回归 config["schedule"]。
        """
        if config.get("schedule_groups"):
            config["schedule_groups"] = []
        set_config_callback("schedule", value=_get_tasks())

    def _normalize_field(field: str, value: Any):
        """字段类型归一（2026-11-17 批次：布尔开关/整数周期/字符串数组文案写回）。

        Returns:
            (True, 归一值)；数值非法时 (False, None)——调用方提示并跳过本次落盘
        """
        try:
            if field in ("time_min", "time_max"):
                return True, int(float(value))  # 整数写回（存量 float 一并归一）
            if field == "enable":
                return True, bool(value)
            if field == "copy":
                # textarea 文本 → 字符串数组（按行 split 滤空行，同 _auto_save split_lines 口径）
                return True, [line for line in str(value or "").split("\n") if line.strip()]
        except (TypeError, ValueError):
            return False, None
        return True, value

    def _set_task_field(task: Dict[str, Any], field: str, value, label: str = ""):
        """组内字段写入本组 dict 并整表落盘（活引用写法与组下标解耦——删除他组
        导致的下标漂移不影响本组写入；2026-11-17 批次）"""
        ok, normalized = _normalize_field(field, value)
        if not ok:
            ui.notify(position="top", type="warning",
                      message=f"{label or field} 应为数字，本次修改未保存")
            return
        task[field] = normalized
        _persist_tasks()

    def _bind_blur(comp, task: Dict[str, Any], field: str, label: str = ""):
        """blur 兜底（同 auto_bind 口径）：填完直接关页/断连时 change 未发、值
        丢失——失焦把组件当前值再存一次（归一后值未变则跳过，避免重复落盘）"""
        def _on_blur(_e):
            try:
                current = comp.value
            except Exception:
                return
            if current is None:
                return
            ok, normalized = _normalize_field(field, current)
            if not ok:
                ui.notify(position="top", type="warning",
                          message=f"{label or field} 应为数字，本次修改未保存")
                return
            if task.get(field) != normalized:
                task[field] = normalized
                _persist_tasks()
        reg = getattr(comp, "on", None)
        if callable(reg):
            reg("blur", _on_blur)

    def _delete_task(index: int):
        """删除任务组（从数组 pop + 重建容器；删除最后一组留空数组边界）。"""
        tasks = _get_tasks()
        if not (0 <= index < len(tasks)):
            ui.notify(position="top", type="warning", message=f"定时任务组 #{index + 1} 不存在")
            return
        tasks.pop(index)
        _persist_tasks()
        _render_tasks()
        ui.notify(position="top", type="positive",
                  message=f"已删除定时任务组 #{index + 1}" + ("（当前已无任务组）" if not tasks else ""))

    def _add_task():
        """添加新任务组：追加到现有任务组之后（数组尾部），默认模板与现有组样式一致。"""
        if not isinstance(config.get("schedule"), list):
            config["schedule"] = []
        config["schedule"].append(dict(_NEW_TASK_TEMPLATE))
        _persist_tasks()
        _render_tasks()
        ui.notify(position="top", type="positive",
                  message=f"已新增定时任务组 #{len(config['schedule'])}")

    def _render_tasks():
        """重建任务组容器（新增/删除后整表重渲染，新组样式与现有组完全一致）。"""
        tasks_box.clear()
        tasks = _get_tasks()
        with tasks_box:
            if not tasks:
                # 空数组边界：删除最后一组后展示占位提示（后端注册零任务，不报错）
                ui.label('暂无定时任务组，点击下方「添加新任务组」创建').style('opacity:.7; padding:8px;')
            for index, task in enumerate(tasks):
                if not isinstance(task, dict):
                    continue
                with ui.card().style(card_css):
                    # 组头：组标题 + 删除按钮（2026-11-17 批次新增）
                    with ui.row().style('width:100%; align-items:center; justify-content:space-between;'):
                        ui.label(f'定时任务组 #{index + 1}')
                        ui.button('删除本组', on_click=lambda _e, i=index: _delete_task(i),
                                  color=button_internal_color).style(button_internal_css)
                    with ui.grid(columns=3):
                        # 开关+关联参数 上下结构单元：启用开关在上，时间上下限在下
                        with ui.column().style("width:100%;"):
                            enable_sw = ui.switch(
                                text=f"启用任务#{index + 1}",
                                value=bool(task.get("enable", False))).style(switch_internal_css)
                            enable_sw.on_value_change(
                                lambda e, t=task, i=index:
                                _set_task_field(t, "enable", e.value, label=f"启用任务#{i + 1}"))
                            _register_search(f"启用任务#{index + 1}", ("schedule", str(index), "enable"))
                            tmin_in = ui.input(
                                label=f"最小循环周期（秒）#{index + 1}",
                                value=task.get("time_min", 1200),
                                placeholder='定时任务循环的周期最小时长（秒）').style("width:100%;")
                            tmin_in.on_value_change(
                                lambda e, t=task, i=index:
                                _set_task_field(t, "time_min", e.value, label=f"最小循环周期#{i + 1}"))
                            _bind_blur(tmin_in, task, "time_min", label=f"最小循环周期#{index + 1}")
                            _register_search(f"最小循环周期#{index + 1}", ("schedule", str(index), "time_min"))
                            tmax_in = ui.input(
                                label=f"最大循环周期（秒）#{index + 1}",
                                value=task.get("time_max", 2400),
                                placeholder='定时任务循环的周期最大时长（秒）').style("width:100%;")
                            tmax_in.on_value_change(
                                lambda e, t=task, i=index:
                                _set_task_field(t, "time_max", e.value, label=f"最大循环周期#{i + 1}"))
                            _bind_blur(tmax_in, task, "time_max", label=f"最大循环周期#{index + 1}")
                            _register_search(f"最大循环周期#{index + 1}", ("schedule", str(index), "time_max"))
                        copy_ta = ui.textarea(
                            label=f"文案列表#{index + 1}",
                            value=textarea_data_change(task.get("copy", [])),
                            placeholder='存放文案的列表，通过空格或换行分割').style("width:100%;")
                        copy_ta.on_value_change(
                            lambda e, t=task, i=index:
                            _set_task_field(t, "copy", e.value, label=f"文案列表#{i + 1}"))
                        _bind_blur(copy_ta, task, "copy", label=f"文案列表#{index + 1}")
                        _register_search(f"文案列表#{index + 1}", ("schedule", str(index), "copy"))

    # 页面说明（顶部提示条，主题 CSS 类，不硬编码颜色）
    with ui.element('div').classes('p-2 hint-callout'):
        ui.label('定时任务：每个任务组按组内最小~最大循环周期（秒）随机触发一次组内文案；'
                 '启用开关控制该组是否参与调度。新增任务组追加到现有任务组下方，删除本组即时生效。')

    # 旧版接管键提示（2026-11-17 批次：D4 schedule_groups 组接管 UI 退役）
    if config.get("schedule_groups"):
        with ui.element('div').classes('p-2 hint-callout'):
            ui.label('检测到旧版任务组接管键 schedule_groups 非空（后端优先消费）。'
                     '本页保存任意任务组后该键将自动清空，任务组统一回归本页管理。')

    # 任务组容器（每组独立卡片；先建容器再渲染，「添加新任务组」管理卡片固定其后）
    tasks_box = ui.column().style("width:100%; gap:8px;")
    _render_tasks()

    # 「添加新任务组」管理模块卡片——始终位于最下方（在所有任务组之后渲染，2026-11-17 批次）
    with ui.card().style(card_css):
        ui.label('任务组管理')
        with ui.row():
            ui.button('添加新任务组', on_click=lambda _e: _add_task(),
                      color=button_internal_color).style(button_internal_css)
