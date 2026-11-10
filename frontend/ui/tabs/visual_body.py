# -*- coding: utf-8 -*-
"""
画面设置标签页模块（EDTalk功能集成计划 §4.1，原"虚拟身体"页重做）

三卡顺序 = 摄像头/虚拟设备 → 数字人基础配置 → 推理配置（默认折叠）。
- 摄像头区：UnityCapture 虚拟摄像头安装/检测（安装=helpers.install_unity_capture，
  与启动链自动安装共用；F1 修复：原本地实现引用未导入的 get_base_path → NameError）
- 数字人基础配置：enable + 摄像头输出参数（target_fps/buffer/preroll）；
  角色素材目录已退役（2026-10-31 画面设置批次：素材增删/清单由『动作编排』页
  接管，目录为内部量默认 wy——materials.resolve_character_dir 兜底）；
  端口与解释器为系统内部量（runtime.py 自动解析+记忆，端口被占自动切换）；
  段播放调度已并入『动作编排』页（启动固定 --segments auto）
- 推理配置：修复/视线(+收敛角)/口型统一卡片——应用=热推当前服务（立即生效）
  + 写回 config（下次启动初值）；口型为纯运行中开关（画外音）；手动切段入口

动作编排规则管理与素材管理在「动作编排」二级页（choreography.py）；
本页仅保留编排互锁状态源（F7 手动切段确认弹窗）。
"""
import asyncio
import os

from nicegui import ui
from typing import Any, Callable, Dict

from frontend.ui.components import FormField
from frontend.ui.components.config_helper import get_nested_value
from frontend.config.paths import get_base_path
from utils.edtalk_realtime.helpers import (
    install_unity_capture,
    query_unity_capture,
)


def _int_or(v, default: int) -> int:
    """安全整数解析（2026-10-31 画面设置批次 F3：原裸整数转换遇非数字输入
    抛 ValueError 中断 on_change，值不落盘且 UI 报错）。"""
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return default


