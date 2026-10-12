# -*- coding: UTF-8 -*-
"""
平台配置标签页——配置面与运行面分离（整合第二轮 R2，autoplan 已批准）

核心语义（specs/integration/DanmakuListener配置运行分离优化计划.md）：
- **配置面直写**：房间/关键词操作 set_config_callback 直写 config.json——
  配置期（main.py 未运行）完整可用，不经 /danmaku 运行时 API
- **写者分配矩阵**（Eng F-ENG-1/2）：服务运行中（running）→ 房间开关/关键词
  走窄端点（main.py 磁盘 RMW+内存节刷新+联动启停，即时生效）；离线/未构建 →
  直写 config（下次启动生效）；TOCTOU 回退链防静默
- **登录态平台级**（Design S-1）：cookie 同平台多房间共享，徽章按平台缓存
- **配置期登录**（Eng F-ENG-13）：仅 bilibili/kuaishou/douyin（登录服务）；
  其余平台"启动系统后首次监听时登录"（运行期 login_gate 覆盖）
- 写前校验（CEO F5）：重复/平台枚举/非空/长度≤200/链接试解析提示
"""
import asyncio

from nicegui import ui, app
from typing import Dict, Any, Callable

import requests

from frontend.ui.components.config_helper import get_nested_value
from utils.my_log import logger
from frontend.ui.components.danmaku_login_service import get_login_service

# 平台元数据（中文名 / 分组——Design D-2：常用前置+分组下拉）
_PLATFORM_GROUPS = [
    ("常用", ["bilibili", "douyu", "douyin"]),
    ("游戏与娱乐", ["huya", "kuaishou", "pdd"]),
    ("电商与购物", ["taobao", "1688", "jd", "meituan"]),
    ("其他", ["xiaohongshu", "wechat_channels"]),
]
_PLATFORM_LABELS = {
    "bilibili": "哔哩哔哩", "douyin": "抖音", "douyu": "斗鱼", "huya": "虎牙",
    "kuaishou": "快手", "taobao": "淘宝", "1688": "1688", "meituan": "美团",
    "xiaohongshu": "小红书", "pdd": "拼多多", "jd": "京东", "wechat_channels": "视频号",
}
_ALL_PLATFORMS = list(_PLATFORM_LABELS.keys())
# 配置期登录支持范围（Eng F-ENG-13 裁决）
_CONFIG_LOGIN_PLATFORMS = {"bilibili", "kuaishou", "douyin", "douyu", "huya"}
_ROOM_SPEC_MAX = 200  # Eng F-ENG-9

# 状态中文文案表（Design S-2；动词统一：启动系统/开始·停止监听）
_ROOM_STATE_TEXT = {
    "running": ("监听中", "var(--color-positive, #21ba45)"),
    "saved_pending": ("已保存·待启动", "var(--text-hint, #9e9e9e)"),
    "stalled": ("收流停滞", "var(--color-negative, #c10015)"),
    "disabled": ("已停用", "var(--text-hint, #9e9e9e)"),
}
_LOGIN_BADGE = {  # J-3 三级视觉
    "none": ("未登录", "grey-6"),
    "confirmed": ("已登录", "positive"),
}


def _api_base(config: Dict[str, Any]) -> str:
    try:
        # Config.get 无 default kwarg（65948e8f 同款 or 兜底）——default= kwarg 会
        # TypeError 被 except 吞掉，端口静默落 8000，而实际控制面在 api_port
        # （8082）：此前 /danmaku/* 前端轮询全部打错端口，开关与发送房间
        # 下拉的运行态联动全数失效。
        port = int(config.get("api_port") or 8000)
    except Exception:
        port = 8000
    return f"http://127.0.0.1:{port}"


# 本机控制面专用 Session（trust_env=False——同 danmaku_login_service.py 根因注记）
_LOCAL_SESSION = requests.Session()
_LOCAL_SESSION.trust_env = False
_LOCAL_SESSION.proxies = {"http": None, "https": None}


