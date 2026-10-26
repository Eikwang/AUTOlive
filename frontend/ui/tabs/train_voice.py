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
import time
from typing import Any, Callable, Dict

from nicegui import ui

from frontend.ui.components import FormField
from frontend.ui.components.config_helper import get_nested_value
from utils.autolive_paths import AutolivePaths
from utils.my_log import logger
from utils import alarm_notifier
from utils.train_voice_pipeline import (
    FAILED, PENDING, RUNNING, SUCCESS, TrainVoicePipeline,
)
from utils.train_rvc_pipeline import TrainRvcPipeline

# 模块级单例（防多开；与 train_streamer 同款范式）
_pipeline: TrainVoicePipeline | None = None
_pipeline_lock = threading.Lock()
# P3-1 修订：串行编排的管线链（[(名称, pipeline)]，启动后由编排线程顺序消费）
_pipes: list = []


def pipeline_outcome(p) -> str:
    """管线终态判定（报警重构 2026-10-26）。

    Returns:
        "stopped"=用户主动停止（不发报警/通知）；"failed"=存在失败或未执行
        步骤（报警）；"ok"=全步骤成功（进度通知）。
    """
    if getattr(p, "stop_requested", False):
        return "stopped"
    states = set(p.stage_states().values())
    if FAILED in states or PENDING in states or RUNNING in states:
        return "failed"
    return "ok"

