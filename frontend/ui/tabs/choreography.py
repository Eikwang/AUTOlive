# -*- coding: utf-8 -*-
"""
动作编排标签页（数字人二级页；2026-10-26 数字人功能优化批次）

两卡片：
1. 动作素材管理：character_dir 段清单（磁盘扫描+服务在线信息合并）；
   添加=本地视频 → 后台预处理（拆帧+人脸检测产四件套）→ 落盘 → 热上线
   （POST /edtalk_materials/publish 单飞行任务 2s 轮询）；
   删除=引用保护（编排规则/默认池/段调度表/服务当前段）拒绝时给修复动作。
2. 编排规则管理：自画面设置页迁移（/autoplan 20261021 批次 P2 的
   「卡片 3.5：动作编排管理」整体搬迁），行为不变：
   打赏/关键词/点歌 → 片段序列（上场→主线→离场）+ 情绪分级 + 模拟触发。
   无人脸段（face_ratio=0）在步骤下拉标注「画外音」。

后端契约：素材 /edtalk_materials*（main 进程 materials.py）；
编排 /choreography/*（main 进程 orchestration/api.py）；段下拉来源
EDTalk /status available_segments + segment_details。
"""
import asyncio
import copy as _copy
import json as _json

from nicegui import ui
from typing import Any, Callable, Dict

from frontend.ui.components import FormField
from frontend.ui.components.config_helper import get_nested_value


