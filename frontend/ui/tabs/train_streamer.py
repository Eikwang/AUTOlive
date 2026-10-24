# -*- coding: utf-8 -*-
"""
训练主播标签页模块（EDTalk功能集成计划 §5.1）

选择素材视频（本地文件选择对话框，只记录路径） → 开始 → 素材复制到角色
目录 → 预处理编排(11子步) → 底模微调 → 评估 → 口型微调。无开关、独立
功能、不进 ENABLE_PATHS。

素材选择逻辑（2026-10-15 用户裁决）：弃浏览器上传——webui 跑在本机，
服务端直读文件系统只记录路径（无浏览器上传内存限制），用户可跨目录
累计/移除已选文件，开始训练时才复制到 数据根/<角色名>/original_videos/。

设计义务（design 阶段裁决）：常驻步骤条（4 大阶段+11 子步）、日志区空态
引导、配色走设计令牌（--bg-log/--text-log）、窄屏 overflow-x、停止二次
确认（代价文案）、训练完成 ui.notify + 产物路径高亮、评估"仍要继续"
Quasar dialog、应用到数字人确认对话框（映射验证前置灰）。
"""
import asyncio
import os
import re
import shutil
import threading
import time
import traceback
from collections import deque
from typing import Any, Callable, Dict

from nicegui import ui

from frontend.ui.components import FormField
from frontend.ui.components.config_helper import get_nested_value
from frontend.ui.components.local_file_picker import LocalFilePicker, format_size
from utils.my_log import logger
from utils.train_streamer.pipeline import (
    TrainPipeline,
    get_edtalk_dir,
    validate_role_name,
)

# 日志回显缓冲（后台线程写入，ui.timer 消费——NiceGUI 线程安全）
_LOG_BUFFER: deque = deque(maxlen=800)
# 模块级单例（防多开）
_pipeline: TrainPipeline | None = None
_pipeline_lock = threading.Lock()


def _upload_dir_for(role: str, data_root: str = "") -> str:
    """素材落点（original_videos 目录本身）。配置化数据根（义务块：
    config.train_streamer.data_root，未配置时默认 <AUTOlive根>/data/
    edtalk_train/{role}/original_videos/——基准取 get_edtalk_dir() 的
    父目录即 AUTOlive 根，与 edtalk 副本/进度文件同一事实源；此前
    dirname 层数算错落到 frontend/data/ 下，2026-10-15 修正）。
    data_root 由调用方从 config 传入（本函数为模块级，不持有 tab 闭包
    的 config）。"""
    if not data_root:
        data_root = os.path.join(os.path.dirname(get_edtalk_dir()), "data", "edtalk_train")
    return os.path.join(data_root, role, "original_videos")


