# -*- coding: UTF-8 -*-
"""
动态文案标签页模块
从 common_config.py 拆分而来

布局规范：
- 设置项用最多 3 列网格排布，组件宽度跟随列宽（width:100%）
- 开关+关联参数 为上下结构单元：开关在上，输入框在下

UI-16 批次 R5（任务 13 唯一逻辑变更任务）：
- LLM 选项收敛 {custom_llm, fastrag（内置）}；存量 chatgpt 等历史值渲染为置灰
  禁用项（option key 保持存储值，显示「（已停用）」——EV-1j 防保存回写显示文案）
- 文案来源从 folder_path 文件夹改为前端 textarea 直输（回车分段、即时保存，
  复用闲时任务 idle_time_task 模式）；后端消费链见 task_scheduler.run_trends_copywriting
- 提示词转换上移为页级参数（作用于全部文案条目；关闭态=置灰禁用保留展示）
- 存量 folder_path 迁移：进页自动重试（幂等）+部分成功态+非破坏退役（DV-1b⑥/
  CEO F-3：目录不可读保留原键 notify 指引，仅回填成功才退役）；体量上限
  每条目 200 文件/单文件 100KB 截断（EV-1a），文件 IO 走 run.io_bound 卸载
"""
from nicegui import ui
from typing import Dict, Any, Callable

from frontend.ui.components.config_helper import get_nested_value
from frontend.utils.common import textarea_data_change

# EV-1a：迁移体量上限（每条目文件数/单文件字节，超限截断并 notify 说明）
_MIGRATE_MAX_FILES_PER_FOLDER = 200
_MIGRATE_MAX_FILE_BYTES = 100 * 1024


def _migrate_folders(folders):
    """读取存量文案目录内容（模块级：供进页迁移与守卫测试共用；EV-1a 上限）。

    Returns:
        (imported, succeeded, failed) —— imported=文案文本列表；
        succeeded=完成目录列表；failed=[(路径, 原因)] 列表。
        目录不可读→该目录记入 failed（调用方保留其 copywriting 条目，
        下次进页重试=非破坏语义 DV-1b⑥）。
    """
    imported, succeeded, failed = [], [], []
    for _folder in folders:
        try:
            from utils.common import Common
            _files = sorted(Common().get_all_file_paths(_folder))
        except Exception as exc:
            failed.append((_folder, f'目录不可读：{exc}'))
            continue
        for _fp in _files[:_MIGRATE_MAX_FILES_PER_FOLDER]:
            try:
                with open(_fp, 'r', encoding='utf-8', errors='replace') as _f:
                    _text = _f.read(_MIGRATE_MAX_FILE_BYTES)
            except Exception:
                failed.append((_fp, '文件不可读'))
                continue
            _text = _text.replace('\r', '').strip()
            if _text:
                imported.append(_text)
        if len(_files) > _MIGRATE_MAX_FILES_PER_FOLDER:
            ui.notify(f'迁移截断：{_folder} 文件数超过 {_MIGRATE_MAX_FILES_PER_FOLDER}，'
                      f'仅导入前 {_MIGRATE_MAX_FILES_PER_FOLDER} 个', type='warning')
        succeeded.append(_folder)
    return imported, succeeded, failed


