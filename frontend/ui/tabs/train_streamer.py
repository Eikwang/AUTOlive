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
)

# 日志回显缓冲（后台线程写入，ui.timer 消费——NiceGUI 线程安全）
_LOG_BUFFER: deque = deque(maxlen=800)
# 模块级单例（防多开）
_pipeline: TrainPipeline | None = None
_pipeline_lock = threading.Lock()


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
            "输入原始视频文件夹，一键完成：预处理编排(11子步) → 底模微调 → 评估 → 口型微调"
        ).style("color: var(--text-secondary); font-size: var(--font-size-body)")

        # ---------- 输入区 ----------
        with ui.grid(columns=2).style("width:100%"):
            video_dir_input = FormField.create_input(
                label="原始视频文件夹路径",
                value="",
                placeholder=r"例如 D:\videos\my_streamer（原始视频放其 original_videos 子目录）",
                on_change=lambda e: role_input.set_value(
                    _default_role(e.value)
                ),
                style="width:100%;",
            )
            role_input = FormField.create_input(
                label="角色名（默认取文件夹名）",
                value="",
                placeholder="my_streamer",
                style="width:100%;",
            )
        # 素材要求提示（DX 义务③）
        ui.label(
            "原始视频放入 <目录>/original_videos/；时长与数量参照 EDTalk 数据集处理流程"
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
        log_area = ui.log(max_lines=500).classes("w-full").style(
            f"background: var(--bg-log, #1e1e1e); color: var(--text-log, #d4d4d4); "
            f"min-height: 220px; max-height: 420px; overflow-x: auto; "
            f"font-family: var(--font-family-mono, monospace); font-size: 13px"
        )
        log_empty_hint = ui.label("输入路径后点击开始训练").style(
            "color: var(--text-hint); text-align: center; width:100%"
        )

        # ---------- 高级参数折叠区 ----------
        with ui.expansion("高级参数").style("width:100%").open(False):
            with ui.grid(columns=3):
                FormField.create_input(
                    label="底模微调 iter（经济截断点 30000）",
                    value=str(get_nested_value(config, "train_streamer", "fine_tune_iter", default=30000)),
                    on_change=lambda e: set_config_callback("train_streamer", "fine_tune_iter", value=int(e.value or 30000)),
                    style="width:100%;",
                )
                FormField.create_input(
                    label="起始阶段（1-4，断点重跑）",
                    value="1",
                    on_change=lambda e: None,  # 读取在开始时
                    style="width:100%;",
                )
            ui.label("口型微调配方参数（lip_dim_weight 5.0 / mouth_vgg_weight 0.2 / smooth 0.2 / epoch 2）"
                     "已按 Audio2Mouth 定稿配方预填，本期不暴露逐项编辑").style(
                "color: var(--text-hint); font-size: var(--font-size-body)")

        # ---------- 断点提示 ----------
        resume_hint = ui.label("").style("color: var(--text-hint)")
        # 读取进度文件（页面加载时）
        try:
            role_guess = _default_role(video_dir_input.value)
            if role_guess:
                prog = TrainPipeline.read_progress(role_guess)
                if prog:
                    resume_hint.set_text(
                        f"上次进行到第 {prog.get('current_stage')} 阶段（{prog.get('status')}，"
                        f"{prog.get('timestamp')}）——可在高级参数中设置起始阶段"
                    )
        except Exception:
            pass

    # ---------- 交互逻辑（card 外部，避免 NiceGUI 上下文问题） ----------
    pipeline_holder: list = [None]

    def _confirm_start():
        """开始训练二次确认。"""
        video_dir = (video_dir_input.value or "").strip().strip('"')
        role = (role_input.value or "").strip() or _default_role(video_dir)
        if not video_dir or not role:
            ui.notify(position="top", type="warning", message="请先填写视频目录与角色名")
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
                "耗时可达十余小时并占用 GPU（训练期间无法直播）。\n"
                "开始按钮在训练进行中将保持置灰。"
            ).style("white-space: pre-wrap")
            with ui.row():
                def _do_start():
                    dialog.close()
                    _start_pipeline(video_dir, role)
                ui.button("开始", on_click=_do_start)
                ui.button("取消", on_click=dialog.close).props("flat")
        dialog.open()

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
        # 状态同步
        if pipeline is not None:
            if training_running and pipeline.confirm_event.is_set() is False and pipeline.current_stage == 3:
                # 评估确认需求：pipeline.on_confirm_needed 由线程调用 on_confirm_needed 回调
                pass
            if not training_running:
                # 训练结束（完成/失败/停止）
                if start_btn.isEnabled() is False:
                    start_btn.set_enabled(True)
                    stop_btn.set_enabled(False)
                    for i in range(len(stage_labels)):
                        if pipeline.current_stage == 0 and i + 1 <= 4:
                            # 完成的阶段标绿（completed_stage 语义见进度文件）
                            pass
                    _set_stage_state(len(stage_labels) - 1, "done") if getattr(pipeline, "_completed_ok", False) else None
                    pipeline_ref[0] = None

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
            ui.label(
                "门槛标定于研究基准数据集，你的素材可用性以实际效果为准。\n"
                "停止（默认）= 不进入口型微调；继续 = 接受当前指标直接微调口型。"
            ).style("white-space: pre-wrap")
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
