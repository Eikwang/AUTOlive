# -*- coding: UTF-8 -*-
"""
DanmakuListener 扫码登录对话框（整合计划 G5）

- 常驻入口（login_entry 开关）：侧边栏"平台登录"按钮 + 待处理徽标（视频号扫码为常规路径）
- NEEDS_LOGIN 队列逐个呈现：平台标识 + reason_code/fix_hint 三段式 + QR 图像或登录 URL
- QR 渲染安全（义务块）：MIME 白名单（png/jpeg/gif）+ base64 大小钳制（≤64KB）+ data URL 格式校验
- 过期态：expires_at 到期呈现"已过期"，引擎重发新 NEEDS_LOGIN 自动刷新；UI 提供手动关闭出口
- 自动关闭：组件内恢复检测（engine=recovered / connected 事件）→ 关闭对话框并弹出队列下一项

数据源（整合计划 P2-3/S5 修复）：main.py 进程控制面 API /danmaku/*（webui 进程轮询）——
原 get_service_instance() 进程内轮询在双进程架构下恒为 None（服务挂 main.py 进程），
登录弹窗数据源已断裂；本版经 /danmaku/login_state 与 /danmaku/events 恢复数据流。
"""
import asyncio
from typing import Optional

import requests
from nicegui import ui

from utils.my_log import logger

# 本机控制面专用 Session：trust_env=False + 禁代理——系统代理（如 Clash 7897）
# 会让 127.0.0.1 请求耗时 2s+，同步阻塞 NiceGUI 事件循环饿死 socket.io 心跳，
# 触发页面 reload 死循环（investigate 2026-10-06 根因）。
_LOCAL_SESSION = requests.Session()
_LOCAL_SESSION.trust_env = False
_LOCAL_SESSION.proxies = {"http": None, "https": None}


def _api_base() -> str:
    try:
        from frontend.config.settings import get_config
        return f"http://127.0.0.1:{int(get_config().get('api_port', default=8000))}"
    except Exception:
        return "http://127.0.0.1:8000"


async def _api_get(path: str, timeout: float = 3.0) -> Optional[dict]:
    """轮询请求（async：requests 移入线程池，绝不阻塞事件循环）。"""
    try:
        r = await asyncio.to_thread(
            _LOCAL_SESSION.get, f"{_api_base()}{path}", timeout=timeout)
        return r.json()
    except Exception:
        return None


async def _api_post(path: str, body: Optional[dict] = None, timeout: float = 6.0) -> Optional[dict]:
    try:
        r = await asyncio.to_thread(
            _LOCAL_SESSION.post, f"{_api_base()}{path}", json=body or {}, timeout=timeout)
        return r.json()
    except Exception:
        return None

# QR 渲染安全钳制（义务块"扫码 UI 安全与状态"）
_ALLOWED_MIME = {"image/png", "image/jpeg", "image/gif"}
_MAX_B64_CHARS = 88_000  # 64KB 解码后 ≈ 87.8k base64 字符
_LOGIN_KINDS = {"needs_login", "login_active"}


def _safe_qr_data_url(interactive: dict) -> Optional[str]:
    """从 interactive_login 提取可安全渲染的 data URL；不合规返回 None。"""
    if not isinstance(interactive, dict):
        return None
    b64 = interactive.get("qr_image_b64")
    if not b64 or not isinstance(b64, str):
        return None
    if b64.startswith("data:"):
        # 完整 data URL：校验 MIME 白名单
        head = b64.split(",", 1)[0].lower()
        mime = head[5:].split(";")[0]
        if mime not in _ALLOWED_MIME or len(b64) > _MAX_B64_CHARS + 64:
            return None
        return b64
    # 裸 base64：默认按 PNG 处理
    if len(b64) > _MAX_B64_CHARS:
        return None
    import re
    if not re.fullmatch(r"[A-Za-z0-9+/=\s]+", b64):
        return None
    return f"data:image/png;base64,{b64}"


