# -*- coding: UTF-8 -*-
"""
数字人-训练声音标签页（P2-3）

- 录音指引卡（D1：最低时长/建议条数/环境/干声说明）
- 启动确认卡（授权勾选 P1-7 机制 + "产出当前仅可用于 TTS"明示 CEO-D1）
- 数据集路径 + GPU 占用检查 + 步骤清单五态（D5）+ 全链路一键 + 日志尾
- 末步导出声音档案（voice_profiles 写入，三端可选用 CEO-F7 明示）
"""
import os
import threading
from typing import Any, Callable, Dict

from nicegui import ui

from frontend.ui.components import FormField
from frontend.ui.components.config_helper import get_nested_value
from utils.autolive_paths import AutolivePaths
from utils.my_log import logger
from utils.train_voice_pipeline import (
    FAILED, PENDING, RUNNING, SUCCESS, TrainVoicePipeline,
)
from utils.train_rvc_pipeline import TrainRvcPipeline

# 模块级单例（防多开；与 train_streamer 同款范式）
_pipeline: TrainVoicePipeline | None = None
_pipeline_lock = threading.Lock()

# D1 录音指引文案（一屏内讲完）
_GUIDE_LINES = [
    "① 时长：最低 10 分钟，建议 30 分钟以上（越长效果越稳）",
    "② 条数：50 条以上短句（每句 3-10 秒），语速自然、情绪多样",
    "③ 环境：安静房间，无混响/背景音乐/电流声",
    "④ 干声：请提供已分离人声的干声文件（本版不含 UVR5 分离）",
]


