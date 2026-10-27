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
   总开关在二级菜单（enable_paths.py 注册 choreography → choreography.enable；
   关闭态顶部状态条由 layout.py DG-B1 统一渲染）。

后端契约（2026-10-27 动作编排完善批次）：素材 /edtalk_materials* 挂 webui 进程
（素材管理&创建=独立线程，进 webui 即可管理、无需启动主系统）；
编排 /choreography/*（main 进程 orchestration/api.py，编排器在主进程跑）；
段下拉来源 EDTalk /status available_segments + segment_details。
"""
import asyncio
import copy as _copy
import json as _json

from nicegui import ui
from typing import Any, Callable, Dict

from frontend.ui.components import FormField
from frontend.ui.components.config_helper import get_nested_value
from frontend.ui.components.local_file_picker import LocalFilePicker


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

    def _webui_api_url() -> str:
        """webui 进程自身 API（素材管理端点所在进程——独立线程，不依赖主系统）。"""
        webui_port = get_nested_value(config, "webui", "port", default=8086)
        return f"http://127.0.0.1:{webui_port}"

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
            ui.label("动作素材管理（画面资产库）").style("font-size: var(--font-size-title)")
            mat_status_label = ui.label("").style("color: var(--text-hint)")
        ui.label("添加：选本地视频 → 指定类型（上场/主线/离场）→ 勾选口型推理决定预处理链"
                 "（勾选=拆帧+人脸检测；不勾选=仅拆序列帧，播放时作画外音直通）→ 上线。"
                 "删除：被编排规则/默认池/段调度引用或正在渲染的素材会被拒绝并给出去处").style(
            "color: var(--text-hint); font-size: var(--font-size-body)")

        # 视频选择（文件选择器取代手输路径；防重入沿训练页 LocalFilePicker 模式）
        _picked_video = [""]
        _picker_open = [False]

        async def _open_video_picker():
            if _picker_open[0]:
                return
            _picker_open[0] = True
            mat_pick_btn.set_enabled(False)
            try:
                result = await LocalFilePicker(multiple=False, title="选择素材视频")
            finally:
                _picker_open[0] = False
                mat_pick_btn.set_enabled(True)
            if result:
                _picked_video[0] = str(result[0])
                mat_picked_label.set_text(f"已选：{result[0].name}").tooltip(
                    str(result[0])).style("color: var(--text-body); font-size: var(--font-size-body)")

        with ui.row().style("width:100%;align-items:center;gap:10px"):
            mat_pick_btn = ui.button("选择视频文件", icon="movie", on_click=_open_video_picker).props("outline")
            mat_picked_label = ui.label("未选择视频").style(
                "color: var(--text-hint); font-size: var(--font-size-body); "
                "max-width:420px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap")
        with ui.grid(columns=3):
            mat_seg_input = FormField.create_input(
                label='素材段名（字母/数字/下划线/短横线；编排规则按此名引用）',
                value="", style="width:100%;")
            mat_type_select = FormField.create_select(
                label='素材类型（规则步骤按此过滤候选）',
                options={'main': '主线（常规画面）', 'enter': '上场（规则入场段）',
                         'exit': '离场（规则收尾段）'},
                value="main", style="width:100%;")
            mat_device_select = FormField.create_select(
                label='预处理设备',
                options={'cuda': 'cuda（推荐）', 'cpu': 'cpu（GPU 异常时排查用）'},
                value="cuda", style="width:100%;")
        with ui.row().style("width:100%;align-items:center;gap:12px"):
            mat_lip_checkbox = ui.checkbox(
                "口型推理预处理（勾选=完整人脸链；不勾选=画外音直通素材）", value=True)
            mat_add_btn = ui.button("添加素材", on_click=lambda: _publish_material())

        mat_task_label = ui.label("").style("color: var(--text-hint); white-space: pre-wrap")
        mat_list_slot = ui.column().style("width:100%")

        async def _publish_material():
            video = _picked_video[0]
            seg = (mat_seg_input.value or "").strip()
            if not video or not seg:
                ui.notify(position="top", type="warning", message="先选择视频文件并填写素材段名")
                return
            try:
                import requests
                resp = await asyncio.to_thread(
                    requests.post, f"{_webui_api_url()}/edtalk_materials/publish",
                    json={"video_path": video, "segment": seg,
                          "device": mat_device_select.value or "cuda",
                          "material_type": mat_type_select.value or "main",
                          "lip_flag": bool(mat_lip_checkbox.value)},
                    timeout=8,
                )
            except Exception as e:
                ui.notify(position="top", type="negative",
                          message=f"提交失败（webui 服务异常？）：{e}")
                return
            if resp.status_code in (200, 202):
                mat_task_label.set_text(f"任务已受理：{seg}（预处理耗时与视频长度成正比）").style(
                    "color: var(--color-warning)")
                mat_add_btn.set_enabled(False)  # 单飞行：处理中禁用防叠发（R-dsg6 忙碌指示）
            else:
                detail = ""
                try:
                    detail = (resp.json() or {}).get("detail", {})
                    if isinstance(detail, dict):
                        detail = detail.get("message") or str(detail)
                except Exception:
                    pass
                # 人脸检测失败导流（R-dsg6：可行动文案指引不勾选口型推理）
                ui.notify(position="top", type="warning",
                          message=f"未受理（HTTP {resp.status_code}）：{detail}")

        async def _poll_task():
            """添加任务 2s 轮询：running 中更新进度行；终态提示+解锁+刷新清单。"""
            try:
                import requests
                resp = await asyncio.to_thread(
                    requests.get, f"{_webui_api_url()}/edtalk_materials/task", timeout=2)
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
                    _picked_video[0] = ""
                    mat_picked_label.set_text("未选择视频").style(
                        "color: var(--text-hint); font-size: var(--font-size-body)")
                await _refresh_materials()

        async def _refresh_materials():
            """清单刷新：磁盘扫描+服务在线信息（webui /edtalk_materials——独立线程）。"""
            try:
                import requests
                resp = await asyncio.to_thread(
                    requests.get, f"{_webui_api_url()}/edtalk_materials", timeout=6)
                data = (resp.json() or {}) if resp.status_code == 200 else {}
            except Exception as e:
                # 连接拒绝=webui 自身服务未就绪（异常态而非主系统未启动）
                import requests as _rq
                if isinstance(e, _rq.exceptions.ConnectionError):
                    mat_status_label.set_text(
                        f"webui 服务未就绪（{_webui_api_url()} 无法连接）").style(
                        "color: var(--color-warning)")
                else:
                    mat_status_label.set_text(f"清单获取失败：{type(e).__name__}: {e}").style(
                        "color: var(--color-negative)")
                return
            items = data.get("items") or []
            online = bool(data.get("service_online"))
            # 素材类型化清单 → 规则卡下拉候选源（R-dsg12③：sidecar 主源按类型过滤）
            try:
                _choreo_avail["typed"] = {"items": items}
            except NameError:
                pass
            mat_status_label.set_text(
                f"共 {len(items)} 个素材 · 数字人服务{'运行中' if online else '未运行（磁盘清单）'}"
                + (f" · 当前段：{data.get('current_segment')}" if data.get("current_segment") else "")
            ).style("color: " + ("var(--color-positive)" if online else "var(--text-hint)"))
            # 迁移首启横幅（R-dsg5：一次性告知定型/冲突总量；冲突项标冲突徽标）
            mig = data.get("migration") or {}
            if mig.get("typed"):
                msg = (f"已按既有规则为 {mig['typed']} 个存量素材定型类型"
                       f"（未引用素材默认主线）")
                if mig.get("conflicts"):
                    msg += f"；{mig['conflicts']} 个素材存在跨类引用，需在列表中人工改类型"
                ui.notify(position="top", type="info", message=msg, duration=8)
            _render_materials(items)

        def _material_badges(it: dict) -> str:
            """素材行徽标系（R-dsg2）：口型/特写/冲突（空格分隔，随类型徽标并存）。"""
            badges = []
            if it.get("lip_flag") is False:
                badges.append("画外音")
            if it.get("closeup"):
                badges.append("⚠头部特写")
            if it.get("conflict"):
                badges.append("跨类冲突")
            return " ".join(badges)

        def _render_materials(items: list):
            mat_list_slot.clear()
            with mat_list_slot:
                if not items:
                    ui.label("角色目录暂无素材——先在训练主播页完成角色训练，或在上方添加素材").style(
                        "color: var(--text-hint)")
                    return
                by_type = {"enter": [], "main": [], "exit": []}
                for it in items:
                    by_type.setdefault(str(it.get("type") or "main"), []).append(it)
                type_names = {"enter": "上场", "main": "主线", "exit": "离场"}
                for tname in ("enter", "main", "exit"):
                    group = by_type.get(tname) or []
                    with ui.row().style("width:100%;gap:10px;align-items:center;"
                                        "margin-top:8px"):
                        ui.label(f"{type_names[tname]}").style(
                            "min-width:52px;font-size: var(--font-size-body)")
                        ui.label(f"{len(group)} 个").style(
                            "color: var(--text-hint);font-size: var(--font-size-body)")  # 计数徽标（T9）
                    if not group:
                        ui.label(f"暂无{type_names[tname]}素材——上传时类型选「{type_names[tname]}」").style(
                            "color: var(--text-hint); margin-left:52px")  # R-dsg12① 分组空态
                        continue
                    with ui.row().style("width:100%;gap:16px;color: var(--text-hint);margin-left:52px"):
                        for h in ("段名", "徽标", "时长", "帧数", "人脸比例", "引用/状态", "改类型", "操作"):
                            ui.label(h).style("min-width:80px")
                    for it in group:
                        seg = it.get("segment", "?")
                        with ui.row().style("width:100%;align-items:center;gap:16px;margin-left:52px"):
                            ui.label(seg).style("min-width:80px")
                            badges = _material_badges(it)
                            lab = ui.label(badges or "—").style("min-width:120px")
                            if "⚠头部特写" in badges:
                                lab.tooltip("输出将只有头部区域，请更换完整画面源并重训/重传"
                                            "（前往训练主播页重训）；部分特写素材可能不触发此警告"
                                            "——R-eng8 召回率边界")  # F-2/R-dsg2/R-dsg12⑥
                            dur = it.get("duration_s")
                            ui.label(f"{dur:.1f}s" if isinstance(dur, (int, float)) else "时长未知").style(
                                "min-width:80px")
                            ui.label(str(it.get("total_frames") or "?")).style("min-width:80px")
                            ratio = it.get("face_ratio")
                            if ratio is None:
                                rtxt, rcolor = "?", "var(--text-hint)"
                            elif ratio <= 0:
                                rtxt, rcolor = "0%（画外音）", "var(--color-warning)"
                            else:
                                rtxt, rcolor = f"{ratio * 100:.0f}%", "var(--color-positive)"
                            ui.label(rtxt).style(f"min-width:90px;color:{rcolor}")
                            refs = it.get("references") or []
                            tags = []
                            if it.get("is_current"):
                                tags.append("渲染中")
                            if it.get("in_scheduler"):
                                tags.append("调度中")
                            tags += refs[:2] + [f"等{len(refs)}处引用" if len(refs) > 2 else ""]
                            ui.label(" · ".join(x for x in tags if x) or "未引用").style(
                                "min-width:110px;color: var(--text-hint); max-width:280px;"
                                "overflow:hidden;text-overflow:ellipsis;white-space:nowrap")
                            # 改类型（R-dsg3：纯元数据即时生效）
                            ui.select(
                                options={'enter': '上场', 'main': '主线', 'exit': '离场'},
                                value=str(it.get("type") or "main"),
                                on_change=lambda e, s=seg: _change_type(s, e.value),
                            ).style("min-width:96px")
                            ui.button("重处理", on_click=lambda s=seg, lf=bool(
                                it.get("lip_flag", True)): _reprocess(s, lf)).props(
                                "outline dense").tooltip("按口型推理目标重跑预处理链并热上线"
                                "（当前播放中的素材会被拒绝——先切走）")
                            ui.button("删除", on_click=lambda s=seg: _delete_material(s)).props(
                                "outline color=negative dense")

        async def _change_type(segment: str, new_type: str):
            """改类型（R-dsg3：纯元数据即时生效+清单刷新）。"""
            try:
                import requests
                resp = await asyncio.to_thread(
                    requests.post, f"{_webui_api_url()}/edtalk_materials/set_meta",
                    json={"segment": segment, "type": new_type}, timeout=6)
                if resp.status_code == 200:
                    ui.notify(position="top", type="positive",
                              message=f"已改类型：{segment} → {new_type}")
                    await _refresh_materials()
                else:
                    detail = (resp.json() or {}).get("detail", {})
                    ui.notify(position="top", type="warning",
                              message=f"改类型被拒（HTTP {resp.status_code}）：{detail}")
            except Exception as e:
                ui.notify(position="top", type="negative", message=f"改类型失败：{e}")

        async def _reprocess(segment: str, current_lip: bool):
            """按口型推理重处理（R-dsg3：切到相反链；R-eng4② 非当前段保护由后端执行）。"""
            target = not current_lip
            try:
                import requests
                resp = await asyncio.to_thread(
                    requests.post, f"{_webui_api_url()}/edtalk_materials/reprocess",
                    json={"segment": segment, "lip_flag": target}, timeout=8)
                if resp.status_code in (200, 202):
                    ui.notify(position="top", type="positive",
                              message=f"重处理已受理：{segment}（口型推理={'开' if target else '关'}）")
                else:
                    detail = (resp.json() or {}).get("detail", {})
                    msg = detail.get("message") if isinstance(detail, dict) else str(detail)
                    ui.notify(position="top", type="warning",
                              message=f"重处理被拒（HTTP {resp.status_code}）：{msg}")
            except Exception as e:
                ui.notify(position="top", type="negative", message=f"重处理失败：{e}")

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
                    requests.post, f"{_webui_api_url()}/edtalk_materials/delete",
                    json={"segment": segment}, timeout=10,
                )
            except Exception as e:
                ui.notify(position="top", type="negative",
                          message=f"删除失败（webui 服务异常？）：{e}")
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
    _choreo_avail = {"segments": [], "voiceover": set(), "typed": {}}

    def _default_rule():
        """新语义规则（R-ceo4：pred_version=new——时长/音轨由系统接管）。"""
        return {"rule_id": "new_rule", "trigger_type": "gift", "match": "", "min_price": 0,
                "pred_version": "new",
                "sequence": {
                    "enter": {"segment": "", "switch_at": "segment_end"},
                    "main": {"segment": "", "switch_at": "segment_end"},
                    "exit": {"segment": "", "switch_at": "segment_end"},
                },
                "emotion_tiers": [], "interrupt": "standard"}

    choreo_node = _copy.deepcopy(get_nested_value(config, "choreography", default={}) or {})
    if not isinstance(choreo_node, dict) or "enable" not in choreo_node:
        choreo_node = {"enable": False, "default_mode": "rotate", "default_segments": [],
                       "step_timeout_s": 60, "reset_after_s": 300, "rules": []}
    choreo_node.setdefault("rules", [])
    choreo_node.setdefault("default_segments", [])
    # 退役键不再写出（R-ceo4/R-ceo5：min_retrigger_s/min_sequence_hold_s 已退役——
    # 旧 config 读取兼容一代（config_model 退役告警），本页应用即清除）

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

    def _seg_options(current: str, want_type: str = None) -> dict:
        """步骤段下拉选项（R-dsg12③）：sidecar 素材清单按类型过滤 ∪ 规则已引用段
        ∪ 服务可用段。EDTalk 不可达不阻塞编辑（素材清单为主源）。"""
        opts = []
        for it in (_choreo_avail.get("typed") or {}).get("items", []):
            if want_type is None or it.get("type") == want_type:
                opts.append(str(it.get("segment")))
        opts += _choreo_avail["segments"] or []
        opts.append(current or "")
        seen, out = set(), {}
        for s in opts:
            if not s or s in seen:
                continue
            seen.add(s)
            out[s] = f"{s}（画外音）" if s in _choreo_avail["voiceover"] else s
        return out

    def _render_rules():
        rules_container.clear()
        with rules_container:
            for idx, rule in enumerate(choreo_node["rules"]):
                is_new = str(rule.get("pred_version", "legacy")) == "new"
                tiers = rule.get("emotion_tiers") or []
                tier_note = f" · {len(tiers) + 1} 档" if tiers else ""
                with ui.expansion(
                    f"规则 {rule.get('rule_id') or ('#' + str(idx + 1))}"
                    f"（{rule.get('trigger_type', '?')} · {rule.get('match') or '任意'}）"
                    f"{' · 新语义' if is_new else ' · 存量语义（重存后转新）'}{tier_note}"
                ).style("width:100%"):
                    if not is_new:
                        ui.label("⚠ 存量语义规则：维持旧时长/音轨判定直至本页重存；"
                                 "重存后转新语义（时长=素材时长，音轨由系统判断）").style(
                            "color: var(--color-warning); font-size: var(--font-size-body)")
                    # 第一行：规则ID | 触发类型 | 匹配值 | 价值门槛 | 触发次数摘要（R-dsg1）
                    with ui.grid(columns=5):
                        FormField.create_input(
                            label='规则 ID（唯一）', value=str(rule.get("rule_id", "")),
                            on_change=lambda e, r=rule: r.__setitem__("rule_id", (e.value or "").strip()),
                            style="width:100%;")
                        FormField.create_select(
                            label='触发类型',
                            options={'gift': '礼物打赏', 'keyword': '弹幕关键词', 'song': '点歌命中'},
                            value=str(rule.get("trigger_type", "gift")),
                            on_change=lambda e, r=rule: r.__setitem__("trigger_type", e.value),
                            tooltip="song 触发=接管（歌曲音频走既有链路，主线保持至歌终）；"
                                    "gift/keyword 新语义=不接管（原生答谢照常）",
                            style="width:100%;")
                        FormField.create_input(
                            label='匹配值（gift=礼物名全等 / keyword=子串 / song 可空=任意）',
                            value=str(rule.get("match", "")),
                            on_change=lambda e, r=rule: r.__setitem__("match", e.value or ""),
                            style="width:100%;")
                        FormField.create_input(
                            label='价值门槛（仅 gift；0=不限）',
                            value=str(rule.get("min_price", 0)),
                            on_change=lambda e, r=rule: r.__setitem__(
                                "min_price", float(e.value or 0) if str(e.value or "").replace(".", "", 1).isdigit() else 0),
                            style="width:100%;")
                        # 打断模式（2026-10-27 批次：优先级排序联动——相互依赖，
                        # 由「切换方式」+权重队列配合完成灵活动态画面控制）
                        ui.select(
                            options={"standard": "打断当前（触发即切）",
                                     "queue": "排队等待（当前序列播完再切）"},
                            value=str(rule.get("interrupt", "standard")),
                            label="多规则触发方式",
                            on_change=lambda e, r=rule: r.__setitem__("interrupt", e.value),
                        ).style("width:100%;").tooltip(
                            "打断当前=新触发立即切换画面（高权重规则用）；排队等待=低权重"
                            "触发进入队列，等待当前片段播放完成后再切换执行（配合优先级"
                            "排序权重使用，如点歌权重低于动态文案时排队）")
                        # 触发次数摘要（2026-10-27 批次：计次进档「从第 N 次起」）
                        tier_mins = [str(t.get("min_count", t.get("max_count"))) for t in tiers]
                        n_label = ("档位：从第 " + "/从第 ".join(tier_mins) + " 次起"
                                   if tier_mins else "基础档（无覆盖档）")
                        ui.label(n_label).style(
                            "color: var(--text-hint); align-self:center; "
                            "font-size: var(--font-size-body)").tooltip(
                            "计次进档：设定时间内或主线片段播放完成前再次触发进入档位计次，"
                            "从第 N 次起的触发使用该档片段（示例：档位一 1 次/档位二 4 次/"
                            "档位三 7 次 → 第 1~3 次档一、第 4~6 次档二、第 7 次起档三）")
                    # 第二行：上场|主线|离场（2026-10-27 批次：主线必选+设切换方式；
                    # 上场/离场=可选过渡片段，固定播完接下一步，无切换方式——R-dsg1）
                    for step_name in ("enter", "main", "exit"):
                        step = rule.setdefault("sequence", {}).setdefault(step_name, {})
                        want_type = {"enter": "enter", "main": "main", "exit": "exit"}[step_name]
                        is_optional = step_name != "main"
                        dur = step.get("duration_s")
                        dur_txt = (f"素材时长 {dur:.1f}s" if isinstance(dur, (int, float))
                                   else ("素材时长（重存后自动）" if is_new else "时长未知"))
                        with ui.row().style("width:100%;align-items:flex-end;gap:8px"):
                            ui.label({"enter": "上场", "main": "主线", "exit": "离场"}[step_name]
                                     + ("（可选）" if is_optional else "（必选）")).style(
                                "min-width:74px;color: var(--text-hint);font-size: var(--font-size-body)")
                            ui.select(
                                options=_seg_options(str(step.get("segment", "")), want_type),
                                value=str(step.get("segment", "")) or None,
                                label=("过渡片段（可空）" if is_optional else "主线片段"),
                                clearable=is_optional,
                                on_change=lambda e, s=step: s.__setitem__("segment", e.value or ""),
                            ).style("min-width:180px").tooltip(
                                "候选=该类型素材（素材管理页三列表）∪ 已引用段；"
                                "标「画外音」的段播放时画面原样+语音照常；"
                                + ("过渡片段可空=跳过该步（如默认画面→主线直连）" if is_optional
                                   else "主线片段必选"))
                            if is_new and step_name == "main" and rule.get("trigger_type") == "song":
                                dur_txt += "（歌曲未结束前主线素材循环播放）"  # R-dsg7/F-12
                            ui.label(dur_txt).style(
                                "min-width:150px;color: var(--text-hint);"
                                "font-size: var(--font-size-body)").tooltip(
                                "播放时长=素材时长（X2 系统接管）；时长未知=允许保存+生效待就绪"
                                "（R-eng6 两分法）" if is_new else "存量规则保留手工时长")
                            if is_optional:
                                ui.label("过渡片段·播完接下一步").style(
                                    "min-width:170px;color: var(--text-hint);"
                                    "font-size: var(--font-size-body)").tooltip(
                                    "上场/离场是片段间过渡片段，不设切换方式（固定 segment_end）")
                            else:
                                ui.select(
                                    options={"segment_end": "无缝切换（播完后切）",
                                             "immediate": "立刻切换（触发即切）"},
                                    value=str(step.get("switch_at", "segment_end")),
                                    label="切换方式",
                                    on_change=lambda e, s=step: s.__setitem__("switch_at", e.value),
                                ).style("min-width:170px").tooltip(
                                    "无缝切换=等当前片段播放完成后再切换；"
                                    "立刻切换=触发新规则后立即切换（用户裁决 2026-10-27：仅主线可设）")
                            if not is_new:
                                # 存量规则旧编辑器（时长/音轨——R-ceo4 零静默直至重存）
                                FormField.create_input(
                                    label='时长(s)', value=str(step.get("duration_s", "")),
                                    on_change=lambda e, s=step: s.__setitem__(
                                        "duration_s", float(e.value) if str(e.value or "").replace(".", "", 1).isdigit() and e.value else None),
                                    style="width:90px;")
                                FormField.create_select(
                                    label='音轨', options={'none': '无（TTS 照常）', 'tts': '答谢语音', 'file': '本地文件'},
                                    value=str((step.get("audio") or {}).get("mode", "none")),
                                    on_change=lambda e, s=step: s.__setitem__(
                                        "audio", {"mode": e.value, "path": (s.get("audio") or {}).get("path", "")}
                                        if e.value != "none" else None),
                                    style="width:130px;")
                    # 档位编辑器（R-dsg4 D2-1/2/3/4：第 1 行=基础档同源，第 2 行起=覆盖档）
                    ui.label("触发次数档位（第 1 行=基础档与上方三下拉同源；覆盖档留空=沿用基础档；"
                             "首覆盖档阈值须 ≥2——=1 会在首次触发即遮蔽基础档）").style(
                        "color: var(--text-hint); font-size: var(--font-size-body); margin-top:6px")
                    _render_tier_editor(rule)
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

    def _render_tier_editor(rule: dict):
        """多档编辑器（R-dsg4/R-dsg12④：结构化档位行，取代 JSON textarea）。

        第 1 行=基础档摘要（同源展示非第二控件实例）；第 2 行起=覆盖档
        （min_count「从第 N 次起」+三下拉继承哨兵+mood）；增删档按钮。
        2026-10-27 批次：计次语义=下界（从第 N 次起），存量 max_count 上界
        数据读取兼容（首档→1、后续→前档 max+1），保存统一写 min_count。
        """
        base = rule.setdefault("sequence", {})
        with ui.row().style("width:100%;align-items:center;gap:8px;margin-left:44px"):
            ui.label("基础档").style("min-width:64px;color: var(--text-hint)")
            ui.label("第 1 次起").style("min-width:70px;color: var(--text-hint)")
            for n in ("enter", "main", "exit"):
                seg = ((base.get(n) or {}).get("segment") or "（未选）")
                ui.label(f"{ {'enter': '上场', 'main': '主线', 'exit': '离场'}[n] }: {seg}").style(
                    "min-width:150px;color: var(--text-hint); font-size: var(--font-size-body)")
        tiers = rule.setdefault("emotion_tiers", [])
        for ti, tier in enumerate(tiers):
            ov = tier.setdefault("segment_overrides", {})
            # 旧上界数据读取兼容（不回写原键，保存时统一为 min_count）
            legacy = tier.get("min_count")
            if legacy is None and tier.get("max_count") is not None:
                legacy = 1 if ti == 0 else int(tiers[ti - 1].get("max_count", 0)) + 1
            with ui.row().style("width:100%;align-items:flex-end;gap:8px;margin-left:44px"):
                ui.label(f"档位 {ti + 1}").style("min-width:64px;color: var(--text-hint)")
                FormField.create_input(
                    label='从第 N 次起', value=str(legacy if legacy is not None else ""),
                    on_change=lambda e, tr=tier: tr.__setitem__(
                        "min_count", int(e.value) if str(e.value or "").strip().isdigit() else 0),
                    tooltip="从第 N 次触发起使用本档片段（须严格递增；=1 则首次触发即用本档）；"
                            "设定时间内或主线片段播放完成前再次触发进入档位计次",
                    style="width:110px;")
                for n in ("enter", "main", "exit"):
                    cur = str(ov.get(n, "") or "")
                    ui.select(
                        options=dict([("（继承基础档）", "")] + list(_seg_options(cur).items())),
                        value=cur,
                        label={"enter": "上场", "main": "主线", "exit": "离场"}[n],
                        on_change=lambda e, o=ov, k=n: (
                            o.__setitem__(k, e.value) if e.value else o.pop(k, None)),
                    ).style("min-width:170px").tooltip("留空=沿用基础档（部分覆盖语义）")
                FormField.create_input(
                    label='mood（可空）', value=str(tier.get("mood", "")),
                    on_change=lambda e, tr=tier: tr.__setitem__("mood", e.value or ""),
                    style="width:110px;")
                ui.button("删档", on_click=lambda i=ti: (_del_tier(rule, i))).props(
                    "outline color=negative dense")
        with ui.row().style("margin-left:44px"):
            ui.button("加一档", on_click=lambda: _add_tier(rule)).props("outline dense")

    def _add_tier(rule: dict):
        tiers = rule.setdefault("emotion_tiers", [])
        prev = tiers[-1].get("min_count") if tiers and tiers[-1].get("min_count") is not None \
            else (int(tiers[-1].get("max_count", 0)) + 1 if tiers else 1)
        tiers.append({"min_count": int(prev) + 1, "mood": "", "segment_overrides": {}})
        _render_rules()

    def _del_tier(rule: dict, idx: int):
        try:
            rule.setdefault("emotion_tiers", []).pop(idx)
        except Exception:
            return
        _render_rules()

    def _delete_rule(idx: int):
        try:
            choreo_node["rules"].pop(idx)
        except Exception:
            return
        _render_rules()

    async def _apply_choreography():
        """组装整节 → main /choreography/rules（校验+热生效+持久化，单写者）。"""
        # 总开关已迁二级菜单（enable_paths.py）：choreo_node 为进页快照，
        # 应用前回读实时 config 的 enable，防菜单开关刚切换被快照旧值回写翻转
        choreo_node["enable"] = bool(get_nested_value(config, "choreography", "enable", default=False))
        choreo_node.pop("min_retrigger_s", None)        # R-ceo5 退役键不再写出
        choreo_node.pop("min_sequence_hold_s", None)    # R-ceo5/uc2a 退役键不再写出
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
                ui.notify(position="top", type="positive", message=msg)  # 已生效
            elif resp.status_code == 503:
                # R-dsg6 两态提示（R-eng6① C5-1 语义）：已保存但 EDTalk 不可达暂未生效
                detail = data.get("message") or "EDTalk 不可达"
                ui.notify(position="top", type="warning", duration=8,
                          message=f"已保存、暂未生效（EDTalk 不可达，就绪后重试应用）：{detail}")
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
        ui.label("打赏/关键词/点歌 → 片段序列（上场→主线→离场）+ 触发次数轮换档位。"
                 "改动后点『应用』= 校验 + 热生效 + 写回 config（重启后按 config 装载）。"
                 "⚠ 触发命中立即切换，无最短保持保护（最短再触发间隔/序列最小展示已按"
                 "用户裁决退役；去重由弹幕层承担）——R-dsg7/R-ceo5").style(
            "color: var(--text-hint); font-size: var(--font-size-body)")
        with ui.grid(columns=3):
            FormField.create_select(
                label='默认画面模式',
                options={'rotate': '轮播（顺序）', 'random': '随机', 'single': '单段'},
                value=str(choreo_node.get("default_mode", "rotate")),
                on_change=lambda e: choreo_node.__setitem__("default_mode", e.value),
                tooltip="序列结束后恢复的默认轮播方式（承载=EDTalk 段调度器）",
                style="width:100%;")
            FormField.create_input(
                label='步骤超时上限(s)——步骤推进保险丝',
                value=str(choreo_node.get("step_timeout_s", 60)),
                on_change=lambda e: choreo_node.__setitem__(
                    "step_timeout_s", float(e.value or 60) if str(e.value or "").replace(".", "", 1).isdigit() else 60),
                tooltip="F3 调查结论（2026-10-27）：当前步完成上限——防 segment_end 等待悬挂"
                        "卡死序列（materialized 于 current_deadline，monotonic）；新语义规则"
                        "素材时长未知时亦以此兜底推进。建议保持默认 60s",
                style="width:100%;")
            FormField.create_input(
                label='计数窗口(s)', value=str(choreo_node.get("reset_after_s", 300)),
                on_change=lambda e: choreo_node.__setitem__(
                    "reset_after_s", float(e.value or 300) if str(e.value or "").replace(".", "", 1).lstrip("-").isdigit() else 300),
                tooltip="触发次数计数时间窗口（GAP-2）：超过该秒数未触发则计数清零重新累计；0=禁用衰减",
                style="width:100%;")
        # 默认画面（2026-10-27 动作编排完善批次）：段名池改选择模式（多选主线段）；
        # 一律立刻切换，无需设切换方式；单段模式=pingpong 首尾循环（可指定循环段，
        # 空=列表第一个）
        _def_pool_select = ui.select(
            multiple=True, label='默认画面片段池（多选；空=不接管默认轮播）',
            options={s: s for s in _choreo_avail["segments"]},
            value=[s for s in (choreo_node.get("default_segments") or [])
                   if s in _choreo_avail["segments"]],
            on_change=lambda e: choreo_node.__setitem__("default_segments", list(e.value or [])),
            with_input=True,
        ).style("width:60%").tooltip("候选=EDTalk 可用段（「刷新可用段」同步）；"
                                     "轮播=按列表顺序逐个 / 随机=随机播放 / 单段=循环指定片段")

        def _sync_default_single(v):
            choreo_node.__setitem__("default_single_segment", str(v or ""))

        _def_single_select = ui.select(
            label='单段模式循环段（空=循环列表第一个）',
            options={s: s for s in (choreo_node.get("default_segments") or [])},
            value=str(choreo_node.get("default_single_segment", "") or "") or None,
            on_change=lambda e: _sync_default_single(e.value),
            with_input=True, clearable=True,
        ).style("width:60%").tooltip("仅「单段」模式生效：循环播放该片段（pingpong 首尾循环）；"
                                     "候选=默认画面片段池已选片段")

        def _refresh_def_single_options():
            """默认池变化 → 循环段候选同步（已选优先保留）。"""
            pool = [s for s in (choreo_node.get("default_segments") or [])]
            _def_single_select.options = {s: s for s in pool}
            cur = str(choreo_node.get("default_single_segment", "") or "")
            if cur and cur not in pool:
                choreo_node["default_single_segment"] = ""
                _def_single_select.value = None
            _def_single_select.update()

        _def_pool_select.on_value_change(lambda _e: _refresh_def_single_options())
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
