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
import json

from nicegui import ui
from typing import Dict, Any, Callable

import requests

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


def _disk_danmaku_section() -> Dict[str, Any]:
    """磁盘 config.json 的 danmaku_listener 节（2026-10-12 开关语义修复）。

    webui 与 main.py 各持内存副本且互不同步（config_watcher 未接线）——运行期
    toggle 由 main.py 落盘，webui 内存副本 stale。开关=配置意图（启动系统时是否
    启动监听），其显示值必须以磁盘为唯一真相：运行态由状态胶囊承担，二者职责
    分离（此前轮询把运行面 enabled 回读进开关，开关被做成了状态指示器——启动后
    自动变开启、停止后自动变关闭，用户裁定为错误语义）。
    """
    try:
        with open("config.json", "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("danmaku_listener") or {}
    except Exception:
        logger.error("[platform_config] 磁盘 config.json 读取失败", exc_info=True)
        return {}


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

    async def _write_rooms(rooms: list) -> bool:
        """config.rooms 写盘（F-ENG-2 分流，2026-10-13）：main 可达时 Settings.save
        磁盘回读会覆盖 danmaku_listener 节直写（添加/删除房间假成功）——走
        /danmaku/rooms-config 窄端点落盘+服务端权威对账；main_down 时 webui 是
        该节唯一写者，set_config 直写可落盘。"""
        if state["mode"] in ("running", "service_disabled"):
            r = await _api(config, "POST", "/danmaku/rooms-config", {"rooms": rooms})
            return bool(isinstance(r, dict) and r.get("success"))
        return set_config_callback("danmaku_listener", "rooms", value=rooms, _notify=False) is not False

    async def _write_keywords() -> bool:
        """关键词写盘（F-ENG-2 分流：main 可达时 Settings.save 磁盘回读会覆盖
        danmaku_listener 节直写——走窄端点落盘+服务端热载；main_down 时 webui
        是该节唯一写者，set_config 直写可落盘）。"""
        if state["mode"] in ("running", "service_disabled"):
            r = await _api(config, "POST", "/danmaku/keywords-config", {
                "keywords": list(state["kw"]), "enabled": bool(state["kw_on"])})
            return bool(isinstance(r, dict) and r.get("success"))
        set_config_callback("danmaku_listener", "keywords",
                            value=list(state["kw"]), _notify=False)
        set_config_callback("danmaku_listener", "keyword_filter_enabled",
                            value=state["kw_on"], _notify=False)
        return True

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
            # 开关=配置意图（初值读磁盘真相；注释见 _disk_danmaku_section）
            head_switch = ui.switch(
                "启用监听", value=bool(_disk_danmaku_section().get("enabled", False)))

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
            # 旧「登录：空闲」chip 已移除（2026-10-13 用户裁定弃用——登录进度由
            # 表单下方 login_progress 行内呈现，房间行登录徽章承载登录态）
            pill = ui.badge("未启用").props("outline")

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

    def _login_state_text() -> str:
        """登录进度文本（login_chip 移除后 login_progress 的数据源）。"""
        ls = login_svc.get_state()
        status = ls.get("status", "idle")
        plat = ls.get("platform")
        return {"idle": "登录：空闲", "queued": f"登录排队：{plat}", "launching": f"浏览器启动中：{plat}",
                "waiting_scan": f"等待扫码：{plat}", "saving": f"保存中：{plat}",
                "success": f"登录成功：{plat}", "failed": f"登录失败：{plat}",
                "timeout": f"登录超时：{plat}"}.get(status, "登录：空闲")

    # ---------- 发送：行内发送处理器 + 全局发送设置（autoplan 2026-10-22 批次） ----------
    #
    # 结构（用户裁决=行内化；规格审查 C1/C4 + design F1-F15 + eng E1-E2 全规格）：
    # - 独立"弹幕发送"卡删除；每个监听中房间行内联输入框+发送按钮（仅 running）；
    #   服务运行中未监听房间=禁用发送行+占位提示；main_down 全页无发送行
    # - 输入态双保险：渲染签名跳过 + platform:room 草稿快照回填 + 焦点恢复
    # - 发送设置收进房间管理卡"发送设置（全局）"折叠区：token/白名单/实发/对账
    # - 回执=行内持久微指示（保留至下次发送）+短 toast；失败/unknown 不自动消失

    state["last_request_id"] = ""
    state.setdefault("send_drafts", {})    # platform:room -> 草稿（重建回填；成功即清/失败保留/停止保留）
    state.setdefault("receipts", {})       # platform:room -> {"kind","text","rid"} 最近回执
    state.setdefault("rooms_render_sig", None)
    state.setdefault("send_inputs", {})    # platform:room -> 输入组件引用（草稿快照/焦点恢复用）
    state.setdefault("last_focus_key", None)
    state.setdefault("send_inflight", set())

    def _disk_token() -> str:
        """发送 token 磁盘真相（config.ws_token_file——webui 与 main 同机直接读
        文件。此前只存 app.storage.browser：webui 重启/NiceGUI 存储目录清理后
        丢失，每次重启都要重填——2026-10-13 用户报告）。"""
        try:
            path = str(_disk_danmaku_section().get("ws_token_file")
                       or "persistence_data/send_token.txt")
            with open(path, "r", encoding="utf-8") as f:
                return f.read().strip()
        except Exception:
            return ""

    async def _send_token() -> str:
        return (send_token_input.value or "").strip() or _disk_token()

    def _apply_receipt(key: str, r: Dict[str, Any]) -> None:
        """回执四态 → 行内持久微指示 + 短 toast（长引导进 tooltip，F14）。"""
        code = r.get("status_code")
        if code == 503:
            kind, text = "warn", f"发送未配置：{r.get('error', '')}"
        elif code == 401:
            kind, text = "fail", "token 不匹配——核对 persistence_data/send_token.txt"
        else:
            status = str(r.get("status") or "")
            rid = str(r.get("request_id") or "")
            state["last_request_id"] = rid
            reason = str(r.get("reason_code") or "")
            if status == "sent":
                kind, text = "ok", f"已发送｜{rid}"
            elif status == "dry_run":
                kind, text = "dry", f"演练：已记录不发送｜{rid}"
            elif status == "unknown":
                kind, text = "warn", f"回执未知｜{rid}"
            else:
                kind = "fail"
                text = {"ROOM_NOT_LISTENED": "该房间未在监听",
                        "SENDER_DISABLED": "平台未在发送白名单",
                        "PLATFORM_REJECTED": "平台登录态失效——请先在该行重新登录",
                        "RATE_LIMITED": "触发限速守卫——稍后重试",
                        "BLANK": "内容为空"}.get(reason, (f"发送失败" + (f"：{reason}" if reason else "")))
            if r.get("error"):
                text += f"｜{r['error']}"
        state["receipts"][key] = {"kind": kind, "text": text,
                                  "rid": str(r.get("request_id") or "")}
        toast_kind = {"ok": "positive", "dry": "info", "warn": "warning"}.get(kind, "negative")
        ui.notify(text, type=toast_kind,
                  **({} if kind in ("ok", "dry") else {"close_button": "知道了", "timeout": 0}))

    async def _send_from_row(key: str, platform: str, room_id: str, inp, btn):
        if key in state["send_inflight"]:
            return
        content = (inp.value or "").strip()
        if not content:
            await _toast("弹幕内容不能为空", "warning")
            return
        if len(content) > 100:
            await _toast("弹幕单条上限 100 字", "warning")
            return
        tok = await _send_token()
        if not tok:
            await _toast("未配置发送 token——展开『发送设置（全局）』填入", "warning")
            return
        state["send_inflight"].add(key)
        btn.text = "发送中"
        btn.disable()
        try:
            try:
                r = await asyncio.wait_for(
                    _api(config, "POST", "/danmaku/send", body={
                        "platform": platform, "room_id": room_id,
                        "content": content, "token": tok}, timeout=80), 15.0)
            except asyncio.TimeoutError:
                state["receipts"][key] = {"kind": "warn",
                                          "text": "发送超时（15s 无回执）——可重试", "rid": ""}
                ui.notify("发送超时——可重试", type="warning", close_button="知道了")
                return
            if not isinstance(r, dict):
                state["receipts"][key] = {"kind": "fail",
                                          "text": "发送请求失败（控制面不可达）", "rid": ""}
                ui.notify("发送请求失败（控制面不可达）", type="negative",
                          close_button="知道了", timeout=0)
                return
            _apply_receipt(key, r)
            if str(r.get("status") or "") in ("sent", "dry_run"):
                # 草稿生命周期（F6）：成功即清；失败保留
                state["send_drafts"][key] = ""
                inp.value = ""
        finally:
            state["send_inflight"].discard(key)
            btn.text = "发送"
            btn.enable()

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

    async def _save_send_platforms(e):
        # e.value 保证是新值（on_value_change 官方契约）——此前 on("update:model-value")
        # 触发时 select.value 仍是旧值，把旧白名单写回磁盘（界面已选、落盘不变的假保存）
        val = ",".join(str(x) for x in (list(e.value or []) if e is not None else []))
        # main 可达时 Settings.save 会磁盘回读覆盖 danmaku_listener 节（F-ENG-2
        # 写者分配矩阵）——直写被静默丢弃，必须走运行面窄端点（对齐 F1 房间开关）
        if state["mode"] in ("running", "service_disabled"):
            r = await _api(config, "POST", "/danmaku/send-config", {"platforms": val})
            if not isinstance(r, dict) or not r.get("success"):
                await _err_notify(r or {"error": "发送平台保存失败（运行面不可达）"})
                return
        else:
            ok = set_config_callback("danmaku_listener", "send_enabled_platforms",
                                     value=val, _notify=False)
            if ok is False:
                await _err_notify({"error": "发送平台保存失败", "fix_hint": "配置未变更"})
                return
        if state["mode"] == "running":
            await _toast("发送平台已保存——关闭再开启『启用监听』开关（重建监听）后生效", "info")
        else:
            await _toast("发送平台已保存——启动系统后生效", "info")

    async def _save_send_dry_run(e):
        live = bool(e.value) if e is not None else bool(send_live_switch.value)
        # 分流同 _save_send_platforms（F-ENG-2：main 可达时直写不落盘）
        if state["mode"] in ("running", "service_disabled"):
            r = await _api(config, "POST", "/danmaku/send-config", {"dry_run": not live})
            if not isinstance(r, dict) or not r.get("success"):
                await _err_notify(r or {"error": "发送模式保存失败（运行面不可达）"})
                return
        else:
            ok = set_config_callback("danmaku_listener", "send_dry_run",
                                     value=not live, _notify=False)
            if ok is False:
                await _err_notify({"error": "发送模式保存失败", "fix_hint": "配置未变更"})
                return
        if live:
            await _toast("已开启实发——生效后弹幕将真实发送至直播间", "warning")
        else:
            await _toast("已开启观察模式（dry_run：只记录不发送）", "info")

    # ---------- 卡①：房间管理（首屏主对象——Design D-1） ----------

    with ui.card().style(card_css):
        ui.label("房间管理").style("font-size:var(--font-size-card-title,16px);font-weight:600")
        # 发送全局态徽标（常驻卡顶——design F2/F11：可发现性；_poll 内随磁盘真相刷新）
        send_dry_badge = ui.badge("演练模式（不会真实发送）").props(
            "outline color=warning").style("display:none")
        send_token_badge = ui.badge("未配置发送 token").props(
            "outline color=negative").style("display:none")

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
                if not await _write_rooms(rooms):
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

        # 发送设置（全局）折叠区（默认折叠——design F15 归属表述；安全态全保留）
        with ui.expansion("发送设置（全局）").classes("w-full").props("dense"):
            ui.label("token 与白名单为跨房间全局配置；运行期变更在监听重建后生效").style(
                "font-size:var(--font-size-tiny,11px);color:var(--text-hint)")
            send_token_input = ui.input(
                label="发送 token（自动读取本机文件，可修改）", password=True,
                value=_disk_token()).style("width:var(--input-width-lg,400px)")
            _send_sec0 = _disk_danmaku_section()
            send_platforms_select = ui.select(
                label="发送启用平台（白名单——保存后重启监听生效）",
                options={p: _PLATFORM_LABELS[p] for p in _ALL_PLATFORMS},
                multiple=True,
                value=[p.strip() for p in str(_send_sec0.get("send_enabled_platforms") or "").split(",")
                       if p.strip()],
            ).classes("w-full")
            send_live_switch = ui.switch(
                "实发模式（关闭=观察 dry_run，仅记录不发送）",
                value=not bool(_send_sec0.get("send_dry_run", True)))
            ui.button("对账最近（按最近一次发送的 request_id）", on_click=_do_reconcile).props("outline")

    # ---------- 卡②：关键词屏蔽（J2 固定文案；运行期热生效 M-1 ③） ----------

    with ui.card().style(card_css):
        with ui.row().classes("w-full items-center justify-between"):
            ui.label("关键词屏蔽").style("font-size:var(--font-size-card-title,16px);font-weight:600")
            kw_switch = ui.switch("过滤开关", value=True)

            async def _toggle_kw(_e=None):
                state["kw_on"] = kw_switch.value
                if not await _write_keywords():
                    await _err_notify({"error": "过滤开关保存失败",
                                       "fix_hint": "开关状态未变更"})
                    return
                if state["mode"] == "running":  # M-1 ③：热载已由窄端点服务端完成
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
        if not await _write_keywords_wrap():
            await _err_notify({"error": "配置保存失败", "fix_hint": "检查磁盘/权限（状态未翻转）"})
            return
        kw_input.value = ""
        _render_kw()  # 即时渲染（DX 发现 2：不依赖轮询）
        if state["mode"] == "running":  # M-1 ③ 热载由窄端点服务端完成
            await _toast("关键词已保存并即时生效")

    async def _write_keywords_wrap() -> bool:
        """关键词写盘（失败返回 False——DX 发现 5 调用方感知）。"""
        try:
            return await _write_keywords()
        except Exception:
            logger.error("[platform_config] 关键词写盘失败", exc_info=True)
            return False

    async def _rm_kw(kw: str):
        state["kw"] = [k for k in state["kw"] if k != kw]
        if not await _write_keywords_wrap():
            await _err_notify({"error": "配置保存失败", "fix_hint": "词条未删除"})
            _render_kw()
            return
        _render_kw()

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


    send_platforms_select.on_value_change(_save_send_platforms)
    send_live_switch.on_value_change(_save_send_dry_run)

    # ---------- 登录编排（CEO F2 onboard_room 单入口） ----------

    async def _poll_relogin_status(rid: str):
        """重登编排终态轮询（v0.8.0 5 态状态机，2026-10-14）：每 3s 查询、上限 5 分钟。

        recovering=持续轮询；4 终态（completed/login_required/failed/superseded）
        停轮询+对应文案；found=False（GC 淘汰 10 条/3600s 或进程重启）立即停轮询；
        读取失败（status.read_failed/read_timeout=可重试语义）继续轮询至上限（ENG F1 分流）。
        """
        if not rid:
            return
        for _ in range(100):  # 100×3s=5 分钟上限
            await asyncio.sleep(3)
            r = await _api(config, "GET", f"/danmaku/relogin/status?id={rid}", timeout=10)
            if not isinstance(r, dict):
                continue  # 网络抖动——继续轮询
            if not r.get("success"):
                continue  # 可重试语义——继续轮询至上限
            if not r.get("found"):
                await _toast("重登状态已不可查（记录过期或系统重启）——"
                             "以实际弹窗结果为准，可重新点『登录』", "warning")
                return
            st = str(r.get("state") or "")
            if st == "recovering":
                continue
            msgs = {
                "completed": "重登完成——监听与发送登录态已恢复（一次登录双载体齐活）",
                "login_required": "重登需要登录——请在弹出的窗口完成扫码/登录",
                "failed": "重登失败——可重新点『登录』重试",
                "superseded": "该次重登已被更新的登录操作取代",
            }
            await _toast(msgs.get(st, f"重登状态：{st}"),
                         "positive" if st == "completed" else "warning")
            return
        await _toast("重登状态跟踪超时（5 分钟）——重登仍在进行或已中断，可重新点『登录』",
                     "warning")

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

    # 渲染签名（F7：单一元组集中构造——新增渲染依赖必须入签名，否则界面静默停更）
    # 依赖：房间集合/enabled/run_state(含 stalled 标记)/登录徽章/回执(kind+rid)/模式
    def _room_signature() -> str:
        items = []
        for r in _rooms():
            p, rid = str(r.get("platform")), str(r.get("room_id"))
            key = f"{p}:{rid}"
            logged = bool(login_svc.check_login(p)) if p in _CONFIG_LOGIN_PLATFORMS else True
            rec = state["receipts"].get(key) or {}
            items.append((key, bool(r.get("enabled")), _room_run_state(r), logged,
                          str(rec.get("kind", "")), str(rec.get("rid", "")),
                          state["mode"]))
        return repr(items)

    _RECEIPT_BADGE = {"ok": ("check_circle", "positive"), "dry": ("science", "info"),
                      "warn": ("error_outline", "warning"),
                      "fail": ("cancel", "negative")}

    def _render_rooms():
        # 输入态双保险（规格审查 C1）：签名不变跳过重建（焦点/值自然保留）；
        # 重建前按 platform:room 草稿快照回填（保字），焦点恢复到最后聚焦输入
        # （保字保焦——F9；光标落尾为已知取舍，选区恢复属 P3 细化）
        sig = _room_signature()
        if sig == state["rooms_render_sig"]:
            return
        for key, el in list(state["send_inputs"].items()):
            try:
                state["send_drafts"][key] = el.value
            except Exception:
                pass
        list_host.clear()
        state["send_inputs"] = {}
        state["rooms_render_sig"] = sig
        rooms = _rooms()
        import time as _time
        with list_host:
            if not rooms:
                ui.label("还没有监听房间——① 选择平台 → ② 填房间号或粘贴链接 → ③ 添加并扫码登录") \
                    .style("color:var(--text-hint);font-size:var(--font-size-body,14px)")
                return
            for r in rooms:
                p, rid = str(r.get("platform")), str(r.get("room_id"))
                key = f"{p}:{rid}"
                run_state = _room_run_state(r)
                text, color = _ROOM_STATE_TEXT[run_state]
                enabled = bool(r.get("enabled"))
                with ui.row().classes("w-full items-center q-py-xs q-px-sm gap-2").style(
                        "border:1px solid var(--border-color,#e0e0e0);border-radius:var(--radius-sm,4px)"):
                    ui.badge(_PLATFORM_LABELS.get(p, p)).props("outline")
                    ui.label(rid).style("font-size:var(--font-size-body,14px)")
                    ui.label(text).style(f"color:{color};font-size:var(--font-size-small,12px);font-weight:500")
                    if run_state == "stalled":
                        # 冻结时刻绝对时间（design F10：相对时间冻结会变成错误信息）
                        age = state["run_status"].get("last_msg_age_s") or 0
                        abs_hm = _time.strftime("%H:%M", _time.localtime(_time.time() - age))
                        ui.label(f"最近消息 {abs_hm}（暂停刷新）——确认开播或查日志") \
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
                        if not await _write_rooms(rr):  # DX 发现 5：失败不翻转
                            sw.value = not target
                            await _err_notify({"error": "配置保存失败", "fix_hint": "状态未翻转"})
                            return
                        await _toast(f"{p}:{rid} {'开始监听' if target else '已停止监听'}"
                                     + ("" if state["mode"] == "running" else "（下次启动系统后生效）"))
                    room_switch.on("update:model-value", _toggle_room)

                    # 登录（2026-10-14 v0.8.0 一次登录双载体齐活）：重登编排同步段
                    # 即时回执→后台轮询终态（监听+发送一次扫码同时恢复）
                    async def _relogin(pp=p, rd=rid):
                        if state["mode"] in ("running", "service_disabled"):
                            # v0.8.0 异步两段式：专用超时 75s（同步段最坏 >35s）
                            r = await _api(config, "POST", "/danmaku/relogin",
                                           {"platform": pp, "room_id": rd}, timeout=90)
                            if not isinstance(r, dict):
                                await _toast("重登请求失败（运行面不可达）", "negative")
                                return
                            if not r.get("success"):
                                await _err_notify(r)
                                return
                            rid_ = str(r.get("relogin_id") or "")
                            tip = str(r.get("msg") or "登录窗口已弹出——完成后自动恢复监听")
                            if rid_:
                                tip += "；进度将自动跟踪"
                            await _toast(tip, "info")
                            await _poll_relogin_status(rid_)
                        else:
                            r = login_svc.request_relogin(pp)
                            if r.get("success"):
                                await _toast("登录载体已清除——登录窗口排队弹出", "info")
                            else:
                                await _err_notify(r)
                    ui.button("登录", on_click=_relogin).props("flat dense color=warning").tooltip(
                        "清除该平台登录态并重新弹出登录窗口（一次登录同时恢复监听与发送，"
                        "完成后自动恢复监听）")

                    # 删除（运行态两步走——M-1 ②：先窄端点停再删配置；eng E2 清草稿/回执）
                    async def _remove(_e=None, pp=p, rd=rid, k=key):
                        if state["mode"] == "running":
                            await _api(config, "POST", f"/danmaku/rooms/{pp}/{rd}/enabled",
                                       {"enabled": False}, timeout=10)
                        kept = [x for x in _rooms()
                                if not (x.get("platform") == pp and str(x.get("room_id")) == rd)]
                        if not await _write_rooms(kept):
                            await _err_notify({"error": "删除保存失败", "fix_hint": "房间列表未变更"})
                            return
                        state["send_drafts"].pop(k, None)
                        state["receipts"].pop(k, None)
                        await _toast("房间已删除")
                    ui.button("删除", on_click=_remove).props("flat dense color=negative")

                # ---- 行内发送行（用户裁决=行内化；design F1/F5/F8/F13 分态） ----
                # main_down/disabled 不渲染（全页一致）；服务运行中未监听=禁用占位；
                # 未登录（配置期登录平台）=禁用+请先登录；监听中=完整发送行
                if state["mode"] != "running":
                    continue
                if run_state != "running":
                    if enabled:
                        ui.input(placeholder="房间未在监听，启动后可发送").classes(
                            "w-full q-ml-md").disable()
                    continue
                if p in _CONFIG_LOGIN_PLATFORMS and not login_svc.check_login(p):
                    ui.input(placeholder="请先登录后再发送（点上方『登录』）").classes(
                        "w-full q-ml-md").disable()
                    continue
                with ui.row().classes("w-full items-center gap-2 q-ml-md q-pl-sm").style(
                        "border-left:2px solid var(--border-color,#e0e0e0)"):
                    inp = ui.input(placeholder="输入要发送的弹幕…（回车发送）",
                                   value=state["send_drafts"].get(key, "")).classes("w-full")
                    state["send_inputs"][key] = inp
                    inp.on("focus", lambda k=key: state.__setitem__("last_focus_key", k))
                    receipt = state["receipts"].get(key)
                    if receipt:
                        icon, col = _RECEIPT_BADGE.get(str(receipt.get("kind")), ("help_outline", "grey"))
                        tip = str(receipt.get("text") or "")
                        rid_txt = str(receipt.get("rid") or "")
                        if rid_txt:
                            tip = tip + "｜request_id=" + rid_txt
                        ui.badge(icon).props("outline color=" + col).tooltip(tip)
                    send_btn = ui.button("发送")

                    async def _row_send(k=key, pp=p, rd=rid, i=inp, _btn=send_btn):
                        await _send_from_row(k, pp, rd, i, _btn)
                    send_btn.on("click", _row_send)

                    def _on_enter(_e=None, k=key, pp=p, rd=rid, i=inp, _btn=send_btn):
                        import asyncio as _aio
                        try:
                            loop = _aio.get_running_loop()
                            loop.create_task(_send_from_row(k, pp, rd, i, _btn))
                        except RuntimeError:
                            pass
                    inp.on("keydown.enter", _on_enter)

        # 焦点恢复（F9 保字保焦）：重建后聚焦最后聚焦的房间输入
        fk = state.get("last_focus_key")
        if fk and fk in state["send_inputs"]:
            _el = state["send_inputs"][fk]
            try:
                _el.run_javascript(
                    'const e=document.getElementById("c' + str(_el.id) + '"); if(e) e.focus();')
            except Exception:
                pass


    # ---------- 轮询（3s） ----------

    async def _poll():
        try:
            await _poll_inner()
        except Exception:
            logger.error("[platform_config] 轮询渲染异常（下轮重试）", exc_info=True)

    async def _poll_inner():
        # 磁盘真相（2026-10-13）：房间/关键词运行期经窄端点落盘（F-ENG-2 分流），
        # webui 内存副本不随动——列表/开关初值/关键词展示必须读磁盘，否则运行期
        # 添加的房间"落盘成功但列表不显示"。3s 一次小 JSON 读，开销可忽略。
        sec = _disk_danmaku_section()
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
            # 发送全局态徽标（design F2/F11：磁盘真相随轮询刷新）
            dry_on = bool(sec.get("send_dry_run", True))
            send_dry_badge.style("display:" + ("inline-flex" if dry_on else "none"))
            has_token = bool(_disk_token()) or bool((send_token_input.value or "").strip())
            send_token_badge.style("display:" + ("none" if has_token else "inline-flex"))
        # 开关不回读运行态（2026-10-12 语义修复）：开关=配置意图（磁盘 config
        # enabled，仅用户点击经直写/toggle 落盘变更，初值读磁盘）；监听的实际
        # 运行状态由 _render_pill 状态胶囊承担——二者职责分离。
        _render_pill()
        _render_rooms()
        _render_kw()
        # 登录进度行内呈现（Design A-1 非模态；login_chip 已弃用移除）
        ls = login_svc.get_state()
        if ls.get("status") in ("queued", "launching", "waiting_scan", "saving"):
            login_progress.text = f"登录进度：{_login_state_text()}"
            login_progress.style("display:block")
        else:
            login_progress.style("display:none")

    ui.timer(3.0, _poll)

    _render_rooms()
    _render_kw()
    _render_pill()
