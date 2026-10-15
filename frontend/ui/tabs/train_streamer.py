# -*- coding: utf-8 -*-
"""
训练主播标签页模块（EDTalk功能集成计划 §5.1）

一键全链路训练：输入原始视频文件夹路径 → 开始 → 预处理编排(11子步) →
底模微调 → 评估 → 口型微调。无开关、独立功能、不进 ENABLE_PATHS。

设计义务（design 阶段裁决）：常驻步骤条（4 大阶段+11 子步）、日志区空态
引导、配色走设计令牌（--bg-log/--text-log）、窄屏 overflow-x、停止二次
确认（代价文案）、训练完成 ui.notify + 产物路径高亮、评估"仍要继续"
Quasar dialog、应用到数字人确认对话框（映射验证前置灰）。
"""
import os
import shutil
import threading
import time
import traceback
from collections import deque
from typing import Any, Callable, Dict

from nicegui import ui

from frontend.ui.components import FormField
from frontend.ui.components.config_helper import get_nested_value
from utils.my_log import logger
from utils.train_streamer.pipeline import (
    TrainPipeline,
    get_edtalk_dir,
    training_running,
    validate_role_name,
)

# 日志回显缓冲（后台线程写入，ui.timer 消费——NiceGUI 线程安全）
_LOG_BUFFER: deque = deque(maxlen=800)
# 模块级单例（防多开）
_pipeline: TrainPipeline | None = None
_pipeline_lock = threading.Lock()
# 上传暂存（auto_upload=False：用户点上传按钮后逐文件回调收集，开始训练时统一落盘）
_pending_uploads: list = []


def _upload_dir_for(role: str) -> str:
    """上传落点=配置化数据根（义务块：config.train_streamer.data_root，默认
    <AUTOlive根>/data/edtalk_train/{role}/original_videos/）。"""
    from frontend.ui.components.config_helper import get_nested_value as _gnv
    root = _gnv(config, "train_streamer", "data_root", default="") if config else ""
    if not root:
        base = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        root = os.path.join(base, "data", "edtalk_train")
    return os.path.join(root, role, "original_videos")


