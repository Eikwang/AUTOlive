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
        combo_warning = ui.label("").style("color: var(--color-warning, #b58900); white-space: pre-wrap")

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
            "运行时控制（立即生效，不保存）").style("width:100%").open(False)
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
        ).style("width:100%").open(not edtalk_selected)
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
            ).style("color: var(--color-positive, #21ba45)")
            control_expansion.open(True)
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
                    push_health_label.set_text("EDTalk 推送：正常").style("color: var(--color-positive, #21ba45)")
                else:
                    push_health_label.set_text(
                        f"EDTalk 推送：最近 {fc} 次失败（{data.get('last_error', '')[:60]}）"
                    ).style("color: var(--color-negative, #c10015)")
            else:
                push_health_label.set_text("EDTalk 推送：未知（音频模块未初始化）").style("color: var(--text-hint)")
        except Exception:
            push_health_label.set_text("EDTalk 推送：未知（直播运行时未启动）").style("color: var(--text-hint)")

    # 5 秒轮询（仅当 edtalk 被选中时活动；native F14：失败不转全局离线，仅本页显示）
    ui.timer(5.0, _poll_status_once)
