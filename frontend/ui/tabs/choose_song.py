# -*- coding: UTF-8 -*-
"""
点歌模式标签页模块
从 common_config.py 拆分而来

布局规范：
- 设置项用最多 3 列网格排布，组件宽度跟随列宽（width:100%）
- 开关+关联参数 为上下结构单元：开关在上，输入框在下
- 歌曲翻唱模块（/autoplan 2026-10-14 C-3）：折叠分组承载（状态/参数/成品），
  队列状态经 main 侧 /cover/status 窄端点轮询（写者矩阵：webui 只读）
"""
import difflib
import os
import threading

import requests
from nicegui import ui
from typing import Dict, Any, Callable
from frontend.ui.components.config_helper import get_nested_value
from utils.my_log import logger
from frontend.utils.common import textarea_data_change
from utils.autolive_paths import song_audio_root, SONG_AUDIO_DIR_DEFAULT
from utils.common.text_mixin import match_gift_to_song_preview, match_song_intent

# main 侧窄端点轮询（nicegui3 教训：requests trust_env=False 禁系统代理+线程池解耦）
_session = requests.Session()
_session.trust_env = False
_session.proxies = {"http": None, "https": None}

# 曲库候选扩展名（与 utils.audio.get_dir_audios_filename 识别口径一致）
AUDIO_EXTS = (".mp3", ".wav", ".flac", ".m4a", ".ogg", ".aac")


def list_song_names(song_path: str) -> list:
    """读取歌曲目录的候选歌名（不含扩展名，排序稳定）。

    /autoplan 2026-10-23 批次 R1：song_path 用户配置键退役——实参传 None/空串时
    回落内部约定根目录（song_audio_root）；显式传路径仍生效（测试兼容）。"""
    if not song_path:
        song_path = song_audio_root()
    if not os.path.isdir(song_path):
        return []
    names = []
    for fn in os.listdir(song_path):
        stem, ext = os.path.splitext(fn)
        if ext.lower() in AUDIO_EXTS and stem:
            names.append(stem)
    return sorted(names)


def match_gift_to_song(
    gift_name: str,
    gift_song_map: Dict[str, str],
    song_lists: list,
    similarity: float = 0.5,
):
    """礼物名→歌曲 匹配（纯函数，供映射预演与测试共用）。

    /autoplan 2026-10-23 批次 D-20 统一口径：委托 utils.common.text_mixin.
    match_gift_to_song_preview——前端预演与主进程实播同规则（native F5 归一
    同源：CoverQueue.normalize_song），消除 D4 时期的前后端双实现漂移。"""
    return match_gift_to_song_preview(gift_name, gift_song_map, song_lists, similarity)


def _main_base(config) -> str:
    """点歌翻唱控制面基址（T3 实测批次改为 webui 同源）。

    /cover/* 现挂载于 webui 进程自身（frontend/main.py register_cover_routes），
    api_port（主进程 8082）在 webui 单进程工作流下不存在——同源 webui 端口
    承载（NiceGUI 页面与其 API 同进程）。"""
    return f"http://127.0.0.1:{int(get_nested_value(config, 'webui', 'port') or 8086)}"