class DanmakuLoginDialog:
    """平台登录常驻入口 + 扫码对话框。调用 mount(container) 后由 ui.timer 驱动。"""

    def __init__(self):
        self._dialog_built = False
        self._shown_for_ts = 0.0  # 当前对话框呈现的登录请求时间戳（变更即重建内容）
        self._dialog = None
        self._content_area = None
        self._entry_row = None
        self._badge_label = None

    # ---------- 挂载 ----------

    def mount(self, container: ui.column):
        with container:
            self._entry_row = ui.row().classes("w-full items-center justify-between").style("gap:4px")
        self._build_entry()
        ui.timer(1.0, self.refresh)

    def _build_entry(self):
        """侧边栏入口已移除（R2 用户裁定：登录入口统一为"添加房间后自动弹窗"）。

        弹窗本体保留供运行期 NEEDS_LOGIN 自动呈现（_refresh_dialog 检测
        active 自动开）；本方法保留以兼容既有 mount() 调用链。
        """
        self._entry_row.clear()

    # ---------- 对话框 ----------

    def _ensure_dialog(self):
        if self._dialog_built:
            return
        self._dialog = ui.dialog().props("persistent minim")
        with self._dialog, ui.card().style("min-width:360px;max-width:440px"):
            ui.label("平台登录").classes("text-h6")
            self._content_area = ui.column().style("width:100%;gap:8px")
            with ui.row().classes("w-full justify-end"):
                ui.button("关闭", on_click=lambda: self._close(manual=True)).props("flat dense")
        self._dialog_built = True

    async def _open_dialog(self):
        self._ensure_dialog()
        st = await _api_get("/danmaku/login_state") or {}
        await self._render_content(st.get("active"), force=True)
        self._dialog.open()

    async def _close(self, manual: bool = False):
        """关闭对话框；手动关闭=确认当前请求处理完毕，弹出队列下一项。"""
        if manual:
            await _api_post("/danmaku/login/ack")
        if self._dialog:
            self._dialog.close()

    # ---------- 轮询刷新 ----------

    async def refresh(self):
        try:
            await self._refresh_entry()
            await self._refresh_dialog()
        except Exception:
            logger.error("[danmaku-login] 刷新异常", exc_info=True)

    async def _refresh_entry(self):
        """侧边栏入口已移除（R2 用户裁定）——保留空实现兼容 mount() 轮询链。"""
        return

    async def _refresh_dialog(self):
        """对话框开着时：呈现激活的登录请求；恢复/清空自动关闭。"""
        if not (self._dialog and self._dialog.visible):
            return
        st = await _api_get("/danmaku/login_state")
        item = (st or {}).get("active")
        if item is None:
            self._close(manual=False)
            return
        await self._render_content(item)

    async def _render_content(self, item: Optional[dict], force: bool = False):
        if item is None:
            return
        st = await _api_get("/danmaku/login_state") or {}
        if not force and self._shown_for_ts == item.get("ts"):
            return  # 内容未变化（timer 重绘节流）
        self._shown_for_ts = item.get("ts", 0)
        self._content_area.clear()
        with self._content_area:
            ui.label(f"平台：{item['platform']}").classes("text-subtitle1")
            ui.label(item["reason_code"]).style(
                "color:var(--color-danger);font-family:monospace;font-size:12px")
            ui.label(item["fix_hint"]).classes("text-body2")
            interactive = item.get("interactive") or {}
            qr = _safe_qr_data_url(interactive)
            if qr:
                ui.image(qr).style("width:220px;height:220px").classes("self-center")
            else:
                url = interactive.get("login_url")
                if url and url.startswith(("https://", "http://")):
                    ui.link("打开登录链接", url, new_tab=True)
                else:
                    ui.label("该请求未携带可渲染的二维码/链接").classes(
                        "text-body2").style("color:var(--color-warning)")
                    ui.label("请查看组件日志或运维手册完成登录").classes("text-body2")
            # 过期态（expires_at 为秒级时间戳）
            expires = interactive.get("expires_at")
            if expires:
                remain = int(expires - item.get("ts", 0))
                if remain <= 0:
                    ui.label("二维码已过期——等待引擎重发新的登录请求").classes(
                        "text-body2").style("color:var(--color-warning)")
                else:
                    ui.label(f"有效期剩余约 {remain // 60} 分 {remain % 60} 秒").classes("text-caption")
            remaining = int((st or {}).get('queue') or 0)
            if remaining:
                ui.label(f"队列中还有 {remaining} 个登录请求").classes("text-caption")
            ui.label("完成扫码后引擎将自动恢复，本窗口自动关闭").classes("text-caption").style("opacity:0.7")


class DanmakuEventNotifier:
    """G4：system 事件 → 界面通知（route_failed/backpressure/heartbeat_lost 等告警性事件）。

    独立于状态条胶囊（胶囊由 ServiceRegistry 驱动）：本组件消费 service 事件环，
    增量拉取并以 ui.notify 呈现；心跳失联/路线失效/契约不匹配为最高优先级。
    """

    _NOTIFY_TYPE = {
        "route_failed": "negative",
        "contract_mismatch": "negative",
        "zombie": "negative",
        "heartbeat_lost": "warning",
        "backpressure": "warning",
        "gap": "warning",
        "auth_error": "warning",
        "handoff": "warning",
        "engine": "info",
        "recovered": "positive",
        "connected": None,       # 静默（连接成功不打扰）
        "component_recovered": "positive",
        "count": None,           # 静默（计数快路径高频）
        "room": None,
        "gap_silent": None,
    }
    _NOTIFY_TEXT = {
        "route_failed": "弹幕路线失效",
        "contract_mismatch": "契约版本不兼容，消费者已停止",
        "zombie": "弹幕服务疑似僵死，触发重拉",
        "heartbeat_lost": "弹幕服务心跳失联",
        "backpressure": "弹幕服务背压丢弃（消费过慢）",
        "gap": "弹幕消息缺口",
        "auth_error": "弹幕服务鉴权失败",
        "handoff": "弹幕消费者连续连接失败，移交托管层",
        "recovered": "弹幕服务已恢复",
        "component_recovered": "弹幕组件内部恢复",
    }

    def __init__(self):
        self._last_seq = 0

    def mount(self, container: ui.column):
        ui.timer(2.0, self.refresh)
        self._container = container

    async def refresh(self):
        data = await _api_get(f"/danmaku/events?since={self._last_seq}")
        if not isinstance(data, dict):
            return
        for event in (data.get("events") or []):
            self._last_seq = max(self._last_seq, int(event.get("seq") or 0))
            kind = event["kind"]
            ntype = self._NOTIFY_TYPE.get(kind)
            if ntype is None:
                continue  # 静默事件
            text = self._NOTIFY_TEXT.get(kind, kind)
            data = event.get("data") or {}
            detail = data.get("reason_code") or data.get("detail") or ""
            if kind == "gap":
                detail = f"{data.get('window_start')}~{data.get('window_end')} ({data.get('reason')})"
            ui.notify(f"{text}" + (f"：{detail}" if detail else ""), type=ntype, close_button="知道了")
