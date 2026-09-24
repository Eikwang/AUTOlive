# -*- coding: UTF-8 -*-
"""
服务状态常驻条（P1-3）

挂载于侧边栏底部的托管服务胶囊区（CEO-D3）：
- 每个托管服务一个胶囊：状态色圆点 + 服务名；点选展开详情（日志尾/手动重启/帮助）
- 排序 = 异常优先 + 启动顺序；>4 折叠为 +N；置红自动置顶
- 置红态升级为胶囊区顶部常驻警示横幅（B-5，复用设计令牌，非独立通知系统）
- 置红态保留手动重启，成功清零回健康态（D10）；胶囊附"查看日志与帮助"（DX-2）
- 全部色彩使用 design_tokens 令牌变量（禁止硬编码色值，设计规范节）

数据源：utils.service_orchestrator.ServiceRegistry（main.py 启动时注册，
注册先后即启动顺序；本组件定时轮询注册表刷新，不反向依赖服务模块）。
"""
from typing import Dict

from nicegui import ui

from utils.my_log import logger
from utils.service_orchestrator import ServiceRegistry, ST_HEALTHY, ST_RED

# 状态 → 令牌色（设计规范节：禁止硬编码色值）
_STATE_COLOR = {
    "healthy": "var(--color-success)",
    "starting": "var(--color-warning)",
    "crashed": "var(--color-warning)",
    "red": "var(--color-danger)",
    "stopped": "var(--border-color)",
}
_STATE_LABEL = {
    "healthy": "运行中",
    "starting": "启动中",
    "crashed": "重拉中",
    "red": "已置红",
    "stopped": "已停止",
}
# 异常优先排序权重（D3）：越小越靠前
_STATE_ORDER = {"red": 0, "crashed": 1, "starting": 2, "healthy": 3, "stopped": 4}

_MAX_VISIBLE = 4  # D3：超过 4 个折叠为 +N


class ServiceStatusStrip:
    """侧边栏服务状态胶囊区。调用 mount(container) 后由 ui.timer 驱动刷新。"""

    def __init__(self):
        self._detail_open: Dict[str, bool] = {}  # 服务名 → 详情展开态
        self._banner_shown_for: set = set()      # 已发过置红通知的服务（防刷屏）

    # ---------- 排序与折叠 ----------

    def _sorted_services(self):
        """D3 排序：异常优先（置红置顶）+ 启动顺序（注册顺序）稳定排序。"""
        svcs = ServiceRegistry.instance().all()
        return sorted(
            enumerate(svcs),
            key=lambda pair: (_STATE_ORDER.get(pair[1].state, 9), pair[0]),
        )

    # ---------- 渲染 ----------

    def mount(self, container: ui.column):
        """在给定容器内构建静态骨架并启动 3s 定时刷新。"""
        with container:
            self._banner_area = ui.column().style("width:100%")
            self._capsules_area = ui.column().style("width:100%;gap:4px")
        ui.timer(3.0, self.refresh)

    def refresh(self):
        """定时刷新：重建胶囊区与警示横幅（服务数量少，重建开销可忽略）。"""
        try:
            self._render_banner()
            self._render_capsules()
        except Exception:
            logger.error("[service-strip] 刷新异常", exc_info=True)

    def _render_banner(self):
        """B-5：任一服务置红 → 胶囊区顶部常驻警示横幅；恢复后自动消失。"""
        self._banner_area.clear()
        reds = [s for s in ServiceRegistry.instance().all() if s.state == ST_RED]
        if not reds:
            self._banner_shown_for.clear()
            return
        with self._banner_area:
            ui.label("⚠ 有服务停止自动重拉，直播链路可能中断").style(
                "width:100%;padding:6px 8px;border-radius:6px;"
                "background:var(--color-danger);color:var(--text-on-primary);"
                "font-size:12px;"
            )
        for s in reds:
            if s.name not in self._banner_shown_for:
                self._banner_shown_for.add(s.name)
                ui.notify(
                    position="top",
                    type="negative",
                    message=f"服务 {s.name} 已置红：连续失败 {s.fail_count} 次，停止自动重拉",
                )

    def _render_capsules(self):
        svcs_sorted = self._sorted_services()
        visible = svcs_sorted[:_MAX_VISIBLE]
        collapsed = svcs_sorted[_MAX_VISIBLE:]
        self._capsules_area.clear()
        with self._capsules_area:
            for _, svc in visible:
                self._render_capsule(svc)
            if collapsed:
                ui.label(f"+{len(collapsed)} 更多服务").style(
                    "width:100%;text-align:center;font-size:11px;"
                    "color:var(--text-secondary);padding:2px 0;"
                ).on("click", lambda: self._expand_all(collapsed))
        # >4 折叠态下点击 +N 全量展开（一次性展开本轮全部，简化实现）

    def _expand_all(self, collapsed_pairs):
        """折叠展开：把超出上限的服务临时全部渲染一次（下轮刷新回归折叠）。"""
        self._capsules_area.clear()
        with self._capsules_area:
            for _, svc in self._sorted_services():
                self._render_capsule(svc)

    def _render_capsule(self, svc):
        color = _STATE_COLOR.get(svc.state, "var(--border-color)")
        label = _STATE_LABEL.get(svc.state, svc.state)
        open_now = self._detail_open.get(svc.name, False)
        exp = ui.expansion(svc.name, caption=label, value=open_now).style(
            "width:100%;border:1px solid var(--border-color);border-radius:6px;"
            "padding:0;font-size:12px;background:var(--bg-card);"
        )
        exp.on("value-change", lambda e, n=svc.name: self._remember_open(n, e.args))
        with exp:
            with ui.row().style("align-items:center;gap:6px;padding:2px 4px;"):
                ui.html(
                    f'<span style="display:inline-block;width:8px;height:8px;'
                    f'border-radius:50%;background:{color};"></span>'
                )
                ui.label(f"{label} · 失败 {svc.fail_count} 次").style("font-size:11px")
            with ui.row().style("gap:6px;padding:2px 4px;"):
                ui.button("重启", on_click=lambda s=svc: self._manual_restart(s)).props(
                    "flat dense size=sm"
                )
                ui.button("帮助", on_click=lambda: ui.notify(
                    position="top", type="info",
                    message="帮助：docs/开发环境搭建.md 含端口表与常见失败对照（DX-2）",
                )).props("flat dense size=sm")
            log_tail = svc.tail_log(8)
            ui.code(log_tail or "（暂无日志）").style(
                "max-height:120px;font-size:10px;width:100%;"
            )

    def _remember_open(self, name: str, value):
        self._detail_open[name] = bool(value)

    def _manual_restart(self, svc):
        """D10：手动重启，成功清零回健康态；结果 notify 反馈。"""
        logger.info(f"[service-strip] 手动重启 {svc.name}")
        ok = svc.manual_restart()
        ui.notify(
            position="top",
            type="positive" if ok else "negative",
            message=(
                f"服务 {svc.name} 重启成功" if ok
                else f"服务 {svc.name} 重启失败，请查看日志尾"
            ),
        )
        self.refresh()