def create_choose_song_tab(
    config: Dict[str, Any],
    theme_config: Dict[str, str],
    set_config_callback: Callable
):
    """创建点歌模式标签页"""
    card_css = theme_config.get("card", "")
    switch_internal_css = theme_config.get("switch_internal", "")

    # 导入自动绑定函数
    from frontend.utils.config_auto_save import create_auto_save
    _auto_save = create_auto_save(config, set_config_callback, tab_key="choose_song")

    # 点歌模式（/autoplan 2026-10-23 批次 R1：移除 取消点歌命令/随机点歌命令/
    # 歌曲路径 三控件——歌曲目录改为内部约定 song/；2026-10-31 点歌审查批次：
    # 移除 准备文案/同歌冷却间隔/弹幕回应节流 三控件（系统已有限入/去重/优先级
    # 队列防刷屏），命中行为按礼物点歌开关分叉——关=关键词直点歌、开=打赏即唱）
    with ui.card().style(card_css):
        ui.label('点歌模式')
        ui.label("弹幕含触发关键词+歌名 → 匹配歌曲列表：礼物点歌关闭=播报并直接开唱；开启=回应引导文案，打赏礼物即唱。歌曲列表=礼物映射歌名 ∪ 翻唱成品 ∪ song/ 曲源（翻唱产物自动可点）").style(
            "font-size: var(--font-size-assist, 12px); color: var(--text-hint, #9ca3af);")
        with ui.grid(columns=3):
            with ui.column().style("width:100%;"):
                _auto_save(ui.textarea(label='点歌触发关键词', value=textarea_data_change(get_nested_value(config, "choose_song", "start_cmd")), placeholder='弹幕中包含即触发点歌判断，换行分隔\n例如：主播会唱 / 可以唱 / 能唱').style("width:100%;"), keys=("choose_song", "start_cmd"))
            with ui.column().style("width:100%;"):
                _auto_save(ui.input(label='匹配失败文案', value=get_nested_value(config, "choose_song", "match_fail_copy"), placeholder='列表没有这首歌时回应\n变量：{user} {content}').style("width:100%;"), keys=("choose_song", "match_fail_copy"))
                _auto_save(ui.input(label='引导文案（礼物点歌开启时）', value=get_nested_value(config, "choose_song", "guide_copy"), placeholder='开启礼物点歌后，匹配成功未打赏的回应，不播放\n变量：{user} {content} {gift}').style("width:100%;"), keys=("choose_song", "guide_copy"))
            with ui.column().style("width:100%;"):
                _auto_save(ui.input(label='匹配成功文案', value=get_nested_value(config, "choose_song", "match_success_copy"), placeholder='点歌匹配成功后播报（关键词直点/礼物点歌共用）\n变量：{user} {content} {gift}').style("width:100%;"), keys=("choose_song", "match_success_copy"))
        with ui.grid(columns=2):
            with ui.column().style("width:100%;"):
                similarity_val = float(get_nested_value(config, "choose_song", "similarity") or 0.5)
                simlab = ui.label(f'匹配最低相似度: {similarity_val:.2f}').style("font-size:12px")
                ui.slider(min=0.1, max=1.0, step=0.05, value=similarity_val,
                          on_change=lambda e: (simlab.set_text(f'匹配最低相似度: {float(e.value):.2f}'),
                                               set_config_callback("choose_song", "similarity", value=float(e.value)))
                          ).props("label-always").style("width:100%")
            with ui.column().style("width:100%;"):
                bv_val = float(get_nested_value(config, "choose_song", "backing_volume") or 0.8)
                bvlab = ui.label(f'伴奏音量（数字人演唱时）: {bv_val:.0%}').style("font-size:12px")
                ui.slider(min=0.0, max=1.0, step=0.05, value=bv_val,
                          on_change=lambda e: (bvlab.set_text(f'伴奏音量（数字人演唱时）: {float(e.value):.0%}'),
                                               set_config_callback("choose_song", "backing_volume", value=float(e.value)))
                          ).props("label-always").style("width:100%")
        ui.label("伴奏音量：数字人演唱时伴奏从直播设备外放的音量（0=静音）；干声经数字人口型驱动，伴奏本地外放").style(
            "font-size: var(--font-size-assist, 12px); color: var(--text-hint, #9ca3af);")

    # ---------- 礼物点歌映射表+匹配预演（D4：choose_song.gift_song_map 新键） ----------
    with ui.card().style(card_css):
        with ui.expansion("礼物点歌（礼物事件映射歌曲播放）", value=False).style("width:100%"):
            # 启用开关（2026-10-31 点歌审查批次：gift_song_enable——开启后礼物命中
            # 映射即点歌播放、关键词点歌仅回应引导文案；关闭后关键词直点歌、礼物仅答谢）
            with ui.row().style("width:100%;align-items:center;gap:12px"):
                ui.switch('启用礼物点歌',
                          value=bool(get_nested_value(config, "choose_song", "gift_song_enable")),
                          on_change=lambda e: set_config_callback("choose_song", "gift_song_enable", value=bool(e.value)))
                ui.label("开启：礼物命中映射→播报+开唱，关键词点歌=引导打赏（不播放）；关闭：关键词直点歌，礼物仅答谢").style(
                    "font-size: var(--font-size-assist, 12px); color: var(--text-hint, #9ca3af);")
            ui.label("礼物名命中映射表后，按点歌既有链路（数字人口型）播放对应歌曲；配置即时保存，直播中可增删；礼物无映射歌曲时答谢文案照常触发").style(
                "font-size: var(--font-size-assist, 12px); color: var(--text-hint, #9ca3af);")
            # 拥塞规则（计划 D-23/E1）：同一礼物同时触发答谢与点歌时，答谢先出，
            # 点歌请求以 song 优先级恒定降权排队——映射表只决定"唱什么"，不改变队列次序。
            ui.label("拥塞规则：答谢先出，点歌降权恒定——礼物点歌只决定唱什么，队列次序仍由「优先级配置」的 礼物答谢 > 点歌 决定").style(
                "font-size: var(--font-size-assist, 12px); color: var(--color-warning, #d97706);")

            live_rows = []  # [(gift_input, song_input), ...]

            def _map_from_rows() -> Dict[str, str]:
                return {(gi.value or "").strip(): (si.value or "").strip()
                        for gi, si in live_rows
                        if (gi.value or "").strip() and (si.value or "").strip()}

            def _validate_rows() -> str:
                """R8 录入校验（CP-1+native F5）：空行自动忽略；礼物名归一后冲突拒绝
                （归一口径=CoverQueue.normalize_song 与实播一致）。返回错误说明或 ''。"""
                from utils.cover_worker import CoverQueue
                seen = {}
                for gi, si in live_rows:
                    g, s = (gi.value or "").strip(), (si.value or "").strip()
                    if not g or not s:
                        continue
                    nk = CoverQueue.normalize_song(g)
                    if nk in seen and seen[nk] != s:
                        return f"礼物名「{g}」与「{seen[nk]}对应行」归一后重复（{nk}），请合并或改名"
                    seen[nk] = s
                return ""

            def _sync_gift_map():
                err = _validate_rows()
                if err:
                    ui.notify(err, type="warning")
                    return
                cfg = config.setdefault("choose_song", {}) if isinstance(config, dict) else None
                if cfg is None:
                    return
                cfg["gift_song_map"] = _map_from_rows()
                set_config_callback("choose_song", "gift_song_map", value=cfg["gift_song_map"])

            def _add_row(gift: str = "", song: str = ""):
                row_wrap = ui.row().style("width:100%;align-items:center;gap:8px")
                with row_wrap:
                    gi = ui.input(label='礼物名', value=gift, placeholder='例如：小火箭').style("width:100%;")
                    si = ui.input(label='歌曲名', value=song, placeholder='例如：晴天').style("width:100%;")
                    del_btn = ui.button(icon='delete').props('flat dense round')
                live_rows.append((gi, si))

                def _remove_row():
                    live_rows[:] = [r for r in live_rows if r[0] is not gi]
                    row_wrap.delete()
                    _sync_gift_map()

                del_btn.on_click(_remove_row)
                gi.on_value_change(lambda e: _sync_gift_map())
                si.on_value_change(lambda e: _sync_gift_map())

            rows_container = ui.column().style("width:100%;gap:4px")
            with rows_container:
                for _g, _s in dict(get_nested_value(config, "choose_song", "gift_song_map", default={}) or {}).items():
                    _add_row(str(_g), str(_s))
            with ui.row():
                ui.button('添加映射', on_click=lambda: _add_row()).props('outline dense')

            # CP-6（2026-10-23 TODOS 批次）：歌曲列表 JSON 导入导出（歌曲数多时批量配置）
            def _export_map():
                import json as _json
                m = _map_from_rows()
                if not m:
                    ui.notify('当前映射为空，无可导出内容', type='warning')
                    return
                ui.download(
                    _json.dumps(m, ensure_ascii=False, indent=2).encode('utf-8'),
                    'gift_song_map.json')

            def _import_map():
                import json as _json
                from utils.cover_worker import CoverQueue
                with ui.dialog() as dlg, ui.card().style(card_css):
                    ui.label('粘贴 gift_song_map JSON（{"礼物名": "歌曲名"}）').style(
                        "font-size: var(--font-size-body, 14px);")
                    ta = ui.textarea().style("width:100%;min-height:160px")
                    with ui.row():
                        def _do_import():
                            try:
                                data = _json.loads(ta.value or "{}")
                                if not isinstance(data, dict):
                                    raise ValueError("顶层必须是对象")
                                clean = {str(k).strip(): str(v).strip()
                                         for k, v in data.items() if str(k).strip() and str(v).strip()}
                                seen = {}
                                for k, v in clean.items():
                                    nk = CoverQueue.normalize_song(k)
                                    if nk in seen and seen[nk] != v:
                                        ui.notify(f'礼物名「{k}」归一后与既有行重复，导入中止', type='negative')
                                        return
                                    seen[nk] = v
                            except Exception as e:
                                ui.notify(f'JSON 解析失败：{e}', type='negative')
                                return
                            for gi, si in list(live_rows):
                                live_rows[:] = [r for r in live_rows if r[0] is not gi]
                                gi.parent.delete()
                            for g, s in clean.items():
                                _add_row(g, s)
                            _sync_gift_map()
                            dlg.close()
                            ui.notify(f'已导入 {len(clean)} 条映射', type='positive')
                        ui.button('导入', on_click=_do_import).props('outline dense')
                        ui.button('取消', on_click=dlg.close).props('flat dense')
                dlg.open()

            with ui.row():
                ui.button('导出映射', on_click=_export_map).props('outline dense')
                ui.button('导入映射', on_click=_import_map).props('outline dense')

            # 匹配预演（D4/D-23：输入礼物名预览命中结果，防直播中误触发不可撤回）
            ui.separator()
            with ui.row().style("width:100%;align-items:flex-end;gap:8px"):
                preview_input = ui.input(label='匹配预演', placeholder='输入礼物名，例如：超级小火箭').style("width:100%;")
                preview_btn = ui.button('预演', on_click=lambda: _preview()).props('outline dense')
            preview_result = ui.label('').style("font-size: var(--font-size-assist, 12px);")

            def _preview():
                gift = (preview_input.value or "").strip()
                if not gift:
                    preview_result.set_text("请先输入礼物名")
                    return
                similarity = get_nested_value(config, "choose_song", "similarity", default=0.5)
                songs = list_song_names("")  # 内部约定根目录（R1：song_path 配置键退役）
                hit = match_gift_to_song(gift, _map_from_rows(), songs, similarity)
                if hit is None:
                    preview_result.set_text(
                        f"未命中——礼物「{gift}」无映射，或映射歌曲不在曲库（当前曲库 {len(songs)} 首）")
                    preview_result.style("font-size: var(--font-size-assist, 12px); color: var(--color-warning, #d97706);")
                else:
                    name, ratio, via = hit
                    preview_result.set_text(f"命中《{name}》（相似度 {ratio:.2f}，{via}）")
                    preview_result.style("font-size: var(--font-size-assist, 12px); color: var(--color-positive, #16a34a);")

    # ---------- 歌曲翻唱（C-3：折叠分组防卡片墙，Design P4/P7） ----------
    with ui.card().style(card_css):
        with ui.expansion("歌曲翻唱（点歌命中自动翻唱→成品自动插播）", value=False).style("width:100%"):
            # ① 队列状态卡（/cover/status 轮询；to_thread 解耦事件循环——nicegui3 教训）
            status_lab = ui.label("队列状态查询中…").style("font-size:12px;color:var(--text-secondary)")

            def _safe_get(base):
                try:
                    r = _session.get(f"{base}/cover/status", timeout=4)
                    return r.json()
                except Exception as e:
                    return {"available": False, "reason": str(e)[:80]}

            _prev_wait = {"code": "", "song": None}  # T3/Eng F1：转换检测状态（闭包）

            async def _poll_task():
                import asyncio as _aio
                data = await _aio.to_thread(lambda: _safe_get(_main_base(config)))
                if not data.get("available"):
                    status_lab.set_text(f"○ 翻唱队列不可用：{data.get('reason', 'main 未运行')}")
                    return
                q = data.get("queue") or []
                cur = data.get("current")
                if cur:
                    wr = cur.get("wait_reason") or ""
                    wc = cur.get("wait_code") or ""
                    if wr:
                        # 等待中（GPU 准入未过：直播运行/EDTalk 占用/显存不足/fail-closed）
                        status_lab.set_text(f"⏳ 等待中《{cur['song']}》：{wr}"
                                            f" · 排队 {len(q)} 个 · 空闲后自动开始")
                    else:
                        status_lab.set_text(f"▶ 正在翻唱《{cur['song']}》（{cur.get('elapsed_s', 0):.0f}s）"
                                            f" · 排队 {len(q)} 个")
                    # Eng F1：转换通知在轮询侧（cover_worker 主进程无 NiceGUI 客户端，
                    # worker 侧 notify=no-op）——wait_code 非空→空且同任务=开始翻唱
                    if _prev_wait["code"] and not wc and _prev_wait["song"] == cur.get("song"):
                        ui.notify(f"《{cur['song']}》翻唱开始（GPU 空闲）", type="positive")
                    _prev_wait["code"] = wc
                    _prev_wait["song"] = cur.get("song")
                elif q:
                    status_lab.set_text(f"○ 排队中：{len(q)} 个（下一首《{q[0]['song']}》）")
                    _prev_wait["code"] = ""
                    _prev_wait["song"] = None
                else:
                    status_lab.set_text("○ 翻唱队列空闲（弹幕点歌自动入队）")
                    _prev_wait["code"] = ""
                    _prev_wait["song"] = None

            ui.timer(5.0, _poll_task)

            # ①.5 上传歌曲+一键翻唱（2026-11-02 用户裁决重构：①上传入口改标准
            # outline 按钮（q-uploader 原生拾取按钮过小且样式不统一——llm 页隐藏
            # uploader 模式同款先例），上传后显示音频预览；②「选择歌曲」下拉移除
            # （多余）——一键翻唱直接使用本次上传的歌曲；③手动入队 manual=True
            # 旁路冷却/同观众上限，连翻多首不被静默拒绝）
            ui.separator()

            async def _handle_upload(e):
                import re as _re
                # 双版本兼容：NiceGUI ≥3.x 新 API 为 e.file（FileUpload，
                # save/read 异步）；旧 API 为 e.name + e.content（BytesIO 同步读）
                file = getattr(e, "file", None)
                if file is not None:
                    name = file.name

                    async def _save(dest):
                        await file.save(dest)
                else:
                    name = getattr(e, "name", None)

                    async def _save(dest):
                        with open(dest, "wb") as f:
                            f.write(e.content.read())
                name = os.path.basename(name or "").strip()
                stem, ext = os.path.splitext(name)
                if ext.lower() not in (".mp3", ".wav", ".flac"):
                    ui.notify(f"格式 {ext or '未知'} 不在支持列表 [.mp3/.wav/.flac]，请转换格式后重传",
                              type="warning")
                    return
                safe = _re.sub(r'[\\/:*?"<>|]', "_", stem).strip() or "untitled"
                dest = os.path.join(song_audio_root(), safe + ext.lower())
                try:
                    await _save(dest)
                except Exception as ex:
                    ui.notify(f"保存失败：{ex}", type="negative")
                    return
                uploaded_song["name"] = safe  # 一键翻唱使用本次上传的歌曲
                upload_hint.set_text(f"已上传：{name}")
                _show_upload_preview(name)
                ui.notify(f"上传完成：{name}", type="positive")

            def _show_upload_preview(filename):
                from urllib.parse import quote
                upload_preview.clear()
                with upload_preview:
                    try:
                        ui.audio(f"/song_source/{quote(filename)}", controls=True
                                 ).style("width:100%;max-width:420px;height:40px")
                    except Exception as _pv_err:
                        logger.warning(f"上传预览挂载/渲染失败（降级为无试听）：{_pv_err}")  # CEO F1

            _song_uploader = ui.upload(label='上传歌曲', auto_upload=True,
                                       on_upload=_handle_upload
                                       ).props('accept=.mp3,.wav,.flac').style("width:100%")
            _song_uploader.set_visibility(False)
            _song_uploader.on_rejected(lambda _e: ui.notify(
                position="top", type="negative",
                message="problem=文件被拒绝；cause=浏览器/上传器拒绝该文件；fix=确认文件为 .mp3/.wav/.flac 且可读后重试"))

            def _pick_song():
                # NiceGUI 元素根节点无 DOM id（getElementById 仅作快速路径）；
                # 按上传器元素 id 选取内部原生 input[type=file]（隐藏态程序化
                # 点击有效，且处于按钮点击的用户激活上下文内）
                ui.run_javascript(
                    "(() => { const root = document.getElementById("
                    f"'{_song_uploader.id}');"
                    "const input = (root || document)"
                    ".querySelector('input.q-uploader__input, input[type=file]');"
                    "if (input) { input.value = ''; input.click(); } })()")  # 选前清空：同文件重选也触发 change

            def _one_click_cover():
                song = (uploaded_song.get("name") or "").strip()
                if not song:
                    ui.notify("请先点击「上传歌曲」选择要翻唱的歌曲", type="warning")
                    return

                def _post():
                    try:
                        r = _session.post(f"{_main_base(config)}/cover/enqueue",
                                          json={"song": song}, timeout=6)
                        return r.status_code, r.json()
                    except Exception as ex:
                        return None, {"detail": str(ex)[:80]}

                code, data = _post()
                if code == 200:
                    action = data.get("action")
                    if action == "queued":
                        ui.notify(f"《{song}》已入翻唱队列（第 {data.get('position')} 位）——若 GPU 忙"
                                  f"（直播运行/数字人占用/显存不足）将自动排队，空闲后自动开始，完成后自动进入歌曲列表",
                                  type="positive")
                    elif action == "cached":
                        ui.notify(f"《{song}》已有翻唱成品，点歌即可播放", type="info")
                    elif action == "queue_full":
                        ui.notify("翻唱队列已满，稍后再试", type="warning")
                    elif action == "cooldown":
                        ui.notify("操作过于频繁（冷却中），稍后再试", type="warning")
                    else:
                        ui.notify(f"入队结果：{action}", type="info")
                else:
                    ui.notify(f"翻唱入队失败：{data.get('detail', code)}", type="negative")

            uploaded_song: Dict[str, str] = {}  # 本次会话上传的歌曲 {"name": 歌名 stem}
            upload_hint = ui.label("未上传歌曲").style("font-size:12px;color:var(--text-secondary)")
            upload_preview = ui.column().style("width:100%;margin-top:2px")
            with ui.row().style("width:100%;align-items:center;gap:8px;flex-wrap:wrap"):
                ui.button('上传歌曲', icon='upload_file', on_click=_pick_song).props('outline').tooltip(
                    "打开文件管理器选取歌曲（.mp3/.wav/.flac），上传后可预览，并供翻唱与关键词点歌")
                ui.button('一键翻唱', icon='mic_external_on', on_click=_one_click_cover).props('outline').tooltip(
                    "使用刚上传的歌曲入翻唱队列（音色档案/音高用下方参数），完成后自动进入歌曲列表并插播")

            # ② 翻唱参数（档案选择写 cover_profile_id、音高写 cover_pitch——
            # cover_worker 派发 -p 直传八度，音色变更下次点歌生效）
            with ui.grid(columns=2).style("width:100%;margin-top:8px"):
                from utils.voice_profiles import load_profiles
                profs = load_profiles()
                opts = {pid: (p.get("name") or pid) for pid, p in profs.items() if p.get("rvc_model")}
                if not opts:
                    opts = {"default": "（默认：models/rvc/ 第一个模型）"}
                # NiceGUI select 的 value 必须是 options 的键：config 未存过档案 id 时
                # 旧代码回落 "default"——有档案后 options 不含该键，页面加载即
                # "Invalid value: default"。归一到首个可用键（空档案时即 "default"）。
                saved_pid = get_nested_value(config, "choose_song", "cover_profile_id")
                if saved_pid not in opts:
                    saved_pid = next(iter(opts))
                _auto_save(ui.select(label='翻唱音色档案（RVC）',
                                     value=saved_pid,
                                     options=opts).style("width:100%;"),
                           keys=("choose_song", "cover_profile_id"))
                with ui.column().style("width:100%"):
                    pitch_val = int(get_nested_value(config, "choose_song", "cover_pitch") or 0)
                    plab = ui.label(f"翻唱音高（八度）: {pitch_val:+d}").style("font-size:12px")
                    ui.slider(min=-12, max=12, step=1, value=pitch_val,
                              on_change=lambda e: (plab.set_text(f"翻唱音高（八度）: {int(e.value):+d}"),
                                                   set_config_callback("choose_song", "cover_pitch", value=int(e.value))),
                              ).props("label-always").style("width:100%")
            ui.label("音高单位=八度（男转女 +1）；AI 翻唱内部换算半音").style(
                "font-size:11px;color:var(--text-secondary)")

            # ③ 歌曲列表（已翻唱成品，内部约定 song/covers/，R1；可预览播放，
            # 手动刷新——翻唱完成入队通知后点刷新即见新品）
            ui.separator()

            def _render_covers():
                covers_dir = os.path.join(song_audio_root(), "covers")
                covers = sorted(
                    (f for f in (os.listdir(covers_dir) if os.path.isdir(covers_dir) else [])
                     if f.endswith(".wav") and not (f.endswith("_vocal.wav") or f.endswith("_inst.wav"))),
                    reverse=True)
                covers_box.clear()
                with covers_box:
                    if not covers:
                        ui.label("暂无翻唱成品——上传歌曲后点击「一键翻唱」，或弹幕点歌命中自动翻唱；成品在此列出并可试听").style(
                            "font-size:12px;color:var(--text-secondary)")
                        return
                    # /song_audio 挂载已提升至 tab 构建期一次性挂载（T2/Eng F3：
                    # 每次 _render_covers 重复注册致路由表无界增长）
                    with ui.column().style("width:100%;gap:2px;max-height:240px;overflow:auto"):
                        for fn in covers[:20]:
                            with ui.row().style("width:100%;align-items:center;gap:6px"):
                                ui.label(f"🎵 {fn}").style("font-size:12px;flex:1")
                                try:
                                    ui.audio(f"/song_audio/{fn}", controls=True).style("height:32px;width:260px")
                                except Exception as _au_err:
                                    logger.warning(f"成品试听控件渲染失败：{_au_err}")  # CEO F1

            covers_box = ui.column().style("width:100%")
            with ui.row().style("width:100%;align-items:center;gap:4px"):
                ui.label("歌曲列表（已翻唱成品，可预览播放）").style(
                    "font-size:12px;color:var(--text-secondary)")
                ui.button(icon='refresh').props('flat dense round').tooltip(
                    '刷新列表（翻唱完成后刷新即见新品）').on_click(_render_covers)
            _render_covers()

            # 曲源静态服务（T2/Eng F3 根修，2026-11-02 批次）：原守卫访问 NiceGUI
            # 3.16 不存在的 _app._stack 属性 → AttributeError 被 pass 吞 → 两路由
            # 从未挂载（预览 404 真根因）。修=守卫换实存的 _app.routes+add_static_files
            # 目录挂载（新文件自动可达）+max_cache_age=0（同名重传/重翻零陈旧，
            # CEO F3/Eng F4）+构建期 makedirs（covers 不存在=全程 404）。
            try:
                from nicegui import app as _app
                _song_root = song_audio_root()
                _covers_root = os.path.join(_song_root, "covers")
                os.makedirs(_song_root, exist_ok=True)
                os.makedirs(_covers_root, exist_ok=True)
                _have = {getattr(r, "path", None) for r in _app.routes}
                if "/song_source" not in _have:
                    _app.add_static_files("/song_source", _song_root, max_cache_age=0)
                if "/song_audio" not in _have:
                    _app.add_static_files("/song_audio", _covers_root, max_cache_age=0)
            except Exception as _st_err:
                logger.warning(f"曲源静态服务挂载失败（降级为无试听）：{_st_err}")  # CEO F1