def create_train_streamer_tab(
    config: Dict[str, Any],
    theme_config: Dict[str, str],
    set_config_callback: Callable,
):
    """创建训练主播标签页。"""
    card_css = theme_config.get("card", "")
    pipeline_ref: list = [None]
    pipeline_holder: list = [None]  # 与 pipeline_ref 同置 card 内：刷新恢复段需要挂接

    with ui.card().style(card_css):
        ui.label("训练主播")
        ui.label(
            "上传角色视频，一键完成：预处理编排(11子步) → 底模微调 → 评估 → 口型微调 → 自动部署"
        ).style("color: var(--text-secondary); font-size: var(--font-size-body)")

        # ---------- 区①：训练状态卡置顶（design D1/D17/F7：ETA/已耗时/进度行） ----------
        with ui.card().style(
            "width:100%;padding:10px 14px;border:1px solid var(--border-color);"
            "background:var(--bg-hover)"
        ):
            with ui.row().style("width:100%;align-items:center;gap:12px"):
                status_icon = ui.element("span").style(
                    "width:10px;height:10px;border-radius:50%;"
                    "background:var(--border-color);flex-shrink:0"
                )
                status_text = ui.label("空闲").style(
                    "font-size: var(--font-size-body); font-weight: var(--font-weight-title)"
                )
                eta_text = ui.label("").style(
                    "color: var(--text-secondary); font-size: var(--font-size-body)"
                )
            progress_line = ui.label("").style(
                "color: var(--text-hint); font-size: var(--font-size-body); width:100%"
            )

        # ---------- 区②：素材选择与角色表单区（design D1：训练中禁用） ----------
        def _validate_role_input(e):
            """角色名即时校验（design D4：非法输入即时拒绝）。"""
            err = validate_role_name(e.value or "")
            if err:
                role_warning.set_text(err)
                role_warning.style("color: var(--color-danger); display:block")
            else:
                role_warning.style("display:none")

        role_input = FormField.create_input(
            label="角色名（必填，字母/数字/下划线/短横线，1-32 位）",
            value="",
            placeholder="my_streamer",
            on_change=_validate_role_input,
            style="width:100%;",
        )
        role_warning = ui.label("").style("color: var(--color-danger); display:none")

        # 素材选择状态（闭包内局部态：选择只记录路径，开始训练时才复制落盘）
        picked_videos: list = []      # 已选素材绝对路径（str）
        last_browse: list = [None]    # 上次浏览目录（本会话记忆，对话框起点）
        pick_row_btns: list = []      # 已选列表删除按钮引用（训练中锁定用）
        _picker_open = [False]

        def _render_picks():
            """重绘已选素材列表（文件名+大小+删除；空态提示）。"""
            pick_row_btns.clear()
            picks_container.clear()
            with picks_container:
                if not picked_videos:
                    ui.label("尚未选择素材（点击上方按钮，从本机选择视频文件，可多选）").style(
                        "color: var(--text-hint); font-size: var(--font-size-body)")
                    return
                total_b = 0
                for p in picked_videos:
                    try:
                        b = os.path.getsize(p)
                        total_b += b
                        size_txt = format_size(b)
                    except OSError:
                        size_txt = "（文件不存在）"
                    with ui.row().style("width:100%;align-items:center;gap:8px"):
                        ui.icon("movie").style("color: var(--text-hint); flex-shrink:0")
                        ui.label(os.path.basename(p)).tooltip(p).style(
                            "flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;"
                            "white-space:nowrap;color: var(--text-body); font-size: var(--font-size-body)")
                        ui.label(size_txt).style(
                            "color: var(--text-secondary); font-size: var(--font-size-small); flex-shrink:0")
                        _del = ui.button(
                            icon="close", on_click=lambda _, pp=p: _remove_pick(pp)).props("flat dense round")
                        _del.tooltip("移除该素材（不删除原文件）")
                        pick_row_btns.append(_del)
                ui.label(f"共 {len(picked_videos)} 个文件 · 合计 {format_size(total_b)}"
                         "（仅记录路径，开始训练时才复制到角色目录）").style(
                    "color: var(--text-secondary); font-size: var(--font-size-small)")

        def _remove_pick(path: str):
            if path in picked_videos:
                picked_videos.remove(path)
            _render_picks()

        async def _open_picker():
            """打开本地文件选择对话框（防重入；选择结果仅追加路径）。"""
            if _picker_open[0]:
                return
            _picker_open[0] = True
            pick_btn.set_enabled(False)
            try:
                result = await LocalFilePicker(last_browse[0], multiple=True, title="选择素材视频")
            finally:
                _picker_open[0] = False
                pick_btn.set_enabled(True)
            if not result:
                return
            added = 0
            for p in result:
                sp = str(p)
                if sp not in picked_videos:
                    picked_videos.append(sp)
                    added += 1
            last_browse[0] = str(result[0].parent)
            _render_picks()
            if added:
                ui.notify(position="top", type="positive",
                          message=f"已添加 {added} 个素材文件（仅记录路径，开始训练时复制）")

        pick_btn = ui.button("选择素材视频（可多选）", icon="folder_open", on_click=_open_picker).props(
            "size=lg").style("width:100%")
        picks_container = ui.column().style("width:100%;gap:4px")
        _render_picks()

        # 受限路径入口（Eng F-E1 语义弱化：本机直读无浏览器上传内存限制后，
        # 该入口服务"素材已组织好/批量目录"场景；design D21：旧路径只读识别）
        with ui.expansion("既有目录（高级路径入口）").style("width:100%"):
            video_dir_input = FormField.create_input(
                label="既有视频目录（含 original_videos 子目录）",
                value="",
                placeholder=r"例如 D:\videos\my_streamer（原始视频放其 original_videos 子目录）",
                style="width:100%;",
            )
            ui.label("素材已组织为标准目录结构（含 original_videos 子目录）或需要批量复用"
                     "整个目录时可直接填路径；旧数据目录保持只读识别，训练原样可用").style(
                "color: var(--text-hint); font-size: var(--font-size-body)")

        # 素材要求提示（DX 义务③；2026-10-15 补续训说明）
        ui.label(
            "全新训练（勾选阶段 1）时，已选素材将在开始训练时复制到 数据根/<角色名>/original_videos/；"
            "仅勾选阶段 2-4（续训）则无需素材，直接使用该角色已处理的数据集。"
            "素材时长与数量参照 EDTalk 数据集处理流程"
            "（wy 实例 = 529 视频 / 63055 clips；更少素材可训，口型质量预期下降）"
        ).style("color: var(--text-hint); font-size: var(--font-size-body)")

        # 显存提示区（开始训练前双检测：8000 端口 + running_flag）
        gpu_warning = ui.label("").style(
            "color: var(--color-warning); display:none"
        )

        # ---------- 操作按钮 ----------
        with ui.row():
            # lambda 延迟解析：_confirm_start 定义在本 card 构建块之后（async，
            # NiceGUI 会 await handler 返回的 coroutine——events.should_await）
            start_btn = ui.button("开始训练", on_click=lambda: _confirm_start())
            stop_btn = ui.button("停止", on_click=lambda: _confirm_stop()).props("outline")
            stop_btn.set_enabled(False)

        def _set_form_locked(locked: bool):
            """D1 义务落地：训练中锁定区②表单（角色名/素材选择/既有目录/
            流程选择按钮），训练结束在 _poll 恢复——防训练中改角色名/路径
            造成进度文件与 UI 不一致。"""
            role_input.set_enabled(not locked)
            pick_btn.set_enabled(not locked)
            video_dir_input.set_enabled(not locked)
            for _btn in pick_row_btns:
                _btn.set_enabled(not locked)
            for _sb in stage_buttons:  # 运行时查找：本函数定义于流程条之前
                _sb.set_enabled(not locked)

        def _default_role(path: str) -> str:
            path = (path or "").strip().strip('"')
            return os.path.basename(os.path.normpath(path)) if path else ""

        def _check_gpu_conflict() -> str:
            """双检测：realtime_serve（8000 端口）+ 直播系统 running_flag。"""
            # 系统运行态由调用方 app 注入较深，此处以端口探测为准 + 计划义务 running_flag
            # 检测在 start_programs 侧同样存在（双向防护）
            import requests as _requests
            ed_port = get_nested_value(config, "edtalk_realtime", "port", default=8000)
            try:
                r = _requests.get(f"http://127.0.0.1:{ed_port}/status", timeout=1)
                if r.status_code == 200:
                    return "检测到实时推理服务器正在运行（GPU 显存冲突风险，16GB 卡训练与推理不可并存），建议先关闭"
            except Exception:
                pass
            return ""

        # ---------- 流程选择条（常驻；2026-10-15 用户裁决：步骤条改为多选
        #           按钮——默认全选，可任意子集（如只勾 2-4 续训跳过预处理），
        #           按序执行；运行中锁定，染色由 _poll 接管为运行状态） ----------
        selected_stages: list = [1, 2, 3, 4]
        stage_buttons = []

        def _toggle_stage(idx: int):
            s = idx + 1
            if s in selected_stages:
                selected_stages.remove(s)
            else:
                selected_stages.append(s)
            _render_stage_btn(idx)

        def _render_stage_btn(idx: int):
            """选中=主色底白字；未选=灰底半透明。
            UI-16 批次 R9/DV-1g：改两态类切换（stage-btn-selected/unselected，
            全局 CSS 成对令牌）——原 .style() 字符串累积会在同一元素上留下两份
            color 声明（CSS 取第一个），这是选中态文字发灰的根因。"""
            stage_buttons[idx].style(
                "padding: 6px 12px; border-radius: var(--radius-md); "
                "font-size: var(--font-size-body); cursor: pointer")
            stage_buttons[idx].classes(remove='stage-btn-selected stage-btn-unselected text-white')
            # text-white=Quasar 原生工具类（color:#fff!important）——与令牌类双保险，
            # 二轮验收：用户实测选中态仍灰，加一层与 Quasar 体系同源的强制白
            stage_buttons[idx].classes(add=('stage-btn-selected text-white' if (idx + 1) in selected_stages else 'stage-btn-unselected'))

        with ui.row().classes("q-mt-sm q-gutter-sm"):
            for _i, _name in enumerate(TrainPipeline.STAGE_NAMES):
                _btn = ui.button(_name, on_click=lambda _, i=_i: _toggle_stage(i)).props(
                    "flat dense no-caps").style(
                    "padding: 6px 12px; border-radius: var(--radius-md); "
                    "font-size: var(--font-size-body)")
                _btn.tooltip("点击选择/取消该阶段；可任意组合，按序号顺序执行")
                stage_buttons.append(_btn)
                _render_stage_btn(_i)  # UI-16 批次 R9：初始两态类渲染（默认全选=主色白字）

        def _set_stage_state(idx: int, state: str):
            """state: pending/running/done/failed（运行态染色，覆盖选中样式）"""
            colors = {
                "pending": ("var(--bg-hover)", "var(--text-secondary)"),
                "running": ("var(--color-primary)", "var(--text-on-primary)"),
                "done": ("var(--color-positive)", "var(--text-on-primary)"),
                "failed": ("var(--color-negative)", "var(--text-on-primary)"),
            }
            bg, fg = colors[state]
            stage_buttons[idx].style(
                f"padding: 4px 10px; border-radius: var(--radius-md); "
                f"background: {bg}; color: {fg}; font-size: var(--font-size-body)"
            )

        # ---------- 日志区 ----------
        # 空态提示覆盖在日志区中央（design 审查 FINDING-004：原实现排在日志区
        # 下方，220px 空黑盒读作死区）；开始训练后 display:none（下方启动链）
        with ui.column().style("width:100%;position:relative;gap:0"):
            log_area = ui.log(max_lines=500).classes("w-full").style(
                f"background: var(--bg-log, #1e1e1e); color: var(--text-log, #d4d4d4); "
                f"min-height: 220px; max-height: 420px; overflow-x: auto; "
                f"font-family: var(--font-family-mono, monospace); font-size: 13px"
            )
            log_empty_hint = ui.label("输入路径后点击开始训练").style(
                "position:absolute;top:100px;left:50%;transform:translateX(-50%);"
                "color: var(--text-hint); pointer-events:none; white-space:nowrap"
            )

        # ---------- 区③：高级参数折叠区（义务块"训练高级参数暴露"；
        #           默认值单一事实源表：iter=50000/epoch=2(uc2b)/batch=4/lr 三段/amp 开） ----------
        with ui.expansion("高级参数").style("width:100%"):
            with ui.grid(columns=3):
                FormField.create_input(
                    label="底模微调 iter（1000-1000000）",
                    value=str(get_nested_value(config, "train_streamer", "fine_tune_iter", default=50000)),
                    on_change=lambda e: set_config_callback("train_streamer", "fine_tune_iter", value=int(e.value or 50000)),
                    style="width:100%;",
                )
                FormField.create_input(
                    label="口型微调 epoch（1-10）",
                    value=str(get_nested_value(config, "train_streamer", "epoch", default=2)),
                    on_change=lambda e: set_config_callback("train_streamer", "epoch", value=int(e.value or 2)),
                    style="width:100%;",
                )
                FormField.create_input(
                    label="batch_size（1-32）",
                    value=str(get_nested_value(config, "train_streamer", "batch_size", default=4)),
                    on_change=lambda e: set_config_callback("train_streamer", "batch_size", value=int(e.value or 4)),
                    style="width:100%;",
                )
                FormField.create_input(
                    label="lr_schedule（三段逗号分隔）",
                    value=str(get_nested_value(config, "train_streamer", "lr_schedule", default="0.001,0.001,0.0001")),
                    on_change=lambda e: set_config_callback("train_streamer", "lr_schedule", value=e.value),
                    style="width:100%;",
                )
                # 混合精度开关：label 置顶与相邻字段对齐（design 审查 FINDING-005：
                # ui.switch 自带 label 在控件右侧，三列网格里与 label 在上的输入框错位）
                with ui.column().style("gap:0;width:100%"):
                    ui.label("混合精度 amp").style(
                        "font-size: var(--font-size-field-label); color: var(--text-secondary)")
                    amp_switch = ui.switch(
                        value=bool(get_nested_value(config, "train_streamer", "amp", default=True)),
                        on_change=lambda e: set_config_callback("train_streamer", "amp", value=bool(e.value)),
                    )
            ui.label("--only_fine_tune_dec 保持强制启用（实践最佳配方：小数据集全参训练易崩，"
                     "且判别器链路未启用时 dis 仅注入噪声梯度）").style(
                "color: var(--text-hint); font-size: var(--font-size-body)")

        # ---------- 断点提示 + 刷新恢复（design D11：进度文件为唯一事实源，无条件重挂接） ----------
        resume_hint = ui.label("").style("color: var(--text-hint)")
        try:
            from utils.train_streamer import pipeline as _tp_mod
            role_guess = getattr(_tp_mod, "training_role", None) or _default_role(video_dir_input.value)
            prog = TrainPipeline.read_progress(role_guess) if role_guess else None
            if prog:
                status_txt = prog.get("status", "")
                if _tp_mod.training_running:
                    # 刷新恢复：重挂接进行中训练的呈现（内存 pipeline 不可达时进度文件兜底）
                    resume_hint.set_text(
                        f"检测到进行中的训练：角色 {prog.get('role')}，阶段 {prog.get('current_stage')}/4"
                        f"（{status_txt}，已耗时 {prog.get('elapsed_s', 0) / 60:.0f} 分钟）"
                    )
                    # 2026-10-15 补：按钮/表单/引用同步恢复——原实现只恢复提示
                    # 文本，刷新后 stop 仍灰（无法停止训练）、start 仍可点（被
                    # 防重入拦）——用户被迫手动杀 python 进程
                    _live = _pipeline  # 模块级单例：训练线程仍在跑
                    if _live is not None:
                        pipeline_ref[0] = _live
                        pipeline_holder[0] = _live
                        start_btn.set_enabled(False)
                        stop_btn.set_enabled(True)
                        _set_form_locked(True)
                elif prog.get("status") in ("running", "failed"):
                    resume_hint.set_text(
                        f"上次进行到第 {prog.get('current_stage')} 阶段（{status_txt}，{prog.get('timestamp')}）"
                        f"——可在高级参数中设置起始阶段"
                    )
        except Exception:
            pass

    # ---------- 交互逻辑（card 外部，避免 NiceGUI 上下文问题） ----------

    async def _confirm_start():
        """开始训练二次确认（含素材复制+角色名校验，义务块"上传视频落点"）。"""
        role = (role_input.value or "").strip()
        # 角色名校验（D4：前端即时+编排层二次）
        name_err = validate_role_name(role)
        if name_err:
            role_warning.set_text(name_err)
            role_warning.style("color: var(--color-danger); display:block")
            ui.notify(position="top", type="negative", message=name_err)
            return
        # 素材 vs 既有路径 vs 续训三条入口（2026-10-15 用户裁决：起始阶段>1
        # 即续训——直接使用该角色已有数据集，无需再选素材/填路径）
        stage_min = min(selected_stages) if selected_stages else 1
        video_dir = (video_dir_input.value or "").strip().strip('"')
        if video_dir:
            source_txt = f"既有目录入口：{video_dir}（已选素材将被忽略）"
        elif stage_min > 1:
            data_root = get_nested_value(config, "train_streamer", "data_root", default="")
            ds_dir = os.path.dirname(_upload_dir_for(role, data_root))
            if not os.path.isdir(ds_dir):
                ui.notify(position="top", type="negative",
                          message=f"problem=未找到角色数据集；cause={ds_dir} 不存在；"
                                  f"fix=检查角色名，或先勾选阶段 1 执行预处理编排")
                return
            video_dir = ds_dir
            source_txt = f"续训模式（从阶段 {stage_min} 起）：使用已有数据集 {ds_dir}"
        elif picked_videos:
            data_root = get_nested_value(config, "train_streamer", "data_root", default="")
            dest = _upload_dir_for(role, data_root)
            total_b = sum((os.path.getsize(p) for p in picked_videos if os.path.exists(p)), 0)
            source_txt = f"已选素材 {len(picked_videos)} 个文件（合计 {format_size(total_b)}）→ 复制到 {dest}"
        else:
            ui.notify(position="top", type="warning",
                      message="请先选择素材文件，或使用高级路径入口填写既有目录")
            return
        gpu_msg = await asyncio.to_thread(_check_gpu_conflict)
        if gpu_msg:
            gpu_warning.set_text(gpu_msg)
            gpu_warning.style("color: var(--color-warning); display:block")
        with ui.dialog() as dialog, ui.card():
            ui.label("确认开始训练？").style("font-weight: var(--font-weight-title)")
            sel_names = " → ".join(
                TrainPipeline.STAGE_NAMES[i - 1] for i in sorted(set(selected_stages) or {1, 2, 3, 4}))
            ui.label(
                f"角色：{role}\n素材：{source_txt}\n"
                + f"本次执行：{sel_names}\n\n"
                "训练将按所选阶段顺序执行，\n"
                "耗时可达十余小时并占用 GPU（训练期间无法启动主系统直播）。\n"
                "完成后自动部署为推理可用角色包。"
            ).style("white-space: pre-wrap")
            with ui.row():
                async def _do_start():
                    dialog.close()
                    await _start_with_copy(video_dir, role)
                ui.button("开始", on_click=_do_start)
                ui.button("取消", on_click=dialog.close).props("flat")
        dialog.open()

    def _persist_picks(role: str) -> str:
        """把已选素材复制到 data_root/{role}/original_videos/（2026-10-15 用户
        裁决：选择时只记录路径，开始训练时才复制；自动建目录+磁盘预检+文件名
        清洗（Eng F-E5）+冲突唯一化）。返回落点目录；无素材返回空串；失败抛
        ValueError。工作线程中执行，不碰 UI。"""
        if not picked_videos:
            return ""
        data_root = get_nested_value(config, "train_streamer", "data_root", default="")
        dest = _upload_dir_for(role, data_root)
        os.makedirs(dest, exist_ok=True)
        missing = [p for p in picked_videos if not os.path.isfile(p)]
        if missing:
            raise ValueError(
                f"problem=素材文件不存在；cause={os.path.basename(missing[0])}（共 {len(missing)} 个失效）；"
                f"fix=在已选列表中移除失效文件后重新开始")
        total = sum(os.path.getsize(p) for p in picked_videos)
        free_gb = shutil.disk_usage(dest).free / (1024 ** 3)
        need_gb = total / (1024 ** 3) * 200  # 预处理产物放大系数保守 200x
        if free_gb < max(50, need_gb):
            raise ValueError(
                f"problem=磁盘空间不足；cause=剩余 {free_gb:.0f}GB，预处理产物预估需 {max(50, need_gb):.0f}GB；"
                f"fix=清理磁盘后重试")
        saved = 0
        for src in picked_videos:
            name = os.path.basename(src)
            ext = os.path.splitext(name)[1].lower()
            if ext not in (".mp4", ".avi", ".mov", ".mkv", ".webm"):
                raise ValueError(f"problem=不支持的文件类型；cause={name}；fix=仅支持 mp4/avi/mov/mkv/webm")
            stem = re.sub(r"[^A-Za-z0-9_-]", "_", os.path.splitext(name)[0]) or "video"
            target = os.path.join(dest, f"{stem}{ext}")
            n = 1
            while os.path.exists(target):  # 冲突唯一化（默认不覆盖）
                target = os.path.join(dest, f"{stem}_{n}{ext}")
                n += 1
            shutil.copy2(src, target)
            saved += 1
        picked_videos.clear()
        # 返回 video_dir 语义=含 original_videos 的父目录（pipeline preflight
        # 检查 {video_dir}/original_videos；此前直接返回 original_videos 本身
        # 导致"目录中未找到 original_videos 子目录"必现，2026-10-15 修复）
        return os.path.dirname(dest)

    async def _start_with_copy(video_dir: str, role: str):
        """确认后执行：复制素材（既有目录入口跳过）→ 启动训练线程。"""
        if not video_dir:
            start_btn.set_enabled(False)
            status_text.set_text("正在复制素材到数据目录…")
            try:
                video_dir = await asyncio.to_thread(_persist_picks, role)
            except ValueError as ve:
                start_btn.set_enabled(True)
                status_text.set_text("空闲")
                ui.notify(position="top", type="negative", message=str(ve))
                return
            if not video_dir:  # 双保险（确认框已挡空素材）
                start_btn.set_enabled(True)
                status_text.set_text("空闲")
                return
        _start_pipeline(video_dir, role)

    def _confirm_stop():
        """停止二次确认——文案暴露代价（design 义务）。"""
        pipeline = pipeline_ref[0]
        if pipeline is None:
            return
        elapsed_h = (time.time() - getattr(pipeline, "_start_time", time.time())) / 3600
        with ui.dialog() as dialog, ui.card():
            ui.label("确认停止训练？").style("font-weight: var(--font-weight-title)")
            ui.label(
                f"终止将丢弃当前步骤进度（本期无断点续训），\n"
                f"已耗时 {elapsed_h:.1f} 小时，确定停止？"
            ).style("white-space: pre-wrap")
            with ui.row():
                def _do_stop():
                    dialog.close()
                    pipeline.confirm_stop()
                    pipeline.stop()
                ui.button("确定停止", on_click=_do_stop).props("color=negative")
                ui.button("继续训练", on_click=dialog.close).props("flat")
        dialog.open()

    def _start_pipeline(video_dir: str, role: str):
        """启动后台训练线程。"""
        global _pipeline
        with _pipeline_lock:
            # 实例级 _finished 判定（2026-10-15 修复：from-import 的
            # training_running 是值绑定，run() 置 True 不可见→防重入失效）
            if _pipeline is not None and not getattr(_pipeline, "_finished", True):
                ui.notify(position="top", type="warning", message="已有训练任务进行中")
                return
            try:
                pipeline = TrainPipeline(
                    config_data=config._config if hasattr(config, "_config") else config,
                    video_dir=video_dir,
                    role_name=role,
                    on_log=lambda line, stage=None: _LOG_BUFFER.append((line, stage)),
                    on_complete=lambda ok, msg: (
                        setattr(pipeline_holder[0], "_completed_ok", ok) if pipeline_holder[0] else None,
                        _LOG_BUFFER.append((f"=== {'完成' if ok else '结束'}：{msg} ===", None)),
                    ),
                    on_confirm_needed=lambda: setattr(pipeline_holder[0], "_confirm_pending", True),
                    stages=set(selected_stages) or {1, 2, 3, 4},
                )
                pipeline._start_time = time.time()
                _pipeline = pipeline
                pipeline_ref[0] = pipeline
                pipeline_holder[0] = pipeline
                threading.Thread(target=pipeline.run, daemon=True).start()
                log_empty_hint.style("display:none")
                start_btn.set_enabled(False)
                stop_btn.set_enabled(True)
                _set_form_locked(True)
                ui.notify(position="top", type="positive", message="训练已开始，进度见下方日志")
            except Exception as e:
                logger.error(traceback.format_exc())
                ui.notify(position="top", type="negative", message=f"启动训练失败：{e}")

    # ---------- 轮询：日志回显 + 状态同步 + 评估确认（NiceGUI 线程安全） ----------

    def _poll():
        pipeline = pipeline_ref[0]
        # 消费日志缓冲
        while _LOG_BUFFER:
            line, stage = _LOG_BUFFER.popleft()
            log_area.push(line)
            if stage is not None and pipeline is not None:
                for i in range(len(stage_buttons)):
                    if i + 1 < pipeline.current_stage:
                        _set_stage_state(i, "done")
                    elif i + 1 == pipeline.current_stage:
                        _set_stage_state(i, "running")
                    else:
                        _set_stage_state(i, "pending")
        # 状态同步 + ETA/已耗时（义务块"训练 ETA 呈现"/design D17：进行中显示已耗时+预计剩余）
        if pipeline is not None:
            # 运行判定用实例级 _finished（2026-10-15 修复：from-import 的
            # training_running 是值绑定——run() 置 True 后这里仍读到 False，
            # _poll 首轮即走"结束分支"把 start/stop 复位，停止按钮 0.5s 内熄灭）
            if not getattr(pipeline, "_finished", False):
                # 置顶状态卡更新（design D1）
                status_icon.style("background: var(--color-warning)")
                stage_name = TrainPipeline.STAGE_NAMES[max(0, pipeline.current_stage - 1)]
                status_text.set_text(f"训练中 · {stage_name}")
                elapsed_s = time.time() - (getattr(pipeline, "_t_start", None) or time.time())
                eta_txt = ""
                prog = TrainPipeline.read_progress(getattr(pipeline, "role_name", "") or "")
                if pipeline.current_stage == 4 and prog and prog.get("eta_s"):
                    eta_txt = f" · 预计剩余约 {int(prog['eta_s'] // 60)} 分钟"
                eta_text.set_text(f"已耗时 {elapsed_s / 60:.0f} 分钟{eta_txt}")
                progress_line.set_text(f"阶段 {pipeline.current_stage}/4 · 详细日志见下方")
            else:
                # 训练结束（完成/失败/停止）：按钮恢复+状态卡+完成引导（D2/D12）
                if start_btn.enabled is False:
                    start_btn.set_enabled(True)
                    stop_btn.set_enabled(False)
                    _set_form_locked(False)
                    ok = getattr(pipeline, "_completed_ok", False)
                    status_icon.style(
                        "background: var(--color-success)" if ok else "background: var(--color-danger)")
                    status_text.set_text("训练完成" if ok else "训练结束")
                    eta_text.set_text("")
                    progress_line.set_text(getattr(pipeline, "_final_message", "") or "")
                    pipeline_ref[0] = None
                    if ok:
                        ui.notify(position="top", type="positive", timeout=10,
                                  message="训练完成并已自动部署。下一步：到 数字人-画面设置 页选择该角色，启动系统开播。")

        # 评估"仍要继续"确认需求（pipeline 线程 set confirm_event 前调 on_confirm_needed）
        if pipeline is not None and getattr(pipeline, "_confirm_pending", False):
            _show_eval_confirm(pipeline)

    def _on_confirm_needed_wrapper(pipeline):
        """线程侧回调——仅置标志，UI 轮询消费。"""
        pipeline._confirm_pending = True

    def _show_eval_confirm(pipeline: TrainPipeline):
        """评估不过门槛的"仍要继续"确认框（Quasar dialog，design 义务⑥）。"""
        if getattr(pipeline, "_confirm_dialog_shown", False):
            return
        pipeline._confirm_dialog_shown = True
        with ui.dialog() as dialog, ui.card():
            ui.label("评估未过门槛").style("font-weight: var(--font-weight-title)").style("color: var(--color-negative)")
            # D12：具体指标+两选项后果（全旅程情感最重时刻不盲选）
            summary = getattr(pipeline, "_last_eval_summary", None) or {}
            verdict = summary.get("verdict") or "未解析到评估指标"
            cand = summary.get("candidate") or {}
            base = summary.get("base") or {}
            metric_lines = [f"未过原因：{verdict}", ""]
            if cand:
                metric_lines.append(f"候选模型：姿势MAE {cand.get('pose_keep_mae_px', '?')}px（门槛≤11.1）、贴回 {cand.get('paste_proxy_mae_px', '?')}px（门槛≤3.6）、开合比 {cand.get('lip_open_ratio_vs_ref', '?')}")
            if base:
                metric_lines.append(f"底模对照：背景SSIM {base.get('bg_ssim', '?')}")
            metric_lines += ["", "门槛标定于研究基准数据集，你的素材可用性以实际效果为准。",
                             "停止：保留已训练模型（可手动部署）。继续：使用当前模型微调口型，效果可能不佳。"]
            ui.label("\n".join(metric_lines)).style("white-space: pre-wrap")
            with ui.row():
                def _stop():
                    dialog.close()
                    pipeline._confirm_pending = False
                    pipeline.confirm_stop()
                def _cont():
                    dialog.close()
                    pipeline._confirm_pending = False
                    pipeline.confirm_continue()
                ui.button("停止（默认）", on_click=_stop).props("color=negative")
                ui.button("仍要继续", on_click=_cont)
        dialog.open()

    ui.timer(0.5, _poll)
