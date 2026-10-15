# -*- coding: utf-8 -*-
"""
画面设置标签页模块（EDTalk功能集成计划 §4.1，原"虚拟身体"页重做）

三区顺序 = 驱动类型 → EDTalk 配置区 → 运行控制区（默认折叠，服务运行时展开）。
- 驱动类型：metahuman_stream / edtalk_realtime 二选一（互斥）
- EDTalk 配置区：config.edtalk_realtime 全参数（port/interpreter_path 可覆盖）
- 运行控制区：直调 HTTP 不落 config（gaze/repair 热切、段调度、/status 回显）
- metahuman_stream 参数区保留（edtalk 选中时收起为折叠面板）

设计义务：运行控制区独立卡片+"立即生效"标注+5s 轮询+手动刷新+空态；
按钮状态矩阵（驱动类型×服务状态 2×4）；驱动类型×enable 四组合提示；
推送健康度可见面（经 main.py /edtalk_status）。
"""
import asyncio

from nicegui import ui
from typing import Any, Callable, Dict

from frontend.ui.components import FormField
from frontend.ui.components.config_helper import get_nested_value
from utils.my_log import logger
from utils.edtalk_realtime.helpers import is_edtalk_active, is_edtalk_selected


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
            options={'edtalk_realtime': 'EDTalk 实时推理（推荐）', 'metahuman_stream': 'metahuman_stream'},
            value=get_nested_value(config, "visual_body", default="edtalk_realtime"),
            on_change=lambda e: set_config_callback("visual_body", value=e.value),
            style="width:100%;",
        ).tooltip("二选一互斥：EDTalk 实时推理 = 本地 TTS + 音频推送 + 虚拟摄像头帧锁定播放；"
                  "metahuman_stream = 文本 echo 由对端托管")

        # 驱动类型 × enable 四组合提示（常驻区）
        combo_warning = ui.label("").style("color: var(--color-warning); white-space: pre-wrap")

        def _update_combo_warning():
            body = get_nested_value(config, "visual_body", default="metahuman_stream")
            ed_enable = bool(get_nested_value(config, "edtalk_realtime", "enable", default=False))
            fastrag_enable = bool(get_nested_value(config, "fastrag", "enable", default=False))
            msgs = []
            if body == "edtalk_realtime" and not ed_enable:
                msgs.append("EDTalk 服务未启用（enable 关闭）：运行系统不会拉起数字人，"
                            "声音走本地播放、无数字人画面与口型")
            if body == "metahuman_stream" and ed_enable:
                msgs.append("edtalk_realtime.enable 已开但当前驱动为 metahuman_stream——该开关不会生效")
            if body == "metahuman_stream" and fastrag_enable:
                msgs.append("fastrag server 会随系统启动，但 metahuman 驱动不使用该服务（仅 custom_llm 指向 /query 时生效）")
            combo_warning.set_text("\n".join(msgs) if msgs else "")

        _update_combo_warning()

    # ================= 虚拟摄像头检测卡（义务块：检测=启动系统时触发+页面打开刷新；
    #                  缺失态警示色最高视觉权重；安装编程化 regsvr32 静默+runas 提权） =================

    import winreg as _winreg
    import re as _re_cam

    def _query_unity_capture():
        r"""检测 UnityCapture 安装状态（其注册机制：DllInstall 写
        HKCU\Software\UnityCapture\FilterName）。返回 (installed: bool, name: str)。"""
        try:
            with _winreg.OpenKey(_winreg.HKEY_CURRENT_USER, r"Software\UnityCapture") as key:
                name, _ = _winreg.QueryValueEx(key, "FilterName")
                return True, str(name)
        except OSError:
            return False, ""

    def _install_unity_capture(cam_name: str):
        """编程化安装（义务块：regsvr32 静默注入名称+runas 提权；Eng F-E4：
        名称白名单拒绝全部 shell 元字符+参数列表方式）。同步阻塞（线程池调用）。"""
        if not _re_cam.match(r"^[A-Za-z0-9 _-]{1,64}$", cam_name or ""):
            raise ValueError("problem=摄像头名含非法字符；cause=仅允许字母/数字/空格/下划线/短横线（1-64 位）；fix=改用合法名称")
        import os as _os
        dll = _os.path.join(str(get_base_path()), "edtalk", "UnityCapture-master", "Install", "UnityCaptureFilter64.dll")
        if not _os.path.isfile(dll):
            raise FileNotFoundError("problem=安装件缺失；cause=" + dll + "；fix=运行 Scripts/copy_edtalk.py 补复制")
        import subprocess as _sp
        # 参数列表传值（无 shell 拼接）；UAC 提权经 PowerShell -Verb RunAs（用户在场确认）
        ps_cmd = "Start-Process regsvr32 -ArgumentList '/s','" + dll + "','/i:UnityCaptureName=" + cam_name + "' -Verb RunAs -Wait"
        r = _sp.run(["powershell", "-NoProfile", "-Command", ps_cmd], capture_output=True, timeout=120, text=True)
        if r.returncode != 0:
            raise RuntimeError("problem=安装命令失败；cause=" + (r.stderr or "").strip()[:200] + "；fix=查看日志或手动运行 InstallCustomName.bat")

    _cam_installed, _cam_name = _query_unity_capture()

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
                label="摄像头名称（字母/数字/空格/下划线/短横线，1-64 位；OBS 里按此名选择）",
                value="AUTOlive 数字人",
                style="width:100%;",
            )
            cam_status_label = ui.label("").style("color: var(--text-hint)")

            async def _do_install():
                name = (cam_name_input.value or "").strip()
                cam_status_label.set_text("安装中……（请在弹出的 UAC 窗口确认）")
                cam_status_label.style("color: var(--color-warning)")
                try:
                    await asyncio.to_thread(_install_unity_capture, name)
                except Exception as e:
                    cam_status_label.set_text(str(e))
                    cam_status_label.style("color: var(--color-danger)")
                    ui.notify(position="top", type="negative", message="安装失败：" + str(e))
                    return
                ok, got = _query_unity_capture()
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

            async def _do_recheck():
                """就地重查注册表并刷新状态行（design 审查 FINDING-002：
                原实现 ui.navigate.reload() 整页刷新会把用户踢回默认页）。"""
                ok, got = await asyncio.to_thread(_query_unity_capture)
                if ok:
                    cam_status_label.set_text("检测到已安装：" + got)
                    cam_status_label.style("color: var(--color-positive)")
                else:
                    cam_status_label.set_text(
                        "problem=仍未检测到 UnityCapture 注册项；cause=尚未安装或被安全软件拦截；"
                        "fix=点击『安装虚拟摄像头』，或手动运行 InstallCustomName.bat")
                    cam_status_label.style("color: var(--color-warning)")

            ui.button("安装虚拟摄像头", on_click=_do_install).props("outline")
            ui.button("重新检测", on_click=_do_recheck).props("flat")
        ui.label("单读者限制：UnityCapture 为 DirectShow 滤镜，OBS 与 realtime_serve "
                 "不可同时读取同一设备；重装/改名=重新填写名称点击安装（覆盖注册）").style(
            "color: var(--text-hint); font-size: var(--font-size-body)")

    # ================= 卡片 2：EDTalk 实时推理配置区 =================
    with ui.card().style(card_css):
        ui.label("EDTalk 实时推理配置")

        enable_switch = ui.switch(
            "启用数字人服务（开启后随系统启动）",
            value=bool(get_nested_value(config, "edtalk_realtime", "enable", default=False)),
            on_change=lambda e: (set_config_callback("edtalk_realtime", "enable", value=e.value),
                                 _update_combo_warning()),
        ).tooltip("开启后『运行系统』时自动拉起 realtime_serve；运行中切换只影响下次运行系统")

        with ui.grid(columns=3):
            FormField.create_input(
                label='服务端口（三处统一引用）',
                value=str(get_nested_value(config, "edtalk_realtime", "port", default=8000)),
                on_change=lambda e: (
                    set_config_callback("edtalk_realtime", "port", value=int(e.value or 8000)),
                    set_config_callback("edtalk_realtime", "api_ip_port",
                                        value=f"http://127.0.0.1:{int(e.value or 8000)}"),
                ),
                tooltip="服务端启动参数、客户端 api_ip_port、端口幂等探测三处统一引用此键",
                style="width:100%;",
            )
            FormField.create_input(
                label='角色素材目录',
                value=get_nested_value(config, "edtalk_realtime", "character_dir", default="wy"),
                on_change=lambda e: set_config_callback("edtalk_realtime", "character_dir", value=e.value),
                tooltip="realtime_serve 的 --character-dir（orifra/ 素材所在目录名，大小写敏感）",
                style="width:100%;",
            )
            FormField.create_input(
                label='目标帧率',
                value=str(get_nested_value(config, "edtalk_realtime", "target_fps", default=25)),
                on_change=lambda e: set_config_callback("edtalk_realtime", "target_fps", value=int(e.value or 25)),
                tooltip="仅约束摄像头输出帧率",
                style="width:100%;",
            )
            FormField.create_input(
                label='缓冲帧数',
                value=str(get_nested_value(config, "edtalk_realtime", "buffer_frames", default=16)),
                on_change=lambda e: set_config_callback("edtalk_realtime", "buffer_frames", value=int(e.value or 16)),
                tooltip="输出缓冲容量；每帧约 11MB@1440×2560",
                style="width:100%;",
            )
            FormField.create_input(
                label='预填充帧数',
                value=str(get_nested_value(config, "edtalk_realtime", "preroll_frames", default=8)),
                on_change=lambda e: set_config_callback("edtalk_realtime", "preroll_frames", value=int(e.value or 8)),
                tooltip="启动预填充帧数，须 ≤ 缓冲帧数",
                style="width:100%;",
            )
            FormField.create_select(
                label='面部修复',
                options={'adaptive': 'adaptive（推荐）', 'on': 'on', 'off': 'off'},
                value=get_nested_value(config, "edtalk_realtime", "face_repair", default="adaptive"),
                on_change=lambda e: set_config_callback("edtalk_realtime", "face_repair", value=e.value),
                tooltip="off 启动后热切不可用（409）",
                style="width:100%;",
            )
            FormField.create_select(
                label='视线纠正',
                options={'adaptive': 'adaptive（推荐）', 'on': 'on', 'off': 'off'},
                value=get_nested_value(config, "edtalk_realtime", "gaze_correction", default="adaptive"),
                on_change=lambda e: set_config_callback("edtalk_realtime", "gaze_correction", value=e.value),
                tooltip="off 启动后热切不可用（409）",
                style="width:100%;",
            )
            FormField.create_input(
                label='视线收敛角',
                value=str(get_nested_value(config, "edtalk_realtime", "gaze_convergence", default=0.05)),
                on_change=lambda e: set_config_callback("edtalk_realtime", "gaze_convergence", value=float(e.value or 0.05)),
                tooltip="[-1,1]；素材人物坐姿/朝向不同可调，负值发散",
                style="width:100%;",
            )
            FormField.create_select(
                label='弹幕触发切段',
                options={'true': '开', 'false': '关'},
                value=str(get_nested_value(config, "edtalk_realtime", "reaction_segment", default=True)).lower(),
                on_change=lambda e: set_config_callback("edtalk_realtime", "reaction_segment", value=e.value == "true"),
                tooltip="布尔开关：直播回复后自动切换表情段（segment_end 无缝）",
                style="width:100%;",
            )
            FormField.create_input(
                label='绑定地址 host',
                value=get_nested_value(config, "edtalk_realtime", "host", default="127.0.0.1"),
                on_change=lambda e: set_config_callback("edtalk_realtime", "host", value=e.value),
                tooltip="默认回环（安全）；远程消费需 0.0.0.0 并配 api_token",
                style="width:100%;",
            )
            FormField.create_input(
                label='API Token（可选）',
                value=get_nested_value(config, "edtalk_realtime", "api_token", default=""),
                on_change=lambda e: set_config_callback("edtalk_realtime", "api_token", value=e.value),
                tooltip="设置后全路由要求 Bearer 鉴权",
                style="width:100%;",
            )
            FormField.create_input(
                label='解释器路径',
                value=get_nested_value(config, "edtalk_realtime", "interpreter_path",
                                       default="D:\\AI\\EDTalk\\runtime312\\python.exe"),
                on_change=lambda e: set_config_callback("edtalk_realtime", "interpreter_path", value=e.value),
                tooltip="runtime312 解释器；拉起/训练前校验存在性",
                style="width:100%;",
            )

        # segments：多行文本（一行一个段名）或 auto
        segments_mode = get_nested_value(config, "edtalk_realtime", "segments", default="auto")
        segments_value = "" if segments_mode == "auto" else str(segments_mode)
        with ui.row().style("width:100%; align-items:flex-start; gap:16px"):
            segments_area = ui.textarea(
                label='段播放调度（auto=全部自然排序循环；或一行一个段名）',
                value=segments_value,
                on_change=lambda e: set_config_callback(
                    "edtalk_realtime", "segments",
                    value=(e.value or "").strip() or "auto"),
            ).style("width:60%").tooltip("段名大小写敏感，以 orifra/ 下目录实际名称为准")
            ui.button(
                "从服务获取可用段",
                on_click=lambda: _fetch_segments(segments_area),
            ).props("outline")

    # ================= 卡片 3：运行控制区（立即生效，不保存） =================
    with ui.card().style(card_css):
        control_expansion = ui.expansion(
            "运行时控制（立即生效，不保存）").style("width:100%")
        with control_expansion:
            status_label = ui.label("服务未运行").style("color: var(--text-hint)")
            push_health_label = ui.label("").style("color: var(--text-hint)")

            with ui.grid(columns=2):
                gaze_select = FormField.create_select(
                    label='视线纠正热切',
                    options={'on': 'on', 'adaptive': 'adaptive', 'off': 'off'},
                    value=get_nested_value(config, "edtalk_realtime", "gaze_correction", default="adaptive"),
                    style="width:100%;",
                )
                repair_select = FormField.create_select(
                    label='面部修复热切',
                    options={'adaptive': 'adaptive', 'on': 'on', 'off': 'off'},
                    value=get_nested_value(config, "edtalk_realtime", "face_repair", default="adaptive"),
                    style="width:100%;",
                )
            with ui.row():
                ui.button("应用 gaze", on_click=lambda: _apply_gaze(gaze_select.value)).props("outline")
                ui.button("应用修复", on_click=lambda: _apply_repair(repair_select.value)).props("outline")
            with ui.row():
                segment_switch_input = FormField.create_input(
                    label='切换到段（段名）', value="", style="width:40%;")
                ui.button(
                    "立即切换",
                    on_click=lambda: _switch_segment(segment_switch_input.value),
                ).props("outline")
                ui.button("刷新状态", on_click=lambda: _poll_status_once()).props("outline")

    # ================= 卡片 4：metahuman_stream 参数（保留，edtalk 时收起） =================
    edtalk_selected = is_edtalk_selected(config)
    with ui.card().style(card_css):
        mh_expansion = ui.expansion(
            "metahuman_stream 参数" + ("（当前使用 EDTalk 实时推理，以下参数不生效）" if edtalk_selected else "")
        ).style("width:100%")
        mh_expansion.value = not edtalk_selected
        with mh_expansion:
            with ui.grid(columns=3):
                FormField.create_select(
                    label='类型',
                    options={'ernerf': 'ernerf', 'musetalk': 'musetalk', 'wav2lip': 'wav2lip'},
                    value=get_nested_value(config, "metahuman_stream", "type"),
                    on_change=lambda e: set_config_callback("metahuman_stream", "type", e.value),
                    style="width:100%;"
                )
                FormField.create_input(
                    label='API地址',
                    value=get_nested_value(config, "metahuman_stream", "api_ip_port"),
                    placeholder='metahuman_stream应用启动API后，监听的ip和端口',
                    on_change=lambda e: set_config_callback("metahuman_stream", "api_ip_port", e.value),
                    style="width:100%;"
                )

    # ================= 交互函数 =================

    def _edtalk_api() -> str:
        port = get_nested_value(config, "edtalk_realtime", "port", default=8000)
        return f"http://127.0.0.1:{port}"

    def _auth_headers() -> dict:
        token = (get_nested_value(config, "edtalk_realtime", "api_token", default="") or "").strip()
        return {"Authorization": f"Bearer {token}"} if token else {}

    def _fetch_segments(area):
        """从 /status 获取可用段填入多行框。"""
        try:
            import requests
            resp = requests.get(f"{_edtalk_api()}/status", headers=_auth_headers(), timeout=3)
            segs = (resp.json() or {}).get("available_segments") or []
            if segs:
                area.set_value("\n".join(str(s) for s in segs))
                ui.notify(position="top", type="positive", message=f"已获取 {len(segs)} 个可用段")
            else:
                ui.notify(position="top", type="warning", message="服务未运行或无可用段")
        except Exception as e:
            ui.notify(position="top", type="negative", message=f"获取失败：{e}")

    async def _apply_gaze(mode: str):
        try:
            import requests
            resp = await asyncio.to_thread(
                requests.post, f"{_edtalk_api()}/gaze/params",
                json={"mode": mode}, headers=_auth_headers(), timeout=3,
            )
            if resp.status_code == 409:
                ui.notify(position="top", type="warning",
                          message="视线纠正启动时为 off，热切不可用（409）——需重启服务并带 gaze 参数")
            elif resp.status_code == 200:
                ui.notify(position="top", type="positive", message="gaze 参数已生效（下一帧）")
            else:
                ui.notify(position="top", type="negative", message=f"失败 HTTP {resp.status_code}")
        except Exception as e:
            ui.notify(position="top", type="negative", message=f"服务未连接：{e}")

    async def _apply_repair(mode: str):
        try:
            import requests
            resp = await asyncio.to_thread(
                requests.post, f"{_edtalk_api()}/repair/params",
                json={"mode": mode}, headers=_auth_headers(), timeout=3,
            )
            if resp.status_code == 409:
                ui.notify(position="top", type="warning",
                          message="面部修复启动时为 off，热切不可用（409）——需重启服务并带修复参数")
            elif resp.status_code == 200:
                ui.notify(position="top", type="positive", message="修复模式已生效（下一帧）")
            else:
                ui.notify(position="top", type="negative", message=f"失败 HTTP {resp.status_code}")
        except Exception as e:
            ui.notify(position="top", type="negative", message=f"服务未连接：{e}")

    async def _switch_segment(segment: str):
        if not segment:
            ui.notify(position="top", type="warning", message="请输入段名")
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