def create_trends_copywriting_tab(
    config: Dict[str, Any],
    theme_config: Dict[str, str],
    set_config_callback: Callable
):
    """创建动态文案标签页"""
    card_css = theme_config.get("card", "")
    switch_internal_css = theme_config.get("switch_internal", "")

    # 导入自动绑定函数
    from frontend.utils.config_auto_save import create_auto_save
    _auto_save = create_auto_save(config, set_config_callback, tab_key="trends_copywriting")

    with ui.card().style(card_css):
        ui.label('动态文案')
        with ui.grid(columns=3):
            # R5①/DV-1b⑦/EV-1j：选项收敛 + 存量历史值置灰禁用（key=存储值防回写）
            _llm_type_val = get_nested_value(config, "trends_copywriting", "llm_type") or "custom_llm"
            _llm_type_options = {'custom_llm': '自定义LLM', 'fastrag': 'fastrag（内置）'}
            _llm_stale = bool(_llm_type_val) and _llm_type_val not in _llm_type_options
            if _llm_stale:
                _llm_type_options[_llm_type_val] = f'{_llm_type_val}（已停用）'
            _llm_select = _auto_save(ui.select(label='LLM类型', options=_llm_type_options, value=_llm_type_val).style("width:100%;"), ("trends_copywriting", "llm_type"))
            if _llm_stale:
                ui.notify(f'LLM类型存量值「{_llm_type_val}」已停用——请改选「自定义LLM」或「fastrag（内置）」后保存',
                          type='warning', close_button='知道了')
            with ui.column().style("width:100%;"):
                _auto_save(ui.switch('随机播放', value=get_nested_value(config, "trends_copywriting", "random_play")).style(switch_internal_css), ("trends_copywriting", "random_play"))
                _auto_save(ui.input(label='文案播放间隔', value=get_nested_value(config, "trends_copywriting", "play_interval"), placeholder='文案之间的播放间隔时间（秒）').style("width:100%;"), ("trends_copywriting", "play_interval"))

        # R5③/DV-1g：提示词转换页级化（开关+内容框，作用于全部文案条目；
        # 关闭态=置灰禁用保留展示，与站内开关行为对齐）
        with ui.row().style("align-items:center; gap:12px; flex-wrap:wrap;"):
            _pc_switch = _auto_save(ui.switch('提示词转换', value=bool(get_nested_value(config, "trends_copywriting", "prompt_change_enable"))).style(switch_internal_css).tooltip(
                '开启后全部文案先经 LLM 按此提示词改写再合成播放'), ("trends_copywriting", "prompt_change_enable"))
            _pc_input = _auto_save(ui.input(
                label='提示词转换内容',
                value=get_nested_value(config, "trends_copywriting", "prompt_change_content"),
                placeholder='使用此提示词内容对文案内容进行转换后再进行合成'
            ).style("width:100%; flex:1 1 320px;"), ("trends_copywriting", "prompt_change_content"))

            def _sync_pc_state(show_value: bool):
                _pc_input.props(f'disable={not show_value}')

            _pc_switch.on_value_change(lambda e: _sync_pc_state(bool(e.value)))
            _sync_pc_state(bool(_pc_switch.value))

        # R5②：文案直输 textarea（回车分段、即时保存；复用闲时任务模式，
        # placeholder=空态占位）
        _auto_save(ui.textarea(
            label='文案列表',
            value=textarea_data_change(get_nested_value(config, "trends_copywriting", "copy")),
            placeholder='每行一条文案，回车分段',
        ).style("width:100%; min-height:16rem;").tooltip('每行一条文案，回车分段；保存后即时生效（随机播放受上方开关控制）'),
            ("trends_copywriting", "copy"))

        # 存量迁移状态行（DV-1b⑥：迁移结果/失败指引的持久可见面）
        _migrate_status = ui.label('').style(
            'font-size: var(--font-size-assist, 12px); color: var(--text-secondary); width:100%;')

    # ---------- 存量 folder_path 迁移（进页自动重试，幂等；DV-1b⑥/EV-1a） ----------
    async def _migrate_folder_paths():
        _entries = list(get_nested_value(config, "trends_copywriting", "copywriting") or [])
        _folders = [str((e or {}).get("folder_path", "")).strip() for e in _entries]
        _folders = [f for f in _folders if f]
        if not _folders:
            return  # 无 folder_path 键=迁移已完成或无存量

        try:
            from nicegui import run
            imported, succeeded, failed = await run.io_bound(_migrate_folders, _folders)
        except Exception as exc:
            _migrate_status.set_text(f'存量文案迁移执行失败：{exc}')
            return

        if not imported and not succeeded and not failed:
            return

        # 合并去重保序（幂等：重复进页不重复追加）
        existing = [str(c).strip() for c in (get_nested_value(config, "trends_copywriting", "copy") or []) if str(c).strip()]
        merged = list(dict.fromkeys(existing + imported))

        # 剩余未完成条目=失败目录（非破坏保留，DV-1b⑥；成功条目退役）
        failed_paths = {f for f, _ in failed}
        remaining = [e for e in _entries
                     if str((e or {}).get("folder_path", "")).strip() in failed_paths]

        set_config_callback("trends_copywriting", "copy", value=merged)
        set_config_callback("trends_copywriting", "copywriting", value=remaining)

        parts = []
        if imported:
            parts.append(f'已导入 {len(imported)} 条文案')
        if succeeded:
            parts.append(f'完成目录 {len(succeeded)} 个')
        if failed:
            fail_paths = '；'.join(p for p, _ in failed[:3])
            more = f' 等 {len(failed)} 项' if len(failed) > 3 else ''
            parts.append(f'失败 {len(failed)} 项（{fail_paths}{more}）——'
                         '恢复该目录可读后重新进入本页即自动回填')
            _migrate_status.style('font-size: var(--font-size-assist, 12px); color: var(--color-warning); width:100%;')
            ui.notify('存量文案迁移部分失败：失败目录已保留，修复可读后重进本页自动重试',
                      type='warning', close_button='知道了')
        else:
            ui.notify(f'存量文案迁移完成（{"；".join(parts) or "无存量"}）', type='positive')
        _migrate_status.set_text('；'.join(parts))

    from nicegui import background_tasks
    background_tasks.create(_migrate_folder_paths())