async def _api(config: Dict[str, Any], method: str, path: str, body: Dict[str, Any] = None,
               timeout: float = 35.0) -> Any:
    """运行面调用（async 线程池；仅 running 态使用）。"""
    try:
        if method == "GET":
            r = await asyncio.to_thread(
                _LOCAL_SESSION.get, f"{_api_base(config)}{path}", timeout=timeout)
        else:
            r = await asyncio.to_thread(
                _LOCAL_SESSION.request, method, f"{_api_base(config)}{path}",
                json=body or {}, timeout=timeout)
        return r.json()
    except Exception:
        return None


def _validate_room(platform: str, room_spec: str, rooms: list) -> Dict[str, Any]:
    """写前校验（CEO F5 + DX 发现 6）：返回 {"ok": bool, "error": str, "confirm": str|None}。"""
    spec = (room_spec or "").strip()
    if not platform or platform not in _ALL_PLATFORMS:
        return {"ok": False, "error": "请选择有效平台"}
    if not spec:
        return {"ok": False, "error": "房间号或链接不能为空"}
    if len(spec) > _ROOM_SPEC_MAX:
        return {"ok": False, "error": f"房间标识过长（>{_ROOM_SPEC_MAX} 字符）"}
    for r in rooms:
        if isinstance(r, dict) and r.get("platform") == platform and \
                str(r.get("room_id")) == spec:
            return {"ok": False, "error": f"该平台已存在相同房间：{spec}"}
    confirm = None
    if "://" in spec:
        try:
            from danmaku_listener.utils.platform_parser import extract_from_link
            if extract_from_link(spec) is None:
                confirm = "无法识别的链接格式，将按原文保存，运行时可能无法监听——确认添加？"
        except Exception:
            confirm = "链接解析不可用，将按原文保存——确认添加？"
    return {"ok": True, "error": "", "confirm": confirm}


