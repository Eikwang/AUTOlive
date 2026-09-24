# -*- coding: UTF-8 -*-
"""
变声标签页（P2-2 重写：RVC 包裹式服务前端）

布局规范：
- 设置项最多 3 列网格，组件宽度跟随列宽（width:100%）
- 模型列表点选即热切换（D7 事务语义：切换中 loading，失败保持旧模型 notify）
- 空态卡片指向训练页（D4）；试听=实时流启停（S11）
- 实时参数默认暴露 f0/key、索引率、保护率三项，其余进高级折叠（D12）
- 旧 so-vits-svc 卡默认关闭+隐藏，legacy_svc.show 可恢复（DX-9），首次进入提示（D2/F5）
- 服务状态来自 RvcVcClient（P2-1 控制面）；服务未就绪时整卡降级提示
"""
import json
import os
import threading
from typing import Any, Callable, Dict

from nicegui import ui

from frontend.ui.components import FormField
from frontend.ui.components.config_helper import get_nested_value
from utils.my_log import logger
from utils.rvc_service import RvcVcClient
from utils.voice_profiles import load_profiles

# 热切换状态（模块级：与 RvcVcClient 调用线程解耦，ui.timer 消费——NiceGUI 线程安全）
_switch_state: Dict[str, Any] = {"busy": False, "result": None, "error": None}
_status_state: Dict[str, Any] = {"status": None, "devices": None, "error": None}
_client: RvcVcClient | None = None


def _client_for(config: Dict[str, Any]) -> RvcVcClient:
    global _client
    if _client is None:
        port = get_nested_value(config, "rvc_vc", "http_port")
        _client = RvcVcClient(http_port=int(port) if port else None)
    return _client


def _scan_models(models_dir: str):
    """扫描 models/rvc/：返回 [{name, pth, index}]（.pth 与同名前缀 .index 配对）。"""
    models = []
    if not os.path.isdir(models_dir):
        return models
    for fn in sorted(os.listdir(models_dir)):
        if not fn.endswith(".pth"):
            continue
        name = fn[:-4]
        base = os.path.join(models_dir, name)
        index = None
        for cand in (f"{base}.index",):
            if os.path.isfile(cand):
                index = cand
                break
        if index is None:
            prefix = name + "_"
            for other in sorted(os.listdir(models_dir)):
                if other.startswith(prefix) and other.endswith(".index"):
                    index = os.path.join(models_dir, other)
                    break
        models.append({"name": name, "pth": os.path.join(models_dir, fn),
                       "index": index})
    return models


