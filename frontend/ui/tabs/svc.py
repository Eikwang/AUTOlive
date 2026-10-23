# -*- coding: UTF-8 -*-
"""
变声标签页（/autoplan 2026-10-22 批次 TE6 重构：独立设备到设备流程页面）

布局（D-1 三段式，设计令牌 CSS 变量无硬编码色值）：
- 控制段：服务状态卡（延迟/推理读数+统一内联错误条 D-5）+设备选择（E-9 name 持久化）
  +启停按钮四态（D-4 idle/starting/running/error）
- 模型段：档案优先+目录兜底（A-2 来源过滤；D-8 缺 index 行内标记；D-3 空态指引）
- 参数段：热更组（即时生效）在上；重建管线级组（D-7「改后需重启流生效」+待生效标记）
  在下。全部参数持久化 rvc_vc 节（D-6，同一双写通道）

A-7 诊断修复：单 timer + async 回调（run.io_bound 卸载网络调用，事件循环零阻塞）
+模块级 _active_timers 显式停旧（进出页面不再泄漏 timer）。
模型切换（D-2）：/switch 事务热替换（同 tgt_sr 零间隙），busy 态表达，不重启流。
"""
import os
import threading
from typing import Any, Callable, Dict

from nicegui import background_tasks, run, ui

from frontend.ui.components.config_helper import get_nested_value
from utils.my_log import logger
from utils.rvc_service import RvcVcClient
from utils.voice_profiles import load_profiles

# 热切换状态（模块级：与调用线程解耦，timer 消费——NiceGUI 线程安全）
_switch_state: Dict[str, Any] = {"busy": False, "error": None}
_stream_state: Dict[str, Any] = {"state": "idle", "error": None}  # D-4 四态
_active_timers: list = []  # A-7：进出页面显式停旧 timer（clear() 不 cancel）
_client: RvcVcClient | None = None

_PARAM_KEYS_HOT = ("threhold", "rms_mix_rate", "f0method",
                   "I_noise_reduce", "O_noise_reduce", "use_pv")
_PARAM_KEYS_REBUILD = ("block_time", "crossfade_time", "extra_time",
                       "sr_type", "n_cpu")


def _client_for(config: Dict[str, Any]) -> RvcVcClient:
    global _client
    if _client is None:
        port = get_nested_value(config, "rvc_vc", "http_port")
        _client = RvcVcClient(http_port=int(port) if port else None)
    return _client


def _stop_stale_timers():
    """A-7：进入页面先停旧 timer（config_page.clear() 不 cancel，须显式管理）。"""
    global _active_timers
    for t in _active_timers:
        try:
            t.active = False
        except Exception:
            pass
    _active_timers = []


def _scan_models(models_dir: str):
    """扫描 models/rvc/：返回 [{name, pth, index}]（.pth 与同名前缀 .index 配对，
    缺 index 仍返回但 index=None——D-8 前端加警告标记，不隐藏）。"""
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