def create_choreography_tab(
    config: Dict[str, Any],
    theme_config: Dict[str, str],
    set_config_callback: Callable,
):
    """创建动作编排标签页。"""
    card_css = theme_config.get("card", "")

    def _main_api_url() -> str:
        api_port = get_nested_value(config, "api_port", default=8082)
        api_ip = get_nested_value(config, "api_ip", default="127.0.0.1")
        if str(api_ip) == "0.0.0.0":
            api_ip = "127.0.0.1"
        return f"http://{api_ip}:{api_port}"

    def _edtalk_api() -> str:
        from utils.edtalk_realtime.runtime import resolve_api_base
        return resolve_api_base(config)

    def _auth_headers() -> dict:
        token = (get_nested_value(config, "edtalk_realtime", "api_token", default="") or "").strip()
        return {"Authorization": f"Bearer {token}"} if token else {}

    # ==================================================================
    # 卡片 1：动作素材管理（添加 / 删除 / 清单）
    # ==================================================================
    with ui.card().style(card_css):
        with ui.row().style("width:100%;align-items:center;gap:12px"):
            ui.label("动作素材管理（数字人可用画面片段）").style("font-size: var(--font-size-title)")
            mat_status_label = ui.label("").style("color: var(--text-hint)")
        ui.label("添加：选本地视频 → 自动预处理（拆帧+人脸检测）→ 上线；"
                 "全段无人脸的视频会作为「画外音」素材（画面原样+语音照常）。"
                 "删除：被编排规则/默认池/段调度引用或正在渲染的素材会被拒绝并给出去处").style(
            "color: var(--text-hint); font-size: var(--font-size-body)")

        with ui.grid(columns=4):
            mat_video_input = FormField.create_input(
                label='视频文件路径（mp4/avi/mov/mkv/flv/webm/wmv）',
                value="", style="width:100%;")
            mat_seg_input = FormField.create_input(
                label='素材段名（字母/数字/下划线/短横线；编排规则按此名引用）',
                value="", style="width:100%;")
            mat_device_select = FormField.create_select(
                label='预处理设备',
                options={'cuda': 'cuda（推荐）', 'cpu': 'cpu（GPU 异常时排查用）'},
                value="cuda", style="width:100%;")
            mat_add_btn = ui.button("添加素材", on_click=lambda: _publish_material())

        mat_task_label = ui.label("").style("color: var(--text-hint); white-space: pre-wrap")
        mat_list_slot = ui.column().style("width:100%")

        async def _publish_material():
            video = (mat_video_input.value or "").strip()
            seg = (mat_seg_input.value or "").strip()
            if not video or not seg:
                ui.notify(position="top", type="warning", message="先填写视频路径与素材段名")
                return
            try:
                import requests
                resp = await asyncio.to_thread(
                    requests.post, f"{_main_api_url()}/edtalk_materials/publish",
                    json={"video_path": video, "segment": seg,
                          "device": mat_device_select.value or "cuda"},
                    timeout=8,
                )
            except Exception as e:
                ui.notify(position="top", type="negative",
                          message=f"提交失败（运行系统未启动？）：{e}")
                return
            if resp.status_code in (200, 202):
                mat_task_label.set_text(f"任务已受理：{seg}（预处理耗时与视频长度成正比）").style(
                    "color: var(--color-warning)")
                mat_add_btn.set_enabled(False)  # 单飞行：处理中禁用防叠发
            else:
                detail = ""
                try:
                    detail = (resp.json() or {}).get("detail", {})
                    if isinstance(detail, dict):
                        detail = detail.get("message") or str(detail)
                except Exception:
                    pass
                ui.notify(position="top", type="warning",
                          message=f"未受理（HTTP {resp.status_code}）：{detail}")

        async def _poll_task():
            """添加任务 2s 轮询：running 中更新进度行；终态提示+解锁+刷新清单。"""
            try:
                import requests
                resp = await asyncio.to_thread(
                    requests.get, f"{_main_api_url()}/edtalk_materials/task", timeout=2)
                t = (resp.json() or {}) if resp.status_code == 200 else {}
            except Exception:
                return
            state = t.get("state", "idle")
            if state == "running":
                seg = t.get("segment", "")
                stage = t.get("stage", "")
                mat_task_label.set_text(f"[{seg}] {stage}……").style(
                    "color: var(--color-warning)")
            elif state in ("done", "error") and mat_add_btn.enabled is False:
                ok = state == "done"
                msg = t.get("message") or ""
                mat_task_label.set_text(
                    ("完成：" + msg) if ok else ("失败：" + msg + "（fix 见消息与日志）")).style(
                    "color: " + ("var(--color-positive)" if ok else "var(--color-negative)"))
                mat_add_btn.set_enabled(True)
                if ok:
                    mat_video_input.set_value("")
                await _refresh_materials()

        async def _refresh_materials():
            """清单刷新：磁盘扫描+服务在线信息（main /edtalk_materials）。"""
            try:
                import requests
                resp = await asyncio.to_thread(
                    requests.get, f"{_main_api_url()}/edtalk_materials", timeout=6)
                data = (resp.json() or {}) if resp.status_code == 200 else {}
            except Exception as e:
                mat_status_label.set_text(f"清单获取失败：{e}").style(
                    "color: var(--color-negative)")
                return
            items = data.get("items") or []
            online = bool(data.get("service_online"))
            mat_status_label.set_text(
                f"共 {len(items)} 个素材 · 数字人服务{'运行中' if online else '未运行（磁盘清单）'}"
                + (f" · 当前段：{data.get('current_segment')}" if data.get("current_segment") else "")
            ).style("color: " + ("var(--color-positive)" if online else "var(--text-hint)"))
            _render_materials(items)

        def _render_materials(items: list):
            mat_list_slot.clear()
            with mat_list_slot:
                if not items:
                    ui.label("角色目录暂无素材——先在训练主播页完成角色训练，或在上方添加素材").style(
                        "color: var(--text-hint)")
                    return
                with ui.row().style("width:100%;gap:16px;color: var(--text-hint)"):
                    for h in ("段名", "帧数", "人脸比例", "分辨率", "引用/状态", "操作"):
                        ui.label(h).style("min-width:90px")
                for it in items:
                    with ui.row().style("width:100%;align-items:center;gap:16px"):
                        seg = it.get("segment", "?")
                        ui.label(seg).style("min-width:90px")
                        ui.label(str(it.get("total_frames") or "?")).style("min-width:90px")
                        ratio = it.get("face_ratio")
                        if ratio is None:
                            rtxt = "?"
                            rcolor = "var(--text-hint)"
                        elif ratio <= 0:
                            rtxt = "0%（画外音素材）"
                            rcolor = "var(--color-warning)"
                        else:
                            rtxt = f"{ratio * 100:.0f}%"
                            rcolor = "var(--color-positive)"
                        ui.label(rtxt).style(f"min-width:120px;color:{rcolor}")
                        res = it.get("resolution")
                        ui.label(f"{res[0]}x{res[1]}" if isinstance(res, (list, tuple)) and len(res) == 2
                                 else "?").style("min-width:90px")
                        refs = it.get("references") or []
                        tags = []
                        if it.get("is_current"):
                            tags.append("渲染中")
                        if it.get("in_scheduler"):
                            tags.append("调度中")
                        tags += refs[:2] + [f"等{len(refs)}处引用" if len(refs) > 2 else ""]
                        ui.label(" · ".join(x for x in tags if x) or "未引用").style(
                            "min-width:90px;color: var(--text-hint); max-width:340px;"
                            "overflow:hidden;text-overflow:ellipsis;white-space:nowrap")
                        ui.button("删除", on_click=lambda s=seg: _delete_material(s)).props(
                            "outline color=negative dense")

        async def _delete_material(segment: str):
            with ui.dialog() as dlg, ui.card().style(card_css):
                ui.label(f"删除素材「{segment}」？\n将从角色目录移除其全部帧与元数据"
                         "（服务运行中会先热卸载）。被引用时会被拒绝。").style(
                    "white-space: pre-wrap; max-width: 420px")
                with ui.row():
                    ui.button("删除", on_click=lambda: dlg.submit(True)).props("color=negative")
                    ui.button("取消", on_click=lambda: dlg.submit(False)).props("flat")
            if not await dlg:
                return
            try:
                import requests
                resp = await asyncio.to_thread(
                    requests.post, f"{_main_api_url()}/edtalk_materials/delete",
                    json={"segment": segment}, timeout=10,
                )
            except Exception as e:
                ui.notify(position="top", type="negative",
                          message=f"删除失败（运行系统未启动？）：{e}")
                return
            if resp.status_code == 200:
                ui.notify(position="top", type="positive", message=f"素材已删除：{segment}")
                await _refresh_materials()
            else:
                detail = ""
                try:
                    d = (resp.json() or {}).get("detail", {})
                    detail = d.get("message") if isinstance(d, dict) else str(d)
                except Exception:
                    pass
                ui.notify(position="top", type="warning",
                          message=f"删除被拒（HTTP {resp.status_code}）：{detail}")

        ui.button("刷新清单", on_click=_refresh_materials).props("flat")
        ui.timer(2.0, _poll_task)
        ui.timer(8.0, lambda: asyncio.create_task(_refresh_materials()))

    # ==================================================================
    # 卡片 2：编排规则管理（自画面设置页迁移；行为不变）
    # ==================================================================
    _choreo_avail = {"segments": [], "voiceover": set()}

    def _default_rule():
        return {"rule_id": "new_rule", "trigger_type": "gift", "match": "", "min_price": 0,
                "sequence": {
                    "enter": {"segment": "", "switch_at": "segment_end", "duration_s": 4},
                    "main": {"segment": "", "switch_at": "segment_end", "duration_s": 30},
                    "exit": {"segment": "", "switch_at": "segment_end", "duration_s": 4},
                },
                "emotion_tiers": [], "interrupt": "standard"}

    choreo_node = _copy.deepcopy(get_nested_value(config, "choreography", default={}) or {})
    if not isinstance(choreo_node, dict) or "enable" not in choreo_node:
        choreo_node = {"enable": False, "default_mode": "rotate", "default_segments": [],
                       "min_retrigger_s": 2, "min_sequence_hold_s": 5, "step_timeout_s": 60,
                       "reset_after_s": 300, "rules": []}
    choreo_node.setdefault("rules", [])
    choreo_node.setdefault("default_segments", [])

    async def _fetch_avail_segments():
        """从 EDTalk /status 拉可用段+人脸信息（步骤下拉；画外音标注数据源）。"""
        try:
            import requests
            resp = await asyncio.to_thread(
                requests.get, f"{_edtalk_api()}/status",
                headers=_auth_headers(), timeout=3,
            )
            data = resp.json() or {}
            segs = [str(s) for s in (data.get("available_segments") or [])]
            _choreo_avail["segments"] = segs
            details = data.get("segment_details") or {}
            _choreo_avail["voiceover"] = {
                s for s, d in details.items()
                if isinstance(d, dict) and (d.get("face_ratio") or 0) <= 0}
            if segs:
                ui.notify(position="top", type="positive", message=f"可用段 {len(segs)} 个已刷新")
            else:
                ui.notify(position="top", type="warning", message="EDTalk 未运行或无可用段")
        except Exception as e:
            ui.notify(position="top", type="negative", message=f"获取失败：{e}")
        _render_rules()

    def _seg_options(current: str) -> dict:
        """步骤段下拉选项：服务可用段 ∪ 规则已引用段（未拉取时至少能显示现值）。"""
        opts = list(dict.fromkeys((_choreo_avail["segments"] or []) + [current or ""]))
        out = {}
        for s in opts:
            if not s:
                continue
            out[s] = f"{s}（画外音）" if s in _choreo_avail["voiceover"] else s
        return out

    def _render_rules():
        rules_container.clear()
        with rules_container:
            for idx, rule in enumerate(choreo_node["rules"]):
                with ui.expansion(
                    f"规则 {rule.get('rule_id') or ('#' + str(idx + 1))}"
                    f"（{rule.get('trigger_type', '?')} · {rule.get('match') or '任意'}）"
                ).style("width:100%"):
                    with ui.grid(columns=4):
                        FormField.create_input(
                            label='规则 ID（唯一）', value=str(rule.get("rule_id", "")),
                            on_change=lambda e, r=rule: r.__setitem__("rule_id", (e.value or "").strip()),
                            style="width:100%;")
                        FormField.create_select(
                            label='触发类型',
                            options={'gift': '礼物打赏', 'keyword': '弹幕关键词', 'song': '点歌命中'},
                            value=str(rule.get("trigger_type", "gift")),
                            on_change=lambda e, r=rule: r.__setitem__("trigger_type", e.value),
                            tooltip="entrance/follow 为预留枚举位（P1 不订阅，需先给节流方案）",
                            style="width:100%;")
                        FormField.create_input(
                            label='匹配值（gift=礼物名全等 / keyword=子串 / song 可空=任意）',
                            value=str(rule.get("match", "")),
                            on_change=lambda e, r=rule: r.__setitem__("match", e.value or ""),
                            style="width:100%;")
                        FormField.create_input(
                            label='最低价值门槛（仅 gift，与答谢链同口径）',
                            value=str(rule.get("min_price", 0)),
                            on_change=lambda e, r=rule: r.__setitem__(
                                "min_price", float(e.value or 0) if str(e.value or "").replace(".", "", 1).isdigit() else 0),
                            style="width:100%;")
                    for step_name in ("enter", "main", "exit"):
                        step = rule.setdefault("sequence", {}).setdefault(step_name, {})
                        step.setdefault("switch_at", "segment_end")
                        with ui.row().style("width:100%;align-items:flex-end;gap:8px"):
                            ui.label({"enter": "上场", "main": "主线", "exit": "离场"}[step_name]).style(
                                "min-width:44px;color: var(--text-hint);font-size: var(--font-size-body)")
                            ui.select(
                                options=_seg_options(str(step.get("segment", ""))),
                                value=str(step.get("segment", "")),
                                label="段名",
                                on_change=lambda e, s=step: s.__setitem__("segment", e.value or ""),
                            ).style("min-width:150px").tooltip(
                                "段名=素材管理页的段名；标「画外音」的段无正脸，"
                                "播放时画面原样+语音照常（自动跳过口型）")
                            ui.select(
                                options={"segment_end": "segment_end（边界无缝）", "immediate": "immediate（立即硬切）"},
                                value=str(step.get("switch_at", "segment_end")),
                                label="切换方式",
                                on_change=lambda e, s=step: s.__setitem__("switch_at", e.value),
                            ).style("min-width:170px").tooltip("衔接短素材建议 segment_end；immediate 硬切≤一次边界")
                            FormField.create_input(
                                label='时长(s)', value=str(step.get("duration_s", "")),
                                on_change=lambda e, s=step: s.__setitem__(
                                    "duration_s", float(e.value) if str(e.value or "").replace(".", "", 1).isdigit() and e.value else None),
                                tooltip="无音轨步骤必填 >0；有音轨时语义=完成上限（F1 看门狗）",
                                style="width:90px;")
                            FormField.create_select(
                                label='音轨', options={'none': '无（TTS 照常）', 'tts': '答谢语音', 'file': '本地文件'},
                                value=str((step.get("audio") or {}).get("mode", "none")),
                                on_change=lambda e, s=step: s.__setitem__(
                                    "audio", {"mode": e.value, "path": (s.get("audio") or {}).get("path", "")}
                                    if e.value != "none" else None),
                                style="width:130px;")
                            if (step.get("audio") or {}).get("mode") == "file":
                                FormField.create_input(
                                    label='音频路径', value=str((step.get("audio") or {}).get("path", "")),
                                    on_change=lambda e, s=step: s.__setitem__(
                                        "audio", {"mode": "file", "path": e.value or ""}),
                                    tooltip="本地音频走点歌直通通道（type=song）",
                                    style="width:220px;")
                    tiers_raw = _json.dumps(rule.get("emotion_tiers", []), ensure_ascii=False)
                    ui.textarea(
                        label='情绪分级（JSON 数组；可空=单档恒用基础素材）', value=tiers_raw,
                        on_change=lambda e, r=rule: _set_tiers(r, e.value),
                    ).style("width:100%").tooltip('[{"max_count":3,"mood":"happy"},'
                                                  '{"max_count":6,"mood":"angry",'
                                                  '"segment_overrides":{"enter":"段名"}}] '
                                                  'max_count 严格递增；超出末档用最高档')
                    with ui.row():
                        ui.button("删除规则", on_click=lambda i=idx: _delete_rule(i)).props(
                            "outline color=negative")
        # GAP-3：规则增删后同步模拟触发下拉选项（控件在下方创建，首次调用晚于其定义）
        try:
            choreo_sim_select.options = {
                str(r.get("rule_id")): f"{r.get('rule_id')}（{r.get('trigger_type')}:{r.get('match', '')}）"
                for r in (choreo_node.get("rules") or [])}
            choreo_sim_select.update()
        except NameError:
            pass

    def _set_tiers(rule: dict, raw: str):
        """情绪分级 JSON 解析（非法 JSON 保持旧值+提示，防半编辑态写坏结构）。"""
        try:
            parsed = _json.loads(raw) if (raw or "").strip() else []
        except Exception:
            ui.notify(position="top", type="warning", message="情绪分级不是合法 JSON，未保存本次修改")
            return
        if not isinstance(parsed, list):
            ui.notify(position="top", type="warning", message="情绪分级需为 JSON 数组")
            return
        rule["emotion_tiers"] = parsed

    def _delete_rule(idx: int):
        try:
            choreo_node["rules"].pop(idx)
        except Exception:
            return
        _render_rules()

    async def _apply_choreography():
        """组装整节 → main /choreography/rules（校验+热生效+持久化，单写者）。"""
        try:
            import requests
            resp = await asyncio.to_thread(
                requests.post, f"{_main_api_url()}/choreography/rules",
                json=choreo_node, timeout=8,
            )
            data = (resp.json() or {}) if resp.status_code == 200 else {}
            if resp.status_code == 200 and data.get("applied"):
                bad = data.get("rejected") or []
                missing = data.get("missing_all") or []
                msg = f"已应用：{len(data.get('enabled_rules') or [])} 条生效"
                if bad:
                    msg += f"，{len(bad)} 条结构拒绝"
                if missing:
                    msg += f"，缺失段 {missing}"
                if not data.get("persisted"):
                    msg += "（⚠ 持久化失败：重启后丢失）"
                ui.notify(position="top", type="positive", message=msg)
            else:
                detail = data.get("message") or (resp.json() or {}).get("detail", {})
                ui.notify(position="top", type="warning",
                          message=f"未应用（HTTP {resp.status_code}）：{detail}")
        except Exception as e:
            ui.notify(position="top", type="negative",
                      message=f"应用失败（运行系统未启动？）：{e}")

    async def _poll_choreo_status():
        """编排状态条 5s 轮询（main API；未装配/未启动=提示态，不报错刷屏）。"""
        try:
            import requests
            resp = await asyncio.to_thread(
                requests.get, f"{_main_api_url()}/choreography/status", timeout=2)
            st = (resp.json() or {}) if resp.status_code == 200 else {}
        except Exception:
            st = {}
        if not st:
            choreo_status_label.set_text("状态：运行系统未启动或编排器未装配").style(
                "color: var(--text-hint)")
            return
        if not st.get("enable"):
            choreo_status_label.set_text("状态：已停用（开关关闭，订阅点短路直通）").style(
                "color: var(--text-hint)")
            return
        if not st.get("worker_alive", True):
            choreo_status_label.set_text(
                "状态：已启用但 worker 未运行（检查启动日志三段式：[编排] worker 启动失败）").style(
                "color: var(--color-negative)")
            return
        if st.get("state") == "sequence":
            mood = f" · {st.get('mood')}" if st.get("mood") else ""
            pend = " · 重排待执行" if st.get("pending_requeue") else ""
            remain = st.get("step_remaining_s")
            remain_txt = f" · 剩余 {remain}s" if remain is not None else ""
            text = (f"执行中：{st.get('active_rule')} · {st.get('step')}{mood}{pend}{remain_txt} · "
                    f"计数 {st.get('counters', {})} · 降级 {st.get('degrade_total', 0)}")
            choreo_status_label.set_text(text).style("color: var(--color-warning)")
        else:
            text = (f"待机（默认轮播）· 生效规则 {st.get('enabled_rules', [])} · "
                    f"计数 {st.get('counters', {})} · 降级 {st.get('degrade_total', 0)}")
            choreo_status_label.set_text(text).style("color: var(--color-positive)")
        if not st.get("ready"):
            choreo_status_label.set_text(
                choreo_status_label.text + " · ⚠ EDTalk 未就绪（规则未校验生效）").style(
                "color: var(--color-warning)")

    with ui.card().style(card_css):
        with ui.row().style("width:100%;align-items:center;gap:12px"):
            ui.label("编排规则（事件驱动画面序列）").style("font-size: var(--font-size-title)")
            choreo_status_label = ui.label("状态：未轮询").style("color: var(--text-hint)")
        ui.label("打赏/关键词/点歌 → 片段序列（上场→主线→离场）+ 情绪分级。"
                 "改动后点『应用』= 校验 + 热生效 + 写回 config（重启后按 config 装载）。"
                 "前置：EDTalk 驱动启用且服务运行（段名校验依赖 available_segments）").style(
            "color: var(--text-hint); font-size: var(--font-size-body)")
        with ui.grid(columns=6):
            ui.switch("总开关", value=bool(choreo_node.get("enable")),
                      on_change=lambda e: choreo_node.__setitem__("enable", e.value))
            FormField.create_select(
                label='默认画面模式',
                options={'rotate': '轮播（顺序）', 'random': '随机', 'single': '单段'},
                value=str(choreo_node.get("default_mode", "rotate")),
                on_change=lambda e: choreo_node.__setitem__("default_mode", e.value),
                tooltip="序列结束后恢复的默认轮播方式（承载=EDTalk 段调度器）",
                style="width:100%;")
            FormField.create_input(
                label='最短再触发间隔(s)', value=str(choreo_node.get("min_retrigger_s", 2)),
                on_change=lambda e: choreo_node.__setitem__(
                    "min_retrigger_s", float(e.value or 2) if str(e.value or "").replace(".", "", 1).isdigit() else 2),
                tooltip="每规则防连点风暴；被节流触发不计数",
                style="width:100%;")
            FormField.create_input(
                label='序列最小展示(s)', value=str(choreo_node.get("min_sequence_hold_s", 5)),
                on_change=lambda e: choreo_node.__setitem__(
                    "min_sequence_hold_s", float(e.value or 5) if str(e.value or "").replace(".", "", 1).isdigit() else 5),
                tooltip="hold 窗口内他键触发忽略（防画面乒乓，F5）",
                style="width:100%;")
            FormField.create_input(
                label='步骤超时上限(s)', value=str(choreo_node.get("step_timeout_s", 60)),
                on_change=lambda e: choreo_node.__setitem__(
                    "step_timeout_s", float(e.value or 60) if str(e.value or "").replace(".", "", 1).isdigit() else 60),
                tooltip="音轨步骤完成回调丢失时的强制推进上限（F1 看门狗）",
                style="width:100%;")
            FormField.create_input(
                label='计数窗口(s)', value=str(choreo_node.get("reset_after_s", 300)),
                on_change=lambda e: choreo_node.__setitem__(
                    "reset_after_s", float(e.value or 300) if str(e.value or "").replace(".", "", 1).lstrip("-").isdigit() else 300),
                tooltip="情绪分级计数时间窗口（GAP-2）：超过该秒数未触发则计数清零重新累计；0=禁用衰减",
                style="width:100%;")
        ui.textarea(
            label='默认画面段名池（一行一个；序列段结束后恢复轮播的池）',
            value="\n".join(str(s) for s in (choreo_node.get("default_segments") or [])),
            on_change=lambda e: choreo_node.__setitem__(
                "default_segments", [s.strip() for s in (e.value or "").splitlines() if s.strip()]),
        ).style("width:40%").tooltip("空池=不接管默认轮播（全部素材自然排序轮播）")
        ui.label("规则列表").style("font-size: var(--font-size-body)")
        rules_container = ui.column().style("width:100%")
        with ui.row():
            ui.button("新增规则", on_click=lambda: (choreo_node["rules"].append(_default_rule()),
                                                    _render_rules())).props("outline")
            ui.button("刷新可用段", on_click=_fetch_avail_segments).props("outline")
            ui.button("应用（校验+热生效+写回）", on_click=_apply_choreography)

        async def _simulate_trigger():
            """GAP-3 模拟触发（rule_id 主形态；与「应用」分行+outline 弱化防误触）。"""
            rid = choreo_sim_select.value
            if not rid:
                ui.notify(position="top", type="warning", message="先选择要模拟的规则")
                return
            try:
                import requests
                resp = await asyncio.to_thread(
                    requests.post, f"{_main_api_url()}/choreography/trigger",
                    json={"rule_id": rid}, timeout=8)
                if resp.status_code == 403:
                    ui.notify(position="top", type="negative",
                              message="403：非本机来源被拒（编排控制仅限本机）")
                    return
                data = (resp.json() or {}) if resp.status_code == 200 else {}
                if resp.status_code == 200:
                    if data.get("accepted"):
                        ui.notify(position="top", type="positive",
                                  message=f"已触发：{data.get('message')}（观察画面与状态条）")
                    else:
                        ras = data.get("retry_after_s")
                        extra = f"，{ras}s 后可重试" if ras else ""
                        ui.notify(position="top", type="warning",
                                  message=f"未触发[{data.get('reason')}]：{data.get('message')}{extra}"
                                          f" · 修复：{data.get('fix')}")
                else:
                    ui.notify(position="top", type="warning",
                              message=f"触发失败（HTTP {resp.status_code}）：{data.get('detail') or ''}")
            except Exception as e:
                ui.notify(position="top", type="negative",
                          message=f"触发失败（运行系统未启动？）：{e}")

        # 模拟触发分区（义务：与「应用」按钮分行分隔；outline 弱化——CEO S11 防误触）
        ui.label("模拟触发（闲时调试用：直播中会干扰实时编排状态——计数/hold/节流/序列打断）").style(
            "color: var(--text-hint); font-size: var(--font-size-body)")
        with ui.row():
            choreo_sim_select = ui.select(
                options={str(r.get("rule_id")): f"{r.get('rule_id')}（{r.get('trigger_type')}:{r.get('match', '')}）"
                         for r in (choreo_node.get("rules") or [])},
                label="选择规则", value=None,
            ).style("min-width:280px")
            ui.button("模拟触发", on_click=_simulate_trigger).props("outline")
        ui.label("改链提示：规则引用的段名必须存在于 EDTalk available_segments（素材管理页可添加），"
                 "否则该规则被禁用并计入资产缺口报告（启动日志三段式）").style(
            "color: var(--text-hint); font-size: var(--font-size-body)")

    _render_rules()
    ui.timer(5.0, _poll_choreo_status)