def create_svc_tab(
    config: Dict[str, Any],
    theme_config: Dict[str, str],
    set_config_callback: Callable,
):
    """创建变声标签页（RVC 包裹式，P2-2）。"""
    global _client
    card_css = theme_config.get("card", "")
    client = _client_for(config)
    models_dir = os.path.join(
        get_nested_value(config, "paths", "autolive_home")
        or os.path.dirname(os.path.dirname(os.path.abspath(__file__))) + "/..",
        "models", "rvc",
    )
    models_dir = os.path.abspath(models_dir)
    models = _scan_models(models_dir)
    rvc_cfg = config.get("rvc_vc") or {}

    # ---------- 空态（D4） ----------
    if not models and not load_profiles():
        with ui.card().style(card_css):
            ui.label("还没有声音模型").style("font-weight:var(--font-weight-emphasis)")
            ui.label("去【数字人-训练声音】创建你的第一个声音，或手动放入模型文件到 models/rvc/")
            ui.button("前往训练声音", on_click=lambda: ui.run_javascript(
                "window.location.hash = '#train_voice'"))
        return

    # ---------- 服务状态卡 ----------
    with ui.card().style(card_css):
        ui.label("RVC 实时变声")
        status_label = ui.label("状态查询中...").style("font-size:12px;color:var(--text-secondary)")
        with ui.row():
            stream_btn = ui.button("启动试听", on_click=lambda: _toggle_stream(config))

        def _poll():
            try:
                st = client.status()
                running = st.get("state") == "running"
                model = (st.get("model") or {}).get("pth", "")
                infer_t = st.get("last_infer_time_s", 0)
                model_short = os.path.basename(model) if model else "未加载"
                status_label.set_text(
                    f"{'● 运行中' if running else '○ 待机'} · 模型 {model_short} · "
                    f"单块推理 {infer_t*1000:.0f}ms")
                stream_btn.set_text("停止试听" if running else "启动试听")
            except Exception:
                status_label.set_text("○ 服务未就绪（rvc-vc 未运行或端口不符）")

        ui.timer(2.0, _poll)

    # ---------- 声音模型列表（点选即热切换）----------
    with ui.card().style(card_css):
        ui.label("声音模型（共享目录 models/rvc/）")
        if not models:
            ui.label("models/rvc/ 目录为空").style("color:var(--text-secondary)")
        else:
            with ui.column().style("width:100%;gap:4px"):
                for m in models:
                    has_index = bool(m["index"])
                    with ui.row().style(
                        "width:100%;align-items:center;border:1px solid var(--border-color);"
                        "border-radius:var(--radius-md);padding:6px 10px;"
                    ):
                        ui.label(m["name"]).style("flex:1")
                        ui.label(".pth + .index ✔" if has_index else "缺 index ⚠").style(
                            "font-size:11px;color:var(--text-secondary)")
                        switch_btn = ui.button(
                            "切换",
                            on_click=lambda m=m: _do_switch(config, m),
                        ).props("dense unelevated no-caps")

                        def _btn_state(b=switch_btn):
                            b.set_enabled(not _switch_state["busy"])
                        ui.timer(1.0, _btn_state)

        switch_result = ui.label("").style("font-size:12px;color:var(--text-secondary)")

    # ---------- 实时参数（D12：三项起步 + 高级折叠）----------
    with ui.card().style(card_css):
        with ui.expansion("实时参数", value=False).style("width:100%"):
            with ui.grid(columns=3):
                with ui.column().style("width:100%"):
                    FormField.create_input(
                        label="f0 变调 (key)",
                        value=str(get_nested_value(config, "rvc_vc", "pitch") or 0),
                        on_change=lambda e: set_config_callback("rvc_vc", "pitch", _as_float(e.value)),
                        style="width:100%",
                    )
                with ui.column().style("width:100%"):
                    FormField.create_input(
                        label="索引率",
                        value=str(get_nested_value(config, "rvc_vc", "index_rate") or 0.75),
                        on_change=lambda e: set_config_callback("rvc_vc", "index_rate", _as_float(e.value)),
                        style="width:100%",
                    )
                with ui.column().style("width:100%"):
                    FormField.create_input(
                        label="保护率",
                        value=str(get_nested_value(config, "rvc_vc", "protect") or 0.33),
                        on_change=lambda e: set_config_callback("rvc_vc", "protect", _as_float(e.value)),
                        style="width:100%",
                    )
            ui.label("高级参数（block/crossfade/阈值等）在 config rvc_vc 节").style(
                "font-size:11px;color:var(--text-secondary)")

    # ---------- 旧 so-vits-svc（DX-9：隐藏+legacy_svc.show 可恢复；D2/F5 首次提示）----------
    show_legacy = bool(get_nested_value(config, "legacy_svc", "show"))
    if show_legacy:
        with ui.card().style(card_css):
            ui.label("旧版 SO-VITS-SVC（已由 RVC 替代）").style("color:var(--text-secondary)")
            with ui.grid(columns=2):
                FormField.create_input(
                    label="配置文件路径",
                    value=get_nested_value(config, "so_vits_svc", "config_path"),
                    on_change=lambda e: set_config_callback("so_vits_svc", "config_path", e.value),
                    style="width:100%",
                )
                FormField.create_input(
                    label="API 地址",
                    value=get_nested_value(config, "so_vits_svc", "api_ip_port"),
                    on_change=lambda e: set_config_callback("so_vits_svc", "api_ip_port", e.value),
                    style="width:100%",
                )


def _as_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _do_switch(config: Dict[str, Any], model: dict):
    """点选即热切换（D7）：busy 状态防再选；失败 notify 保持旧模型（事务在服务端）。"""
    if _switch_state["busy"]:
        ui.notify(position="top", type="warning", message="切换进行中，请稍候")
        return
    _switch_state.update({"busy": True, "result": None, "error": None})

    def _work():
        try:
            client = _client_for(config)
            r = client.switch_model(model["pth"], model["index"],
                                    _as_float(get_nested_value(
                                        config, "rvc_vc", "index_rate") or 0.75))
            _switch_state["result"] = r
        except Exception as e:
            _switch_state["error"] = str(e)
        finally:
            _switch_state["busy"] = False

    threading.Thread(target=_work, daemon=True).start()
    ui.notify(position="top", type="info", message=f"正在切换到 {model['name']} ...")
    _notify_when_done()


def _notify_when_done():
    def _check():
        if _switch_state["busy"]:
            return False  # 继续 timer
        if _switch_state["error"]:
            ui.notify(position="top", type="negative",
                      message=f"切换失败（旧模型继续服务）：{_switch_state['error'][:120]}")
        else:
            ui.notify(position="top", type="positive", message="切换成功")
        return True  # 停止 timer

    ui.timer(1.0, _check)


def _toggle_stream(config: Dict[str, Any]):
    """S11/D4：试听=实时流启停（无模型时服务端拒绝并给三要素）。"""
    def _work():
        try:
            client = _client_for(config)
            st = client.status()
            if st.get("state") == "running":
                client.stop_stream()
            else:
                client.start_stream()
        except Exception as e:
            ui.notify(position="top", type="negative", message=f"试听失败: {str(e)[:120]}")

    threading.Thread(target=_work, daemon=True).start()
