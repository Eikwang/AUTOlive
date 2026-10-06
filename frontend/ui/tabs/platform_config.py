# -*- coding: UTF-8 -*-
"""
平台配置标签页——DanmakuListener 进程内模块控制面（整合计划 P2-0~P2-3，已批准）

布局规范（P2-0 八项状态呈现规范为实施权威依据）：
- 页头：监听总开关 + 服务胶囊四态（绿=运行/灰=禁用/红=守护置红/黄=重建中）+ 登录请求数
- 卡①：房间管理（首屏主导）——平台开关独立区 / 添加行 / 房间列表（七态状态机）
- 卡②：关键词屏蔽（固定文案"仅屏蔽弹幕内容，不影响礼物与进场"）
- 设计令牌：宽度三档 / 间距 / 色值一律 var(--...) 语义变量（禁硬编码）
- 通道：webui 进程 → fetch http://127.0.0.1:{api_port}/danmaku/*（跨进程唯一通道）
- 乐观更新：启停/删除/关键词/平台开关（5s 超时报错回滚提示）；login_required 不适用
"""
from nicegui import ui
from typing import Dict, Any, Callable

import requests

from frontend.ui.components.config_helper import get_nested_value

# 七态状态机（P2-0 ①）：state → (行内文案, 颜色语义变量, 可用操作集)
_ROOM_STATES = {
    "running": ("监听中", "var(--color-positive, #21ba45)"),
    "stopped": ("已停止", "var(--text-hint, #9e9e9e)"),
    "login_required": ("待登录", "var(--color-warning, #f2c037)"),
    "login_failed": ("登录失败", "var(--color-warning, #f2c037)"),
    "login": ("登录中", "var(--color-info, #31ccec)"),
    "stalled": ("收流停滞", "var(--color-negative, #c10015)"),
    "platform_disabled": ("平台已禁用", "var(--text-hint, #9e9e9e)"),
}

# 12 平台登录要求标注（J1：数据源 = P1-5 登录态矩阵）
_PLATFORM_LOGIN_HINT = {
    "bilibili": "需扫码登录", "kuaishou": "需扫码登录", "douyin": "需扫码登录",
    "douyu": "免登录", "huya": "免登录", "taobao": "免登录", "meituan": "免登录",
    "1688": "浏览器登录态", "xiaohongshu": "浏览器登录态", "pdd": "浏览器登录态",
    "jd": "浏览器登录态", "wechat_channels": "需扫码登录",
}
_PLATFORM_LABELS = {
    "bilibili": "哔哩哔哩", "douyin": "抖音", "douyu": "斗鱼", "huya": "虎牙",
    "kuaishou": "快手", "taobao": "淘宝", "1688": "1688", "meituan": "美团",
    "xiaohongshu": "小红书", "pdd": "拼多多", "jd": "京东", "wechat_channels": "视频号",
}


def _api_base(config: Dict[str, Any]) -> str:
    try:
        port = int(config.get("api_port", default=8000))
    except Exception:
        port = 8000
    return f"http://127.0.0.1:{port}"


def _api(config: Dict[str, Any], method: str, path: str, body: Dict[str, Any] = None,
         timeout: float = 35.0) -> Dict[str, Any]:
    """控制面调用（跨进程 HTTP；5s 内常规操作超时即报错——乐观回滚触发条件 P2-2）。"""
    try:
        if method == "GET":
            r = requests.get(f"{_api_base(config)}{path}", timeout=timeout)
        else:
            r = requests.request(method, f"{_api_base(config)}{path}", json=body or {}, timeout=timeout)
        return r.json()
    except requests.Timeout:
        return {"success": False, "error": "控制面响应超时", "reason_code": "api.timeout",
                "fix_hint": "主程序可能忙——稍后重试或查看 logs/", "docs_anchor": ""}
    except requests.ConnectionError:
        return {"success": False, "error": "无法连接主程序控制面", "reason_code": "api.unreachable",
                "fix_hint": "确认主程序（运行系统）已启动", "docs_anchor": ""}
    except Exception as e:  # noqa: BLE001
        return {"success": False, "error": str(e)[:120], "reason_code": "api.error",
                "fix_hint": "查看日志", "docs_anchor": ""}


