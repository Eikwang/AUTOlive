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
    return f"http://127.0.0.1:{int(get_nested_value(config, 'api_port') or 8000)}"


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
    # 歌曲路径 三控件——歌曲目录改为内部约定 song/；R2/R3 新增四文案+冷却+伴奏音量）
    with ui.card().style(card_css):
        ui.label('点歌模式')
        ui.label("弹幕含触发关键词+歌名 → 播报引导文案（打赏礼物即唱）；打赏礼物 → 匹配歌曲列表直接开唱。歌曲音频目录为内置 song/（无需配置）").style(
            "font-size: var(--font-size-assist, 12px); color: var(--text-hint, #9ca3af);")
        with ui.grid(columns=3):
            with ui.column().style("width:100%;"):
                _auto_save(ui.textarea(label='点歌触发关键词', value=textarea_data_change(get_nested_value(config, "choose_song", "start_cmd")), placeholder='弹幕中包含即触发点歌判断，换行分隔\n例如：主播会唱 / 可以唱 / 能唱').style("width:100%;"), keys=("choose_song", "start_cmd"))
            with ui.column().style("width:100%;"):
                _auto_save(ui.input(label='匹配失败文案', value=get_nested_value(config, "choose_song", "match_fail_copy"), placeholder='列表没有这首歌时回应\n变量：{user} {content}').style("width:100%;"), keys=("choose_song", "match_fail_copy"))
                _auto_save(ui.input(label='引导文案（列表有歌）', value=get_nested_value(config, "choose_song", "guide_copy"), placeholder='弹幕问歌未打赏时回应，不播放\n变量：{user} {content} {gift}').style("width:100%;"), keys=("choose_song", "guide_copy"))
            with ui.column().style("width:100%;"):
                _auto_save(ui.input(label='匹配成功文案（礼物点歌）', value=get_nested_value(config, "choose_song", "match_success_copy"), placeholder='礼物命中歌曲后播报\n变量：{user} {content} {gift}').style("width:100%;"), keys=("choose_song", "match_success_copy"))
                _auto_save(ui.input(label='准备文案（翻唱生成中）', value=get_nested_value(config, "choose_song", "prepare_copy"), placeholder='命中但无成品、翻唱生成中播报\n变量：{user} {content}').style("width:100%;"), keys=("choose_song", "prepare_copy"))
        with ui.grid(columns=3):
            with ui.column().style("width:100%;"):
                similarity_val = float(get_nested_value(config, "choose_song", "similarity") or 0.5)
                simlab = ui.label(f'匹配最低相似度: {similarity_val:.2f}').style("font-size:12px")
                ui.slider(min=0.1, max=1.0, step=0.05, value=similarity_val,
                          on_change=lambda e: (simlab.set_text(f'匹配最低相似度: {float(e.value):.2f}'),
                                               set_config_callback("choose_song", "similarity", float(e.value)))
                          ).props("label-always").style("width:100%")
            with ui.column().style("width:100%;"):
                cd_val = int(get_nested_value(config, "choose_song", "song_cooldown_s") or 300)
                cdlab = ui.label(f'同歌冷却间隔: {cd_val}s').style("font-size:12px")
                ui.slider(min=0, max=1800, step=30, value=cd_val,
                          on_change=lambda e: (cdlab.set_text(f'同歌冷却间隔: {int(e.value)}s'),
                                               set_config_callback("choose_song", "song_cooldown_s", int(e.value)))
                          ).props("label-always").style("width:100%")
            with ui.column().style("width:100%;"):
                rc_val = int(get_nested_value(config, "choose_song", "reply_cooldown_s") or 30)
                rclab = ui.label(f'弹幕回应节流: {rc_val}s').style("font-size:12px")
                ui.slider(min=0, max=300, step=5, value=rc_val,
                          on_change=lambda e: (rclab.set_text(f'弹幕回应节流: {int(e.value)}s'),
                                               set_config_callback("choose_song", "reply_cooldown_s", int(e.value)))
                          ).props("label-always").style("width:100%")
        with ui.grid(columns=2):
            with ui.column().style("width:100%;"):
                bv_val = float(get_nested_value(config, "choose_song", "backing_volume") or 0.8)
                bvlab = ui.label(f'伴奏音量（数字人演唱时）: {bv_val:.0%}').style("font-size:12px")
                ui.slider(min=0.0, max=1.0, step=0.05, value=bv_val,
                          on_change=lambda e: (bvlab.set_text(f'伴奏音量（数字人演唱时）: {float(e.value):.0%}'),
                                               set_config_callback("choose_song", "backing_volume", float(e.value)))
                          ).props("label-always").style("width:100%")
            ui.label("伴奏音量：数字人演唱时伴奏从直播设备外放的音量（0=静音）").style(
                "font-size: var(--font-size-assist, 12px); color: var(--text-hint, #9ca3af);")

    # ---------- 礼物点歌映射表+匹配预演（D4：choose_song.gift_song_map 新键） ----------
    with ui.card().style(card_css):
        with ui.expansion("礼物点歌（礼物事件映射歌曲播放）", value=False).style("width:100%"):
            ui.label("礼物名命中映射表后，按点歌既有链路（数字人口型）播放对应歌曲；配置即时保存，直播中可增删").style(
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

            async def _poll_task():
                import asyncio as _aio
                data = await _aio.to_thread(lambda: _safe_get(_main_base(config)))
                if not data.get("available"):
                    status_lab.set_text(f"○ 翻唱队列不可用：{data.get('reason', 'main 未运行')}")
                    return
                q = data.get("queue") or []
                cur = data.get("current")
                if cur:
                    status_lab.set_text(f"▶ 正在翻唱《{cur['song']}》（{cur.get('elapsed_s', 0):.0f}s）"
                                        f" · 排队 {len(q)} 个")
                elif q:
                    status_lab.set_text(f"○ 排队中：{len(q)} 个（下一首《{q[0]['song']}》）")
                else:
                    status_lab.set_text("○ 翻唱队列空闲（弹幕点歌自动入队）")

            ui.timer(5.0, _poll_task)

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
                                                   set_config_callback("choose_song", "cover_pitch", int(e.value))),
                              ).props("label-always").style("width:100%")
            ui.label("音高单位=八度（男转女 +1）；AI 翻唱内部换算半音").style(
                "font-size:11px;color:var(--text-secondary)")

            # ③ 成品列表（内部约定 song/covers/，R1）+ 试听入口（R7 UI 三项）+ EMPTY 引导
            covers_dir = os.path.join(song_audio_root(), "covers")
            covers = sorted((f for f in (os.listdir(covers_dir) if os.path.isdir(covers_dir) else [])
                             if f.endswith(".wav") and not (f.endswith("_vocal.wav") or f.endswith("_inst.wav"))),
                            reverse=True)
            if not covers:
                ui.label("暂无翻唱成品——礼物点歌命中后将自动翻唱，成品在此列出并可试听").style(
                    "font-size:12px;color:var(--text-secondary)")
            else:
                try:
                    from nicegui import app as _app
                    if not any(r.path == "/song_audio" for r in _app._stack.routes if hasattr(r, "path")):
                        _app.add_media_files("/song_audio", covers_dir)
                except Exception:
                    pass  # 静态服务注册失败仅降级为无试听（列表仍可用）
                with ui.column().style("width:100%;gap:2px;max-height:240px;overflow:auto"):
                    for fn in covers[:20]:
                        with ui.row().style("width:100%;align-items:center;gap:6px"):
                            ui.label(f"🎵 {fn}").style("font-size:12px;flex:1")
                            try:
                                ui.audio(f"/song_audio/{fn}", controls=True).style("height:32px;width:260px")
                            except Exception:
                                pass
