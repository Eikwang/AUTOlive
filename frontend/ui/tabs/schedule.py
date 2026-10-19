# -*- coding: UTF-8 -*-
"""
定时任务标签页模块
从 common_config.py 拆分而来

布局规范：
- 设置项用最多 3 列网格排布，组件宽度跟随列宽（width:100%）
- 开关+关联参数 为上下结构单元：开关在上，输入框在下

D4 任务组管理（2026-10-19 切片 3）：
- 新增任务组 UI（组列表+添加/删除+组内编辑：组名/最小最大循环周期/文案列表）
- 键结构注记：任务书建议 config["schedule"]["groups"]，但 config.schedule 存量为
  list（utils/task_scheduler.py:82 直接按 list 消费 task["enable"]/["time_min"]/
  ["time_max"]/["copy"]），list 不可挂子键——故落**顶层新键 config["schedule_groups"]**，
  结构 [{name, min_interval, max_interval, copywriting_list}]；存量 schedule list
  原样保留兼容，utils 消费方接线归收尾批
- 存量单任务迁移：映射为"默认组"（migrate_tasks_to_groups 纯函数，UI 一键生成）
"""
import re

from nicegui import ui
from typing import Dict, Any, Callable, List
from frontend.ui.components.config_helper import get_nested_value
from frontend.utils.common import textarea_data_change

# 组内字段与存量单任务字段的映射（存量 time_min/time_max/copy → 组 min_interval/max_interval/copywriting_list）
_TASK_GROUP_FIELDS = ("name", "min_interval", "max_interval", "copywriting_list")


def validate_schedule_groups(groups: Any) -> List[str]:
    """校验任务组结构（纯函数，供测试与保存前校验共用）。

    Returns:
        错误描述列表；空列表 = 结构合法
    """
    errs: List[str] = []
    if not isinstance(groups, list):
        return ["schedule_groups 应为列表"]
    for i, g in enumerate(groups):
        if not isinstance(g, dict):
            errs.append(f"组#{i} 应为对象")
            continue
        name = str(g.get("name") or "").strip()
        if not name:
            errs.append(f"组#{i} 缺少组名")
        try:
            mn = float(g.get("min_interval", 0))
            mx = float(g.get("max_interval", 0))
        except (TypeError, ValueError):
            errs.append(f"组#{i}({name}) 循环周期应为数字")
        else:
            if mn < 0 or mx < 0:
                errs.append(f"组#{i}({name}) 循环周期不能为负")
            elif mx < mn:
                errs.append(f"组#{i}({name}) 最大循环周期应不小于最小值")
        copy_list = g.get("copywriting_list")
        if not isinstance(copy_list, list) or not any(str(c).strip() for c in copy_list):
            errs.append(f"组#{i}({name}) 文案列表为空")
    return errs


def migrate_tasks_to_groups(tasks: Any) -> List[Dict[str, Any]]:
    """存量单任务列表 → 任务组结构（纯函数）。

    映射规则：每条任务=一个组（时间字段名迁移 time_min/time_max → min_interval/
    max_interval，copy → copywriting_list）；首组命名「默认组」，其余「默认组#2」起。
    """
    groups: List[Dict[str, Any]] = []
    for i, task in enumerate(tasks or []):
        if not isinstance(task, dict):
            continue
        groups.append({
            "name": "默认组" if i == 0 else f"默认组#{i + 1}",
            "min_interval": task.get("time_min", 60),
            "max_interval": task.get("time_max", 120),
            "copywriting_list": [str(c) for c in (task.get("copy") or []) if str(c).strip()],
        })
    return groups


def _copy_text_to_list(text: str) -> List[str]:
    """文案 textarea 文本 → 列表（换行/空格分隔；与存量 placeholder 语义一致）"""
    return [c for c in re.split(r"[\s]+", (text or "").strip()) if c]