def create_platform_config_tab(
    config: Dict[str, Any],
    theme_config: Dict[str, str],
    set_config_callback: Callable
):
    """创建平台配置标签页（配置面直写 + 登录服务 + 运行态叠加）"""
    card_css = theme_config.get("card", "")

    state: Dict[str, Any] = {
        "mode": "main_down",       # main_down / service_disabled / running（三态分流 Eng F-ENG-5）
        "rooms_cfg": [],           # 配置真相（config.rooms）
        "run_status": {},          # 运行态叠加（/danmaku/status）
        "run_rooms": {},           # 运行态房间映射（join 用）
        "kw": [], "kw_on": True,
        "confirm_pending": None,   # 链接确认挂起（DX 发现 6）
    }
    login_svc = get_login_service()

    async def _toast(msg: str, kind: str = "positive"):
        ui.notify(position="top", type=kind, message=msg[:180])

    async def _err_notify(result: Dict[str, Any]):
        msg = result.get("error", "操作失败")
        if result.get("fix_hint"):
            msg += f"｜{result['fix_hint']}"
        await _toast(msg, "negative")

    def _rooms() -> list:
        return [r for r in state["rooms_cfg"] if isinstance(r, dict)]

    def _write_rooms(rooms: list) -> bool:
        """config.rooms 直写（set_config 通道双回滚；失败时调用方保持 UI 不翻转——DX 发现 5）。"""
        return set_config_callback("danmaku_listener", "rooms", value=rooms, _notify=False) is not False

    def _write_keywords():
        set_config_callback("danmaku_listener", "keywords",
                            value=list(state["kw"]), _notify=False)
        set_config_callback("danmaku_listener", "keyword_filter_enabled",
                            value=state["kw_on"], _notify=False)

    # ---------- 三态判定（Eng F-ENG-5） ----------

    def _mode_from_status(st: Any) -> str:
        if not isinstance(st, dict) or "enabled" not in st:
            return "main_down"
        if not st.get("enabled"):
            return "service_disabled"
        return "running"

    # ---------- 页头：总开关 + 胶囊 + 登录进度 chip ----------

    with ui.row().classes("w-full items-center justify-between q-py-sm"):
        with ui.row().classes("items-center gap-2"):
            ui.label("弹幕监听（DanmakuListener）").style(
                "font-size:var(--font-size-page-title,16px);font-weight:600;color:var(--text-primary)")
            head_switch = ui.switch("启用监听", value=False)

            async def _toggle_service(_e=None):
                target = head_switch.value
                # main 活着的两种形态都走运行面端点（假启动批次修复 2026-10-12）：
                # service_disabled（服务未构建）走 toggle 可在线构建——此前只有
                # running 走 API，service_disabled 落配置期直写，但 main.py 内存
                # 不会热重载 config 文件，导致开关写盘后运行面永远不知道。
                if state["mode"] in ("running", "service_disabled"):
                    r = await _api(config, "POST", "/danmaku/service/toggle", {"enabled": target})
                    if r is None or not isinstance(r, dict) or not r.get("success"):
                        await _err_notify(r or {"error": "运行面切换失败"})
                        head_switch.value = not target
                        return
                    await _toast("监听已开启" if target else "监听已关闭")
                else:  # 配置期：直写 config（不尝试 API——消除误导提示与闪动）
                    ok = set_config_callback("danmaku_listener", "enabled",
                                             value=target, _notify=False)
                    if ok is False:
                        await _err_notify({"error": "配置保存失败",
                                           "fix_hint": "开关状态未翻转"})
                        head_switch.value = not target
                        return
                    await _toast("已开启——启动系统后将同时启动已启用房间的监听"
                                 if target else "已关闭")
            head_switch.on("update:model-value", _toggle_service)

        with ui.row().classes("items-center gap-2"):
            pill = ui.badge("未启用").props("outline")
            login_chip = ui.chip("登录：空闲", icon="qr_code_2").props("outline").style(
                "font-size:var(--font-size-tiny,11px)")

    def _render_pill():
        mode = state["mode"]
        st = state["run_status"]
        if mode == "main_down":
            pill.text, c = "主程序未运行", "grey-6"
        elif mode == "service_disabled":
            pill.text, c = "未启用", "grey-6"
        elif st.get("red"):
            pill.text, c = "异常（守护置红）", "negative"
        elif st.get("running"):
            pill.text, c = "运行中", "positive"
        else:
            pill.text, c = "已就绪", "grey-8"
        pill._props["color"] = c
        pill.update()

    def _render_login_chip():
        ls = login_svc.get_state()
        status = ls.get("status", "idle")
        plat = ls.get("platform")
        text = {"idle": "登录：空闲", "queued": f"登录排队：{plat}", "launching": f"浏览器启动中：{plat}",
                "waiting_scan": f"等待扫码：{plat}", "saving": f"保存中：{plat}",
                "success": f"登录成功：{plat}", "failed": f"登录失败：{plat}",
                "timeout": f"登录超时：{plat}"}.get(status, "登录：空闲")
        login_chip.text = text
        login_chip._props["color"] = {"success": "positive", "failed": "negative",
                                      "timeout": "warning"}.get(status, "info")
        login_chip.update()

    # ---------- 卡①：房间管理（首屏主对象——Design D-1） ----------

    with ui.card().style(card_css):
        ui.label("房间管理").style("font-size:var(--font-size-card-title,16px);font-weight:600")

        # 添加表单（Design D-2：分组下拉，白名单平台）
        with ui.row().classes("w-full items-end gap-2"):
            plat_options: Dict[str, str] = {}
            for gname, plats in _PLATFORM_GROUPS:
                for p in plats:
                    plat_options[p] = f"{gname}｜{_PLATFORM_LABELS[p]}"
            plat_sel = ui.select(plat_options, label="平台", value="bilibili",
                                 with_input=True).style("width:var(--input-width-md,250px)")
            room_input = ui.input(label="房间号或直播间链接",
                                  placeholder="例：12345 或直播间分享链接").style(
                "width:var(--input-width-lg,400px)").tooltip(
                "支持直播间链接自动识别；配置期即可登录保存")
            login_hint = ui.label("").style(
                "font-size:var(--font-size-tiny,11px);color:var(--color-info, #31ccec)")

            async def _add_room():
                platform = plat_sel.value
                spec = (room_input.value or "").strip()
                v = _validate_room(platform, spec, _rooms())
                if not v["ok"]:
                    await _err_notify({"error": v["error"],
                                       "fix_hint": "检查平台选择与房间号输入"})
                    return
                if v["confirm"] and state.get("confirm_pending") != spec:
                    state["confirm_pending"] = spec
                    await _toast(v["confirm"], "warning")
                    return
                state["confirm_pending"] = None
                rooms = _rooms()
                rooms.append({"platform": platform, "room_id": spec,
                              "enabled": True})  # DX 发现 1：添加即启用（active 字段已退役）
                if not _write_rooms(rooms):
                    await _err_notify({"error": "配置保存失败",
                                       "fix_hint": "检查磁盘/权限后重试（状态未翻转）"})
                    return
                room_input.value = ""
                _render_rooms()  # 即时渲染（不依赖 3s 轮询——用户问题"添加后不显示列表"）
                await _toast(f"房间已保存：{_PLATFORM_LABELS[platform]} {spec}")
                # onboard_room 编排（CEO F2 单入口）：登录检测 → 自动弹窗
                await _onboard_login(platform)
            ui.button("添加", icon="add", on_click=_add_room).props("unelevated color=primary")

        login_progress = ui.label("").style(
            "font-size:var(--font-size-tiny,11px);color:var(--color-info, #31ccec);display:none")
        list_host = ui.column().classes("w-full")

    # ---------- 卡②：关键词屏蔽（J2 固定文案；运行期热生效 M-1 ③） ----------

    with ui.card().style(card_css):
        with ui.row().classes("w-full items-center justify-between"):
            ui.label("关键词屏蔽").style("font-size:var(--font-size-card-title,16px);font-weight:600")
            kw_switch = ui.switch("过滤开关", value=True)

            async def _toggle_kw(_e=None):
                state["kw_on"] = kw_switch.value
                _write_keywords()
                if state["mode"] == "running":  # M-1 ③：运行期 reload 热生效
                    await _api(config, "POST", "/danmaku/keywords/reload", timeout=8)
                    await _toast("过滤开关已保存并即时生效")
            kw_switch.on("update:model-value", _toggle_kw)
        ui.label("仅屏蔽弹幕内容，不影响礼物与进场；运行中修改即时生效").style(
            "font-size:var(--font-size-tiny,11px);color:var(--text-hint)")
        with ui.row().classes("w-full items-center gap-2"):
            kw_input = ui.input(label="添加屏蔽关键词").style("width:var(--input-width-md,250px)") \
                .on("keydown.enter", lambda: _add_kw())
            ui.button("添加", on_click=lambda: _add_kw()).props("unelevated color=primary outline")
        kw_chips = ui.row().classes("w-full flex-wrap gap-1")

    async def _add_kw():
        kw = (kw_input.value or "").strip()
        if not kw or len(kw) > 50:
            await _toast("关键词须为 1-50 字符", "warning")
            return
        if kw in state["kw"]:
            await _toast("关键词已存在", "warning")
            return
        state["kw"] = state["kw"] + [kw]
        if not _write_keywords_wrap():
            await _err_notify({"error": "配置保存失败", "fix_hint": "检查磁盘/权限（状态未翻转）"})
            return
        kw_input.value = ""
        _render_kw()  # 即时渲染（DX 发现 2：不依赖轮询）
        if state["mode"] == "running":  # M-1 ③ 热生效
            await _api(config, "POST", "/danmaku/keywords/reload", timeout=8)
            await _toast("关键词已保存并即时生效")

    def _write_keywords_wrap() -> bool:
        """关键词写盘（失败返回 False——DX 发现 5 调用方感知）。"""
        try:
            _write_keywords()
            return True
        except Exception:
            logger.error("[platform_config] 关键词写盘失败", exc_info=True)
            return False

    async def _rm_kw(kw: str):
        state["kw"] = [k for k in state["kw"] if k != kw]
        if not _write_keywords_wrap():
            await _err_notify({"error": "配置保存失败", "fix_hint": "词条未删除"})
            _render_kw()
            return
        _render_kw()
        if state["mode"] == "running":
            await _api(config, "POST", "/danmaku/keywords/reload", timeout=8)

    def _render_kw():
        """关键词词条（NiceGUI 3.16 ui.chip 无 on_removal 参数——badge+close 组合）。"""
        kw_chips.clear()
        with kw_chips:
            if not state["kw"]:
                ui.label("暂无屏蔽关键词——添加后弹幕中出现该词将被过滤").style(
                    "color:var(--text-hint);font-size:var(--font-size-small,12px)")
            for kw in state["kw"]:

                async def _rm(k=kw):
                    await _rm_kw(k)
                with ui.row().classes("items-center q-px-sm q-py-xs").style(
                        "border:1px solid var(--border-color,#e0e0e0);border-radius:var(--radius-sm,4px);gap:4px"):
                    ui.label(kw).style("font-size:var(--font-size-small,12px)")
                    ui.button(icon="close", on_click=_rm).props("flat dense size=xs")

    # ---------- 弹幕发送（O2 第一阶段：单条手动；鉴权=组件 send_token） ----------

    state["last_request_id"] = ""

    async def _send_token() -> str:
        tok = (send_token_input.value or "").strip()
        if tok:
            try:
                app.storage.browser["send_token"] = tok
            except Exception:
                pass
            return tok
        try:
            return str(app.storage.browser.get("send_token") or "")
        except Exception:
            return ""

    def _render_send_result(r: Dict[str, Any]):
        code = r.get("status_code")
        if code == 503:
            send_result_label.text = ""
            ui.notify(f"发送未配置：{r.get('error')}｜{r.get('fix_hint', '')}",
                      type="warning", close_button="知道了")
            return
        if code == 401:
            send_result_label.text = ""
            ui.notify("token 不匹配——核对 persistence_data/send_token.txt",
                      type="negative", close_button="知道了")
            return
        status = str(r.get("status") or "")
        rid = str(r.get("request_id") or "")
        state["last_request_id"] = rid
        kind = {"sent": "positive", "dry_run": "info", "unknown": "warning"}.get(status, "negative")
        msg = f"回执 {status or '无'}｜request_id={rid}"
        if r.get("reason_code"):
            msg += f"｜{r.get('reason_code')}"
        if r.get("fix_hint"):
            msg += f"｜{r.get('fix_hint')}"
        send_result_label.text = msg
        ui.notify(msg, type=kind, close_button="知道了")
        if status in ("sent", "dry_run", "unknown"):
            send_content.value = ""

    async def _do_send():
        key = send_room_select.value
        room = state["run_rooms"].get(key) if key else None
        if not room:
            await _toast("请选择在听房间（服务未运行或无在听房间）", "warning")
            return
        content = (send_content.value or "").strip()
        if not content:
            await _toast("弹幕内容不能为空", "warning")
            return
        tok = await _send_token()
        if not tok:
            await _toast("首次发送需填入发送 token（persistence_data/send_token.txt 内容）", "warning")
            return
        r = await _api(config, "POST", "/danmaku/send", body={
            "platform": room.get("platform"), "room_id": room.get("room_id"),
            "content": content, "token": tok}, timeout=80)
        if not isinstance(r, dict):
            await _toast("发送请求失败（控制面不可达）", "negative")
            return
        _render_send_result(r)

    async def _do_reconcile():
        rid = state.get("last_request_id") or ""
        if not rid:
            await _toast("无最近 request_id——先发送一次", "warning")
            return
        tok = await _send_token()
        r = await _api(config, "POST", "/danmaku/send-results",
                       body={"request_id": rid, "token": tok}, timeout=10)
        if not isinstance(r, dict):
            await _toast("对账请求失败", "negative")
            return
        seen = r.get("result")
        ui.notify(f"对账 {rid}: {(str(seen)[:160] if seen else '无记录')}",
                  type="info", close_button="知道了")

    with ui.card().style(card_css):
        with ui.row().classes("w-full items-center justify-between"):
            ui.label("弹幕发送").style("font-size:var(--font-size-card-title,16px);font-weight:600")
            send_result_label = ui.label("").style(
                "font-size:var(--font-size-small,12px);color:var(--text-hint)")
        ui.label("单条手动发送（限速/熔断由组件守卫链兜底）；dry_run=已记录未实发，实发需组件 send_dry_run=false").style(
            "font-size:var(--font-size-tiny,11px);color:var(--text-hint)")
        with ui.row().classes("w-full items-center gap-2"):
            send_room_select = ui.select(
                label="目标房间（在听）", options={}).style("width:var(--input-width-md,250px)")
            send_token_input = ui.input(
                label="发送 token（首次填入后本机缓存）", password=True).style(
                "width:var(--input-width-md,250px)")
        send_content = ui.input(label="弹幕内容（单条上限 100 字）").classes("w-full")
        with ui.row().classes("w-full items-center gap-2"):
            ui.button("发送", on_click=_do_send).props("unelevated color=primary")
            ui.button("对账最近", on_click=_do_reconcile).props("outline")

    # ---------- 登录编排（CEO F2 onboard_room 单入口） ----------

    async def _onboard_login(platform: str):
        """添加→检测→自动弹窗→列表刷新（CEO F2 编排接缝；Eng F-ENG-13 三平台）。"""
        if platform not in _CONFIG_LOGIN_PLATFORMS:
            login_hint.text = "该平台在启动系统后首次监听时按引导登录"
            return
        if login_svc.check_login(platform):
            login_hint.text = "登录态正常"
            return
        r = login_svc.request_login(platform)
        if r.get("success"):
            login_hint.text = "登录窗口已弹出——请完成扫码，登录后自动保存"
            await _toast("检测到未登录——登录窗口已弹出（完成后自动保存）", "info")

    async def _retry_login(platform: str):
        await _onboard_login(platform)

    # ---------- 房间列表（config 真相 + 运行态叠加；Eng F-ENG-6 join 规则） ----------

    def _room_run_state(r: Dict[str, Any]) -> str:
        """行内状态（用户裁定：开关即控制——关=停用，开=监听/待启动；无活跃概念）。"""
        if not r.get("enabled"):
            return "disabled"
        key = f"{r.get('platform')}:{r.get('room_id')}"
        rr = state["run_rooms"].get(key)
        if state["mode"] == "running" and rr is not None:
            if rr.get("status") == "running":
                age = state["run_status"].get("last_msg_age_s")
                if age is not None and age > state["run_status"].get("stall_threshold_s", 60):
                    return "stalled"
                return "running"
        return "saved_pending"

    def _render_rooms():
        list_host.clear()
        rooms = _rooms()
        with list_host:
            if not rooms:
                ui.label("还没有监听房间——① 选择平台 → ② 填房间号或粘贴链接 → ③ 添加并扫码登录") \
                    .style("color:var(--text-hint);font-size:var(--font-size-body,14px)")
                return
            for r in rooms:
                p, rid = str(r.get("platform")), str(r.get("room_id"))
                run_state = _room_run_state(r)
                text, color = _ROOM_STATE_TEXT[run_state]
                enabled = bool(r.get("enabled"))
                with ui.row().classes("w-full items-center q-py-xs q-px-sm gap-2").style(
                        "border:1px solid var(--border-color,#e0e0e0);border-radius:var(--radius-sm,4px)"):
                    ui.badge(_PLATFORM_LABELS.get(p, p)).props("outline")
                    ui.label(rid).style("font-size:var(--font-size-body,14px)")
                    ui.label(text).style(f"color:{color};font-size:var(--font-size-small,12px);font-weight:500")
                    if run_state == "stalled":
                        ui.label(f"最近消息 {state['run_status'].get('last_msg_age_s')}s 前——确认开播或查日志") \
                            .style("font-size:var(--font-size-tiny,11px);color:var(--color-negative,#c10015)")
                    ui.element("div").classes("col")
                    # 登录徽章（平台级——Design S-1；J-3 三级灰/绿）
                    if p in _CONFIG_LOGIN_PLATFORMS:
                        logged = login_svc.check_login(p)
                        lb_text, lb_color = _LOGIN_BADGE["confirmed" if logged else "none"]
                        ui.badge(lb_text).props(f"outline color={lb_color}").tooltip(
                            "同平台共享登录态；运行期以引擎实际验证为准")
                        if not logged:
                            ui.button("登录", on_click=lambda pp=p: _retry_login(pp)) \
                                .props("flat dense color=warning")
                    else:
                        ui.badge("运行期登录").props("outline color=info").tooltip(
                            "该平台（受控页面引擎）在启动系统后首次监听时按引导自动登录")
                    # 启用开关（行内——问题 3 根治；三态分流 F1）
                    room_switch = ui.switch("监听", value=enabled)

                    async def _toggle_room(_e=None, rr=rooms, idx=rooms.index(r), sw=room_switch):
                        target = sw.value
                        rr[idx]["enabled"] = target
                        if state["mode"] == "running":  # F1 窄端点（config-first 联动启停）
                            resp = await _api(config, "POST",
                                              f"/danmaku/rooms/{rr[idx]['platform']}/{rr[idx]['room_id']}/enabled",
                                              {"enabled": target}, timeout=10)
                            if resp is None or not isinstance(resp, dict) or not resp.get("success"):
                                st2 = await _api(config, "GET", "/danmaku/status", timeout=5)
                                if not isinstance(st2, dict) or "enabled" not in st2:
                                    pass  # TOCTOU：确认离线 → 降级直写（已写）
                                else:
                                    sw.value = not target
                                    await _err_notify({"error": "运行面切换失败",
                                                       "fix_hint": "服务仍在线——请重试"})
                                    return
                        if not _write_rooms(rr):  # DX 发现 5：失败不翻转
                            sw.value = not target
                            await _err_notify({"error": "配置保存失败", "fix_hint": "状态未翻转"})
                            return
                        await _toast(f"{p}:{rid} {'开始监听' if target else '已停止监听'}"
                                     + ("" if state["mode"] == "running" else "（下次启动系统后生效）"))
                    room_switch.on("update:model-value", _toggle_room)

                    # 删除（运行态两步走——M-1 ②：先窄端点停再删配置）
                    async def _remove(_e=None, pp=p, rd=rid):
                        if state["mode"] == "running":
                            await _api(config, "POST", f"/danmaku/rooms/{pp}/{rd}/enabled",
                                       {"enabled": False}, timeout=10)
                        kept = [x for x in _rooms()
                                if not (x.get("platform") == pp and str(x.get("room_id")) == rd)]
                        if not _write_rooms(kept):
                            await _err_notify({"error": "删除保存失败", "fix_hint": "房间列表未变更"})
                            return
                        await _toast("房间已删除")
                    ui.button("删除", on_click=_remove).props("flat dense color=negative")


    # ---------- 轮询（3s） ----------

    async def _poll():
        try:
            await _poll_inner()
        except Exception:
            logger.error("[platform_config] 轮询渲染异常（下轮重试）", exc_info=True)

    async def _poll_inner():
        sec = get_nested_value(config, "danmaku_listener", default={}) or {}
        state["rooms_cfg"] = list(sec.get("rooms") or [])
        state["kw"] = list(sec.get("keywords") or [])
        state["kw_on"] = bool(sec.get("keyword_filter_enabled", True))
        if kw_switch.value != state["kw_on"]:
            kw_switch.value = state["kw_on"]
        # 三态判定（Eng F-ENG-5）
        st = await _api(config, "GET", "/danmaku/status", timeout=3)
        state["mode"] = _mode_from_status(st)
        if state["mode"] == "running":
            state["run_status"] = st if isinstance(st, dict) else {}
            rr = await _api(config, "GET", "/danmaku/rooms", timeout=5)
            if isinstance(rr, dict):
                state["run_rooms"] = {f"{x.get('platform')}:{x.get('room_id')}": x
                                      for x in (rr.get("rooms") or []) if isinstance(x, dict)}
            # 发送卡目标房间联动（O2 第一阶段）
            _rooms_now = list(state["run_rooms"].keys())
            if list(send_room_select.options or []) != _rooms_now:
                send_room_select.options = _rooms_now
                if send_room_select.value not in _rooms_now:
                    send_room_select.value = None
                # NiceGUI 3.x：options 属性赋值不自动推前端（value 是 reactive
                # 而 options 不是）——缺 update() 则前端下拉永远显示旧选项
                send_room_select.update()
        if state["mode"] == "running":  # 回读运行面真相（假启动批次修复：此前读
            # webui 进程 stale 内存——toggle 由 main.py 写文件+main 内存，webui
            # 无跨进程同步，3 秒后轮询回弹成旧值）
            head_switch.value = bool(state["run_status"].get("enabled",
                                     sec.get("enabled", False)))
        _render_pill()
        _render_login_chip()
        _render_rooms()
        _render_kw()
        # 登录进度行内呈现（Design A-1 非模态）
        ls = login_svc.get_state()
        if ls.get("status") in ("queued", "launching", "waiting_scan", "saving"):
            login_progress.text = f"登录进度：{login_chip.text}"
            login_progress.style("display:block")
        else:
            login_progress.style("display:none")

    ui.timer(3.0, _poll)

    _render_rooms()
    _render_kw()
    _render_pill()
    _render_login_chip()