# D1 录音指引文案（一屏内讲完；深化整合后 MDXNet 分离已内置）
_GUIDE_LINES = [
    "① 时长：最低 10 分钟，建议 30 分钟以上（越长效果越稳）",
    "② 条数：50 条以上短句（每句 3-10 秒），语速自然、情绪多样",
    "③ 环境：安静房间，无混响/背景音乐/电流声",
    "④ 带 BGM/噪音的素材可直接使用：系统内置 MDXNet 分离（勾选『已是干声』则跳过分离）",
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

    # ---------- 数据集与参数卡（F-1：分组折叠——基础/高级，D-1 裁决 ASR 语言归基础组） ----------
    with ui.card().style(card_css):
        ui.label("数据集与训练参数")
        ds_input = ui.input(
            label="数据集目录（干声或带 BGM/噪音的原始素材）",
            value="",
            placeholder=r"例如 D:\datasets\my_voice",
        ).style("width:100%")
        exp_input = ui.input(
            label="实验名（即声音档案名）", value="",
            placeholder="例如 ikaros_v2",
        ).style("width:100%")
        with ui.grid(columns=3):
            batch_input = ui.number(label="批大小", value=4, min=1, max=64)
            dry_check = ui.checkbox("已是干声（跳过 MDX 分离）", value=True)
            # D-1：ASR 语言是素材属性（决定标注路由），归基础组——默认 zh 折叠收起会让
            # 非中文素材静默走错路由，失败延迟到 40 分钟训练后才发现
            asr_lang_sel = ui.select(
                {k: v for k, v in [("zh", "中文"), ("yue", "粤语"), ("en", "英语"),
                                   ("ja", "日语"), ("ko", "韩语"), ("auto", "多语自动")]},
                label="素材语言（决定 ASR 模型）", value="zh")
        with ui.expansion("高级参数（通常保持默认）").style("width:100%"):
            with ui.grid(columns=3):
                epoch_s2_input = ui.number(label="SoVITS 轮数", value=10, min=1, max=100)
                epoch_s1_input = ui.number(label="GPT 轮数", value=20, min=1, max=100)
                lr_input = ui.number(label="文本模块学习率权重", value=0.4, min=0.0, max=1.0, step=0.05)
                save_s2_input = ui.number(label="SoVITS 每N轮保存", value=5, min=1, max=50)
                save_s1_input = ui.number(label="GPT 每N轮保存", value=10, min=1, max=50)
                dpo_check = ui.checkbox("DPO 训练（推荐）", value=True)
            # F-1 RVC 可调组（Design P7 复核发现 8：epoch 是最常调旋钮不固定）；
            # 40k/v2/f0=true/CPU10/rmvpe_gpu/spk0=固定规格不暴露（管线内置）
            with ui.grid(columns=3).style("margin-top:6px"):
                epoch_rvc_input = ui.number(label="RVC 轮数", value=20, min=1, max=200)
                save_rvc_input = ui.number(label="RVC 每N轮保存", value=8, min=1, max=50)
                ui.label("RVC 规格：40k · v2 · f0 指导 · rmvpe_gpu · CPU 进程 10 · 说话人 0").style(
                    "font-size:11px;color:var(--text-secondary);align-self:center")

    # ---------- 管线开关（P3-1 修订：双开关默认全开，可单独关闭；串行编排） ----------
    pipe_enabled = {"gpt-sovits": True, "rvc": True}
    pipe_btns: dict = {}

    with ui.card().style(card_css):
        ui.label("训练管线（默认全开，点击切换启用/关闭；实心=启用，描边=关闭）").style(
            "font-weight:var(--font-weight-emphasis)")
        ui.label("启用多条时串行执行：先 GPT-SoVITS 后 RVC；某条失败不影响另一条继续").style(
            "font-size:11px;color:var(--text-secondary)")
        with ui.row():
            pipe_btns["gpt-sovits"] = ui.button(
                "GPT-SoVITS (TTS)", on_click=lambda: _toggle_pipe("gpt-sovits")
            ).props("unelevated no-caps").style("font-size:12px")
            pipe_btns["rvc"] = ui.button(
                "RVC (变声/翻唱)", on_click=lambda: _toggle_pipe("rvc")
            ).props("unelevated no-caps").style("font-size:12px")

    def _toggle_pipe(t: str):
        if pipe_enabled[t]:
            if not any(v for k, v in pipe_enabled.items() if k != t):
                ui.notify(position="top", type="warning", message="至少保留一条训练管线")
                return
            pipe_enabled[t] = False
        else:
            pipe_enabled[t] = True
        for k, btn in pipe_btns.items():
            btn.props("unelevated no-caps" if pipe_enabled[k] else "outline no-caps")
        on_txt = "、".join(
            ("GPT-SoVITS" if k == "gpt-sovits" else "RVC")
            for k, v in pipe_enabled.items() if v)
        ui.notify(position="top", type="info", message=f"本次训练将执行：{on_txt}")

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
        ui.label("注意：训练产出的声音将写入声音档案——TTS（AI对话）与变声（AI对话-变声）、"
                 "翻唱（高级功能-点歌）三端消费。").style(
            "font-size:12px;color:var(--color-warning)")
        ui.label("提示：训练与 AI 对话共享同一张显卡（进程内 TTS 常驻显存已计入准入预算）；"
                 "训练期间对话合成可能变慢，建议避开直播时段训练。").style(
            "font-size:12px;color:var(--text-secondary)")

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

        # 值变化必须走 on_value_change（ValueChangeEventArguments 才有 .value）；
        # 底层 .on("update:model-value") 的事件对象只有 sender/client/args——
        # handler 读 change.value 抛 AttributeError 被 NiceGUI 吞掉，
        # authorized 永远 False → 勾选后点开始仍提示未确认（2026-10-16 用户实测）
        consent.on_value_change(_on_consent)

    started_ts = {"value": 0.0}  # 启动时刻（防 _poll 在编排线程置位 running_stage 前误判"已结束"）

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
        global _pipeline, _pipes
        with _pipeline_lock:
            if any(p.running_stage for _, p in _pipes):
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
            started_ts["value"] = time.time()
            pipes: list = []
            if pipe_enabled["gpt-sovits"]:
                p = TrainVoicePipeline(config, paths)
                p.prepare(
                    dataset_dir=ds_input.value, exp_name=exp_input.value.strip(),
                    batch_size=int(batch_input.value or 4),
                    already_dry=dry_check.value, authorized=True,
                    asr_lang=asr_lang_sel.value or "zh",
                    epochs_s2=int(epoch_s2_input.value or 10),
                    epochs_s1=int(epoch_s1_input.value or 20),
                    text_low_lr_rate=float(lr_input.value or 0.4),
                    save_every_s2=int(save_s2_input.value or 5),
                    save_every_s1=int(save_s1_input.value or 10),
                    if_dpo=bool(dpo_check.value),
                )
                if getattr(p, "asset_issues", None):
                    _msgs = "；".join(f"{n}({r})" for r, n, _ in p.asset_issues[:4])
                    ui.notify(position="top", type="negative",
                              message=f"资产预检未通过（{p.asset_issues.__len__()} 项缺失）：{_msgs}——详见日志")
                pipes.append(("GPT-SoVITS (TTS)", p))
            if pipe_enabled["rvc"]:
                p = TrainRvcPipeline(config, paths)
                p.prepare(
                    dataset_dir=ds_input.value, exp_name=exp_input.value.strip(),
                    batch_size=int(batch_input.value or 8),
                    total_epoch=int(epoch_rvc_input.value or 20),
                    save_every=int(save_rvc_input.value or 8),
                    already_dry=dry_check.value,
                    authorized=True,
                )
                if getattr(p, "asset_issues", None):
                    _msgs = "；".join(f"{n}({r})" for r, n, _ in p.asset_issues[:4])
                    ui.notify(position="top", type="negative",
                              message=f"RVC 资产预检未通过（{p.asset_issues.__len__()} 项）：{_msgs}——详见日志")
                pipes.append(("RVC (变声/翻唱)", p))
            _pipes = pipes
            _pipeline = pipes[0][1]
            _chain_run(pipes)
        start_btn.set_enabled(False)
        stop_btn.set_enabled(True)

    def _chain_run(pipes):
        """串行编排：先 GPT-SoVITS 后 RVC（单卡 GPU 不能并行训练）。
        管线相互独立（资产/产物不同源），某条失败不影响另一条继续——
        失败项由页面红字+重跑按钮呈现，续跑代价上限≈另一条的 MDX 段时长。"""

        def _worker():
            global _pipeline
            for name, p in pipes:
                _pipeline = p
                p.run_all()
                while p.running_stage is not None:
                    time.sleep(0.5)
                # 报警重构（2026-10-26）：训练声音结果推送——完成=进度通知（非错误
                # 报警）；失败/未执行完整=报警；用户主动停止不发（有意操作）
                outcome = pipeline_outcome(p)
                if outcome == "stopped":
                    logger.info(f"[train-voice] {name} 用户停止，不发报警/通知")
                elif outcome == "failed":
                    logger.error(f"[train-voice] {name} 管线存在失败/未执行步骤（详见页面红字），继续后续管线")
                    alarm_notifier.alarm_error(
                        alarm_notifier.MODULE_TRAIN_VOICE,
                        f"训练声音 {name} 未能完成（存在失败或未执行步骤，详见训练页日志）")
                else:
                    alarm_notifier.alarm_notice(
                        alarm_notifier.MODULE_TRAIN_VOICE, f"训练声音 {name} 训练完成")
            logger.info("[train-voice] 全部启用管线执行完毕（失败项见页面标注）")

        threading.Thread(target=_worker, name="train-voice-chain", daemon=True).start()

    def _stop():
        for _, p in _pipes:
            p.stop()
        ui.notify(position="top", type="warning", message="已请求停止（当前步骤跑完即止）")

    # ---------- 状态轮询（0.5s，train_streamer 同款；多管线分组渲染） ----------
    def _poll():
        global _pipeline
        if not _pipes:
            return
        steps_column.clear()
        with steps_column:
            for name, p in _pipes:
                ui.label(name).style(
                    "font-size:11px;color:var(--text-secondary);margin-top:4px")
                for sid, state in p.stage_states().items():
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
                        # DX2 失败四要素：原因摘要（日志尾）+ 日志定位 + 重跑入口 + 查看帮助
                        ui.label("失败：请查看下方日志定位原因（常见原因见录音指引卡与 docs/开发环境搭建.md）").style(
                            "font-size:11px;color:var(--color-danger)")
                        ui.button("重跑此步（覆盖下游）",
                                  on_click=lambda p=p, sid=sid: _rerun(p, sid)).props(
                            "dense unelevated no-caps")
        active = next((p for _, p in _pipes if p.running_stage), None)
        if active is not None:
            _pipeline = active
        shown = active or _pipes[-1][1]
        log_box.set_content("\n".join(shown.log_lines[-30:]))
        if (not any(p.running_stage for _, p in _pipes)
                and start_btn.enabled is False
                and time.time() - started_ts["value"] > 1.0):
            start_btn.set_enabled(True)
            stop_btn.set_enabled(False)

    def _rerun(p, sid: str):
        global _pipeline
        _pipeline = p
        p.reset_from(sid)
        p.run_all()

    ui.timer(0.5, _poll)