def create_train_voice_tab(
    config: Dict[str, Any],
    theme_config: Dict[str, str],
    set_config_callback: Callable,
):
    """创建训练声音标签页（数字人分组）。"""
    global _pipeline
    card_css = theme_config.get("card", "")
    paths = AutolivePaths(config)

    # ---------- 录音指引卡（D1） ----------
    with ui.card().style(card_css):
        ui.label("录音指引（开始前必读）").style("font-weight:var(--font-weight-emphasis)")
        for line in _GUIDE_LINES:
            ui.label(line).style("font-size:12px;color:var(--text-secondary)")

    # ---------- 数据集与参数卡 ----------
    with ui.card().style(card_css):
        ui.label("数据集与训练参数")
        ds_input = ui.input(
            label="数据集目录（含干声 wav）",
            value="",
            placeholder=r"例如 D:\datasets\my_voice",
        ).style("width:100%")
        exp_input = ui.input(
            label="实验名（即声音档案名）", value="",
            placeholder="例如 ikaros_v2",
        ).style("width:100%")
        with ui.grid(columns=3):
            epoch_input = ui.number(label="训练轮数", value=12, min=1, max=100)
            batch_input = ui.number(label="批大小", value=8, min=1, max=64)
            dry_check = ui.checkbox("已是干声（跳过 UVR5）", value=True)

    # ---------- 管线切换（P3-1：GPT-SoVITS / RVC 双流共用一页） ----------
    pipeline_type_state = {"value": "gpt-sovits"}
    with ui.card().style(card_css):
        ui.label("训练管线")
        with ui.row():
            ui.button("GPT-SoVITS (TTS)", on_click=lambda: _set_pipeline("gpt-sovits")).props(
                "unelevated no-caps").style("font-size:12px")
            ui.button("RVC (变声/翻唱)", on_click=lambda: _set_pipeline("rvc")).props(
                "unelevated no-caps").style("font-size:12px")

    def _set_pipeline(t):
        pipeline_type_state["value"] = t
        ui.notify(position="top", type="info", message=f"训练管线已切换: {t}")

    # ---------- GPU 状态卡 ----------
    gpu_label: dict = {}
    with ui.card().style(card_css):
        ui.label("GPU 占用检查")
        gpu_label["text"] = ui.label("检查中...").style("font-size:12px;color:var(--text-secondary)")

        def _poll_gpu():
            from utils.gpu_policy import is_background_task_allowed
            ok, reason = is_background_task_allowed(float(
                get_nested_value(config, "audio_integration", "policies", "vram_reserve_gb") or 4.0))
            gpu_label["text"].set_text(
                ("✔ 空闲：" if ok else "✖ 占用：") + reason)

        ui.timer(5.0, _poll_gpu)

    # ---------- 启动确认卡（D1/P1-7：授权勾选 + P2 限制明示） ----------
    authorized: dict = {"value": False}
    with ui.card().style(card_css):
        ui.label("启动确认").style("font-weight:var(--font-weight-emphasis)")
        consent = ui.checkbox(
            "我确认拥有录制/使用该声音的合法授权（将留档）")
        ui.label("注意：训练产出的声音当前仅可用于 TTS；变声/翻唱消费随后续版本开放。").style(
            "font-size:12px;color:var(--color-warning)")

        def _on_consent(change):
            authorized["value"] = bool(change.value)
            if change.value:
                # P1-7 授权留档（数据结构+文案存档；商用前升级为可验证绑定 S-2）
                try:
                    from utils.voice_profiles import _atomic_update
                    import datetime
                    _atomic_update(
                        os.path.join(os.path.dirname(os.path.dirname(
                            os.path.dirname(os.path.abspath(__file__)))), "config.json"),
                        lambda d: d.setdefault("voice_consent_log", []).append({
                            "exp": exp_input.value,
                            "consented_at": datetime.datetime.now().isoformat(),
                            "text": "确认拥有录制/使用该声音的合法授权",
                        }))
                except Exception:
                    logger.error("[train-voice] 授权留档失败", exc_info=True)

        consent.on("update:model-value", _on_consent)

    # ---------- 步骤清单（五态）+ 控制按钮 ----------
    with ui.card().style(card_css):
        ui.label("流程步骤（一键全链路，单步重跑覆盖下游产物）")
        steps_column = ui.column().style("width:100%;gap:2px")
        log_box = ui.code("").style("max-height:180px;font-size:11px;width:100%")
        start_btn = ui.button("开始全链路训练",
                              on_click=lambda: _start())
        stop_btn = ui.button("停止", on_click=lambda: _stop()).props("flat")
        stop_btn.set_enabled(False)

    def _start():
        global _pipeline
        with _pipeline_lock:
            if _pipeline is not None and _pipeline.running_stage:
                ui.notify(position="top", type="warning", message="训练进行中")
                return
            if not authorized["value"]:
                ui.notify(position="top", type="negative",
                          message="请先勾选声音使用授权（启动确认卡）")
                return
            if not ds_input.value or not os.path.isdir(ds_input.value):
                ui.notify(position="top", type="negative",
                          message=f"数据集目录不存在: {ds_input.value}")
                return
            if not exp_input.value.strip():
                ui.notify(position="top", type="negative", message="请填写实验名")
                return
            pipeline_type = pipeline_type_state.get("value", "gpt-sovits")
            if pipeline_type == "rvc":
                _pipeline = TrainRvcPipeline(config, paths)
                _pipeline.prepare(
                    dataset_dir=ds_input.value, exp_name=exp_input.value.strip(),
                    total_epoch=int(epoch_input.value or 20),
                    batch_size=int(batch_input.value or 8),
                )
            else:
                _pipeline = TrainVoicePipeline(config, paths)
                _pipeline.prepare(
                    dataset_dir=ds_input.value, exp_name=exp_input.value.strip(),
                    total_epoch=int(epoch_input.value or 12),
                    batch_size=int(batch_input.value or 8),
                    already_dry=dry_check.value, authorized=True,
                )
        start_btn.set_enabled(False)
        stop_btn.set_enabled(True)
        _pipeline.run_all()

    def _stop():
        if _pipeline:
            _pipeline.stop()
            ui.notify(position="top", type="warning", message="已请求停止（当前步骤跑完即止）")

    # ---------- 状态轮询（0.5s，train_streamer 同款） ----------
    def _poll():
        global _pipeline
        p = _pipeline
        if p is None:
            return
        states = p.stage_states()
        steps_column.clear()
        with steps_column:
            for sid, state in states.items():
                plan = p._plan()[sid]
                icon = {PENDING: "·", RUNNING: "▶", SUCCESS: "✔",
                        FAILED: "✖", "skipped": "—"}[state]
                color = {"running": "var(--color-warning)",
                         "success": "var(--color-success)",
                         "failed": "var(--color-danger)"}.get(
                    state, "var(--text-secondary)")
                with ui.row().style("width:100%;justify-content:space-between;padding:2px 6px"):
                    ui.label(f"{icon} {plan['name']}").style(f"color:{color};font-size:13px")
                    ui.label(state).style("font-size:11px;color:var(--text-secondary)")
                if state == FAILED:
                    # D5 失败三要素：原因摘要（日志尾）+ 日志定位 + 重跑入口
                    ui.label("失败：请查看下方日志定位原因").style(
                        "font-size:11px;color:var(--color-danger)")
                    ui.button("重跑此步（覆盖下游）", on_click=lambda sid=sid: _rerun(sid)).props(
                        "dense unelevated no-caps")
        log_box.set_content("\n".join(p.log_lines[-30:]))
        if p.running_stage is None and start_btn.enabled is False:
            start_btn.set_enabled(True)
            stop_btn.set_enabled(False)

    def _rerun(sid: str):
        if _pipeline:
            _pipeline.reset_from(sid)
            _pipeline.run_all()

    ui.timer(0.5, _poll)
