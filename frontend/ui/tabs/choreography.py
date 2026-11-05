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
import os
import re
import threading
import time

from nicegui import ui
from typing import Any, Callable, Dict

from frontend.ui.components import FormField
from frontend.ui.components.config_helper import get_nested_value
from frontend.ui.components.local_file_picker import LocalFilePicker


def _persist_choreo_node_fallback(node: dict, config_path: str = None) -> tuple:
    """main 进程不可达时的降级持久化（webui 进程窄写者；2026-11-04 二批）。

    写者矩阵补充声明：choreography 节磁盘主写者=main 进程
    orchestration.api._write_choreography_section（热应用链路）。本函数仅当
    main 连接拒绝（进程未运行——此刻它不是该节写者）时由 webui 调用：
    读磁盘 → 整节替换 → tmp+os.replace 原子写回（同款模式）。main 可达时
    绝不走本路径——防 webui 旧快照覆盖 main 的活跃写入（沿 Settings.save
    写者分配矩阵同纪律：运行期窄写者仅限于写者缺位时）。

    Returns: (成功与否, 失败原因)
    """
    try:
        if config_path is None:
            from frontend.config.settings import get_config
            config_path = str(get_config().config_path)
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = _json.load(f)
        # 写盘审计（2026-11-04 四批，写者矩阵三写点各记变更——丢失可溯源）
        try:
            _prev = cfg.get("choreography") or {}
            from utils.my_log import logger
            logger.info(
                f"[choreography] 写盘审计（webui 降级窄写）：rules {len(_prev.get('rules') or [])}"
                f"→{len(node.get('rules') or [])}，pool {_prev.get('default_segments')}"
                f"→{node.get('default_segments')}，mode {_prev.get('default_mode')}"
                f"→{node.get('default_mode')}，enable {_prev.get('enable')}→{node.get('enable')}")
        except Exception:
            pass
        cfg["choreography"] = node
        tmp = config_path + ".tmp-webui"
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            _json.dump(cfg, f, ensure_ascii=False, indent=2)
        os.replace(tmp, config_path)
        return True, ""
    except Exception as e:
        return False, str(e)


# 素材清单模块级缓存（2026-11-04 用户反馈「进页等待几秒」优化，SWR 策略）：
# 曾为 tab 闭包局部变量——每次进页清零重建，规则/覆盖档下拉先用空清单渲染、
# 清单 task 到达后签名变化再全量重渲染（双倍构建=进页等待几秒的主因）。
# 提升为模块级后跨页面/会话存活：进页直接用缓存渲染（秒开），后台仍恒校验
# （签名比对防抖——清单无变化不重渲染）。webui 单用户场景，Lock 仅护写写并发。
_CHOREO_AVAIL: dict = {"segments": [], "voiceover": set(), "typed": {},
                       "typed_sig": None, "ts": 0.0}