def create_visual_body_tab(
    config: Dict[str, Any],
    theme_config: Dict[str, str],
    set_config_callback: Callable,
):
    """创建画面设置标签页。"""
    card_css = theme_config.get("card", "")

    # ================= 卡片 1：驱动类型 =================
    with ui.card().style(card_css):
        ui.label("驱动类型")
        driver_select = FormField.create_select(
            label='数字人驱动',
            # 2026-10-22 批次（G2）：metahuman_stream 驱动退役移除——单驱动 edtalk_realtime
            options={'edtalk_realtime': 'EDTalk 实时推理（推荐）'},
            value=get_nested_value(config, "visual_body", default="edtalk_realtime"),
            on_change=lambda e: set_config_callback("visual_body", value=e.value),
            style="width:100%;",
        ).tooltip("EDTalk 实时推理 = 本地 TTS + 音频推送 + 虚拟摄像头帧锁定播放"
                  "（metahuman_stream 驱动已随 2026-10-22 批次退役）")

        # 驱动类型 × enable 组合提示（常驻区）
        combo_warning = ui.label("").style("color: var(--color-warning); white-space: pre-wrap")

        def _update_combo_warning():
            body = get_nested_value(config, "visual_body", default="edtalk_realtime")
            ed_enable = bool(get_nested_value(config, "edtalk_realtime", "enable", default=False))
            msgs = []
            if body == "edtalk_realtime" and not ed_enable:
                msgs.append("EDTalk 服务未启用（enable 关闭）：运行系统不会拉起数字人，"
                            "声音走本地播放、无数字人画面与口型")
            combo_warning.set_text("\n".join(msgs) if msgs else "")

        _update_combo_warning()

    # ================= 虚拟摄像头检测卡（义务块：检测=启动系统时触发+页面打开刷新；
    #                  缺失态警示色最高视觉权重；安装=helpers 共享函数，启动链同源） =================

    _cam_installed, _cam_name = query_unity_capture()

    with ui.card().style(card_css):
        if _cam_installed:
            with ui.row().style("width:100%;align-items:center;gap:8px"):
                ui.element("span").style("width:8px;height:8px;border-radius:50%;background:var(--color-success)")
                ui.label("虚拟摄像头已安装：" + _cam_name).style("font-size: var(--font-size-body)")
            cam_status_label = ui.label("").style("color: var(--text-hint)")
        else:
            ui.label("⚠ 未检测到 UnityCapture 虚拟摄像头——数字人画面输出依赖它，请先安装").style(
                "width:100%;padding:6px 10px;border-radius:6px;"
                "background:var(--color-danger-strong);color:var(--text-on-primary);font-size: var(--font-size-body)")
            cam_name_input = FormField.create_input(
                label="摄像头名称（中英文字母/数字/空格/下划线/短横线，1-64 位；OBS 里按此名选择）",
                value="AUTOlive 数字人",
                style="width:100%;",
            )
            cam_status_label = ui.label("").style("color: var(--text-hint)")

            async def _do_install():
                name = (cam_name_input.value or "").strip()
                # 防重入（审查 FINDING-012）：安装期间禁用两按钮，防连点叠发
                # 多个 UAC 提权流程；结束（含失败路径）finally 恢复
                install_btn.set_enabled(False)
                recheck_btn.set_enabled(False)
                try:
                    cam_status_label.set_text("安装中……（请在弹出的 UAC 窗口确认）")
                    cam_status_label.style("color: var(--color-warning)")
                    try:
                        await asyncio.to_thread(
                            install_unity_capture, name, str(get_base_path()))
                    except Exception as e:
                        cam_status_label.set_text(str(e))
                        cam_status_label.style("color: var(--color-danger)")
                        ui.notify(position="top", type="negative", message="安装失败：" + str(e))
                        return
                    ok, got = query_unity_capture()
                    if ok:
                        cam_status_label.set_text(
                            "安装完成：" + got + "。如消费端未立即出现设备，请重启系统后重开本页（驱动签名/需重启场景）")
                        cam_status_label.style("color: var(--color-positive)")
                        ui.notify(position="top", type="positive",
                                  message="虚拟摄像头 " + got + " 安装完成；若设备未出现请重启后重试")
                    else:
                        # "已安装未枚举"分支（DX F-6）
                        cam_status_label.set_text(
                            "problem=安装命令已执行但未检测到设备；cause=可能需重启或被安全软件拦截；"
                            "fix=重启系统后重开本页，或手动运行 InstallCustomName.bat")
                        cam_status_label.style("color: var(--color-warning)")
                finally:
                    install_btn.set_enabled(True)
                    recheck_btn.set_enabled(True)

            async def _do_recheck():
                """就地重查注册表并刷新状态行（design 审查 FINDING-002：
                原实现 ui.navigate.reload() 整页刷新会把用户踢回默认页）。"""
                ok, got = await asyncio.to_thread(query_unity_capture)
                if ok:
                    cam_status_label.set_text("检测到已安装：" + got)
                    cam_status_label.style("color: var(--color-positive)")
                else:
                    cam_status_label.set_text(
                        "problem=仍未检测到 UnityCapture 注册项；cause=尚未安装或被安全软件拦截；"
                        "fix=点击『安装虚拟摄像头』，或手动运行 InstallCustomName.bat")
                    cam_status_label.style("color: var(--color-warning)")

            # 页面唯一前进动作=实心主按钮（审查 FINDING-014：原 outline 权重
            # 不足——摄像头缺失态下安装是整页的关键下一步）
            install_btn = ui.button("安装虚拟摄像头", on_click=_do_install)
            recheck_btn = ui.button("重新检测", on_click=_do_recheck).props("flat")
        ui.label("单读者限制：UnityCapture 为 DirectShow 滤镜，OBS 与 realtime_serve "
                 "不可同时读取同一设备；重装/改名=重新填写名称点击安装（覆盖注册）").style(
            "color: var(--text-hint); font-size: var(--font-size-body)")

    # ================= 卡片 2：数字人基础配置区 =================
    # 2026-10-26 配置面收敛：端口/解释器/段调度改为系统内部量——
    #   端口：拉起时自动择址（被占自动切换），实际端口写入 data/edtalk_runtime.json，
    #         消费方经 utils/edtalk_realtime/runtime.resolve_api_base 统一解析；
    #   解释器：自动探测+记忆（runtime.resolve_interpreter）；
    #   段调度：与『动作编排』重合，画面接管统一走编排页（启动固定 --segments auto）。
    # 推理参数（修复/视线/口型）整合至卡片 3『推理配置』（热切+保存初值）。
    with ui.card().style(card_css):
        ui.label("数字人基础配置")

        enable_switch = ui.switch(
            "启用数字人服务（开启后随系统启动）",
            value=bool(get_nested_value(config, "edtalk_realtime", "enable", default=False)),
            on_change=lambda e: (set_config_callback("edtalk_realtime", "enable", value=e.value),
                                 _update_combo_warning()),
        ).tooltip("开启后『运行系统』时自动拉起 realtime_serve；端口与解释器自动解析，无需配置")

        # 数字人角色选择（2026-11-10 tuber 角色库改造）：**只显示角色库
        # （tuber/）中已有的角色子目录**——训练部署会自动建包入库；选择后
        # 写 character_dir（=当前启用角色）并在服务运行中自动重启加载。
        def _role_options() -> dict:
            from utils.digital_human_models import tuber_root
            cur = str(get_nested_value(config, "edtalk_realtime", "character_dir",
                                       default="wy") or "wy")
            opts = {cur: f"{cur}（当前）"}
            tb = tuber_root()
            for d in (sorted(os.listdir(tb)) if os.path.isdir(tb) else []):
                if os.path.isdir(os.path.join(tb, d)):
                    opts.setdefault(d, d)
            return opts

        _role_switching = {"busy": False}

        async def _switch_role(e):
            role = str(e.value or "").strip()
            if not role or _role_switching["busy"]:
                return
            from utils.digital_human_models import validate_switch
            err = validate_switch(config, role)
            if err:
                ui.notify(position="top", type="negative", message=err)
                role_select.value = str(get_nested_value(
                    config, "edtalk_realtime", "character_dir", default="wy") or "wy")
                return
            set_config_callback("edtalk_realtime", "character_dir", value=role,
                                _notify=False)
            from utils.edtalk_realtime.materials import service_status
            online = service_status(config) is not None
            if not online:
                ui.notify(position="top", type="positive",
                          message=f"已选择角色 {role}——动作编排将使用 tuber/{role} "
                                  "（服务未运行，下次启动生效）")
                return
            # 服务运行中：自动重启加载新角色（模型+编排片段都随角色切换）
            from frontend.main import AutoliveApp
            app = AutoliveApp.APP_HOLDER.get("app")
            if app is None:
                ui.notify(position="top", type="warning",
                          message=f"已选择角色 {role}——请重启主系统生效（服务管理器不可达）")
                return
            _role_switching["busy"] = True
            ui.notify(position="top", type="info", timeout=None,
                      message=f"正在按角色 {role} 重启数字人服务（模型+画面加载约 20-40 秒）……")
            try:
                await asyncio.to_thread(app.stop_program, "edtalk_realtime")
                ok, reason, fix_hint = await asyncio.to_thread(
                    app._start_edtalk_realtime, config)
                if ok:
                    ui.notify(position="top", type="positive",
                              message=f"数字人服务已按角色 {role} 重启完成——"
                                      "动作编排已切换到该角色的素材")
                else:
                    ui.notify(position="top", type="negative", timeout=10,
                              message=f"数字人服务重启失败：{reason}" +
                                      (f"｜{fix_hint}" if fix_hint else ""))
            finally:
                _role_switching["busy"] = False

        role_select = ui.select(
            label="数字人角色（微调模型）",
            options=_role_options(),
            value=str(get_nested_value(config, "edtalk_realtime", "character_dir",
                                       default="wy") or "wy"),
            on_change=_switch_role,
        ).style("width:100%;").tooltip(
            "以角色名为单位切换数字人模型（训练主播流程的微调结果）；"
            "服务运行中将自动重启加载，画面会中断约 20-40 秒").classes("w-full")

        with ui.grid(columns=3):
            # 角色素材目录已退役（2026-10-31 画面设置批次）：素材增删/清单由
            # 『动作编排』页接管，目录为内部量（默认 wy，materials.resolve_character_dir
            # 与启动参数同源兜底）——UI 配置项移除，存量键经 _RETIRED_CONFIG_KEYS 告知
            FormField.create_input(
                label='目标帧率',
                value=str(get_nested_value(config, "edtalk_realtime", "target_fps", default=25)),
                on_change=lambda e: set_config_callback(
                    "edtalk_realtime", "target_fps", value=_int_or(e.value, 25)),
                tooltip="数字人服务器输出帧率",
                style="width:100%;",
            )
            FormField.create_input(
                label='缓冲帧数',
                value=str(get_nested_value(config, "edtalk_realtime", "buffer_frames", default=16)),
                on_change=lambda e: set_config_callback(
                    "edtalk_realtime", "buffer_frames", value=_int_or(e.value, 16)),
                tooltip="输出缓冲容量，让画面更流畅；每帧约 11MB@1440×2560",
                style="width:100%;",
            )
            FormField.create_input(
                label='预填充帧数',
                value=str(get_nested_value(config, "edtalk_realtime", "preroll_frames", default=8)),
                on_change=lambda e: set_config_callback(
                    "edtalk_realtime", "preroll_frames", value=_int_or(e.value, 8)),
                tooltip="启动预填充帧数，须 ≤ 缓冲帧数（越界由启动链自动修正）",
                style="width:100%;",
            )

    # ================= 卡片 3：推理配置（热切 + 保存初值二合一） =================
    # 2026-10-26 合并：原卡 2『推理组件（启动初值）』与原卡 3『运行时控制（热切）』
    # 重合配置整合为一处——应用=热推当前服务（立即生效）+ 写回 config（下次启动初值）。
    # 口型推理无启动参数概念，纯热切（画外音总开关，不落 config）。
    with ui.card().style(card_css):
        control_expansion = ui.expansion(
            "推理配置（立即生效；同步保存为下次启动初值）").style("width:100%")
        with control_expansion:
            status_label = ui.label("服务未运行").style("color: var(--text-hint)")
            push_health_label = ui.label("").style("color: var(--text-hint)")

            with ui.grid(columns=4):
                repair_select = FormField.create_select(
                    label='面部修复',
                    options={'adaptive': 'adaptive（推荐）', 'on': 'on', 'off': 'off'},
                    value=get_nested_value(config, "edtalk_realtime", "face_repair", default="adaptive"),
                    style="width:100%;",
                ).tooltip("off 启动的服务热切不可用（409）——需重启服务开启")
                gaze_select = FormField.create_select(
                    label='视线纠正',
                    options={'adaptive': 'adaptive（推荐）', 'on': 'on', 'off': 'off'},
                    value=get_nested_value(config, "edtalk_realtime", "gaze_correction", default="adaptive"),
                    style="width:100%;",
                ).tooltip("off 启动的服务热切不可用（409）——需重启服务开启")
                gaze_conv_input = FormField.create_input(
                    label='视线收敛角',
                    value=str(get_nested_value(config, "edtalk_realtime", "gaze_convergence", default=0.05)),
                    style="width:100%;",
                ).tooltip("[-1,1]；随『应用』热推+保存；素材人物坐姿/朝向不同可调，负值发散")
                lip_select = FormField.create_select(
                    label='口型推理',
                    options={'on': 'on（正常口型）', 'off': 'off（画外音）'},
                    value="on",
                    style="width:100%;",
                ).tooltip("off=跳过口型推理（省 GPU），画面保持素材原样，语音照常播放"
                          "（画外音）；无人脸段自动直通，任意时刻可双向热切（下一帧生效）；"
                          "纯运行中开关，不保存初值")
            with ui.row():
                ui.button("应用（热推+保存初值）", on_click=lambda: _apply_inference())
                ui.button("刷新状态", on_click=lambda: _poll_status_once()).props("flat")
            with ui.row():
                segment_switch_input = FormField.create_input(
                    label='手动切换画面段（段名，与编排互斥）', value="", style="width:40%;")
                ui.button(
                    "立即切换",
                    on_click=lambda: _switch_segment(segment_switch_input.value),
                ).props("outline")

    # ================= 编排互锁状态源（规则管理已迁「动作编排」二级页） =================
    # F7：手动切段确认弹窗需要编排 active 标志；只拉 /choreography/status 填缓存，无 UI。
    _choreo_status_cache = {}

    def _main_api_url() -> str:
        api_port = get_nested_value(config, "api_port", default=8082)
        api_ip = get_nested_value(config, "api_ip", default="127.0.0.1")
        if str(api_ip) == "0.0.0.0":
            api_ip = "127.0.0.1"
        return f"http://{api_ip}:{api_port}"

    async def _poll_choreo_active():
        try:
            import requests
            resp = await asyncio.to_thread(
                requests.get, f"{_main_api_url()}/choreography/status", timeout=2)
            st = (resp.json() or {}) if resp.status_code == 200 else {}
        except Exception:
            st = {}
        _choreo_status_cache["active"] = bool(
            st.get("enable") and st.get("ready") and st.get("state") is not None)

    ui.timer(5.0, _poll_choreo_active)

    # （2026-10-22 批次 G2）卡片 4：metahuman_stream 参数卡已随驱动退役移除——
    # 存量 metahuman_stream 配置节由 frontend/config/settings.py 归一化为 edtalk_realtime

    # ================= 交互函数 =================

    def _edtalk_api() -> str:
        from utils.edtalk_realtime.runtime import resolve_api_base
        return resolve_api_base(config)

    def _auth_headers() -> dict:
        token = (get_nested_value(config, "edtalk_realtime", "api_token", default="") or "").strip()
        return {"Authorization": f"Bearer {token}"} if token else {}

    async def _apply_inference():
        """推理参数统一应用（卡 3 合并后唯一入口）：
        1) 热推当前服务（repair/gaze+收敛角/lip 三路，下一帧生效；服务未运行则跳过）；
        2) 保存初值（face_repair/gaze_correction/gaze_convergence 写回 config，
           下次启动生效；口型为纯运行中开关，不落 config）。"""
        # 1) 保存初值（先落盘——即使服务未运行，用户意图也已持久化）
        set_config_callback("edtalk_realtime", "face_repair", value=repair_select.value)
        set_config_callback("edtalk_realtime", "gaze_correction", value=gaze_select.value)
        try:
            conv = float(gaze_conv_input.value or 0.05)
        except (TypeError, ValueError):
            conv = 0.05
            gaze_conv_input.set_value("0.05")
        set_config_callback("edtalk_realtime", "gaze_convergence", value=conv)

        # 2) 热推（to_thread 异步——同步 requests 会阻塞 UI 线程）
        results = []

        async def _push(path: str, payload: dict):
            try:
                import requests
                resp = await asyncio.to_thread(
                    requests.post, f"{_edtalk_api()}{path}",
                    json=payload, headers=_auth_headers(), timeout=3)
                return resp.status_code
            except Exception:
                return None  # 服务未连接（未运行）——非错误态

        repair_code = await _push("/repair/params", {"mode": repair_select.value})
        gaze_code = await _push("/gaze/params",
                                {"mode": gaze_select.value, "convergence": conv})
        lip_code = await _push("/lip/params", {"mode": lip_select.value})

        if repair_code is None and gaze_code is None and lip_code is None:
            ui.notify(position="top", type="positive",
                      message="初值已保存（服务未运行，下次启动生效）")
            return
        if repair_code == 200:
            results.append("修复")
        elif repair_code == 409:
            results.append("修复✗409")
        if gaze_code == 200:
            results.append("视线")
        elif gaze_code == 409:
            results.append("视线✗409")
        if lip_code == 200:
            results.append("口型")
        ok = [r for r in results if "✗" not in r]
        bad = [r for r in results if "✗" in r]
        msg = f"已生效：{'、'.join(ok) if ok else '无'}（下一帧）；初值已保存"
        if bad:
            msg += f"；{('、'.join(bad))} 启动时为 off，热切不可用——需重启服务开启"
            ui.notify(position="top", type="warning", message=msg)
        else:
            ui.notify(position="top", type="positive", message=msg)

    async def _switch_segment(segment: str):
        if not segment:
            ui.notify(position="top", type="warning", message="请输入段名")
            return
        # F7 机械互锁（/autoplan 20261021 批次 P2）：编排启用中手动切段会覆盖/
        # 清除编排 pending（单槽 last-write-wins）导致序列丢步——确认弹窗保留逃生口
        if _choreo_status_cache.get("active"):
            with ui.dialog() as confirm_dlg, ui.card().style(card_css):
                ui.label("动作编排已启用且服务就绪：手动切段会覆盖编排队列的待执行切换"
                         "（单槽 last-write-wins），可能导致编排序列丢步。仍要继续？").style(
                    "white-space: pre-wrap; max-width: 420px")
                with ui.row():
                    ui.button("仍要切换", on_click=lambda: confirm_dlg.submit(True))
                    ui.button("取消", on_click=lambda: confirm_dlg.submit(False)).props("flat")
            confirmed = await confirm_dlg
            if not confirmed:
                return
        try:
            import requests
            resp = await asyncio.to_thread(
                requests.post, f"{_edtalk_api()}/segment/switch",
                json={"segment": segment, "at": "segment_end"},
                headers=_auth_headers(), timeout=5,
            )
            if resp.status_code == 200:
                ui.notify(position="top", type="positive", message=f"切段已入队：{segment}（边界无缝）")
            elif resp.status_code == 404:
                ui.notify(position="top", type="negative", message=f"段不存在：{segment}（大小写敏感）")
            else:
                ui.notify(position="top", type="negative", message=f"失败 HTTP {resp.status_code}")
        except Exception as e:
            ui.notify(position="top", type="negative", message=f"服务未连接：{e}")

    async def _poll_status_once():
        """刷新 /status 回显与推送健康度（5s 轮询与手动刷新共用）。"""
        api_port = get_nested_value(config, "api_port", default=8082)
        api_ip = get_nested_value(config, "api_ip", default="127.0.0.1")
        if str(api_ip) == "0.0.0.0":
            api_ip = "127.0.0.1"
        # 1) EDTalk /status（webui 直连）
        try:
            import requests
            resp = await asyncio.to_thread(
                lambda: __import__("requests").get(f"{_edtalk_api()}/status", headers=_auth_headers(), timeout=2)
            )
            status = resp.json() if resp.status_code == 200 else {}
        except Exception:
            status = {}
        if status:
            preroll = status.get("preroll_state", "?")
            seg = status.get("current_segment", "?")
            fps = status.get("production_fps", "?")
            underruns = status.get("underruns", "?")
            res = status.get("camera_resolution", "?")
            status_label.set_text(
                f"运行中 | 段:{seg} | preroll:{preroll} | 生产帧率:{fps} | "
                f"欠载:{underruns} | 分辨率:{res}"
            ).style("color: var(--color-positive)")
            control_expansion.value = True
            # 口型门控回显（无 capable 概念：服务端任意时刻可双向热切）
            lip_mode = status.get("lip_mode")
            if lip_mode in ("on", "off"):
                lip_select.set_value(lip_mode)
            # capable 探测（义务块"推理热切 UI"：false 时禁用对应控件并提示——
            # off 启动的组件未构造，热切 409）
            for sel, cap_key, cap_label in (
                (gaze_select, "gaze_capable", "视线纠正"),
                (repair_select, "repair_capable", "面部修复"),
            ):
                capable = status.get(cap_key)
                if capable is False:
                    sel.set_enabled(False)
                    sel.set_value(None)
                    sel.props(f'label="{cap_label}热切（服务未启用该组件——off 启动无法热开）"')
                else:
                    sel.set_enabled(True)
        else:
            status_label.set_text("服务未运行").style("color: var(--text-hint)")
        # 2) 推送健康度（经 main.py API 代理——推送发生在运行时进程）
        try:
            import requests
            resp = await asyncio.to_thread(
                lambda: __import__("requests").get(f"http://{api_ip}:{api_port}/edtalk_status", timeout=2)
            )
            data = (resp.json() or {}).get("data", {}) if resp.status_code == 200 else {}
            if data.get("available"):
                fc = data.get("failure_count", 0)
                if fc == 0:
                    push_health_label.set_text("EDTalk 推送：正常").style("color: var(--color-positive)")
                else:
                    push_health_label.set_text(
                        f"EDTalk 推送：最近 {fc} 次失败（{data.get('last_error', '')[:60]}）"
                    ).style("color: var(--color-negative)")
            else:
                push_health_label.set_text("EDTalk 推送：未知（音频模块未初始化）").style("color: var(--text-hint)")
        except Exception:
            push_health_label.set_text("EDTalk 推送：未知（直播运行时未启动）").style("color: var(--text-hint)")

    # 5 秒轮询（仅当 edtalk 被选中时活动；native F14：失败不转全局离线，仅本页显示）
    ui.timer(5.0, _poll_status_once)
    # （2026-10-26 修复迁移遗留）原 `ui.timer(5.0, _poll_choreo_status)` 引用
    # 编排卡迁移后已不存在的函数（页面渲染 NameError）——F7 互锁状态源由
    # 上方 _poll_choreo_active 定时器承担，状态条轮询在『动作编排』页。