def _as_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def create_svc_tab(
    config: Dict[str, Any],
    theme_config: Dict[str, str],
    set_config_callback: Callable,
):
    """创建变声标签页（独立设备到设备流程，TE6）。"""
    global _client
    card_css = theme_config.get("card", "")
    client = _client_for(config)
    _stop_stale_timers()
    models_dir = os.path.join(
        get_nested_value(config, "paths", "autolive_home")
        or os.path.dirname(os.path.dirname(os.path.abspath(__file__))) + "/..",
        "models", "rvc",
    )
    models_dir = os.path.abspath(models_dir)
    models = _scan_models(models_dir)
    profiles = load_profiles()

    # ---------- D-6：双写工具（config 持久化+/params 热更；E-1 服务端真值） ----------
    def _apply_param(key, value, cast=None, rebuild_hint=False):
        v = cast(value) if cast else value
        set_config_callback("rvc_vc", key, v)
        try:
            client.update_params({key: v})
            _rebuild_dirty.discard(key)
        except Exception as e:
            _rebuild_dirty.add(key)
            _show_error(f"参数热更新失败：{str(e)[:120]}")
            return
        if rebuild_hint:
            _rebuild_dirty.add(key)
            _update_rebuild_badge()
        _flash_ok()

    _rebuild_dirty: set = set()  # D-7：已改未生效（待重启流）键集
    _error_label: list = []      # D-5：统一内联错误条
    _ok_label: list = []         # D-5：成功回显

    def _show_error(msg: str):
        if _error_label:
            _error_label[0].set_text(msg)
            _error_label[0].set_visibility(True)

    def _clear_error():
        if _error_label:
            _error_label[0].set_visibility(False)

    def _flash_ok():
        if _ok_label:
            _ok_label[0].set_text("✓ 参数已生效")
            ui.timer(1.5, lambda: (_ok_label[0].set_text("") if _ok_label else None),
                     once=True)

    def _update_rebuild_badge():
        if _rebuild_badge:
            _rebuild_badge.set_visibility(bool(_rebuild_dirty))

    _rebuild_badge: list = []

    # ---------- 空态（D-3/A-2）：无模型且无档案时给指引，不渲染空壳页面 ----------
    if not models and not load_profiles():
        with ui.card().style(card_css):
            ui.label("还没有声音模型").style("font-weight:var(--font-weight-emphasis)")
            ui.label("先在【数字人-训练声音】完成 RVC 训练，或将 .pth+.index 放入 models/rvc/"
                     "（同前缀配对即被识别）")
            ui.button("前往训练声音", on_click=lambda: ui.run_javascript(
                "window.location.hash = '#train_voice'"))
        return

    # ============ 控制段 ============
    with ui.card().style(card_css):
        ui.label("RVC 实时变声（独立流程：指定麦克风 → 变声 → 指定输出）").style(
            "font-weight:var(--font-weight-emphasis)")
        status_label = ui.label("状态查询中...").style(
            "font-size:12px;color:var(--text-secondary)")
        # D-5：统一内联错误条（状态卡下方，全页一致）
        err = ui.label("").style(
            "font-size:12px;color:var(--color-danger);width:100%").classes("mt-1")
        err.set_visibility(False)
        _error_label.append(err)
        ok = ui.label("").style("font-size:12px;color:var(--color-positive)")
        _ok_label.append(ok)

        # ---- D-3：设备选择（E-9：持久化 name，空态禁用启动）----
        with ui.row().style("width:100%;align-items:center;gap:8px;flex-wrap:wrap"):
            ui.label("输入麦克风").style("min-width:80px;font-size:12px")
            in_sel = ui.select([], value=get_nested_value(config, "rvc_vc", "input_device"),
                               label="选择输入麦克风",
                               on_change=lambda e: set_config_callback(
                                   "rvc_vc", "input_device", e.value),
                               ).style("min-width:220px")
            ui.label("输出设备").style("min-width:80px;font-size:12px")
            out_sel = ui.select([], value=get_nested_value(config, "rvc_vc", "output_device"),
                                label="选择输出设备",
                                on_change=lambda e: set_config_callback(
                                    "rvc_vc", "output_device", e.value),
                                ).style("min-width:220px")
            ui.button("刷新设备", on_click=lambda: _refresh_devices()).props(
                "dense unelevated no-caps")
        no_device_label = ui.label("").style(
            "font-size:12px;color:var(--color-warning)")
        no_device_label.set_visibility(False)

        # ---- D-4：启停按钮四态 ----
        with ui.row().style("align-items:center;gap:8px"):
            stream_btn = ui.button("启动变声", on_click=lambda: _toggle_stream())

        # 服务就绪自愈（2026-10-23 排障批）：rvc-vc 随主系统 main.py 托管
        # （webui.auto_run=false 时不自动拉起，点「启动系统」后约 10s 初始化），
        # 页签先于服务就绪打开时首次枚举必失败——此前错误常驻、下拉恒空
        # （唯一出路手动刷新），用户感知「变声无法使用」。loaded/pending 让
        # _poll 探测到服务上线后自动补枚举+就绪清错，不再依赖手动刷新。
        _devices_state = {"loaded": False, "pending": False}

        async def _refresh_devices():
            """E-9：/devices 枚举（io_bound 卸载）→ 填充下拉；空态禁用启动。
            pending 防并发（首次枚举/补枚举在途时跳过）；失败给行动指引不锁死。"""
            def _enum():
                d = client.devices()
                out = {"input": [], "output": []}
                for dev in d.get("devices") or []:
                    name = dev.get("name")
                    if not name:
                        continue
                    if dev.get("max_input_channels", 0) > 0:
                        out["input"].append(name)
                    if dev.get("max_output_channels", 0) > 0:
                        out["output"].append(name)
                return out
            if _devices_state["pending"]:
                return
            _devices_state["pending"] = True
            try:
                devs = await run.io_bound(_enum)
            except Exception as e:
                _show_error(f"变声服务未连接——主系统未启动或服务初始化中。"
                            f"请点侧边栏「启动系统」，服务就绪后设备将自动加载"
                            f"（也可稍后点「刷新设备」重试）[{str(e)[:80]}]")
                return
            finally:
                _devices_state["pending"] = False
            _devices_state["loaded"] = True
            _clear_error()
            in_sel.options = sorted(set(devs["input"]))
            out_sel.options = sorted(set(devs["output"]))
            in_sel.update(); out_sel.update()
            empty = not in_sel.options
            no_device_label.set_text("未检测到输入设备（请检查麦克风连接）")
            no_device_label.set_visibility(empty)
            stream_btn.set_enabled(not empty or _stream_state["state"] == "running")

        def _set_btn(state: str):
            """D-4：四态 idle/starting/running/error。"""
            _stream_state["state"] = state
            if state == "starting":
                stream_btn.set_text("启动中..."); stream_btn.set_enabled(False)
            elif state == "running":
                stream_btn.set_text("停止变声"); stream_btn.set_enabled(True)
            else:  # idle / error
                stream_btn.set_text("启动变声"); stream_btn.set_enabled(True)

        def _toggle_stream():
            if _stream_state["state"] == "starting":
                return
            if _stream_state["state"] == "running":
                _set_btn("starting")

                def _stop():
                    try:
                        client.stop_stream()
                        _stream_state["error"] = None
                        _set_btn("idle"); _clear_error()
                        _rebuild_dirty.clear()
                        _update_rebuild_badge()
                    except Exception as e:
                        _set_btn("error")
                        _show_error(f"停止失败：{str(e)[:120]}")
                threading.Thread(target=_stop, daemon=True).start()
                return
            # idle / error → starting
            _set_btn("starting"); _clear_error()
            in_name = in_sel.value or get_nested_value(config, "rvc_vc", "input_device")
            out_name = out_sel.value or get_nested_value(config, "rvc_vc", "output_device")

            def _start():
                try:
                    client.start_stream(input_device=in_name, output_device=out_name)
                    _stream_state["error"] = None
                    _set_btn("running"); _clear_error()
                    _rebuild_dirty.clear(); _update_rebuild_badge()
                except Exception as e:
                    _set_btn("error")
                    _show_error(f"启动失败：{str(e)[:160]}（修正后可直接再点启动）")
            threading.Thread(target=_start, daemon=True).start()

        # ---- A-7 修复：单 timer + async 回调（io_bound 卸载）----
        model_label_cache = {"pth": None}

        async def _poll():
            try:
                st = await run.io_bound(client.status, _timeout_none := None) \
                    if False else await run.io_bound(client.status)
            except Exception:
                _devices_state["loaded"] = False  # 服务下线：下次上线重新枚举
                status_label.set_text(
                    "○ 服务未就绪——点侧边栏「启动系统」后约 10-20 秒自动就绪")
                return
            running = st.get("state") == "running"
            model = (st.get("model") or {}).get("pth", "")
            model_short = os.path.basename(model) if model else "未加载"
            delay = st.get("algo_delay_ms") or 0
            infer_t = st.get("last_infer_time_s", 0)
            last_err = st.get("last_error")
            status_label.set_text(
                f"{'● 运行中' if running else '○ 待机'} · 模型 {model_short} · "
                f"算法延迟 {delay}ms · 单块推理 {infer_t*1000:.0f}ms")
            if running and _stream_state["state"] != "running":
                _set_btn("running")
            elif not running and _stream_state["state"] == "running":
                _set_btn("idle")
            if last_err and not _stream_state.get("error_shown") == last_err:
                _stream_state["error_shown"] = last_err
                _show_error(f"服务端报告：{last_err}")
            model_label_cache["pth"] = model
            # 服务就绪自愈：设备未成功枚举过（服务晚于页面就绪/服务重启）→ 补枚举
            if not _devices_state["loaded"] and not _devices_state["pending"]:
                background_tasks.create(_refresh_devices())

        _active_timers.append(ui.timer(2.0, _poll))

    # ============ 模型段 ============
    with ui.card().style(card_css):
        ui.label("声音模型（训练产物优先；只显示 RVC 管道来源）").style(
            "font-weight:var(--font-weight-emphasis)")
        profile_models = [
            {"name": pid, "display": (p.get("name") or pid),
             "pth": p.get("rvc_model"), "index": p.get("rvc_index"),
             "source": "档案"}
            for pid, p in (profiles or {}).items() if p.get("rvc_model")
        ]
        scanned_names = {os.path.splitext(os.path.basename(m["pth"]))[0]
                         for m in profile_models}
        scan_models = [
            {**m, "display": m["name"], "source": "目录"}
            for m in models if m["name"] not in scanned_names
        ]
        if not profile_models and not scan_models:
            # D-3：空态内联指引（A-2 来源过滤下唯一合法空态）
            ui.label("暂无 RVC 模型——先在【数字人-训练声音】完成 RVC 训练，"
                     "或将 .pth+.index 放入 models/rvc/（同前缀配对即被识别）"
                     ).style("color:var(--text-secondary)")
        else:
            with ui.column().style("width:100%;gap:4px"):
                for m in profile_models + scan_models:
                    has_index = bool(m["index"])
                    # D-8：缺 index 行内后缀+tooltip（A-2：不隐藏）
                    name_text = m["display"] + ("" if has_index else "（缺 .index）")
                    with ui.row().style(
                        "width:100%;align-items:center;border:1px solid var(--border-color);"
                        "border-radius:var(--radius-md);padding:6px 10px;"
                    ):
                        lbl = ui.label(name_text).style("flex:1")
                        if not has_index:
                            lbl.tooltip("缺少同名前缀 .index 检索文件：变声可运行，"
                                        "但检索精度受限；补齐 .index 后刷新页面")
                            lbl.style("color:var(--color-warning)")
                        ui.label(f"{m['source']} · " +
                                 (".pth + .index ✔" if has_index else "缺 index ⚠")
                                 ).style("font-size:11px;color:var(--text-secondary)")
                        switch_btn = ui.button(
                            "切换",
                            on_click=lambda m=m: _do_switch(config, m),
                        ).props("dense unelevated no-caps")
                        # E-12：按钮态并入单 timer 轮询（不再每行一个 ui.timer）

        def _do_switch(model: dict):
            """D-2：/switch 事务热切换（busy 态；同 tgt_sr 零间隙，异采样率服务端
            自动重建管线毫秒缝隙）。"""
            if _switch_state["busy"]:
                ui.notify(position="top", type="warning", message="切换进行中，请稍候")
                return
            _switch_state.update({"busy": True, "error": None})

            def _work():
                try:
                    c = _client_for(config)
                    c.switch_model(model["pth"], model["index"],
                                   _as_float(get_nested_value(
                                       config, "rvc_vc", "index_rate") or 0.75))
                except Exception as e:
                    _switch_state["error"] = str(e)
                finally:
                    _switch_state["busy"] = False

            threading.Thread(target=_work, daemon=True).start()
            ui.notify(position="top", type="info",
                      message=f"正在切换到 {model['name']} ...")
            # busy 态消费并入主 timer（A-10 高亮由 status.model 驱动）
            async def _notify_done():
                while _switch_state["busy"]:
                    await run.io_bound(lambda: None)
                if _switch_state["error"]:
                    _show_error(f"切换失败（旧模型继续服务）：{_switch_state['error'][:120]}")
                else:
                    ui.notify(position="top", type="positive", message="切换成功")
            background_tasks.create(_notify_done())

    # ============ 参数段 ============
    with ui.card().style(card_css):
        # ---- 热更组（即时生效）在上（D-1）----
        with ui.expansion("实时参数（调整即生效）", value=False).style("width:100%"):
            def _slider_row(label, value, lo, hi, step, fmt, key, cast,
                            rebuild=False):
                with ui.row().style("width:100%;align-items:center;gap:8px"):
                    ui.label(label).style("min-width:150px;font-size:12px")
                    val_lab = ui.label(fmt(value)).style(
                        "min-width:44px;font-size:12px;color:var(--text-secondary)")
                    ui.slider(min=lo, max=hi, step=step, value=value,
                              on_change=lambda e, k=key, c=cast, r=rebuild, f=fmt, vl=val_lab: (
                                  vl.set_text(f(e.value)),
                                  _apply_param(k, e.value, c, rebuild_hint=r)),
                              ).props("label-always").style("flex:1")

            def _bool_row(label, key, tip):
                cb = ui.checkbox(label, value=bool(get_nested_value(config, "rvc_vc", key)),
                                 on_change=lambda e, k=key: _apply_param(k, bool(e.value)))
                if tip:
                    cb.tooltip(tip)

            _slider_row("f0 变调 pitch（半音）",
                        float(get_nested_value(config, "rvc_vc", "pitch") or 0),
                        -16, 16, 1, lambda v: f"{int(v):+d}", "pitch", int)
            _slider_row("性别因子 formant",
                        float(get_nested_value(config, "rvc_vc", "formant") or 0.0),
                        -2.0, 2.0, 0.05, lambda v: f"{v:+.2f}", "formant", float)
            _slider_row("索引率",
                        float(get_nested_value(config, "rvc_vc", "index_rate") or 0.75),
                        0.0, 1.0, 0.01, lambda v: f"{v:.2f}", "index_rate", float)
            _slider_row("保护率",
                        float(get_nested_value(config, "rvc_vc", "protect") or 0.33),
                        0.0, 1.0, 0.01, lambda v: f"{v:.2f}", "protect", float)
            _slider_row("响应阈值 threhold（dB，-60=关闭门控）",
                        float(get_nested_value(config, "rvc_vc", "threhold") or -60),
                        -60, 0, 1, lambda v: f"{int(v):d}", "threhold", int)
            _slider_row("响度混合 rms_mix_rate（0=跟随输入）",
                        float(get_nested_value(config, "rvc_vc", "rms_mix_rate") or 0.25),
                        0.0, 1.0, 0.01, lambda v: f"{v:.2f}", "rms_mix_rate", float)
            with ui.row().style("width:100%;align-items:center;gap:16px;flex-wrap:wrap"):
                _bool_row("输入降噪", "I_noise_reduce", "对麦克风输入做降噪（轻微延迟增加）")
                _bool_row("输出降噪", "O_noise_reduce", "对变声输出做降噪")
                _bool_row("相位声码器 use_pv", "use_pv", "交叉淡化改用相位声码器（更平滑，稍耗算力）")
                fm = ui.select(
                    ["pm", "harvest", "crepe", "rmvpe", "fcpe"],
                    value=get_nested_value(config, "rvc_vc", "f0method") or "rmvpe",
                    label="音高算法 f0method",
                    on_change=lambda e: _apply_param(
                        "f0method", e.value, rebuild_hint=(e.value == "harvest")),
                    ).style("min-width:140px")
                fm.tooltip("harvest 需要重启音频流后生效（进程池随启流创建）")
            ui.label("以上参数保存到 config rvc_vc 节（下次启动默认值）；服务在线时即时热更"
                     ).style("font-size:11px;color:var(--text-secondary)")

        # ---- 重建管线级组（D-7「待生效」标记）在下 ----
        with ui.expansion("性能与设备采样（改后需重启流生效）", value=False).style("width:100%"):
            badge = ui.badge("待生效", color="warning").style("margin-left:8px")
            badge.set_visibility(False)
            _rebuild_badge.append(badge)
            _slider_row("采样长度 block_time（s，越小延迟越低）",
                        float(get_nested_value(config, "rvc_vc", "block_time") or 0.25),
                        0.02, 1.5, 0.01, lambda v: f"{v:.2f}s", "block_time", float,
                        rebuild=True)
            _slider_row("淡入淡出 crossfade（s）",
                        float(get_nested_value(config, "rvc_vc", "crossfade_time") or 0.04),
                        0.01, 0.15, 0.01, lambda v: f"{v:.2f}s", "crossfade_time", float,
                        rebuild=True)
            _slider_row("额外推理时长 extra_time（s）",
                        float(get_nested_value(config, "rvc_vc", "extra_time") or 0.5),
                        0.05, 5.0, 0.05, lambda v: f"{v:.2f}s", "extra_time", float,
                        rebuild=True)
            srt = ui.toggle({"sr_model": "模型采样率", "sr_device": "设备采样率"},
                            value=get_nested_value(config, "rvc_vc", "sr_type") or "sr_model",
                            on_change=lambda e: _apply_param("sr_type", e.value,
                                                             rebuild_hint=True))
            srt.tooltip("设备采样率：以麦克风/扬声器原始采样率开流（E-9 双路）")
            _slider_row("harvest 进程数 n_cpu",
                        float(get_nested_value(config, "rvc_vc", "n_cpu") or 4),
                        1, 8, 1, lambda v: f"{int(v)}", "n_cpu", int, rebuild=True)
            ui.label("本组参数写入后显示「待生效」——停止并重新启动变声后生效"
                     ).style("font-size:11px;color:var(--text-secondary)")

    # 首次设备枚举（页面尾部：全部控件已就位）
    try:
        background_tasks.create(_refresh_devices())
    except Exception as e:
        logger.warning(f"[svc] 首次设备枚举失败: {e}")