_CHOREO_AVAIL_LOCK = threading.Lock()


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
    # 卡片 1：动作素材创建（添加表单 / 预处理任务状态）
    # ==================================================================
    with ui.card().style(card_css):
        ui.label("动作素材创建（画面资产库）").style("font-size: var(--font-size-title)")
        ui.label("选本地视频（可多选批量处理）→ 指定类型（上场/主线/离场，整批共用）→ "
                 "勾选口型推理决定预处理链（勾选=拆帧+人脸检测；不勾选=仅拆序列帧，"
                 "播放时作画外音直通）→ 上线。素材段名可留空（用视频文件名作段名）；"
                 "批量处理按队列逐个执行，预处理耗时与视频长度成正比，"
                 "完成自动出现在下方素材列表").style(
            "color: var(--text-hint); font-size: var(--font-size-body)")

        # 视频选择（文件选择器取代手输路径；防重入沿训练页 LocalFilePicker 模式）。
        # 多选批量：选择器可跨目录累计多个视频，添加时按队列逐个提交处理。
        _picked_videos: list = []
        _picker_open = [False]
        _batch_running = [False]

        async def _open_video_picker():
            if _picker_open[0]:
                return
            _picker_open[0] = True
            mat_pick_btn.set_enabled(False)
            try:
                result = await LocalFilePicker(multiple=True, title="选择素材视频（可多选）")
            finally:
                _picker_open[0] = False
                mat_pick_btn.set_enabled(True)
            if result:
                _picked_videos[:] = [str(p) for p in result]
                names = "、".join(p.name for p in result[:3])
                if len(result) > 3:
                    names += f" 等 {len(result)} 个"
                mat_picked_label.set_text(f"已选 {len(result)} 个：{names}").tooltip(
                    "\n".join(str(p) for p in result)).style(
                    "color: var(--text-body); font-size: var(--font-size-body)")

        def _derive_segment_name(video_path: str, used: set) -> str:
            """段名缺省派生：视频文件名清洗为段名白名单字符（批内去重）。

            后端段名白名单=^[A-Za-z0-9_-]{1,64}$，非白名单字符替换为下划线，
            清洗后为空则用时间戳兜底。
            """
            stem = os.path.splitext(os.path.basename(video_path))[0]
            name = re.sub(r"[^A-Za-z0-9_-]", "_", stem).strip("_")[:64]
            if not name:
                name = "mat_" + time.strftime("%Y%m%d%H%M%S")
            candidate, i = name, 2
            while candidate in used:
                candidate = f"{name}_{i}"
                i += 1
            used.add(candidate)
            return candidate

        with ui.row().style("width:100%;align-items:center;gap:10px"):
            mat_pick_btn = ui.button("选择视频文件", icon="movie", on_click=_open_video_picker).props("outline")
            mat_picked_label = ui.label("未选择视频").style(
                "color: var(--text-hint); font-size: var(--font-size-body); "
                "max-width:420px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap")
        with ui.grid(columns=3):
            mat_seg_input = FormField.create_input(
                label='素材段名（可选：留空=用视频文件名；多选时填写=作段名前缀）',
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

        async def _publish_material():
            """批量添加：按队列逐个「提交→轮询至终态→下一个」。

            素材类型/口型推理/预处理设备整批共用同一配置；后端单飞行任务
            不变，由前端串行排队驱动（一次只有一个任务在跑）。
            """
            if _batch_running[0]:
                return
            videos = list(_picked_videos)
            if not videos:
                ui.notify(position="top", type="warning", message="先选择视频文件")
                return
            seg_input = (mat_seg_input.value or "").strip()
            # 段名规划：留空=视频文件名派生（单选/多选一致）；
            # 多选且填写=作为前缀（前缀_1.._N），单选且填写=直接使用
            used: set = set()
            prefix_mode = len(videos) > 1 and bool(seg_input)
            plan = []
            for i, v in enumerate(videos, start=1):
                seg = f"{seg_input}_{i}" if prefix_mode else _derive_segment_name(v, used)
                plan.append((v, seg))
            if prefix_mode:
                ui.notify(position="top", type="info", duration=6,
                          message=f"批量模式：段名使用前缀 {seg_input}_1.._{len(videos)}")
            total = len(plan)
            _batch_running[0] = True
            mat_add_btn.set_enabled(False)  # 整批单飞行：处理中禁用防叠发
            ok_segs, fails = [], []
            try:
                import requests
                for idx, (video, seg) in enumerate(plan, start=1):
                    mat_task_label.set_text(f"[{idx}/{total}] 提交：{seg}……")
                    try:
                        resp = await asyncio.to_thread(
                            requests.post, f"{_webui_api_url()}/edtalk_materials/publish",
                            json={"video_path": video, "segment": seg,
                                  "device": mat_device_select.value or "cuda",
                                  "material_type": mat_type_select.value or "main",
                                  "lip_flag": bool(mat_lip_checkbox.value)},
                            timeout=8,
                        )
                    except Exception as e:
                        fails.append(f"{seg}：提交失败（webui 服务异常？）{e}")
                        continue
                    if resp.status_code not in (200, 202):
                        detail = ""
                        try:
                            d = (resp.json() or {}).get("detail", {})
                            detail = d.get("message") if isinstance(d, dict) else str(d)
                        except Exception:
                            pass
                        fails.append(f"{seg}：未受理（HTTP {resp.status_code}）{detail}")
                        continue
                    # 轮询该任务至终态（批量循环自管，_poll_task 让位）
                    state = "idle"
                    while True:
                        await asyncio.sleep(1.0)
                        try:
                            tr = await asyncio.to_thread(
                                requests.get, f"{_webui_api_url()}/edtalk_materials/task",
                                timeout=2)
                            t = (tr.json() or {}) if tr.status_code == 200 else {}
                        except Exception:
                            t = {}
                        state = t.get("state", "idle")
                        if state == "running":
                            mat_task_label.set_text(
                                f"[{idx}/{total}] [{seg}] {t.get('stage', '')}……").style(
                                "color: var(--color-warning)")
                        else:
                            break
                    if state == "done":
                        ok_segs.append(seg)
                    else:
                        fails.append(f"{seg}：{t.get('message') or '处理失败'}")
            finally:
                _batch_running[0] = False
                mat_add_btn.set_enabled(True)
                summary = f"批量完成：成功 {len(ok_segs)}/{total} 个"
                if ok_segs:
                    summary += f"（{'、'.join(ok_segs)}）"
                if fails:
                    summary += "\n失败 " + str(len(fails)) + " 个：\n" + "\n".join(fails)
                mat_task_label.set_text(summary).style(
                    "color: " + ("var(--color-warning)" if fails else "var(--color-positive)"))
                if ok_segs:
                    _picked_videos.clear()
                    mat_picked_label.set_text("未选择视频").style(
                        "color: var(--text-hint); font-size: var(--font-size-body)")
                    await _refresh_materials()

        async def _poll_task():
            """添加任务 2s 轮询：running 中更新进度行；终态提示+解锁+刷新清单。

            批量处理运行中让位：轮询与按钮/清单管理由 _publish_material 循环自管。
            """
            if _batch_running[0]:
                return
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
                    _picked_videos.clear()
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
            # 素材类型化清单 → 规则卡下拉候选源（R-dsg12③：sidecar 主源按类型过滤；
            # 模块级缓存写回带锁——8s timer 与进页 task 并发刷新的写写竞争）。
            # prev_sig=写回前缓存签名（「清单变化→规则区重渲染」的比对基准——
            # 曾在同函数写回后又与刚写值比较，恒等致重渲染永不触发）
            prev_sig = _choreo_avail.get("typed_sig")
            sig = tuple(sorted((str(it.get("segment")), str(it.get("type") or "main"))
                               for it in items))
            try:
                with _CHOREO_AVAIL_LOCK:
                    _choreo_avail["typed"] = {"items": items}
                    _choreo_avail["typed_sig"] = sig
                    _choreo_avail["ts"] = time.time()
            except NameError:
                pass
            # 片段池候选同步（typed 主源到达后刷新；池控件定义在卡片 2——
            # 定时器/按钮触发均晚于定义，NameError 兜底沿上方同款模式）
            try:
                _def_pool_select.options = _def_pool_options()
                _def_pool_select.update()
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
            # 素材清单变化 → 规则区重渲染（_seg_options/_def_pool 选项源同步）。
            # 曾仅 8s timer 晚到且到达后不重渲染规则区——规则/覆盖档下拉停在
            # 构建期空集，「加一档」无法选择新片段（2026-11-04 erro 批次）。
            # 签名比对（对写回前 prev_sig）：清单未变不重渲染，防 8s 心跳折叠/
            # 打断编辑中控件。全段异常捕获（三批观测）：本协程在裸 task 上下文
            # 运行——曾仅 requests 段有 try，UI 重渲染段异常会静默炸 task 且零日志。
            try:
                if sig != prev_sig:
                    try:
                        _render_rules()
                    except NameError:
                        pass
            except Exception:
                from utils.my_log import logger
                logger.error("[编排页] 素材清单触发的规则区同步异常（task 上下文）",
                             exc_info=True)

        def _render_materials(items: list):
            """素材库三列显示（2026-10-27 用户裁决：上场/主线/离场三列分离，
            每列只显示段名/帧数/引用状态/操作，「改类型」并入操作菜单）。"""
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
                with ui.grid(columns=3).style("width:100%;gap:14px"):
                    for tname in ("enter", "main", "exit"):
                        group = by_type.get(tname) or []
                        with ui.column().style("width:100%;gap:4px").classes("border rounded"):
                            with ui.row().style("width:100%;gap:8px;align-items:center;padding:6px 10px"):
                                ui.label(type_names[tname]).style(
                                    "font-size: var(--font-size-body);font-weight:500")
                                ui.label(f"{len(group)} 个").style(
                                    "color: var(--text-hint);font-size: var(--font-size-body)")
                            if not group:
                                ui.label(f"暂无{type_names[tname]}素材").style(
                                    "color: var(--text-hint);font-size: var(--font-size-body);"
                                    "padding:2px 10px 8px")
                                continue
                            with ui.row().style("width:100%;gap:10px;color: var(--text-hint);padding:0 10px"):
                                for h in ("段名", "帧数", "引用/状态"):
                                    ui.label(h).style("min-width:76px")
                            for it in group:
                                seg = it.get("segment", "?")
                                with ui.row().style("width:100%;align-items:center;gap:10px;padding:2px 10px"):
                                    ui.label(seg).style(
                                        "min-width:76px;max-width:120px;overflow:hidden;"
                                        "text-overflow:ellipsis;white-space:nowrap")
                                    ui.label(str(it.get("total_frames") or "?")).style("min-width:76px")
                                    refs = it.get("references") or []
                                    tags = []
                                    if it.get("is_current"):
                                        tags.append("渲染中")
                                    if it.get("in_scheduler"):
                                        tags.append("调度中")
                                    tags += refs[:2] + [f"等{len(refs)}处引用" if len(refs) > 2 else ""]
                                    ui.label(" · ".join(x for x in tags if x) or "未引用").style(
                                        "min-width:76px;color: var(--text-hint);max-width:150px;"
                                        "overflow:hidden;text-overflow:ellipsis;white-space:nowrap")
                                    # 操作菜单（改类型并入操作——2026-10-27 用户裁决）
                                    with ui.dropdown_button("操作", auto_close=True).props(
                                            "outline dense").style("min-width:70px"):
                                        for target_t, t_label in type_names.items():
                                            if target_t != str(it.get("type") or "main"):
                                                ui.item(f"改为{t_label}", on_click=lambda
                                                        s=seg, tt=target_t: _change_type(s, tt))
                                        ui.item("重处理", on_click=lambda s=seg, lf=bool(
                                            it.get("lip_flag", True)): _reprocess(s, lf)) \
                                            .tooltip("按口型推理目标重跑预处理链并热上线"
                                                     "（当前播放中的素材会被拒绝——先切走）")
                                        ui.item("删除", on_click=lambda s=seg: _delete_material(s))

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

        ui.timer(2.0, _poll_task)

    # ==================================================================
    # 卡片 2：素材列表（清单 / 引用状态 / 改类型·重处理·删除操作）
    # ==================================================================
    with ui.card().style(card_css):
        with ui.row().style("width:100%;align-items:center;gap:12px"):
            ui.label("素材列表").style("font-size: var(--font-size-title)")
            mat_status_label = ui.label("").style("color: var(--text-hint)")
            ui.button("刷新清单", on_click=_refresh_materials).props("flat")
        ui.label("删除：被编排规则/默认池/段调度引用或正在渲染的素材会被拒绝并给出去处；"
                 "「改类型」即时生效并同步规则步骤下拉候选").style(
            "color: var(--text-hint); font-size: var(--font-size-body)")
        mat_list_slot = ui.column().style("width:100%")
        # 8s 周期校验：直调 async 回调（禁 create_task 包装——裸 task 在 client
        # 删除后不可取消，僵尸渲染警告同构建尾部；见 _render_rules 后注）
        ui.timer(8.0, _refresh_materials)

    # ==================================================================
    # 卡片 2：编排规则管理（自画面设置页迁移；行为不变）
    # ==================================================================
    _choreo_avail = _CHOREO_AVAIL   # 模块级缓存绑定（SWR——跨页面/会话存活）
    # 规则展开状态（重渲染保留——素材清单到达/变化触发 _render_rules 重渲染
    # 同步下拉选项源，不折叠用户正在编辑的规则）
    _rule_expanded: set = set()

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
                       "step_timeout_s": 60, "rules": []}
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
        """步骤段下拉选项（R-dsg12③）：sidecar 素材清单主源。

        2026-11-04 用户裁决二批：指定 want_type 时**严格类型过滤**——仅列出该
        类型素材（上场/主线/离场各归各类，避免片段多时选错类型）；EDTalk /status
        全量段不再并入类型化候选（该来源无类型信息，曾把全部段混进每个步骤下拉）。
        want_type=None（默认画面片段池等不分类型场景）保持全量并入。
        current 恒保底——已选段不在候选时仍可显示（NiceGUI value 校验不炸）。
        """
        opts = []
        for it in (_choreo_avail.get("typed") or {}).get("items", []):
            if want_type is None or it.get("type") == want_type:
                opts.append(str(it.get("segment")))
        if want_type is None:
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
                rid = str(rule.get('rule_id') or ('#' + str(idx + 1)))
                with ui.expansion(
                    f"规则 {rid}"
                    f"（{rule.get('trigger_type', '?')} · {rule.get('match') or '任意'}）"
                    f"{' · 新语义' if is_new else ' · 存量语义（重存后转新）'}{tier_note}"
                ).style("width:100%") as _exp:
                    # 展开状态保留（清单变化重渲染不折叠编辑中的规则）
                    if rid in _rule_expanded:
                        _exp.value = True
                    _exp.on_value_change(lambda e, r=rid: (
                        _rule_expanded.add(r) if e.value else _rule_expanded.discard(r)))
                    if not is_new:
                        ui.label("⚠ 存量语义规则：维持旧时长/音轨判定直至本页重存；"
                                 "重存后转新语义（时长=素材时长，音轨由系统判断）").style(
                            "color: var(--color-warning); font-size: var(--font-size-body)")
                    # 第一行：规则ID | 触发类型 | 匹配值 | 价值门槛 | 档位摘要+删除
                    # （2026-10-27 用户裁决：多规则触发方式冗余移除——主线切换方式已覆盖）
                    with ui.grid(columns=5):
                        FormField.create_input(
                            label='规则 ID（唯一）', value=str(rule.get("rule_id", "")),
                            on_change=lambda e, r=rule: r.__setitem__("rule_id", (e.value or "").strip()),
                            style="width:100%;")
                        FormField.create_select(
                            label='触发类型',
                            options={'gift': '礼物打赏', 'keyword': '弹幕关键词', 'song': '点歌命中',
                                     'entrance': '观众入场', 'follow': '观众关注'},
                            value=str(rule.get("trigger_type", "gift")),
                            on_change=lambda e, r=rule: r.__setitem__("trigger_type", e.value),
                            tooltip="song 触发=接管（歌曲音频走既有链路，主线保持至歌终）；"
                                    "gift/keyword 新语义=不接管（原生答谢照常）；"
                                    "entrance/follow=入场/关注事件触发（T4：自动排队不打断，"
                                    "10s 类型节流防风暴，权重恒最低档）；"
                                    "动态文案「关键词|文案」的关键词也走本类型触发——"
                                    "动态文案播出时自动判定（无独立触发类型）",
                            style="width:100%;")
                        FormField.create_input(
                            label='匹配值（gift=礼物名全等 / keyword=弹幕或动态文案关键词子串 / song·entrance·follow 可空=任意；非空=用户名等值）',
                            value=str(rule.get("match", "")),
                            on_change=lambda e, r=rule: r.__setitem__("match", e.value or ""),
                            style="width:100%;")
                        FormField.create_input(
                            label='价值门槛（仅 gift；0=不限）',
                            value=str(rule.get("min_price", 0)),
                            on_change=lambda e, r=rule: r.__setitem__(
                                "min_price", float(e.value or 0) if str(e.value or "").replace(".", "", 1).isdigit() else 0),
                            style="width:100%;")
                        # 触发次数摘要（计次进档「从第 N 次起」）+规则删除按钮
                        tier_mins = [str(t.get("min_count", t.get("max_count"))) for t in tiers]
                        n_label = ("档位：从第 " + "/从第 ".join(tier_mins) + " 次起"
                                   if tier_mins else "基础档")
                        with ui.row().style("width:100%;align-items:center;gap:6px"):
                            ui.label(n_label).style(
                                "color: var(--text-hint); font-size: var(--font-size-body)").tooltip(
                                "计次进档：每次触发进入档位计次（主线播放中忽略不计数；"
                                "他键触发清零），从第 N 次起的触发使用该档片段"
                                "（示例：档位一 1 次/档位二 4 次/"
                                "档位三 7 次 → 第 1~3 次档一、第 4~6 次档二、第 7 次起档三）")
                            ui.button(icon="delete", on_click=lambda i=idx: _delete_rule(i)).props(
                                "outline color=negative dense").tooltip("删除本规则")
                    # 第二行：上场|主线|切换方式|离场 一行四参数（2026-10-27 用户裁决重排；
                    # 时长/音轨删除——新语义由系统接管，存量语义重存后自动转换）
                    seq = rule.setdefault("sequence", {})
                    with ui.row().style("width:100%;align-items:flex-end;gap:10px"):
                        ui.select(
                            options=_seg_options(str((seq.get("enter") or {}).get("segment", "")), "enter"),
                            value=str((seq.get("enter") or {}).get("segment", "")) or None,
                            label="上场（可选）", clearable=True,
                            on_change=lambda e: (seq.setdefault("enter", {})
                                                 .__setitem__("segment", e.value or "")),
                        ).style("min-width:200px;flex:1").tooltip(
                            "过渡片段（可空=跳过该步，默认画面→主线直连）；固定播完接主线，不设切换方式；"
                            "标「画外音」的段播放时画面原样+语音照常")
                        ui.select(
                            options=_seg_options(str((seq.get("main") or {}).get("segment", "")), "main"),
                            value=str((seq.get("main") or {}).get("segment", "")) or None,
                            label="主线（必选）",
                            on_change=lambda e: (seq.setdefault("main", {})
                                                 .__setitem__("segment", e.value or "")),
                        ).style("min-width:200px;flex:1").tooltip(
                            "主线片段必选；候选=主线素材∪已引用段∪服务可用段")
                        ui.select(
                            options={"segment_end": "无缝切换（播完后切）",
                                     "immediate": "立刻切换（触发即切）"},
                            value=str((seq.get("main") or {}).get("switch_at", "segment_end")),
                            label="切换方式（主线）",
                            on_change=lambda e: (seq.setdefault("main", {})
                                                 .__setitem__("switch_at", e.value)),
                        ).style("min-width:180px").tooltip(
                            "无缝切换=等当前片段播放完成后再切换；"
                            "立刻切换=触发新规则后立即切换")
                        ui.select(
                            options=_seg_options(str((seq.get("exit") or {}).get("segment", "")), "exit"),
                            value=str((seq.get("exit") or {}).get("segment", "")) or None,
                            label="离场（可选）", clearable=True,
                            on_change=lambda e: (seq.setdefault("exit", {})
                                                 .__setitem__("segment", e.value or "")),
                        ).style("min-width:200px;flex:1").tooltip(
                            "过渡片段（可空=主线播完直接回默认画面）；固定播完接下一步，不设切换方式")
                    # 档位编辑器（R-dsg4 D2-1/2/3/4：第 1 行=基础档同源，第 2 行起=覆盖档）
                    ui.label("触发次数档位（第 1 行=基础档与上方三下拉同源；覆盖档留空=沿用基础档；"
                             "覆盖档仅覆盖片段，切换方式等其余参数恒沿用基础档；"
                             "首覆盖档阈值须 ≥2——=1 会在首次触发即遮蔽基础档）").style(
                        "color: var(--text-hint); font-size: var(--font-size-body); margin-top:6px")
                    _render_tier_editor(rule)
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
                            "每次触发进入档位计次（他键触发清零）",
                    style="width:110px;")
                for n in ("enter", "main", "exit"):
                    cur = str(ov.get(n, "") or "")
                    ui.select(
                        # NiceGUI dict options 语义=键为值/值为标签：哨兵须为 ("", 标签)——
                        # 曾反写为 ("（继承基础档）", "")，空覆盖档 value="" 不在键中
                        # → ValueError: Invalid value:（erro2.log；选中哨兵还会把字面
                        #   文本写进 segment_overrides）。留空=沿用基础档语义不变。
                        # 候选同基础档严格类型过滤（二批：覆盖档也按步骤类型）。
                        options=dict([("", "（继承基础档）")] + list(_seg_options(cur, n).items())),
                        value=cur,
                        label={"enter": "上场", "main": "主线", "exit": "离场"}[n],
                        on_change=lambda e, o=ov, k=n: (
                            o.__setitem__(k, e.value) if e.value else o.pop(k, None)),
                    ).style("min-width:170px").tooltip(
                        "留空=沿用基础档（部分覆盖语义）；仅覆盖片段——"
                        "切换方式/时长/音轨等非段参数恒沿用基础档设置")
                FormField.create_input(
                    label='mood（可空）', value=str(tier.get("mood", "")),
                    on_change=lambda e, tr=tier: tr.__setitem__("mood", e.value or ""),
                    tooltip="档位情绪标注（自由文本，如 happy/angry/breakdown）："
                            "命中该档时显示在编排状态条（执行中：…· mood），"
                            "纯观测字段，不影响画面/音频/文案行为",
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
        choreo_node.pop("reset_after_s", None)          # 计数窗口退役（2026-10-31 审查批次）不再写出
        try:
            import requests
            resp = await asyncio.to_thread(
                requests.post, f"{_main_api_url()}/choreography/rules",
                json=choreo_node, timeout=8,
            )
            data = (resp.json() or {}) if resp.status_code == 200 else {}

            def _align_memory():
                """webui 内存快照对齐（2026-11-04）：磁盘落盘后本进程 Settings
                不自动 reload，刷新页面 deepcopy 到旧值。仅对齐内存不落盘——
                choreography 节磁盘写者=main 端点/降级窄写（写者矩阵）。"""
                try:
                    from frontend.config.settings import get_config
                    get_config().set("choreography",
                                     value=_copy.deepcopy(choreo_node))
                except Exception:
                    pass

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
                else:
                    _align_memory()
                ui.notify(position="top", type="positive", message=msg)  # 已生效
            elif resp.status_code == 503:
                # C5-1 两态提示（2026-11-04 实测校正）：后端对 EDTalk 不可达已
                # 持久化（reason=edtalk_unreachable），文案与实际对齐——曾无差别
                # 报「已保存」而实际未写盘（假成功）。persisted 分支同样对齐内存
                # （三批：对齐缺失面——此路径曾漏对齐，叠加全量 save 无保护共同
                # 造成「保存后又被旧快照覆盖」）
                detail = data.get("message") or "EDTalk 不可达"
                if data.get("persisted"):
                    _align_memory()
                    ui.notify(position="top", type="warning", duration=8,
                              message=f"已保存、暂未生效（EDTalk 不可达，就绪后重试应用即生效）：{detail}")
                else:
                    ui.notify(position="top", type="negative", duration=8,
                              message=f"未保存（持久化失败，配置未变更）：{detail}")
            else:
                detail = data.get("message") or (resp.json() or {}).get("detail", {})
                ui.notify(position="top", type="warning",
                          message=f"未应用（HTTP {resp.status_code}）：{detail}")
        except Exception as e:
            # 降级持久化（2026-11-04 用户实测二批）：main 不可达（连接拒绝
            # 10061）时规则端点根本不存在——配置期改动（默认画面池/单段段）
            # 此前无任何落盘路径，点应用=永远失败且丢失。此刻 main 进程未
            # 运行、不是 choreography 节写者 → webui 降级窄写（模块级
            # _persist_choreo_node_fallback，写者矩阵补充声明见其 docstring）。
            # main 可达时的其他异常（超时等）不落盘——防覆盖活跃写者。
            import requests as _requests
            if isinstance(e, _requests.exceptions.ConnectionError):
                ok, err = _persist_choreo_node_fallback(choreo_node)
                if ok:
                    try:
                        from frontend.config.settings import get_config
                        get_config().set("choreography",
                                         value=_copy.deepcopy(choreo_node))
                    except Exception:
                        pass
                    ui.notify(position="top", type="warning", duration=10,
                              message="已保存到配置文件（运行系统未启动，暂未生效；"
                                      "启动系统后重试『应用』即校验+热生效）")
                else:
                    ui.notify(position="top", type="negative",
                              message=f"保存失败（运行系统未启动且本地写盘异常）：{err}")
            else:
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

    # ==================================================================
    # 卡片 3：默认画面（空闲轮播——EDTalk 段调度器承载）
    # ==================================================================
    with ui.card().style(card_css):
        ui.label("默认画面（空闲轮播）").style("font-size: var(--font-size-title)")
        ui.label("无事件触发时数字人画面的承载方式（序列结束后恢复）。"
                 "启动系统后编排器立即接管默认轮播（从片段池开播——启动时看到的"
                 "画面切换属正常行为，非编排规则触发）。"
                 "改动后点『应用』= 校验 + 热生效 + 写回 config（与编排规则整节同一次提交）。").style(
            "color: var(--text-hint); font-size: var(--font-size-body)")
        # 默认画面模式（计数窗口已退役——2026-10-31 审查批次，
        # 触发计次不再随时间衰减，控件与键移除）
        FormField.create_select(
            label='默认画面模式',
            options={'rotate': '轮播（顺序）', 'random': '随机', 'single': '单段'},
            value=str(choreo_node.get("default_mode", "rotate")),
            on_change=lambda e: choreo_node.__setitem__("default_mode", e.value),
            tooltip="序列结束后恢复的默认轮播方式（承载=EDTalk 段调度器）："
                    "轮播=片段池顺序循环 / 随机=片段池随机 / 单段=指定片段循环",
            style="width:60%;")
        # 默认画面（2026-10-27 动作编排完善批次）：段名池改选择模式（多选主线段）；
        # 一律立刻切换，无需设切换方式；单段模式=pingpong 首尾循环（可指定循环段，
        # 空=列表第一个）
        def _def_pool_options() -> dict:
            """片段池候选=素材清单段 ∪ EDTalk 可用段 ∪ 已配置段（修复：此前仅
            EDTalk /status 可用段——服务未跑时为空、已配置段被 value 过滤丢失）。"""
            opts = [str(it.get("segment"))
                    for it in (_choreo_avail.get("typed") or {}).get("items", [])]
            opts += _choreo_avail["segments"] or []
            opts += [str(s) for s in (choreo_node.get("default_segments") or [])]
            seen, out = set(), {}
            for s in opts:
                if s and s not in seen:
                    seen.add(s)
                    out[s] = f"{s}（画外音）" if s in _choreo_avail["voiceover"] else s
            return out

        _def_pool_select = ui.select(
            multiple=True, label='默认画面片段池（多选；空=不接管默认轮播）',
            options=_def_pool_options(),
            value=[str(s) for s in (choreo_node.get("default_segments") or [])],
            on_change=lambda e: choreo_node.__setitem__("default_segments", list(e.value or [])),
            with_input=True,
        ).style("width:60%").tooltip("候选=素材清单段∪EDTalk 可用段（「刷新可用段」同步）；"
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
        with ui.row().style("width:100%;align-items:center;gap:10px;margin-top:4px"):
            ui.button("应用（校验+热生效+写回）", on_click=_apply_choreography).props("outline")
            ui.label("EDTalk 未运行也可保存（就绪后重试应用即生效）").style(
                "color: var(--text-hint); font-size: var(--font-size-body)")

    # ==================================================================
    # 卡片 4：编排规则（事件驱动画面序列 + 模拟触发）
    # ==================================================================
    with ui.card().style(card_css):
        with ui.row().style("width:100%;align-items:center;gap:12px"):
            ui.label("编排规则（事件驱动画面序列）").style("font-size: var(--font-size-title)")
            choreo_status_label = ui.label("状态：未轮询").style("color: var(--text-hint)")
        ui.label("打赏/关键词/点歌 → 片段序列（上场→主线→离场）+ 触发次数轮换档位。"
                 "改动后点『应用』= 校验 + 热生效 + 写回 config（重启后按 config 装载）。"
                 "⚠ 触发命中立即切换，无最短保持保护（最短再触发间隔/序列最小展示已按"
                 "用户裁决退役；去重由弹幕层承担）——R-dsg7/R-ceo5").style(
            "color: var(--text-hint); font-size: var(--font-size-body)")
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
    # SWR 渲染（2026-11-04 进页等待优化）：缓存有清单 → 构建上下文内直接渲染
    # 素材列表（秒开——曾每次进页都空渲染+等 task 到达重渲染的双倍构建）。
    _cached_items = (_CHOREO_AVAIL.get("typed") or {}).get("items") or []
    if _cached_items:
        try:
            _render_materials(_cached_items)
            mat_status_label.set_text(
                f"共 {len(_cached_items)} 个素材（缓存显示，后台校验中）").style(
                "color: var(--text-hint)")
        except Exception:
            pass
    # 后台校验 timer 化（once）——禁用裸 create_task：task 无法取消，client
    # 删除（切页/刷新/重连）后仍跑并向已删元素创建 UI =「Client has been
    # deleted but is still being used」僵尸警告+无效渲染（2026-11-04 用户
    # 实测栈，_refresh_materials→_render_materials→ui.grid）。ui.timer 挂
    # client，_clear_config_page 统一取消（layout.py:1552 同款清理语义）。
    ui.timer(0.1, _refresh_materials, once=True)
    ui.timer(5.0, _poll_choreo_status)