def create_schedule_tab(
    config: Dict[str, Any],
    theme_config: Dict[str, str],
    set_config_callback: Callable
):
    """创建定时任务标签页"""
    card_css = theme_config.get("card", "")
    switch_internal_css = theme_config.get("switch_internal", "")

    # 导入自动绑定函数
    from frontend.utils.config_auto_save import create_auto_save
    _auto_save = create_auto_save(config, set_config_callback, tab_key="schedule")

    # ---------- 存量单任务（原样保留展示；迁移说明见下方任务组卡） ----------
    with ui.card().style(card_css):
        ui.label('定时任务')
        schedule_var = {}
        schedule_config_card = ui.card()
        for index, schedule in enumerate(config.get("schedule", [])):
            with schedule_config_card.style(card_css):
                with ui.grid(columns=3):
                    with ui.column().style("width:100%;"):
                        schedule_var[str(4 * index)] = ui.switch(text=f"启用任务#{index}", value=schedule.get("enable", False)).style(switch_internal_css)
                        schedule_var[str(4 * index + 1)] = ui.input(label=f"最小循环周期#{index}", value=schedule.get("time_min", 60), placeholder='定时任务循环的周期最小时长（秒）').style("width:100%;")
                        schedule_var[str(4 * index + 2)] = ui.input(label=f"最大循环周期#{index}", value=schedule.get("time_max", 120), placeholder='定时任务循环的周期最大时长（秒）').style("width:100%;")
                    schedule_var[str(4 * index + 3)] = ui.textarea(label=f"文案列表#{index}", value=textarea_data_change(schedule.get("copy", [])), placeholder='存放文案的列表，通过空格或换行分割').style("width:100%;")

    # ---------- 任务组管理（D4：schedule_groups 新键） ----------
    with ui.card().style(card_css):
        ui.label('任务组管理')
        ui.label("任务组=组名+最小/最大循环周期（秒）+文案列表；组内按周期随机触发文案").style(
            "font-size: var(--font-size-assist, 12px); color: var(--text-hint, #9ca3af);")

        group_rows = []  # [(name_in, min_in, max_in, copy_ta, wrap_element)]

        def _sync_groups():
            cfg = config if isinstance(config, dict) else None
            if cfg is None:
                return
            groups = [{
                "name": (name_in.value or "").strip(),
                "min_interval": min_in.value,
                "max_interval": max_in.value,
                "copywriting_list": _copy_text_to_list(copy_ta.value),
            } for name_in, min_in, max_in, copy_ta, _w in group_rows]
            cfg["schedule_groups"] = groups
            set_config_callback("schedule_groups", value=groups)

        def _add_group_row(name="", min_iv="", max_iv="", copy_list=None):
            wrap = ui.column().style("width:100%;border:1px solid var(--border-color, #e5e7eb);border-radius:8px;padding:8px;gap:4px")
            with wrap:
                with ui.row().style("width:100%;align-items:center;gap:8px"):
                    name_in = ui.input(label='组名', value=name, placeholder='例如：默认组').style("width:100%;")
                    del_btn = ui.button(icon='delete', on_click=lambda: _request_remove_group(wrap)).props('flat dense round')
                with ui.grid(columns=2).style("width:100%;"):
                    min_in = ui.input(label='最小循环周期（秒）', value=min_iv, placeholder='例如：60').style("width:100%;")
                    max_in = ui.input(label='最大循环周期（秒）', value=max_iv, placeholder='例如：120').style("width:100%;")
                copy_ta = ui.textarea(label='文案列表', value=textarea_data_change(list(copy_list or [])),
                                      placeholder='存放文案的列表，通过空格或换行分割').style("width:100%;")
            row = (name_in, min_in, max_in, copy_ta, wrap)
            group_rows.append(row)
            for comp in (name_in, min_in, max_in, copy_ta):
                comp.on_value_change(lambda e: _sync_groups())
            return row

        def _rebuild_rows(groups):
            for _n, _mi, _ma, _c, w in list(group_rows):
                w.delete()
            group_rows.clear()
            with groups_container:
                for g in groups:
                    _add_group_row(g.get("name", ""), g.get("min_interval", ""), g.get("max_interval", ""),
                                   g.get("copywriting_list") or [])

        def _migrate_existing():
            # 存量单任务 → 任务组（首组命名「默认组」）；写入即保存
            groups = migrate_tasks_to_groups(config.get("schedule", []))
            if isinstance(config, dict):
                config["schedule_groups"] = groups
            set_config_callback("schedule_groups", value=groups)
            _rebuild_rows(groups)
            ui.notify(position="top", type="positive",
                      message=f"已从存量任务生成 {len(groups)} 个任务组（首组为「默认组」）；utils 消费方接线归收尾批")

        # 删除组前确认（D4）
        pending_wrap = {"el": None}
        with ui.dialog() as group_confirm, ui.card().style("min-width: 320px;"):
            ui.label("确认删除任务组").style(
                "font-size: var(--font-size-card-title, 16px); font-weight: var(--font-weight-title, 600); color: var(--text-title); margin-bottom: 4px;")
            ui.label("删除后该组的周期与文案配置一并移除").style("color: var(--text-body);")
            with ui.row().style("justify-content: flex-end; width: 100%; gap: 8px; margin-top: 8px;"):
                ui.button("取消", on_click=lambda: group_confirm.close()).props("flat no-caps")
                ui.button("确认删除", on_click=lambda: _do_remove_group()).props(
                    "unelevated no-caps").style(
                    "background: var(--color-danger); color: var(--text-on-primary);")

        def _request_remove_group(wrap):
            pending_wrap["el"] = wrap
            group_confirm.open()

        def _do_remove_group():
            wrap = pending_wrap.get("el")
            group_confirm.close()
            if wrap is None:
                return
            group_rows[:] = [r for r in group_rows if r[4] is not wrap]
            wrap.delete()
            _sync_groups()

        groups_container = ui.column().style("width:100%;gap:8px")
        existing_groups = list(get_nested_value(config, "schedule_groups", default=[]) or [])
        if not existing_groups:
            # 存量兼容：无 groups 键时显示迁移提示（存量任务继续生效，不受影响）
            with groups_container:
                with ui.element('div').classes('p-2 bg-blue-100'):
                    ui.label(f"尚未配置任务组。检测到存量单任务 {len(config.get('schedule', []) or [])} 条——"
                             "点击下方按钮将映射为任务组（首组命名「默认组」，存量任务继续生效不受影响）；"
                             "也可直接「添加任务组」从零开始")
            with ui.row():
                ui.button('从存量任务生成任务组', on_click=lambda: _migrate_existing()).props('outline dense')
                ui.button('添加任务组', on_click=lambda: _add_group_row()).props('outline dense')
        else:
            with groups_container:
                for g in existing_groups:
                    _add_group_row(g.get("name", ""), g.get("min_interval", ""), g.get("max_interval", ""),
                                   g.get("copywriting_list") or [])
            with ui.row():
                ui.button('添加任务组', on_click=lambda: _add_group_row()).props('outline dense')

        if existing_groups and validate_schedule_groups(existing_groups):
            ui.label("⚠ 现有任务组结构存在告警（保存时会按界面值重建）："
                     + "；".join(validate_schedule_groups(existing_groups))).style(
                "font-size: var(--font-size-assist, 12px); color: var(--color-warning, #d97706);")