def _error_notify(result: Dict[str, Any]) -> None:
    """三段式错误呈现（P2-0 ③：错误类 toast + fix_hint/docs_anchor）。"""
    msg = f"{result.get('error', '操作失败')}"
    if result.get("fix_hint"):
        msg += f"｜{result['fix_hint']}"
    ui.notify(position="top", type="negative", message=msg[:180])


def create_platform_config_tab(
    config: Dict[str, Any],
    theme_config: Dict[str, str],
    set_config_callback: Callable
):
    """创建平台配置标签页（DanmakuListener 进程内模块控制面）"""
    card_css = theme_config.get("card", "")

    # 事件消费指针（增量拉取——P2-2 状态实时性）
    state = {"evt_seq": 0, "rooms": [], "running": False, "red": False, "enabled": False,
             "last_msg_age_s": None, "stall": 60, "active": "", "login_queue": 0, "streak": 0}
    timers: list = []

    async def _toast(msg: str, kind: str = "positive"):
        ui.notify(position="top", type=kind, message=msg[:180])

    # ---------- 页头：总开关 + 胶囊四态 + 登录请求数（P2-0 ④⑤） ----------

    with ui.row().classes("w-full items-center justify-between q-py-sm"):
        with ui.row().classes("items-center gap-2"):
            ui.label("弹幕监听（DanmakuListener）").style(
                "font-size:var(--font-size-page-title,16px);font-weight:600;color:var(--text-primary)")
            head_switch = ui.switch("启用监听", value=False)

            async def _toggle_service(_e=None, val: bool = None):
                target = val if val is not None else head_switch.value
                r = _api(config, "POST", "/danmaku/service/toggle", {"enabled": target})
                if not r.get("success"):
                    _error_notify(r)
                    head_switch.value = not target
                else:
                    await _toast("监听已开启" if target else "监听已关闭")
            head_switch.on("update:model-value", _toggle_service)

        with ui.row().classes("items-center gap-2"):
            pill = ui.badge("未启用").props("outline")
            login_badge = ui.badge("0").props("outline color=warning")
            ui.label("登录请求").style("font-size:var(--font-size-small,12px);color:var(--text-hint)")

    def _render_pill():
        if not state["enabled"]:
            pill.text = "未启用"
            pill._props["color"] = "grey-6"
        elif state["red"]:
            pill.text = "异常（守护置红）"
            pill._props["color"] = "negative"
        elif state["running"]:
            pill.text = "运行中"
            pill._props["color"] = "positive"
        elif state["streak"]:
            pill.text = "重建中"
            pill._props["color"] = "warning"
        else:
            pill.text = "已就绪"
            pill._props["color"] = "grey-8"
        pill.update()
        login_badge.text = str(state["login_queue"])
        login_badge.update()

    # ---------- 卡①：房间管理（首屏主导） ----------

    with ui.card().style(card_css):
        ui.label("房间管理").style("font-size:var(--font-size-card-title,16px);font-weight:600")

        # 活跃房间常驻状态行 + 无活跃警示条（P2-0 ⑦）
        active_row = ui.row().classes("w-full items-center gap-2")
        warn_banner = ui.row().classes("w-full q-pa-sm items-center").style(
            "background:var(--color-warning, #f2c037);border-radius:var(--radius-md,8px);display:none")

        # 平台启用开关独立区（P2-0 ⑥）：12 平台紧凑开关行
        ui.label("平台启用").style("font-size:var(--font-size-field-label,14px);color:var(--text-secondary)")
        plat_switches: Dict[str, ui.switch] = {}
        with ui.row().classes("w-full flex-wrap gap-1"):

            def _mk_plat_toggle(p: str):
                async def _toggle(_e=None):
                    r = _api(config, "POST", f"/danmaku/platform/{p}/toggle",
                             {"enabled": plat_switches[p].value})
                    if not r.get("success"):
                        _error_notify(r)
                        plat_switches[p].value = not plat_switches[p].value
                    else:
                        # 破坏性确认已由后端联动 stop 承担（Eng 义务）；关闭平台时提示
                        if not plat_switches[p].value:
                            await _toast(f"{_PLATFORM_LABELS.get(p, p)} 已禁用——运行中房间已联动停止", "warning")
                return _toggle

            for p in _PLATFORM_LABELS:
                sw = ui.switch(_PLATFORM_LABELS[p], value=False)
                sw.on("update:model-value", _mk_plat_toggle(p))
                sw.style("font-size:var(--font-size-small,12px)")
                plat_switches[p] = sw

        # 添加行：平台下拉（未启用禁用标灰 + 登录标注 tooltip）+ 输入 + 添加
        with ui.row().classes("w-full items-end gap-2"):
            platform_sel = ui.select(
                {p: f"{label}（{_PLATFORM_LOGIN_HINT[p]}）" for p, label in _PLATFORM_LABELS.items()},
                label="平台", value="bilibili", with_input=True,
            ).style("width:var(--input-width-md,250px)")
            room_input = ui.input(label="房间号或直播间链接",
                                  placeholder="例：12345 或 https://live.bilibili.com/12345",
                                  ).style("width:var(--input-width-lg,400px)").tooltip(
                "支持直播间链接自动识别；未启用平台先在上方打开开关")
            warn_label = ui.label("").style("font-size:var(--font-size-tiny,11px);color:var(--text-hint)")

            async def _add_room():
                p = platform_sel.value
                spec = (room_input.value or "").strip()
                if not p or not spec:
                    await _toast("请选择平台并填写房间号", "warning")
                    return
                r = _api(config, "POST", "/danmaku/rooms", {"platform": p, "room_spec": spec})
                if r.get("status") == "login_required":
                    await _toast(r.get("message") or "需要登录：请在弹出的浏览器中扫码", "info")
                    room_input.value = ""
                elif not r.get("success"):
                    _error_notify(r)
                else:
                    for w in (r.get("warnings") or []):
                        await _toast(f"平台提示：{w}", "warning")
                    await _toast("房间已添加并开始监听")
                    room_input.value = ""
            ui.button("添加", icon="add", on_click=_add_room).props("unelevated color=primary")

        # 房间列表（表格：P2-0 ①七态 + ②生命信号 + 活跃徽章 + 仅监听不互动）
        table_host = ui.column().classes("w-full")

    # ---------- 卡②：关键词屏蔽（J2 固定文案） ----------

    kw_state = {"list": [], "enabled": True}

    with ui.card().style(card_css):
        with ui.row().classes("w-full items-center justify-between"):
            ui.label("关键词屏蔽").style("font-size:var(--font-size-card-title,16px);font-weight:600")
            kw_switch = ui.switch("过滤开关", value=True)
        ui.label("仅屏蔽弹幕内容，不影响礼物与进场").style(
            "font-size:var(--font-size-tiny,11px);color:var(--text-hint)")
        with ui.row().classes("w-full items-center gap-2"):
            kw_input = ui.input(label="添加屏蔽关键词").style("width:var(--input-width-md,250px)") \
                .on("keydown.enter", lambda: _add_kw())
            ui.button("添加", on_click=lambda: _add_kw()).props("unelevated color=primary outline")

        async def _add_kw():
            kw = (kw_input.value or "").strip()
            if not kw:
                return
            r = _api(config, "POST", "/danmaku/keywords", {"keyword": kw})
            if not r.get("success"):
                _error_notify(r)
            else:
                kw_input.value = ""
                await _refresh_keywords()
        kw_chips = ui.row().classes("w-full flex-wrap gap-1")

        async def _toggle_kw_filter(_e=None):
            r = _api(config, "POST", "/danmaku/keywords/toggle", {"enabled": kw_switch.value})
            if not r.get("success"):
                _error_notify(r)
                kw_switch.value = not kw_switch.value

        kw_switch.on("update:model-value", _toggle_kw_filter)

    # ---------- 渲染函数 ----------

    def _room_state_of(r: Dict[str, Any]) -> str:
        st = r.get("status") or "stopped"
        if st == "running":
            age = state.get("last_msg_age_s")
            if age is not None and age > state.get("stall", 60):
                return "stalled"
        return st

    def _render_rooms():
        table_host.clear()
        with table_host:
            if not state["rooms"]:
                ui.label("暂无监听房间——选择平台 → 填房间号或粘贴直播间链接 → 添加") \
                    .style("color:var(--text-hint);font-size:var(--font-size-body,14px)")
                return
            with ui.row().classes("w-full items-center gap-2 q-py-xs"):
                ui.label(f"当前互动房间：{state['active'] or '未指定'}") \
                    .style("font-size:var(--font-size-field-label,14px);font-weight:500")
            if state["rooms"] and not state["active"] and state["running"]:
                warn_banner.style("display:flex")
                with warn_banner:
                    ui.icon("warning", color="black")
                    ui.label("有房间在监听但未指定活跃房间——数字人不会互动。请在下方点选「设为活跃」") \
                        .style("color:black;font-size:var(--font-size-small,12px)")
            else:
                warn_banner.style("display:none")
            with ui.column().classes("w-full gap-1"):
                for r in state["rooms"]:
                    p, rid = str(r.get("platform")), str(r.get("room_id"))
                    st = _room_state_of(r)
                    label, color = _ROOM_STATES.get(st, (st, "var(--text-hint)"))
                    is_active = bool(r.get("active"))
                    with ui.row().classes("w-full items-center q-py-xs q-px-sm gap-2").style(
                            f"border:1px solid var(--border-color,#e0e0e0);border-radius:var(--radius-sm,4px)"
                            + (";background:var(--color-primary-opacity, rgba(41,121,255,0.06))" if is_active else "")):
                        ui.badge(_PLATFORM_LABELS.get(p, p)).props("outline")
                        ui.label(rid).style("font-size:var(--font-size-body,14px)")
                        ui.label(label).style(f"color:{color};font-size:var(--font-size-small,12px);font-weight:500")
                        if is_active:
                            ui.badge("活跃").props("color=primary")
                        if st == "running" and not is_active:
                            ui.label("仅监听不互动").style(
                                "font-size:var(--font-size-tiny,11px);color:var(--text-hint)")
                        if st == "stalled":
                            ui.label(f"最近消息 {state['last_msg_age_s']}s 前——检查直播间是否开播或查看日志") \
                                .style("font-size:var(--font-size-tiny,11px);color:var(--color-negative,#c10015)")
                        if st in ("login_required", "login_failed"):
                            ui.button("去登录", on_click=lambda p=p, rid=rid: _start_room(p, rid)) \
                                .props("flat dense color=warning")
                        ui.element("div").classes("col")
                        with ui.row().classes("gap-1"):
                            if st == "running":
                                ui.button("停止", on_click=lambda p=p, rid=rid: _stop_room(p, rid)) \
                                    .props("flat dense")
                            else:
                                ui.button("启动", on_click=lambda p=p, rid=rid: _start_room(p, rid)) \
                                    .props("flat dense color=primary")
                            ui.button("设为活跃", on_click=lambda p=p, rid=rid: _set_active(p, rid)) \
                                .props("flat dense color=primary" if not is_active else "flat dense disabled")
                            ui.button("删除", on_click=lambda p=p, rid=rid: _remove_room(p, rid)) \
                                .props("flat dense color=negative")

    # ---------- 操作（乐观更新 P2-2） ----------

    async def _start_room(p, rid):
        r = _api(config, "POST", f"/danmaku/rooms/{p}/{rid}/start")
        if r.get("status") == "login_required":
            await _toast("需要登录：请在弹出的浏览器中扫码", "info")
        elif not r.get("success"):
            _error_notify(r)
        else:
            await _toast(f"{p}:{rid} 已启动")

    async def _stop_room(p, rid):
        r = _api(config, "POST", f"/danmaku/rooms/{p}/{rid}/stop")
        if not r.get("success"):
            _error_notify(r)
        else:
            await _toast(f"{p}:{rid} 已停止（重启后按启用配置恢复）")

    async def _remove_room(p, rid):
        r = _api(config, "DELETE", f"/danmaku/rooms/{p}/{rid}")
        if not r.get("success"):
            _error_notify(r)
        else:
            await _toast("房间已删除")

    async def _set_active(p, rid):
        # 活跃指定写回 config.rooms[].active（P1-3 显式指定；经平台开关通道风格——走 config 回调）
        rooms = list(get_nested_value(config, "danmaku_listener", "rooms", default=[]) or [])
        found = False
        for r in rooms:
            if isinstance(r, dict):
                was = bool(r.get("active"))
                r["active"] = (r.get("platform") == p and str(r.get("room_id")) == rid)
                if r["active"]:
                    r["enabled"] = True
                    found = True
                if was and not r["active"] and not any(x.get("active") for x in rooms):
                    pass
        if not found:
            rooms.append({"platform": p, "room_id": rid, "enabled": True, "active": True})
        set_config_callback("danmaku_listener", "rooms", value=rooms, _notify=False)
        await _toast(f"活跃房间已设为 {p}:{rid}（该房间消息将进对话链）")

    async def _refresh_keywords():
        r = _api(config, "GET", "/danmaku/keywords")
        if isinstance(r, dict) and "keywords" in r:
            kw_state["list"] = r.get("keywords") or []
            kw_state["enabled"] = bool(r.get("enabled", True))
            kw_switch.value = kw_state["enabled"]
            _render_keywords()

    def _render_keywords():
        kw_chips.clear()
        with kw_chips:
            if not kw_state["list"]:
                ui.label("暂无屏蔽关键词").style("color:var(--text-hint);font-size:var(--font-size-small,12px)")
            for kw in kw_state["list"]:

                async def _rm(k=kw):
                    r = _api(config, "DELETE", f"/danmaku/keywords/{k}")
                    if not r.get("success"):
                        _error_notify(r)
                    else:
                        await _refresh_keywords()
                ui.chip(kw, removable=True, on_removal=_rm).props("outline")

    # ---------- 平台开关同步（下拉禁用态联动） ----------

    def _sync_platform_switches(platforms: Dict[str, bool]):
        for p, sw in plat_switches.items():
            enabled = bool((platforms or {}).get(p, False))
            if sw.value != enabled:
                sw.value = enabled
            plat = platform_sel.options.get(p)
            # 下拉项不禁用（NiceGUI select 单值禁用复杂），以 tooltip + 添加时后端拒绝兜底
            sw.style("opacity:1" if enabled else "opacity:0.5")

    # ---------- 轮询（P2-2：3s，对齐 service_status） ----------

    async def _poll():
        sec = get_nested_value(config, "danmaku_listener", default={}) or {}
        enabled = bool(sec.get("enabled"))
        st = _api(config, "GET", "/danmaku/status", timeout=5)
        if isinstance(st, dict) and ("enabled" in st):
            state["enabled"] = bool(st.get("enabled"))
            state["running"] = bool(st.get("running"))
            state["red"] = bool(st.get("red"))
            state["streak"] = int(st.get("rebuild_count") or 0)
            state["last_msg_age_s"] = st.get("last_msg_age_s")
            state["stall"] = float(st.get("stall_threshold_s") or 60)
            state["active"] = str(st.get("active_room") or "")
        ev = _api(config, "GET", f"/danmaku/events?since={state['evt_seq']}", timeout=5)
        if isinstance(ev, dict) and ev.get("events"):
            for e in ev["events"]:
                if e.get("kind") == "login_request":
                    pass  # 登录请求经 login_badge 计数呈现（danmaku_login.py 弹窗独立轮询）
            state["evt_seq"] = int(ev.get("last_seq") or state["evt_seq"])
        rooms_r = _api(config, "GET", "/danmaku/rooms", timeout=5)
        if isinstance(rooms_r, dict) and "rooms" in rooms_r:
            state["rooms"] = rooms_r.get("rooms") or []
            state["running"] = bool(rooms_r.get("running", state["running"]))
        login_r = _api(config, "GET", "/danmaku/login_state", timeout=5)
        if isinstance(login_r, dict):
            state["login_queue"] = int(login_r.get("queue") or 0) + (1 if login_r.get("active") else 0)
        _sync_platform_switches(sec.get("platforms") or {})
        head_switch.value = state["enabled"]
        _render_pill()
        _render_rooms()

    timer = ui.timer(3.0, _poll)
    timers.append(timer)

    # 首次渲染
    _render_rooms()
    _render_keywords()
    _render_pill()