def create_train_streamer_tab(
    config: Dict[str, Any],
    theme_config: Dict[str, str],
    set_config_callback: Callable,
):
    """创建训练主播标签页。"""
    card_css = theme_config.get("card", "")
    pipeline_ref: list = [None]

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

        # ---------- 区②：上传与角色表单区（design D1：训练中禁用） ----------
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
        # 上传控件（义务块"上传视频落点"+design D5 状态五件套；NiceGUI 上传
        # 为内存态——Eng F-E1：单文件上限 500MB，超限走下方受限路径入口降级）
        upload_files_label = ui.label("尚未选择文件").style("color: var(--text-hint)")
        def _collect_upload(e):
            """逐文件收集（D5 状态五件套：文件名+大小列表；Eng F-E1 单文件上限）。"""
            name = getattr(e, "name", "")
            size = getattr(e, "size", 0) or 0
            if size > 500 * 1024 * 1024:
                ui.notify(position="top", type="negative",
                          message=f"problem=文件超限；cause={name} 为 {size / 1024 / 1024:.0f}MB > 500MB；"
                                  f"fix=使用下方高级路径入口填写既有目录（大文件逃生通道）")
                return
            _pending_uploads.append(e)
            total = len(_pending_uploads)
            upload_files_label.set_text(f"已选 {total} 个文件（开始训练时自动落盘）")

        upload_btn = ui.upload(
            label="上传视频（单文件 ≤500MB，开始训练时自动放入数据根）",
            multiple=True,
            auto_upload=False,
            on_upload=_collect_upload,
        ).props('accept="video/*,.mp4,.avi,.mov,.mkv"').style("width:100%")
        # 受限路径入口（Eng F-E1：GB 级视频逃生通道；design D21：旧路径只读识别）
        with ui.expansion("大文件/既有目录（高级路径入口）").style("width:100%"):
            video_dir_input = FormField.create_input(
                label="既有视频目录（含 original_videos 子目录）",
                value="",
                placeholder=r"例如 D:\videos\my_streamer（原始视频放其 original_videos 子目录）",
                style="width:100%;",
            )
            ui.label("大文件建议直接放入既有目录后填此路径（避开浏览器上传内存限制）；"
                     "旧数据目录保持只读识别，训练原样可用").style(
                "color: var(--text-hint); font-size: var(--font-size-body)")

        # 素材要求提示（DX 义务③）
        ui.label(
            "上传的视频将复制到 数据根/<角色名>/original_videos/；时长与数量参照 EDTalk 数据集处理流程"
            "（wy 实例 = 529 视频 / 63055 clips；更少素材可训，口型质量预期下降）"
        ).style("color: var(--text-hint); font-size: var(--font-size-body)")

        # 显存提示区（开始训练前双检测：8000 端口 + running_flag）
        gpu_warning = ui.label("").style(
            "color: var(--color-warning, #b58900); display:none"
        )

        # ---------- 操作按钮 ----------
        with ui.row():
            start_btn = ui.button("开始训练", on_click=lambda: _confirm_start())
            stop_btn = ui.button("停止", on_click=lambda: _confirm_stop()).props("outline")
            stop_btn.set_enabled(False)

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

        # ---------- 步骤条（常驻，4 大阶段） ----------
        stage_labels = []
        with ui.row().classes("q-mt-md q-gutter-sm"):
            for name in TrainPipeline.STAGE_NAMES:
                lbl = ui.label(name).style(
                    "padding: 4px 10px; border-radius: var(--radius-md); "
                    "background: var(--bg-hover); color: var(--text-secondary); "
                    "font-size: var(--font-size-body)"
                )
                stage_labels.append(lbl)

        def _set_stage_state(idx: int, state: str):
            """state: pending/running/done/failed"""
            colors = {
                "pending": ("var(--bg-hover)", "var(--text-secondary)"),
                "running": ("var(--color-primary, #1976d2)", "#fff"),
                "done": ("var(--color-positive, #21ba45)", "#fff"),
                "failed": ("var(--color-negative, #c10015)", "#fff"),
            }
            bg, fg = colors[state]
            stage_labels[idx].style(
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
                FormField.create_input(
                    label="起始阶段（1-4，断点重跑）",
                    value="1",
                    on_change=lambda e: None,  # 读取在开始时
                    style="width:100%;",
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
                elif prog.get("status") in ("running", "failed"):
                    resume_hint.set_text(
                        f"上次进行到第 {prog.get('current_stage')} 阶段（{status_txt}，{prog.get('timestamp')}）"
                        f"——可在高级参数中设置起始阶段"
                    )
        except Exception:
            pass

    # ---------- 交互逻辑（card 外部，避免 NiceGUI 上下文问题） ----------
    pipeline_holder: list = [None]

    def _confirm_start():
        """开始训练二次确认（含上传落盘+角色名校验，义务块"上传视频落点"）。"""
        role = (role_input.value or "").strip()
        # 角色名校验（D4：前端即时+编排层二次）
        name_err = validate_role_name(role)
        if name_err:
            role_warning.set_text(name_err)
            role_warning.style("color: var(--color-danger); display:block")
            ui.notify(position="top", type="negative", message=name_err)
            return
        # 上传 vs 既有路径两条入口（Eng F-E1：GB 级降级）
        uploads = list(getattr(upload_btn, "clients", []) or [])
        video_dir = (video_dir_input.value or "").strip().strip('"')
        if video_dir:
            # 既有路径入口：preflight 会做 original_videos 结构校验
            pass
        else:
            # 上传入口：落盘到 data_root/{role}/original_videos/（复制+磁盘预检）
            try:
                video_dir = _persist_uploads(role)
            except ValueError as ve:
                ui.notify(position="top", type="negative", message=str(ve))
                return
            if not video_dir:
                ui.notify(position="top", type="warning",
                          message="请先上传视频文件，或使用高级路径入口填写既有目录")
                return
        gpu_msg = _check_gpu_conflict()
        if gpu_msg:
            gpu_warning.set_text(gpu_msg)
            gpu_warning.style("color: var(--color-warning, #b58900); display:block")
        with ui.dialog() as dialog, ui.card():
            ui.label("确认开始训练？").style("font-weight: var(--font-weight-title)")
            ui.label(
                f"角色：{role}\n目录：{video_dir}\n\n"
                "训练将连续执行 4 大阶段（预处理 / 底模微调 / 评估 / 口型微调），\n"
                "耗时可达十余小时并占用 GPU（训练期间无法启动主系统直播）。\n"
                "完成后自动部署为推理可用角色包。"
            ).style("white-space: pre-wrap")
            with ui.row():
                def _do_start():
                    dialog.close()
                    _start_pipeline(video_dir, role)
                ui.button("开始", on_click=_do_start)
                ui.button("取消", on_click=dialog.close).props("flat")
        dialog.open()

    def _persist_uploads(role: str) -> str:
        """把 ui.upload 选中的文件落盘到 data_root/{role}/original_videos/
        （义务块"上传视频落点"：自动建目录+复制+磁盘预检；Eng F-E5 文件名清洗；
        D5 五件套的失败含原因）。返回落点目录；无文件返回空串；失败抛 ValueError。"""
        content = getattr(upload_btn, "content", None)
        # NiceGUI multi 上传：e 为 UploadEventArguments 列表，存于 btn 的内部状态——
        # 实际取法：遍历 ui.upload 的 events 不可行（auto_upload=False 时文件在
        # 前端暂存，点击开始时经 handle_upload 回调逐个到达）。此处采用二次确认后
        # 由 on_upload 回调累积到模块级列表，见 _collect_upload 注册。
        files = list(_pending_uploads)
        if not files:
            return ""
        dest = _upload_dir_for(role)
        os.makedirs(dest, exist_ok=True)
        # 磁盘预检（上传前义务；Eng F-E1）
        total = sum(getattr(f, "size", 0) or 0 for f in files)
        free_gb = shutil.disk_usage(dest).free / (1024 ** 3)
        need_gb = total / (1024 ** 3) * 200  # 预处理产物放大系数保守 200x
        if free_gb < max(50, need_gb):
            raise ValueError(
                f"problem=磁盘空间不足；cause=剩余 {free_gb:.0f}GB，预处理产物预估需 {max(50, need_gb):.0f}GB；"
                f"fix=清理磁盘后重试")
        import re as _re
        saved = 0
        for f in files:
            # 文件名清洗（Eng F-E5：Path.name 取尾+白名单扩展+冲突唯一化）
            safe_name = os.path.basename(getattr(f, "name", "video.mp4"))
            ext = os.path.splitext(safe_name)[1].lower()
            if ext not in (".mp4", ".avi", ".mov", ".mkv", ".webm"):
                raise ValueError(f"problem=不支持的文件类型；cause={safe_name}；fix=仅支持 mp4/avi/mov/mkv/webm")
            stem = _re.sub(r"[^A-Za-z0-9_-]", "_", os.path.splitext(safe_name)[0]) or "video"
            target = os.path.join(dest, f"{stem}{ext}")
            n = 1
            while os.path.exists(target):  # 冲突唯一化（默认不覆盖）
                target = os.path.join(dest, f"{stem}_{n}{ext}")
                n += 1
            with open(target, "wb") as out:
                out.write(f.content.read())
            saved += 1
            upload_files_label.set_text(f"已落盘 {saved}/{len(files)}：{os.path.basename(target)}")
        _pending_uploads.clear()
        upload_files_label.set_text(f"共 {saved} 个文件已放入 {dest}")
        return dest

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
            if _pipeline is not None and training_running:
                ui.notify(position="top", type="warning", message="已有训练任务进行中")
                return
            try:
                start_stage = 1
                # 高级参数中的起始阶段（读 config）
                start_stage = int(get_nested_value(config, "train_streamer", "start_stage", default=1) or 1)

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
                    start_stage=max(1, start_stage),
                )
                pipeline._start_time = time.time()
                _pipeline = pipeline
                pipeline_ref[0] = pipeline
                pipeline_holder[0] = pipeline
                threading.Thread(target=pipeline.run, daemon=True).start()
                log_empty_hint.style("display:none")
                start_btn.set_enabled(False)
                stop_btn.set_enabled(True)
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
                for i in range(len(stage_labels)):
                    if i + 1 < pipeline.current_stage:
                        _set_stage_state(i, "done")
                    elif i + 1 == pipeline.current_stage:
                        _set_stage_state(i, "running")
                    else:
                        _set_stage_state(i, "pending")
        # 状态同步 + ETA/已耗时（义务块"训练 ETA 呈现"/design D17：进行中显示已耗时+预计剩余）
        if pipeline is not None:
            if training_running:
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
                if start_btn.isEnabled() is False:
                    start_btn.set_enabled(True)
                    stop_btn.set_enabled(False)
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
            ui.label("评估未过门槛").style("font-weight: var(--font-weight-title)").style("color: var(--color-negative, #c10015)")
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
